"""Wave 3 — JCC Scene projection (schema ``jcc-scene/1``).

This module is the SINGLE source of truth for the visual command center's
read-only world state. It is a PROJECTION ONLY: it reads live engine/store
state and released artifacts and assembles one document. It never writes,
never creates a second truth store, and never invents facts.

Source semantics (machine-readable, per top-level section)
---------------------------------------------------------
Every section carries ``source`` ∈ {LIVE, RELEASE, DERIVED, UNKNOWN}:

  * LIVE     — read directly from the running control plane (engine/store).
  * RELEASE  — read from a frozen release artifact (e.g. capability matrix).
  * DERIVED  — computed from other LIVE/RELEASE data inside this projection.
  * UNKNOWN  — genuinely not knowable in this build/state.

HARD RULE (anti-fabrication): UNKNOWN is NEVER collapsed to ``0`` / ``false`` /
``"healthy"`` / ``[]`` (an empty list would falsely claim "verified none").
When a section cannot be produced it is returned as
``{"source": "UNKNOWN", "data": null, "reason": "..."}`` — an explicit,
honest gap the UI must render as "unknown", never as a zero.

Robustness: every section is assembled independently inside a try/except. A
malformed/partial store row (the "hostile" case) must degrade that ONE section
to UNKNOWN, never crash the whole projection and never coerce the gap to a
false-clean value.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .policy import COST_UNITS_PER_ACTION
from .schemas import DirectiveAction

#: The scene document schema version. Bump only with a compatible contract
#: change; the 3D world keys off this string.
SCENE_SCHEMA_VERSION = "jcc-scene/1"

#: Where the v1.2 build looks for the released capability matrix. Overridable
#: via ``JCC_CAPABILITY_MATRIX`` so tests can point at a fixture.
DEFAULT_CAPABILITY_MATRIX = (
    Path(__file__).resolve().parents[2]
    / "artifacts"
    / "release"
    / "V1_1_CAPABILITY_MATRIX.json"
)

SOURCE_LEGEND = {
    "LIVE": "read directly from the running control plane (engine/store)",
    "RELEASE": "read from a frozen release artifact (capability matrix, etc.)",
    "DERIVED": "computed inside this projection from LIVE/RELEASE data",
    "UNKNOWN": "not knowable in this build/state; rendered as unknown, "
    "never coerced to 0/false/healthy",
}


def _section(source: str, data: Any, *, reason: Optional[str] = None) -> Dict[str, Any]:
    """Build a labelled section. ``reason`` is required for UNKNOWN sections."""
    sec: Dict[str, Any] = {"source": source, "data": data}
    if source == "UNKNOWN":
        sec["reason"] = reason or "not available in this build"
    return sec


def _safe_get(d: Any, *keys, default: Any = None) -> Any:
    """Defensive nested get that tolerates non-dict / missing leaves."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k, default)
        if cur is None:
            return default
    return cur


