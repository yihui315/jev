"""Data layer for the 2D Control UI.

Every value the screens render is pulled from exactly one real source:

* live control-plane API responses (``ApiClient``), or
* the real ``config/jcc.yaml`` read through ``jcc_core.config`` (the SAME file
  the server loads -- the UI is a read-only projection, so it must not invent a
  second source of truth).

There are NO placeholder values and NO fixture pages. Where the control plane
exposes no endpoint for a piece of data (per-session budget usage, evidence
detail, a "git-test" probe), the model returns ``None`` / an explicit
``no_endpoint`` marker and the renderer says so -- it does not fabricate a number.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from control_ui.api_client import ApiClient, ApiError, ApiUnreachable
from control_ui.api_client import quote as _quote


# --------------------------------------------------------------------------- #
# failure-tolerant API access
# --------------------------------------------------------------------------- #
def _safe(fn, *args, **kw) -> Dict[str, Any]:
    """Call an API helper; normalise transport/auth failures into a dict.

    Returns ``{"ok": True, "data": ...}`` or ``{"ok": False, ...}`` so the
    renderer can show "API UNREACHABLE" instead of raising an empty page.
    """
    try:
        return {"ok": True, "data": fn(*args, **kw)}
    except ApiUnreachable as exc:
        return {"ok": False, "error": "API UNREACHABLE", "detail": str(exc)}
    except ApiError as exc:
        return {"ok": False, "error": "API ERROR", "status": exc.status, "detail": exc.message}


def _get_json(api: ApiClient, path: str) -> Dict[str, Any]:
    return _safe(api.get_json, path)


# --------------------------------------------------------------------------- #
# demo classification (derived from DATA, never a hardcoded flag)
# --------------------------------------------------------------------------- #
#: Mirrors ``fixtures/demo/load.py::is_demo_run`` so a real agent's session can
#: never be labelled DEMO and a demo run can never impersonate a real agent.
#: Kept in product code as the rendering rule; the fixture owns the canonical
#: copy and the demo e2e test asserts the two agree.
def is_demo_session(session: Dict[str, Any]) -> bool:
    project = str(session.get("project") or "").strip()
    session_id = str(session.get("session_id") or "").strip()
    if project.upper() == "DEMO":
        return True
    return session_id.lower().startswith("demo-")


# --------------------------------------------------------------------------- #
# config-derived helpers (real file, non-secret fields only)
# --------------------------------------------------------------------------- #
def _config():
    from jcc_core.config import load_config

    return load_config()


def _agent_capabilities(cfg) -> Dict[str, Any]:
    return dict(cfg.agents or {})


def _budget_limits(cfg) -> Dict[str, Any]:
    shadow = (cfg.raw.get("p3") or {}).get("shadow") or {}
    return dict(shadow.get("budget") or {})


def _live_thresholds(cfg) -> Dict[str, Any]:
    shadow = (cfg.raw.get("p3") or {}).get("shadow") or {}
    default = (shadow.get("thresholds") or {}).get("default") or {}
    return dict(default)


def _policy_summary(cfg) -> Dict[str, Any]:
    return {
        "version": cfg.policy.version,
        "priority": list(cfg.policy.priority),
        "thresholds_retired": dict(cfg.policy.thresholds),
        "thresholds_live": _live_thresholds(cfg),
    }


# --------------------------------------------------------------------------- #
# per-screen models
# --------------------------------------------------------------------------- #
def dashboard_model(api: ApiClient) -> Dict[str, Any]:
    health = _get_json(api, "/api/health")
    sessions = _get_json(api, "/api/sessions")
    cfg = _config()

    sess_list: List[Dict[str, Any]] = sessions["data"] if sessions["ok"] else []
    risk_by_band: Dict[str, int] = {}
    problems = 0
    demo_count = 0
    for s in sess_list:
        assess = s.get("current_assessment") or {}
        band = assess.get("band")
        if band:
            risk_by_band[band] = risk_by_band.get(band, 0) + 1
        if s.get("status") in ("escalated", "quarantine", "await", "paused"):
            problems += 1
        if is_demo_session(s):
            demo_count += 1

    return {
        "health": health,
        "sessions": sessions,
        "agents": _agent_capabilities(cfg),
        "budget_limits": _budget_limits(cfg),
        "risk_by_band": risk_by_band,
        "problems": problems,
        "session_count": len(sess_list),
        "demo_count": demo_count,
    }


def sessions_model(api: ApiClient) -> Dict[str, Any]:
    sessions = _get_json(api, "/api/sessions")
    sess_list: List[Dict[str, Any]] = sessions["data"] if sessions["ok"] else []
    for s in sess_list:
        s = dict(s)  # type: ignore[assignment]
        s["_demo"] = is_demo_session(s)
    return {"sessions": sessions, "rows": sess_list}


def session_detail_model(api: ApiClient, session_id: str) -> Dict[str, Any]:
    session = _get_json(api, f"/api/sessions/{_quote(session_id)}")
    decisions = _get_json(api, api.decisions_path(session_id))
    audit = _get_json(api, "/api/audit")

    sess: Optional[Dict[str, Any]] = session["data"] if session["ok"] else None
    agent = sess.get("agent") if sess else None
    cfg = _config()
    capability = (_agent_capabilities(cfg).get(agent)) if agent else None

    audit_rows = [
        a for a in (audit["data"] if audit["ok"] else [])
        if a.get("session_id") == session_id
    ]

    return {
        "session_id": session_id,
        "session": session,
        "decisions": decisions,
        "audit_rows": audit_rows,
        "policy": _policy_summary(cfg),
        "budget_limits": _budget_limits(cfg),
        "capability": capability or {},
        "is_demo": is_demo_session(sess) if sess else False,
        # Reported, not fabricated: the control plane exposes no per-session
        # budget-usage, evidence-detail or git-test endpoint over the open
        # surface, so those sub-sections carry explicit "no endpoint" markers.
        "no_endpoint_sections": ["budget_usage", "evidence_detail", "git_test"],
    }


def decisions_model(api: ApiClient, session_id: Optional[str] = None) -> Dict[str, Any]:
    decisions = _get_json(api, api.decisions_path(session_id))
    verify = _get_json(api, "/api/audit/verify")
    cfg = _config()
    dec_list: List[Dict[str, Any]] = decisions["data"] if decisions["ok"] else []
    demo = any(
        str(d.get("session_id") or "").lower().startswith("demo-") for d in dec_list
    ) or any(
        str(d.get("project") or "").upper() == "DEMO" for d in dec_list
    )
    return {
        "session_id": session_id,
        "decisions": decisions,
        "verify": verify,
        "policy": _policy_summary(cfg),
        "is_demo": demo,
    }


def security_model(api: ApiClient) -> Dict[str, Any]:
    health = _get_json(api, "/api/health")

    # Probe a MUTATING endpoint the way an anonymous caller would -- no token is
    # ever sent by this client. The status (401/403) is the live proof that the
    # fail-closed gate is engaged. A 2xx here would be a critical finding.
    probe_status: Optional[int] = None
    probe_error: Optional[str] = None
    try:
        resp = api.post_json(
            "/api/assess",
            {
                "session_id": "ui-security-probe",
                "event_id": "ui-security-probe-e1",
                "delivery_id": "ui-security-probe-e1-d1",
                "agent": "workbuddy",
                "project": "DEMO",
                "event_type": "session_start",
            },
        )
        probe_status = resp.status
    except ApiUnreachable as exc:
        probe_error = str(exc)

    open_endpoints = (
        health["data"].get("open_get_endpoints") if health.get("ok") else None
    )
    return {
        "health": health,
        "open_endpoints": open_endpoints,
        "probe_status": probe_status,
        "probe_error": probe_error,
    }


def settings_model() -> Dict[str, Any]:
    cfg = _config()
    return {
        "server": {
            "host": cfg.server.host,
            "port": cfg.server.port,
            "cors_origins": list(cfg.server.cors_origins),
        },
        # Env-var NAME only -- never the secret value.
        "control_token_env": cfg.control_token_env,
        "jev": {
            "provider": cfg.jev.provider,
            "model": cfg.jev.model,
            "base_url": cfg.jev.base_url,
            "feature_flags": dict(cfg.jev.feature_flags),
        },
        "actuation": {
            "enabled": cfg.actuation.enabled,
            "allowed_actions": list(cfg.actuation.allowed_actions),
        },
        "policy": _policy_summary(cfg),
        "budget_limits": _budget_limits(cfg),
        "agents": _agent_capabilities(cfg),
    }


def audit_model(api: ApiClient) -> Dict[str, Any]:
    audit = _get_json(api, "/api/audit")
    verify = _get_json(api, "/api/audit/verify")
    return {"audit": audit, "verify": verify}


def health_model(api: ApiClient) -> Dict[str, Any]:
    return {"health": _get_json(api, "/api/health")}
