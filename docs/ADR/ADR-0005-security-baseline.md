# ADR-0005 — Security Baseline (Host Exposure Findings)

- **Status:** Proposed
- **Date:** 2026-09-24
- **Owner:** Atlas (jcc-protocol-architect) — findings relayed from Aegis (jcc-security-engineer)
- **Supersedes:** none
- **Superseded by:** none

## Context

The JEV COMMAND CENTER charter requires a **local-first control plane**. During the PHASE A audit and the security review, two host-exposure conditions were identified on the machine hosting `F:\jev`. This ADR's scope is strictly to **record** them as findings. **No mitigation or modification is performed here** — remediation is the STEP 6 containment proposal, owned by Aegis/Harbor.

## Decision

Record the following as **HOST EXPOSURE FINDINGS** (document-only):

- **FINDING-1 — `jev-cascade` control plane exposure (VERIFIED).**
  `F:\jev\jev-cascade\backend\api\server.py` HARDCODES `uvicorn.run(app, host="0.0.0.0", port=8000)` (all interfaces, not a CLI flag), uses `allow_origins=["*"]`, and exposes unauthenticated control endpoints (`POST /api/kill_switch`, WebSocket control messages). Source: `DEPLOYMENT.md`, `SECURITY.md`, `ARCHITECTURE.md`.
  *Classification:* legacy-reference exposure (ADR-0001). The new JCC control plane MUST bind **localhost-only (127.0.0.1)** with authenticated, CORS-restricted endpoints (enforced by ADR-0002 / ADR-0004). It is **forbidden** to inherit `server.py`'s bind/CORS/control patterns.

- **FINDING-2 — Ollama exposure (VERIFIED).**
  Ollama binds `0.0.0.0:11434` (all interfaces), unauthenticated. Source: Aegis direct observation (PHASE A netstat `0.0.0.0:11434 LISTENING PID 1532 = ollama.exe`), corroborated by `C:\Users\Administrator\.ollama\config.json` (no host override) and absence of repo-local `OLLAMA_HOST`. Team-verified VERIFIED (label upgraded from COMMUNITY-VERIFIED per Aegis, security owner).
  *Classification:* external-dependency exposure. The JCC design MUST NOT assume Ollama (or any model server) is network-isolated; treat it as a potentially reachable service and keep all JCC control traffic localhost-only.

Both findings are **legacy/external** exposures. They are **not modified or mitigated** within this ADR. They are handed to the STEP 6 containment proposal.

## Consequences

- **Positive:** Explicit, attributed risk visibility; clean separation of "findings" vs "fixes"; preserves freeze integrity.
- **Negative / cost:** Exposure persists until STEP 6; risk that new code accidentally replicates FINDING-1 patterns (mitigated by ADR-0001 forbidden list and ADR-0002 localhost-only mandate).
- **Constraint preserved:** This ADR documents only; it performs no change to `jev-cascade`, Ollama, or any running service.

## Verification labels

- FINDING-1 (0.0.0.0:8000, open CORS, unauthenticated control): **VERIFIED** (in-repo docs).
- FINDING-2 (Ollama 0.0.0.0:11434): **VERIFIED** (Aegis direct observation + config corroboration).
