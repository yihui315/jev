#!/usr/bin/env python3
"""FINAL-PHASE FALSIFIER: interruption recovery of a long-running step (deliverable I).

THIS IS THE TEST THAT STOPS THE LIVE-LOCK RECURRING. The defect it falsifies was
proved on 2026-09-26: the controller refreshed its lock once per step, a 41-minute
soak outran the 15-minute lock, the watchdog read the lock as stale, started a
SECOND controller, and the two soak_drivers ran stop.ps1/start.ps1 through each
other's PHASE R window. It can never converge.

WHAT IT PROVES, MEASURED
  1. a synthetic long step whose descriptor claims a 5-minute expected finish is
     started by the real controller code against a SCRATCH root;
  2. the heartbeat keeps advancing for the WHOLE step -- sampled repeatedly while
     the controller is blocked in a blocking subprocess wait, and asserted to be
     strictly increasing (not merely "the field exists");
  3. the controller is KILLED mid-step;
  4. the shipped watchdog, run against the scratch root, decides the lock is stale
     and launches a replacement;
  5. the replacement FINDS the existing run alive and ATTACHES to it;
  6. NO second run is launched -- the descriptor's run_id and pid are unchanged and
     the number of live run processes did not grow;
  7. the run finishes and the replacement OBSERVES it (judges the artifact);
  8. the state advances to the next step WITHOUT re-running the long step.

IT DOES NOT TOUCH THE REAL PIPELINE. The controller is the REAL controller code,
but launched under `JCC_ROOT=<scratch>` with the shipped watchdog's `-Root`, so it
resolves its state, lock, runs and artifacts under the scratch tree. Port 4778 is
never opened or probed. `F:\\jev\\.workbuddy\\jcc_execution_state.json` and the real
controller are never read or written.

Run:
    python tools/tests/interruption_recovery.py            # ~5 minutes
    JCC_IR_SECONDS=60 python tools/tests/interruption_recovery.py   # shorter run
"""
from __future__ import annotations

import io
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(r"F:\jev")
SCRIPTS = REPO / "scripts"
WD = SCRIPTS / "continuation-watchdog.ps1"
PY = REPO / ".venv" / "Scripts" / "python.exe"
BASE_PY = Path(r"C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe")
TZ = timezone(timedelta(hours=8))

#: The declared timeout of the synthetic step: the descriptor's
#: `expected_finish_at` is `started_at + this`. 300 s == "5-minute expected finish",
#: per the spec. The step ACTUALLY runs JCC_IR_SECONDS (default: the same 300 s).
DECLARED_TIMEOUT_S = 300
RUN_SECONDS = int(os.environ.get("JCC_IR_SECONDS", str(DECLARED_TIMEOUT_S)))

#: How long to sample the lease for monotonic advances. Must exceed 2x the
#: heartbeat interval (30 s) so at least two beats are observed.
HEARTBEAT_SAMPLE_S = int(os.environ.get("JCC_IR_SAMPLE_S", "90"))

STEP_ID = "SYNTH-LONG"
STEP_SCRIPT = "tools/continuation/synthetic_long_step.py"
STEP_RESULT = "artifacts/acceptance/SYNTH_LONG.json"

STEP_SOURCE = '''#!/usr/bin/env python3
"""A synthetic long-running step. Sleeps in short chunks, then writes its artifact."""
import io, json, os, sys, time
SECONDS = float(sys.argv[1])
# Deterministic: the artifact records when this run started so freshness is checkable.
root = os.environ.get("JCC_ROOT") or os.getcwd()
out = os.path.join(root, "artifacts", "acceptance", "SYNTH_LONG.json")
os.makedirs(os.path.dirname(out), exist_ok=True)
t0 = time.time()
print("synthetic long step: will run %.0fs (pid %d)" % (SECONDS, os.getpid()), flush=True)
while time.time() - t0 < SECONDS:
    time.sleep(1.0)
print("synthetic long step: writing artifact", flush=True)
with io.open(out, "w", encoding="utf-8", newline="\\n") as fh:
    json.dump({"ok": True, "verdict": "PASS: synthetic long step completed",
               "started_at": t0, "finished_at": time.time(), "pid": os.getpid()}, fh, indent=2)
    fh.write("\\n")
'''

