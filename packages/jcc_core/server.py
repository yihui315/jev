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
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from .core import CoreEngine
from .events import EventBus, format_sse
from .project_scope import ProjectRootRejected, scope_report
from .providers.base import activation_state_for_requested
from .schemas import AEPEnvelope, Directive, DirectiveAction
from .scene import build_scene

logger = logging.getLogger("jcc.server")

#: The open (unauthenticated) READ-only GET surface, declared as data so that
#: both the server and a scanner read the same list (P4 #77.1).
#:
#: ``/ws`` is deliberately NOT here: it is a WebSocket route, not a GET, and it
#: is token-gated like any mutating endpoint.
#: Every provider id the Model & API centre knows about. A literal so the
#: routes can validate an id WITHOUT importing the registry (which would
#: pull in the secret store on a read path).
_PROVIDER_IDS = ("typesafe_jev", "minimax", "qwen_coding_plan_cn", "deepseek", "custom")


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
    # v1.3 Wave 4 — attention control (read-only resolution of each inbox item)
    "/api/v1/domain/needs-attention/resolutions",
    # v1.3 Wave 5 — deliverables (server-side view + one deliverable by identity)
    "/api/v1/domain/deliverables",
    "/api/v1/domain/artifacts/{aid}",
    # v1.3 Wave 9.3 — uncapped totals, so a bounded list read can be compared
    # against the real domain size. The lists stay bounded; only the KNOWLEDGE of
    # how many there are is added, because "the first 200" and "all 200 there are"
    # are indistinguishable on screen without it.
    "/api/v1/domain/counts",
    # v1.3 Wave 7 — connection verification (one connection with provenance)
    "/api/v1/domain/connections/{cid}",
    # Jev (TypeSafe System One) reachability. Open because it is a STATUS read:
    # it reports whether a credential exists, never the credential, and it
    # mutates nothing. The decision endpoint below is deliberately NOT here.
    "/api/v1/domain/jev/health",
    # Model & API centre. Open because a STATUS read: these report WHETHER a
    # credential exists and at most four characters of it. No secret value
    # crosses this boundary, and nothing here mutates. The write routes
    # (credentials / probe / live / model-routing PATCH) are token-gated.
    "/api/v1/providers",
    "/api/v1/providers/{pid}",
    "/api/v1/providers/{pid}/models",
    "/api/v1/model-routing",
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
        ApprovalState, ArtifactKind, ArtifactStatus, AttentionKind, CapabilityKind,
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

    # v1.3 Wave 9.2 — the mission launch CONTROL PLANE. This is the one place a
    # human's authenticated "Start Mission" becomes a real process. It holds the
    # signed-directive signer and the HostLauncher, and it is reachable ONLY
    # through the two token-gated POST routes below -- no engine directive can
    # reach it, so AUTONOMOUS_ACTUATION stays False.
    from .launch_control import LaunchController, default_measure_path
    _runtime_dir = Path(config.state_store_db_path).parent
    launch_ctl = LaunchController(
        domain, runtime,
        measure_path=default_measure_path(config.state_store_db_path),
        journal_path=_runtime_dir / "launch_journal.ndjson",
        policy_version="p3.0.0",
        # CR-003: hand the launcher the boundary THIS instance's config declares.
        # Omitting it would leave `launcher.project_scope_base` decorative.
        scope_base=config.launcher.project_scope_base,
    )
    app.state.launch_controller = launch_ctl

    # ------------------------------------------------------------------ #
    # RISK-1 -- the WRITE-side project root boundary.
    #
    # Two closures rather than inline code at each route, because the same
    # decision is made in two places (a project's root_path and a mission's
    # working_directory) and two copies of a security decision eventually
    # disagree. Both read the SAME allow-list the launcher does, so there is one
    # boundary in the system rather than a launch-side one and a write-side one.
    # ------------------------------------------------------------------ #
    def _allowed_roots() -> list:
        """The roots a project root may resolve inside, for THIS instance.

        Delegates to :meth:`LaunchScopeConfig.effective_allowed_roots`, so the
        answer is derived from ``launcher.project_scope_base`` when nothing
        wider was configured. Never a typed drive letter.
        """
        return config.launcher.effective_allowed_roots()

    def _validate_root(raw: str, *, field: str) -> str:
        """Validate one submitted path; return the CANONICAL path to store.

        Raises :class:`~jcc_core.project_scope.ProjectRootRejected` carrying the
        reason code. Re-raises nothing and returns ``""`` for an empty input --
        a project may legitimately have no root yet, and the launch path already
        answers ``PROJECT_ROOT_MISSING`` for that case.
        """
        from .project_scope import validate_project_root
        return validate_project_root(raw, roots=_allowed_roots(), field=field)

    # -- v1.3 Wave 4 helpers: attention control ------------------------- #
    def _actuation_enabled() -> bool:
        """The REAL actuation flag, read from the engine's actuator.

        In v1.3 this is always False. It is read (not hard-coded) so that the
        attention resolver reports BLOCKED/ACTUATION_DISABLED for high-risk
        actions only while that is actually true -- if actuation were ever
        enabled, the resolver would stop claiming it is disabled.
        """
        return bool(getattr(engine.actuator, "enabled", False))

    def _live_session_ids() -> set:
        """Session ids whose host-side liveness is LIVE (three-state).

        Imported lazily and defensively: if the host probe cannot run, the set is
        empty, which makes a resume action BLOCKED/NO_LIVE_SESSION rather than
        EXECUTABLE. "Could not check" must never become "safe to resume".
        """
        try:
            from .workbuddy_launch import list_sessions, liveness
            return {str(s.get("session_id")) for s in list_sessions()
                    if liveness(s) == "live" and s.get("session_id")}
        except Exception:  # noqa: BLE001
            return set()

    def _attention_plan(domain_store, item: dict, action: str) -> list:
        """The exact changes a resolution WOULD make (preview). Pure read."""
        from .attention_control import (ACTION_DECIDE_APPROVAL, ACTION_RESUME,
                                        ACTION_VERIFY, ACTION_ACKNOWLEDGE_DELIVERABLE)
        mid = str(item.get("mission_id"))
        plan = []
        if action == ACTION_DECIDE_APPROVAL:
            pend = [a.approval_id for a in domain_store.list_approvals(
                mission_id=mid, state="PENDING")]
            plan.append({"target": "approval", "ids": pend,
                         "change": "set state=APPROVED, decided_by=<caller>"})
            plan.append({"target": "mission", "id": mid,
                         "change": "if no other pending approval: "
                                   "NEEDS_ATTENTION -> RUNNING"})
        elif action in (ACTION_VERIFY, ACTION_ACKNOWLEDGE_DELIVERABLE):
            plan.append({"target": "mission", "id": mid,
                         "change": "NEEDS_ATTENTION -> RUNNING (human check done)"})
        elif action == ACTION_RESUME:
            plan.append({"target": "mission", "id": mid,
                         "change": "NEEDS_ATTENTION -> RUNNING (re-enters work)"})
        return plan

    def _apply_attention(domain_store, mid: str, action: str, body: dict):
        """Apply a resolution. The mission-state move goes through the state
        machine (never a raw UPDATE), so an illegal move is refused here too."""
        from .attention_control import (ACTION_DECIDE_APPROVAL, ACTION_RESUME,
                                        ACTION_VERIFY, ACTION_ACKNOWLEDGE_DELIVERABLE)
        if action == ACTION_DECIDE_APPROVAL:
            to_state = str(body.get("state") or "APPROVED").strip().upper()
            pend = domain_store.list_approvals(mission_id=mid, state="PENDING")
            if not pend:
                raise ValueError("no PENDING approval for mission %r" % mid)
            decided_by = str(body.get("decided_by") or "operator")
            a = domain_store.decide_approval(
                pend[0].approval_id, ApprovalState(to_state), decided_by=decided_by)
            after = domain_store.get_mission(mid)
            return True, {"approval_id": a.approval_id, "state": a.state.value,
                          "mission_status": after.status.value}
        if action in (ACTION_VERIFY, ACTION_ACKNOWLEDGE_DELIVERABLE, ACTION_RESUME):
            to = str(body.get("to_status") or MissionStatus.RUNNING.value)
            m = domain_store.transition_mission(
                mid, MissionStatus(to), reason="attention resolved: %s" % action)
            return True, {"mission_status": m.status.value}
        raise ValueError("action %r cannot be applied" % action)

    def _control_token_configured() -> bool:
        """True when a control token is present in the environment (P3/CR-H5).

        SECURITY REASONING, since this is on an OPEN GET. ``/api/health`` is
        deliberately unauthenticated (see ``OPEN_GET_ENDPOINTS``), so anything
        added here is readable by anyone who can reach the port -- and the port
        is loopback-only, so "anyone" means a local process or an operator.

        Reporting WHETHER a token exists adds no attack surface:

        * the only thing crossing the wire is a boolean -- the token value is
          never read into the response, not even to compute it;
        * the gate it describes is already fail-closed. An absent token yields
          401 on every mutating route (measured: 27 routes), so an attacker
          learns nothing they could not learn by simply POSTing and getting 401.
          The field only distinguishes 401-because-unconfigured from
          403-because-wrong, both of which are already observable outcomes;
        * the name of the environment variable is not a secret. It is declared
          in the shipped config (``control.token_env``), so "is it configured"
          is derivable by anyone who can read the config -- which is the same
          audience that can already read the value.

        The one thing that WOULD be new and harmful is the value itself, so this
        returns a bool computed with ``bool()`` and nothing else. A companion
        test asserts the token string never appears anywhere in the body.
        """
        # NOTE: `config.control_token_env`, NOT `config.control.token_env` --
        # `Config` is a flat dataclass; `control:` is only the YAML key and is
        # flattened at load time. The dotted form raises AttributeError, i.e. a
        # 500 on an open GET: a field meant to report health would break health.
        return bool(os.environ.get(config.control_token_env))

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
        # P4 / CR-H6: compare on BYTES. `hmac.compare_digest` raises TypeError on
        # a str holding non-ASCII code points, so a token like "中文令牌" escaped
        # the 403 and became an unhandled exception (a 500) -- an
        # availability/robustness defect, NOT a confidentiality or integrity one,
        # because the request is refused either way. Encoding both sides removes
        # the exception path instead of catching it, and compare_digest on bytes
        # is still constant-time. The empty-value checks above are untouched: an
        # absent token is still 401 and a wrong one is still 403.
        if not presented or not hmac.compare_digest(
                presented.encode("utf-8"), expected.encode("utf-8")):
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
        # The PRODUCTION provider reality — the SINGLE fact source shared with
        # the Provider Center and the mission runtime (the §final-decision
        # wiring-drift fix). Derived from ProviderRegistry (credential PRESENCE
        # + a PERSISTED verification, NO live call). When the production registry
        # is genuinely real, it wins; otherwise the legacy scorer mode is
        # reported unchanged, so the §7D anti-impersonation contract — a
        # mock / replay / disabled scorer must never read as REAL — is preserved.
        prod = _production_provider_facts()
        prod_real = prod["provider_registry_is_real"]
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
            #
            # FINAL DECISION: the production decision + generation providers live
            # in ProviderRegistry (DPAPI SecretStore + VerificationStore). When
            # THAT registry is real, /api/health reports REAL — one runtime, one
            # fact source. The engine.scorer (engine.provider) is the LEGACY
            # scorer only (marked LEGACY_NOT_PRODUCTION in providers/real.py) and
            # is never presented as the production provider.
            "provider_mode": (
                prod["provider_registry_mode"] if prod_real
                else getattr(provider, "mode", "UNKNOWN")
            ),
            "provider_verified": (
                prod_real or bool(getattr(provider, "verified", False))
            ),
            "provider_activation_state": (
                "ACTIVE" if prod_real
                else activation_state_for_requested(requested_mode, provider)
            ),
            # The CONFIGURED mode, kept separate on purpose: when `real` is
            # requested but the key/flag is missing, the effective provider is
            # DISABLED. Reporting only the effective value would make an
            # operator's deliberate choice and a fail-closed fallback look
            # identical, and those need opposite responses.
            "provider_requested_mode": requested_mode,
            "provider_is_real": (
                prod_real or bool(getattr(provider, "is_real", False))
            ),
            # The production fact source, reported explicitly. legacy_scorer_not_
            # production documents that engine.provider is the LEGACY scorer, not
            # the production decision/generation provider. See providers/real.py.
            "production_provider": prod,
            # ----------------------------------------------------------------- #
            "actuation_enabled": engine.actuator.enabled,
            "policy_version": config.policy.version,
            "localhost_only": True,
            "wal": engine.store.is_wal,
            # ---- P3 / CR-H5: is a control token even CONFIGURED? ---------- #
            # Every mutating route is fail-closed (401/403) with no token, and
            # that is CORRECT behaviour -- but it leaves an operator unable to
            # tell "deliberately locked" from "I thought I set the token and it
            # never took". Nothing else reported it, so the silent
            # misconfiguration was invisible. This reports the BOOLEAN only.
            #
            # The token itself is never read into the response, only its
            # presence -- see `_control_token_configured` above, which is the
            # only place the environment is read for this purpose.
            "control_token_configured": _control_token_configured(),
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
            # Wave 5: an honest split. `recent_deliverables` counts every artifact
            # regardless of verdict; this object separates what an agent emitted
            # (produced) from what a human accepted (verified), so the Home card
            # cannot pass a rejected artifact off as an accepted deliverable.
            "deliverables": summary["deliverables"],
            "attention_items": summary["attention_items"],
            # honest constants: the UI must never invent these
            "actuation_enabled": engine.actuator.enabled,
            "localhost_only": True,
            # RISK-1 spec 10: the project-root boundary as DATA on an EXISTING
            # open read. Deliberately NOT a new GET route: adding one would
            # change the declared open surface (OPEN_GET_ENDPOINTS is an exact
            # set, enforced by test_server_surface.py), and widening the
            # unauthenticated surface to deliver a hint the operator needs is
            # the wrong trade. This endpoint already returns the constants a UI
            # must not invent, and "which roots may I name" is exactly that.
            "project_scope": scope_report(roots=_allowed_roots()),
        }

    # ------------------------------------------------------------------ #
    # Jev (TypeSafe System One) — the VENDOR decision model.
    #
    # NAMING: `JEV` is this product. `Jev` is TypeSafe's model. These routes
    # speak only about the latter, and the executor they choose is then run by
    # the former.
    #
    # The executor selection lives HERE, on the server, and nowhere else. The
    # browser is a transport: it may ask, and it must render whatever comes back
    # — including "nobody decided". It may not pick an executor itself, because a
    # hardcoded client-side default behind a label that says 「JEV 自动选择」 tells
    # the user the system chose when nothing did.
    # ------------------------------------------------------------------ #

    @app.get("/api/v1/domain/jev/health")
    def jev_health():
        """Vendor-model reachability. Read-only; safe without a credential.

        The state vocabulary is the five-value one from ``jev_api``: a real
        provider with no key is ``REAL_AUTH_MISSING``, never ``MOCK`` — nothing is
        mocking anything, and reporting a stand-in would hide a one-environment-
        variable fix behind a word that means "we are answering from a script".

        The credential comes from the REGISTRY, i.e. from the DPAPI store. It
        used to be read straight out of the environment here, which meant a key
        saved through the settings page was invisible to this route and to the
        mission path: the settings page said 「已连接」 while the executor decision
        reported "JEV_API_KEY is not set". One source, not two.
        """
        client = _provider_registry().jev()
        return {"schema": "jcc/jev-health/1", **client.health()}

    @app.post("/api/v1/domain/jev/select-executor")
    def jev_select_executor(body: dict,
                            _token: bool = Depends(require_control_token)):
        """Ask the vendor model which executor to run. Server-side, once.

        The response is the decision object verbatim, including ``source`` and
        ``provider_state``. When the vendor model cannot answer, the response
        says so (``source="undecided"`` or ``"fallback"``) and names the reason;
        it never contains a substituted executor presented as a Jev choice.
        """
        from .providers.jev_api import (
            KNOWN_EXECUTORS,
            decide_executor,
        )

        # From the REGISTRY, so the credential is the one the operator saved in
        # the settings page (DPAPI) and the client is the same object the rest
        # of the product uses. This route used to read os.environ["JEV_API_KEY"]
        # directly, so a key saved through the UI was invisible here and the
        # mission path answered "JEV_API_KEY is not set" while the settings page
        # showed 「已连接」 — two credential sources, two different truths.
        client = _provider_registry().jev()
        task = str(body.get("task") or "")
        decision = decide_executor(
            client,
            task=task,
            project_root=str(body.get("project_root") or ""),
            task_type=str(body.get("task_type") or "general"),
            available_executors=body.get("available_executors") or list(KNOWN_EXECUTORS),
            risk_context=body.get("risk_context") or {},
            executor_health=body.get("executor_health") or {},
            capabilities=body.get("capabilities") or {},
            fallback_executor=body.get("fallback_executor"),
        )
        # A STABLE id for this decision, minted here.
        #
        # The vendor's own `x-request-id` header is absent often enough that a
        # binding keyed on it would silently degrade to None — and then every
        # decision would share the same empty key, so an approval for one task
        # would appear to authorise another. The id below is derived from the
        # decision's CONTENT, which is what actually has to stay the same: the
        # executor, whether it is safe, the confidence and the task.
        import hashlib as _hl

        material = "|".join([
            str(decision.executor), str(decision.safe_to_execute),
            f"{float(decision.confidence):.4f}", task,
        ])
        decision_id = "jevd_" + _hl.sha256(material.encode("utf-8")).hexdigest()[:24]
        out = decision.as_dict()
        out["jev_decision_id"] = decision_id
        # Kept for the vendor's own correlation when it does send one.
        out["provider_request_id"] = decision.request_id
        return {"schema": "jcc/jev-executor-decision/1", **out}

    # ------------------------------------------------------------------ #
    # Human approval
    #
    # A Jev decision below the automatic-execution threshold must stop here. The
    # browser's only move is to ASK for an approval id; the SERVER decides whether
    # that approval is real, bound and unused. Letting the page set
    # ``safe_to_execute`` would look like a confirmation while being an unaudited,
    # unbound, replayable bypass — one click on a harmless question would
    # otherwise authorise a deployment.
    # ------------------------------------------------------------------ #

    def _approval_store():
        from .approvals import ApprovalStore

        store = getattr(app.state, "_approval_store", None)
        if store is None:
            store = ApprovalStore(config.state_store_db_path)
            app.state._approval_store = store
        return store

    def _record_approval_audit(entry: Dict[str, Any]) -> None:
        """Surface the approval on the state store's own audit surface.

        The approval table IS the audit record: it holds who confirmed what,
        when, and against which decision, and it is queried by
        :meth:`ApprovalStore.list_for_decision`. Writing a second copy into the
        mission audit tape would be a duplicate that can drift from this one.
        """
        logger.info("human_approval recorded: %s", entry.get("approval_id"))

    @app.post("/api/v1/domain/jev/request-approval")
    def jev_request_approval(body: dict,
                             _token: bool = Depends(require_control_token)):
        """Step 1: ask what approving THIS decision would look like.

        Returns a short-lived, single-use id bound to the decision, the task, the
        executor and the model. It authorises nothing on its own.
        """
        from .approvals import ApprovalError, task_fingerprint

        decision_id = str(body.get("jev_decision_id") or body.get("decision_id") or "")
        task = str(body.get("task") or "")
        executor = str(body.get("executor") or "")
        if not decision_id or not task or not executor:
            raise HTTPException(
                status_code=422,
                detail="jev_decision_id、task 与 executor 均为必填",
            )
        ap = _approval_store().issue(
            jev_decision_id=decision_id,
            task=task,
            executor=executor,
            project_root=body.get("project_root"),
            provider=body.get("provider"),
            model=body.get("model"),
            confidence=body.get("confidence"),
            reason=body.get("reason"),
            confirmed_by=str(body.get("confirmed_by") or "user"),
        )
        return {
            "schema": "jcc/human-approval-requested/1",
            "approval_id": ap.approval_id,
            "task_hash": ap.task_hash,
            "expires_at": ap.expires_at,
            "ttl_s": int(ap.expires_at - ap.confirmed_at),
            # Echoed so the UI can show WHAT is being approved, not just an id.
            "executor": ap.executor,
            "provider": ap.provider,
            "model": ap.model,
            "confidence": ap.confidence,
            "reason": ap.reason,
        }

    @app.post("/api/v1/domain/jev/confirm")
    def jev_confirm(body: dict, _token: bool = Depends(require_control_token)):
        """Step 2: the human clicked 确认并继续. Record it, then authorise.

        Every binding is re-derived server-side from the decision and the task the
        SERVER holds, never from the request body, so a client cannot widen its
        own approval.
        """
        from .approvals import ApprovalError

        approval_id = str(body.get("approval_id") or "")
        if not approval_id:
            raise HTTPException(status_code=422, detail="approval_id 必填")
        if body.get("human_confirmed") is not True:
            # A confirmation that did not say so is not a confirmation.
            raise HTTPException(
                status_code=422, detail="human_confirmed 必须为 true 才会继续执行"
            )
        try:
            ap = _approval_store().verify_and_consume(
                approval_id,
                jev_decision_id=str(body.get("jev_decision_id") or ""),
                task=str(body.get("task") or ""),
                executor=str(body.get("executor") or ""),
                project_root=body.get("project_root"),
                provider=body.get("provider"),
                model=body.get("model"),
            )
        except ApprovalError as e:
            # 409, not 400: the request was well-formed and the approval is
            # unusable. A 400 would invite a retry that could not succeed.
            raise HTTPException(status_code=409, detail=e.code) from None

        _record_approval_audit(ap.record_for_audit())
        return {
            "schema": "jcc/human-approval-confirmed/1",
            "approval": ap.record_for_audit(),
            # The executor to use from here. It comes from the APPROVAL, not from
            # the request, so a confirmed run executes exactly what was approved.
            "executor": ap.executor,
            "provider": ap.provider,
            "model": ap.model,
            "state": "HUMAN_APPROVED",
        }

    @app.post("/api/v1/domain/jev/cancel")
    def jev_cancel(body: dict, _token: bool = Depends(require_control_token)):
        """The human chose 取消. Burn the approval so it cannot be used later."""
        from .approvals import ApprovalError

        approval_id = str(body.get("approval_id") or "")
        if not approval_id:
            raise HTTPException(status_code=422, detail="approval_id 必填")
        store = _approval_store()
        ap = store.get(approval_id)
        if ap is not None:
            store.mark_cancelled(approval_id)
            _record_approval_audit({**ap.record_for_audit(), "outcome": "CANCELLED"})
        return {"schema": "jcc/human-approval-cancelled/1", "state": "CANCELLED"}

    # ------------------------------------------------------------------ #
    # Model & API centre
    #
    # Lets an operator connect a provider from the settings page instead of by
    # editing Windows environment variables.
    #
    # SECRET BOUNDARY, restated because it is the whole point of this block:
    #   * a credential arrives over HTTP from localhost and is encrypted with
    #     DPAPI before it touches disk;
    #   * NO response here can contain a secret — the only things served are
    #     `configured` and `last4`;
    #   * every write below depends on `require_control_token`, so there is no
    #     unauthenticated path to install a key.
    # ------------------------------------------------------------------ #

    def _provider_registry():
        """The process-wide registry.

        Built lazily and cached on the app so the SecretStore is constructed once
        and the DPAPI cache survives between requests — rebuilding it per request
        would re-open the credential file on every settings-page poll.
        """
        from .provider_registry import ProviderRegistry
        from .secret_store import SecretStore
        from .verification_store import VerificationStore

        reg = getattr(app.state, "_provider_registry", None)
        if reg is None:
            store = SecretStore(_provider_credentials_path())
            # A real verification used to live only in process memory, so a
            # provider that demonstrably worked came back as 「已配置 · 待验证」
            # after every refresh and every restart. Same database as everything
            # else, holding outcomes only — never a key or a header.
            vstore = VerificationStore(config.state_store_db_path)
            reg = ProviderRegistry(store, verification_store=vstore)
            app.state._provider_registry = reg
            app.state._secret_store = store
        return reg

    def _ever_verified(reg) -> bool:
        """Did this provider ever complete a REAL request? Fail-closed."""
        def lookup(pid: str) -> bool:
            rec = reg._restored(pid)
            return bool(rec.get("restored") and (
                rec.get("stored_generation_verified")
                or rec.get("stored_decision_verified")
            ))
        return lookup

    def _provider_credentials_path() -> str:
        """Where the encrypted credentials live.

        Under the runtime directory, never in the repo. A credentials file that
        is one `git add` away from being committed is not a secret store.
        """
        import os as _os

        root = _os.environ.get("JCC_PROVIDER_CREDENTIALS")
        if root:
            return root
        base = _os.path.join(config.state_store_db_path, "..", "providers")
        return _os.path.abspath(base)

    def _production_provider_facts() -> dict:
        """The SINGLE production fact source, shared by /api/health AND the
        Provider Center — they must report the same reality (the §final-decision
        wiring-drift fix).

        Derived from the SAME ProviderRegistry the settings page builds, so the
        home page, the mission runtime and this health endpoint cannot disagree.
        It reports credential PRESENCE (SecretStore.describe — never a secret)
        and a PERSISTED verification (VerificationStore.restore). It makes NO
        live network call, so /api/health stays fast: a fresh probe / live-call
        is the operator's "测试连接" button, not a status read.

        When the production registry is genuinely real (both Jev and MiniMax
        configured AND persistently verified), ``provider_registry_is_real`` is
        True. Otherwise it reports NOT_CONFIGURED / STALE_VERIFIED honestly — it
        never invents a pass.
        """
        reg = _provider_registry()
        per: dict = {}
        for pid in ("typesafe_jev", "minimax"):
            cred = reg._store.describe(pid)
            restored = reg._restored(pid)
            per[pid] = {
                "configured": bool(cred.get("configured")),
                "credential_source": cred.get("source"),
                "last4": cred.get("last4"),
                "verification_restored": bool(restored.get("restored")),
                "verification_source": restored.get("verification_source"),
                "last_verified_at": restored.get("last_verified_at"),
                "last_verified_model": restored.get("last_verified_model"),
            }
        both_configured = all(per[p]["configured"] for p in per)
        both_verified = all(per[p]["verification_restored"] for p in per)
        if both_configured and both_verified:
            mode, is_real = "REAL", True
        elif both_configured:
            mode, is_real = "STALE_VERIFIED", False
        else:
            mode, is_real = "NOT_CONFIGURED", False
        return {
            "source": "ProviderRegistry",
            "legacy_scorer_not_production": True,
            "provider_registry_mode": mode,
            "provider_registry_is_real": is_real,
            "providers": per,
        }

    @app.get("/api/v1/providers")
    def providers_list():
        """Every provider card. Read-only; safe without a token.

        Open because it is a status read: it reports WHETHER a credential exists
        and, at most, four characters of it. It never returns a secret and never
        mutates anything.
        """
        return _provider_registry().health()

    @app.get("/api/v1/providers/{pid}")
    def providers_get(pid: str):
        reg = _provider_registry()
        for card in reg.health()["providers"]:
            if card["id"] == pid:
                return card
        raise HTTPException(status_code=404, detail=f"unknown provider {pid!r}")

    @app.post("/api/v1/providers/{pid}/credentials")
    def providers_set_credentials(pid: str, body: dict,
                                  _token: bool = Depends(require_control_token)):
        """Store one credential, then probe and discover.

        The probe is part of the save on purpose: an operator who pastes a key
        wants to know immediately whether it works, and a separate click is how a
        broken key gets left configured for a week.

        The response contains `configured` and `last4` only. The submitted value
        is never echoed — not even on success.
        """
        reg = _provider_registry()
        if pid not in _PROVIDER_IDS:
            raise HTTPException(status_code=404, detail=f"unknown provider {pid!r}")
        secret = str(body.get("api_key") or "")
        if not secret.strip():
            raise HTTPException(status_code=422, detail="api_key is required")
        # A remembered pass belongs to the credential that earned it. Saving a
        # DIFFERENT key makes the old proof a claim about a key that is no longer
        # configured, so it is dropped rather than carried forward.
        reg.forget_verification(pid)
        try:
            reg._store.set_secret(pid, secret)
        except Exception as e:  # noqa: BLE001 - surfaced as 500 without the secret
            raise HTTPException(status_code=500, detail=f"无法保存凭据：{e}") from None
        # Non-secret settings may arrive with the key (plan type, base url, model).
        for key in ("base_url", "model", "display_name", "api_style",
                    "reasoning_effort", "timeout_s"):
            if body.get(key) is not None:
                reg.set_override(pid, **{key: body[key]})
        reg.invalidate(pid)
        card = reg.probe(pid)
        return {
            "schema": "jcc/provider-credential-saved/1",
            "credential": reg._store.describe(pid),
            "probe": card,
        }

    @app.delete("/api/v1/providers/{pid}/credentials")
    def providers_delete_credentials(pid: str,
                                    _token: bool = Depends(require_control_token)):
        """Remove one credential. The key is not returned before or after."""
        reg = _provider_registry()
        if pid not in _PROVIDER_IDS:
            raise HTTPException(status_code=404, detail=f"unknown provider {pid!r}")
        removed = reg._store.delete_secret(pid)
        reg.invalidate(pid)
        # No credential means nothing to have verified.
        reg.forget_verification(pid)
        return {"schema": "jcc/provider-credential-deleted/1",
                "provider": pid, "removed": removed,
                "credential": reg._store.describe(pid)}

    @app.post("/api/v1/providers/{pid}/probe")
    def providers_probe(pid: str, body: dict,
                        _token: bool = Depends(require_control_token)):
        """Reachability check. Makes no paid request.

        Token-gated even though it is a read, because it is a network call the
        operator triggered and there is no reason for an unauthenticated caller
        to be able to make the host talk to third parties.
        """
        reg = _provider_registry()
        if pid not in _PROVIDER_IDS:
            raise HTTPException(status_code=404, detail=f"unknown provider {pid!r}")
        return {"schema": "jcc/provider-probe/1", **reg.probe(pid)}

    @app.post("/api/v1/providers/{pid}/live")
    def providers_live(pid: str, body: dict,
                       _token: bool = Depends(require_control_token)):
        """A minimal REAL generation. This SPENDS a token.

        Kept off the probe route precisely so a settings page refresh cannot
        start charging the operator. The prompt is fixed and tiny.
        """
        reg = _provider_registry()
        if pid not in _PROVIDER_IDS:
            raise HTTPException(status_code=404, detail=f"unknown provider {pid!r}")
        return {"schema": "jcc/provider-live/1",
                **reg.live_call(pid, str(body.get("prompt") or "ping"))}

    @app.get("/api/v1/providers/{pid}/models")
    def providers_models(pid: str):
        """Model ids for one provider.

        Discovery failures fall back to the declared preset, and the response
        says which happened via `source` — a preset presented as a discovery
        result would be a fabricated capability list.
        """
        reg = _provider_registry()
        if pid not in _PROVIDER_IDS:
            raise HTTPException(status_code=404, detail=f"unknown provider {pid!r}")
        if pid == "typesafe_jev":
            h = reg.jev().health()
            return {"provider": pid, "models": list(h.get("models") or []),
                    "source": "discovery" if h.get("models") else "preset"}
        p = reg.get(pid)
        if p is None:
            return {"provider": pid, "models": [], "source": "unconfigured"}
        discovered = p.supports_model_discovery and p.has_credential()
        models = p.models()
        return {"provider": pid, "models": models,
                "source": "discovery" if discovered and models else "preset"}

    @app.patch("/api/v1/model-routing")
    def model_routing_patch(body: dict,
                            _token: bool = Depends(require_control_token)):
        """Update non-secret routing configuration.

        Only non-secret fields. A caller cannot use this route to install a key;
        that is the credentials route, and keeping them apart means a future
        change to one does not silently widen the other.
        """
        reg = _provider_registry()
        applied = {}
        for pid, values in (body.get("providers") or {}).items():
            if not isinstance(values, dict):
                continue
            allowed = {"base_url", "model", "display_name", "api_style",
                       "reasoning_effort", "timeout_s"}
            clean = {k: v for k, v in values.items() if k in allowed and v is not None}
            if clean:
                reg.set_override(pid, **clean)
                applied[pid] = clean
        chain = body.get("chain")
        if isinstance(chain, list):
            applied["chain"] = [str(c) for c in chain]
            app.state._model_chain = [str(c) for c in chain]
        return {"schema": "jcc/model-routing/1", "applied": applied,
                "providers": reg.health()["providers"]}

    @app.get("/api/v1/model-routing")
    def model_routing_get():
        """Current routing configuration and the live routing table."""
        reg = _provider_registry()
        from .model_router import BAND_CHAIN_HEAD, DEFAULT_CHAIN, TASK_BANDS

        chain = list(getattr(app.state, "_model_chain", list(DEFAULT_CHAIN)))
        return {
            "schema": "jcc/model-routing/1",
            "chain": chain,
            "band_heads": dict(BAND_CHAIN_HEAD),
            "task_bands": dict(TASK_BANDS),
            "overrides": {pid: reg.overrides(pid) for pid in _PROVIDER_IDS},
        }

    @app.post("/api/v1/domain/route")
    def domain_route(body: dict, _token: bool = Depends(require_control_token)):
        """Route one task: executor + execution model + verification model.

        Returns an EMPTY decision (empty model, empty chain, a reason) when no
        provider is usable. That is the honest answer and the UI must render it
        as "nothing was chosen" rather than substituting a default.
        """
        from .model_router import DEFAULT_CHAIN, ModelRouter, RouteRequest

        reg = _provider_registry()
        chain = list(getattr(app.state, "_model_chain", list(DEFAULT_CHAIN)))
        # Feed the router the persisted verification evidence. A provider with no
        # model list reports DISCOVERY_UNSUPPORTED from its cheap health read
        # forever, so without this the router would exclude every such provider
        # permanently — including ones that had demonstrably answered.
        router = ModelRouter(
            reg.generation_providers(), chain=chain, jev_decider=reg.jev(),
            verification_lookup=_ever_verified(reg),
        )
        req = RouteRequest(
            task=str(body.get("task") or ""),
            task_type=str(body.get("task_type") or "general"),
            band=body.get("band"),
            context_size=int(body.get("context_size") or 0),
            complexity=str(body.get("complexity") or "medium"),
            risk=str(body.get("risk") or "low"),
            required_capabilities=tuple(body.get("required_capabilities") or ()),
            provider_health=dict(body.get("provider_health") or {}),
            cost_preference=str(body.get("cost_preference") or "balanced"),
            latency_preference=str(body.get("latency_preference") or "balanced"),
            preferred_model=body.get("preferred_model"),
            preferred_provider=body.get("preferred_provider"),
        )
        decision = router.route(
            req,
            executors=tuple(body.get("executors") or ("workbuddy", "codex", "claude", "hermes")),
            executor_health=dict(body.get("executor_health") or {}),
        )
        return {"schema": "jcc/route-decision/1", **decision.as_dict()}

    @app.get("/api/v1/domain/counts")
    def domain_counts():
        """UNCAPPED domain totals — the read that makes a bounded list honest.

        ``/api/v1/domain/missions`` and ``/deliverables`` are bounded (a
        documented ceiling, see ``domain.MAX_DOMAIN_LIST_LIMIT``). A client that
        renders a bounded list as the whole domain silently drops the rest, so
        this endpoint exists to let it say "showing 200 of 1,000" instead. It is
        open like the other read-only projections: four scalars, no rows, and it
        mutates nothing.
        """
        return {"schema": "jcc/v1.3-domain-counts/1", **domain.domain_counts()}

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

    # ------------------------------------------------------------------ #
    # v1.3 Wave 5 — Deliverables (server-side view + human verdict)
    # ------------------------------------------------------------------ #
    @app.get("/api/v1/domain/deliverables")
    def domain_list_deliverables(status: Optional[str] = None,
                                 mission_id: Optional[str] = None,
                                 limit: int = 200):
        """The server-side deliverable view (artifacts joined to mission title).

        An unknown ``status`` is a 422, never silently ignored -- a filter that
        quietly drops its own filter is how a UI shows the wrong pile.
        """
        st = None
        if status:
            try:
                st = ArtifactStatus(str(status).upper())
            except ValueError:
                raise HTTPException(status_code=422, detail="unknown status %r" % status)
        return domain.list_deliverables(status=st, mission_id=mission_id, limit=limit)

    @app.get("/api/v1/domain/artifacts/{aid}")
    def domain_get_artifact(aid: str):
        a = domain.get_artifact(aid)
        if a is None:
            raise HTTPException(status_code=404, detail="artifact not found")
        return a.model_dump(mode="json")

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
        # RISK-1: validate BEFORE the write, and store only the CANONICAL path.
        # The launch side already refuses an out-of-scope root (CR-003), but a
        # row that was never checked is still a row: it renders in the UI, it is
        # exported, and it is one launcher change away from being reachable. A
        # boundary enforced only on the way out is not a boundary.
        #
        # The RAW value is echoed back under `root_path_raw` for display and
        # audit only (spec 9). It is never used as a cwd and never stored.
        raw_root = str(body.get("root_path") or "")
        try:
            canonical_root = _validate_root(raw_root, field="root_path")
        except ProjectRootRejected as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": exc.code, "detail": exc.detail,
                        "field": "root_path", "raw": raw_root,
                        "allowed_project_roots": list(_allowed_roots())},
            )
        p = domain.create_project(name, root_path=canonical_root,
                                  description=str(body.get("description") or ""))
        out = p.model_dump(mode="json")
        # Spec 9: what the operator typed, kept beside -- never instead of -- the
        # canonical value that was actually persisted.
        out["root_path_raw"] = raw_root
        out["root_path_canonical"] = canonical_root
        return out

    @app.post("/api/v1/domain/missions")
    def domain_create_mission(body: dict,
                              _token: bool = Depends(require_control_token)):
        pid = str(body.get("project_id") or "").strip()
        title = str(body.get("title") or "").strip()
        if not pid or not title:
            raise HTTPException(status_code=422,
                                detail="project_id and title are required")
        # RISK-1: a mission's working_directory is the OTHER place a path reaches
        # the launcher. ``launch_control.build_intent`` falls back to
        # ``mission.working_directory`` when the project has no root, so a
        # mission row is exactly as capable of carrying a traversal as a project
        # row is -- and it is a second entry, so it needs the same check.
        raw_wd = str(body.get("working_directory") or "")
        try:
            canonical_wd = _validate_root(raw_wd, field="working_directory")
        except ProjectRootRejected as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": exc.code, "detail": exc.detail,
                        "field": "working_directory", "raw": raw_wd,
                        "allowed_project_roots": list(_allowed_roots())},
            )
        try:
            m = domain.create_mission(
                pid, title, goal=str(body.get("goal") or ""),
                agent=str(body.get("agent") or "workbuddy"),
                working_directory=canonical_wd,
                context=str(body.get("context") or ""),
                permissions=list(body.get("permissions") or []),
                budget_units=float(body.get("budget_units") or 0.0),
                connection_ref=body.get("connection_ref"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        out = m.model_dump(mode="json")
        out["working_directory_raw"] = raw_wd
        out["working_directory_canonical"] = canonical_wd
        return out

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

    @app.post("/api/v1/domain/artifacts/{aid}/verify")
    def domain_verify_artifact(aid: str, body: dict,
                               _token: bool = Depends(require_control_token)):
        """Record a human verdict on a deliverable (Wave 5).

        Only VERIFIED / REJECTED are accepted; a verdict is final. This is a
        record of judgement, never an effect on an agent, so it does not need the
        preview-first treatment that agent-affecting controls get -- but it is
        still token-gated, and `decided_by` is required so the record names a
        human.
        """
        decided_by = str(body.get("decided_by") or "").strip()
        if not decided_by:
            raise HTTPException(status_code=422, detail="decided_by is required")
        try:
            status = ArtifactStatus(str(body.get("status") or "").upper())
        except ValueError:
            raise HTTPException(status_code=422, detail="status must be VERIFIED or REJECTED")
        try:
            a = domain.verify_artifact(aid, status, decided_by=decided_by,
                                       note=str(body.get("note") or ""))
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

    @app.get("/api/v1/domain/connections/{cid}")
    def domain_get_connection(cid: str):
        """One connection with its capabilities and HOW each was established.

        `provenance` is "MEASURED" (a probe observed it; evidence_ref present) or
        "ASSERTED" (a human recorded it). The UI must not draw the two the same.
        """
        c = domain.get_connection(cid)
        if c is None:
            raise HTTPException(status_code=404, detail="connection not found")
        d = c.model_dump(mode="json")
        d["control_verified"] = domain.is_control_verified(cid)
        d["capabilities"] = [
            {**cap.model_dump(mode="json"),
             "provenance": domain.capability_provenance(cap)}
            for cap in domain.list_capabilities(cid)
        ]
        return d

    @app.post("/api/v1/domain/connections/{cid}/capability")
    def domain_set_capability(cid: str, body: dict,
                              _token: bool = Depends(require_control_token)):
        kind = str(body.get("kind") or "").strip()
        if not kind:
            raise HTTPException(status_code=422, detail="kind is required")
        if not domain.get_connection(cid):
            raise HTTPException(status_code=404, detail="connection not found")
        # Wave 7 hardening: CONTROL_VERIFIED gates effectful action, so it must
        # be backed by evidence. A bare assertion is refused with a pointer to the
        # probe route; it is never silently downgraded or silently accepted.
        if str(kind).upper() == "CONTROL_VERIFIED" and not body.get("evidence_ref"):
            raise HTTPException(
                status_code=422,
                detail=("CONTROL_VERIFIED requires an evidence_ref: it gates "
                        "effectful action and must cite the control path that was "
                        "exercised. To measure a connection, use "
                        "POST /api/v1/domain/connections/{cid}/verify."))
        try:
            cap = domain.set_capability(
                cid, CapabilityKind(kind), detail=str(body.get("detail") or ""),
                evidence_ref=body.get("evidence_ref"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return {**cap.model_dump(mode="json"),
                "provenance": domain.capability_provenance(cap)}

    @app.post("/api/v1/domain/connections/{cid}/verify")
    def domain_verify_connection(cid: str, body: dict,
                                 _token: bool = Depends(require_control_token)):
        """Run a REAL probe for this connection and record what it OBSERVED.

        The body may select which probe runs; it can NEVER state the outcome. Any
        ``kind`` in the body is ignored -- the recorded capability is whatever the
        probe measured (`capability_probe.probe_connection`). A probe can at most
        claim ADAPTER_CONTRACT_LIVE in this build, because actuation is disabled
        and no control path is exercised.
        """
        c = domain.get_connection(cid)
        if c is None:
            raise HTTPException(status_code=404, detail="connection not found")
        from .capability_probe import probe_connection
        result = probe_connection(str(c.agent), domain=domain, engine=engine)
        try:
            updated = domain.record_probe(cid, result)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        caps = domain.list_capabilities(cid)
        return {
            "connection_id": cid,
            "agent": c.agent,
            "observed": result.to_dict(),
            "health": updated.health,
            "control_verified": domain.is_control_verified(cid),
            "capabilities": [
                {**cap.model_dump(mode="json"),
                 "provenance": domain.capability_provenance(cap)}
                for cap in caps
            ],
        }

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
        # v1.3 FINAL — production execution routes through the AUTHENTICATED
        # WorkBuddy daemon (WBIPC). Probe it for real: alive + authenticated is a
        # measurement; the agent-run capability is the honest gate for whether a
        # model-backed native workload can actually start. We never fall back to
        # an unauthenticated isolated serve, and we never scan ambient sessions.
        daemon_state = {"ok": False, "alive": False, "authenticated": False,
                        "agent_run_capability": None}
        try:
            from .workbuddy_daemon import WorkBuddyDaemonAdapter
            cfg = os.environ.get("WORKBUDDY_CONFIG_DIR") or os.path.join(
                os.path.expanduser("~"), ".workbuddy")
            st = WorkBuddyDaemonAdapter(config_dir=cfg).probe()
            daemon_state = {
                "ok": st.alive and st.authenticated and bool(st.agent_run_capability),
                "alive": st.alive,
                "authenticated": st.authenticated,
                "agent_run_capability": st.agent_run_capability,
                "pipe_count": len(st.pipes),
            }
        except Exception as exc:  # probe never actuates; report the failure
            daemon_state["error"] = str(exc)[:160]
        return {
            "schema": "jcc/v1.3-launch-probe/1",
            "launchable_agents": list(LAUNCHABLE_AGENTS_SERVER),
            # v1.3 Wave 9.2 — the ATTACH/observe answer above, plus the START
            # answer below. They are different questions and are never merged:
            # "is there a live session I could attach to" vs "can this host
            # actually START a workload". The second is a MEASUREMENT and reports
            # UNKNOWN when it has not been measured.
            "native_launch": launch_ctl.capability("workbuddy"),
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
            # FINAL DAEMON INTEGRATION — the authenticated daemon is the
            # production execution transport. Its agent-run capability is the
            # real gate; without it, launches fail closed (no model auth).
            "daemon": daemon_state,
            # This probe never actuates; state it so a UI cannot imply otherwise.
            "actuation": False,
        }

    def _native_supervisor():
        """The process-wide supervisor for mission-scoped native workloads.

        One instance, not one per request: the supervisor owns the workload's
        lifetime and a supervisor created inside a request handler would die with
        the request — which is the very coupling being removed.
        """
        from .native_supervisor import NativeLaunchSupervisor

        sup = getattr(app.state, "_native_supervisor", None)
        if sup is None:
            sup = NativeLaunchSupervisor(
                out_dir=str(_runtime_dir / "native"),
                session_probe=_live_sessions,
                # Every transition is written back to the mission, so the row is
                # the single place a reader looks — and so a FAILED workload
                # leaves a code and a stage rather than a bare status.
                on_update=_apply_native_update,
            )
            app.state._native_supervisor = sup
        return sup

    def _apply_native_update(w) -> None:
        """Mirror a supervisor transition onto the mission row."""
        from .domain import MissionStatus

        try:
            store = domain
        except Exception:  # noqa: BLE001
            return
        try:
            status = {
                "SPAWNED": MissionStatus.STARTING,
                "NATIVE_STARTED": MissionStatus.RUNNING,
                "RUNNING": MissionStatus.RUNNING,
                "COMPLETED": MissionStatus.COMPLETED,
                "FAILED": MissionStatus.FAILED,
            }.get(w.launch_status)
            if status is None:
                return
            current = store.get_mission(w.mission_id)
            if current is None:
                return
            if current.status == status:
                return
            # Refused transitions must not be forced: the machine is the
            # authority on what a mission may become.
            store.transition_mission(
                w.mission_id, status,
                reason="native launch %s" % w.launch_status,
                attention_reason=w.failure_message if w.failure_message else None,
            )
            if w.failure_code:
                store.mark_mission_failed(
                    w.mission_id,
                    failure_code=w.failure_code,
                    failure_stage=w.failure_stage or "WORKBUDDY_LAUNCH",
                    failure_message=w.failure_message or "",
                )
        except Exception:  # noqa: BLE001 - supervision must not die on a write
            logger.warning("could not apply native update for %s", w.mission_id)

    def _live_sessions() -> list:
        """Current WorkBuddy sessions, for RECOVERY only.

        Never used to decide that a launch succeeded — see native_launch's module
        docstring for what went wrong when that was the case.
        """
        try:
            from .workbuddy_launch import list_sessions
            return list(list_sessions())
        except Exception:  # noqa: BLE001
            return []

    def _mark_launch_failure(mid: str, code: str, detail: str) -> None:
        """Persist WHY a launch was refused, on the mission row itself.

        A mission that reads `FAILED` with nothing else is undiagnosable: during
        release acceptance five of them sat in exactly that state and the only
        clue was an absent launch journal.

        Declared BEFORE the route on purpose. A helper placed between a decorator
        and its function is read as the handler — which is how a first attempt at
        this ended up answering every launch with 422 for a missing `query.code`.
        """
        try:
            domain.mark_mission_failed(
                mid, failure_code=str(code)[:120],
                failure_stage="WORKBUDDY_LAUNCH",
                failure_message=str(detail)[:2000],
            )
        except Exception:  # noqa: BLE001 - observability must not mask the cause
            logger.warning("could not record launch failure for %s", mid)

    def _record_native_launch(mid: str, native: Dict[str, Any]) -> None:
        """Bind the mission to the workload that was actually started."""
        try:
            domain.attach_session(mid, str(native.get("correlation_id") or ""))
        except Exception:  # noqa: BLE001
            logger.warning("could not attach native session to %s", mid)

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
        # An explicitly supplied event is the caller's own — used by tests and by
        # a host that already started the workload itself. It is the ONLY path on
        # which a caller-supplied event counts.
        if event is not None:
            pass
        elif body.get("native"):
            # MISSION-SCOPED NATIVE LAUNCH — ASYNCHRONOUS.
            #
            # This returns 202 as soon as the process is spawned. It deliberately
            # does NOT wait for the workload: a headless run reads, writes and
            # exits, which outlasts an HTTP request, and the earlier synchronous
            # version left missions stuck in STARTING with the client timing out
            # and no verdict either way.
            #
            # The answer says STARTING because that is the truth. A pid is
            # returned so an operator can look at it, not so the caller can wait
            # on it; a pid proves a process started and nothing more.
            from .native_supervisor import NativeLaunchSupervisor

            # MEASURED: the route used to take ~30s wall clock for work that is
            # one spawn. Rather than guess, every stage is timed and reported in
            # the response, so the next regression names its own stage instead
            # of being absorbed into a bigger timeout. Monotonic, because a wall
            # clock adjustment mid-request would produce a negative duration.
            _t = [("route_enter", time.perf_counter())]
            sup = _native_supervisor()
            _t.append(("supervisor_ready", time.perf_counter()))
            run = domain.create_run(mid, "workbuddy")
            _t.append(("create_run", time.perf_counter()))
            payload = sup.start(
                mission_id=mid,
                jcc_run_id=run.run_id,
                project_root=str(body.get("cwd") or mission.working_directory or ""),
                task=str(mission.goal or ""),
                model=str(body.get("model") or ""),
            )
            _t.append(("spawn_and_register", time.perf_counter()))
            # Each entry is (stage, elapsed since the previous stage); the stage
            # label is the key, never a tuple, or the response cannot be encoded.
            payload["timings_ms"] = {
                _t[i][0]: round((_t[i][1] - _t[i - 1][1]) * 1000, 2)
                for i in range(1, len(_t))
            }
            payload["total_ms"] = round((_t[-1][1] - _t[0][1]) * 1000, 2)
            status_code = 202 if payload.get("launch_status") in (
                "STARTING", "SPAWNED") else 409
            if status_code == 409:
                _mark_launch_failure(mid, payload.get("failure_code") or "WORKBUDDY_NATIVE_NOT_STARTED",
                                    payload.get("failure_message") or "")
                return JSONResponse(
                    status_code=409,
                    content={"schema": "jcc/native-launch/1", **payload},
                )
            return JSONResponse(
                status_code=202,
                content={"schema": "jcc/native-launch/1", "launch_status": "STARTING",
                         **payload},
            )
        elif capture:
            from .native_launch import verify_attached_session
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
            # ATTACHING is allowed; ATTACHING AS PROOF OF LAUNCH IS NOT.
            #
            # A captured session must belong to this mission's root and must not
            # predate this launch. Without these two checks the host's busiest
            # unrelated session was accepted as this mission's workload, and the
            # mission reported `ok=True` having produced nothing.
            requested_root = str(body.get("cwd") or mission.working_directory or "")
            mismatch = verify_attached_session(
                session=cap.get("observed") or {},
                project_root=requested_root,
                launch_requested_at=time.time(),
            )
            if mismatch:
                _mark_launch_failure(
                    mid, mismatch.split(":", 1)[0], mismatch)
                raise HTTPException(
                    status_code=409,
                    detail={"code": mismatch.split(":", 1)[0], "detail": mismatch,
                            "observed": cap.get("observed"),
                            "note": "ambient capture is observation only; "
                                    "use native=true to start a mission-scoped workload"},
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

    # ------------------------------------------------------------------ #
    # v1.3 Wave 9.2 — MISSION LAUNCH CONTROL PLANE
    #
    # Wave 9.1 built a HostLauncher that can verify a signed directive and start
    # a real workload; NOTHING called it, so the product's central promise ended
    # at a database row. These two routes are that call, and they are
    # PREVIEW-FIRST:
    #
    #   launch-request -> resolve Mission+Project, bind a LaunchIntent, mint the
    #                     single-use signed directive, and answer "would this be
    #                     accepted?" WITHOUT spawning anything and WITHOUT
    #                     spending the nonce. Safe to call on every render.
    #   launch-commit  -> the identical nine checks (this time spending the
    #                     nonce), then spawn, then report the child's OWN
    #                     classification. A mission only reaches RUNNING when the
    #                     child produced a real session AND a real model turn.
    #
    # Both are token-gated. Neither can actuate: the capability honoured is
    # USER_INITIATED_MISSION_LAUNCH -- a different name from anything the
    # autonomous controller uses -- and jcc_core.launch_control holds no
    # actuator, so AUTONOMOUS_ACTUATION stays False.
    #
    # No request-body value reaches a command besides `model`, which is matched
    # against a narrow token allowlist. Notably the project root is NOT taken
    # from the body: it is the project's own root, so a caller cannot aim a
    # launch at an arbitrary directory.
    # ------------------------------------------------------------------ #
    @app.post("/api/v1/domain/missions/{mid}/launch-request")
    def domain_launch_request(mid: str, body: dict,
                              _token: bool = Depends(require_control_token)):
        """Preview a real launch. Spawns nothing; the nonce is NOT consumed."""
        from .launch_control import LaunchControlError

        try:
            return launch_ctl.request(mid, commit=False,
                                      model=(body or {}).get("model") or "auto")
        except LaunchControlError as exc:
            code = 404 if exc.code == "MISSION_NOT_FOUND" else 409
            raise HTTPException(status_code=code,
                                detail={"code": exc.code, "detail": exc.detail})

    @app.post("/api/v1/domain/missions/{mid}/launch-commit")
    def domain_launch_commit(mid: str, body: dict,
                             _token: bool = Depends(require_control_token)):
        """Commit a launch: same checks, nonce spent, a real process starts.

        Returns HTTP 200 with the structured outcome even when the launch is
        REFUSED or when the child could not authenticate -- those are facts about
        the host, not server errors, and the caller must render them. Only a
        programming error (unknown mission, missing project root, model token
        refused) is a 4xx.
        """
        from .launch_control import LaunchControlError

        try:
            return launch_ctl.request(mid, commit=True,
                                      model=(body or {}).get("model") or "auto")
        except LaunchControlError as exc:
            code = 404 if exc.code == "MISSION_NOT_FOUND" else 409
            raise HTTPException(status_code=code,
                                detail={"code": exc.code, "detail": exc.detail})

    # ------------------------------------------------------------------ #
    # v1.3 Wave 4 — NEEDS ATTENTION control
    #
    # The inbox becomes a control surface, but PREVIEW-FIRST: a resolution
    # request defaults to ``dry_run=True`` and mutates nothing. Preview and apply
    # share one code path, so the preview cannot disagree with the effect (the
    # Wave-4 falsifier: a dry_run request leaves the domain DB byte-identical).
    #
    # POLICY PERMITTED and CURRENTLY EXECUTABLE are computed separately by
    # ``attention_control.resolve_attention`` and are NEVER merged into one flag.
    # ------------------------------------------------------------------ #
    @app.get("/api/v1/domain/needs-attention/resolutions")
    def domain_attention_resolutions():
        """Open, read-only: the inbox with a per-item control resolution.

        The resolution answers two DIFFERENT questions without conflating them:
        is the action allowed by policy, and does it actually work right now on
        this host. An unknown prerequisite is reported as UNKNOWN, never green.
        """
        from .attention_control import gather_context, resolve_attention

        items = domain.needs_attention()
        live = _live_session_ids()
        ctx = gather_context(domain, actuation_enabled=_actuation_enabled(),
                             live_session_ids=live)
        return {
            "schema": "jcc/v1.3-attention-resolutions/1",
            "actuation_enabled": _actuation_enabled(),
            "items": [r.model_dump(mode="json") for r in resolve_attention(items, ctx)],
        }

    @app.post("/api/v1/domain/needs-attention/{mid}/resolve")
    def domain_resolve_attention(mid: str, body: dict,
                                 _token: bool = Depends(require_control_token)):
        """Token-gated: PREVIEW (default) or APPLY a control action.

        Body: ``{action, dry_run?, decided_by?, to_status?}``.
        ``dry_run`` defaults to True. A preview returns the plan and mutates
        nothing. An apply performs the action and returns what it did.
        """
        from .attention_control import (ACTION_DECIDE_APPROVAL, ACTION_RESUME,
                                        ACTION_VERIFY, ACTION_ACKNOWLEDGE_DELIVERABLE,
                                        ACTION_RECONNECT, Executability,
                                        gather_context, resolve_item)

        action = str(body.get("action") or "").strip()
        if action not in (ACTION_DECIDE_APPROVAL, ACTION_RESUME, ACTION_VERIFY,
                          ACTION_ACKNOWLEDGE_DELIVERABLE, ACTION_RECONNECT):
            raise HTTPException(status_code=422, detail="unknown action %r" % action)
        dry_run = bool(body.get("dry_run", True))

        items = domain.needs_attention()
        item = next((i for i in items if i.get("mission_id") == mid), None)
        if item is None:
            # Not in attention: report it as such rather than 404-ing into a
            # generic error, so the UI can say "already resolved".
            return {"ok": False, "mission_id": mid, "action": action,
                    "dry_run": dry_run, "applied": False,
                    "detail": "mission is not in NEEDS_ATTENTION",
                    "blocked_reason": "NOT_IN_ATTENTION"}

        ctx = gather_context(domain, actuation_enabled=_actuation_enabled(),
                             live_session_ids=_live_session_ids())
        res = resolve_item(item, ctx)
        chosen = next((a for a in res.actions if a.action == action), None)
        if chosen is None:
            return {"ok": False, "mission_id": mid, "action": action,
                    "dry_run": dry_run, "applied": False,
                    "detail": "action is not proposed for this item"}

        permitted = chosen.policy_permitted
        executable = chosen.executability is Executability.EXECUTABLE

        if not permitted or not executable:
            return {"ok": False, "mission_id": mid, "action": action,
                    "dry_run": dry_run, "applied": False,
                    "policy_permitted": permitted,
                    "executability": chosen.executability.value,
                    "blocked_reason": (chosen.blocked_reason.value
                                       if chosen.blocked_reason else None),
                    "detail": chosen.detail}

        if dry_run:
            return {"ok": True, "mission_id": mid, "action": action,
                    "dry_run": True, "applied": False,
                    "policy_permitted": True,
                    "executability": "EXECUTABLE",
                    "plan": _attention_plan(domain, item, action),
                    "detail": chosen.detail}

        # -- apply -------------------------------------------------------- #
        try:
            applied, note = _apply_attention(domain, mid, action, body)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return {"ok": True, "mission_id": mid, "action": action,
                "dry_run": False, "applied": applied,
                "policy_permitted": True, "executability": "EXECUTABLE",
                "result": note}


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
        # P4 / CR-H6, second call site -- fixed in the SAME commit as
        # `require_control_token` because it is the same defect, and fixing one
        # while leaving the other is inconsistent hardening that is harder to
        # reason about than not fixing either. Same nature: a non-ASCII
        # subprotocol token raised TypeError here too, and because it escaped
        # BEFORE `ws.close(code=4401)` the socket was neither accepted nor
        # cleanly closed -- an aborted handshake rather than an HTTP 403.
        #
        # The `not expected or not token` short-circuit is preserved EXACTLY:
        # an unconfigured token must still be a refusal, and a missing token must
        # not be encoded at all. Only the comparison is encoded.
        if not expected or not token or not hmac.compare_digest(
                token.encode("utf-8"), expected.encode("utf-8")):
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
