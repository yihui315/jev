# JCC v1.2 Visual Command Center — Operator Guide

**What this is:** a SECOND, fully isolated JCC Core instance that serves the 3D
Visual Command Center. It runs ALONGSIDE the frozen GREEN service and never
touches it.

## Endpoints (all loopback-only, `127.0.0.1`)

| Surface | URL | Notes |
|---------|-----|-------|
| **Visual Command Center (production UI)** | http://127.0.0.1:4780 | static SPA served by `serve_visual.py` |
| **Visual UI DEV/debug** | http://127.0.0.1:4781 | `npm run dev` (Vite) — local debugging ONLY, never production |
| **v1.2 Control API** | http://127.0.0.1:4779/api/health | `python -m jcc_core.run` (config `config/jcc-v1.2.yaml`) |
| **GREEN (frozen v1.0.1 — DO NOT TOUCH)** | http://127.0.0.1:4778 | pid 7588; stop/restart/kill/write all forbidden |

## Everyday commands (fail-closed, GREEN-isolated)

```powershell
# full bring-up + verify (flow A-K)
.\scripts\jcc-v1.2-up.ps1

# status of the whole v1.2 surface + GREEN protection
.\scripts\status-jcc-v1.2.ps1

# start / stop / restart pieces
.\scripts\start-jcc-v1.2.ps1                 # API 4779
.\scripts\stop-jcc-v1.2.ps1                  # API 4779
.\scripts\restart-jcc-v1.2.ps1               # API 4779
.\scripts\start-jcc-v1.2-visual-ui.ps1       # UI 4780
.\scripts\stop-jcc-v1.2-visual-ui.ps1        # UI 4780  (verified, never touches API/GREEN)

# write ownership markers for an already-running live runtime
.\scripts\mark-jcc-v1.2-runtime.ps1

# durable runtime (host-side, runs across reboots)
.\scripts\jcc-v1.2-register-tasks.ps1         # operator runs on HOST (see DURABLE_RUNTIME_TASK_SCHEDULER.md)
```

## Make it durable across reboots

The agent session reclaims ordinary child processes when a command ends. For a
service that survives reboots, register the Windows Task Scheduler tasks on the
**host** (as Administrator):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\jcc-v1.2-register-tasks.ps1
```

This creates `JCC-v1.2-API` (4779) and `JCC-v1.2-VisualUI` (4780), both
local-only, with restart-on-failure. (Registration is `PENDING_EXTERNAL_ACTION`
from inside the agent sandbox because `schtasks.exe` is blocked there.)

## Activation Gate (design-gated, off by default)

The Visual UI is fully built and live, but it does **not** drive agent actuation
yet. Actuation is `enabled: false`, the Jev provider is `mock`, and the control
token is unset — fail-closed by design. To activate (only with explicit
operator decision + a real provider key + a control token):

1. set `actuation.enabled: true` in `config/jcc-v1.2.yaml`
2. set `jev.provider: real` + `JEV_API_KEY`
3. set `JCC_CONTROL_TOKEN` (server enforces it via `require_control_token`, `hmac.compare_digest`)

Until then the UI is a read-only live view — safe by construction.

## Safety invariants

- GREEN 4778 (pid 7588) is never examined or touched by any v1.2 script.
- Every stop/start verifies marker + pid + command line + port owner + ancestry
  before signalling anything (fail-closed).
- A port that answers but is not provably ours is reported as foreign and left alone.
