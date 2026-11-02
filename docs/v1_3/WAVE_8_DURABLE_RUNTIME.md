# WAVE 8 — Windows Durable Product Runtime

> v1.3 PRODUCT EDITION. Turn "start / stop / restart / status / doctor / repair"
> from a folder of scripts into **one fail-closed control surface backed by a
> durable Windows runtime** — and replace the stdlib demo server in the serving
> path with a real ASGI server.
> Owner: Harbor (Windows runtime) / Forge (control surface) / Aegis (fail-closed
> review) / Atlas (config + ports) / Prism (UI runtime) / Metric (gate).

## 1. The hole this Wave closes

The order was explicit on three points: **"正式 Start/Stop/Restart/Status/Doctor/
Repair JCC"**, **"BLUE API/UI 由 durable Windows runtime 托管
(Register-ScheduledTask)"**, and **"不用 python http.server 作为成熟产品
runtime"**. v1.2.1 satisfied none of them:

1. **Six scripts, no entry point.** `start-jcc-v1.2.ps1`, `stop-…`, `restart-…`,
   `status-…`, `start-jcc-v1.2-visual-ui.ps1`, `stop-…-visual-ui.ps1`,
   `mark-jcc-v1.2-runtime.ps1`. `restart` existed only to call the other two in
   the right order, and an operator had to know which of **six** files to run.
2. **No durability at all.** A launcher script is not a runtime: the processes it
   starts live only as long as nothing reaps them, and nothing comes back after a
   logoff or a reboot. Windows' durable primitive is Task Scheduler, and no task
   existed.
3. **The UI was served by the stdlib demo server.** `serve_visual.py` used
   `ThreadingHTTPServer` + `SimpleHTTPRequestHandler`. Python's own documentation
   says of `http.server`: *"not recommended for production, as it only implements
   basic security checks."* Shipping that as the product's UI runtime is exactly
   what the order forbade.

## 2. The two rules this Wave hangs on

> **Rule 1 — A launcher is not a runtime.** Durable ownership belongs to Task
> Scheduler (AtStartup, SYSTEM, restart-on-failure ×3, no execution time limit);
> the launcher stays the operator's control surface. The consequence, stated
> rather than hidden: a task-launched instance was not started by a launcher, so
> no launcher wrote an ownership marker for it — it must be adopted explicitly
> with `-Action mark` before it counts as ours.

> **Rule 2 — A verdict must come from a source that can actually see the truth.**
> Three instruments available in this session were measurably **blind**, and each
> had to be replaced rather than trusted:
>
> | Blind instrument | What it did | What replaced it |
> |---|---|---|
> | `Get-ScheduledTask` | answers `CimException` ("the system cannot find the file specified") for **every** task, including pre-existing ones | the task store on disk: `C:\Windows\System32\Tasks\<name>` |
> | `Register-ScheduledTask`'s return value | returns without error whether or not anything happened | an on-disk read-back **after** the call |
> | substring scans of source text | cannot tell a command from a sentence about that command | the PowerShell parser (`CommandAst`), the Python AST, the process list |

Rule 2 is the reason §7 exists: applying it to my own instruments found four
defects in them.

## 3. The contract

### 3.1 Serving path — uvicorn (ASGI), not `http.server`

`apps/command_center_3d/serve_visual.py` was rewritten to build a Starlette
application and run it under uvicorn:

- `LOOPBACK = "127.0.0.1"`; a non-loopback `--host` is **refused with exit 3
  before binding**, so a refused start cannot leave a listener behind.
- `GET /health` and `GET /api/health` return
  `{"runtime": "uvicorn-asgi", "port": <bound port>, "dist": <dir>}`.
- Static files are served through `FileResponse` / `StaticFiles`; a `--dist` that
  is missing `index.html` exits 2.
- Path containment: `_safe_target()` `resolve()`s the join and rejects anything
  not strictly inside the dist root (drive-letter / UNC / symlink escape).

