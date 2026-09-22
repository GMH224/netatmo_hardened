"""The Netatmo data handler."""

from __future__ import annotations

import logging
from collections import deque
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import islice
from time import monotonic, time
from typing import Any

import aiohttp
import pyatmo
from homeassistant.components import cloud
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.device_registry import EventDeviceRegistryUpdatedData
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.helpers.event import (
    async_track_device_registry_updated_event,
    async_track_time_interval,
)
from pyatmo.exceptions import ApiTooManyRequestError
from pyatmo.modules.device_types import (
    DeviceCategory as NetatmoDeviceCategory,
)
from pyatmo.modules.device_types import (
    DeviceType as NetatmoDeviceType,
)
from pyatmo.schedule import Schedule

from .const import (
    CAMERA_CONNECTION_WEBHOOKS,
    CONF_ENABLE_WEBHOOK,
    DEFAULT_ENABLE_WEBHOOK,
    DOMAIN,
    MANUFACTURER,
    NETATMO_CREATE_BUTTON,
    NETATMO_CREATE_CAMERA,
    NETATMO_CREATE_CAMERA_LIGHT,
    NETATMO_CREATE_CLIMATE,
    NETATMO_CREATE_CLIMATE_BATTERY_SENSOR,
    NETATMO_CREATE_CONNECTIVITY_BINARY_SENSOR,
    NETATMO_CREATE_COVER,
    NETATMO_CREATE_FAN,
    NETATMO_CREATE_LEGACY_SENSOR,
    NETATMO_CREATE_LIGHT,
    NETATMO_CREATE_OPENING_BINARY_SENSOR,
    NETATMO_CREATE_ROOM_SENSOR,
    NETATMO_CREATE_SELECT,
    NETATMO_CREATE_SENSOR,
    NETATMO_CREATE_SWITCH,
    NETATMO_CREATE_WEATHER_BINARY_SENSOR,
    NETATMO_CREATE_WEATHER_SENSOR,
    PLATFORMS,
    WEBHOOK_ACTIVATION,
    WEBHOOK_DEACTIVATION,
    WEBHOOK_PUSH_TYPE,
)
from .device import (
    async_disabled_netatmo_ids,
    async_register_parent_devices,
    async_sync_home_disabled_state,
    netatmo_module_parents,
)
from .telemetry import (
    ERROR_AUTH,
    ERROR_NO_DEVICE,
    ERROR_THROTTLING,
    ERROR_TIMEOUT,
    ERROR_TRANSPORT,
    ERROR_UNKNOWN,
    ApiTelemetry,
    classify_status,
)

_LOGGER = logging.getLogger(__name__)

SIGNAL_NAME = "signal_name"
ACCOUNT = "account"
HOME = "home"
WEATHER = "weather"
AIR_CARE = "air_care"
PUBLIC = NetatmoDeviceType.public
EVENT = "event"

PUBLISHERS = {
    ACCOUNT: "async_update_topology",
    HOME: "async_update_status",
    WEATHER: "async_update_weather_stations",
    AIR_CARE: "async_update_air_care",
    PUBLIC: "async_update_public_weather",
    EVENT: "async_update_events",
}

BATCH_SIZE = 3
DEV_FACTOR = 7
DEV_LIMIT = 400
CLOUD_FACTOR = 2
CLOUD_LIMIT = 150
DEFAULT_INTERVALS = {
    ACCOUNT: 10800,
    HOME: 300,
    WEATHER: 600,
    AIR_CARE: 300,
    PUBLIC: 600,
    EVENT: 600,
}
SCAN_INTERVAL = 60
UNAVAILABLE_AFTER_ERRORS = 3
# [hardened-fork] Upstream used a 3600s (1 hour) ceiling here. A brief outage
# (a firewall reload, a router hiccup, a DNS blip) could trip
# UNAVAILABLE_AFTER_ERRORS and then leave the integration dark for up to an
# hour waiting for its own backed-off retry, even though the outage itself
# was over in seconds - the only fix was a manual "Reload". 300s keeps the
# same protective backoff behavior for sustained outages while making
# transient ones recover on their own within a few minutes.
MAX_ERROR_BACKOFF = 300
# [hardened-fork] If every publisher has been continuously unavailable for
# longer than this, something is stuck in a way the per-publisher backoff
# isn't recovering from on its own (e.g. a wedged OAuth session). Rather than
# waiting for the user to notice and hit "Reload" themselves, do it for them.
STALE_RELOAD_AFTER = 900
# [hardened-fork] The watchdog is a recovery mechanism, not a cure. If the
# fault survives this many automatic reloads then reloading is not working, and
# continuing forever is an unbounded loop generating API traffic and log noise
# in an unattended deployment. After this many attempts the integration stops
# reloading itself and raises a repair issue, so a human is told the truth: it
# is down and it cannot fix itself (defect C-13).
MAX_WATCHDOG_RELOADS = 3
ISSUE_WATCHDOG_EXHAUSTED = "watchdog_reload_exhausted"
ISSUE_AUTH_FAILED = "auth_failed_reauth_required"

