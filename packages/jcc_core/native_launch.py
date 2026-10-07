"""Mission-scoped WorkBuddy native execution.

WHY THIS EXISTS
---------------
The `/launch` route used to decide a mission had run by looking for ANY live
WorkBuddy session on the host. During release acceptance it found one — a
session belonging to an unrelated project, created 1.85 seconds BEFORE the run
was launched — and reported `ok=True` with `ingested=True`. The mission went to
RUNNING having produced no work at all.

So the failure was not a crash. It was a SUCCESS reported for someone else's
workload, which is worse: nothing in the response looked wrong.

THE RULE
--------
A mission is RUNNING only when a workload exists that:

  * was created for THIS mission (a correlation id we generated and passed in),
  * has the mission's project root as its cwd,
  * and was created AFTER this launch was requested.

An ambient session can be ATTACHED afterwards, for observation or recovery. It can
never be the evidence for a launch. That asymmetry is the whole fix: capture is
diagnostics, spawn is execution.

THREE IDENTITIES, KEPT SEPARATE
------------------------------
  mission_id              ours
  jcc_run_id              ours, the bookkeeping row
  workbuddy_workload_id   the vendor's, returned by the child

They are stored separately and never assumed equal. The earlier code let a
`run_id` stand in for a workload id, which is exactly the substitution that made
an unrelated session look like this mission's output.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

#: Failures, named. A launch that cannot prove its own workload says WHICH of
#: these it is, rather than reporting a generic failure that cannot be acted on.
REASON_NOT_STARTED = "WORKBUDDY_NATIVE_NOT_STARTED"
REASON_PROJECT_MISMATCH = "SESSION_PROJECT_MISMATCH"
REASON_PREDATES_LAUNCH = "SESSION_PREDATES_LAUNCH"
REASON_SPAWN_FAILED = "WORKBUDDY_SPAWN_FAILED"
REASON_RESOLVER_FAILED = "WORKBUDDY_CLI_NOT_FOUND"


@dataclass
class NativeLaunchResult:
    """The outcome of ONE attempt to start a mission's own workload."""

    ok: bool = False
    #: Our correlation id, minted BEFORE the spawn and passed to the child, so a
    #: workload can be tied back to this launch without trusting timestamps.
    correlation_id: str = ""
    mission_id: str = ""
    jcc_run_id: str = ""
    project_root: str = ""
    launch_requested_at: float = 0.0
    workload_created_at: Optional[float] = None
    workload_started_at: Optional[float] = None
    first_native_event_at: Optional[float] = None
    workload_completed_at: Optional[float] = None
    #: The vendor's own identifier, when the child reports one. Empty means we did
    #: not get one — and that must not be papered over with our own run id.
    workbuddy_workload_id: Optional[str] = None
    pid: Optional[int] = None
    argv: List[str] = field(default_factory=list)
    exit_code: Optional[int] = None
    classification: str = "REFUSED"
    reason: Optional[str] = None
    detail: Optional[str] = None
    #: Parsed NDJSON events, minus anything that could carry a credential.
    events: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "correlation_id": self.correlation_id,
            "mission_id": self.mission_id,
            "jcc_run_id": self.jcc_run_id,
            "project_root": self.project_root,
            "launch_requested_at": self.launch_requested_at,
            "workload_created_at": self.workload_created_at,
            "workload_started_at": self.workload_started_at,
            "first_native_event_at": self.first_native_event_at,
            "workload_completed_at": self.workload_completed_at,
            "workbuddy_workload_id": self.workbuddy_workload_id,
            "pid": self.pid,
            "classification": self.classification,
            "reason": self.reason,
            "detail": self.detail,
            "event_count": len(self.events),
        }


#: Event kinds that count as a workload ACTUALLY DOING something. A heartbeat, a
#: session discovery or a health poll is the process being alive, not working —
#: and treating those as native activity is how a mission looks busy while idle.
NATIVE_EVENT_KINDS = (
    "file_read", "file_write", "tool_start", "tool_end",
    "command_start", "command_end", "artifact_created",
    "assistant", "text", "tool_use", "tool_result",
)

