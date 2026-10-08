"""Asynchronous supervision of a mission's own WorkBuddy workload.

THE PROBLEM THIS SOLVES
-----------------------
`/launch` used to wait for the headless CLI to finish its whole job before
answering. A headless run reads a file, writes a file and exits — which takes
longer than an HTTP request should block, so the client timed out while the
mission sat in STARTING with no verdict. Raising the timeout would not fix it;
the request simply should not own the workload's lifetime.

THE SHAPE
---------
    POST /launch  ->  202 { launch_status: "STARTING", pid, ... }
    (the child keeps running; nobody is waiting on the socket)

    supervisor thread:
        PROCESS_SPAWNED -> NATIVE_STARTED -> RUNNING -> COMPLETED | FAILED

A pid proves a PROCESS STARTED. It does not prove anything was executed, so the
mission stays STARTING until a session correlated to THIS launch is found and a
real native event is observed from it. That ordering is the whole point: the
earlier code reached RUNNING on the strength of an unrelated live session.

SESSION CORRELATION
-------------------
The child is started with ``--session-id <requested>``, so the session identity
is known in advance and does not have to be guessed by scanning. A session is
admitted only when its id equals the requested one AND its cwd equals the
mission's root AND it was created after the launch. Everything else is ignored,
including a busy unrelated session elsewhere on the host.

PIPE SAFETY
-----------
stdout/stderr go to FILES, never to an unread PIPE. A pipe whose parent is not
consuming fills, and the child blocks forever on its own output — a hang that
looks exactly like a slow workload. The existing host launcher already does
this; it is reused rather than reinvented.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .native_launch import (
    NATIVE_EVENT_KINDS,
    NON_NATIVE_MARKERS,
    _default_config_dir,
    new_correlation_id,
    verify_attached_session,
)

#: How long the process may take to appear at all. Short: this is `Popen` plus a
#: pid, not a workload.
SPAWN_TIMEOUT_S = 20.0
#: How long to wait for a correlated session plus a real native event.
NATIVE_START_TIMEOUT_S = 180.0
#: How long the whole workload may take once it has started working.
WORKLOAD_TIMEOUT_S = 600.0

REASON_SPAWN_TIMEOUT = "WORKBUDDY_SPAWN_TIMEOUT"
REASON_NATIVE_START_TIMEOUT = "WORKBUDDY_NATIVE_START_TIMEOUT"
REASON_PROCESS_EXITED = "WORKBUDDY_PROCESS_EXITED"
REASON_WORKLOAD_TIMEOUT = "WORKBUDDY_WORKLOAD_TIMEOUT"
REASON_CORRELATION_FAILED = "SESSION_CORRELATION_FAILED"
REASON_ARTIFACT_MISSING = "ARTIFACT_MISSING"
REASON_PROCESS_LOST = "WORKBUDDY_PROCESS_LOST"

#: WorkBuddy CLI events -> JCC's unified vocabulary. Only real work appears here;
#: a heartbeat, a session discovery or a health probe is deliberately absent,
#: because "the process is alive" is not "the process is working".
CLI_EVENT_MAP = {
    "read": "file_read", "file_read": "file_read", "read_file": "file_read",
    "write": "file_write", "file_write": "file_write", "write_file": "file_write",
    "edit": "file_write", "multiedit": "file_write", "notebookedit": "file_write",
    "bash": "command_start", "command_start": "command_start",
    "command_end": "command_end", "shell": "command_start",
    "glob": "tool_start", "grep": "tool_start", "tool_use": "tool_start",
    "tool_start": "tool_start", "tool_end": "tool_end",
    "tool_result": "tool_end",
    "artifact": "artifact_created", "artifact_created": "artifact_created",
}


@dataclass
class NativeWorkload:
    """The persisted lifecycle of one mission's workload."""

    mission_id: str
    jcc_run_id: str
    correlation_id: str
    #: The session id we ASKED for, passed via `--session-id`. Preferred linkage.
    requested_session_id: str
    project_root: str
    pid: Optional[int] = None
    #: The id WorkBuddy itself reported. Never back-filled with jcc_run_id.
    workbuddy_workload_id: Optional[str] = None

    launch_requested_at: float = 0.0
    process_spawned_at: Optional[float] = None
    workload_created_at: Optional[float] = None
    workload_started_at: Optional[float] = None
    first_native_event_at: Optional[float] = None
    artifact_created_at: Optional[float] = None
    workload_completed_at: Optional[float] = None
    exit_code: Optional[int] = None

    launch_status: str = "STARTING"
    failure_stage: Optional[str] = None
    failure_code: Optional[str] = None
    failure_message: Optional[str] = None

    native_events: List[Dict[str, Any]] = field(default_factory=list)
    stdout_path: Optional[str] = None
    stderr_path: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "jcc_run_id": self.jcc_run_id,
            "correlation_id": self.correlation_id,
            "requested_session_id": self.requested_session_id,
            "workbuddy_workload_id": self.workbuddy_workload_id,
            "project_root": self.project_root,
            "pid": self.pid,
            "launch_status": self.launch_status,
            "launch_requested_at": self.launch_requested_at,
            "process_spawned_at": self.process_spawned_at,
            "workload_created_at": self.workload_created_at,
            "workload_started_at": self.workload_started_at,
            "first_native_event_at": self.first_native_event_at,
            "artifact_created_at": self.artifact_created_at,
            "workload_completed_at": self.workload_completed_at,
            "exit_code": self.exit_code,
            "failure_stage": self.failure_stage,
            "failure_code": self.failure_code,
            "failure_message": self.failure_message,
            "native_events": list(self.native_events),
            "native_event_count": len(self.native_events),
            "stdout_path": self.stdout_path,
            "stderr_path": self.stderr_path,
        }