type NetatmoConfigEntry = ConfigEntry[NetatmoDataHandler]


def _is_auth_failure(err: pyatmo.ApiError) -> bool:
    """Return whether an API error means "your authorization is no longer valid".

    [hardened-fork] 401 is unambiguous. 403 is not: Netatmo also answers 403
    when throttling, which pyatmo surfaces as ``ApiThrottlingError`` - a
    subclass of ``ApiError``. Treating that as an auth failure would push a
    rate-limited user into a pointless reauth prompt, so it is excluded
    explicitly rather than by status alone (defect C-7).
    """
    if isinstance(err, (pyatmo.ApiThrottlingError, ApiTooManyRequestError)):
        return False
    status = getattr(err, "status", None)
    return status in (401, 403)


def async_get_loaded_entry(hass: HomeAssistant) -> NetatmoConfigEntry | None:
    """Return the single loaded Netatmo config entry, if any."""
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    return entries[0] if entries else None


@dataclass
class NetatmoDevice:
    """Netatmo device class."""

    data_handler: NetatmoDataHandler
    device: pyatmo.modules.Module
    parent_id: str
    signal_name: str


@dataclass
class NetatmoHome:
    """Netatmo home class."""

    data_handler: NetatmoDataHandler
    home: pyatmo.Home
    parent_id: str
    signal_name: str


@dataclass
class NetatmoRoom:
    """Netatmo room class."""

    data_handler: NetatmoDataHandler
    room: pyatmo.Room
    parent_id: str
    signal_name: str


@dataclass
class NetatmoPublisher:
    """Class for keeping track of Netatmo data class metadata."""

    name: str
    interval: int
    next_scan: float
    subscriptions: set[CALLBACK_TYPE | None]
    method: str
    kwargs: dict
    available: bool = True
    error_count: int = 0
    unavailable_logged: bool = False