The prohibition is enforced by an **AST** audit, `tools/v1_3_ui_runtime_audit.py`,
not by a grep — see §7.1 for the false FAIL that forced this.

### 3.2 Port ownership (canonical, in `config/jcc-v1.3.yaml`)

| Port | Owner |
|---|---|
| 4778 | **GREEN** — frozen v1.0.1 service. Read-only from v1.3; never started, stopped or signalled. |
| 4779 | **PRODUCT API** — `server.port` in this config. |
| 4780 | **PRODUCT UI** — the built SPA, served by `serve_visual.py` under uvicorn. |
| 4781 | DEV UI (`npm run dev`). Never a production surface. |
| 4782 | smoke/diagnostic only. Must not be used as a production port. |

The config also pins `server.host: 127.0.0.1`, `actuation.enabled: false`,
`jev.provider: mock`, `policy.version: p3.0.0`, `p3.shadow.enabled: false`, and a
`F:/jev/runtime/jcc-v1.3/jcc.db` state store isolated from GREEN and from v1.2.

### 3.3 The one control surface — `scripts/jcc-v1.3.ps1`

```
-Action up | down | restart | status | doctor | repair | mark
```

Every safety rule of v1.2 is kept; only the number of files changed. The hard
rule, unchanged:

> This script **never signals a process it did not start and cannot positively
> identify.** "Identify" means *all* of: an ownership marker we wrote; the pid
> file agreeing with that marker; the process alive; its command line being the
> expected service start line for **this** repo; the listening owner of the
> expected port being that pid (or nobody); and the pid descending from a process
> that really ran that start line. Anything else — a foreign holder, a
> marker/pid mismatch, a live-but-unverifiable pid — produces a **REFUSAL**, not
> a signal.

Exit codes are part of the contract (the gate and Task Scheduler rely on them):

| Action | Codes |
|---|---|
| `up` | 0 up+healthy · 3 refused (foreign/prereq) · 4 did not become healthy |
| `down` | 0 stopped (or already stopped) · 2 refused (would not signal) · 3 foreign hold / missing prerequisite · 4 stop did not complete |
| `restart` | 0 verified back up · 3 refused before anything · 4 a half failed · 6 started but the post-condition does not hold |
| `status` | 0 UP · 2 DEGRADED (or answering-but-unverified) · 1 DOWN |
| `doctor` | 0 every check PASS · 1 one or more FAIL |
| `repair` | 0 nothing unsafe left · 2 a condition it deliberately did not touch (needs a human) · 3 missing prerequisite |
| `mark` | 0 both halves marked · 1 a half could not be verified |

Two properties worth naming:

- **`repair` moves, it never deletes.** Stale bookkeeping is quarantined by RENAME
  to `<name>.stale-<stamp>`, verified on both sides (source gone AND destination
  present). `repair` has no code path that signals a process, and it never probes
  GREEN. The header prose here once said "delete"; the code has always moved, so
  the prose was corrected to match the code — the gate asserts the move.
- **`-DryRun` is a pre-flight, not a failure.** It runs the prerequisite gate and
  the port reconciliation, prints what *would* happen, changes nothing (it does
  not even create the runtime dir or remove a stale pid file), and exits 0.
  Precondition failures still exit 3, because those are findings about the host
  rather than about the dry run.
- **`-LogFile` records from inside the process** (`Start-Transcript`). A
  caller-side redirect is provably insufficient here: two entire successful runs
  (`-Action up`, then `-Action restart`) left **no** log at all, because the
  caller was torn down the moment the child processes appeared and the trailing
  write never executed.

### 3.4 Durable registration — `scripts/jcc-v1.3-register-tasks.ps1`

| Task | Action |
|---|---|
| `JCC-v1.3-API` | `cmd.exe /c "set JCC_CONFIG=… && set PYTHONPATH=… && "<venv>\python.exe" -m jcc_core.run"` |
| `JCC-v1.3-VisualUI` | `cmd.exe /c "set PYTHONPATH=… && "<venv>\python.exe" serve_visual.py --host 127.0.0.1 --port 4780"` |

