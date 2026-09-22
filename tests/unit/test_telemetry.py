"""API telemetry tests.

Traceability: F-002 (API telemetry), and the C-11 / E-009 hygiene rules
re-applied to a new output channel.

The whole module is pure by design, so everything meaningful about it is
testable here: the window arithmetic, the classification table, the
redaction, and - most importantly - the guarantee that recording an outcome
can never raise into the update path that produced it.

Time is passed in explicitly on every call, so these tests never sleep and
never patch a clock. A window test that has to wait an hour does not get
written; a window test that runs in microseconds does.
"""

from __future__ import annotations

import pytest

HOUR = 3600


# ---------------------------------------------------------------------------
# Failure ratio - window arithmetic
# ---------------------------------------------------------------------------


def test_no_samples_is_unknown_not_zero(telemetry):
    """An integration that has made no calls has no failure rate.

    Reporting 0% would be a claim the data does not support, and it is the
    more dangerous of the two possible wrong answers: it says "healthy" to an
    operator and to any alerting rule watching the number.
    """
    recorder = telemetry.ApiTelemetry()
    assert recorder.failure_ratio(1000.0) is None


def test_all_successes_is_zero(telemetry):
    """Evidence of health is reported as health."""
    recorder = telemetry.ApiTelemetry()
    for offset in range(10):
        recorder.record_success(1000.0 + offset)
    assert recorder.failure_ratio(1010.0) == 0.0


def test_all_failures_is_one(telemetry):
    """A totally broken API reports 100%, not 0% or None."""
    recorder = telemetry.ApiTelemetry()
    for offset in range(4):
        recorder.record_failure(1000.0 + offset, telemetry.ERROR_SERVER, "boom")
    assert recorder.failure_ratio(1004.0) == 1.0


@pytest.mark.parametrize(
    ("successes", "failures", "expected"),
    [(3, 1, 0.25), (1, 1, 0.5), (9, 1, 0.1), (1, 3, 0.75), (0, 5, 1.0)],
)
def test_ratio_is_failures_over_total(telemetry, successes, failures, expected):
    """The ratio is over *calls*, not over time."""
    recorder = telemetry.ApiTelemetry()
    now = 1000.0
    for _ in range(successes):
        recorder.record_success(now)
    for _ in range(failures):
        recorder.record_failure(now, telemetry.ERROR_TIMEOUT, "slow")
    assert recorder.failure_ratio(now) == pytest.approx(expected)


def test_samples_outside_the_window_are_dropped(telemetry):
    """The number must describe the last hour, not all of history.

    This is the assertion that a silently wrong window would fail. A ratio
    computed over the wrong interval still renders as a plausible percentage,
    so nothing about the displayed value would give it away.
    """
    recorder = telemetry.ApiTelemetry()

    # Ten failures, two hours ago.
    for offset in range(10):
        recorder.record_failure(1000.0 + offset, telemetry.ERROR_SERVER, "old")
    # Two successes, now.
    now = 1000.0 + 2 * HOUR
    recorder.record_success(now)
    recorder.record_success(now)

    assert recorder.failure_ratio(now) == 0.0


def test_a_sample_exactly_on_the_boundary_is_kept(telemetry):
    """Exactly one hour old is inside a one-hour window.

    Stated explicitly because an off-by-one at the boundary is invisible in
    any realistic observation - it changes the ratio by one sample.
    """
    recorder = telemetry.ApiTelemetry()
    recorder.record_failure(1000.0, telemetry.ERROR_SERVER, "edge")
    assert recorder.failure_ratio(1000.0 + HOUR) == 1.0
    # One second past the window, it is gone - and with nothing left the
    # answer is "no evidence", not "0%".
    assert recorder.failure_ratio(1000.0 + HOUR + 1) is None


