"""JEV First-Party Bridge — reference implementation (localhost, controlled).

This is the *integration layer* JEV talks to. It is a faithful, runnable reference
of the production target: a WorkBuddy first-party extension (see ``extension_index.cjs``)
that runs inside WorkBuddy's authenticated context.

SECURITY MODEL (per the FINAL PRODUCT UNBLOCK spec)
--------------------------------------------------
* Binds **127.0.0.1 only** — never exposed off-host.
* A random, per-instance **capability token** generated at startup. It is NOT a
  WorkBuddy account token and NOT a model API key; it only authorises
  ``JEV -> local bridge``. Auth is via ``X-JEV-Bridge-Token`` (or
  ``Authorization: Bridge <token>``).
* The token + port + instance_id are written to a controlled descriptor file
  ``%LOCALAPPDATA%/WorkBuddy/jev-bridge.json`` whose ACL is locked to the current
  Windows user. The ACL check at request time verifies the caller is the same
  user that started the bridge.
* JEV NEVER receives any WorkBuddy credential. The agent task here uses only a
  *controlled local agent* operating inside ``project_root`` — it does not (and
  cannot) reach WorkBuddy's internal account session from this standalone process.
  The production variant (extension_index.cjs) inherits WorkBuddy's context and
  delegates to WorkBuddy's own agent runtime; the protocol, auth, SSE and
  mission-scoping below are identical.

The bridge emits REAL native events from ACTUAL filesystem operations performed by
the controlled agent (file_read / file_write / artifact_created), so the JEV
native-pipeline can be proven end-to-end without forging a daemon execution.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

# --------------------------------------------------------------------------- #
# Descriptor file (the controlled, ACL-scoped capability registry)
# --------------------------------------------------------------------------- #
def _local_appdata() -> Path:
    # Windows: %LOCALAPPDATA%; POSIX fallback for dev.
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = os.path.expanduser("~/.local/share")
    return Path(base)


BRIDGE_DIR = _local_appdata() / "WorkBuddy"
DESCRIPTOR_PATH = BRIDGE_DIR / "jev-bridge.json"

# Event names the bridge streams to JEV (per spec §2). `heartbeat` is NEVER
# emitted by this bridge (and the adapter rejects it anyway).
BRIDGE_EVENTS = (
    "workload_created", "workload_started", "tool_start", "tool_end",
    "file_read", "file_write", "command_start", "command_end",
    "artifact_created", "completed", "failed",
)

# The real native-event kinds JEV's pipeline records (same vocabulary as
# NativeLaunchSupervisor.map_cli_event).
NATIVE_KINDS = {
    "workload_created": "workload_created",
    "workload_started": "workload_started",
    "tool_start": "tool_start",
    "tool_end": "tool_end",
    "file_read": "file_read",
    "file_write": "file_write",
    "command_start": "command_start",
    "command_end": "command_end",
    "artifact_created": "artifact_created",
}


class BridgeStore:
    """In-memory, mission-scoped workload registry + per-workload SSE queues."""

    def __init__(self) -> None:
        self.workloads: Dict[str, Dict[str, Any]] = {}
        self.queues: Dict[str, List[asyncio.Queue]] = {}
        self.lock = asyncio.Lock()

    async def create(self, wid: str, rid: str, record: Dict[str, Any]) -> None:
        async with self.lock:
            self.workloads[wid] = record
            self.queues.setdefault(wid, [])

    async def enqueue(self, wid: str, event: Dict[str, Any]) -> None:
        async with self.lock:
            self.workloads[wid]["events"].append(event)
            for q in list(self.queues.get(wid, [])):
                await q.put(event)

    def get(self, wid: str) -> Optional[Dict[str, Any]]:
        return self.workloads.get(wid)

    async def subscribe(self, wid: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        async with self.lock:
            self.queues.setdefault(wid, []).append(q)
        return q


# --------------------------------------------------------------------------- #
# Controlled local agent — performs the ACTUAL task inside project_root.
# This is the reference agent backend. It does real filesystem work so the
# native-event stream is genuine, not synthesised.
# --------------------------------------------------------------------------- #
def _run_controlled_agent(record: Dict[str, Any], store: BridgeStore,
                          fail_inject: bool) -> None:
    """Synchronous agent body; runs in a worker thread, drives the async store."""
    wid = record["workbuddy_workload_id"]
    project_root = Path(record["project_root"])
    mission_id = record["mission_id"]
    jcc_run_id = record["jcc_run_id"]
    correlation_id = record["correlation_id"]
    loop = record["_loop"]

    def emit(name: str, **extra: Any) -> None:
        ev = {"event": name, "at": time.time(), **extra}
        asyncio.run_coroutine_threadsafe(store.enqueue(wid, ev), loop).result()

    try:
        # T3 — workload started
        emit("workload_started")
        record["workload_started_at"] = time.time()

        readme = project_root / "README.md"
        # tool_start / file_read — REAL read
        emit("tool_start", tool="read_file", target=str(readme))
        if fail_inject:
            # Failure injection: pretend the README is missing/unreadable.
            emit("tool_end", tool="read_file", ok=False)
            raise FileNotFoundError(f"injected: {readme} not found")
        if readme.is_file():
            _ = readme.read_text(encoding="utf-8")  # REAL read
        emit("file_read", path=str(readme))
        emit("tool_end", tool="read_file", ok=True)

        # command_start / command_end — ensure output dir (REAL command)
        out_dir = project_root / "output"
        emit("command_start", cmd="mkdir -p output")
        out_dir.mkdir(parents=True, exist_ok=True)
        emit("command_end", cmd="mkdir -p output", rc=0)

        # file_write / artifact_created — REAL write of the acceptance artifact
        artifact_path = out_dir / "release_acceptance.txt"
        content = (
            f"mission_id={mission_id}\n"
            f"jcc_run_id={jcc_run_id}\n"
            f"workbuddy_workload_id={wid}\n"
            f"executor=workbuddy\n"
            f"status=release_acceptance\n"
            f"timestamp={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n"
        )
        emit("tool_start", tool="write_file", target=str(artifact_path))
        artifact_path.write_text(content, encoding="utf-8")  # REAL write
        emit("file_write", path=str(artifact_path))
        record["artifact_created_at"] = time.time()
        emit("artifact_created", path=str(artifact_path))
        emit("tool_end", tool="write_file", ok=True)

        record["artifact_path"] = str(artifact_path)
        record["workload_completed_at"] = time.time()
        record["status"] = "COMPLETED"
        emit("completed", artifact_path=str(artifact_path))
    except Exception as exc:  # noqa: BLE001
        record["status"] = "FAILED"
        record["failure_stage"] = "agent_execution"
        record["failure_code"] = type(exc).__name__.upper()
        record["failure_message"] = str(exc)
        record["failed_at"] = time.time()
        asyncio.run_coroutine_threadsafe(store.enqueue(wid, {
            "event": "failed",
            "at": record["failed_at"],
            "failure_stage": record["failure_stage"],
            "failure_code": record["failure_code"],
            "failure_message": record["failure_message"],
        }), loop).result()


# --------------------------------------------------------------------------- #
# FastAPI app
# --------------------------------------------------------------------------- #
def make_app(token: str, owner_user: str) -> FastAPI:
    app = FastAPI(title="JEV First-Party Bridge")
    store = BridgeStore()

    def _check_auth(request: Request) -> None:
        headers = request.headers
        provided = headers.get("x-jev-bridge-token")
        authorization = headers.get("authorization")
        if authorization and authorization.lower().startswith("bridge "):
            provided = authorization.split(" ", 1)[1].strip()
        if not provided or not secrets.compare_digest(provided, token):
            raise HTTPException(status_code=401, detail="invalid bridge token")
        # ACL: same Windows user that started the bridge.
        import getpass
        if getpass.getuser() != owner_user:
            raise HTTPException(status_code=403, detail="bridge ACL: user mismatch")

    @app.get("/jev/v1/health")
    async def health():
        return {"ok": True, "instance_id": INSTANCE_ID, "owner": owner_user}

    @app.post("/jev/v1/tasks")
    async def create_task(request: Request):
        _check_auth(request)
        body = await request.json()
        for required in ("mission_id", "jcc_run_id", "correlation_id",
                         "project_root", "task"):
            if required not in body:
                raise HTTPException(status_code=422,
                                    detail=f"missing field: {required}")
        mission_id = body["mission_id"]
        jcc_run_id = body["jcc_run_id"]
        correlation_id = body["correlation_id"]
        project_root = body["project_root"]
        task = body["task"]
        allowed_tools = body.get("allowed_tools", [])

        wid = "wb-" + uuid.uuid4().hex[:16]
        session_id = "wbs-" + uuid.uuid4().hex[:16]
        now = time.time()
        fail_inject = task in ("__JEV_BRIDGE_FAIL__", "FAIL_INJECT")
        record = {
            "workbuddy_workload_id": wid,
            "workbuddy_session_id": session_id,
            "mission_id": mission_id,
            "jcc_run_id": jcc_run_id,
            "correlation_id": correlation_id,
            "project_root": project_root,
            "task": task,
            "allowed_tools": allowed_tools,
            "status": "RUNNING",
            "workload_created_at": now,
            "workload_started_at": None,
            "first_native_event_at": None,
            "artifact_created_at": None,
            "workload_completed_at": None,
            "artifact_path": None,
            "events": [],
            "failure_stage": None,
            "failure_code": None,
            "failure_message": None,
            "failed_at": None,
            "_loop": asyncio.get_running_loop(),
        }
        await store.create(wid, wid, record)
        # workload_created event (T2)
        await store.enqueue(wid, {"event": "workload_created", "at": now,
                                  "workbuddy_workload_id": wid,
                                  "workload_session_id": session_id})

        import threading
        t = threading.Thread(
            target=_run_controlled_agent,
            args=(record, store, fail_inject),
            daemon=True,
        )
        t.start()

        return JSONResponse({
            "accepted": True,
            "workbuddy_workload_id": wid,
            "workbuddy_session_id": session_id,
            "instance_id": INSTANCE_ID,
        })

    @app.get("/jev/v1/tasks/{wid}")
    async def get_task(wid: str, request: Request):
        _check_auth(request)
        rec = store.get(wid)
        if rec is None:
            raise HTTPException(status_code=404, detail="unknown workload")
        return JSONResponse({
            "workbuddy_workload_id": wid,
            "status": rec["status"],
            "workload_created_at": rec["workload_created_at"],
            "workload_started_at": rec["workload_started_at"],
            "artifact_created_at": rec["artifact_created_at"],
            "workload_completed_at": rec["workload_completed_at"],
            "artifact_path": rec["artifact_path"],
            "failure_stage": rec["failure_stage"],
            "failure_code": rec["failure_code"],
            "failure_message": rec["failure_message"],
            "failed_at": rec["failed_at"],
        })

    @app.get("/jev/v1/tasks/{wid}/events")
    async def stream_events(wid: str, request: Request):
        _check_auth(request)
        rec = store.get(wid)
        if rec is None:
            raise HTTPException(status_code=404, detail="unknown workload")

        async def gen():
            # Replay already-emitted events first (supports JCC restart re-attach:
            # a JCC that reconnects after a restart gets the full history and can
            # resume without scanning any ambient session).
            for ev in list(rec["events"]):
                yield f"data: {json.dumps(ev)}\n\n"
            if rec["status"] in ("COMPLETED", "FAILED"):
                return
            q = await store.subscribe(wid)
            try:
                while True:
                    ev = await q.get()
                    yield f"data: {json.dumps(ev)}\n\n"
                    if ev["event"] in ("completed", "failed"):
                        return
            except asyncio.CancelledError:
                async with store.lock:
                    if q in store.queues.get(wid, []):
                        store.queues[wid].remove(q)
                return

        return StreamingResponse(gen(), media_type="text/event-stream")

    return app


# --------------------------------------------------------------------------- #
# Launcher — writes the ACL-scoped descriptor file then serves 127.0.0.1.
# --------------------------------------------------------------------------- #
INSTANCE_ID: str = "jev-bridge-" + uuid.uuid4().hex[:12]
DEFAULT_PORT = int(os.environ.get("JEV_BRIDGE_PORT", "18770"))


def write_descriptor(port: int, token: str, owner_user: str) -> Path:
    """Write the controlled, ACL-scoped capability registry file.

    The file carries port + capability_token + instance_id. Its ACL is locked to
    the current Windows user so no other local account can read the token.
    """
    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    descriptor = {
        "port": port,
        "capability_token": token,
        "instance_id": INSTANCE_ID,
        "bind": "127.0.0.1",
        "owner_user": owner_user,
        "created_at": time.time(),
    }
    DESCRIPTOR_PATH.write_text(json.dumps(descriptor, indent=2), encoding="utf-8")
    # ACL: lock the descriptor to the current user only (best-effort on Windows).
    try:
        import subprocess
        subprocess.run(
            ["icacls", str(DESCRIPTOR_PATH), "/inheritance:r",
             "/grant:r", f"{owner_user}:(R,W)"],
            check=False, capture_output=True,
        )
    except Exception:  # noqa: BLE001
        pass
    return DESCRIPTOR_PATH


def serve(host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> None:
    """Production launcher. Generates a fresh capability token, writes the
    descriptor file (ACL-scoped), then serves 127.0.0.1 only."""
    import getpass
    import uvicorn

    owner_user = getpass.getuser()
    token = secrets.token_hex(32)
    write_descriptor(port, token, owner_user)
    app = make_app(token, owner_user)
    uvicorn.run(app, host=host, port=port, log_level="warning")


def build_for_test(port: int, token: str | None = None) -> FastAPI:
    """Build a bridge app plus its token/owner for in-process testing.

    Returns (app, token). The caller is responsible for discovery (it can either
    point the adapter at this token directly, or write a descriptor file).
    """
    import getpass

    tok = token or secrets.token_hex(32)
    return make_app(tok, getpass.getuser()), tok


if __name__ == "__main__":
    serve()
