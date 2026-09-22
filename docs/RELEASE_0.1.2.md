# Release Record — 0.1.2

| Field | Value |
| --- | --- |
| **Version** | 0.1.2 |
| **Date** | 2026-09-22 |
| **Type** | Corrective + functional — remediates the 0.1.1 live soak, adds a push-event switch |
| **Domain** | `netatmo_hardened` |
| **Platform baseline** | Home Assistant 2026.9 · Python 3.14.2 · pyatmo 9.9.0 (unchanged) |
| **Predecessor** | 0.1.1 (2026-09-22) |
| **Status** | Released with one recorded deviation — see §6 |

---

## 1. Why this release exists

Two reasons, one operational and one requested.

**The soak found a defect.** `COMMISSIONING.md` was written for 0.1.1 as a
compensating control for the unexecuted tier 2 suite. The operator ran it
against Home Assistant 2026.9.3 on 2026-09-22, and within four minutes it
surfaced **E-010**: 0.1.1 retried a webhook registration that Netatmo rejects
deterministically — `400 — invalid webhook url (WH006)` — every fifteen
minutes, for ever, against a rate-limited account, with no statement of what
the operator should change.

**The operator asked for a switch.** An installation with no publicly
reachable HTTPS endpoint cannot use Netatmo push events at all. 0.1.2 makes the
webhook subsystem an explicit option, **off by default** (F-001). E-010's fix
handles the symptom; F-001 removes the cause.

A third item, **E-011**, was found by inspection while implementing the above:
24 user-facing strings shipped as unresolved `[%key:…%]` placeholders and would
have been displayed to the operator as literal placeholder text.

Full analysis: [`AUDIT_0.1.2.md`](AUDIT_0.1.2.md).

## 2. Scope

| Class | Count | IDs |
| --- | --- | --- |
| Defects fixed | 2 | E-010, E-011 |
| of which regressions introduced by this fork | 1 | E-010 (by 0.1.1) |
| Deliberate functional changes | 1 | F-001 |
| Dependency changes | 0 | pyatmo stays pinned at 9.9.0 |
| Platform baseline changes | 0 | |
| New tier-1 tests | 35 | |
| New tier-2 tests | 9 | |

## 3. Changes

### E-010 — webhook retries now classify the failure

`helper.webhook_failure_is_permanent()` decides whether another attempt could
ever succeed. `400/401/403/404` stop the loop and raise a repair issue carrying
Netatmo's own error text; throttling is exempt, because Netatmo answers `403`
when rate limiting; everything else, including failures with no status at all,
retries exactly as 0.1.1 did.

### E-011 — translations are literal text

All 24 `[%key:…%]` references expanded in `strings.json` and
`translations/en.json`, which are now byte-identical. Home Assistant resolves
those references when it builds core, not at load time, so a custom
integration's file is served verbatim.

### F-001 — push events are opt-in

New option `enable_webhook`, default `False`, under a new options menu
(*Push events* / *Public weather areas*). Polling is untouched; only push is
affected. See §5 for what this costs an existing installation.

## 4. Documentation set

```
docs/
├── AUDIT_0.1.0.md          Internal audit that produced 0.1.0
├── AUDIT_0.1.1.md          External independent audit of 0.1.0 + remediation
├── AUDIT_0.1.2.md          Live soak and internal review of 0.1.1
├── RELEASE_0.1.0.md        Release record, 0.1.0
├── RELEASE_0.1.1.md        Release record, 0.1.1
├── RELEASE_0.1.2.md        This document
├── DEFECT_REGISTER.md      Cumulative traceability, C-, D-, E- and F-series
├── TEST_PLAN.md            Objectives and coverage map
├── VERIFICATION_REPORT.md  What was and was not executed
├── COMPATIBILITY.md        Platform baseline and 2026 deprecation sweep
├── COMMISSIONING.md        Live soak checklist before unattended deployment
├── MIGRATION.md            Cut-over from the built-in integration
└── SECURITY.md             Trust boundaries
```

## 5. Upgrade — read this before updating

0.1.1 → 0.1.2 is a drop-in update through HACS. No re-authentication, no
entity change, no configuration migration.

