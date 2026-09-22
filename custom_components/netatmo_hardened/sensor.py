"""Support for the Netatmo sensors."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from time import time
from typing import Any, Final, cast, override

import pyatmo
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    DEGREE,
    PERCENTAGE,
    EntityCategory,
    EntityStateAttribute,
    UnitOfPower,
    UnitOfPrecipitationDepth,
    UnitOfPressure,
    UnitOfRatio,
    UnitOfSoundPressure,
    UnitOfSpeed,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util
from pyatmo.modules import PublicWeatherArea
from pyatmo.modules.device_types import DeviceCategory as NetatmoDeviceCategory

from .const import (
    CONF_URL_CONTROL,
    CONF_URL_ENERGY,
    CONF_URL_PUBLIC_WEATHER,
    CONF_URL_SECURITY,
    CONF_URL_WEATHER,
    CONF_WEATHER_AREAS,
    DOMAIN,
    MANUFACTURER,
    NETATMO_CREATE_CLIMATE_BATTERY_SENSOR,
    NETATMO_CREATE_LEGACY_SENSOR,
    NETATMO_CREATE_ROOM_SENSOR,
    NETATMO_CREATE_SENSOR,
    NETATMO_CREATE_WEATHER_SENSOR,
    SIGNAL_NAME,
)
from .coordinator import (
    HOME,
    PUBLIC,
    NetatmoConfigEntry,
    NetatmoDataHandler,
    NetatmoDevice,
    NetatmoRoom,
)
from .entity import (
    NetatmoBaseEntity,
    NetatmoDeviceEntity,
    NetatmoModuleEntity,
    NetatmoRoomEntity,
    NetatmoWeatherModuleEntity,
    room_device_info,
)
from .helper import NetatmoArea
from .telemetry import (
    ERROR_TYPES,
    FAILURE_RATIO_WINDOW_SECONDS,
    TelemetrySnapshot,
)

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


DIRECTION_OPTIONS = [
    "n",
    "ne",
    "e",
    "se",
    "s",
    "sw",
    "w",
    "nw",
]


def process_health(health: StateType) -> str | None:
    """Process health index and return string for display."""
    if not isinstance(health, int):
        return None
    return {
        0: "healthy",
        1: "fine",
        2: "fair",
        3: "poor",
    }.get(health, "unhealthy")


def process_rf(strength: StateType) -> str | None:
    """Process wifi signal strength and return string for display."""
    if not isinstance(strength, int):
        return None
    if strength >= 90:
        return "Low"
    if strength >= 76:
        return "Medium"
    if strength >= 60:
        return "High"
    return "Full"


def process_wifi(strength: StateType) -> str | None:
    """Process wifi signal strength and return string for display."""
    if not isinstance(strength, int):
        return None
    if strength >= 86:
        return "Low"
    if strength >= 71:
        return "Medium"
    if strength >= 56:
        return "High"
    return "Full"


@dataclass(frozen=True, kw_only=True)
class NetatmoSensorEntityDescription(SensorEntityDescription):
    """Describes Netatmo sensor entity."""

    # For legacy sensors netatmo_name is set and is used as
    # the translation_key! Legacy sensors are: weather,
    # climate, switch and meter sensors, as they were the
    # first ones implemented. For new sensors,
    # translation_key should be set explicitly on key and
    # netatmo_name should be used only to retrieve the value
    # from the device. If the netatmo_name is not set, the
    # key is used to retrieve the value from the device.
    netatmo_name: str | None = None
    # Mark sensors whose last known native_value may be
    # retained when fresh data is unavailable. This is
    # intended for sensors where the last reported value
    # remains useful, such as battery level or a last known
    # state. This flag does not by itself keep the entity
    # available; the entity may still become unavailable
    # when the device is unreachable.
    is_sticky: bool | None = None
    value_fn: Callable[[StateType], StateType] = lambda x: x


NETATMO_WEATHER_SENSOR_DESCRIPTIONS: Final[list[NetatmoSensorEntityDescription]] = [
    NetatmoSensorEntityDescription(
        key="temperature",
        netatmo_name="temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.TEMPERATURE,
        suggested_display_precision=1,
    ),
    NetatmoSensorEntityDescription(
        key="temp_trend",
        netatmo_name="temp_trend",
        entity_registry_enabled_default=False,
    ),
    NetatmoSensorEntityDescription(
        key="co2",
        netatmo_name="co2",
        native_unit_of_measurement=UnitOfRatio.PARTS_PER_MILLION,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.CO2,
    ),
    NetatmoSensorEntityDescription(
        key="pressure",
        netatmo_name="pressure",
        native_unit_of_measurement=UnitOfPressure.MBAR,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.ATMOSPHERIC_PRESSURE,
        suggested_display_precision=1,
    ),
    NetatmoSensorEntityDescription(
        key="pressure_trend",
        netatmo_name="pressure_trend",
        entity_registry_enabled_default=False,
    ),
    NetatmoSensorEntityDescription(
        key="noise",
        netatmo_name="noise",
        native_unit_of_measurement=UnitOfSoundPressure.DECIBEL,
        device_class=SensorDeviceClass.SOUND_PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    NetatmoSensorEntityDescription(
        key="humidity",
        netatmo_name="humidity",
        native_unit_of_measurement=UnitOfRatio.PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.HUMIDITY,
    ),
    NetatmoSensorEntityDescription(
        key="rain",
        netatmo_name="rain",
        native_unit_of_measurement=UnitOfPrecipitationDepth.MILLIMETERS,
        device_class=SensorDeviceClass.PRECIPITATION,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    NetatmoSensorEntityDescription(
        key="sum_rain_1",
        netatmo_name="sum_rain_1",
        entity_registry_enabled_default=False,
        native_unit_of_measurement=UnitOfPrecipitationDepth.MILLIMETERS,
        device_class=SensorDeviceClass.PRECIPITATION,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=1,
    ),
    NetatmoSensorEntityDescription(
        key="sum_rain_24",
        netatmo_name="sum_rain_24",
        native_unit_of_measurement=UnitOfPrecipitationDepth.MILLIMETERS,
        device_class=SensorDeviceClass.PRECIPITATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    NetatmoSensorEntityDescription(
        key="battery_percent",
        netatmo_name="battery",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=UnitOfRatio.PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.BATTERY,
    ),
    NetatmoSensorEntityDescription(
        key="windangle",
        netatmo_name="wind_direction",
        device_class=SensorDeviceClass.ENUM,
        options=DIRECTION_OPTIONS,
        value_fn=lambda x: x.lower() if isinstance(x, str) else None,
    ),
    NetatmoSensorEntityDescription(
        key="windangle_value",
        netatmo_name="wind_angle",
        entity_registry_enabled_default=False,
        native_unit_of_measurement=DEGREE,
        state_class=SensorStateClass.MEASUREMENT_ANGLE,
        device_class=SensorDeviceClass.WIND_DIRECTION,
    ),
    NetatmoSensorEntityDescription(
        key="windstrength",
        netatmo_name="wind_strength",
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        device_class=SensorDeviceClass.WIND_SPEED,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    NetatmoSensorEntityDescription(
        key="gustangle",
        netatmo_name="gust_direction",
        entity_registry_enabled_default=False,
        device_class=SensorDeviceClass.ENUM,
        options=DIRECTION_OPTIONS,
        value_fn=lambda x: x.lower() if isinstance(x, str) else None,
    ),
    NetatmoSensorEntityDescription(
        key="gustangle_value",
        netatmo_name="gust_angle",
        entity_registry_enabled_default=False,
        native_unit_of_measurement=DEGREE,
        state_class=SensorStateClass.MEASUREMENT_ANGLE,
        device_class=SensorDeviceClass.WIND_DIRECTION,
    ),
    NetatmoSensorEntityDescription(
        key="guststrength",
        netatmo_name="gust_strength",
        entity_registry_enabled_default=False,
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        device_class=SensorDeviceClass.WIND_SPEED,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    NetatmoSensorEntityDescription(
        key="reachable",
        netatmo_name="reachable",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    NetatmoSensorEntityDescription(
        key="rf_status",
        netatmo_name="rf_strength",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=process_rf,
    ),
    NetatmoSensorEntityDescription(
        key="wifi_status",
        netatmo_name="wifi_strength",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=process_wifi,
    ),
    NetatmoSensorEntityDescription(
        key="health_idx",
        netatmo_name="health_idx",
        device_class=SensorDeviceClass.ENUM,
        options=["healthy", "fine", "fair", "poor", "unhealthy"],
        value_fn=process_health,
    ),
    NetatmoSensorEntityDescription(
        key="power",
        netatmo_name="power",
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.POWER,
    ),
]


@dataclass(frozen=True, kw_only=True)
class NetatmoPublicWeatherSensorEntityDescription(SensorEntityDescription):
    """Describes Netatmo sensor entity."""

    value_fn: Callable[[PublicWeatherArea], dict[str, Any]]


PUBLIC_WEATHER_STATION_TYPES: tuple[
    NetatmoPublicWeatherSensorEntityDescription, ...
] = (
    NetatmoPublicWeatherSensorEntityDescription(
        key="temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.TEMPERATURE,
        suggested_display_precision=1,
        value_fn=lambda area: area.get_latest_temperatures(),
    ),
    NetatmoPublicWeatherSensorEntityDescription(
        key="pressure",
        native_unit_of_measurement=UnitOfPressure.MBAR,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.ATMOSPHERIC_PRESSURE,
        suggested_display_precision=1,
        value_fn=lambda area: area.get_latest_pressures(),
    ),
    NetatmoPublicWeatherSensorEntityDescription(
        key="humidity",
        native_unit_of_measurement=UnitOfRatio.PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.HUMIDITY,
        value_fn=lambda area: area.get_latest_humidities(),
    ),
    NetatmoPublicWeatherSensorEntityDescription(
        key="rain",
        native_unit_of_measurement=UnitOfPrecipitationDepth.MILLIMETERS,
        device_class=SensorDeviceClass.PRECIPITATION,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda area: area.get_latest_rain(),
    ),
    NetatmoPublicWeatherSensorEntityDescription(
        key="sum_rain_1",
        translation_key="sum_rain_1",
        entity_registry_enabled_default=False,
        native_unit_of_measurement=UnitOfPrecipitationDepth.MILLIMETERS,
        device_class=SensorDeviceClass.PRECIPITATION,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=1,
        value_fn=lambda area: area.get_60_min_rain(),
    ),
    NetatmoPublicWeatherSensorEntityDescription(
        key="sum_rain_24",
        translation_key="sum_rain_24",
        native_unit_of_measurement=UnitOfPrecipitationDepth.MILLIMETERS,
        device_class=SensorDeviceClass.PRECIPITATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda area: area.get_24_h_rain(),
    ),
    NetatmoPublicWeatherSensorEntityDescription(
        key="windangle_value",
        entity_registry_enabled_default=False,
        native_unit_of_measurement=DEGREE,
        state_class=SensorStateClass.MEASUREMENT_ANGLE,
        device_class=SensorDeviceClass.WIND_DIRECTION,
        value_fn=lambda area: area.get_latest_wind_angles(),
    ),
    NetatmoPublicWeatherSensorEntityDescription(
        key="windstrength",
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        device_class=SensorDeviceClass.WIND_SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda area: area.get_latest_wind_strengths(),
    ),
    NetatmoPublicWeatherSensorEntityDescription(
        key="gustangle_value",
        translation_key="gust_angle",
        entity_registry_enabled_default=False,
        native_unit_of_measurement=DEGREE,
        state_class=SensorStateClass.MEASUREMENT_ANGLE,
        device_class=SensorDeviceClass.WIND_DIRECTION,
        value_fn=lambda area: area.get_latest_gust_angles(),
    ),
    NetatmoPublicWeatherSensorEntityDescription(
        key="guststrength",
        translation_key="gust_strength",
        entity_registry_enabled_default=False,
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        device_class=SensorDeviceClass.WIND_SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda area: area.get_latest_gust_strengths(),
    ),
)

NETATMO_CLIMATE_BATTERY_SENSOR_DESCRIPTIONS: Final[
    list[NetatmoSensorEntityDescription]
] = [
    NetatmoSensorEntityDescription(
        key="battery",
        netatmo_name="battery",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=UnitOfRatio.PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.BATTERY,
    )
]

NETATMO_OPENING_SENSOR_DESCRIPTIONS: Final[list[NetatmoSensorEntityDescription]] = [
    NetatmoSensorEntityDescription(
        key="battery",
        netatmo_name="battery",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=UnitOfRatio.PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.BATTERY,
        is_sticky=True,
    ),
    NetatmoSensorEntityDescription(
        key="rf_status",
        netatmo_name="rf_strength",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=process_rf,
    ),
]

DEVICE_CATEGORY_CLIMATE_BATTERY_SENSORS: Final[
    dict[NetatmoDeviceCategory, list[NetatmoSensorEntityDescription]]
] = {
    NetatmoDeviceCategory.climate: NETATMO_CLIMATE_BATTERY_SENSOR_DESCRIPTIONS,
}

DEVICE_CATEGORY_NEW_SENSORS: Final[
    dict[NetatmoDeviceCategory, list[NetatmoSensorEntityDescription]]
] = {
    NetatmoDeviceCategory.opening: NETATMO_OPENING_SENSOR_DESCRIPTIONS,
}

DEVICE_CATEGORY_WEATHER_SENSORS: Final[
    dict[NetatmoDeviceCategory, list[NetatmoSensorEntityDescription]]
] = {
    NetatmoDeviceCategory.air_care: NETATMO_WEATHER_SENSOR_DESCRIPTIONS,
    NetatmoDeviceCategory.weather: NETATMO_WEATHER_SENSOR_DESCRIPTIONS,
}

# Duplicate for meter, climate, switch  sensors for legacy reasons
# (as originally weather definitions reused - target for future simplification)
DEVICE_CATEGORY_LEGACY_SENSORS: Final[
    dict[NetatmoDeviceCategory, list[NetatmoSensorEntityDescription]]
] = {
    NetatmoDeviceCategory.meter: NETATMO_WEATHER_SENSOR_DESCRIPTIONS,
    NetatmoDeviceCategory.switch: NETATMO_WEATHER_SENSOR_DESCRIPTIONS,
    NetatmoDeviceCategory.climate: NETATMO_WEATHER_SENSOR_DESCRIPTIONS,
}

DEVICE_CATEGORY_SENSOR_URLS: Final[dict[NetatmoDeviceCategory, str]] = {
    NetatmoDeviceCategory.climate: CONF_URL_ENERGY,
    NetatmoDeviceCategory.meter: CONF_URL_ENERGY,
    NetatmoDeviceCategory.opening: CONF_URL_SECURITY,
    NetatmoDeviceCategory.switch: CONF_URL_CONTROL,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NetatmoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Netatmo sensor platform."""

    @callback
    def _create_base_sensor_entity(
        sensorClass: type[NetatmoBaseSensor],
        descriptions: dict[NetatmoDeviceCategory, list[NetatmoSensorEntityDescription]],
        netatmo_device: NetatmoDevice,
    ) -> None:
        """Create sensor entities for a Netatmo device."""

        if netatmo_device.device.device_category is None:
            return

        descriptions_to_add = descriptions.get(
            netatmo_device.device.device_category, []
        )

        entities: list[NetatmoBaseSensor] = []

        # Create sensors for module
        for description in descriptions_to_add:
            if description.netatmo_name is None:
                feature_check = description.key
            else:
                feature_check = description.netatmo_name
            if feature_check in netatmo_device.device.features:
                _LOGGER.debug(
                    'Adding key = "%s" / netatmo_name = "%s" sensor for device %s',
                    description.key,
                    description.netatmo_name,
                    netatmo_device.device.name,
                )
                entities.append(
                    sensorClass(
                        netatmo_device,
                        description,
                    )
                )

        if entities:
            async_add_entities(entities)

    sensor_subscriptions = [
        (
            NETATMO_CREATE_CLIMATE_BATTERY_SENSOR,
            NetatmoClimateBatterySensor,
            DEVICE_CATEGORY_CLIMATE_BATTERY_SENSORS,
        ),
        (
            NETATMO_CREATE_SENSOR,
            NetatmoSensor,
            DEVICE_CATEGORY_NEW_SENSORS,
        ),
        (
            NETATMO_CREATE_WEATHER_SENSOR,
            NetatmoWeatherSensor,
            DEVICE_CATEGORY_WEATHER_SENSORS,
        ),
        (
            NETATMO_CREATE_LEGACY_SENSOR,
            NetatmoLegacySensor,
            DEVICE_CATEGORY_LEGACY_SENSORS,
        ),
    ]

    for signal, sensor_class, descriptions in sensor_subscriptions:
        entry.async_on_unload(
            async_dispatcher_connect(
                hass,
                signal,
                partial(_create_base_sensor_entity, sensor_class, descriptions),
            )
        )

    @callback
    def _create_room_sensor_entity(netatmo_device: NetatmoRoom) -> None:
        if not netatmo_device.room.climate_type:
            msg = f"No climate type found for this room: {netatmo_device.room.name}"
            _LOGGER.debug(msg)
            return

        descriptions_to_add = DEVICE_CATEGORY_LEGACY_SENSORS.get(
            NetatmoDeviceCategory.climate, []
        )

        async_add_entities(
            NetatmoRoomSensor(netatmo_device, description)
            for description in descriptions_to_add
            if description.key in netatmo_device.room.features
        )

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, NETATMO_CREATE_ROOM_SENSOR, _create_room_sensor_entity
        )
    )

    device_registry = dr.async_get(hass)
    data_handler = entry.runtime_data

    async def add_public_entities(update: bool = True) -> None:
        """Retrieve Netatmo public weather entities."""
        entities = {
            device.name: device.id
            for device in dr.async_entries_for_config_entry(
                device_registry, entry.entry_id
            )
            if device.model == "Public Weather station"
        }

        new_entities: list[NetatmoPublicSensor] = []
        for area in [
            NetatmoArea(**i) for i in entry.options.get(CONF_WEATHER_AREAS, {}).values()
        ]:
            signal_name = f"{PUBLIC}-{area.uuid}"

            if area.area_name in entities:
                entities.pop(area.area_name)

                if update:
                    async_dispatcher_send(
                        hass,
                        f"netatmo-config-{area.area_name}",
                        area,
                    )
                    continue

            await data_handler.subscribe(
                PUBLIC,
                signal_name,
                None,
                lat_ne=area.lat_ne,
                lon_ne=area.lon_ne,
                lat_sw=area.lat_sw,
                lon_sw=area.lon_sw,
                area_id=str(area.uuid),
            )

            new_entities.extend(
                NetatmoPublicSensor(data_handler, area, description)
                for description in PUBLIC_WEATHER_STATION_TYPES
            )

        for device_id in entities.values():
            device_registry.async_remove_device(device_id)

        async_add_entities(new_entities)

    async_dispatcher_connect(
        hass, f"signal-{DOMAIN}-public-update-{entry.entry_id}", add_public_entities
    )

    await add_public_entities(False)

    # [hardened-fork] F-002. Added unconditionally and synchronously: these
    # describe the API connection itself, so they must exist even on an
    # account with no devices, and they must be present *before* the first
    # poll rather than created in response to one - an integration whose very
    # first call fails should still report that failure.
    async_add_entities(
        NetatmoTelemetrySensor(data_handler, description)
        for description in TELEMETRY_SENSORS
    )


