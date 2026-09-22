"""Nested event identity and log-record integrity.

These cover the ingress defects found by the independent audit of 0.1.0.

Traceability: E-003 (nested identity override), E-009 (control characters).

The common theme is that *shape* validation is not *provenance* validation. A
substituted identifier that happens to name another real device passes every
type, length and existence check 0.1.0 performed. Identity has to be taken from
the trusted envelope, not merely checked for plausibility.
"""

from __future__ import annotations

import pytest

PARENT = {
    "event_type": "outdoor",
    "home_id": "HOME-A",
    "device_id": "CAMERA-A",
    "camera_id": "CAMERA-A",
    "message": "Motion detected",
}


# ---------------------------------------------------------------------------
# E-003 - parent identity is immutable for nested members
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    ["home_id", "device_id", "camera_id", "room_id", "module_id", "user_id"],
)
def test_nested_member_cannot_override_parent_identity(validate, field):
    """A sub-event may not change which device an event is attributed to.

    ``{**parent, **subevent}`` inherits the parent's identifiers - which is
    required, because Netatmo omits them on nested objects - but it also lets
    the nested member overwrite them. That is the wrong direction across a
    trust boundary: the resulting value becomes the Home Assistant event's
    ``device_id``, so a nested member could aim an event at a device it does
    not belong to, and any automation keyed on that device would act on it.
    """
    subevent = {"type": "human", field: "ATTACKER-SUPPLIED"}

    merged = validate.merge_subevent(PARENT, subevent, "subevents")

    assert merged.get(field) == PARENT.get(field)
    assert merged.get(field) != "ATTACKER-SUPPLIED"


def test_nested_member_cannot_introduce_identity_the_parent_lacks(validate):
    """A field absent from the parent must not be injectable by the member."""
    parent = {"event_type": "outdoor", "home_id": "HOME-A"}

    merged = validate.merge_subevent(
        parent, {"type": "human", "device_id": "INJECTED"}, "subevents"
    )

    assert "device_id" not in merged


def test_identity_is_inherited_when_the_member_is_honest(validate):
    """The legitimate case still works: members inherit parent identity."""
    merged = validate.merge_subevent(PARENT, {"type": "human"}, "subevents")

    assert merged["home_id"] == "HOME-A"
    assert merged["device_id"] == "CAMERA-A"


def test_subevent_type_names_the_resulting_event(validate):
    """A sub-event's own ``type`` becomes the event type.

    This is what makes the outdoor camera's ``human`` / ``animal`` /
    ``vehicle`` device triggers reachable: they only ever arrive nested inside
    an ``outdoor`` envelope. Without this the sub-event would re-dispatch as
    ``outdoor`` and those three documented triggers would stay dead - which is
    the state 0.1.0 shipped in.
    """
    merged = validate.merge_subevent(PARENT, {"type": "human"}, "subevents")
    assert merged["event_type"] == "human"


def test_subevent_without_a_type_keeps_the_parent_event_type(validate):
    merged = validate.merge_subevent(PARENT, {"id": "x"}, "subevents")
    assert merged["event_type"] == "outdoor"


def test_subevent_type_must_itself_be_a_valid_identifier(validate):
    """A malformed nested type must not become the event name."""
    merged = validate.merge_subevent(PARENT, {"type": "human\r\nINJECTED"}, "subevents")
    assert merged["event_type"] == "outdoor"


def test_collection_does_not_travel_inside_its_own_members(validate):
    """The sub-event list must not be nested inside each derived event."""
    parent = {**PARENT, "subevents": [{"type": "human"}, {"type": "animal"}]}

    merged = validate.merge_subevent(parent, {"type": "human"}, "subevents")

    assert "subevents" not in merged


def test_member_descriptive_fields_are_preserved(validate):
    """Non-identity fields from the member are exactly what should survive."""
    merged = validate.merge_subevent(
        PARENT, {"type": "human", "id": "SUB-1", "snapshot": {"url": "x"}}, "subevents"
    )

    assert merged["id"] == "SUB-1"
    assert merged["snapshot"] == {"url": "x"}


# ---------------------------------------------------------------------------
# E-009 - control characters in identifier-shaped values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "person\r\nFAKE LOG LINE",
        "person\n",
        "person\r",
        "person\ttab",
        "person\x00null",
        "person\x1b[31mred",
        "person\x7f",
        "\x9bcsi",
    ],
    ids=["crlf", "lf", "cr", "tab", "nul", "ansi-escape", "del", "c1-csi"],
)
def test_control_characters_are_rejected_in_identifiers(validate, value):
    """Externally supplied values must not be able to forge log records.

    Identifier-shaped values reach the debug log through ``redacted_summary``.
    A value carrying CR, LF or terminal escapes can fabricate or corrupt log
    lines - which matters precisely because those logs are the forensic record
    for an externally reachable endpoint. No legitimate Netatmo identifier
    contains one (defect E-009).
    """
    assert validate.identifier({"k": value}, "k") is None
    assert validate.event_type({"event_type": value}, "event_type") is None


@pytest.mark.parametrize(
    "value",
    ["person", "therm_mode", "70:ee:50:00:00:01", "91763b24c43d3e344f424e8b", "a-b_c"],
)
def test_legitimate_identifiers_still_pass(validate, value):
    """The control-character rule must not reject real Netatmo values."""
    assert validate.identifier({"k": value}, "k") == value


def test_rejected_event_type_cannot_reach_the_log_summary(validate):
    """A forged event type must not appear in the log line either."""
    summary = validate.redacted_summary(
        {"event_type": "x\r\nFORGED"},
        validate.event_type({"event_type": "x\r\nFORGED"}, "event_type"),
    )

    assert "\r" not in summary
    assert "\n" not in summary
    assert "FORGED" not in summary
