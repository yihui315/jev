"""Wave 1 domain tests -- Project/Mission/AgentRun/Connection/Capability/Approval/Artifact.

Owner: Metric. The Mission state machine and the "frontend is not the source of
truth" claim are the two load-bearing properties here, so they get falsifiers
that fail if the implementation is permissive.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from jcc_core import domain as D  # noqa: E402


@pytest.fixture()
def store(tmp_path):
    return D.DomainStore(str(tmp_path / "jcc_domain_test.db"))


# --- projects / missions -----------------------------------------------------

def test_create_project_and_mission(store):
    p = store.create_project("Alpha", root_path="F:/work/alpha")
    assert p.project_id.startswith("prj_")
    m = store.create_mission(p.project_id, "Ship v1", goal="make it work")
    assert m.status == D.MissionStatus.DRAFT
    assert m.project_id == p.project_id
    assert store.get_mission(m.mission_id).title == "Ship v1"


def test_mission_requires_existing_project(store):
    with pytest.raises(ValueError):
        store.create_mission("prj_does_not_exist", "nope")


def test_permissions_are_deduplicated_and_sorted(store):
    p = store.create_project("P")
    m = store.create_mission(p.project_id, "M", permissions=["write", "read", "read"])
    assert m.permissions == ["read", "write"]


# --- state machine -----------------------------------------------------------

def test_initial_state_is_draft(store):
    p = store.create_project("P")
    m = store.create_mission(p.project_id, "M")
    assert m.status == D.MissionStatus.DRAFT


def test_happy_path_transitions(store):
    p = store.create_project("P")
    m = store.create_mission(p.project_id, "M")
    chain = [D.MissionStatus.READY, D.MissionStatus.STARTING,
             D.MissionStatus.RUNNING, D.MissionStatus.VERIFYING,
             D.MissionStatus.COMPLETED]
    for st in chain:
        m = store.transition_mission(m.mission_id, st)
        assert m.status == st
    assert m.finished_at is not None
    assert m.started_at is not None


def test_draft_cannot_jump_to_running(store):
    """The launch handshake cannot be skipped: DRAFT -> RUNNING is illegal."""
    p = store.create_project("P")
    m = store.create_mission(p.project_id, "M")
    with pytest.raises(ValueError):
        store.transition_mission(m.mission_id, D.MissionStatus.RUNNING)
    assert store.get_mission(m.mission_id).status == D.MissionStatus.DRAFT


def test_illegal_transition_leaves_row_unchanged(store):
    p = store.create_project("P")
    m = store.create_mission(p.project_id, "M")
    with pytest.raises(ValueError):
        store.transition_mission(m.mission_id, D.MissionStatus.COMPLETED)
    assert store.get_mission(m.mission_id).status == D.MissionStatus.DRAFT
    # the refused attempt must not have been recorded either
    assert len(store.mission_timeline(m.mission_id)) == 1  # only "created"


def test_unknown_mission_transition_refused(store):
    with pytest.raises(ValueError):
        store.transition_mission("msn_nope", D.MissionStatus.READY)


def test_needs_attention_carries_a_reason(store):
    p = store.create_project("P")
    m = store.create_mission(p.project_id, "M")
    for st in (D.MissionStatus.READY, D.MissionStatus.STARTING, D.MissionStatus.RUNNING):
        m = store.transition_mission(m.mission_id, st)
    m = store.transition_mission(
        m.mission_id, D.MissionStatus.NEEDS_ATTENTION,
        attention_kind=D.AttentionKind.APPROVAL_REQUIRED,
        attention_reason="directive needs human approval")
    assert m.attention_kind == D.AttentionKind.APPROVAL_REQUIRED.value
    assert "approval" in m.attention_reason


def test_paused_can_resume(store):
    p = store.create_project("P")
    m = store.create_mission(p.project_id, "M")
    for st in (D.MissionStatus.READY, D.MissionStatus.STARTING, D.MissionStatus.RUNNING,
               D.MissionStatus.PAUSED, D.MissionStatus.RUNNING):
        m = store.transition_mission(m.mission_id, st)
    assert m.status == D.MissionStatus.RUNNING


def test_terminal_can_be_reopened_only_to_draft(store):
    p = store.create_project("P")
    m = store.create_mission(p.project_id, "M")
    for st in (D.MissionStatus.READY, D.MissionStatus.STARTING, D.MissionStatus.RUNNING,
               D.MissionStatus.COMPLETED):
        m = store.transition_mission(m.mission_id, st)
    m = store.transition_mission(m.mission_id, D.MissionStatus.DRAFT)
    assert m.status == D.MissionStatus.DRAFT
    assert m.finished_at is not None  # history is preserved, not erased


def test_cancelled_reachable_from_every_live_state(store):
    for path in ([D.MissionStatus.DRAFT],
                 [D.MissionStatus.READY],
                 [D.MissionStatus.READY, D.MissionStatus.STARTING],
                 [D.MissionStatus.READY, D.MissionStatus.STARTING, D.MissionStatus.RUNNING],
                 [D.MissionStatus.READY, D.MissionStatus.STARTING, D.MissionStatus.RUNNING,
                  D.MissionStatus.PAUSED]):
        p = store.create_project("P")
        m = store.create_mission(p.project_id, "M")
        for st in path:
            m = store.transition_mission(m.mission_id, st)
        m = store.transition_mission(m.mission_id, D.MissionStatus.CANCELLED)
        assert m.status == D.MissionStatus.CANCELLED


def test_timeline_is_derivable_from_store(store):
    p = store.create_project("P")
    m = store.create_mission(p.project_id, "M")
    for st in (D.MissionStatus.READY, D.MissionStatus.STARTING, D.MissionStatus.RUNNING):
        store.transition_mission(m.mission_id, st, reason="step %s" % st.value)
    tl = store.mission_timeline(m.mission_id)
    assert [t["to_status"] for t in tl] == ["DRAFT", "READY", "STARTING", "RUNNING"]
    assert tl[0]["from_status"] is None


def test_state_machine_is_closed(store):
    """Every state must appear as a source in MISSION_TRANSITIONS (no orphans)."""
    for s in D.MissionStatus:
        assert s in D.MISSION_TRANSITIONS, s
    # and every destination must be a real state
    for src, dsts in D.MISSION_TRANSITIONS.items():
        for d in dsts:
            assert isinstance(d, D.MissionStatus)


# --- agent runs --------------------------------------------------------------

def test_run_creation_and_session_attach(store):
    p = store.create_project("P")
    m = store.create_mission(p.project_id, "M")
    r = store.create_run(m.mission_id, "workbuddy")
    assert r.run_id.startswith("run_")
    runs = store.list_runs(m.mission_id)
    assert len(runs) == 1
    store.attach_session(m.mission_id, "sess_123")
    assert store.get_mission(m.mission_id).session_id == "sess_123"


def test_run_requires_existing_mission(store):
    with pytest.raises(ValueError):
        store.create_run("msn_nope", "workbuddy")


# --- connections + secrets ---------------------------------------------------

def test_connection_upsert_and_health(store):
    c = store.upsert_connection("claude", display_name="Claude Code",
                                secret_env_var="JCC_CLAUDE_TOKEN")
    assert c.health == "UNKNOWN"
    assert c.secret_env_var == "JCC_CLAUDE_TOKEN"
    store.set_connection_health(c.connection_id, "CONNECTED",
                                last_session_id="sess_x",
                                detail={"probe": "ok"})
    c2 = store.get_connection(c.connection_id)
    assert c2.health == "CONNECTED"
    assert c2.last_session_id == "sess_x"
    assert c2.detail == {"probe": "ok"}


def test_connection_refuses_a_secret_value(store):
    with pytest.raises(ValueError):
        store.upsert_connection("claude", secret_env_var="secret_abc123")


def test_upsert_is_idempotent_per_agent(store):
    a = store.upsert_connection("codex")
    b = store.upsert_connection("codex", display_name="Codex CLI")
    assert a.connection_id == b.connection_id
    assert len(store.list_connections()) == 1


# --- capability gate ---------------------------------------------------------

def test_control_verified_is_the_only_gate(store):
    c = store.upsert_connection("claude")
    # absent row => UNKNOWN => not verified
    assert store.is_control_verified(c.connection_id) is False
    store.set_capability(c.connection_id, D.CapabilityKind.TELEMETRY_ONLY)
    assert store.is_control_verified(c.connection_id) is False
    store.set_capability(c.connection_id, D.CapabilityKind.ADAPTER_CONTRACT_LIVE)
    assert store.is_control_verified(c.connection_id) is False
    store.set_capability(c.connection_id, D.CapabilityKind.CONTROL_VERIFIED,
                         detail="exercised pause path")
    assert store.is_control_verified(c.connection_id) is True


def test_capability_upsert_is_unique_per_connection_and_kind(store):
    c = store.upsert_connection("claude")
    store.set_capability(c.connection_id, D.CapabilityKind.TELEMETRY_ONLY, detail="a")
    store.set_capability(c.connection_id, D.CapabilityKind.TELEMETRY_ONLY, detail="b")
    caps = store.list_capabilities(c.connection_id)
    assert len(caps) == 1
    assert caps[0].detail == "b"


# --- approvals ---------------------------------------------------------------

def test_approval_lifecycle(store):
    p = store.create_project("P")
    m = store.create_mission(p.project_id, "M")
    a = store.create_approval(m.mission_id, "DIRECTIVE", reason="high risk")
    assert a.state == D.ApprovalState.PENDING
    a2 = store.decide_approval(a.approval_id, D.ApprovalState.APPROVED,
                               decided_by="operator")
    assert a2.state == D.ApprovalState.APPROVED
    assert a2.decided_by == "operator"


def test_approval_decision_is_final(store):
    p = store.create_project("P")
    m = store.create_mission(p.project_id, "M")
    a = store.create_approval(m.mission_id, "DIRECTIVE")
    store.decide_approval(a.approval_id, D.ApprovalState.APPROVED, decided_by="op")
    with pytest.raises(ValueError):
        store.decide_approval(a.approval_id, D.ApprovalState.REJECTED, decided_by="op2")


def test_approval_cannot_be_decided_as_pending(store):
    p = store.create_project("P")
    m = store.create_mission(p.project_id, "M")
    a = store.create_approval(m.mission_id, "X")
    with pytest.raises(ValueError):
        store.decide_approval(a.approval_id, D.ApprovalState.PENDING, decided_by="op")


# --- artifacts ---------------------------------------------------------------

def test_artifact_hashing(store):
    p = store.create_project("P")
    m = store.create_mission(p.project_id, "M")
    a = store.add_artifact(m.mission_id, "report.md", kind=D.ArtifactKind.REPORT,
                           data=b"hello world")
    assert a.sha256 == ("b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9")
    assert a.bytes == 11
    assert a.status == D.ArtifactStatus.PRODUCED


def test_artifact_requires_existing_mission(store):
    with pytest.raises(ValueError):
        store.add_artifact("msn_nope", "x")


# --- home summary ------------------------------------------------------------

def test_home_summary_counts(store):
    p = store.create_project("P")
    m1 = store.create_mission(p.project_id, "M1")
    m2 = store.create_mission(p.project_id, "M2")
    for st in (D.MissionStatus.READY, D.MissionStatus.STARTING, D.MissionStatus.RUNNING):
        store.transition_mission(m1.mission_id, st)
    store.transition_mission(m2.mission_id, D.MissionStatus.READY)
    store.transition_mission(m2.mission_id, D.MissionStatus.STARTING)
    store.transition_mission(m2.mission_id, D.MissionStatus.RUNNING)
    store.transition_mission(m2.mission_id, D.MissionStatus.NEEDS_ATTENTION,
                             attention_kind=D.AttentionKind.AGENT_STUCK,
                             attention_reason="no progress for 12 events")
    s = store.home_summary()
    assert s["active_projects"] == 1
    assert s["running_missions"] == 1
    assert s["needs_attention"] == 1
    assert len(s["attention_items"]) == 1
    assert s["attention_items"][0]["kind"] == D.AttentionKind.AGENT_STUCK.value


# --- migrations --------------------------------------------------------------

def test_domain_schema_is_additive_on_an_existing_v1_2_db(tmp_path):
    """A v1.2 database (sessions/events/...) opens and gains domain tables."""
    db = str(tmp_path / "jcc.db")
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE sessions (session_id TEXT PRIMARY KEY, agent TEXT);"
        "CREATE TABLE events (event_id TEXT, session_id TEXT, "
        "PRIMARY KEY(session_id, event_id));")
    conn.execute("INSERT INTO sessions VALUES ('s1','workbuddy')")
    conn.commit()
    conn.close()
    # opening the domain store must NOT drop or alter the v1.2 tables
    st = D.DomainStore(db)
    p = st.create_project("P")
    m = st.create_mission(p.project_id, "M")
    assert st.get_mission(m.mission_id) is not None
    conn = sqlite3.connect(db)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"sessions", "events"} <= tables
    assert {"projects", "missions", "agent_runs", "domain_connections",
            "capabilities", "approvals", "artifacts", "mission_events"} <= tables


def test_domain_store_is_idempotent(tmp_path):
    db = str(tmp_path / "jcc.db")
    a = D.DomainStore(db)
    p = a.create_project("P")
    b = D.DomainStore(db)  # re-open
    assert b.get_project(p.project_id) is not None
    assert b.list_projects()[0].name == "P"


def test_no_secret_leaks_in_connection_serialisation(store):
    c = store.upsert_connection("hermes", secret_env_var="JCC_HERMES_TOKEN")
    d = c.model_dump()
    # the NAME is fine and expected
    assert d["secret_env_var"] == "JCC_HERMES_TOKEN"
    # the real property: no KEY carries secret_-prefixed material. The field
    # name `secret_env_var` is a NAME slot, not a value slot, so it is the only
    # legitimate occurrence of the prefix.
    secret_keys = [k for k in d if k.startswith("secret_") and k != "secret_env_var"]
    assert secret_keys == []
    # and the persisted row must hold the env var NAME, never a value
    assert "sk-" not in c.model_dump_json()
    assert "Bearer " not in c.model_dump_json()
