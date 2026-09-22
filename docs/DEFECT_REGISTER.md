# Defect Register and Traceability Matrix

**Release:** 0.1.2 (cumulative — covers 0.1.0, 0.1.1 and 0.1.2)
**Date:** 2026-09-22
**Baseline:** Home Assistant 2026.9, pyatmo 9.9.0, Python 3.14.2

Every defect accepted for 0.1.0 is listed here with its origin, the fix, and
the test that holds the fix in place. A row with no test reference is a row
where the fix is not yet verifiable, and is marked as such rather than being
quietly omitted.

**ID scheme**

| Prefix | Meaning |
| --- | --- |
| `P0-n` | Packaging / release-integrity defect. Found by the independent audit only. |
| `C-n` | Code defect. |
| `D-n` | Home Assistant deprecation to migrate. See `COMPATIBILITY.md`. |
| `E-n` | Defects found by the **independent external audit of 0.1.0**. Four are regressions introduced by 0.1.0's own remediation. See `AUDIT_0.1.1.md`. |
| `F-n` | Deliberate functional change, not a defect fix. Listed here because it alters shipped behaviour and needs the same traceability. |
| `NET-n` / `SEC-n` | Identifiers from the first supplied external audit, for cross-reference. |

---

## 1. Packaging and release integrity

| ID | Defect | Severity | Fix | Verified by |
| --- | --- | --- | --- | --- |
| P0-1 | `hacs.json` declared a minimum of HA 2024.1.0; the code requires HA ≥ 2026.3 (Python ≥ 3.14 lazy annotations). Installing on any older HA raised `NameError` at import. | Critical | Floor set to `2026.9.0`. `from __future__ import annotations` added to **every** module so the forward-reference class of failure cannot recur silently. | `ruff check` (F821 clean); CI job `import-floor`; CI `tier2` matrix pinned to the declared floor |
| P0-2 | No `translations/` directory. `strings.json` is a Core build-time file and is not read at runtime by custom integrations, so the config flow rendered raw keys. | High | `translations/en.json` generated from `strings.json` and shipped. | CI `hassfest` + `hacs` validation |
| P0-3 | `quality_scale.yaml` asserted `config-flow-test-coverage: done` and `test-before-setup: done` with zero tests present. `NOTICE.md` claimed "no other logic was changed" while `device.py`, `entity.py`, `coordinator.py` and `services.py` all carried undocumented changes. | High | Attestations corrected to `partial` with named evidence. `NOTICE.md` rewritten to describe the actual change set. | Manual review; `docs/VERIFICATION_REPORT.md` |
| P0-4 | `manifest.json` version `2026.9.21`, no repository tag. | Medium | Version set to `0.1.0`; release tagged `v0.1.0`. | `manifest.json` |

---

## 2. Code defects

Ordered by operational consequence, not by discovery order.