def test_recovery_moves_the_ratio_down_over_time(telemetry):
    """A resolved incident must stop colouring the number."""
    recorder = telemetry.ApiTelemetry()
    for offset in range(5):
        recorder.record_failure(1000.0 + offset, telemetry.ERROR_TRANSPORT, "down")
    assert recorder.failure_ratio(1005.0) == 1.0

    later = 1000.0 + HOUR + 10
    for offset in range(5):
        recorder.record_success(later + offset)
    assert recorder.failure_ratio(later + 5) == 0.0


def test_a_backwards_clock_step_does_not_wipe_the_history(telemetry):
    """An NTP correction must not silently reset the ratio to a clean 0%.

    A VM resuming from suspend, or a Home Assistant OS host syncing its clock
    after a power cut, steps wall time backwards. If future-dated samples were
    discarded, the operator would read "0% failures" immediately after an
    outage - the most misleading possible moment for that number to be wrong.
    """
    recorder = telemetry.ApiTelemetry()
    for offset in range(4):
        recorder.record_failure(2000.0 + offset, telemetry.ERROR_SERVER, "boom")

    # Clock steps back ten minutes; the samples are now "in the future".
    assert recorder.failure_ratio(2000.0 - 600) == 1.0


# ---------------------------------------------------------------------------
# Bounded memory
# ---------------------------------------------------------------------------


def test_sample_buffer_is_bounded(telemetry):
    """Memory is capped by construction, not by the caller behaving well.

    The E-010 retry loop is the precedent: a loop nobody expected to run for
    ever ran for ever. A buffer that is only bounded by the poll interval is
    bounded by an assumption, and assumptions in this codebase have not held.
    """
    recorder = telemetry.ApiTelemetry(max_samples=50)
    for offset in range(5000):
        recorder.record_success(1000.0 + offset * 0.001)

    snapshot = recorder.snapshot(1000.0 + 5.0)
    assert snapshot.sample_count <= 50
    # The lifetime counters are not capped - they are integers, not a buffer.
    assert snapshot.total_polls == 5000


def test_window_and_cap_are_independent(telemetry):
    """Both limits apply; neither disables the other."""
    recorder = telemetry.ApiTelemetry(window_seconds=10, max_samples=1000)
    for offset in range(100):
        recorder.record_success(1000.0 + offset)
    # Only the last ~10 seconds survive the window, well under the cap.
    assert recorder.snapshot(1099.0).sample_count <= 11


# ---------------------------------------------------------------------------
# Last error / last success semantics
# ---------------------------------------------------------------------------


def test_a_success_does_not_erase_the_last_error(telemetry):
    """The operator asked for the *last* error, not the *current* one.

    Clearing it on the next successful poll would erase it within a minute of
    it happening, which makes the sensor useless for reconstructing what
    happened overnight - the main thing it is for.
    """
    recorder = telemetry.ApiTelemetry()
    recorder.record_failure(1000.0, telemetry.ERROR_TIMEOUT, "timed out")
    recorder.record_success(1060.0)

    snapshot = recorder.snapshot(1060.0)
    assert snapshot.last_error == "timed out"
    assert snapshot.last_error_type == telemetry.ERROR_TIMEOUT
    assert snapshot.last_error_timestamp == 1000.0
    assert snapshot.last_success_timestamp == 1060.0


def test_last_success_is_not_set_by_a_failure(telemetry):
    """A failed call is not a success, however recent."""
    recorder = telemetry.ApiTelemetry()
    recorder.record_failure(1000.0, telemetry.ERROR_SERVER, "boom")
    assert recorder.snapshot(1000.0).last_success_timestamp is None


def test_both_timestamps_are_tracked_independently(telemetry):
    """Each advances only on its own kind of outcome."""
    recorder = telemetry.ApiTelemetry()
    recorder.record_success(1000.0)
    recorder.record_failure(1100.0, telemetry.ERROR_SERVER, "boom")
    recorder.record_success(1200.0)
    recorder.record_success(1300.0)

    snapshot = recorder.snapshot(1300.0)
    assert snapshot.last_success_timestamp == 1300.0
    assert snapshot.last_error_timestamp == 1100.0


