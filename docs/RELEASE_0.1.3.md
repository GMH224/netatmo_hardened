# Release Record — 0.1.3

| Field | Value |
| --- | --- |
| **Version** | 0.1.3 |
| **Date** | 2026-09-22 |
| **Type** | Feature — API telemetry (observability only; no control-path change) |
| **Domain** | `netatmo_hardened` |
| **Platform baseline** | Home Assistant 2026.9 · Python 3.14.2 · pyatmo 9.9.0 (unchanged) |
| **Predecessor** | 0.1.2 (2026-09-22) — soaked, E-001 and E-010 cleared live |
| **Status** | Released with two recorded deviations — see §7 and §8 |

---

## 1. Why this release exists

The operator asked for six values the integration was not exposing: API
failure ratio over one hour, last error, last error time, last error type,
last success, and poll latency.

The integration already knew whether it was healthy — every entity's
availability depends on it. What it could not answer was **how** healthy, or
**when it last was not**. A gap in a history graph looked identical whether
it came from a Netatmo outage, a local network fault or a rate limit, and an
API failing 30% of the time for an hour was invisible as long as the other
70% kept the entities populated.

Full analysis: [`AUDIT_0.1.3.md`](AUDIT_0.1.3.md).

## 2. Scope

| Class | Count | IDs |
| --- | --- | --- |
| Features added | 1 | F-002 |
| Defects fixed in shipped code | 0 | — |
| Defects found and fixed **in the new code, pre-release** | 2 | §4 of the audit |
| Toolchain defects found | 1 | `ruff` corrupts source, reports clean — audit §5 |
| Control-path changes | 0 | telemetry observes; it does not act |
| Dependency changes | 0 | pyatmo stays pinned at 9.9.0 |
| Platform baseline changes | 0 | |
| New tier-1 tests | 60 | |
| New tier-2 tests | 13 | |

## 3. What you get

Six new diagnostic sensors on a new **Netatmo API** service device:

| Sensor | Type | Notes |
| --- | --- | --- |
| API failure ratio (1h) | % | **Unknown, not 0%, before the first poll.** Attributes carry `sample_count`, `window_seconds`, `total_polls`, `total_failures` — 100% over two samples and over two hundred are different claims. |
| API last error | text | Redacted and truncated. Survives recovery. |
| API last error type | enum | `auth`, `rate_limit`, `throttling`, `timeout`, `transport`, `server`, `client`, `no_device`, `unknown` — a bounded vocabulary an automation can match. |
| API last error time | timestamp | |
| API last success | timestamp | |
| API poll latency | ms | Measured on failures too: a call that fails after 30 s is a different fault from one that fails instantly. |

All six are `EntityCategory.DIAGNOSTIC` and enabled by default.

### The one design point worth knowing

**These sensors stay available when the API is down.** Every other entity in
this integration goes unavailable precisely when the API fails. A diagnostic
sensor that followed the same rule would report nothing at the only moment
anyone reads it. That behaviour is asserted by a test rather than left to a
comment, because it looks like an oversight to anyone tidying the code later.

## 4. Documentation set

```
docs/
├── AUDIT_0.1.0.md          Internal audit that produced 0.1.0
├── AUDIT_0.1.1.md          External independent audit of 0.1.0 + remediation
├── AUDIT_0.1.2.md          Live soak and internal review of 0.1.1
├── AUDIT_0.1.3.md          Feature-addition review of 0.1.3
├── RELEASE_0.1.0.md        Release record, 0.1.0
├── RELEASE_0.1.1.md        Release record, 0.1.1
├── RELEASE_0.1.2.md        Release record, 0.1.2
├── RELEASE_0.1.3.md        This document
├── DEFECT_REGISTER.md      Cumulative traceability, C-, D-, E- and F-series
├── TEST_PLAN.md            Objectives and coverage map
├── VERIFICATION_REPORT.md  What was and was not executed
├── COMPATIBILITY.md        Platform baseline and 2026 deprecation sweep
├── COMMISSIONING.md        Live soak checklist before unattended deployment
├── MIGRATION.md            Cut-over from the built-in integration
└── SECURITY.md             Trust boundaries
```

## 5. Upgrade

0.1.2 → 0.1.3 is a drop-in update through HACS. **No behaviour changes**, no
re-authentication, no configuration migration, no change to any existing
entity. Push events keep whatever setting you gave them in 0.1.2.