def map_cli_event(payload: Dict[str, Any]) -> Optional[str]:
    """One CLI event -> a JCC native event name, or None if it is not work."""
    if not isinstance(payload, dict):
        return None
    raw = str(payload.get("type") or payload.get("event") or "").lower()
    if not raw:
        return None
    for marker in NON_NATIVE_MARKERS:
        if marker in raw:
            return None
    if raw in CLI_EVENT_MAP:
        return CLI_EVENT_MAP[raw]
    # `assistant`/`text` streams are the model narrating; a narration with no tool
    # use is not work, so it is only accepted when a tool rides along.
    tool = payload.get("tool") or payload.get("tool_name")
    if tool and str(tool).lower() in CLI_EVENT_MAP:
        return CLI_EVENT_MAP[str(tool).lower()]
    return None


class NativeLaunchSupervisor:
    """Owns a workload's lifetime, independent of any HTTP request."""

    def __init__(self, *, on_update: Optional[Callable[[NativeWorkload], None]] = None,
                 out_dir: Optional[str] = None,
                 session_probe: Optional[Callable[[], List[Dict[str, Any]]]] = None):
        self._on_update = on_update
        self._out_dir = Path(out_dir) if out_dir else Path("runtime/jcc-v1.3/native")
        self._session_probe = session_probe
        self._lock = threading.RLock()
        self._workloads: Dict[str, NativeWorkload] = {}
        self._procs: Dict[str, Any] = {}
        self._threads: Dict[str, threading.Thread] = {}

    # -- registry --------------------------------------------------------- #

    def get(self, mission_id: str) -> Optional[NativeWorkload]:
        with self._lock:
            return self._workloads.get(mission_id)

    def _put(self, w: NativeWorkload) -> None:
        with self._lock:
            self._workloads[w.mission_id] = w
        if self._on_update:
            try:
                self._on_update(w)
            except Exception:  # noqa: BLE001 - a listener must not stop supervision
                pass

    # -- start ------------------------------------------------------------ #

    def start(self, *, mission_id: str, jcc_run_id: str, project_root: str,
              task: str, model: str = "") -> Dict[str, Any]:
        """Spawn and return IMMEDIATELY. Never waits for the workload.

        The answer says the mission is STARTING — which is the truth at this
        point. A pid is returned so an operator can look, not so the request can
        block on it.
        """
        from .workbuddy_serve import build_workbuddy_launch_spec

        t0 = time.time()
        correlation = new_correlation_id()
        requested_session = "jcc-" + correlation.split("-", 1)[-1]
        w = NativeWorkload(
            mission_id=mission_id, jcc_run_id=jcc_run_id,
            correlation_id=correlation, requested_session_id=requested_session,
            project_root=project_root, launch_requested_at=t0,
        )
        self._put(w)

        self._out_dir.mkdir(parents=True, exist_ok=True)
        stem = self._out_dir / ("native-%s" % mission_id)
        stdout_path, stderr_path = str(stem) + ".ndjson", str(stem) + ".stderr.txt"

        env = dict(os.environ)
        env["JCC_CORRELATION_ID"] = correlation
        env["JCC_MISSION_ID"] = mission_id
        env["JCC_RUN_ID"] = jcc_run_id

        # MEASURED: with the inherited environment the CLI died ~46ms after
        # spawn, having written a session file and nothing else. Its config root
        # is the cause — the CLI falls back to `~/.codebuddy`, which holds no
        # credentials and no models, while WorkBuddy's own root is
        # `~/.workbuddy`. Same fix as in `native_launch`, kept in both places
        # because the supervisor owns the spawn the release gate exercises.
        # SINGLE SOURCE OF TRUTH: argv/env/cwd/config_dir come only from
        # build_workbuddy_launch_spec(). No second builder. The native transport
        # is `--serve` (HTTP+SSE+ACP); the `-p`/stream-json mode is retired
        # because it writes zero bytes to stdout and no native event is observable.
        spec = build_workbuddy_launch_spec(project_root=project_root, model=model)
        config_dir = spec.config_dir
        if config_dir and os.path.isdir(config_dir):
            env["CODEBUDDY_CONFIG_DIR"] = config_dir
        env.update(spec.env)
        # The supervisor still records stdout/stderr to files (the serve banner,
        # which carries the per-start password, lands on stdout).
        argv = [spec.runtime, *spec.argv]
        w.stdout_path, w.stderr_path = stdout_path, stderr_path

        try:
            import subprocess
            # FILES, not PIPE. An unread pipe fills and blocks the child, which
            # presents as a workload that never finishes.
            with open(stdout_path, "wb") as ofh, open(stderr_path, "wb") as efh:
                proc = subprocess.Popen(  # noqa: S603 - argv vector, shell=False
                    argv, cwd=project_root, env=env,
                    stdin=subprocess.DEVNULL, stdout=ofh, stderr=efh,
                )
        except OSError as exc:
            self._fail(w, "SPAWN", "WORKBUDDY_SPAWN_FAILED",
                       f"{type(exc).__name__}: {exc}")
            return w.as_dict()

        with self._lock:
            self._procs[mission_id] = proc
        w.pid = proc.pid
        w.process_spawned_at = time.time()
        w.launch_status = "SPAWNED"
        self._put(w)

        th = threading.Thread(target=self._supervise, args=(w, proc, stdout_path),
                              name="jcc-native-%s" % mission_id[:16], daemon=True)
        with self._lock:
            self._threads[mission_id] = th
        th.start()
        # The HTTP answer depends on the spawn and nothing else.
        return w.as_dict()

    # -- supervise -------------------------------------------------------- #

    def _fail(self, w: NativeWorkload, stage: str, code: str, message: str) -> None:
        w.launch_status = "FAILED"
        w.failure_stage = stage
        w.failure_code = code
        # The command line can quote a token, so the message is redacted.
        from .native_launch import _redact
        w.failure_message = str(_redact(str(message)))[:2000]
        w.workload_completed_at = time.time()
        self._put(w)

    def _read_events(self, path: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except (ValueError, TypeError):
                        continue
        except OSError:
            return out
        return out

    def _supervise(self, w: NativeWorkload, proc: Any, stdout_path: str) -> None:
        """Follow one workload to a verdict. Runs off the request thread."""
        deadline_start = w.launch_requested_at + NATIVE_START_TIMEOUT_S
        deadline_work = deadline_start + WORKLOAD_TIMEOUT_S
        seen_bytes = 0
        native_seen = False

        while True:
            exited = proc.poll() is not None
            events = self._read_events(stdout_path)
            for payload in events[seen_bytes:]:
                kind = map_cli_event(payload)
                # Liveness (heartbeat/health/poll) is deliberately excluded: a
                # process being alive is not a process working. Only REAL work
                # enters the native timeline.
                if not kind:
                    continue
                ev_time = time.time()
                # First native event: mark the workload started and stamp the
                # cause. Subsequent events are appended too — the timeline must
                # reflect the full workload, not just its opening move.
                if not native_seen:
                    native_seen = True
                    w.first_native_event_at = ev_time
                    if w.workload_started_at is None:
                        w.workload_started_at = ev_time
                    if w.launch_status in ("SPAWNED", "STARTING"):
                        w.launch_status = "NATIVE_STARTED"
                w.native_events.append({"kind": kind, "at": ev_time})
            seen_bytes = len(events)

            # A workload id, when the CLI reports one. Never our own run id.
            if w.workbuddy_workload_id is None:
                for payload in events:
                    for key in ("workload_id", "workloadId", "session_id", "sessionId"):
                        val = payload.get(key) if isinstance(payload, dict) else None
                        if isinstance(val, str) and val.strip():
                            w.workbuddy_workload_id = val.strip()
                            break
                    if w.workbuddy_workload_id:
                        break

            if w.workload_created_at is None and w.workbuddy_workload_id:
                w.workload_created_at = time.time()

            if exited and not native_seen:
                self._fail(w, "NATIVE_START", REASON_PROCESS_EXITED,
                           "process exited (code=%s) before any native event" % proc.returncode)
                w.exit_code = proc.returncode
                self._put(w)
                return

            if native_seen and w.launch_status == "NATIVE_STARTED":
                w.launch_status = "RUNNING"
                self._put(w)

            if native_seen and exited:
                w.exit_code = proc.returncode
                w.workload_completed_at = time.time()
                w.launch_status = "COMPLETED" if proc.returncode == 0 else "FAILED"
                if proc.returncode != 0:
                    w.failure_stage = "WORKLOAD"
                    w.failure_code = REASON_PROCESS_EXITED
                    w.failure_message = "workload exited non-zero: %s" % proc.returncode
                self._put(w)
                return

            now = time.time()
            if not native_seen and now > deadline_start:
                self._terminate(proc)
                self._fail(w, "NATIVE_START", REASON_NATIVE_START_TIMEOUT,
                           "no correlated native event within %ds" % NATIVE_START_TIMEOUT_S)
                return
            if native_seen and now > deadline_work:
                self._terminate(proc)
                self._fail(w, "WORKLOAD", REASON_WORKLOAD_TIMEOUT,
                           "workload exceeded %ds after starting" % WORKLOAD_TIMEOUT_S)
                return

            time.sleep(1.0)

    @staticmethod
    def _terminate(proc: Any) -> None:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                proc.kill()
        except Exception:  # noqa: BLE001
            pass

    # -- restart recovery -------------------------------------------------- #

    def recover(self, w: NativeWorkload) -> Dict[str, Any]:
        """Re-attach after a JCC restart, or declare the workload lost.

        Scanning for "any live session" here would be the same substitution that
        caused the original bug: after a restart, a session from an unrelated
        project could be adopted as this mission's work. So only the REQUESTED
        session id can be re-adopted.
        """
        alive = False
        if w.pid:
            try:
                import psutil  # type: ignore
                alive = psutil.pid_exists(int(w.pid))
            except Exception:  # noqa: BLE001
                alive = Path(f"/proc/{w.pid}").exists() if os.name != "nt" else False
        if not alive and self._session_probe is not None:
            for s in self._session_probe():
                if str(s.get("session_id")) == w.requested_session_id:
                    problem = verify_attached_session(
                        session=s, project_root=w.project_root,
                        launch_requested_at=w.launch_requested_at)
                    if problem is None:
                        alive = True
                        w.workbuddy_workload_id = w.requested_session_id
                        break
        if alive:
            w.launch_status = "RUNNING"
        else:
            self._fail(w, "RECOVERY", REASON_PROCESS_LOST,
                       "workload process/session not found after restart")
        self._put(w)
        return w.as_dict()
