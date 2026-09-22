# Verification Report

**Release:** 0.1.1
**Date:** 2026-09-22
**Baseline:** Home Assistant 2026.9, pyatmo 9.9.0, Python 3.14.2

This report states what was actually executed before release, and — equally
important for an ICS package — what was not. A verification report that claims
more coverage than it has is worse than no report, because it removes the
reader's ability to judge residual risk for themselves. The release this one
replaces asserted `config-flow-test-coverage: done` with no tests in the
archive at all; that is the failure mode this document exists to prevent.

---

## 1. Summary

| Activity | Result | Executed? |
| --- | --- | --- |
| Static syntax check, all modules | Pass | ✅ Yes |
| `ruff check` (F, E, W, B, S, ASYNC, RUF, UP, I) | **All checks passed** | ✅ Yes |
| `ruff format --check` | 31 files already formatted | ✅ Yes |
| Tier 1 test suite (pure logic) | **124 passed, 0 failed** | ✅ Yes |
| Tier 2 test suite (integration) | Authored, not executed here | ❌ **No — see §4** |
| `hassfest` manifest validation | Configured in CI | ❌ Not executed here |
| HACS validation | Configured in CI | ❌ Not executed here |
| Live Home Assistant deployment | Not performed | ❌ No |
| pyatmo 9.9.0 source review | Performed for C-14, C-7, NET-001 | ✅ Yes |

---

## 2. What was executed

### 2.1 Static analysis

```
$ ruff check custom_components tests
All checks passed!

$ ruff format --check custom_components tests
31 files already formatted
```

Two results are worth calling out specifically:

* **`F821` (undefined name) is clean.** The pre-release tree reported four
  `F821` errors — `NetatmoDataHandler` in three `coordinator.py` dataclasses
  and `NetatmoSource` in `media_source.py`. Those were the visible symptom of
  defect P0-1: the code only imported on Python 3.14+. With
  `from __future__ import annotations` now in every module, that class of
  latent break is gone rather than merely hidden by a version floor.
* **`S101` (assert) is clean across `custom_components/`.** Defect C-16 is
  verified by absence: there are no assertions left in production code.

### 2.2 Tier 1 — pure logic (executed)

```
$ python -m pytest tests/unit
124 passed in 0.09s
```

| File | Tests | Covers |
| --- | --- | --- |
| `test_control_integrity.py` | 52 | C-1, C-2, C-19, C-21, **E-004**, **E-006** |
| `test_event_validation.py` | 55 | C-8, C-9, C-11, C-18 |
| `test_identity_protection.py` | 17 | **E-003**, **E-009** |

These are executed on the authoring environment's Python 3.11 and in CI on
3.11 / 3.12 / 3.13 / 3.14. That portability is deliberate: the arithmetic
deciding how long a heating override lasts, and the validation deciding whether
a hostile webhook payload is accepted, should be verifiable without standing up
a smart-home platform first.

**Notable result (0.1.1).** The coordinate test that let **E-006** through is
corrected. In 0.1.0 it included `90.0` in its parameters but asserted only
`isinstance(result, float)` — that the function did not crash, not that it
produced a legal coordinate. It now asserts the value lies within the
coordinate's legal domain, across 14 inputs including every boundary. A test
that checks the weaker of two available properties is worse than no test,
because it converts an unknown into documented confidence.

**Notable result (0.1.0).** One test failed on first run —
`test_coordinate_normalisation_reproduces_old_crash` — and the failure was in
the *test*, not the fix: `1e-7` already carries seven decimal places, so the
correct behaviour is to leave it untouched. The test was corrected and a
parametrised precision-rule test added. Recorded here because it is evidence
the suite was genuinely run rather than written and asserted green.

### 2.3 Dependency source review (executed)

`pyatmo 9.9.0` was downloaded from PyPI and read directly to verify three
claims that could not be settled from the integration source alone:

| Claim | File reviewed | Outcome |
| --- | --- | --- |
| `disabled_homes_ids` is a home-only denylist | `account.py` | Confirmed — external finding NET-001 rejected as stated |
| Cooling exposes `cooling_setpoint_*` and unified `setpoint_mode` / `setpoint_temperature` | `room.py` | Confirmed — C-14 fix built on these |
| `ApiError` carries `status` / `code`; throttling is a distinct subclass | `exceptions.py` | Confirmed — C-7 classification built on these |

---

## 3. Upstream corroboration (executed)

Three referenced Home Assistant issues were opened and read, not taken on
trust:

