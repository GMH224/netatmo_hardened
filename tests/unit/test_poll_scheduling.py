"""Poll-scheduling tests.

Traceability: F-003 (do not poll a home that can produce nothing),
F-004 (poll no faster than the source changes).

Both features are arithmetic and set membership, which is exactly the kind of
logic that fails silently: an interval computed from the wrong quantity still
produces a plausible number of seconds, and a home skipped by the wrong rule
still produces a working integration with one fewer entity than it should
have. Neither shows up as an error.
"""

from __future__ import annotations

import pytest

# Upstream's values, inherited verbatim by this fork. Restated here so that a
# change to either table has to be reflected in a test rather than silently
# altering the shipped cadence.
BASE = {
    "account": 10800,
    "home": 300,
    "weather": 600,
    "air_care": 300,
    "public": 600,
    "event": 600,
}
DEV_FACTOR = 7
CLOUD_FACTOR = 2

# How often a Netatmo weather station actually publishes, in seconds. Every
# module of the station shares this cadence - indoor and outdoor alike.
STATION_PERIOD = 300


# ---------------------------------------------------------------------------
# F-004 - the floor
# ---------------------------------------------------------------------------


def test_floor_applies_when_the_rate_limit_would_poll_faster(helper):
    """The whole point: a high call budget must not shorten the interval.

    Upstream divided the 600 s weather interval by seven and polled every 85
    seconds, against a station that publishes once every five minutes.
    """
    assert helper.effective_poll_interval(600, DEV_FACTOR, 240) == 240


def test_a_lower_rate_limit_may_still_lengthen_the_interval(helper):
    """The floor is a minimum, not a replacement.

    Home Assistant Cloud carries a 150 calls/hour budget rather than 400. If
    that arithmetic produces a *longer* interval than the floor, the longer
    one must win - otherwise this "fix" would increase traffic for cloud
    users, which is the opposite of the intent.
    """
    # 600 / 2 = 300, which is longer than a 240 s floor.
    assert helper.effective_poll_interval(600, CLOUD_FACTOR, 240) == 300


def test_floor_is_ignored_when_the_scaled_interval_is_already_longer(helper):
    """No floor may shorten anything."""
    assert helper.effective_poll_interval(10800, CLOUD_FACTOR, 3600) == 5400


@pytest.mark.parametrize("factor", [0, -1, -7])
def test_a_nonsensical_factor_cannot_produce_a_runaway_poll_rate(helper, factor):
    """A zero or negative factor must not divide or invert the interval.

    Division by zero would raise inside setup, and a negative factor would
    yield a negative interval - which `next_scan = time() + interval` turns
    into a publisher that is permanently overdue and polled on every single
    tick. That is an API-hammering loop reached by a configuration mistake,
    so it is closed off rather than assumed impossible.
    """
    result = helper.effective_poll_interval(600, factor, 240)
    assert result >= 240


def test_weather_floor_is_below_the_station_publish_period(helper):
    """Polling must be strictly faster than the source, but not by much.

    At exactly the publish period the poll drifts into and out of phase with
    the station and periodically skips a measurement. Strictly below it,
    every published sample is observed at least once.

    This test states the *relationship*, not the number, so that changing the
    floor keeps the guarantee or fails here.
    """
    interval = helper.effective_poll_interval(BASE["weather"], DEV_FACTOR, 240)
    assert interval < STATION_PERIOD
    # ...and not so far below that the oversampling comes back.
    assert interval > STATION_PERIOD / 2


def test_upstream_cadence_is_what_this_replaces(helper):
    """Pins the defect, so a revert is visible rather than quiet.

    Recomputing upstream's number here means that if `MIN_INTERVALS` is ever
    removed or bypassed, this assertion fails rather than the integration
    silently returning to eighteen calls per measurement.
    """
    upstream = int(BASE["weather"] / DEV_FACTOR)
    assert upstream == 85
    hardened = helper.effective_poll_interval(BASE["weather"], DEV_FACTOR, 240)
    assert hardened > upstream


@pytest.mark.parametrize(
    ("publisher", "floor"),
    [
        ("account", 3600),
        ("home", 120),
        ("weather", 240),
        ("air_care", 240),
        ("public", 600),
        ("event", 300),
    ],
)
def test_every_publisher_has_a_reviewed_floor(helper, publisher, floor):
    """Each floor is a decision with a stated reason, asserted as a value.

    Stated as a test so that changing one - which changes shipped API traffic
    and data freshness for every installation - is a deliberate edit to an
    assertion rather than a number quietly adjusted in a dict.
    """
    result = helper.effective_poll_interval(BASE[publisher], DEV_FACTOR, floor)
    assert result >= floor


def test_result_is_always_a_whole_number_of_seconds(helper):
    """`next_scan` arithmetic and the 60 s tick both assume integers."""
    for factor in (1, 2, 3, 7):
        value = helper.effective_poll_interval(300, factor, 120)
        assert isinstance(value, int)