class NetatmoLegacyReachableSensor(NetatmoDeviceEntity, SensorEntity):
    """Sensor mixin that goes unavailable, keeping its last value, when unreachable."""

    @callback
    def _async_set_unavailable_if_unreachable(self) -> bool:
        """Set the entity unavailable and write state when the device is unreachable.

        Returns True when the device is unreachable so callers return early.
        """
        device = cast("pyatmo.Module | pyatmo.Room", self.device)
        if device.reachable:
            return False
        if self.available:
            self._attr_available = False
        self.async_write_ha_state()
        return True


class NetatmoBaseSensor(NetatmoModuleEntity, NetatmoLegacyReachableSensor):
    """Implementation of a Netatmo sensor."""

    entity_description: NetatmoSensorEntityDescription

    def __init__(
        self,
        netatmo_device: NetatmoDevice,
        description: NetatmoSensorEntityDescription,
        **kwargs: Any,
    ) -> None:
        """Initialize the sensor."""

        # To prevent exception about missing URL we need to set it explicitly
        if netatmo_device.device.device_category is not None:
            if (
                DEVICE_CATEGORY_SENSOR_URLS.get(netatmo_device.device.device_category)
                is not None
            ):
                self._attr_configuration_url = DEVICE_CATEGORY_SENSOR_URLS[
                    netatmo_device.device.device_category
                ]

        super().__init__(netatmo_device, **kwargs)
        self.entity_description = description

    # Legacy value retrieval for weather, climate, switch
    # and meter sensors to prevent breaking changes, as they
    # were the first ones implemented.
    @callback
    @override
    def async_update_callback(self) -> None:
        """Update the entity's state (the legacy way)."""
        # Keep the last known value for these legacy sensors when the device is
        # unreachable to preserve the historical behavior expected by existing entities.
        if self._async_set_unavailable_if_unreachable():
            return

        self._attr_available = True
        self._attr_native_value = getattr(self.device, self.entity_description.key)

        self.async_write_ha_state()


