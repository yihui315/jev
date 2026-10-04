# §15 — Gate measurement at HEAD `2899bab`

**This supersedes §11 of `RETEST_ADJUDICATION.md` as the current gate state.** It is
deliberately NOT a 30-leg result, and the reason is the finding rather than an excuse:
**three separate measurement instruments of mine were broken, and every one of them
first presented as a product failure.** That is the same class of error this round has
already produced three times, so it gets its own document rather than a footnote.

- Owner: Harbor (jcc-test-sre-engineer)
- Measured at: `2899bab77116978ed6c77cb01a714050e10d934f` (and `88ff8a8` for the
  A-group legs — see §15.3, this matters)
- Scope: `D:\jev`, `release/jcc-v1.3-final`
- Verdict vocabulary: PASS / FAIL / **NOT MEASURED**. No leg is reported as anything else.

---

## 15.1 What actually completed

Per-leg, each in its own process. Sources: `per_leg_results2.txt`, and the leg records
on disk in `artifacts/release/v1.3_product/legs/`.

| leg | verdict | evidence |
|---|---|---|
| A2_DOMAIN | PASS | 47 passed |
| A3_MISSION | PASS | 23 passed |
| A4_ATTENTION | PASS | 32 passed |
| A5_DELIVERABLES | PASS | 26 passed |
| A6_CONNECTIONS | PASS | 43 passed |
| A8_SECURITY_GATE | PASS | `GO, non-vacuous`, 9 checks, high=0 medium=0 low=0 info=0 |
| C3a_TYPECHECK | PASS | `exit=0 ; tsc_ran=1` |
| D2_PRODUCT_CONFIG_INERT | PASS | `boot=OK actuation=False localhost_only=True` |
| D3_UI_IS_NOT_STDLIB | PASS | `positives=4 banned=0` |
| E2_GATE_INPUTS_TRACKED | PASS | 33 inputs, 0 missing |
| E3_A8_PINS_VS_HEAD_BLOBS | PASS | 12/12 pins match the HEAD blob |
| E4_NO_LISTENERS_LEFT_BEHIND | PASS | 4779/4780/4781/4782 all 0 |
| F1_INSTRUMENTS_ARE_FALSIFIABLE | PASS | 7/7 falsifiers behaved |
| A1_FREEZE_MODEL | **FAIL** | 13 passed / 6 errors, floor 19 — CR-H1, shared basetemp |
| E5_FREEZE_ANCHOR_RESOLVES | **FAIL** | see §15.3 |

**15 of 30 legs measured: 13 PASS, 2 FAIL, 15 NOT MEASURED.**

**NOT MEASURED** (no verdict, in either direction): A7_INVARIANTS, B1–B8 (8 legs),
C1_CORE_E2E, C2_TOOLS_TESTS, C3b_VITEST, C3c_BUILD, D1_GREEN_PROTECTION,
E1_FROZEN_TREE.

C2 *is* covered by §9 of the main report as a direct pytest measurement
(6 failed / 236 passed / 0 errors, explicit `--basetemp`) — but that is **not** a gate
leg at this HEAD, and the two must not be conflated.

## 15.2 The `--full` run was reaped, and neither reaping is a leg failing

The original `--full` run (log `full_gate_run_88ff8a8.txt`) reached A7_INVARIANTS at
18:28 and never printed a verdict for it. By 19:32 the process was gone, the log had
**no summary line and no exit code**, and its last content was a row of progress dots
— which is indistinguishable from a hang unless you know to look for the missing
summary.

Measured evidence that it was a **reap** and not a hang or a crash:
* scratch churn continued for 40+ minutes (`jcc_release_truth_selftest_*`,
  `jcc_cr002_run_*`, `jcc_porttype_*` appearing in the shim temp tree) — it was
  doing work;
* the python process's working set grew steadily across samples
  (100 988 K → 106 408 K → 106 740 K → 106 752 K) — not blocked on a dead lock;
* `wmic` is on this host's Program Blacklist, so parent/child PID inspection was
  unavailable. Stated as a limit, not worked around.

Second reaping: my standalone A7 attempt ran **90+ minutes** with no verdict, and a
standalone C1 attempt reached 7% and stopped. Both ended with no summary line.

`measure_legs.ps1` and `measure_legs2.ps1` were written to survive this — one leg per
process, so partial coverage persists on disk. Both are kept **including their broken
first versions**, because the failures are the finding.

## 15.3 E5 FAIL is caused by my own commits, and that is the rule working

```
recorded=9bfcc8d61e41  head=2899bab77116
problems = ['the freeze document was last changed by 9bfcc8d61e41,
            not by HEAD 2899bab77116']
```

