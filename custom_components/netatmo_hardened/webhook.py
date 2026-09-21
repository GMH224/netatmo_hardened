"""Netatmo webhook registration, recovery and event ingress.

[hardened-fork] This module owns the integration's only externally reachable
surface. Three classes of defect were fixed here; see ``docs/DEFECT_REGISTER.md``
for the full traceability matrix.

* Ingress validation (C-8, C-9, C-18). Every inbound payload is normalised by
  :mod:`.event_validation` before anything downstream sees it.
* Registration recovery (C-6). The retry envelope now covers the *whole*
  registration transaction, including cloudhook creation, not just the final
  API call.
* Lifecycle (C-4, C-12). Exactly one shutdown listener and at most one pending
  retry exist per config entry, and cleanup tolerates transport failures.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

import aiohttp
import pyatmo
from aiohttp.web import Request
from homeassistant.components import cloud
from homeassistant.components.webhook import (
    async_generate_url as webhook_generate_url,
)
from homeassistant.components.webhook import (
    async_register as webhook_register,
)
from homeassistant.components.webhook import (
    async_unregister as webhook_unregister,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    ATTR_DEVICE_ID,
    ATTR_ID,
    ATTR_NAME,
    ATTR_PERSONS,
    CONF_WEBHOOK_ID,
    EVENT_HOMEASSISTANT_STOP,
)
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later

from . import event_validation as validate
from .const import (
    ATTR_EVENT_TYPE,
    ATTR_FACE_URL,
    ATTR_HOME_ID,
    ATTR_IS_KNOWN,
    CONF_CLOUDHOOK_URL,
    DEFAULT_PERSON,
    DOMAIN,
    EVENT_ID_MAP,
    EVENT_TYPE_OUTDOOR,
    EVENT_TYPE_THERM_MODE,
    NETATMO_EVENT,
    WEBHOOK_DEACTIVATION,
    WEBHOOK_PUSH_TYPE,
)
from .coordinator import NetatmoConfigEntry, NetatmoDataHandler

_LOGGER = logging.getLogger(__name__)

# Event types that are dispatched in their own right *and* may carry nested
# sub-events.
#
# [hardened-fork] Upstream mapped both of these to the empty string, so the
# nested loop read ``data.get("", [])`` and was unconditionally empty. The
# outdoor camera's animal / human / vehicle device triggers are only ever
# delivered as sub-events of an ``outdoor`` event, which meant those three
# documented triggers could never fire at all. ``therm_mode`` genuinely has no
# sub-events, so it is dispatched without a nested walk (defect C-18).
PARENT_EVENT_TYPES = (EVENT_TYPE_OUTDOOR, EVENT_TYPE_THERM_MODE)
SUBEVENT_COLLECTION = {EVENT_TYPE_OUTDOOR: "subevents"}

# [hardened-fork] Registration retry schedule. Upstream gave up permanently
# after a single ApiError - a transient network blip or a 429 required a manual
# "Reload" to recover. We retry with backoff and keep retrying indefinitely at
# the final interval, so the webhook self-heals without the user intervening.
WEBHOOK_RETRY_DELAYS = [30, 120, 600, 900]

# Failures that justify another attempt. Upstream caught only ``pyatmo.ApiError``
# and ``TimeoutError``, and only around the final API call, so a transport-level
# failure or a cloudhook outage escaped the recovery path entirely (defect C-6).
RECOVERABLE_ERRORS: tuple[type[Exception], ...] = (
    pyatmo.ApiError,
    TimeoutError,
    aiohttp.ClientError,
    cloud.CloudNotAvailable,
)


async def async_handle_webhook(
    hass: HomeAssistant, webhook_id: str, request: Request
) -> None:
    """Handle an inbound Netatmo webhook callback.

    This runs on attacker-reachable input. Nothing below may raise on a
    malformed body: the contract is "drop the event, log once, return".
    """
    try:
        raw = await request.json()
    except (ValueError, aiohttp.ClientError) as err:
        _LOGGER.warning("Discarding Netatmo webhook with unreadable body: %s", err)
        return

    data = validate.as_mapping(raw)
    if data is None:
        # Valid JSON, wrong shape: a list, string, number, bool or null.
        _LOGGER.warning(
            "Discarding Netatmo webhook payload with non-object root (%s)",
            type(raw).__name__,
        )
        return

    entry = next(
        (
            entry
            for entry in hass.config_entries.async_loaded_entries(DOMAIN)
            if entry.data.get(CONF_WEBHOOK_ID) == webhook_id
        ),
        None,
    )
    if entry is None:
        return
    data_handler = entry.runtime_data

    event_name = validate.event_type(data, ATTR_EVENT_TYPE)
    if event_name is None:
        # Upstream defaulted this to the string "None", which collided with the
        # coordinator's own activation signal and reached a bare
        # ``event["data"]["push_type"]`` lookup (defect C-8).
        _LOGGER.debug("Discarding Netatmo webhook event without a type")
        return

    _LOGGER.debug("Netatmo webhook: %s", validate.redacted_summary(data, event_name))

    if event_name in PARENT_EVENT_TYPES:
        async_send_event(data_handler, event_name, data)

        collection_key = SUBEVENT_COLLECTION.get(event_name)
        if collection_key is not None:
            for subevent in validate.mapping_members(data.get(collection_key)):
                # Sub-events inherit the parent's identifiers; Netatmo omits
                # them on the nested objects.
                merged = {**data, **subevent}
                merged.pop(collection_key, None)
                async_evaluate_event(data_handler, merged)
        return

    async_evaluate_event(data_handler, data)


def async_evaluate_event(
    data_handler: NetatmoDataHandler, event_data: dict[str, Any]
) -> None:
    """Evaluate a single validated event from the webhook."""
    event_name = validate.event_type(event_data, ATTR_EVENT_TYPE)
    if event_name is None:
        return

    if event_name != "person":
        async_send_event(data_handler, event_name, event_data)
        return

    # Person events fan out to one dispatch per recognised person. Upstream
    # iterated ``event_data.get(ATTR_PERSONS, {})`` - note the mapping default,
    # which yields *keys* when a dict arrives - and then indexed
    # ``data_handler.persons[event_data[ATTR_HOME_ID]]`` without checking that
    # the home was known (defect C-8, external finding SEC-003).
    if not validate.is_known_home(event_data, ATTR_HOME_ID, data_handler.persons):
        _LOGGER.debug("Discarding person event for an unknown home")
        return

    home_persons = data_handler.persons[event_data[ATTR_HOME_ID]]

    for person in validate.mapping_members(event_data.get(ATTR_PERSONS)):
        person_event_data = dict(event_data)
        person_id = validate.identifier(person, ATTR_ID)
        person_event_data[ATTR_ID] = person_id
        person_event_data[ATTR_NAME] = home_persons.get(person_id, DEFAULT_PERSON)
        person_event_data[ATTR_IS_KNOWN] = person.get(ATTR_IS_KNOWN)
        person_event_data[ATTR_FACE_URL] = person.get(ATTR_FACE_URL)

        async_send_event(data_handler, event_name, person_event_data)


def async_send_event(
    data_handler: NetatmoDataHandler, event_name: str, data: dict[str, Any]
) -> None:
    """Dispatch a validated event to entities and onto the Home Assistant bus."""
    hass = data_handler.hass
    _LOGGER.debug("Dispatching %s", validate.redacted_summary(data, event_name))

    async_dispatcher_send(
        hass,
        f"signal-{DOMAIN}-webhook-{event_name}",
        {"type": event_name, "data": data},
    )

    event_data: dict[str, Any] = {"type": event_name, "data": data}

    netatmo_id = validate.device_id_for_event(data, event_name, EVENT_ID_MAP)
    if netatmo_id is not None:
        event_data[ATTR_DEVICE_ID] = data_handler.device_ids.get(netatmo_id)

    hass.bus.async_fire(event_type=NETATMO_EVENT, event_data=event_data)


async def async_cloudhook_generate_url(
    hass: HomeAssistant, entry: NetatmoConfigEntry
) -> str:
    """Generate the full URL for a webhook_id."""
    if CONF_CLOUDHOOK_URL not in entry.data:
        webhook_url = await cloud.async_create_cloudhook(
            hass, entry.data[CONF_WEBHOOK_ID]
        )
        data = {**entry.data, CONF_CLOUDHOOK_URL: webhook_url}
        hass.config_entries.async_update_entry(entry, data=data)
        return webhook_url
    return str(entry.data[CONF_CLOUDHOOK_URL])


async def async_unregister_webhook(
    hass: HomeAssistant, entry: NetatmoConfigEntry
) -> None:
    """Unregister the webhook locally and from the Netatmo backend.

    Cleanup is best-effort and must never raise: this runs from config entry
    unload, and an exception here aborts the unload, which in turn makes every
    subsequent reload fail - including the coordinator's own stale-data
    watchdog, which fires for exactly the network conditions that cause it
    (defect C-4).
    """
    if CONF_WEBHOOK_ID not in entry.data:
        return

    _LOGGER.debug("Unregistering Netatmo webhook")
    async_cancel_webhook_retry(entry)

    async_dispatcher_send(
        hass,
        f"signal-{DOMAIN}-webhook-None",
        {"type": "None", "data": {WEBHOOK_PUSH_TYPE: WEBHOOK_DEACTIVATION}},
    )
    webhook_unregister(hass, entry.data[CONF_WEBHOOK_ID])

    try:
        await entry.runtime_data.auth.async_dropwebhook()
    except RECOVERABLE_ERRORS as err:
        # The remote registration may survive; Netatmo replaces it on the next
        # successful addwebhook, so a failure here is recorded and tolerated.
        _LOGGER.debug("Could not drop the Netatmo webhook: %s", err)


def async_cancel_webhook_retry(entry: NetatmoConfigEntry) -> None:
    """Cancel any pending registration retry for this entry."""
    data_handler = entry.runtime_data
    if data_handler.webhook_retry_cancel is not None:
        data_handler.webhook_retry_cancel()
        data_handler.webhook_retry_cancel = None


async def async_register_webhook(
    hass: HomeAssistant, entry: NetatmoConfigEntry, _retry: int = 0
) -> None:
    """Register the webhook with the Netatmo backend, retrying on failure.

    The entire transaction is inside the retry envelope - webhook id creation,
    cloudhook URL generation, local registration and the backend call. Upstream
    retried only the last of those, so a transient cloudhook failure bypassed
    the recovery this fork exists to provide (defect C-6).
    """
    if entry.state is not ConfigEntryState.LOADED:
        return

    data_handler = entry.runtime_data
    async_cancel_webhook_retry(entry)

    if CONF_WEBHOOK_ID not in entry.data:
        data = {**entry.data, CONF_WEBHOOK_ID: secrets.token_hex()}
        hass.config_entries.async_update_entry(entry, data=data)

    webhook_id = entry.data[CONF_WEBHOOK_ID]
    registered_locally = False

    try:
        if cloud.async_active_subscription(hass):
            webhook_url = await async_cloudhook_generate_url(hass, entry)
        else:
            webhook_url = webhook_generate_url(hass, webhook_id)

        if entry.data[
            "auth_implementation"
        ] == cloud.DOMAIN and not webhook_url.startswith("https://"):
            _LOGGER.warning(
                "Webhook not registered - https and port 443 are required "
                "to register the webhook"
            )
            return

        # Re-registering the same id replaces the handler, so retries are safe.
        webhook_register(hass, DOMAIN, "Netatmo", webhook_id, async_handle_webhook)
        registered_locally = True

        await data_handler.auth.async_addwebhook(webhook_url)

    except RECOVERABLE_ERRORS as err:
        if registered_locally:
            webhook_unregister(hass, webhook_id)

        delay = _retry_delay(err, _retry)
        _LOGGER.warning(
            "Netatmo webhook registration failed (%s). Retrying in %s seconds "
            "(attempt %s)",
            err,
            delay,
            _retry + 1,
        )

        async def _retry_registration(_now: Any = None) -> None:
            data_handler.webhook_retry_cancel = None
            if entry.state is not ConfigEntryState.LOADED:
                return
            await async_register_webhook(hass, entry, _retry=_retry + 1)

        # Exactly one pending retry per entry. Upstream pushed every cancel
        # handle onto ``entry.async_on_unload``, which never prunes, so an
        # entry that retried for days accumulated thousands of them (C-12).
        data_handler.webhook_retry_cancel = async_call_later(
            hass, delay, _retry_registration
        )
        return

    # [hardened-fork] The webhook URL contains the webhook id, which Home
    # Assistant documents as equivalent to a password. Upstream logged the full
    # URL at debug level, putting a bearer credential into log files, backups
    # and bug reports (defect C-11, external finding SEC-001).
    _LOGGER.debug("Netatmo webhook registered (id=<redacted>)")

    async_install_stop_listener(hass, entry)


def _retry_delay(err: Exception, attempt: int) -> float:
    """Return the delay before the next attempt, honouring server backoff."""
    retry_after = getattr(err, "retry_after", None)
    if isinstance(retry_after, (int, float)) and retry_after > 0:
        # Netatmo told us when to come back; respect it rather than hammering.
        return float(retry_after)
    return WEBHOOK_RETRY_DELAYS[min(attempt, len(WEBHOOK_RETRY_DELAYS) - 1)]


def async_install_stop_listener(hass: HomeAssistant, entry: NetatmoConfigEntry) -> None:
    """Install the shutdown unregister hook exactly once per config entry.

    Upstream added a fresh ``EVENT_HOMEASSISTANT_STOP`` listener on every
    successful registration. ``manage_cloudhook`` re-registers on each cloud
    reconnect, so a long-lived instance accumulated one listener per reconnect
    and fired that many duplicate ``async_dropwebhook()`` calls at shutdown
    (defect C-12).
    """
    data_handler = entry.runtime_data
    if data_handler.webhook_stop_listener is not None:
        return

    async def _handle_stop(_: Event) -> None:
        await async_unregister_webhook(hass, entry)

    data_handler.webhook_stop_listener = hass.bus.async_listen_once(
        EVENT_HOMEASSISTANT_STOP, _handle_stop
    )


def async_remove_stop_listener(entry: NetatmoConfigEntry) -> None:
    """Remove the shutdown hook, if one is installed."""
    data_handler = entry.runtime_data
    if data_handler.webhook_stop_listener is not None:
        data_handler.webhook_stop_listener()
        data_handler.webhook_stop_listener = None