class NetatmoWeatherSensor(NetatmoWeatherModuleEntity, NetatmoBaseSensor):
    """Implementation of a Netatmo weather/home coach sensor."""

    entity_description: NetatmoSensorEntityDescription

    def __init__(
        self,
        netatmo_device: NetatmoDevice,
        description: NetatmoSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(netatmo_device, description=description)
        self.entity_description = description
        self._attr_translation_key = description.netatmo_name
        self._attr_unique_id = f"{self.device.entity_id}-{description.key}"

    @property
    @override
    def available(self) -> bool:
        """Return True if entity is available."""
        return super().available and (
            self.device.reachable
            or getattr(
                self.device,
                self.entity_description.netatmo_name or self.entity_description.key,
            )
            is not None
        )

    @callback
    @override
    def async_update_callback(self) -> None:
        """Update the entity's state."""
        value = cast(
            StateType,
            getattr(
                self.device,
                self.entity_description.netatmo_name or self.entity_description.key,
            ),
        )
        if value is not None:
            value = self.entity_description.value_fn(value)
        self._attr_native_value = value
        self.async_write_ha_state()


class NetatmoLegacySensor(NetatmoBaseSensor):
    """Implementation of a Netatmo legacy sensor."""

    # Legacy sensors are sensors that were implemented
    # before the refactor (like climate, meter and switch)
    # and that still use the old way (weather style) of
    # retrieving values from the device,

    entity_description: NetatmoSensorEntityDescription

    def __init__(
        self,
        netatmo_device: NetatmoDevice,
        description: NetatmoSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(netatmo_device, description=description)

        self.entity_description = description

        self._publishers.extend(
            [
                {
                    "name": HOME,
                    "home_id": self.home.entity_id,
                    SIGNAL_NAME: netatmo_device.signal_name,
                },
            ]
        )

        self._attr_unique_id = (
            f"{self.device.entity_id}-{self.device.entity_id}-{description.key}"
        )


class NetatmoClimateBatterySensor(NetatmoLegacySensor):
    """Implementation of a Netatmo Climate Battery sensor."""

    entity_description: NetatmoSensorEntityDescription
    device: pyatmo.modules.NRV

    def __init__(
        self,
        netatmo_device: NetatmoDevice,
        description: NetatmoSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(netatmo_device, description=description)

        self._attr_unique_id = (
            f"{netatmo_device.parent_id}"
            f"-{self.device.entity_id}"
            f"-{self.entity_description.key}"
        )
        # This sensor lives on the room's device, so it must describe the room
        # rather than the valve it reads, or it renames the room after the valve
        self._attr_device_info = room_device_info(
            self.device.home.rooms[netatmo_device.parent_id],
            netatmo_device.data_handler.parent_device_ids[self.device.home.entity_id],
        )

    @callback
    @override
    def async_update_callback(self) -> None:
        """Update the entity's state."""
        if self._async_set_unavailable_if_unreachable():
            return

        self._attr_available = True
        self._attr_native_value = self.device.battery
        self.async_write_ha_state()


class NetatmoSensor(NetatmoBaseSensor):
    """Implementation of a Netatmo refactored sensor."""

    entity_description: NetatmoSensorEntityDescription

    def __init__(
        self,
        netatmo_device: NetatmoDevice,
        description: NetatmoSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(netatmo_device, description=description)
        self.entity_description = description
        self._attr_translation_key = description.netatmo_name
        self._attr_unique_id = f"{self.device.entity_id}-{description.key}"

        self._publishers.extend(
            [
                {
                    "name": self.home.entity_id,
                    "home_id": self.home.entity_id,
                    SIGNAL_NAME: netatmo_device.signal_name,
                },
            ]
        )

    # New sensor implementation optional netatmo_name to
    # retrieve value from device, if not set key is used.
    # Value is set unavailable if device is not reachable
    # except is_sticky, otherwise it is set to the
    # processed value
    @callback
    @override
    def async_update_callback(self) -> None:
        """Update the entity's state."""
        if not self.device.reachable:
            if self.available:
                self._attr_available = False
            if not self.entity_description.is_sticky:
                self._attr_native_value = None
        else:
            if self.entity_description.netatmo_name is None:
                raw_value = getattr(self.device, self.entity_description.key, None)
            else:
                raw_value = getattr(
                    self.device, self.entity_description.netatmo_name, None
                )

            if raw_value is not None:
                value = self.entity_description.value_fn(raw_value)
            else:
                value = None

            self._attr_available = True
            self._attr_native_value = value

        self.async_write_ha_state()


class NetatmoRoomSensor(NetatmoRoomEntity, NetatmoLegacyReachableSensor):
    """Implementation of a Netatmo room sensor."""

    entity_description: NetatmoSensorEntityDescription

    def __init__(
        self,
        netatmo_room: NetatmoRoom,
        description: NetatmoSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(netatmo_room)
        self.entity_description = description

        self._publishers.extend(
            [
                {
                    "name": HOME,
                    "home_id": self.home.entity_id,
                    SIGNAL_NAME: netatmo_room.signal_name,
                },
            ]
        )

        self._attr_unique_id = (
            f"{self.device.entity_id}-{self.device.entity_id}-{description.key}"
        )

    @callback
    @override
    def async_update_callback(self) -> None:
        """Update the entity's state."""
        if self._async_set_unavailable_if_unreachable():
            return

        self._attr_available = True
        self._attr_native_value = getattr(self.device, self.entity_description.key)

        self.async_write_ha_state()


class NetatmoPublicSensor(NetatmoBaseEntity, SensorEntity):
    """Represent a single sensor in a Netatmo."""

    entity_description: NetatmoPublicWeatherSensorEntityDescription

    def __init__(
        self,
        data_handler: NetatmoDataHandler,
        area: NetatmoArea,
        description: NetatmoPublicWeatherSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(data_handler)
        self.entity_description = description

        self._signal_name = f"{PUBLIC}-{area.uuid}"
        self._publishers.append(
            {
                "name": PUBLIC,
                "lat_ne": area.lat_ne,
                "lon_ne": area.lon_ne,
                "lat_sw": area.lat_sw,
                "lon_sw": area.lon_sw,
                "area_name": area.area_name,
                SIGNAL_NAME: self._signal_name,
            }
        )

        self._station = data_handler.account.public_weather_areas[str(area.uuid)]

        self.area = area
        self._mode = area.mode
        self._show_on_map = area.show_on_map
        self._attr_unique_id = f"{area.area_name.replace(' ', '-')}-{description.key}"

        self._attr_extra_state_attributes.update(
            {
                EntityStateAttribute.LATITUDE: (area.lat_ne + area.lat_sw) / 2,
                EntityStateAttribute.LONGITUDE: (area.lon_ne + area.lon_sw) / 2,
            }
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, area.area_name)},
            name=area.area_name,
            model="Public Weather station",
            manufacturer="Netatmo",
            configuration_url=CONF_URL_PUBLIC_WEATHER,
        )

    @override
    async def async_added_to_hass(self) -> None:
        """Entity created."""
        await super().async_added_to_hass()

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"netatmo-config-{self.area.area_name}",
                self.async_config_update_callback,
            )
        )

    async def async_config_update_callback(self, area: NetatmoArea) -> None:
        """Update the entity's config.

        [hardened-fork] ``_station`` and the map coordinates are refreshed here
        too. Upstream updated the area, the signal name, the publisher list and
        the subscription, but left ``self._station`` pointing at the station
        object for the *previous* area - and ``async_update_callback`` reads its
        values from exactly that object. The entity therefore kept reporting
        readings from the old geographic area under the new area's name, with
        every visible indicator suggesting the change had taken effect
        (defect C-3, external finding NET-012).
        """
        if self.area == area:
            return

        await self.data_handler.unsubscribe(
            self._signal_name, self.async_update_callback
        )

        self.area = area
        self._signal_name = f"{PUBLIC}-{area.uuid}"
        self._mode = area.mode
        self._show_on_map = area.show_on_map
        self._attr_extra_state_attributes.update(
            {
                EntityStateAttribute.LATITUDE: (area.lat_ne + area.lat_sw) / 2,
                EntityStateAttribute.LONGITUDE: (area.lon_ne + area.lon_sw) / 2,
            }
        )
        self._publishers = [
            {
                "name": PUBLIC,
                "lat_ne": area.lat_ne,
                "lon_ne": area.lon_ne,
                "lat_sw": area.lat_sw,
                "lon_sw": area.lon_sw,
                "area_name": area.area_name,
                SIGNAL_NAME: self._signal_name,
            }
        ]
        await self.data_handler.subscribe(
            PUBLIC,
            self._signal_name,
            self.async_update_callback,
            lat_ne=area.lat_ne,
            lon_ne=area.lon_ne,
            lat_sw=area.lat_sw,
            lon_sw=area.lon_sw,
        )

        # Re-bind to the station object the new area registered. Without this
        # the entity reads the previous area's data forever (defect C-3).
        station = self.data_handler.account.public_weather_areas.get(str(area.uuid))
        if station is None:
            _LOGGER.error(
                "No public weather station registered for area %s; the sensor "
                "will report as unavailable rather than serve stale data",
                area.area_name,
            )
            self._attr_available = False
        self._station = station

    @callback
    @override
    def async_update_callback(self) -> None:
        """Update the entity's state."""
        if self._station is None:
            self._attr_available = False
            self.async_write_ha_state()
            return

        data = self.entity_description.value_fn(self._station)

        if not data:
            if self.available:
                _LOGGER.error(
                    "No station provides %s data in the area %s",
                    self.entity_description.key,
                    self.area.area_name,
                )

            self._attr_available = False
            self.async_write_ha_state()
            return

        if values := [x for x in data.values() if x is not None]:
            if self._mode == "avg":
                self._attr_native_value = round(sum(values) / len(values), 1)
            elif self._mode == "max":
                self._attr_native_value = max(values)
            elif self._mode == "min":
                self._attr_native_value = min(values)

        self._attr_available = self.native_value is not None
        self.async_write_ha_state()


# ---------------------------------------------------------------------------
# API telemetry (feature F-002, 0.1.3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class NetatmoTelemetrySensorEntityDescription(SensorEntityDescription):
    """Describes a Netatmo API telemetry sensor.

    [hardened-fork] ``value_fn`` reads a ``TelemetrySnapshot`` rather than the
    live recorder, so the six sensors produced by one update cycle cannot
    disagree with one another.
    """

    value_fn: Callable[[TelemetrySnapshot], StateType | datetime]


def _timestamp(value: float | None) -> datetime | None:
    """Return a timezone-aware datetime, or None.

    Home Assistant requires an aware datetime for ``SensorDeviceClass.TIMESTAMP``
    and will reject a naive one. The recorder stores plain epoch seconds
    because it has no Home Assistant imports, so the conversion happens here.
    """
    if value is None:
        return None
    return dt_util.utc_from_timestamp(value)


TELEMETRY_SENSORS: Final[tuple[NetatmoTelemetrySensorEntityDescription, ...]] = (
    NetatmoTelemetrySensorEntityDescription(
        key="api_failure_ratio_1h",
        translation_key="api_failure_ratio_1h",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=1,
        # None (not 0) until there is evidence: an integration that has made
        # no calls has not achieved a 0% failure rate.
        value_fn=lambda snapshot: (
            None
            if snapshot.failure_ratio is None
            else round(snapshot.failure_ratio * 100, 2)
        ),
    ),
    NetatmoTelemetrySensorEntityDescription(
        key="api_last_error",
        translation_key="api_last_error",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda snapshot: snapshot.last_error,
    ),
    NetatmoTelemetrySensorEntityDescription(
        key="api_last_error_type",
        translation_key="api_last_error_type",
        device_class=SensorDeviceClass.ENUM,
        options=list(ERROR_TYPES),
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda snapshot: snapshot.last_error_type,
    ),
    NetatmoTelemetrySensorEntityDescription(
        key="api_last_error_time",
        translation_key="api_last_error_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda snapshot: _timestamp(snapshot.last_error_timestamp),
    ),
    NetatmoTelemetrySensorEntityDescription(
        key="api_last_success",
        translation_key="api_last_success",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda snapshot: _timestamp(snapshot.last_success_timestamp),
    ),
    NetatmoTelemetrySensorEntityDescription(
        key="api_poll_latency",
        translation_key="api_poll_latency",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=0,
        value_fn=lambda snapshot: (
            None
            if snapshot.latency_seconds is None
            else round(snapshot.latency_seconds * 1000, 1)
        ),
    ),
)


class NetatmoTelemetrySensor(NetatmoBaseEntity, SensorEntity):
    """A diagnostic sensor reporting the health of the Netatmo API itself.

    [hardened-fork] Feature F-002.

    Two properties of this class matter more than what it measures.

    **It never goes unavailable.** ``_publishers`` is left empty, so the
    inherited availability check - which ANDs the health of every subscribed
    publisher - reduces to ``all([])``, which is ``True``. That is deliberate,
    not an oversight: every other entity in this integration is unavailable
    precisely when the API is failing, and a diagnostic sensor that follows
    the same rule would report nothing at the only moment an operator looks
    at it. The whole point is to be readable during an outage.

    **It is not driven by publisher subscriptions.** Updates arrive on a
    dedicated dispatcher signal from the coordinator. Subscribing to a
    publisher would have coupled these sensors to that publisher's
    availability and reintroduced the problem above through the back door.
    """

    entity_description: NetatmoTelemetrySensorEntityDescription
    _attr_should_poll = False
    _attr_entity_registry_enabled_default = True

    def __init__(
        self,
        data_handler: NetatmoDataHandler,
        description: NetatmoTelemetrySensorEntityDescription,
    ) -> None:
        """Initialize the telemetry sensor."""
        super().__init__(data_handler)
        self.entity_description = description

        entry_id = data_handler.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}-{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}-api")},
            manufacturer=MANUFACTURER,
            name="Netatmo API",
            # A service device rather than a physical one: this represents the
            # cloud endpoint, which has no firmware, serial or hardware model
            # to report. Claiming otherwise would put fictional hardware in
            # the device registry.
            entry_type=dr.DeviceEntryType.SERVICE,
            configuration_url=CONF_URL_WEATHER,
        )

    @override
    async def async_added_to_hass(self) -> None:
        """Subscribe to telemetry updates."""
        await super().async_added_to_hass()

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"signal-{DOMAIN}-telemetry-{self.data_handler.config_entry.entry_id}",
                self.async_update_callback,
            )
        )

    @callback
    @override
    def async_update_callback(self) -> None:
        """Publish the current telemetry value."""
        snapshot = self.data_handler.telemetry.snapshot(time())
        self._attr_native_value = self.entity_description.value_fn(snapshot)

        if self.entity_description.key == "api_failure_ratio_1h":
            # Without the sample count the ratio is not interpretable: 100%
            # over two samples and 100% over two hundred are different claims.
            self._attr_extra_state_attributes = {
                "sample_count": snapshot.sample_count,
                "window_seconds": FAILURE_RATIO_WINDOW_SECONDS,
                "total_polls": snapshot.total_polls,
                "total_failures": snapshot.total_failures,
            }

        self.async_write_ha_state()
