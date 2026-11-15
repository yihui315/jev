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
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
#
# ROOT is overridable so the interruption-recovery falsifier can drive the REAL
# controller against a scratch root (a copy of this file whose lock/state/runs
# all resolve under the scratch tree) without ever touching F:\jev. The watchdog
# exports the same JCC_ROOT to the controller it launches, so the two always
# agree on which tree they are talking about -- a watchdog rooted at A that
# starts a controller rooted at B is two systems, not one.

ROOT = os.environ.get("JCC_ROOT") or str(
    Path(__file__).resolve().parents[1]
)
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")

#: The state file is overridable so the selftest can exercise the state machine
#: without rewriting the real pipeline's progress. A selftest that mutates the
#: thing it is testing is not a selftest.
STATE_PATH = os.environ.get(
    "JCC_STATE_PATH", os.path.join(ROOT, ".workbuddy", "jcc_execution_state.json"))
STATE_PREV = STATE_PATH + ".previous.json"
LOCK_DIR = os.path.join(ROOT, ".workbuddy", "locks")
CONTROLLER_LOCK = os.path.join(LOCK_DIR, "jcc-continuation.lock.json")
#: One durable run descriptor per long-running step (see `run_step`). Ignored by
#: git (`/.workbuddy/` is in .gitignore), like the rest of the controller state.
RUNS_DIR = os.path.join(ROOT, ".workbuddy", "runs")
#: The watchdog's exclusive-controller ELECTION lock. Created with an atomic
#: CreateNew so exactly one tick can be the replacement launcher; see
#: scripts/continuation-watchdog.ps1 rule 3.
ELECTION_LOCK = os.path.join(LOCK_DIR, "controller-election.lock.json")
SCHED_LOG_DIR = os.path.join(ROOT, ".workbuddy", "scheduled", "logs")
STEP_LOG_DIR = os.path.join(ROOT, ".workbuddy", "scheduled", "steps")
ACC = os.path.join(ROOT, "artifacts", "acceptance")
REL = os.path.join(ROOT, "artifacts", "release")

SCHEMA_VERSION = 2
TZ = timezone(timedelta(hours=8))

#: The controller heartbeat lease. WHY THESE TWO NUMBERS AND NOT THE OLD 900 s.
#:
#: MEASURED DEFECT (live-lock, 2026-09-26): the old lock was refreshed only at the
#: TOP of each loop iteration -- once per step -- while `LOCK_TTL_S = 900` (15 min).
#: FINAL-08_SOAK runs ~41 minutes, so for the whole soak the lock was EXPIRED even
#: though the controller was alive and healthy. The watchdog requires
#: `$alive -and $fresh`; an expired lock fell through to "start a replacement",
#: and the replacement -- seeing "pid alive AND lock expired" -- overwrote the lock
#: and ran a SECOND controller next to the first. Two soak_drivers then raced
#: stop.ps1/start.ps1 through each other's PHASE R window. It can never converge.
#:
#: The fix is a heartbeat that is INDEPENDENT of the step: its own thread beats
#: every HEARTBEAT_INTERVAL seconds, so it keeps beating through a 41-minute soak,
#: a 10-minute pytest run, or a 2-hour external run. LOCK_TTL is 6x the interval,
#: so a lease survives five missed beats before it is called stale -- deliberately
#: generous, because the cost of a false "stale" is a duplicate controller.
HEARTBEAT_INTERVAL = 30
LOCK_TTL = 180
#: Legacy name. Kept so any caller that still reads LOCK_TTL_S keeps working; it
#: is the same value and the same meaning.
LOCK_TTL_S = LOCK_TTL
MAX_TOTAL_S = 8 * 3600          # a controller run gives up after 8 h, not never
BACKOFF_LADDER = (10, 30, 60, 120, 300)
#: A step whose declared timeout is at least this is a "long-running run": it gets
#: a durable descriptor under .workbuddy/runs/ and recovery ATTACHES to it rather
#: than re-launching it. 1800 s keeps FINAL-09/10/11/12 and FINAL-08 in and leaves
#: the four 900 s steps out.
LONG_RUNNING_MIN_S = 1800

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
        #: The backoff input. It means CONSECUTIVE REAL FAILURES of this step, not
        #: "controller generations that ever touched it". `attempts` above is kept
        #: as a truthful generation record but is DELIBERATELY not the backoff
        #: input: a replacement controller that attaches to a still-running long
        #: step must not count as a failure, and a pipeline that advances past a
        #: step must not carry its history into the next one. See run_pipeline.
        "consecutive_failures": {},
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
    """Is `pid` a LIVE process?

    STILL_ACTIVE, NOT "OpenProcess succeeded".
    MEASURED DEFECT, found by tools/tests/interruption_recovery.py. A process that
    has been TerminateProcess'd but whose object is still REFERENCED (e.g. the
    launcher still holds a handle) keeps answering `OpenProcess` -- so the old
    implementation returned True for a DEAD controller. A replacement controller
    then read the dead controller's still-fresh lease as live and refused to
    reclaim it, and the pipeline never recovered. `GetExitCodeProcess` is the
    authoritative answer and it reports STILL_ACTIVE (259) only for a live process.
    """
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
    try:
        code = ctypes.c_ulong(0)
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == 259                        # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def process_start_time_iso(pid: Any = None) -> Optional[str]:
    """The REAL start time of `pid`, as an ISO-8601 string, or None.

    WHY THIS IS NOT `datetime.now()`. The lease records the start time of the
    process that owns it, so a watchdog can tell "the live controller" apart from
    "an unrelated process that happens to have been given the same pid after a
    reuse". A pid is only unique while it is alive; Windows recycles them, and a
    recycled pid is exactly how a stale lock can look fresh to a liveness-only
    check. Recording the start time of OUR OWN process (os.getpid()) and comparing
    it later is the cheap, reliable discriminator.

    It is deliberately NOT a wall clock read at the moment the lease is written:
    that value would be true of the writer and meaningless about the process.
    """
    try:
        pid = int(pid if pid is not None else os.getpid())
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)   # QUERY_LIMITED_INFORMATION
    if not handle:
        # The pid exists but this caller may not query it, or it does not exist.
        # Indeterminate -- the caller decides; never fabricate a start time.
        return None
    try:
        creation = ctypes.c_ulonglong(0)
        exit_t = ctypes.c_ulonglong(0)
        kernel_t = ctypes.c_ulonglong(0)
        user_t = ctypes.c_ulonglong(0)
        ok = kernel32.GetProcessTimes(handle, ctypes.byref(creation),
                                      ctypes.byref(exit_t), ctypes.byref(kernel_t),
                                      ctypes.byref(user_t))
        if not ok or creation.value == 0:
            return None
        # FILETIME is 100-ns intervals since 1601-01-01 UTC.
        epoch_s = creation.value / 1e7 - 11644473600.0
        return datetime.fromtimestamp(epoch_s, TZ).isoformat()
    except OSError:
        return None
    finally:
        kernel32.CloseHandle(handle)


