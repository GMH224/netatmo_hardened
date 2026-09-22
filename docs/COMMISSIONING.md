# Commissioning and Soak Checklist

**Applies to:** 0.1.2 · test or staging installation
**Purpose:** establish, by observation on a live system, the behaviour that
neither static analysis nor the tier-1 test suite can establish.

---

## Why this exists

0.1.0's two worst defects — the integration reloading itself every few hours
(E-001) and Home Assistant Cloud subscribers receiving no webhook at all
(E-002) — were both invisible to static analysis and to every executed test,
and both would have been **obvious within hours of real use**. A reload cycle
shows plainly in the log. A missing webhook shows on the first restart.

That is what this checklist is for. It is not a formality: for this defect
class a short supervised soak is the highest-yield verification available, and
it is the step whose absence produced 0.1.1.

**It has now paid for itself once.** The first run of this checklist, against
0.1.1 on 2026-09-22, surfaced **E-010** four minutes after start — a retry loop
with no exit, hammering a rate-limited account for ever against a rejection
that could never succeed. No amount of reading the code had found it, because
the defect is about what a correct-looking loop does when the environment never
changes. 0.1.2 exists because of this document.

That also bounds what it can do: it catches what a running system does, not
what a rarely-taken branch does. It is a compensating control for the
unexecuted tier-2 suite, not a replacement for it.

Record the outcome. A soak whose results are not written down has not been
performed.

---

## 0. Before you start

Enable debug logging for the integration. Most checks below depend on it.

```yaml
# configuration.yaml
logger:
  default: info
  logs:
    custom_components.netatmo_hardened: debug
    pyatmo: info
```

Restart Home Assistant after adding this.

---

## 1. First five minutes

| # | Check | Pass criterion | Guards |
| --- | --- | --- | --- |
| 1.1 | Integration appears under **Settings → Devices & services** | Devices and entities are listed, entry is not in an error state | setup |
| 1.2 | Config flow text is readable | Titles and field labels are real sentences, **not** raw keys like `component.netatmo_hardened.config.step.user.title` | P0-2 |
| 1.3 | Entities have states | Temperatures, humidity etc. show values rather than `unknown` | polling |
| 1.4 | **Push-event decision is deliberate** | See 1.4a — decide before you check anything else | **F-001** |
| 1.5 | **Webhook state matches that decision** | See 1.5 | **E-002**, **E-010** |

### 1.4a Decide about push events first

From 0.1.2 push events are **off by default**, so "no webhook" is the expected
state and is not a fault. Decide which case you are in before reading any
webhook check, or you will chase a defect that is a setting.

| Is Home Assistant reachable from the internet over **HTTPS on port 443**? | Do this |
| --- | --- |
| No — the normal case for a local-only installation | Leave push events off. Everything arrives by polling. Skip to §2. |
| Yes — you publish HA, or you have a Home Assistant Cloud subscription | Settings → Devices & services → Netatmo (hardened) → **Configure** → *Push events* → enable. The integration reloads. Then do §1.5. |

If you are not sure, leave it off. Turning it on later costs one click; the
failure mode of guessing "yes" wrongly is E-010, which 0.1.2 now catches but
which still means push does not work.

### 1.5 Confirming the webhook state matches your decision

Do not infer this from absence of errors. Check it directly:

**Settings → Devices & services → Netatmo (hardened) → ⋮ → Download diagnostics**

In the downloaded JSON, read `webhook_registered` together with
`options.enable_webhook`:

| `enable_webhook` | `webhook_registered` | Meaning |
| --- | --- | --- |
| absent or `false` | `false` | ✅ Correct. Push is off by choice. No registration was attempted. |
| absent or `false` | `true` | ❌ Gating failed — the option was ignored. Report. |
| `true` | `true` | ✅ Push is working. |
| `true` | `false` | Registration was attempted and refused — see below. |

For the last row, check **Settings → System → Repairs** first. A repair issue
titled *"Netatmo push events could not be enabled"* means 0.1.2 classified the
rejection as permanent and stopped retrying; it quotes Netatmo's own error.
`invalid webhook url (WH006)` means HA is not publicly reachable over HTTPS on
port 443 — either fix that under Settings → System → Network, or turn push
events back off.

No repair issue, and the log shows `Netatmo webhook registration failed …
Retrying in N seconds`, means a transient failure that is still being retried.
That is normal for a few minutes. If it is **still** retrying after an hour
with the same error text, that is E-010 recurring — the classification missed a
deterministic rejection. Stop and report, with the exact error text.

> With push enabled this matters most if you have a **Home Assistant Cloud
> subscription**. That was the exact population 0.1.0 silently broke (E-002).

### 1.5a Push events off must not cost anything else

If you left push off, confirm the two things that were *supposed* to be
unaffected:

- every sensor still has a value (polling is untouched);
- if you have a Presence/Outdoor camera, its **floodlight entity is available
  and controllable** — not greyed out. Under 0.1.1 a missing webhook made it
  unavailable; under the 0.1.2 default that would have been permanent, so the
  availability rule was changed to apply only when push is expected.

### 1.6 The webhook id must not be in the log (only if push is enabled)

