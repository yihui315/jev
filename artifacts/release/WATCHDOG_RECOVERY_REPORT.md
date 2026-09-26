# WATCHDOG RECOVERY REPORT

**Product:** JEV COMMAND CENTER · **Port:** 4778 · **Written:** 2026-09-26T13:03:58

## Summary

| part | what was proved | verdict |
| --- | --- | --- |
| T1 | the shipped watchdog's decision procedure: replace a dead controller, no-op on a live one, never resurrect a finished pipeline | **PASS** |
| T2 | service-level detect-and-recover: an outage is detected and the service comes back with a new pid, using product scripts only | **PASS** |

**Overall: PASS**

## T1 — the watchdog's decision procedure

Run against an isolated replica root (`F:\jev\.workbuddy\scratch\watchdog_proof\root_20260926_130250_392545`) in run `20260926_130250_392545`.

The artifact records the launch context it was produced in, so the claim that it ran detached is checkable rather than assumed: `context = {'in_job': True, 'note': "in_job is reported as a READING of IsProcessInJob for this process, and it answers 'is this process in ANY job' -- not 'is it outside the agent host's job'. Measured on this very launch chain: the WMI-created cmd.exe reads False while the python it spawns reads True, and that python still outlives its launching command. It is therefore NOT used here as evidence of escaping anything. The load-bearing evidence for the launch path is behavioural survival, measured in artifacts/acceptance/CANARY_SURVIVAL.json."}`. Raw captures of every watchdog invocation (`6` file pairs) are named in `raw_captures`.

The **only** edit to the shipped script is `$ProjectRoot`, and the harness asserts that exactly one line differs (`lines_changed = 1`). The controller is replaced by a stub that implements only the documented lock handshake and lives 45 s, because the real controller's next step is a full E-07 plus the final gate — running it here would contend with the runtime under measurement.

| case | expectation | measured | exit |
| --- | --- | --- | --- |
| Rule 3 — start a replacement | started controller | matched | 0 |
| Rule 2 — no-op on a live controller | already running | matched | 0 |
| recovery — replace a dead controller | will start a replacement | matched | 0 |
| Rule 1 — never resurrect a finished pipeline | already complete | matched | 0 |
| E host conditioning: proxy env vs Start-Process | - | NOT MATCHED | 0 |

`Rule 2` is the idempotency proof (a second tick must do nothing) and `case D` is the recovery proof (the next tick after a controller dies starts its replacement and the new lock names a live pid).

### A blocking defect found while building T1

The first attempt at case B observed an already-dead controller. Cause, measured: **when the watchdog's own stdout is a pipe, the call does not return until the controller it started exits.**

| run | watchdog stdout | returned at | controller afterwards |
| --- | --- | --- | --- |
| 1 | captured on a pipe | 21.3 s (controller lifetime 20 s) | exited |
| 2 | redirected to a file | 3.1 s | still alive |

This is the trap the watchdog's own header documents, reappearing one level up: the header guards the **child's** stdout, but the watchdog's **own** stdout was a pipe. The operational consequence is that any invoker that captures the watchdog's output — a scheduled task with piped output, a CI harness, a naive `subprocess` call — blocks for the controller's whole lifetime and never gets a chance to observe it. The precise handle-inheritance path was **not** determined; only the behaviour is claimed.

Evidence: `artifacts/acceptance/WATCHDOG_PIPE_PROBE.json`.

## T2 — service detect-and-recover (the substantive proof)

Run on the service that had just finished the soak, using only the product's scripts.

| measure | value |
| --- | --- |
| pid before | 3384 |
| stop.ps1 exit | 0 |
| outage detected by status.ps1 | True (exit 1) |
| port free while down | False |
| health while down | URLError: <urlopen error [WinError 10061] 由于目标计算机积极拒绝，无法连接。> |
| start.ps1 exit | 0 |
| /api/health after | 200 in 29.0 ms |
| pid after (new) | 15576 |
| status.ps1 exit after | 0 |
| ok | True |

## Scope limits — stated, not implied

* The shipped watchdog supervises the **continuation controller**, not the service. **Nothing in this repository restarts `jcc_core` if it dies.** T2 is an operator-invoked detect-and-recover cycle through the product's scripts, not an automatic watchdog.
* T1 proves the watchdog's decision procedure against a stub controller, so it is evidence about the recovery *mechanism*, not about the controller's own pipeline. The pipeline is proved separately by E-07 and the final gate.
* The scheduled task `JEV-JCC-Continuation-Watchdog` is **not registered** on this host; registering it needs elevation, which was not requested. The watchdog script itself was invoked directly, which is what the task would do.
* This proof runs **outside** the agent host's job object: the launcher reports its own `in_job` reading and the artifact records the context it was produced in. Note that `IsProcessInJob` answers "is it in ANY job", so the load-bearing evidence for the launch path is behavioural survival, measured in `CANARY_SURVIVAL.json`, not the boolean. See PROJECT_RUN_READINESS.md §4.1.
* Console output from `taskkill` on this host is GBK. It is now decoded with the codepage it was actually written in, so the cleanup lines below read correctly. Before that fix, decoding it as UTF-8 produced replacement characters, and printing one of those to the GBK stdout raised `UnicodeEncodeError` -- which killed a run whose five decision-rule cases had **all** passed, at a cleanup print **29 lines before the evidence write**. The artifact was therefore never produced. The fix closes that for the whole script (encoding-aware captures + a guarded cleanup block), and the soak driver now reconciles the exit code against the artifact instead of trusting either alone.

Evidence: `artifacts/acceptance/WATCHDOG_RECOVERY_PROOF.json`, `artifacts/acceptance/WATCHDOG_PIPE_PROBE.json`, `artifacts/acceptance/CANARY_SURVIVAL.json`, `artifacts/acceptance/JCC_SOAK_RESULT.json`.
