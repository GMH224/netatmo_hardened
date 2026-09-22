# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.4] - 2026-09-22

Cuts API traffic on the reference account from **210 calls an hour to 46**.
No data is lost — the integration was polling far faster than the hardware
produces measurements.

Full analysis: [`docs/AUDIT_0.1.4.md`](docs/AUDIT_0.1.4.md).
Release record: [`docs/RELEASE_0.1.4.md`](docs/RELEASE_0.1.4.md).

### Fixed

- **Polling was derived from the rate limit, not from how often the data
  changes** (F-004). Every Netatmo weather station module publishes to the
  cloud once every five minutes, indoor and outdoor alike. The integration
  polled it every 85 seconds — about 18 API calls per actual measurement once
  the homes were counted.

  The cause: upstream divides each interval by seven because an application
  with its own credentials may make 400 calls an hour instead of 150. Nothing
  in that calculation asks how often the data changes. Every one of those
  constants is inherited verbatim from `home-assistant/core`, verified by
  diff, and three prior audits of this fork went past them.

  There is now a floor per publisher, drawn from the source's own behaviour:

  | Publisher | Was | Now |
  | --- | ---: | ---: |
  | Weather / air quality | 85 s | 240 s |
  | Home status | 42 s | 120 s |
  | Camera events | 85 s | 300 s |
  | Public weather | 85 s | 600 s |
  | Topology | ~26 min | 60 min |

  The floor is a minimum, not a replacement — Home Assistant Cloud's smaller
  budget can still produce a *longer* interval, and does. Weather sits at
  240 s rather than 300 s deliberately: polling at exactly the publish period
  drifts in and out of phase and periodically skips a measurement, while
  staying strictly below it cannot.

  **What you will notice:** sensor values update every 4 minutes instead of
  every 2. No measurement is lost — the station only makes one every 5
  minutes. A change made in the *Netatmo app* on an installation without push
  events now takes up to 2 minutes to appear instead of 1.

- **Homes that can never produce an entity were polled for ever** (F-003). A
  Netatmo account routinely carries homes the app created for an address that
  was never equipped — rooms, but no hardware. Two of the reference account's
  three homes are like this, and each was polled every 60 seconds
  indefinitely: two thirds of all home-status traffic fetched nothing.

  The same now applies when **you have disabled everything inside a home**.
  Disabling a device stops its entities updating but did not stop the API
  call, because the request fetches the whole home. Disabling a home already
  worked; this extends it to the same intent expressed a different way.

  **What this cannot do:** there is no per-room API call. Disabling one room
  of three saves no traffic, and a home with any enabled content is still
  polled in full. Said here because it would otherwise look like a bug.

### Known and deferred

- The rate-limit brake **freezes** polling rather than slowing it: once the
  hourly budget is exceeded it pushes every publisher's next scan back by 60 s
  on each 60 s tick, so the schedule advances as fast as wall time until the
  counter resets up to an hour later. This release puts the reference account
  far out of its reach (46 of 400) but does not fix it. Changing a backoff
  rule without live evidence is what produced E-010.
- `HOME` and `EVENT` share a signal name, so the event publisher is never
  created and camera events are never polled. Upstream behaviour; fixing it
  would *add* traffic in the release that exists to reduce it, and it needs
  camera hardware to verify.

### Testing

- 25 new tier-1 tests and 10 new tier-2 tests (244 tier-1 total, up from 219).
- Tier 2 remains **unexecuted** here. Both fixes are pure functions and fully
  covered by tier 1; what is unrun is every test that checks the coordinator
  actually *calls* them.

## [0.1.3] - 2026-09-22

Adds the API telemetry the operator asked for. Additive only — no existing
entity, option or control path changes.

Full analysis: [`docs/AUDIT_0.1.3.md`](docs/AUDIT_0.1.3.md).
Release record: [`docs/RELEASE_0.1.3.md`](docs/RELEASE_0.1.3.md).

### Added

