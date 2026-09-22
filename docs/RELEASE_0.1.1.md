# Release Record — 0.1.1

| Field | Value |
| --- | --- |
| **Version** | 0.1.1 |
| **Date** | 2026-09-22 |
| **Type** | Corrective release — remediates an independent external audit of 0.1.0 |
| **Domain** | `netatmo_hardened` |
| **Platform baseline** | Home Assistant 2026.9 · Python 3.14.2 · pyatmo 9.9.0 (unchanged) |
| **Predecessor** | 0.1.0 (2026-09-22) — **superseded; do not deploy** |
| **Status** | Released with one recorded deviation — see §5 |

---

## 1. Why this release exists

An independent third party audited 0.1.0 and returned a **FAIL** disposition.
Nine defects, all verified. **Four were introduced by 0.1.0's own remediation**
— written to close the first audit's findings, reviewed by the same party that
wrote them, and shipped with the integration test tier unexecuted.

Two are live availability defects that affect every installation:

* **E-001** — a routine OAuth token refresh reloaded the whole integration.
  Netatmo tokens last about three hours, so 0.1.0 tore itself down and rebuilt
  roughly **eight times a day, indefinitely**.
* **E-002** — a state guard added for safety blocked the only webhook
  registration a Home Assistant Cloud subscriber gets, so **no webhook was ever
  registered** for that population. Polling continued, so the integration
  looked healthy while every event-driven function was dead.

0.1.0 should not be deployed. Full analysis: [`AUDIT_0.1.1.md`](AUDIT_0.1.1.md).

## 2. Scope

Defect remediation only. No new features, no dependency changes, no platform
baseline change. Every modification traces to a finding in `AUDIT_0.1.1.md`.

| Class | Count |
| --- | --- |
| Regressions introduced by 0.1.0 and now fixed | 4 (E-001, E-002, E-003, E-006) |
| Pre-existing defects fixed | 5 (E-004, E-005, E-007, E-008, E-009) |
| Severity ratings revised with reasoning | 1 (E-003, HIGH → MEDIUM) |
| New tier-1 tests | 39 |
| New tier-2 tests | 13 |

## 3. Documentation set

Audit documents are versioned per release, as required:

```
docs/
├── AUDIT_0.1.0.md          Internal audit that produced 0.1.0
├── AUDIT_0.1.1.md          External independent audit of 0.1.0 + remediation
├── RELEASE_0.1.0.md        Release record, 0.1.0
├── RELEASE_0.1.1.md        This document
├── DEFECT_REGISTER.md      Cumulative traceability, C- and E-series
├── TEST_PLAN.md            Objectives and coverage map
├── VERIFICATION_REPORT.md  What was and was not executed
├── COMPATIBILITY.md        Platform baseline and 2026 deprecation sweep
├── COMMISSIONING.md        Live soak checklist before unattended deployment
├── MIGRATION.md            Cut-over from the built-in integration
└── SECURITY.md             Trust boundaries
```

## 4. Acceptance criteria

| # | Criterion | Met |
| --- | --- | --- |
| 1 | Every external finding independently verified before acceptance | ✅ 9/9 |
| 2 | Every accepted finding fixed and traced to a test | ✅ |
| 3 | Severity revisions documented with reasoning | ✅ E-003, §4 of the audit |
| 4 | Root cause of the introduced regressions identified | ✅ audit §6 |
| 5 | Static analysis clean | ✅ `ruff` all checks passed |
| 6 | Tier 1 suite green | ✅ 124 passed |
| 7 | Tier 2 suite green | ❌ **not executed — §5** |
| 8 | No weak assertions left in the test that missed E-006 | ✅ now asserts range |
| 9 | Audit documents versioned per release | ✅ |

## 5. Recorded deviation — unchanged from 0.1.0, and now demonstrably material

**Acceptance criterion 7 is not met.** Home Assistant 2026.9.3 requires Python
≥ 3.14.2; the build environment can obtain only CPython 3.14.0rc2. Forcing the
install remains rejected: a green run on an interpreter the platform excludes
is not evidence about the supported configuration.

This is the same deviation recorded for 0.1.0 — but it is no longer
theoretical. **E-001 and E-002 are precisely what the tier-2 suite exercises**,
and both shipped. The deviation is the direct cause of this release existing.

**Consequence.** The 0.1.1 fixes for E-001, E-002, E-004, E-007 and E-008 rest
on source review, static analysis and authored-but-unexecuted tests. The fixes
for E-003, E-005, E-006 and E-009 are covered by executed tier-1 tests, because
the logic was extracted into pure functions specifically so that it could be.

**Condition.** CI runs tier 2 on every push. **Do not deploy 0.1.1 to an
unattended installation until that job has passed at least once**, and update
`VERIFICATION_REPORT.md` with the result.

**Compensating control.** `COMMISSIONING.md` adds a live soak checklist. It
exists because both of the defects that made this release necessary were
invisible to static analysis and to every executed test, yet would have been
obvious within hours of real use - a reload cycle is plain in the log, a
missing webhook shows on the first restart. For this defect class a supervised
soak is the highest-yield verification available, and it does not depend on
the Python version that blocks tier 2.

## 6. Upgrade

0.1.0 → 0.1.1 is a drop-in update through HACS. No configuration change, no
entity change, no re-authentication.

Behaviour you should observe afterwards:

* the integration stops reloading itself every few hours;
* HA Cloud subscribers get a working webhook after a restart;
* a Netatmo-rejected command now raises a visible error instead of silently
  showing the wrong state.

## 7. Approval

| Role | Basis |
| --- | --- |
| External audit | Independent third party, 2026-09-22, evidence base excluded this project's own material |
| Verification of findings | All nine re-verified against source and dependency before acceptance |
| Remediation | This release |
| Deviations | One, §5, carried forward and now understood as causal |
| Certification claims | **None.** No IEC 62443 / 61511 / 61508 conformance is claimed. |
