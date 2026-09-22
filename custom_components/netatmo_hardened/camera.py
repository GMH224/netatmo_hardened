"""Support for the Netatmo cameras."""

from __future__ import annotations

import logging
from typing import Any, cast, override

import aiohttp
import probatio
from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.const import ATTR_PERSONS
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pyatmo import ApiError as NetatmoApiError
from pyatmo import modules as NaModules
from pyatmo.event import Event as NaEvent

from .const import (
    ATTR_CAMERA_LIGHT_MODE,
    ATTR_EVENT_TYPE,
    ATTR_PERSON,
    CAMERA_LIGHT_MODES,
    CAMERA_TRIGGERS,
    CONF_URL_SECURITY,
    DOMAIN,
    EVENT_TYPE_CONNECTION,
    EVENT_TYPE_DISCONNECTION,
    EVENT_TYPE_LIGHT_MODE,
    EVENT_TYPE_OFF,
    EVENT_TYPE_ON,
    MANUFACTURER,
    NETATMO_ALIM_STATUS_ONLINE,
    NETATMO_CREATE_CAMERA,
    SERVICE_SET_CAMERA_LIGHT,
    SERVICE_SET_PERSON_AWAY,
    SERVICE_SET_PERSONS_HOME,
    WEBHOOK_PUSH_TYPE,
)
from .coordinator import EVENT, HOME, SIGNAL_NAME, NetatmoConfigEntry, NetatmoDevice
from .entity import NetatmoModuleEntity
from .helper import build_event_index, device_type_to_str

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

DEFAULT_QUALITY = "high"

