"""Provides device automations for Netatmo."""

from __future__ import annotations

from typing import Any

import probatio
from homeassistant.components.device_automation import (
    DEVICE_TRIGGER_BASE_SCHEMA,
    InvalidDeviceAutomationConfig,
)
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.const import (
    ATTR_DEVICE_ID,
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_ENTITY_ID,
    CONF_PLATFORM,
    CONF_TYPE,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import (
    config_validation as cv,
)
from homeassistant.helpers import (
    device_registry as dr,
)
from homeassistant.helpers import (
    entity_registry as er,
)
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .climate import STATE_NETATMO_AWAY, STATE_NETATMO_HG, STATE_NETATMO_SCHEDULE
from .const import (
    CLIMATE_TRIGGERS,
    DOMAIN,
    EVENT_TYPE_THERM_MODE,
    INDOOR_CAMERA_TRIGGERS,
    NETATMO_EVENT,
    OUTDOOR_CAMERA_TRIGGERS,
)

CONF_SUBTYPE = "subtype"

DEVICES = {
    "Smart Indoor Camera": INDOOR_CAMERA_TRIGGERS,
    "Smart Outdoor Camera": OUTDOOR_CAMERA_TRIGGERS,
    "Smart Thermostat": CLIMATE_TRIGGERS,
    "Smart Valve": CLIMATE_TRIGGERS,
}

SUBTYPES = {
    EVENT_TYPE_THERM_MODE: [
        STATE_NETATMO_SCHEDULE,
        STATE_NETATMO_HG,
        STATE_NETATMO_AWAY,
    ]
}

# Where a subtype's value actually lives inside the emitted event payload.
# webhook.async_send_event fires {"type": <event>, "data": <netatmo payload>},
# and for therm_mode the Netatmo payload nests the mode under "home".
SUBTYPE_PAYLOAD_PATH = {EVENT_TYPE_THERM_MODE: "home"}
SUBTYPE_PAYLOAD_KEY = {EVENT_TYPE_THERM_MODE: EVENT_TYPE_THERM_MODE}

TRIGGER_TYPES = OUTDOOR_CAMERA_TRIGGERS + INDOOR_CAMERA_TRIGGERS + CLIMATE_TRIGGERS

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {
        probatio.Required(CONF_ENTITY_ID): cv.entity_id_or_uuid,
        probatio.Required(CONF_TYPE): probatio.In(TRIGGER_TYPES),
        probatio.Optional(CONF_SUBTYPE): str,
    }
)


async def async_validate_trigger_config(
    hass: HomeAssistant, config: ConfigType
) -> ConfigType:
    """Validate config."""
    config = TRIGGER_SCHEMA(config)

    device_registry = dr.async_get(hass)
    device = device_registry.async_get(
        config[CONF_DEVICE_ID], include_child_devices=False
    )

    if not device or device.model is None:
        raise InvalidDeviceAutomationConfig(
            f"Trigger invalid, device with ID {config[CONF_DEVICE_ID]} not found"
        )

    trigger = config[CONF_TYPE]

    if (
        not device
        or device.model not in DEVICES
        or trigger not in DEVICES[device.model]
    ):
        raise InvalidDeviceAutomationConfig(f"Unsupported model {device.model}")

    return config


async def async_get_triggers(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, str]]:
    """List device triggers for Netatmo devices."""
    registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    triggers: list[dict[str, str]] = []

    for entry in er.async_entries_for_device(registry, device_id):
        if (
            device := device_registry.async_get(device_id, include_child_devices=False)
        ) is None or device.model is None:
            continue

        for trigger in DEVICES.get(device.model, []):
            if trigger in SUBTYPES:
                triggers.extend(
                    {
                        CONF_PLATFORM: "device",
                        CONF_DEVICE_ID: device_id,
                        CONF_DOMAIN: DOMAIN,
                        CONF_ENTITY_ID: entry.id,
                        CONF_TYPE: trigger,
                        CONF_SUBTYPE: subtype,
                    }
                    for subtype in SUBTYPES[trigger]
                )
            else:
                triggers.append(
                    {
                        CONF_PLATFORM: "device",
                        CONF_DEVICE_ID: device_id,
                        CONF_DOMAIN: DOMAIN,
                        CONF_ENTITY_ID: entry.id,
                        CONF_TYPE: trigger,
                    }
                )

    return triggers


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Attach a trigger."""
    device_registry = dr.async_get(hass)
    device = device_registry.async_get(
        config[CONF_DEVICE_ID], include_child_devices=False
    )

    if not device:
        return lambda: None

    if device.model not in DEVICES:
        return lambda: None

    # [hardened-fork] Build one coherent filter (defect C-5, external finding
    # NET-004).
    #
    # Upstream *replaced* the whole event-data filter when a subtype was
    # configured, which broke the trigger in both directions at once:
    #
    #   * it discarded the event type and the device id, so a "thermostat mode
    #     changed to away" automation fired for every Netatmo device in every
    #     home, not just the selected one; and
    #   * it matched on ``data.mode``, but the therm_mode event this integration
    #     emits carries the mode at ``data.home.therm_mode``, so in practice it
    #     matched nothing and the automation silently never ran.
    #
    # An automation that looks correct in the UI and never fires is the worst
    # failure mode available here, so the subtype is now an additional
    # constraint on top of the identity constraints rather than a replacement
    # for them.
    event_data: dict[str, Any] = {
        "type": config[CONF_TYPE],
        ATTR_DEVICE_ID: config[ATTR_DEVICE_ID],
    }

    if config[CONF_TYPE] in SUBTYPES and CONF_SUBTYPE in config:
        event_data["data"] = {
            SUBTYPE_PAYLOAD_PATH[config[CONF_TYPE]]: {
                SUBTYPE_PAYLOAD_KEY[config[CONF_TYPE]]: config[CONF_SUBTYPE]
            }
        }

    event_config = {
        event_trigger.CONF_PLATFORM: "event",
        event_trigger.CONF_EVENT_TYPE: NETATMO_EVENT,
        event_trigger.CONF_EVENT_DATA: event_data,
    }

    event_config = event_trigger.TRIGGER_SCHEMA(event_config)
    return await event_trigger.async_attach_trigger(
        hass, event_config, action, trigger_info, platform_type="device"
    )