def pid_identity_matches(pid: Any, expected_start_iso: Optional[str]) -> Optional[bool]:
    """True if `pid` is the process the lease meant; None if we cannot tell.

    The three-valued result matters: `False` (a DIFFERENT process holds the pid
    now) is a definite reason to treat a lock as stale; `None` (we cannot query
    the process) must NOT be read as "stale", because the cost of being wrong is
    starting a second controller over one we merely cannot see. Callers combine
    this with pid_alive(), which already keeps that asymmetry.
    """
    if not expected_start_iso:
        return None
    actual = process_start_time_iso(pid)
    if not actual:
        return None
    try:
        a = datetime.fromisoformat(str(actual))
        b = datetime.fromisoformat(str(expected_start_iso))
    except ValueError:
        return None
    # Windows FILETIME has ~10 ms resolution on this host; 2 s is generous and
    # still far below any plausible pid-reuse interval.
    return abs((a - b).total_seconds()) < 2.0


# --------------------------------------------------------------------------- #
# The controller heartbeat lease
# --------------------------------------------------------------------------- #
#
# The lease file is the single thing every other component agrees on: the
# watchdog decides "is a controller live?" from it, and a second controller
# refuses to race the first from it. It used to be written twice per run (acquire
# + one refresh per step) and to carry only {pid, expires_at}. It now carries the
# identity of the run, and a background thread beats it independently of the step.

#: Mutable fields the main thread publishes into the lease; the heartbeat thread
#: merges them in on every beat. Guarded by _LEASE_LOCK so a beat never races a
#: step transition.
_LEASE: Dict[str, Any] = {
    "generation": 1,
    "current_step": None,
    "run_id": None,
    "head_sha": None,
    "run_started_at": None,
    "acquired_at": None,
}
_LEASE_LOCK = threading.Lock()
_HEARTBEAT: Dict[str, Any] = {"thread": None, "stop": None}


def _iso_is_fresh(ts: Optional[str], ttl_s: int) -> bool:
    """True if an ISO timestamp is within `ttl_s` of now. Unparsable -> stale."""
    if not ts:
        return False
    try:
        then = datetime.fromisoformat(str(ts))
    except ValueError:
        return False
    if then.tzinfo is None:
        then = then.replace(tzinfo=TZ)
    return (now() - then) < timedelta(seconds=ttl_s)


def _legacy_expires_fresh(lock: Dict[str, Any]) -> bool:
    """The pre-heartbeat freshness rule, kept verbatim for backward compatibility.

    A lock written by an older controller generation carries `expires_at` and no
    `heartbeat_at`. The generation running the moment this code is installed is
    exactly that, so this fallback is what makes installing the new watchdog safe
    MID-FLIGHT: for such a lock the watchdog behaves byte-for-byte as it always
    has. See scripts/continuation-watchdog.ps1 rule 2.
    """
    return str(lock.get("expires_at", "")) > iso()


def lock_is_live(lock: Optional[Dict[str, Any]]) -> bool:
    """Is this lease held by a LIVE controller? The single definition of live.

    New generation (has `heartbeat_at`): live iff the pid is alive AND (we can
    prove, or cannot disprove, that it is the same process) AND the heartbeat is
    fresh. A recycled pid therefore cannot be mistaken for the controller.
    Old generation (no `heartbeat_at`): fall back to `expires_at`, unchanged.
    """
    if not isinstance(lock, dict):
        return False
    pid = lock.get("pid")
    if not pid_alive(pid):
        return False
    heartbeat = lock.get("heartbeat_at")
    if heartbeat:
        identity = pid_identity_matches(pid, lock.get("process_start_time"))
        if identity is False:
            return False            # the pid now names a different process
        return _iso_is_fresh(heartbeat, LOCK_TTL)
    return _legacy_expires_fresh(lock)


def new_lease_record() -> Dict[str, Any]:
    """A fully-populated lease record for THIS process, written from inside it.

    WRITTEN FROM INSIDE THE CONTROLLER ON PURPOSE. `.venv\\Scripts\\python.exe`
    is a REDIRECTOR STUB on this host: the process that actually runs the
    controller is a CHILD of the pid Start-Process reports. So a lease written by
    the launcher would name the wrong pid. `os.getpid()` here is the real one.
    """
    return {
        "owner": "tools/jcc_continuation.py",
        "pid": os.getpid(),
        "process_start_time": process_start_time_iso(os.getpid()),
        "generation": _LEASE.get("generation", 1),
        "heartbeat_at": iso(),
        "current_step": _LEASE.get("current_step"),
        "run_id": _LEASE.get("run_id"),
        "head_sha": _LEASE.get("head_sha"),
        "acquired_at": _LEASE.get("acquired_at") or iso(),
        "expires_at": iso(now() + timedelta(seconds=LOCK_TTL)),
        "controller_sha256": sha256_file(os.path.abspath(__file__)),
    }