Both set their environment **explicitly**, so neither depends on ambient state.
**Writing is opt-in**: a bare run prints the plan and changes nothing;
`-Apply` is required to touch the host; `-WhatIf` is the platform's own common
parameter (the script declares `SupportsShouldProcess`) and every
host-modifying call routes through `$PSCmdlet.ShouldProcess`.

**Why the tasks run the servers directly** rather than
`jcc-v1.3.ps1 -Action up`: that script would start the server as a detached child
and then `exit 0`, so Task Scheduler would see a *successfully completed* action
and its restart-on-failure setting would never fire. Running the server itself is
what makes the task's liveness, restart count and boot trigger mean anything.

The task XML actually written on this host (saved at
`.workbuddy/scratch/w8_registered_task_api.xml`, sha256 `884bdda70c63ec1b…`):

```xml
<BootTrigger><Enabled>true</Enabled></BootTrigger>
<Principal id="Author"><UserId>S-1-5-18</UserId><RunLevel>HighestAvailable</RunLevel></Principal>
<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
<StartWhenAvailable>true</StartWhenAvailable>
<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>          <!-- 0 = no limit: a long-lived service -->
<RestartOnFailure><Interval>PT1M</Interval><Count>3</Count></RestartOnFailure>
<Command>cmd.exe</Command>
<WorkingDirectory>F:\jev</WorkingDirectory>
```

That is the host-reboot-recovery configuration the order asked for: AtStartup +
restart-on-failure + no execution time limit + single-instance.

## 4. Falsifiers

The gate is built so each claim can fail. The load-bearing ones:

1. **The serving path still uses the demo server** → the AST audit's synthetic
   fixtures (`import http.server`, `from http import server`) must be REJECTED,
   and a docstring *mentioning* `http.server` must be ACCEPTED.
2. **The audit is vacuous** → an empty file and a file without uvicorn/Starlette
   positives must both FAIL.
3. **The server binds the world** → `--host 0.0.0.0` must exit 3 **and** leave the
   port unbound.
4. **A dry run mutates the host** → the runtime dir is fingerprinted before and
   after `-Action up -DryRun`.
5. **The stop path signals a foreign process** → with an unrelated listener on the
   port, `-Action down` must refuse (non-zero + `REFUSED`) and the foreign
   listener must still hold its port afterwards; our own surfaces must also be
   untouched by the refused run.
6. **`repair` deletes** → the stale files must still exist, renamed; a live but
   unverified pid must be refused with its pid file intact.
7. **The registration script writes without being asked** → the Windows task store
   is hashed before and after a bare run and must be unchanged.
8. **`repair` could signal** → the PowerShell **parser** must resolve no
   signalling/deleting command anywhere in repair's transitive call graph, while
   resolving at least one (non-vacuity).
9. **doctor lies about a task** → its verdict must agree with the on-disk task
   store (§7.5).
10. **GREEN is disturbed** → port 4778's pid and HTTP status are identical before
    and after the whole gate.

## 5. Boundaries held

- **Actuation stays OFF** (`actuation.enabled: false`, asserted three ways: the
  config, the live `/health` JSON, and the gate).
- **GREEN 4778 is read-only**: pid 7588 and HTTP 200 identical before and after
  every leg; `down` prints *"GREEN 4778 was never examined"*.
- **No secrets are read or printed**: the control token is reported only as
  SET / UNSET.
- **No route surface change**: `OPEN_GET_ENDPOINTS` stays 27, the A11 POST set is
  unchanged, and the security artifact sha is unchanged — so **no A8 window is
  needed for this Wave** (§8).
- **Nothing is deleted** (`PERMANENT_DELETE=0`); quarantine is by rename.

## 6. MEASURED INCIDENT — the registration that happened while the instrument said it hadn't

This is the most important thing in this document.

