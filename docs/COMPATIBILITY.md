# Platform Compatibility and Deprecation Sweep

**Release:** 0.1.0
**Baseline:** Home Assistant **2026.9**, Python **3.14.2**, pyatmo **9.9.0**

This document exists because the version this release replaced declared a
supported floor two years below what its own source required (defect P0-1).
The purpose here is not only to record the current baseline but to survey every
deprecation announced during 2026, so the next Home Assistant release does not
force an unplanned rebuild.

---

## 1. Supported platform

| Component | Supported | Notes |
| --- | --- | --- |
| Home Assistant | **≥ 2026.9.0** | Declared in `hacs.json`; CI tests 2026.9.0 and 2026.9.3 |
| Python | **≥ 3.14.2** | Required by HA 2026.3+; the integration relies on PEP 649 lazy annotations |
| pyatmo | **== 9.9.0** | Pinned. See §4 |

### Why the floor is 2026.9 and not lower

Three independent constraints, each of which alone rules out older releases:

1. **Python 3.14 lazy annotations.** HA 2026.2 required Python ≥ 3.13.2;
   HA 2026.3 moved to ≥ 3.14.2. Under Python ≤ 3.13, annotations are evaluated
   eagerly, so forward references such as `NetatmoDataHandler` in
   `coordinator.py`'s dataclasses raise `NameError` at import.
   *Mitigated in 0.1.0:* `from __future__ import annotations` is now present in
   every module, so this specific failure cannot recur — but the floor is kept
   at 2026.9 because of the two constraints below.
2. **`probatio`.** HA 2026.9 replaced voluptuous with probatio and installs it
   as a process-wide shim. Earlier releases do not ship it.
3. **Device registry API.** `include_child_devices=`, `AnyDeviceEntry` and the
   `via_device_id` form of `DeviceInfo` all postdate 2026.7.

> **Policy.** Do not widen the floor without running the full tier 2 suite
> against the target version. The floor is a tested claim, not an aspiration.

---

## 2. Deprecation sweep — Home Assistant 2026.x

Every developer-blog deprecation announced during 2026 was checked against this
codebase. Entries marked *n/a* were confirmed by inspection, not assumed.

| Announced | Deprecation | Removal | Applies here | Status |
| --- | --- | --- | --- | --- |
| 2026-09-17 | Config entry exceptions in `async_migrate_entry` | — | n/a — no migration method | — |
| 2026-09-15 | `DeviceEntry.config_entries` enforced | 2027.10 (custom) | Not used | ✅ Clean |
| 2026-09-07 | OAuth2 helper raises config entry exceptions itself | 2026.10 | **Yes** | ✅ **D-7 adopted** |
| 2026-09-02 | `modbus.get_hub` | — | n/a | — |
| 2026-08-31 | `configurator` integration | — | n/a | — |
| 2026-08-24 | `via_device`, `default_manufacturer/model/name`, `merge_connections`, `merge_identifiers`, `DeviceRegistry.devices` mapping access, `deleted_devices`, `created_at`/`modified_at` | 2027.8 – 2027.9 | **Partly** | ✅ **D-2…D-6 already clean** |
| 2026-08-19 | Device registry WebSocket API | — | n/a — frontend surface | — |
| 2026-07-22 | Standard button event types | — | n/a | — |
| 2026-07-21 | One config entry + one subentry per device | — | Yes | ✅ `single_config_entry: true` |
| 2026-07-03 | Media source search | — | Optional | ⏳ Roadmap 0.2.0 |
| 2026-06-30 | `home_assistant_start` flag of `async_initialize_triggers` | — | Not called directly | ✅ Clean |
| 2026-06-30 | New unit enumerators | — | **Yes** | ✅ **D-9** already on `UnitOfRatio` etc. |
| 2026-06-15 | Device tracker entity model | — | n/a | — |
| 2026-05-07 | **Update listener + reloading method in config flow** | **Error from 2026.12** | **Yes** | ✅ **D-1 fixed** |
| 2026-04-20 | Legacy device tracker platform API | — | n/a | — |
| 2026-04-07 | Entity IDs with mismatched domains | — | Yes | ✅ Clean — all entity ids are platform-generated |
| 2026-02-24 | Custom integrations may ship brand images | — | Optional | ⏳ Roadmap |
| 2026-02-23 | Deprecated light features removed | — | n/a — camera floodlight only | ✅ Clean |
| 2026-02-19 | OAuth 2.0 helper error handling | — | **Yes** | ✅ Superseded by D-7 |
| 2026-02-18 | Webhook helper reconfiguration support | — | Optional | ⏳ Roadmap 0.2.0 |
| 2026-02-16 | Labs `async_listen` | — | n/a | — |

### D-1 in detail — the one that would have broken in December

**Announced 2026-05-07. Becomes a hard error in 2026.12.**

> "Using a config entry listener together with any reloading methods in a
> config flow is deprecated and will result in an error from 2026.12."

The inherited code had both halves:

* `__init__.py` registered `entry.add_update_listener(async_config_entry_updated)`,
  needed so that changing a public weather area reconfigures the sensors in
  place rather than reloading the whole integration; and
* `config_flow.py::async_oauth_create_entry` called
  `hass.config_entries.async_reload()` directly after a reauth.