def acquire_controller_lock() -> Optional[Dict[str, Any]]:
    existing = load_json(CONTROLLER_LOCK)
    if lock_is_live(existing):
        return None
    record = new_lease_record()
    _LEASE["acquired_at"] = record["acquired_at"]
    with _LEASE_LOCK:
        fsync_atomic_write(CONTROLLER_LOCK, json.dumps(record, indent=2))
    return record


def refresh_controller_lock() -> None:
    """One heartbeat beat. Re-read, update only the lease fields, write atomically.

    Re-reading (rather than rewriting a cached record) means a beat can never
    revert a field another writer -- the main thread publishing `run_id`, or an
    operator editing the lease -- has just set.
    """
    with _LEASE_LOCK:
        existing = load_json(CONTROLLER_LOCK)
        if not (isinstance(existing, dict) and existing.get("pid") == os.getpid()):
            return
        existing["heartbeat_at"] = iso()
        existing["expires_at"] = iso(now() + timedelta(seconds=LOCK_TTL))
        if _LEASE.get("current_step") is not None:
            existing["current_step"] = _LEASE["current_step"]
        if _LEASE.get("run_id") is not None:
            existing["run_id"] = _LEASE["run_id"]
        fsync_atomic_write(CONTROLLER_LOCK, json.dumps(existing, indent=2))


def start_heartbeat() -> None:
    """Beat the lease from a thread the step cannot block.

    `run_step` executes the child with a BLOCKING subprocess call, so a heartbeat
    that only ran between steps would be exactly the defect being fixed. A thread
    keeps ticking while the main thread waits in `Popen.wait`, because CPython's
    Windows wait releases the GIL. That claim is MEASURED, not asserted: see
    tools/tests/test_interruption_recovery.py, which samples the lease for the
    whole of a synthetic long step and requires monotonic advances.
    """
    if _HEARTBEAT.get("thread") is not None:
        return
    stop = threading.Event()

    def _beat() -> None:
        while not stop.wait(HEARTBEAT_INTERVAL):
            try:
                refresh_controller_lock()
            except Exception:                               # noqa: BLE001
                # A beat that fails must never kill the pipeline; the next beat
                # (or the main thread's own refresh) tries again.
                pass

    t = threading.Thread(target=_beat, name="jcc-heartbeat", daemon=True)
    _HEARTBEAT["stop"] = stop
    _HEARTBEAT["thread"] = t
    t.start()


def stop_heartbeat() -> None:
    stop = _HEARTBEAT.get("stop")
    if stop is not None:
        stop.set()
    _HEARTBEAT["thread"] = None
    _HEARTBEAT["stop"] = None


def release_controller_lock() -> None:
    with _LEASE_LOCK:
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
                 timeout_s: int, needs_service: bool = False,
                 long_running: bool = False):
        self.id = sid
        self.title = title
        self.script = script
        self.args = args
        self.result = result
        self.judge = judge
        self.timeout_s = timeout_s
        self.needs_service = needs_service
        #: A long-running step gets a durable run descriptor under
        #: .workbuddy/runs/ and, on recovery, is ATTACHED to rather than
        #: re-launched. The declared timeout is the honest signal; a step can also
        #: force it (see FINAL-08).
        self.long_running = bool(long_running) or int(timeout_s) >= LONG_RUNNING_MIN_S


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

#: Steps that may be accepted with a NON-ZERO exit code. This is empty ON PURPOSE,
#: and the check below keeps it empty. "The script failed but its artifact looks
#: right" is exactly the reasoning that let a stale PASS file stand in for a run
#: that never happened, so the first exemption would have to be argued for rather
#: than slipped in.
EXIT_CODE_ALLOWLIST: Tuple[str, ...] = ()

if EXIT_CODE_ALLOWLIST:  # fail closed at import, not at the first step
    raise RuntimeError("EXIT_CODE_ALLOWLIST must stay empty; got %r"
                       % (EXIT_CODE_ALLOWLIST,))

#: Steps that structurally CANNOT have an exit code, and are therefore judged on
#: their artifact alone. EMPTY, and asserted empty at import.
#:
#: WHY THIS EXISTS AT ALL. The previous predicate tested `"exit_code" in record`
#: and skipped GATE 1 when the key was missing. That made "the key happens to be
#: absent" stand in for "this step does not need an exit code" -- DATA standing in
#: for POLICY. The three paths that omit the key are launch-failed, timeout and
#: an attached run: exactly the three where the exit code is unknown, i.e. exactly
#: the three that must not be read as success. An exemption now has to be a NAME in
#: this tuple, which means it has to be argued for in review and cannot be reached
#: by not writing a field. `Expects no exit code` is a property of a step, not of a
#: record.
EXIT_CODE_EXEMPT: Tuple[str, ...] = ()

if EXIT_CODE_EXEMPT:  # fail closed at import, not at the first step
    raise RuntimeError("EXIT_CODE_EXEMPT must stay empty; got %r -- an exempt step "
                       "is judged on its artifact alone, so each one needs its own "
                       "argument in the acceptance report" % (EXIT_CODE_EXEMPT,))


