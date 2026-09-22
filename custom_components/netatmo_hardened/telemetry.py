"""API telemetry for the Netatmo integration.

[hardened-fork] Added in 0.1.3 (feature F-002).

The integration already knew whether it was healthy - every entity's
``available`` property depends on it. What it could not answer was *how*
healthy, or *when it last was not*: an operator who noticed a gap in a
history graph had no way to tell a Netatmo outage from a local network fault
from a rate limit, and no way to see that the API had been failing 30% of the
time for an hour while every entity still showed a value.

That is an observability gap rather than a defect. In an ICS context it is
still worth closing, because the distinction between "this reading is stale"
and "this reading is wrong" is not visible from the reading.

This module is deliberately free of Home Assistant and pyatmo imports so it
can be unit-tested without a runtime (``tests/unit/``), for the same reason
``helper.py`` and ``event_validation.py`` are. The window arithmetic below is
the kind of thing that is easy to get subtly wrong and impossible to notice:
a ratio that is quietly computed over the wrong interval still renders as a
plausible percentage.

Two rules govern everything here:

1. **Telemetry must never break the thing it measures.** Every recording
   entry point is total: it accepts any input, and it cannot raise into the
   update path. A monitoring subsystem that can take down the system it
   monitors is worse than no monitoring.
2. **Memory is bounded by construction, not by convention.** The sample
   window is capped at a fixed number of entries as well as by age, so a
   pathological poll rate cannot grow it without limit.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Final

# The reporting window for the failure ratio, in seconds.
#
# One hour is chosen to match how the number is read: it answers "is the API
# misbehaving right now", not "has it ever misbehaved". A window much shorter
# is dominated by single failures; much longer and a resolved incident keeps
# colouring the number long after it ended.
FAILURE_RATIO_WINDOW_SECONDS: Final = 3600

# Hard cap on retained samples, independent of the time window.
#
# At the integration's own cadence (a publisher poll every 60 s at most, times
# a handful of publishers) an hour holds well under a hundred samples. The cap
# is an order of magnitude above that, so it never truncates in normal
# operation - it exists so that a future change to the poll interval, or a
# pathological retry loop, cannot turn this into an unbounded buffer. That
# failure mode is exactly what the E-010 retry loop would have produced.
MAX_SAMPLES: Final = 2048

# Maximum length of the stored error message.
#
# Home Assistant rejects a sensor state longer than 255 characters outright -
# the state is dropped and the entity goes unknown. An API error carrying a
# long URL or a JSON body would silently destroy the very sensor meant to
# report it, so the message is truncated here rather than at the entity.
MAX_ERROR_MESSAGE_LENGTH: Final = 200

# Error classifications.
#
# A bounded vocabulary rather than the exception's class name: this value is
# what an automation or an alerting rule matches on, so it has to be stable
# across pyatmo versions and free of anything account-identifying. The class
# name of a third-party exception is neither.
ERROR_AUTH: Final = "auth"
ERROR_RATE_LIMIT: Final = "rate_limit"
ERROR_THROTTLING: Final = "throttling"
ERROR_TIMEOUT: Final = "timeout"
ERROR_TRANSPORT: Final = "transport"
ERROR_SERVER: Final = "server"
ERROR_CLIENT: Final = "client"
ERROR_NO_DEVICE: Final = "no_device"
ERROR_UNKNOWN: Final = "unknown"

ERROR_TYPES: Final = (
    ERROR_AUTH,
    ERROR_RATE_LIMIT,
    ERROR_THROTTLING,
    ERROR_TIMEOUT,
    ERROR_TRANSPORT,
    ERROR_SERVER,
    ERROR_CLIENT,
    ERROR_NO_DEVICE,
    ERROR_UNKNOWN,
)


def classify_status(status: Any) -> str:
    """Map an HTTP status onto one of the bounded error classifications.

    Only the status is considered here. The caller is responsible for the
    conditions a status cannot express - a ``TimeoutError`` carries no status
    at all, and Netatmo answers **403** both for a genuine authorization
    failure and for rate limiting, which pyatmo distinguishes by exception
    type rather than by code. That ambiguity is the same one that made
    ``webhook_failure_is_permanent()`` take ``throttled`` as a separate
    argument (defect E-010); repeating the shape here keeps one rule in one
    place instead of two subtly different ones.
    """
    # Types are checked rather than an int() conversion being attempted,
    # because ``bool`` is a subclass of ``int`` and ``int(True)`` is 1 - so a
    # caller that passed a flag by mistake would be silently reported as a
    # "continue" status rather than as unknown.
    if isinstance(status, bool):
        return ERROR_UNKNOWN
    if isinstance(status, int):
        code = status
    elif isinstance(status, str) and status.strip().lstrip("+-").isdigit():
        code = int(status)
    else:
        return ERROR_UNKNOWN

    if code in (401, 403):
        return ERROR_AUTH
    if code == 429:
        return ERROR_RATE_LIMIT
    if code in (408, 504):
        return ERROR_TIMEOUT
    if 500 <= code <= 599:
        return ERROR_SERVER
    if 400 <= code <= 499:
        return ERROR_CLIENT
    return ERROR_UNKNOWN


def redact_error_message(message: Any, limit: int = MAX_ERROR_MESSAGE_LENGTH) -> str:
    """Return an error message safe to publish as an entity state.

    Three separate hazards, all of which have bitten this integration before:

    * **Length.** Home Assistant drops a state above 255 characters, so an
      over-long message would blank the sensor instead of populating it.
    * **Secrets.** Defect C-11 was the webhook id reaching the debug log. That
      id is a bearer credential, and a webhook-related API error is exactly the
      kind that quotes the URL containing it. Anything that looks like a
      webhook callback path is stripped here, before it can reach a state that
      is world-readable to every dashboard, template and logbook entry.
    * **Control characters.** Defect E-009 was control characters in
      identifiers; the same reasoning applies to a value rendered in a UI.
    """
    text = str(message) if message is not None else ""

    # Strip anything resembling a webhook callback URL, including the id.
    #
    # The scan resumes *after* each replacement rather than restarting from
    # the beginning. The replacement text contains the marker it searches for,
    # so re-searching from zero finds the substitution it just made and
    # rewrites it for ever - a non-terminating loop inside a function that
    # runs on the integration's poll path. The cursor is what makes this
    # terminate; it is not a micro-optimisation.
    marker = "/api/webhook/"
    replacement = f"{marker}<redacted>"
    cursor = 0
    while (index := text.lower().find(marker, cursor)) != -1:
        end = index + len(marker)
        while end < len(text) and not text[end].isspace() and text[end] not in "'\")":
            end += 1
        text = f"{text[:index]}{replacement}{text[end:]}"
        cursor = index + len(replacement)

    # ``isprintable()`` is False for every C0 and C1 control character, for
    # DEL, and for the invisible format characters (U+FEFF, U+200B and the
    # like) - so the single test covers the whole class. A space is admitted
    # explicitly because it is the one non-printable character that belongs in
    # a human-readable message.
    text = "".join(
        character for character in text if character == " " or character.isprintable()
    ).strip()

    if len(text) > limit:
        # Leave room for the ellipsis so the result never exceeds the limit.
        text = f"{text[: limit - 1].rstrip()}…"
    return text


def _usable_latency(value: Any) -> float | None:
    """Return a latency safe to publish, or None.

    [hardened-fork] Recording is required to be *total* - no input may raise,
    because this runs on the poll path (see the module docstring). A duration
    is the one field a caller can plausibly get wrong, so it is validated here
    rather than trusted.

    A negative duration is rejected as well as a non-numeric one. Negative
    means the clock moved during the call; publishing it would put a nonsense
    value into a graph and into long-term statistics, where it persists long
    after the clock is fixed. ``bool`` is excluded because it is a subclass of
    ``int`` and would otherwise be recorded as a latency of 0 or 1 seconds.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    latency = float(value)
    # NaN fails every comparison, so it is rejected by this test rather than
    # slipping through the `>= 0` check below.
    if not latency >= 0:
        return None
    return latency