**One behaviour changes by default: push events are now off.**

| You are… | What happens | What to do |
| --- | --- | --- |
| Without a public HTTPS endpoint (most installations) | The repeating `WH006` warning stops. Nothing else changes — all data already arrived by polling. | Nothing. |
| A Home Assistant Cloud subscriber, or you publish HA over HTTPS on port 443 | **Push events stop working** until you turn them on. Data still arrives, on the polling interval instead of instantly. | Settings → Devices & services → Netatmo (hardened) → **Configure** → *Push events* → enable. |

The default is off because the integration cannot detect whether the deployment
is reachable from the internet, and the failure mode of guessing wrong in the
other direction is the one E-010 documents. Turning it on takes one click and
is remembered.

Camera floodlights remain **available** when push is off: they are controllable
and their state is refreshed by polling. Under 0.1.1 the absence of a webhook
made them unavailable, which would have been permanent under the new default.

## 6. Acceptance criteria

| # | Criterion | Met |
| --- | --- | --- |
| 1 | Every soak finding reproduced from evidence before acceptance | ✅ 2/2 |
| 2 | Every fix traced to a test in `DEFECT_REGISTER.md` | ✅ |
| 3 | Functional change documented with its cost to existing operators | ✅ §5, `MIGRATION.md` |
| 4 | E-001 boundary explicitly re-tested after adding a reload path | ✅ `test_unrelated_option_change_does_not_reload` |
| 5 | Static analysis clean | ✅ `ruff check` — all checks passed |
| 6 | Tier 1 suite green | ✅ **159 passed** (124 → 159) |
| 7 | Tier 2 suite green | ❌ **not executed — §7** |
| 8 | Shipped artefacts validated, not just their sources | ✅ new in 0.1.2 — translations and archive layout both asserted |
| 9 | Audit document versioned per release | ✅ `AUDIT_0.1.2.md` |
| 10 | Independent audit of this release | ❌ **not performed** — see §8 |

## 7. Recorded deviation — carried forward from 0.1.0 and 0.1.1

**Acceptance criterion 7 is not met.** Home Assistant 2026.9.3 requires Python
≥ 3.14.2; the build environment can obtain only CPython 3.14.0rc2. Forcing the
install with `--ignore-requires-python` remains rejected: a green run on an
interpreter the platform excludes is not evidence about the supported
configuration.

**Consequence for this release.** F-001 is a *load-time* decision taken in
`async_setup_entry`, which is precisely the region tier 2 covers and tier 1
cannot reach. The nine tier 2 tests written for it are unrun. What is executed
is the decision data underneath them: the classification function (15 cases),
the option's default and key, the shipped translation file (11 cases), and the
release artefact itself (9 cases).

**Condition.** CI runs tier 2 on every push. **Do not deploy 0.1.2 to an
unattended installation until that job has passed at least once**, and record
the result in `VERIFICATION_REPORT.md`.

**Compensating control.** `COMMISSIONING.md`, updated for 0.1.2 with the
push-event decision and the checks that distinguish "push is off because I
turned it off" from "push is broken". Its first use produced E-010 four minutes
in, which is the strongest available evidence both that the control works and
that it is not a substitute for the test tier it compensates for.

## 8. Approval

| Role | Basis |
| --- | --- |
| Operational evidence | Live soak, operator's test environment, HA 2026.9.3 / Python 3.14.6 / HA OS 18.3, 2026-09-22 |
| Verification of findings | E-010 reproduced from the operator's log and diagnostics; E-011 verified against Home Assistant's own `helpers/translation.py` at `dev` |
| Remediation | This release |
| Deviations | Two: §7 (tier 2 unexecuted) and §6 criterion 10 (no independent audit of 0.1.2) |
| Certification claims | **None.** No IEC 62443 / 61511 / 61508 conformance is claimed. |

**Note on criterion 10.** 0.1.0 was self-reviewed and shipped four regressions
of its own making, found only when an independent party looked. 0.1.2 is a
smaller change — but so was the 0.1.1 retry fix that became E-010. Self-review
has a two-for-three record on this project. An independent audit before
unattended deployment remains the recommendation, and its absence is recorded
here rather than left to inference.