# [hardened-fork] Recoverable transport failures for camera media.
#
# TimeoutError is the addition that matters (defect E-007). pyatmo's image
# request carries a finite HTTP timeout, and a camera or WAN path that accepts
# the connection but stalls raises asyncio.TimeoutError - which is TimeoutError
# on Python 3.11+ and is NOT an aiohttp.ClientError, so it escaped the old
# tuple entirely. aiohttp.ClientError replaces the three specific subclasses
# previously listed, which missed siblings such as ServerTimeoutError.
CAMERA_TRANSPORT_ERRORS: tuple[type[Exception], ...] = (
    TimeoutError,
    aiohttp.ClientError,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NetatmoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Netatmo camera platform."""

    @callback
    def _create_entity(netatmo_device: NetatmoDevice) -> None:
        entity = NetatmoCamera(netatmo_device)
        async_add_entities([entity])

    entry.async_on_unload(
        async_dispatcher_connect(hass, NETATMO_CREATE_CAMERA, _create_entity)
    )

    platform = entity_platform.async_get_current_platform()

    platform.async_register_entity_service(
        SERVICE_SET_PERSONS_HOME,
        {probatio.Required(ATTR_PERSONS): probatio.All(cv.ensure_list, [cv.string])},
        "_service_set_persons_home",
    )
    platform.async_register_entity_service(
        SERVICE_SET_PERSON_AWAY,
        {probatio.Optional(ATTR_PERSON): cv.string},
        "_service_set_person_away",
    )
    platform.async_register_entity_service(
        SERVICE_SET_CAMERA_LIGHT,
        {probatio.Required(ATTR_CAMERA_LIGHT_MODE): probatio.In(CAMERA_LIGHT_MODES)},
        "_service_set_camera_light",
    )


class NetatmoCamera(NetatmoModuleEntity, Camera):
    """Representation of a Netatmo camera."""

    _attr_brand = MANUFACTURER
    _attr_supported_features = CameraEntityFeature.STREAM
    _attr_configuration_url = CONF_URL_SECURITY
    device: NaModules.Camera
    _quality = DEFAULT_QUALITY
    _monitoring: bool | None = None
    _attr_name = None

    def __init__(
        self,
        netatmo_device: NetatmoDevice,
    ) -> None:
        """Set up for access to the Netatmo camera images."""
        Camera.__init__(self)
        super().__init__(netatmo_device)

        self._attr_unique_id = (
            f"{netatmo_device.device.entity_id}-{device_type_to_str(self.device_type)}"
        )
        self._light_state = None

        self._publishers.extend(
            [
                {
                    "name": HOME,
                    "home_id": self.home.entity_id,
                    SIGNAL_NAME: f"{HOME}-{self.home.entity_id}",
                },
                {
                    "name": EVENT,
                    "home_id": self.home.entity_id,
                    SIGNAL_NAME: f"{EVENT}-{self.home.entity_id}",
                },
            ]
        )

    @override
    async def async_added_to_hass(self) -> None:
        """Entity created."""
        await super().async_added_to_hass()

        for event_type in CAMERA_TRIGGERS:
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass,
                    f"signal-{DOMAIN}-webhook-{event_type}",
                    self.handle_event,
                )
            )

        self.data_handler.cameras[self.device.entity_id] = self.device.name

    @callback
    def handle_event(self, event: dict) -> None:
        """Handle webhook events."""
        data = event.get("data") if isinstance(event, dict) else None
        if not isinstance(data, dict):
            return

        event_type = data.get(ATTR_EVENT_TYPE)
        push_type = data.get(WEBHOOK_PUSH_TYPE)

        if not push_type:
            _LOGGER.debug("Event has no push_type, returning")
            return

        if not data.get("camera_id"):
            _LOGGER.debug("Event %s has no camera ID, returning", event_type)
            return

        # [hardened-fork] home_id was indexed directly here while camera_id and
        # push_type were guarded, so a payload carrying a camera id but no home
        # id still raised KeyError (defect C-9).
        if (
            data.get("home_id") == self.home.entity_id
            and data["camera_id"] == self.device.entity_id
        ):
            # device_type to be stripped "DeviceType."
            device_push_type = f"{self.device_type.name}-{event_type}"
            if push_type != device_push_type:
                _LOGGER.debug(
                    "Event push_type %s does not match device push_type %s, returning",
                    push_type,
                    device_push_type,
                )
                return

            if event_type in [EVENT_TYPE_DISCONNECTION, EVENT_TYPE_OFF]:
                _LOGGER.debug(
                    "Camera %s has received %s event,"
                    " turning off and idleing streaming",
                    data["camera_id"],
                    event_type,
                )
                self._attr_is_streaming = False
                self._monitoring = False
            elif event_type in [EVENT_TYPE_CONNECTION, EVENT_TYPE_ON]:
                _LOGGER.debug(
                    "Camera %s has received %s event,"
                    " turning on and enabling streaming"
                    " if applicable",
                    data["camera_id"],
                    event_type,
                )
                if self.device_type != "NDB":
                    self._attr_is_streaming = True
                self._monitoring = True
            elif event_type == EVENT_TYPE_LIGHT_MODE:
                if data.get("sub_type"):
                    self._light_state = data["sub_type"]
                else:
                    _LOGGER.debug(
                        "Camera %s has received light mode event without sub_type",
                        data["camera_id"],
                    )
            else:
                _LOGGER.debug(
                    "Camera %s has received unexpected event as type %s",
                    data["camera_id"],
                    event_type,
                )

            self.async_write_ha_state()
            return

    @override
    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a still image response from the camera."""
        try:
            return cast(bytes, await self.device.async_get_live_snapshot())
        except (*CAMERA_TRANSPORT_ERRORS, NetatmoApiError) as err:
            _LOGGER.debug("Could not fetch live camera image (%s)", err)
        return None

    @property
    @override
    def supported_features(self) -> CameraEntityFeature:
        """Return supported features."""
        supported_features = CameraEntityFeature.ON_OFF
        if self.device_type != "NDB":
            supported_features |= CameraEntityFeature.STREAM
        return supported_features

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return entity specific state attributes."""
        return {
            "id": self.device.entity_id,
            "monitoring": self._monitoring,
            "sd_status": self.device.sd_status,
            "alim_status": self.device.alim_status,
            "is_local": self.device.is_local,
            "vpn_url": self.device.vpn_url,
            "local_url": self.device.local_url,
            "light_state": self._light_state,
        }

    @override
    async def async_turn_off(self) -> None:
        """Turn off camera."""
        await self.async_command(self.device.async_monitoring_off(), "turn off")

    @override
    async def async_turn_on(self) -> None:
        """Turn on camera."""
        await self.async_command(self.device.async_monitoring_on(), "turn on")

    @override
    async def stream_source(self) -> str:
        """Return the stream source.

        [hardened-fork] The URL refresh is a network call and is wrapped like
        any other. A stalled local camera used to let a timeout escape into
        Home Assistant's stream machinery (defect E-007); the cached URL is a
        better answer than a traceback.
        """
        if self.device.is_local:
            try:
                await self.device.async_update_camera_urls()
            except (*CAMERA_TRANSPORT_ERRORS, NetatmoApiError) as err:
                _LOGGER.debug("Could not refresh camera URLs (%s)", err)

        if self.device.local_url:
            return f"{self.device.local_url}/live/files/{self._quality}/index.m3u8"
        return f"{self.device.vpn_url}/live/files/{self._quality}/index.m3u8"

    @callback
    @override
    def async_update_callback(self) -> None:
        """Update the entity's state."""
        self._attr_is_on = self.device.alim_status is not None
        self._attr_available = self.device.alim_status is not None

        if self.device_type == "NDB":
            self._monitoring = self.device.alim_status == NETATMO_ALIM_STATUS_ONLINE
        elif self.device.monitoring is not None:
            self._monitoring = self.device.monitoring
            self._attr_is_streaming = self.device.monitoring
            self._attr_motion_detection_enabled = self.device.monitoring

        self.data_handler.events[self.device.entity_id] = self.process_events(
            self.device.events
        )

        self.async_write_ha_state()

    def process_events(self, event_list: list[NaEvent]) -> dict[str, dict[str, Any]]:
        """Return a detached snapshot of the camera's events.

        [hardened-fork] Delegates to ``helper.build_event_index``, which never
        mutates the pyatmo Event objects and keys on the event id rather than
        its timestamp. See that function for the two defects this fixes
        (C-2, C-19).
        """
        return build_event_index(event_list, self.get_video_url)

    def get_video_url(self, video_id: str) -> str:
        """Get video url."""
        if self.device.is_local:
            return (
                f"{self.device.local_url}/vod/{video_id}"
                f"/files/{self._quality}/index.m3u8"
            )
        return f"{self.device.vpn_url}/vod/{video_id}/files/{self._quality}/index.m3u8"

    def fetch_person_ids(self, persons: list[str | None]) -> list[str]:
        """Fetch matching person ids for given list of persons."""
        person_ids = []
        person_id_errors = []

        for person in persons:
            person_id = None
            for pid, data in self.home.persons.items():
                if data.pseudo == person:
                    person_ids.append(pid)
                    person_id = pid
                    break

            if person_id is None:
                person_id_errors.append(person)

        if person_id_errors:
            raise HomeAssistantError(f"Person(s) not registered {person_id_errors}")

        return person_ids

    async def _service_set_persons_home(self, **kwargs: Any) -> None:
        """Service to change current home schedule."""
        persons = kwargs.get(ATTR_PERSONS, [])
        person_ids = self.fetch_person_ids(persons)

        await self.home.async_set_persons_home(person_ids=person_ids)
        _LOGGER.debug("Set %s as at home", persons)

    async def _service_set_person_away(self, **kwargs: Any) -> None:
        """Service to mark a person as away or set the home as empty."""
        person = kwargs.get(ATTR_PERSON)
        person_ids = self.fetch_person_ids([person] if person else [])
        person_id = next(iter(person_ids), None)

        await self.home.async_set_persons_away(
            person_id=person_id,
        )

        if person_id:
            _LOGGER.debug("Set %s as away %s", person, person_id)
        else:
            _LOGGER.debug("Set home as empty")

    async def _service_set_camera_light(self, **kwargs: Any) -> None:
        """Service to set light mode."""
        if not isinstance(self.device, NaModules.netatmo.NOC):
            raise HomeAssistantError(
                f"{self.device_type} <{self.device.name}> does not have a floodlight"
            )

        mode = str(kwargs.get(ATTR_CAMERA_LIGHT_MODE))
        _LOGGER.debug("Turn %s camera light for '%s'", mode, self._attr_name)
        await self.async_command(
            self.device.async_set_floodlight_state(mode), f"set floodlight {mode}"
        )
