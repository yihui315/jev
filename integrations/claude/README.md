# JCC × Claude Code telemetry — P2

**Status: CONNECTED** (probe verified). Telemetry only; actuation DISABLED.

## Mechanism (verified on this host)

- Claude Code **2.1.196**, binary `~/.local/bin/claude.exe`.
- User settings: `~/.claude/settings.json` (+ `~/.claude/settings.local.json`,
  `~/.claude.json` all present).
- Native hook schema: `hooks → { EventName: [ {matcher?, hooks:[{type:"command"|"prompt",command}]} ] }`,
  hook input JSON on stdin with `session_id` (primary) + `cwd` (metadata).
- Real observed event shapes also captured: transcript jsonl
  (`~/.claude/projects/<slug>/<sessionId>.jsonl`) and internal telemetry
  (`~/.claude/telemetry/1p_failed_events.*.json` → `ClaudeCodeInternalEvent`).

## Events hooked (telemetry only)

`SessionStart, SessionEnd, UserPromptSubmit, PreToolUse, PostToolUse, Stop,
Notification, PreCompact`.

> **User hooks are PRESERVED.** JCC entries carry a separate `jcc_emit.py` marker
> and are merged into the existing `hooks` object — existing `env`, `model`,
> `permissions`, and any user hooks are never modified.

## Install / uninstall

```powershell
powershell -ExecutionPolicy Bypass -File F:\jev\integrations\claude\install.ps1         # merge hooks (backup first)
powershell -ExecutionPolicy Bypass -File F:\jev\integrations\claude\install.ps1 -DryRun
powershell -ExecutionPolicy Bypass -File F:\jev\integrations\claude\uninstall.ps1       # remove only JCC entries
powershell -ExecutionPolicy Bypass -File F:\jev\integrations\claude\uninstall.ps1 -RestoreBackup
```

Hook command on Windows is the wrapper `jcc-claude-hook.cmd` (works from both
cmd.exe and Git Bash; always exits 0).

## Rollback

`~/.claude/settings.json.jcc-backup-<timestamp>` is written before every change;
uninstall is marker-scoped; `-RestoreBackup` restores the newest backup verbatim.

## Verification & residual risk

- VERIFIED: probe, normalizer (hook + transcript + telemetry shapes), emit
  pipeline, redaction, no secret leakage.
- UNVERIFIED: agent-side hook trigger in a live session (Windows shell for hook
  execution is provider-dependent; the `.cmd` wrapper covers cmd.exe and Git Bash).
