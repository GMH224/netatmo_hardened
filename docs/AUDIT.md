# Defect Audit — Basis for Release 0.1.0

**Audit date:** 2026-09-21 / 22
**Artifact audited:** the fork as distributed at version `2026.9.21`
**Baseline:** Home Assistant 2026.9 · pyatmo 9.9.0 · Python 3.14.2

This document records how the defects fixed in 0.1.0 were found, and how a
supplied third-party audit report was validated against the source. It is kept
in the package because the traceability matrix in `DEFECT_REGISTER.md` is only
meaningful if the provenance of each finding is also auditable.

---

## 1. Method

| Activity | Detail |
| --- | --- |
| Source review | All 24 Python modules plus `manifest.json`, `hacs.json`, `strings.json`, `services.yaml`, `quality_scale.yaml`, `NOTICE.md`, `README.md`, read in full |
| Static analysis | `ruff` with rules F, E9, B, S, ASYNC, RUF |
| Dependency review | `pyatmo 9.9.0` downloaded from PyPI; `account.py`, `room.py`, `exceptions.py`, `event.py`, `webhook.py` read directly |
| Platform review | Home Assistant Python floor per release taken from PyPI release metadata; every 2026 developer-blog deprecation enumerated and checked against the tree |
| Reproduction | Annotation-evaluation failure reproduced locally; coordinate-parsing failure reproduced in a unit test |
| Upstream corroboration | Three referenced core issues opened and read |
| **Not performed** | Live Home Assistant deployment; real Netatmo hardware. See `VERIFICATION_REPORT.md` §4 |

The independent review was carried out **before** reading the supplied external
report's findings in detail, so that the two could be compared rather than one
anchoring the other.

---

## 2. Findings summary

| Category | Count |
| --- | --- |
| Packaging / release-integrity defects | 4 |
| Code defects | 21 |
| Deprecations requiring migration | 2 |
| Deprecations verified already compliant | 7 |

Five code defects were found by the independent review only and do not appear
in the external report: **C-2** (sub-event destruction — the report noted the
mutation but not its effect), **C-10** (deque mutation race), **C-13**
(unbounded watchdog loop), **C-18** (dead sub-event map disabling three
documented triggers), and the whole **P0** packaging class.

Full detail in [`DEFECT_REGISTER.md`](DEFECT_REGISTER.md).

---

## 3. The two defects that mattered most

### 3.1 C-4 — the recovery mechanism disabled itself

`async_unload_entry` caught only `pyatmo.ApiError` around
`async_dropwebhook()`. A `TimeoutError` therefore propagated out of unload.

A Home Assistant reload is an unload followed by a setup, so a failing unload
breaks every reload — including `async_schedule_reload()`, which the fork's own
stale-data watchdog calls to recover from a wedged state.

The watchdog fires when the network is unreachable. `async_dropwebhook()` times
out when the network is unreachable. The fork's headline recovery feature was
therefore disabled by precisely the condition it existed to handle. Neither the
original authors nor the external report connected these two halves; the
report listed the exception handler as a Medium "cleanup gap".

### 3.2 C-2 — silent, permanent data loss

`process_events` assigned into `event.__dict__` — the live attribute dictionary
of pyatmo's `Event` object:

```python
event_data = event.__dict__
event_data["subevents"] = [
    event.__dict__ for event in event_data.get("subevents", [])
    if not isinstance(event, dict)
]
```

The first pass replaced each `Event` sub-event with its `__dict__`, writing the
result back onto the shared object. On the second poll the same filter,
`if not isinstance(event, dict)`, matched nothing — every sub-event had become
a dict — so `subevents` became `[]` permanently.

Camera sub-event history disappeared after one update cycle, with no error
raised anywhere. `tests/unit/test_control_integrity.py::test_event_index_is_idempotent_across_polls`
now runs three consecutive passes specifically to pin this.

---

## 4. Validation of the supplied external report

The supplied report contained 34 numbered findings across four passes. It was
useful — roughly twenty findings are real, and its adversarial payload matrix
was adopted substantially intact into `TEST_PLAN.md` §3. It should not,
however, have been used as a release gate.