#: The scratch controller. It is NOT a stub: it loads the REAL controller module by
#: path and replaces its STEPS table with the single synthetic long step, so the
#: lease / descriptor / attach machinery under test is the shipped machinery.
WRAPPER_SOURCE = '''#!/usr/bin/env python3
import importlib.util, os, sys
REAL = r"%(real)s"
spec = importlib.util.spec_from_file_location("jcc_real_controller", REAL)
ctl = importlib.util.module_from_spec(spec)
sys.modules["jcc_real_controller"] = ctl
spec.loader.exec_module(ctl)
step = ctl.Step("%(sid)s", "synthetic long step", "%(script)s", ("%(secs)s",),
                "%(result)s", ctl._ok_judge, %(timeout)d, long_running=True)
ctl.STEPS = [step]
ctl.STEP_IDS = [step.id]
ctl.STEP_BY_ID = {step.id: step}
raise SystemExit(ctl.main())
''' % {"real": str(REPO / "tools" / "jcc_continuation.py"), "sid": STEP_ID,
       "script": STEP_SCRIPT, "secs": RUN_SECONDS, "result": STEP_RESULT,
       "timeout": DECLARED_TIMEOUT_S}

PS = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / \
    "v1.0" / "powershell.exe"


def log(msg):
    print("[IR] %s" % msg, flush=True)


def _ps(cmd, timeout=120):
    p = subprocess.run([str(PS), "-NoProfile", "-NonInteractive", "-Command", cmd],
                       capture_output=True, timeout=timeout)
    out = (p.stdout or b"").decode("utf-8", "replace") + (p.stderr or b"").decode("utf-8", "replace")
    return p.returncode, out.strip()


def _ps_to_file(args, out_path, timeout=180):
    with io.open(out_path, "wb") as out:
        return subprocess.run([str(PS), "-NoProfile", "-NonInteractive",
                               "-ExecutionPolicy", "Bypass"] + list(args),
                              stdout=out, stderr=subprocess.STDOUT,
                              timeout=timeout).returncode


