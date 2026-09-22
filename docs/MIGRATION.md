# Migration Guide

**Applies to:** 0.1.0 · domain `netatmo_hardened`

## Why the domain is not `netatmo`

Earlier builds of this fork kept the `netatmo` domain so it would silently
override Home Assistant's built-in integration. That is convenient, and it is
the wrong trade for a deployment you care about.

Overriding the core domain **removes your fallback.** While the fork shadows
the built-in integration, the built-in integration effectively does not exist.
This fork is coupled to a lot of Home Assistant internals — `probatio`,
`EntityStateAttribute`, `include_child_devices`, `AnyDeviceEntry` — so an
upgrade that breaks it takes Netatmo out entirely, rather than degrading to the
working core integration. It also means you silently stop receiving upstream's
own Netatmo fixes.

With its own domain, `netatmo_hardened` installs alongside the built-in
integration and touches none of Home Assistant's files. If it ever breaks, you
disable it and the core integration takes over.

---

## What changes

| | Built-in `netatmo` | `netatmo_hardened` |
| --- | --- | --- |
| Install location | ships with HA | `config/custom_components/netatmo_hardened/` |
| Overwritten by HA upgrade | — | **No.** Custom components live in your config directory |
| Services | `netatmo.set_schedule` | `netatmo_hardened.set_schedule` |
| Bus event | `netatmo_event` | `netatmo_hardened_event` |
| Account linking | Home Assistant Cloud, or your own credentials | **Your own credentials only** — see below |
| API rate budget | 150 calls/hour (cloud) | 400 calls/hour (own app) |

### Home Assistant Cloud linking is not available

This is the one real cost of an independent domain, and it is unavoidable.

Home Assistant Cloud's Netatmo account linking is registered by Nabu Casa for
the `netatmo` domain specifically. A custom domain cannot use it. You must
create your own Netatmo developer application and add its credentials to Home
Assistant.

**This is mostly an upside.** Own-application credentials carry a **400
calls/hour** budget instead of Cloud's 150, and the integration polls roughly
3.5× more frequently as a result (`DEV_FACTOR` vs `CLOUD_FACTOR` in
`coordinator.py`). Data is fresher and recovery after an outage is faster. The
cost is a five-minute one-time setup.

### What "application credentials" actually are

Netatmo's API uses OAuth2. Two separate identities are involved:

* **The application** — proves *which program* is asking. This is the client ID
  and client secret, issued by Netatmo when you register an app.
* **You** — proves *whose data* it may read. This happens later, when you log in
  to Netatmo and approve the request.

Application credentials are the first of those. They are the integration's
identity, not your account password.

With the built-in integration you never see this step, because Nabu Casa
registered an application with Netatmo and Home Assistant Cloud lends you its
credentials. That registration is tied to the `netatmo` domain, so an
independently-domained integration cannot use it. You register your own instead
— once, in about five minutes.

### Creating them

1. Go to <https://dev.netatmo.com/apps/createanapp> and sign in with your
   **normal Netatmo account** (the same one your devices are on — you do not
   need a separate developer account).
2. Create an app. Give it any name and description, e.g. "Home Assistant".
3. **Leave the redirect URI and the webhook URI blank.** Home Assistant supplies
   both at runtime, and filling them in here can break the OAuth callback. The
   webhook URL is derived from your Home Assistant external URL, so make sure
   that is configured correctly under **Settings → System → Network**.
4. Save, then copy the **Client ID** and **Client secret**.
5. Enter them in Home Assistant. Either:
   * let the config flow ask you — **Settings → Devices & services → + Add
     integration → Netatmo (hardened)** prompts for them the first time; or
   * add them up front — **Settings → Devices & services → ⋮ (top right) →
     Application credentials → Add credential**, pick **Netatmo (hardened)**,
     paste both, and give the credential a name.
6. Complete the OAuth flow: you are sent to Netatmo, you log in, you approve the
   requested permissions, and you are returned to Home Assistant.

You only do this once. The credentials persist across restarts, upgrades and
re-authentications.

---

## Recommended cut-over: preserving entity IDs and history

