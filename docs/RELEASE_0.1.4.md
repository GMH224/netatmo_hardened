# Release Record — 0.1.4

| Field | Value |
| --- | --- |
| **Version** | 0.1.4 |
| **Date** | 2026-09-22 |
| **Type** | Corrective — poll scheduling; reduces API traffic 4.6× on the reference account |
| **Domain** | `netatmo_hardened` |
| **Platform baseline** | Home Assistant 2026.9 · Python 3.14.2 · pyatmo 9.9.0 (unchanged) |
| **Predecessor** | 0.1.3 (2026-09-22) |
| **Status** | Released with three recorded deviations — see §7 and §8 |

---

## 1. Why this release exists

The operator read the 0.1.3 polling figures and asked why a weather station
whose modules publish once every five minutes was being polled every
eighty-five seconds.

The answer was that nobody had chosen it. Every polling constant is inherited
verbatim from `home-assistant/core` — verified by diff against `dev` — and the
interval is derived from the **rate limit** (400 calls/hour with your own
credentials, so divide by seven) rather than from how often the data changes.

On the reference account that produced **210 API calls an hour against 12
actual measurements** — roughly 18 calls per measurement — with 180 of those
calls fetching home status, two thirds of them for homes containing no
hardware at all.

Full analysis: [`AUDIT_0.1.4.md`](AUDIT_0.1.4.md).

## 2. Scope

| Class | Count | IDs |
| --- | --- | --- |
| Defects fixed | 2 | F-003, F-004 |
| Defects identified and **deferred with reasoning** | 2 | audit §6 |
| Upstream quirks recorded, not fixed | 1 | audit §5 |
| Control-path changes | 0 | scheduling only |
| Dependency changes | 0 | pyatmo stays pinned at 9.9.0 |
| Platform baseline changes | 0 | |
| New tier-1 tests | 25 | |
| New tier-2 tests | 10 | |

## 3. Changes

### F-004 — poll no faster than the source changes

`helper.effective_poll_interval()` applies a floor drawn from the source's
behaviour, taking `max()` of it and the rate-limit figure — so a *lower* call
budget (Home Assistant Cloud) can still lengthen an interval, but a higher one
can never shorten it below the point where there is nothing new to fetch.

| Publisher | Was (own credentials) | Now | Floor's basis |
| --- | ---: | ---: | --- |
| `account` | ~26 min | 60 min | Topology changes when a human adds hardware |
| `home` | 42 s | 120 s | **Judgement, not measurement** — see §8 |
| `weather` | 85 s | 240 s | Station publishes every 300 s |
| `air_care` | 42 s | 240 s | Same sensor cadence |
| `public` | 85 s | 600 s | Third-party stations, feeds a map not a control decision |
| `event` | 85 s | 300 s | Camera events arrive by push when enabled |

**Why 240 s and not 300 s.** Polling at exactly the publish period gives a
fixed phase offset that clock drift slowly walks; each time it wraps, a
measurement is skipped. Strictly below the period, no published sample can be
missed. The cost is 15 calls/hour instead of 12.

### F-003 — do not poll a home that can produce nothing

A home gets no status publisher when it has **no modules**, or when **every
module and room in it is disabled**. A log line names how many were skipped.

**What this cannot do, stated plainly:** there is no per-room API call.
`async_update_status` fetches an entire home or nothing, so disabling one room
of three saves no traffic. A home with any enabled content is still polled in
full. Both the code and a test say so, because this is the kind of limitation
that gets quietly "optimised" into a bug later.

## 4. What you should see

On the reference account — three homes, one weather station, two homes with no
modules:

| | Before | After |
| --- | ---: | ---: |
| `weather` | 30/h | 15/h |
| `home` | 180/h (3 homes) | 30/h (1 home) |
| `account` | ~0/h | 1/h |
| **Total** | **210/h** | **46/h** |

Weather oversampling falls from 2.5× to 1.25×. Sensor values will update every
4 minutes instead of every 2 — **no data is lost**, because the station only
produces a new measurement every 5 minutes either way.

**This is the first release with an instrument already fitted for its own
effect.** The 0.1.3 telemetry sensors — poll latency, failure ratio, last
success — shipped one release ago and have never been watched through a
change. A 4.6× traffic reduction is a good first thing to point them at.
`COMMISSIONING.md` §1.8 says what to look for.

## 5. Documentation set

