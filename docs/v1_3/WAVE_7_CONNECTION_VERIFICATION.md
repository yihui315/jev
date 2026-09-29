# WAVE 7 — Agent Connection Verification

> v1.3 PRODUCT EDITION. Make "can JCC actually work with this agent?" a **measured**
> fact rather than an asserted one — and be explicit about the one thing v1.3
> genuinely cannot verify.
> Owner: Orion (design) / Relay (probe) / Forge (domain) / Atlas (API) / Aegis
> (capability honesty) / Prism (UI) / Metric (tests + gate).

## 1. The hole this Wave closes

The user's own words, twice, were: *"打开 JCC 后，不知道如何连接 WorkBuddy /
Claude / Codex / Hermes"*. v1.2.1 answered that with the AI CONNECTION CENTER: a
registry keyed by agent, with real probes (`connections.test_local_cli`,
`_adapter_probe`, `_aep_heartbeat_test`) driven by
`POST /api/connections/{agent}/test`.

v1.3 Wave 1 then introduced a **domain** `Connection` with a `Capability` table and
the gate:

```python
def is_control_verified(connection_id) -> bool:
    return get_capability(connection_id, CONTROL_VERIFIED) is not None
```

But nothing *measures* that capability. The only way a domain connection becomes
`CONTROL_VERIFIED` is:

```
POST /api/v1/domain/connections/{cid}/capability  {"kind": "CONTROL_VERIFIED"}
```

— i.e. a human **types** the value. So the single gate that permits effectful
action can be satisfied by an assertion with no evidence. That is exactly the
class of claim this project hunts: *a PASS with no evidence is not a PASS.*

There is also no way for a user to see **why** JCC believes a connection works, or
when it last checked.

## 2. The two rules this Wave hangs on

> **Rule 1 — A capability is MEASURED, not asserted.** Verification runs a real
> probe and records what the probe *observed*. The caller cannot state the
> outcome; it can only ask for a probe to be run.

> **Rule 2 — Claim only what you can prove, and say what you cannot.** v1.3 keeps
> actuation disabled, so it **never exercises a control path**. Therefore a v1.3
> probe **cannot** honestly yield `CONTROL_VERIFIED`, and it must not. The most a
> probe may claim is `ADAPTER_CONTRACT_LIVE` (we can see the agent and speak its
> adapter contract). `CONTROL_VERIFIED` remains reachable only by citing external
> evidence for a control path that was actually exercised.

Rule 2 is the interesting one, and it is deliberately self-limiting: a naive
"verification" feature would report green across the board. The honest feature
reports exactly the ceiling of what it did.

## 3. The contract

### 3.1 Probe (`packages/jcc_core/capability_probe.py`, new)

```python
@dataclass
class ProbeResult:
    kind: CapabilityKind          # the OBSERVED capability
    detail: str
    evidence: Dict[str, Any]      # the raw observation (sessions found, adapter smoke, ...)
    evidence_ref: Optional[str]   # sha256 of the canonical evidence JSON
    reason: Optional[str]         # why UNKNOWN, when it is UNKNOWN

def probe_connection(agent: str, *, domain=None, engine=None) -> ProbeResult: ...
```

Behaviour, per agent:

| Situation | Observed capability | Notes |
|---|---|---|
| `workbuddy` with ≥1 **live** session | `ADAPTER_CONTRACT_LIVE` | we can see it and the adapter can normalise it; **never** CONTROL_VERIFIED |
| `workbuddy` with sessions but none live | `TELEMETRY_ONLY` | stale/dead sessions are still evidence of contact |
| `workbuddy` with no session dir / no sessions | `UNKNOWN` | no evidence at all; reason recorded |
| `claude` / `codex` / `hermes` with a successful adapter smoke | `TELEMETRY_ONLY` | adapter contract parses; no live link observed |
| adapter smoke raises | `UNKNOWN` | reason = the exception class; never coerced to healthy |
| unknown agent | `UNKNOWN` | reason = `unknown_agent` |

**Invariant (asserted in tests):** `probe_connection` **never** returns
`CONTROL_VERIFIED`, for any input. This is the falsifier for "the probe over-claims".

### 3.2 Domain (`domain.py`)

- `set_capability` gains an `asserted: bool` distinction implicitly via
  `evidence_ref`: a capability with an `evidence_ref` is **measured**; without one
  it is **asserted**. Exposed via a helper `capability_provenance(cap)` →
  `"MEASURED" | "ASSERTED"`.
- New `record_probe(connection_id, result)` → writes the capability, sets
  `health` from the capability, and stores the evidence.
- `get_connection(cid)` already exists.

### 3.3 API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/api/v1/domain/connections/{cid}` | open | connection + capabilities with provenance |
| `POST` | `/api/v1/domain/connections/{cid}/verify` | token | **run a real probe and record what it observed** |

- The verify body may carry only `{probe?: "auto"}`. Supplying `kind` is
  **ignored** — the outcome is never caller-supplied. Asserted in tests.
