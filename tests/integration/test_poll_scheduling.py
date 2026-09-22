"""Tier 2 tests for poll scheduling.

Traceability: F-003 (do not poll a home that can produce nothing),
F-004 (poll no faster than the source changes).

Tier 1 covers both decisions as pure functions. What only a runtime can show
is that the coordinator actually *uses* them: that `subscribe()` stores the
floored interval rather than the rate-limit one, and that `async_dispatch`
skips the homes the rule excludes. Either could be correct in isolation and
simply not wired in - which would leave the shipped cadence unchanged while
every unit test passed.

These tests require a real Home Assistant runtime and have not been executed
in the authoring environment - see docs/VERIFICATION_REPORT.md.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.netatmo_hardened.coordinator import (
    ACCOUNT,
    DEFAULT_INTERVALS,
    DEV_FACTOR,
    DEV_LIMIT,
    HOME,
    MIN_INTERVALS,
    SCAN_INTERVAL,
    WEATHER,
)

EQUIPPED_HOME = "5ef37f972f57748b18076045"
EMPTY_HOME_A = "64eaac5587b343b64307bbb8"
EMPTY_HOME_B = "69c4b00babf413b12700ac25"


def _home(home_id: str, modules: dict, rooms: dict) -> MagicMock:
    """Build a pyatmo-shaped home stub."""
    home = MagicMock()
    home.entity_id = home_id
    home.modules = modules
    home.rooms = rooms
    home.persons = {}
    return home


async def _setup(hass: HomeAssistant, entry) -> None:
    """Set up the integration for an already-built config entry."""
    assert await async_setup_component(hass, "application_credentials", {})
    entry.add_to_hass(hass)

    with patch(
        "homeassistant.helpers.config_entry_oauth2_flow.OAuth2Session"
    ) as session:
        session.return_value.async_ensure_token_valid = AsyncMock()
        session.return_value.token = entry.data["token"]
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


# ---------------------------------------------------------------------------
# F-004 - the floor reaches the scheduler
# ---------------------------------------------------------------------------


async def test_publisher_intervals_respect_the_floor(
    hass: HomeAssistant, push_disabled_config_entry, mock_auth, mock_account
) -> None:
    """Every created publisher must carry a floored interval.

    This is the test that catches "the helper is right but nobody calls it".
    """
    await _setup(hass, push_disabled_config_entry)

    data_handler = push_disabled_config_entry.runtime_data
    assert data_handler.publisher, "no publishers were created"

    for signal_name, publisher in data_handler.publisher.items():
        assert publisher.interval >= min(MIN_INTERVALS.values()), signal_name


async def test_weather_is_not_polled_faster_than_the_station_publishes(
    hass: HomeAssistant, push_disabled_config_entry, mock_auth, mock_account
) -> None:
    """The defect this release exists for.

    Every module of a Netatmo weather station publishes to the cloud once
    every five minutes, indoor and outdoor alike. Upstream polled it every 85
    seconds - roughly three and a half calls for every measurement, before
    counting the homes.
    """
    await _setup(hass, push_disabled_config_entry)

    data_handler = push_disabled_config_entry.runtime_data
    weather = data_handler.publisher.get(WEATHER)
    if weather is None:
        pytest.skip("no weather publisher on this fixture account")

    upstream_interval = int(DEFAULT_INTERVALS[WEATHER] / DEV_FACTOR)
    assert weather.interval > upstream_interval
    assert weather.interval == MIN_INTERVALS[WEATHER]
    # Strictly below the 300 s publish period, so no sample can be skipped.
    assert weather.interval < 300


async def test_the_account_publisher_is_not_polled_every_25_minutes(
    hass: HomeAssistant, push_disabled_config_entry, mock_auth, mock_account
) -> None:
    """Topology changes when a human adds hardware, not on a timer."""
    await _setup(hass, push_disabled_config_entry)

    account = push_disabled_config_entry.runtime_data.publisher.get(ACCOUNT)
    if account is not None:
        assert account.interval >= MIN_INTERVALS[ACCOUNT]


async def test_total_call_rate_stays_clear_of_the_brake(
    hass: HomeAssistant, push_disabled_config_entry, mock_auth, mock_account
) -> None:
    """Staying inside the budget is what keeps the brake unreachable.

    `async_update` pushes every publisher's next scan back by 60 s on each
    tick that the hourly call count exceeds `DEV_LIMIT` - and ticks are 60 s
    apart, so once tripped the schedule advances as fast as wall time and
    polling stops rather than slows. Oversampling one publisher is what
    starves all of them.
    """
    await _setup(hass, push_disabled_config_entry)

    data_handler = push_disabled_config_entry.runtime_data
    calls_per_hour = sum(
        3600 / max(publisher.interval, SCAN_INTERVAL)
        for publisher in data_handler.publisher.values()
    )
    assert calls_per_hour < DEV_LIMIT / 2


# ---------------------------------------------------------------------------
# F-003 - homes that can produce nothing are not polled
# ---------------------------------------------------------------------------


async def test_a_home_with_no_modules_gets_no_publisher(
    hass: HomeAssistant, push_disabled_config_entry, mock_auth, mock_account
) -> None:
    """The operator's own account shape: three homes, one with hardware.

    The two empty homes carry rooms but no modules - created by the Netatmo
    app and never equipped. Upstream polled all three identically, so two
    thirds of the home-status traffic fetched nothing.
    """
    mock_account.homes = {
        EQUIPPED_HOME: _home(
            EQUIPPED_HOME,
            {"70:ee:50:13:44:da": MagicMock(), "03:00:00:0a:e4:94": MagicMock()},
            {"2757209110": MagicMock()},
        ),
        EMPTY_HOME_A: _home(EMPTY_HOME_A, {}, {"2058227814": MagicMock()}),
        EMPTY_HOME_B: _home(EMPTY_HOME_B, {}, {"1766520607": MagicMock()}),
    }

    await _setup(hass, push_disabled_config_entry)

    publishers = push_disabled_config_entry.runtime_data.publisher
    assert f"{HOME}-{EQUIPPED_HOME}" in publishers
    assert f"{HOME}-{EMPTY_HOME_A}" not in publishers
    assert f"{HOME}-{EMPTY_HOME_B}" not in publishers


async def test_skipping_empty_homes_does_not_break_setup(
    hass: HomeAssistant, push_disabled_config_entry, mock_auth, mock_account
) -> None:
    """Dropping a home must not fail the load.

    Skipping is a traffic optimisation; it must never be able to cost the
    entry. An account whose homes are *all* empty is the degenerate case and
    must still load cleanly.
    """
    mock_account.homes = {
        EMPTY_HOME_A: _home(EMPTY_HOME_A, {}, {"2058227814": MagicMock()}),
        EMPTY_HOME_B: _home(EMPTY_HOME_B, {}, {}),
    }

    await _setup(hass, push_disabled_config_entry)

    assert push_disabled_config_entry.state is ConfigEntryState.LOADED


async def test_an_equipped_home_is_still_polled(
    hass: HomeAssistant, push_disabled_config_entry, mock_auth, mock_account
) -> None:
    """The negative tests above pass trivially if nothing is ever polled."""
    mock_account.homes = {
        EQUIPPED_HOME: _home(
            EQUIPPED_HOME,
            {"70:ee:50:13:44:da": MagicMock()},
            {"2757209110": MagicMock()},
        ),
    }

    await _setup(hass, push_disabled_config_entry)

    publishers = push_disabled_config_entry.runtime_data.publisher
    assert f"{HOME}-{EQUIPPED_HOME}" in publishers
    assert publishers[f"{HOME}-{EQUIPPED_HOME}"].interval >= MIN_INTERVALS[HOME]


async def test_a_home_with_every_device_disabled_gets_no_publisher(
    hass: HomeAssistant, push_disabled_config_entry, mock_auth, mock_account
) -> None:
    """The operator's explicit request: disabling the contents must stop the poll.

    Disabling a device in Home Assistant stops its entities updating, but
    `async_update_status` fetches the whole home - so before this release,
    disabling everything inside a home changed nothing about the traffic.
    """
    modules = {"module-a": MagicMock(), "module-b": MagicMock()}
    rooms = {"room-1": MagicMock()}
    mock_account.homes = {EQUIPPED_HOME: _home(EQUIPPED_HOME, modules, rooms)}

    with patch(
        "custom_components.netatmo_hardened.coordinator.async_disabled_netatmo_ids",
        return_value=["module-a", "module-b", "room-1"],
    ):
        await _setup(hass, push_disabled_config_entry)

    publishers = push_disabled_config_entry.runtime_data.publisher
    assert f"{HOME}-{EQUIPPED_HOME}" not in publishers


async def test_one_enabled_device_keeps_the_home_polled(
    hass: HomeAssistant, push_disabled_config_entry, mock_auth, mock_account
) -> None:
    """Partial disabling must not silently drop the rest of the home.

    This is the boundary that matters: an operator tidying away one sensor
    must not lose the others.
    """
    modules = {"module-a": MagicMock(), "module-b": MagicMock()}
    rooms = {"room-1": MagicMock()}
    mock_account.homes = {EQUIPPED_HOME: _home(EQUIPPED_HOME, modules, rooms)}

    with patch(
        "custom_components.netatmo_hardened.coordinator.async_disabled_netatmo_ids",
        return_value=["module-a"],
    ):
        await _setup(hass, push_disabled_config_entry)

    publishers = push_disabled_config_entry.runtime_data.publisher
    assert f"{HOME}-{EQUIPPED_HOME}" in publishers