```bash
grep -ci "api/webhook/" home-assistant.log
```

Expected: `0`. Home Assistant documents the webhook id as equivalent to a
password (guards C-11).

---

## 2. First six hours — the reload check

**This is the single most important observation in the list.**

Netatmo access tokens live about three hours. Home Assistant refreshes shortly
before expiry, and each refresh writes the new token to the config entry. In
0.1.0 that write triggered a full reload.

```bash
grep -c "Setting up netatmo_hardened" home-assistant.log
```

| Observation | Meaning |
| --- | --- |
| **1** (your initial setup) after 6 h | ✅ **E-001 cleared.** Two refresh cycles have passed without a reload. |
| Increments every ~3 h | ❌ **E-001 is still present.** Stop the soak and report. |
| Increments every ~15 min | ❌ Different fault — the stale-data watchdog is firing. Check for `all data has been unavailable`. |

Six hours covers two token refreshes. That is the minimum to call it cleared.

---

## 3. First day

### 3.1 Webhook events actually arrive

Polling masks a dead webhook, so test push explicitly. Change something from
the **Netatmo app**, not from Home Assistant:

* switch the thermostat between Schedule and Away, or
* trigger motion in front of a camera.

| Observation | Meaning |
| --- | --- |
| Home Assistant updates within a few seconds | ✅ Push is working |
| Updates only after a minute or more | ❌ Polling only — the webhook is not delivering |

### 3.2 Commands are honest about failure

Guards **E-004** — the defect where a rejected command still displayed as
success.

1. Operate a switch, light, cover or camera from Home Assistant.
2. Confirm the physical device actually changed.
3. Confirm the Home Assistant state matches the device.

If you can contrive a rejection (device unplugged, offline), the correct
behaviour is a **visible error** in the UI and the entity state **unchanged** —
not a silent, wrong state.

### 3.3 Long temperature override

Guards **C-1** — overrides of 24 h or more used to be silently truncated.

1. Call `netatmo_hardened.set_temperature_with_time_period` with
   `time_period: 26:00:00`.
2. Open the Netatmo app and check the override's end time.
3. It must be **26 hours** away, not 2.

### 3.4 Public weather area (if used)

Guards **E-006** and **C-3**.

1. Add an area whose coordinates sit at a boundary if your geography allows
   (latitude 90 / longitude 180 are the interesting cases), or any ordinary area.
2. Save. It must save without error.
3. Reconfigure the area to a **different location**.
4. Readings must change to the new location, not keep reporting the old one.

---

## 4. First week

| # | Check | Pass criterion | Guards |
| --- | --- | --- | --- |
| 4.1 | Reload count still 1 (or equal to your deliberate restarts) | No drift | E-001 |
| 4.2 | No repair issues raised under **Settings → Repairs** | None, or only ones you understand | C-13, C-15 |
| 4.3 | Restart Home Assistant; re-run check 1.5 | `webhook_registered: true` again | E-002 |
| 4.4 | Entities did not go permanently `unavailable` after a network blip | Recovery within ~5 minutes | C-4, C-7 |
| 4.5 | Camera recordings browsable in **Media** | Events listed, playable | C-19, C-20 |
| 4.6 | Log volume is reasonable | No repeating error every few seconds | general |

### 4.7 Device triggers, if you use them

Guards **C-5** and **E-008**.

1. Create an automation with a Netatmo device trigger.
2. Check the trigger list offers each option **once**, not once per entity.
3. Verify it fires for the intended device — and *only* that device.

---

## 5. If something fails

Capture both of these before changing anything:

1. **Diagnostics** — Settings → Devices & services → Netatmo (hardened) → ⋮ →
   Download diagnostics. Tokens, webhook id and coordinates are redacted
   automatically.
2. **Log excerpt** — from ten minutes before the symptom to ten minutes after.

Then check the excerpt for the webhook id before sharing it:

```bash
grep -ci "api/webhook/" excerpt.log   # must be 0
```

---

## 6. Exit criteria

Deploy to an unattended installation only when **all** of the following hold:

- [ ] CI tier-2 suite has passed at least once
- [ ] §1 complete, including a **deliberate** push-event decision (§1.4a) and a
      `webhook_registered` value that matches it (§1.5)
- [ ] If push is off: §1.5a confirms polling and floodlight availability are
      unaffected
- [ ] If push is on: no *"Netatmo push events could not be enabled"* repair
      issue outstanding, and no registration retry still looping after an hour
- [ ] §2 clear after a minimum of six hours
- [ ] §3 complete for every device class you actually own
- [ ] §4 clear after seven days
- [ ] Results recorded, with dates

Until then 0.1.2 is a **test-environment release**, whatever its version number
says.

---

## 7. Record of soak

| Field | Value |
| --- | --- |
| Installation | |
| HA version | |
| Cloud subscription? | |
| Publicly reachable over HTTPS:443? | |
| Push events enabled? | |
| Device classes present | |
| Soak start | |
| Soak end | |
| §1 result | |
| §1.5 result (`enable_webhook` / `webhook_registered`) | |
| §2 result (reload count at 6 h) | |
| §3 result | |
| §4 result | |
| Defects observed | |
| Disposition | |