Entity IDs are derived from the device and entity names, which this fork does
not change. If the **old entities are removed first**, the freed entity IDs are
regenerated identically by the new integration — so automations, dashboards and
long-term statistics carry over, because all three key on `entity_id`.

Order matters. Do not add the new integration before removing the old one, or
Home Assistant will suffix the new entities (`climate.living_room_2`) and you
will keep the suffix.

### Procedure

Install first, delete second, configure third. Doing it in that order keeps the
window in which you have no Netatmo integration down to a few minutes, because
installing the custom integration changes nothing until you configure it.

0. **Create your Netatmo developer application** (see above) and have the
   client ID and secret to hand before you start.

1. **Record your current entity IDs.**
   Developer tools → Template, and run:

   ```jinja
   {{ states | selectattr('entity_id', 'search', 'netatmo') 
      | map(attribute='entity_id') | list | join('\n') }}
   ```

   Save the output. This is your verification list for step 6.

2. **Back up.** Settings → System → Backups → Create backup. Do not skip this;
   step 3 is destructive.

3. **Install `netatmo_hardened`** via HACS and restart Home Assistant.
   Nothing changes yet — an installed but unconfigured integration is inert.

4. **Delete the built-in Netatmo integration.**
   Settings → Devices & services → Netatmo → ⋮ → **Delete**.
   This removes its entities from the registry and frees their entity IDs.
   Delete, do not disable — see below for why.

5. **Add your application credentials**
   (Settings → Devices & services → ⋮ → Application credentials → Add
   credential → **Netatmo (hardened)**), then **Add integration → Netatmo
   (hardened)** and complete the Netatmo OAuth flow.

6. **Verify.** Re-run the template from step 1 and compare. Entity IDs should
   match your saved list. Check that history for one long-lived sensor still
   shows data from before the migration.

7. **Re-create public weather areas.** These live in the config entry's
   options, not in the entity registry, so they do not carry over. Settings →
   Devices & services → Netatmo (hardened) → **Configure**.

### If an entity ID came back suffixed

Something still held the old ID. Settings → Devices & services → Entities,
search for the `_2` entity, open it, and edit the entity ID back to the
original. The old one will be free if step 3 completed.

---

## Running both integrations at once

Supported, and useful for a cautious cut-over — but **do not leave both
configured against the same Netatmo account for long.**

Both poll independently, and Netatmo's rate limit is per account, not per
client. Two configured integrations halve your effective budget and both may
start getting throttled (HTTP 429). Netatmo also permits only one active
webhook per account, so whichever registers last wins and the other silently
stops receiving push events.

### Do not "disable" the old entry instead of deleting it

It is tempting to disable the built-in Netatmo config entry rather than delete
it, to keep a fallback ready. **Do not do this if you want your entity IDs
preserved.**

A disabled config entry keeps its entities in the entity registry. The registry
is what reserves an entity ID, so `climate.living_room` remains taken and the
new integration's equivalent comes up as `climate.living_room_2`. You then have
to repoint every automation and dashboard card — exactly the outcome the
cut-over above exists to avoid.

Deleting costs you nothing in fallback terms. The built-in `netatmo`
integration ships with Home Assistant; deleting your *configuration* of it does
not remove the integration itself. You can re-add it from
**Settings → Devices & services → Add integration** at any time, in about two
minutes. The fallback is the integration being present in Home Assistant, not
a stale config entry sitting disabled.

Disabling is only the right move in one case: you want both configured
temporarily during a cautious cut-over and you are willing to live with
suffixed entity IDs on the new integration while both exist.

---

## Rolling back

1. Settings → Devices & services → **Netatmo (hardened)** → ⋮ → Delete.
2. Re-add the built-in **Netatmo** integration.
3. If you used the cut-over above, its entity IDs will be free and will
   regenerate to the same values.

Nothing this integration installs touches Home Assistant's own files, so
removing it via HACS leaves no trace beyond its config entry.

---

## Cosmetic note: the integration icon

Home Assistant's brand images are keyed by domain and served from the central
`home-assistant/brands` repository, which has no entry for `netatmo_hardened`.
The integration therefore shows a generic icon rather than the Netatmo logo.
Custom integrations have been able to ship their own brand images since HA
2026.2; adding them is on the roadmap for 0.2.0 and is purely visual.
