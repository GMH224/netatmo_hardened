"""Device registry helpers for the Netatmo integration."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING

import pyatmo
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from pyatmo.modules.device_types import DEVICE_DESCRIPTION_MAP

from .const import CONF_URL_CONTROL, DOMAIN, MANUFACTURER

_LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .coordinator import NetatmoConfigEntry


def _bridged_children(home: pyatmo.Home) -> Iterator[tuple[str, str]]:
    """Yield (module id, parent id) for every child a module lists as bridged."""
    return (
        (child_id, module.entity_id)
        for module in home.modules.values()
        for child_id in module.modules or ()
        if child_id in home.modules and child_id != module.entity_id
    )


def _declared_bridges(home: pyatmo.Home) -> Iterator[tuple[str, str]]:
    """Yield (module id, parent id) for every module that names its bridge."""
    return (
        (module.entity_id, bridge)
        for module in home.modules.values()
        if (bridge := module.bridge)
        and bridge in home.modules
        and bridge != module.entity_id
    )


def netatmo_module_parents(account: pyatmo.AsyncAccount) -> dict[str, str]:
    """Map each module id to the id of the module it reports through.

    The API records the relationship from both ends and neither end is
    complete: a station lists bridged children that never name it back, and a
    module can name a bridge that does not list it. `bridge` is single-valued,
    so it wins wherever the two disagree.
    """
    parents: dict[str, str] = {}
    for home in account.homes.values():
        for child_id, parent_id in _bridged_children(home):
            parents.setdefault(child_id, parent_id)
        parents.update(_declared_bridges(home))

    return parents


def _register_bridge(
    device_registry: dr.DeviceRegistry,
    entry: NetatmoConfigEntry,
    home: pyatmo.Home,
    module_id: str,
    module_parents: dict[str, str],
    parent_device_ids: dict[str, str],
    seen: set[str],
) -> str:
    """Register a bridging module after its own bridge and return its device id."""
    if (device_id := parent_device_ids.get(module_id)) is not None:
        return device_id

    seen.add(module_id)
    via_device_id = parent_device_ids[home.entity_id]
    parent_id = module_parents.get(module_id)
    # `seen` bounds the walk; a cycle in unvalidated API data would not terminate
    if parent_id is not None and parent_id not in seen:
        via_device_id = _register_bridge(
            device_registry,
            entry,
            home,
            parent_id,
            module_parents,
            parent_device_ids,
            seen,
        )

    module = home.modules[module_id]
    manufacturer, model = DEVICE_DESCRIPTION_MAP.get(
        module.device_type, (MANUFACTURER, module.device_type.value)
    )
    device_entry = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, module_id)},
        manufacturer=manufacturer,
        model=model,
        name=module.name,
        via_device_id=via_device_id,
    )
    parent_device_ids[module_id] = device_entry.id
    return device_entry.id


@callback
def async_disabled_netatmo_ids(
    hass: HomeAssistant, entry: NetatmoConfigEntry
) -> list[str]:
    """Return the Netatmo ids of every disabled device.

    A superset of the disabled home ids. Module ids never match a home id, so
    passing them through to pyatmo's denylist is harmless and avoids having to
    know which devices are homes before the topology has been fetched.
    """
    disabled = [
        identifier[1]
        for device in dr.async_entries_for_config_entry(
            dr.async_get(hass), entry.entry_id
        )
        if device.disabled_by
        for identifier in device.identifiers
        if identifier[0] == DOMAIN
    ]

    # [hardened-fork] Say so. The upstream complaint about this behaviour
    # (home-assistant/core#181448) was not that disabling a home suppresses it -
    # that is intended - but that it happened in total silence, leaving the
    # integration looking loaded with no entities and no explanation. One log
    # line makes it diagnosable in seconds (external finding NET-001, residual).
    if disabled:
        _LOGGER.info(
            "%s Netatmo device(s) are disabled and will be excluded from "
            "polling; any of these that are homes will contribute no entities",
            len(disabled),
        )

    return disabled


@callback
def async_register_parent_devices(
    hass: HomeAssistant,
    entry: NetatmoConfigEntry,
    account: pyatmo.AsyncAccount,
    module_parents: dict[str, str],
) -> dict[str, str]:
    """Register a device per home and per bridging module.

    Maps Netatmo ids to device registry ids.
    """
    device_registry = dr.async_get(hass)
    parent_device_ids: dict[str, str] = {}

    for home_id, home_name in account.all_home_names.items():
        device_entry = device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, home_id)},
            manufacturer=MANUFACTURER,
            model="Home",
            name=home_name,
            configuration_url=CONF_URL_CONTROL,
        )
        parent_device_ids[home_id] = device_entry.id

    for home in account.homes.values():
        bridges = {
            module_parents[module_id]
            for module_id in home.modules
            if module_id in module_parents
        }
        for module_id in home.modules:
            if module_id in bridges:
                _register_bridge(
                    device_registry,
                    entry,
                    home,
                    module_id,
                    module_parents,
                    parent_device_ids,
                    set(),
                )

    return parent_device_ids


@callback
def async_sync_home_disabled_state(
    hass: HomeAssistant, entry: NetatmoConfigEntry, home_device_ids: Iterable[str]
) -> None:
    """Mirror each home device's disabled state onto its descendants.

    Devices disabled by the user are left alone in both directions, so toggling a
    home never undoes a manual choice.
    """
    device_registry = dr.async_get(hass)

    children: dict[str, list[dr.DeviceEntry]] = {}
    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        if device.via_device_id:
            children.setdefault(device.via_device_id, []).append(device)

    for home_device_id in home_device_ids:
        # [hardened-fork] The registry entry can legitimately be gone by now -
        # a device removed in another task between building the id list and
        # walking it. An assertion turned that race into a hard failure of the
        # whole setup (defect C-16).
        home_device = device_registry.async_get(home_device_id)
        if home_device is None:
            continue

        disabled = home_device.disabled_by is not None
        # Walk the whole subtree; a module can be a grandchild via its gateway
        stack = list(children.get(home_device_id, []))
        while stack:
            device = stack.pop()
            stack.extend(children.get(device.id, []))

            if disabled and device.disabled_by is None:
                device_registry.async_update_device(
                    device.id, disabled_by=dr.DeviceEntryDisabler.INTEGRATION
                )
            elif (
                not disabled
                and device.disabled_by is dr.DeviceEntryDisabler.INTEGRATION
            ):
                device_registry.async_update_device(device.id, disabled_by=None)