`E5_FREEZE_ANCHOR_RESOLVES` requires the commit that last changed
`docs/v1_3/FINAL_PRODUCT_FREEZE.md` to be HEAD. That commit is `9bfcc8d`. **Every
commit made after the freeze breaks this leg by design** — which is the leg working
correctly: the freeze anchor asserts the frozen bytes are the release bytes, and each
of my commits (`88ff8a8`, `d311ef5`, `2899bab`, plus the doc commits) moved HEAD.

**This is not a defect and must not be "fixed" by editing the freeze document.**
Correct disposition: the freeze is re-anchored as the last step of the release batch,
after all product commits land. Flagged for team-lead; I have not touched that file.

The same mechanism means **the numbers in §15.1 span two HEADs** — `88ff8a8` for the
A-group legs, `2899bab` for C3a/E4/E5/F1. A verdict belongs to one commit. **A re-run
at a single frozen HEAD is required before any of this is quoted as a release number.**

## 15.4 Three broken instruments of mine, and what each one looked like

Every one of these initially read as "the product failed". None of them did.

**(a) The per-leg driver recorded 16 legs at rc=1 in 0.5–0.9 s.**
0.6 s is far too fast for any leg to do work, and the output file was entirely silent.
Three distinct causes, found in this order:

* `git` resolves as a PowerShell command on this host but **not** from a Python
  child. PATH begins `D;D:\WorkBuddy\resources\app.asar.unpacked\cli\vendor\shim\safe-bin;…`
  and the safe-bin shim shadows it, so `subprocess.run(['git', …])` died at
  `CreateProcess` with `FileNotFoundError [WinError 2]`. Measured in
  `probe_nested_env.py` / `nested_ps_env.json` (which also confirms
  `py_exists=True`, `git_resolved=True`, and that python *does* launch — it caught its
  own `NameError`). Fix: read `.git/HEAD` directly, no `git` at all.
* `Get-Content -Raw` returned `System.Object[]`, so `.Trim()` raised
  `MethodNotFound` — **on a line that ran before the gate invocation**. The exception
  terminated the script, so the gate was **never launched at all**. This, not the rc,
  is why all 16 "failed".
* `& exe 2>&1` inside `powershell -File` on PS 5.1 routes child stdout to the error
  stream, so a control that cannot fail (`print('CONTROL-OK')`) came back empty. An
  empty capture is indistinguishable from a failed child. Fix: redirect to files,
  which stream-routing cannot lose.

**(b) `str.format()` on a PowerShell template** raised
`KeyError: ' Write-Output ("GATE'` because the body is full of PS `{ }` script blocks.
Same class as the no-op mutation below: the templating, not the thing under test.

**(c) Hard measurement limits on this host.** The Bash tool refuses to invoke
`powershell.exe` at all ("Invoking PowerShell from Bash bypasses PowerShell security
checks"), and `wmic.exe` is on the Program Blacklist. So the further nesting layer
Bash → python → powershell → python is unavailable for diagnosis. Stated, not worked
around — and the reason the nested-PS route was abandoned in favour of running legs
directly from Bash.

## 15.5 Mutation test of my own instruments — a real hole, found and fixed

Adopting metric-verify's method (break the code on purpose, check the detector
notices) found a genuine defect in `tools/fix_missing_pathlib_import.py` — my own
repair tool. Copies only, under `runtime/harbor_mutation/`, so `D:\jev` was never
written to.

**The defect.** The repair loop `continue`d on the "already imports pathlib" branch, so
a file that **had** the import but whose `Path(...)` use was not resolvable at module
scope never reached the verify pass. Output was `changed=0 skipped=1 failed=0`, exit 0
— the one input the tool exists to catch, waved through. Fixed with a `to_verify` list
covering every target, repaired or not (`2899bab`).

Real tree unchanged by the fix: 26/26 skipped, exit 0; controller selftest 33/33 with
`JCC_ROOT` unset.

Measured (`MUTATION_RESULTS.json`):
* **M1** remove the import → **CAUGHT** (repaired on disk, bytes changed, exit 0)
* **M2** import under `TYPE_CHECKING` → **CAUGHT** (listed FAILED, exit 1)
* **M3** falsifier's own VACUOUS guard removed → **INFO**, guard confirmed load-bearing

Two harness faults surfaced first as "the detector missed it", where the accusation was
wrong — metric-verify's warning about proving your own tool before reporting a failure,
paid for twice:

* **The oracle was exit-code-only.** This is a *repair* tool: its contract is "fix a
  missing import and exit 0", reserving non-zero for "could not fix". M1 therefore had
  to assert the import was back on disk, re-read after the run. As written it produced
  **two false MISSES against a working tool**.
* **The first M2 was a no-op.** It re-indented the `Path(__file__)` line by four
  spaces — but that line is the *continuation* of a module-level expression
  (`ROOT = os.environ.get("JCC_ROOT") or str(` / `Path(__file__)…` / `)`), so the name
  still binds at module scope and the detector was **right** to pass it. I had mutated
  formatting, not binding. A mutation that does not change behaviour cannot test a
  detector; reporting it as a MISS would have been a fabricated finding. M2 now
  rewrites the import into a `TYPE_CHECKING` guard — legal, parses, genuinely broken at
  runtime, which is exactly the `NameError` class this repair exists to prevent.