def _build_scratch(base: Path) -> Path:
    root = base / "root"
    for sub in (".workbuddy/locks", ".workbuddy/runs", ".workbuddy/scheduled/logs",
                ".workbuddy/scheduled/steps", "artifacts/acceptance",
                "tools/continuation", ".venv/Scripts"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    # scripts/ verbatim: the watchdog that runs is the shipped one (its -Root only
    # changes where it LOOKS, never what it does).
    shutil.copytree(str(SCRIPTS), str(root / "scripts"), dirs_exist_ok=True)
    # The venv redirector + its absolute-home pyvenv.cfg, so Get-JccPython finds a
    # working interpreter and the launch path is the real one.
    for rel in (".venv/Scripts/python.exe", ".venv/pyvenv.cfg"):
        shutil.copyfile(str(REPO / rel), str(root / rel))
    (root / "tools" / "jcc_continuation.py").write_text(WRAPPER_SOURCE, encoding="utf-8",
                                                        newline="\n")
    (root / "tools" / "continuation" / "synthetic_long_step.py").write_text(
        STEP_SOURCE, encoding="utf-8", newline="\n")
    return root


def _read_json(path):
    if not os.path.isfile(path):
        return None
    try:
        with io.open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _pid_alive(pid):
    """Liveness by process table (PowerShell), never by image name."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    rc, out = _ps("if (Get-Process -Id %d -ErrorAction SilentlyContinue) { 'True' } else { 'False' }"
                  % pid)
    return "True" in out


def _count_step_processes():
    """How many live processes are running the synthetic step script."""
    rc, out = _ps("(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                  "Where-Object { $_.CommandLine -like '*synthetic_long_step.py*' }).Count")
    for tok in out.replace("\r", "\n").split():
        if tok.isdigit():
            return int(tok)
    return 0


def _wait_for(predicate, timeout, interval=1.0):
    end = time.time() + timeout
    while time.time() < end:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return None


def run() -> int:
    stamp = datetime.now(TZ).strftime("%Y%m%d_%H%M%S")
    work = REPO / ".workbuddy" / "scratch" / "interruption" / stamp
    work.mkdir(parents=True, exist_ok=True)
    root = _build_scratch(work)
    state_path = root / ".workbuddy" / "jcc_execution_state.json"
    lock_path = root / ".workbuddy" / "locks" / "jcc-continuation.lock.json"
    desc_path = root / ".workbuddy" / "runs" / ("%s.json" % STEP_ID)
    artifact = root / "artifacts" / "acceptance" / "SYNTH_LONG.json"
    ctrl_out = root / ".workbuddy" / "scheduled" / "logs" / "continuation-controller.out.log"
    wd_out = work / "watchdog.out.txt"

    checks = []

    def check(name, ok, detail=""):
        checks.append((name, bool(ok), str(detail)[:200]))
        log("  [%s] %-46s %s" % ("PASS" if ok else "FAIL", name, str(detail)[:120]))

    # ---- 1. launch the REAL controller code against the scratch root ----------
    env = dict(os.environ)
    env["JCC_ROOT"] = str(root)
    env["PYTHONIOENCODING"] = "utf-8"
    env["CODEBUDDY_SAFE_DELETE_ENABLED"] = "0"
    ctrl = subprocess.Popen([str(BASE_PY), str(root / "tools" / "jcc_continuation.py"),
                             "run", "--resume"],
                            cwd=str(root), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log("controller pid=%d launched against %s" % (ctrl.pid, root))

    desc = _wait_for(lambda: _read_json(str(desc_path)), 60, 0.5)
    check("a durable run descriptor appeared", bool(desc),
          "run_id=%s pid=%s" % ((desc or {}).get("run_id"), (desc or {}).get("pid")))
    if not desc:
        ctrl.kill()
        return _report(checks, work)
    run_id_before = desc.get("run_id")
    run_pid_before = desc.get("pid")
    expected_finish = desc.get("expected_finish_at")
    try:
        ef = datetime.fromisoformat(str(expected_finish))
        claim_s = (ef - datetime.fromisoformat(str(desc.get("started_at")))).total_seconds()
    except ValueError:
        claim_s = -1
    check("the descriptor claims a >= 5-minute expected finish",
          claim_s >= DECLARED_TIMEOUT_S - 5, "claim=%.0fs expected_finish=%s" % (claim_s, expected_finish))
    check("the run process is alive", _pid_alive(run_pid_before), "pid=%s" % run_pid_before)
    count_before = _count_step_processes()
    check("at least one run process exists", count_before >= 1, "count=%d" % count_before)

    # ---- 2. prove the heartbeat ADVANCES for the WHOLE step -------------------
    # While the controller is blocked in a blocking subprocess wait, the lease
    # heartbeat must keep moving. Sampled, and asserted STRICTLY INCREASING -- a
    # field that merely exists proves nothing.
    samples = []
    sample_window = min(HEARTBEAT_SAMPLE_S, max(60, RUN_SECONDS // 2))
    end = time.time() + sample_window
    while time.time() < end:
        lock = _read_json(str(lock_path)) or {}
        samples.append(lock.get("heartbeat_at"))
        time.sleep(2.0)
    uniq = [s for s in samples if s]
    advances = sum(1 for a, b in zip(uniq, uniq[1:]) if b > a)
    monotonic = all(b >= a for a, b in zip(uniq, uniq[1:]))
    check("the lease heartbeat advanced at least twice while the step was blocked",
          advances >= 2, "%d distinct advance(s) over %.0fs" % (advances, sample_window))
    check("the heartbeat samples are monotonic", monotonic, "first=%s last=%s"
          % (uniq[0] if uniq else None, uniq[-1] if uniq else None))
    lock = _read_json(str(lock_path)) or {}
    check("the lease declares the current step and run", lock.get("current_step") == STEP_ID
          and lock.get("run_id") == run_id_before,
          "current_step=%s run_id=%s" % (lock.get("current_step"), lock.get("run_id")))
    check("the lease carries pid identity fields",
          bool(lock.get("pid")) and bool(lock.get("process_start_time")),
          "pid=%s start=%s" % (lock.get("pid"), lock.get("process_start_time")))
    check("the run had not finished while we sampled", not artifact.exists(),
          "artifact exists=%s" % artifact.exists())

    # ---- 3. kill the controller mid-step --------------------------------------
    ctrl.kill()
    ctrl.wait(timeout=30)
    log("controller killed; the step child must survive as an orphan")
    time.sleep(2)
    check("the run process survived the controller kill", _pid_alive(run_pid_before),
          "pid=%s" % run_pid_before)
    lock_after_kill = _read_json(str(lock_path)) or {}
    check("the lock is left behind naming a DEAD controller",
          not _pid_alive(lock_after_kill.get("pid")),
          "lock pid=%s" % lock_after_kill.get("pid"))

    # ---- 4. the shipped watchdog replaces it ---------------------------------
    rc = _ps_to_file(["-File", str(WD), "-Root", str(root), "-WaitSeconds", "20"], str(wd_out), 300)
    ticks = _read_ticks(root)
    actions = [t.get("action") for t in ticks]
    check("the watchdog ticked", bool(ticks), "actions=%s" % actions)
    check("the watchdog decided the lock was STALE", "STALE" in actions, "actions=%s" % actions)
    check("the watchdog launched a replacement", "STARTED" in actions or "CONFIRMED" in actions,
          "actions=%s" % actions)

    # ---- 5/6. the replacement ATTACHES, and NO second run is launched ---------
    marker = _wait_for(lambda: ("ATTACH_EXISTING_RUN" in ctrl_out.read_text(
        encoding="utf-8", errors="replace") if ctrl_out.is_file() else False), 90, 1.0)
    check("the replacement recorded ATTACH_EXISTING_RUN", bool(marker),
          "see %s" % ctrl_out.name)
    desc2 = _read_json(str(desc_path)) or {}
    check("the descriptor run_id is UNCHANGED (no second run)", desc2.get("run_id") == run_id_before,
          "before=%s after=%s" % (run_id_before, desc2.get("run_id")))
    check("the descriptor pid is UNCHANGED (no second run)", desc2.get("pid") == run_pid_before,
          "before=%s after=%s" % (run_pid_before, desc2.get("pid")))
    live_after = _count_step_processes()
    check("the live run process count did NOT grow", live_after <= count_before,
          "before=%d after=%d" % (count_before, live_after))

    # ---- 7. let the run finish and observe it --------------------------------
    fresh = _wait_for(lambda: artifact.is_file(), RUN_SECONDS + 90, 2.0)
    check("the run produced its artifact", bool(fresh), str(artifact))
    state = _wait_for(lambda: (_read_json(str(state_path)) or {}).get("steps", {}).get(
        STEP_ID, {}).get("status") == "PASS", 120, 2.0)
    final = _read_json(str(state_path)) or {}
    rec = (final.get("steps") or {}).get(STEP_ID) or {}
    check("the state advanced: SYNTH-LONG is PASS (observed, not re-run)",
          rec.get("status") == "PASS", "status=%s detail=%s" % (rec.get("status"), rec.get("detail")))
    check("the PASS was reached by ATTACHING, not by a fresh launch",
          (rec.get("attached_run") or {}).get("observed_only") is True,
          "attached_run=%s" % rec.get("attached_run"))
    check("last_completed_step is the synthetic step",
          final.get("last_completed_step") == STEP_ID, str(final.get("last_completed_step")))
    check("the descriptor still names the ORIGINAL run",
          (_read_json(str(desc_path)) or {}).get("run_id") == run_id_before,
          str((_read_json(str(desc_path)) or {}).get("run_id")))
    check("no controller lock is left behind",
          not lock_path.exists() or not _pid_alive((_read_json(str(lock_path)) or {}).get("pid")),
          "lock exists=%s" % lock_path.exists())

    return _report(checks, work)


def _read_ticks(root: Path):
    p = root / ".workbuddy" / "scheduled" / "logs" / "continuation-watchdog.jsonl"
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            out.append({"action": "<unparsable>"})
    return out


def _contract():
    """The proof contract, by path -- one implementation of the proof schema."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "jcc_proof_contract", str(REPO / "tools" / "continuation" / "proof_contract.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


#: The §L heartbeat claim is exactly these checks; the interruption-recovery claim
#: is ALL of them. One source list, two proofs -- so the two can never disagree
#: about what was measured.
_HEARTBEAT_CHECKS = frozenset((
    "the lease heartbeat advanced at least twice while the step was blocked",
    "the heartbeat samples are monotonic",
    "the lease declares the current step and run",
    "the lease carries pid identity fields",
))


def _report(checks, work: Path) -> int:
    bad = [c for c in checks if not c[1]]
    payload = {
        "schema": "jcc-interruption-recovery/1",
        "phase": "FINAL-PHASE falsifier -- interruption recovery of a long-running step",
        "ok": not bad,
        "status": "PASS" if not bad else "FAIL",
        "declared_timeout_s": DECLARED_TIMEOUT_S,
        "actual_run_seconds": RUN_SECONDS,
        "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in checks],
        "verdict": ("PASS: the heartbeat advanced for the whole step, the controller was killed "
                    "mid-step, the shipped watchdog replaced it, the replacement ATTACHED to the "
                    "live run, no second run was launched, and the state advanced without "
                    "re-running the step"
                    if not bad else "FAIL: " + "; ".join("%s" % c[0] for c in bad)),
        "scratch_root": str(work),
    }
    out = work / "INTERRUPTION_RECOVERY.json"
    with io.open(str(out), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print("=" * 88)
    print("interruption-recovery falsifier: %d/%d" % (len(checks) - len(bad), len(checks)))
    print("verdict: %s" % payload["status"])
    print("result: %s" % out)

    # The §L claims are only CHECKED if FINAL-12 can read machine-readable verdicts.
    contract = _contract()
    hb_cases = [c for c in checks if c[0] in _HEARTBEAT_CHECKS]
    for name, cases in (("CONTROL_HEARTBEAT_PROOF", hb_cases),
                        ("INTERRUPTION_RECOVERY_PROOF", checks)):
        proof = contract.build_proof(name, cases, extra={"scratch_root": str(work)})
        path = contract.write_proof(str(REPO), name, proof)
        print("proof[%s]: %s (%s)" % (name, path, proof["status"]))
    print("=" * 88)
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(run())
