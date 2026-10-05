# JCC Agent Contract — activation boundaries and non-negotiables

**Owner:** metric-verify (independent verifier)
**Repo root:** `D:\jev` (contract rule 1 — no other drive)
**Branch at authoring:** `release/jcc-v1.3-final`, HEAD `3af548b`
**Status snapshot:** see `## Status snapshot` at the bottom — read it, do not trust this table's prose if they disagree.

## How to read the labels

Every row is one of:

| Label | Meaning |
|---|---|
| **VERIFIED** | I executed it on this host and observed the stated behaviour. Command + result recorded. |
| **UNVERIFIED** | Not measured by me. **Not** "probably fine". The reason is stated. |
| **COMMUNITY-VERIFIED** | Not applicable in this repo — nothing here is sourced from community/prior art rather than measurement. |

**Nothing in this file is labelled PASS on the strength of a test name, a comment, or a config value.** Where a contract row could be satisfied "on paper" but not in fact, the row says which.

---

## 1. Phase activation boundaries

### P0–P2 (current release): no automatic side effects

| # | Contract | Status | Evidence |
|---|---|---|---|
| 1.1 | **`Actuator.execute()` cannot act, even when fully enabled** | **VERIFIED** | `Actuator(enabled=True, allowed_actions=["ESCALATE"]).execute(d)` → `acted=False, reason=real_actuation_not_implemented`. The gate is not the config flag — **the code path does not exist**. Stronger than "configured off". |
| 1.2 | `Actuator.dispatch()` (the engine seam) is permanently inert | **VERIFIED** | Returns `acted=False, reason="shadow_mode"` regardless of `enabled`. |
| 1.3 | `ShadowActuationPort` records intent, never acts | **VERIFIED** | Returns `acted=False, executable=False, reason=SHADOW_NO_ACTUATION`. |
| 1.4 | Shipped configs have actuation and shadow off | **VERIFIED** | `config/jcc.yaml` and `config/jcc-v1.3.yaml`: `actuation.enabled=False`, `p3.shadow.enabled=False`. |
| 1.5 | `can_act()` is advisory and is not a capability | **VERIFIED** | With both gates open `can_act()` returns `True` while `execute()` still returns `acted=False`. `can_act` must never be read as permission. |
| 1.6 | Hermes bad signature rejected | **VERIFIED** | Garbage sig, wrong-bytes sig, tampered action, wrong key → all rejected. Via the nine-check path: `reason='BAD_SIGNATURE'`. |
| 1.7 | Replay rejected | **VERIFIED** | Same nonce presented twice against one `ReplayCache` → `ok=False, reason='REPLAY'`. `ttl_s`/`nonce` are inside the signed bytes, so a fresh timestamp cannot rescue a replay. |
| 1.8 | Expired / unsigned rejected | **VERIFIED** | `reason='EXPIRED'`, `reason='UNSIGNED'`. With no key configured the gate reports `unavailable:no_key` rather than claiming validity. |
| 1.9 | Four-agent identity distinguishable | **VERIFIED** | All four declared in config; each carries its own status tuple. Note: `distinct identity tuples = 2 of 4` — workbuddy and claude share `{CONNECTED, aep=true, verified=true}`. Distinguishable by **name + config_path + version**, not by status alone. |
| 1.10 | WorkBuddy native permission flow intact | **VERIFIED** | `FORBIDDEN_EVENTS = {"PermissionRequest"}`; workbuddy's installed events are `SessionStart, SessionEnd, UserPromptSubmit, PreToolUse, PostToolUse, Stop, Notification, PreCompact` — intersection with the forbidden set is **empty**. |
| 1.11 | JCC binds localhost only | **VERIFIED** | `BIND_HOST='127.0.0.1'`. `check_bind_host` raises `SystemExit` for `0.0.0.0`, `192.168.1.10`, `::`; accepts `127.0.0.1`, `localhost`, `127.0.0.2`. |
| 1.12 | No secret material in config | **VERIFIED** | `control` block holds only `token_env`; `signing` holds only `key_env` + `required`. No literal tokens or keys in either shipped config. |
| 1.13 | GREEN baseline port protected | **VERIFIED** | Non-GREEN instance asking for 4778 → refused. GREEN (`config/jcc.yaml`) → allowed. Guard fires **before** store, marker and socket (offsets: guard 3935 < marker 10137 < StateStore 15250 < uvicorn 16241). |
| 1.14 | Port-type confusion cannot bypass the port guard | **VERIFIED** | 22-case adversarial battery: every spelling meaning 4778 refused (`'4778'`, `"4778"`, `4778.0`, `' 4778 '`, `'+4778'`, `'4_778'`, `'04778'`, `4778.9`, `b'4778'`, custom `__int__`). Non-4778 values not over-refused (`4779`, `4779.9`, `'0x1E52'`, `'not-a-port'`, `None`). Host-independence proven by stubbing `getaddrinfo` to simulate a host where a service name means 4778. |

