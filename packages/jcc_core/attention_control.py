"""Wave 4 — attention control resolution.

The inbox in Wave 1/2 is honest but inert: it lists every mission in
``NEEDS_ATTENTION`` with its kind and reason. Wave 4 turns it into a control
surface, and the entire design exists to keep one distinction that a naive
implementation would collapse:

    POLICY PERMITTED    -- does this action exist in the deterministic policy
                           vocabulary?  A fact about the RULES. Stable.
    CURRENTLY EXECUTABLE -- do the prerequisites hold on THIS host, NOW?
                           A fact about the WORLD. Changes while you look.

A mission can be policy-permitted but not executable (the action is legal, but
there is no live session to run it on). Rendering that as a green button would be
a lie, so the two are reported separately and never merged.

Two further rules, both enforced here rather than in prose:

  * ``UNKNOWN`` is never ``EXECUTABLE``. If a prerequisite cannot be determined,
    the answer is UNKNOWN and the UI must render it neutral. "I could not check"
    must never fold into "it is fine" (the probe-tri-state discipline).
  * Every non-executable action carries a ``BlockedReason`` from a CLOSED
    vocabulary. Free text cannot be rendered honestly -- the UI cannot know which
    note to highlight -- so the reason is an enum and the UI maps it to copy.

This module is pure: it takes the inbox items and a pre-gathered ``context`` and
returns resolutions. It performs no I/O, so its truth table is unit-testable and
cannot drift from a live probe.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Set

from pydantic import BaseModel, Field


class Executability(str, Enum):
    """Whether an action's prerequisites hold on this host, right now."""

    EXECUTABLE = "EXECUTABLE"   # every prerequisite is satisfied NOW
    BLOCKED = "BLOCKED"         # a known prerequisite is missing (see blocked_reason)
    UNKNOWN = "UNKNOWN"         # could not be determined -- NEVER rendered as green


class BlockedReason(str, Enum):
    """Closed vocabulary for why an action is not currently executable."""

    NO_LIVE_SESSION = "NO_LIVE_SESSION"
    NO_PENDING_APPROVAL = "NO_PENDING_APPROVAL"
    CONNECTION_NOT_CONTROL_VERIFIED = "CONNECTION_NOT_CONTROL_VERIFIED"
    CONNECTION_UNKNOWN = "CONNECTION_UNKNOWN"
    ACTUATION_DISABLED = "ACTUATION_DISABLED"
    ALREADY_RESOLVED = "ALREADY_RESOLVED"
    NOT_IN_ATTENTION = "NOT_IN_ATTENTION"


#: Action verbs. Closed: an action not in here cannot be proposed or applied.
ACTION_RESUME = "resume"
ACTION_DECIDE_APPROVAL = "decide_approval"
ACTION_VERIFY = "verify"
ACTION_ACKNOWLEDGE_DELIVERABLE = "acknowledge_deliverable"
ACTION_RECONNECT = "reconnect"

ACTION_VERBS: tuple[str, ...] = (
    ACTION_RESUME, ACTION_DECIDE_APPROVAL, ACTION_VERIFY,
    ACTION_ACKNOWLEDGE_DELIVERABLE, ACTION_RECONNECT,
)

#: Risk level. "high" means the action RE-ENTERS WORK (or otherwise has effect on
#: a running agent), so it must clear an explicit approval step in the UI and is
#: refused outright while actuation is disabled.
RISK_LOW = "low"
RISK_HIGH = "high"

#: kind -> (verb, human label, risk). The deterministic policy table. A kind
#: absent from here yields NO proposed action (never a default).
_KIND_ACTION: Dict[str, tuple[str, str, str]] = {
    "APPROVAL_REQUIRED": (ACTION_DECIDE_APPROVAL, "Decide approval", RISK_LOW),
    # A launch whose workload spawned but never produced a model turn. The kind
    # is the approval's own kind (see ``launch_control.APPROVAL_HOST_ACTIVATION``)
    # and it reaches this table only because the inbox now surfaces
    # approval-backed rows. Spelled as a literal on purpose: this module must
    # stay free of imports that pull in host I/O, so the coupling is asserted by
    # a test instead -- an undetectable duplicate would be a silent drift.
    "LAUNCH_HOST_ACTIVATION": (ACTION_DECIDE_APPROVAL, "Decide approval",
                               RISK_LOW),
    "AGENT_STUCK": (ACTION_RESUME, "Resume run", RISK_HIGH),
    "POLICY_ESCALATION": (ACTION_RESUME, "Resume run", RISK_HIGH),
    "BUDGET_WARNING": (ACTION_RESUME, "Resume run", RISK_HIGH),
    "VERIFICATION_REQUIRED": (ACTION_VERIFY, "Mark verified", RISK_LOW),
    "CONNECTION_LOST": (ACTION_RECONNECT, "Reconnect", RISK_LOW),
    "DELIVERABLE_READY": (ACTION_ACKNOWLEDGE_DELIVERABLE,
                          "Acknowledge deliverable", RISK_LOW),
}


