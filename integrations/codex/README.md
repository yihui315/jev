# JCC × Codex telemetry — P2

**Status: DEGRADED** (observation verified; push not installed). Actuation and
App Server steering DISABLED.

## What is verified on this host

- Codex CLI `0.136.0` present; `~/.codex/config.toml` present (parses).
- **Observation path (VERIFIED):** `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`
  (362 files here), one JSON object per line
  `{timestamp, ordinal, type, payload}`. Only the first line
  (`type=session_meta`) carries `session_id`; the observer attaches it to every
  following record. This is the authoritative, `session_id`-bearing source.
- The **native hooks runtime** exists in the 0.136.0 binary (strings:
  `PreToolUse/PostToolUse/PermissionRequest/SessionStart/...`,
  `command_windows`, `ConfiguredHookMatcherGroup{matcher,hooks[]}`) and a legacy
  `notify` payload `{type:"agent-turn-complete", turn-id, input-messages,
  last-assistant-message}`.
  **We do NOT assume the Claude hook schema**, and we could not verify the Codex
  hooks schema end-to-end (no interactive turn) → capability flag stays `false`.

## Why DEGRADED (not CONNECTED)

1. Push via `notify` is **UNVERIFIED** and the user **already owns** `notify`
   (`codex-computer-use.exe turn-ended`). The installer therefore performs **no
   config change by default** (never clobber user config).
2. App Server / realtime steering cannot be verified → left **disabled** (P0-P2).

## Run the observer (verified path)

Run from the repository root; the observer derives the checkout from its own
location, so these work wherever the repo lives.

```powershell
.\.venv\Scripts\python.exe .\integrations\codex\codex_rollout_observer.py --once
.\.venv\Scripts\python.exe .\integrations\codex\codex_rollout_observer.py            # continuous
```
Offsets are tracked in `~/.jcc/codex_observer_state.json`, so records are read
once. Records with no resolvable `session_id` are never ingested.

## Optional (opt-in) push — reversible, refuses to overwrite user notify

```powershell
# probe + observer guidance only (no config change):
powershell -ExecutionPolicy Bypass -File .\integrations\codex\install.ps1
# chain JCC notify (refuses if a user notify exists; -Force to chain):
powershell -ExecutionPolicy Bypass -File .\integrations\codex\install.ps1 -EnableNotify
powershell -ExecutionPolicy Bypass -File .\integrations\codex\install.ps1 -EnableNotify -Force
# rollback:
powershell -ExecutionPolicy Bypass -File .\integrations\codex\uninstall.ps1
```

`config.toml` is backed up (`config.toml.jcc-backup-<ts>`) and the original
`notify` line is stored in `config.toml.jcc-notify-original.json`
(`{"notify": [...], "raw_core": "<verbatim line>"}`); uninstall restores it
**byte-exactly** (original spacing / quotes / CRLF preserved) and removes the
sidecar. The chaining wrapper is `jcc-codex-notify.cmd` → `jcc-codex-notify.py`
(forward to JCC + re-invoke the user's original notify; always exits 0).

> The `notify` push mechanism and its payload contract remain **UNVERIFIED**
> (evidence-grade, from binary strings only). Do not treat it as production-ready.
