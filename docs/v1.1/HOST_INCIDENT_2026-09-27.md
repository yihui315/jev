# Host incident & blocker — 2026-09-27 (v1.1 verification window)

Status: **BLOCKED (owner decision required)** · Recorded by: team lead (Orion)
Related: `docs/p3/P3_RELEASE_GATE.md`, `artifacts/release/V1_1_BASE_BASELINE.json`,
`.workbuddy/memory/2026-09-27.md`

This file exists because the two facts it records are **host-level**, not
product-level, and they are load-bearing for every verdict taken in this window.
It is committed (not left in a gitignored scratch dir) on purpose: an untracked
record is unrecoverable, which this very incident demonstrated.

---

## 1. The host is undergoing repeated unclean shutdowns

`Get-WinEvent` over the System log (3-day window):

| time (local) | id | source | reading |
|---|---|---|---|
| 2026-09-26 14:49:44 | 1001 | Memory-Diagnostic-Task-Handler | **Windows removed bad memory regions** |
| 2026-09-26 21:54:04 | 1001 | Memory-Diagnostic-Task-Handler | **Windows removed bad memory regions** (again) |
| 2026-09-27 18:16:32 | 6008 | EventLog | previous shutdown at 18:16:32 was **unexpected** |
| 2026-09-27 18:24:21 | 1001 | WER-SystemErrorReporting | bugcheck **`0x00000139`, P1=4** (LIST_ENTRY corruption) |
| 2026-09-27 21:20:06 | 41 | Kernel-Power | unexpected shutdown |
| 2026-09-27 21:27:20 | 41 | Kernel-Power | unexpected shutdown (last boot 21:27:17) |

The `0x139` family already appears in this host's history with `P1=3`
(2026-09-11). Windows' own memory diagnostic flagging bad regions twice is
stronger evidence than any user-space stress test.

**Disk is not the suspect:** `fsutil dirty query F:` → "没有损坏"; every NTFS
volume reports 运行状况良好; no `disk`/`stor*` errors in the window.

## 2. A working source file was silently destroyed (recovered)

`tools/tests/test_v1_1_security_gate_artifact.py` was found **entirely 0x00** at
its exact length, 27863 bytes, mtime `18:17:07` — inside the 18:16:32 crash
window. Classified `STABLY_CORRUPT` (4 consecutive reads, sha256
`247d4953744c5017…`), so not an unstable read path.

Signature = **metadata written, data lost**: the length and mtime survived, the
bytes did not. That is a lost write-back after an unclean shutdown, not an edit
error and not a syntax error. Exactly one file in the checkout was affected.

**Recovered byte-exactly** by replaying this session's own tool-call log
(1 `Write` + 6 `Edit`s) and validating against the stale `.pyc` oracle:

* replayed intermediate length **18545** == the size the `.pyc` header records;
* replayed final length **27863** == the exact size of the zeroed file;
* restored: 0 NUL, compiles, reproduces the pre-corruption result
  (2 pre-commit HEAD-locks + 32 passed), sha256 `9e984822f80e7f09`;
* the zeroed bytes are preserved under `.workbuddy/scratch/CORRUPT_allnul_backup.py`
  (`PERMANENT_DELETE=0`).

It survived only because the content could be rebuilt. Had the commit not
followed, it would have been gone: **an untracked artifact is not just an
unreproducible verdict, it is an unrecoverable one.**

## 3. BLOCKER — the protected v1.0.1 runtime is dead, and the baseline forbids reviving it

Measured at 2026-09-27 22:13 local:

| probe | result |
|---|---|
| TCP listen on 127.0.0.1:4778 | **none** |
| connect 127.0.0.1:4778 | times out |
| pid 9952 (baselined service) | **dead** |
| pid 18348 (baselined launcher) | **dead** |
| python processes on host | none |
| `runtime/jcc/jcc.db` sha256 | `0a644062b4b5032d52c883b59a3f874333f28deb9d0cc12d14280b26af62ab53` — **identical to baseline** |
| `runtime/jcc/jcc.ownership.json` sha256 | `e68c1e73cedd6de1d970f6c49a68a99e359e0bb04c51b612c19815009b9b7dbd` — **identical to baseline** |