- **Six API telemetry sensors** (F-002), on a new **Netatmo API** service
  device, all diagnostic and enabled by default:

  | Sensor | Notes |
  | --- | --- |
  | API failure ratio (1h) | Percentage of API calls in the last hour that failed |
  | API last error | The message, redacted and truncated |
  | API last error type | `auth`, `rate_limit`, `throttling`, `timeout`, `transport`, `server`, `client`, `no_device`, `unknown` |
  | API last error time | When it happened |
  | API last success | When the API last worked |
  | API poll latency | How long the last call took |

  The integration already knew *whether* it was healthy — every entity's
  availability depends on it. It could not say *how* healthy, or *when it last
  was not*. A gap in a history graph looked identical whether it came from a
  Netatmo outage, a local network fault or a rate limit.

  Three behaviours are deliberate and worth knowing:

  - **They stay available when the API is down.** Every other entity goes
    unavailable exactly then. A diagnostic sensor that did the same would
    report nothing at the only moment anyone reads it.
  - **Failure ratio is *unknown*, not 0%, before the first poll.** An
    integration that has made no calls has not achieved a 0% failure rate, and
    "healthy" is the more dangerous of the two possible wrong answers.
    `sample_count` is published as an attribute, because 100% over two samples
    and over two hundred are different claims.
  - **The last error is not cleared by the next success.** You asked for the
    last error, not the current one — it is read the morning after, not
    during.

  Error messages are redacted before they become states: an entity state is
  readable by every dashboard, template and history export, and a
  webhook-related API error quotes a URL carrying a bearer credential.

### Fixed

- Nothing in shipped code. Two defects were found **in this release's own new
  code before it shipped** — a non-terminating loop in the redaction routine,
  and a recorder that was not total despite its docstring saying so. Both are
  written up in [`docs/AUDIT_0.1.3.md`](docs/AUDIT_0.1.3.md) §4 rather than
  quietly corrected, because how they were caught is the useful part.

### Changed — build process

- **The release now compiles every `.py` file explicitly.** `ruff 0.15.11` in
  this build environment rewrites `except (A, B):` into `except A, B:` —
  invalid Python — and then reports the corrupted file as clean. No shipped
  release is affected (every existing `except` uses the `as err` form, which
  the tool leaves alone), but `ruff check` has been the static-analysis gate
  since 0.1.0 and has now been shown to pass a file that cannot be imported.
  A linter is not a syntax gate. See [`docs/AUDIT_0.1.3.md`](docs/AUDIT_0.1.3.md) §5.

### Testing

- 60 new tier-1 tests and 13 new tier-2 tests (219 tier-1 total, up from 159).
- Tier 2 remains **unexecuted** in the authoring environment for the reason
  recorded since 0.1.0. The feature's central requirement — availability
  during an outage — is a tier-2 property, so it is written and unrun.

## [0.1.2] - 2026-09-22

Found by running 0.1.1 in a real Home Assistant, exactly as
[`docs/COMMISSIONING.md`](docs/COMMISSIONING.md) prescribes. The first defect
surfaced four minutes after start.

Full analysis: [`docs/AUDIT_0.1.2.md`](docs/AUDIT_0.1.2.md).
Release record: [`docs/RELEASE_0.1.2.md`](docs/RELEASE_0.1.2.md).

### Added

- **Push events can now be turned off, and are off by default** (F-001).
  Netatmo registers a webhook only against a publicly reachable HTTPS endpoint
  on port 443. An installation without one — the normal case for a Home
  Assistant that is not published to the internet — cannot use push events at
  all, and 0.1.1 would keep asking anyway.

  There is now an options menu with a **Push events** page. The integration
  cannot detect whether your deployment is reachable from the internet, so it
  no longer assumes that it is.

  **If you use Home Assistant Cloud, or publish HA over HTTPS on port 443, push
  events stop after this update until you enable them** in Settings → Devices &
  services → Netatmo (hardened) → Configure → Push events. Everything still
  works meanwhile; data arrives on the polling interval rather than instantly.

  Camera floodlights stay *available* when push is off — controllable, with
  state refreshed by polling. Under 0.1.1 a missing webhook made them
  unavailable, which would have been permanent under the new default.

