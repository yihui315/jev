# ADR-0007 — P2 Addendum: Secret-Redaction Boundary and Shared Redaction Kernel

- **Status:** **ACCEPTED** — team-lead (Orion) acceptance recorded 2026-09-24, satisfying condition (2) of the P2 architecture gate (`artifacts/acceptance/P2_ARCH_CONFORMANCE_ATLAS.md`, DECISION). Addendum to ADR-0002 / ADR-0003 / ADR-0005.
- **Date:** 2026-09-24
- **Owner:** Atlas (jcc-protocol-architect)
- **Supersedes:** none
- **Superseded by:** none
- **Triggered by:** P2 architecture-conformance gate (`artifacts/acceptance/P2_ARCH_CONFORMANCE_ATLAS.md`), judging commit `19de2ff`; security findings C1/C2/C3 (`55237c4`), FINDING-D (`f81c962`), F-1 (task #35).
- **Does NOT re-open:** ADR-0002/0003/0004/0005/0006 (frozen). This addendum only *extends* them with an explicit rule that P2 introduced implicitly.

## Context

P2 added a value-pattern secret scrub (`scrub_value_secrets`/`scrub_str`) and calls it at three places:

1. inside `CoreEngine.ingest()` — `packages/jcc_core/core.py:85-87`;
2. at the emit boundary — `integrations/_common/jcc_emit.py:78-80`;
3. at the spool persistence boundary — `integrations/_common/jcc_emit.py:58`.

Two facts force an explicit architectural rule:

- **`/api/assess` bypasses the adapters entirely.** `packages/jcc_core/server.py:133-137` does `env = AEPEnvelope(**body); engine.ingest(env)` — there is **no adapter** in that path, so the adapter-side `redact()` never runs. The scrub inside `core.ingest()` is therefore the *only* protection for that ingress. (Confirmed by the F-1 analysis: a depth-12 secret POSTed to `/api/assess` reached `jcc.db` at the pre-fix commit.)
- **The layer chain is directional.** ADR-0002 (`:11,:13`) fixes `Agent → Adapter → AEP → State Store → Evidence → Jev → Policy → Directive → Actuator`. In that chain the *Adapter* plane depends on the *AEP contract*; the *core/telemetry* plane must **not** depend on the adapter plane.

However, `packages/jcc_core/core.py:25` imports `from .adapters._common import scrub_value_secrets`. That makes the core (control/telemetry) plane depend **downward-free but sidebar** on the Execution/Adapter plane — a dependency-direction inversion relative to the frozen chain.

## Decision

**D-1. Redaction is a persistence-boundary guarantee, not an adapter-only concern.**
The Telemetry-Evidence plane (here: `CoreEngine.ingest`) MUST scrub secrets at the single chokepoint every ingress passes through, BEFORE anything is persisted (`core.py:85-87`, ahead of `store.add_event` at `:105` and `add_evidence` at `:120`). This is the correct architecture and is **not** drift: an ingress that carries no adapter (`/api/assess`) must still be covered. Adapter-side `redact()` remains defense-in-depth.

**D-2. The scrub also runs at the emit and spool boundaries.**
`jcc_emit.normalize_event` (`:78-80`) and `jcc_emit.spool` (`:58`) scrub so that the dry-run print, the HTTP body, and the on-failure spool are clean even if the core is unreachable. The spool is a persistence boundary (FINDING-D) and MUST be scrubbed.

**D-3. The redaction kernel is a lower-layer shared kernel — it must NOT live under `adapters/`.**
`scrub_str`, `scrub_value_secrets`, `redact`, and their constants (`MAX_TEXT_LEN`, `MAX_SCRUB_DEPTH`, `DEPTH_LIMIT_MARK`, the regex tables) are a **neutral shared kernel** consumed by both the Adapter plane and the telemetry/emit boundaries. The currently-correct architecture is:

- **Required (target):** the kernel is hoisted to a neutral module (e.g. `packages/jcc_core/redaction.py`), and both `adapters/*` and `core.py` import *downward* from it. Adapter and core become **siblings**; neither imports the other.
- **Accepted-for-P2 (drift, documented):** the kernel sits in `packages/jcc_core/adapters/_common.py` and `core.py:25` imports it. This is a *known* dependency-direction deviation, tolerated because it is behaviourally correct and hoisting is a mechanical move; it MUST be scheduled before the module is treated as a stable boundary, and MUST NOT be propagated to any new consumer.

**D-4. The depth cap is a collapsing (fail-safe) guard, never a raw passthrough.**
`MAX_SCRUB_DEPTH = 8`; beyond it the sub-tree collapses to `DEPTH_LIMIT_MARK = "[DEPTH_LIMIT]"` — the *same* marker `redact()` uses — so `scrub_value_secrets` and `redact` behave identically. Data deeper than the cap is lost, but never persisted raw. (F-1 fix; the committed state at `19de2ff` still returned the raw value and is therefore non-conformant until the fix lands.)

**D-5. Statement of the one identity exception (hermes gateway liveness).**
ADR-0003's "no `session_id` ⇒ `UnidentifiableEvent`" rule has exactly one deliberate exception: `HermesAdapter._normalize_gateway_state` synthesizes a *host-level* liveness identity `hermes-gateway[:<pid>]` (`adapters/hermes.py:201`) because a gateway heartbeat has no native session. This identity is **synthesized, never derived from `cwd`**, and is asserted distinct from `cwd` (`tests/test_adapters_contract.py:171`). Any future synthesised identity MUST also be cwd-independent and documented here.

**D-6. `project` is metadata, never identity.**
Derivations such as `codex.py:240` (leaf directory of `cwd` → `project`) are metadata-only. `project` MUST NOT be used as, or promoted to, the primary key. Identity stays `session_id` (ADR-0003).

## Consequences

- **Positive:** the redaction guarantee is expressed where it actually holds (the ingest chokepoint) instead of only where it is convenient (the adapters); the drift is named with a concrete remediation path; the hermes/codex metadata nuances are recorded rather than silently tolerated.
- **Negative / cost:** until D-3 is executed, `core` carries a sideways dependency on `adapters`; a future adapter refactor must not assume it can move `_common` freely. The depth cap is lossy for pathological payloads (accepted, fail-safe).
- **Constraint preserved:** `session_id` > `cwd`; unknown evidence is never auto-marked safe; Jev scores only, Policy directs; visual plane is not source of truth; P0–P2 no actuation.

## Verification labels

- `/api/assess` bypasses adapters (`AEPEnvelope(**body); engine.ingest`): **VERIFIED** (`packages/jcc_core/server.py:133-137`).
- Redaction occurs inside `ingest()` before persistence: **VERIFIED** (`packages/jcc_core/core.py:85-87` ahead of `:105`,`:120`).
- Spool/emit boundaries scrub: **VERIFIED** (`integrations/_common/jcc_emit.py:58,78-80`).
- `core` imports the kernel from `adapters._common` (dependency inversion): **VERIFIED** (`packages/jcc_core/core.py:25`).
- Depth cap collapses (not passthrough): **UNVERIFIED at the judged commit `19de2ff`** (committed `_common.py` still returns the raw value past depth 8); the collapsing version exists only as an uncommitted working-tree edit (task #35).
- hermes gateway synthesised identity is cwd-independent: **VERIFIED** (`adapters/hermes.py:201`, `tests/test_adapters_contract.py:171`).