### P3 (shadow supervision): observe, record, never actuate

| # | Contract | Status | Evidence |
|---|---|---|---|
| 3.1 | Jev scores; Policy emits directives | **VERIFIED** | `Directive` is produced by `policy`; the shadow plane subscribes to engine events and records. The Sentinel's dispatch port is the inert `ShadowActuationPort`. |
| 3.2 | Thresholds are per-agent, resolved once | **VERIFIED (code)** / **UNVERIFIED (config values)** | `run._wire_shadow` builds one `ThresholdsConfig` and injects the *same object* into engine and Sentinel. The four shipped `per_agent` nodes are all empty, so the effective surface is `default` only. |
| 3.3 | Signature `required` flag is false in shipped config | **VERIFIED** | `config/jcc-v1.3.yaml` → `signing.required: false`. The kernel reports `unavailable:no_key` honestly rather than treating unsigned as valid. |
| 3.4 | `verify_risk` is retired and rejected by the kernel | **UNVERIFIED** | The config comment asserts the kernel rejects a config still carrying `verify_risk`. **I did not execute that rejection.** Do not cite this row as proof. |

### P8+ (post-freeze): not in this release

| # | Item | Status | Note |
|---|---|---|---|
| 8.1 | `run.py` splits by lifecycle stage | **DEFERRED by lead** | File is **450 lines** (not 796 — an earlier figure in circulation was wrong). The "one file owns the whole lifecycle" concern is recorded as a P8+ refactor item. **Do not restructure before freeze**: it would invalidate every pinned hash. |
| 8.2 | Real actuation implementation | **NOT PRESENT** | `real_actuation_not_implemented`. Any future work here is a new capability, not a config flip, and re-opens gate 1.1. |

---

## 2. Non-negotiables (breaking one is a blocker)

| # | Non-negotiable | Status | Why it is non-negotiable |
|---|---|---|---|
| N1 | **GREEN `127.0.0.1:4778` is read/observe only** — never stop, kill, rewrite, migrate or mutate | **VERIFIED (guard)** / **UNVERIFIED (restore path)** | The port guard holds. The **actual restore action has never been executed** by me — it mutates the live GREEN runtime, which I was instructed not to touch. |
| N2 | **Never bind GREEN's port from a product instance** | **VERIFIED** | See 1.13/1.14. Identity is the config file that was loaded, never the port value. |
| N3 | **JCC binds loopback only** — never `0.0.0.0` or a routable address | **VERIFIED** | See 1.11. A refusal, not a warning. |
| N4 | **No automatic side-effect actuator exists or is enabled in P0–P2** | **VERIFIED** | See 1.1. Strongest form: the code path is absent, not merely disabled. |
| N5 | **Secrets are env-only; never stored in config** | **VERIFIED** | See 1.12. |
| N6 | **`session_id` outranks `cwd`; cwd is never the sole identity** | **UNVERIFIED** | Contract rule 7. I have **not** measured this. Requires a session-isolation test I did not run. |
| N7 | **UI is not a source of truth** | **UNVERIFIED** | Contract rule 9. Not measured. |
| N8 | **Every phase carries rollback evidence** | **UNVERIFIED** | Contract rule 10. I did not audit rollback evidence per phase. |
| N9 | **Full transcript stored locally; truncate/redact/hash before external judgement** | **UNVERIFIED** | Contract rule 8. Partially visible in `tools/release_truth.py` redaction code, which I did not exercise. |
| N10 | **fixture → normalizer → installer** ordering | **UNVERIFIED** | Contract rule 6. Not measured. |

