# ADR-0004 — Supervision and Actuation

- **Status:** Proposed
- **Date:** 2026-09-24
- **Owner:** Atlas (jcc-protocol-architect)
- **Supersedes:** none
- **Superseded by:** none

## Context

The core contract is explicit: **P0–P2 no automatic actuation** (core contract #4); **Jev only scores, Policy produces the directive** (core contract #3). `jev-cascade` conflates these (its `RiskEngine` both gates and the `PaperBroker` acts). The new design must separate scoring, deterministic policy arbitration, directive signing, and actuation — and keep actuation disabled through P0–P2.

> Sentinel absent this session; schema/Jev-supervisor compatibility is owned by Atlas in this ADR.

## Decision

- **Shadow supervision (P0–P3):** Jev runs in shadow — it produces scores/probabilities only. No actuation.
- **Deterministic Policy (`packages/policy-engine`):** arbitrates Jev scores + Evidence-Builder output + risk gates, and emits a **SIGNED directive**: unique `directive_id`, cryptographic signature, `timestamp`, `session_id`, target `actuator`, `params`, `policy_version`.
- **Actuator (`packages/actuation`):** consumes **only policy-signed directives**. It MUST verify the signature and **reject** any unsigned, malformed, or forged directive. **No direct model actuation** — Jev cannot actuate; only Policy-authored, signed directives reach an actuator.
- **Actuation disabled (P0–P2):** any real actuation requires human approval (charter) and is P3+ shadow-only first. The actuator layer ships with actuation OFF.
- **Supervisor (`packages/supervisor`):** the arbiter that validates directives against policy and the four-plane boundaries (ADR-0002). It MUST reject directives that bypass the Evidence or Policy planes, and it is the integration point for the future Jev supervisor.
- **Control-plane exposure mandate (reinforces ADR-0002, P1-blocking):** the supervisor's control endpoints (and all JCC control traffic) MUST bind localhost-only (`127.0.0.1`), require authentication, and use an explicit CORS allowlist — never `0.0.0.0`, never `allow_origins=['*']`, never unauthenticated control.

## Consequences

- **Positive:** Clear single actuation authority (Policy); cryptographic non-repudiation; safe containment for P0–P2; clean upgrade path to P3 shadow.
- **Negative / cost:** Signing/verification overhead on every directive; Policy engine is a hard dependency for any actuation; requires disciplined plane enforcement.
- **Constraint preserved:** Jev scorer-only; P0–P2 no automatic actuation; human approval for high-risk side effects.

## Verification labels

- P0–P2 no-auto-actuation + Jev-scorer-only: **VERIFIED** (core contract #3, #4).
- External-agent interfaces (for future adapters): **UNVERIFIED** (P2).