def _final10_judge(rec: Any) -> Tuple[bool, str]:
    """FINAL-10 verdict: is this evidence about THIS run, or merely "successful"?

    `_ok_judge` reads `ok` and nothing else, and `ok` is written by whoever
    produced the file -- including a producer that ran weeks ago. So the question
    here is not whether it succeeded but whether it is about this run. Three
    fields carry that, and a stale copy cannot have them: the candidate SHA, the
    platform, and when the check happened relative to this run's start.

    TWO HALVES, and both are needed -- they block different things:
      * _artifact_fresh (GATE 2, disk mtime vs started_at) blocks "the FILE is old".
        A touch-ed old file passes it, which is why it is not enough on its own.
      * the three self-declared fields here block "the file is new but belongs to
        another run". A freshly written file with the wrong candidate SHA passes
        mtime and must still be refused.
    Dropping either half leaves a hole the other one does not cover.
    """
    if not isinstance(rec, dict):
        return False, "result is not an object"
    want = _LEASE.get("head_sha")
    got = rec.get("release_sha")
    if not want:
        return False, "this run has no candidate SHA (state.release_sha is %r)" % (want,)
    if got != want:
        return False, "release_sha %r != candidate %r" % (got, want)
    platform = rec.get("platform")
    if not isinstance(platform, dict) or not platform.get("os"):
        return False, "platform missing or without os: %r" % (platform,)
    started = _LEASE.get("run_started_at")
    try:
        checked_dt = datetime.fromisoformat(str(rec.get("checked_at")))
        started_dt = datetime.fromisoformat(str(started))
    except (TypeError, ValueError):
        return False, ("checked_at %r / run_started_at %r unparsable"
                       % (rec.get("checked_at"), started))
    if checked_dt.timestamp() < started_dt.timestamp() - 1.0:
        return False, ("checked_at %s is BEFORE this run started at %s"
                       % (checked_dt.isoformat(), started_dt.isoformat()))
    ok = rec.get("ok")
    if ok is not True:
        return False, "ok=%r verdict=%r" % (ok, str(rec.get("verdict"))[:120])
    return True, str(rec.get("verdict") or "ok=true")[:160]


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
         "artifacts/acceptance/REBOOT_RECOVERY.json", _final10_judge, 1800),
    # FINAL-10B closes the make-or-break gap between "the pipeline produced
    # evidence" and "the final gate can be evaluated". The gate's P-01 reads
    # `git status` and FAILS on a dirty tree, and the steps above write TRACKED
    # evidence -- so without a boundary commit here, P-01 could never see a clean
    # tree and FINAL-11 was structurally unreachable. This step commits EXACTLY
    # the evidence the run just produced, fails closed if it cannot reach a clean
    # tree, and it does NOT touch P-01: the gate's cleanliness rule is unchanged
    # and no "evidence is tolerated" branch is added anywhere.
    #
    # THE RESULT PATH MUST NOT BE THE RUN DESCRIPTOR PATH. This step's 1800 s
    # timeout makes it long_running, so `run_descriptor_path` is
    # `.workbuddy/runs/FINAL-10B_EVIDENCE_COMMIT.json`. It used to pass that SAME
    # path as `--json-out` while also declaring it as `result`: the controller
    # wrote its RUNNING descriptor there, the child then overwrote it with its own
    # result (losing pid/run_id), and on recovery `_artifact_fresh(result_path=the
    # descriptor itself)` was trivially true -- so an `attach_to_run` could read a
    # RUNNING record as this step's result, mark the step BLOCKED, and launch a
    # SECOND commit tool. The result therefore goes to a `.result.json` sibling,
    # and `assert_step_results_are_not_run_descriptors(STEPS)` below refuses the
    # old pairing if it is ever reintroduced.
    Step("FINAL-10B_EVIDENCE_COMMIT", "commit produced evidence; require a clean tree",
         "tools/finalize/commit_evidence.py",
         ("--message", "chore(final): commit release evidence produced by the "
                       "FINAL pipeline run",
          "--json-out", ".workbuddy/runs/FINAL-10B_EVIDENCE_COMMIT.result.json"),
         ".workbuddy/runs/FINAL-10B_EVIDENCE_COMMIT.result.json", _ok_judge, 1800),
    # FINAL-11's AUTHORITATIVE result goes to an IGNORED staging path, not to the
    # tracked canonical file. Rationale (defect 3, self-reference): a commit must
    # never be required to already contain a gate result that does not exist yet,
    # and a gate that writes a tracked file re-dirties the very tree its own P-01
    # just certified. The tracked canonical summary is produced by FINAL-11B
    # (and consumed by the publisher) AFTER the gate has run. P-01 is NOT touched.
    Step("FINAL-11_FINAL_GATE", "final gate on the frozen tree",
         "artifacts/acceptance/final_gate_run.py",
         ("--full", "--record-moment",
          "--json-out", "runtime/final-gate/final_gate_result.json"),
         "runtime/final-gate/final_gate_result.json", _gate_judge, 3600),
    Step("FINAL-11B_CANONICAL_GATE", "publish the tracked canonical gate summary",
         "tools/finalize/publish_gate_result.py", (),
         "artifacts/acceptance/final_gate_result.json", _gate_judge, 900),
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
#
# A long-running step is a RUN, not merely a step. The difference matters the
# moment a controller dies mid-step: re-running a 41-minute soak from zero is
# both wasteful and destructive (soak PHASE Q stops and starts the service), and
# a second soak_driver running next to the first is exactly the live-lock this
# project hit. So each long-running step gets ONE durable descriptor under
# .workbuddy/runs/, written atomically (temp -> fsync -> os.replace), and
# recovery ATTACHES to it instead of launching a second one:
#
#   descriptor exists AND its pid is live  -> ATTACH_EXISTING_RUN (observe only)
#   descriptor exists AND its pid is dead  -> RUN_LOST            (then relaunch)
#   no descriptor                          -> normal launch
#
# "Observe" means: wait for the run's declared artifact to appear, judge it with
# the step's own judge, and never launch a second one. The consecutive-failure
# counter is NOT incremented for an attach -- a replacement controller that found
# the run alive has not failed at anything.

RUN_DESCRIPTOR_SCHEMA = "jcc-controller-run/1"


def run_descriptor_path(step: "Step") -> str:
    return os.path.join(RUNS_DIR, "%s.json" % step.id)


