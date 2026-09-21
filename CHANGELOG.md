# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-09-22

First release of the hardened fork under its own version line. Treated as a
clean baseline: the inherited `2026.9.21` version string is retired and the
change record below is written from an independent audit of the whole tree
rather than from the previous `NOTICE.md`, which did not describe its own
change set accurately.

**Baseline:** Home Assistant 2026.9 · Python 3.14.2 · pyatmo 9.9.0

Full traceability of every item to its fix and test: [`docs/DEFECT_REGISTER.md`](docs/DEFECT_REGISTER.md).

### ⚠️ Architecture change — independent domain

The integration now uses its **own domain, `netatmo_hardened`**, instead of
shadowing Home Assistant's built-in `netatmo`.

Overriding the core domain removed the user's fallback. This fork is tightly
coupled to Home Assistant internals, and while it masked the built-in
integration a breaking upgrade took Netatmo out entirely rather than degrading
to the working core version. It also meant upstream's own Netatmo fixes were
silently never received. An independent domain installs alongside core, writes
nothing into Home Assistant's files, and can simply be disabled if it ever
misbehaves.

Consequences, in full:

- **Entity IDs change** unless you follow the cut-over in
  [`docs/MIGRATION.md`](docs/MIGRATION.md), which frees and regenerates them
  identically so automations, dashboards and statistics carry over.
- **Services rename** to `netatmo_hardened.*`.
- **The bus event renames** to `netatmo_hardened_event`, and all internal
  dispatcher signals are namespaced, so the two integrations cannot receive
  each other's entity-creation messages when installed side by side.
- **HomeKit discovery was removed** from the manifest; leaving it would make
  both integrations claim the same discovered devices.
- **Home Assistant Cloud account linking is unavailable** — Cloud's Netatmo
  linking is registered for the `netatmo` domain only. Use your own Netatmo
  developer application. This raises the API budget from 150 to 400 calls/hour
  and roughly triples polling frequency.
- **No brand icon** until we ship our own images (0.2.0); cosmetic only.

### ⚠️ Behaviour changes

- **Outdoor camera sub-events are now processed** (C-18). `SUBEVENT_TYPE_MAP`
  previously mapped every key to `""`, so the nested loop read `data.get("", [])`
  and was always empty. The documented `human`, `animal` and `vehicle` device
  triggers for outdoor cameras therefore **could never fire**. They now do.
  If you have automations that were silently dead, they will start running.
- **Camera events are keyed by event id, not timestamp** (C-19). Media source
  identifiers changed from `events/<camera>/<timestamp>` to
  `events/<camera>/<event_id>`. Bookmarked media URLs will not resolve; browse
  again to get current ones.
- **Cooling-mode thermostats report real values** (C-14). Rooms in cooling mode
  previously reported preset `schedule` with no target temperature. They now
  report the actual cooling setpoint. Automations keying on the old incorrect
  `schedule` preset in cooling homes need review.
- **The stale-data watchdog now gives up** (C-13). After 3 unsuccessful
  automatic reloads it stops and raises a repair issue instead of reloading
  every 15 minutes indefinitely.

### Added

- `custom_components/netatmo/event_validation.py` — single validated ingress
  boundary for all externally supplied webhook data.
- `translations/en.json` — required at runtime by custom integrations; its
  absence meant the config flow rendered raw translation keys (P0-2).
- Repair issues: `partial_oauth_scopes` and `watchdog_reload_exhausted`.
- Two-tier test suite: 85 executed pure-logic tests plus an integration suite
  for CI.
- CI: `hassfest`, HACS validation, ruff, and a tier 1 matrix across Python
  3.11–3.14 plus a tier 2 matrix across HA 2026.9.0 and 2026.9.3.
- Documentation set under `docs/` — audit, defect register, test plan,
  verification report, compatibility and deprecation sweep, security policy.

### Fixed — control integrity

- **Temperature overrides of 24 hours or more were silently shortened** (C-1).
  `timedelta.seconds` discards whole days, so a 26-hour override was sent to
  Netatmo as 2 hours. The service accepted one command and the API received a
  different one, with nothing logged.
- **Camera sub-events were permanently destroyed after one poll** (C-2).
  `process_events` mutated pyatmo's `Event.__dict__` in place and was not
  idempotent; the second pass discarded every sub-event it had converted on
  the first.
- **Public weather sensors reported the wrong geographic area after
  reconfiguration** (C-3). The subscription moved to the new area but the
  station object did not, so readings continued to come from the old area
  under the new area's name.
- **Thermostat mode device triggers both over- and under-fired** (C-5).

