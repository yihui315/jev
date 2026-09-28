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
import time
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from .core import CoreEngine
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
)


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
        version="p1.0.0",
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
    # Mutating (token-gated) endpoints
    # ------------------------------------------------------------------ #
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
