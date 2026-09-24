# HOST_SECURITY_CONTAINMENT_PLAN.md

- **Status:** PROPOSAL ONLY — **DO NOT APPLY**. Execution requires separate explicit user authorization + a dedicated change window.
- **Date:** 2026-09-24
- **Owner:** Aegis (jcc-security-engineer) + Harbor (jcc-test-sre-engineer)
- **Authoring:** Orion (lead integration of Aegis/Harbor postures from P0.5 STEP 3 review)
- **Context:** P0.5 STEP 6. Records containment options for the two HOST EXPOSURE FINDINGS documented (not mitigated) in ADR-0005. Neither finding is modified by this plan — it is a proposal for future remediation.

## Findings (both VERIFIED)

- **FINDING-1 — `jev-cascade` control plane exposure (VERIFIED).**
  `F:\jev\jev-cascade\backend\api\server.py` HARDCODES `uvicorn.run(app, host="0.0.0.0", port=8000)`, uses `allow_origins=["*"]`, and exposes unauthenticated control endpoints (`POST /api/kill_switch`, WebSocket `/ws`). Legacy-reference exposure. Source: in-repo `DEPLOYMENT.md` / `SECURITY.md` / `ARCHITECTURE.md`, confirmed by Aegis re-read of server.py (L206 / L41-42 / L172-176 / L182-201).

- **FINDING-2 — Ollama exposure (VERIFIED).**
  Ollama binds `0.0.0.0:11434` (all interfaces), unauthenticated. VERIFIED by Aegis direct PHASE A observation (`netstat`: `0.0.0.0:11434 LISTENING PID 1532 = ollama.exe`) + config corroboration (no `OLLAMA_HOST` repo-local override; `C:\Users\Administrator\.ollama\config.json` has no host override → default 0.0.0.0:11434). External-dependency exposure.

## Containment options (proposal — pick per future authorization)

### FINDING-1 (jev-cascade 0.0.0.0:8000)
- **Option A (preferred if legacy code stays read-only):** host/network-layer containment — launch the legacy service bound to `127.0.0.1` via a wrapper/launcher, or host firewall DROP inbound 8000 except loopback; front with an authenticated reverse proxy only if network access is genuinely required.
- **Option B (if code may change):** edit `server.py` → `host="127.0.0.1"`, `allow_origins=[known UI origin]` (drop `"*"+credentials`), add authn/authz to `POST /api/kill_switch` and `/ws` control.
- **Option C (JCC separation):** the new JCC control plane MUST be a SEPARATE localhost-only service (ADR-0002 bullet 5 / ADR-0004) — never inherit server.py's bind / CORS / unauthenticated-control patterns (ADR-0001 forbidden list).

### FINDING-2 (Ollama 0.0.0.0:11434)
- **Option A (preferred):** set `OLLAMA_HOST=127.0.0.1` (env) so Ollama binds loopback only.
- **Option B:** host firewall DROP inbound 11434 except loopback.
- **Option C (if network access required):** place Ollama behind an authenticated reverse proxy with strict CORS + network isolation (separate interface/VLAN).
- **Note:** JCC MUST treat any model server as untrusted-on-network and keep all control traffic on `127.0.0.1` (ADR-0005 classification).

## Fail-closed conditions
- Default-deny ingress to both ports.
- If a service cannot bind loopback, BLOCK it at host firewall rather than expose.
- No JEV/JCC component may trust an unauthenticated network-reachable model or control endpoint.
- No change to `jev-cascade` / Ollama / firewall is performed by P0.5; this plan is documentation only.

## Post-remediation verification (for future execution)
- `netstat -ano -p TCP` → expect `127.0.0.1:8000` and `127.0.0.1:11434` only; no `0.0.0.0` listeners on these ports.
- JCC control endpoints respond only on loopback with authentication.

## Authorization
This document is a PROPOSAL. Applying any option requires a separate, explicit user authorization and a dedicated change window. P0.5 does NOT execute it.
