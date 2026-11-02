# JCC × WorkBuddy (CodeBuddy) telemetry — P2

**Status: CONNECTED** (probe verified). Telemetry only; actuation DISABLED.

## Mechanism (verified on this host)

- WorkBuddy's CLI is **CodeBuddy Code**. The host exports
  `CODEBUDDY_CONFIG_DIR=%USERPROFILE%\.workbuddy`, so the **effective** user
  settings file is `~/.workbuddy/settings.json` (the documented default
  `~/.codebuddy/settings.json` and the spec candidate `~/.workbuddy-ai/settings.json`
  do **not** exist here).
- Hook schema (from the installed CLI `hooks.md`):
  `settings.json → hooks → { EventName: [ {matcher?, hooks:[{type:"command",command}]} ] }`.
  Hook input is **JSON on stdin** and always carries `session_id` (primary
  identity) + `cwd` (metadata only) + `hook_event_name`.
- Windows shell is **Git Bash** (enforced by CodeBuddy), so the hook command uses
  the POSIX form of the venv python.

## Events hooked (telemetry only)

`SessionStart, SessionEnd, UserPromptSubmit, PreToolUse, PostToolUse, Stop,
Notification, PreCompact`.

> **P2 hard rule:** `PermissionRequest` is **never** hooked — WorkBuddy's native
> GUI permission flow is left completely untouched.

## Install / uninstall

Run from the repository root. `-JccRoot` is optional — the script derives the
checkout from its own location, so these commands work wherever the repo lives.

```powershell
powershell -ExecutionPolicy Bypass -File .\integrations\workbuddy\install.ps1        # add hooks (backup first)
powershell -ExecutionPolicy Bypass -File .\integrations\workbuddy\install.ps1 -DryRun
powershell -ExecutionPolicy Bypass -File .\integrations\workbuddy\uninstall.ps1      # remove only JCC entries
powershell -ExecutionPolicy Bypass -File .\integrations\workbuddy\uninstall.ps1 -RestoreBackup
```

## Rollback

Every write creates `<settings.json>.jcc-backup-<timestamp>` next to the file.
Uninstall removes **only** entries whose command contains the `jcc_emit.py`
marker; pre-existing user hooks are preserved. `-RestoreBackup` restores the
newest JCC backup verbatim. Logs: `install.log` / `uninstall.log` in this folder.

## Verification & residual risk

- VERIFIED: config location, hook schema, `session_id` identity, normalizer
  (contract test), emit pipeline (integration smoke), no secret leakage.
- UNVERIFIED: the agent-side **trigger** (hooks are snapshotted at session start
  and require the `/hooks` review panel), so activation must be confirmed in a
  live session. Actuation stays disabled.