class AttentionAction(BaseModel):
    """One control action proposed for one attention item."""

    action: str
    label: str
    risk: str
    policy_permitted: bool
    executability: Executability
    blocked_reason: Optional[BlockedReason] = None
    detail: str = ""


class AttentionResolution(BaseModel):
    """The resolution a caller may render for one inbox item."""

    mission_id: str
    kind: Optional[str] = None
    actions: List[AttentionAction] = Field(default_factory=list)

    @property
    def any_executable(self) -> bool:
        return any(a.executability is Executability.EXECUTABLE for a in self.actions)

    @property
    def any_unknown(self) -> bool:
        return any(a.executability is Executability.UNKNOWN for a in self.actions)


def _blocked(verb: str, label: str, risk: str, reason: BlockedReason,
             detail: str) -> AttentionAction:
    return AttentionAction(
        action=verb, label=label, risk=risk, policy_permitted=True,
        executability=Executability.BLOCKED, blocked_reason=reason, detail=detail,
    )


def _executable(verb: str, label: str, risk: str, detail: str = "") -> AttentionAction:
    return AttentionAction(
        action=verb, label=label, risk=risk, policy_permitted=True,
        executability=Executability.EXECUTABLE, blocked_reason=None, detail=detail,
    )


def _unknown(verb: str, label: str, risk: str, detail: str) -> AttentionAction:
    return AttentionAction(
        action=verb, label=label, risk=risk, policy_permitted=True,
        executability=Executability.UNKNOWN, blocked_reason=None, detail=detail,
    )


def resolve_item(item: Dict[str, Any], context: Dict[str, Any]) -> AttentionResolution:
    """Resolve ONE inbox item into proposed control actions.

    ``item``    -- a row from ``DomainStore.needs_attention()``
                   (``mission_id``, ``kind``, ``reason``, ...).
    ``context`` -- pre-gathered facts about the host:
        ``in_attention``       : set of mission_ids currently NEEDS_ATTENTION
        ``live_session_ids``   : set of session_ids with liveness == live
        ``mission_session_ids``: {mission_id: session_id} for the mission's live run
        ``pending_approval_ids``: set of mission_ids with >=1 PENDING approval
        ``connection_capability``: {connection_ref_or_agent: capability string}
        ``actuation_enabled``  : bool (v1.3: always False)
    """
    mission_id = str(item.get("mission_id") or "")
    kind = item.get("kind")
    kind = str(kind) if kind else None

    res = AttentionResolution(mission_id=mission_id, kind=kind)

    # (0) The item must actually be in attention. A stale UI must not be able to
    #     act on an item the server has already resolved.
    in_attention: Set[str] = set(context.get("in_attention") or ())
    if mission_id not in in_attention:
        spec = _KIND_ACTION.get(kind or "")
        if spec:
            verb, label, risk = spec
            res.actions.append(_blocked(
                verb, label, risk, BlockedReason.NOT_IN_ATTENTION,
                "this mission is no longer in NEEDS_ATTENTION"))
        return res

    spec = _KIND_ACTION.get(kind or "")
    if spec is None:
        # An unrecognised kind yields NO action. Never guess a default verb.
        return res

    verb, label, risk = spec
    actuation_enabled = bool(context.get("actuation_enabled", False))

    if verb == ACTION_DECIDE_APPROVAL:
        pending: Set[str] = set(context.get("pending_approval_ids") or ())
        if mission_id in pending:
            res.actions.append(_executable(verb, label, risk,
                                           "a PENDING approval awaits a decision"))
        else:
            res.actions.append(_blocked(
                verb, label, risk, BlockedReason.NO_PENDING_APPROVAL,
                "the attention flag says an approval is required, but no PENDING "
                "approval exists for this mission"))
        return res

    if verb == ACTION_VERIFY or verb == ACTION_ACKNOWLEDGE_DELIVERABLE:
        # A human judgement needs no host prerequisite.
        res.actions.append(_executable(verb, label, risk,
                                       "a human check needs no host prerequisite"))
        return res

    if verb == ACTION_RECONNECT:
        cap_map: Dict[str, str] = dict(context.get("connection_capability") or {})
        ref = item.get("connection_ref") or item.get("agent")
        cap = cap_map.get(str(ref)) if ref is not None else None
        if cap is None:
            res.actions.append(_unknown(
                verb, label, risk,
                "the connection's capability could not be read (UNKNOWN, not fine)"))
        elif cap == "CONTROL_VERIFIED":
            res.actions.append(_executable(verb, label, risk,
                                           "the connection is CONTROL_VERIFIED"))
        else:
            res.actions.append(_blocked(
                verb, label, risk, BlockedReason.CONNECTION_NOT_CONTROL_VERIFIED,
                "the connection capability is %s, not CONTROL_VERIFIED" % cap))
        return res

    if verb == ACTION_RESUME:
        # Resume re-enters work: it is high risk, so actuation must be ON, and a
        # live session must actually exist for this mission's run.
        if not actuation_enabled:
            res.actions.append(_blocked(
                verb, label, risk, BlockedReason.ACTUATION_DISABLED,
                "actuation is disabled in this build; resuming would re-enter work"))
            return res
        live: Set[str] = set(context.get("live_session_ids") or ())
        ms: Dict[str, Any] = dict(context.get("mission_session_ids") or {})
        sid = ms.get(mission_id)
        if sid is None:
            res.actions.append(_blocked(
                verb, label, risk, BlockedReason.NO_LIVE_SESSION,
                "no session is associated with this mission's run"))
        elif str(sid) in live:
            res.actions.append(_executable(verb, label, risk,
                                           "a live session is available"))
        else:
            res.actions.append(_blocked(
                verb, label, risk, BlockedReason.NO_LIVE_SESSION,
                "the mission's session is not live"))
        return res

    # Unknown verb in the table: no action rather than a fabricated one.
    return res


