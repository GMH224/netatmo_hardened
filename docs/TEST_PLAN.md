# Test Plan

**Release:** 0.1.0
**Baseline:** Home Assistant 2026.9 · Python 3.14.2 · pyatmo 9.9.0

## 1. Strategy

The suite is split into two tiers on a deliberate principle: **the logic where
a silent defect changes what the integration does must be verifiable without a
Home Assistant runtime.**

The two most damaging defects in this release — a truncated heating override
and destroyed camera sub-events — were both pure-logic faults sitting inside
code that needed a whole smart-home platform to exercise. Extracting that logic
into `helper.py` and `event_validation.py` was a design change made to make it
testable, not a cosmetic refactor.

| Tier | Location | Needs HA? | Runs on | Purpose |
| --- | --- | --- | --- | --- |
| 1 | `tests/unit/` | No | Python 3.11–3.14 | Control-path arithmetic, event snapshots, webhook ingress validation |
| 2 | `tests/integration/` | Yes | Python 3.14.2 + HA 2026.9 | Lifecycle, recovery, auth classification, triggers, entity behaviour |

## 2. Coverage map

| Defect | Objective | Tier | Test |
| --- | --- | --- | --- |
| C-1 | Override durations ≥ 24 h are not truncated | 1 | `test_override_duration_is_not_truncated` (7 durations), `test_override_duration_regression_against_old_behaviour` |
| C-2 | Event snapshots do not mutate source objects and are idempotent | 1 | `test_event_index_does_not_mutate_source_objects`, `test_event_index_is_idempotent_across_polls` |
| C-19 | Same-timestamp events both survive | 1 | `test_events_sharing_a_timestamp_are_both_kept` |
| C-21 | All valid coordinates normalise without raising | 1 | `test_coordinate_normalisation_never_raises` (10 coords), `test_coordinate_precision_rule_matches_upstream_intent` |
| C-8 | No inbound payload can raise | 1 | `test_adversarial_corpus_never_raises` (17 payloads) + 30 targeted cases |
| C-11 | Identifiers never reach the log | 1 | `test_summary_never_contains_identifier_values` |
| C-11 | Webhook URL never reaches the log | 2 | `test_webhook_url_is_never_logged` |
| C-4 | Unload and reload survive cleanup failure | 2 | `test_unload_survives_a_failing_dropwebhook` (3 error types), `test_reload_succeeds_while_the_backend_is_unreachable` |
| C-6 | Cloudhook failure is retried | 2 | `test_cloudhook_failure_is_inside_the_retry_envelope` |
| C-12 | Listeners and timers do not accumulate | 2 | `test_stop_listener_is_installed_only_once`, `test_pending_retry_is_cancelled_on_unload` |
| C-7 | Auth failures distinguished from transport failures | 2 | `test_auth_failures_are_classified` (6 statuses), `test_revoked_token_starts_reauth_not_a_reload_loop`, `test_reauth_is_started_only_once` |
| C-13 | Watchdog is bounded and escalates | 2 | `test_watchdog_stops_after_max_reloads`, `test_watchdog_counter_survives_reload` |
| C-10 | Update cycle tolerates concurrent subscription change | 2 | `test_update_survives_subscription_change_mid_cycle` |
| C-5 | Subtype triggers constrain identity and subtype | 2 | `test_subtype_filter_keeps_device_and_type_constraints`, `test_thermostat_subtype_trigger_matches_real_payload` |
| C-15 | Partial scopes degrade, no scopes is fatal | 2 | `test_partial_scopes_raise_a_repair_issue_but_still_set_up`, `test_token_with_no_usable_scope_is_fatal` |
| P0-1 | Package imports on the declared floor | CI | job `import-floor`, tier 2 HA matrix |
| C-16 | No assertions in production code | Static | `ruff check --select S101` |
| C-17 | No direct mapping index on device types | Static + review | `ruff`, manual |

## 3. Adversarial payload corpus

Applied to the ingress boundary. Expected result in every case: controlled
rejection, no exception, no partial entity mutation.

| Class | Example |
| --- | --- |
| Wrong root type | `null`, `[]`, `"x"`, `42`, `true` |
| Empty / unknown event | `{}`, `{"event_type": "bogus"}`, `{"event_type": 123}` |
| Missing mapped identifier | `{"event_type": "movement"}` |
| Wrong collection type | `{"persons": {}}`, `{"persons": "x"}` |
| Wrong member type | `{"persons": ["x"]}`, `{"persons": [null]}`, `{"persons": [123]}` |
| Unknown home | known event + random home id |
| Wrong nested type | `{"home": "not-an-object"}`, `{"subevents": "not-a-list"}` |
| Oversized collection | 5 000 members → bounded to 256 |

The corpus originates from the supplied external audit's appendix C.5, which
was the strongest part of that report and is adopted here substantially intact.

## 4. Objectives not yet covered

Recorded so the gap is visible rather than implied. These correspond to the
external audit's NET-031 through NET-033, which were reclassified from
"defects" to test objectives because no code fault was demonstrated.

| Objective | Status |
| --- | --- |
| Event replay and out-of-order delivery determinism | Not covered — 0.2.0 |
| Sustained event-flood behaviour and event-loop latency | Not covered — needs a live harness |
| Listener/timer growth over days of real reconnect cycles | Structural test only |
| Late async completion after unload mutating a new runtime | Partially covered (C-12); no generation token |
| Full HTTP status matrix (400/409/502/503/504, TLS, DNS) | Partially covered (401/403/429/500) |
| Live cooling-mode payloads from real hardware | Not covered — C-14 built on the pyatmo data model |

## 5. Entry and exit criteria

**Entry:** `ruff check` and `ruff format --check` pass; the package imports on
the declared floor.

**Exit for a release:**

1. Tier 1 green on all four Python versions.
2. Tier 2 green on both HA versions in the matrix.
3. `hassfest` and HACS validation green.
4. `docs/VERIFICATION_REPORT.md` updated with the actual results.
5. Every P0 and every C-defect rated High has an executed test.

> **0.1.0 does not meet exit criterion 2.** It was tagged with tier 2 authored
> but unexecuted, for the reason given in `VERIFICATION_REPORT.md` §4.1. This
> is a recorded, accepted deviation — not an omission — and 0.2.0 is blocked on
> clearing it.
