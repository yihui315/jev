# RETEST_ADJUDICATION — C2_TOOLS_TESTS / C1_CORE_E2E failure families

- **Owner**: Harbor (jcc-test-sre-engineer), Windows Durable Runtime / Test-SRE
- **Repo root**: `D:\jev` (the `F:\jev` named in the master directive does not exist on this host)
- **Branch / HEAD adjudicated**: `release/jcc-v1.3-final`. Families A–I were adjudicated at `88ff8a8e60070a1a4d6a0162cf1325e3ed5e0c0e`; Family J was found later, at `46ff531730e431e76fb9458ff86b8ece0d7031b3`. **A verdict belongs to one commit — do not merge the two runs' numbers.**
- **Baseline HEAD when assigned**: `05715da68794eff374703467f00de0871a44d65a`
- **venv**: `D:\jev\.venv\Scripts\python.exe` (Python 3.13.14)
- **Host default codec**: `cp936` (`locale.getencoding()`), measured
- **Verdict vocabulary**: every family is one of REAL PRODUCT DEFECT / TEST DEFECT / GENUINE ENVIRONMENT LIMIT, with a code-line citation. No family is classified from documentation.

---

## 0. Reproduction baseline (VERIFIED)

Command actually run, exactly as assigned:

```
cd D:/jev && CODEBUDDY_SAFE_DELETE_ENABLED=0 PYTHONPATH=D:/jev/packages PYTHONIOENCODING=utf-8 \
  D:/jev/.venv/Scripts/python.exe -m pytest tools/tests -q -p no:randomly
```

Result — **identical to the figures handed to me**, so the reproduction is real and not
inferred from documents:

```
21 failed, 209 passed, 6 skipped, 12 errors in 434.21s
exit=1
```

Full capture: `artifacts/release/v1.3_final/c2_tools_tests_full.txt`, and
`c2_after_pathlib_fix.txt` / `c2_after_all_fixes.txt` for the two later runs below.

---

## 1. Family A — missing `pathlib` import (26 files) — **REAL PRODUCT DEFECT, FIXED**

**Verdict: real product defect. Committed as `88ff8a8`.**

### Evidence

`tools/tests/test_continuation_infra.py:57-60` loads the controller through a
module-scoped fixture:

```python
@pytest.fixture(scope="module")
def ctl():
    return _load(CONTROLLER, "_jcc_controller")
```

`_load` calls `spec.loader.exec_module(mod)` (line 53). Every one of the 12 ERRORs
was this fixture failing, and the traceback terminates at
**`tools/jcc_continuation.py:71`**:

```
>   Path(__file__).resolve().parents[1]
NameError: name 'Path' is not defined
```

`tools/jcc_continuation.py` lines 46-57 import `argparse, ctypes, hashlib, io, json,
os, subprocess, sys, threading, time, datetime, typing` — **`pathlib` is absent** —
while line 70-72 uses it:

```python
ROOT = os.environ.get("JCC_ROOT") or str(
    Path(__file__).resolve().parents[1]
)
```

### Why the defect survived earlier review (the load-bearing detail)

The expression short-circuits. Measured on this host:

```
$ env -u JCC_ROOT python tools/jcc_continuation.py selftest
NameError: name 'Path' is not defined                     # exit != 0, module body dies

$ JCC_ROOT=D:/jev python tools/jcc_continuation.py selftest
controller selftest: 33/33                                # right-hand side never evaluated
```

The watchdog exports `JCC_ROOT`, so every watchdog-launched run took the left operand
and never raised. The crash only fires when `JCC_ROOT` is **absent** — which is
exactly what a test or a human does.

### Blast radius — measured, not assumed

A scan of every tracked `tools/`, `tools/**/`, `scripts/` file that uses
`Path(__file__)` without importing `pathlib` found **26 files**. Import-checking each
one individually (9 spot-checked) returned `NameError: name 'Path' is not defined`
for all of them. The set includes `tools/jcc_continuation.py` (the controller the
entire FINAL pipeline is driven by), `tools/continuation/run_infra_suite.py`,
`tools/continuation/soak_driver.py`, `tools/continuation/write_final_acceptance_report.py`,
`tools/stable_runtime_snapshot.py`, `scripts/measure-v1.2-perf.py`.

Provenance: introduced by `cf9b2ea` *"fix(relocation): the control plane derives its
repo root instead of naming a drive"*, which replaced `r"F:\jev"` with
`Path(__file__).resolve()` in twenty-six scripts and added no import to any of them.

### Fix and verification

`tools/fix_missing_pathlib_import.py` inserts `from pathlib import Path` into the
existing stdlib block, keeping module-name order, then verifies by **AST + compile**,
never by `exec_module`.