The first version of `jcc-v1.3-register-tasks.ps1` attempted registration whenever
Task Scheduler *looked* usable, and then verified the result with
`Get-ScheduledTask`. In this session that verification is **blind**: it answers
`CimException` for every task, including the pre-existing `JEV-JCC-*` tasks that
demonstrably exist. The script therefore reported *"reported success but
re-reading the task found nothing → PENDING_EXTERNAL_ACTION"* — while, checked
against the filesystem, the task **had in fact been created**.

A verification step that cannot see anything does not turn a real write into a
non-write. It only makes the report wrong, and a wrong report in the *safe*
direction is still a wrong report.

Two conclusions were baked into the revision:

1. **Writing is opt-in** (`-Apply`), because modifying a host's boot behaviour is
   a deliberate act.
2. **The verdict comes from the filesystem**, not from the CIM provider
   (`C:\Windows\System32\Tasks\<name>`), and provider blindness is reported as
   *blindness*, never as *absence*.

**Consequence, left on this host and requiring an operator.** The incident left
two stray tasks that this session could not remove: `Unregister-ScheduledTask`
goes through the same blind provider, `schtasks.exe` is hard-blocked by the
Program Blacklist with an explicit instruction not to work around it, and this
script will not hand-delete task files (that leaves the TaskCache index
inconsistent):

```
C:\Windows\System32\Tasks\JCC-v1.3-API       (created 2026-09-29 12:15:03, 4176 bytes)
C:\Windows\System32\Tasks\JCC-v1.3-PROBE     (created 2026-09-29 12:15:18, 3036 bytes)
```

On the **HOST**, in an elevated PowerShell:

```powershell
Unregister-ScheduledTask -TaskName JCC-v1.3-API     -Confirm:$false
Unregister-ScheduledTask -TaskName JCC-v1.3-PROBE   -Confirm:$false
# then register the product runtime for real:
powershell -ExecutionPolicy Bypass -File scripts\jcc-v1.3-register-tasks.ps1 -Apply
.\scripts\jcc-v1.3.ps1 -Action mark
.\scripts\jcc-v1.3.ps1 -Action status
```

`doctor` D-13 will report both strays as `PRESENT` until then, which is correct —
they are present.

## 7. Four defects found in MY OWN instruments during this Wave

Rule 2 applied to the instruments I was writing. Each one was caught by using the
instrument, not by reading it.

### 7.1 doctor D-4b: a substring scan failed on a *correct* file

D-4 (added by this Wave to refuse a runtime whose serving path still contains
`http.server`) flagged **its own product** — because `serve_visual.py`'s docstring
explains the dependency that was removed. A statement *about* the removed
dependency is not the dependency. Replaced with an AST audit
(`tools/v1_3_ui_runtime_audit.py`) that answers the real question and requires
**positive** evidence (`uvicorn_imported`, `uvicorn_run_called`,
`starlette_app_referenced`, `serves_files`), so an empty file cannot pass by
saying nothing. The gate feeds it synthetic files it must reject, and a docstring
it must not be fooled by.

The audit had a false FAIL of its own on first run: `from starlette.responses
import FileResponse` makes `FileResponse` an `ast.Name`, not an `ast.Attribute`,
so an attribute-only scan reported "serves no files". Both occurrences are
recorded in the code.

### 7.2 `-Action up -DryRun` exited 4 on every healthy host

A dry run is a pre-flight. It ran the prerequisite gate and the port
reconciliation correctly, then fell through to the `NOT FULLY UP` branch — because
nothing *was* up, which is what a dry run means. Exit 4 made the flag useless in a
scripting context and actively misleading to an operator. Fixed: the dry run ends
with its own `RESULT: DRY RUN (v1.3)` and exit 0. Precondition failures
(foreign hold, dead interpreter, missing config) still exit 3, because those are
findings about the host, not about the dry run.

### 7.3 `-WhatIf` was unreachable