| Verdict | Count | Findings |
| --- | --- | --- |
| Confirmed | 20 | NET-003…016, SEC-001…006, SEC-009, SEC-012 |
| **Materially wrong** | **2** | **NET-001, NET-002 — both ranked by the report as top release blockers** |
| Unsubstantiated / not defects | 5 | NET-028, NET-029, NET-031, NET-032, NET-033 |
| Correctly self-labelled non-defects | 2 | SEC-007, SEC-011 |
| One defect counted as seven | — | NET-010, NET-011, SEC-002…006 are the same ingress defect |

### 4.1 NET-001 — rejected as stated

Ranked Critical. Claimed that passing disabled device IDs to pyatmo's
`disabled_homes_ids` can suppress an entire home.

Verified against `pyatmo/account.py` (9.9.0): `disabled_homes_ids` is compared
only against `home_id` in `_is_home_disabled`; `all_home_names` stays populated
for denylisted homes so the Home device survives in the registry; and Netatmo
home IDs (24-character hex) and module IDs (MAC addresses) are disjoint
namespaces, so the docstring's assumption is sound.

The behaviour is also deliberate in this fork:
`device.py::async_sync_home_disabled_state` mirrors a home's disabled state
onto its descendants, and `coordinator._handle_home_device_update` reloads on
toggle.

Upstream issue #181448 was read. Its actual complaint is that the behaviour is
**silent** — "a log line or a repair issue would have made this diagnosable in
seconds" — not that it is wrong. That residual is accepted and fixed with a
diagnostic log line. Severity: Low, not Critical.

### 4.2 NET-002 — mechanism wrong

Ranked High. Claimed cooling mode raises `KeyError: None`, arguing that
`getattr(self.device, "therm_setpoint_mode", None)` "only handles the attribute
being absent, not the attribute existing and containing `None`."

That is not how `getattr` works, and in any case the code explicitly tested
`if therm_setpoint_mode is None` on the following line. In pyatmo 9.9.0
`Room.therm_setpoint_mode` is a class attribute defaulting to `None`, so the
guard fires and no `KeyError` is possible.

Upstream issue #175581 is real but describes **core**, which this fork had
already patched. The genuine residual is different and was missed: the patch
substituted preset `schedule` with no target temperature, so a cooling home
reported wrong data as healthy. Fixed as **C-14** using pyatmo's unified
`setpoint_mode` / `setpoint_temperature`. Severity: Medium, not High.

### 4.3 NET-008 — correct finding, unsafe recommendation

The intersection-versus-subset scope test is a real defect. The recommended fix
— `required_scopes <= actual_scopes` — would break every installation that owns
only some Netatmo product families, because Netatmo issues scopes per family.
Implemented differently as **C-15**; reasoning in `DEFECT_REGISTER.md` §4.1.

### 4.4 Reporting quality observations

Recorded because they bear on how much weight to give similar reports in
future:

- Both top-ranked blockers fail source verification.
- Several "deterministic reproductions" are presented as executed — including
  specific console output for NET-004 — although the report states it had no
  Home Assistant runtime and no payload fixtures. The underlying defect is real;
  the evidence for it was not obtained as described.
- One ingress defect is split across seven identifiers, inflating the count.
- NET-031 through NET-033 are test-plan prose with no code citation; the
  report's own Appendix D concedes this. They are reclassified here as test
  objectives (`TEST_PLAN.md` §4).
- The entire packaging class — including the two defects that prevented the
  integration from installing or displaying at all — is absent.

---

## 5. Conclusion

The fork's hardening concept was sound and its four documented changes address
genuine, externally corroborated upstream failures. What it lacked was
verification: no tests, untrue conformance attestations, a change record that
did not describe its own changes, and a declared platform range on which the
code could not run.

0.1.0 closes the defects and supplies the evidence. The one criterion not met —
execution of the integration tier — is recorded as a deviation in
`RELEASE_0.1.0.md` §5 rather than papered over, which is the practice whose
absence caused most of the problems found by this audit.