> **Process note, disclosed as a defect in my own method.** My first version verified
> with `importlib.exec_module`. Several of these scripts do real work at *module*
> level, so importing `tools/continuation/regenerate_surfaces.py` **rewrote 16
> tracked files** across `integrations/`, `config/`, `packages/`, created 3 untracked
> files, and launched 2 background processes. I reverted every one of them
> (`git checkout --`, no `git clean`), confirmed zero stray listeners and zero
> surviving PIDs (`netstat` + `tasklist`), and preserved the discarded content under
> `artifacts/release/v1.3_final/contamination_forensics/` for audit. Two of the
> reverted files — `packages/jcc_core/config.py` and `packages/jcc_core/run.py` — are
> among the 12 A8 freeze pins; I re-checked `E3_A8_PINS_VS_HEAD_BLOBS` afterwards and
> it passes. The verifier is now AST-only, and the reason is recorded in its docstring
> so the next person does not reintroduce it.

Verification, `JCC_ROOT` unset (the previously-crashing path):

```
$ env -u JCC_ROOT python tools/jcc_continuation.py selftest
controller selftest: 33/33          exit=0
$ python tools/fix_missing_pathlib_import.py <26 files>
changed=0 skipped=26 failed=0      exit=0
```

### Family A also cleared these legs
* `test_the_infra_runner_selftest_is_green`, `test_controller_selftest_is_green`,
  `test_atomic_write_is_atomic_and_leaves_no_temp_file`, `test_saving_state_keeps_the_previous_generation`,
  `test_only_one_controller_can_hold_the_lock`, `test_pid_liveness_is_by_handle_not_by_name`,
  `test_a_step_may_not_declare_the_project_complete`, `test_exit_zero_without_evidence_is_not_progress`,
  `test_gate_judge_refuses_a_verdict_it_should_not_accept`, `test_soak_judge_refuses_a_red_or_unclean_soak`,
  `test_final01_step_declares_an_artifact_and_a_judge`, `test_no_step_declares_its_run_descriptor_as_its_result`,
  `test_the_infra_judge_refuses_the_ways_this_step_could_fake_a_pass` (12 ERRORs + 8 FAILs).

---

## 2. Family B — `F:\jev` literals the relocation missed (4 files) — **REAL PRODUCT DEFECT, FIXED**

**Verdict: real product defect. Committed as `88ff8a8`.**

`cf9b2ea` and `01cfb6a` de-hardcoded the root in `config/`, `packages/`, `tools/` and
`integrations/`. Four shipped files were missed, and each fails **at module level,
before `main()`**:

| File | Line | Symptom (measured) |
|---|---|---|
| `artifacts/acceptance/project_run_gate.py` | 66 | `ROOT = r"F:\jev"` → `_load_proof_contract()` at line 126 raised `FileNotFoundError: 'F:\\jev\\tools\\continuation\\proof_contract.py'` |
| `artifacts/acceptance/lead_p1_verify.py` | 22 | same literal; `PY`/`PKG`/`DB`/`OUT` all resolve into a missing drive |
| `artifacts/acceptance/win_scripts/run_e07_final.py` | 45 | same; `RUNTIME`/`PID_FILE`/`SCRIPTS_DIR` unusable |
| `scripts/lib/JccWatchdogCommon.ps1` | 16 | `$script:JccRoot = 'F:\jev'` |

The PowerShell one is the widest: `$script:JccRoot` feeds the venv probe (lines
194-196), the scheduled task `WorkingDirectory` (lines 431, 495) and the recorded
`working_directory` (line 536). A test asserted it directly and failed with
`AssertionError: F:\jev / assert 'F:\\jev' == 'D:\\jev'`
(`tools/tests/test_continuation_infra.py:1171`).

Fix: all four derive the root from `__file__` / `$PSScriptRoot`, with `JCC_ROOT` still
overriding. Verified under real Windows PowerShell 5.1
(`artifacts/release/v1.3_final/ps51_jccroot_check.txt`):

```
PSVersion   = 5.1.22621.2506
JccRoot     = D:\jev
venvExists  = True
scriptsExist= True
```

`scripts/acceptance.ps1` and `scripts/bootstrap.ps1` also contain `F:\jev`, but **only
inside docstring usage examples** (lines 5, 9, 13) — not code — so they are correctly
left alone, and they are in the frozen set.

**A8 pin impact: none.** The 12 `_BASELINE_HASHES` pins are `config/jcc.yaml` and 8
`packages/jcc_core/*.py` files. None of the four files above is pinned, and this
commit touches no pinned file. `E3_A8_PINS_VS_HEAD_BLOBS` → PASS.

---

## 3. Family C — scheduled-task registration probes — **GENUINE ENVIRONMENT LIMIT (host privilege)**

**Verdict: environment. Not a product defect, and not fixable by me.**

