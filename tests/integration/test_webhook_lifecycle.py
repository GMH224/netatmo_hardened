"""Webhook registration, recovery and shutdown lifecycle.

Traceability: C-4, C-6, C-12, and the recovery matrix in docs/TEST_PLAN.md.

These are the tests that matter most for the fork's stated purpose. The whole
reason this fork exists is that upstream gave up permanently on a transient
webhook failure; if that recovery regresses, the fork has no reason to exist.
"""

from __future__ import annotations

from unittest.mock import patch

import aiohttp
import pyatmo
import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.netatmo_hardened import webhook as netatmo_webhook


async def test_registration_retries_after_transient_api_error(
    hass: HomeAssistant, setup_integration, mock_auth
) -> None:
    """A transient failure must schedule a retry, not give up permanently.

    Upstream logged the error once and stopped, leaving entities unavailable
    until a human noticed and pressed Reload - in one reported case for three
    days (home-assistant/core#178195).
    """
    mock_auth.async_addwebhook.side_effect = [
        pyatmo.ApiError("429 Too Many Requests", status=429),
        None,
    ]

    await netatmo_webhook.async_register_webhook(hass, setup_integration)
    await hass.async_block_till_done()

    assert setup_integration.runtime_data.webhook_retry_cancel is not None

    async_fire_time_changed(
        hass,
        dt_util.utcnow()
        + dt_util.dt.timedelta(seconds=netatmo_webhook.WEBHOOK_RETRY_DELAYS[0] + 1),
    )
    await hass.async_block_till_done()

    assert mock_auth.async_addwebhook.call_count == 2


async def test_cloudhook_failure_is_inside_the_retry_envelope(
    hass: HomeAssistant, setup_integration, mock_auth
) -> None:
    """Cloudhook creation must be retried like any other registration step.

    Defect C-6: upstream created the cloudhook *outside* the try block, so a
    transient cloud outage propagated out of registration and the retry logic -
    the entire point of the fork - was never reached.
    """
    with (
        patch(
            "homeassistant.components.cloud.async_active_subscription",
            return_value=True,
        ),
        patch(
            "homeassistant.components.cloud.async_create_cloudhook",
            side_effect=TimeoutError("cloud unreachable"),
        ) as create_cloudhook,
    ):
        await netatmo_webhook.async_register_webhook(hass, setup_integration)
        await hass.async_block_till_done()

        assert create_cloudhook.called
        # The failure was absorbed and a retry scheduled, not raised.
        assert setup_integration.runtime_data.webhook_retry_cancel is not None


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("read timeout"),
        aiohttp.ClientConnectorError(None, OSError("connection refused")),
        pyatmo.ApiError("500 Server Error", status=500),
    ],
    ids=["timeout", "connection-error", "api-error"],
)
async def test_unload_survives_a_failing_dropwebhook(
    hass: HomeAssistant, setup_integration, mock_auth, error
) -> None:
    """Unload must succeed even when the remote cleanup call fails.

    Defect C-4, and the most consequential single fix in this release.
    Upstream caught only ``pyatmo.ApiError``. A timeout therefore propagated
    out of ``async_unload_entry``, which aborts the unload - and because a
    reload is an unload followed by a setup, it also broke every reload,
    including the coordinator's own stale-data watchdog. The watchdog fires
    precisely when the network is unreachable, which is precisely when
    ``async_dropwebhook()`` times out, so the recovery mechanism disabled
    itself exactly when it was needed.
    """
    mock_auth.async_dropwebhook.side_effect = error

    assert await hass.config_entries.async_unload(setup_integration.entry_id)
    await hass.async_block_till_done()


async def test_reload_succeeds_while_the_backend_is_unreachable(
    hass: HomeAssistant, setup_integration, mock_auth
) -> None:
    """The watchdog's recovery action must work during a network outage."""
    mock_auth.async_dropwebhook.side_effect = TimeoutError("network down")

    assert await hass.config_entries.async_reload(setup_integration.entry_id)
    await hass.async_block_till_done()


async def test_stop_listener_is_installed_only_once(
    hass: HomeAssistant, setup_integration, mock_auth
) -> None:
    """Repeated registration must not accumulate shutdown listeners.

    Defect C-12: ``manage_cloudhook`` re-registers on every HA Cloud reconnect,
    and upstream added a fresh EVENT_HOMEASSISTANT_STOP listener each time via
    ``async_on_unload``, which never prunes. A long-lived instance fired one
    duplicate ``async_dropwebhook()`` per reconnect at shutdown.
    """
    for _ in range(10):
        await netatmo_webhook.async_register_webhook(hass, setup_integration)
        await hass.async_block_till_done()

    listeners = hass.bus.async_listeners().get(EVENT_HOMEASSISTANT_STOP, 0)

    mock_auth.async_dropwebhook.reset_mock()
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()

    assert mock_auth.async_dropwebhook.call_count <= 1, (
        f"duplicate unregister storm at shutdown ({listeners} stop listeners)"
    )


async def test_pending_retry_is_cancelled_on_unload(
    hass: HomeAssistant, setup_integration, mock_auth
) -> None:
    """A scheduled retry must not fire against an unloaded entry."""
    mock_auth.async_addwebhook.side_effect = pyatmo.ApiError("boom", status=500)

    await netatmo_webhook.async_register_webhook(hass, setup_integration)
    await hass.async_block_till_done()
    assert setup_integration.runtime_data.webhook_retry_cancel is not None

    await hass.config_entries.async_unload(setup_integration.entry_id)
    await hass.async_block_till_done()

    mock_auth.async_addwebhook.reset_mock()
    async_fire_time_changed(hass, dt_util.utcnow() + dt_util.dt.timedelta(hours=1))
    await hass.async_block_till_done()

    assert not mock_auth.async_addwebhook.called


async def test_server_retry_after_is_honoured(
    hass: HomeAssistant, setup_integration, mock_auth
) -> None:
    """When Netatmo says when to come back, respect it rather than guessing."""
    from pyatmo.exceptions import ApiTooManyRequestError

    err = ApiTooManyRequestError("429", retry_after=42.0, status=429)
    assert netatmo_webhook._retry_delay(err, 0) == 42.0
    # Without a server hint, fall back to the schedule.
    assert (
        netatmo_webhook._retry_delay(pyatmo.ApiError("x"), 0)
        == (netatmo_webhook.WEBHOOK_RETRY_DELAYS[0])
    )


async def test_webhook_url_is_never_logged(
    hass: HomeAssistant, setup_integration, mock_auth, caplog
) -> None:
    """The webhook id is a bearer secret and must stay out of the logs.

    Defect C-11 / external finding SEC-001. Home Assistant documents the
    webhook id as equivalent to a password; upstream wrote the complete URL to
    the debug log, putting it into log files, backups and bug reports.
    """
    import logging

    caplog.set_level(logging.DEBUG)

    await netatmo_webhook.async_register_webhook(hass, setup_integration)
    await hass.async_block_till_done()

    assert "test-webhook-id" not in caplog.text
    assert "/api/webhook/" not in caplog.text