- **Hardening:** `POST /api/v1/domain/connections/{cid}/capability` now **refuses**
  `CONTROL_VERIFIED` when no `evidence_ref` is supplied (422, with a message
  pointing at `/verify`). Recording the other kinds is unchanged. This closes the
  "assert control-verified" hole without changing who signs what.

### 3.4 UI (`ConnectionsView`)

- One card per domain connection: agent, health, last-probe time, and **capability
  chips with their provenance** — `measured` (with the evidence ref) vs `asserted`.
- A per-connection **"Verify now"** button (token-gated) that calls `/verify` and
  shows what the probe observed.
- `UNKNOWN` renders neutral and shows the probe's `reason`; it is never drawn as
  `CONNECTED`, and a chip is never green without `MEASURED` provenance.

## 4. Falsifiers

1. **The probe over-claims**: assert `probe_connection` never returns
   `CONTROL_VERIFIED` for any agent/state.
2. **The caller states the outcome**: POST `{kind:"CONTROL_VERIFIED"}` to `/verify`
   and assert the recorded capability is what the *probe* observed, not the body.
3. **An assertion counts as a measurement**: hand-set `CONTROL_VERIFIED` with no
   `evidence_ref` → **422**.
4. **UNKNOWN is coerced**: with no sessions, the recorded capability is `UNKNOWN`
   and carries a reason; it is never `CONTROL_VERIFIED`/`ADAPTER_CONTRACT_LIVE`.
5. **Verification writes without a human/token**: `/verify` without the control
   token → 401/403.

## 5. Boundaries held

- Actuation stays disabled; a probe **reads** sessions/adapter contracts and never
  dispatches.
- v1.3 cannot yield `CONTROL_VERIFIED` (Rule 2); the capability ceiling is stated
  in the API response and the UI.
- UNKNOWN never renders as connected.
- GREEN 4778 untouched.

## 6. One existing assertion had to MOVE, not be deleted

`tests/e2e/test_domain_api_v1_3.py::test_connection_and_capability_endpoints`
(Wave 1) asserted the *old* contract: `POST /capability {"kind":"CONTROL_VERIFIED"}`
→ **200**, and the gate opens. That is exactly the hole §3.3 closes.

The test was **narrowed, not weakened** — the same call now asserts:

1. bare `CONTROL_VERIFIED` → **422** and `control_verified is False` (the hole is
   closed, and a refused assertion opens nothing);
2. the same call **with** an `evidence_ref` → **200**, `provenance == "MEASURED"`,
   `control_verified is True` (**non-vacuity**: the route is alive, so the 422
   above is a narrowing of what counts as proof, not a dead endpoint).

Deleting the assertion would have hidden the change; asserting only 422 would have
made a disabled route indistinguishable from a hardened one. Both halves are
required, which is why the update is part of this Wave's contract rather than a
"test fix".

## 7. Evidence

Recorded at closeout on `feature/jcc-v1.2-visual-command-center`.

| Leg | Command | Result |
|---|---|---|
| Product gate | `python tools/v1_3_wave7_connection_gate.py` | **PASS 20/20** (first run, no retry) |
| Core + e2e | `pytest packages/jcc_core/tests tests/e2e` (fresh `--basetemp`) | **786 passed / 0 failed / 0 skipped** |
| Tools + harness | `pytest tools/tests` (fresh `TEMP`) | **247 passed / 0 failed** |
| A8 pin + inbound surface | `pytest test_p3_acceptance_gate.py test_server_surface.py` | **55 passed** |
| Frontend types | `tsc --noEmit` | clean |
| Frontend units | `vitest run` | **71 passed** (was 64; +4 status, +3 domainApi) |
| Frontend build | `vite build` | OK (scratch `dist_w7` removed) |
| Security gate | `python tools/v1_1_security_gate.py` / `--check` | `verdict=GO high=0 medium=0 ran=9/9`, `[check] OK` (sha `3fcb3f9f66d6eb5969083929`) |
| Protected GREEN 4778 | read-only `GET /api/health` | `ok` / policy `p3.0.0` / `actuation_enabled=false` / `localhost_only=true` — **untouched** |

Artifacts:

- gate evidence: `.workbuddy/scratch/v1_3_wave7_gate_20260929-114051.json`,
  sha256 `a368f93811a1d0886deabec5ca1873b299fb3b6cb47ebf30ac5b60571926b944`.
- A8 window: `V1.3-FREEZE-W-7-CONNECTION` in `docs/p3/A8_REBASELINE.json`
  (`server.py` `7845d2ad.. -> 09f9955c..`; the other 11 pinned files byte-identical).
- Route-surface counters: `OPEN_GET_ENDPOINTS` 26 → **27**; A11 POST set +1;
  `test_server_surface` declared-open +1.

### One regression was found and fixed at the contract level

The first full core+e2e run reported **785 passed / 1 failed**:
`tests/e2e/test_domain_api_v1_3.py::test_connection_and_capability_endpoints`
still asserted the pre-Wave-7 contract. It was narrowed per §6 (refused **and**
still-accepted), then the full run was repeated from scratch:
**786 passed / 0 failed**. The re-run is the verdict; the first, red run is
recorded here rather than dropped, because "it passed after I changed the test"
is only honest if the change is stated.