**Resolution.** The config flow no longer reloads. The reload decision moved
into `async_config_entry_updated`, which compares the entry's access token
against the one the running handler was built with:

* token changed → credentials were replaced → `async_schedule_reload()`
* otherwise → options-only change → dispatch the public-weather signal

This is both compliant and more correct than the original: an options change
never costs a full reload, and a reauth always gets one.

### D-7 in detail — OAuth2 error handling

From HA 2026.10 the following inherit from config entry exceptions and must
**not** be manually translated:

| OAuth2 exception | Now raises as |
| --- | --- |
| `ImplementationUnavailableError` | `ConfigEntryNotReady` |
| `UnknownImplementationError` | `ConfigEntryAuthFailed` |
| `OAuth2TokenRequestError` | `ConfigEntryNotReady` |
| `OAuth2TokenRequestTransientError` | `ConfigEntryNotReady` |
| `OAuth2TokenRequestConnectionError` | `ConfigEntryNotReady` |
| `OAuth2TokenRequestReauthError` | `ConfigEntryAuthFailed` |

`async_setup_entry` therefore calls `session.async_ensure_token_valid()` bare,
and `coordinator.async_fetch_data` catches `ConfigEntryAuthFailed` explicitly
so it starts a reauth flow rather than being swallowed by the publisher backoff
(defect C-7).

---

## 3. Deliberately not adopted in 0.1.0

### pyatmo's own webhook parser

pyatmo 9.9.0 ships a complete, typed webhook parser — `pyatmo.webhook` with
`WebhookEvent`, `WebhookResult`, `WebhookKind` and
`AsyncAccount.process_webhook()`. It classifies payloads, resolves home ids,
handles cooling setpoints and merges device events into the account model.

This is almost certainly the right long-term architecture, and it would retire
most of `event_validation.py` along with defects C-8, C-9 and C-14 structurally
rather than defensively.

**It is not adopted in 0.1.0 by deliberate decision.** Migrating would change
every dispatcher signal and every entity event handler in the integration — a
behavioural rewrite in the same release as a defect-fix baseline, which is
exactly the change-control mistake this release exists to correct. It is
recorded as the primary 0.2.0 objective so that it remains a decision rather
than an oversight.

---

## 4. pyatmo pin

`manifest.json` pins `pyatmo==9.9.0`. Version 9.9.1 exists and contains further
topology fixes.

**Decision: hold at 9.9.0 for this release.** The integration's disabled-home
behaviour is defined by `AsyncAccount.process_topology()` in this exact
version, and the 0.1.0 verification was performed against it. Moving to 9.9.1
is a 0.2.0 task gated on re-running the tier 2 topology matrix.

Do not bump this pin without:

1. re-running `tests/integration/` in full;
2. re-checking `disabled_homes_ids` semantics in `account.py`;
3. re-checking `Room.setpoint_mode` / `setpoint_temperature` (defect C-14
   depends on them);
4. recording the outcome in `CHANGELOG.md`.

---

## 5. Domain independence

This integration uses its own domain, `netatmo_hardened`. It does **not**
override Home Assistant's built-in `netatmo`.

The earlier design did override it, to be drop-in. That was reconsidered and
rejected for 0.1.0: masking the core integration removes the operator's
fallback. This fork depends on a substantial amount of Home Assistant internal
surface — `probatio`, `EntityStateAttribute`, `include_child_devices`,
`AnyDeviceEntry`, the device registry helpers in §2 — and any of those can move
in a future release. While the core domain was masked, such a break removed
Netatmo support entirely. With an independent domain the failure mode is
"disable the custom integration, core takes over", which is a recoverable state
an operator can reach without a maintainer.

**Consequences accepted:**

| Consequence | Handling |
| --- | --- |
| Entity IDs are not inherited | Cut-over procedure in `docs/MIGRATION.md` regenerates them identically |
| HA Cloud account linking unavailable | Own developer credentials; raises budget 150 → 400 calls/hour |
| Upstream core fixes not inherited | Manual review against upstream each release (§6) |
| Two integrations can poll one account | Documented: disable rather than delete the core entry |

**Collision control.** Because both integrations can now be installed
simultaneously, every cross-component identifier is namespaced: the domain, all
`NETATMO_CREATE_*` dispatcher signals, the `netatmo_hardened_event` bus event,
and the webhook signals (which derive from `DOMAIN`). HomeKit discovery was
removed from the manifest so the two do not claim the same discovered devices.
An un-namespaced dispatcher signal would be received by both integrations and
cause each to create the other's entities.

## 6. Upstream drift

Independent domain or not, this remains a fork of a moving target. Upstream
continues to fix its own Netatmo integration, and those fixes do not arrive
here automatically.

**Controls in place:**

* CI runs `hassfest` and HACS validation on every push and weekly on a
  schedule, so a platform-level structural change surfaces as a build failure.
* The HA floor is an explicitly tested claim (§1), not an assumption.
* This document is reviewed against the developer blog each release.

**Control not yet in place:** an automated diff against the upstream
`homeassistant/components/netatmo` tree at the pinned version, to surface
upstream fixes worth porting. Recommended for 0.2.0; until then the review is
manual and this is a known limitation.
