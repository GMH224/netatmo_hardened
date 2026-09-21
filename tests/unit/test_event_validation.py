"""Webhook ingress validation tests.

The webhook is the integration's only externally reachable surface. Home
Assistant authenticates it on possession of the webhook id alone, so once that
id leaks - and upstream wrote it to the debug log in full - the request body is
attacker-controlled.

The contract these tests enforce is simple and absolute: **no inbound payload,
however malformed, may raise.** Every function here returns a safe value
instead.

Traceability: C-8, C-9, C-11, C-18, and external findings SEC-002 to SEC-006,
SEC-012. The adversarial corpus is drawn from the external audit's appendix
C.5, which was the strongest part of that report.
"""

from __future__ import annotations

import pytest

# --------------------------------------------------------------------------
# Root type - SEC-002
# --------------------------------------------------------------------------

MALFORMED_ROOTS = [None, [], "hello", 42, 3.14, True, ["a", "b"], ()]


@pytest.mark.parametrize("payload", MALFORMED_ROOTS)
def test_non_object_roots_are_rejected(validate, payload):
    """Syntactically valid JSON that is not an object must be dropped.

    ``json.loads`` returns lists, strings, numbers, bools and None for valid
    JSON. Upstream called ``.get()`` on the result and raised ``AttributeError``
    inside the request handler, producing a 5xx and a traceback per request.
    """
    assert validate.as_mapping(payload) is None


def test_object_root_is_accepted(validate):
    assert validate.as_mapping({"event_type": "person"}) == {"event_type": "person"}


# --------------------------------------------------------------------------
# Collections - SEC-003
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "persons",
    [None, {}, "x", 123, ["x"], [None], [123], [[]], True],
    ids=[
        "missing",
        "object-not-array",
        "string",
        "int",
        "array-of-strings",
        "array-of-null",
        "array-of-ints",
        "array-of-arrays",
        "bool",
    ],
)
def test_malformed_person_collections_yield_no_members(validate, persons):
    """A wrong-typed persons collection yields nothing rather than raising.

    Upstream iterated ``event_data.get(ATTR_PERSONS, {})``. Note the mapping
    default: iterating a dict yields its *keys*, which are strings, and the
    next line called ``person.get(...)`` on them.
    """
    assert validate.mapping_members(persons) == []


def test_valid_members_survive_a_malformed_neighbour(validate):
    """One bad member must not discard the whole event."""
    members = validate.mapping_members([{"id": "a"}, "garbage", None, {"id": "b"}])
    assert members == [{"id": "a"}, {"id": "b"}]


def test_collections_are_bounded(validate):
    """A hostile sender must not make the event loop walk an unbounded list."""
    huge = [{"id": str(i)} for i in range(5000)]
    assert len(validate.mapping_members(huge)) == validate.MAX_COLLECTION_ITEMS


# --------------------------------------------------------------------------
# Identifiers - SEC-004
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [None, "", 123, [], {}, True, "x" * 500],
    ids=["missing", "empty", "int", "list", "dict", "bool", "overlong"],
)
def test_bad_identifiers_are_rejected(validate, value):
    """Anything used as a dict key or entity comparison must be a sane string."""
    assert validate.identifier({"home_id": value}, "home_id") is None


def test_good_identifier_is_returned(validate):
    assert validate.identifier({"home_id": "abc123"}, "home_id") == "abc123"


def test_event_type_absent_returns_none_not_the_string_none(validate):
    """The 'None' default was not inert - it collided with a real signal.

    Upstream defaulted a missing event type to the *string* ``"None"``, which
    produced the dispatcher signal ``signal-netatmo-webhook-None``. The
    coordinator subscribes to exactly that signal for webhook activation
    notices, so a payload with no event type reached
    ``event["data"]["push_type"]`` and raised ``KeyError``.
    """
    result = validate.event_type({}, "event_type")
    assert result is None
    assert result != "None"


@pytest.mark.parametrize(
    ("payload", "event_name"),
    [
        ({"event_type": "movement"}, "movement"),
        ({"event_type": "person"}, "person"),
        ({"event_type": "therm_mode"}, "therm_mode"),
        ({"event_type": "set_point"}, "set_point"),
    ],
)
def test_known_event_type_without_its_id_field_is_rejected(
    validate, payload, event_name
):
    """Every EVENT_ID_MAP entry must tolerate its id field being absent.

    Upstream indexed ``data[EVENT_ID_MAP[event_type]]`` directly, so naming a
    known event type while omitting its identifier raised ``KeyError``.
    """
    event_id_map = {
        "movement": "device_id",
        "person": "device_id",
        "therm_mode": "home_id",
        "set_point": "room_id",
    }
    assert validate.device_id_for_event(payload, event_name, event_id_map) is None