def test_failure_latency_is_recorded_too(telemetry):
    """A call that fails slowly and one that fails instantly differ.

    The first is a timeout or a hung connection; the second is a rejection.
    Timing only successes loses that distinction at the moment it matters.
    """
    recorder = telemetry.ApiTelemetry()
    recorder.record_failure(
        1000.0, telemetry.ERROR_TIMEOUT, "timed out", latency_seconds=30.0
    )
    assert recorder.snapshot(1000.0).latency_seconds == 30.0


def test_negative_latency_is_ignored(telemetry):
    """A negative duration is impossible and must not be published.

    It would mean the clock moved during the call. Publishing it would put a
    nonsense value on a graph and into long-term statistics.
    """
    recorder = telemetry.ApiTelemetry()
    recorder.record_success(1000.0, latency_seconds=0.5)
    recorder.record_success(1001.0, latency_seconds=-3.0)
    assert recorder.snapshot(1001.0).latency_seconds == 0.5


def test_snapshot_is_internally_consistent(telemetry):
    """The six values come from one instant, so they cannot disagree."""
    recorder = telemetry.ApiTelemetry()
    recorder.record_failure(1000.0, telemetry.ERROR_AUTH, "denied", 1.5)
    snapshot = recorder.snapshot(1000.0)

    assert snapshot.last_error_timestamp == 1000.0
    assert snapshot.last_error == "denied"
    assert snapshot.last_error_type == telemetry.ERROR_AUTH
    assert snapshot.failure_ratio == 1.0
    assert snapshot.total_failures == 1
    assert snapshot.total_polls == 1

    # Frozen, so an entity cannot mutate what the next entity will read.
    with pytest.raises(AttributeError):
        snapshot.last_error = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, "auth"),
        (403, "auth"),
        (429, "rate_limit"),
        (408, "timeout"),
        (504, "timeout"),
        (500, "server"),
        (502, "server"),
        (503, "server"),
        (400, "client"),
        (404, "client"),
        (422, "client"),
    ],
)
def test_statuses_classify(telemetry, status, expected):
    """The classification is a bounded vocabulary an automation can match."""
    assert telemetry.classify_status(status) == expected


@pytest.mark.parametrize(
    "status", [None, "", "not-a-number", object(), 3.7, [], {}, float("nan")]
)
def test_unusable_status_is_unknown_not_a_crash(telemetry, status):
    """Classification is total: no input can raise.

    It runs inside the update path, so a raise here would take out the poll
    cycle it was meant to describe.
    """
    assert telemetry.classify_status(status) == telemetry.ERROR_UNKNOWN


def test_boolean_status_is_not_silently_an_http_code(telemetry):
    """``bool`` is a subclass of ``int`` and ``int(True) == 1``.

    A caller passing a flag by mistake must be reported as unknown, not
    classified against status code 1.
    """
    assert telemetry.classify_status(True) == telemetry.ERROR_UNKNOWN
    assert telemetry.classify_status(False) == telemetry.ERROR_UNKNOWN


def test_numeric_string_status_is_accepted(telemetry):
    """Some clients surface the status as text."""
    assert telemetry.classify_status("503") == telemetry.ERROR_SERVER


def test_unrecognised_error_type_is_coerced_to_unknown(telemetry):
    """A sensor with device_class ENUM must only ever hold a listed option.

    Home Assistant logs an error and drops the state if it does not, so an
    unexpected classification would blank the sensor rather than degrade it.
    """
    recorder = telemetry.ApiTelemetry()
    recorder.record_failure(1000.0, "something-invented", "boom")
    assert recorder.snapshot(1000.0).last_error_type == telemetry.ERROR_UNKNOWN


def test_every_classification_is_a_declared_option(telemetry):
    """`classify_status` can only produce values the sensor declares."""
    produced = {
        telemetry.classify_status(status)
        for status in [*range(100, 600), None, "x", object()]
    }
    assert produced <= set(telemetry.ERROR_TYPES)


