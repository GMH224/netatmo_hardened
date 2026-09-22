# Defect Audit 0.1.4 — Poll Scheduling

**Release under audit:** 0.1.3 as shipped, plus the 0.1.4 change
**Date:** 2026-09-22
**Baseline:** Home Assistant 2026.9.3, pyatmo 9.9.0, Python 3.14.6, Home Assistant OS 18.3
**Method:** operator question, source review against upstream, arithmetic from the operator's own diagnostics
**Status:** 2 defects found (F-003, F-004), both fixed. 2 further defects
identified and **deferred with reasoning** (§6). 1 upstream quirk recorded.

---

## 1. How this one was found

The operator read the 0.1.3 release notes, looked at the polling figures, and
asked why a weather station whose modules publish once every five minutes was
being polled every eighty-five seconds.

That is the whole audit trail. No tool found this, no test failed, and three
prior audits of this codebase went past these constants without stopping —
including the one that rewrote the retry logic sitting a few lines away.

The reason is worth stating, because it generalises. Every previous audit
asked *"is this code correct?"* and the polling code is correct: it computes
what it intends to compute and the integration works. The question that had
not been asked was *"is this the right quantity to compute at all?"* — and the
answer was no, because the interval is derived from the **rate limit** rather
than from how often the data changes.

A defect that requires knowing something about the physical world — how often a
Netatmo module transmits — is invisible to code review, to static analysis and
to any test written from the code's own assumptions. It needed someone who
owns the hardware.

---

## 2. The finding

### 2.1 What upstream does

```python
DEFAULT_INTERVALS = {ACCOUNT: 10800, HOME: 300, WEATHER: 600, ...}
DEV_FACTOR = 7      # own application credentials: 400 calls/hour
CLOUD_FACTOR = 2    # Home Assistant Cloud: 150 calls/hour
...
interval = int(DEFAULT_INTERVALS[publisher] / self._interval_factor)
```

Every one of these constants was diffed against `home-assistant/core` at `dev`
and is **inherited verbatim**. This fork changed `MAX_ERROR_BACKOFF`
(3600 → 300) in 0.1.0 and never examined the cadence beside it.

The intent is legible: an application with its own credentials gets a larger
call budget, so use it. Nothing in the calculation refers to the data.

### 2.2 What that produced on the operator's account

The 60-second tick floors everything — a publisher due at t+42 s cannot be
called until the next tick at t+60 s.

| Publisher | Count | Nominal | Effective | Calls/hour |
| --- | ---: | ---: | ---: | ---: |
| `weather` | 1 | 85 s | 120 s | 30 |
| `home` | 3 | 42 s | 60 s | 180 |
| | | | **Total** | **210** |

Against a weather station publishing every 300 s — **12 measurements an hour**
— that is roughly **18 API calls per actual measurement**.

Worse, 180 of the 210 calls were `home` status polls, and **two of the three
homes contain no modules at all**. They are homes the Netatmo app creates for
an address that was never equipped. They were polled every sixty seconds,
indefinitely, and could never contribute a single entity.

### 2.3 Why oversampling is not merely wasteful

`async_update` carries a rate-limit brake:

```python
if cph > self._rate_limit:
    for publisher in self.publisher.values():
        publisher.next_scan += 60
```

That runs on **every tick**, and ticks are 60 s apart. Once the hourly call
count exceeds the limit, `next_scan` advances exactly as fast as wall time:
polling does not slow down, **it stops**, and stays stopped until `poll_count`
resets — which happens only when `time() - poll_start > 3600`.

So the budget is a shared resource with a cliff, not a gradient. Spending it on
redundant reads of one publisher is what starves every other one, for up to an
hour at a time.

