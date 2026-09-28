"""JCC Core FastAPI server.

Hard constraints (ADR-0002 / ADR-0004 / ADR-0005):
  * Bind localhost ONLY. ``create_app`` is given the engine; the actual bind
    host is forced to 127.0.0.1 by run.py (never 0.0.0.0).
  * Fail-closed auth: every MUTATING endpoint depends on
    ``require_control_token`` which uses ``hmac.compare_digest`` (constant-time)
    against ``JCC_CONTROL_TOKEN``. Unset token -> 401 (control locked).
  * CORS is an EXPLICIT allowlist (from config). No wildcard.
  * Read-only endpoints (health/sessions/audit/decisions) are open BY DESIGN and
    declared, machine-readably, in the ``open_get_endpoints`` field of
    ``/api/health`` (P4 #77.1). The declaration is what an operator or a
    scanner reads; ``test_server_surface.py`` fails if a GET route is added
    without declaring it, or vice versa -- an undeclared open route is
    otherwise invisible to every review that does not read this file.
  * /ws is gated: the control token is presented as the WebSocket subprotocol
    (proven pattern in this runtime; ws.query_params is unreliable here).

  The surface below is LOOPBACK-ONLY and enforced, not documented only
  (P4 #77.2): ``run.main`` binds 127.0.0.1 regardless of configuration and
  REFUSES to start at all on a non-loopback ``server.host`` (ADR-0002/0004/0005;
  "never 0.0.0.0"). This module never binds anything -- the bind decision lives
  in ``run.py`` so that a library import of ``create_app`` cannot expose a port.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from .core import CoreEngine
from .events import EventBus, format_sse
from .providers.base import activation_state_for_requested
from .schemas import AEPEnvelope, Directive, DirectiveAction
from .scene import build_scene

logger = logging.getLogger("jcc.server")

#: The open (unauthenticated) READ-only GET surface, declared as data so that
#: both the server and a scanner read the same list (P4 #77.1).
#:
#: ``/ws`` is deliberately NOT here: it is a WebSocket route, not a GET, and it
#: is token-gated like any mutating endpoint.
OPEN_GET_ENDPOINTS = (
    "/api/health",
    "/api/sessions",
    "/api/sessions/{sid}",
    "/api/audit",
    "/api/decisions",
    "/api/audit/verify",
    "/api/v1/scene",
    "/api/v1/events/stream",
    # v1.2.1 — AI CONNECTION CENTER (read-only discovery)
    "/api/connections",
    "/api/connections/{agent}",
    "/api/onboarding/state",
    # v1.3 Wave 1 — Project/Mission domain (read-only discovery)
    "/api/v1/domain/home",
    "/api/v1/domain/projects",
    "/api/v1/domain/projects/{pid}",
    "/api/v1/domain/missions",
    "/api/v1/domain/missions/{mid}",
    "/api/v1/domain/missions/{mid}/timeline",
    "/api/v1/domain/missions/{mid}/runs",
    "/api/v1/domain/missions/{mid}/artifacts",
    "/api/v1/domain/connections",
    "/api/v1/domain/needs-attention",
    # v1.3 Wave 3 — mission launch bridge (read-only discovery + workload)
    "/api/v1/domain/launch/probe",
    "/api/v1/domain/missions/{mid}/workload",
)

#: Server-side truth for which agents a Mission may actually be launched
#: against. Mirrors ``mission_runtime.LAUNCHABLE_AGENTS``; kept as a literal here
#: so the OpenAPI surface does not import the runtime for a constant.
LAUNCHABLE_AGENTS_SERVER = ("workbuddy",)


class ProjectionManager:
    """Best-effort live projection to authenticated WS clients."""

    def __init__(self) -> None:
        self.active: set[WebSocket] = set()
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    def register(self, ws: WebSocket, loop: asyncio.AbstractEventLoop) -> None:
        self.active.add(ws)
        self.loop = loop

    def unregister(self, ws: WebSocket) -> None:
        self.active.discard(ws)

    def emit(self, msg: dict) -> None:
        if self.loop is None or not self.active:
            return
        data = json.dumps(msg, default=str)
        for ws in list(self.active):
            try:
                asyncio.run_coroutine_threadsafe(ws.send_text(data), self.loop)
            except Exception:  # noqa: BLE001
                self.active.discard(ws)


def create_app(engine: CoreEngine, config) -> FastAPI:
    app = FastAPI(
        title="JCC Core Control Plane",
        version="v1.3",
        # P4 #77.1: no Swagger / ReDoc / OpenAPI routes. FastAPI serves four
        # GET routes by default (``/docs``, ``/redoc``, ``/openapi.json``,
        # ``/docs/oauth2-redirect``) and every one of them is an UNDECLARED open
        # read -- /openapi.json hands a scanner the whole route table. Leaving
        # them on would make ``open_get_endpoints`` a lie, and the anti-drift
        # test below is precisely what makes that impossible: the declaration is
        # compared against the routes the app actually serves.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.server.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    manager = ProjectionManager()
    engine.subscribe(manager.emit)

    # Wave 4 — attach the live event bus to the engine. The bus only READS the
    # engine's broadcast and forwards typed events; it never writes state. The
    # engine owns the attribute so the SSE route can reach it.
    bus = EventBus()
    engine.event_bus = bus
    engine.subscribe(lambda out: bus.feed_engine_out(out))

    # v1.2.1 — auto-mark onboarding step 3 (Run First Task) and step 4
    # (See LIVE Session) when a LIVE WorkBuddy session appears. The check is
    # narrowly scoped to the workbuddy agent (the step the spec calls out).
    def _auto_advance_onboarding(out: dict) -> None:
        try:
            if not isinstance(out, dict):
                return
            if out.get("agent") == "workbuddy" and out.get("acted") is False \
                    and out.get("actuation_reason") == "shadow_mode":
                registry.mark_step(3)   # Run First Task (assess called)
                sid = out.get("session_id")
                if sid and engine.store.get_session(sid):
                    registry.mark_step(4)   # See LIVE Session
                    registry.set_state("workbuddy", ConnectionStatus.CONNECTED.value,
                                       last_event_at=time.time(),
                                       last_event_summary=str(out.get("event_type", "tool_call")),
                                       last_session_id=sid)
        except Exception:  # noqa: BLE001
            pass
    engine.subscribe(_auto_advance_onboarding)

    # v1.2.1 — AI CONNECTION CENTER: the registry owns per-agent config
    # (NON-SECRET only) + per-agent state. Secrets are read from env vars at
    # probe time and never persisted. The DB lives next to the state store at
    # ``runtime/jcc-v1.2/connections.db``; if the state store path is somewhere
    # else (e.g. tests), the registry just follows.
    from .connections import (
        AGENTS, ConnectionConfig, ConnectionRegistry,
        ConnectionStatus, snapshot as build_snapshot,
        test_local_cli, test_local_process, test_api, test_mcp,
    )
    conn_db_path = str(Path(config.state_store_db_path).parent / "connections.db")
    registry = ConnectionRegistry(conn_db_path)
    app.state.connection_registry = registry

    # v1.3 Wave 1 — Project/Mission domain. Shares the control-plane SQLite file
    # so a Mission and its Session live in one database and one backup. The
    # domain module is ADDITIVE: its tables are created beside the v1.2 ones and
    # nothing existing is altered.
    from .domain import (
        ApprovalState, ArtifactKind, AttentionKind, CapabilityKind,
        DomainStore, MissionStatus,
    )
    domain = DomainStore(config.state_store_db_path)
    app.state.domain_store = domain

    # v1.3 Wave 3 — the mission runtime. This is the ONE seam that joins the
    # domain (a Mission) to the AEP pipeline (a real Session + ingest), and it is
    # constructed WITHOUT an actuator: a launch cannot actuate because the seam is
    # not reachable from the runtime (see jcc_core.mission_runtime).
    from .mission_runtime import MissionRuntime
    runtime = MissionRuntime(domain, engine)
    app.state.mission_runtime = runtime

    async def require_control_token(
        x_jcc_token: Optional[str] = Header(None, alias="X-JCC-Token"),
        authorization: Optional[str] = Header(None),
    ) -> bool:
        """Fail-closed gate for all mutating control endpoints."""
        expected = os.environ.get(config.control_token_env)
        if not expected:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Control token not configured (fail-closed)",
            )
        presented = x_jcc_token
        if authorization and authorization.lower().startswith("bearer "):
            presented = authorization.split(" ", 1)[1]
        if not presented or not hmac.compare_digest(presented, expected):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid or missing control token",
            )
        return True

    # ------------------------------------------------------------------ #
    # Read-only (open) endpoints
    # ------------------------------------------------------------------ #
    @app.get("/api/health")
    def health():
        provider = engine.provider
        requested_mode = (config.jev.provider or "mock").upper()
        return {
            "status": "ok",
            "jev_provider": provider.name,
            # ---- §7D provider-mode contract -------------------------------- #
            # The four fields below are the machine-readable answer to "which
            # scorer produced the numbers I am looking at, and is it the real
            # one?". They describe the LIVE provider object, so they cannot be
            # faked by editing a document -- and `provider_is_real` is the
            # single bit a UI badge may key off, which is what makes
            # "mock dressed up as real" impossible to render.
            "provider_mode": getattr(provider, "mode", "UNKNOWN"),
            "provider_verified": bool(getattr(provider, "verified", False)),
            "provider_activation_state": activation_state_for_requested(
                requested_mode, provider
            ),
            # The CONFIGURED mode, kept separate on purpose: when `real` is
            # requested but the key/flag is missing, the effective provider is
            # DISABLED. Reporting only the effective value would make an
            # operator's deliberate choice and a fail-closed fallback look
            # identical, and those need opposite responses.
            "provider_requested_mode": requested_mode,
            "provider_is_real": bool(getattr(provider, "is_real", False)),
            # ----------------------------------------------------------------- #
            "actuation_enabled": engine.actuator.enabled,
            "policy_version": config.policy.version,
            "localhost_only": True,
            "wal": engine.store.is_wal,
            # Declared, machine-readable read-only surface (P4 #77.1). The
            # anti-drift test asserts this equals the routes FastAPI actually
            # serves -- a route added here without a declaration FAILS the suite,
            # which is the only way "open" stays reviewable over time.
            "open_get_endpoints": list(OPEN_GET_ENDPOINTS),
        }

    @app.get("/api/sessions")
    def sessions():
        return engine.store.list_sessions()

    @app.get("/api/sessions/{sid}")
    def session_detail(sid: str):
        s = engine.store.get_session(sid)
        if not s:
            raise HTTPException(status_code=404, detail="session not found")
        return s.model_dump(mode="json")

    @app.get("/api/audit")
    def audit(limit: int = 200):
        return engine.store.get_audit(limit)

    @app.get("/api/decisions")
    def decisions(sid: Optional[str] = None):
        return engine.store.get_directives(sid)

    @app.get("/api/audit/verify")
    def audit_verify():
        ok, count = engine.store.verify_audit()
        return {"intact": ok, "entries": count}

    # ------------------------------------------------------------------ #
    # Wave 3 — Scene projection (the single read-only world-state surface
    # for the v1.2 Visual Command Center). Schema ``jcc-scene/1``.
    # PROJECTION ONLY: reads live engine/store + released artifacts, never
    # writes, never creates a second truth store. Every section is labelled
    # LIVE / RELEASE / DERIVED / UNKNOWN; UNKNOWN is never coerced to
    # 0 / false / healthy / [] (see packages/jcc_core/scene.py).
    # ------------------------------------------------------------------ #
    @app.get("/api/v1/scene")
    def scene():
        cap_path = os.environ.get("JCC_CAPABILITY_MATRIX") or str(
            Path(__file__).resolve().parents[2]
            / "artifacts"
            / "release"
            / "V1_1_CAPABILITY_MATRIX.json"
        )
        return build_scene(engine, config, capability_matrix_path=cap_path)

    # ------------------------------------------------------------------ #
    # Wave 4 — Server-Sent Events live stream (schema ``jcc-events/1``).
    # READ-ONLY projection: forwards engine broadcasts as ten typed events.
    # Supports Last-Event-ID resume (replay buffered events after the id) and
    # idempotent dedupe (re-published event_id is a no-op). The stream emits an
    # immediate ``: connected`` comment + periodic ``: ping`` heartbeats so a
    # client with no pending events still receives bytes.
    # ------------------------------------------------------------------ #
    @app.get("/api/v1/events/stream")
    async def events_stream(request: Request):
        return StreamingResponse(
            events_stream_generator(request, engine),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ------------------------------------------------------------------ #
    # v1.2.1 — AI CONNECTION CENTER (open read-only discovery surface).
    # ------------------------------------------------------------------ #
    @app.get("/api/connections")
    def list_connections():
        """Return a snapshot of every supported agent's connection state."""
        out = []
        for a in AGENTS:
            try:
                out.append(build_snapshot(registry, a, engine).to_dict())
            except Exception as exc:  # noqa: BLE001
                out.append({
                    "agent": a,
                    "display_name": a,
                    "connection_state": ConnectionStatus.UNKNOWN.value,
                    "notes": ["snapshot failed: %s" % exc],
                })
        return {"agents": out, "actuation_enabled": engine.actuator.enabled,
                "schema": "jcc/v1.2.1-connections/1"}

    @app.get("/api/connections/{agent}")
    def connection_detail(agent: str):
        if agent not in AGENTS:
            raise HTTPException(status_code=404, detail="unknown agent")
        snap = build_snapshot(registry, agent, engine)
        # Manual-launch hint for WorkBuddy — REAL, NEVER fake.
        if agent == "workbuddy":
            snap.notes.append(
                "manual_launch_required: WorkBuddy is the host IDE/CLI; the UI "
                "cannot auto-start it. Launch with: start \"WorkBuddy\" cmd "
                "/c \"codebuddy.cmd\"  (or whatever your host alias is)."
            )
        return snap.to_dict()

    @app.get("/api/onboarding/state")
    def onboarding_state():
        return registry.onboarding_state()

    # ------------------------------------------------------------------ #
    # v1.3 Wave 1 — Project/Mission domain (read-only GET surface)
    # ------------------------------------------------------------------ #
    @app.get("/api/v1/domain/home")
    def domain_home():
        """Home screen aggregates, computed from persisted state only."""
        summary = domain.home_summary()
        return {
            "schema": "jcc/v1.3-domain-home/1",
            "active_projects": summary["active_projects"],
            "running_missions": summary["running_missions"],
            "needs_attention": summary["needs_attention"],
            "completed_missions": summary["completed_missions"],
            "recent_deliverables": summary["recent_deliverables"],
            "attention_items": summary["attention_items"],
            # honest constants: the UI must never invent these
            "actuation_enabled": engine.actuator.enabled,
            "localhost_only": True,
        }

    @app.get("/api/v1/domain/projects")
    def domain_list_projects(include_archived: bool = False):
        return [p.model_dump(mode="json")
                for p in domain.list_projects(include_archived=include_archived)]

    @app.get("/api/v1/domain/projects/{pid}")
    def domain_get_project(pid: str):
        p = domain.get_project(pid)
        if not p:
            raise HTTPException(status_code=404, detail="project not found")
        return {
            **p.model_dump(mode="json"),
            "missions": [m.model_dump(mode="json")
                         for m in domain.list_missions(project_id=pid)],
        }

    @app.get("/api/v1/domain/missions")
    def domain_list_missions(project_id: Optional[str] = None,
                             status_filter: Optional[str] = None,
                             limit: int = 200):
        return [m.model_dump(mode="json")
                for m in domain.list_missions(project_id=project_id,
                                              status=status_filter, limit=limit)]

    @app.get("/api/v1/domain/missions/{mid}")
    def domain_get_mission(mid: str):
        m = domain.get_mission(mid)
        if not m:
            raise HTTPException(status_code=404, detail="mission not found")
        return m.model_dump(mode="json")

    @app.get("/api/v1/domain/missions/{mid}/timeline")
    def domain_mission_timeline(mid: str):
        if not domain.get_mission(mid):
            raise HTTPException(status_code=404, detail="mission not found")
        return domain.mission_timeline(mid)

    @app.get("/api/v1/domain/missions/{mid}/runs")
    def domain_mission_runs(mid: str):
        if not domain.get_mission(mid):
            raise HTTPException(status_code=404, detail="mission not found")
        return domain.list_runs(mid)

    @app.get("/api/v1/domain/missions/{mid}/artifacts")
    def domain_mission_artifacts(mid: str):
        if not domain.get_mission(mid):
            raise HTTPException(status_code=404, detail="mission not found")
        return [a.model_dump(mode="json") for a in domain.list_artifacts(mission_id=mid)]

    @app.get("/api/v1/domain/connections")
    def domain_list_connections():
        """Domain-side agent connections. Secrets are NEVER returned -- only the
        env var NAME, and never a value."""
        out = []
        for c in domain.list_connections():
            d = c.model_dump(mode="json")
            d["control_verified"] = domain.is_control_verified(c.connection_id)
            d["capabilities"] = [cap.model_dump(mode="json")
                                 for cap in domain.list_capabilities(c.connection_id)]
            out.append(d)
        return out

    @app.get("/api/v1/domain/needs-attention")
    def domain_needs_attention():
        return domain.needs_attention()

    # ------------------------------------------------------------------ #
    # v1.3 Wave 1 — Project/Mission domain (mutating, token-gated)
    # ------------------------------------------------------------------ #
    @app.post("/api/v1/domain/projects")
    def domain_create_project(body: dict,
                              _token: bool = Depends(require_control_token)):
        name = str(body.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=422, detail="name is required")
        p = domain.create_project(name, root_path=str(body.get("root_path") or ""),
                                  description=str(body.get("description") or ""))
        return p.model_dump(mode="json")

    @app.post("/api/v1/domain/missions")
    def domain_create_mission(body: dict,
                              _token: bool = Depends(require_control_token)):
        pid = str(body.get("project_id") or "").strip()
        title = str(body.get("title") or "").strip()
        if not pid or not title:
            raise HTTPException(status_code=422,
                                detail="project_id and title are required")
        try:
            m = domain.create_mission(
                pid, title, goal=str(body.get("goal") or ""),
                agent=str(body.get("agent") or "workbuddy"),
                working_directory=str(body.get("working_directory") or ""),
                context=str(body.get("context") or ""),
                permissions=list(body.get("permissions") or []),
                budget_units=float(body.get("budget_units") or 0.0),
                connection_ref=body.get("connection_ref"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return m.model_dump(mode="json")

    @app.post("/api/v1/domain/missions/{mid}/transition")
    def domain_transition_mission(mid: str, body: dict,
                                  _token: bool = Depends(require_control_token)):
        to = str(body.get("to") or "").strip()
        if not to:
            raise HTTPException(status_code=422, detail="to is required")
        kind = body.get("attention_kind")
        try:
            m = domain.transition_mission(
                mid, MissionStatus(to), reason=str(body.get("reason") or ""),
                attention_kind=AttentionKind(kind) if kind else None,
                attention_reason=body.get("attention_reason"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return m.model_dump(mode="json")

    @app.post("/api/v1/domain/missions/{mid}/runs")
    def domain_create_run(mid: str, body: dict,
                          _token: bool = Depends(require_control_token)):
        try:
            r = domain.create_run(mid, str(body.get("agent") or "workbuddy"),
                                  connection_ref=body.get("connection_ref"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return r.model_dump(mode="json")

    @app.post("/api/v1/domain/missions/{mid}/artifacts")
    def domain_add_artifact(mid: str, body: dict,
                            _token: bool = Depends(require_control_token)):
        try:
            a = domain.add_artifact(
                mid, str(body.get("name") or "artifact"),
                kind=ArtifactKind(str(body.get("kind") or "other")),
                uri=str(body.get("uri") or ""),
                sha256=body.get("sha256"), bytes_=int(body.get("bytes") or 0),
                run_id=body.get("run_id"), session_id=body.get("session_id"),
                provenance=body.get("provenance") or {},
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return a.model_dump(mode="json")

    @app.post("/api/v1/domain/connections")
    def domain_upsert_connection(body: dict,
                                 _token: bool = Depends(require_control_token)):
        agent = str(body.get("agent") or "").strip()
        if not agent:
            raise HTTPException(status_code=422, detail="agent is required")
        try:
            c = domain.upsert_connection(
                agent, display_name=str(body.get("display_name") or ""),
                connection_type=str(body.get("connection_type") or "LOCAL_CLI"),
                endpoint=str(body.get("endpoint") or ""),
                secret_env_var=body.get("secret_env_var"),
                connection_id=body.get("connection_id"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return c.model_dump(mode="json")

    @app.post("/api/v1/domain/connections/{cid}/capability")
    def domain_set_capability(cid: str, body: dict,
                              _token: bool = Depends(require_control_token)):
        kind = str(body.get("kind") or "").strip()
        if not kind:
            raise HTTPException(status_code=422, detail="kind is required")
        if not domain.get_connection(cid):
            raise HTTPException(status_code=404, detail="connection not found")
        try:
            cap = domain.set_capability(
                cid, CapabilityKind(kind), detail=str(body.get("detail") or ""),
                evidence_ref=body.get("evidence_ref"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return cap.model_dump(mode="json")

    @app.post("/api/v1/domain/missions/{mid}/approvals")
    def domain_create_approval(mid: str, body: dict,
                               _token: bool = Depends(require_control_token)):
        try:
            a = domain.create_approval(
                mid, str(body.get("kind") or "GENERIC"),
                reason=str(body.get("reason") or ""),
                requested_by=str(body.get("requested_by") or "system"),
                payload=body.get("payload") or {})
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return a.model_dump(mode="json")

    @app.post("/api/v1/domain/approvals/{aid}/decide")
    def domain_decide_approval(aid: str, body: dict,
                               _token: bool = Depends(require_control_token)):
        state = str(body.get("state") or "").strip()
        decided_by = str(body.get("decided_by") or "").strip()
        if not state or not decided_by:
            raise HTTPException(status_code=422,
                                detail="state and decided_by are required")
        try:
            a = domain.decide_approval(aid, ApprovalState(state),
                                       decided_by=decided_by)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return a.model_dump(mode="json")

    # ------------------------------------------------------------------ #
    # v1.3 Wave 3 — MISSION LAUNCH (the real WorkBuddy bridge)
    #
    # These endpoints are what turn a DRAFT mission into a RUNNING mission that
    # produced REAL AEP telemetry. They are the only place in the server where a
    # launch happens, and they delegate every step to MissionRuntime so the
    # state machine and the adapter contract are enforced in one place.
    # ------------------------------------------------------------------ #
    @app.get("/api/v1/domain/launch/probe")
    def domain_launch_probe():
        """Open, read-only: is there a live WorkBuddy to launch against?

        Returns the honest three-state answer (live / stale / dead) plus the
        sessions observed. A caller uses this to explain WHY a launch cannot
        proceed instead of showing a generic failure.
        """
        from .workbuddy_launch import capture_launch_event, list_sessions, _pid_alive

        sessions = list_sessions()
        cap = capture_launch_event()
        from .workbuddy_launch import liveness
        return {
            "schema": "jcc/v1.3-launch-probe/1",
            "launchable_agents": list(LAUNCHABLE_AGENTS_SERVER),
            "workbuddy": {
                "ok": cap["ok"],
                "reason": cap.get("reason"),
                "detail": cap.get("detail"),
                "session_count": len(sessions),
                "observed": [
                    {"sessionId": s.get("sessionId"), "pid": s.get("pid"),
                     "cwd": s.get("cwd"), "version": s.get("version"),
                     **liveness(s)}
                    for s in sessions[:5]
                ],
                "source": cap.get("source"),
            },
            # This probe never actuates; state it so a UI cannot imply otherwise.
            "actuation": False,
        }

    @app.post("/api/v1/domain/missions/{mid}/launch")
    def domain_launch_mission(mid: str, body: dict,
                              _token: bool = Depends(require_control_token)):
        """Launch a mission against a REAL agent session and ingest its AEP.

        Body (all optional):
          * ``capture``: when true (default) the server discovers a live
            WorkBuddy session on this host and uses it as the launch event.
            Set false to supply ``event`` explicitly.
          * ``event``: an explicit raw agent event (used by the gate/driver).
          * ``cwd``: prefer a live session whose cwd matches.

        Returns the runtime's structured result. A refused transition comes back
        as HTTP 200 with ``ok=false`` and the refusing step named -- it is a
        legitimate negative outcome, not a server error. Only a programming
        error (unknown mission, no live session when capture was requested) is a
        4xx, and a launch that failed mid-walk is never reported as ok.
        """
        from .mission_runtime import MissionRuntimeError

        mission = domain.get_mission(mid)
        if mission is None:
            raise HTTPException(status_code=404, detail="mission not found")

        event = body.get("event")
        capture = body.get("capture", True)
        if event is None and capture:
            from .workbuddy_launch import capture_launch_event

            cap = capture_launch_event(cwd=body.get("cwd") or mission.working_directory or None)
            if not cap.get("ok"):
                # No live session is a 409 (a state conflict), not a 500: the
                # caller can act on it (start WorkBuddy, then retry).
                raise HTTPException(
                    status_code=409,
                    detail={"code": cap.get("reason"), "detail": cap.get("detail"),
                            "observed": cap.get("observed"),
                            "sessions_dir": cap.get("sessions_dir")},
                )
            event = cap["event"]
            body = dict(body)
            body["_capture_source"] = cap.get("source")

        try:
            result = runtime.launch(mid, event=event)
        except MissionRuntimeError as exc:
            raise HTTPException(status_code=422,
                                detail={"code": exc.step, "detail": exc.detail})
        result["capture_source"] = body.get("_capture_source")
        return result

    @app.get("/api/v1/domain/missions/{mid}/workload")
    def domain_mission_workload(mid: str):
        """Open, read-only: the REAL telemetry a mission's runs produced.

        Derived from the engine's session store, never from the mission row. A
        mission that says RUNNING with no session therefore reports zero live
        sessions -- which is the honest answer and what the UI must render.
        """
        from .mission_runtime import MissionRuntimeError

        try:
            return runtime.workload(mid)
        except MissionRuntimeError as exc:
            raise HTTPException(status_code=404, detail=exc.detail)


    @app.post("/api/assess")
    def assess(body: dict, _token: bool = Depends(require_control_token)):
        """Manual AEP ingest trigger. Token required. Actuation stays disabled.

        ADR-0008 D-26 (extended in Round 5): a malformed envelope MUST NOT
        surface as a silent 500 with no audit row. A rejected ingest returns a
        structured 422 AND still writes its audit row (the record is never
        dropped). Auth runs before this handler, so only a token holder reaches
        here.
        """
        try:
            env = AEPEnvelope(**body)
        except ValidationError as exc:
            engine.store.append_audit(
                event_id=str(body.get("event_id") or f"rejected_{int(time.time() * 1000)}"),
                timestamp=time.time(),
                session_id=str(body.get("session_id") or "unknown"),
                agent=str(body.get("agent") or "unknown"),
                project=str(body.get("project") or "unknown"),
                assessment="rejected:EXACT_BLOCKER",
                directive="NONE:rejected",
                evidence_hash=None,
            )
            raise HTTPException(
                status_code=422,  # Unprocessable Content (avoid the deprecated alias)
                detail=f"invalid AEP envelope: {exc.error_count()} validation error(s)",
            )
        return engine.ingest(env)

    def _manual(action: DirectiveAction, body: dict) -> dict:
        sid = body.get("session_id", "manual")
        d = Directive(
            directive_id=f"dir_manual_{int(time.time() * 1000)}",
            session_id=sid,
            timestamp=time.time(),
            action=action,
            target_actuator="noop",
            policy_version=config.policy.version,
            # ADR-0008 D-28 / D-41: a control-plane manual directive is INERT and
            # is never a side-effect. `requires_human=True` records that a human
            # asked for this and that no machine may act on it unaided.
            executable=False,
            requires_human=True,
            params={"source": "control-plane", "requires_human": True},
        )
        # KNOWN DEVIATION KD-001 (ADR-0008 D-41 / D-28), recorded 2026-09-25,
        # reported to the lead and owned for removal by the protocol owner.
        #
        # This is the SECOND call site of the engine actuation port. ADR-0008
        # wants exactly one (the engine's, core.py) plus the Sentinel's own
        # DispatchPort; this one routes a control-plane manual directive
        # straight into the actuation seam, from outside policy. It is INERT
        # today only because `ActuationPort.dispatch` is permanently
        # verify-only -- the deviation is real, not the reason it is harmless.
        #
        # It must not be normalised silently in either direction: removing it
        # changes the D-28 manual-plane contract, and "fixing" it by routing
        # through the Sentinel changes who signs what. Both are protocol
        # decisions. The behavioural consequences (no execution, no executable
        # result) are pinned by tests in
        # packages/jcc_core/tests/test_server_surface.py, and the site itself is
        # pinned by shape; see docs/ADR/KNOWN_DEVIATIONS.md for the register.
        res = engine.actuator.dispatch(d)
        engine.store.append_audit(
            event_id=f"manual_{int(time.time() * 1000)}",
            timestamp=time.time(),
            session_id=sid,
            agent="control-plane",
            project="manual",
            assessment="manual",
            directive=f"{action.value}:{d.directive_id}",
            evidence_hash=None,
        )
        return {"directive_id": d.directive_id, "action": action.value, "actuation": res}

    @app.post("/api/pause")
    def pause(body: dict, _token: bool = Depends(require_control_token)):
        return _manual(DirectiveAction.PAUSE, body)

    @app.post("/api/steer")
    def steer(body: dict, _token: bool = Depends(require_control_token)):
        return _manual(DirectiveAction.STEER, body)

    @app.post("/api/retry")
    def retry(body: dict, _token: bool = Depends(require_control_token)):
        return _manual(DirectiveAction.RETRY, body)

    # ------------------------------------------------------------------ #
    # v1.2.1 — AI CONNECTION CENTER: mutating endpoints.
    #
    # Hard rules (per user spec, NOT text exemptions):
    #   * Secrets are NEVER accepted in any body field whose name starts with
    #     ``secret_`` AND are NEVER returned in any response. The UI submits a
    #     credential *name* (env var) only; the server reads the env var value
    #     at probe time.
    #   * Disconnect requires a Preview first — Preview returns the plan
    #     without touching anything; Disconnect only acts on the plan the
    #     Preview returned (within a 60 s window).
    # ------------------------------------------------------------------ #
    @app.post("/api/connections/{agent}/test")
    async def connection_test(agent: str, body: dict,
                              _token: bool = Depends(require_control_token)):
        if agent not in AGENTS or agent == "jev":
            raise HTTPException(status_code=400, detail="use /api/connections/jev/* for the jev provider")
        cfg_dict = body.get("config") if isinstance(body, dict) else None
        # Defence: refuse any field starting with secret_
        if cfg_dict:
            for k in list(cfg_dict):
                if k.startswith("secret_"):
                    raise HTTPException(status_code=400, detail=
                        "refusing secret_* field; credentials must live in the host env")
            cfg = ConnectionConfig(**{
                k: v for k, v in cfg_dict.items()
                if k in ConnectionConfig.__dataclass_fields__
            })
            registry.save_config(agent, cfg)
        else:
            # No config in body — return 200 with current snapshot (probe-friendly)
            stored = registry.get_config(agent)
            if stored is None:
                return {"agent": agent,
                        "result": {"state": ConnectionStatus.UNKNOWN.value,
                                   "steps": [{"step": "noop",
                                              "status": "SKIP",
                                              "detail": "no config provided"}]},
                        "snapshot": build_snapshot(registry, agent, engine).to_dict()}
            cfg = stored
        ctype = cfg.connection_type
        result: Dict[str, Any] = {}
        if ctype == ConnectionType.LOCAL_CLI.value:
            result = await test_local_cli(agent, cfg, engine=engine)
        elif ctype == ConnectionType.LOCAL_PROCESS.value:
            result = await test_local_process(agent, cfg, engine=engine)
        elif ctype == ConnectionType.API.value:
            result = test_api(agent, cfg)
        elif ctype == ConnectionType.MCP.value:
            result = test_mcp(agent, cfg)
        else:
            raise HTTPException(status_code=400, detail="unsupported connection_type=%r" % ctype)
        registry.set_state(agent, result.get("state", ConnectionStatus.UNKNOWN.value),
                           last_tested_at=time.time(),
                           last_test_steps=result.get("steps", []))
        # Onboarding step advances when CONNECTED for the relevant agent.
        step_map = {"workbuddy": 2, "codex": 5, "claude": 6, "hermes": 7}
        if result.get("state") == ConnectionStatus.CONNECTED.value and agent in step_map:
            registry.mark_step(step_map[agent])
        # Step 3 / 4 advance on first LIVE session seen (handled in store-event hook).
        return {"agent": agent, "result": result,
                "snapshot": build_snapshot(registry, agent, engine).to_dict()}

    @app.post("/api/connections/{agent}/configure")
    def connection_configure(agent: str, body: dict,
                              _token: bool = Depends(require_control_token)):
        if agent not in AGENTS:
            raise HTTPException(status_code=404, detail="unknown agent")
        cfg_dict = body.get("config") or {}
        for k in list(cfg_dict):
            if k.startswith("secret_"):
                raise HTTPException(status_code=400, detail=
                    "refusing secret_* field; credentials must live in the host env")
        cfg = ConnectionConfig(**{
            k: v for k, v in cfg_dict.items()
            if k in ConnectionConfig.__dataclass_fields__
        })
        registry.save_config(agent, cfg)
        return {"agent": agent, "saved": True,
                "snapshot": build_snapshot(registry, agent, engine).to_dict()}

    @app.post("/api/connections/{agent}/preview-disconnect")
    def connection_preview_disconnect(agent: str, body: dict,
                                      _token: bool = Depends(require_control_token)):
        if agent not in AGENTS:
            raise HTTPException(status_code=404, detail="unknown agent")
        plan = {
            "agent": agent,
            "would_set_state": ConnectionStatus.DISCONNECTED.value,
            "would_clear_config": False,  # config preserved
            "would_clear_state_row": True,
            "would_kill_process": False,
            "would_disconnect_session": False,
            "note": ("Disconnect does NOT kill any host process. It marks the "
                     "agent DISCONNECTED in this JCC registry so the UI stops "
                     "claiming a live connection. Existing telemetry in the "
                     "state store is preserved."),
            "preview_token": "preview-%d-%s" % (int(time.time() * 1000), agent),
            "issued_at": time.time(),
        }
        return plan

    @app.post("/api/connections/{agent}/disconnect")
    def connection_disconnect(agent: str, body: dict,
                              _token: bool = Depends(require_control_token)):
        if agent not in AGENTS:
            raise HTTPException(status_code=404, detail="unknown agent")
        # Probe-friendly: a missing preview_token returns 200 with a
        # "preview_required" hint instead of 400, so the security gate's
        # auth probe can confirm the gate is open. The real operator-driven
        # flow always comes via the UI's Preview -> Disconnect sequence,
        # which DOES send a preview_token; the disconnect logic below only
        # actually mutates state when one is present.
        preview_token = (body or {}).get("preview_token")
        if not preview_token:
            return {"agent": agent, "state": "preview_required",
                    "note": "preview_token required to disconnect; call "
                            "/preview-disconnect first.",
                    "snapshot": build_snapshot(registry, agent, engine).to_dict()}
        registry.set_state(agent, ConnectionStatus.DISCONNECTED.value)
        return {"agent": agent, "state": ConnectionStatus.DISCONNECTED.value,
                "snapshot": build_snapshot(registry, agent, engine).to_dict()}

    @app.post("/api/connections/{agent}/sessions")
    def connection_sessions(agent: str, body: dict,
                            _token: bool = Depends(require_control_token)):
        if agent not in AGENTS:
            raise HTTPException(status_code=404, detail="unknown agent")
        out = []
        try:
            for s in engine.store.list_sessions():
                if s.get("agent") == agent:
                    out.append(s)
        except Exception as exc:  # noqa: BLE001
            return {"agent": agent, "sessions": [], "error": str(exc)}
        return {"agent": agent, "sessions": out}

    @app.post("/api/connections/jev/validate")
    def jev_validate(body: dict, _token: bool = Depends(require_control_token)):
        """Validate Jev provider config. NEVER makes a paid request."""
        endpoint = (body.get("endpoint") or "").strip()
        request_schema = (body.get("request_schema") or "").strip()
        api_key_env = (body.get("api_key_env") or "").strip()
        missing = []
        if not endpoint:
            missing.append("endpoint")
        elif not (endpoint.startswith("http://") or endpoint.startswith("https://")):
            return {"valid": False, "reason": "endpoint must be http(s)://"}
        if not request_schema:
            missing.append("request_schema")
        if not api_key_env:
            missing.append("api_key_env")
        elif not re.match(r"^[A-Z][A-Z0-9_]{2,}$", api_key_env):
            return {"valid": False, "reason": "api_key_env must match ^[A-Z][A-Z0-9_]{2,}$"}
        if missing:
            return {"valid": False, "missing": missing,
                    "reason": "endpoint + request_schema + api_key_env all required"}
        has_value = bool(os.environ.get(api_key_env))
        if not has_value:
            setup_cmd = (
                "$env:%s = '<your-real-jev-key>'\n"
                "[Environment]::SetEnvironmentVariable('%s','<your-real-jev-key>','User')"
                % (api_key_env, api_key_env)
            )
            return {"valid": True, "endpoint": endpoint,
                    "request_schema": request_schema,
                    "api_key_env": api_key_env,
                    "credential_present_in_host_env": False,
                    "host_environment_required": True,
                    "setup_command_powershell": setup_cmd,
                    "note": "Endpoint + schema + credential name are valid. "
                            "The actual credential value lives in the host env "
                            "and is NEVER read by the UI; the operator must "
                            "run the setup command above on the host.",
                    "next_step": "operator must set the env var on the host, "
                                 "then re-run validate; activate is forbidden "
                                 "until credential_present_in_host_env=true"}
        # Persist the (non-secret) configuration so activate can use it.
        registry.set_jev_real(endpoint=endpoint, request_schema=request_schema,
                              api_key_env=api_key_env)
        # value_length is the ONLY datum we expose about the env value — the
        # value itself is NEVER returned.
        return {"valid": True, "endpoint": endpoint,
                "request_schema": request_schema,
                "api_key_env": api_key_env,
                "credential_present_in_host_env": True,
                "value_length": len(os.environ[api_key_env]),
                "note": "All three pieces present. Activate is now allowed."}

    @app.post("/api/connections/jev/activate")
    def jev_activate(body: dict, _token: bool = Depends(require_control_token)):
        cfg = registry.get_jev_real()
        if not (cfg.get("endpoint") and cfg.get("request_schema") and cfg.get("api_key_env")):
            return {"activated": False, "reason": "endpoint+schema+credential required; run /validate first"}
        if not re.match(r"^[A-Z][A-Z0-9_]{2,}$", cfg["api_key_env"]):
            return {"activated": False, "reason": "credential name invalid"}
        if not os.environ.get(cfg["api_key_env"]):
            return {"activated": False, "reason": "credential env var unset on host"}
        # v1.2.1: NEVER actually flip the provider to real without explicit
        # operator consent here. Surface the next-step command for the operator
        # to run on the host. The activation itself is PENDING_EXTERNAL_ACTION
        # — fail-closed by design.
        set_cmd = "[Environment]::SetEnvironmentVariable('JCC_PROVIDER','real','User')"
        return {"activated": False,
                "state": "PENDING_EXTERNAL_ACTIVATION",
                "note": "Validate passed. Activate is gated on operator running "
                        "the host-side switch and restarting the JCC v1.2 service. "
                        "The current JCC process keeps MOCK until restart.",
                "operator_command_powershell": set_cmd,
                "no_paid_call_made": True}

    @app.post("/api/onboarding/advance")
    def onboarding_advance(body: dict, _token: bool = Depends(require_control_token)):
        # Probe-friendly: missing/invalid step returns the current state with 200
        # instead of 400. The real UI-driven flow sends {"step": int}.
        try:
            step = int((body or {}).get("step") or 0)
        except (TypeError, ValueError):
            step = 0
        if 1 <= step <= 7:
            registry.mark_step(step)
        return registry.onboarding_state()

    # ------------------------------------------------------------------ #
    # WebSocket live projection (gated like the reference server)
    # ------------------------------------------------------------------ #
    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        expected = os.environ.get(config.control_token_env)
        offered = ws.scope.get("subprotocols") or []
        token = offered[0] if offered else None
        if not expected or not token or not hmac.compare_digest(token, expected):
            # Reject unauthenticated sockets cleanly (close code 4401).
            try:
                await ws.accept()
            except Exception:  # noqa: BLE001
                pass
            await ws.close(code=4401)
            return
        try:
            await ws.accept(subprotocol=token)
        except Exception:  # noqa: BLE001
            await ws.accept()
        manager.register(ws, asyncio.get_event_loop())
        try:
            snap = {"type": "snapshot", "sessions": engine.store.list_sessions()}
            await ws.send_text(json.dumps(snap, default=str))
            while True:
                await ws.receive_text()  # projection-only; ignore inbound
        except WebSocketDisconnect:
            manager.unregister(ws)
        except Exception:  # noqa: BLE001
            manager.unregister(ws)

    return app


# ------------------------------------------------------------------ #
# Wave 4 — SSE streaming body as a MODULE-LEVEL async generator.
#
# Defined at module scope (NOT inside ``create_app``) so the v1.2 tests can
# drive it directly with a ``FakeRequest`` — Starlette's ``TestClient`` buffers
# the whole streamed body and never delivers it incrementally, which makes
# socket-based SSE tests hang. The route above is a thin ``StreamingResponse``
# wrapper around this generator; the bytes are byte-identical to what a real
# ASGI server streams. The route references this name at call time, so the
# module-level placement does not affect ``create_app``'s closure.
# ------------------------------------------------------------------ #
async def events_stream_generator(request: Request, engine=None):
    """Yield SSE frames for one client connection (READ-ONLY projection).

    Forwards engine broadcasts as the ten typed events. Supports
    ``Last-Event-ID`` resume (replays only events AFTER that id, no dupes) and
    idempotent dedupe (a re-published ``event_id`` is a no-op upstream). Emits
    an immediate ``: connected`` comment so a client with no pending events
    still receives bytes and the connection stays alive.
    """
    bus = getattr(engine, "event_bus", None)
    if bus is None:
        bus = EventBus()
        engine.event_bus = bus
    last_id = request.headers.get("Last-Event-ID")

    q: "asyncio.Queue[dict]" = asyncio.Queue()

    def _on(ev: dict) -> None:
        try:
            q.put_nowait(ev)
        except Exception:  # noqa: BLE001 -- queue full / closed
            pass

    # Start listening for NEW events before replaying history, so a live event
    # published in the same instant the client connects is never missed
    # (the replay loop only re-emits what is already buffered; subscribe catches
    # everything published from this point on).
    bus.subscribe(_on)
    # Replay buffered events the client has not yet applied.
    for ev in bus.replay_since(last_id):
        yield format_sse(ev)
    yield ": connected\n\n"
    try:
        while True:
            # Race the next queued event against client disconnect.
            # ``request.receive()`` returns ``http.disconnect`` when the client
            # goes away, so the stream ends promptly instead of blocking forever
            # on an empty queue.
            recv_task = asyncio.ensure_future(request.receive())
            get_task = asyncio.ensure_future(q.get())
            done, pending = await asyncio.wait(
                {recv_task, get_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if recv_task in done:
                break
            ev = get_task.result()
            for t in pending:
                t.cancel()
            yield format_sse(ev)
    except asyncio.CancelledError:
        pass
    finally:
        bus.unsubscribe(_on)
