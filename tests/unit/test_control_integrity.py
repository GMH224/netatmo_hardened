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
# C-21 / E-006 - public weather coordinate normalisation
# --------------------------------------------------------------------------

LATITUDE_LIMIT = 90.0
LONGITUDE_LIMIT = 180.0


@pytest.mark.parametrize(
    ("coordinate", "limit"),
    [
        (0.0000001, LATITUDE_LIMIT),
        (-0.0000001, LATITUDE_LIMIT),
        (1e-7, LATITUDE_LIMIT),
        (-1e-7, LATITUDE_LIMIT),
        (1e-5, LATITUDE_LIMIT),
        (0.0, LATITUDE_LIMIT),
        (8.1234567, LATITUDE_LIMIT),
        (52.0, LATITUDE_LIMIT),
        (90.0, LATITUDE_LIMIT),
        (-90.0, LATITUDE_LIMIT),
        (89.9999999, LATITUDE_LIMIT),
        (180.0, LONGITUDE_LIMIT),
        (-180.0, LONGITUDE_LIMIT),
        (-179.9999999, LONGITUDE_LIMIT),
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
        "max-latitude",
        "min-latitude",
        "just-inside-max-latitude",
        "max-longitude",
        "min-longitude",
        "just-inside-min-longitude",
    ],
)
def test_normalised_coordinate_stays_in_range(helper, coordinate, limit):
    """The output must be a legal coordinate. Always.

    This is the assertion that was missing in 0.1.0. The test existed and
    included 90.0, but only asserted ``isinstance(result, float)`` - so it
    verified that the function did not crash, not that it produced a usable
    value. ``normalise_coordinate(90.0)`` returned ``90.0000001`` and the suite
    stayed green (defect E-006).

    A test that checks the weaker of two available properties is worse than no
    test, because it converts an unknown into false confidence.
    """
    result = helper.normalise_coordinate(coordinate, limit)
    assert isinstance(result, float)
    assert -limit <= result <= limit, f"{coordinate} normalised out of range"


@pytest.mark.parametrize(
    ("value", "limit"),
    [(90.0, LATITUDE_LIMIT), (180.0, LONGITUDE_LIMIT)],
    ids=["latitude-90", "longitude-180"],
)
def test_exact_maximum_is_nudged_inward(helper, value, limit):
    """At the positive boundary the nudge must go inward, not outward."""
    result = helper.normalise_coordinate(value, limit)
    assert result < value
    assert result == pytest.approx(value - 1e-7)


def test_coordinate_normalisation_reproduces_old_crash(helper):
    """The scientific-notation input that used to crash the options flow.

    ``1e-7`` already carries seven decimal places, so the correct behaviour is
    to leave it untouched. Upstream could not even get that far: reading
    ``str(1e-07).split(".")[1]`` raises before any decision is made.
    """
    value = 1e-7

    with pytest.raises(IndexError):
        len(str(value).split(".")[1])  # the upstream implementation, verbatim

    assert helper.normalise_coordinate(value, LATITUDE_LIMIT) == value


@pytest.mark.parametrize(
    ("value", "should_change"),
    [
        (52.0, True),
        (52.12, True),
        (52.123456, True),
        (8.1234567, False),
        (1e-7, False),
    ],
    ids=["1dp", "2dp", "6dp", "7dp", "7dp-scientific"],
)
def test_coordinate_precision_rule_matches_upstream_intent(
    helper, value, should_change
):
    """Padding applies exactly when precision is below seven decimals."""
    result = helper.normalise_coordinate(value, LATITUDE_LIMIT)
    assert (result != value) is should_change


# --------------------------------------------------------------------------
# E-004 - backend command results
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("result", "is_failure"),
    [
        (False, True),
        (True, False),
        (None, False),
        (0, False),
        ("", False),
        ([], False),
    ],
    ids=["false", "true", "none", "zero", "empty-string", "empty-list"],
)
def test_only_explicit_false_counts_as_command_failure(helper, result, is_failure):
    """``False`` means rejected; nothing else may be read as a failure.

    pyatmo's control methods are a mixed bag: ``async_on`` and
    ``async_set_state`` return ``bool``, while the room-level
    ``async_therm_set`` / ``async_therm_manual`` / ``async_therm_home`` return
    ``None`` and give no success indication at all.

    Both halves matter. Treating ``False`` as success publishes state the
    device never reached (defect E-004). But treating ``None`` as failure would
    make every thermostat setpoint raise an error - fabricating a guarantee the
    dependency does not offer, in the opposite direction. Falsy-but-not-False
    values are covered because ``if not result`` would have been the obvious
    wrong implementation.
    """
    assert helper.command_failed(result) is is_failure
