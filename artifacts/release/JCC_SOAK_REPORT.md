# JCC SOAK REPORT

**Phases:** Q controlled start · R soak · S restart acceptance · T watchdog recovery
**Port:** 4778 · **soak window:** 40 min · **finished:** 2026-09-26T22:18:24

## Verdict

PASS: controlled start ok; 40-min soak with 0 health failure(s) and 0 pid change(s); restart acceptance ok=True; watchdog decision procedure ok=True (exit 0, artifact PASS); detect-and-recover ok=True

## Q — controlled start (product scripts only)

| measure | value |
| --- | --- |
| stop.ps1 exit | 1 |
| start.ps1 exit | 0 |
| /api/health | 200 in 31.4 ms |
| listener pid | 4624 |
| ownership marker written | True |

## R — soak

| measure | value |
| --- | --- |
| polls (every 2 s) | 1151 |
| health failures | 0 |
| longest run of consecutive failures | 0 |
| latency mean / max (ms) | 18.007 / 49.9 |
| 20-request bursts | 39 (failures in them: 0) |
| listener pid changes | 0 |
| final health | 200 |

## S — restart acceptance

| measure | value |
| --- | --- |
| restart.ps1 exit | 0 |
| pid before -> after | 4624 -> 1840 |
| new pid | True |
| /api/health after | 200 in 24.7 ms |
| status.ps1 exit after | 0 |

## T — watchdog recovery

**T1 decision procedure** (isolated replica; the controller is replaced by a stub implementing only the lock handshake): exit `0`, artifact verdict `PASS`, artifact written by this step: `True`.

**T2 detect-and-recover** (the substantive proof, product scripts only):

| measure | value |
| --- | --- |
| stop.ps1 exit | 0 |
| outage detected by status.ps1 | True (exit 1) |
| port free while down | False |
| health while down | URLError: <urlopen error [WinError 10061] 由于目标计算机积极拒绝，无法连接。> |
| start.ps1 exit | 0 |
| /api/health after | 200 in 3.8 ms |
| pid before -> after | 1840 -> 9952 |
| status.ps1 exit after | 0 |
| ok | True |

## Scope limits (stated, not implied)

* The shipped watchdog supervises the **continuation controller**, not the service. Nothing in this repository restarts `jcc_core` if it dies; the service recovery above is an operator-invoked cycle through the product's own scripts.
* T1 proves the watchdog's decision procedure against a stub controller, so it is evidence about the recovery *mechanism*, not about the controller's pipeline.
* Registration of the scheduled task `JEV-JCC-Continuation-Watchdog` is **not** verified through the scheduler API from here, but the task **is registered** on this host and fires every 2 minutes: its own definition under `C:\Windows\System32\Tasks\` is `Enabled=true` with a `PT2M` repetition under principal `S-1-5-18`, and the watchdog's tick log gains exactly one line every 120 s. The agent sandbox cannot enumerate Task Scheduler through CIM (`Get-ScheduledTask` fails there with ERROR_FILE_NOT_FOUND), so the fact rests on the task's own definition plus its tick interval, not on a live query. This line previously stated the reverse — it repeated the sandbox's silence as a fact about the host.

Trace: `artifacts/acceptance/JCC_SOAK_TRACE.jsonl` · machine record: `artifacts/acceptance/JCC_SOAK_RESULT.json`
