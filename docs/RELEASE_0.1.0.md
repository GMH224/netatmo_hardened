# Release Record — 0.1.0

| Field | Value |
| --- | --- |
| **Version** | 0.1.0 |
| **Date** | 2026-09-22 |
| **Type** | Baseline release — first version under an independent version line |
| **Domain** | `netatmo_hardened` (independent; installs alongside the built-in integration) |
| **Platform baseline** | Home Assistant 2026.9 · Python 3.14.2 · pyatmo 9.9.0 |
| **Predecessor** | Inherited build versioned `2026.9.21`; retired |
| **Status** | Released with recorded deviation — see §5 |

---

## 1. Purpose

0.1.0 establishes a controlled baseline. The inherited build could not be
treated as one: it declared platform support it did not have, asserted test
coverage that did not exist, and its change record did not describe its own
change set. Those are release-integrity defects, and they are fixed here
alongside the code defects.

## 2. Contents of the deployment package

Test cases and documentation ship **inside** the package, as required.

```
netatmo-hardened/
├── custom_components/netatmo_hardened/   Integration source (25 modules)
│   ├── event_validation.py         NEW - validated webhook ingress boundary
│   ├── helper.py                   Pure, unit-testable control-path logic
│   └── translations/en.json        NEW - required at runtime (defect P0-2)
├── tests/
│   ├── unit/                       Tier 1 - 85 tests, executed, green
│   └── integration/                Tier 2 - authored for CI
├── docs/
│   ├── AUDIT.md                    Audit method, findings, external validation
│   ├── DEFECT_REGISTER.md          Traceability matrix + rejected findings
│   ├── TEST_PLAN.md                Objectives and coverage map
│   ├── VERIFICATION_REPORT.md      What was and was not executed
│   ├── COMPATIBILITY.md            Baseline + 2026 deprecation sweep
│   ├── MIGRATION.md                Cut-over preserving entity IDs
│   ├── SECURITY.md                 Trust boundaries
│   └── RELEASE_0.1.0.md            This document
├── .github/workflows/              hassfest, HACS, ruff, test matrices
├── CHANGELOG.md   NOTICE.md   README.md   LICENSE
├── hacs.json      pyproject.toml   requirements_test.txt
```

## 2a. Architecture decision — independent domain

0.1.0 moves the integration off the `netatmo` domain onto `netatmo_hardened`.

**Rationale.** Shadowing the core domain removes the operator's fallback. The
fork depends on substantial Home Assistant internal surface; while it masked
the built-in integration, a breaking upgrade removed Netatmo support entirely
rather than degrading to the working core version. Retaining a recoverable
failure state was judged more valuable than drop-in convenience.

**Accepted costs:** entity IDs are not inherited (mitigated by the cut-over in
`MIGRATION.md`), Home Assistant Cloud account linking is unavailable, and no
brand icon until 0.2.0. The Cloud limitation raises the API budget from 150 to
400 calls/hour, so it is a net gain operationally.

**Collision control:** domain, all dispatcher signals, and the bus event are
namespaced; HomeKit discovery removed from the manifest.

## 2b. Packaging

One artifact per release: the repository source archive. HACS installs from
`custom_components/netatmo_hardened/` in the tree, so no separate asset is
built or attached.

The optional HACS `zip_release` mechanism was evaluated and rejected. It
requires a pre-built asset whose files must sit at the archive root; an asset
that is missing, or nested one level too deep, yields a broken installation
that produces no error at release time. It would also strip the tests and
version documents out of the release artifact, which are required to ship with
the deployment package.

## 3. Defects closed

| Class | Count |
| --- | --- |
| Packaging / release integrity (P0) | 4 |
| Code defects (C) | 21 |
| Deprecations migrated (D) | 2 fixed, 7 verified already compliant |
| External findings rejected with documented reasoning | 2 |
| External findings reclassified as test objectives | 5 |

Full detail: [`DEFECT_REGISTER.md`](DEFECT_REGISTER.md).

**Highest-consequence closures:**

1. **C-4** — webhook cleanup timeout aborted unload, and therefore every
   reload, disabling the watchdog's own recovery for exactly the fault that
   triggers it.
2. **C-1** — overrides of 24 h or more silently shortened before transmission.
3. **C-2** — camera sub-events permanently destroyed after one poll cycle.
4. **P0-1** — package could not import on most of the HA versions it claimed.
5. **D-1** — deprecated pattern that becomes a hard error in HA 2026.12.

## 4. Acceptance criteria

| # | Criterion | Met |
| --- | --- | --- |
| 1 | Every accepted defect has a documented fix | ✅ |
| 2 | Every accepted defect is traceable to a test or an explicit exception | ✅ |
| 3 | Rejected recommendations documented with reasoning | ✅ |
| 4 | Static analysis clean | ✅ `ruff` all checks passed |
| 5 | No assertions used for runtime validation in production code | ✅ verified by `ruff --select S101` |
| 6 | Tier 1 suite green | ✅ 85 passed |
| 7 | Tier 2 suite green | ❌ **not executed — §5** |
| 8 | Declared platform floor is a tested claim | ⚠️ enforced by CI, not yet executed |
| 9 | Quality-scale attestations match reality | ✅ corrected |
| 10 | Change record matches the actual change set | ✅ `NOTICE.md` rewritten |
| 11 | All 2026 deprecations surveyed | ✅ `COMPATIBILITY.md` §2 |
| 12 | Security-sensitive logging eliminated | ✅ C-11 |

## 5. Recorded deviation

**Acceptance criterion 7 is not met.**

Tier 2 integration tests were authored but not executed before tagging. Home
Assistant 2026.9.3 requires Python ≥ 3.14.2 and the build environment could
obtain only CPython 3.14.0rc2. Forcing the install was rejected: a green run on
an interpreter the platform explicitly excludes is not evidence about the
supported configuration, and presenting it as such would repeat the category of
error this release corrects.

**Accepted risk.** Lifecycle and recovery fixes (C-3 through C-7, C-10, C-12
through C-15, C-20) rest on code review, static analysis and authored-but-unrun
tests. The control-integrity fixes with the highest consequence (C-1, C-2) are
covered by executed tests and do not depend on this.

**Mitigation and condition.** CI runs tier 2 on every push. **0.2.0 is blocked
until that job has passed**, and `VERIFICATION_REPORT.md` must then be updated
with the result.

## 6. Roadmap

| Target | Item |
| --- | --- |
| 0.2.0 | Clear the §5 deviation — first green tier 2 run |
| 0.2.0 | Adopt pyatmo's own webhook parser (`AsyncAccount.process_webhook`), retiring most of `event_validation.py` structurally |
| 0.2.0 | Evaluate pyatmo 9.9.1 against the topology matrix |
| 0.2.0 | Automated upstream diff in CI to detect core drift |
| 0.3.0 | Full cooling HVAC mode support (`HVACMode.COOL`) |
| 0.3.0 | Event replay / out-of-order determinism |

## 7. Approval

| Role | Basis |
| --- | --- |
| Prepared by | Independent defect audit and remediation, 2026-09-21/22 |
| Evidence | `VERIFICATION_REPORT.md` |
| Deviations | One, recorded in §5 |
| Certification claims | **None.** No IEC 62443 / 61511 / 61508 conformance is claimed. This is a development-quality package following ICS-style change control and traceability practice. |