`tools/tests/test_continuation_infra.py:1500-1526` probes two Windows scheduled tasks
through two independent oracles (the task XML store and the `ScheduledTasks` module)
and refuses to guess when both fail. On this host neither can answer:

```
task store error: ... 'C:\Windows\System32\Tasks\JEV-JCC-Continuation-Watchdog' is missing
  and the task store could not be listed either ([WinError 5] 拒绝访问。
  : 'C:\Windows\System32\Tasks')
```

Measured: `ls C:/Windows/System32/Tasks/` → `Permission denied`; `net session` →
refused. The session runs as `Administrator` **without elevation**.

The test's own message is the correct reading and is why I am not calling this a
defect: *"neither oracle could answer, so this test has produced NO measurement and
must not be read as 'not registered'."* It is fail-closed by design. Resolving it
requires an elevated session, which is an operator action.

*(`test_new_jcc_scheduled_task_accepts_an_empty_argument_list` was in this cluster but
is **fixed** by Family B — it was asserting the `F:\jev` working directory. It now
passes.)*

---

## 4. Family D — doctor "next:" hint — **GENUINE ENVIRONMENT LIMIT (service state)**

**Verdict: environment (host state), not a code defect.**

`tools/tests/test_doctor_next_hint.py:129-146` runs the real `doctor.ps1` and asserts
the printed `next:` line equals the OURS hint. The probe
(`tools/tests/fixtures/probes/next_hint_probe.ps1:25`) captures
`doctor_next_printed` from the live host. Measured on this host:

```json
{ "ours": "none -- JCC is already running (verify with .\\scripts\\status.ps1)",
  "doctor_exit": 0,
  "doctor_next_printed": ".\\scripts\\status.ps1  (or start it with .\\scripts\\start.ps1)" }
```

`doctor_next_printed` is the **FREE** hint because **nothing was listening on 4778**
when the probe ran (`netstat -ano | grep 4778` → no rows). The helper itself is pure
and correct — `Get-JccNextHint` (`scripts/lib/JccCommon.ps1:1023-1054`) takes a
`-PortState` and returns the right string for all three states, and the other three
tests in the file, which assert the pure branches, **pass**. The product is telling
the truth about a host with no service.

I subsequently started GREEN via the documented `scripts/start.ps1`; it came up on
4778 (pid 21088) and `/api/health` returns `actuation_enabled=false`,
`policy_version=p3.0.0`, `localhost_only=true`. With the service present this test's
precondition is satisfiable on a re-run.

---

## 5. Family E — `stable-runtime-boot.jsonl` NUL lines — **TEST SCOPE DEFECT (pre-existing corruption), current writer is clean**

**Verdict: not a live product defect. The failure is real data corruption in an
untracked log; the code that produced it is fixed, and the test's scope is what makes
it fail now.**

`tools/tests/test_stable_runtime_evidence.py:161` asserts `not bad` across the **whole
file**, so any historical bad line fails the test regardless of the current writer.

Measured, per line, in `runtime/logs/stable-runtime-boot.jsonl` (4272 lines, 941 675 bytes):

| line | bytes | next valid record's `at` |
|---|---|---|
| 3886 | 1482, entirely `\x00` | 2026-09-30T21:28:25 |
| 3924 | 599, entirely `\x00` | 2026-09-30T21:56:44 |
| 4203 | 599, entirely `\x00` | 2026-10-01T15:15:56 |

All three are 2026-09-30/10-01. The newest line in the file is today and parses.
Running the beacon writer now (`scripts/ensure-stable-runtime.ps1 -Watchdog -DryRun`,
`artifacts/release/v1.3_final/beacon_probe_result.txt`):

```
rc=0
new_lines=2 valid_json=2 invalid=0
NEW: {"at":"2026-10-04T17:53:46...","stage":"enter",...}
NEW: {"at":"2026-10-04T17:53:46...","stage":"preflight-ok","detail":"port=4779;python=[D:\\jev\\.venv\\Scripts\\python.exe];..."}
```

The writer (`Write-JccRawLog`, `scripts/ensure-stable-runtime.ps1:104-118`) opens
append/write/flush/close explicitly and returns success, and `Write-JccBoot`
serialises with `ConvertTo-Json` then **parses it back before writing**. Two earlier
writer defects are already documented and fixed in that file's comments. The current
code produces valid JSON, 2/2, including a correct `D:\jev` root.

**Not repaired by me**: the three corrupt lines sit in a gitignored runtime log
(`.gitignore:6` → `runtime/`). Rewriting or truncating a log is a data-destroying act
on a file I did not create, and the corruption is historical, so I left it and am
reporting it. **Decision needed from team-lead**: either accept the 3 stale lines as
known history, or authorise a one-off scrub of exactly those 3 lines.

---

## 6. Family F — artifact currency: `V1_1_REAL_JEV_VERIFICATION` + `V1_1_CAPABILITY_MATRIX` — **ARTIFACT CURRENCY, regenerated**