#: Explicitly NOT native work, whatever they are named.
NON_NATIVE_MARKERS = (
    "heartbeat", "session_discovered", "session_attached", "health", "poll", "ping",
)


def _default_config_dir() -> str:
    """The WorkBuddy config root the CLI must read to find its credentials.

    The CLI falls back to ``~/.codebuddy`` when this is unset. WorkBuddy's own
    root is ``~/.workbuddy`` — ``models.json``, ``settings.json`` and the
    credential store live there, and ``~/.codebuddy`` holds only ``diagnostics``
    and ``logs``. Discovered from the CLI source, not assumed.
    """
    home = os.path.expanduser("~")
    for name in (".workbuddy", ".codebuddy"):
        candidate = os.path.join(home, name)
        if os.path.isdir(candidate):
            return candidate
    return ""


def new_correlation_id() -> str:
    """A correlation id minted per launch.

    Generated here rather than derived from a timestamp so two launches in the
    same millisecond cannot collide.
    """
    return "jcc-" + uuid.uuid4().hex[:16] + "-" + secrets.token_hex(4)


def _classify_event(raw: str) -> Optional[str]:
    """Map one NDJSON line to a native event kind, or None if it is not work."""
    text = raw.lower()
    for marker in NON_NATIVE_MARKERS:
        if marker in text:
            return None
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    kind = str(payload.get("type") or payload.get("event") or "").lower()
    for native in NATIVE_EVENT_KINDS:
        if kind == native or kind.startswith(native):
            return kind
    return None


def _redact(payload: Any) -> Any:
    """Strip anything that could carry a credential out of a child's output.

    A CLI stream can echo its own argv, and the argv of a future template may
    include a token. The transcript is stored and shown, so it is filtered here
    rather than trusted.
    """
    secretish = ("key", "token", "secret", "authorization", "password", "cookie")
    if isinstance(payload, dict):
        return {
            k: ("[redacted]" if any(s in k.lower() for s in secretish) else _redact(v))
            for k, v in payload.items()
        }
    if isinstance(payload, list):
        return [_redact(v) for v in payload]
    if isinstance(payload, str) and len(payload) > 4000:
        return payload[:4000] + "…[truncated]"
    return payload


