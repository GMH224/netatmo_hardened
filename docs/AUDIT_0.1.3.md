# Defect Audit 0.1.3 — Feature Addition Review

**Release under audit:** 0.1.3 (the change itself, reviewed as it was built)
**Date:** 2026-09-22
**Baseline:** Home Assistant 2026.9.3, pyatmo 9.9.0, Python 3.14.6, Home Assistant OS 18.3
**Method:** design review, self-review during implementation, tier-1 execution
**Status:** 1 feature added (F-002). 2 defects found **in the new code before
release**, both fixed. 1 toolchain defect found in the build environment.

---

## 1. What this audit is, and how it differs from the last three

0.1.0 through 0.1.2 audited code that already existed — upstream's, or this
fork's own mistakes. This one audits a change while it is being written, which
is the weaker position: there is no independent reader, and the author of a
feature is the worst-placed person to find its defects.

Two things partially compensate, and both earned their place in this release:

* **The tier-1 suite caught a defect in code I had just written and asserted
  was correct** (§4.2). The docstring claimed the recorder was total against
  any input; it was not, and a test written from that docstring failed.
* **An automated gate caught a defect the linter reported as clean** (§5).

Neither is a substitute for independent review. §7 states that plainly.

---

## 2. The feature — F-002, API telemetry

The operator asked for six values: failure ratio over one hour, last error,
last error time, last error type, last success, and poll latency. All six are
about the *connection*, not about any device.

The integration already knew whether it was healthy — every entity's
`available` property depends on it. What it could not answer was **how**
healthy, or **when it last was not**. A gap in a history graph looked the same
whether it came from a Netatmo outage, a local network fault, or a rate limit,
and an API failing 30% of the time for an hour was invisible as long as the
remaining 70% kept the entities populated.

That is an observability gap rather than a defect. In an ICS context it is
still worth closing, because **the distinction between "this reading is stale"
and "this reading is wrong" is not visible from the reading**.

### 2.1 The design decision that matters

Everything else in this feature is bookkeeping. This is not:

> **The telemetry sensors must not go unavailable when the API fails.**

Every other entity in this integration is unavailable precisely when the API
is failing — that is what `NetatmoBaseEntity.available` does, and it is
correct for a temperature reading. Applying the same rule to a diagnostic
sensor produces something that reports nothing at the only moment anybody
reads it.

The mechanism is that `NetatmoTelemetrySensor` leaves `_publishers` empty, so
the inherited availability check reduces to `all([])`, which is `True`. That
is subtle enough to be "tidied up" by a future change that does not understand
why it is there, so it is asserted by a test
(`test_telemetry_stays_available_while_the_api_fails`) rather than left to the
comment that explains it.

The same reasoning drove the update path. These sensors are refreshed by a
dedicated dispatcher signal rather than by subscribing to a publisher, because
a publisher subscription carries availability with it and would have
reintroduced the problem through the back door.

### 2.2 Decisions taken, with their reasoning

| Decision | Alternative rejected | Why |
| --- | --- | --- |
| No samples → **unknown**, not 0% | Report 0% on a fresh start | An integration that has made no calls has not achieved a 0% failure rate. Of the two possible wrong answers, "healthy" is the dangerous one: it satisfies an alerting rule that should have fired. |
| A success does **not** clear the last error | Clear on recovery | The operator asked for the *last* error, not the *current* one. Clearing on the next poll erases it within a minute — and the sensor is read the morning after, not during. |
| Error type is a **bounded vocabulary** | The exception's class name | This value is what an automation matches on. A pyatmo class name is neither stable across versions nor guaranteed free of account-identifying text. `SensorDeviceClass.ENUM` also requires a declared option set. |
| Latency measured with `monotonic()`, timestamps with `time()` | One clock for both | Wall time can step during a slow call and yield a negative duration. A monotonic reading is meaningless on a dashboard. These are genuinely different clocks. |
| Failure latency recorded too | Time only successes | A call failing after 30 s and one failing instantly are different faults — a timeout versus a rejection. Timing only successes loses that at the moment it matters. |
| Future-dated samples **kept** | Prune anything after `now` | An NTP correction or a VM resuming from suspend steps wall time backwards. Pruning would reset the ratio to a clean 0% immediately after an outage — the most misleading possible moment. |
| Not persisted across restarts | Restore the window | The ratio describes the running system. Restoring an hour-old window would report a fault the restart may have resolved. |
| Service device, not physical | Attach to a home, or no device | The Netatmo cloud endpoint has no firmware, serial or model. Claiming otherwise puts fictional hardware in the device registry. |

---

## 3. Risk introduced by the feature itself

A monitoring subsystem that can break the system it monitors is worse than no
monitoring. Two independent defences, deliberately not one:

1. **The recorder is total.** No input to `record_success` or `record_failure`
   can raise — the classification, the redaction and the latency are each
   validated rather than trusted.
2. **The coordinator wraps the call anyway.** If a future change breaks rule 1,
   the poll cycle survives and the failure is logged.

The wrapper alone would not be enough: with it, a broken recorder means the
sensors silently stop updating while everything looks fine. Rule 1 is what
makes the failure loud. Both are tested
(`test_recording_a_failure_never_raises`, `test_a_broken_recorder_cannot_break_the_poll`).

Memory is bounded twice — by age and by a hard sample cap — because the E-010
precedent is a loop nobody expected to run for ever that ran for ever. A
buffer bounded only by the poll interval is bounded by an assumption, and
assumptions in this codebase have not held.