def test_unknown_event_type_has_no_id_field(validate):
    assert validate.device_id_for_event({"device_id": "x"}, "bogus", {}) is None


def test_device_id_is_returned_when_present(validate):
    result = validate.device_id_for_event(
        {"device_id": "70:ee:50:00:00:01"}, "movement", {"movement": "device_id"}
    )
    assert result == "70:ee:50:00:00:01"


# --------------------------------------------------------------------------
# Home identity - SEC-003 / SEC-005
# --------------------------------------------------------------------------


def test_unknown_home_is_rejected(validate):
    """An event naming a home we do not have is stale or forged."""
    known = {"home-a": {}}
    assert validate.is_known_home({"home_id": "home-b"}, "home_id", known) is False
    assert validate.is_known_home({}, "home_id", known) is False
    assert validate.is_known_home({"home_id": "home-a"}, "home_id", known) is True


def test_is_known_home_tolerates_a_bad_container(validate):
    assert validate.is_known_home({"home_id": "a"}, "home_id", None) is False


# --------------------------------------------------------------------------
# Log hygiene - SEC-001 / SEC-012
# --------------------------------------------------------------------------


def test_summary_never_contains_identifier_values(validate):
    """Debug logging must not reproduce payload contents.

    Netatmo person events carry identity metadata and signed face/snapshot
    URLs. Upstream logged the entire decoded payload at debug level, into the
    same log stream that already received the webhook URL in full.
    """
    payload = {
        "event_type": "person",
        "home_id": "SECRET_HOME",
        "camera_id": "SECRET_CAMERA",
        "face_url": "https://netatmo.invalid/faces/SECRET_TOKEN.jpg",
        "persons": [{"id": "SECRET_PERSON", "face": {"url": "https://x/SECRET"}}],
    }

    summary = validate.redacted_summary(payload, "person")

    for secret in (
        "SECRET_HOME",
        "SECRET_CAMERA",
        "SECRET_TOKEN",
        "SECRET_PERSON",
        "https://",
    ):
        assert secret not in summary, f"{secret!r} leaked into the log line"

    # It must still be useful for diagnosis.
    assert "event_type=person" in summary
    assert "home_id" in summary
    assert "camera_id" in summary


def test_summary_handles_a_payload_with_nothing_in_it(validate):
    assert "event_type=missing" in validate.redacted_summary({}, None)


# --------------------------------------------------------------------------
# Full adversarial corpus - the contract, restated
# --------------------------------------------------------------------------

ADVERSARIAL_PAYLOADS = [
    {},
    {"event_type": "bogus"},
    {"event_type": "movement"},
    {"event_type": "person"},
    {"event_type": "person", "persons": [None]},
    {"event_type": "person", "persons": ["x"]},
    {"event_type": "person", "persons": {}},
    {"event_type": "person", "home_id": "unknown-home", "persons": [{"id": "p"}]},
    {"event_type": "camera", "camera_id": None},
    {"event_type": "set_point", "home_id": "unknown"},
    {"event_type": "", "home_id": ""},
    {"event_type": 123},
    {"event_type": "light_mode", "sub_type": 123},
    {"push_type": None},
    {"event_type": "therm_mode", "home": "not-an-object"},
    {"event_type": "outdoor", "subevents": "not-a-list"},
    {"event_type": "outdoor", "subevents": [None, 1, "x"]},
]


@pytest.mark.parametrize("payload", ADVERSARIAL_PAYLOADS)
def test_adversarial_corpus_never_raises(validate, payload):
    """The whole validation surface, over the audit's adversarial corpus.

    This is the contract in one test: whatever arrives, nothing raises.
    """
    event_id_map = {"movement": "device_id", "person": "device_id"}

    validate.as_mapping(payload)
    name = validate.event_type(payload, "event_type")
    validate.mapping_members(payload.get("persons"))
    validate.mapping_members(payload.get("subevents"))
    validate.identifier(payload, "home_id")
    validate.is_known_home(payload, "home_id", {"known": {}})
    validate.has_required(payload, "home_id", "camera_id")
    if name:
        validate.device_id_for_event(payload, name, event_id_map)
    validate.redacted_summary(payload, name)
