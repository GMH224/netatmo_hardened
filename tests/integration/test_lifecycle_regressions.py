"""Regression tests for the lifecycle defects introduced in 0.1.0.

Traceability: E-001 (token refresh reload), E-002 (setup-path webhook
registration), E-004 (actuation results), E-007 (camera timeouts).

These are the tests that would have caught 0.1.0's two worst defects had the
tier 2 suite been executed before tagging. They are deliberately written
against the *lifecycle*, not against the helpers - the helpers were fine in
both cases; the wiring was not.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pyatmo
import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.netatmo_hardened import webhook as netatmo_webhook

# ---------------------------------------------------------------------------
# E-001 - a routine OAuth token refresh must not reload the integration
# ---------------------------------------------------------------------------


async def test_token_refresh_does_not_reload(
    hass: HomeAssistant, setup_integration
) -> None:
    """Persisting a refreshed access token must not schedule a reload.

    Home Assistant's ``OAuth2Session.async_ensure_token_valid()`` writes every
    refreshed token back through ``async_update_entry``, which fires the config
    entry update listener. 0.1.0 compared the token against the one the running
    handler was built with and reloaded on any difference - so with a Netatmo
    access token lifetime of roughly three hours, the integration tore itself
    down and rebuilt about eight times a day, indefinitely.
    """
    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        hass.config_entries.async_update_entry(
            setup_integration,
            data={
                **setup_integration.data,
                "token": {
                    **setup_integration.data["token"],
                    "access_token": "a-freshly-refreshed-token",
                },
            },
        )
        await hass.async_block_till_done()

        assert not reload.called, "a routine token refresh triggered a full reload"


async def test_repeated_token_refresh_never_reloads(
    hass: HomeAssistant, setup_integration
) -> None:
    """Eight refreshes is one day of normal operation."""
    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        for index in range(8):
            hass.config_entries.async_update_entry(
                setup_integration,
                data={
                    **setup_integration.data,
                    "token": {
                        **setup_integration.data["token"],
                        "access_token": f"token-{index}",
                    },
                },
            )
            await hass.async_block_till_done()

        assert reload.call_count == 0


async def test_options_change_still_refreshes_public_weather(
    hass: HomeAssistant, setup_integration
) -> None:
    """The listener must still do the one job it is actually for."""
    from custom_components.netatmo_hardened.const import DOMAIN

    signals: list[object] = []

    from homeassistant.helpers.dispatcher import async_dispatcher_connect

    async_dispatcher_connect(
        hass,
        f"signal-{DOMAIN}-public-update-{setup_integration.entry_id}",
        lambda *args: signals.append(args),
    )

    hass.config_entries.async_update_entry(
        setup_integration, options={"weather_areas": {"Somewhere": {}}}
    )
    await hass.async_block_till_done()

    assert signals, "an options change did not refresh public weather entities"


async def test_token_refresh_does_not_churn_public_weather(
    hass: HomeAssistant, setup_integration
) -> None:
    """A token write must not rebuild public weather entities either."""
    from homeassistant.helpers.dispatcher import async_dispatcher_connect

    from custom_components.netatmo_hardened.const import DOMAIN

    signals: list[object] = []
    async_dispatcher_connect(
        hass,
        f"signal-{DOMAIN}-public-update-{setup_integration.entry_id}",
        lambda *args: signals.append(args),
    )

    hass.config_entries.async_update_entry(
        setup_integration,
        data={
            **setup_integration.data,
            "token": {**setup_integration.data["token"], "access_token": "rotated"},
        },
    )
    await hass.async_block_till_done()

    assert not signals


# ---------------------------------------------------------------------------
# E-002 - the webhook must register during setup, not only on a cloud change
# ---------------------------------------------------------------------------


async def test_webhook_registers_during_setup_with_active_cloud(
    hass: HomeAssistant, mock_config_entry, mock_auth, mock_account
) -> None:
    """A connected cloud subscriber must have a webhook after setup.

    This is defect E-002 exactly. 0.1.0 guarded ``async_register_webhook`` on
    ``ConfigEntryState.LOADED``, but ``async_setup_entry`` awaits it directly,
    while the entry is still ``SETUP_IN_PROGRESS``. Registration returned
    immediately. Because ``manage_cloudhook`` only fires on a *change* of cloud
    connection state, a subscriber whose cloud stayed connected never got a
    webhook for the lifetime of that runtime - silently.
    """
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "application_credentials", {})
    mock_config_entry.add_to_hass(hass)

    with (
        patch(
            "homeassistant.components.cloud.async_active_subscription",
            return_value=True,
        ),
        patch("homeassistant.components.cloud.async_is_connected", return_value=True),
        patch(
            "homeassistant.components.cloud.async_create_cloudhook",
            AsyncMock(return_value="https://hooks.example.invalid/abc"),
        ),
        patch(
            "homeassistant.helpers.config_entry_oauth2_flow.OAuth2Session"
        ) as session,
    ):
        session.return_value.async_ensure_token_valid = AsyncMock()
        session.return_value.token = mock_config_entry.data["token"]

        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert mock_auth.async_addwebhook.called, (
        "no webhook was registered during setup for a connected cloud subscriber"
    )


async def test_registration_is_not_gated_on_loaded_state(
    hass: HomeAssistant, setup_integration, mock_auth
) -> None:
    """Registration must proceed while the entry is still setting up."""
    mock_auth.async_addwebhook.reset_mock()

    with patch.object(
        type(setup_integration),
        "state",
        ConfigEntryState.SETUP_IN_PROGRESS,
        create=True,
    ):
        await netatmo_webhook.async_register_webhook(hass, setup_integration)
        await hass.async_block_till_done()

    assert mock_auth.async_addwebhook.called


# ---------------------------------------------------------------------------
# E-004 - a rejected command must not publish success
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("module", "entity_cls", "method", "control"),
    [
        ("switch", "NetatmoSwitch", "async_turn_on", "async_on"),
        ("switch", "NetatmoSwitch", "async_turn_off", "async_off"),
    ],
    ids=["switch-on", "switch-off"],
)
async def test_rejected_command_raises_and_leaves_state_alone(
    hass: HomeAssistant, setup_integration, module, entity_cls, method, control
) -> None:
    """``False`` from pyatmo must raise, not become optimistic state.

    pyatmo's control methods return ``bool`` and answer ``False`` when the
    Netatmo API rejects the request, without raising. 0.1.0 awaited the call
    and then wrote the new state unconditionally, so a rejected command still
    showed as ON in Home Assistant and any automation keyed on that transition
    ran on a state the device never entered (defect E-004).
    """
    import importlib

    platform = importlib.import_module(f"custom_components.netatmo_hardened.{module}")
    entity = object.__new__(getattr(platform, entity_cls))
    entity.device = MagicMock()
    setattr(entity.device, control, AsyncMock(return_value=False))
    entity._attr_is_on = None
    entity.async_write_ha_state = MagicMock()

    with pytest.raises(HomeAssistantError):
        await getattr(entity, method)()

    assert entity._attr_is_on is None, "state was published for a rejected command"
    assert not entity.async_write_ha_state.called


async def test_accepted_command_updates_state(
    hass: HomeAssistant, setup_integration
) -> None:
    """The success path must be unaffected."""
    import importlib

    platform = importlib.import_module("custom_components.netatmo_hardened.switch")
    entity = object.__new__(platform.NetatmoSwitch)
    entity.device = MagicMock()
    entity.device.async_on = AsyncMock(return_value=True)
    entity._attr_is_on = False
    entity.async_write_ha_state = MagicMock()

    await entity.async_turn_on()

    assert entity._attr_is_on is True
    assert entity.async_write_ha_state.called


async def test_command_with_no_success_indication_is_not_treated_as_failure(
    hass: HomeAssistant, setup_integration
) -> None:
    """Room thermostat methods return None and must not raise.

    ``Room.async_therm_set`` / ``_manual`` / ``_home`` return ``None``. Reading
    that as failure would make every setpoint change raise - fabricating a
    guarantee the dependency does not provide, in the opposite direction from
    E-004.
    """
    import importlib

    platform = importlib.import_module("custom_components.netatmo_hardened.switch")
    entity = object.__new__(platform.NetatmoSwitch)
    entity.device = MagicMock()
    entity.device.async_on = AsyncMock(return_value=None)
    entity._attr_is_on = False
    entity.async_write_ha_state = MagicMock()

    await entity.async_turn_on()

    assert entity._attr_is_on is True


# ---------------------------------------------------------------------------
# E-007 - camera network timeouts are recoverable, not exceptional
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [TimeoutError("stalled"), pyatmo.ApiError("api down", status=500)],
    ids=["timeout", "api-error"],
)
async def test_camera_snapshot_timeout_is_absorbed(
    hass: HomeAssistant, setup_integration, error
) -> None:
    """A stalled camera returns no image rather than raising.

    pyatmo's image request carries a finite HTTP timeout, and a camera that
    accepts the connection but stalls raises ``asyncio.TimeoutError`` - which
    is ``TimeoutError`` on Python 3.11+ and is not an ``aiohttp.ClientError``,
    so 0.1.0's exception tuple missed it entirely (defect E-007).
    """
    import importlib

    platform = importlib.import_module("custom_components.netatmo_hardened.camera")
    entity = object.__new__(platform.NetatmoCamera)
    entity.device = MagicMock()
    entity.device.async_get_live_snapshot = AsyncMock(side_effect=error)

    assert await entity.async_camera_image() is None


async def test_camera_url_refresh_timeout_is_absorbed(
    hass: HomeAssistant, setup_integration
) -> None:
    """A stalled URL refresh falls back to the cached URL."""
    import importlib

    platform = importlib.import_module("custom_components.netatmo_hardened.camera")
    entity = object.__new__(platform.NetatmoCamera)
    entity.device = MagicMock()
    entity.device.is_local = True
    entity.device.local_url = "http://camera.invalid"
    entity.device.async_update_camera_urls = AsyncMock(side_effect=TimeoutError())
    entity._quality = "high"

    result = await entity.stream_source()

    assert result.startswith("http://camera.invalid")