`[CmdletBinding()]` without `SupportsShouldProcess` means PowerShell **owns** the
name `-WhatIf`. A hand-rolled `[switch]$WhatIf` is never consulted; the documented
dry run simply errored out with
`NamedParameterNotFound,jcc-v1.3-register-tasks.ps1`. Fixed by declaring
`SupportsShouldProcess` and routing every host-modifying call through
`$PSCmdlet.ShouldProcess`, so a dry run reaches a real guard rather than merely
parsing. (The v1.2 register script has the same shape; it is *not* touched here
because it is covered by `V1_2_RELEASE_MANIFEST.json` via `sha256_blob`.)

### 7.4 Two static invariants could not tell a command from a sentence

`REPAIR_HAS_NO_SIGNAL_AND_NO_DELETE` searched the repair function's text for
`Stop-Process` and reported `has_Stop-Process: True` — because the function's own
operator-facing line reads *"there is no Stop-Process path in this action"*. The
first fix (strip comments) **failed again**, because the mention is a string
literal, not a comment. Neither text scan can distinguish an invocation from
prose, in either direction.

Replaced with the **PowerShell parser**: `_ps_ast()` runs
`[Parser]::ParseFile` under `-EncodedCommand` and returns every `CommandAst`'s
resolved name plus line number. The invariant is now answered as *"is there a
signalling COMMAND in this range?"* and it is **transitive** over project-local
callees (`Invoke-V13Repair → Repair-V13Half → Move-V13StaleFile`, 7 functions / 22
commands, zero forbidden, and `Move-Item` present). It also refuses a zero-result
parse — an analyser that reports nothing must not read as "no violations".

That last point caught a bug in the new helper within the same session: the first
version used `param([string]$Path)`, but `-EncodedCommand` carries no arguments,
so the path bound to nothing and it returned `commands=0, functions=0` — a
perfect false-PASS shape. The path is now injected into the script body and a
zero-result parse is reported as an error.

### 7.5 doctor D-13 answered "ABSENT" about a task that was on disk

`doctor` still asked `Get-ScheduledTask` and folded *cannot see* into *absent* —
exactly the §6 incident, still live in the operator's primary diagnostic. It
printed `JCC-v1.3-API=ABSENT` while that task's file was sitting in
`C:\Windows\System32\Tasks` (mtime 12:15:03, 4176 bytes). D-13 is now **three-state**
(`PRESENT` / `ABSENT` / `CANNOT_QUERY`) and reads the task store on disk, with
provider state only as optional enrichment:

```
[INFO] D-13 durable scheduled tasks
measured: JCC-v1.3-API=PRESENT (provider blind in this session; on-disk verdict used instead),
          JCC-v1.3-VisualUI=ABSENT  (verdicts read from C:\Windows\System32\Tasks; ...)
```

A gate check, `DOCTOR_TASK_VERDICT_AGREES_WITH_THE_TASK_STORE`, now cross-checks
that verdict against the store — falsifiable (it fails the moment doctor says
ABSENT about a file that exists) and non-vacuous (it requires the store to be
readable, so "absent" is a statement about the host, not about the instrument).

### 7.6 And one piece of noise

`Get-NetTCPConnection -ErrorAction Stop` wrote a `TerminatingError` into the
transcript for the entirely normal case of "nothing is listening", which made the
record look like something had gone wrong when nothing had. `Get-JccPortOwner`
was changed (behaviour-preserving) to `-ErrorAction SilentlyContinue` with an
early "nothing listens" return; the netstat fallback now runs only when the
cmdlet is absent. The final restart transcript contains **0** `TerminatingError`
occurrences.

### 7.7 A hash anchor that would not have reproduced from the repo

Found while staging this commit, and it is the same class of error as the
`docs/p3` anchor incidents: **an anchor is only an anchor if a fresh checkout can
reproduce it.**

`.gitattributes` here declares `* text=auto eol=lf` and `core.autocrlf=true`.
One file — `tools/v1_3_wave8_runtime_gate.py` — had ended up with CRLF line
endings, because the throwaway patch script that rewrote it used Python's
`Path.write_text()`, which translates `\n` to `\r\n` on Windows. Git therefore
normalised the **blob** to LF while the **working tree** kept CRLF, and only git's
`warning: CRLF will be replaced by LF` gave it away. The gate hashes the working
tree, so an anchor taken from it would have described bytes that the committed
blob does not contain, and would have silently changed the next time the file was
checked out.

