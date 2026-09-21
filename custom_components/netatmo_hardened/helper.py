"""Helpers for the Netatmo integration.

[hardened-fork] The pure functions in this module carry no Home Assistant or
pyatmo imports so they can be unit-tested without a Home Assistant runtime
(``tests/unit/``). The control-path arithmetic and the camera event snapshot
both live here for that reason: they are the two places where a silent,
untested defect changed what the integration did rather than merely how it
logged it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from pyatmo.modules.device_types import DeviceType as NetatmoDeviceType


def device_type_to_str(device_type: NetatmoDeviceType) -> str:
    """Convert a device type to a string.

    Used to generate backwards compatible unique ids.
    """
    return f"{type(device_type).__name__}.{device_type}"


def end_timestamp_for_period(now_timestamp: float, time_period: timedelta) -> int:
    """Return the absolute end timestamp for a manual temperature override.

    Uses ``total_seconds()``. Upstream used ``timedelta.seconds``, which is the
    seconds component *after whole days have been removed* - so every override
    of 24 hours or more was silently shortened before it was sent to Netatmo:

        timedelta(days=1, hours=2).seconds       ->    7200  (2 hours)
        timedelta(days=1, hours=2).total_seconds -> 93600    (26 hours)

    The service accepted one command and the API received a different one, with
    no error anywhere. That is a control-integrity defect, not a rounding bug.

    Addresses defect C-1 (external finding NET-003).
    """
    return int(now_timestamp + time_period.total_seconds())


def _subevent_mappings(subevents: Any) -> list[dict[str, Any]]:
    """Return subevents as plain dictionaries, whatever form they arrive in.

    Tolerates both pyatmo ``Event`` objects (first pass over fresh API data)
    and dictionaries (anything already converted), because the same event set
    is processed on every poll.
    """
    if not isinstance(subevents, Iterable) or isinstance(subevents, (str, bytes)):
        return []

    converted: list[dict[str, Any]] = []
    for subevent in subevents:
        if isinstance(subevent, Mapping):
            converted.append(dict(subevent))
        elif hasattr(subevent, "__dict__"):
            converted.append(dict(vars(subevent)))
    return converted


def build_event_index(
    events: Iterable[Any],
    media_url_for: Callable[[str], str],
) -> dict[str, dict[str, Any]]:
    """Return a snapshot of camera events keyed by the event's own identifier.

    Two upstream defects are fixed here, and both were silent.

    1. Upstream took ``event.__dict__`` - the live attribute dictionary of the
       pyatmo ``Event`` - and wrote ``subevents`` and ``media_url`` straight
       back into it. That mutated library state owned by another component, and
       it was not idempotent: the first pass replaced each ``Event`` subevent
       with its ``__dict__``, and the second pass filtered on
       ``if not isinstance(event, dict)``, so on the *next* poll every subevent
       was discarded. Sub-event history disappeared after one update cycle and
       never came back. This builds a detached copy and never touches the
       source object (defect C-2, external finding NET-030).

    2. Upstream keyed the result on ``event.event_time``. Cameras legitimately
       emit several events within the same second - a person detection and a
       movement, say - and the later one silently overwrote the earlier. Keying
       on the event's own id keeps both (defect C-19, external finding NET-013).

    Event ids are returned as strings so that media-source identifiers round
    trip through a URL path without an ``int()`` conversion that could raise
    (defect C-20).
    """
    index: dict[str, dict[str, Any]] = {}

    for event in events:
        video_id = getattr(event, "video_id", None)
        if not video_id:
            continue

        data = dict(vars(event)) if hasattr(event, "__dict__") else {}
        data["subevents"] = _subevent_mappings(data.get("subevents"))
        data["media_url"] = media_url_for(video_id)

        key = getattr(event, "entity_id", None) or data.get("entity_id")
        if key is None:
            # No stable id: fall back to the timestamp, but keep both events by
            # suffixing collisions rather than overwriting.
            key = str(data.get("event_time", len(index)))
            if key in index:
                key = f"{key}-{len(index)}"
        index[str(key)] = data

    return index


def normalise_coordinate(value: float, minimum_precision: int = 7) -> float:
    """Return a coordinate the Netatmo API accepts, without string parsing.

    The API rejects coordinates carrying too few decimal places, so upstream
    nudged them by 1e-7. It decided whether to nudge with
    ``len(str(value).split(".")[1])``, which raises ``IndexError`` whenever
    Python renders the float in scientific notation - true for any magnitude
    below 1e-4, i.e. any location within roughly 11 metres of the equator or
    the prime meridian. The options flow crashed rather than saving the area.

    This compares the value against its own rounding instead, which has no
    string representation dependency at all.

    Addresses defect C-21 (external finding SEC-009).
    """
    if round(value, minimum_precision - 1) == value:
        return value + 10**-minimum_precision
    return value


@dataclass
class NetatmoArea:
    """Class for keeping track of an area."""

    area_name: str
    lat_ne: float
    lon_ne: float
    lat_sw: float
    lon_sw: float
    mode: str
    show_on_map: bool
    uuid: UUID = field(default_factory=uuid4)