| ID | External ref | Location (pre-fix) | Defect | Severity | Fix | Verified by |
| --- | --- | --- | --- | --- | --- | --- |
| C-1 | NET-003 | `climate.py:534` | `timedelta.seconds` discards whole days, silently shortening every override ≥ 24 h. A 26 h command became 2 h. | High | `helper.end_timestamp_for_period()` using `total_seconds()`. | `tests/unit/test_control_integrity.py` (7 durations + regression pin) |
| C-2 | NET-030 (impact missed) | `camera.py:289-303` | `process_events` mutated the pyatmo `Event.__dict__` in place and was not idempotent: the second poll discarded every subevent permanently. | High | `helper.build_event_index()` builds a detached copy. | `test_event_index_is_idempotent_across_polls`, `test_event_index_does_not_mutate_source_objects` |
| C-3 | NET-012 | `sensor.py:945, 979-1017` | Public-weather reconfiguration updated area, signal, publishers and subscription but never `_station`, so the entity reported the **old geographic area** under the new name. | High | `_station` and map coordinates refreshed; `None` station fails closed to unavailable. | `tests/integration/` (public weather); manual review |
| C-4 | NET-007 (understated) | `webhook.py:170-173`, `__init__.py:117-120` | Cleanup caught only `pyatmo.ApiError`. A timeout aborted unload → aborted every reload → disabled the watchdog's own recovery, for exactly the network fault that triggers it. | High | `RECOVERABLE_ERRORS` tuple covering transport failures; cleanup is best-effort. | `test_unload_survives_a_failing_dropwebhook` (3 error types), `test_reload_succeeds_while_the_backend_is_unreachable` |
| C-5 | NET-004 | `device_trigger.py:161-164` | Subtype branch replaced the whole event filter, dropping event-type and device-id constraints (over-trigger) while matching `data.mode`, which the payload does not carry (under-trigger). | High | Subtype added as an additional constraint at the real payload path `data.home.therm_mode`. | `test_subtype_filter_keeps_device_and_type_constraints`, `test_thermostat_subtype_trigger_matches_real_payload` |
| C-6 | NET-005 | `webhook.py:191` vs `:217` | Cloudhook creation sat outside the retry envelope — the exact failure class the fork exists to fix. | Med-High | Entire registration transaction wrapped; `RECOVERABLE_ERRORS` includes `CloudNotAvailable`. | `test_cloudhook_failure_is_inside_the_retry_envelope` |
| C-7 | NET-009 | `coordinator.py:345-350` | All `ApiError` flattened together. No 401 → reauth path; a revoked token looked like an outage and drove an endless reload loop. | Med-High | `_is_auth_failure()` on `ApiError.status`, excluding throttling; `async_start_reauth()` once per handler. | `test_auth_failures_are_classified` (6 cases), `test_revoked_token_starts_reauth_not_a_reload_loop`, `test_reauth_is_started_only_once` |
| C-8 | SEC-002…005 | `webhook.py:84, 101-106, 134` | No ingress validation. Non-object root → `AttributeError`; malformed `persons` → `AttributeError`; missing id fields → `KeyError`. Missing `event_type` defaulted to the string `"None"`, colliding with the coordinator's own activation signal. | Med-High | New `event_validation.py` module; single normalising boundary. | `tests/unit/test_event_validation.py` (17-payload adversarial corpus + targeted cases) |
| C-9 | NET-011 / SEC-006 | `climate.py`, `select.py`, `light.py`, `camera.py`, `coordinator.py` | Entity handlers re-indexed raw webhook dictionaries directly. | Medium | All handlers use `.get()` with type guards; dispatcher callbacks cannot raise. | Same as C-8; `tests/integration/` |
| C-10 | *(missed by external audit)* | `coordinator.py:259-274` | `async_update` iterated the live deque across an `await`; a concurrent entity add/remove raises `RuntimeError: deque mutated during iteration`. | Medium | Iterate a snapshot; skip publishers unsubscribed mid-cycle. | `test_update_survives_subscription_change_mid_cycle` |
| C-11 | SEC-001, SEC-012 | `webhook.py:219, 70, 121` | Full webhook URL (a bearer secret) and complete payloads written to debug log. | Medium | URL never logged; `redacted_summary()` logs shape only. | `test_summary_never_contains_identifier_values`, `test_webhook_url_is_never_logged` |
| C-12 | NET-006 | `webhook.py:241, 243` | Stop listeners and retry cancel handles accumulated without bound via `async_on_unload`. | Medium | One stop listener and at most one pending retry per entry, tracked on the data handler. | `test_stop_listener_is_installed_only_once`, `test_pending_retry_is_cancelled_on_unload` |
| C-13 | *(missed by external audit)* | `coordinator.py:286-314` | Watchdog reload counter lived on the object the reload rebuilds → unbounded reload loop, no escalation, no repair issue. | Medium | Counter in `hass.data`; capped at `MAX_WATCHDOG_RELOADS`; raises a repair issue on exhaustion. | `test_watchdog_stops_after_max_reloads`, `test_watchdog_counter_survives_reload` |
| C-14 | NET-002 (mechanism wrong) | `climate.py:436-441` | Cooling homes reported as preset `schedule` with **no target temperature**. The crash was already fixed upstream of this fork; the substitute was wrong data presented as healthy. | Medium | Reads pyatmo 9.9.0's unified `setpoint_mode` / `setpoint_temperature`; unmapped modes warn once instead of raising. | `tests/integration/` climate matrix |
| C-15 | NET-008 / SEC-010 | `__init__.py:63` | Scope check used set intersection, accepting a partially authorized token silently. | Medium | **Deliberately not** the subset test the external audit recommended — see §4. Repair issue naming the missing scopes; fatal only when no usable scope is granted. | `test_partial_scopes_raise_a_repair_issue_but_still_set_up`, `test_token_with_no_usable_scope_is_fatal` |
| C-16 | NET-015 | `select.py:65,119`, `entity.py:132,169,220`, `device.py:185` | `assert` used to validate live API and registry state. | Medium | Assertions removed; degrade or raise `HomeAssistantError` with an actionable message. | `ruff check --select S101` clean on `custom_components/` |
| C-17 | NET-016 | `entity.py:118,133` | `DEVICE_DESCRIPTION_MAP[...]` indexed directly while `device.py` used `.get()` on the same map. | Medium | `.get()` with a generic fallback everywhere. | Manual review; CI lint |
| C-18 | *(missed by external audit)* | `webhook.py:46-49, 89` | `SUBEVENT_TYPE_MAP` mapped both keys to `""`, so `data.get("", [])` was always empty — outdoor-camera `human`/`animal`/`vehicle` device triggers could **never fire**. | Med-Low | `SUBEVENT_COLLECTION` maps `outdoor` → `subevents`; sub-events inherit parent identifiers. **Behaviour change — see CHANGELOG.** | `tests/unit/test_event_validation.py` (subevent corpus) |
| C-19 | NET-013 | `camera.py:302` | Events keyed on `event_time`; two events in the same second overwrote each other. | Low | Keyed on the event's own id. | `test_events_sharing_a_timestamp_are_both_kept` |
| C-20 | NET-014 / SEC-008 | `media_source.py:64, 179` | `int(event_id)` raised `ValueError`; chained lookup raised `KeyError`; neither mapped to `Unresolvable`. | Low | String ids end to end; every layer validated; all failures become `Unresolvable`. | `tests/integration/` media source |
| C-21 | SEC-009 | `config_flow.py:213` | `str(coord).split(".")[1]` raised `IndexError` for any coordinate below 1e-4 (near equator / prime meridian). | Low | `helper.normalise_coordinate()` — numeric, no string parsing. | `test_coordinate_normalisation_never_raises` (10 coords), `test_coordinate_precision_rule_matches_upstream_intent` |