This is consistent with [home-assistant/core#152449](https://github.com/home-assistant/core/issues/152449),
where users report the *opposite* symptom to what the constants predict —
temperature and humidity taking hours to update. The constants say 42 seconds.
The brake explains the gap.

The operator's account sat at 210/400, inside the limit. An account with more
homes, or cameras, would not.

---

## 3. F-004 — poll no faster than the source changes

`helper.effective_poll_interval()` applies a floor drawn from the behaviour of
the source rather than from the call budget. `max()` rather than replacement,
so a *lower* rate limit (Home Assistant Cloud) can still lengthen an interval —
otherwise this "fix" would have increased traffic for cloud users.

| Publisher | Floor | Reasoning |
| --- | ---: | --- |
| `ACCOUNT` | 3600 s | Topology changes when a human adds or moves hardware. Upstream polled it every 25 minutes. |
| `HOME` | 120 s | Event-driven, so it has **no natural period** — this floor is budget-derived, not source-derived, and is stated as such. Immediacy comes from `async_force_update()` after every command and from push events (F-001). |
| `WEATHER` | 240 s | Every module of a weather station publishes every 300 s, indoor and outdoor alike. |
| `AIR_CARE` | 240 s | Same sensor cadence. |
| `PUBLIC` | 600 s | Third-party stations are no better than ten minutes, and this feeds a map, not a control decision. |
| `EVENT` | 300 s | Camera events arrive by push when enabled; polling is the fallback. |

### Why 240 s and not 300 s

Polling at exactly the publish period gives a fixed phase offset — every sample
observed once, at constant latency. That is ideal in theory and fragile in
practice: clock drift walks the offset, and each time it wraps, a measurement
is skipped. At 240 s the poll is strictly faster than the source, so no
published sample can be missed, at a cost of 15 calls/hour instead of 12.

The test asserts the *relationship* (`interval < STATION_PERIOD`), not the
number, so a future change either keeps the guarantee or fails.

### The honest limit of this fix

`HOME` is the one floor with no evidentiary basis in the source, because home
status has no publish period — it changes when something happens. 120 s is a
judgement, not a measurement, and it is the value most likely to be wrong.
It is nearly three times upstream's effective 60 s, which is the direction the
evidence supports, but the specific number is not derived from anything.

---

## 4. F-003 — do not poll a home that can produce nothing

The operator's request was narrower than the fix: *do not update rooms I have
disabled*. Investigating it turned up something the request did not cover.

**What could not be done, and why.** There is no per-room fetch.
`async_update_status` returns an entire home or nothing. Disabling one room of
three therefore saves no traffic, and `home_is_pollable()` does not pretend
otherwise — a home with any enabled content stays polled in full. The
docstring and a test both say so, because this is exactly the kind of
limitation that gets quietly "optimised" into a bug later.

**What could.** Two situations produce a home that is polled for ever and can
never yield an entity:

1. **A home with no modules.** Rooms but no hardware. Two of the operator's
   three homes.
2. **A home whose every module and room is disabled.** Disabling a device
   stops its entities updating but not the API call. An operator who has
   disabled everything inside a home has expressed the same intent as one who
   disabled the home itself — and disabling the *home* already worked, because
   that id reaches pyatmo's `disabled_homes_ids` denylist.

Both now skip the publisher entirely, with a log line naming the count. The
silence was the substance of the upstream complaint in
[core#181448](https://github.com/home-assistant/core/issues/181448), and the
same reasoning applies here.

### Result on the operator's account

| | Before | After |
| --- | ---: | ---: |
| `weather` | 30/h | 15/h |
| `home` | 180/h (3 homes) | 30/h (1 home) |
| `account` | ~0/h | 1/h |
| **Total** | **210/h** | **46/h** |

A 4.6× reduction, and weather oversampling falls from 2.5× to 1.25×.

---

## 5. An upstream quirk found on the way

`async_dispatch` subscribes `HOME` and `EVENT` under the **same** signal name:

```python
signal_home = f"{HOME}-{home.entity_id}"
await self.subscribe(HOME, signal_home, None, home_id=home.entity_id)
await self.subscribe(EVENT, signal_home, None, home_id=home.entity_id)
```

`subscribe()` returns early when the signal name already exists, so the second
call only adds a callback — **the `EVENT` publisher is never created** and
`async_update_events` is never scheduled by the poll loop.

Recorded, not fixed. It is upstream behaviour, it does not affect this
operator (no cameras), and changing it would *add* API traffic in the same
release that exists to reduce it. It belongs to whoever next works on the
camera path, with real camera hardware to verify against. Logged as an open
item in `TEST_PLAN.md` §4.

---

## 6. Deferred, with reasoning

Two further defects were identified and are **not** fixed here.

**The rate-limit brake freezes rather than degrades** (§2.3). The fix is to
push `next_scan` out by a bounded fraction, or to reset the hourly counter on a
rolling basis rather than at a fixed hour boundary. Deferred because 0.1.4
takes the operator from 210 to 46 calls/hour, which puts the brake far out of
reach for this deployment, and because changing backoff behaviour is precisely
what produced E-010 — the last time this project adjusted a retry rule without
live evidence, it shipped a regression. This one should be fixed with a
reproduction, not from reading.

**`HOME` at 120 s is a judgement, not a measurement** (§3). It should be
derived from observed home-status change rates, which nobody has measured.

---

## 7. What this audit did not cover

- **Tier 2 remains unexecuted.** Python ≥ 3.14.2 unavailable in the build
  environment. The 10 tier-2 tests added here are written and unrun, including
  every test that checks the coordinator actually *uses* the new rules.
- **No live soak of 0.1.4.** The call-rate reduction is arithmetic from the
  operator's diagnostics, not an observation. The 0.1.3 telemetry sensors are
  what would confirm it, and they have not been watched through a change yet.
- **The 300 s station period is taken from the operator**, corroborated by
  community sources. Netatmo's developer documentation did not render through
  the available fetch tooling, so it is not cited from the vendor directly.
  The design is deliberately tolerant of this: the floor sits below the stated
  period, so a true period *longer* than 300 s leaves the fix still correct,
  and only a period shorter than 240 s would make it wrong.
- **No independent audit, and no measurement of home-status change rates.**

---

## 8. Recommendation

**Release 0.1.4, and use the 0.1.3 telemetry to confirm it.**

This is the first release with an instrument fitted for its own effect. **API
poll latency** and the failure ratio were shipped one release ago and have
never been watched through a change; a 4.6× traffic reduction is a good first
thing to point them at.

The conservative observation: both fixes only ever *reduce* traffic, and
neither touches the control path. The realistic counterweight: the previous
two times this project changed timing behaviour — the backoff ceiling in 0.1.0
and the retry rule in 0.1.1 — one shipped fine and one became E-010. The
difference was that E-010 changed a rule under *failure* conditions, which are
rare and hard to reason about, while this changes a rule under *normal*
conditions, which occur continuously and are visible within minutes.

Release gate addition carried forward:

1. **Ask what quantity a calculation is derived from, not just whether the
   arithmetic is right.** Three audits passed over an interval computed from a
   rate limit rather than from a data rate. "Correct" and "computing the right
   thing" are different questions, and only the second one found this.