So the crash killed the **processes only**; every state file is byte-identical
to the frozen baseline. Nothing tampered with the protected runtime — but it is
not running.

`V1_1_BASE_BASELINE.json` states the contract explicitly:

> "the v1.0.1 service (pid 9952, port 4778) is PRESERVED for the entire v1.1
> work. The `feature/jcc-v1.1-activation` branch must NEVER touch `runtime/`,
> must NEVER send SIGTERM/SIGKILL to pid 9952, and **must NEVER restart
> `jcc_core` on port 4778**."

There is a second reason not to improvise a restart: the worktree is now the
**v1.1 branch**. `scripts/start.ps1` runs `jcc_core.run` from the current tree,
so a naive restart would not reproduce the v1.0.1 artifact (policy p3.0.0,
provider mock, actuation disabled) that the baseline pins.

### Consequence — five tests cannot pass, for one root cause

`pytest tools/tests` (full directory, clean tree, 2026-09-27 22:0x) →
**218 passed, 1 failed, 4 skipped**, and the regenerated
`artifacts/acceptance/CONTINUATION_INFRA_TEST.json` records **5** failing rows:

| test | why |
|---|---|
| `test_doctor_on_this_host_does_not_suggest_starting_an_up_service` | asserts doctor's **OURS** hint; requires the service UP |
| `test_deltas_track_wall_clock` | setup requires the live process |
| `test_pid_is_stable_across_samples` | setup requires the live process |
| `test_printed_uptime_matches_live_process_at_the_same_instant` | setup requires the live process |
| `test_uptime_is_monotonic` | setup requires the live process |

These are **live-state** tests, not product regressions. Proof: the same suite
ran at 18:15 on the same code (ORDER A2) with **none** of these five failing —
the only failures then were the 2 expected pre-commit HEAD-locks plus one
load-dependent probe flake. They went red when the service died at 21:20/21:27.

This is why `FINAL-G ("failed=0 全绿")` is **not reachable without an owner
decision**: the only fix is to restart port 4778, which the frozen baseline
forbids this branch from doing.

### What is NOT blocked

Everything that does not read the live service is unaffected: §22 Reliability
Gate (designed for port 4779 / `runtime/v1.1/reliability/`), §23 Watchdog, §24
Control-UI smoke, §25 security re-run, §26 final gates' non-live legs.

### Decision needed

1. Authorise a **faithful** restore of the protected v1.0.1 runtime on 4778
   (and confirm the restore source: the v1.0.1 artifact snapshot / tag
   `jcc-v1.0.1-ops` at `f8ac95d30f67f892e28fba03e3d99f900a65a61d`, *not* the
   v1.1 worktree), recording new-PID provenance in the baseline; **or**
2. keep 4778 down for this phase and record the live-service leg as an explicit
   `EXACT_BLOCKER` (not green) — which means a lawful release short of
   `jcc-v1.1-local`; **or**
3. pause this verification effort and address host stability first (the documented
   pending remediation: BIOS `DRAM Frequency → DDR4-2133`, RAM A/B in safe mode,
   reduce to two DIMMs, BIOS update, MemTest86 per-DIMM).

---

## Resolution (2026-09-27 22:29–22:37) — option 1 was chosen and executed

**GREEN 4778 is back, serving the frozen v1.0.1 code, and is now self-healing.**
The measured record is `artifacts/release/V1_1_GREEN_RUNTIME_RESTORE.json`
(emitted by `tools/v1_1_emit_green_runtime_restore.py`, which re-checks itself
against live state with `--check`).

### How the code is served

`packages/` from `f8ac95d30f67f892e28fba03e3d99f900a65a61d` — the baseline's own
`rollback_sha` — is extracted into an immutable snapshot at
`runtime/stable_v101/` (gitignored, so the worktree stays clean), and
`PYTHONPATH` puts that snapshot FIRST. The v1.1 worktree is never checked out,
reset, or modified. Provenance was settled by measurement, not assumption: the
`packages/` tree is **byte-identical** between `f8ac95d` and
`cefed66159a5` (the commit on disk when pid 9952 actually started), and only
`scripts/` moved between them — and scripts are re-read on each invocation, never
loaded into the running process. So the snapshot reproduces exactly what the dead
service had loaded.