The only visible difference is six new diagnostic entities under a new
**Netatmo API** device. If you do not want them, disable them individually in
the entity registry; nothing else depends on them.

Note that **API failure ratio (1h)** reads *unknown* until the first poll
completes, and its attributes tell you how many samples the number rests on.
Treat a ratio backed by two samples accordingly.

## 6. Acceptance criteria

| # | Criterion | Met |
| --- | --- | --- |
| 1 | Feature covers every value the operator specified | ✅ 6/6 |
| 2 | Telemetry cannot break the poll path it measures | ✅ recorder is total **and** the caller wraps it — two independent defences |
| 3 | Memory bounded by construction | ✅ age window and hard sample cap, both tested |
| 4 | Secrets cannot reach a telemetry state (C-11 re-applied) | ✅ webhook URLs redacted; tier-1 and tier-2 tests |
| 5 | Static analysis clean | ✅ `ruff check` — all checks passed |
| 6 | **Every file compiles** | ✅ new gate, 39 files — see §8 |
| 7 | Tier 1 suite green | ✅ **219 passed** (159 → 219) |
| 8 | Tier 2 suite green | ❌ **not executed — §7** |
| 9 | Audit document versioned per release | ✅ `AUDIT_0.1.3.md` |
| 10 | Independent audit of this release | ❌ **not performed** |
| 11 | Live soak of this release | ❌ **not performed** |

## 7. Recorded deviation — tier 2, carried forward since 0.1.0

**Acceptance criterion 8 is not met.** Home Assistant 2026.9.3 requires Python
≥ 3.14.2; the build environment can obtain only CPython 3.14.0rc2. Forcing the
install with `--ignore-requires-python` remains rejected: a green run on an
interpreter the platform excludes is not evidence about the supported
configuration.

**Consequence for this release.** The feature's central requirement — that the
telemetry sensors stay available while the API fails — is a Home Assistant
entity-lifecycle property, reachable only from tier 2. That test is written
and unrun. What is executed is the recorder underneath it: 60 tier-1 cases
covering the window arithmetic, the classification table, the redaction, the
bounded memory and the totality guarantee.

**Condition.** CI runs tier 2 on every push. **Do not rely on these sensors in
an unattended installation until that job has passed at least once.**

## 8. New in this release — a compile gate, and why it was needed

`ruff 0.15.11` in this build environment rewrites `except (A, B):` into
`except A, B:` — Python 2 syntax, a hard `SyntaxError` — and then reports
**"All checks passed!"** on the file it just corrupted. Reproduced on a
minimal six-line case; full analysis in `AUDIT_0.1.3.md` §5.

**No shipped release is affected.** Every `except` clause in 0.1.0 through
0.1.2 uses the `except (A, B) as err:` form, which the tool leaves alone. The
one affected construct was written in this release and caught before it
shipped — by a manual `py_compile`, not by the lint gate.

`ruff check` has been this project's static-analysis gate since 0.1.0 and has
now been shown to pass a file that cannot be imported. **A linter is not a
syntax gate.** From 0.1.3 the release runs an explicit compile over every
`.py` file in the repository, locally and in CI.

This is the third instance of one pattern in this project: the check that was
supposed to validate the artefact did not check the thing that mattered
(E-011, the packaging defects, and now the linter itself).

## 9. Approval

| Role | Basis |
| --- | --- |
| Requirement | Operator, 2026-09-22 — six named telemetry values |
| Design review | `AUDIT_0.1.3.md` §2, with each decision and its rejected alternative recorded |
| Defects found pre-release | 2, both in new code, both fixed — audit §4 |
| Remediation | This release |
| Deviations | Three: §7 (tier 2 unexecuted), §6 criterion 10 (no independent audit), §6 criterion 11 (no live soak) |
| Certification claims | **None.** No IEC 62443 / 61511 / 61508 conformance is claimed. |

**Note on criteria 10 and 11.** 0.1.2 was soaked against a real account before
it was trusted, and that soak is what produced E-010. 0.1.3 has been soaked
nowhere. Its failure paths have been exercised by fixtures only — the
operator's environment has been healthy throughout, so the failure ratio has
never been observed going non-zero and back on real hardware.

An untested indicator that reads "healthy" is the same hazard as no indicator
at all. Watch these sensors before believing them.
