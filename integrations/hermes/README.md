# JCC × Hermes telemetry — P2

**Status: CONNECTED** (official hook mechanism verified from the installed
source). Telemetry only; Hermes native safety is **not** bypassed.

## Mechanism (verified on this host)

- Hermes Agent **v0.4.0** (`C:\Program Files\Python312\Scripts\hermes.exe`),
  source `C:\Users\Administrator\evolution_labs\hermes-agent-camel`.
- `~/.hermes/config.yaml` present; gateway recorded in
  `~/.hermes/gateway_state.json` (`kind:"hermes-gateway"`, running).
- **Official hook mechanism** (read from the installed `gateway/hooks.py` + the
  shipped `website/docs/user-guide/features/hooks.md`):
  a hook is a directory `~/.hermes/hooks/<name>/` containing `HOOK.yaml`
  (`name`, `description`, `events`) + `handler.py` exposing
  `handle(event_type: str, context: dict)`.
  Events: `gateway:startup, session:start, session:end, session:reset,
  agent:start, agent:step, agent:end, command:*`.
  Hooks are **local trusted Python**; the documented "webhook" pattern is an
  **outbound** POST from the handler.

## Install / uninstall

Run from the repository root. `-JccRoot` is optional — the script derives the
checkout from its own location, so these commands work wherever the repo lives.

```powershell
powershell -ExecutionPolicy Bypass -File .\integrations\hermes\install.ps1      # installs ~/.hermes/hooks/jcc-telemetry/
powershell -ExecutionPolicy Bypass -File .\integrations\hermes\install.ps1 -DryRun
powershell -ExecutionPolicy Bypass -File .\integrations\hermes\uninstall.ps1    # moves the dir to a timestamped backup
```

> Hermes loads hooks at **gateway startup** — restart the gateway to activate.

## Transport

- Default (`JCC_MODE=loopback`): handler calls `jcc_emit.emit_event("hermes", ...)`
  → loopback `POST /api/assess` with `JCC_CONTROL_TOKEN`. No network exposure.
- Optional (`JCC_MODE=signed`): handler signs with
  `SecureWebhookReceiver.sender_headers` and POSTs to `JCC_HOOKS_URL`; the JCC
  receiver enforces **HMAC-SHA256 + timestamp window + delivery_id replay cache +
  body size limit + per-source session isolation** (see `../_common/README.md`).
  **Not mounted:** the JCC core server exposes **no** inbound receiver route
  (`/api/hooks` → 404); `SecureWebhookReceiver` is a hardened library used
  in-process/tests today. Signed mode therefore requires the deployment to host
  the receiver (wiring it into the server is a later phase) — do not assume a
  live JCC endpoint exists.

## Rollback

Install backs up any pre-existing `HOOK.yaml`/`handler.py` to
`*.jcc-backup-<timestamp>`; uninstall verifies the manifest declares
`name: jcc-telemetry` before moving the whole directory to
`jcc-telemetry.jcc-backup-<timestamp>` (never a hard delete unless `-Purge`).

## Verification & residual risk

- VERIFIED: hook schema + event names (installed source), normalizer, signed
  receiver controls, emit pipeline, redaction.
- UNVERIFIED: gateway-side hook firing (requires a running gateway restart and a
  real message). Handler errors are isolated by Hermes and by our own
  try/except, so telemetry can never block the agent.