---

## 2a. External audit of 0.1.0 — E-series (fixed in 0.1.1)

All nine findings were independently verified against the source and the pinned
dependency before acceptance. **R** marks a regression introduced by 0.1.0.

| ID | R | Location (pre-fix) | Defect | Severity | Fix | Verified by |
| --- | :-: | --- | --- | --- | --- | --- |
| E-001 | ⚠️ | `__init__.py` update listener | Access-token comparison scheduled a full reload. HA's OAuth2Session persists **every routine refresh** through `async_update_entry`, which fires update listeners — so with ~3 h Netatmo tokens the integration reloaded ~8×/day for ever. | High | Listener compares `entry.options` and never reloads. No reload is needed for credentials: `OAuth2Session` reads `entry.data["token"]` live. | `test_token_refresh_does_not_reload`, `test_repeated_token_refresh_never_reloads`, `test_options_change_still_refreshes_public_weather`, `test_token_refresh_does_not_churn_public_weather` |
| E-002 | ⚠️ | `webhook.py` registration entry point | `if entry.state is not ConfigEntryState.LOADED: return` at the top, but `async_setup_entry` awaits it while the entry is `SETUP_IN_PROGRESS`. Cloud subscribers got **no webhook at all**; `manage_cloudhook` only fires on a connection-state *change*. | High | Guard removed. Retry callback keeps its `LOADED` check (correct there); unload still cancels pending retries. | `test_webhook_registers_during_setup_with_active_cloud`, `test_registration_is_not_gated_on_loaded_state` |
| E-003 | ⚠️ | `webhook.py` subevent merge | `{**data, **subevent}` let a nested member overwrite `home_id` / `device_id`, which become the HA event's `device_id`. Shape validation cannot catch a substituted id that names another real device. | ~~High~~ **Medium** — see `AUDIT_0.1.1.md` §4 | `validate.merge_subevent()`: identity comes from the parent envelope only; a member's `type` still names the event. | 9 tests in `test_identity_protection.py` |
| E-004 | | `switch/light/cover/fan/button/camera/climate` | pyatmo control methods return `bool` and answer `False` on API rejection without raising. Entities awaited and then published optimistic state, so a rejected command still showed ON/OPEN/CLOSED. | High | `helper.command_failed()` + `NetatmoBaseEntity.async_command()`; raises `HomeAssistantError` on `False`, before any state write. | `test_only_explicit_false_counts_as_command_failure` (6 cases), `test_rejected_command_raises_and_leaves_state_alone`, `test_accepted_command_updates_state`, `test_command_with_no_success_indication_is_not_treated_as_failure` |
| E-005 | | `webhook.py` person loop | `person_id` validated, then dispatched anyway when `None` — neutralising the check. | Medium | `continue` when the id is absent. | Adversarial corpus; tier 2 |
| E-006 | ⚠️ | `helper.normalise_coordinate` | Nudge was unconditionally additive: `90.0 → 90.0000001`, `180.0 → 180.0000001`, persisted after HA had already range-validated the input. | Medium | Nudge applied inward when outward would leave `[-limit, +limit]`; `limit` is now a required argument. | `test_normalised_coordinate_stays_in_range` (14 coords), `test_exact_maximum_is_nudged_inward` |
| E-007 | | `camera.py` | `TimeoutError` absent from the recoverable tuple; pyatmo's image request has a finite timeout, and `asyncio.TimeoutError` is not an `aiohttp.ClientError`. Stream URL refresh unwrapped. | Medium | `CAMERA_TRANSPORT_ERRORS = (TimeoutError, aiohttp.ClientError)`; stream path wrapped, falls back to the cached URL. | `test_camera_snapshot_timeout_is_absorbed` (2 cases), `test_camera_url_refresh_timeout_is_absorbed` |
| E-008 | | `device_trigger.py` discovery | Triggers emitted once per entity; a 5-entity device offered 5 identical choices, differing only by an `entity_id` that attachment ignores. | Low | One trigger per logical device event, carrying a representative entity id so existing automations still validate. | Tier 2 |
| E-009 | | `event_validation.identifier` | Control characters accepted in identifier-shaped values, which reach the debug log and can forge or corrupt records. | Low | C0/C1/DEL rejected. | 8 tier-1 cases + `test_rejected_event_type_cannot_reach_the_log_summary` |