The file was normalised to LF, the gate was re-run (54/54, new evidence), and the
anchor was re-derived. The check now asserts what actually matters: for all seven
artefacts, `sha256(working tree) == sha256(git blob)` — verified, not assumed.
Two lessons: write files with `newline="\n"` explicitly; and treat a git CRLF
warning as a **hash-anchor defect**, not as cosmetic.

## 8. Evidence

Recorded at closeout on `feature/jcc-v1.2-visual-command-center`, HEAD `ff21bb7`
with Wave 8 uncommitted.

| Leg | Command | Result |
|---|---|---|
| Product gate | `python tools/v1_3_wave8_runtime_gate.py` | **PASS 54/54** (final run, no retry) |
| Core + e2e | `pytest packages/jcc_core/tests tests/e2e` (fresh `--basetemp`) | **786 passed / 0 failed / 0 skipped** |
| Tools + harness | `pytest tools/tests` (fresh `TEMP`) | **247 passed / 0 failed** (first run 246/1 — see §9) |
| A8 pin + inbound surface | `pytest test_p3_acceptance_gate.py test_server_surface.py` | **55 passed** |
| Security gate | `python tools/v1_1_security_gate.py --check` | `verdict=GO high=0 medium=0 ran=9/9`; `[check] OK` (sha `3fcb3f9f66d6eb5969083929`, unchanged from Wave 7) |
| Protected GREEN 4778 | pid + `GET /api/health` by the gate | pid `7588` before **and** after; HTTP 200 before **and** after — **untouched** |

Artifacts:

- gate evidence: `.workbuddy/scratch/v1_3_wave8_gate_20260929-132455.json`,
  sha256 `fbcfe7ba6e2d36f9cd6be84f5e3f23e794e67949a49612964f463585e6dfc56c`.
  Its `EVIDENCE_HASHES` step was re-computed against the working tree after the
  last edit, and for all seven files the recorded digest equals **both** the
  on-disk SHA-256 and the SHA-256 of the same file's **git blob** — so the
  anchors in this document reproduce from a fresh checkout, not only from this
  machine's working copy (§7.7).
- operator transcripts (written by `-LogFile` from inside the producing process):
  `w8_restart_final.txt` sha256 `306ce92955f149f03e93976e8133a42091b3617b18a5fcbb43a866a51f372343`,
  `w8_down_final.txt` sha256 `38dcf011858d7177f8baa742ea15df00a69ca0241b3c6da01464b84030c717b1`,
  `w8_repair_final.txt` sha256 `c3bac06b298df2590ec30b9f1b0191d21a0f53746b6b2f35dcabf5178b9eeef1`,
  `w8_doctor_final2.txt` sha256 `e85dec646b56d8e153ec76605179687eb4046e3720ee19fd71caf54c1d7cdd10`.
- registered task XML: `.workbuddy/scratch/w8_registered_task_api.xml`,
  sha256 `884bdda70c63ec1bd7c90f0d4e08c8a44ad568fc511f6d11a5b52a1fd329c536`.
- **A8 window: none required.** Wave 8 touches no file in
  `A8_REBASELINE`'s twelve pinned products (`git diff HEAD -- <the 12>` is
  empty), so no `A8_REBASELINE.json` entry is added and no pin moves.

### The control-plane round trip, as measured

