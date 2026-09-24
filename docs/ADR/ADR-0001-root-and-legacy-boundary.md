# ADR-0001 — Root Directory and Legacy Boundary

- **Status:** Proposed
- **Date:** 2026-09-24
- **Owner:** Atlas (jcc-protocol-architect)
- **Supersedes:** none
- **Superseded by:** none

## Context

`F:\jev` is the single project root and the only source of truth (VERIFIED). Inside it sits `F:\jev\jev-cascade`, a pre-existing, self-contained trading-terminal project (Python/FastAPI backend + Next.js/TypeScript frontend). It already implements capabilities analogous to the target JCC layer chain: a Jev client (`backend/jev/`), an append-only event store (`backend/events/`), a risk engine (`backend/risk/`), a paper broker (`backend/execution/`), an evidence/fingerprint pipeline (`backend/features/`), a combined API/WS/simulation server (`backend/api/`), and a Next.js terminal (`frontend/`).

The PHASE A audit (VERIFIED) established that:
- The target JCC monorepo layout (`apps/`, `packages/`, `adapters/`, `facades/`, `integrations/`, …) is **100% greenfield** — none of those directories exist at `F:\jev`.
- `jev-cascade` violates the four-plane separation mandated by the core contract: `backend/api/server.py` is a monolith mixing Control + Telemetry + Execution, binds `0.0.0.0:8000` (all interfaces), uses open CORS (`allow_origins=["*"]`), and exposes unauthenticated control endpoints (`POST /api/kill_switch`, WS control).
- `jev-cascade` is business code that must NOT be modified (read-only legacy subject).

## Decision

**Classify `F:\jev\jev-cascade` as `LEGACY_REFERENCE`.** The new JCC implementation must have **no implicit coupling** to it — no imports, no shared modules, no inherited runtime configuration.

### Reuse policy

**ALLOWED — ideas and patterns only, NEVER direct code import:**
- Algorithm ideas (Kelly sizing, risk math, feature/indicator computations).
- Test ideas (golden-case eval harness, injection-corpus unit tests).
- Jev client experience: `jev-1.13.0` model pin, `/v1/jev/{model}/decide` contract, per-agent batching, circuit breaker, degraded-mode fallback.
- Evidence fingerprint approach: `build_jev_state()`, deterministic `state_fingerprint`, `sanitize_external_state()`.
- Risk-engine concepts: `PASS / OVERRIDE / ABORT / HALT` verdicts, confidence bands.

**FORBIDDEN — direct reuse prohibited:**
- `server.py` monolith (combined REST + WebSocket + Simulation control/telemetry/execution).
- `0.0.0.0` default bind (all-interfaces exposure).
- Open CORS (`allow_origins=["*"]`).
- Unauthenticated control endpoints (`/api/kill_switch`, WS control messages).
- `PaperBroker` actuator semantics (a paper-trade broker is NOT the JCC external-agent actuator).

## Consequences

- **Positive:** Clean stack decoupling; JCC owns its own architecture (TS monorepo per target layout); legacy risk patterns cannot leak into the new control plane.
- **Negative / cost:** Conscious porting effort required to transfer the ALLOWED patterns; risk of duplication if patterns are not deliberately re-implemented.
- **Constraint preserved:** `jev-cascade` remains read-only reference; it is never edited by JCC work.

## Verification labels

- `F:\jev` = single root: **VERIFIED**.
- `jev-cascade` is Python + Next.js with the modules listed: **VERIFIED** (PHASE A audit).
- Target JCC layout is greenfield: **VERIFIED** (PHASE A audit).
- Forbidden items (0.0.0.0 bind, open CORS, unauthenticated control): **VERIFIED** (from `DEPLOYMENT.md`, `SECURITY.md`, `ARCHITECTURE.md`).