class NetatmoDataHandler:
    """Manages the Netatmo data handling."""

    account: pyatmo.AsyncAccount
    _interval_factor: int

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: NetatmoConfigEntry,
        auth: pyatmo.AbstractAsyncAuth,
    ) -> None:
        """Initialize self."""
        self.hass = hass
        self.config_entry = config_entry
        self.auth = auth
        self.publisher: dict[str, NetatmoPublisher] = {}
        self._queue: deque = deque()
        self._webhook: bool = False
        if config_entry.data["auth_implementation"] == cloud.DOMAIN:
            self._interval_factor = CLOUD_FACTOR
            self._rate_limit = CLOUD_LIMIT
        else:
            self._interval_factor = DEV_FACTOR
            self._rate_limit = DEV_LIMIT
        self.poll_start = time()
        self.poll_count = 0
        self.persons: dict[str, dict[str, str | None]] = {}
        self.schedules: dict[str, dict[str, Schedule]] = {}
        self.device_ids: dict[str, str] = {}
        self.cameras: dict[str, str] = {}
        self.events: dict[str, dict] = {}
        self.parent_device_ids: dict[str, str] = {}
        self.module_parents: dict[str, str] = {}
        self.home_device_ids: list[str] = []
        # [hardened-fork] stale-data watchdog state; see STALE_RELOAD_AFTER.
        self._all_unavailable_since: float | None = None
        self._reload_scheduled = False
        # [hardened-fork] Webhook lifecycle handles. Held here, one each, so
        # that registration is idempotent and neither the shutdown listener nor
        # the pending retry can accumulate across reconnects (defect C-12).
        self.webhook_stop_listener: CALLBACK_TYPE | None = None
        self.webhook_retry_cancel: CALLBACK_TYPE | None = None
        self._reauth_started = False
        # [hardened-fork] Snapshot of the options this handler was built with,
        # so the update listener can tell a real options change from the
        # routine OAuth token writes that also fire it (defect E-001).
        self.active_options: dict[str, Any] = deepcopy(dict(config_entry.options))
        # Whether the operator has declared that push events can work here.
        # Distinct from `webhook`, which reports whether one is *established*.
        self.webhook_expected: bool = bool(
            config_entry.options.get(CONF_ENABLE_WEBHOOK, DEFAULT_ENABLE_WEBHOOK)
        )
        # [hardened-fork] API telemetry (F-002). Deliberately not persisted:
        # the failure ratio describes the running system, and restoring an
        # hour-old window across a restart would report a fault that the
        # restart itself may have resolved.
        self.telemetry = ApiTelemetry()

    @property
    def _watchdog_reloads(self) -> int:
        """Return how many times the watchdog has already reloaded this entry.

        Kept in ``hass.data`` rather than on ``self`` because the data handler
        is rebuilt by every reload, and a counter that resets on reload cannot
        bound a reload loop (defect C-13).
        """
        store: dict[str, int] = self.hass.data.setdefault(f"{DOMAIN}_watchdog", {})
        return store.get(self.config_entry.entry_id, 0)

    @_watchdog_reloads.setter
    def _watchdog_reloads(self, value: int) -> None:
        store: dict[str, int] = self.hass.data.setdefault(f"{DOMAIN}_watchdog", {})
        store[self.config_entry.entry_id] = value

    @callback
    def async_note_recovery(self) -> None:
        """Clear watchdog state after a successful fetch."""
        if self._watchdog_reloads:
            self._watchdog_reloads = 0
            ir.async_delete_issue(
                self.hass,
                DOMAIN,
                f"{ISSUE_WATCHDOG_EXHAUSTED}_{self.config_entry.entry_id}",
            )

    async def async_setup(self) -> None:
        """Set up the Netatmo data handler."""
        self.config_entry.async_on_unload(
            async_track_time_interval(
                self.hass, self.async_update, timedelta(seconds=SCAN_INTERVAL)
            )
        )

        self.config_entry.async_on_unload(
            async_dispatcher_connect(
                self.hass,
                f"signal-{DOMAIN}-webhook-None",
                self.handle_event,
            )
        )

        self.account = pyatmo.AsyncAccount(
            self.auth,
            disabled_homes_ids=async_disabled_netatmo_ids(self.hass, self.config_entry),
        )

        await self.subscribe(ACCOUNT, ACCOUNT, None)

        # Parents must exist before a platform links a child to one
        self.module_parents = netatmo_module_parents(self.account)
        self.parent_device_ids = async_register_parent_devices(
            self.hass, self.config_entry, self.account, self.module_parents
        )
        self.home_device_ids = [
            self.parent_device_ids[home_id]
            for home_id in self.account.all_home_names
            if home_id in self.parent_device_ids
        ]
        async_sync_home_disabled_state(
            self.hass, self.config_entry, self.home_device_ids
        )
        self.config_entry.async_on_unload(
            async_track_device_registry_updated_event(
                self.hass, self.home_device_ids, self._handle_home_device_update
            )
        )

        await self.hass.config_entries.async_forward_entry_setups(
            self.config_entry, PLATFORMS
        )
        await self.async_dispatch()

    async def async_update(self, event_time: datetime) -> None:
        """Update device.

        We do up to BATCH_SIZE calls in one update in order
        to minimize the calls on the api service.
        """
        # [hardened-fork] Iterate a snapshot, not the live deque. ``async_fetch_data``
        # awaits network I/O, and an entity being added or removed during that
        # await calls subscribe()/unsubscribe(), which append to or remove from
        # this same deque - raising "deque mutated during iteration" and
        # aborting the whole update cycle (defect C-10).
        scheduled = list(islice(self._queue, 0, BATCH_SIZE * self._interval_factor))

        for data_class in scheduled:
            if data_class.next_scan > time():
                continue

            # The publisher may have been unsubscribed while we awaited an
            # earlier one in this same batch.
            if data_class.name not in self.publisher:
                continue

            if publisher := data_class.name:
                error = await self.async_fetch_data(publisher)

                if error:
                    self.publisher[publisher].next_scan = time() + min(
                        data_class.interval * 2 ** (data_class.error_count - 1),
                        MAX_ERROR_BACKOFF,
                    )
                else:
                    self.publisher[publisher].next_scan = time() + data_class.interval

        self._queue.rotate(BATCH_SIZE)
        cph = self.poll_count / (time() - self.poll_start) * 3600
        _LOGGER.debug("Calls per hour: %i", cph)
        if cph > self._rate_limit:
            for publisher in self.publisher.values():
                publisher.next_scan += 60
        if (time() - self.poll_start) > 3600:
            self.poll_start = time()
            self.poll_count = 0

        self._check_stale_watchdog()

    def _check_stale_watchdog(self) -> None:
        """[hardened-fork] Self-heal if every publisher has been down a while.

        The per-publisher backoff (MAX_ERROR_BACKOFF) handles ordinary
        transient outages. This is a second-line safety net for states that
        backoff alone doesn't recover from - e.g. a wedged OAuth session, or
        any failure mode we haven't specifically accounted for - so the
        integration finds its own way back instead of silently staying
        unavailable until a person notices and hits "Reload".
        """
        if self._reload_scheduled or not self.publisher:
            return

        if not all(not p.available for p in self.publisher.values()):
            self._all_unavailable_since = None
            return

        if self._all_unavailable_since is None:
            self._all_unavailable_since = time()
            return

        if time() - self._all_unavailable_since <= STALE_RELOAD_AFTER:
            return

        attempts = self._watchdog_reloads
        if attempts >= MAX_WATCHDOG_RELOADS:
            # [hardened-fork] Stop. Reloading has not fixed this, and repeating
            # it forever is worse than being honestly unavailable (defect C-13).
            self._reload_scheduled = True
            _LOGGER.error(
                "%s: still unavailable after %s automatic reloads; giving up and "
                "raising a repair issue. Manual intervention is required",
                MANUFACTURER,
                attempts,
            )
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                f"{ISSUE_WATCHDOG_EXHAUSTED}_{self.config_entry.entry_id}",
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key=ISSUE_WATCHDOG_EXHAUSTED,
                translation_placeholders={"attempts": str(attempts)},
            )
            return

        self._reload_scheduled = True
        self._watchdog_reloads = attempts + 1
        _LOGGER.warning(
            "%s: all data has been unavailable for over %s seconds; reloading "
            "the integration automatically (attempt %s of %s)",
            MANUFACTURER,
            STALE_RELOAD_AFTER,
            attempts + 1,
            MAX_WATCHDOG_RELOADS,
        )
        self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)

    @callback
    def async_force_update(self, signal_name: str) -> None:
        """Prioritize data retrieval for given data class entry."""
        self.publisher[signal_name].next_scan = time()
        self._queue.rotate(-(self._queue.index(self.publisher[signal_name])))

    async def handle_event(self, event: dict) -> None:
        """Handle webhook activation events.

        [hardened-fork] Upstream indexed ``event["data"][WEBHOOK_PUSH_TYPE]``
        three times without checking either key. The webhook path defaulted a
        missing event type to the string ``"None"``, which is exactly the
        dispatcher signal this handler subscribes to, so any payload lacking an
        ``event_type`` arrived here and raised ``KeyError`` (defect C-8).
        Ingress validation now blocks that, but this remains defensive: it is a
        dispatcher callback and must not raise.
        """
        data = event.get("data") if isinstance(event, dict) else None
        if not isinstance(data, dict):
            return

        push_type = data.get(WEBHOOK_PUSH_TYPE)
        if push_type == WEBHOOK_ACTIVATION:
            _LOGGER.debug("%s webhook successfully registered", MANUFACTURER)
            self._webhook = True

        elif push_type == WEBHOOK_DEACTIVATION:
            _LOGGER.debug("%s webhook unregistered", MANUFACTURER)
            self._webhook = False

        elif push_type in CAMERA_CONNECTION_WEBHOOKS:
            _LOGGER.debug("%s camera reconnected", MANUFACTURER)
            self.async_force_update(ACCOUNT)

    async def async_fetch_data(self, signal_name: str) -> bool:
        """Fetch data and notify."""
        self.poll_count += 1
        publisher = self.publisher[signal_name]
        was_available = publisher.available
        has_error = False
        # [hardened-fork] F-002. Two clocks on purpose: `monotonic` for the
        # duration, because wall time can step during a slow call and produce
        # a negative latency; `time` for the timestamps an operator reads,
        # because a monotonic reading means nothing on a dashboard.
        started = monotonic()
        error_type: str | None = None
        error_message: Any = None
        try:
            await getattr(self.account, publisher.method)(**publisher.kwargs)

        except ConfigEntryAuthFailed:
            # [hardened-fork] From HA 2026.10 the OAuth2 helper raises config
            # entry exceptions directly, so an expired or revoked grant arrives
            # here already classified. Reauthentication is a user action - it
            # must start a reauth flow, never be retried silently as though it
            # were a network blip (defect C-7).
            has_error = True
            error_type = ERROR_AUTH
            error_message = "Authentication failed; reauthentication required"
            self._async_start_reauth()

        except pyatmo.ApiError as err:
            # [hardened-fork] Upstream flattened every ApiError into the same
            # "unavailable, back off" path. pyatmo 9.9.0 carries the HTTP status
            # and the Netatmo error code, so an authorization failure can be
            # told apart from a rate limit or a server fault and routed to the
            # recovery that actually applies (defect C-7).
            has_error = True
            error_message = err
            # Throttling is checked before the status, because Netatmo answers
            # 403 when rate limiting and `classify_status` would call that an
            # authorization failure - the same ambiguity E-010 had to handle.
            if isinstance(err, (pyatmo.ApiThrottlingError, ApiTooManyRequestError)):
                error_type = ERROR_THROTTLING
            else:
                error_type = classify_status(getattr(err, "status", None))
            if _is_auth_failure(err):
                error_type = ERROR_AUTH
                self._async_start_reauth()
                _LOGGER.warning(
                    "Netatmo rejected our authorization while fetching %s "
                    "(status=%s code=%s); reauthentication required",
                    signal_name,
                    getattr(err, "status", None),
                    getattr(err, "code", None),
                )
            else:
                self._log_publisher_error(publisher, signal_name, err)

        except (
            pyatmo.NoDeviceError,
            TimeoutError,
            aiohttp.ClientError,
        ) as err:
            has_error = True
            error_message = err
            if isinstance(err, pyatmo.NoDeviceError):
                error_type = ERROR_NO_DEVICE
            elif isinstance(err, TimeoutError):
                error_type = ERROR_TIMEOUT
            else:
                error_type = ERROR_TRANSPORT
            self._log_publisher_error(publisher, signal_name, err)
        else:
            if publisher.unavailable_logged:
                _LOGGER.info("Fetching %s data recovered", signal_name)
                publisher.unavailable_logged = False
            self.async_note_recovery()

        # [hardened-fork] F-002. Recording is wrapped because telemetry must
        # never be able to break the thing it measures: an exception raised
        # here would propagate out of the update loop and take the publisher
        # cycle down with it, turning a monitoring feature into an outage.
        try:
            self._record_telemetry(
                has_error, monotonic() - started, error_type, error_message
            )
        except Exception:  # see above; telemetry is never allowed to be fatal
            _LOGGER.exception("Failed to record Netatmo API telemetry")

        if has_error:
            publisher.error_count += 1
        else:
            publisher.error_count = 0

        # Tolerate transient backend errors before marking entities unavailable
        publisher.available = publisher.error_count < UNAVAILABLE_AFTER_ERRORS

        # [hardened-fork] A single publisher recovering is a strong signal
        # the underlying outage (network, API, auth) is over. Nudge any
        # other still-unavailable publishers to retry on the *next* regular
        # update tick (<=60s) instead of waiting out their own individual
        # backoff, which may have grown to several minutes.
        if publisher.available and not was_available:
            for other_name, other in self.publisher.items():
                if other_name != signal_name and not other.available:
                    other.next_scan = min(other.next_scan, time())

        self._notify_subscribers(signal_name)
        return has_error

    def _record_telemetry(
        self,
        has_error: bool,
        latency_seconds: float,
        error_type: str | None,
        error_message: Any,
    ) -> None:
        """Record one API outcome and tell the telemetry sensors to refresh.

        [hardened-fork] F-002.

        The refresh is dispatched on its own signal rather than through the
        publisher subscriptions every other entity uses. Publisher
        subscriptions carry availability with them - an entity bound to a
        failing publisher goes unavailable - and a diagnostic sensor that goes
        unavailable exactly when the API starts failing reports nothing at the
        only moment anybody reads it.
        """
        now = time()
        if has_error:
            self.telemetry.record_failure(
                now,
                error_type or ERROR_UNKNOWN,
                error_message,
                latency_seconds,
            )
        else:
            self.telemetry.record_success(now, latency_seconds)

        async_dispatcher_send(
            self.hass, f"signal-{DOMAIN}-telemetry-{self.config_entry.entry_id}"
        )

    def _log_publisher_error(
        self, publisher: NetatmoPublisher, signal_name: str, err: Exception
    ) -> None:
        """Log a publisher failure once, then quietly until it recovers."""
        if not publisher.unavailable_logged:
            _LOGGER.info("Error while fetching %s data: %s", signal_name, err)
            publisher.unavailable_logged = True
        else:
            _LOGGER.debug("Still failing to fetch %s data: %s", signal_name, err)

    @callback
    def _async_start_reauth(self) -> None:
        """Start a reauthentication flow, at most one at a time."""
        if self._reauth_started:
            return
        self._reauth_started = True
        self.config_entry.async_start_reauth(self.hass)

    def _notify_subscribers(self, signal_name: str) -> None:
        """Notify all subscribers of a publisher to update their state."""
        for update_callback in self.publisher[signal_name].subscriptions:
            if update_callback:
                update_callback()

    def is_signal_available(self, signal_name: str) -> bool:
        """Return whether the last fetch for a publisher succeeded."""
        publisher = self.publisher.get(signal_name)
        return publisher is None or publisher.available

    async def subscribe(
        self,
        publisher: str,
        signal_name: str,
        update_callback: CALLBACK_TYPE | None,
        **kwargs: Any,
    ) -> None:
        """Subscribe to publisher."""
        if signal_name in self.publisher:
            if update_callback not in self.publisher[signal_name].subscriptions:
                self.publisher[signal_name].subscriptions.add(update_callback)
            return

        if publisher == "public":
            kwargs = {"area_id": self.account.register_public_weather_area(**kwargs)}

        interval = int(DEFAULT_INTERVALS[publisher] / self._interval_factor)
        self.publisher[signal_name] = NetatmoPublisher(
            name=signal_name,
            interval=interval,
            next_scan=time() + interval,
            subscriptions={update_callback},
            method=PUBLISHERS[publisher],
            kwargs=kwargs,
        )

        try:
            await self.async_fetch_data(signal_name)
        except KeyError:
            self.publisher.pop(signal_name)
            raise

        self._queue.append(self.publisher[signal_name])
        _LOGGER.debug("Publisher %s added", signal_name)

    async def unsubscribe(
        self, signal_name: str, update_callback: CALLBACK_TYPE | None
    ) -> None:
        """Unsubscribe from publisher."""
        if update_callback not in self.publisher[signal_name].subscriptions:
            return

        self.publisher[signal_name].subscriptions.remove(update_callback)

        if not self.publisher[signal_name].subscriptions:
            self._queue.remove(self.publisher[signal_name])
            self.publisher.pop(signal_name)
            _LOGGER.debug("Publisher %s removed", signal_name)

    @property
    def webhook(self) -> bool:
        """Return the webhook state."""
        return self._webhook

    async def async_dispatch(self) -> None:
        """Dispatch the creation of entities."""
        await self.subscribe(WEATHER, WEATHER, None)
        await self.subscribe(AIR_CARE, AIR_CARE, None)

        self.setup_air_care()

        for home in self.account.homes.values():
            signal_home = f"{HOME}-{home.entity_id}"

            await self.subscribe(HOME, signal_home, None, home_id=home.entity_id)
            await self.subscribe(EVENT, signal_home, None, home_id=home.entity_id)

            self.setup_climate_schedule_select(home, signal_home)
            self.setup_rooms(home, signal_home)
            self.setup_modules(home, signal_home)

            self.persons[home.entity_id] = {
                person.entity_id: person.pseudo for person in home.persons.values()
            }

        await self.unsubscribe(WEATHER, None)
        await self.unsubscribe(AIR_CARE, None)

    @callback
    def _handle_home_device_update(
        self, event: Event[EventDeviceRegistryUpdatedData]
    ) -> None:
        """Reload when a home device is enabled or disabled."""
        if event.data["action"] == "update" and "disabled_by" in event.data["changes"]:
            self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)

    def setup_air_care(self) -> None:
        """Set up home coach/air care modules."""
        for module in self.account.modules.values():
            if module.device_category is NetatmoDeviceCategory.air_care:
                for signal in (
                    NETATMO_CREATE_WEATHER_BINARY_SENSOR,
                    NETATMO_CREATE_WEATHER_SENSOR,
                ):
                    async_dispatcher_send(
                        self.hass,
                        signal,
                        NetatmoDevice(
                            self,
                            module,
                            AIR_CARE,
                            AIR_CARE,
                        ),
                    )

    def setup_modules(self, home: pyatmo.Home, signal_home: str) -> None:
        """Set up modules."""
        netatmo_type_signal_map = {
            NetatmoDeviceCategory.camera: [
                NETATMO_CREATE_CAMERA,
                NETATMO_CREATE_CAMERA_LIGHT,
            ],
            NetatmoDeviceCategory.dimmer: [NETATMO_CREATE_LIGHT],
            NetatmoDeviceCategory.shutter: [
                NETATMO_CREATE_COVER,
                NETATMO_CREATE_BUTTON,
            ],
            NetatmoDeviceCategory.switch: [
                NETATMO_CREATE_LIGHT,
                NETATMO_CREATE_SWITCH,
                NETATMO_CREATE_LEGACY_SENSOR,
            ],
            NetatmoDeviceCategory.meter: [NETATMO_CREATE_LEGACY_SENSOR],
            NetatmoDeviceCategory.fan: [NETATMO_CREATE_FAN],
            NetatmoDeviceCategory.opening: [
                NETATMO_CREATE_CONNECTIVITY_BINARY_SENSOR,
                NETATMO_CREATE_OPENING_BINARY_SENSOR,
                NETATMO_CREATE_SENSOR,
            ],
        }
        for module in home.modules.values():
            if not module.device_category:
                continue

            for signal in netatmo_type_signal_map.get(module.device_category, []):
                async_dispatcher_send(
                    self.hass,
                    signal,
                    NetatmoDevice(
                        self,
                        module,
                        home.entity_id,
                        signal_home,
                    ),
                )
            if module.device_category is NetatmoDeviceCategory.weather:
                for signal in (
                    NETATMO_CREATE_WEATHER_BINARY_SENSOR,
                    NETATMO_CREATE_WEATHER_SENSOR,
                ):
                    async_dispatcher_send(
                        self.hass,
                        signal,
                        NetatmoDevice(
                            self,
                            module,
                            home.entity_id,
                            WEATHER,
                        ),
                    )

    def setup_rooms(self, home: pyatmo.Home, signal_home: str) -> None:
        """Set up rooms."""
        for room in home.rooms.values():
            if NetatmoDeviceCategory.climate in room.features:
                async_dispatcher_send(
                    self.hass,
                    NETATMO_CREATE_CLIMATE,
                    NetatmoRoom(
                        self,
                        room,
                        home.entity_id,
                        signal_home,
                    ),
                )

                for module in room.modules.values():
                    if module.device_category is NetatmoDeviceCategory.climate:
                        async_dispatcher_send(
                            self.hass,
                            NETATMO_CREATE_CLIMATE_BATTERY_SENSOR,
                            NetatmoDevice(
                                self,
                                module,
                                room.entity_id,
                                signal_home,
                            ),
                        )

                if "humidity" in room.features:
                    async_dispatcher_send(
                        self.hass,
                        NETATMO_CREATE_ROOM_SENSOR,
                        NetatmoRoom(
                            self,
                            room,
                            room.entity_id,
                            signal_home,
                        ),
                    )

    def setup_climate_schedule_select(
        self, home: pyatmo.Home, signal_home: str
    ) -> None:
        """Set up climate schedule per home."""
        if NetatmoDeviceCategory.climate in [
            next(iter(x)) for x in [room.features for room in home.rooms.values()] if x
        ]:
            self.schedules[home.entity_id] = self.account.homes[
                home.entity_id
            ].schedules

            async_dispatcher_send(
                self.hass,
                NETATMO_CREATE_SELECT,
                NetatmoHome(
                    self,
                    home,
                    home.entity_id,
                    signal_home,
                ),
            )
