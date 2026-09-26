# JCC SOAK REPORT

**Phases:** Q controlled start · R soak · S restart acceptance · T watchdog recovery
**Port:** 4778 · **soak window:** 40 min · **finished:** 2026-09-26T13:03:40

## Verdict

PASS: controlled start ok; 40-min soak with 0 health failure(s) and 0 pid change(s); restart acceptance ok=True; watchdog decision procedure ok=True (exit 0, artifact PASS); detect-and-recover ok=True

## Q — controlled start (product scripts only)

| measure | value |
| --- | --- |
| stop.ps1 exit | 0 |
| start.ps1 exit | 0 |
| /api/health | 200 in 12.4 ms |
| listener pid | 19692 |
| ownership marker written | False |

## R — soak

| measure | value |
| --- | --- |
| polls (every 2 s) | 1151 |
| health failures | 0 |
| longest run of consecutive failures | 0 |
| latency mean / max (ms) | 15.102 / 30.19 |
| 20-request bursts | 39 (failures in them: 0) |
| listener pid changes | 0 |
| final health | 200 |

## S — restart acceptance

| measure | value |
| --- | --- |
| restart.ps1 exit | 0 |
| pid before -> after | 19692 -> 3384 |
| new pid | True |
| /api/health after | 200 in 4.4 ms |
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
| /api/health after | 200 in 29.0 ms |
| pid before -> after | 3384 -> 15576 |
| status.ps1 exit after | 0 |
| ok | True |

## Scope limits (stated, not implied)

* The shipped watchdog supervises the **continuation controller**, not the service. Nothing in this repository restarts `jcc_core` if it dies; the service recovery above is an operator-invoked cycle through the product's own scripts.
* T1 proves the watchdog's decision procedure against a stub controller, so it is evidence about the recovery *mechanism*, not about the controller's pipeline.
* The scheduled task `JEV-JCC-Continuation-Watchdog` is not registered on this host; registering it needs elevation, which was not requested.

Trace: `artifacts/acceptance/JCC_SOAK_TRACE.jsonl` · machine record: `artifacts/acceptance/JCC_SOAK_RESULT.json`
