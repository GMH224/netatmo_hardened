# Defect Audit 0.1.2 — Live Soak and Internal Review

**Release under audit:** 0.1.1
**Audit performed for:** 0.1.2
**Date:** 2026-09-22
**Baseline:** Home Assistant 2026.9.3, pyatmo 9.9.0, Python 3.14.6, Home Assistant OS 18.3
**Method:** operational soak in the operator's test environment, plus source
review of the areas that soak touched
**Status:** 2 defects found, both fixed. 1 deliberate functional change.

---

## 1. What this audit was

0.1.0 and 0.1.1 were audited by reading code. This one was mostly not.

`COMMISSIONING.md` was written for 0.1.1 as a *compensating control*: the tier 2
integration suite has never been executed in the authoring environment (§6 of
`AUDIT_0.1.1.md`), so a documented first-start procedure in a real runtime was
substituted for the test evidence that was missing. The operator ran it on
2026-09-22 against HA 2026.9.3.

It worked. The first defect surfaced four minutes after start, from the log and
the diagnostics download, and it is not a defect that reading the code would
plausibly have found — it is a defect about what happens to a correct retry loop
when the environment never changes.

That is the finding worth recording ahead of the defects themselves. The
compensating control paid for itself on its first use, which is also evidence
that it is not an adequate *substitute* for the tier 2 suite — only that it is
better than nothing. It catches what a running system does; it does not catch
what a rarely-taken branch does.

---

## 2. Evidence reviewed

| Artefact | Source | What it showed |
| --- | --- | --- |
| `home-assistant_2026-09-22T06-23-10.log` | Operator's test environment | `400 - invalid webhook url (WH006)`, repeating on the retry schedule |
| `config_entry-netatmo_hardened-01M33W3N4PWKBYA097NA8F29MM.json` | Diagnostics download | `webhook_registered: false`; `options: {}`; topology loaded and polling normally |
| `custom_components` block of the same file | Diagnostics download | 0.1.1 installed as a custom integration; `overwrites_built_in: false` — the independent-domain cut-over holds |
| `homeassistant/helpers/translation.py` @ `dev` | HA source | No `[%key:…%]` substitution at load time (§4) |
| `homeassistant/strings.json`, `components/netatmo/strings.json` @ `dev` | HA source | Literal English for all 24 references |

The diagnostics redaction was verified before use: tokens, `webhook_id`,
`cloudhook_url`, home names and coordinates are all `**REDACTED**`. `grep -ci
"api/webhook/"` over the supplied log returned 0, so the webhook bearer secret
did not leave the operator's environment.

---

## 3. E-010 — a retry loop with no exit

**Severity:** Medium. **Regression introduced by 0.1.1.**

### What was observed

```
Netatmo webhook registration failed (400 - invalid webhook url (WH006)).
Retrying in 30 seconds (attempt 1)
```

then at 120 s, 600 s, 900 s, and every 900 s thereafter, unchanged.

### Why it happens

Netatmo will only accept a webhook URL that is a publicly reachable HTTPS
endpoint on port 443. The operator's Home Assistant is not published to the
internet — a normal, and for an ICS-adjacent deployment a *preferable*,
configuration. Netatmo therefore answers `400 WH006` deterministically, and will
answer it identically until a human changes the network.

### Why the code did it

This is the third position the code has held on the same question, and the
first two were both wrong in the same way.

| Version | Behaviour on webhook registration failure | Failure mode |
| --- | --- | --- |
| Upstream | Give up after the first failure | One transient 429 left push events dead until a manual reload. Silent. |
| 0.1.1 | Retry indefinitely, backing off to 15 min | A deterministic rejection retried for ever: useless API traffic against a rate-limited account, a `WARNING` every 15 min, and no statement of what to fix. |
| 0.1.2 | Classify, then retry or stop | — |

Neither earlier version distinguished *"this might work later"* from *"this
cannot work until a human changes something"*. Upstream treated everything as
the second; 0.1.1 treated everything as the first. The defect is not the
direction, it is that the question was never asked.

### Fix

`helper.webhook_failure_is_permanent(status, *, throttled)` — a pure function,
tier-1 testable, with the classification stated as data:

```python
PERMANENT_WEBHOOK_FAILURE_STATUSES = frozenset({400, 401, 403, 404})
```

On a permanent classification the loop stops and a repair issue is raised
carrying the API's own error text, so the operator is told that push events are
unavailable, why, and that polling is unaffected. Everything else retries as
0.1.1 did.

`throttled` is passed separately and wins, because Netatmo answers **403** when
rate limiting and pyatmo surfaces that as `ApiThrottlingError`. Classifying on
the status alone would have abandoned a registration that would have succeeded
minutes later — reintroducing the upstream defect while fixing the 0.1.1 one.
A `status` of `None` (`TimeoutError`, `aiohttp.ClientError`) is transient: an
unknown failure must not be treated as permanent.

### Residual risk

`400` is broad. If Netatmo ever answers `400` for a genuinely transient
condition, push events will stop with a repair issue rather than recovering on
their own. Accepted: the failure is loud, it is visible in the UI, and it is
one reload away from being retried — whereas the alternative is unbounded
traffic against a rate-limited account. The set is asserted explicitly in
`test_permanent_status_set_is_explicit` so that widening it must be a deliberate
edit to an assertion.

---

## 4. E-011 — 24 user-facing strings shipped as placeholder text

**Severity:** Medium. Found by inspection, not by the soak.

`translations/en.json` contained 24 unresolved cross-references:

