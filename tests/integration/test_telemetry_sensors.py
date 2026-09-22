"""Tier 2 tests for the API telemetry sensors.

Traceability: F-002.

Tier 1 covers the recorder itself - the window arithmetic, the
classification, the redaction, the totality guarantee. What it cannot reach
is the part that only exists inside a running Home Assistant: whether the
sensors are created at all, whether they stay readable while the API is
failing, and whether the coordinator actually records what it observes.

That last group is the whole point of the feature. A telemetry sensor that
goes unavailable during an outage reports nothing at the only moment anyone
looks at it, and no amount of tier-1 coverage of the recorder would reveal
that.

These tests require a real Home Assistant runtime and have not been executed
in the authoring environment - see docs/VERIFICATION_REPORT.md.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pyatmo
import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.netatmo_hardened.telemetry import (
    ERROR_AUTH,
    ERROR_SERVER,
    ERROR_THROTTLING,
    ERROR_TIMEOUT,
    ERROR_TRANSPORT,
)

TELEMETRY_ENTITIES = (
    "sensor.netatmo_api_api_failure_ratio_1h",
    "sensor.netatmo_api_api_last_error",
    "sensor.netatmo_api_api_last_error_type",
    "sensor.netatmo_api_api_last_error_time",
    "sensor.netatmo_api_api_last_success",
    "sensor.netatmo_api_api_poll_latency",
)


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
# The sensors exist, unconditionally
# ---------------------------------------------------------------------------


async def test_all_six_telemetry_sensors_are_created(
    hass: HomeAssistant, push_disabled_config_entry, mock_auth, mock_account
) -> None:
    """All six exist on a bare account.

    They describe the API connection, not a device, so an account with no
    hardware at all must still get them. Creating them from a device-discovery
    dispatcher - as every other sensor in this integration is created - would
    have made them depend on there being something to discover.
    """
    await _setup(hass, push_disabled_config_entry)

    registry = er.async_get(hass)
    for entity_id in TELEMETRY_ENTITIES:
        assert registry.async_get(entity_id) is not None, f"{entity_id} missing"


async def test_telemetry_sensors_are_diagnostic(
    hass: HomeAssistant, push_disabled_config_entry, mock_auth, mock_account
) -> None:
    """They belong in the diagnostic section, not beside the temperatures."""
    await _setup(hass, push_disabled_config_entry)

    registry = er.async_get(hass)
    for entity_id in TELEMETRY_ENTITIES:
        entry = registry.async_get(entity_id)
        assert entry is not None
        assert entry.entity_category == er.EntityCategory.DIAGNOSTIC


async def test_telemetry_sensors_share_one_service_device(
    hass: HomeAssistant, push_disabled_config_entry, mock_auth, mock_account
) -> None:
    """One device for the API, not six orphan entities.

    A service device rather than a physical one: the Netatmo cloud endpoint
    has no firmware, serial or model, and claiming otherwise would put
    fictional hardware into the device registry.
    """
    await _setup(hass, push_disabled_config_entry)

    registry = er.async_get(hass)
    device_ids = {
        registry.async_get(entity_id).device_id for entity_id in TELEMETRY_ENTITIES
    }
    assert len(device_ids) == 1
    assert None not in device_ids


# ---------------------------------------------------------------------------
# The reason the feature exists: readable during an outage
# ---------------------------------------------------------------------------


async def test_telemetry_stays_available_while_the_api_fails(
    hass: HomeAssistant, push_disabled_config_entry, mock_auth, mock_account
) -> None:
    """This is the central requirement of F-002.

    Every other entity in this integration is unavailable precisely when the
    API is failing, because availability is derived from publisher health. If
    the telemetry sensors inherited that rule they would report nothing at the
    only moment an operator reads them, and the feature would be worthless.

    The mechanism under test is that ``_publishers`` is left empty, so the
    inherited availability check reduces to ``all([]) is True``. That is
    subtle enough to be "cleaned up" by a future change, which is why it is
    asserted rather than left to the comment that explains it.
    """
    await _setup(hass, push_disabled_config_entry)

    error = pyatmo.ApiError("service unavailable")
    error.status = 503
    mock_account.async_update_topology = AsyncMock(side_effect=error)
    mock_account.async_update_status = AsyncMock(side_effect=error)
    mock_account.async_update_weather_stations = AsyncMock(side_effect=error)

    data_handler = push_disabled_config_entry.runtime_data
    for signal_name in list(data_handler.publisher):
        await data_handler.async_fetch_data(signal_name)
    await hass.async_block_till_done()

    for entity_id in TELEMETRY_ENTITIES:
        state = hass.states.get(entity_id)
        assert state is not None, f"{entity_id} has no state"
        assert state.state != "unavailable", f"{entity_id} went unavailable"


async def test_failure_ratio_reports_an_ongoing_outage(
    hass: HomeAssistant, push_disabled_config_entry, mock_auth, mock_account
) -> None:
    """A fully failing API reads 100%, not unknown and not 0%."""
    await _setup(hass, push_disabled_config_entry)

    error = pyatmo.ApiError("service unavailable")
    error.status = 503
    data_handler = push_disabled_config_entry.runtime_data
    signal_name = next(iter(data_handler.publisher))

    for method in (
        "async_update_topology",
        "async_update_status",
        "async_update_weather_stations",
        "async_update_air_care",
        "async_update_events",
        "async_update_public_weather",
    ):
        setattr(mock_account, method, AsyncMock(side_effect=error))

    data_handler.telemetry = type(data_handler.telemetry)()
    for _ in range(4):
        await data_handler.async_fetch_data(signal_name)
    await hass.async_block_till_done()

    state = hass.states.get("sensor.netatmo_api_api_failure_ratio_1h")
    assert float(state.state) == pytest.approx(100.0)
    assert state.attributes["sample_count"] == 4


# ---------------------------------------------------------------------------
# The coordinator records what it observes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exception", "status", "expected"),
    [
        (pyatmo.ApiError("server"), 503, ERROR_SERVER),
        (pyatmo.ApiError("denied"), 401, ERROR_AUTH),
        (TimeoutError(), None, ERROR_TIMEOUT),
        (pyatmo.ApiThrottlingError("slow down"), 403, ERROR_THROTTLING),
    ],
    ids=["server", "auth", "timeout", "throttling"],
)
async def test_error_type_is_classified_from_the_real_exception(
    hass: HomeAssistant,
    push_disabled_config_entry,
    mock_auth,
    mock_account,
    exception,
    status,
    expected,
) -> None:
    """Classification must survive the round trip through the fetch path.

    Throttling is the case that matters: Netatmo answers **403** when rate
    limiting, so classifying on the status alone would report it as an
    authorization failure and send an operator chasing a reauthentication
    that was never needed. This is the same ambiguity E-010 had to handle,
    and getting it wrong here is quieter - it produces a plausible but wrong
    diagnosis rather than a visible fault.
    """
    await _setup(hass, push_disabled_config_entry)

    if status is not None:
        exception.status = status

    data_handler = push_disabled_config_entry.runtime_data
    signal_name = next(iter(data_handler.publisher))
    method = data_handler.publisher[signal_name].method
    setattr(mock_account, method, AsyncMock(side_effect=exception))

    await data_handler.async_fetch_data(signal_name)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.netatmo_api_api_last_error_type").state == expected


async def test_last_success_is_populated_by_a_normal_poll(
    hass: HomeAssistant, push_disabled_config_entry, mock_auth, mock_account
) -> None:
    """The happy path must write a timestamp, not leave it unknown."""
    await _setup(hass, push_disabled_config_entry)

    data_handler = push_disabled_config_entry.runtime_data
    await data_handler.async_fetch_data(next(iter(data_handler.publisher)))
    await hass.async_block_till_done()

    state = hass.states.get("sensor.netatmo_api_api_last_success")
    assert state.state not in ("unknown", "unavailable")


async def test_last_error_survives_a_subsequent_success(
    hass: HomeAssistant, push_disabled_config_entry, mock_auth, mock_account
) -> None:
    """A recovered fault must remain visible.

    Clearing the last error on the next successful poll would erase it within
    a minute, which defeats the purpose of having it: the operator reads it
    the morning after, not during.
    """
    await _setup(hass, push_disabled_config_entry)

    data_handler = push_disabled_config_entry.runtime_data
    signal_name = next(iter(data_handler.publisher))
    method = data_handler.publisher[signal_name].method

    error = pyatmo.ApiError("transient blip")
    error.status = 502
    setattr(mock_account, method, AsyncMock(side_effect=error))
    await data_handler.async_fetch_data(signal_name)

    setattr(mock_account, method, AsyncMock())
    await data_handler.async_fetch_data(signal_name)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.netatmo_api_api_last_error").state == (
        "transient blip"
    )
    assert hass.states.get("sensor.netatmo_api_api_last_error_type").state == (
        ERROR_SERVER
    )
    assert hass.states.get("sensor.netatmo_api_api_last_success").state not in (
        "unknown",
        "unavailable",
    )


async def test_a_broken_recorder_cannot_break_the_poll(
    hass: HomeAssistant, push_disabled_config_entry, mock_auth, mock_account
) -> None:
    """Telemetry must never take down the thing it measures.

    The recorder is total on its own (tier 1 proves that), but the coordinator
    also wraps the call. This asserts the wrapper: if a future change makes
    recording raise, the poll cycle must survive it and the entry must stay
    loaded. A monitoring feature that can cause an outage is worse than none.
    """
    await _setup(hass, push_disabled_config_entry)

    data_handler = push_disabled_config_entry.runtime_data
    signal_name = next(iter(data_handler.publisher))

    with patch.object(
        data_handler.telemetry,
        "record_success",
        side_effect=RuntimeError("telemetry exploded"),
    ):
        await data_handler.async_fetch_data(signal_name)
        await hass.async_block_till_done()

    assert push_disabled_config_entry.state is ConfigEntryState.LOADED
    assert data_handler.publisher[signal_name].available


async def test_webhook_id_never_reaches_a_telemetry_state(
    hass: HomeAssistant, mock_config_entry, mock_auth, mock_account
) -> None:
    """Defect C-11, re-applied to a channel far more exposed than a log.

    An entity state is readable by every dashboard, template, logbook entry
    and history export. A webhook-related API error is exactly the kind that
    quotes the callback URL, and that URL carries a bearer credential.
    """
    await _setup(hass, mock_config_entry)

    webhook_id = mock_config_entry.data["webhook_id"]
    error = pyatmo.ApiError(
        f"400 invalid webhook url https://example.com/api/webhook/{webhook_id} (WH006)"
    )
    error.status = 400

    data_handler = mock_config_entry.runtime_data
    signal_name = next(iter(data_handler.publisher))
    method = data_handler.publisher[signal_name].method
    setattr(mock_account, method, AsyncMock(side_effect=error))

    await data_handler.async_fetch_data(signal_name)
    await hass.async_block_till_done()

    state = hass.states.get("sensor.netatmo_api_api_last_error")
    assert webhook_id not in state.state
    assert "<redacted>" in state.state
    # The diagnosis must survive the redaction, or the sensor is useless.
    assert "WH006" in state.state


async def test_no_telemetry_state_exceeds_the_state_length_limit(
    hass: HomeAssistant, push_disabled_config_entry, mock_auth, mock_account
) -> None:
    """Home Assistant drops a state over 255 characters entirely.

    An untruncated API error would blank the very sensor meant to report it.
    """
    await _setup(hass, push_disabled_config_entry)

    error = pyatmo.ApiError("x" * 5000)
    error.status = 500

    data_handler = push_disabled_config_entry.runtime_data
    signal_name = next(iter(data_handler.publisher))
    method = data_handler.publisher[signal_name].method
    setattr(mock_account, method, AsyncMock(side_effect=error))

    await data_handler.async_fetch_data(signal_name)
    await hass.async_block_till_done()

    state = hass.states.get("sensor.netatmo_api_api_last_error")
    assert state.state not in ("unknown", "unavailable")
    assert len(state.state) < 255


async def test_transport_errors_are_classified_as_network(
    hass: HomeAssistant, push_disabled_config_entry, mock_auth, mock_account
) -> None:
    """An aiohttp failure is a local network fault, not a Netatmo fault.

    Telling those apart is the main thing an operator wants from these
    sensors: one is worth calling the ISP about and the other is worth
    waiting out.
    """
    import aiohttp

    await _setup(hass, push_disabled_config_entry)

    data_handler = push_disabled_config_entry.runtime_data
    signal_name = next(iter(data_handler.publisher))
    method = data_handler.publisher[signal_name].method
    setattr(
        mock_account,
        method,
        AsyncMock(side_effect=aiohttp.ClientConnectionError("no route to host")),
    )

    await data_handler.async_fetch_data(signal_name)
    await hass.async_block_till_done()

    assert (
        hass.states.get("sensor.netatmo_api_api_last_error_type").state
        == ERROR_TRANSPORT
    )
