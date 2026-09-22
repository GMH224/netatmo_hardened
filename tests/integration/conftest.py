"""Fixtures for tier 2 integration tests.

Tier 2 requires a real Home Assistant runtime (HA >= 2026.9, Python >= 3.14.2)
supplied by ``pytest-homeassistant-custom-component``. These tests are executed
in CI, not in the authoring environment - see docs/VERIFICATION_REPORT.md for
exactly what was and was not executed before release.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.netatmo_hardened.const import CONF_ENABLE_WEBHOOK, DOMAIN

CLIENT_ID = "1234"
CLIENT_SECRET = "5678"
HOME_ID = "91763b24c43d3e344f424e8b"
CAMERA_ID = "12:34:56:00:f1:62"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading the custom integration in every test."""
    return


def _make_config_entry(options: dict) -> MockConfigEntry:
    """Build a configured Netatmo config entry with the given options."""
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        title="Netatmo",
        options=options,
        data={
            "auth_implementation": "cloud",
            "webhook_id": "test-webhook-id",
            "token": {
                "access_token": "mock-access-token",
                "refresh_token": "mock-refresh-token",
                "expires_at": 9_999_999_999,
                "scope": [
                    "read_station",
                    "read_camera",
                    "access_camera",
                    "write_camera",
                    "read_presence",
                    "access_presence",
                    "write_presence",
                    "read_thermostat",
                    "write_thermostat",
                    "read_smokedetector",
                    "read_homecoach",
                ],
            },
        },
    )


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a configured Netatmo config entry with push events enabled.

    [hardened-fork] Push events became opt-in in 0.1.2, so an entry with no
    options no longer wires up the webhook subsystem at all. The default
    fixture therefore opts *in*, because most of this suite exists to exercise
    webhook behaviour and would otherwise pass vacuously - asserting that
    nothing happened, for the wrong reason.

    Tests that need the shipped default use ``push_disabled_config_entry``.
    """
    return _make_config_entry({CONF_ENABLE_WEBHOOK: True})


@pytest.fixture
def push_disabled_config_entry() -> MockConfigEntry:
    """Return an entry with push events left at the shipped default.

    Options are empty rather than ``{"enable_webhook": False}`` so this also
    covers the upgrade path: an entry created by 0.1.0 or 0.1.1 has no
    ``enable_webhook`` key at all and must take the default, which is off.
    """
    return _make_config_entry({})


@pytest.fixture
def mock_auth():
    """Patch the pyatmo auth object used by the integration."""
    with patch(
        "custom_components.netatmo_hardened.api.AsyncConfigEntryNetatmoAuth",
        autospec=True,
    ) as mock:
        instance = mock.return_value
        instance.async_addwebhook = AsyncMock(return_value=None)
        instance.async_dropwebhook = AsyncMock(return_value=None)
        yield instance


@pytest.fixture
def mock_account():
    """Patch pyatmo.AsyncAccount with a minimal, well-formed topology."""
    with patch("pyatmo.AsyncAccount", autospec=True) as mock:
        account = mock.return_value
        account.async_update_topology = AsyncMock()
        account.async_update_status = AsyncMock()
        account.async_update_events = AsyncMock()
        account.async_update_weather_stations = AsyncMock()
        account.async_update_air_care = AsyncMock()
        account.async_update_public_weather = AsyncMock()
        account.all_home_names = {HOME_ID: "MyHome"}
        account.homes = {}
        account.modules = {}
        account.public_weather_areas = {}
        account.raw_data = {}
        yield account


@pytest.fixture
async def setup_integration(
    hass: HomeAssistant, mock_config_entry, mock_auth, mock_account
):
    """Set up the integration and return the config entry."""
    assert await async_setup_component(hass, "application_credentials", {})
    mock_config_entry.add_to_hass(hass)

    with patch(
        "homeassistant.helpers.config_entry_oauth2_flow.OAuth2Session"
    ) as session:
        session.return_value.async_ensure_token_valid = AsyncMock()
        session.return_value.token = mock_config_entry.data["token"]
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    return mock_config_entry


@pytest.fixture
def data_handler(setup_integration) -> MagicMock:
    """Return the integration's runtime data handler."""
    return setup_integration.runtime_data