```json
"n": "[%key:component::netatmo::entity::sensor::wind_direction::state::n%]",
"title": "[%key:component::netatmo::options::step::public_weather::title%]",
"reauth_successful": "[%key:common::config_flow::abort::reauth_successful%]"
```

Home Assistant expands those when it **builds core**, not when it loads
translations. `homeassistant/helpers/translation.py` at `dev` does
`load_json()` followed by `recursive_flatten()` — there is no substitution step
anywhere in the load path, and `homeassistant/components/netatmo/translations/`
does not exist in the repository at all, because core's `translations/en.json`
is a *generated* artefact.

A custom integration has no such build step. Whatever is in the file is what the
operator sees. Every wind direction would have rendered as
`[%key:component::netatmo::entity::sensor::wind_direction::state::n%]`.

This is the unfinished half of **P0-1/P0-2**. The 0.1.0 register records that no
`translations/` directory was shipped and that one was "generated from
`strings.json`". It was generated by copying. That fixed the loud failure — raw
dotted keys in the config flow — and left a quieter one behind, which no
subsequent audit caught because both audits read `strings.json`, where the
references are correct.

### Fix

Every reference expanded to literal English in both files, resolved from
`homeassistant/strings.json` and `homeassistant/components/netatmo/strings.json`
at `dev`. `strings.json` and `translations/en.json` are now byte-identical, and
a test asserts that they stay so: with no build step between the reviewed file
and the served file, identity is the only thing that keeps them honest.

### Process finding

Both 0.1.0 and 0.1.1 verified `strings.json`. Neither verified the file the
runtime actually reads. The general form — *validate the shipped artefact, not
the source it was derived from* — is the same lesson as the `netatmo.zip`
packaging defect in 0.1.0, where the archive layout was wrong while the source
tree was right. Two instances is a pattern, so §7 adds it to the release gate.

---

## 5. F-001 — push events are opt-in (deliberate change)

E-010's fix stops the futile retries. It does not stop the deployment from
*asking* for something it cannot have. The operator asked for the switch
directly, and it is the correct structural answer: the integration cannot
detect whether Home Assistant is publicly reachable, and the operator can.

`enable_webhook`, default `False`. Full rationale, operator impact and the
E-001 boundary are in `DEFECT_REGISTER.md` §2c.

Two consequences were handled rather than accepted:

**Camera floodlight availability.** `light.py` marked floodlights unavailable
whenever no webhook was established, because their state arrives by push. Under
the new default that would have stranded the entity permanently — unavailable,
therefore not even controllable. `webhook_expected` (did the operator ask for
push?) is now distinct from `webhook` (is one established?), and the gate applies
only when push is expected and missing. Deliberately-off means available:
commands work, state comes from polling.

**A reload the update listener may schedule.** Whether the webhook subsystem is
wired up is decided in `async_setup_entry`, so this one option cannot be applied
in place. E-001 was a reload on every routine token write — eight times a day,
for ever. This is a reload when a human changes one setting.
`test_unrelated_option_change_does_not_reload` holds the boundary explicitly,
because the distinction is the kind that erodes.

**Regression risk accepted:** the tier 2 fixture previously had no options, so
every webhook test in the suite would now pass *vacuously* — asserting nothing
happened, for the wrong reason. `mock_config_entry` therefore opts in, and a
separate `push_disabled_config_entry` fixture covers the shipped default and the
0.1.1→0.1.2 upgrade path (empty options, not `False`).

---

## 6. What this audit did not cover

Stated plainly, because an audit that does not say where it stops is not
evidence of anything.

- **The tier 2 suite still has not been executed.** HA 2026.9.3 requires Python
  ≥ 3.14.2; the authoring container offers 3.14.0rc2. Forcing the install with
  `--ignore-requires-python` would produce a green result that is not evidence.
  The nine tier 2 tests added in 0.1.2 are therefore **written and unrun**, like
  the rest of that tier. CI executes them; see `VERIFICATION_REPORT.md`.
- **The permanent/transient classification has been observed for `400` only.**
  `401`, `403` and `404` are reasoned, not observed.
- **No camera, thermostat or presence hardware was in the soak.** The operator's
  account is a weather station (`NAMain` + 2 × `NAModule4`). Everything in the
  control-integrity class (E-004) remains unexercised against real hardware.
- **The push-enabled path was not soaked**, because the environment cannot
  register a webhook. Turning F-001 on is untested outside CI.
- **No second independent audit of 0.1.2.** 0.1.1 was externally audited and
  that audit found four regressions this project had introduced. 0.1.2 has not
  been. See §7.

---

## 7. Recommendation

**Release 0.1.2, then soak before trusting it.**

The two defects are small, well-understood and covered by 24 new tests. The
functional change is the one the operator asked for and is off by default, which
is the conservative direction.

Against that: 0.1.0 shipped with four regressions of its own making, found only
by an independent audit. 0.1.2 is a smaller change than 0.1.0 was — but so was
the E-010 fix that 0.1.1 introduced, and it was still wrong. The honest position
is that this project's record on self-review is two-for-three, and the one time
it was checked independently, it failed.

Release gate additions carried forward from this audit:

1. **Validate the shipped artefact, not its source.** Applies to
   `translations/en.json` versus `strings.json` (E-011) and to the release
   archive layout versus the source tree (P0-x). Both are now asserted by tests.
2. **Any change to retry, backoff or give-up logic requires an explicit
   statement of which failures are permanent**, reviewed as a list. Three
   versions have now taken three positions on this single question.
3. **A default that changes shipped behaviour must name what it costs the
   existing operator**, in `MIGRATION.md`, before the release is tagged.
