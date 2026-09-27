"""§20 v1.1 panels — every field traceable to ONE declared source.

THE RULE THIS MODULE ENFORCES
-----------------------------
A panel is only allowed to exist in one of two shapes::

    {"available": True,  "source": <named>, "data": {...}}
    {"available": False, "source": <named>, "reason": <non-empty>}

There is no third shape, and in particular there is no "available with a
placeholder". The existing UI already states this discipline for the v1.0
screens ("it does not fabricate a number"); §20 makes it structural, because the
v1.1 panels are exactly where a number would be tempting — actuation level,
observation counts, cost. Every one of those has a truthful "we do not know"
answer and no useful invented one.

THE SEVEN §20 PANELS AND WHERE EACH VALUE COMES FROM
----------------------------------------------------
===================  ==========================================================
Provider badge       LIVE ``/api/health`` (§7D fields). The four-state
                     vocabulary is read off the live provider object, so a mock
                     cannot be dressed up as real by editing a document.
Capability matrix    ``artifacts/release/V1_1_CAPABILITY_MATRIX.json`` — the
                     FROZEN §10 record, already re-verified against the
                     filesystem when it was emitted. The UI must not re-probe:
                     a second probe would be a second answer.
Shadow stats         the §19 gate artifact's recorded readings
                     (observed_decisions / span / actuated / chain_intact).
Actuation level      ``actuation_levels.describe()`` embedded in the §19 gate
Pending activation   artifact. The ladder is emitted from the same table the
                     enforcement reads, so the UI cannot describe a ladder
                     nobody runs.
Budget               config ``p3.shadow.budget`` limits (the real file) plus
                     ``budget_report.describe()``. USAGE is deliberately
                     reported as unavailable-with-a-reason: the control plane
                     exposes no budget endpoint.
Directive            LIVE ``/api/decisions``.
===================  ==========================================================

WHY NOTHING HERE READS THE DATABASE
-----------------------------------
Two reasons, both measured rather than assumed.

1. ``StateStore.__init__`` runs ``_init_db()`` — it WRITES (``executescript``
   plus a commit). Constructing one against the protected runtime database from
   a UI process would be a write to a database the v1.0 service is using.
2. Reading it read-only has its own trap. ``mode=ro`` against this database
   reports ``journal_mode=wal`` and can create ``-shm``/``-wal`` side files;
   ``immutable=1`` avoids touching anything but makes SQLite IGNORE the WAL, so
   it can silently read a pre-checkpoint state. For the §19 emitter that is an
   acceptable trade (a missed observation can only push the verdict toward
   PENDING, the fail-closed direction) and it is documented there. A UI panel
   that showed a stale reading as if it were live would be reporting a number it
   has no basis for, so this module does not read the tape at all.

The honest consequence, stated rather than hidden: LIVE shadow stats need a
control-plane endpoint, and ``packages/jcc_core/server.py`` is one of the twelve
A8-pinned production artifacts, so adding a route is an A8 re-baseline and not a
UI change. Until then the panel renders the RELEASE-TIME readings and names them
as such.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from control_ui.paths import REPO_ROOT

#: The seven panels, in render order.
PANEL_NAMES = (
    "provider",
    "capability_matrix",
    "shadow_stats",
    "actuation",
    "pending_activation",
    "budget",
    "directive",
)

CAPABILITY_ARTIFACT = "artifacts/release/V1_1_CAPABILITY_MATRIX.json"
GATE_ARTIFACT = "artifacts/release/V1_1_LIVE_SHADOW_GATE.json"
VERIFICATION_ARTIFACT = "artifacts/release/V1_1_REAL_JEV_VERIFICATION.json"

#: Reasons a panel can be unavailable. Frozen strings so a test can assert the
#: vocabulary rather than guessing at prose.
REASON_ARTIFACT_MISSING = "artifact_missing"
REASON_ARTIFACT_UNREADABLE = "artifact_unreadable"
REASON_API_UNREACHABLE = "api_unreachable"
REASON_NO_ENDPOINT = "no_endpoint"
REASON_NO_SESSION = "no_session"


def _panel(name: str, source: str, *, data: Optional[Dict[str, Any]] = None,
           reason: Optional[str] = None, detail: str = "") -> Dict[str, Any]:
    """Build a panel in one of the two legal shapes. Never both, never neither."""
    if reason is None:
        if data is None:
            raise ValueError(f"panel {name!r} must carry data or a reason")
        return {"panel": name, "source": source, "available": True, "data": data}
    if data is not None:
        raise ValueError(f"panel {name!r} may not be available AND unavailable")
    return {"panel": name, "source": source, "available": False,
            "reason": reason, "detail": detail}


def read_artifact(rel: str) -> Dict[str, Any]:
    """Read a release artifact, distinguishing missing from unreadable.

    The two are separate reasons on purpose — the same three-state rule that
    ``live_shadow_gate`` applies to the tape. "The file is not there" and "the
    file is there and I could not parse it" need different responses from an
    operator, and collapsing them costs exactly the information that matters.
    """
    path = Path(REPO_ROOT) / rel
    if not path.is_file():
        return {"ok": False, "reason": REASON_ARTIFACT_MISSING, "detail": rel}
    try:
        return {"ok": True, "data": json.loads(path.read_text(encoding="utf-8"))}
    except Exception as exc:  # noqa: BLE001 — a corrupt artifact is a finding
        return {"ok": False, "reason": REASON_ARTIFACT_UNREADABLE,
                "detail": f"{rel}: {type(exc).__name__}: {exc}"}


def verification_decision() -> Dict[str, Any]:
    """The §7 ``decision`` (plus ``smoke``) block, or a reason it could not be read.

    Used for ONE purpose: reconciling the live provider state against the
    recorded verification verdict. Two sources are only worth carrying when a
    disagreement between them is more informative than either one alone -- and
    for the provider it is, because the whole §7D design is that a *requested*
    real provider and an *effective* real provider are different things. Showing
    only the live value would hide a stale verification claim; showing only the
    artifact would hide a provider that changed under it.

    Read once per panel build and passed around: the artifact is the same file
    for both callers, and re-reading it would let the two panels disagree within
    one render if the file changed between them.
    """
    got = read_artifact(VERIFICATION_ARTIFACT)
    if not got["ok"]:
        return {"ok": False, "reason": got["reason"], "detail": got["detail"]}
    doc = got["data"] or {}
    decision = doc.get("decision") or {}
    if not decision:
        return {"ok": False, "reason": REASON_ARTIFACT_UNREADABLE,
                "detail": f"{VERIFICATION_ARTIFACT}: no decision block"}
    return {"ok": True, "data": decision, "smoke": doc.get("smoke") or {}}


# --------------------------------------------------------------------------- #
# Provider badge
# --------------------------------------------------------------------------- #
def provider_badge(health: Dict[str, Any]) -> str:
    """The single word a reader may key off. Derived from the LIVE fields only.

    ``provider_is_real`` is the load-bearing input. A mode string alone is not
    enough: the whole §7D point is that ``provider=real`` can be *requested* while
    the effective provider is a fail-closed ``DisabledProvider``, and those two
    situations must not render the same.
    """
    if not health.get("provider_is_real"):
        state = str(health.get("provider_activation_state") or "").upper()
        if state in ("PENDING_EXTERNAL_ACTIVATION", "PENDING"):
            return "PENDING_ACTIVATION"
        mode = str(health.get("provider_mode") or "").upper()
        if mode == "DISABLED":
            return "DISABLED"
        if mode == "MOCK":
            return "MOCK"
        return "NOT_REAL"
    return "REAL" if health.get("provider_verified") else "REAL_UNVERIFIED"


def provider_panel(health: Dict[str, Any]) -> Dict[str, Any]:
    """Live §7D fields, plus the §7 recorded verdict as a labelled "as of".

    The recorded verdict is NOT compared field-by-field against the live one.
    The first draft did that and raised a divergence on every render driven by
    the UI test harness -- live ``provider_mode=MOCK`` against a recorded ``REAL``
    -- because the two records describe *different configurations* (the harness
    runs mock; the repository config requests real). A warning that fires on the
    normal case is worse than no warning: it teaches the reader to ignore it.

    So there is exactly one reconciliation rule, and it is the only difference
    that is alarming rather than expected: the live provider claiming to be real
    while the recorded interface card is not verified. Everything else is
    reported side by side, labelled with where it came from.
    """
    source = "live /api/health (§7D provider-mode contract)"
    if not health.get("ok"):
        return _panel("provider", source, reason=REASON_API_UNREACHABLE,
                      detail=str(health.get("detail") or health.get("error") or ""))
    h = health["data"]
    if not isinstance(h, dict) or "provider_mode" not in h:
        return _panel("provider", source, reason=REASON_NO_ENDPOINT,
                      detail="/api/health answered without the §7D provider fields")

    recorded = verification_decision()
    rec = recorded["data"] if recorded["ok"] else None

    safety_divergence = None
    if rec is not None and h.get("provider_is_real") and not rec.get("provider_verified"):
        safety_divergence = (
            "the live provider reports is_real=true while the recorded §7 "
            f"interface card is not verified (real_jev={rec.get('real_jev')}); "
            "a real provider in an unverified release is the one case worth an "
            "alarm"
        )

    return _panel("provider", source, data={
        "badge": provider_badge(h),
        "provider_mode": h.get("provider_mode"),
        "provider_requested_mode": h.get("provider_requested_mode"),
        "provider_is_real": h.get("provider_is_real"),
        "provider_verified": h.get("provider_verified"),
        "provider_activation_state": h.get("provider_activation_state"),
        "jev_provider": h.get("jev_provider"),
        "actuation_enabled_configured": h.get("actuation_enabled"),
        "policy_version": h.get("policy_version"),
        # -- the §7 record, shown as a labelled reading and not as a verdict --
        "verified_artifact": VERIFICATION_ARTIFACT,
        "recorded_real_jev": rec.get("real_jev") if rec else None,
        "recorded_provider_mode": rec.get("provider_mode") if rec else None,
        "recorded_provider_verified": rec.get("provider_verified") if rec else None,
        "recorded_reason": None if recorded["ok"] else recorded.get("reason"),
        "recorded_detail": None if recorded["ok"] else recorded.get("detail"),
        "recorded_note": (
            "recorded at the §7 emission; the live fields above describe the "
            "running process, which may be a different configuration (e.g. a "
            "test harness on MOCK against a config requesting REAL)"
        ),
        "safety_divergence": safety_divergence,
    })


# --------------------------------------------------------------------------- #
# Capability matrix
# --------------------------------------------------------------------------- #
def capability_matrix_panel() -> Dict[str, Any]:
    source = CAPABILITY_ARTIFACT
    got = read_artifact(CAPABILITY_ARTIFACT)
    if not got["ok"]:
        return _panel("capability_matrix", source, reason=got["reason"], detail=got["detail"])
    doc = got["data"]
    agents = doc.get("agents") or {}
    rows = []
    for agent in sorted(agents):
        block = agents[agent] or {}
        counts = block.get("status_counts") or {}
        rows.append({
            "agent": agent,
            "adapter_state": block.get("adapter_state"),
            "verified": counts.get("VERIFIED", 0),
            "degraded": counts.get("DEGRADED", 0),
            "unsupported": counts.get("UNSUPPORTED", 0),
            "unverified": counts.get("UNVERIFIED", 0),
        })
    summary = doc.get("actuation_summary") or {}
    probe = doc.get("probe") or {}
    # Every key below was read off the emitted artifact, not guessed. The first
    # draft of this function asked for ``states`` and ``probe_source``, neither of
    # which the artifact has (the real keys are ``vocabulary`` and ``probe``), so
    # the panel silently rendered an empty list and a null -- a shape that looks
    # like data and carries none. A test now pins each key to the artifact.
    return _panel("capability_matrix", source, data={
        "rows": rows,
        # Named agent_count, not agents: the artifact has a real ``agents`` key
        # holding the per-agent dicts, and shadowing it with an integer would make
        # "where does this number come from" unanswerable by grep.
        "agent_count": len(rows),
        "actuable_capability_count": summary.get("actuable_capability_count"),
        "actuable": list(summary.get("actuable") or []),
        "vocabulary": list(doc.get("vocabulary") or []),
        "section": doc.get("section"),
        "schema": doc.get("schema"),
        "verified_total": sum(r["verified"] for r in rows),
        "telemetry_verified": sum(
            1 for r in rows
            if ((agents.get(r["agent"]) or {}).get("capabilities") or {})
            .get("telemetry", {}).get("status") == "VERIFIED"
        ),
        "probe_script": probe.get("script"),
        "probe_measurement": probe.get("measurement"),
        "evidence_dir": probe.get("evidence_dir"),
        # Named "recorded" on purpose: this is the head the artifact was emitted
        # at, which is not necessarily the head the reader is looking at.
        "repo_head_recorded": doc.get("repo_head"),
    })


# --------------------------------------------------------------------------- #
# The two §19 panels
# --------------------------------------------------------------------------- #
def _gate_readings(verdict: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": verdict.get("status"),
        "satisfied": verdict.get("satisfied"),
        "observed_decisions": verdict.get("observed_decisions"),
        "observed_span_s": verdict.get("observed_span_s"),
        "actuated_observations": verdict.get("actuated_observations"),
        "chain_intact": verdict.get("chain_intact"),
        "defects": list(verdict.get("defects") or []),
        "thresholds": verdict.get("thresholds"),
    }


def shadow_stats_panel() -> Dict[str, Any]:
    """RELEASE-TIME readings from the §19 gate, named as release-time.

    Not live, and the source string says so. Rendering a release-time reading as
    if it were current is the specific dishonesty this avoids — see the module
    docstring for why the UI does not open the tape itself.
    """
    source = f"{GATE_ARTIFACT} (release-time readings, not live)"
    got = read_artifact(GATE_ARTIFACT)
    if not got["ok"]:
        return _panel("shadow_stats", source, reason=got["reason"], detail=got["detail"])
    verdict = (got["data"] or {}).get("verdict") or {}
    readings = _gate_readings(verdict)
    if readings["observed_decisions"] is None:
        return _panel("shadow_stats", source, reason=REASON_ARTIFACT_UNREADABLE,
                      detail="the gate artifact carries no verdict readings")
    return _panel("shadow_stats", source, data=readings)


def actuation_panel(health: Dict[str, Any]) -> Dict[str, Any]:
    """The ladder, the gate's verdict, and the CONFIG-vs-GATE reconciliation."""
    source = GATE_ARTIFACT
    got = read_artifact(GATE_ARTIFACT)
    if not got["ok"]:
        return _panel("actuation", source, reason=got["reason"], detail=got["detail"])
    doc = got["data"] or {}
    head = doc.get("headline") or {}
    ladder = doc.get("actuation_ladder") or []

    configured_bool = None
    if health.get("ok") and isinstance(health.get("data"), dict):
        configured_bool = health["data"].get("actuation_enabled")

    # The reconciliation is the point of this panel. Two switches govern
    # effects: the v1.0 `actuation.enabled` boolean and the v1.1 gate. Showing
    # only one would let a reader believe effects are on (or off) when the other
    # one says otherwise.
    mismatch = None
    if configured_bool is not None and head.get("actuation_enabled") is not None:
        if bool(configured_bool) and not bool(head.get("actuation_enabled")):
            mismatch = ("actuation.enabled is true but the live-shadow gate is "
                        f"{head.get('status')}; the gate holds the rung down")
        elif not bool(configured_bool) and bool(head.get("actuation_enabled")):
            mismatch = "the gate permits actuation but actuation.enabled is false"

    return _panel("actuation", source, data={
        "effective_level": head.get("effective_level"),
        "configured_level": head.get("configured_level"),
        "granted_ceiling": head.get("granted_ceiling"),
        "gate_status": head.get("status"),
        "gate_satisfied": head.get("satisfied"),
        "actuation_enabled_gate": head.get("actuation_enabled"),
        "actuation_enabled_configured": configured_bool,
        "mismatch": mismatch,
        "ladder": ladder,
        "defects": list((doc.get("verdict") or {}).get("defects") or []),
        "db_present": (doc.get("db") or {}).get("present"),
        "note": doc.get("note"),
    })


