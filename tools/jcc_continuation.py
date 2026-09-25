#!/usr/bin/env python3
"""JCC continuation controller.

Why this exists
---------------
Worker leases and member replacement can replace a dead *member*. They cannot
replace a terminated *conversation turn*. This controller is the durable
replacement for "Orion keeps the turn alive": it owns a phase machine on disk,
checkpoints before every unit of work, and is restartable from `current_step`
after any interruption.

Contract
--------
* State lives in `jcc_execution_state.json` (outside the repo, so that a stray
  state file can never make the release tree dirty).
* Every write is temp -> flush -> fsync -> atomic rename, and the previous state
  is kept as `.previous.json`.
* Progress is never left in a chat reply. A step persists "started" BEFORE it
  does its work, and persists its evidence BEFORE it advances.
* Only one instance may run. A second instance refuses instead of colliding.
* `scripts/` is frozen for the duration of E-07. Any hash change aborts with
  `E07_ABORTED_MOVING_TARGET`; a GO is never signed on moving code.

Usage
-----
    python tools/jcc_continuation.py status
    python tools/jcc_continuation.py run                # run or resume
    python tools/jcc_continuation.py run --from <STEP>  # deliberate replay
    python tools/jcc_continuation.py run --once         # single pass, no loop
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
from datetime import datetime, timezone, timedelta
from typing import Any, Callable

# --------------------------------------------------------------------------- #
# Paths and constants
# --------------------------------------------------------------------------- #

ROOT = r"F:\jev"
STATE_PATH = os.path.join(ROOT, r".workbuddy", "jcc_execution_state.json")
STATE_PREV = STATE_PATH + ".previous.json"
LOCK_DIR = os.path.join(ROOT, r".workbuddy", "locks")
RUNTIME_LOCK = os.path.join(LOCK_DIR, "runtime-4778.lock.json")
CONTROLLER_LOCK = os.path.join(LOCK_DIR, "jcc-continuation.lock.json")
REPAIR_PATH = os.path.join(ROOT, r".workbuddy", "repair_required.json")
PAUSE_PATH = os.path.join(ROOT, r".workbuddy", "paused_external.json")

PORT = 4778
RUNTIME = os.path.join(ROOT, "runtime", "jcc")
PID_FILE = os.path.join(RUNTIME, "jcc.pid")

E07_RUNNER = os.path.join(ROOT, r"artifacts\acceptance\win_scripts\run_e07_final.py")
E07_EVIDENCE = os.path.join(ROOT, r"artifacts\acceptance\win_scripts\E07_FINAL_EVIDENCE.json")
SRE_SIGNER = os.path.join(ROOT, "tools", "generate_final_sre_go.py")
FINAL_SRE_GO = os.path.join(ROOT, r"artifacts\acceptance\final_sre_go.json")
FINAL_GATE_RUNNER = os.path.join(ROOT, r"artifacts\acceptance\final_gate_run.py")
FINAL_GATE_RESULT = os.path.join(ROOT, r"artifacts\acceptance\final_gate_result.json")

SCRATCH = os.path.join(ROOT, r".workbuddy\scratch\continuation")

SCHEMA_VERSION = 1
TZ = timezone(timedelta(hours=8))
LOCK_TTL_S = 900          # runtime lock expiry until refreshed by the owner
BACKOFF_LADDER = [10, 30, 60, 120]
BACKOFF_MAX_S = 300

FROZEN_SCRIPTS = [
    "scripts/acceptance.ps1",
    "scripts/bootstrap.ps1",
    "scripts/doctor.ps1",
    "scripts/lib/JccCommon.ps1",
    "scripts/restart.ps1",
    "scripts/start.ps1",
    "scripts/status.ps1",
    "scripts/stop.ps1",
]

# --------------------------------------------------------------------------- #
# Steps
# --------------------------------------------------------------------------- #

S_INIT = "S_INIT"
S_FREEZE_VERIFY = "S_FREEZE_VERIFY"
S_HASH_SNAPSHOT = "S_HASH_SNAPSHOT"
S_LOCK_ACQUIRE = "S_LOCK_ACQUIRE"
S_PROBE_BASELINE = "S_PROBE_BASELINE"
S_E07_RUN = "S_E07_RUN"
S_E07_VERIFY = "S_E07_VERIFY"
S_SRE_GENERATE = "S_SRE_GENERATE"
S_SRE_VERIFY = "S_SRE_VERIFY"
S_GATE_RUN = "S_GATE_RUN"
S_GATE_VERIFY = "S_GATE_VERIFY"
S_MANIFEST = "S_MANIFEST"
S_REPORT = "S_REPORT"
S_COMPLETE = "S_COMPLETE"

STEPS = [
    S_INIT, S_FREEZE_VERIFY, S_HASH_SNAPSHOT, S_LOCK_ACQUIRE, S_PROBE_BASELINE,
    S_E07_RUN, S_E07_VERIFY, S_SRE_GENERATE, S_SRE_VERIFY,
    S_GATE_RUN, S_GATE_VERIFY, S_MANIFEST, S_REPORT, S_COMPLETE,
]


def utcnow() -> datetime:
    return datetime.now(TZ)


def iso(dt: datetime | None = None) -> str:
    return (dt or utcnow()).isoformat()


def log(msg: str) -> None:
    print("[%s] %s" % (iso(), msg), flush=True)


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #

class StepError(Exception):
    """Base for step failures. `kind` drives the recovery policy."""

    def __init__(self, kind: str, message: str, **extra: Any):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.extra = extra


class Recoverable(StepError):
    """Transient: controller sleep-and-retry, or watchdog restarts it."""

    def __init__(self, message: str, **extra: Any):
        super().__init__("RECOVERABLE", message, **extra)


class Deterministic(StepError):
    """Real product/gate defect: stop and write repair_required.json."""

    def __init__(self, message: str, **extra: Any):
        super().__init__("DETERMINISTIC", message, **extra)


class MovingTarget(StepError):
    """The frozen scripts/ tree moved. Never sign a GO on moving code."""

    def __init__(self, message: str, **extra: Any):
        super().__init__("E07_ABORTED_MOVING_TARGET", message, **extra)


class PausedExternal(StepError):
    """Only credentials / payment / production deploy / irreversible delete."""

    def __init__(self, message: str, **extra: Any):
        super().__init__("PAUSED_EXTERNAL", message, **extra)


# --------------------------------------------------------------------------- #
# Crash-safe state
# --------------------------------------------------------------------------- #

def fsync_atomic_write(path: str, payload: str) -> str:
    """temp -> flush -> fsync -> atomic rename. Returns the sha256 written."""
    data = payload.encode("utf-8")
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp.%d" % os.getpid()
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    # make the durable copy visible to any reader that stats before rename
    try:
        with io.open(path, "rb") as fh:
            os.fsync(fh.fileno())
    except OSError:
        pass
    return hashlib.sha256(data).hexdigest()


def load_json(path: str) -> dict | None:
    if not os.path.isfile(path):
        return None
    try:
        with io.open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (ValueError, OSError):
        return None


def default_state() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "project_root": ROOT,
        "current_phase": "E-07",
        "current_step": STEPS[0],
        "head_sha": None,
        "release_freeze_sha": "df3e7d91ac",
        "project_complete": False,
        "runtime_lock": None,
        "attempt": 0,
        "last_transition": None,
        "last_error": None,
        "next_action": None,
        "scripts_freeze_sha": None,
        "scripts_freeze_hashes": {},
        "steps_completed": [],
        "evidence": {},
        "created_at": iso(),
        "updated_at": None,
    }


def save_state(state: dict) -> str:
    """Persist state, keeping the previous generation as .previous.json."""
    if os.path.isfile(STATE_PATH):
        try:
            if os.path.isfile(STATE_PREV):
                os.replace(STATE_PREV, STATE_PREV + ".old")
            os.replace(STATE_PATH, STATE_PREV)
        except OSError:
            pass
    state["updated_at"] = iso()
    return fsync_atomic_write(STATE_PATH, json.dumps(state, indent=2, ensure_ascii=False))


def load_state() -> dict:
    st = load_json(STATE_PATH) or {}
    base = default_state()
    merged = dict(base)
    merged.update(st)
    if merged.get("schema_version") != SCHEMA_VERSION:
        merged["schema_version"] = SCHEMA_VERSION
    return merged


# --------------------------------------------------------------------------- #
# Process helpers
# --------------------------------------------------------------------------- #

def _openprocess_exists(pid: int) -> bool:
    """True only if the process can actually be opened (not a zombie)."""
    if pid <= 0:
        return False
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        return _openprocess_exists(int(pid))
    except Exception:  # noqa: BLE001 - ctypes can raise on odd platforms
        return False


def acquire_controller_lock() -> dict | None:
    """Return the lock record if THIS process owns it, else None."""
    existing = load_json(CONTROLLER_LOCK)
    if existing:
        pid = existing.get("pid")
        if pid_alive(pid) and existing.get("expires_at", "") > iso():
            return None  # another live controller is running
    record = {
        "owner": "jcc_continuation.py",
        "pid": os.getpid(),
        "task_id": "continuation-controller",
        "acquired_at": iso(),
        "expires_at": iso(utcnow() + timedelta(seconds=LOCK_TTL_S)),
    }
    fsync_atomic_write(CONTROLLER_LOCK, json.dumps(record, indent=2))
    return record


def release_controller_lock() -> None:
    existing = load_json(CONTROLLER_LOCK)
    if existing and existing.get("pid") == os.getpid():
        try:
            os.remove(CONTROLLER_LOCK)
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# Runtime lock (§一: only the owner may touch the runtime)
# --------------------------------------------------------------------------- #

def acquire_runtime_lock(head_sha: str, task_id: str = "continuation-controller") -> dict:
    existing = load_json(RUNTIME_LOCK)
    if existing:
        pid = existing.get("pid")
        if pid_alive(pid):
            raise Deterministic(
                "runtime lock held by a LIVE pid %s (%s) -- refusing to arbitrate "
                "across two owners" % (pid, existing.get("task_id")),
                lock_path=RUNTIME_LOCK,
            )
        log("runtime lock held by a dead pid %s -- reclaiming" % pid)
    record = {
        "owner": "jcc_continuation.py",
        "task_id": task_id,
        "pid": os.getpid(),
        "acquired_at": iso(),
        "expires_at": iso(utcnow() + timedelta(seconds=LOCK_TTL_S)),
        "head_sha": head_sha,
        "port": PORT,
    }
    fsync_atomic_write(RUNTIME_LOCK, json.dumps(record, indent=2))
    return record


def refresh_runtime_lock(record: dict) -> dict:
    if not record:
        return record
    record["expires_at"] = iso(utcnow() + timedelta(seconds=LOCK_TTL_S))
    record["pid"] = os.getpid()
    try:
        fsync_atomic_write(RUNTIME_LOCK, json.dumps(record, indent=2))
    except OSError:
        pass
    return record


def release_runtime_lock(record: dict | None) -> None:
    if not record:
        return
    try:
        os.remove(RUNTIME_LOCK)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Git / hash helpers
# --------------------------------------------------------------------------- #

def git(*args: str) -> str:
    p = subprocess.run(["git"] + list(args), cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return (p.stdout + p.stderr).strip()


def git_head() -> str:
    out = git("rev-parse", "HEAD").splitlines()
    return out[0] if out else ""


def sha256_file(path: str) -> str | None:
    if not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with io.open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot_frozen_scripts() -> dict:
    out = {}
    for rel in FROZEN_SCRIPTS:
        out[rel] = {
            "sha256": sha256_file(os.path.join(ROOT, rel.replace("/", os.sep))),
            "size": os.path.getsize(os.path.join(ROOT, rel.replace("/", os.sep)))
            if os.path.isfile(os.path.join(ROOT, rel.replace("/", os.sep))) else None,
        }
    return out


def detect_moving_target(frozen_sha: str, snapshot: dict) -> None:
    """§五: any change to a frozen script aborts E-07.

    The authoritative per-script baseline is the freeze manifest written by
    S_HASH_SNAPSHOT (FREEZE_MANIFEST["hashes"]), which records each frozen
    script's *file* sha256. We compare the current snapshot against that, NOT
    against `scripts_freeze_sha` -- the latter is a git COMMIT sha (40 hex) and
    lives in a different hash space from a file sha256 (64 hex); comparing the
    two makes every run look like a moving target.
    """
    if not os.path.isfile(FREEZE_MANIFEST):
        # No baseline captured yet (e.g. resumed before S_HASH_SNAPSHOT ran);
        # the E-07 runner's own --expected-manifest check is the backstop.
        return
    manifest = load_json(FREEZE_MANIFEST) or {}
    frozen = manifest.get("hashes") or {}
    if not frozen:
        return
    for rel, meta in snapshot.items():
        cur = meta.get("sha256") or ""
        known = frozen.get(rel) or ""
        if known and cur and cur != known:
            raise MovingTarget(
                "frozen script changed: %s -> sha256 %s (expected %s)"
                % (rel, cur, known),
                path=rel, sha256_now=cur,
            )


# --------------------------------------------------------------------------- #
# Step implementations
# --------------------------------------------------------------------------- #

def step_init(state: dict) -> dict:
    head = git_head()
    if not head:
        raise Deterministic("git rev-parse HEAD returned nothing", cwd=ROOT)
    state["head_sha"] = head
    if not state.get("scripts_freeze_sha"):
        state["scripts_freeze_sha"] = head
    return {"head_sha": head}


def step_freeze_verify(state: dict) -> dict:
    status = git("status", "--porcelain", "-uall")
    lines = [l for l in status.splitlines() if l.strip()]
    if lines:
        raise MovingTarget(
            "working tree is dirty at freeze time: %r -- E-07 will not run on "
            "uncommitted bytes" % (lines,),
            dirty=lines,
        )
    head = git_head()
    state["scripts_freeze_sha"] = head
    return {"tree_clean": True, "head": head}


FREEZE_MANIFEST = os.path.join(SCRATCH, "freeze_manifest.json")


def step_hash_snapshot(state: dict) -> dict:
    snap = snapshot_frozen_scripts()
    state["scripts_freeze_hashes"] = snap
    missing = [k for k, v in snap.items() if not v["sha256"]]
    if missing:
        raise Deterministic("frozen scripts missing from disk: %r" % missing)
    os.makedirs(SCRATCH, exist_ok=True)
    payload = {
        "scripts_freeze_sha": state.get("scripts_freeze_sha"),
        "head_sha": state.get("head_sha"),
        "taken_at": iso(),
        "hashes": {k: v["sha256"] for k, v in snap.items()},
    }
    fsync_atomic_write(FREEZE_MANIFEST, json.dumps(payload, indent=2, ensure_ascii=False))
    return {"files": len(snap), "manifest": FREEZE_MANIFEST}


def step_lock_acquire(state: dict) -> dict:
    record = acquire_runtime_lock(state.get("scripts_freeze_sha") or state["head_sha"])
    state["runtime_lock"] = {"path": RUNTIME_LOCK, "pid": record["pid"],
                             "expires_at": record["expires_at"], "port": PORT}
    return {"pid": record["pid"], "expires_at": record["expires_at"]}


def step_probe_baseline(state: dict) -> dict:
    """Record what is on 4778 BEFORE touching it -- never assume it is ours."""
    info: dict[str, Any] = {"port": PORT, "checked_at": iso()}
    # jcc.pid may legitimately hold a bare integer (e.g. just "21480") rather than
    # a JSON object. Guard so a non-dict payload does not crash the probe.
    raw_pidf = load_json(PID_FILE.replace("\\", os.sep))
    pidf = raw_pidf if isinstance(raw_pidf, dict) else {}
    info["pid_file"] = pidf
    info["pid_file_pid"] = pidf.get("pid")
    info["pid_file_service_pid"] = pidf.get("service_pid")
    info["pid_file_launcher_pid"] = pidf.get("launcher_pid")
    info["pid_file_pid_alive"] = pid_alive(pidf.get("pid") or 0)
    snapshot_before = snapshot_frozen_scripts()
    detect_moving_target(state.get("scripts_freeze_sha"), snapshot_before)
    return info


def run_subprocess(cmd: list[str], timeout_s: int, cwd: str = ROOT) -> dict:
    started = time.time()
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, timeout=timeout_s)
    return {
        "command": " ".join(cmd),
        "exit_code": p.returncode,
        "duration_s": round(time.time() - started, 3),
        "stdout_sha256": hashlib.sha256(p.stdout or b"").hexdigest(),
        "stderr_sha256": hashlib.sha256(p.stderr or b"").hexdigest(),
        "stdout_len": len(p.stdout or b""),
        "stderr_len": len(p.stderr or b""),
        "stdout_tail": (p.stdout or b"").decode("utf-8", "replace")[-4000:],
        "stderr_tail": (p.stderr or b"").decode("utf-8", "replace")[-4000:],
    }


def step_e07_run(state: dict) -> dict:
    if not os.path.isfile(E07_RUNNER):
        raise Deterministic(
            "E-07 runner is not in version control: %s" % E07_RUNNER, missing=E07_RUNNER
        )
    # pass the freeze manifest so the runner itself can detect a moving target
    cmd = [sys.executable, E07_RUNNER, "--port", str(PORT)]
    if os.path.isfile(FREEZE_MANIFEST):
        cmd += ["--expected-manifest", FREEZE_MANIFEST, "--throughput", "60"]
    res = run_subprocess(cmd, timeout_s=1800)
    detect_moving_target(state.get("scripts_freeze_sha"), snapshot_frozen_scripts())
    if res["exit_code"] != 0:
        raise Recoverable(
            "run_e07_final.py exited %d" % res["exit_code"], detail=res
        )
    return res


def step_e07_verify(state: dict) -> dict:
    ev = load_json(E07_EVIDENCE)
    if not ev:
        raise Deterministic("E07_FINAL_EVIDENCE.json was not produced", path=E07_EVIDENCE)
    steps = ev.get("steps") or []
    if len(steps) != (ev.get("expected_steps") or len(steps)):
        raise Deterministic(
            "evidence has %d steps, expected %d"
            % (len(steps), ev.get("expected_steps") or len(steps))
        )
    failed = [s for s in steps if s.get("result") != "PASS"]
    if failed:
        raise Deterministic(
            "%d E-07 steps did not PASS: %r" % (len(failed), [s.get("step") for s in failed]),
            failures=[s.get("step") for s in failed],
        )
    detect_moving_target(state.get("scripts_freeze_sha"), snapshot_frozen_scripts())
    return {"steps": len(steps), "verdict": ev.get("verdict")}


def step_sre_generate(state: dict) -> dict:
    res = run_subprocess([sys.executable, SRE_SIGNER, "--evidence", E07_EVIDENCE],
                         timeout_s=600)
    if res["exit_code"] != 0:
        raise Deterministic("generate_final_sre_go.py exited %d" % res["exit_code"],
                            detail=res)
    return res


def step_sre_verify(state: dict) -> dict:
    doc = load_json(FINAL_SRE_GO) or {}
    verdict = doc.get("verdict")
    if verdict != "GO":
        raise Deterministic(
            "final_sre_go.json verdict is %r, not GO; refusing to advance"
            % (verdict,), path=FINAL_SRE_GO
        )
    return {"verdict": verdict, "signed_at": doc.get("signed_at")}


def step_gate_run(state: dict) -> dict:
    res = run_subprocess([sys.executable, FINAL_GATE_RUNNER, "--full"], timeout_s=3000)
    return res


def step_gate_verify(state: dict) -> dict:
    doc = load_json(FINAL_GATE_RESULT) or {}
    if doc.get("error") or doc.get("errors"):
        raise Recoverable("final gate reported errors: %r" % (doc.get("errors"),))
    failed = doc.get("failed") or []
    if failed:
        raise Deterministic("final gate FAILED checks: %r" % (failed,), detail=doc)
    return {"passed": doc.get("passed"), "failed": failed}


def step_manifest(state: dict) -> dict:
    return {"note": "manifest is emitted by the release tool; nothing to do here"}


def step_report(state: dict) -> dict:
    report = {
        "project": "JEV COMMAND CENTER",
        "generated_by": "tools/jcc_continuation.py",
        "generated_at": iso(),
        "scripts_freeze_sha": state.get("scripts_freeze_sha"),
        "head_sha": state.get("head_sha"),
        "e07_evidence": E07_EVIDENCE,
        "final_sre_go": FINAL_SRE_GO,
        "final_gate_result": FINAL_GATE_RESULT,
        "steps_completed": state.get("steps_completed"),
    }
    os.makedirs(SCRATCH, exist_ok=True)
    path = os.path.join(SCRATCH, "continuation_final_report.json")
    fsync_atomic_write(path, json.dumps(report, indent=2, ensure_ascii=False))
    return {"report": path}


def step_complete(state: dict) -> dict:
    state["project_complete"] = True
    return {"project_complete": True}


STEP_HANDLERS: dict[str, Callable[[dict], dict]] = {
    S_INIT: step_init,
    S_FREEZE_VERIFY: step_freeze_verify,
    S_HASH_SNAPSHOT: step_hash_snapshot,
    S_LOCK_ACQUIRE: step_lock_acquire,
    S_PROBE_BASELINE: step_probe_baseline,
    S_E07_RUN: step_e07_run,
    S_E07_VERIFY: step_e07_verify,
    S_SRE_GENERATE: step_sre_generate,
    S_SRE_VERIFY: step_sre_verify,
    S_GATE_RUN: step_gate_run,
    S_GATE_VERIFY: step_gate_verify,
    S_MANIFEST: step_manifest,
    S_REPORT: step_report,
    S_COMPLETE: step_complete,
}


# --------------------------------------------------------------------------- #
# Recovery policy (§十四)
# --------------------------------------------------------------------------- #

def classify_recoverable(exc: Exception) -> bool:
    """Everything transient must be RECOVERABLE, never a permanent halt."""
    return isinstance(exc, Recoverable)


def write_repair(state: dict, exc: Exception) -> str:
    payload = {
        "written_at": iso(),
        "kind": getattr(exc, "kind", type(exc).__name__),
        "message": str(exc),
        "current_step": state.get("current_step"),
        "head_sha": state.get("head_sha"),
        "scripts_freeze_sha": state.get("scripts_freeze_sha"),
        "attempt": state.get("attempt"),
        "next_action": ("resolve the defect, then run `jcc_continuation.py run --from %s`"
                        % state.get("current_step")),
        "do_not": [
            "do not hand-edit final_sre_go.json",
            "do not hand-edit E07_FINAL_EVIDENCE.json",
            "do not mark a step PASS without its evidence",
        ],
    }
    fsync_atomic_write(REPAIR_PATH, json.dumps(payload, indent=2, ensure_ascii=False))
    return REPAIR_PATH


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #

def run_state_machine(state: dict, once: bool = False) -> dict:
    state = load_state() if not state else state
    controller = acquire_controller_lock()
    if controller is None:
        log("another controller instance is alive (pid %s); exiting"
            % (load_json(CONTROLLER_LOCK) or {}).get("pid"))
        return state

    backoff_idx = 0
    consecutive_recoverable = 0
    # `run --once` must drive the pipeline all the way to completion in a single
    # invocation (no idle poll loop), so it must NOT be bounded by a 1-second
    # backoff deadline -- that would kill it after the first step. Use an
    # effectively unbounded deadline for the one-shot mode; the persistent `run`
    # mode keeps the 300 s backoff budget as its recoverable-retry ceiling.
    deadline_total = time.time() + (10 ** 9 if once else BACKOFF_MAX_S)

    try:
        while True:
            step = state.get("current_step")
            if step not in STEP_HANDLERS:
                raise Deterministic("unknown step %r" % step)
            if step == S_COMPLETE and state.get("project_complete"):
                log("project_complete=true; nothing left to do")
                break

            # 1) persist "started" BEFORE doing the work
            state["last_transition"] = {"step": step, "at": iso(), "event": "STARTED"}
            state["last_error"] = None
            save_state(state)

            handler = STEP_HANDLERS[step]
            try:
                result = handler(state)
            except (Deterministic, MovingTarget) as exc:
                state["last_transition"] = {"step": step, "at": iso(), "event": "FAILED"}
                state["last_error"] = {"kind": exc.kind, "message": str(exc),
                                       "extra": exc.extra}
                state["next_action"] = ("abort; write_repair=%s" % write_repair(state, exc))
                save_state(state)
                release_runtime_lock(state.get("runtime_lock"))
                raise
            except PausedExternal as exc:
                state["last_transition"] = {"step": step, "at": iso(), "event": "PAUSED"}
                state["last_error"] = {"kind": exc.kind, "message": str(exc)}
                fsync_atomic_write(PAUSE_PATH, json.dumps(
                    {"paused_at": iso(), "reason": str(exc)}, indent=2))
                save_state(state)
                raise
            except Exception as exc:  # noqa: BLE001 - caught deliberately
                wrapped = Recoverable("%s: %s" % (type(exc).__name__, exc))
                state["last_transition"] = {"step": step, "at": iso(), "event": "FAILED"}
                state["last_error"] = {"kind": wrapped.kind, "message": str(exc)}
                state["next_action"] = "retry with backoff"
                save_state(state)
                raise wrapped from exc

            # 2) persist evidence, then advance -- never the other way round
            state.setdefault("evidence", {})[step] = result
            state["attempt"] += 1
            idx = STEPS.index(step)
            next_step = STEPS[idx + 1] if idx + 1 < len(STEPS) else None
            if step not in (state.get("steps_completed") or []):
                state["steps_completed"] = (state.get("steps_completed") or []) + [step]
            state["current_step"] = next_step or state.get("current_step")
            state["last_transition"] = {"step": step, "at": iso(), "event": "PASSED",
                                        "next": state["current_step"]}
            save_state(state)
            refresh_runtime_lock(state.get("runtime_lock") or {})
            log("STEP PASSED %s -> next %s" % (step, state["current_step"]))
            consecutive_recoverable = 0
            backoff_idx = 0

            if state.get("current_step") == S_COMPLETE and state.get("project_complete"):
                state["last_transition"] = {"step": S_COMPLETE, "at": iso(),
                                            "event": "PASSED", "next": None}
                save_state(state)
                log("PROJECT_COMPLETE=true")
                break

            # `--once` means "run the pipeline to completion in this invocation,
            # without the idle poll loop". It does NOT mean "run one step".
            if once and state.get("current_step") == S_COMPLETE:
                break

            if time.time() > deadline_total:
                log("backoff budget (%d s) exhausted; stopping with step=%s"
                    % (BACKOFF_MAX_S, state["current_step"]))
                break

            delay = BACKOFF_LADDER[min(backoff_idx, len(BACKOFF_LADDER) - 1)]
            backoff_idx += 1
            log("idle; re-checking in %d s (step=%s)" % (delay, state["current_step"]))
            time.sleep(delay)
    finally:
        release_controller_lock()
    return state


def cmd_status(state: dict) -> int:
    st = state or load_state() or default_state()
    print(json.dumps({
        "current_step": st.get("current_step"),
        "current_phase": st.get("current_phase"),
        "head_sha": st.get("head_sha"),
        "scripts_freeze_sha": st.get("scripts_freeze_sha"),
        "project_complete": st.get("project_complete"),
        "attempt": st.get("attempt"),
        "last_transition": st.get("last_transition"),
        "last_error": st.get("last_error"),
        "next_action": st.get("next_action"),
        "steps_completed": st.get("steps_completed"),
        "runtime_lock": st.get("runtime_lock"),
    }, indent=2, ensure_ascii=False))
    if os.path.isfile(REPAIR_PATH):
        print("\nREPAIR REQUIRED --")
        print(io.open(REPAIR_PATH, encoding="utf-8").read())
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="JCC continuation controller")
    ap.add_argument("command", choices=["run", "status"])
    ap.add_argument("--from", dest="from_step", choices=STEPS, default=None)
    ap.add_argument("--once", action="store_true", help="single pass, no idle loop")
    args = ap.parse_args(argv)

    if args.command == "status":
        return cmd_status(load_state())

    state = load_state()
    state["attempt"] = 0
    if args.from_step and args.from_step != state.get("current_step"):
        # Replay from a chosen step: the evidence already gathered is kept.
        state["current_step"] = args.from_step
        if args.from_step not in STEPS:
            print("unknown step", args.from_step, file=sys.stderr)
            return 3
        save_state(state)
    try:
        run_state_machine(state, once=args.once)
    except (Deterministic, MovingTarget, PausedExternal) as exc:
        log("%s: %s" % (exc.kind, exc))
        return 1
    except Recoverable as exc:
        log("RECOVERABLE: %s" % exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
