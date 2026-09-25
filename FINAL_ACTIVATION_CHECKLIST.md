# JEV COMMAND CENTER — FINAL ACTIVATION CHECKLIST

**Owner:** lead (Orion) · **Enforced by:** `artifacts/acceptance/final_gate_run.py` (the only stop condition)
**Release commit:** `df3e7d9…` (product freeze — all product/ops code, no release
evidence; chosen over the later `dc154b0` because the checklist itself is F-02
evidence and a freeze must not carry evidence) ·
**Verdict:** `PROJECT_COMPLETE=true` at `final_gate_run.py --full`
**Status:** activation steps below were executed against this working copy; every port, path and command is copied from a real run, not from documentation.

This is the operator's path from an empty machine to a running, verified platform.
Step 8 is the one that answers "is this project actually finished" — **no earlier
step, and no earlier gate (P4, P5, a security GO, an SRE GO), is a stop condition.**

---

## 0. Prerequisites

| Requirement | Notes |
| --- | --- |
| Windows, Windows PowerShell **5.1** | All `scripts/*.ps1` are 5.1-only. They are not cross-platform. |
| `F:\jev\.venv\` | The project virtualenv. `scripts/lib/JccCommon.ps1` locates it via `Get-JccPython`; without it the scripts refuse to run. |
| `git` on `PATH` | Used by the gate and by the provenance checks. |
| Free disk ≥ **1 GiB** on the `F:` volume | Checked by `scripts\doctor.ps1` item D-7. |

Execution policy: the scripts dot-source `scripts\lib\JccCommon.ps1`; run them
with `powershell -NoProfile -NonInteractive` if a wider policy is in force.

---

## 1. Bootstrap (install dependencies)

```powershell
F:\jev> .\scripts\bootstrap.ps1
```

| param | default | meaning |
| --- | --- | --- |
| `-Requirements` | `''` | requirements file to install |
| `-ForceInstall` | off | re-run a forced install even if nothing changed |
| `-TimeoutSec` | `900` | install timeout |

Expect: a dependency install, then a clean return with exit `0`.

---

## 2. Doctor (pre-flight self-check) — **read the whole output**

```powershell
F:\jev> .\scripts\doctor.ps1 -MinFreeGb 1
```

| param | default |
| --- | --- |
| `-MinFreeGb` | `1` |

Checks D-1 … D-8: python on PATH, venv interpreter runs, runtime packages importable,
`config/jcc.yaml` present and parses, SQLite directory writable, server port not held by a
foreign process, free disk space, final-gate runner present.

> **Read the output end to end, not just the exit code.** `scripts\doctor.ps1` had a
> runtime defect where it aborted half-way through its own report (a PowerShell
> mixed-type `+` chain coerced `' GiB free / '` into `System.Double`), so a run can
> print several `PASS` lines and then die. A truncated report is a defect, not a
> partial success. W-01..W-07 only prove the scripts *parse*; running them is a
> separate question (see `docs/FINAL_STOP_CONDITIONS.md` sec. I).

Exit `0` = every check PASS. Anything else: fix the named check, do not proceed.

---

## 3. Start the service

```powershell
F:\jev> .\scripts\start.ps1
```

| param | default |
| --- | --- |
| `-Port` | `0` → taken from `config/jcc.yaml` (this host: **4778**, bound to `127.0.0.1` only) |
| `-HealthTimeoutSec` | `90` |

The script returns once `/api/health` actually answers — it does not return on a
spawned process, so "the command returned" means "the service is up".

---

## 4. Confirm the service is the one we started

```powershell
F:\jev> .\scripts\status.ps1
```

| param | default |
| --- | --- |
| `-Port` | `0` → same resolution as `start.ps1` |

```
  service      : UP
  port         : 4778 (bound to 127.0.0.1 only)
  health       : status=... provider=... actuation=... policy=... wal=... open_get_endpoints=...
```

`status.ps1` reads the same ownership marker `stop.ps1` verifies against, so `UP`
means *the process we started is answering*, not merely *something answers*.
Exit `0` = UP and healthy. A DOWN result names the concrete reason (no HTTP
response / foreign port owner / missing pid file).

---

## 5. Routine operations

```powershell
F:\jev> .\scripts\restart.ps1                 # restart + wait for health
F:\jev> .\scripts\stop.ps1                    # PID file + ownership marker + known port identity
F:\jev> .\scripts\stop.ps1 -DryRun            # show what would be stopped, change nothing
```

`stop.ps1` deliberately avoids the blunt "kill every python/node on the box"
approach required by W-01..W-07: PID file, ownership marker and known port/process
identity all have to agree before anything is stopped.

---

## 6. Re-run the acceptance gate (the stop condition)

```powershell
F:\jev> .\scripts\acceptance.ps1              # same as step 8 below
F:\jev> .\scripts\acceptance.ps1 <other-runner> -RunnerArgs @(...)
```

The runner's exit code is **propagated verbatim** — nothing here swallows a
non-zero exit or prints SUCCESS on failure.

---

## 7. Rollback

Primary rollback is the **evidence snapshot** (ADR-0006); git is secondary.

```powershell
F:\jev> git status --porcelain                # must be empty before any verdict
F:\jev> git reset --hard <previous-commit>     # secondary only
```

Anything untracked inside a tracked area fails provenance (P-01/P-05). Evidence
written under `artifacts/` is gitignored by design (ADR-0009) and does not dirty
the tree; scratch work never belongs in the repo root.

---

## 8. The verdict

```powershell
F:\jev> F:\jev\.venv\Scripts\python.exe F:\jev\artifacts\acceptance\final_gate_run.py --full
```

Exit `0` ⟺ `PROJECT_COMPLETE=true`. That single command is the entire stop
condition; see `docs/FINAL_STOP_CONDITIONS.md` and
`artifacts/acceptance/FINAL_ACCEPTANCE_REPORT.md`.

---

## Known, verified-degraded conditions (they do NOT block activation)

| ID | Condition | Operator consequence |
| --- | --- | --- |
| A-01 | WorkBuddy adapter: no verified steering contract; probe reports the recommendation path / human-gated retry | Adapters run **recommendation-only**. Do not expect autonomous approval steering. |
| A-02/A-03/A-04 | Claude / Codex / Hermes: mechanism present but not exercisable in this environment (no validated steering contract) | Same as A-01. Nothing here blocks startup. |
| S-04 | Control UI present, full 8-screen smoke coverage not separately evidenced | Use the real API-backed screens; treat B-surface completeness as not yet evidenced. |
| S-05 | Demo mode exists and is labelled `DEMO` | Immutable rule: demo runs must stay labelled `DEMO`; never present them as a real agent. |
| KD-004 | Pre-F1 directive rows cannot be backfilled in place (`origin` is not derivable) | Shadow-store history before F1 keeps `origin IS NULL`. Read code must tolerate it; only M1 may touch `origin IS NULL` rows. |

## What is deliberately NOT in this project

* No paid provider calls (`PAID_PROVIDER_CALLS=0` is pinned by the gate itself, not
  just by the caller), no external publishing (`EXTERNAL_PUBLISH=0`), no permanent
  deletes (`PERMANENT_DELETE=0`).
* The platform is observe-only since P3: a score or a shadow signal never becomes an
  action on its own. High-risk actions require human approval.