### Fixed

- **A webhook rejection that can never succeed is no longer retried for ever**
  (E-010, a regression introduced by 0.1.1). Netatmo answers `400 — invalid
  webhook url (WH006)` when the callback URL is not publicly reachable over
  HTTPS. That answer does not change until a human changes the network, but
  0.1.1 retried it every fifteen minutes indefinitely: useless API calls
  against a rate-limited account, a warning in the log for ever, and no
  statement of what to fix.

  Upstream had the opposite defect — it gave up after the *first* failure, so a
  single rate-limit response left push events dead until someone pressed
  Reload. Neither version asked the actual question, which is whether another
  attempt could ever succeed. It is now asked explicitly: `400/401/403/404`
  stop the loop and raise a repair issue quoting Netatmo's own error, while
  throttling (which Netatmo reports as `403`) and everything else keeps
  retrying as before.

- **24 user-facing strings displayed as raw placeholder text** (E-011). Wind
  directions, the public weather options title, the authentication step titles
  and two service field descriptions shipped as unresolved
  `[%key:component::netatmo::…%]` references. Home Assistant expands those when
  it *builds core*, not when it loads translations — so a core integration
  ships an expanded file while a custom integration ships whatever is in it.
  All references are now literal English, and `strings.json` and
  `translations/en.json` are byte-identical, with a test that keeps them so.

### Testing

- 35 new tier-1 tests and 9 new tier-2 tests (159 tier-1 total, up from 124).
  Nine of the new tier-1 tests check the *shipped package* rather than the
  source — the version documents for this release exist, the changelog records
  it, the runtime translation file is present, the domain has not drifted back
  to `netatmo`, and the dependency pin is exact. Both P0-x and E-011 were
  defects in what shipped, not in what was written.
- The tier-2 fixture now opts *in* to push events, because with the new default
  every webhook test in the suite would otherwise have passed vacuously. A
  separate fixture covers the shipped default and the 0.1.1 → 0.1.2 upgrade
  path.
- Tier 2 remains **unexecuted** in the authoring environment for the reason
  recorded since 0.1.0 (Python 3.14.2 unavailable). See
  [`docs/VERIFICATION_REPORT.md`](docs/VERIFICATION_REPORT.md).

## [0.1.1] - 2026-09-22

**0.1.0 is superseded and should not be deployed.** An independent external
audit returned a FAIL disposition on it. Nine defects, all verified — and
**four were introduced by 0.1.0's own remediation**. Two of those are live
availability defects affecting every installation.

Full analysis: [`docs/AUDIT_0.1.1.md`](docs/AUDIT_0.1.1.md).
Release record: [`docs/RELEASE_0.1.1.md`](docs/RELEASE_0.1.1.md).

### Fixed — regressions introduced by 0.1.0

- **The integration reloaded itself roughly every three hours** (E-001).
  0.1.0's fix for the 2026.12 config-entry deprecation compared access tokens
  in the update listener and reloaded when they changed. But Home Assistant's
  `OAuth2Session` persists **every routine token refresh** through
  `async_update_entry`, which fires that listener — and Netatmo tokens last
  about three hours. The result was a permanent teardown/rebuild cycle roughly
  eight times a day: entity churn, transient unavailability, webhook
  re-registration, and reloads racing in-flight commands. A deprecation warning
  had been traded for a worse availability defect. The listener now compares
  options and never reloads; no reload is needed for credentials, because
  `OAuth2Session` reads the token live.
- **Home Assistant Cloud subscribers got no webhook at all** (E-002). 0.1.0
  added a `ConfigEntryState.LOADED` guard at the top of webhook registration as
  a safety measure. But `async_setup_entry` awaits that function directly,
  while the entry is still `SETUP_IN_PROGRESS` — so it returned immediately,
  having done nothing. Because the cloud handler only fires on a *change* of
  connection state, a subscriber whose cloud stayed connected never got a
  webhook for the lifetime of that runtime. Polling continued, so nothing
  looked wrong. This is the same silent failure as upstream issue #178195 —
  the issue cited as a reason this fork exists. Guard removed; the retry
  callback keeps its own check, which is correct there.
