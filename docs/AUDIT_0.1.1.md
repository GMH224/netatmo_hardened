# Defect Audit 0.1.1 — External, Independent

**Audit series:** `AUDIT_0.1.0.md` · `AUDIT_0.1.1.md` (this document)
**Subject:** `netatmo-hardened` 0.1.0
**Audit date:** 2026-09-22
**Auditor:** independent third party
**Assessment:** source, malformed-input, lifecycle, concurrency and security audit
**Disposition of 0.1.0:** **FAIL — not suitable for operational release**

---

## 1. What this audit was and why it matters

An independent party reviewed the 0.1.0 production source. It was given the
integration only, and it **deliberately excluded** this project's own audit
reports, test plans, defect registers, verification reports and test source
from its evidence base. Source comments claiming a defect was already fixed
were read for control flow but not accepted as proof of correctness.

That independence is the reason it found what it found. Four of its nine
findings are defects **introduced by 0.1.0's own remediation** — code written
to fix the first audit's findings, reviewed by the same party that wrote it,
and shipped with its integration tests unexecuted. A review that had started
from this project's own documentation would have inherited the same blind spot.

**Every one of the nine findings was independently verified against the source
before acceptance. All nine are real.** One severity rating is revised, with
reasoning, in §4.

---

## 2. Findings and disposition

| ID | Severity | Defect | Origin | Verified by |
| --- | --- | --- | --- | --- |
| E-001 | **HIGH** | OAuth token refresh triggers a full config-entry reload | **0.1.0 regression** | HA `config_entry_oauth2_flow` source |
| E-002 | **HIGH** | Webhook registration skipped during the only startup window a cloud subscriber gets | **0.1.0 regression** | Source; `SETUP_IN_PROGRESS` at call time |
| E-003 | **HIGH → MEDIUM** | Nested sub-event members can override parent identity | **0.1.0 regression** | Source; merge reproduced |
| E-004 | **HIGH** | pyatmo boolean command results ignored; false success published | Inherited from upstream | pyatmo 9.9.0 `home.py` |
| E-005 | MEDIUM | Person members without an id are still dispatched | Inherited | Source |
| E-006 | MEDIUM | Coordinate normalisation pushes exact maxima out of range | **0.1.0 regression** | Executed probe |
| E-007 | MEDIUM | Camera timeouts escape the recoverable-error envelope | Inherited | Source; exception hierarchy |
| E-008 | LOW | Device-trigger discovery duplicates per entity | Inherited | Source |
| E-009 | LOW | Event types accept control characters that reach logs | 0.1.0 (new module) | Source |

---

## 3. The two that matter most

### E-001 — the fix that was worse than the defect

0.1.0 migrated deprecation **D-1** (a config-entry update listener combined
with a reloading method in the config flow, a hard error from HA 2026.12). The
reload decision was moved out of the config flow and into the update listener,
where it compared the entry's access token against the one the running handler
was built with, and reloaded when they differed — on the reasoning that a
changed token meant a completed reauthentication.

It does not. Verified against Home Assistant's current
`config_entry_oauth2_flow.py`:

```python
self.hass.config_entries.async_update_entry(
    self.config_entry, data={**self.config_entry.data, "token": new_token}
)
```

`OAuth2Session.async_ensure_token_valid()` persists **every routine refresh**
through `async_update_entry`, which fires update listeners. Netatmo access
tokens live about three hours. 0.1.0 therefore tore the integration down and
rebuilt it roughly **eight times a day, indefinitely** — entity teardown,
transient unavailability, webhook churn, and a reload racing whatever commands
or coordinator work was in flight.

A deprecation warning was replaced with a permanent availability defect. The
lesson is not subtle: the migration changed *when a reload happens* without
establishing *what else writes to the config entry*.

**Fixed in 0.1.1** by removing token comparison entirely. No reload is needed
for credentials in any case — `OAuth2Session` reads `entry.data["token"]` live,
so both a refresh and a completed reauth are picked up without restarting
anything. The listener now compares `entry.options` and exists solely to
refresh public weather entities.

### E-002 — a guard that disabled the feature it guarded

0.1.0 added, at the top of `async_register_webhook`:

```python
if entry.state is not ConfigEntryState.LOADED:
    return
```

It was added as belt-and-braces. But `async_setup_entry` awaits that function
**directly**, and at that moment the entry is `SETUP_IN_PROGRESS`. Registration
returned immediately, having done nothing.

The consequence is asymmetric and was therefore easy to miss:

| Path | Outcome |
| --- | --- |
| No cloud subscription → `async_at_started(register_webhook)` | Fires after startup, entry is `LOADED` — **works** |
| Cloud subscription + connected → `await register_webhook()` in setup | `SETUP_IN_PROGRESS` — **silently does nothing** |

`manage_cloudhook` only fires on a *change* of cloud connection state, so a
subscriber whose cloud stayed connected never got a webhook for the lifetime
of that runtime. Polling continued, so the integration looked healthy while
every event-driven function was dead.