@dataclass(frozen=True)
class TelemetrySnapshot:
    """An immutable view of the telemetry at one instant.

    Entities read a snapshot rather than the live recorder so that the six
    sensors published from one update cycle cannot disagree with each other -
    a failure recorded between two entity reads would otherwise produce a
    "last error" that is newer than the "last error time" beside it.
    """

    failure_ratio: float | None
    sample_count: int
    last_error: str | None
    last_error_type: str | None
    last_error_timestamp: float | None
    last_success_timestamp: float | None
    latency_seconds: float | None
    total_polls: int
    total_failures: int


class ApiTelemetry:
    """Records the outcome of every Netatmo API call.

    Time is injected as an explicit ``now`` argument on every call rather than
    read from a clock inside this class. That keeps the module pure - the
    window arithmetic is testable without patching a clock or sleeping - and
    it means the caller decides which clock applies. The coordinator passes a
    monotonic reading for latency and a wall-clock reading for the timestamps
    the operator sees, which are genuinely different clocks and should not be
    conflated.
    """

    def __init__(
        self,
        window_seconds: int = FAILURE_RATIO_WINDOW_SECONDS,
        max_samples: int = MAX_SAMPLES,
    ) -> None:
        """Set up an empty recorder."""
        self._window_seconds = window_seconds
        # deque with maxlen gives the hard bound for free: appending to a full
        # deque discards from the opposite end rather than growing.
        self._samples: deque[tuple[float, bool]] = deque(maxlen=max_samples)
        self._last_error: str | None = None
        self._last_error_type: str | None = None
        self._last_error_timestamp: float | None = None
        self._last_success_timestamp: float | None = None
        self._latency_seconds: float | None = None
        self._total_polls = 0
        self._total_failures = 0

    def record_success(self, now: float, latency_seconds: float | None = None) -> None:
        """Record one successful API call.

        A success deliberately does **not** clear ``last_error``. The operator
        asked for the last error, not the current one: a fault that has since
        recovered is the most useful thing on the list when reconstructing
        what happened overnight, and clearing it on the next poll would erase
        it within a minute of it occurring.
        """
        self._total_polls += 1
        self._samples.append((now, True))
        self._last_success_timestamp = now
        if (latency := _usable_latency(latency_seconds)) is not None:
            self._latency_seconds = latency

    def record_failure(
        self,
        now: float,
        error_type: str,
        message: Any = None,
        latency_seconds: float | None = None,
    ) -> None:
        """Record one failed API call.

        The latency of a *failure* is recorded too. A call that fails after
        thirty seconds and one that fails instantly are different faults - the
        first is a timeout or a hung connection, the second a rejection - and
        the distinction is lost if only successes are timed.
        """
        self._total_polls += 1
        self._total_failures += 1
        self._samples.append((now, False))
        self._last_error_timestamp = now
        self._last_error_type = (
            error_type if error_type in ERROR_TYPES else ERROR_UNKNOWN
        )
        self._last_error = redact_error_message(message)
        if (latency := _usable_latency(latency_seconds)) is not None:
            self._latency_seconds = latency

    def _prune(self, now: float) -> None:
        """Drop samples that have fallen out of the window.

        Samples are appended in time order, so pruning from the left stops at
        the first one still inside the window.

        A sample from the *future* is kept. That sounds wrong, but a system
        clock stepping backwards - an NTP correction, a VM resuming from
        suspend, a Home Assistant OS host syncing after a power cut - would
        otherwise silently discard the entire history and reset the ratio to
        a clean 0%. An operator reading "0% failures" because the clock moved
        is worse off than one reading a ratio computed over a slightly odd
        window.
        """
        cutoff = now - self._window_seconds
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def failure_ratio(self, now: float) -> float | None:
        """Return the fraction of calls in the window that failed, 0.0-1.0.

        ``None`` when the window holds no samples at all, which is not the
        same as zero. A freshly started integration has *not* achieved a 0%
        failure rate; it has no evidence either way, and reporting 0% would
        be a claim the data does not support.
        """
        self._prune(now)
        if not self._samples:
            return None
        failures = sum(1 for _, ok in self._samples if not ok)
        return failures / len(self._samples)

    def snapshot(self, now: float) -> TelemetrySnapshot:
        """Return a consistent view of every telemetry value."""
        ratio = self.failure_ratio(now)
        return TelemetrySnapshot(
            failure_ratio=ratio,
            sample_count=len(self._samples),
            last_error=self._last_error,
            last_error_type=self._last_error_type,
            last_error_timestamp=self._last_error_timestamp,
            last_success_timestamp=self._last_success_timestamp,
            latency_seconds=self._latency_seconds,
            total_polls=self._total_polls,
            total_failures=self._total_failures,
        )