def _norm_path(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def step_result_path(step: "Step") -> Optional[str]:
    """The absolute path of the step's declared result artifact, if any."""
    return rel(*step.result.split("/")) if step.result else None


def step_result_descriptor_conflict(step: "Step") -> Optional[str]:
    """Description of a result/descriptor path collision, else None. Pure.

    A step's RESULT (what it produced) and its RUN DESCRIPTOR (the bookkeeping
    record of the process producing it) must be different files. When they are the
    same file, two things break at once: the child overwrites the descriptor, so
    the pid/run_id a replacement controller needs are gone; and `_artifact_fresh`
    is asked about the descriptor itself, so a RUNNING record reads as a finished
    artifact and the step can be judged -- and relaunched -- off its own run
    record. Measured on FINAL-10B before this guard existed.
    """
    result = step_result_path(step)
    if not result:
        return None
    descriptor = run_descriptor_path(step)
    if _norm_path(result) == _norm_path(descriptor):
        return ("%s declares result %r, which IS its run descriptor %r"
                % (step.id, step.result,
                   os.path.relpath(descriptor, ROOT).replace(os.sep, "/")))
    return None


def assert_step_results_are_not_run_descriptors(steps: List["Step"]) -> None:
    """Fail closed if ANY step's result artifact is its own run descriptor.

    A guard over the whole table rather than a fixed constant: it fires for any
    step that reintroduces the pairing, not just for the one that was repaired.
    """
    conflicts = [c for c in (step_result_descriptor_conflict(s) for s in steps) if c]
    if conflicts:
        raise AssertionError(
            "step/descriptor path collision -- a step's result would be overwritten "
            "by, or mistaken for, its own run record. Give the result a distinct "
            "path: " + "; ".join(conflicts))


#: Structural invariant, checked at import: the pipeline cannot even be loaded
#: with a step whose result artifact is its own run descriptor.
assert_step_results_are_not_run_descriptors(STEPS)


def load_run_descriptor(step: "Step") -> Optional[Dict[str, Any]]:
    d = load_json(run_descriptor_path(step))
    return d if isinstance(d, dict) else None


def _write_run_descriptor(step: "Step", payload: Dict[str, Any]) -> None:
    payload["schema"] = RUN_DESCRIPTOR_SCHEMA
    fsync_atomic_write(run_descriptor_path(step),
                       json.dumps(payload, indent=2, ensure_ascii=False))


def _new_run_id(step: "Step") -> str:
    return "%s-%s-%d" % (step.id, datetime.now(TZ).strftime("%Y%m%dT%H%M%S"), os.getpid())


def _freshness_verdict(path: str, since_iso: Optional[str]) -> Tuple[bool, str]:
    """Was the artifact written at or after `since_iso`? Returns (ok, reason-tag).

    A stale artifact left by an earlier attempt must never be read as this run's
    result. "I cannot tell when this run started" is NOT the same as "the file is
    fine": the two unknown-input branches below used to `return True`, which made
    a missing/unparsable `since` a way to switch the gate off by not filling in a
    field. They now fail closed and say which input was bad, so the reason is in
    the record instead of in the reader's head.
    """
    if not os.path.isfile(path):
        return False, "ARTIFACT_MISSING"
    if not since_iso:
        return False, "FRESHNESS_SINCE_MISSING"
    try:
        since = datetime.fromisoformat(str(since_iso)).timestamp()
    except (TypeError, ValueError):
        return False, "FRESHNESS_SINCE_UNPARSABLE"
    if os.path.getmtime(path) >= since - 1.0:
        return True, "FRESH"
    return False, "ARTIFACT_PREDATES_RUN"


def _artifact_fresh(path: str, since_iso: Optional[str]) -> bool:
    """Boolean view of `_freshness_verdict`, kept for the descriptor call sites."""
    return _freshness_verdict(path, since_iso)[0]


def _carry_detail(record: Dict[str, Any], new_detail: str) -> str:
    """Carry the earlier reason forward instead of letting a verdict erase it.

    A failing step usually already knows why it failed ("launch failed: ...",
    "timeout after 1800 s"). Overwriting that with a judge's one-liner keeps the
    information that the step RAN and loses the information that it BROKE, and the
    survivor then reads as a conclusion. Both are kept, oldest first.
    """
    prior = record.get("detail")
    if prior and prior not in new_detail:
        return "%s | %s" % (prior, new_detail)
    return new_detail


def _judge_artifact(step: "Step", record: Dict[str, Any]) -> Dict[str, Any]:
    """Fill in status/detail/evidence from the step's declared artifact."""
    if not step.result:
        # No artifact: the exit code is all there is, and it is explicitly a
        # weaker basis. Recorded as such rather than dressed up as evidence.
        record["status"] = "PASS" if record.get("exit_code") == 0 else "BLOCKED"
        record["detail"] = "exit code %r (no result artifact declared)" % record.get("exit_code")
        return record
    path = rel(*step.result.split("/"))
    rec = load_json(path)
    if rec is None:
        record["status"] = "BLOCKED"
        record.setdefault("detail", "result artifact missing or unparsable: %s" % step.result)
        return record
    # GATE 1 -- the exit code is a verdict, not a field. It used to be recorded and
    # then ignored whenever the step declared a result artifact, so a script that
    # crashed still passed as long as the previous run's file was on disk.
    #
    # `rc is None` is NOT "no opinion". run_step writes None when the child timed
    # out, when the launch failed, and when it exited without one -- that is, in
    # exactly the cases where the step did not finish its job. Measured on
    # 0bb240b: {"exit_code": None, "detail": "launch failed: [WinError 2] ..."}
    # came back PASS. The predicate is therefore "did it exit 0", not "does it
    # have a number".
    #
    # AND AN ABSENT KEY IS NOT "not applicable" (aegis, P1). This used to read
    # `rc_present = "exit_code" in record` and skip GATE 1 when it was missing --
    # but the key is missing on exactly the three paths where the exit code is
    # UNKNOWN (launch failed, timeout, an attached run this process never
    # launched). A missing field looks like "this rule does not apply here" and
    # is in fact "this rule did not run here", which is worse than a rule that
    # was never written: it leaves no trace. So: unknown exit code -> FAIL. A step
    # that genuinely cannot have one must be named in EXIT_CODE_EXEMPT (empty).
    rc = record.get("exit_code")
    rc_missing = "exit_code" not in record
    if step.script and rc_missing and step.id not in EXIT_CODE_EXEMPT:
        record["status"] = "FAIL"
        record["detail"] = _carry_detail(record, (
            "exit_code absent from the step record for %s -- EXIT_CODE_UNKNOWN "
            "(launch failed, timeout, or a run this process attached to and never "
            "launched); an unknown exit cannot be treated as success"
            % (step.script or step.id)))
        return record
    if step.script and rc != 0 and step.id not in EXIT_CODE_ALLOWLIST:
        record["status"] = "FAIL"
        record["detail"] = _carry_detail(record, (
            "exit code %r from %s; a non-zero exit (including None: timeout, launch "
            "failure, or an exit without a status) is fatal and no artifact may "
            "override it" % (rc, step.script or step.id)))
        return record
    # GATE 2 -- freshness. `_artifact_fresh` was written for exactly this sentence
    # but was only ever wired to run descriptors, never to a step result. A PASS
    # file from an earlier era is a true record of an earlier success and a false
    # claim about this run.
    #
    # `since` is resolved by KEY PRESENCE, not by truthiness. `or` would fall back
    # to the attach moment whenever the descriptor had no `started_at`, so the
    # "we do not know when this run began" case silently became "measure against
    # now" -- a different question than the one GATE 2 asks. An explicit None now
    # reaches `_freshness_verdict` and fails closed there.
    since = record["freshness_since"] if "freshness_since" in record else record.get("started_at")
    fresh, why = _freshness_verdict(path, since)
    if not fresh:
        record["status"] = "FAIL"
        record["detail"] = _carry_detail(record, (
            "result artifact %s is not this run's result (%s, since=%r); a stale "
            "PASS file is a true record of an earlier success and a false claim "
            "about this run" % (step.result, why, since)))
        return record

    ok, detail = step.judge(rec) if step.judge else (True, "present")
    record["status"] = "PASS" if ok else "BLOCKED"
    # The verdict text must never erase a pre-existing reason. aegis measured the
    # old behaviour: an input detail of "launch failed: ..." came back as
    # "PASS: a simulated reboot ...", so the cause was gone and the surviving
    # sentence pointed the reader at a conclusion.
    record["detail"] = detail if ok else _carry_detail(record, detail)
    record["evidence"] = {
        "path": step.result,
        "sha256": sha256_file(path),
        "bytes": os.path.getsize(path),
    }
    return record


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
        os.makedirs(RUNS_DIR, exist_ok=True)
        log_path = os.path.join(STEP_LOG_DIR, "%s.log" % step.id)
        cmd = [PY, script] + list(step.args)
        env = dict(os.environ)
        env["CODEBUDDY_SAFE_DELETE_ENABLED"] = "0"
        env["PAID_PROVIDER_CALLS"] = "0"
        env["EXTERNAL_PUBLISH"] = "0"
        env["JCC_CONTINUATION_CHILD"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        run_id = _new_run_id(step) if step.long_running else None
        # The child's output goes to a FILE, never to a pipe this process holds:
        # the controller is long-lived and must not be coupled to a reader's
        # lifetime (the same trap the scheduled-task path had).
        with io.open(log_path, "ab") as logf:
            logf.write(("\n===== %s %s =====\n" % (step.id, iso())).encode("utf-8"))
            logf.flush()
            rc: Optional[int] = None
            try:
                # Popen (not subprocess.run) because a long-running step must
                # record the pid of the process it actually launched: that pid is
                # what a replacement controller checks to decide "attach" vs
                # "relaunch".
                proc = subprocess.Popen(cmd, cwd=ROOT, stdout=logf,
                                        stderr=subprocess.STDOUT, env=env)
                if run_id is not None:
                    record["run_id"] = run_id
                    _LEASE["run_id"] = run_id
                    _LEASE["current_step"] = step.id
                    refresh_controller_lock()
                    _write_run_descriptor(step, {
                        "run_id": run_id,
                        "step": step.id,
                        "pid": proc.pid,
                        "started_at": iso(),
                        "expected_finish_at": iso(now() + timedelta(seconds=step.timeout_s)),
                        "output": os.path.relpath(log_path, ROOT).replace(os.sep, "/"),
                        "evidence": step.result,
                        "status": "RUNNING",
                    })
                try:
                    rc = proc.wait(timeout=step.timeout_s)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    rc = None
                    record["detail"] = "timeout after %d s" % step.timeout_s
            except OSError as exc:
                rc = None
                record["detail"] = "launch failed: %s" % exc
        if run_id is not None:
            status = "PASS" if rc == 0 else "FINISHED"
            d = load_run_descriptor(step) or {}
            d.update({"status": status, "exit_code": rc,
                      "finished_at": iso(), "pid_dead": not pid_alive(d.get("pid"))})
            _write_run_descriptor(step, d)
            _LEASE["run_id"] = None
            refresh_controller_lock()
        record["exit_code"] = rc
        record["log"] = os.path.relpath(log_path, ROOT).replace(os.sep, "/")
    else:
        record["exit_code"] = None

    #: What the freshness gate measures against. Explicit rather than inferred from
    #: `started_at`, because `attach_to_run` observes a step that began earlier.
    record["freshness_since"] = record.get("started_at")
    record["duration_s"] = round(time.time() - started, 3)
    return _judge_artifact(step, record)


def attach_to_run(step: Step) -> Dict[str, Any]:
    """Observe a long-running step that is ALREADY running. Never launches one.

    Called by `run_pipeline` when the persisted `current_step` is a long-running
    step and a durable descriptor exists for it with a LIVE pid. Waiting for the
    existing run's artifact to appear is the whole point: the alternative -- a
    fresh launch -- is the duplicate-run live-lock.
    """
    started = time.time()
    desc = load_run_descriptor(step) or {}
    record: Dict[str, Any] = {
        "id": step.id,
        "title": step.title,
        "started_at": iso(),
        "script": step.script,
        "result_path": step.result,
        "status": "ATTACHED",
        "attached_run": {"run_id": desc.get("run_id"), "pid": desc.get("pid"),
                         "observed_only": True, "note": "ATTACH_EXISTING_RUN"},
    }
    result_path = rel(*step.result.split("/")) if step.result else None
    deadline = time.time() + int(step.timeout_s)
    while time.time() < deadline:
        if result_path and _artifact_fresh(result_path, desc.get("started_at")):
            break
        if not pid_alive(desc.get("pid")):
            break
        time.sleep(2)
    record["duration_s"] = round(time.time() - started, 3)
    record["attached_run"]["pid_alive_after_observe"] = pid_alive(desc.get("pid"))
    #: The observed run began before this attach, so freshness is measured against
    #: the descriptor's start. Using the attach moment would fail every legitimate
    #: attach, which is why this key exists instead of a bare `started_at`.
    record["freshness_since"] = desc.get("started_at")
    # THE EXIT CODE OF A RUN THIS PROCESS DID NOT LAUNCH.
    #
    # The descriptor is the only place it can be known from, and it only carries it
    # if the controller that launched the child lived long enough to write it (see
    # the `d.update({"exit_code": rc, ...})` in run_step). When the original
    # controller died mid-run -- which is the ONLY reason an attach happens -- the
    # descriptor still says RUNNING and the exit code is genuinely unknown.
    #
    # That case used to be invisible: `_judge_artifact` skipped GATE 1 whenever the
    # key was absent, and this record never had the key, so EVERY attach skipped
    # GATE 1. Combined with a descriptor lacking `started_at` (which switched GATE 2
    # off as well, because an empty `since` used to mean "fresh"), the two gates
    # were simultaneously absent on this path -- for FINAL-10_REBOOT_SIM, whose 1800 s
    # timeout makes it long_running, that meant the reboot evidence could be judged
    # with no gate at all. Now: copy the code across when it exists, and when it does
    # not, leave the key absent so `_judge_artifact` refuses it as EXIT_CODE_UNKNOWN.
    # The refusal is the correct answer -- "we could not confirm how it ended" is not
    # "it ended well".
    if "exit_code" in desc:
        record["exit_code"] = desc.get("exit_code")
        record["exit_code_source"] = "run-descriptor"
    else:
        record["exit_code_source"] = "run-descriptor (absent: EXIT_CODE_UNKNOWN)"
    if result_path and _artifact_fresh(result_path, desc.get("started_at")):
        return _judge_artifact(step, record)
    if pid_alive(desc.get("pid")):
        # The run is STILL GOING. That is not a failure and must not be counted as
        # one: WAITING tells run_pipeline to leave the run alone and stop, so the
        # next generation attaches again. Relaunching here would be the duplicate
        # run this whole mechanism exists to prevent.
        record["status"] = "WAITING"
        record["detail"] = ("attached run %s (pid %s) is still running; not relaunching"
                           % (desc.get("run_id"), desc.get("pid")))
        return record
    record["status"] = "BLOCKED"
    #: The reason travels with the refusal. "RUN_LOST" alone reads as "the file was
    #: missing", which is only one of the ways a result can be inadmissible -- the
    #: descriptor could also lack a `started_at`, in which case nobody can say
    #: whether the file predates the run.
    lost_why = ("ARTIFACT_MISSING" if not result_path
                else _freshness_verdict(result_path, desc.get("started_at"))[1])
    record["detail"] = ("the attached run pid %s produced no admissible result "
                        "(%s) for %s (RUN_LOST)"
                        % (desc.get("pid"), lost_why, step.result))
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

    # PUBLISH THE RUN'S IDENTITY, THEN START THE INDEPENDENT HEARTBEAT.
    #
    # The heartbeat is started BEFORE the first step and beats on its own thread,
    # so the lease stays fresh for the WHOLE of a 41-minute soak -- not only at the
    # top of each loop iteration, which is the defect that let the lock expire
    # while the controller was alive and healthy. The generation counter is a
    # truthful record of how many controller generations have run this pipeline;
    # it is deliberately NOT the backoff input (see `consecutive_failures`).
    _LEASE["generation"] = int(state.get("controller_generation") or 0) + 1
    _LEASE["current_step"] = state["current_step"]
    _LEASE["head_sha"] = state["release_sha"]
    _LEASE["run_started_at"] = state.get("started_at")
    state["controller_generation"] = _LEASE["generation"]
    refresh_controller_lock()
    start_heartbeat()

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
            _LEASE["current_step"] = step.id
            state["runtime_state"] = "RUNNING"
            state["last_error"] = None
            save_state(state)
            refresh_controller_lock()

            # RECOVERY: if the persisted long-running step already has a run on
            # disk, ATTACH to it instead of launching a second one. This is the
            # fix for "a replacement re-runs the long step instead of attaching to
            # it". A replacement that finds the run alive is proving the run is
            # making progress, so it neither increments the failure counter nor
            # starts a new process.
            attached: Optional[Dict[str, Any]] = None
            if step.long_running:
                desc = load_run_descriptor(step)
                artifact_fresh = bool(
                    step.result and _artifact_fresh(rel(*step.result.split("/")),
                                                    desc.get("started_at"))) if desc else False
                if desc and (pid_alive(desc.get("pid")) or artifact_fresh):
                    attached = desc
                    log("ATTACH_EXISTING_RUN %s -- run_id=%s pid=%s alive=%s; observing, "
                        "never launching a second one"
                        % (step.id, desc.get("run_id"), desc.get("pid"),
                           pid_alive(desc.get("pid"))))
                elif desc:
                    log("RUN_LOST %s -- descriptor pid %s is not alive and no fresh "
                        "artifact exists; launching a replacement"
                        % (step.id, desc.get("pid")))
                    _write_run_descriptor(step, dict(desc, status="RUN_LOST",
                                                     lost_at=iso()))

            if attached is not None:
                record = attach_to_run(step)
            else:
                # A real (re)launch. Only THIS path counts toward the backoff
                # ladder; an attach must never move it.
                state["attempts"][step.id] = state["attempts"].get(step.id, 0) + 1
                save_state(state)
                log("STEP START %s -- %s" % (step.id, step.title))
                record = run_step(step)

            # An attach that is still observing is neither a pass nor a failure:
            # leave the run alone and let the next generation attach again. NOT
            # incrementing here is what stops the "replacement re-runs / gives up"
            # oscillation.
            if record["status"] == "WAITING":
                state["steps"][step.id] = record
                save_state(state)
                log("ATTACH still running %s -- leaving the run to complete; not relaunching"
                    % step.id)
                break

            state["steps"][step.id] = record
            derive(state)
            if record["status"] == "PASS":
                if step.id not in state["steps_completed"]:
                    state["steps_completed"] = state["steps_completed"] + [step.id]
                state["last_completed_step"] = step.id
                state["last_error"] = None
                # The pipeline advanced past this step: its consecutive real
                # failures are history and must not gate anything later.
                state["consecutive_failures"][step.id] = 0
                if step.long_running:
                    _write_run_descriptor(step, dict(load_run_descriptor(step) or {},
                                                     status="PASS", finished_at=iso()))
                log("STEP PASS %s -- %s" % (step.id, record.get("detail")))
                save_state(state)
                backoff_idx = 0
                if once:
                    break
                continue

            # A BLOCKED record. `consecutive_failures` is the backoff input; the
            # generation counter `attempts` is kept only as a truthful record.
            state["consecutive_failures"][step.id] = \
                state["consecutive_failures"].get(step.id, 0) + 1
            state["last_error"] = {
                "step": step.id,
                "at": iso(),
                "kind": "BLOCKED",
                "detail": record.get("detail"),
                "log": record.get("log"),
                "consecutive_failures": state["consecutive_failures"][step.id],
                "generation_attempts": state["attempts"].get(step.id, 0),
            }
            state["next_action"] = "investigate %s and re-run `--resume`" % step.id
            save_state(state)
            log("STEP BLOCKED %s -- %s" % (step.id, record.get("detail")))
            if once:
                break
            if state["consecutive_failures"][step.id] >= len(BACKOFF_LADDER):
                log("giving up on %s after %d consecutive failure(s); leaving state for a human"
                    % (step.id, state["consecutive_failures"][step.id]))
                break
            if time.time() > deadline:
                log("total budget exhausted with %s blocked" % step.id)
                break
            delay = BACKOFF_LADDER[min(backoff_idx, len(BACKOFF_LADDER) - 1)]
            backoff_idx += 1
            log("retrying %s in %d s (consecutive failure %d)"
                % (step.id, delay, state["consecutive_failures"][step.id]))
            save_state(state)
            time.sleep(delay)
        derive(state)
        save_state(state)
    finally:
        stop_heartbeat()
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

    # 6b. the lease rule. BACKWARD COMPATIBILITY IS THE POINT of the first two:
    #     a lock written by the running (old) generation has no heartbeat, and the
    #     new rule must answer about it EXACTLY as the old rule did -- that is what
    #     makes installing the new watchdog safe mid-flight.
    me = os.getpid()
    legacy_fresh = {"pid": me, "expires_at": iso(now() + timedelta(seconds=600))}
    case("legacy_lock_without_heartbeat_is_live_like_before", lock_is_live(legacy_fresh))
    legacy_stale = {"pid": me, "expires_at": iso(now() - timedelta(seconds=600))}
    case("legacy_expired_lock_is_not_live", not lock_is_live(legacy_stale))
    my_start = process_start_time_iso(me)
    case("the_process_can_report_its_own_start_time", bool(my_start), str(my_start))
    hb_fresh = {"pid": me, "heartbeat_at": iso(now()), "process_start_time": my_start}
    case("heartbeat_lease_is_live", lock_is_live(hb_fresh))
    hb_stale = {"pid": me, "process_start_time": my_start,
                "heartbeat_at": iso(now() - timedelta(seconds=LOCK_TTL + 60))}
    case("stale_heartbeat_is_not_live", not lock_is_live(hb_stale))
    hb_reused = {"pid": me, "heartbeat_at": iso(now()),
                 "process_start_time": iso(now() - timedelta(days=400))}
    case("a_reused_pid_is_not_mistaken_for_the_controller", not lock_is_live(hb_reused))
    case("a_dead_pid_is_not_live_even_with_a_heartbeat_field",
         not lock_is_live({"pid": 0, "heartbeat_at": iso(now())}))
    case("an_absent_lock_is_not_live", not lock_is_live(None))

    # 6c. run descriptors: the attach/relaunch decision, without launching anything
    #     or touching a REAL step's descriptor.
    tmp_step = Step("X-LONG", "synthetic long step", None, (), None, None, 5400)
    _write_run_descriptor(tmp_step, {"pid": me, "status": "RUNNING", "run_id": "r1"})
    back = load_run_descriptor(tmp_step) or {}
    case("a_run_descriptor_round_trips", back.get("run_id") == "r1", str(back.get("run_id")))
    case("a_large_timeout_is_long_running", tmp_step.long_running, str(tmp_step.long_running))
    case("a_short_timeout_is_not_long_running",
         not Step("X-SHORT", "s", None, (), None, None, 900).long_running)
    case("final08_is_declared_long_running", STEP_BY_ID["FINAL-08_SOAK"].long_running)

    # 6d. the result/descriptor path-collision guard, over the WHOLE table plus the
    #     exact pairing that was measured on FINAL-10B before it was repaired.
    case("no_step_result_is_its_own_run_descriptor",
         all(step_result_descriptor_conflict(s) is None for s in STEPS),
         "checked %d step(s), including %s" % (len(STEPS), STEP_BY_ID[
             "FINAL-10B_EVIDENCE_COMMIT"].result))
    old_final10b = Step("FINAL-10B_EVIDENCE_COMMIT", "the repaired step, as it was",
                        "tools/finalize/commit_evidence.py", (),
                        ".workbuddy/runs/FINAL-10B_EVIDENCE_COMMIT.json", _ok_judge, 1800)
    case("the_old_final10b_pairing_is_refused",
         bool(step_result_descriptor_conflict(old_final10b)),
         str(step_result_descriptor_conflict(old_final10b)))
    refused_table = False
    try:
        assert_step_results_are_not_run_descriptors([STEPS[0], old_final10b])
    except AssertionError:
        refused_table = True
    case("the_guard_refuses_a_table_with_the_old_pairing", refused_table)
    try:
        os.remove(run_descriptor_path(tmp_step))
    except OSError:
        pass

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
        "attempts", "consecutive_failures", "controller_generation",
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