```
JCC v1.3 -Action restart
  ... down half: both halves verified then stopped, exit code 0 each
  RESULT: STOPPED (v1.3). GREEN 4778 was never examined.
  down half exit code: 0
  ... up half: prerequisite gate OK; reconcile ports -> 4779 FREE, 4780 FREE
  API UP (v1.3)  service pid : 28320   ownership : descends from the JCC service start line
  UI  UP (v1.3)  service pid : 28492   health url : http://127.0.0.1:4780/health
  GREEN 4778 : UP (protected, untouched) (pid 7588)
  API http 200 : True    API ownership : True
  UI  http 200 : True    UI  ownership : True
  GREEN protected : True
  RESULT: UP (v1.3) -- API + Visual UI verified, GREEN protected
  up half exit code: 0
post-condition: API pid 28320 == port owner 28320; UI pid 28492 == port owner 28492; both HTTP 200
RESULT: RESTART OK (v1.3)
```

**What this transcript is evidence for, and what it is not.** It proves the
control-plane round trip — start, health, ownership, stop, restart, and a real
post-condition — on processes the script itself launched and positively
identified. It is **not** evidence for parent-exit survival or reboot recovery:
the transcript above was captured from a tool invocation whose process tree is
reaped when the call returns, and a later `-Action down` indeed found both
recorded pids already dead (see §8.1). Durability is the *task's* property, and
the task cannot be registered from this session (§6). That claim stays
`PENDING_EXTERNAL_ACTION` until §6 is run on the host.

### 8.1 A second, unplanned piece of evidence

The `-Action down` that followed that restart returned
`RESULT: INCOMPLETE (v1.3) -- worst half exit code 2`, with both ports free but
the bookkeeping still present:

```
UI REFUSED: not signalled. Verification failed for pid 28492.
  reason: recorded UI pid 28492 is not running
```

That is the designed behaviour: the services had been reaped with the calling
tree, so `down` could not positively identify its target and **refused** rather
than signalling or cleaning up on a guess. It pointed at `-Action repair`, which
then quarantined the four genuinely-stale files by rename, verified on both sides:

```
moved jcc-v1.3.ownership.json    -> jcc-v1.3.ownership.json.stale-20260929-131051 (verified: source gone, destination present)
moved jcc-v1.3.pid               -> jcc-v1.3.pid.stale-20260929-131051            (verified: source gone, destination present)
moved jcc-v1.3-ui.ownership.json -> jcc-v1.3-ui.ownership.json.stale-20260929-131052 (verified: source gone, destination present)
moved jcc-v1.3-ui.pid            -> jcc-v1.3-ui.pid.stale-20260929-131052          (verified: source gone, destination present)
bookkeeping files quarantined : 4
RESULT: OK -- nothing unsafe left. The host is in a state this script can reason about.
```

So the fail-closed split between `down` (never touches what it cannot verify) and
`repair` (moves only what is provably stale) was exercised against a real stale
state, not a fixture.

## 9. One regression was found and fixed at the file, not at the gate

The first full `tools/tests` run reported **246 passed / 1 failed**:

```
FAILED tools/tests/test_continuation_infra.py::test_no_script_shells_out_to_schtasks
```

The offender was mine: the operator hint in
`jcc-v1.3-register-tasks.ps1` suggested
`schtasks /Delete /TN …` as an alternative removal command. `schtasks.exe` is
**blacklisted on this host**, and the detector in that test is already the good
instrument — it strips comments, allows plain mentions, and fires only on a real
invocation (verb switch or execution operator), bracketed by two falsifier tests.

So the file was wrong, not the gate. Printing the blacklisted binary as a
supported path is bad advice regardless of the detector, and the ScheduledTasks
cmdlet was already printed immediately above it. The hint was replaced with a
line explaining why no command-line fallback is offered. Re-run from scratch with
a fresh `TEMP`: **247 passed / 0 failed**. `tools/tests` does not use the product
ports, so the earlier red run is recorded here rather than dropped.

## 10. Operator actions outstanding

1. **Remove the two stray tasks** (§6) — `JCC-v1.3-API`, `JCC-v1.3-PROBE`.
2. **Register the durable runtime for real** (§6) and adopt it with `-Action mark`.
3. **Set `JCC_CONTROL_TOKEN`** if the write surface is to be exercised; unset is
   fail-closed (401), not a defect.