### Note on E-004 and `None`

The external report cites `climate.py:412` among the ignored-result paths. That
line calls `Room.async_therm_set`, which returns `None` — pyatmo offers **no**
success indication for room-level setpoints. `command_failed()` therefore
treats only an explicit `False` as failure. Reading `None` as failure would
make every thermostat setpoint raise, fabricating a guarantee the dependency
does not provide, in the opposite direction from the defect itself.

---

## 2b. Findings from the 0.1.1 live soak and review (fixed in 0.1.2)

E-010 was found by running 0.1.1 in the operator's test environment, exactly as
`COMMISSIONING.md` prescribes; it surfaced within four minutes of the first
start. E-011 was found by inspection while implementing 0.1.2.

| ID | R | Location (pre-fix) | Defect | Severity | Fix | Verified by |
| --- | :-: | --- | --- | --- | --- | --- |
| E-010 | ⚠️ | `webhook.py` retry loop | Every webhook registration failure was retried, without limit. A deployment with no public HTTPS endpoint receives `400 — invalid webhook url (WH006)` on **every** attempt, so 0.1.1 generated a failed API call and a `WARNING` every 15 minutes, for ever, against a rate-limited account — and never told the operator what to change. | Medium | `helper.webhook_failure_is_permanent()` classifies the failure. `400/401/403/404` stop the loop and raise a repair issue naming the API error; throttling is exempt because Netatmo answers 403 when rate limiting. Everything else retries as before. | 15 tier-1 cases in `test_push_event_policy.py`; `test_permanent_rejection_stops_retrying` (4 cases), `test_transient_failure_still_retries`, `test_throttling_still_retries` (tier 2) |
| E-011 | | `translations/en.json` | 24 user-facing strings shipped as unresolved `[%key:…%]` cross-references — every wind direction, the public weather options title, the OAuth step titles and two service field descriptions. HA expands those when it **builds core**, not at load time: `helpers/translation.py` does a plain `load_json` + `recursive_flatten` with no substitution step, so a custom integration's file is served verbatim and the operator sees the literal placeholder text. | Medium | All references expanded to literal English in both `strings.json` and `translations/en.json`, which are now byte-identical. Values resolved from `homeassistant/strings.json` and `homeassistant/components/netatmo/strings.json` at HA `dev`. | `test_shipped_translations_contain_no_unresolved_references`, `test_strings_and_translations_agree`, `test_user_facing_keys_have_text` (4 cases), `test_webhook_issue_text_carries_the_error_placeholder`, `test_push_events_option_step_is_translated` |

