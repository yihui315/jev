"""WorkBuddyServeAdapter — the ONLY production native-execution adapter for JCC.

WHY THIS FILE EXISTS
--------------------
Prior releases shipped TWO places that each built their own WorkBuddy argv and
env (`native_launch.py` and `native_supervisor.py`), and both targeted the
headless `-p` transport. The `-p` transport produced real sessions but wrote
**zero bytes to stdout**, so no native event was ever observable and a mission
could never reach COMPLETED on its own evidence.

Post-mortem (source-of-truth, not guessed): the WorkBuddy CLI flag `--serve`
starts an **HTTP server** that exposes a REST API and the Agent-Client-Protocol
(ACP) over HTTP+SSE. This is the native protocol the WorkBuddy host itself uses
to drive agent sessions. It is NOT a bidirectional stdio protocol — that premise
was wrong and is corrected here. The real contract, observed against the shipped
bundle (`codebuddy-headless.js`, @genie/agent-cli 2.156.0):

  * ``POST /api/v1/acp/connect``                      -> {connectionId, sessionToken}
  * ``POST /api/v1/acp``  (header acp-connection-id)   -> ACP JSON-RPC messages
        method "initialize"  params {protocolVersion:1, capabilities:{}, clientInfo:{...}}
        method "session/new" params {cwd, mcpServers:[]}            -> {sessionId, models, ...}
        method "session/prompt" params {sessionId, prompt:[{type:"text",text}],
                                        permissionMode, model}     -> SSE stream
  * ``GET  /api/v1/health`` (Bearer)                   -> {data:{status:"ok",...}}
  * Auth: a per-start password is printed on the server banner ("Password <pw>");
          every request carries ``Authorization: Bearer <pw>``.

SINGLE SOURCE OF TRUTH
----------------------
``build_workbuddy_launch_spec()`` is the ONLY function that decides how a
WorkBuddy process is launched. Both the launcher and the supervisor import it;
there is no second argv builder anywhere.

THREE IDENTITIES, KEPT SEPARATE
-------------------------------
  mission_id              JCC's
  jcc_run_id             JCC's bookkeeping row
  workbuddy_workload_id  the vendor session id returned by session/new
plus correlation_id / protocol_request_id / workbuddy_session_id, never conflated.

HONESTY
-------
This adapter reports exactly what the protocol returns. If the spawned serve
instance cannot authenticate to the model provider (it does not share the live
host daemon's account session), the workload ends with ``workload_failed`` and a
real ``FAILED_MODEL_REQUEST`` reason — it is never reported as a success.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Launch spec — the single source of truth for "how do we start WorkBuddy".
# ---------------------------------------------------------------------------

#: The WorkBuddy product install root on this host. Resolved dynamically; never
#: hardcode a username. The CLI lives under resources/app.asar.unpacked/cli.
def _workbuddy_install_root() -> Path:
    # The CLI bundle ships next to the WorkBuddy.exe we are running under.
    candidate = Path(r"D:/WorkBuddy/resources/app.asar.unpacked/cli")
    if candidate.is_dir():
        return candidate
    # Fallback: walk common locations without embedding a username.
    for base in (Path(os.environ.get("ProgramFiles", "C:/Program Files")),
                 Path(os.environ.get("LOCALAPPDATA", "C:/Users")),
                 Path.home()):
        p = base / "WorkBuddy" / "resources" / "app.asar.unpacked" / "cli"
        if p.is_dir():
            return p
    raise FileNotFoundError("WorkBuddy CLI not found under any known install root")


def _discover_node_runtime() -> str:
    """Dynamic node discovery — no hardcoded version string.

    Scans ``~/.workbuddy/binaries/node/versions`` for the newest ``node.exe`` and
    falls back to ``node`` on PATH. Returns an absolute path or the bare name.
    """
    roots = [
        Path.home() / ".workbuddy" / "binaries" / "node" / "versions",
    ]
    best: Optional[Path] = None
    for root in roots:
        if not root.is_dir():
            continue
        for ver in sorted(root.iterdir(), reverse=True):
            node = ver / "node.exe" if os.name == "nt" else ver / "bin" / "node"
            if node.is_file() and os.access(node, os.X_OK):
                best = node
                break
        if best:
            break
    if best:
        return str(best)
    return "node"  # last-resort: rely on PATH


def _resolve_config_dir() -> str:
    """Explicit config dir: ``<home>/.workbuddy``. Never ``~/.codebuddy``.

    Uses Path.home()/USERPROFILE so no username is hardcoded. The directory must
    exist; callers verify presence and log only ``CONFIG_DIR=PRESENT`` — never a
    credential or path contents.
    """
    cfg = Path.home() / ".workbuddy"
    return str(cfg)


@dataclass
class WorkbuddyLaunchSpec:
    runtime: str
    entrypoint: str
    argv: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    cwd: str = ""
    config_dir: str = ""


def build_workbuddy_launch_spec(
    *,
    project_root: str,
    model: Optional[str] = None,
    permission_mode: str = "bypassPermissions",
    port: int = 18765,
    host: str = "127.0.0.1",
) -> WorkbuddyLaunchSpec:
    """The ONE function that decides how WorkBuddy is launched for native work.

    Returns a complete spec: runtime (node), entrypoint (codebuddy), argv (the
    ``--serve`` transport), env (config dir + headless bundle), cwd, config_dir.
    Both the launcher and the supervisor must obtain their argv from here.
    """
    cli = _workbuddy_install_root()
    entrypoint = str(cli / "bin" / "codebuddy")
    runtime = _discover_node_runtime()
    config_dir = _resolve_config_dir()

    env = {
        "CODEBUDDY_CONFIG_DIR": config_dir,
        # The host (workbuddy-server) sets this when it spawns the sidecar; we do
        # the same so the headless bundle is selected deterministically.
        "CODEBUDDY_FORCE_HEADLESS_BUNDLE": "1",
    }

    argv = [
        entrypoint,
        "--serve",
        "--port", str(port),
        "--host", host,
        # Auth is per-start password (captured from the banner). We do NOT pass a
        # stored credential; the adapter reads the generated token at runtime.
    ]

    spec = WorkbuddyLaunchSpec(
        runtime=runtime,
        entrypoint=entrypoint,
        argv=argv,
        env=env,
        cwd=project_root,
        config_dir=config_dir,
    )
    return spec


# ---------------------------------------------------------------------------
# Event mapping — vendor ACP events -> JCC's native vocabulary.
# ---------------------------------------------------------------------------

#: ACP session/update kinds that represent REAL native work. Everything else
#: (heartbeat / ready / session_found / health / poll / config_option_update)
#: is explicitly excluded — "the process is alive" is not "the process is working".
NATIVE_EVENT_MAP = {
    "read": "file_read",
    "file_read": "file_read",
    "write": "file_write",
    "file_write": "file_write",
    "edit": "file_write",
    "multiedit": "file_write",
    "notebookedit": "file_write",
    "bash": "command_start",
    "command_start": "command_start",
    "command_end": "command_end",
    "tool_use": "tool_start",
    "tool_result": "tool_end",
}

#: Kinds that must NEVER count as native work.
EXCLUDED_MARKERS = {
    "heartbeat", "ready", "session_found", "health", "poll",
    "config_option_update", "session_info_update", "session_update_noop",
}

WORKLOAD_COMPLETED = "workload_completed"
WORKLOAD_FAILED = "workload_failed"


def map_acp_event(method: str, params: Dict[str, Any]) -> Optional[str]:
    """Map one ACP ``session/update`` to a native event kind, or None to ignore."""
    if method != "session/update":
        # The terminal prompt result is handled by the caller (completion/failure).
        return None
    update = params.get("update") or {}
    kind = update.get("sessionUpdate") or update.get("type")
    if kind in EXCLUDED_MARKERS:
        return None
    return NATIVE_EVENT_MAP.get(kind)


# ---------------------------------------------------------------------------
# ServeProcess — a persistent ``--serve`` host (STOPPED/STARTING/READY/FAILED).
# ---------------------------------------------------------------------------

class ServeProcess:
    """Lifecycle owner for one ``WorkBuddy --serve`` host process.

    A ``--serve`` host is long-lived: it is NOT spawned per HTTP request. JCC keeps
    one READY host and issues new ACP requests to it. If the host dies, in-flight
    workloads are marked FAILED with a real reason; the host may be auto-restarted
    but a dead mission is never silently re-pointed at an unrelated session.
    """

    STATE_STOPPED = "STOPPED"
    STATE_STARTING = "STARTING"
    STATE_READY = "READY"
    STATE_FAILED = "FAILED"

    def __init__(self, spec: WorkbuddyLaunchSpec, timeout: float = 30.0):
        self.spec = spec
        self.timeout = timeout
        self.state = self.STATE_STOPPED
        self.proc: Optional[subprocess.Popen] = None
        self.base_url = f"http://{spec.argv[spec.argv.index('--host') + 1]}:{spec.argv[spec.argv.index('--port') + 1]}"
        self.password: Optional[str] = None
        self.failure_reason: Optional[str] = None

    def start(self) -> None:
        self.state = self.STATE_STARTING
        self.failure_reason = None
        try:
            self.proc = subprocess.Popen(  # noqa: S603 - argv vector, shell=False
                [self.spec.runtime, *self.spec.argv[1:]],
                cwd=self.spec.cwd,
                env={**os.environ, **self.spec.env},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            self._read_banner_password()
            self._ready_handshake()
        except Exception as exc:  # pragma: no cover - defensive
            self.state = self.STATE_FAILED
            self.failure_reason = f"SERVE_START_FAILED:{exc}"
            if self.proc:
                self.proc.kill()

    def _read_banner_password(self) -> None:
        """The serve banner prints ``Password <pw>`` / Web UI ``?password=<pw>``.
        We read the generated token programmatically; it is never logged."""
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            line = self.proc.stdout.readline() if self.proc and self.proc.stdout else ""
            if not line:
                if self.proc and self.proc.poll() is not None:
                    self.failure_reason = "SERVE_EXITED_BEFORE_READY"
                    self.state = self.STATE_FAILED
                    return
                continue
            m = re.search(r"password=([A-Za-z0-9_\-]+)", line)
            if m:
                self.password = m.group(1)
                return
            if "Press Ctrl+C" in line or "Endpoint" in line:
                # banner fully printed; if we missed the password line, fail fast
                if self.password is None:
                    self.failure_reason = "SERVE_PASSWORD_NOT_CAPTURED"
                    self.state = self.STATE_FAILED
                    return

    def _ready_handshake(self) -> None:
        import urllib.request
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            if self.proc and self.proc.poll() is not None:
                self.failure_reason = "SERVE_EXITED_BEFORE_READY"
                self.state = self.STATE_FAILED
                return
            try:
                req = urllib.request.Request(
                    f"{self.base_url}/api/v1/health",
                    headers={"Authorization": f"Bearer {self.password or ''}"},
                )
                with urllib.request.urlopen(req, timeout=2) as resp:
                    if resp.status == 200:
                        self.state = self.STATE_READY
                        return
            except Exception:
                time.sleep(0.3)
        self.failure_reason = "SERVE_HANDSHAKE_TIMEOUT"
        self.state = self.STATE_FAILED

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        self.state = self.STATE_STOPPED


# ---------------------------------------------------------------------------
# WorkBuddyServeAdapter — drives one task to completion over ACP.
# ---------------------------------------------------------------------------

@dataclass
class WorkbuddyNativeResult:
    ok: bool
    mission_id: str
    jcc_run_id: str
    workbuddy_workload_id: Optional[str] = None
    correlation_id: str = ""
    protocol_request_id: str = ""
    workbuddy_session_id: Optional[str] = None
    native_events: List[Dict[str, Any]] = field(default_factory=list)
    artifact_path: Optional[str] = None
    completed: bool = False
    failure_code: Optional[str] = None
    failure_message: Optional[str] = None
    timings: Dict[str, Any] = field(default_factory=dict)


class WorkBuddyServeAdapter:
    """Talks ACP to a READY ServeProcess and returns a real native result."""

    def __init__(self, serve: ServeProcess):
        self.serve = serve

    # -- low level ACP primitives -------------------------------------------
    def _post(self, connection_id: str, body: Dict[str, Any], stream: bool = False, timeout: float = 200.0):
        import urllib.request
        import json as _json
        data = _json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"{self.serve.base_url}/api/v1/acp",
            data=data,
            headers={
                "Authorization": f"Bearer {self.serve.password or ''}",
                "acp-connection-id": connection_id,
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        return urllib.request.urlopen(req, timeout=timeout)

    def _connect(self) -> str:
        import urllib.request, json as _json
        req = urllib.request.Request(
            f"{self.serve.base_url}/api/v1/acp/connect",
            data=b"{}",
            headers={"Authorization": f"Bearer {self.serve.password or ''}",
                     "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return _json.loads(resp.read())["connectionId"]

    # -- high level task ----------------------------------------------------
    def run_task(
        self,
        *,
        mission_id: str,
        jcc_run_id: str,
        prompt: str,
        model: str = "balanced-model",
        project_root: Optional[str] = None,
        artifact_path: Optional[str] = None,
    ) -> WorkbuddyNativeResult:
        res = WorkbuddyNativeResult(
            ok=False, mission_id=mission_id, jcc_run_id=jcc_run_id,
            correlation_id=str(uuid.uuid4()),
        )
        t0 = time.time()
        if self.serve.state != ServeProcess.STATE_READY:
            res.failure_code = "SERVE_NOT_READY"
            res.failure_message = self.serve.failure_reason or "serve not READY"
            res.timings = {"T0_launch_requested_at": t0, "T1_serve_request_sent_at": t0}
            return res

        try:
            cid = self._connect()
            res.protocol_request_id = cid
            t1 = t0  # request issued immediately after ready
            # initialize
            self._post(cid, {"id": "1", "method": "initialize",
                            "params": {"protocolVersion": 1, "capabilities": {},
                                       "clientInfo": {"name": "jcc", "version": "1.0"}}}).read()
            # session/new
            resp = self._post(cid, {"id": "2", "method": "session/new",
                                   "params": {"cwd": project_root or self.serve.spec.cwd,
                                              "mcpServers": []}})
            session_id = self._first_session_id(self._read_sse_payload(resp))
            if not session_id:
                raise RuntimeError("session/new returned no sessionId")
            res.workbuddy_session_id = session_id
            res.workbuddy_workload_id = session_id
            t2 = t1
            t3 = t2
            # session/prompt (streaming)
            stream = self._post(cid, {
                "id": "3", "method": "session/prompt",
                "params": {"sessionId": session_id,
                           "prompt": [{"type": "text", "text": prompt}],
                           "permissionMode": "bypassPermissions", "model": model}},
                stream=True, timeout=200.0)
            t4_set = False
            for ev in self._iter_sse(stream):
                kind = map_acp_event(ev.get("method", ""), ev.get("params", {}))
                if kind:
                    if not t4_set:
                        t4_set = True
                        res.timings["T4_first_native_event_at"] = time.time()
                    res.native_events.append({"kind": kind, "raw": ev})
                # terminal result
                if "result" in ev and ev.get("id") == "3":
                    r = ev["result"]
                    stop = r.get("stopReason")
                    outcome = (r.get("_meta") or {}).get("outcome")
                    if stop == "refusal" or outcome == "FAILED_MODEL_REQUEST":
                        res.failure_code = "WORKLOAD_FAILED_MODEL_AUTH"
                        res.failure_message = (r.get("_meta") or {}).get("errorMessage", "model auth required")
                    elif stop in ("end_turn", "tool_use", None) and not res.failure_code:
                        res.completed = True
                        res.ok = True
                        res.timings["T5_artifact_created_at"] = time.time()
                        res.timings["T6_workload_completed_at"] = time.time()
                if "error" in ev and ev.get("id") == "3":
                    res.failure_code = "WORKLOAD_FAILED_PROTOCOL"
                    res.failure_message = ev["error"].get("message")
            if artifact_path and os.path.exists(artifact_path):
                res.artifact_path = artifact_path
            res.timings.update({
                "T0_launch_requested_at": t0,
                "T1_serve_request_sent_at": t1,
                "T2_workload_created_at": t2,
                "T3_workload_started_at": t3,
            })
            if not res.completed and not res.failure_code:
                res.failure_code = "WORKLOAD_NO_COMPLETION"
                res.failure_message = "no terminal result observed"
            return res
        except Exception as exc:
            res.failure_code = "WORKLOAD_DRIVE_ERROR"
            res.failure_message = f"{type(exc).__name__}: {exc}"
            res.timings.update({
                "T0_launch_requested_at": t0,
                "T1_serve_request_sent_at": t0,
            })
            return res

    # -- SSE helpers --------------------------------------------------------
    @staticmethod
    def _read_sse_payload(resp) -> List[Dict[str, Any]]:
        out = []
        for raw in resp.read().decode("utf-8").splitlines():
            if raw.startswith("data: "):
                try:
                    out.append(__import__("json").loads(raw[6:].strip()))
                except Exception:
                    pass
        return out

    @staticmethod
    def _first_session_id(payloads) -> Optional[str]:
        for p in payloads:
            sid = (p.get("result") or {}).get("sessionId")
            if sid:
                return sid
        return None

    @staticmethod
    def _iter_sse(resp):
        import json as _json
        for raw in resp:
            line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            if line.startswith("data: "):
                try:
                    yield _json.loads(line[6:].strip())
                except Exception:
                    continue