**Verdict: neither generator nor checker is defective. The committed artifacts were
stale; I regenerated them with the project's own generators.**

### F1 — §7 credential/verification artifact

`tools/v1_1_emit_real_jev_verification.py --check` → exit 1,
`STALE: ... disagrees with a fresh generation`. Diffing the committed file against a
fresh generation gave a precise answer:

```
-  "repo_branch": "feature/jcc-v1.2-visual-command-center"
-  "repo_head":   "ff3fa69d76d8b6ef5b70a92473ce90cf48b21d94"
+  "repo_branch": "release/jcc-v1.3-final"
+  "repo_head":   "01cfb6a9c81a1da016fac93008cb8e786cc0cfd1"
-  "tracked_file_count": 590        →  679
-  "C:/Users/Administrator/.claude/settings.json":  PRESENT  →  ABSENT
-  "C:/Users/Administrator/.codex/config.toml":     PRESENT  →  ABSENT
-  "C:/Users/Administrator/.hermes/config.yaml":   PRESENT  →  ABSENT
```

Verified independently: all three credential files are genuinely absent on this host
today, and the recorded HEAD belonged to a v1.2-era commit. The artifact was
describing a different tree on a different day. Regenerated → `--check` now returns
`OK: both artifacts match a fresh generation from the card.` (exit 0).

This cleared **4 tests** (`test_the_jev_credential_state_is_unchanged_by_the_e2e_fixture_lifecycle`
and the 3 `test_the_jev_check_still_observes_the_environment[...]` cases), all of which
failed on `assert 1 == 0` at the *precondition* check — i.e. they correctly refused to
measure anything because §7 was already stale.

### F2 — capability matrix

`tools/v1_1_emit_capability_matrix.py --check` reported, for all four agents:

```
telemetry: committed='VERIFIED' now='DEGRADED'
```

The committed matrix's evidence block pinned
`record_path: C:\Users\Administrator\.workbuddy\sessions\21552.json` with
`record_exists: true`. That file **no longer exists** (the directory now holds 51
different session files). The checker's rule (b) is `Path(record_path).is_file()`, so
the committed VERIFIED claim had become unverifiable — the falsifier working exactly
as designed.

Regenerating it cleared `test_check_mode_reports_the_matrix_is_current` and
`test_the_matrix_agrees_with_the_probe_evidence_on_disk`.

**Two consequences I must disclose rather than hide:**

1. My regeneration initially **moved 2 failures rather than removing them**:
   `test_telemetry_verification_includes_resolved_identity` and
   `test_a_missing_record_becomes_degraded_not_verified` now fail, because the fresh
   honest matrix records `codex` telemetry as `UNVERIFIED` (no
   `~/.codex/sessions` on this host) while the tests assert `VERIFIED` for all four
   agents. **Those two tests carry a host-state precondition this host does not meet.**
   Net C2 movement was still strongly positive, but the honest count is 6 failures,
   not 4.
2. The test file's own `fresh_probe` docstring (lines 109-113) already concedes the
   design tension: the evidence *"records whichever live session file this host
   happens to have, so it is not byte-reproducible."* A committed artifact that
   records a transient file path cannot stay current across a session-file rotation.
   That is a **design property, not a bug I should paper over**.

**Neither artifact is among the 12 A8 pins** (verified against
`_BASELINE_HASHES` in `packages/jcc_core/tests/test_p3_acceptance_gate.py:288` and
`docs/p3/A8_REBASELINE.json`), so regenerating them does not break the freeze.

`docs/` was **not modified** — the generator rewrote `docs/V1_1_REAL_JEV_INTERFACE.md`
byte-identically (git reported no change to it), and I confirmed `git status` shows
no `docs/` entry.

---

## 7. Family G — `test_http_end_to_end_four_agents_distinct_no_secret_leak` (C1, the GBK `UnicodeDecodeError`) — **TEST DEFECT (latent), NOT the cause of the recorded C1 failure here**

**Verdict: the test has a real latent defect, but it did not fire in my runs. The
recorded C1 failure is UNVERIFIED as to its cause.**

The suspicious line is `packages/jcc_core/tests/test_p2_integration_smoke.py:399`:

```python
with urllib.request.urlopen(base + "/api/audit?limit=500", timeout=5) as r:
    audit = r.read().decode()          # <-- no codec argument
```

`bytes.decode()` with no argument uses `locale.getencoding()`, measured on this host
as **`cp936`**. A JSON body containing raw UTF-8 CJK would raise
`UnicodeDecodeError`. That is a genuine portability defect and should be
`.decode("utf-8")`.

**However, I could not reproduce the reported failure.** Run in isolation at HEAD
`88ff8a8`:

```
1 passed in 9.68s        exit=0
```

