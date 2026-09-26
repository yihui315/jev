#!/usr/bin/env python3
"""JCC continuation controller -- the FINAL release pipeline's durable executor.

Why this exists
---------------
A conversation turn is not an executor. Every phase below either takes tens of
minutes (the 40-minute soak) or must survive the thing that started it (the
watchdog restarting a dead controller). Running that work inside an agent turn
means it dies when the turn ends, and this project has already paid for that
lesson once: the service was killed by a Job Object the moment the launching
command returned, and a 4-minute gate run was terminated mid-flight leaving
fabricated all-FAIL evidence behind.

So the pipeline lives here:

* **State on disk, not in memory.** `.workbuddy/jcc_execution_state.json`,
  written temp -> flush -> fsync -> atomic replace. A hard kill loses at most the
  step in flight, and the step in flight is re-runnable by construction.
* **Progress is never a chat message.** A step is recorded as STARTED before it
  does anything and as PASSED only after its result artifact has been read back
  from disk and judged. "The child exited 0" is not progress; "the artifact it
  was supposed to write says PASS" is.
* **One instance only.** A lock file with a pid and an expiry; a second
  controller refuses instead of racing the first.
* **Restartable from anywhere.** `--resume` continues at `current_step`. The
  watchdog starts this file with `--resume` on every tick when no live
  controller holds the lock.

Verdict discipline
------------------
A step passes only if its declared result artifact exists, parses, and satisfies
its predicate. Nothing is inferred from an exit code, and no step may mark itself
complete: `runtime_state` and `project_run` are derived from the recorded step
results, never written by a step.

Usage
-----
    python tools/jcc_continuation.py status
    python tools/jcc_continuation.py run --resume
    python tools/jcc_continuation.py run --once          # advance as far as possible, then exit
    python tools/jcc_continuation.py run --only FINAL-08_SOAK
    python tools/jcc_continuation.py selftest            # falsify the state machine itself
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import io
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #

ROOT = r"F:\jev"
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")

#: The state file is overridable so the selftest can exercise the state machine
#: without rewriting the real pipeline's progress. A selftest that mutates the
#: thing it is testing is not a selftest.
STATE_PATH = os.environ.get(
    "JCC_STATE_PATH", os.path.join(ROOT, ".workbuddy", "jcc_execution_state.json"))
STATE_PREV = STATE_PATH + ".previous.json"
LOCK_DIR = os.path.join(ROOT, ".workbuddy", "locks")
CONTROLLER_LOCK = os.path.join(LOCK_DIR, "jcc-continuation.lock.json")
SCHED_LOG_DIR = os.path.join(ROOT, ".workbuddy", "scheduled", "logs")
STEP_LOG_DIR = os.path.join(ROOT, ".workbuddy", "scheduled", "steps")
ACC = os.path.join(ROOT, "artifacts", "acceptance")
REL = os.path.join(ROOT, "artifacts", "release")

SCHEMA_VERSION = 2
TZ = timezone(timedelta(hours=8))
LOCK_TTL_S = 900
MAX_TOTAL_S = 8 * 3600          # a controller run gives up after 8 h, not never
BACKOFF_LADDER = (10, 30, 60, 120, 300)

#: The only DEGRADED statuses the final gate's stop condition permits: the four
#: adapter capability probes, which report "this environment cannot exercise the
#: mechanism" rather than "this component is not evidenced". Anything else
#: degraded means the release is claiming completeness over a surface that is
#: not proven, so it must not be accepted as complete.
PERMITTED_DEGRADED = frozenset({"A-01", "A-02", "A-03", "A-04"})


def now() -> datetime:
    return datetime.now(TZ)


def iso(dt: Optional[datetime] = None) -> str:
    return (dt or now()).isoformat()


def log(msg: str) -> None:
    line = "[%s] %s" % (iso(), msg)
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        # This host's console code page is cp936; a non-encodable character in a
        # child's message must not be allowed to kill the controller mid-pipeline.
        print(line.encode("ascii", "backslashreplace").decode("ascii"), flush=True)


def rel(*parts: str) -> str:
    return os.path.join(ROOT, *parts)


# --------------------------------------------------------------------------- #
# Crash-safe state
# --------------------------------------------------------------------------- #

def fsync_atomic_write(path: str, payload: str) -> str:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_json(path: str) -> Optional[Any]:
    if not os.path.isfile(path):
        return None
    try:
        with io.open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (ValueError, OSError):
        return None


def default_state() -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "project_root": ROOT,
        "phase": "FINAL",
        "current_step": STEP_IDS[0],
        "last_completed_step": None,
        "pid": None,
        "started_at": None,
        "updated_at": None,
        "attempt": 0,
        "attempts": {},
        "last_error": None,
        "release_sha": None,
        "runtime_state": "IDLE",
        "project_run": "UNKNOWN",
        "steps_completed": [],
        "steps": {},
        "blockers": [],
        "next_action": None,
    }


def save_state(state: Dict[str, Any]) -> None:
    if os.path.isfile(STATE_PATH):
        try:
            os.replace(STATE_PATH, STATE_PREV)
        except OSError:
            pass
    state["updated_at"] = iso()
    fsync_atomic_write(STATE_PATH, json.dumps(state, indent=2, ensure_ascii=False))


def load_state() -> Dict[str, Any]:
    st = load_json(STATE_PATH) or {}
    merged = dict(default_state())
    merged.update(st if isinstance(st, dict) else {})
    merged["schema_version"] = SCHEMA_VERSION
    return merged


def verify_recorded_evidence(state: Dict[str, Any]) -> List[str]:
    """A PASS whose artifact is gone, or is no longer the bytes it was judged on,
    is not a PASS. Returns the ids that were downgraded.

    MEASURED 2026-09-26, and it is the reason this function exists. The state
    file said ``FINAL-01_CONTROLLER: PASS`` with ``evidence.sha256 =
    8917ac76...`` and ``bytes = 27349``; ``artifacts/acceptance/
    CONTINUATION_INFRA_TEST.json`` did not exist. ``run_infra_suite.py`` moves
    that file out of the repository before pytest runs -- so a run that is
    interrupted after the move and before the write leaves the repository
    WITHOUT the artifact -- and nothing went back to notice. The pipeline was
    one step away from reporting a whole release as evidenced by a file that is
    not there.

    So the recorded verdict is re-anchored to the disk on every state
    derivation. Existence is checked, and the sha256 is re-derived when the
    record carries one, because "the file exists" and "the file the step was
    judged on exists" are different claims and the second is the one the release
    needs. A step that fails this is marked BLOCKED with the reason named, which
    makes it re-runnable instead of silently complete.
    """
    downgraded: List[str] = []
    for sid, rec in (state.get("steps") or {}).items():
        if not isinstance(rec, dict) or rec.get("status") != "PASS":
            continue
        ev = rec.get("evidence") or {}
        rel_path = ev.get("path")
        if not rel_path:
            continue
        path = rel(*rel_path.split("/"))
        if not os.path.isfile(path):
            rec["status"] = "BLOCKED"
            rec["detail"] = ("the artifact this PASS was judged on is not on disk any "
                             "more: %s (it said: %s)"
                             % (rel_path, str(rec.get("detail"))[:200]))
            rec["evidence_checked"] = {"path": rel_path, "present": False, "at": iso()}
            downgraded.append(sid)
            continue
        recorded = ev.get("sha256")
        actual = sha256_file(path) if recorded else None
        rec["evidence_checked"] = {"path": rel_path, "present": True,
                                   "sha256_recorded": recorded,
                                   "sha256_now": actual, "at": iso()}
        if recorded and actual != recorded:
            rec["status"] = "BLOCKED"
            rec["detail"] = ("the artifact changed after it was judged: %s is now "
                             "%s, was %s (it said: %s)"
                             % (rel_path, actual, recorded, str(rec.get("detail"))[:160]))
            downgraded.append(sid)
    if downgraded:
        state["steps_completed"] = [s for s in (state.get("steps_completed") or [])
                                    if s not in downgraded]
        state["evidence_downgraded"] = sorted(downgraded)
    return downgraded


def derive(state: Dict[str, Any]) -> None:
    """Recompute the two derived fields from the recorded step results.

    Never set by a step: a step that could declare the project complete would be
    exactly the "instrument reports success" failure this pipeline exists to
    avoid.
    """
    verify_recorded_evidence(state)
    steps = state.get("steps") or {}
    failed = [k for k, v in steps.items() if v.get("status") == "BLOCKED"]
    pending = [s for s in STEP_IDS if (steps.get(s) or {}).get("status") != "PASS"]
    if failed:
        state["runtime_state"] = "BLOCKED"
        state["blockers"] = failed
    elif not pending:
        state["runtime_state"] = "COMPLETE"
        state["blockers"] = []
    else:
        state["runtime_state"] = "RUNNING"
        state["blockers"] = []

    gate = load_json(os.path.join(ACC, "project_run_gate_result.json")) or {}
    if gate.get("project_run"):
        state["project_run"] = gate["project_run"]
    elif not pending:
        state["project_run"] = "UNKNOWN"
    else:
        state["project_run"] = state.get("project_run") or "UNKNOWN"


# --------------------------------------------------------------------------- #
# Process helpers
# --------------------------------------------------------------------------- #

def pid_alive(pid: Any) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)   # QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    kernel32.CloseHandle(handle)
    return True


def acquire_controller_lock() -> Optional[Dict[str, Any]]:
    existing = load_json(CONTROLLER_LOCK)
    if isinstance(existing, dict):
        if pid_alive(existing.get("pid")) and str(existing.get("expires_at", "")) > iso():
            return None
    record = {
        "owner": "tools/jcc_continuation.py",
        "pid": os.getpid(),
        "acquired_at": iso(),
        "expires_at": iso(now() + timedelta(seconds=LOCK_TTL_S)),
        "controller_sha256": sha256_file(os.path.abspath(__file__)),
    }
    fsync_atomic_write(CONTROLLER_LOCK, json.dumps(record, indent=2))
    return record


def refresh_controller_lock() -> None:
    existing = load_json(CONTROLLER_LOCK)
    if isinstance(existing, dict) and existing.get("pid") == os.getpid():
        existing["expires_at"] = iso(now() + timedelta(seconds=LOCK_TTL_S))
        fsync_atomic_write(CONTROLLER_LOCK, json.dumps(existing, indent=2))


def release_controller_lock() -> None:
    existing = load_json(CONTROLLER_LOCK)
    if isinstance(existing, dict) and existing.get("pid") == os.getpid():
        try:
            os.remove(CONTROLLER_LOCK)
        except OSError:
            pass


def sha256_file(path: str) -> Optional[str]:
    if not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with io.open(path, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def git(*args: str) -> str:
    p = subprocess.run(["git"] + list(args), cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return (p.stdout or "").strip()


# --------------------------------------------------------------------------- #
# Step definitions
# --------------------------------------------------------------------------- #
#
# A step is: an optional command to run, and a RESULT ARTIFACT whose contents
# decide the verdict. The command's exit code is recorded but is never the
# verdict -- a child that exits 0 without writing its artifact has failed.

class Step:
    def __init__(self, sid: str, title: str, script: Optional[str],
                 args: Tuple[str, ...], result: Optional[str],
                 judge: Optional[Callable[[Any], Tuple[bool, str]]],
                 timeout_s: int, needs_service: bool = False):
        self.id = sid
        self.title = title
        self.script = script
        self.args = args
        self.result = result
        self.judge = judge
        self.timeout_s = timeout_s
        self.needs_service = needs_service


def _gate_judge(rec: Any) -> Tuple[bool, str]:
    if not isinstance(rec, dict):
        return False, "gate result is not an object"
    if rec.get("missing") or rec.get("failed"):
        return False, "missing=%r failed=%r" % (rec.get("missing"), rec.get("failed"))
    if rec.get("project_complete") is not True:
        return False, "project_complete=%r" % (rec.get("project_complete"),)
    if rec.get("blocker") not in (0, None):
        return False, "blocker=%r" % (rec.get("blocker"),)
    # DEGRADED is non-blocking, but only for the four adapter probes: the stop
    # condition permits "a mechanism this environment cannot exercise". It does
    # NOT permit a surface or a component to ride into a PASS while saying it is
    # not evidenced -- that is how S-04 shipped for weeks with the note "full
    # 8-screen coverage not evidenced". Refuse anything else by name.
    stray = [c for c in (rec.get("degraded") or []) if c not in PERMITTED_DEGRADED]
    if stray:
        return False, ("degraded outside the permitted set %s: %r"
                       % (sorted(PERMITTED_DEGRADED), stray))
    return True, "PROJECT_COMPLETE=true; %d check(s); blocker=0; degraded=%r" % (
        len(rec.get("checks") or []), sorted(rec.get("degraded") or []))


def _soak_judge(rec: Any) -> Tuple[bool, str]:
    if not isinstance(rec, dict):
        return False, "soak result is not an object"
    verdict = str(rec.get("verdict") or "")
    if not verdict.startswith("PASS"):
        return False, "verdict=%r" % (verdict[:160],)
    soak = rec.get("soak") or {}
    if soak.get("health_failures") not in (0, None) and soak.get("health_failures"):
        return False, "health_failures=%r" % (soak.get("health_failures"),)
    if soak.get("pid_changes") not in (0, None) and soak.get("pid_changes"):
        return False, "pid_changes=%r" % (soak.get("pid_changes"),)
    return True, "verdict=%s; polls=%s; health_failures=%s; pid_changes=%s" % (
        verdict[:80], soak.get("polls"), soak.get("health_failures"), soak.get("pid_changes"))


def _ok_judge(rec: Any) -> Tuple[bool, str]:
    if not isinstance(rec, dict):
        return False, "result is not an object"
    ok = rec.get("ok")
    if ok is not True:
        return False, "ok=%r verdict=%r" % (ok, str(rec.get("verdict"))[:120])
    return True, str(rec.get("verdict") or rec.get("detail") or "ok=true")[:160]


def _infra_judge(rec: Any) -> Tuple[bool, str]:
    """FINAL-01's verdict, read back from the artifact the suite wrote.

    Not the exit code, and not ``_ok_judge`` reuse, because the two failure modes
    that matter here are specific to this step:

    * the declared script is a pytest MODULE. Executing it as a script defines
      the tests and exits 0 -- the step passed by running nothing until
      tools/continuation/run_infra_suite.py took over. An exit code cannot tell
      that apart from a green suite; an artifact that counts its own checks can.
    * the artifact says "the suite passed". A run that deselected some of the
      suite must not be able to say that (tools/tests/conftest.py now refuses to
      write one), so the provenance is re-checked here as well: two independent
      places to notice, which is the point of a claim this load-bearing.
    """
    if not isinstance(rec, dict):
        return False, "infra result is not an object"
    if rec.get("ok") is not True or rec.get("status") != "PASS":
        return False, "ok=%r status=%r verdict=%r" % (
            rec.get("ok"), rec.get("status"), str(rec.get("verdict"))[:120])
    if rec.get("failed"):
        return False, "failed=%r" % (rec.get("failed"),)
    if not rec.get("check_count"):
        return False, ("check_count=%r: a suite that collected nothing is not a "
                       "suite that passed" % (rec.get("check_count"),))
    if rec.get("passed_count") != rec.get("check_count"):
        return False, "passed_count=%r check_count=%r" % (
            rec.get("passed_count"), rec.get("check_count"))
    selection = str((rec.get("provenance") or {}).get("selection") or "")
    if "full directory" not in selection:
        return False, ("provenance.selection=%r: the artifact does not state that "
                       "the whole suite ran" % selection)
    return True, "%s; %d check(s); failed=0; selection=full" % (
        rec.get("verdict"), rec.get("check_count"))


def _exists(rel_path: str) -> Tuple[bool, str]:
    p = rel(*rel_path.split("/"))
    if not os.path.isfile(p):
        return False, "missing %s" % rel_path
    return True, "%s present (%d bytes)" % (rel_path, os.path.getsize(p))


STEPS: List[Step] = [
    # The declared script is NOT the test module. `run_step` executes
    # `[venv-python, <script>] + args`, and running a pytest module as a script
    # defines the tests and exits 0 without running one of them -- a step that
    # passes by doing nothing, on the step that produces the platform's own
    # executor. run_infra_suite.py invokes pytest, parks the previous artifact so
    # a stale green file cannot stand in for this run, and reconciles the
    # artifact against pytest's junit report.
    Step("FINAL-01_CONTROLLER", "controller + state persistence + spawned-task registrations",
         "tools/continuation/run_infra_suite.py", (),
         "artifacts/acceptance/CONTINUATION_INFRA_TEST.json", _infra_judge, 900),
    Step("FINAL-05_DETACHED", "detached survival (behavioural, not in_job)",
         "tools/continuation/detached_survival.py", (),
         "artifacts/acceptance/DETACHED_SURVIVAL_TEST.json", _ok_judge, 900),
    Step("FINAL-07_SURFACES", "S-04 / S-05 evidence regenerated from source",
         "tools/continuation/regenerate_surfaces.py", (),
         "artifacts/acceptance/SURFACE_EVIDENCE.json", _ok_judge, 900),
    Step("FINAL-08_SOAK", "40-minute soak on the frozen runtime",
         "tools/continuation/soak_driver.py", ("40",),
         "artifacts/acceptance/JCC_SOAK_RESULT.json", _soak_judge, 5400,
         needs_service=True),
    Step("FINAL-09_RECOVERY", "service watchdog + continuation watchdog recovery",
         "tools/continuation/recovery_acceptance.py", (),
         "artifacts/acceptance/WATCHDOG_RECOVERY_PROOF.json", _ok_judge, 1800,
         needs_service=True),
    Step("FINAL-10_REBOOT_SIM", "host-restart recovery simulation (stale pid/lock/marker)",
         "tools/continuation/reboot_recovery_sim.py", (),
         "artifacts/acceptance/REBOOT_RECOVERY.json", _ok_judge, 1800),
    Step("FINAL-11_FINAL_GATE", "final gate on the frozen tree",
         "artifacts/acceptance/final_gate_run.py",
         ("--full", "--json-out", "artifacts/acceptance/final_gate_result.json"),
         "artifacts/acceptance/final_gate_result.json", _gate_judge, 3600),
    Step("FINAL-12_PROJECT_RUN", "project-run gate: every acceptance leg together",
         "artifacts/acceptance/project_run_gate.py", (),
         "artifacts/acceptance/project_run_gate_result.json", _ok_judge, 3600),
    Step("FINAL-13_PUBLISH", "publish release evidence with recomputable provenance",
         "tools/continuation/publish_release_evidence.py", (),
         "artifacts/release/final-manifest.json", _ok_judge, 900),
]

STEP_IDS: List[str] = [s.id for s in STEPS]
STEP_BY_ID: Dict[str, Step] = {s.id: s for s in STEPS}


# --------------------------------------------------------------------------- #
# Running a step
# --------------------------------------------------------------------------- #

def run_step(step: Step) -> Dict[str, Any]:
    """Execute one step and judge it from its artifact. Returns the step record."""
    started = time.time()
    record: Dict[str, Any] = {
        "id": step.id,
        "title": step.title,
        "started_at": iso(),
        "script": step.script,
        "result_path": step.result,
        "status": "STARTED",
    }
    if step.script:
        script = rel(*step.script.split("/"))
        if not os.path.isfile(script):
            record.update(status="BLOCKED", detail="missing script: %s" % step.script,
                          duration_s=0.0)
            return record
        os.makedirs(STEP_LOG_DIR, exist_ok=True)
        log_path = os.path.join(STEP_LOG_DIR, "%s.log" % step.id)
        cmd = [PY, script] + list(step.args)
        env = dict(os.environ)
        env["CODEBUDDY_SAFE_DELETE_ENABLED"] = "0"
        env["PAID_PROVIDER_CALLS"] = "0"
        env["EXTERNAL_PUBLISH"] = "0"
        env["JCC_CONTINUATION_CHILD"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        # The child's output goes to a FILE, never to a pipe this process holds:
        # the controller is long-lived and must not be coupled to a reader's
        # lifetime (the same trap the scheduled-task path had).
        with io.open(log_path, "ab") as logf:
            logf.write(("\n===== %s %s =====\n" % (step.id, iso())).encode("utf-8"))
            logf.flush()
            try:
                proc = subprocess.run(cmd, cwd=ROOT, stdout=logf, stderr=subprocess.STDOUT,
                                      env=env, timeout=step.timeout_s)
                rc: Optional[int] = proc.returncode
            except subprocess.TimeoutExpired:
                rc = None
                record["detail"] = "timeout after %d s" % step.timeout_s
        record["exit_code"] = rc
        record["log"] = os.path.relpath(log_path, ROOT).replace(os.sep, "/")
    else:
        record["exit_code"] = None

    record["duration_s"] = round(time.time() - started, 3)

    if step.result:
        rec = load_json(rel(*step.result.split("/")))
        if rec is None:
            record["status"] = "BLOCKED"
            record.setdefault("detail", "result artifact missing or unparsable: %s" % step.result)
            return record
        ok, detail = step.judge(rec) if step.judge else (True, "present")
        record["status"] = "PASS" if ok else "BLOCKED"
        record["detail"] = detail
        record["evidence"] = {
            "path": step.result,
            "sha256": sha256_file(rel(*step.result.split("/"))),
            "bytes": os.path.getsize(rel(*step.result.split("/"))),
        }
    else:
        # No artifact: the exit code is all there is, and it is explicitly a
        # weaker basis. Recorded as such rather than dressed up as evidence.
        record["status"] = "PASS" if record.get("exit_code") == 0 else "BLOCKED"
        record["detail"] = "exit code %r (no result artifact declared)" % record.get("exit_code")
    return record


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #

def run_pipeline(state: Dict[str, Any], only: Optional[str] = None,
                 once: bool = False, max_seconds: int = MAX_TOTAL_S) -> Dict[str, Any]:
    lock = acquire_controller_lock()
    if lock is None:
        other = load_json(CONTROLLER_LOCK) or {}
        log("another controller holds the lock (pid %s); refusing to race it"
            % other.get("pid"))
        return state

    state["pid"] = os.getpid()
    state.setdefault("started_at", iso())
    state["release_sha"] = state.get("release_sha") or git("rev-parse", "HEAD")
    save_state(state)
    log("controller pid=%d started; current_step=%s" % (os.getpid(), state["current_step"]))

    # RECONCILE BEFORE CHOOSING WORK. `verify_recorded_evidence` -- reached through
    # `derive` -- is what refuses a PASS whose artifact has gone or changed, and it
    # used to run only AFTER a step executed: that is, after the loop below had
    # already decided what was pending, from the raw and unreconciled state.
    #
    # MEASURED: `status` reported FINAL-01_CONTROLLER BLOCKED ("the artifact changed
    # after it was judged") while `run --only FINAL-01_CONTROLLER` answered "already
    # PASS; nothing to do", from the same state file. It self-healed one invocation
    # later, but a controller that must be run twice before it notices that its
    # evidence is gone is a controller that can be left holding a stale PASS -- and
    # re-anchoring a recorded verdict to the disk BEFORE acting on it is the whole
    # reason the reconciliation exists. So it runs first, and what it refused is
    # named in the log rather than left to be inferred from a later status call.
    derive(state)
    if state.get("evidence_downgraded"):
        log("evidence reconciliation refused %d recorded PASS(es): %s"
            % (len(state["evidence_downgraded"]),
               ", ".join(state["evidence_downgraded"])))
    save_state(state)

    deadline = time.time() + max_seconds
    backoff_idx = 0
    try:
        while True:
            pending = [s for s in STEPS
                       if (state["steps"].get(s.id) or {}).get("status") != "PASS"]
            if only:
                pending = [s for s in pending if s.id == only]
                if not pending and (state["steps"].get(only) or {}).get("status") == "PASS":
                    log("%s already PASS; nothing to do" % only)
                    break
            if not pending:
                log("every step PASS")
                break
            step = pending[0]
            state["current_step"] = step.id
            state["attempt"] += 1
            state["attempts"][step.id] = state["attempts"].get(step.id, 0) + 1
            state["runtime_state"] = "RUNNING"
            state["last_error"] = None
            save_state(state)
            refresh_controller_lock()
            log("STEP START %s -- %s" % (step.id, step.title))

            record = run_step(step)
            state["steps"][step.id] = record
            derive(state)
            if record["status"] == "PASS":
                if step.id not in state["steps_completed"]:
                    state["steps_completed"] = state["steps_completed"] + [step.id]
                state["last_completed_step"] = step.id
                state["last_error"] = None
                log("STEP PASS %s -- %s" % (step.id, record.get("detail")))
                save_state(state)
                backoff_idx = 0
                if once:
                    break
                continue

            state["last_error"] = {
                "step": step.id,
                "at": iso(),
                "kind": "BLOCKED",
                "detail": record.get("detail"),
                "log": record.get("log"),
                "attempt": state["attempts"][step.id],
            }
            state["next_action"] = "investigate %s and re-run `--resume`" % step.id
            save_state(state)
            log("STEP BLOCKED %s -- %s" % (step.id, record.get("detail")))
            if once:
                break
            if state["attempts"][step.id] >= len(BACKOFF_LADDER):
                log("giving up on %s after %d attempt(s); leaving state for a human"
                    % (step.id, state["attempts"][step.id]))
                break
            if time.time() > deadline:
                log("total budget exhausted with %s blocked" % step.id)
                break
            delay = BACKOFF_LADDER[min(backoff_idx, len(BACKOFF_LADDER) - 1)]
            backoff_idx += 1
            log("retrying %s in %d s (attempt %d)"
                % (step.id, delay, state["attempts"][step.id] + 1))
            save_state(state)
            time.sleep(delay)
        derive(state)
        save_state(state)
    finally:
        state["pid"] = None
        save_state(state)
        release_controller_lock()
    return state


# --------------------------------------------------------------------------- #
# Selftest: falsify the state machine itself
# --------------------------------------------------------------------------- #

def selftest() -> int:
    """Prove the controller's own rules can fail. Returns 0 iff every case holds."""
    cases: List[Tuple[str, bool, str]] = []

    def case(name: str, ok: bool, detail: str = "") -> None:
        cases.append((name, ok, detail))

    # 1. a step that exits 0 without writing its artifact must NOT pass
    judge_calls: List[str] = []

    def judge(rec: Any) -> Tuple[bool, str]:
        judge_calls.append("called")
        return True, "unreachable"

    s = Step("X-00", "no such script", "tools/continuation/__definitely_absent__.py",
             (), "artifacts/acceptance/__absent__.json", judge, 10)
    rec = run_step(s)
    case("missing_script_is_blocked", rec["status"] == "BLOCKED", str(rec.get("detail")))
    case("judge_not_called_without_artifact", judge_calls == [], str(judge_calls))

    # 2. a real script that exits 0 and writes no artifact must NOT pass
    probe = os.path.join(ROOT, ".workbuddy", "scratch", "final", "_selftest_ok.py")
    os.makedirs(os.path.dirname(probe), exist_ok=True)
    with io.open(probe, "w", encoding="utf-8") as fh:
        fh.write("raise SystemExit(0)\n")
    s2 = Step("X-01", "exits 0, writes nothing", os.path.relpath(probe, ROOT).replace(os.sep, "/"),
              (), "artifacts/acceptance/__absent__.json", judge, 30)
    rec2 = run_step(s2)
    case("exit0_without_artifact_is_blocked", rec2["status"] == "BLOCKED",
         "%s exit=%r" % (rec2.get("detail"), rec2.get("exit_code")))

    # 3. derive() must never report COMPLETE while a step is unpassed
    st = default_state()
    st["steps"] = {s.id: {"status": "PASS"} for s in STEPS[:-1]}
    derive(st)
    case("incomplete_is_not_complete", st["runtime_state"] == "RUNNING", st["runtime_state"])

    # 4. a BLOCKED step must surface as BLOCKED with the step named
    st2 = default_state()
    st2["steps"] = {STEPS[0].id: {"status": "BLOCKED"}}
    derive(st2)
    case("blocked_is_reported", st2["runtime_state"] == "BLOCKED"
         and st2["blockers"] == [STEPS[0].id], "%s %s" % (st2["runtime_state"], st2["blockers"]))

    # 4a-4c. A PASS has to still be true on disk. Measured 2026-09-26: the state
    #        said FINAL-01 PASS with a sha256 and the artifact was not there --
    #        run_infra_suite.py parks it before pytest and an interrupted run
    #        never puts one back.
    intact = "artifacts/acceptance/api_e2e.json"
    real_hash = sha256_file(rel(*intact.split("/")))
    case("the_fixture_for_the_evidence_recheck_exists", bool(real_hash), intact)

    st_ok = default_state()
    st_ok["steps"] = {STEPS[0].id: {"status": "PASS",
                                    "evidence": {"path": intact, "sha256": real_hash}}}
    derive(st_ok)
    case("an_intact_artifact_keeps_the_pass",
         st_ok["steps"][STEPS[0].id]["status"] == "PASS", str(st_ok["runtime_state"]))

    st_gone = default_state()
    st_gone["steps"] = {STEPS[0].id: {"status": "PASS", "detail": "said PASS",
                                      "evidence": {"path": "artifacts/acceptance/__gone__.json",
                                                   "sha256": "0" * 64}}}
    st_gone["steps_completed"] = [STEPS[0].id]
    derive(st_gone)
    gone = st_gone["steps"][STEPS[0].id]
    case("a_pass_whose_artifact_is_gone_is_not_a_pass",
         gone["status"] == "BLOCKED" and st_gone["steps_completed"] == [],
         "%s completed=%r" % (gone["status"], st_gone["steps_completed"]))
    case("and_it_is_named_in_the_detail",
         "not on disk" in str(gone.get("detail")), str(gone.get("detail"))[:120])

    st_changed = default_state()
    st_changed["steps"] = {STEPS[0].id: {"status": "PASS",
                                         "evidence": {"path": intact, "sha256": "0" * 64}}}
    derive(st_changed)
    case("a_pass_whose_bytes_changed_is_not_a_pass",
         st_changed["steps"][STEPS[0].id]["status"] == "BLOCKED",
         str(st_changed["steps"][STEPS[0].id].get("detail"))[:120])

    # 5. the gate judge must refuse a record with a blocker
    ok, _ = _gate_judge({"project_complete": True, "blocker": 0, "missing": [], "failed": []})
    case("gate_judge_accepts_clean", ok)
    ok2, d2 = _gate_judge({"project_complete": True, "blocker": 2,
                           "missing": ["P-01"], "failed": ["P-05"]})
    case("gate_judge_refuses_blocked", not ok2, d2)
    ok3, d3 = _gate_judge({"project_complete": False, "blocker": 0, "missing": [], "failed": []})
    case("gate_judge_refuses_incomplete", not ok3, d3)

    # 6. the soak judge must refuse a red verdict even with zero failures recorded
    ok4, _ = _soak_judge({"verdict": "FAIL: pid changed", "soak": {}})
    case("soak_judge_refuses_fail_verdict", not ok4)
    ok5, _ = _soak_judge({"verdict": "PASS: clean", "soak": {"health_failures": 0,
                                                            "pid_changes": 0, "polls": 1151}})
    case("soak_judge_accepts_clean", ok5)

    # 7. state round-trips through an atomic write and keeps the previous generation.
    #    Written to a scratch path: the selftest must not rewrite the pipeline's
    #    real progress (JCC_STATE_PATH is honoured by save_state/load_state).
    global STATE_PATH, STATE_PREV
    real_path, real_prev = STATE_PATH, STATE_PREV
    scratch = os.path.join(ROOT, ".workbuddy", "scratch", "final",
                           "_controller_selftest_state.json")
    os.makedirs(os.path.dirname(scratch), exist_ok=True)
    STATE_PATH, STATE_PREV = scratch, scratch + ".previous.json"
    for p in (STATE_PATH, STATE_PREV):
        if os.path.isfile(p):
            try:
                os.remove(p)
            except OSError:
                pass
    try:
        st3 = default_state()
        st3["current_step"] = "FINAL-08_SOAK"
        save_state(st3)
        back = load_state()
        case("state_roundtrip", back["current_step"] == "FINAL-08_SOAK", back["current_step"])
        st3["current_step"] = "FINAL-09_RECOVERY"
        save_state(st3)
        case("previous_generation_kept", os.path.isfile(STATE_PREV),
             "previous generation written on the second save")
        prev = load_json(STATE_PREV) or {}
        case("previous_generation_is_the_prior_state",
             prev.get("current_step") == "FINAL-08_SOAK", str(prev.get("current_step")))
    finally:
        STATE_PATH, STATE_PREV = real_path, real_prev

    print("=" * 74)
    for name, ok, detail in cases:
        print("  [%s] %-38s %s" % ("PASS" if ok else "FAIL", name, detail))
    bad = [c for c in cases if not c[1]]
    print("=" * 74)
    print("controller selftest: %d/%d" % (len(cases) - len(bad), len(cases)))
    return 0 if not bad else 1


def cmd_status() -> int:
    st = load_state()
    derive(st)
    view = {k: st.get(k) for k in (
        "current_step", "last_completed_step", "pid", "started_at", "attempt",
        "last_error", "release_sha", "runtime_state", "project_run", "blockers")}
    view["steps"] = {sid: (st["steps"].get(sid) or {}).get("status", "PENDING")
                     for sid in STEP_IDS}
    print(json.dumps(view, indent=2, ensure_ascii=False))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="JCC continuation controller")
    ap.add_argument("command", choices=["run", "status", "selftest"])
    ap.add_argument("--resume", action="store_true",
                    help="continue from current_step (the default; stated explicitly "
                         "by the watchdog so the intent is visible in its command line)")
    ap.add_argument("--once", action="store_true", help="advance one step, then exit")
    ap.add_argument("--only", choices=STEP_IDS, default=None)
    ap.add_argument("--max-seconds", type=int, default=MAX_TOTAL_S)
    args = ap.parse_args(argv)

    if args.command == "selftest":
        return selftest()
    if args.command == "status":
        return cmd_status()

    state = load_state()
    if getattr(args, "max_seconds", None):
        pass
    run_pipeline(state, only=args.only, once=args.once, max_seconds=args.max_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
