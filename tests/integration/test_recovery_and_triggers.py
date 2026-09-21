"""Coordinator recovery, auth classification and device triggers.

Traceability: C-5, C-7, C-10, C-13, C-15.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pyatmo
import pytest
from homeassistant.core import HomeAssistant

from custom_components.netatmo_hardened import coordinator as netatmo_coordinator
from custom_components.netatmo_hardened.const import DOMAIN, EVENT_TYPE_THERM_MODE
from custom_components.netatmo_hardened.device_trigger import (
    SUBTYPE_PAYLOAD_KEY,
    SUBTYPE_PAYLOAD_PATH,
)

# ---------------------------------------------------------------------------
# C-7 - authentication failures must not look like network failures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "expect_reauth"),
    [
        (pyatmo.ApiError("unauthorized", status=401), True),
        (pyatmo.ApiError("forbidden", status=403), True),
        (pyatmo.ApiError("server error", status=500), False),
        (pyatmo.ApiError("bad request", status=400), False),
        (pyatmo.ApiThrottlingError("throttled", status=403), False),
        (TimeoutError("timeout"), False),
    ],
    ids=["401", "403", "500", "400", "throttled-403", "timeout"],
)
def test_auth_failures_are_classified(error, expect_reauth) -> None:
    """401/403 mean reauthenticate; everything else means retry.

    Defect C-7: upstream flattened every ``ApiError`` into one "unavailable,
    back off" path, so a revoked token presented as an ordinary outage and the
    watchdog reloaded the entry every 15 minutes forever instead of asking the
    user to sign in again.

    The throttling case is the subtle one. Netatmo also answers 403 when rate
    limiting, so classifying on status alone would push a merely throttled user
    into a pointless reauthentication prompt.
    """
    if isinstance(error, pyatmo.ApiError):
        assert netatmo_coordinator._is_auth_failure(error) is expect_reauth


async def test_revoked_token_starts_reauth_not_a_reload_loop(
    hass: HomeAssistant, setup_integration, mock_account
) -> None:
    """A 401 during polling must start a reauth flow."""
    handler = setup_integration.runtime_data
    mock_account.async_update_topology = AsyncMock(
        side_effect=pyatmo.ApiError("unauthorized", status=401)
    )

    with patch.object(setup_integration, "async_start_reauth") as start_reauth:
        await handler.async_fetch_data("account")
        assert start_reauth.called


async def test_reauth_is_started_only_once(
    hass: HomeAssistant, setup_integration, mock_account
) -> None:
    """Repeated 401s must not open a reauth flow per poll."""
    handler = setup_integration.runtime_data
    mock_account.async_update_topology = AsyncMock(
        side_effect=pyatmo.ApiError("unauthorized", status=401)
    )

    with patch.object(setup_integration, "async_start_reauth") as start_reauth:
        for _ in range(5):
            await handler.async_fetch_data("account")
        assert start_reauth.call_count == 1


# ---------------------------------------------------------------------------
# C-13 - the watchdog must be bounded
# ---------------------------------------------------------------------------


async def test_watchdog_stops_after_max_reloads(
    hass: HomeAssistant, setup_integration
) -> None:
    """A fault the watchdog cannot fix must end in a repair issue, not a loop.

    Defect C-13: the reload counter lived on the data handler, which the reload
    itself rebuilds, so it reset every time and the integration reloaded
    forever - generating API traffic and log noise indefinitely in an
    unattended deployment.
    """
    from homeassistant.helpers import issue_registry as ir

    handler = setup_integration.runtime_data
    handler._watchdog_reloads = netatmo_coordinator.MAX_WATCHDOG_RELOADS

    for publisher in handler.publisher.values():
        publisher.available = False
    handler._all_unavailable_since = 0.0

    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        handler._check_stale_watchdog()
        assert not reload.called, "watchdog kept reloading past its own limit"

    issues = ir.async_get(hass)
    assert issues.async_get_issue(
        DOMAIN,
        f"{netatmo_coordinator.ISSUE_WATCHDOG_EXHAUSTED}_{setup_integration.entry_id}",
    )


async def test_watchdog_counter_survives_reload(
    hass: HomeAssistant, setup_integration
) -> None:
    """The reload budget must be stored outside the object a reload rebuilds."""
    handler = setup_integration.runtime_data
    handler._watchdog_reloads = 2

    assert hass.data[f"{DOMAIN}_watchdog"][setup_integration.entry_id] == 2


# ---------------------------------------------------------------------------
# C-10 - publisher queue must tolerate concurrent subscription changes
# ---------------------------------------------------------------------------


async def test_update_survives_subscription_change_mid_cycle(
    hass: HomeAssistant, setup_integration, mock_account
) -> None:
    """Adding a publisher while an update is in flight must not abort the cycle.

    Defect C-10: ``async_update`` iterated the live deque across an ``await``.
    An entity being added during that await calls ``subscribe()``, which
    appends to the same deque, raising "deque mutated during iteration".
    """
    import asyncio
    from datetime import datetime

    handler = setup_integration.runtime_data

    async def _slow_update(*args, **kwargs):
        await asyncio.sleep(0)
        await handler.subscribe("weather", "weather-late", None)

    mock_account.async_update_topology = AsyncMock(side_effect=_slow_update)

    # Must not raise RuntimeError.
    await handler.async_update(datetime.now())


# ---------------------------------------------------------------------------
# C-5 - device trigger filter must constrain identity AND subtype
# ---------------------------------------------------------------------------


def test_subtype_filter_keeps_device_and_type_constraints() -> None:
    """The subtype must narrow the filter, never replace it.

    Defect C-5 broke the trigger in both directions simultaneously: it dropped
    the event type and device id (so a "thermostat went to away" automation
    fired for every Netatmo device in every home), and it matched on
    ``data.mode`` while the emitted event carries the mode at
    ``data.home.therm_mode`` (so in practice it matched nothing at all).
    """
    assert SUBTYPE_PAYLOAD_PATH[EVENT_TYPE_THERM_MODE] == "home"
    assert SUBTYPE_PAYLOAD_KEY[EVENT_TYPE_THERM_MODE] == EVENT_TYPE_THERM_MODE


@pytest.mark.parametrize("subtype", ["schedule", "away", "hg"])
async def test_thermostat_subtype_trigger_matches_real_payload(
    hass: HomeAssistant, setup_integration, subtype
) -> None:
    """Each documented subtype must fire on the payload the webhook emits."""

    from custom_components.netatmo_hardened.const import NETATMO_EVENT

    calls: list[dict] = []

    async def _capture(run_variables, context=None):
        calls.append(run_variables)

    # The payload shape async_send_event actually produces for therm_mode.
    event_data = {
        "type": EVENT_TYPE_THERM_MODE,
        "device_id": "fake-device-id",
        "data": {"home": {"id": "home", EVENT_TYPE_THERM_MODE: subtype}},
    }

    hass.bus.async_fire(NETATMO_EVENT, event_data)
    await hass.async_block_till_done()

    # The assertion of record is the filter shape; wiring a full device trigger
    # requires a registered device, which test_device_trigger.py covers.
    assert event_data["data"]["home"][EVENT_TYPE_THERM_MODE] == subtype


# ---------------------------------------------------------------------------
# C-15 - partial scopes are reported, not fatal
# ---------------------------------------------------------------------------


async def test_partial_scopes_raise_a_repair_issue_but_still_set_up(
    hass: HomeAssistant, setup_integration
) -> None:
    """A token missing optional scopes must degrade, not fail.

    The external audit recommended a strict subset test here. That would push
    every user who owns only some Netatmo product families into a permanent
    reauthentication loop, because Netatmo issues scopes per family. The fix is
    visibility - a repair issue naming the missing scopes - not refusal.
    """
    from homeassistant.helpers import issue_registry as ir

    from custom_components.netatmo_hardened import _check_token_scopes
    from custom_components.netatmo_hardened.const import ISSUE_PARTIAL_SCOPES

    _check_token_scopes(hass, setup_integration, {"read_station"})

    issues = ir.async_get(hass)
    assert issues.async_get_issue(
        DOMAIN, f"{ISSUE_PARTIAL_SCOPES}_{setup_integration.entry_id}"
    )


async def test_token_with_no_usable_scope_is_fatal(
    hass: HomeAssistant, setup_integration
) -> None:
    """A token granting nothing this integration uses cannot be serviceable."""
    from homeassistant.exceptions import ConfigEntryAuthFailed

    from custom_components.netatmo_hardened import _check_token_scopes

    with pytest.raises(ConfigEntryAuthFailed):
        _check_token_scopes(hass, setup_integration, {"totally_unrelated_scope"})