---

## 4. Defects found in the new code, before release

### 4.1 A non-terminating loop in the redaction routine

**Severity:** would have been Critical. Found by the tier-1 suite hanging.

The webhook-URL redaction searched for `/api/webhook/` and replaced the match
with `/api/webhook/<redacted>` — **which contains the string it searches
for**. Re-searching from position zero found the substitution it had just
made and rewrote it, for ever.

This sits on the poll path. Shipping it would have hung a Home Assistant
worker on the first API error quoting a webhook URL — which, given E-010, is
the single most likely error this deployment produces.

Fixed by resuming the scan after each replacement rather than restarting.
The cursor is what makes the loop terminate; the code now says so, because it
reads like a micro-optimisation and is not.

The tier-1 suite found this by hanging rather than by failing. That is worth
recording: a test that hangs is a worse signal than one that fails, and it was
only noticed because the suite normally completes in under a second.

### 4.2 The recorder was not total, though its docstring said it was

**Severity:** Medium. Found by a tier-1 test written from the docstring.

`record_success(latency_seconds="not a number")` raised `TypeError` from the
`latency_seconds >= 0` comparison. The module docstring claimed every
recording entry point was total.

The test was written from the stated contract rather than from the
implementation, which is why it found the gap. Fixed in the code, not in the
test: `_usable_latency()` now rejects non-numeric values, `NaN` (which fails
every comparison, so `>= 0` let it through), negatives, and `bool` — which is
a subclass of `int` and would otherwise have been recorded as a latency of
0 or 1 seconds.

The same `bool`-is-an-`int` trap exists in `classify_status`, where `int(True)`
is `1`; both are now explicit, and both are tested.

---

## 5. A toolchain defect — `ruff` corrupts source and reports it clean

**Severity:** High, for the build process rather than the product.

The `ruff 0.15.11` binary in this build environment rewrites

```python
except (TypeError, ValueError):
```

into

```python
except TypeError, ValueError:
```

which is Python 2 syntax and a hard `SyntaxError` on any supported
interpreter. `ruff check` then reports **"All checks passed!"** on the
corrupted file.

Reproduced on a minimal six-line file. The corruption applies only to the
bare form; `except (A, B) as err:` is left alone.

**Impact on shipped releases: none.** Every `except` clause in 0.1.0 through
0.1.2 uses the `as err` form. The only affected construct was one I wrote in
this release, and it was caught before release — but only because
`py_compile` was run by hand, not because the lint gate objected.

**Process consequence.** `ruff check` has been this project's static-analysis
gate since 0.1.0, and it has now been shown to pass a file that cannot be
imported. A linter is not a syntax gate. 0.1.3 adds an explicit compile step
over every `.py` file in the repository, locally and in CI, and the release
checklist no longer treats a green `ruff` as evidence that the code parses.

This is the third instance of the same class of failure in this project:
**the tool that was supposed to check the artefact did not check the thing
that mattered.** The first was `strings.json` being reviewed while
`translations/en.json` shipped (E-011); the second was the archive layout
being right in the source tree and wrong in the zip (P0-x). The rule added in
`AUDIT_0.1.2.md` §7 — *validate the shipped artefact, not the source it was
derived from* — now extends to the tools themselves.

---

## 6. What this audit did not cover

- **Tier 2 remains unexecuted.** HA 2026.9.3 requires Python ≥ 3.14.2; the
  authoring container offers 3.14.0rc2. The 13 tier-2 tests added here are
  **written and unrun**, including the one asserting the feature's central
  requirement (availability during an outage). CI executes them.
- **No live soak of this release.** 0.1.2 was soaked and cleared E-001 and
  E-010 against a real account. 0.1.3 has not been deployed anywhere.
- **The failure ratio has never been observed non-zero on real hardware.** The
  operator's environment has been healthy throughout. Every failure path in
  this feature is exercised by fixtures only.
- **Latency has no real-world reference.** No measurement of what a normal
  Netatmo poll costs from this deployment, so there is no basis yet for a
  threshold an operator could alert on.
- **No independent audit.** See §7.

---

## 7. Recommendation

**Release 0.1.3, then soak before trusting the numbers.**

The feature is additive, off nobody's critical path, and cannot affect control
behaviour: it observes the poll loop and writes six diagnostic states.

Against that, and stated plainly: this is a self-audited feature release, and
the only two times this project's work has been independently reviewed, the
reviewer found defects the author had missed — nine in 0.1.0, of which four
were self-inflicted. Two defects were found *in this release's new code* by
mechanisms that happened to be in place, which is evidence the mechanisms
work and equally evidence that freshly written code in this project reliably
contains defects.

The sensible reading is that 0.1.3's telemetry should be watched before it is
believed. Specifically: until the failure ratio has been observed going
non-zero and back on real hardware, it is an untested indicator, and an
untested indicator that reads "healthy" is the same hazard as no indicator at
all.

Release gate additions carried forward from this audit:

1. **A linter is not a syntax gate.** Every release runs an explicit compile
   over every `.py` file, in CI and locally. Added in 0.1.3.
2. **Write the test from the contract, not from the implementation.** §4.2 was
   found because the test asserted what the docstring promised rather than
   what the code did.
3. **A hanging test suite is a failure, not a slow pass.** The tier-1 suite
   runs in under a second; CI now bounds it explicitly so a non-terminating
   loop fails the build instead of stalling it.