- **Nested webhook members could override parent identity** (E-003).
  `{**data, **subevent}` let a sub-event overwrite `home_id` and `device_id`,
  and the result becomes the Home Assistant event's `device_id` — so a nested
  member could aim an event at a device it does not belong to. Shape validation
  cannot catch this: a substituted id naming another real device passes every
  check. Identity now comes from the parent envelope only. *Re-rated from the
  audit's HIGH to MEDIUM — reaching this code already requires the webhook id,
  and a holder can forge a well-formed event directly; reasoning in the audit
  §4.*
- **Exact maximum coordinates were pushed out of range** (E-006).
  `normalise_coordinate(90.0)` returned `90.0000001`, persisted after Home
  Assistant had already range-validated the input. The nudge is now applied
  inward at boundaries, and the legal limit is a required argument.

### Fixed — pre-existing defects

- **A rejected command still showed as success** (E-004). pyatmo's control
  methods return `bool` and answer `False` when Netatmo rejects a request,
  without raising. Every entity awaited the call and then published the new
  state, so a failed command still read as ON, OPEN or CLOSED — and any
  automation keyed on that transition ran on a state the device never entered.
  Commands now raise `HomeAssistantError` on rejection, before any state write.
  Room thermostat setpoints return `None` and are deliberately **not** treated
  as failures: inventing a guarantee the dependency does not offer would be the
  same error in the opposite direction.
- **Malformed person events were dispatched anyway** (E-005). The person id was
  validated and then used regardless, neutralising the check.
- **Camera timeouts escaped the recoverable-error envelope** (E-007).
  `TimeoutError` is not an `aiohttp.ClientError`, so a stalled camera raised
  into Home Assistant instead of returning no image. The stream URL refresh is
  now wrapped too, falling back to the cached URL.
- **Device triggers were duplicated per entity** (E-008). A five-entity device
  offered five identical automation choices, differing only by an entity id the
  runtime filter ignores.
- **Control characters were accepted in event types** (E-009), and reached the
  debug log where they could forge or corrupt records.

### Testing

- 124 tier-1 tests (up from 85), all executed and green.
- **The test that let E-006 through is corrected.** It already included `90.0`
  but asserted only `isinstance(result, float)` — that the function did not
  crash, not that it produced a legal coordinate. It now asserts range across
  14 inputs. A test that checks the weaker of two available properties is worse
  than no test.
- New tier-2 suite `test_lifecycle_regressions.py` covering E-001, E-002,
  E-004 and E-007 — the cases that would have caught 0.1.0's two worst defects.

### Known limitations

- **The tier-2 suite still has not been executed**, for the same environmental
  reason as 0.1.0 (Python 3.14.2 unavailable). This is no longer a procedural
  note: it is the direct cause of E-001 and E-002 reaching release. Do not
  deploy to an unattended installation until CI has run it green. See
  `docs/VERIFICATION_REPORT.md` §4.1.
- Unchanged from 0.1.0: no live-hardware verification, no automated upstream
  diff, pyatmo held at 9.9.0, pyatmo's own webhook parser not yet adopted.

### Upgrade

Drop-in from 0.1.0 via HACS. No configuration change, no entity change, no
re-authentication.

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

[0.1.4]: https://github.com/ngen-advisory/netatmo-hardened/releases/tag/v0.1.4
[0.1.3]: https://github.com/ngen-advisory/netatmo-hardened/releases/tag/v0.1.3
[0.1.2]: https://github.com/ngen-advisory/netatmo-hardened/releases/tag/v0.1.2
[0.1.1]: https://github.com/ngen-advisory/netatmo-hardened/releases/tag/v0.1.1
[0.1.0]: https://github.com/ngen-advisory/netatmo-hardened/releases/tag/v0.1.0