**Read this honestly:** five of the ten core contract rules (N6–N10) are **UNVERIFIED by me**. They are not thereby false. They are not thereby proven. Anyone citing this file as "the JCC contract holds" is overclaiming.

**Two related facts that make N6 weaker than "merely unmeasured"** (measured, see `docs/CONSISTENCY_MAP.md` Fact 3):

- The `status` fields in `agents.capabilities` are **assertions with no read-back**. On this host `~/.claude` and `~/.hermes` are **ABSENT**, `~/.codex` exists, and `~/.workbuddy/settings.json` has **no `hooks` key at all** — yet the config asserts three agents `CONNECTED` and one `DEGRADED`. The record is not connected to reality.
- Therefore **no release gate should cite those `status` fields as evidence.** The "four-agent identity can be distinguished" gate is satisfied by **name + `config_path` + `version`** (see 1.9), which is a property of the configuration, not a claim about the live agents.

---

## 3. Status snapshot (the drift alarm)

**This table is a point-in-time reading. If the snapshot below disagrees with the code, the code wins.**

| Field | Value at authoring |
|---|---|
| HEAD | `3af548b` |
| Branch | `release/jcc-v1.3-final` |
| Python | 3.13.14 (`.venv\Scripts\python.exe`) |
| `run.py` | 450 lines, guard present with `getaddrinfo` resolution |
| A8 pins | 12 pins, 0 mismatched vs HEAD blob (measured at `c8ab3cc`; **forge2 has since edited `run.py`/`config.py`/`server.py`, so this will drift again — lead owns the freeze re-baseline) |
| `test_cr002_green_port.py` | 24 passed |
| `test_p3_acceptance_gate.py` | 32 passed |
| signing/identity/adapter suites | 109 passed |
| GREEN 4778 / product 4779 / UI 4780 | **no listener at measurement time** |
| `artifacts/release/manifest.json` | **stale on 6 of 12 entries**, `frozen_at=c2c8367`, 84 commits behind → gate leg P-02 replays **FAIL**. Re-emit deferred by lead (decision #10) because `config/jcc.yaml` is in the set and CR-003 will change it. **Re-emit in the same commit as the next pin re-baseline.** |
| `docs/CONSISTENCY_MAP.md` | 4 facts measured. Fact 4 is **NOT CONVERGED**: `artifacts/security/freeze_check.py` (classified PRODUCT gate code) carries live `F:/jev` literals and **cannot run** — `ModuleNotFoundError: final_gate_run`. Causation isolated to that one file; three other broken gate files have unrelated causes. Only **1 of 4** facts has a declared alignment mechanism. Regenerate before citing any of its numbers. |

**Drift rule:** if you change any file named in a pin, in the manifest, or in the snapshot above, this document is stale until re-measured. Do not update the numbers to match what you expect — re-run the measurement.

---

## 4. How these numbers were produced

| Fact set | Command |
|---|---|
| 19 contract rows (actuation, loopback, secrets, ports) | `runtime/_verify_cr002/d1_contract_facts.py` — 19 rows, 0 violations |
| Signing / replay / tamper (12 rows) | `runtime/_verify_cr002/d1_signing_gate.py` — 12 rows, 0 problems |
| Four-agent identity, WorkBuddy permission | `runtime/_verify_cr002/d1_gate_items.py` |
| Port-type adversarial battery (22 cases) | `runtime/_verify_cr002/forge_fix_attack.py`, `forge_getaddrinfo_attack.py` |

All scripts write only under `runtime/_verify_cr002/`. No product file was modified to produce this table.

**Caveat on method:** several rows were first measured with a stubbed `socket.getaddrinfo` and produced a **false failure**, because my stub did not strip whitespace the way the real one does. The corrected measurement is what is recorded. A stub-based test must reproduce the semantics of the thing it replaces.
