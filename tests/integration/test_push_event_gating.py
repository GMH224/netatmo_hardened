"""Tier 2 tests for the push-event opt-in and the E-010 retry classification.

0.1.2 makes the webhook subsystem opt-in and stops retrying registrations that
cannot ever succeed. Both are decisions taken during ``async_setup_entry`` and
inside the retry callback, so neither is reachable from tier 1: what tier 1
pins down is the classification function and the option's default, and what
this file pins down is that the lifecycle actually honours them.

Traceability: F-001 (push-event opt-in), E-010 (permanent webhook rejections).

These tests require a real Home Assistant runtime and have not been executed in
the authoring environment - see docs/VERIFICATION_REPORT.md.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pyatmo
import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component

from custom_components.netatmo_hardened.const import (
    CONF_ENABLE_WEBHOOK,
    DOMAIN,
    ISSUE_WEBHOOK_REJECTED,
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
# F-001 - push events are opt-in, and off by default
# ---------------------------------------------------------------------------


async def test_default_entry_registers_no_webhook(
    hass: HomeAssistant, push_disabled_config_entry, mock_auth, mock_account
) -> None:
    """With push events at their default, no registration is attempted.

    This is the whole point of the option. An installation with no publicly
    reachable HTTPS endpoint cannot register a webhook, and 0.1.1 would keep
    asking anyway - permanently, against a rate-limited account. The default
    must not make that call even once.
    """
    await _setup(hass, push_disabled_config_entry)

    assert push_disabled_config_entry.state is ConfigEntryState.LOADED
    assert not mock_auth.async_addwebhook.called


async def test_entry_still_loads_and_polls_without_push(
    hass: HomeAssistant, push_disabled_config_entry, mock_auth, mock_account
) -> None:
    """Disabling push must cost push only - not the integration.

    Every sensor in a weather-station deployment arrives by polling. If
    turning push off degraded anything else, the safe default would not be
    safe.
    """
    await _setup(hass, push_disabled_config_entry)

    assert push_disabled_config_entry.state is ConfigEntryState.LOADED
    assert mock_account.async_update_topology.called


async def test_opted_in_entry_registers_a_webhook(
    hass: HomeAssistant, mock_config_entry, mock_auth, mock_account
) -> None:
    """Opting in must restore the 0.1.1 behaviour exactly.

    The negative test above passes trivially if registration is broken for
    everyone, so it is only meaningful alongside this one.
    """
    with patch(
        "homeassistant.components.cloud.async_active_subscription",
        return_value=False,
    ):
        await _setup(hass, mock_config_entry)
        await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert mock_auth.async_addwebhook.called


async def test_disabling_push_reloads_and_drops_the_webhook(
    hass: HomeAssistant, mock_config_entry, mock_auth, mock_account
) -> None:
    """Turning push off must take effect without a manual reload.

    Whether the webhook subsystem is wired up is decided in
    ``async_setup_entry``, so this one option genuinely needs a reload - and
    the unload path must drop the registration at Netatmo rather than leaving
    an orphaned callback URL registered against the account.

    This is the one reload the update listener is allowed to schedule. E-001
    was about reloading on routine *token* writes; reloading on a real options
    change that cannot be applied in place is correct.
    """
    with patch(
        "homeassistant.components.cloud.async_active_subscription",
        return_value=False,
    ):
        await _setup(hass, mock_config_entry)
        await hass.async_block_till_done()

    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_ENABLE_WEBHOOK: False}
    )
    await hass.async_block_till_done()

    assert mock_auth.async_dropwebhook.called
    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert mock_config_entry.runtime_data.webhook_expected is False


async def test_unrelated_option_change_does_not_reload(
    hass: HomeAssistant, mock_config_entry, mock_auth, mock_account
) -> None:
    """Only the push-event option may reload; a weather area may not.

    Guards the E-001 boundary from the other side: the new reload branch must
    be reached by the push-event key alone, not by any options write.
    """
    with patch(
        "homeassistant.components.cloud.async_active_subscription",
        return_value=False,
    ):
        await _setup(hass, mock_config_entry)
        await hass.async_block_till_done()

    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        hass.config_entries.async_update_entry(
            mock_config_entry,
            options={
                CONF_ENABLE_WEBHOOK: True,
                "weather_areas": {"Somewhere": {}},
            },
        )
        await hass.async_block_till_done()

        assert not reload.called


# ---------------------------------------------------------------------------
# E-010 - a rejection that cannot succeed must not be retried
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [400, 401, 403, 404])
async def test_permanent_rejection_stops_retrying(
    hass: HomeAssistant, mock_config_entry, mock_auth, mock_account, status
) -> None:
    """A deterministic rejection must schedule no retry and raise an issue.

    ``400 - invalid webhook url (WH006)`` is what a deployment without a
    public HTTPS endpoint receives, on every attempt, for ever. 0.1.1 retried
    it every fifteen minutes; the operator saw a recurring warning and no
    explanation of what to change.
    """
    error = pyatmo.ApiError("invalid webhook url (WH006)")
    error.status = status
    mock_auth.async_addwebhook = AsyncMock(side_effect=error)

    with (
        patch(
            "homeassistant.components.cloud.async_active_subscription",
            return_value=False,
        ),
        patch(
            "custom_components.netatmo_hardened.webhook.async_call_later"
        ) as call_later,
    ):
        await _setup(hass, mock_config_entry)
        await hass.async_block_till_done()

        assert not call_later.called, "a permanent rejection scheduled a retry"

    issues = ir.async_get(hass)
    assert issues.async_get_issue(
        DOMAIN, f"{ISSUE_WEBHOOK_REJECTED}_{mock_config_entry.entry_id}"
    )


async def test_transient_failure_still_retries(
    hass: HomeAssistant, mock_config_entry, mock_auth, mock_account
) -> None:
    """The E-010 fix must not reintroduce the defect 0.1.1 fixed.

    Upstream abandoned the webhook on the first failure of any kind. If the
    classification were too broad, a 503 or a timeout would once again leave
    push events dead until someone noticed.
    """
    error = pyatmo.ApiError("service unavailable")
    error.status = 503
    mock_auth.async_addwebhook = AsyncMock(side_effect=error)

    with (
        patch(
            "homeassistant.components.cloud.async_active_subscription",
            return_value=False,
        ),
        patch(
            "custom_components.netatmo_hardened.webhook.async_call_later"
        ) as call_later,
    ):
        await _setup(hass, mock_config_entry)
        await hass.async_block_till_done()

        assert call_later.called, "a transient failure abandoned the webhook"

    issues = ir.async_get(hass)
    assert not issues.async_get_issue(
        DOMAIN, f"{ISSUE_WEBHOOK_REJECTED}_{mock_config_entry.entry_id}"
    )


async def test_throttling_still_retries(
    hass: HomeAssistant, mock_config_entry, mock_auth, mock_account
) -> None:
    """Netatmo answers 403 when throttling, and that is transient.

    Classifying on the HTTP status alone would treat rate limiting as a
    permanent configuration fault and give up on a registration that would
    have succeeded minutes later.
    """
    mock_auth.async_addwebhook = AsyncMock(
        side_effect=pyatmo.ApiThrottlingError("slow down")
    )

    with (
        patch(
            "homeassistant.components.cloud.async_active_subscription",
            return_value=False,
        ),
        patch(
            "custom_components.netatmo_hardened.webhook.async_call_later"
        ) as call_later,
    ):
        await _setup(hass, mock_config_entry)
        await hass.async_block_till_done()

        assert call_later.called, "throttling was treated as permanent"


async def test_disabling_push_clears_a_previous_rejection_issue(
    hass: HomeAssistant, push_disabled_config_entry, mock_auth, mock_account
) -> None:
    """Turning push off must retract the repair issue it caused.

    The issue says "fix your HTTPS endpoint or turn push off". Doing the
    second of those and still being nagged about the first would train the
    operator to ignore repair issues.
    """
    issues = ir.async_get(hass)
    issue_id = f"{ISSUE_WEBHOOK_REJECTED}_{push_disabled_config_entry.entry_id}"
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_WEBHOOK_REJECTED,
        translation_placeholders={"error": "invalid webhook url (WH006)"},
    )

    await _setup(hass, push_disabled_config_entry)

    assert not issues.async_get_issue(DOMAIN, issue_id)


async def test_successful_registration_clears_the_issue(
    hass: HomeAssistant, mock_config_entry, mock_auth, mock_account
) -> None:
    """A working webhook must retract a stale rejection issue.

    The operator may have configured the HTTPS endpoint the issue asked for.
    """
    issues = ir.async_get(hass)
    issue_id = f"{ISSUE_WEBHOOK_REJECTED}_{mock_config_entry.entry_id}"
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_WEBHOOK_REJECTED,
        translation_placeholders={"error": "invalid webhook url (WH006)"},
    )

    with patch(
        "homeassistant.components.cloud.async_active_subscription",
        return_value=False,
    ):
        await _setup(hass, mock_config_entry)
        await hass.async_block_till_done()

    assert not issues.async_get_issue(DOMAIN, issue_id)