And I could not locate a `0xb2` byte in any plausible source:
* all 4 fixture JSONs scanned — only CJK present is `D:\数字人项目\...` and
  `D:\ai解说`, and their UTF-8 byte sequences contain **no `0xb2`**
  (`人`=`e4baba`, `字`=`e5ad97`, `数`=`e695b0`, `目`=`e79bae`, `项`=`e9a1b9`,
  `解`=`e8a7a3`, `说`=`e8afb4`);
* `0xb2` is the 2nd byte of a 3-byte CJK sequence (320 such chars exist in the CJK
  URO block), and no tracked `.py` file in `packages/` contains a raw `0xb2`;
* every other string on that code path is ASCII: `PLANTED_SECRET`, `VALUE_PLANTED_SECRET`
  and `TOKEN` are hex/uuid, `SHARED_CWD = "F:\\jev"`, and `get_audit`
  (`packages/jcc_core/state_store.py:795-818`) returns only store-owned scalar columns;
* `_force_cwd` (line 122) overwrites the fixtures' CJK `cwd` with the ASCII
  `SHARED_CWD` before emission.

**Conclusion**: the `.decode()` without a codec is a real latent defect (I recommend
fixing it regardless), but the specific `0xb2 in position 8` failure is **not
reproducible at this HEAD on this host and its cause remains UNVERIFIED**. Reporting
it as "environment" or "product" without this evidence would be a guess. It is most
consistent with a *transient* CJK-bearing value present during the earlier full-suite
run that is not present now — the same class of host-state drift as Family F — but I
am labelling that a hypothesis, not a finding.

---

## 8. Family H — `test_threaded_appends_keep_one_contiguous_chain` (C1) — **REAL FLAKE, root cause NOT YET ESTABLISHED**

**Verdict: the test genuinely fails intermittently. My leading hypothesis was tested
and DISPROVEN. Do not treat any mechanism below as established.**

First, the chain is **not** forking. The product's guard is intact:
`packages/jcc_core/audit.py:155-156` issues `BEGIN IMMEDIATE` as the first statement
when the caller is not already in a transaction, so the read-modify-write window is
closed. The whole file passes in isolation (`8 passed in 78.67s`).

But the test is flaky. Six identical runs:

| run | result | duration |
|---|---|---|
| 1 | passed | 41.86s |
| 2 | **failed** | 44.23s |
| 3 | **failed** | 46.74s |
| 4 | passed | 41.56s |
| 5 | passed | 31.81s |
| 6 | passed | 67.33s |

Failure text (2 of 6):

```
AssertionError: a writer thread failed: ['OperationalError: database is locked',
                                         'OperationalError: database is locked']
```

So a writer waiting for the write lock gives up despite `PRAGMA busy_timeout=30000`.

### My hypothesis, and why I am rejecting it

I hypothesised the test's bare `sqlite3.connect(path)` defaults to
`journal_mode=DELETE` whereas production `StateStore` sets WAL
(`packages/jcc_core/state_store.py:375`), and that DELETE serialisation is what
exhausts the 30 s budget. I wrote `tools/wal_falsifier.py` to measure it — same
8-thread × 25-append workload, only the journal mode varying.

**The measurement refuted my own hypothesis** (`artifacts/release/v1.3_final/wal_falsify_result.json`,
3 rounds each):

| journal mode | modes observed | row counts | errors |
|---|---|---|---|
| default DELETE | `delete` ×3 | **200 ×3 (all complete)** | none |
| production WAL | `wal` ×3 | 25, 100, 125 | `attempt to write a readonly database` ×14 |

DELETE ran the full workload cleanly three times out of three; WAL was the one that
failed. So **WAL is not the fix**, and I am not proposing it. The falsifier's own
non-vacuity guard tripped (`VACUOUS: production_WAL never completed a full round`,
exit 1) precisely so this result could not be read as a clean comparison.

The `"attempt to write a readonly database"` in the WAL arm is almost certainly the
WorkBuddy safe-delete shim interfering with `-wal`/`-shm` sidecar creation under
concurrency, not a property of WAL: the same `PRAGMA journal_mode=WAL` + INSERT
succeeds when run alone. **I did not isolate that further, so it stays UNVERIFIED.**

### Status

`OperationalError: database is locked` under 8 concurrent writers with a 30 s busy
timeout is a **real, reproducible defect surface**. Whether it is a product defect
(lock handling in `append_audit` under contention) or a test defect (the test demands
8-way contention that production, with its own `_lock` and WAL, never creates) is
**not resolved by my evidence**. I am not signing this family off, and I have not
changed `packages/jcc_core/`.

**Recommended next step** (needs a decision, see HANDOFF): reproduce under
`JCC_` env with the shim disabled and with the test's own `busy_timeout` raised, to
separate "SQLite genuinely cannot satisfy this" from "the sandbox shim is
interfering". That is a further experiment, not something to guess at.