### Note on E-011 and P0-1/P0-2

E-011 is the **incomplete half of P0-2**. That row records that 0.1.0 shipped no
`translations/` directory and that one was "generated from `strings.json`". It
was generated by *copying*, not by expanding — which fixed the missing-file
failure mode (raw dotted keys) while leaving a second, quieter one (raw
`[%key:…%]` text) in place. The P0-2 remediation is only now complete.

Marked `⚠️` for E-010 because, like the 0.1.0 E-series regressions, the defect
was introduced by this fork's own remediation: upstream gave up after the first
failure, 0.1.1 corrected that by never giving up, and neither distinguished
"might work later" from "cannot work until a human changes something".

---

## 2c. Functional changes in 0.1.2

| ID | Change | Rationale | Operator impact | Verified by |
| --- | --- | --- | --- | --- |
| F-001 | Push events (the Netatmo webhook) are now an explicit option, `enable_webhook`, **disabled by default**. New options menu: *Push events* / *Public weather areas*. | Netatmo registers a webhook only against a publicly reachable HTTPS endpoint on port 443. The integration cannot detect whether the deployment has one; the operator can. Defaulting to off means an installation that cannot use push never asks for it — which is also the structural fix for E-010's *cause*, where E-010's own fix addresses its symptom. | **Push is off after upgrading**, including for Home Assistant Cloud subscribers who had working push on 0.1.1. Polling is unaffected; all data still arrives. Operators who want push must turn it on once — see `MIGRATION.md` §0.1.2. | `test_push_events_default_to_disabled`, `test_option_key_is_stable` (tier 1); `test_default_entry_registers_no_webhook`, `test_entry_still_loads_and_polls_without_push`, `test_opted_in_entry_registers_a_webhook`, `test_disabling_push_reloads_and_drops_the_webhook`, `test_unrelated_option_change_does_not_reload` (tier 2) |

### Note on F-001 and E-001

F-001 introduces the **only** reload the update listener is permitted to
schedule. Whether the webhook subsystem is wired up is decided in
`async_setup_entry`, so that one option cannot be applied in place. This is not
a re-opening of E-001: E-001 was a reload on every routine *token* write, which
fires roughly eight times a day for ever. This fires when a human changes one
setting. `test_unrelated_option_change_does_not_reload` holds that boundary
from the other side.

A second consequence had to be handled deliberately. `light.py` marked camera
floodlights **unavailable** whenever no webhook was established, because their
state arrives by push. With push opt-in, that rule would have stranded the
entity permanently — unavailable, so not even controllable — on every
installation using the new default. `NetatmoDataHandler.webhook_expected`
(has the operator asked for push?) is therefore distinct from `webhook` (is one
established?), and the availability gate applies only when push is expected and
missing. When push is deliberately off, the entity stays available: commands
work and state is refreshed by polling.

---

## 3. Deprecations migrated

Full analysis in `COMPATIBILITY.md`.

