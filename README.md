# Netatmo (hardened fork)

[![Validate](https://github.com/ngen-advisory/netatmo-hardened/actions/workflows/validate.yml/badge.svg)](https://github.com/ngen-advisory/netatmo-hardened/actions/workflows/validate.yml)
[![Tests](https://github.com/ngen-advisory/netatmo-hardened/actions/workflows/test.yml/badge.svg)](https://github.com/ngen-advisory/netatmo-hardened/actions/workflows/test.yml)

A reliability-hardened fork of Home Assistant Core's built-in
[`netatmo`](https://www.home-assistant.io/integrations/netatmo/) integration,
installable via [HACS](https://hacs.xyz/) as a custom repository.

It uses its **own domain, `netatmo_hardened`**, and installs alongside the
built-in integration rather than overriding it. It writes nothing into Home
Assistant's own files, and an HA upgrade cannot overwrite it — custom
components live in your config directory. Most importantly, **you keep a
fallback**: if this fork ever breaks on an upgrade, disable it and the built-in
integration takes over. A fork that shadows the core domain masks it, so a
breakage takes Netatmo out entirely.

**Version 0.1.0** · Home Assistant **2026.9+** · Python **3.14.2+** · pyatmo **9.9.0**

---

## Why this exists

The stock integration would go "unavailable" after a brief network interruption
and stay that way until manually reloaded. That is the problem this fork was
started to solve, and it does solve it — webhook registration now retries with
backoff, publisher backoff recovers in minutes rather than an hour, and a
watchdog reloads a wedged integration on its own.

Version 0.1.0 is a different kind of release. The fork was put through a full
independent defect audit, and the audit found that several things which looked
fixed were not:

- Temperature overrides of 24 hours or more were **silently shortened** before
  being sent to Netatmo. A 26-hour override became a 2-hour one, with nothing
  logged. The service accepted one command; the API received another.
- Camera sub-events were **permanently destroyed** after a single poll cycle.
- Public weather sensors kept reporting the **previous geographic area** after
  being reconfigured, while every visible indicator said otherwise.
- A webhook cleanup timeout **aborted config entry unload**, which aborts every
  reload — including the watchdog's own recovery, for exactly the network fault
  that triggers it. The recovery mechanism disabled itself when it was needed.
- The integration **could not be installed at all** on most of the Home
  Assistant versions its own packaging advertised support for.

All of the above are fixed. See [`CHANGELOG.md`](CHANGELOG.md) for the complete
list and [`docs/DEFECT_REGISTER.md`](docs/DEFECT_REGISTER.md) for each defect
traced to its fix and test.

---

## Requirements

| | Minimum | Notes |
| --- | --- | --- |
| Home Assistant | **2026.9.0** | Tested against 2026.9.0 and 2026.9.3 in CI |
| Python | **3.14.2** | Required by HA 2026.3+ |
| pyatmo | 9.9.0 | Installed automatically; pinned deliberately |

> The declared minimum is a **tested claim**, not an aspiration. It is enforced
> by a CI job. Do not widen it without running the integration suite against
> the target version — see [`docs/COMPATIBILITY.md`](docs/COMPATIBILITY.md).

## Installation (HACS)

1. HACS → **Integrations** → ⋮ → **Custom repositories**
2. Add this repository's URL, category **Integration**
3. Install "Netatmo (hardened fork)"
4. Restart Home Assistant
5. Add Netatmo **application credentials** and configure the integration

> **Read [`docs/MIGRATION.md`](docs/MIGRATION.md) before installing** if you
> already run the built-in Netatmo integration. Following the cut-over order in
> that document preserves your entity IDs, and therefore your automations,
> dashboards and long-term statistics.

### Two things to know before you start

**Home Assistant Cloud account linking is not available.** Cloud's Netatmo
linking is registered by Nabu Casa for the `netatmo` domain specifically, so a
custom domain cannot use it. You create your own Netatmo developer application
instead — a five-minute, one-time step.

This is mostly an upside: own-application credentials carry a **400 calls/hour**
budget instead of Cloud's 150, so the integration polls roughly 3.5× more
frequently, data is fresher, and recovery after an outage is faster.

**Do not leave both integrations configured against the same account.** Netatmo
rate-limits per account, not per client, and permits only one active webhook.
Disable the built-in entry rather than deleting it if you want an idle
fallback.

### Upgrading from a pre-0.1.0 build of this fork

Earlier builds used the `netatmo` domain. See
[`docs/MIGRATION.md`](docs/MIGRATION.md), and read the **Behaviour changes**
section of [`CHANGELOG.md`](CHANGELOG.md) — three changes can affect existing
automations:

- Outdoor camera `human` / `animal` / `vehicle` triggers **now fire** (they
  previously could not fire at all). Dormant automations will start running.
- Camera media source identifiers changed; bookmarked media URLs need
  re-browsing.
- Cooling-mode thermostats now report their real setpoint instead of preset
  `schedule`.

---

## Documentation

| Document | Contents |
| --- | --- |
| [`CHANGELOG.md`](CHANGELOG.md) | What changed in 0.1.0 and why |
| [`docs/RELEASE_0.1.0.md`](docs/RELEASE_0.1.0.md) | Release record, contents, acceptance criteria |
| [`docs/DEFECT_REGISTER.md`](docs/DEFECT_REGISTER.md) | Every defect → fix → test, plus rejected recommendations |
| [`docs/AUDIT.md`](docs/AUDIT.md) | Audit method, findings, and validation of the external report |
| [`docs/TEST_PLAN.md`](docs/TEST_PLAN.md) | Test objectives and coverage map |
| [`docs/VERIFICATION_REPORT.md`](docs/VERIFICATION_REPORT.md) | **What was and was not executed before release** |
| [`docs/COMPATIBILITY.md`](docs/COMPATIBILITY.md) | Platform baseline and the 2026 deprecation sweep |
| [`docs/MIGRATION.md`](docs/MIGRATION.md) | Cut-over from the built-in integration, preserving entity IDs |
| [`docs/RELEASE_0.1.0.md`](docs/RELEASE_0.1.0.md) | Release record and acceptance criteria |
| [`docs/SECURITY.md`](docs/SECURITY.md) | Trust boundaries and reporting |

## Development

```bash
# Tier 1 - pure logic, no Home Assistant needed, any Python >= 3.11
pip install pytest==8.3.4
pytest tests/unit -v

# Tier 2 - integration, requires Python >= 3.14.2
pip install -r requirements_test.txt
pytest tests/integration -v

# Static analysis
ruff check custom_components tests
ruff format --check custom_components tests
```

The test suite is split deliberately. Tier 1 covers the logic where a silent
defect changes what the integration *does* — control-path arithmetic and
webhook ingress validation — and runs anywhere, with nothing but pytest
installed. Tier 2 covers lifecycle and recovery behaviour that genuinely needs
a Home Assistant runtime.

## Known limitations

Stated plainly rather than omitted:

- Tier 2 integration tests are authored but **had not been executed** at the
  time of tagging 0.1.0. See [`docs/VERIFICATION_REPORT.md`](docs/VERIFICATION_REPORT.md) §4.
- No live deployment against real Netatmo hardware was performed.
- No automated diff against the upstream core integration; drift review is
  manual.
- No brand icon: HA brand images are keyed by domain and the central brands
  repository has no `netatmo_hardened` entry. Cosmetic only; shipping our own
  is a 0.2.0 item.
- Home Assistant Cloud account linking is unavailable by design (see above).
- pyatmo 9.9.0's own webhook parser is not yet adopted (planned for 0.2.0).

## License

Apache License 2.0 — see [`LICENSE`](LICENSE). Derivative work of
[home-assistant/core](https://github.com/home-assistant/core); see
[`NOTICE.md`](NOTICE.md) for attribution and the scope of modification.