def pending_activation_panel() -> Dict[str, Any]:
    """What is still NOT activated, and what would have to change.

    Every item below is a fact read from an artifact, not a to-do list written
    by hand: the gate's own defect list, the ceiling, and the provider state.
    """
    got = read_artifact(GATE_ARTIFACT)
    if not got["ok"]:
        return _panel("pending_activation", GATE_ARTIFACT,
                      reason=got["reason"], detail=got["detail"])
    doc = got["data"] or {}
    head = doc.get("headline") or {}
    verdict = doc.get("verdict") or {}
    vocab = doc.get("gate_vocabulary") or {}

    items: List[Dict[str, Any]] = []
    if not head.get("satisfied"):
        items.append({
            "item": "live_shadow_observation_window",
            "state": vocab.get("pending_status"),
            "why": "the observation criteria are not met",
            "defects": list(verdict.get("defects") or []),
        })
    if head.get("granted_ceiling") == "L0":
        items.append({
            "item": "actuation_ceiling",
            "state": "L0",
            "why": ("the gate's granted_ceiling is still L0, so meeting the "
                    "criteria alone would not enable effects"),
            "defects": [],
        })
    if not head.get("actuation_enabled"):
        items.append({
            "item": "actuation_enabled",
            "state": "false",
            "why": "the operator switch is off (and the gate does not permit it)",
            "defects": [],
        })

    # The provider is the OTHER thing this release leaves inert, and it is
    # recorded in a different artifact by a different emitter (§7E). Omitting it
    # because it lives elsewhere would make "Pending Activation" mean "pending
    # the shadow gate", which is not what an operator needs to know.
    recorded = verification_decision()
    rec = recorded["data"] if recorded["ok"] else None
    if rec is not None:
        closed = list((recorded.get("smoke") or {}).get("closed_gates") or [])
        if str(rec.get("real_jev") or "") != "ACTIVE":
            items.append({
                "item": "real_jev_provider",
                "state": rec.get("real_jev"),
                "why": ("§7E: the real provider stays inert this release; the "
                        "engineering line runs on MOCK / REPLAY / DISABLED"),
                "defects": closed,
            })
    return _panel("pending_activation", GATE_ARTIFACT, data={
        "items": items,
        "count": len(items),
        "headline_status": head.get("status"),
        "provider_artifact": VERIFICATION_ARTIFACT,
        "provider_artifact_reason": recorded.get("reason") if not recorded["ok"] else None,
    })


