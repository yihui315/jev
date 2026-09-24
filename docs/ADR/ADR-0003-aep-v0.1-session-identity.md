# ADR-0003 — AEP v0.1 Contract and Session Identity

- **Status:** Proposed
- **Date:** 2026-09-24
- **Owner:** Atlas (jcc-protocol-architect)
- **Supersedes:** none
- **Superseded by:** none

## Context

JCC needs a unified Agent Event Protocol (AEP) to carry events from the four external agents (WorkBuddy / Claude Code / Codex / Hermes) through Adapter → State Store → Evidence Builder. The core contract mandates (core contract #7): **`session_id` takes priority over `cwd`; `cwd` MUST NOT be the sole identity.** We also need dedup and a safe-default stance on ambiguous evidence.

Current state (VERIFIED, PHASE A): no `session-center`, no AEP package, and no `.git` at `F:\jev`. The four agents are present as CLIs/configs but their concrete event shapes are UNVERIFIED.

## Decision

**AEP version `aep/v0.1`** with these invariants:

- **`session_id`** — primary identity. Assigned at session creation (UUIDv7 or comparable). `cwd` is recorded as **metadata only**, never the primary key.
- **`event_id`** — unique per emitted event. **`delivery_id`** — unique per delivery attempt (supports at-least-once delivery + redelivery). Consumers MUST dedup on `(session_id, event_id)` and on `delivery_id`.
- **"Unknown evidence ≠ safe / completed."** An event whose required evidence fields are missing or unparseable is routed to a **quarantine / await** state. It is **never** auto-marked `safe` or `completed`. Default-deny on ambiguity.
- **External-agent interfaces:** WorkBuddy PRESENT (**VERIFIED**); Claude Code / Codex / Hermes PRESENT as CLIs/configs (**VERIFIED**) but their AEP payload mappings are **UNVERIFIED** — concrete adapter mapping is defined in P2 and must not be fabricated here.

The AEP schema is versioned and MUST remain forward-compatible with the future Jev supervisor (`packages/supervisor`) per ADR-0002.

## Consequences

- **Positive:** Robust identity (no cwd-only reliance), reliable dedup, fail-safe on ambiguity, clean versioning for evolution.
- **Negative / cost:** Adapters must implement normalization + dedup; schema may evolve (hence explicit `aep/v0.1` versioning).
- **Constraint preserved:** `session_id` > `cwd`; safe-default on unknown evidence.

## Verification labels

- `session_id`-over-`cwd` requirement: **VERIFIED** (core contract #7).
- Four agents present (CLI/config): **VERIFIED** (PHASE A).
- Concrete per-agent AEP payload shapes: **UNVERIFIED** (P2 adapters).
- No existing AEP/session-center: **VERIFIED** (PHASE A audit).