| ID | Deprecation | Deadline | Status |
| --- | --- | --- | --- |
| D-1 | Config entry update listener combined with a reloading method in the config flow | **Error from 2026.12** | Fixed — reload decision moved to `async_config_entry_updated` |
| D-2 | `DeviceInfo["via_device"]`, `async_get_or_create(via_device=...)` | 2027.8 | Already compliant (`via_device_id`) |
| D-3 | `default_manufacturer` / `default_model` / `default_name` | 2027.9 | Not used |
| D-4 | `DeviceEntry.config_entries` | 2027.10 (custom) | Not used |
| D-5 | `merge_connections` / `merge_identifiers` | 2027.9 | Not used |
| D-6 | `DeviceRegistry.devices` mapping access, `deleted_devices` | 2027.9 | Not used |
| D-7 | OAuth2 helper raises config entry exceptions directly | 2026.10 | Adopted — no manual translation |
| D-8 | voluptuous → probatio | 2026.9 | Already on `probatio` |
| D-9 | New unit enumerators (`UnitOfRatio`, …) | 2026.6 | Already compliant |

---

## 4. Rejected recommendations

Two recommendations from the external audit were **deliberately not implemented**.
Both are recorded here because in an ICS context a rejected finding must be as
traceable as an accepted one.

### 4.1 NET-008 / SEC-010 — strict OAuth scope subset validation

**Recommended:** replace the intersection test with `required_scopes <= actual_scopes`.

**Rejected.** Netatmo issues scopes per product family — weather station,
thermostat, camera, shutter — and this integration supports all of them
independently. A user who owns only a weather station legitimately holds none
of the camera scopes. A strict subset test would put every such account into a
permanent reauthentication loop, converting a diagnostics gap into a total
outage. The integration's own `API_SCOPES_EXCLUDED_FROM_CLOUD` list exists for
this reason.

**Implemented instead (C-15):** fail closed only when *no* usable scope is
granted; otherwise raise a repair issue naming exactly which scopes are
missing. This delivers the visibility the finding was actually asking for
without the outage.

### 4.2 NET-001 — disabled device IDs passed as `disabled_homes_ids`

**Recommended:** stop passing device IDs to pyatmo's home denylist; treated as
the report's top Critical finding.

**Rejected as stated.** Verified against the pyatmo 9.9.0 source
(`account.py`): `disabled_homes_ids` is compared only against `home_id`,
`all_home_names` remains populated for denylisted homes, and Netatmo home IDs
(24-char hex) and module IDs (MAC addresses) are disjoint namespaces. The
behaviour is also deliberate in this fork — `device.py::async_sync_home_disabled_state`
mirrors a home's disabled state onto its descendants and
`coordinator._handle_home_device_update` reloads on toggle.

**Residual finding accepted and fixed:** upstream issue
[home-assistant/core#181448](https://github.com/home-assistant/core/issues/181448)
complains that the behaviour is *silent*, not that it is wrong. A log line
naming how many devices are excluded was added to
`async_disabled_netatmo_ids()`. Severity: Low, not Critical.

---

## 5. External findings not carried forward

| External ID | Disposition | Reason |
| --- | --- | --- |
| NET-028 | Not a defect | No divergence exists. `NetatmoCamera` inherits `NetatmoBaseEntity.available`, which ANDs publisher health. No line reference was given. |
| NET-029 | Already handled | `webhook.py` already checked `entry.state is not ConfigEntryState.LOADED` and registered the cancel via `async_on_unload`. The real residual was accumulation (C-12), not a race. |
| NET-031, NET-032, NET-033 | Not defects | Generic test-plan prose with no code citation or demonstrated fault; Appendix D concedes this. Folded into `TEST_PLAN.md` as test objectives. |
| SEC-007 | Not a defect | Correctly self-labelled a hardening gap. aiohttp/HA already bound request bodies. Collection caps added anyway as defence in depth (`MAX_COLLECTION_ITEMS`). |
| SEC-011 | Dismissed | `async_ensure_token_valid()` raises before a malformed token reaches `token["access_token"]`. |
