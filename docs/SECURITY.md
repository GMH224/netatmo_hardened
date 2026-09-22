# Security Policy

**Release:** 0.1.0 · Reviewed for 0.1.2: unchanged. The push-event option (F-001) only decides whether the webhook ingress boundary is wired up at all; it adds no new trust boundary. Disabling push removes an ingress surface rather than adding one.

## Reporting a vulnerability

Report suspected vulnerabilities privately through the repository's GitHub
Security Advisories, not as a public issue. Include the Home Assistant version,
the integration version, and a minimal reproduction where possible.

## Trust boundaries

This integration has three boundaries where data crosses from a less trusted
domain into Home Assistant state.

### 1. The webhook endpoint — the primary boundary

Home Assistant authenticates webhook routes on **possession of the webhook id
alone**; the platform documents that id as equivalent to a password. There is
no signature, nonce or replay protection in Netatmo's webhook protocol, so
anyone who learns the id can post arbitrary payloads to it.

**Controls:**

| Control | Implementation |
| --- | --- |
| Id entropy | `secrets.token_hex()` — 128 bits; blind guessing is impractical |
| Id confidentiality | The webhook URL is **never logged** (fixed in 0.1.0, defect C-11) |
| Payload shape | Root must be a JSON object; non-objects dropped |
| Event allowlist | Unknown or absent event types dropped, never defaulted |
| Field validation | Identifiers type- and length-checked before use as dict keys |
| Home identity | Events naming an unknown home are dropped |
| Collection bounds | `MAX_COLLECTION_ITEMS = 256` |
| Log hygiene | Payloads never logged in full; shape summary only |

**Residual risk, stated explicitly.** Possession of the webhook id still allows
an attacker to inject *well-formed* Netatmo events, which Home Assistant
automations will act on. Validation cannot fix this — it is inherent to the
protocol. Treat the webhook id as a credential: do not paste logs containing it
into issues, and rotate the config entry if you believe it has been exposed.

### 2. The Netatmo API

Responses are consumed through pyatmo. The integration does not construct URLs
from user input. Media URLs originate from Netatmo and are passed to the media
player; a compromised upstream could in principle return a hostile URL, which
is noted as an unverified risk rather than claimed as safe.

### 3. OAuth credentials

Tokens are held by Home Assistant's OAuth2 helper and never logged. Diagnostics
redact `access_token`, `refresh_token`, `webhook_id`, `cloudhook_url` and
coordinates. Since 0.1.0 a 401/403 starts a reauthentication flow rather than
being retried indefinitely (defect C-7).

## Security-relevant fixes in 0.1.0

| Defect | Issue |
| --- | --- |
| C-11 | Full webhook URL — a bearer credential — written to debug logs |
| C-11 | Complete webhook payloads logged, including identity metadata and signed face/snapshot URLs |
| C-8 | Malformed payloads raised unhandled exceptions inside the event loop |
| C-9 | Entity handlers re-parsed unvalidated payloads |
| C-15 | Partially authorized tokens accepted silently |

## Non-goals

This integration talks to a vendor cloud over the public internet. It is not
suitable as a safety-instrumented function, and no claim of IEC 62443, 61511 or
61508 conformance is made anywhere in this repository. The documentation set
follows ICS-style change-control and traceability practice; that is a
development-quality claim, not a certification.
