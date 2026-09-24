# ADR-0002 — Four-Plane Architecture

- **Status:** Proposed
- **Date:** 2026-09-24
- **Owner:** Atlas (jcc-protocol-architect)
- **Supersedes:** none
- **Superseded by:** none

## Context

The JCC core contract requires strict **Visual / Control / Telemetry / Execution** separation, and the principle that "UI is not source of truth" (core contract #9). `jev-cascade` violates this (its `server.py` mixes all concerns). The target layer chain is:

`Agent → Adapter → AEP → State Store → Evidence Builder → Jev Scores → Deterministic Policy → Signed Directive → Actuator`

A new architecture must (a) enforce the four planes, (b) realize the layer chain, and (c) keep Jev as scorer-only while Policy is the sole actuation authority.

> Note: Sentinel (schema/Jev-supervisor compatibility reviewer) is absent this session; that responsibility is folded into this ADR's ownership (Atlas).

## Decision

Adopt four explicit planes, each owned by distinct packages (no cross-plane monolith):

1. **Visual plane** — `apps/control-ui`, `integrations/jeview`, `facades/claw3d`. Render-only. Never source of truth.
2. **Control plane** — `packages/supervisor` (orchestration, session/state, policy arbitration). Consumes AEP, requests directives, gates actuation.
3. **Telemetry-Evidence plane** — `packages/state-store`, `packages/audit`, `packages/observability`, `integrations/otel`. Append-only event store, evidence builder, replay. This plane is the authoritative record.
4. **Execution plane** — `packages/actuation`, `adapters/*`. Actuators consume **policy-signed directives only**.

5. **Control-plane exposure mandate (P1-blocking):** The JCC control plane MUST bind localhost-only (127.0.0.1), require authentication on ALL control endpoints, and restrict CORS to an explicit allowlist — never 0.0.0.0, never allow_origins=['*'], never unauthenticated control endpoints.

**Layer-chain mapping:**
- **Agent** — the four external agents: WorkBuddy / Claude Code / Codex / Hermes. All four are **PRESENT** as CLIs/configs (**VERIFIED**, PHASE A) but their AEP payload shapes are **UNVERIFIED** (adapters are P2; do not fabricate).
- **Adapter** — `adapters/{workbuddy,claude,codex,hermes,generic-http}` (greenfield).
- **AEP** — `packages/protocol`, contract `aep/v0.1` (see ADR-0003).
- **State Store** — `packages/state-store`.
- **Evidence Builder** — `packages/audit`.
- **Jev Scores** — external `jev-1.13.0`; **probabilities only**.
- **Deterministic Policy** — `packages/policy-engine`; produces a **unique signed directive**.
- **Signed Directive** — unique id + signature + timestamp + `session_id` + target actuator + params + `policy_version`.
- **Actuator** — `packages/actuation`; consumes signed directive only (see ADR-0004).

**Non-negotiable principles:** Jev outputs probabilities ONLY; Policy produces the directive; Actuator consumes the policy-signed directive. No direct model actuation.

**Schema / Jev-supervisor compatibility (Atlas-owned this session):** the AEP schema MUST be versioned (`aep/v0.1`) and forward-compatible with a future Jev supervisor (`packages/supervisor`). The supervisor subscribes to the Telemetry-Evidence plane and is the arbiter that validates directives against policy and plane boundaries; it MUST reject directives that bypass Evidence/Policy planes.

## Consequences

- **Positive:** Safety containment via hard plane boundaries; clear ownership; enables independent scaling/replacement of planes.
- **Negative / cost:** More packages to build; requires discipline to avoid a new monolith; adapters (P2) must implement the AEP normalization.
- **Constraint preserved:** Visual plane explicitly non-authoritative; Jev scorer-only; Policy sole actuation authority.

## Verification labels

- Four external agents present as CLIs/configs: **VERIFIED** (PHASE A).
- External-agent AEP interfaces: **UNVERIFIED**.
- Four-plane requirement: **VERIFIED** (core contract).
