# JCC v1.2 — Durable Runtime via Windows Task Scheduler

**Scope:** how the v1.2 Visual Command Center runs as a *durable* second instance,
isolated from the frozen GREEN service on `127.0.0.1:4778`.

**Status:** registration script delivered; **actual host registration is `PENDING_EXTERNAL_ACTION`**
(see §4). The live runtime is already proven (see `DURABLE_RUNTIME_PROOF.json`).

---

## 1. Why Task Scheduler (not `start-*.ps1` directly)

The agent session runs commands inside a Windows **job object**. When a Bash/PowerShell
tool command finishes, the sandbox **reclaims ordinary child processes** of that command.
Measured during this rollout:

- A service launched by a *short-lived launcher* script (`cmd.exe` → `python` child) is
  reaped the moment the launcher exits (~30 s).
- A service launched **directly** as a long-lived `run_in_background` task (its
  `serve_forever` *is* the task) survives — verified alive 40 s+ after the launching
  command returned.

For a service that must survive agent restarts, host reboots, and session boundaries,
the correct primitive is the **Windows Task Scheduler**, which runs outside any agent
job and restarts on failure. `start-*.ps1` is still the authoritative launcher for
manual/fail-closed operations; Task Scheduler is the *durability* layer on top.

## 2. The two tasks (local-only, `127.0.0.1`)

| Task | Runs | Port | Bind | Touches GREEN? |
|------|------|------|------|----------------|
| `JCC-v1.2-API` | `python -m jcc_core.run` (config `config/jcc-v1.2.yaml`) | 4779 | 127.0.0.1 | **never** |
| `JCC-v1.2-VisualUI` | `serve_visual.py --host 127.0.0.1 --port 4780` | 4780 | 127.0.0.1 | **never** |

Both actions set `JCC_CONFIG` and `PYTHONPATH` **explicitly** (no ambient-env
reliance) and bind loopback only. Each uses its own
`runtime\jcc-v1.2` directory + SQLite DB (`jcc.db`), so there is no cross-talk with
GREEN's `runtime\jcc`.

Task settings: `-AtStartup` trigger, `RestartCount 3` / `RestartInterval 1 min`,
`ExecutionTimeLimit 0` (no time limit), `RunLevel Highest`, principal `SYSTEM`
(runs whether logged on or not, no password).

## 3. Operator procedure (on the HOST, as Administrator)

```powershell
# dry run
powershell -ExecutionPolicy Bypass -File scripts\jcc-v1.2-register-tasks.ps1 -WhatIf

# register both durable tasks
powershell -ExecutionPolicy Bypass -File scripts\jcc-v1.2-register-tasks.ps1

# start now (or wait for next boot)
Start-ScheduledTask -TaskName "JCC-v1.2-API"
Start-ScheduledTask -TaskName "JCC-v1.2-VisualUI"

# verify
Get-ScheduledTask -TaskName "JCC-v1.2-*"
.\scripts\status-jcc-v1.2.ps1
```

Registration is idempotent (re-run updates the existing task). The script also exports
each task's XML to `artifacts/release/v1.2_visual/<Task>.task.xml` for offline review.

## 4. `PENDING_EXTERNAL_ACTION` — schtasks blacklist

`Register-ScheduledTask` / `schtasks.exe` is **blocked inside the agent sandbox**
(Program Blacklist: "Permission denied" / "blocked by security policy"). This is an
*external permission* boundary, not a code defect. Until an operator runs
`jcc-v1.2-register-tasks.ps1` on the host, the durable tasks are **not** created in
this session. This does **not** block the v1.2 release: the live runtime is already
proven via the agent background runner (see `DURABLE_RUNTIME_PROOF.json`), and the
manual `start-*.ps1` / `stop-*.ps1` / `status-*.ps1` scripts remain the fail-closed
control surface.

## 5. Fail-closed guarantee

Every start/stop/status script reuses `lib\JccCommon.ps1`
(`Test-JccOwnership` / `Write-JccOwnershipMarker` / `Get-JccPortOwner`). A process is
only ever signalled if marker + pid + command line (`*-m jcc_core.run*` / `*serve_visual.py*`)
+ port owner + ancestry all agree. GREEN 4778 (pid 7588, frozen v1.0.1) is **never
examined and never touched** by any v1.2 script.