### The proof that 4778 is the frozen code

Two independent routes prove the on-disk tree (`verify`: manifest **and**
`git cat-file --batch` against the commit — 126/126 files, digest
`3f630125322d…`), and a third proves the LIVE payload: the frozen `server.py`
returns **7** health keys and the v1.1 one returns **12**. The live service
returns exactly those 7, and `provider_mode` — which only the v1.1 file adds — is
absent. That is a non-vacuous discriminator, asserted from both revisions so a
broken parse cannot silently certify anything.

`runtime/jcc/jcc.db` sha256 is `0a644062b4b5032d…`, unchanged: the existing data
was preserved, not recreated. `config/jcc.yaml` is unchanged (and is in any case
identical between the two commits).

### Four real defects found and fixed on the way

1. **The service watchdog had never once recovered anything.** `.workbuddy/release_state.json`
   does not exist and `service-watchdog.ps1` required it to read `READY` before
   acting, so every tick since installation logged
   `NOOP -- release state is <unset>; monitoring is reserved for READY`
   — 951 consecutive lines, still ticking every 120 s while 4778 was dead.
   `scripts/ensure-stable-runtime.ps1` has no such gate.
2. **`Get-JccTaskReport` reported a running, enabled task as non-existent**,
   because it read `Get-ScheduledTask`'s CIM refusal (that cmdlet throws
   `ERROR_FILE_NOT_FOUND` here) as "no such task". Fixed to a three-state probe
   — PRESENT / ABSENT / UNKNOWN — reading the scheduler's own XML store, since
   folding "I could not query" into "it is not there" is the same defect class
   this project has now hit six times.
3. **The scheduled-task action silently did nothing.** `cmd.exe /c "<prog>" args >> "log"`
   contains four quotes, which triggers cmd's legacy rule: strip the leading
   quote and remove the LAST quote on the line. The log path's closing quote was
   eaten, the program path was left unterminated, and the task reported
   `LastTaskResult=1` while creating no log and no audit line. Fixed by wrapping
   the whole command in one more quote pair.
4. **The "immutable" snapshot was writable.** The first restore let CPython write
   `__pycache__/*.pyc` into it — 35 extra files, tree digest changed — and the
   restore's own proof then correctly refused with `FROZEN_PROOF_FAILED`. A tree
   the runtime can mutate is not immutable, so the served process now runs with
   `PYTHONDONTWRITEBYTECODE=1`, and `verify` stays strict instead of being taught
   to ignore the very thing it exists to catch.

Defect 4 is worth keeping: the failure was **loud and self-inflicted by design**.
The restore refused to certify its own result rather than reporting success on a
tree that had changed underneath it.

### What protects it from here

| Task | Trigger | Principal | Action |
|---|---|---|---|
| `JEV-JCC-Stable-Runtime` | AtStartup + AtLogOn | SYSTEM, Highest | `ensure-stable-runtime.ps1` |
| `JEV-JCC-Stable-Health` | every 2 minutes | SYSTEM, Highest | `ensure-stable-runtime.ps1 -Watchdog` |

Both were verified through the task store's XML (`state=Ready`, `enabled=true`,
`repetition=PT2M`, correct script and arguments). The restore action is
fail-closed in every branch that it does not own: a listener it cannot prove is
ours is never signalled, and a runtime marker naming a still-live process is
never cleaned. A recorded pid that is alive but whose start time differs from the
marker is treated as **recycled** — which is exactly what happened during this
restore, and the log records it in those words.

### Honest deviations from the baseline

- The recorded `pid`/`launcher_pid` necessarily differ (9952/18348 → new pids):
  the host rebooted, so new pids are the correct outcome, not a defect.
- The baseline's note reserves switching the production port for "final
  maintenance acceptance". That authorisation was given for this restore, and it
  was used in the most conservative possible way: **the port was switched TO the
  rollback SHA itself**, so a rollback is now a no-op.
- `scripts/service-watchdog.ps1` / `JEV-JCC-Service-Watchdog` were **left
  installed and untouched**. They remain inert (defect 1), and the new tasks do
  not depend on them. Retiring or retargeting that task is a follow-up decision,
  not something to do silently during an outage.

