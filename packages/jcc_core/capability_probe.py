"""Wave 7 — agent connection verification.

THE HOLE THIS CLOSES
--------------------
v1.3 gives a domain ``Connection`` a ``Capability`` and gates effectful action on
``CONTROL_VERIFIED``:

    def is_control_verified(connection_id) -> bool:
        return get_capability(connection_id, CONTROL_VERIFIED) is not None

But until this module nothing *measured* that capability. The only way a domain
connection became ``CONTROL_VERIFIED`` was a human typing the string into
``POST /connections/{cid}/capability``. So the one gate that permits effectful
action could be satisfied by a bare assertion with no evidence.

THE TWO RULES
-------------
1. **A capability is MEASURED, not asserted.** ``probe_connection`` runs a real
   read-only probe and returns what it OBSERVED. A caller selects *which* probe
   runs; it can never state the outcome.

2. **Claim only what you can prove, and say what you cannot.** v1.3 keeps
   actuation DISABLED, so it never exercises a control path. Therefore this probe
   **cannot** honestly yield ``CONTROL_VERIFIED`` and it must not: the ceiling of
   what a v1.3 probe may claim is ``ADAPTER_CONTRACT_LIVE`` (we can see the agent
   and speak its adapter contract). ``CONTROL_VERIFIED`` stays reachable only by
   citing external evidence for a control path that was actually exercised.

This is deliberately self-limiting. A naive "verify" button would light up green
everywhere; the honest one reports exactly the ceiling of what it did.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .domain import CapabilityKind

#: Agents this build knows how to probe.
KNOWN_AGENTS = ("workbuddy", "claude", "codex", "hermes")


@dataclass
class ProbeResult:
    """What a probe OBSERVED. ``kind`` is an observation, never a caller input."""

    kind: CapabilityKind
    detail: str
    evidence: Dict[str, Any] = field(default_factory=dict)
    evidence_ref: Optional[str] = None
    reason: Optional[str] = None
    measured_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind.value,
            "detail": self.detail,
            "evidence": self.evidence,
            "evidence_ref": self.evidence_ref,
            "reason": self.reason,
            "measured_at": self.measured_at,
        }


def _evidence_ref(evidence: Dict[str, Any]) -> str:
    """A stable digest of the observation, so a claim can be re-checked."""
    blob = json.dumps(evidence, sort_keys=True, default=str).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()


def _unknown(detail: str, reason: str, evidence: Optional[Dict[str, Any]] = None) -> ProbeResult:
    ev = evidence or {}
    return ProbeResult(kind=CapabilityKind.UNKNOWN, detail=detail, evidence=ev,
                       evidence_ref=_evidence_ref(ev) if ev else None, reason=reason)


def _probe_workbuddy() -> ProbeResult:
    """Real session discovery (Wave 3). A live session is SEEING, not controlling."""
    try:
        from . import workbuddy_launch as wl
    except Exception as exc:  # noqa: BLE001
        return _unknown("the workbuddy launcher could not be imported",
                        "import_failed:%s" % type(exc).__name__)
    try:
        sessions = wl.list_sessions()
    except Exception as exc:  # noqa: BLE001
        return _unknown("session discovery raised", "discovery_failed:%s" % type(exc).__name__)

    live: list[Dict[str, Any]] = []
    stale_or_dead = 0
    for s in sessions:
        try:
            state = str(wl.liveness(s).get("state") or "unknown")
        except Exception:  # noqa: BLE001 -- a single unreadable record must not blind the probe
            state = "unknown"
        if state == "live":
            live.append({"session_id": s.get("sessionId"), "pid": s.get("pid"),
                         "state": state})
        else:
            stale_or_dead += 1

    evidence = {
        "agent": "workbuddy",
        "sessions_seen": len(sessions),
        "live_sessions": len(live),
        "live": live[:10],
        "stale_or_dead": stale_or_dead,
        "heartbeat_fresh_seconds": getattr(wl, "HEARTBEAT_FRESH_SECONDS", None),
    }
    if live:
        # We can SEE it and the adapter can normalise it. We did NOT exercise a
        # control path (actuation is off), so this is the honest ceiling.
        return ProbeResult(
            kind=CapabilityKind.ADAPTER_CONTRACT_LIVE,
            detail=("a live WorkBuddy session is visible and its adapter contract "
                    "is live; no control path was exercised, so this is not "
                    "CONTROL_VERIFIED"),
            evidence=evidence, evidence_ref=_evidence_ref(evidence))
    if sessions:
        return ProbeResult(
            kind=CapabilityKind.TELEMETRY_ONLY,
            detail=("WorkBuddy sessions exist but none is live (heartbeat stale); "
                    "we have evidence of contact, not a live link"),
            evidence=evidence, evidence_ref=_evidence_ref(evidence))
    return ProbeResult(
        kind=CapabilityKind.UNKNOWN,
        detail="no WorkBuddy session was found on this host",
        evidence=evidence, evidence_ref=_evidence_ref(evidence),
        reason="no_sessions_found")


def _probe_adapter(agent: str) -> ProbeResult:
    """Read-only adapter probe for the non-WorkBuddy agents."""
    try:
        from .adapters import adapter_for
        ad = adapter_for(agent)
    except Exception as exc:  # noqa: BLE001
        return _unknown("the adapter registry could not be read",
                        "import_failed:%s" % type(exc).__name__)
    if ad is None:
        return _unknown("no adapter is registered for agent %r" % agent, "no_adapter")
    try:
        report = asyncio.run(ad.probe())
    except Exception as exc:  # noqa: BLE001
        return _unknown("the adapter probe raised", "probe_raised:%s" % type(exc).__name__,
                        {"agent": agent})
    if not isinstance(report, dict):
        return _unknown("the adapter probe returned a non-object", "probe_bad_shape",
                        {"agent": agent})
    evidence = {"agent": agent, "installed": bool(report.get("installed")),
                "verified": bool(report.get("verified")),
                "status": report.get("status"), "version": report.get("version")}
    if report.get("verified") and report.get("installed"):
        # The adapter can speak for this agent. That is TELEMETRY_ONLY: we have a
        # contract, we have NOT driven the agent.
        return ProbeResult(
            kind=CapabilityKind.TELEMETRY_ONLY,
            detail=("the %s adapter contract is present and its probe verified; no "
                    "live link or control path was exercised" % agent),
            evidence=evidence, evidence_ref=_evidence_ref(evidence))
    return ProbeResult(
        kind=CapabilityKind.UNKNOWN,
        detail="the %s adapter is not verifiable on this host" % agent,
        evidence=evidence, evidence_ref=_evidence_ref(evidence),
        reason="adapter_not_verified")


def probe_connection(agent: str, *, domain: Any = None, engine: Any = None) -> ProbeResult:
    """Run a REAL read-only probe for ``agent`` and return the observation.

    ``domain``/``engine`` are accepted for signature stability and future probes;
    this build needs neither (the probe reads host state directly).

    INVARIANT: this function NEVER returns ``CONTROL_VERIFIED``. v1.3 cannot
    exercise a control path, so claiming it would be a lie. Asserted by tests.
    """
    a = str(agent or "").strip().lower()
    if a == "workbuddy":
        return _probe_workbuddy()
    if a in ("claude", "codex", "hermes"):
        return _probe_adapter(a)
    return _unknown("unknown agent %r" % agent, "unknown_agent", {"agent": agent})