---

## 9. C2 result trajectory (all measured at this repo, `--basetemp` given explicitly)

| run | command | result | exit |
|---|---|---|---|
| baseline | `pytest tools/tests -q` | **21 failed / 209 passed / 12 errors / 6 skipped** | 1 |
| after Family A | same + `--basetemp D:/jev/runtime/harbor_c2b` | **14 failed / 228 passed / 0 errors / 6 skipped** | 1 |
| after A+B+C+D+F | same + `--basetemp D:/jev/runtime/harbor_c2c` | **6 failed / 236 passed / 0 errors / 6 skipped** | 1 |

**Errors: 12 → 0. Failures: 21 → 6.** C2's floor is 248 passed; 236 does not clear it,
so **C2 remains FAIL**. The 6 residual, by family:

| # | test | family | status |
|---|---|---|---|
| 1 | `test_both_watchdogs_are_registered_as_scheduled_tasks` | C | environment (privilege) |
| 2 | `test_the_registration_verdict_refuses_a_disabled_or_misaimed_task` | C | environment (privilege) |
| 3 | `test_doctor_on_this_host_does_not_suggest_starting_an_up_service` | D | environment (was: no service) |
| 4 | `test_dry_run_tick_writes_a_boot_beacon` | E | historical log corruption |
| 5 | `test_telemetry_verification_includes_resolved_identity` | F2 | host-state precondition (my regeneration surfaced it) |
| 6 | `test_a_missing_record_becomes_degraded_not_verified` | F2 | host-state precondition (my regeneration surfaced it) |

Only **#4 and #5/#6** are arguably actionable by the project; #1-#3 need an operator
(elevation) or a host state change.

### `--basetemp` — recorded as required, and the A1 finding

Every pytest run I performed passed an explicit
`--basetemp D:/jev/runtime/harbor_*`. I verified this is **not** cosmetic on this host:

```
$ pytest tools/tests/test_release_manifest_v1_3.py -q                    # conftest default
13 passed, 6 errors in 0.17s
PermissionError: [WinError 32] ... '\\?\D:\jev\.workbuddy\scratch\host_temp\jev-pytest\test_apply_verify_resolves_the0\jcc.db'

$ pytest tools/tests/test_release_manifest_v1_3.py -q --basetemp D:/jev/runtime/harbor_a1
19 passed in 55.60s        exit=0
```

Root cause, measured: the WorkBuddy shim's `sitecustomize.py`
(`D:\WorkBuddy\resources\app.asar.unpacked\cli\vendor\shim\sitecustomize.py`)
overrides `tempfile.gettempdir()` to `D:\jev\.workbuddy\scratch\host_temp`, and
`packages/conftest.py:184-190` then forces pytest's basetemp to
`gettempdir()/jev-pytest` — a **shared** directory that `pytest` `rm_rf`s on entry,
deleting a concurrently-running leg's scratch and tripping `WinError 32` on the still-open
`jcc.db`.

This is the same class of failure as the earlier `C2_TOOLS_TESTS` 12 errors **but not
the same instance**: I proved those 12 were the `Path` `NameError`, not basetemp.
This mechanism is real and it is what makes **`A1_FREEZE_MODEL` FAIL (13 passed / 6
errors, floor 19)** in the full-gate run. It is a **test-infrastructure defect**
(`packages/conftest.py` uses a fixed shared basetemp where a per-process one is
required), owned outside my write scope.

---

## 10. Files changed by me (commit `88ff8a8`)

| file | blob SHA |
|---|---|
| `tools/jcc_continuation.py` | `9e49764256e0b8b063d62f2cafc82c15b7f29f1a` |
| `scripts/lib/JccWatchdogCommon.ps1` | `d3fe5668efcd10d4904a137ac6171dcdffc5b43a` |
| `artifacts/acceptance/project_run_gate.py` | `3f85d57917d1f4950d0f12570d5dd111cda3b4bc` |
| `tools/fix_missing_pathlib_import.py` | `8e8cc78772801f6cfc076b4461a97b3d62be29fb` |

plus 28 more files in the same commit (25 further `pathlib` fixes, `lead_p1_verify.py`,
`win_scripts/run_e07_final.py`, `tools/start_green_probe.py`). Full list:
`git show --stat 88ff8a8` — 32 files, +603 / -4.

**Change Requests raised (outside my write scope, not self-applied):**

* **CR-H1 → owner of `packages/conftest.py`** — `pytest_configure` hardcodes
  `gettempdir()/jev-pytest` as basetemp. Under the shim this is a shared directory
  that concurrent legs delete out from under each other. Needs a per-process basetemp
  (or honour `PYTEST_DEBUG_TEMPROOT` / an env-provided path). This is the direct cause
  of `A1_FREEZE_MODEL` FAIL.