# ---------------------------------------------------------------------------
# Redaction - C-11 and E-009 re-applied to a new output channel
# ---------------------------------------------------------------------------


def test_webhook_id_is_never_published_as_a_state(telemetry):
    """Defect C-11 was the webhook id in a log. A state is worse.

    The webhook id is a bearer credential. An entity state is readable by
    every dashboard, template, logbook entry and history export - a far wider
    blast radius than a debug log, and a webhook-related API error is exactly
    the kind that quotes the URL containing it.
    """
    message = (
        "400 Bad request when posting to "
        "https://example.duckdns.org/api/webhook/abc123secrettoken (WH006)"
    )
    result = telemetry.redact_error_message(message)

    assert "abc123secrettoken" not in result
    assert "<redacted>" in result
    # The useful part survives - a redaction that destroys the diagnosis
    # defeats the purpose of the sensor.
    assert "WH006" in result


def test_multiple_webhook_urls_are_all_redacted(telemetry):
    """One pass must not leave the second occurrence in place."""
    message = "failed /api/webhook/firstsecret then retried /api/webhook/secondsecret"
    result = telemetry.redact_error_message(message)
    assert "firstsecret" not in result
    assert "secondsecret" not in result


def test_control_characters_are_stripped(telemetry):
    """Defect E-009, re-applied to a value rendered in a UI."""
    result = telemetry.redact_error_message("bad\x00value\x1bwith\x7fcontrols")
    assert result == "badvaluewithcontrols"


def test_invisible_format_characters_are_stripped(telemetry):
    """Zero-width and byte-order marks are not legitimate message content."""
    result = telemetry.redact_error_message("a﻿b​c")
    assert result == "abc"


def test_message_is_truncated_below_the_state_limit(telemetry):
    """Home Assistant drops a state above 255 characters entirely.

    An untruncated message would blank the very sensor meant to report it -
    the failure mode being reported would destroy the report.
    """
    result = telemetry.redact_error_message("x" * 5000)
    assert len(result) <= telemetry.MAX_ERROR_MESSAGE_LENGTH
    assert len(result) < 255


@pytest.mark.parametrize("message", [None, 42, object(), ValueError("boom"), b"bytes"])
def test_any_message_type_is_accepted(telemetry, message):
    """Redaction is total: the caller passes whatever the exception carried."""
    assert isinstance(telemetry.redact_error_message(message), str)


def test_none_message_becomes_empty_not_the_word_none(telemetry):
    """The string "None" in a UI is a bug that looks like data."""
    assert telemetry.redact_error_message(None) == ""


# ---------------------------------------------------------------------------
# Telemetry must never break the thing it measures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error_type", "message"),
    [
        (None, None),
        ("", ""),
        ("unknown", object()),
        ("auth", ValueError("nested")),
        (123, {"a": 1}),
    ],
)
def test_recording_a_failure_never_raises(telemetry, error_type, message):
    """This runs inside the poll loop.

    An exception escaping here would propagate out of the publisher update
    cycle and take the poll down with it - turning a monitoring feature into
    the outage it was added to observe. The coordinator wraps the call as a
    second line of defence, but the recorder is required to be total on its
    own; relying solely on the wrapper would mean the sensor silently stops
    updating instead.
    """
    recorder = telemetry.ApiTelemetry()
    recorder.record_failure(1000.0, error_type, message)
    assert recorder.snapshot(1000.0).total_failures == 1


def test_recording_a_success_never_raises(telemetry):
    """Same guarantee on the happy path."""
    recorder = telemetry.ApiTelemetry()
    recorder.record_success(1000.0, latency_seconds=None)
    recorder.record_success(1000.0, latency_seconds="not a number")  # type: ignore[arg-type]
    assert recorder.snapshot(1000.0).total_polls == 2
