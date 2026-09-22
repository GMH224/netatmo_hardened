"""The Netatmo integration."""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any

from homeassistant.components import cloud
from homeassistant.components.webhook import async_unregister as webhook_unregister
from homeassistant.const import CONF_WEBHOOK_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import (
    aiohttp_client,
)
from homeassistant.helpers import (
    config_validation as cv,
)
from homeassistant.helpers import (
    device_registry as dr,
)
from homeassistant.helpers import (
    issue_registry as ir,
)
from homeassistant.helpers.config_entry_oauth2_flow import (
    OAuth2Session,
    async_get_config_entry_implementation,
)
from homeassistant.helpers.device_registry import AnyDeviceEntry, DeviceEntry
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.typing import ConfigType

from . import api
from .const import DOMAIN, ISSUE_PARTIAL_SCOPES, PLATFORMS
from .coordinator import NetatmoConfigEntry, NetatmoDataHandler
from .services import async_setup_services
from .webhook import (
    RECOVERABLE_ERRORS,
    async_cancel_webhook_retry,
    async_register_webhook,
    async_remove_stop_listener,
    async_unregister_webhook,
)

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# [hardened-fork] The upstream MAX_WEBHOOK_RETRIES=3 constant that used to live
# here was declared but never wired up to any retry logic. The real, working
# retry schedule lives in webhook.py as WEBHOOK_RETRY_DELAYS.


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Netatmo component."""
    async_setup_services(hass)

    return True


def _check_token_scopes(
    hass: HomeAssistant, entry: NetatmoConfigEntry, token_scopes: set[str]
) -> None:
    """Validate the granted OAuth scopes and surface any shortfall.

    [hardened-fork] Upstream tested ``granted & required`` and accepted the
    token if the intersection was non-empty, which let a partially authorized
    token look completely healthy until some unrelated feature failed hours
    later (defect C-15, external findings NET-008 / SEC-010).

    The external audit recommended replacing this with a strict subset test.
    That fix is wrong for this integration, and we deliberately do not apply
    it. Netatmo issues scopes per product family - weather station, thermostat,
    camera, shutter - and this integration supports all of them independently.
    A user who owns only a weather station legitimately holds none of the
    camera scopes. A strict subset test would push every such user into a
    permanent reauthentication loop, turning a diagnostics gap into a total
    outage. ``api.get_api_scopes`` already excludes five scopes for cloud
    auth for exactly this reason.

    So the rule is: a token with *no* usable scope is fatal, because nothing
    can work. A token with *some* missing scopes is serviceable but degraded,
    and is reported as a repair issue naming precisely what is missing -
    which is the visibility the audit was actually asking for.
    """
    required = set(api.get_api_scopes(entry.data["auth_implementation"]))
    missing = required - token_scopes
    issue_id = f"{ISSUE_PARTIAL_SCOPES}_{entry.entry_id}"

    if not missing:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return

    if not (token_scopes & required):
        # Nothing this integration can do with this token.
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        raise ConfigEntryAuthFailed(
            "The Netatmo token grants none of the required scopes"
        )

    _LOGGER.warning(
        "Netatmo token is missing %s of %s scopes; features needing them will "
        "be unavailable: %s",
        len(missing),
        len(required),
        ", ".join(sorted(missing)),
    )
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_PARTIAL_SCOPES,
        translation_placeholders={"scopes": ", ".join(sorted(missing))},
    )


async def async_setup_entry(hass: HomeAssistant, entry: NetatmoConfigEntry) -> bool:
    """Set up Netatmo from a config entry."""
    implementation = await async_get_config_entry_implementation(hass, entry)

    # Set unique id if none was set (migration)
    if not entry.unique_id:
        hass.config_entries.async_update_entry(entry, unique_id=DOMAIN)

    session = OAuth2Session(hass, entry, implementation)
    # [hardened-fork] No manual exception translation here. Since HA 2026.10 the
    # OAuth2 helper raises ConfigEntryNotReady / ConfigEntryAuthFailed itself,
    # with user-facing messages already translated, so wrapping these would
    # replace a precise error with a vaguer one.
    await session.async_ensure_token_valid()

    _check_token_scopes(hass, entry, _token_scopes(session.token))

    auth = api.AsyncConfigEntryNetatmoAuth(
        aiohttp_client.async_get_clientsession(hass), session
    )

    data_handler = NetatmoDataHandler(hass, entry, auth)
    entry.runtime_data = data_handler
    await data_handler.async_setup()

    async def register_webhook(_: Any = None) -> None:
        await async_register_webhook(hass, entry)

    async def unregister_webhook(_: Any = None) -> None:
        await async_unregister_webhook(hass, entry)

    async def manage_cloudhook(state: cloud.CloudConnectionState) -> None:
        if state is cloud.CloudConnectionState.CLOUD_CONNECTED:
            await register_webhook()

        if state is cloud.CloudConnectionState.CLOUD_DISCONNECTED:
            await unregister_webhook()
            entry.async_on_unload(async_call_later(hass, 30, register_webhook))

    if cloud.async_active_subscription(hass):
        if cloud.async_is_connected(hass):
            await register_webhook()
        entry.async_on_unload(
            cloud.async_listen_connection_change(hass, manage_cloudhook)
        )
    else:
        entry.async_on_unload(async_at_started(hass, register_webhook))

    entry.async_on_unload(entry.add_update_listener(async_config_entry_updated))

    return True


def _token_scopes(token: dict[str, Any]) -> set[str]:
    """Return the granted scopes as a set, whatever shape the token uses.

    [hardened-fork] Netatmo returns ``scope`` as a JSON array, but the OAuth2
    specification defines it as a space-delimited string and other providers
    send it that way. ``set()`` over a string yields a set of *characters*,
    which would silently match nothing and fail every token. Normalising both
    shapes removes a latent trap rather than relying on Netatmo never changing.
    """
    scope = token.get("scope", [])
    if isinstance(scope, str):
        return set(scope.split())
    if isinstance(scope, (list, tuple, set)):
        return {str(item) for item in scope}
    return set()


async def async_config_entry_updated(
    hass: HomeAssistant, entry: NetatmoConfigEntry
) -> None:
    """Handle the config entry being updated.

    [hardened-fork] From 2026.12 Home Assistant treats "a config entry update
    listener combined with a reloading method in the config flow" as an error.
    Upstream had both: this listener, plus an explicit ``async_reload`` inside
    ``async_oauth_create_entry``. The config flow no longer reloads; the
    decision is made here instead, which is also more correct - an options
    change (a public weather area) must *not* cost a full reload, while new
    credentials must.

    See docs/COMPATIBILITY.md, deprecation D-1.
    """
    data_handler = entry.runtime_data

    # [hardened-fork] This listener never reloads, and never reacts to token
    # data (defect E-001).
    #
    # 0.1.0 compared the entry's access token against the one the running
    # handler was built with and scheduled a full reload when they differed,
    # on the assumption that a changed token meant a completed reauth. It does
    # not. Home Assistant's OAuth2Session persists every *routine* refresh
    # through `async_update_entry`, which fires this listener - so with a
    # Netatmo access token lifetime of about three hours, the integration tore
    # itself down and rebuilt roughly eight times a day, for ever. That is a
    # worse availability defect than the deprecation the change was made to
    # resolve.
    #
    # No reload is needed for credentials in any case: OAuth2Session reads
    # `entry.data["token"]` live, so both a refresh and a completed reauth are
    # picked up by the running session without restarting anything.
    #
    # What this listener is actually for is an options change - the user
    # adding or editing a public weather area. Those are compared explicitly,
    # because the public weather signal rebuilds entities and must not fire on
    # an unrelated token write.
    if entry.options == data_handler.active_options:
        return

    data_handler.active_options = deepcopy(dict(entry.options))
    _LOGGER.debug("Netatmo options changed; refreshing public weather entities")
    async_dispatcher_send(hass, f"signal-{DOMAIN}-public-update-{entry.entry_id}")


async def async_unload_entry(hass: HomeAssistant, entry: NetatmoConfigEntry) -> bool:
    """Unload a config entry.

    [hardened-fork] Cleanup must not be able to fail the unload. Upstream
    caught only ``pyatmo.ApiError`` around ``async_dropwebhook()``, so a
    timeout or connection reset propagated out of here and aborted the unload -
    which also aborts every reload, including the watchdog's own recovery
    attempt for the very network fault that caused it (defect C-4).
    """
    async_cancel_webhook_retry(entry)
    async_remove_stop_listener(entry)

    if CONF_WEBHOOK_ID in entry.data:
        webhook_unregister(hass, entry.data[CONF_WEBHOOK_ID])
        try:
            await entry.runtime_data.auth.async_dropwebhook()
        except RECOVERABLE_ERRORS as err:
            _LOGGER.debug("Could not drop the Netatmo webhook on unload: %s", err)
        _LOGGER.debug("Unregistered Netatmo webhook")

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: NetatmoConfigEntry) -> None:
    """Cleanup when entry is removed."""
    hass.data.get(f"{DOMAIN}_watchdog", {}).pop(entry.entry_id, None)

    if CONF_WEBHOOK_ID in entry.data and cloud.async_active_subscription(hass):
        try:
            _LOGGER.debug("Removing Netatmo cloudhook")
            await cloud.async_delete_cloudhook(hass, entry.data[CONF_WEBHOOK_ID])
        except cloud.CloudNotAvailable:
            pass


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: NetatmoConfigEntry, device_entry: AnyDeviceEntry
) -> bool:
    """Remove a config entry from a device."""
    account = config_entry.runtime_data.account
    # A disabled home leaves the account, so everything below it looks stale to
    # the inventory check. Its descendants keep their own disabler, hence a walk.
    unpolled_home_ids = account.all_home_names.keys() - account.homes.keys()
    device_registry = dr.async_get(hass)
    device: AnyDeviceEntry | None = device_entry
    while device is not None:
        if any(
            identifier[1] in unpolled_home_ids
            for identifier in device.identifiers
            if identifier[0] == DOMAIN
        ):
            return False
        device = (
            device_registry.async_get(device.via_device_id, include_child_devices=False)
            if isinstance(device, DeviceEntry) and device.via_device_id
            else None
        )

    homes = config_entry.runtime_data.account.homes.values()
    valid_ids = {
        *config_entry.runtime_data.account.all_home_names,
        *config_entry.runtime_data.account.modules,
        *(module for home in homes for module in home.modules),
        *(room for home in homes for room in home.rooms),
    }

    return not any(
        identifier[1] in valid_ids
        for identifier in device_entry.identifiers
        if identifier[0] == DOMAIN
    )