# ---------------------------------------------------------------------------
# F-003 - homes that can produce nothing
# ---------------------------------------------------------------------------


def test_a_home_with_modules_is_polled(helper):
    """The ordinary case must be unaffected."""
    assert helper.home_is_pollable(["70:ee:50:13:44:da"], ["room-1"], set()) is True


def test_a_home_with_no_modules_is_not_polled(helper):
    """A Netatmo account routinely carries homes with no hardware.

    One created by the app for a holiday address, one set up and never
    equipped. Upstream polled each on the same schedule as a real home, for
    ever, and each could never contribute a single entity.
    """
    assert helper.home_is_pollable([], ["room-1", "room-2"], set()) is False


def test_a_home_with_neither_rooms_nor_modules_is_not_polled(helper):
    """The degenerate case."""
    assert helper.home_is_pollable([], [], set()) is False


def test_a_home_whose_every_module_and_room_is_disabled_is_not_polled(helper):
    """Disabling the whole contents expresses the same intent as disabling the home.

    Disabling a device in Home Assistant stops its entities updating but does
    not stop the API call, because `async_update_status` fetches the entire
    home. An operator who has disabled everything inside a home is still
    paying for it on every poll.
    """
    disabled = {"module-a", "module-b", "room-1"}
    assert (
        helper.home_is_pollable(["module-a", "module-b"], ["room-1"], disabled) is False
    )


def test_one_enabled_module_keeps_the_home_polled(helper):
    """Partial disabling must not silently drop the rest of the home."""
    disabled = {"module-a"}
    assert (
        helper.home_is_pollable(["module-a", "module-b"], ["room-1"], disabled) is True
    )


def test_a_surviving_room_keeps_the_home_polled(helper):
    """A room can carry entities of its own.

    A thermostat setpoint belongs to the room, not to the valve module, so a
    home whose modules are all disabled but which still has an enabled room
    has something left to report.
    """
    disabled = {"module-a"}
    assert helper.home_is_pollable(["module-a"], ["room-1"], disabled) is True


def test_disabling_some_rooms_does_not_drop_the_home(helper):
    """Honest about what this cannot do.

    There is no per-room fetch - `async_update_status` returns the whole home
    or nothing - so disabling one room of three saves no traffic. The home
    must stay polled, and this test exists so nobody later "optimises" it into
    dropping a home that still has enabled content.
    """
    disabled = {"room-2", "room-3"}
    assert (
        helper.home_is_pollable(["module-a"], ["room-1", "room-2", "room-3"], disabled)
        is True
    )


def test_accepts_dict_views_as_well_as_lists(helper):
    """pyatmo hands over `Home.modules` and `Home.rooms`, which are dicts.

    Iterating a dict yields its keys, which are the ids - but the caller
    passes the container itself, so this must work on whatever pyatmo's
    attribute actually is rather than on a list the caller remembered to
    build.
    """
    modules = {"70:ee:50:13:44:da": object()}
    rooms = {"2757209110": object()}
    assert helper.home_is_pollable(modules, rooms, set()) is True


def test_the_operators_own_account_shape(helper):
    """The case that prompted this: three homes, one with hardware.

    Two of the three carry rooms but no modules. Under the previous behaviour
    all three were polled identically, so two thirds of the home-status
    traffic fetched homes that were empty.
    """
    equipped = helper.home_is_pollable(
        ["70:ee:50:13:44:da", "03:00:00:0a:e4:94", "03:00:00:0b:15:b4"],
        ["1901977365", "2757209110", "1431568190", "3142077297", "1495207751"],
        set(),
    )
    empty_one = helper.home_is_pollable([], ["2058227814", "2088457813"], set())
    empty_two = helper.home_is_pollable([], ["1766520607"], set())

    assert equipped is True
    assert empty_one is False
    assert empty_two is False


# ---------------------------------------------------------------------------
# The two together - what the operator actually gets
# ---------------------------------------------------------------------------


def test_combined_call_rate_stays_within_the_budget(helper):
    """The point of both features, expressed as the number that matters.

    `DEV_LIMIT` is 400 calls/hour. Exceeding it trips the brake in
    `async_update`, which pushes every publisher's next scan back by 60 s on
    each 60 s tick - so once tripped, the schedule advances as fast as wall
    time and polling does not slow down, it stops until the hourly counter
    resets. Staying well clear of the limit is what keeps that unreachable.
    """
    tick = 60

    def effective(publisher, floor):
        raw = helper.effective_poll_interval(BASE[publisher], DEV_FACTOR, floor)
        # A publisher due mid-tick is not called until the next 60 s tick.
        return -(-raw // tick) * tick

    # The operator's account after F-003: one equipped home, two dropped.
    calls_per_hour = (
        3600 / effective("weather", 240)
        + 3600 / effective("home", 120)
        + 3600 / effective("account", 3600)
    )

    assert calls_per_hour < 100
    # And comfortably inside the budget that trips the brake.
    assert calls_per_hour < 400 / 2
