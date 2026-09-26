# Continuation harness (runtime acceptance)

Verbatim copies of the scripts that produced the runtime-acceptance evidence.
They are here because `docs/FINAL_STOP_CONDITIONS.md` (Engineering notes, item 2)
requires that evidence be re-derivable from a clone: an untracked harness is an
unreproducible verdict, exactly as an untracked gate is.

Nothing in this directory is compiled, imported, or executed by the product or by
`final_gate_run.py`. It is archival: it exists so the committed evidence can be
regenerated and audited, not so it can be re-run as part of the build.

The ~50 throwaway probes used to investigate the failures are deliberately NOT
published. Only harnesses that produce a committed artifact are.

## Script -> artifact

| script | artifact it produces | what it does |
| --- | --- | --- |
| `watchdog_proof.py` | `artifacts/acceptance/WATCHDOG_RECOVERY_PROOF.json` | T1: proves the shipped watchdog's decision procedure on an isolated replica (Rule 3 start / Rule 2 idempotent no-op / Rule 1 never resurrect / case D recovery). |
| `soak_driver.py` | `artifacts/acceptance/JCC_SOAK_RESULT.json + JCC_SOAK_TRACE.jsonl` | PHASE Q/R/S/T driver: controlled start, soak, restart acceptance, watchdog recovery. Reconciles T1's exit code against its artifact. |
| `launch_soak.py` | `artifacts/acceptance/JCC_SOAK_RESULT.json` | Launches soak_driver.py outside the agent host's job object. |
| `launch_t1_wmi.py` | `artifacts/acceptance/WATCHDOG_RECOVERY_PROOF.json` | Runs watchdog_proof.py in the detached context and refuses to read a pre-existing result. |
| `wait_soak.py` | `artifacts/acceptance/JCC_SOAK_RESULT.json` | Blocking wait that treats the DONE marker as stale unless its mtime AND the result's finished_at both moved. |
| `canary_survive.py` | `artifacts/acceptance/CANARY_SURVIVAL.json` | A/B survival canary: measures whether a process outlives the command that armed it. This, not IsProcessInJob, is the load-bearing evidence for the launch path. |
| `probe_job_context.py` | (diagnostic only) | Cross-checks job membership from inside a process and from outside it. |
| `probe_job_launch.py` | (diagnostic only) | Runs the job-membership probe down both launch paths and compares. |
| `probe_watchdog_pipe.py` | `artifacts/acceptance/WATCHDOG_PIPE_PROBE.json` | A/B: the watchdog's own stdout as a pipe vs a file (21.3 s vs 3.1 s). |
| `lifetime_trace_v2.py` | `artifacts/acceptance/JCC_LIFETIME_TRACE.jsonl` | 1200 s lifetime trace: the service's own lifetime, outside the job object. |
| `launch_trace_via_wmi.py` | `artifacts/acceptance/JCC_LIFETIME_TRACE.jsonl` | Launches the lifetime tracer detached. |
| `e07_regen_manifest.py` | `artifacts/release/freeze_manifest.json` | Recomputes the 8 frozen scripts' sha256 (the MOVING_TARGET / FROZEN_BYTES pins). |
| `write_watchdog_report.py` | `artifacts/release/WATCHDOG_RECOVERY_REPORT.md` | Renders the watchdog report from the evidence files. |
| `write_readiness_report.py` | `artifacts/release/PROJECT_RUN_READINESS.md` | Renders the readiness report from the evidence files. |
| `write_final_acceptance_report.py` | `artifacts/acceptance/FINAL_ACCEPTANCE_REPORT.md` | Mirrors the gate's machine verdict, and audits that every gate-consumed path is in version control. |
| `test_step22_predicate.py` | (diagnostic only) | 10/10 counter-proofs for the E-07 step-22 arity fix, including a regression lock that the old 22-item input shape must be REFUSED. |
| `test_sre_signer.py` | (diagnostic only) | 15/15 counter-proofs for the SRE signer, asserting no flag can force a GO. |

## sha256 of each published script

| script | sha256 |
| --- | --- |
| `watchdog_proof.py` | `57b6726f31c8102d30ce5214330cf30e92ed6d91fd94bce470ea580e578f3ff3` |
| `soak_driver.py` | `c5438929886eda8de209558f405805b3d14201261415fc8af88adc0f4fca5629` |
| `launch_soak.py` | `16e7a71ed5f8763d35ff549774cfce25fe60167d403ba9fc42f237e262f3baf1` |
| `launch_t1_wmi.py` | `8ca46f44a4a19504639a8696688b09f808fc634faacf580ac3e69040faf4f1a5` |
| `wait_soak.py` | `b699e2350c6d56bcf19e85d91d247558811f59839dd1b8669a716b115a9486ac` |
| `canary_survive.py` | `032f8ec63f3f33c3ff513b4a39ff6e73d4041346aa2ee8b8be92c62168f9cf99` |
| `probe_job_context.py` | `ee1fcdf8237c4a13667c4e2a40ccac6538bc0127e22f477541051ff3915f72ea` |
| `probe_job_launch.py` | `75ea31f599f2089b8205d895422bad7f4aa2ba813a8f016f24afa8a2d38321cf` |
| `probe_watchdog_pipe.py` | `53d357cbfb4454cf0bffd9802a9a70bdb86cd47208a0737532a3eb4d119885af` |
| `lifetime_trace_v2.py` | `d3c5fbd67fc8775802e81deccb8815051c4899f4401b56422649c02e7f939f90` |
| `launch_trace_via_wmi.py` | `e48de9fa50fea7580214d3c92e9fc0996c9fde84cc973f1c75242855d9802136` |
| `e07_regen_manifest.py` | `ba0084d24f9c8c9522d01e83f23921802511a5acc5f35f138d0ab8456c471cf1` |
| `write_watchdog_report.py` | `1b8d472e34be9a51f07567130ad8f7fa5357adcf5e60bdb48ecb5ad4be9e9c3c` |
| `write_readiness_report.py` | `816c1fdf17b4487a646987e6fcee4ecd10aa2bbe2910e3cf1451065e7dc2f708` |
| `write_final_acceptance_report.py` | `0c1023110ee141947c8c88ecdfb891b5f3f72b8897b2e70fa52880f58cc7b7e7` |
| `test_step22_predicate.py` | `6ffe7d0b5a5aa03a7a5ac40b1ba6ed32eaeb96374708c710e2ebeb087c323447` |
| `test_sre_signer.py` | `9b0e1d111248e541b94697838253a6711bab90a41c2c883f3b876c820836d24b` |

Recorded because a copy cannot be trusted without a digest: if a script here is
edited, its digest stops matching the run that produced the committed evidence and
the mismatch is visible instead of silent.