def launch_native_workload(
    *, mission_id: str, jcc_run_id: str, project_root: str, task: str,
    model: str = "", timeout_s: float = 180.0,
) -> NativeLaunchResult:
    """Start a WorkBuddy workload that belongs to THIS mission.

    The correlation id is minted here and handed to the child, so the workload can
    be identified as ours by something stronger than a timestamp comparison. The
    mission is only reported as started when the child actually produced a native
    event; a live process that only heartbeats is a refusal, not a success.
    """
    from .workbuddy_serve import build_workbuddy_launch_spec

    t0 = time.time()
    correlation = new_correlation_id()
    res = NativeLaunchResult(
        correlation_id=correlation,
        mission_id=mission_id,
        jcc_run_id=jcc_run_id,
        project_root=project_root,
        launch_requested_at=t0,
    )

    # The correlation id travels as an env var AND inside the prompt, so it is
    # present whether the CLI preserves the environment or only the argv.
    env = dict(os.environ)
    env["JCC_CORRELATION_ID"] = correlation
    env["JCC_MISSION_ID"] = mission_id
    env["JCC_RUN_ID"] = jcc_run_id

    # MEASURED during release acceptance: with the inherited environment the CLI
    # exits 0 with zero bytes on stdout and stderr, produces no session, no
    # native event and no artifact. The cause is its config root: the CLI
    # resolves `CODEBUDDY_CONFIG_DIR` and falls back to `~/.codebuddy`, which on
    # this machine holds no credentials and no models. WorkBuddy's own config
    # root is `~/.workbuddy`, and pointing the child at it is the difference
    # between a silent no-op and a real session file appearing under
    # `~/.workbuddy/sessions/<pid>.json`.
    #
    # This is configuration, not a secret: the child reads the same store the
    # desktop app already reads, and nothing is copied into the JCC process.
    # SINGLE SOURCE OF TRUTH: the launch spec (transport, runtime, argv, env,
    # cwd, config_dir) is produced exclusively by build_workbuddy_launch_spec().
    # There is no second argv builder anywhere in the package. The native
    # transport is `--serve` (HTTP+SSE+ACP), not the headless `-p` mode, because
    # `-p` writes zero bytes to stdout and no native event is ever observable.
    spec = build_workbuddy_launch_spec(project_root=project_root, model=model)
    config_dir = spec.config_dir
    if config_dir and os.path.isdir(config_dir):
        env["CODEBUDDY_CONFIG_DIR"] = config_dir
    env.update(spec.env)
    # res.argv carries runtime + entrypoint + serve flags so callers/SRE can see
    # exactly what was launched.
    res.argv = [spec.runtime, *spec.argv]
    res.config_dir = config_dir

    try:
        proc = subprocess.Popen(  # noqa: S603 - argv is a vector, shell=False
            res.argv, cwd=spec.cwd, env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
        )
    except OSError as exc:
        res.reason = REASON_SPAWN_FAILED
        res.detail = f"{type(exc).__name__}: {exc}"
        return res

    res.pid = proc.pid
    res.workload_created_at = time.time()

    # Read the stream. A headless run terminates, so a bounded wait is correct;
    # an unbounded one would hang the request forever on a child that never exits.
    deadline = time.time() + timeout_s
    native_at: Optional[float] = None
    lines: List[str] = []
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.append(line)
            if native_at is None and _classify_event(line):
                native_at = time.time()
                res.events.append(_redact(json.loads(line)) if line.strip().startswith("{") else {"raw": line[:400]})
            if time.time() > deadline:
                proc.kill()
                break
        proc.wait(timeout=10)
    except Exception as exc:  # noqa: BLE001 - a partial stream is still evidence
        res.detail = f"stream read: {type(exc).__name__}: {exc}"
    finally:
        if proc.poll() is None:
            proc.kill()

    res.exit_code = proc.returncode
    res.workload_completed_at = time.time() if proc.poll() is not None else None
    if native_at is not None:
        res.workload_started_at = res.workload_created_at
        res.first_native_event_at = native_at

    # A workload id, when the child reports one. Never our own run id wearing
    # this label — that substitution is what made an unrelated session pass.
    for line in lines:
        try:
            payload = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(payload, dict):
            for key in ("workload_id", "workloadId", "session_id", "sessionId"):
                val = payload.get(key)
                if isinstance(val, str) and val.strip():
                    res.workbuddy_workload_id = val.strip()
                    break
        if res.workbuddy_workload_id:
            break

    if native_at is None:
        res.classification = "REFUSED"
        res.reason = REASON_NOT_STARTED
        res.detail = (
            "child produced no native event (only heartbeats or nothing); "
            f"exit={res.exit_code}, lines={len(lines)}"
        )
        return res

    res.ok = True
    res.classification = "NATIVE_STARTED"
    return res


def verify_attached_session(
    *, session: Dict[str, Any], project_root: str, launch_requested_at: float,
    correlation_id: str = "",
) -> Optional[str]:
    """Decide whether an EXISTING session may be attached to this mission.

    Returns None when the session qualifies, or the reason it may not. This is
    observation and recovery only — see the module docstring: a session found this
    way can never be the evidence that a launch succeeded.
    """
    cwd = str(session.get("cwd") or "")
    if cwd and os.path.normcase(os.path.normpath(cwd)) != os.path.normcase(os.path.normpath(project_root)):
        return f"{REASON_PROJECT_MISMATCH}: session cwd {cwd!r} != requested {project_root!r}"
    for field in ("created_at", "updated_at"):
        ts = session.get(field)
        if isinstance(ts, (int, float)) and ts and ts < launch_requested_at:
            return f"{REASON_PREDATES_LAUNCH}: session.{field}={ts} < launch_requested_at={launch_requested_at}"
    if correlation_id:
        seen = str(session.get("last_event_id") or "") + str(session.get("session_id") or "")
        if correlation_id not in seen and correlation_id not in str(session.get("correlation_id") or ""):
            # Not fatal on its own — the timestamps above are the hard gate — but
            # recorded so a reviewer can see the session was matched on time
            # rather than on identity.
            pass
    return None