## 15.6 GREEN (port 4778) could not be held up for the measurement window

`D1_GREEN_PROTECTION` needs a listener on 4778. GREEN was **down** when I started, so I
brought it up twice via the documented `scripts/start.ps1`. Both times it started
cleanly and served `/api/health` 200 with `actuation_enabled=false`,
`policy_version=p3.0.0`, `localhost_only=true`. Both times it was subsequently
**gone**, with no error: `runtime/jcc/server.err.log` shows a clean start
("Application startup complete", "Uvicorn running on http://127.0.0.1:4778") and a
normal shutdown with no traceback.

Incidental corroboration that the F2 relocation fix took effect: the restarted
service rewrote `runtime/jcc/jcc.ownership.json` with `repo_path: D:\jev`,
`cwd: D:\jev`, `venv_python: D:\jev\.venv\…`, `config: D:\jev\config\jcc.yaml` —
previously all `F:\jev`.

A listener that comes up correctly and then disappears without an error is consistent
with the sandbox reaping background processes, the same mechanism that killed the gate
runs (§15.2). **I did not sign D1.** It is NOT MEASURED, which is a host/measurement
limit, not a product verdict.

## 15.7 What survives all of the above

**Unaffected by every measurement problem in this document** — these are code-level and
host-independent:
* Family A: the missing-`pathlib` defect in 26 shipped files, and its fix (§1).
* Family B: the four surviving `F:\jev` literals, and their fix (§2), including the
  PS 5.1 verification that `JccRoot` is now `D:\jev`.
* The C2 trajectory: 21 failed + 12 errors → 6 failed + 0 errors. A direct pytest
  measurement with an explicit `--basetemp`, independent of the gate completing.
* The mutation results in §15.5, which are about my own instruments and fully
  reproducible in ~90 s.

**NOT established, and must not be quoted as established:**
* any 30-leg aggregate, in either direction;
* anything about `C1_CORE_E2E` at any HEAD;
* `E1_FROZEN_TREE` — it cannot pass while `scripts/lib/JccCommon.ps1`,
  `tools/port_watch.py`, `tools/cr002_green_port_falsifier.py` and
  `tools/release_truth.py` are dirty from teammates' concurrent work. Measured
  `product_dirty=3 metadata_dirty=38 untracked=29`. Those files are **not mine** and I
  left them untouched.

---

## Decision

**INFORMATION ONLY** for §15, and **NO-GO** for the release as a whole.

I am not a release gate and this is not a gate signature. What I can say within my own
scope: two real product defects were found, fixed, committed and independently
verified; the C2 failure count fell from 33 items to 6; 13 of 30 gate legs pass; and
**the two failing legs that I can explain are the shared-basetemp test-infrastructure
defect (CR-H1) and the freeze anchor correctly refusing a HEAD that moved after the
freeze.** I did not complete a 30-leg measurement, I did not measure C1 or D1, and
three of my own instruments turned out to be broken in ways that first looked like
product failures — which is the single most useful thing for the next reader to know
about this round.

## Handoff

- **Next owner:** team-lead, for the re-anchoring decision (§15.3) and for deciding
  whether a single-HEAD re-run is authorised.
- **Artifacts to carry forward:**
  `per_leg_results2.txt` (the 15-leg table), `nested_ps_env.json` +
  `probe_nested_env.py` (the shim/PATH finding), `MUTATION_RESULTS.json` +
  `mutation_check.py`, `measure_legs.ps1` / `measure_legs2.ps1` (one-leg-per-process
  drivers, broken versions retained), `full_gate_run_88ff8a8.txt` (the reaped run),
  and `RETEST_ADJUDICATION.md` §1/§2/§9 for the defect findings and C2 trajectory.
- **Facts team-lead must forward, unedited:**
  1. E5 FAIL is *caused by* post-freeze commits and is the rule working — it must not
     be "fixed" by editing the freeze document.
  2. The §15.1 numbers span two HEADs and are not a single-commit verdict.
  3. A1's 6 setup ERRORs are the shim-forced shared basetemp (CR-H1), **not** the
     `pathlib` defect — the 12 C2 errors were the `pathlib` defect, proven from the
     traceback. Two different failures that were briefly conflated.
  4. D1 and C1 are NOT MEASURED. Absence of a verdict is not a pass.
- **Do not let the next phase misread:** "13/30 legs pass" is not "the gate is 43%
  green" — 15 legs were never measured, and two of the failures are infrastructure
  rather than product. The honest summary is that the release is not measurable on
  this host in its current configuration, which is a finding about the harness, not
  about the product.
