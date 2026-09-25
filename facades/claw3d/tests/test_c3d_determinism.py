"""S-07 — determinism tests for the Claw3D projection.

Same input state projects to byte-identical output, and a node's 3D position is
derived deterministically from its session id (never random).
"""
from __future__ import annotations

from jcc_core.schemas import (
    AgentName,
    Assessment,
    Directive,
    DirectiveAction,
    SessionState,
    SessionStatus,
)
from jcc_core.state_store import StateStore

from claw3d import Claw3DFacade
from claw3d.canonical import canonical_bytes, stable_int
from claw3d.projection import GRID


def _session(session_id: str, band: str, score: float, action: DirectiveAction) -> SessionState:
    return SessionState(
        session_id=session_id,
        agent=AgentName.WORKBUDDY,
        project="jev",
        cwd="F:\\jev",
        status=SessionStatus.AWAIT,
        created_at=1700000000.0,
        updated_at=1700000005.0,
        last_event_id=f"ev-{session_id}",
        event_count=2,
        evidence_count=1,
        current_assessment=Assessment(
            agent=AgentName.WORKBUDDY,
            session_id=session_id,
            timestamp=1700000005.0,
            model="jev-1.13.0",
            provider="mock",
            probabilities={"risk": score},
            risk_score=score,
            band=band,
        ).model_dump(mode="json"),
        current_directive=Directive(
            directive_id=f"dir_{session_id}",
            session_id=session_id,
            timestamp=1700000005.0,
            action=action,
            policy_version="p3.0.0",
            signed=False,
        ).model_dump(mode="json"),
    )


def _seed(store, states):
    for st in states:
        store.upsert_session(st)


def test_byte_identical_across_two_stores(tmp_path):
    states = [
        _session("s-aaa", "high", 0.72, DirectiveAction.RETRY),
        _session("s-bbb", "low", 0.11, DirectiveAction.CONTINUE),
        _session("s-ccc", "critical", 0.97, DirectiveAction.ESCALATE),
    ]
    s1 = StateStore(str(tmp_path / "a.db"))
    s2 = StateStore(str(tmp_path / "b.db"))
    _seed(s1, states)
    _seed(s2, states)
    b1 = Claw3DFacade(s1).project_scene_bytes()
    b2 = Claw3DFacade(s2).project_scene_bytes()
    assert b1 == b2
    assert b1 == canonical_bytes(Claw3DFacade(s1).project_scene())


def test_byte_identical_repeat_calls(rich_state, store):
    store.upsert_session(rich_state)
    f = Claw3DFacade(store)
    first = f.project_scene_bytes()
    for _ in range(5):
        assert f.project_scene_bytes() == first


def test_ordering_is_canonical_by_node_id(tmp_path):
    states = [
        _session("s-ccc", "low", 0.1, DirectiveAction.CONTINUE),
        _session("s-aaa", "high", 0.9, DirectiveAction.ESCALATE),
        _session("s-bbb", "medium", 0.5, DirectiveAction.PAUSE),
    ]
    s = StateStore(str(tmp_path / "order.db"))
    _seed(s, states)
    f = Claw3DFacade(s)
    rows = f.project_sessions(s.list_sessions())
    assert [r["node_id"] for r in rows] == ["s-aaa", "s-bbb", "s-ccc"]


def test_geometry_is_deterministic(rich_state, store):
    store.upsert_session(rich_state)
    f = Claw3DFacade(store)
    sid = rich_state.session_id
    n1 = f.get_node(sid)
    n2 = f.get_node(sid)
    assert n1 == n2
    assert n1["pos_x"] == stable_int(sid) % GRID
    assert n1["pos_y"] == stable_int(sid + "#y") % GRID
    assert n1["pos_z"] == stable_int(sid + "#z") % GRID
    assert 0 <= n1["pos_x"] < GRID and 0 <= n1["pos_y"] < GRID and 0 <= n1["pos_z"] < GRID


def test_canonical_encoding_is_stable_json():
    doc = {"z": 1, "a": [3, 2, 1], "b": "x"}
    assert canonical_bytes(doc) == b'{"a":[3,2,1],"b":"x","z":1}'