This is the same silent no-webhook failure as
[home-assistant/core#178195](https://github.com/home-assistant/core/issues/178195)
— the upstream issue cited as a principal reason this fork exists.

**Fixed in 0.1.1** by removing the guard. The lifecycle protection that
actually matters is unaffected: the scheduled retry still checks `LOADED`
before re-entering (correct there — a retry firing minutes later genuinely
must not act on a dead entry), and unload cancels any pending retry outright.

---

## 4. Revised severity: E-003

The report rates the nested-identity override **HIGH**. It is downgraded to
**MEDIUM** here, and fixed regardless.

The defect is real: `{**data, **subevent}` lets a nested member overwrite
`home_id` and `device_id`, and the result becomes the Home Assistant event's
`device_id`. Shape validation does not catch it, because a substituted
identifier naming another real device passes every type, length and existence
check.

The reason for the downgrade is capability, not correctness. Reaching this code
requires possession of the webhook id — and an attacker holding it can already
post a *well-formed* event naming any device directly, with no nesting
involved. The override grants no capability they do not already have. It is a
defence-in-depth failure inside an already-authenticated boundary, not a
boundary crossing. The report's own "Security boundary" paragraph makes
substantially this point.

MEDIUM, fixed in 0.1.1 by taking identity from the parent envelope only.

---

## 5. Assessment of the audit itself

Recorded because the quality of an input governs how much weight it should
carry.

**Strong.** Its probes were genuinely executed and it says which. Its runtime
limitation — Python 3.13 available against a declared floor of 3.14.2, no
package index — is stated plainly rather than papered over. It twice **declined
to promote an observation to a defect** without runtime evidence (§5.9 webhook
retry delay, §5.10 request body size), which is the single clearest signal that
an audit is reasoning rather than counting. Its finding count is not inflated:
nine findings, nine distinct defects.

It also independently confirms controls that do hold — the ingress validation
boundary, bounded collection processing, unknown-home rejection, and
specifically that the 0.1.0 device-trigger fix (C-5) is correct, with "no
confirmed cross-device event filter defect."

**Minor inaccuracy.** §D-004 lists `climate.py:412` among paths ignoring a
success result. That line calls `Room.async_therm_set`, which returns `None` —
pyatmo offers no success indication for room-level setpoints. The finding's
substance is right and its other citations are correct; this one path cannot
be fixed as described, and `command_failed()` deliberately treats `None` as
"no indication" rather than failure for exactly this reason.

**Verdict: accepted in full, with E-003 re-rated.** Its disposition of 0.1.0 —
not suitable for operational release — is correct, and more accurate than this
project's own 0.1.0 assessment.

---

## 6. Root cause

Four defects introduced by a defect-fix release is not bad luck. Two causes,
both process:

**1. The integration tier was never executed.** `VERIFICATION_REPORT.md` §4.1
recorded this as an accepted deviation with a stated reason (no Python 3.14.2
available). E-001 and E-002 are exactly what that tier tests — lifecycle
wiring, not helper logic. The tests authored for 0.1.0 would not themselves
have caught either, because both call `async_register_webhook` on an
already-`LOADED` entry; but writing a *setup-path* test was the obvious next
case and would not have been skipped had the suite been runnable. The deviation
was not paperwork. It was the hole, and the defects went straight through it.

**2. One test asserted the weaker of two available properties.** E-006's input,
`90.0`, was already in the 0.1.0 parameter list. The assertion was
`isinstance(result, float)` — that the function did not crash, not that it
produced a legal coordinate. A test like that is worse than no test: it
converts an unknown into documented confidence.

Both are addressed in 0.1.1. The coordinate test now asserts range, and every
tier-1 test added here asserts the property that matters rather than the
property that is easy to check. The tier-2 deviation **remains open** — see
`VERIFICATION_REPORT.md` — and is the sole blocker on 0.2.0.

---

## 7. Verification of the 0.1.1 remediation

| Defect | Fix | Test |
| --- | --- | --- |
| E-001 | Listener compares options, never tokens; no reload path | `test_token_refresh_does_not_reload`, `test_repeated_token_refresh_never_reloads`, `test_options_change_still_refreshes_public_weather` |
| E-002 | `LOADED` guard removed from the registration entry point | `test_webhook_registers_during_setup_with_active_cloud`, `test_registration_is_not_gated_on_loaded_state` |
| E-003 | `merge_subevent()` — parent identity immutable | 9 tier-1 tests in `test_identity_protection.py` |
| E-004 | `command_failed()` + `async_command()`; raises on `False` | `test_only_explicit_false_counts_as_command_failure` (6 cases), 4 tier-2 tests |
| E-005 | Members without a valid id are skipped | `test_adversarial_corpus_never_raises`, tier 2 |
| E-006 | Nudge applied inward at boundaries; `limit` now required | `test_normalised_coordinate_stays_in_range` (14 coords), `test_exact_maximum_is_nudged_inward` |
| E-007 | `CAMERA_TRANSPORT_ERRORS` incl. `TimeoutError`; stream path wrapped | `test_camera_snapshot_timeout_is_absorbed`, `test_camera_url_refresh_timeout_is_absorbed` |
| E-008 | One trigger per logical device event | Tier 2 |
| E-009 | Control characters rejected in identifiers | 8 tier-1 cases + log-summary test |

Full traceability in `DEFECT_REGISTER.md`. Execution status — including what
was **not** run — in `VERIFICATION_REPORT.md`.