### Fixed — availability and recovery

- **A webhook cleanup timeout aborted config entry unload — and therefore every
  reload, including the watchdog's own recovery** (C-4). This disabled the
  fork's headline recovery mechanism for precisely the network conditions that
  trigger it.
- **Cloudhook creation sat outside the retry envelope** (C-6).
- **Authentication failures were indistinguishable from network failures**
  (C-7). A revoked token now starts a reauthentication flow instead of driving
  an endless reload loop. Netatmo's 403-for-throttling is excluded so rate
  limits do not trigger spurious reauth prompts.
- **Shutdown listeners and retry handles accumulated without bound** (C-12).
- **`RuntimeError: deque mutated during iteration`** when an entity was added
  or removed while a publisher update was in flight (C-10).
- Server-supplied `Retry-After` is now honoured on rate-limited registration.

### Fixed — fault containment

- Malformed webhook payloads can no longer raise (C-8, C-9). Non-object JSON
  roots, wrong-typed collections, missing identifiers and unknown home ids are
  all dropped with a controlled log line.
- Assertions removed from production code (C-16); unknown device types no
  longer raise `KeyError` (C-17); media source identifiers fail closed to
  `Unresolvable` (C-20); coordinates near the equator or prime meridian no
  longer crash the options flow (C-21).

### Fixed — security and privacy

- **The full webhook URL is no longer logged** (C-11). Home Assistant documents
  the webhook id as equivalent to a password; it was being written to debug
  logs, and from there into backups and bug reports.
- Webhook payloads are no longer logged in full — person events carry identity
  metadata and signed face/snapshot URLs. A redacted shape summary is logged
  instead.
- Inbound collections are bounded (`MAX_COLLECTION_ITEMS`).

### Fixed — packaging and release integrity

- **`hacs.json` declared a minimum of HA 2024.1.0 while the code required
  HA ≥ 2026.3** (P0-1). Installing on any older version raised `NameError` at
  import. Floor corrected to 2026.9.0, and `from __future__ import annotations`
  added to every module so the failure class cannot recur silently.
- **`quality_scale.yaml` claimed test coverage that did not exist** (P0-3).
  Corrected to `partial` with named evidence.
- **`NOTICE.md` claimed "no other logic was changed"** while `device.py`,
  `entity.py`, `coordinator.py` and `services.py` all carried undocumented
  changes (P0-3). Rewritten.

### Migrated — Home Assistant deprecations

- **D-1 (hard error from 2026.12):** config entry update listener combined with
  a reloading method in the config flow. The config flow no longer reloads; the
  decision moved to `async_config_entry_updated`, which distinguishes a
  credential change from an options change.
- **D-7 (2026.10):** OAuth2 helper now raises config entry exceptions directly;
  manual translation removed.
- Verified already compliant: `via_device_id`, no `default_*` device info
  fields, no `DeviceEntry.config_entries`, no `merge_connections`, no
  `DeviceRegistry.devices` mapping access, new unit enumerators, `probatio`.

### Rejected

Two external audit recommendations were deliberately not implemented. Both are
documented with reasoning in `docs/DEFECT_REGISTER.md` §4:

- **Strict OAuth scope subset validation** — would put every user who owns only
  some Netatmo product families into a permanent reauthentication loop.
- **NET-001 "disabled device IDs disable a home"** — verified against pyatmo
  9.9.0 source as a false positive; the behaviour is deliberate. The genuine
  residual (silence) is fixed with a diagnostic log line.

### Packaging notes

- HACS installs directly from `custom_components/netatmo_hardened/` in the
  repository tree. The optional `zip_release` mechanism is deliberately **not**
  used: it requires a separate pre-built asset on every release, and an asset
  that is missing or nested one level too deep produces a broken install with
  no error at release time. It would also strip the tests and version documents
  out of the release artifact, which must ship with the package.
- One artifact per release: the source archive GitHub attaches automatically.
  `.github/workflows/release.yml` verifies that the tag matches the manifest
  version and that the required deliverables are present.

### Known limitations

- Tier 2 integration tests are authored but **have not yet been executed** —
  see `docs/VERIFICATION_REPORT.md` §4.
- No automated diff against the upstream core integration; drift review is
  manual (`docs/COMPATIBILITY.md` §5).
- pyatmo held at 9.9.0; 9.9.1 exists and is a 0.2.0 task.
- pyatmo 9.9.0's own webhook parser is not yet adopted; planned for 0.2.0.

[0.1.0]: https://github.com/ngen-advisory/netatmo-hardened/releases/tag/v0.1.0