| Issue | Title | Bearing |
| --- | --- | --- |
| [#181448](https://github.com/home-assistant/core/issues/181448) | Disabling the home device silently removes all entities (2026.9.0) | Complaint is *silence*, not the denylist — NET-001 downgraded, log line added |
| [#175581](https://github.com/home-assistant/core/issues/175581) | Netatmo climate entities unavailable (KeyError: None) in cooling mode | Real, but describes core; the fork had already stopped the crash — C-14 addresses the wrong-data residue |
| [#178195](https://github.com/home-assistant/core/issues/178195) | Degraded state after transient 429 during webhook setup | Confirms the fork's retry work addresses a genuine reported failure |

---

## 4. What was NOT executed, and why

### 4.1 Tier 2 integration tests

**Not executed in the authoring environment — and this deviation is now known
to be causal, not procedural.**

E-001 and E-002, the two live availability defects that made 0.1.1 necessary,
are exactly what this tier exercises: lifecycle wiring rather than helper
logic. The deviation recorded for 0.1.0 was not paperwork. It was the hole, and
two defects went straight through it.

Home Assistant 2026.9.3 requires Python ≥ 3.14.2. The only CPython 3.14 build
obtainable in the build container was **3.14.0rc2**, and the dependency
resolver correctly refused the install:

```
Because the current Python version (3.14rc2) does not satisfy Python>=3.14.2
and homeassistant==2026.9.3 depends on Python>=3.14.2, we can conclude that
homeassistant==2026.9.3 cannot be used.
```

Forcing the install with `--ignore-requires-python` was considered and
rejected: a green run against a release-candidate interpreter the platform
explicitly excludes is not evidence about the supported configuration, and
presenting it as such would be the same category of error as the quality-scale
attestations this release corrects.

**Consequence — residual risk.** The tier 2 files in `tests/integration/` are
authored against the documented Home Assistant test APIs but have **never been
executed**. They should be treated as unproven until the first CI run. Expect
to fix fixture details on that first run; that is normal and is not evidence
the underlying fixes are wrong, which tier 1 and static analysis cover
independently.

**Mitigation.** `.github/workflows/test.yml` runs tier 2 on every push against
HA 2026.9.0 and 2026.9.3. Do not tag a subsequent release until that job has
passed at least once.

### 4.2 Live deployment

No live Netatmo account, no real hardware, and no running Home Assistant
instance were used. The following therefore remain unverified end to end:

* actual webhook delivery from Netatmo's servers;
* real cooling-mode payloads from a cooling home (C-14 is built on the pyatmo
  data model, not on an observed payload);
* the outdoor-camera sub-event behaviour change (C-18) against live traffic;
* long-running listener/timer growth over days of reconnect cycles (C-12 is
  verified structurally and by an authored tier 2 test only).

### 4.3 hassfest and HACS validation

Both are configured in CI but require the GitHub Actions environment. Neither
was run locally.

---

## 5. Defect verification status

| Verification strength | Defects |
| --- | --- |
| **Executed test** | C-1, C-2, C-8, C-9 (partial), C-11 (partial), C-18 (partial), C-19, C-21 |
| **Static analysis** | P0-1, C-16, C-17 |
| **Authored test, awaiting first CI run** | C-3, C-4, C-5, C-6, C-7, C-10, C-12, C-13, C-14, C-15, C-20 |
| **Review and documentation only** | P0-2, P0-3, P0-4, D-1 … D-9 |

---

## 6. Release recommendation

**Fit for release as 0.1.1**, with the residual risk in §4 accepted and
recorded — and with the explicit qualification that the same residual risk
produced the defects this release fixes.

The four release-blocking packaging defects are fixed and statically verified.
The two highest-consequence control-integrity defects — C-1 (truncated
override durations) and C-2 (destroyed camera sub-events) — are covered by
executed tests. The remaining fixes are reviewed, statically clean, and carry
authored tests that will execute on the first CI run.

**Condition on the next release:** 0.2.0 must not be tagged until the tier 2
job has passed, and this report must be updated with its result.

---

## 7. Reproducing this verification

```bash
# Tier 1 - no Home Assistant required, runs on Python >= 3.11
pip install pytest==8.3.4
pytest tests/unit -v

# Static analysis
pip install ruff==0.15.11
ruff check custom_components tests
ruff format --check custom_components tests

# Tier 2 - requires Python >= 3.14.2
pip install -r requirements_test.txt
pytest tests/integration -v
```
