"""Control-path integrity tests.

These cover the defects where the integration accepted one command and sent a
different one, or presented data it had silently corrupted. In an OT context
this is the most important class in the suite: a crash is visible, but a
truncated setpoint is not.

Traceability: C-1, C-2, C-19, C-21.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

# --------------------------------------------------------------------------
# C-1 - manual override duration
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("period", "expected_seconds"),
    [
        (timedelta(minutes=30), 1800),
        (timedelta(hours=23, minutes=59), 86340),
        (timedelta(hours=24), 86400),
        (timedelta(hours=24, minutes=1), 86460),
        (timedelta(days=1, hours=2, minutes=15), 94500),
        (timedelta(days=2), 172800),
        (timedelta(days=3, hours=12), 302400),
    ],
    ids=["30m", "23h59m", "24h", "24h1m", "1d2h15m", "2d", "3d12h"],
)
def test_override_duration_is_not_truncated(helper, period, expected_seconds):
    """The end timestamp must reflect the whole requested period.

    Upstream used ``timedelta.seconds``, which drops whole days. Every case at
    or beyond 24 hours was silently shortened before reaching the Netatmo API,
    so a thermostat returned to its schedule hours or days early with nothing
    logged anywhere.
    """
    now = 1_000_000.0
    assert helper.end_timestamp_for_period(now, period) == now + expected_seconds


def test_override_duration_regression_against_old_behaviour(helper):
    """Pin the specific arithmetic that was wrong.

    A 26-hour override used to become a 2-hour override. This asserts the two
    are no longer equal, so the old implementation cannot be reintroduced
    without failing.
    """
    period = timedelta(days=1, hours=2)
    now = 0.0
    correct = helper.end_timestamp_for_period(now, period)
    old_buggy_behaviour = int(now + period.seconds)

    assert correct == 93600
    assert old_buggy_behaviour == 7200
    assert correct != old_buggy_behaviour


# --------------------------------------------------------------------------
# C-2 / C-19 - camera event snapshot
# --------------------------------------------------------------------------


class _FakeSubEvent:
    def __init__(self, entity_id: str, event_type: str) -> None:
        self.entity_id = entity_id
        self.event_type = event_type


class _FakeEvent:
    def __init__(self, entity_id, event_time, video_id="vid", subevents=None):
        self.entity_id = entity_id
        self.event_time = event_time
        self.video_id = video_id
        self.subevents = subevents if subevents is not None else []


def _url_for(video_id: str) -> str:
    return f"https://example.invalid/vod/{video_id}/index.m3u8"


def test_event_index_does_not_mutate_source_objects(helper):
    """The pyatmo Event objects must come back untouched.

    Upstream wrote ``subevents`` and ``media_url`` straight into
    ``event.__dict__``, mutating state owned by another library.
    """
    subevent = _FakeSubEvent("sub-1", "human")
    event = _FakeEvent("evt-1", 1000, subevents=[subevent])

    helper.build_event_index([event], _url_for)

    assert event.subevents == [subevent], "source event was mutated"
    assert not hasattr(event, "media_url"), "media_url was written onto the source"


def test_event_index_is_idempotent_across_polls(helper):
    """Subevents must survive repeated processing of the same event set.

    This is the regression that mattered most. The first pass converted each
    subevent object to a dict and stored it back on the shared event; the
    second pass then filtered on ``not isinstance(x, dict)`` and dropped every
    one of them. Sub-event history vanished after a single poll cycle and never
    returned, with no error raised.
    """
    event = _FakeEvent("evt-1", 1000, subevents=[_FakeSubEvent("sub-1", "human")])

    first = helper.build_event_index([event], _url_for)
    second = helper.build_event_index([event], _url_for)
    third = helper.build_event_index([event], _url_for)

    assert len(first["evt-1"]["subevents"]) == 1
    assert len(second["evt-1"]["subevents"]) == 1, "subevents lost on second poll"
    assert len(third["evt-1"]["subevents"]) == 1, "subevents lost on third poll"
    assert second["evt-1"]["subevents"][0]["entity_id"] == "sub-1"


def test_events_sharing_a_timestamp_are_both_kept(helper):
    """Two events in the same second must both remain addressable.

    Cameras routinely emit a person detection and a movement within the same
    second. Keying on the timestamp meant the later one overwrote the earlier,
    silently losing a recording from the media browser.
    """
    events = [
        _FakeEvent("evt-a", 1000, video_id="vid-a"),
        _FakeEvent("evt-b", 1000, video_id="vid-b"),
    ]

    index = helper.build_event_index(events, _url_for)

    assert set(index) == {"evt-a", "evt-b"}
    assert index["evt-a"]["media_url"] != index["evt-b"]["media_url"]


def test_events_without_video_are_skipped(helper):
    """Events with no recording are not browsable and must not be indexed."""
    index = helper.build_event_index(
        [_FakeEvent("evt-1", 1000, video_id=None)], _url_for
    )
    assert index == {}


def test_event_keys_are_strings_for_url_round_trip(helper):
    """Media source identifiers travel through a URL path, so keys are strings."""
    index = helper.build_event_index([_FakeEvent("evt-1", 1000)], _url_for)
    assert all(isinstance(key, str) for key in index)


# --------------------------------------------------------------------------
# C-21 - public weather coordinate normalisation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "coordinate",
    [
        0.0000001,
        -0.0000001,
        1e-7,
        -1e-7,
        1e-5,
        0.0,
        8.1234567,
        52.0,
        -179.9999999,
        90.0,
    ],
    ids=[
        "tiny-positive",
        "tiny-negative",
        "sci-positive",
        "sci-negative",
        "sci-1e-5",
        "zero",
        "full-precision",
        "integer-valued",
        "near-min-longitude",
        "max-latitude",
    ],
)
def test_coordinate_normalisation_never_raises(helper, coordinate):
    """Any valid coordinate must normalise without an exception.

    Upstream read ``str(value).split(".")[1]``, which raises ``IndexError`` for
    every float Python renders in scientific notation - that is, any magnitude
    below 1e-4. A user near the equator or the prime meridian could not save a
    public weather area at all.
    """
    result = helper.normalise_coordinate(coordinate)
    assert isinstance(result, float)


def test_coordinate_normalisation_reproduces_old_crash(helper):
    """Demonstrate the exact input that used to crash the options flow.

    ``1e-7`` already carries seven decimal places, so the *correct* behaviour is
    to leave it untouched. Upstream could not even get that far: reading
    ``str(1e-07).split(".")[1]`` raises before any decision is made. The fix
    therefore preserves upstream's intent while removing the crash.
    """
    value = 1e-7

    with pytest.raises(IndexError):
        # The upstream implementation, verbatim.
        len(str(value).split(".")[1])

    assert helper.normalise_coordinate(value) == value


@pytest.mark.parametrize(
    ("value", "should_change"),
    [
        (52.0, True),  # one decimal place - needs padding for the API
        (52.12, True),  # two decimal places
        (52.123456, True),  # six decimal places - still short
        (8.1234567, False),  # exactly seven - already acceptable
        (1e-7, False),  # seven decimals, rendered in scientific notation
    ],
    ids=["1dp", "2dp", "6dp", "7dp", "7dp-scientific"],
)
def test_coordinate_precision_rule_matches_upstream_intent(
    helper, value, should_change
):
    """Padding must apply exactly when precision is below seven decimals."""
    result = helper.normalise_coordinate(value)
    assert (result != value) is should_change