# --------------------------------------------------------------------------- #
# Budget
# --------------------------------------------------------------------------- #
def budget_panel(cfg: Any) -> Dict[str, Any]:
    """Limits from the REAL config; usage reported as unavailable-with-a-reason."""
    from supervisor import budget_report as BR

    shadow = (cfg.raw.get("p3") or {}).get("shadow") or {}
    raw_limits = dict(shadow.get("budget") or {})
    if not raw_limits:
        return _panel("budget", "config/jcc.yaml p3.shadow.budget",
                      reason=REASON_NO_ENDPOINT,
                      detail="the config declares no p3.shadow.budget block")
    return _panel("budget", "config/jcc.yaml p3.shadow.budget + budget_report vocabulary", data={
        "limits": raw_limits,
        "limit_count": len(raw_limits),
        "provenance_values": list(BR.describe()["provenance_values"]),
        "usage_available": False,
        "usage_reason": REASON_NO_ENDPOINT,
        "usage_detail": (
            "the control plane exposes no budget endpoint and this UI does not "
            "read the database, so USAGE is not shown — an invented 0 would be "
            "indistinguishable from a measured 0"
        ),
        "scorer_tokens_waived": "scorer_tokens" in (BR.describe()["dimensions"] or []),
    })


# --------------------------------------------------------------------------- #
# Directive
# --------------------------------------------------------------------------- #
def directive_panel(api: Any, session_id: Optional[str]) -> Dict[str, Any]:
    source = "live /api/decisions"
    from control_ui import model as _model

    got = _model._get_json(api, "/api/decisions" + (f"?sid={session_id}" if session_id else ""))
    if not got.get("ok"):
        return _panel("directive", source, reason=REASON_API_UNREACHABLE,
                      detail=str(got.get("detail") or got.get("error") or ""))
    rows = got["data"] if isinstance(got["data"], list) else []
    signed = sum(1 for r in rows if r.get("signed"))
    acted = sum(1 for r in rows if r.get("acted"))
    sessions = {r.get("session_id") for r in rows if r.get("session_id")}
    # `signed` and `signature` are two real columns; they are supposed to agree.
    # Counting the rows where they do not is a cheap, real check -- and it is the
    # only thing this panel can say about signature integrity, because the
    # endpoint returns the signature BLOB and no verification verdict. The first
    # draft rendered `signature_verified` and `executable`, neither of which is a
    # column, so both cells came out empty while looking like data.
    inconsistent = sum(
        1 for r in rows if bool(r.get("signed")) != bool(r.get("signature"))
    )
    origins: Dict[str, int] = {}
    for r in rows:
        key = str(r.get("origin") or "(none)")
        origins[key] = origins.get(key, 0) + 1
    return _panel("directive", source, data={
        "total": len(rows),
        "signed": signed,
        "unsigned": len(rows) - signed,
        "acted": acted,
        "signed_flag_but_no_signature_or_reverse": inconsistent,
        "origins": origins,
        "session_count": len(sessions),
        "latest": rows[-1] if rows else None,
        "scope": "all sessions" if not session_id else session_id,
        "fields_returned": sorted(rows[-1].keys()) if rows else [],
    })


# --------------------------------------------------------------------------- #
# The model
# --------------------------------------------------------------------------- #
def v1_1_model(api: Any, session_id: Optional[str] = None) -> Dict[str, Any]:
    """Build all seven panels. Runs even when the API is unreachable: the four
    artifact-backed panels do not depend on the control plane, and hiding them
    because one live endpoint is down would be throwing away real information."""
    from control_ui import model as _model

    health = _model._get_json(api, "/api/health")
    cfg = _model._config()

    panels = {
        "provider": provider_panel(health),
        "capability_matrix": capability_matrix_panel(),
        "shadow_stats": shadow_stats_panel(),
        "actuation": actuation_panel(health),
        "pending_activation": pending_activation_panel(),
        "budget": budget_panel(cfg),
        "directive": directive_panel(api, session_id),
    }
    return {
        "panels": panels,
        "order": list(PANEL_NAMES),
        "available_count": sum(1 for p in panels.values() if p["available"]),
        "unavailable_count": sum(1 for p in panels.values() if not p["available"]),
        "session_id": session_id,
    }
