# Test Plan

**Release:** 0.1.2
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
| **E-001** | Token refresh never reloads; options change still refreshes | 2 | `test_token_refresh_does_not_reload`, `test_repeated_token_refresh_never_reloads`, `test_options_change_still_refreshes_public_weather`, `test_token_refresh_does_not_churn_public_weather` |
| **E-002** | Webhook registers during setup for a cloud subscriber | 2 | `test_webhook_registers_during_setup_with_active_cloud`, `test_registration_is_not_gated_on_loaded_state` |
| **E-003** | Nested members cannot alter parent identity | 1 | 9 tests in `test_identity_protection.py` |
| **E-004** | `False` never becomes success; `None` never becomes failure | 1 + 2 | `test_only_explicit_false_counts_as_command_failure` (6 cases) + 4 tier-2 tests |
| **E-006** | Normalised coordinates stay in range at every boundary | 1 | `test_normalised_coordinate_stays_in_range` (14 coords), `test_exact_maximum_is_nudged_inward` |
| **E-007** | Camera timeouts are absorbed on both media paths | 2 | `test_camera_snapshot_timeout_is_absorbed`, `test_camera_url_refresh_timeout_is_absorbed` |
| **E-009** | Control characters rejected before reaching logs | 1 | 8 cases + `test_rejected_event_type_cannot_reach_the_log_summary` |
| C-17 | No direct mapping index on device types | Static + review | `ruff`, manual |
| **E-010** | A rejection that cannot succeed stops the retry loop and raises a repair issue; a transient one keeps retrying | 1 + 2 | `test_deterministic_rejections_are_permanent` (4 statuses), `test_transient_statuses_keep_retrying` (8 statuses), `test_missing_status_keeps_retrying`, `test_throttling_overrides_a_permanent_looking_status`, `test_permanent_status_set_is_explicit` + `test_permanent_rejection_stops_retrying` (4 statuses), `test_transient_failure_still_retries`, `test_throttling_still_retries`, `test_successful_registration_clears_the_issue` |
| **E-011** | The shipped translation file is literal text, and matches the reviewed source | 1 | `test_shipped_translations_contain_no_unresolved_references`, `test_strings_and_translations_agree`, `test_user_facing_keys_have_text` (4 keys), `test_webhook_issue_text_carries_the_error_placeholder`, `test_push_events_option_step_is_translated` |
| **F-001** | Push events default to off, load cleanly without a webhook, and register when enabled | 1 + 2 | `test_push_events_default_to_disabled`, `test_option_key_is_stable`, `test_repair_issue_key_is_stable` + `test_default_entry_registers_no_webhook`, `test_entry_still_loads_and_polls_without_push`, `test_opted_in_entry_registers_a_webhook`, `test_disabling_push_reloads_and_drops_the_webhook`, `test_disabling_push_clears_a_previous_rejection_issue` |
| **F-001 / E-001** | The new reload path is reached by the push-event option only | 2 | `test_unrelated_option_change_does_not_reload` |

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
| Full HTTP status matrix (400/409/502/503/504, TLS, DNS) | Partially covered (401/403/429/500); webhook-registration classification now covers 400/401/403/404/408/409/425/429/500/502/503/504 at tier 1 |
| Push-event path with a real public HTTPS endpoint | Not covered — the soak environment has none, so F-001's enabled branch is CI-only |
| Non-English translation files | Not covered — only `en.json` ships. A future locale must expand its own references (E-011) |
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

> **Neither 0.1.0 nor 0.1.1 meets exit criterion 2**, for the same reason. In
> 0.1.0 that was an accepted procedural deviation; after the external audit it
> is understood as causal — E-001 and E-002 are precisely what tier 2
> exercises. See `AUDIT_0.1.1.md` §6.
>
> **0.1.0 did not meet exit criterion 2.** It was tagged with tier 2 authored
> but unexecuted, for the reason given in `VERIFICATION_REPORT.md` §4.1. This
> is a recorded, accepted deviation — not an omission — and 0.2.0 is blocked on
> clearing it.
