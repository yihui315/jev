"""Wave 4 tests — attention control: POLICY PERMITTED vs CURRENTLY EXECUTABLE.

Owner: Metric. Two halves:

  A. The resolver truth table (pure functions, no I/O). The property that matters
     is the SEPARATION: an action can be policy-permitted yet not executable, and
     a prerequisite that CANNOT be determined is UNKNOWN -- never EXECUTABLE.

  B. The approval -> mission coupling rule. The property is NARROWNESS: approving
     an approval releases an APPROVAL_REQUIRED mission ONLY when no other approval
     is still pending, and a rejection releases nothing.

Each test names its falsifier in the docstring.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jcc_core.attention_control import (  # noqa: E402
    ACTION_ACKNOWLEDGE_DELIVERABLE, ACTION_DECIDE_APPROVAL, ACTION_RECONNECT,
    ACTION_RESUME, ACTION_VERIFY, BlockedReason, Executability, gather_context,
    resolve_attention, resolve_item,
)
from jcc_core.domain import (  # noqa: E402
    ApprovalState, AttentionKind, DomainStore, MissionStatus,
)


def _ctx(**over) -> dict:
    base = {
        "in_attention": {"m1"},
        "live_session_ids": set(),
        "mission_session_ids": {},
        "pending_approval_ids": set(),
        "connection_capability": {},
        "actuation_enabled": False,
    }
    base.update(over)
    return base


def _one(kind: str, ctx: dict, **item_over):
    item = {"mission_id": "m1", "kind": kind, "title": "T"}
    item.update(item_over)
    r = resolve_item(item, ctx)
    assert len(r.actions) <= 1, "a kind proposes at most one action in v1.3"
    return r.actions[0] if r.actions else None


# --------------------------------------------------------------------------- #
# A. Resolver truth table
# --------------------------------------------------------------------------- #
def test_unknown_kind_proposes_no_action():
    """Falsifier: a default verb would make an unrecognised kind look actionable."""
    assert resolve_item({"mission_id": "m1", "kind": "SOMETHING_NEW"}, _ctx()).actions == []


def test_action_on_a_resolved_mission_is_blocked_not_in_attention():
    """Falsifier: a stale UI acting on an already-resolved item."""
    a = _one("AGENT_STUCK", _ctx(in_attention=set()))
    assert a.executability is Executability.BLOCKED
    assert a.blocked_reason is BlockedReason.NOT_IN_ATTENTION


def test_approval_required_is_blocked_without_a_pending_approval():
    """Falsifier: an 'approval required' flag with no approval to decide would
    render an unusable button as executable."""
    a = _one("APPROVAL_REQUIRED", _ctx())
    assert a.action == ACTION_DECIDE_APPROVAL
    assert a.policy_permitted is True
    assert a.executability is Executability.BLOCKED
    assert a.blocked_reason is BlockedReason.NO_PENDING_APPROVAL


def test_approval_required_is_executable_with_a_pending_approval():
    a = _one("APPROVAL_REQUIRED", _ctx(pending_approval_ids={"m1"}))
    assert a.executability is Executability.EXECUTABLE


def test_human_actions_need_no_host_prerequisite():
    for kind, act in (("VERIFICATION_REQUIRED", ACTION_VERIFY),
                      ("DELIVERABLE_READY", ACTION_ACKNOWLEDGE_DELIVERABLE)):
        a = _one(kind, _ctx())
        assert a.action == act
        assert a.executability is Executability.EXECUTABLE


def test_resume_is_blocked_while_actuation_is_disabled():
    """The v1.3 state: resuming would re-enter work, so it is refused and the
    reason is stated. Falsifier: a green Resume while actuation is off."""
    a = _one("AGENT_STUCK", _ctx(actuation_enabled=False))
    assert a.action == ACTION_RESUME
    assert a.risk == "high"
    assert a.executability is Executability.BLOCKED
    assert a.blocked_reason is BlockedReason.ACTUATION_DISABLED


def test_resume_needs_a_live_session_even_when_actuation_is_on():
    # actuation on but no live session -> blocked
    a = _one("AGENT_STUCK", _ctx(actuation_enabled=True, mission_session_ids={"m1": "s1"}))
    assert a.executability is Executability.BLOCKED
    assert a.blocked_reason is BlockedReason.NO_LIVE_SESSION
    # actuation on AND the session is live -> executable
    a = _one("AGENT_STUCK", _ctx(actuation_enabled=True,
                                 mission_session_ids={"m1": "s1"},
                                 live_session_ids={"s1"}))
    assert a.executability is Executability.EXECUTABLE


def test_connection_lost_is_unknown_when_capability_cannot_be_read():
    """THE Wave-4 property: 'could not determine' must NOT become 'fine'.

    Falsifier: defaulting an unread capability to EXECUTABLE would make an
    uncheckable reconnect look safe.
    """
    a = _one("CONNECTION_LOST", _ctx(connection_capability={}), agent="workbuddy")
    assert a.executability is Executability.UNKNOWN
    assert a.blocked_reason is None


def test_connection_lost_respects_measured_capability():
    a = _one("CONNECTION_LOST", _ctx(connection_capability={"workbuddy": "TELEMETRY_ONLY"}),
             agent="workbuddy")
    assert a.executability is Executability.BLOCKED
    assert a.blocked_reason is BlockedReason.CONNECTION_NOT_CONTROL_VERIFIED
    a = _one("CONNECTION_LOST", _ctx(connection_capability={"workbuddy": "CONTROL_VERIFIED"}),
             agent="workbuddy")
    assert a.executability is Executability.EXECUTABLE


def test_every_attention_kind_is_covered_by_the_table():
    """Falsifier: a new AttentionKind escaping coverage. The table must name all."""
    from jcc_core.domain import AttentionKind as AK
    for k in AK:
        a = _one(k.value, _ctx(actuation_enabled=True,
                               pending_approval_ids={"m1"},
                               mission_session_ids={"m1": "s1"},
                               live_session_ids={"s1"},
                               connection_capability={"workbuddy": "CONTROL_VERIFIED"}),
                 agent="workbuddy")
        assert a is not None, "no action proposed for kind %s" % k.value


# --------------------------------------------------------------------------- #
# B. Approval -> mission coupling
# --------------------------------------------------------------------------- #
@pytest.fixture()
def store():
    d = tempfile.mkdtemp()
    return DomainStore(os.path.join(d, "w4.db"))


def _mission_in_attention_with_approval(store, *, extra_pending=False):
    p = store.create_project("P")
    m = store.create_mission(p.project_id, "M")
    store.transition_mission(m.mission_id, MissionStatus.READY)
    store.transition_mission(m.mission_id, MissionStatus.STARTING)
    store.transition_mission(m.mission_id, MissionStatus.RUNNING)
    store.transition_mission(
        m.mission_id, MissionStatus.NEEDS_ATTENTION,
        attention_kind=AttentionKind.APPROVAL_REQUIRED, attention_reason="need ok")
    a1 = store.create_approval(m.mission_id, "k1")
    a2 = store.create_approval(m.mission_id, "k2") if extra_pending else None
    return m, a1, a2


def test_approving_the_only_approval_releases_the_mission(store):
    m, a1, _ = _mission_in_attention_with_approval(store)
    store.decide_approval(a1.approval_id, ApprovalState.APPROVED, decided_by="op")
    mm = store.get_mission(m.mission_id)
    assert mm.status is MissionStatus.RUNNING
    assert mm.attention_kind is None and mm.attention_reason is None


def test_approving_with_another_pending_does_not_release(store):
    """Falsifier: the rule must not clear an UNRELATED open blocker."""
    m, a1, a2 = _mission_in_attention_with_approval(store, extra_pending=True)
    store.decide_approval(a1.approval_id, ApprovalState.APPROVED, decided_by="op")
    assert store.get_mission(m.mission_id).status is MissionStatus.NEEDS_ATTENTION
    # deciding the second one then releases
    store.decide_approval(a2.approval_id, ApprovalState.APPROVED, decided_by="op")
    assert store.get_mission(m.mission_id).status is MissionStatus.RUNNING


def test_rejecting_does_not_release_the_mission(store):
    """A rejection is not a resolution; the operator chooses what is next."""
    m, a1, _ = _mission_in_attention_with_approval(store)
    store.decide_approval(a1.approval_id, ApprovalState.REJECTED, decided_by="op")
    mm = store.get_mission(m.mission_id)
    assert mm.status is MissionStatus.NEEDS_ATTENTION
    assert mm.attention_kind is AttentionKind.APPROVAL_REQUIRED


def test_approval_decision_is_final(store):
    m, a1, _ = _mission_in_attention_with_approval(store)
    store.decide_approval(a1.approval_id, ApprovalState.APPROVED, decided_by="op")
    with pytest.raises(ValueError):
        store.decide_approval(a1.approval_id, ApprovalState.REJECTED, decided_by="op")


def test_coupling_records_a_mission_event(store):
    m, a1, _ = _mission_in_attention_with_approval(store)
    store.decide_approval(a1.approval_id, ApprovalState.APPROVED, decided_by="op")
    tl = store.mission_timeline(m.mission_id)
    assert any(e["from_status"] == "NEEDS_ATTENTION" and e["to_status"] == "RUNNING"
               for e in tl)


# --------------------------------------------------------------------------- #
# C. gather_context over a real store
# --------------------------------------------------------------------------- #
def test_gather_context_reads_real_pending_approvals(store):
    m, a1, _ = _mission_in_attention_with_approval(store)
    ctx = gather_context(store, actuation_enabled=False)
    assert m.mission_id in ctx["in_attention"]
    assert m.mission_id in ctx["pending_approval_ids"]
    assert ctx["actuation_enabled"] is False


def test_resolve_attention_over_a_real_inbox_is_consistent(store):
    m, a1, _ = _mission_in_attention_with_approval(store)
    ctx = gather_context(store, actuation_enabled=False)
    res = resolve_attention(store.needs_attention(), ctx)
    assert len(res) == 1
    assert res[0].mission_id == m.mission_id
    # With a real pending approval, the decision action is executable.
    assert res[0].any_executable