def resolve_attention(items: Iterable[Dict[str, Any]],
                      context: Dict[str, Any]) -> List[AttentionResolution]:
    """Resolve every inbox item. Order is preserved from ``items``."""
    return [resolve_item(dict(it), context) for it in items]


def gather_context(domain: Any, *, actuation_enabled: bool = False,
                   live_session_ids: Optional[Iterable[str]] = None,
                   ) -> Dict[str, Any]:
    """Build the resolver context from the store.

    ``live_session_ids`` is passed in (not computed here) because host liveness
    comes from ``workbuddy_launch``, which this module must not import -- keeping
    the resolver free of host I/O is what makes its truth table testable.
    """
    items = domain.needs_attention()
    in_attention = {str(i.get("mission_id")) for i in items if i.get("mission_id")}

    # A mission's live-run session: the NEWEST run that carries a session (runs
    # come back oldest-first, so scan in reverse and take the first hit).
    mission_session_ids: Dict[str, str] = {}
    for mid in in_attention:
        runs = list(domain.list_runs(mid) if hasattr(domain, "list_runs") else [])
        for r in reversed(runs):
            sid = (r.get("session_id") if isinstance(r, dict)
                   else getattr(r, "session_id", None))
            if sid:
                mission_session_ids[mid] = str(sid)
                break

    pending_approval_ids: Set[str] = set()
    try:
        for a in domain.list_approvals(state="PENDING"):
            pending_approval_ids.add(str(a.mission_id))
    except Exception:  # noqa: BLE001 -- a store without approvals is still valid
        pass

    connection_capability: Dict[str, str] = {}
    for ref in {str(i.get("connection_ref") or i.get("agent") or "")
                for i in items}:
        if not ref:
            continue
        try:
            connection_capability[ref] = "UNKNOWN"
            for c in domain.list_connections():
                if str(c.connection_id) == ref or str(c.agent) == ref:
                    if domain.is_control_verified(str(c.connection_id)):
                        connection_capability[ref] = "CONTROL_VERIFIED"
                    else:
                        cap = domain.get_capability(
                            str(c.connection_id), _any_capability_kind())
                        connection_capability[ref] = (
                            cap.kind.value if cap else "TELEMETRY_ONLY")
                    break
        except Exception:  # noqa: BLE001
            connection_capability[ref] = "UNKNOWN"

    return {
        "in_attention": in_attention,
        "live_session_ids": set(live_session_ids or ()),
        "mission_session_ids": mission_session_ids,
        "pending_approval_ids": pending_approval_ids,
        "connection_capability": connection_capability,
        "actuation_enabled": actuation_enabled,
    }


def _any_capability_kind() -> Any:
    from .domain import CapabilityKind  # local import: keep module import-light
    return CapabilityKind.ADAPTER_CONTRACT_LIVE
