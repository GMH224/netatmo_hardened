"""Validated ingress boundary for externally supplied Netatmo webhook data.

[hardened-fork] Home Assistant's webhook route authenticates on possession of
the webhook id alone, so the request body is attacker-controlled the moment
that id leaks. Upstream indexed the decoded JSON directly, which turned any
malformed, truncated or wrong-typed payload into an unhandled exception inside
the event loop, and let a payload with no ``event_type`` reach the coordinator
as the string ``"None"``.

Everything crossing that boundary is normalised here exactly once, so the
entity platforms downstream never re-parse a raw dictionary.

This module deliberately imports neither Home Assistant nor pyatmo. It is pure
data validation, which means:

* it can be unit-tested on any Python 3.11+ interpreter with no Home Assistant
  runtime (``tests/unit/test_event_validation.py``), and
* the ingress rules can be reviewed in isolation from integration plumbing,
  which is the property an ICS review actually needs.

Addresses defects C-8, C-9, C-18 and external findings SEC-002 through SEC-006.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

# Upper bound on how many members of an inbound collection are processed.
# Netatmo never sends anything near this; the cap exists so a hostile or
# malfunctioning sender cannot make the event loop walk an unbounded list
# (external finding SEC-007, defence in depth).
MAX_COLLECTION_ITEMS: Final = 256

# Maximum accepted length of an identifier-shaped string field. Netatmo ids are
# MAC addresses or 24-character hex strings; anything longer is malformed.
MAX_IDENTIFIER_LENGTH: Final = 128

# C0 controls, DEL, and the C1 range. Rejected in identifiers so that an
# externally supplied value cannot forge or corrupt a log line (defect E-009).
CONTROL_CHARACTERS: Final = frozenset(
    chr(code) for code in [*range(0x00, 0x20), 0x7F, *range(0x80, 0xA0)]
)

# The key naming an event's type in a Netatmo payload.
EVENT_TYPE_KEY: Final = "event_type"

# Fields that establish *which* home, device, room or module an event concerns.
# These are taken from the authenticated parent envelope and may never be
# supplied or altered by a nested member (defect E-003).
PROTECTED_IDENTITY_FIELDS: Final = (
    "home_id",
    "device_id",
    "camera_id",
    "room_id",
    "module_id",
    "user_id",
)


def as_mapping(payload: Any) -> dict[str, Any] | None:
    """Return the payload when it is a JSON object, otherwise ``None``.

    ``json.loads`` happily returns a list, a string, a number, a bool or
    ``None`` for syntactically valid JSON. Upstream then called ``.get()`` on
    it and raised ``AttributeError``. Callers treat ``None`` here as "drop this
    request", never as an empty event.
    """
    if isinstance(payload, dict):
        return payload
    return None


def mapping_members(
    value: Any, limit: int = MAX_COLLECTION_ITEMS
) -> list[dict[str, Any]]:
    """Return the mapping members of a JSON array, bounded by ``limit``.

    Netatmo sends ``persons`` as an array of objects. A payload that sends a
    bare object, or an array containing strings, integers or ``null``, used to
    reach ``person.get(...)`` and raise. Non-mapping members are dropped
    individually so one bad member does not discard the whole event.
    """
    if not isinstance(value, list):
        return []
    return [member for member in value[:limit] if isinstance(member, dict)]


def identifier(data: Mapping[str, Any], key: str) -> str | None:
    """Return ``data[key]`` when it is a usable identifier string, else ``None``.

    Rejects missing keys, non-strings, empty strings, absurd lengths and any
    value containing control characters.

    [hardened-fork] The control-character rule closes defect E-009. An
    identifier-shaped value reaches the debug log through
    :func:`redacted_summary`, and a value carrying CR, LF or terminal escape
    sequences can forge or corrupt log lines - which matters precisely because
    these logs are the forensic record for an externally reachable endpoint.
    No legitimate Netatmo identifier or event name contains one.
    """
    value = data.get(key)
    if not isinstance(value, str):
        return None
    if not value or len(value) > MAX_IDENTIFIER_LENGTH:
        return None
    if any(ch in CONTROL_CHARACTERS for ch in value):
        return None
    return value


def merge_subevent(
    parent: Mapping[str, Any],
    subevent: Mapping[str, Any],
    collection_key: str,
) -> dict[str, Any]:
    """Combine a nested sub-event with its parent, keeping parent identity.

    Netatmo omits the home and device identifiers on nested sub-event objects,
    so they must be inherited from the parent envelope. The obvious way to do
    that - ``{**parent, **subevent}`` - inherits them but also lets the nested
    member *overwrite* them, which is the wrong direction across a trust
    boundary (defect E-003).

    A payload such as::

        {"event_type": "outdoor", "home_id": "A", "device_id": "CAM-A",
         "subevents": [{"type": "human", "home_id": "B", "device_id": "CAM-B"}]}

    would emit an event attributed to ``CAM-B``. Since the resulting device id
    becomes the Home Assistant event's ``device_id``, a nested member could
    steer an event at a device it does not belong to, and any automation keyed
    on that device would act on it. Validation alone does not catch this: a
    substituted identifier that happens to name another real device passes
    every shape and existence check.

    Identity therefore comes from the parent and only from the parent. The
    sub-event contributes its own descriptive fields, plus its ``type``, which
    names the event - this is how an ``outdoor`` envelope produces the
    ``human`` / ``animal`` / ``vehicle`` events that the outdoor camera's
    device triggers listen for.
    """
    merged: dict[str, Any] = {**parent, **subevent}

    # Parent identity is immutable for nested processing.
    for key in PROTECTED_IDENTITY_FIELDS:
        if key in parent:
            merged[key] = parent[key]
        else:
            merged.pop(key, None)

    # The collection itself must not travel inside its own members.
    merged.pop(collection_key, None)

    # A sub-event's own `type` names the event it becomes.
    if (nested_type := identifier(subevent, "type")) is not None:
        merged[EVENT_TYPE_KEY] = nested_type

    return merged


def event_type(data: Mapping[str, Any], key: str) -> str | None:
    """Return the payload's event type when present and well-formed.

    Returns ``None`` rather than the string ``"None"`` that upstream used as a
    default. That default was not inert: it produced a dispatcher signal named
    ``signal-netatmo-webhook-None``, which the coordinator subscribes to for
    genuine webhook activation notices, so an event with no type reached the
    coordinator's ``event["data"]["push_type"]`` lookup and raised ``KeyError``.
    """
    return identifier(data, key)


def has_required(data: Mapping[str, Any], *keys: str) -> bool:
    """Return whether every named key holds a usable identifier string."""
    return all(identifier(data, key) is not None for key in keys)


def device_id_for_event(
    data: Mapping[str, Any],
    event_name: str,
    event_id_map: Mapping[str, str],
) -> str | None:
    """Return the Netatmo id this event refers to, or ``None`` when absent.

    ``event_id_map`` states which key carries the id for a given event type.
    Upstream indexed that key directly, so a payload naming a known event type
    but omitting its id raised ``KeyError`` before the event was dispatched.
    """
    key = event_id_map.get(event_name)
    if key is None:
        return None
    return identifier(data, key)


def is_known_home(data: Mapping[str, Any], home_key: str, known_home_ids: Any) -> bool:
    """Return whether the payload names a home this integration actually has.

    An event for an unknown home is either a stale delivery from a home that
    has since been removed, or a forged payload. Either way it must not be
    applied to entity state, and it must not be used as a dictionary key.
    """
    home_id = identifier(data, home_key)
    if home_id is None:
        return False
    try:
        return home_id in known_home_ids
    except TypeError:
        return False


def redacted_summary(
    data: Mapping[str, Any],
    event_name: str | None,
    *,
    id_keys: tuple[str, ...] = ("home_id", "camera_id", "device_id", "room_id"),
) -> str:
    """Return a log-safe one-line description of an inbound event.

    Upstream logged the complete decoded payload at debug level. Netatmo person
    events carry identity metadata and signed snapshot/face URLs, and the same
    log stream already received the full webhook URL. This renders only the
    event type and which identifier fields were present, never their values.

    Addresses defect C-11 and external finding SEC-012.
    """
    present = [key for key in id_keys if identifier(data, key) is not None]
    fields = ",".join(present) if present else "none"
    keys = len(data) if isinstance(data, Mapping) else 0
    return f"event_type={event_name or 'missing'} ids_present={fields} keys={keys}"
