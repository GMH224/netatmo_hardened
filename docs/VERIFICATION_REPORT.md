# Verification Report

**Release:** 0.1.4
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
| `ruff format --check` | 39 files already formatted | ✅ Yes |
| **Explicit compile of every `.py` file** | **41 files, 0 syntax errors** | ✅ Yes — see §2.1a |
| Tier 1 test suite (pure logic) | **244 passed, 0 failed** | ✅ Yes |
| Tier 2 test suite (integration) | Authored, not executed here | ❌ **No — see §4** |
| `hassfest` manifest validation | Configured in CI | ❌ Not executed here |
| HACS validation | Configured in CI | ❌ Not executed here |
| Live Home Assistant deployment | **Soak performed on 0.1.1** — HA 2026.9.3, Python 3.14.6, HA OS 18.3. Found E-010. | ✅ Yes (0.1.1) |
| Home Assistant translation-loading behaviour | `helpers/translation.py` @ `dev` read; no `[%key:…%]` expansion at load time | ✅ Yes |
| pyatmo 9.9.0 source review | Performed for C-14, C-7, NET-001 | ✅ Yes |

---

## 2. What was executed

### 2.1 Static analysis

```
$ ruff check custom_components tests
All checks passed!

$ ruff format --check custom_components tests
39 files already formatted
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

### 2.1a Compile gate (executed) — new in 0.1.3

```
$ python3.13 -c "compile every .py in the repository"
compile gate: 39 files, 0 errors
```

**This gate exists because `ruff check` was shown to be insufficient.** The
`ruff 0.15.11` binary in this build environment rewrites `except (A, B):` into
`except A, B:` — Python 2 syntax, a hard `SyntaxError` — and then reports
**"All checks passed!"** on the file it corrupted. Reproduced on a minimal
six-line case; full analysis in `AUDIT_0.1.3.md` §5.

No shipped release is affected: every `except` clause in 0.1.0 through 0.1.2
uses the `except (A, B) as err:` form, which the tool leaves alone. The one
affected construct was introduced in 0.1.3 and caught before release — by a
manual `py_compile`, **not** by the lint gate that was supposed to catch it.

`ruff check` has been this project's static-analysis gate since 0.1.0 and has
now been demonstrated to pass a file that cannot be imported. A linter is not
a syntax gate. Both now run, and the compile gate is a release blocker.

### 2.2 Tier 1 — pure logic (executed)

```
$ python -m pytest tests/unit
244 passed in 0.25s
```

| File | Tests | Covers |
| --- | --- | --- |
| `test_control_integrity.py` | 41 | C-1, C-2, C-19, C-21, **E-004**, **E-006** |
| `test_event_validation.py` | 56 | C-8, C-9, C-11, C-18 |
| `test_identity_protection.py` | 27 | **E-003**, **E-009** |
| `test_push_event_policy.py` | 26 | **E-010**, **E-011**, **F-001** |
| `test_release_integrity.py` | 9 | **P0-1**, **P0-2**, shipped-artefact gate |
| `test_telemetry.py` | 60 | **F-002**, plus **C-11** and **E-009** re-applied to entity states |
| `test_poll_scheduling.py` | 25 | **F-003**, **F-004** |

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

### 4.2 Live deployment — partially executed for 0.1.2

**This section is no longer wholly negative.** 0.1.1 was deployed to the
operator's test environment on 2026-09-22 (HA 2026.9.3, Python 3.14.6, Home
Assistant OS 18.3) following `COMMISSIONING.md`. What that established:

| Verified live | Result |
| --- | --- |
| Custom-integration load on the declared baseline | Loaded; `overwrites_built_in: false` — the independent-domain cut-over holds |
| Topology fetch and polling against a real Netatmo account | Working (`NAMain` + 2 × `NAModule4`, three homes) |
| Diagnostics redaction | Tokens, `webhook_id`, `cloudhook_url`, home names and coordinates all `**REDACTED**`; `grep -ci "api/webhook/"` over the supplied log returned **0** |
| Webhook registration without a public HTTPS endpoint | Rejected `400 WH006` — **found E-010** |

What the soak did **not** establish, because the account and environment do not
provide it:

* push-event delivery (no public HTTPS endpoint — F-001's enabled branch);
* camera, thermostat, presence or cooling behaviour (weather station only, so
  the whole control-integrity class remains unexercised against hardware);
* long-running listener/timer growth (the soak was hours, not days);
* whether E-001's reload fix holds over many token refresh cycles — debug
  logging was not enabled, so the reload count could not be read from the log.

### 4.3 Remaining live gaps

No live Netatmo account, no real hardware, and no running Home Assistant
instance were used. The following therefore remain unverified end to end:

* actual webhook delivery from Netatmo's servers;
* real cooling-mode payloads from a cooling home (C-14 is built on the pyatmo
  data model, not on an observed payload);
* the outdoor-camera sub-event behaviour change (C-18) against live traffic;
* long-running listener/timer growth over days of reconnect cycles (C-12 is
  verified structurally and by an authored tier 2 test only).

### 4.4 hassfest and HACS validation

Both are configured in CI but require the GitHub Actions environment. Neither
was run locally.

---

## 5. Defect verification status

| Verification strength | Defects |
| --- | --- |
| **Executed test** | C-1, C-2, C-8, C-9 (partial), C-11 (partial), C-18 (partial), C-19, C-21, E-003, E-004 (tier 1 half), E-006, E-009, **E-010** (classification), **E-011**, **F-001** (default and keys), **F-002** (recorder, classification, redaction, bounded memory, totality) |
| **Static analysis** | P0-1, C-16, C-17 |
| **Observed live** | **E-010** (reproduced from the operator's log and diagnostics) |
| **Authored test, awaiting first CI run** | C-3, C-4, C-5, C-6, C-7, C-10, C-12, C-13, C-14, C-15, C-20, E-001, E-002, E-005, E-007, E-008, **E-010** (lifecycle half), **F-001** (lifecycle half), **F-002** (entity creation, availability during an outage, coordinator round trip) |
| **Review and documentation only** | P0-3, P0-4, D-1 … D-9 |

P0-2 moves out of "review only": its remediation was incomplete until 0.1.2
(see E-011) and is now covered by executed tests.

---

## 6. Release recommendation

**Fit for release as 0.1.4**, with the residual risk in §4 accepted and
recorded.

**What changed for 0.1.3.** A compile gate was added after `ruff check` was
shown to pass a file that cannot be imported (§2.1a) — the third instance in
this project of a check that did not check the thing that mattered. Tier 1
grew from 159 to 219 tests, all of the new ones covering F-002.

**What did not change.** Tier 2 is still unexecuted, and 0.1.3 adds a feature
whose central requirement — that the telemetry sensors stay readable while the
API fails — is reachable only from tier 2. There has also been **no live soak
of 0.1.3**, so every failure path in the new feature has been exercised by
fixtures only. Until the failure ratio has been seen going non-zero and back
on real hardware, it is an untested indicator, and an untested indicator
reading "healthy" is the same hazard as no indicator.

**Carried from 0.1.2.** The live soak is no longer a gap for the load and
polling path — it is executed evidence, and it is what found E-010. Tier 1
grew from 124 to 159 tests and now covers the shipped translation artefact as
well as the source it came from. What has not changed is §4.1: tier 2 remains
unexecuted here, and F-001 is a load-time decision that only tier 2 reaches.

**Carried from 0.1.1**, and still true: the same residual risk that produced
E-001 and E-002 is the residual risk under which E-010's lifecycle half and
F-001's gating are shipping now.

The four release-blocking packaging defects are fixed and statically verified.
The two highest-consequence control-integrity defects — C-1 (truncated
override durations) and C-2 (destroyed camera sub-events) — are covered by
executed tests. The remaining fixes are reviewed, statically clean, and carry
authored tests that will execute on the first CI run.

**Condition on the next release:** 0.2.0 must not be tagged until the tier 2
job has passed, and this report must be updated with its result. That condition
was set for 0.1.2 and is **not met** — 0.1.2 ships with it outstanding, which is
recorded here rather than quietly dropped. The condition stands, unchanged, and
its cost is now two releases of accumulated unexecuted lifecycle tests.

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
