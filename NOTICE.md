# Notice

This repository is a derivative work of the `netatmo` integration from
[home-assistant/core](https://github.com/home-assistant/core)
(`homeassistant/components/netatmo/`), licensed under the
[Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0). See
`LICENSE` for the full license text.

Original copyright: Home Assistant / Nabu Casa, Inc. and contributors.
Original code owner (per upstream `manifest.json`): @cgtobi.

## Scope of modification

This fork uses its own domain, `netatmo_hardened`, and installs **alongside**
Home Assistant's built-in `netatmo` integration rather than overriding it. It
modifies no Home Assistant file. Earlier builds of this fork did override the
core domain; that was reconsidered for 0.1.0 because masking the built-in
integration removes the operator's fallback when an upgrade breaks the fork.

> **Correction notice.** The version of this file distributed before 0.1.0
> stated that "no other logic, entities, or supported devices were changed"
> beyond four listed items. That statement was inaccurate: substantive
> undocumented changes existed in `device.py`, `entity.py`, `coordinator.py`
> and `services.py`. This file has been rewritten to describe the actual change
> set. The inaccuracy is itself recorded as defect P0-3.

The complete, itemised change set relative to upstream is maintained in:

- [`CHANGELOG.md`](CHANGELOG.md) — what changed, grouped by consequence
- [`docs/DEFECT_REGISTER.md`](docs/DEFECT_REGISTER.md) — every change traced to
  the defect it fixes and the test that holds it
- [`docs/COMPATIBILITY.md`](docs/COMPATIBILITY.md) — platform deprecations
  migrated

All fork-specific sections in the source are marked `[hardened-fork]` in a
comment, so a diff against upstream can be reviewed section by section.

## Summary of divergence from upstream

| Area | Nature of change |
| --- | --- |
| `webhook.py` | Rewritten: validated ingress boundary, whole-transaction retry, idempotent lifecycle, log redaction |
| `event_validation.py` | New module — has no upstream counterpart |
| `coordinator.py` | Error classification, bounded watchdog, snapshot iteration, faster recovery, shorter backoff ceiling |
| `device.py` | Bridge topology mapping, disabled-state mirroring, denylist diagnostics |
| `entity.py` | Reachability base class, assertion removal, tolerant device description lookup |
| `helper.py` | Pure, unit-testable control-path and event-snapshot logic |
| `climate.py` | Cooling-mode support, duration arithmetic, defensive event handling |
| `camera.py`, `media_source.py` | Non-mutating event snapshot, id-keyed events, fail-closed resolution |
| `sensor.py` | Public weather reconfiguration binding |
| `config_flow.py`, `__init__.py` | Deprecation migration (D-1), scope diagnostics, tolerant unload |
| `services.py` | Deprecation of the manual webhook actions with a repair issue |
| Packaging | HA floor, runtime translations, honest quality-scale attestations, versioning |

## Attribution requirement

If you redistribute this fork further, keep this `NOTICE.md` and the `LICENSE`
file, per the terms of the Apache License 2.0 (§4).
