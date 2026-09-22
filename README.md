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

**Version 0.1.4** · Home Assistant **2026.9+** · Python **3.14.2+** · pyatmo **9.9.0**

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

### Polling matched to the data (0.1.4)

Upstream derives every poll interval from the **rate limit** — an app with its
own credentials may make 400 calls an hour, so divide by seven — and never from
how often the data changes. A Netatmo weather station publishes once every five
minutes, indoor and outdoor alike; it was being polled every 85 seconds.

Each publisher now has a floor drawn from its source: weather and air quality
at 240 s, home status at 120 s, topology at an hour. The floor is a minimum,
not a replacement, so Home Assistant Cloud's smaller budget still produces
longer intervals where it should.

Homes that can never produce an entity are no longer polled at all — one the
Netatmo app created for an address you never equipped, or one whose every
module and room you have disabled. On the reference account that took API
traffic from **210 calls an hour to 46**, with no loss of data: sensors update
every 4 minutes instead of 2, and the station only produces a measurement
every 5.

There is no per-room API call, so disabling *some* rooms of a home saves no
traffic — the request fetches the whole home either way.

### API telemetry (0.1.3)

Six diagnostic sensors under a **Netatmo API** device answer the question the
integration could not: not *whether* it is healthy — every entity's
availability already told you that — but **how** healthy, and **when it last
was not**.

| Sensor | |
| --- | --- |
| API failure ratio (1h) | Percentage of calls in the last hour that failed |
| API last error | The message, redacted and truncated |
| API last error type | `auth`, `rate_limit`, `throttling`, `timeout`, `transport`, `server`, `client`, `no_device`, `unknown` |
| API last error time | When it happened |
| API last success | When the API last worked |
| API poll latency | How long the last call took |

A gap in a history graph looks identical whether it came from a Netatmo
outage, your own network, or a rate limit. These tell them apart.

**They stay available when the API is down** — deliberately, and unlike every
other entity here. A diagnostic sensor that went unavailable during a fault
would report nothing at the only moment you read it. Failure ratio is
*unknown* rather than `0` before the first poll, because an integration that
has made no calls has not achieved a 0% failure rate.

### Push events are off by default (0.1.2)

Netatmo pushes camera and thermostat events by calling a **webhook**, which it
registers only against a publicly reachable HTTPS endpoint on port 443. Most
Home Assistant installations do not have one, and for a deployment you care
about that is usually the right choice.

Such an installation cannot use push events at all — Netatmo refuses every
attempt with `400 WH006`, identically, for ever. 0.1.1 kept asking every
fifteen minutes anyway, against a rate-limited account, without telling anyone
what to change. So from 0.1.2 the integration asks only when you say it can:
**Configure → Push events**.

Nothing else changes. All data still arrives by polling — on a 400 calls/hour
budget, because you use your own application credentials — and camera
floodlights stay controllable. If you publish Home Assistant over HTTPS or use
Home Assistant Cloud and want push back, it is one switch. See
[`docs/MIGRATION.md`](docs/MIGRATION.md).

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
custom domain cannot use it. You register your own Netatmo application instead
— a five-minute, one-time step at
[dev.netatmo.com](https://dev.netatmo.com/apps/createanapp), using your normal
Netatmo account. **Leave the redirect URI and webhook URI blank**; Home
Assistant supplies both. Full walkthrough in
[`docs/MIGRATION.md`](docs/MIGRATION.md).

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
| [`CHANGELOG.md`](CHANGELOG.md) | What changed in each release and why |
| [`docs/RELEASE_0.1.4.md`](docs/RELEASE_0.1.4.md) | Release record, 0.1.4 |
| [`docs/AUDIT_0.1.4.md`](docs/AUDIT_0.1.4.md) | Poll-scheduling audit, 0.1.4 |
| [`docs/RELEASE_0.1.3.md`](docs/RELEASE_0.1.3.md) | Release record, 0.1.3 |
| [`docs/AUDIT_0.1.3.md`](docs/AUDIT_0.1.3.md) | Feature-addition review of 0.1.3 |
| [`docs/RELEASE_0.1.2.md`](docs/RELEASE_0.1.2.md) | Release record, 0.1.2 — **read §5 before upgrading** |
| [`docs/AUDIT_0.1.2.md`](docs/AUDIT_0.1.2.md) | Live soak and review of 0.1.1 |
| [`docs/RELEASE_0.1.1.md`](docs/RELEASE_0.1.1.md) | Release record, 0.1.1 |
| [`docs/DEFECT_REGISTER.md`](docs/DEFECT_REGISTER.md) | Every defect → fix → test, plus rejected recommendations |
| [`docs/AUDIT_0.1.1.md`](docs/AUDIT_0.1.1.md) | **External independent audit of 0.1.0** and its remediation |
| [`docs/AUDIT_0.1.0.md`](docs/AUDIT_0.1.0.md) | Internal audit that produced 0.1.0 |
| [`docs/TEST_PLAN.md`](docs/TEST_PLAN.md) | Test objectives and coverage map |
| [`docs/VERIFICATION_REPORT.md`](docs/VERIFICATION_REPORT.md) | **What was and was not executed before release** |
| [`docs/COMPATIBILITY.md`](docs/COMPATIBILITY.md) | Platform baseline and the 2026 deprecation sweep |
| [`docs/MIGRATION.md`](docs/MIGRATION.md) | Cut-over from the built-in integration, preserving entity IDs |
| [`docs/COMMISSIONING.md`](docs/COMMISSIONING.md) | **Soak checklist — work through this before any unattended deployment** |
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

# Compile gate - ruff check is NOT a syntax gate; see docs/AUDIT_0.1.3.md
python -c "import pathlib;[compile(f.read_text(),str(f),'exec') for f in pathlib.Path('.').rglob('*.py') if '__pycache__' not in str(f)]"
```

The test suite is split deliberately. Tier 1 covers the logic where a silent
defect changes what the integration *does* — control-path arithmetic and
webhook ingress validation — and runs anywhere, with nothing but pytest
installed. Tier 2 covers lifecycle and recovery behaviour that genuinely needs
a Home Assistant runtime.

## Known limitations

Stated plainly rather than omitted:

- Tier 2 integration tests are authored but **still have not been executed**.
  This is not a formality: it is the direct cause of the two availability
  regressions that 0.1.1 fixed, and 0.1.2's push-event gating is a load-time
  decision that only tier 2 reaches. Do not deploy to an unattended
  installation until CI has run them green. See
  [`docs/VERIFICATION_REPORT.md`](docs/VERIFICATION_REPORT.md) §4.
- A live soak against a real Netatmo account **was** performed for 0.1.2, on a
  weather station only. It found E-010. Cameras, thermostats and presence
  devices remain unexercised against real hardware, as does push delivery.
- Neither 0.1.2 nor 0.1.3 has been independently audited. 0.1.1 was, and that
  audit found four regressions this project had introduced.
- 0.1.3's telemetry has **never been soaked**: the failure ratio has not been
  observed going non-zero and back on real hardware, and there is no latency
  baseline for a real deployment. Watch those sensors before believing them.
- The rate-limit brake **freezes** polling rather than slowing it once the
  hourly budget is exceeded. 0.1.4 puts a normal account far out of its reach
  but does not fix it. See [`docs/AUDIT_0.1.4.md`](docs/AUDIT_0.1.4.md) §6.
- `HOME` and `EVENT` share a signal name upstream, so the event publisher is
  never created and camera events are not polled. Needs camera hardware to
  verify before changing.
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