```
docs/
├── AUDIT_0.1.0.md … AUDIT_0.1.4.md      One per release
├── RELEASE_0.1.0.md … RELEASE_0.1.4.md  One per release
├── DEFECT_REGISTER.md      Cumulative traceability, C-, D-, E- and F-series
├── TEST_PLAN.md            Objectives and coverage map
├── VERIFICATION_REPORT.md  What was and was not executed
├── COMPATIBILITY.md        Platform baseline and 2026 deprecation sweep
├── COMMISSIONING.md        Live soak checklist before unattended deployment
├── MIGRATION.md            Cut-over from the built-in integration
└── SECURITY.md             Trust boundaries
```

## 6. Acceptance criteria

| # | Criterion | Met |
| --- | --- | --- |
| 1 | Every interval floor justified by a stated property of the source | ⚠️ 5 of 6 — `HOME` is a judgement, §8 |
| 2 | A lower rate limit can still lengthen an interval | ✅ `max()`, tested for Home Assistant Cloud |
| 3 | The fix cannot drop a home with enabled content | ✅ tier-1 and tier-2 boundary tests |
| 4 | Limits of the fix documented rather than implied | ✅ no per-room fetch exists; §3 |
| 5 | Static analysis clean | ✅ `ruff check` |
| 6 | Every file compiles | ✅ 41 files — gate added in 0.1.3 |
| 7 | Tier 1 suite green | ✅ **244 passed** (219 → 244) |
| 8 | Tier 2 suite green | ❌ **not executed — §7** |
| 9 | Audit document versioned per release | ✅ `AUDIT_0.1.4.md` |
| 10 | Independent audit of this release | ❌ **not performed** |
| 11 | Live soak of this release | ❌ **not performed** |

## 7. Recorded deviation — tier 2, carried forward since 0.1.0

**Acceptance criterion 8 is not met.** Home Assistant 2026.9.3 requires Python
≥ 3.14.2; the build environment can obtain only CPython 3.14.0rc2. Forcing the
install with `--ignore-requires-python` remains rejected.

**Consequence for this release.** Both fixes are pure functions, fully covered
by 25 executed tier-1 tests. What is *not* executed is every test that checks
the coordinator actually **calls** them — `subscribe()` storing the floored
interval, `async_dispatch` skipping the empty homes. Both helpers could be
correct and simply not wired in, leaving the shipped cadence unchanged while
every unit test passed. That is the specific risk this release carries.

**Condition.** CI runs tier 2 on every push. Until it has passed once, confirm
the effect from the running system instead: `COMMISSIONING.md` §1.8.

## 8. Approval

| Role | Basis |
| --- | --- |
| Requirement | Operator, 2026-09-22 — polling ratio, and disabled rooms |
| Analysis | `AUDIT_0.1.4.md`, with constants diffed against `home-assistant/core` at `dev` |
| Arithmetic | Derived from the operator's own diagnostics (three homes, one equipped) |
| Remediation | This release |
| Deviations | Three: §7 (tier 2), §6 criterion 10 (no independent audit), §6 criterion 11 (no live soak) |
| Certification claims | **None.** No IEC 62443 / 61511 / 61508 conformance is claimed. |

**Note on criterion 1 — the one floor without evidence.** `HOME` is set to
120 s. Home status is event-driven and has no publish period, so this number is
derived from the call budget and a judgement about responsiveness, not from a
measurement. It is the value in this release most likely to be wrong. Two
mechanisms make it safe rather than correct: `async_force_update()` refreshes
within one tick after any command, and push events deliver changes instantly
where enabled. What is genuinely slower is a change made in the *Netatmo app*
on an installation without push — up to 2 minutes instead of 1.

**Note on the station period.** The 300 s figure comes from the operator and
community sources; Netatmo's developer documentation did not render through
the available fetch tooling, so it is not cited from the vendor. The design
tolerates this: the floor sits *below* the stated period, so a true period
longer than 300 s leaves the fix correct. Only a period shorter than 240 s
would make it wrong.

**Note on deferred items.** The rate-limit brake freezes polling rather than
degrading it — once the hourly budget is exceeded it advances `next_scan` as
fast as wall time, stopping polling for up to an hour. That is a real defect
and it is **not** fixed here. 0.1.4 puts this deployment far out of its reach
(46/400), and the last time this project changed a retry rule without live
evidence it shipped E-010. It should be fixed against a reproduction.