def _agents_from_sessions(sessions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """DERIVED: group live sessions by agent, with status + risk breakdown."""
    by_agent: Dict[str, Dict[str, Any]] = {}
    for s in sessions:
        agent = str(s.get("agent") or "unknown")
        bucket = by_agent.setdefault(
            agent,
            {
                "agent": agent,
                "session_count": 0,
                "statuses": {},
                "session_ids": [],
                "latest_risk_band": "unknown",
            },
        )
        bucket["session_count"] += 1
        st = str(s.get("status") or "unknown")
        bucket["statuses"][st] = bucket["statuses"].get(st, 0) + 1
        sid = s.get("session_id")
        if sid:
            bucket["session_ids"].append(sid)
        band = _safe_get(s, "current_assessment", "band", default="unknown")
        if band in ("critical", "high"):
            bucket["latest_risk_band"] = band
    return {
        "agents": list(by_agent.values()),
        "distinct_agent_count": len(by_agent),
    }


def _assessments_from_sessions(sessions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """LIVE: harvest per-session current assessment, defensively."""
    out: List[Dict[str, Any]] = []
    for s in sessions:
        raw = s.get("current_assessment")
        if not isinstance(raw, dict):
            continue
        out.append(
            {
                "session_id": s.get("session_id"),
                "agent": s.get("agent"),
                "band": raw.get("band", "unknown"),
                "risk_score": raw.get("risk_score"),
                "escalate": bool(raw.get("escalate", False)),
                "needs_verify": bool(raw.get("needs_verify", False)),
                "provider_verified": bool(raw.get("provider_verified", False)),
                "blocker_kind": raw.get("blocker_kind", "NONE"),
            }
        )
    return out


def _risks_from_assessments(assessments: List[Dict[str, Any]]) -> Dict[str, Any]:
    """DERIVED: risk band distribution + escalations from live assessments."""
    bands: Dict[str, int] = {}
    escalations = 0
    high_risk_sessions: List[str] = []
    for a in assessments:
        band = str(a.get("band") or "unknown")
        bands[band] = bands.get(band, 0) + 1
        if a.get("escalate"):
            escalations += 1
        if band in ("critical", "high"):
            sid = a.get("session_id")
            if sid:
                high_risk_sessions.append(sid)
    return {
        "band_distribution": bands,
        "escalation_count": escalations,
        "high_risk_session_count": len(high_risk_sessions),
        "high_risk_session_ids": high_risk_sessions,
    }


def _budget_from_directives(directives: List[Dict[str, Any]]) -> Dict[str, Any]:
    """DERIVED: directive-rate / cost counters from the persisted directive rows.

    Cost is looked up through ``policy.COST_UNITS_PER_ACTION`` (fail-closed): an
    action string with no cost entry is recorded as UNCOSTABLE rather than
    silently billed as 0.0 — under-counting would make the cost dimension
    fail-open, which is the D-34 defect class.
    """
    total = len(directives)
    escalations = retries = 0
    cost_units = 0.0
    uncostable: List[str] = []
    for d in directives:
        action = d.get("action")
        if action == "ESCALATE":
            escalations += 1
        elif action == "RETRY":
            retries += 1
        try:
            cost_units += float(COST_UNITS_PER_ACTION[DirectiveAction(action)])
        except (KeyError, ValueError):
            if action not in uncostable:
                uncostable.append(str(action))
    return {
        "directive_count": total,
        "escalations": escalations,
        "retries": retries,
        "cost_units_used": cost_units,
        "uncostable_actions": uncostable,
        "cost_fully_known": len(uncostable) == 0,
    }


def _capabilities(capability_matrix_path: Optional[str]) -> Dict[str, Any]:
    """RELEASE (or UNKNOWN): the frozen per-agent actuation capability matrix."""
    path = capability_matrix_path or str(DEFAULT_CAPABILITY_MATRIX)
    if not path or not os.path.exists(path):
        return _section(
            "UNKNOWN",
            None,
            reason="capability matrix artifact not present in this build",
        )
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return _section(
            "RELEASE",
            data,
            reason=os.path.relpath(path, Path(__file__).resolve().parents[2]),
        )
    except (OSError, ValueError) as exc:
        return _section(
            "UNKNOWN", None, reason=f"capability matrix unreadable: {exc}"
        )


def _shadow(engine: Any) -> Dict[str, Any]:
    """LIVE Jev shadow surface, gated to record_source=='live' telemetry.

    Wires to ``engine.shadow_summary()`` (v1.2 Wave 7). The engine returns
    ``None`` when there is no live telemetry to supervise -- that is an HONEST
    gap (not a fabricated empty-clean summary), so it is rendered as UNKNOWN
    with ``data: null``. A shadow that counts replay/demo records would be the
    D-34 fake-clean defect; the gate in ``CoreEngine.shadow_summary`` excludes
    them by construction.
    """
    summary = getattr(engine, "shadow_summary", None)
    if callable(summary):
        try:
            data = summary()
            if data is None:
                return _section(
                    "UNKNOWN",
                    None,
                    reason="no live telemetry records ingested; shadow gate has "
                    "nothing to supervise (all records are replay/demo or none exist)",
                )
            return _section("LIVE", data)
        except Exception as exc:  # noqa: BLE001 -- defensive
            return _section("UNKNOWN", None, reason=f"shadow read failed: {exc}")
    return _section(
        "UNKNOWN",
        None,
        reason="shadow plane not wired in this build (see Wave 7)",
    )


def _system(engine: Any, config: Any) -> Dict[str, Any]:
    """LIVE: read the running provider/actuation/policy facts.

    Deliberately reports NO invented ``status`` / ``healthy`` boolean. The UI
    badges off ``provider_is_real`` / ``actuation_enabled`` (both LIVE facts),
    which makes "mock dressed up as real" impossible to render.
    """
    provider = getattr(engine, "provider", None)
    return {
        "provider_name": getattr(provider, "name", "UNKNOWN"),
        "provider_mode": getattr(provider, "mode", "UNKNOWN"),
        "provider_verified": bool(getattr(provider, "verified", False)),
        "provider_is_real": bool(getattr(provider, "is_real", False)),
        "actuation_enabled": bool(getattr(getattr(engine, "actuator", None), "enabled", False)),
        "policy_version": getattr(getattr(config, "policy", None), "version", "UNKNOWN"),
        "wal": bool(getattr(getattr(engine, "store", None), "is_wal", False)),
        "localhost_only": True,
        "build": "jcc-v1.2-visual",
        "projection_only": True,
        "green_protected": True,
    }


def build_scene(
    engine: Any,
    config: Any,
    *,
    capability_matrix_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Assemble the ``jcc-scene/1`` projection. Never raises on bad input.

    Returns one document with the meta block plus ten labelled sections. Each
    section independently degrades to UNKNOWN on failure so a single malformed
    store row cannot take down the whole world view.
    """
    # --- sessions (LIVE, root of most DERIVED sections) --------------------
    sessions: List[Dict[str, Any]] = []
    try:
        sessions = list(getattr(engine.store, "list_sessions")() or [])
        sessions_section = _section("LIVE", sessions)
    except Exception as exc:  # noqa: BLE001 -- store read failed
        sessions_section = _section("UNKNOWN", None, reason=f"store read failed: {exc}")

    # --- directives (LIVE) ------------------------------------------------
    directives: List[Dict[str, Any]] = []
    try:
        directives = list(getattr(engine.store, "get_directives")() or [])
        directives_section = _section("LIVE", directives)
    except Exception as exc:  # noqa: BLE001
        directives_section = _section("UNKNOWN", None, reason=f"directive read failed: {exc}")

    # --- agents (DERIVED from live sessions) ------------------------------
    try:
        agents_data = _agents_from_sessions(sessions if sessions_section["source"] == "LIVE" else [])
        agents_section = _section("DERIVED", agents_data)
    except Exception as exc:  # noqa: BLE001
        agents_section = _section("UNKNOWN", None, reason=f"agent derive failed: {exc}")

    # --- assessments (LIVE) ----------------------------------------------
    try:
        assessments = _assessments_from_sessions(sessions if sessions_section["source"] == "LIVE" else [])
        assessments_section = _section("LIVE", assessments)
    except Exception as exc:  # noqa: BLE001
        assessments_section = _section("UNKNOWN", None, reason=f"assessment derive failed: {exc}")

    # --- risks (DERIVED from live assessments) ---------------------------
    try:
        risks = _risks_from_assessments(assessments if assessments_section["source"] == "LIVE" else [])
        risks_section = _section("DERIVED", risks)
    except Exception as exc:  # noqa: BLE001
        risks_section = _section("UNKNOWN", None, reason=f"risk derive failed: {exc}")

    # --- budget (DERIVED from live directives) ---------------------------
    try:
        budget = _budget_from_directives(directives if directives_section["source"] == "LIVE" else [])
        budget_section = _section("DERIVED", budget)
    except Exception as exc:  # noqa: BLE001
        budget_section = _section("UNKNOWN", None, reason=f"budget derive failed: {exc}")

    # --- capabilities (RELEASE / UNKNOWN) ---------------------------------
    capabilities_section = _capabilities(capability_matrix_path)

    # --- shadow (UNKNOWN until Wave 7) -----------------------------------
    shadow_section = _shadow(engine)

    # --- audit_summary (LIVE) --------------------------------------------
    try:
        rows = list(getattr(engine.store, "get_audit")(limit=1000) or [])
        intact, chain_count = getattr(engine.store, "verify_audit")()
        seqs = [r.get("seq") for r in rows if isinstance(r, dict) and r.get("seq") is not None]
        audit_summary = {
            "window_entries": len(rows),
            "chain_intact": bool(intact),
            "chain_entries": int(chain_count),
            "first_seq": min(seqs) if seqs else None,
            "last_seq": max(seqs) if seqs else None,
            "truncated": len(rows) >= 1000,
        }
        audit_section = _section("LIVE", audit_summary)
    except Exception as exc:  # noqa: BLE001
        audit_section = _section("UNKNOWN", None, reason=f"audit read failed: {exc}")

    # --- system (LIVE) ---------------------------------------------------
    try:
        system_section = _section("LIVE", _system(engine, config))
    except Exception as exc:  # noqa: BLE001
        system_section = _section("UNKNOWN", None, reason=f"system read failed: {exc}")

    return {
        "schema_version": SCENE_SCHEMA_VERSION,
        "generated_at": time.time(),
        "source_legend": SOURCE_LEGEND,
        "projection_only": True,
        "system": system_section,
        "agents": agents_section,
        "sessions": sessions_section,
        "assessments": assessments_section,
        "directives": directives_section,
        "risks": risks_section,
        "capabilities": capabilities_section,
        "shadow": shadow_section,
        "budget": budget_section,
        "audit_summary": audit_section,
    }