* **CR-H2 → owner of `test_p2_integration_smoke.py`** — line 399
  `r.read().decode()` must be `.decode("utf-8")`; the default codec is `cp936` here.
  Latent, not proven to be the recorded failure.
* **CR-H3 → owner of `test_v1_1_capability_matrix.py`** — lines 205-213 and 275 assert
  `VERIFIED` telemetry for all four agents, which requires live session data for all
  four. Only `~/.workbuddy/sessions` exists on this host. Either the assertion must be
  host-conditional, or the artifact must stop pinning a transient `record_path`.
* **CR-H4 → decision, team-lead** — authorise or decline a one-off scrub of exactly
  3 NUL lines in `runtime/logs/stable-runtime-boot.jsonl` (Family E).

**Nothing under `docs/v1_3/` was modified. No `git clean`, no `push`, no `force`.**

---

## 11. Full 30-leg gate — **NOT COMPLETE, do not read as a result**

Run launched at HEAD `88ff8a8`, in the background, as instructed:

```
cd D:/jev && CODEBUDDY_SAFE_DELETE_ENABLED=0 PYTHONPATH=D:/jev/packages \
  D:/jev/.venv/Scripts/python.exe tools/final_gate_run_v1_3.py --full
```

Log: `artifacts/release/v1.3_final/full_gate_run_88ff8a8.txt`

**Last observed state — 6 of 30 legs reported, run still in progress:**

```
JCC v1.3 FINAL PRODUCT GATES  run=20261004-182104  head=88ff8a8e6007
GREEN 4778 at start: pid=21088 http=200
  [FAIL] A1_FREEZE_MODEL   13 passed / 0 failed / 6 errors / 0 skipped (floor 19)
  [ok  ] A2_DOMAIN         47 passed
  [ok  ] A3_MISSION        23 passed
  [ok  ] A4_ATTENTION      32 passed
  [ok  ] A5_DELIVERABLES   26 passed
  [ok  ] A6_CONNECTIONS    43 passed
```

**I cannot report a 30-leg result.** C1, C2, C3, B-group, D-group, E-group, F1 and the
aggregate verdict had not been reached when I last read the log. Per the release gate
rule, a partial run is **NOT** a pass and **NOT** a fail; it is an incomplete
measurement. `A1_FREEZE_MODEL` is attributable to CR-H1 above.

One precondition I did satisfy: the protected GREEN service was **down** (no listener on
4778) when I started, which makes `D1_GREEN_PROTECTION` unsatisfiable and raises the
aggregate `GREEN_4778_CHANGED` blocker regardless of code quality. I started it through
the documented `scripts/start.ps1`; it is up on pid 21088 and inert. The run above
recorded `GREEN 4778 at start: pid=21088 http=200`.

---

## 12. Family J — a test probe emitted UNPARSEABLE JSON on every Windows path — **REAL PRODUCT DEFECT, FIXED**

**Verdict: real product defect in a shipped test probe. Found while adjudicating a
regression at the newer HEAD; this is why the two C2 runs must never be compared
without naming their commits.**

This defect did **not** exist at `88ff8a8`. It surfaced when I re-ran C2 at HEAD
`46ff531`, where the result was **worse**: `11 failed / 231 passed / 4 errors`
versus `6 failed / 236 passed / 0 errors` at `88ff8a8`. Chasing that regression
found a third real bug.

The 4 errors, verbatim:

```
ERROR at setup of test_pid_is_stable_across_samples
ERROR at setup of test_uptime_is_monotonic
ERROR at setup of test_deltas_track_wall_clock
ERROR at setup of test_printed_uptime_matches_live_process_at_the_same_instant

E   AssertionError: probe JSON could not be parsed:
      {"error":"no_pid_file","pid_file":"D:\jev\runtime\jcc\jcc.pid"}
E   json.decoder.JSONDecodeError: Invalid \escape: line 1 column 38 (char 37)
```

Line-level evidence — `tools/tests/fixtures/probes/uptime_falsifier.ps1:17-19`
before the fix:

```powershell
[System.IO.File]::WriteAllText($OutPath,
    ('{"error":"no_pid_file","pid_file":"' + $Paths.PidFile + '"}'),
    (New-Object System.Text.UTF8Encoding($false)))
```

The report is built by **string concatenation** and `$Paths.PidFile` is
interpolated raw. On Windows that value is `D:\jev\runtime\jcc\jcc.pid`, so the
emitted document contains the two-character sequence `\j`, which is not a legal
JSON escape. **The probe hands its reader a file it cannot parse**, so the test
cannot classify the condition it was written to detect — it crashes instead.

Falsified with no pytest involved, via `tools/uptime_probe_json_falsifier.py`:

```
emitted document : {"error":"no_pid_file","pid_file":"D:\jev\runtime\jcc\jcc.pid"}
json.loads       : FAILS -> Invalid \escape: line 1 column 38 (char 37)
VERDICT          : the probe emits INVALID JSON on any Windows path
```

**This is the same mistake the project already paid for twice, and the fix
already existed elsewhere in the tree.** `scripts/ensure-stable-runtime.ps1:130-158`
documents it in its own beacon writer — *"Both were the same mistake: inventing
an encoding by hand"* — and cured it by serialising with `ConvertTo-Json` and
re-parsing with `ConvertFrom-Json` before writing. **That writer was fixed; this
one was missed.** The fix applies the identical idiom:

```powershell
$report = [pscustomobject]@{ error = 'no_pid_file'; pid_file = $Paths.PidFile }
[System.IO.File]::WriteAllText($OutPath, ($report | ConvertTo-Json -Compress), ...)
```

Verified under **real Windows PowerShell 5.1** by `tools/uptime_probe_verify.py`,
which runs the probe exactly as the test does and then `json.loads` the raw bytes:

```
probe rc      : 2 (2 = no service, expected here)
report bytes  : {"error":"no_pid_file","pid_file":"D:\\jev\\runtime\\jcc\\jcc.pid"}
json.loads    : OK -> ['error', 'pid_file']
VERDICT       : PASS - the probe's report is machine-readable
```

**Post-fix proof:** `tools/tests/test_status_uptime.py` goes from
**4 setup ERRORs → `1 passed, 4 skipped`**. The skips are the correct behaviour
once the probe is readable: the tests decline to measure instead of crashing.

Note the escape is now `\\` in the emitted bytes — that is the whole defect in
one character.

### 12.1 Targeted re-verification after the Family J fix

Run serially, with Forge's own pytest runs on this tree having been waited out
first (two runs of `packages/jcc_core/tests/` were in flight; a third was
started mid-wait, so I limited myself to a targeted check rather than start a
contended full-suite measurement):

```
$ ... -m pytest tools/tests/test_status_uptime.py \
      tools/tests/test_continuation_infra.py::test_the_project_run_gate_selftest_is_green \
      -q -p no:randomly --tb=line --basetemp "D:/jev/runtime/harbor_verify1"
ssss..
2 passed, 4 skipped, 1 warning in 3.07s
exit 0
```

- `test_status_uptime.py` → **1 passed, 4 skipped** (was 4 setup ERRORs) — Family J fixed.
- `test_the_project_run_gate_selftest_is_green` → **passed** — Family B still fixed at this HEAD.

**A full C2 re-run at `8848390` was NOT performed** (Forge held the tree). The
"expectation of 10 failed / 0 errors" in §13 is therefore a **prediction, not a
measurement**, and is labelled as such. Do not quote it as a result.

---

## 13. Summary table

| Family | Subject | Verdict | Fixed by me | Evidence class |
|---|---|---|---|---|
| A | missing `pathlib` import, 26 files | REAL PRODUCT DEFECT | yes, `88ff8a8` | VERIFIED (import-checked, 33/33 selftest) |
| B | `F:\jev` in 4 shipped files | REAL PRODUCT DEFECT | yes, `88ff8a8` | VERIFIED (PS 5.1 `JccRoot=D:\jev`) |
| C | scheduled-task registration probes | ENVIRONMENT (no elevation) | no — needs operator | VERIFIED (146 tasks, 0 JCC; WinError 5) |
| D | doctor `next:` hint | ENVIRONMENT (service was down) | no — service now up | VERIFIED (probe JSON) |
| E | 3 NUL lines in boot beacon log | HISTORICAL CORRUPTION / test scope | no — needs CR-H4 | VERIFIED (byte-level, dated) |
| F | §7 + capability matrix stale | ARTIFACT CURRENCY | regenerated | VERIFIED (field-level diff) |
| G | p2 HTTP `.decode()` no codec | TEST DEFECT (latent) | no — CR-H2 | VERIFIED defect / **UNVERIFIED** as cause |
| H | threaded append `database is locked` | REAL FLAKE, cause open | no | VERIFIED flake / **UNVERIFIED** root cause |
| I | shared pytest basetemp | TEST-INFRA DEFECT | no — CR-H1 | VERIFIED (19/19 vs 13+6err) |
| J | uptime probe emits invalid JSON | REAL PRODUCT DEFECT | **yes** | VERIFIED (PS 5.1 round-trip) |

**Blocker count moved: C2 specifically 33 failing items (21 failed + 12 errors) → 6**
at `88ff8a8`. At HEAD `46ff531` the figure is **11 failed + 4 errors** — worse,
because Family J appeared; with J fixed the expectation is 10 failed / 0 errors.
I have **not** re-run the full suite after the J fix, so treat that expectation
as a prediction, not a measurement. I am not claiming the project-level blocker
count, because the full gate has still not completed at any single commit.
