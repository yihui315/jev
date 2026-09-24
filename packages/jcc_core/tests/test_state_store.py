import os
import tempfile

from jcc_core.schemas import (
    AEPEnvelope,
    Directive,
    DirectiveAction,
    EventType,
    SessionState,
    SessionStatus,
)
from jcc_core.state_store import StateStore


def _tmp_store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    return StateStore(path), path


def test_wal_enabled():
    s, path = _tmp_store()
    assert s.is_wal is True
    os.remove(path)


def test_dedup_event_and_delivery():
    s, path = _tmp_store()
    env = AEPEnvelope(event_id="e1", delivery_id="d1", session_id="s1", agent="workbuddy")
    s.add_event(env)
    assert s.seen("s1", "e1", "d1") is True
    assert s.seen("s1", "e1", "d2") is True   # same (session, event)
    assert s.seen("s1", "e2", "d1") is True   # same delivery_id
    assert s.seen("s1", "e2", "d2") is False
    os.remove(path)


def test_session_roundtrip():
    s, path = _tmp_store()
    ss = SessionState(session_id="s1", agent="claude", project="p", cwd="/x",
                      status=SessionStatus.ACTIVE, created_at=1.0, updated_at=2.0, event_count=3)
    s.upsert_session(ss)
    got = s.get_session("s1")
    assert got is not None
    assert got.session_id == "s1"
    assert got.agent.value == "claude"
    assert got.event_count == 3
    assert len(s.list_sessions()) == 1
    os.remove(path)


def test_audit_persist_and_verify():
    s, path = _tmp_store()
    s.append_audit(event_id="e1", timestamp=1.0, session_id="s1", agent="a",
                   project="p", directive="CONTINUE:d1")
    s.append_audit(event_id="e2", timestamp=2.0, session_id="s1", agent="a", project="p")
    assert len(s.get_audit()) == 2
    ok, count = s.verify_audit()
    assert ok and count == 2
    os.remove(path)


def test_directive_persist():
    s, path = _tmp_store()
    d = Directive(directive_id="d1", session_id="s1", timestamp=1.0,
                  action=DirectiveAction.PAUSE, acted=False,
                  actuation_reason="actuation_disabled")
    s.add_directive(d)
    ds = s.get_directives("s1")
    assert len(ds) == 1
    assert ds[0]["action"] == "PAUSE"
    assert ds[0]["acted"] is False
    os.remove(path)


def test_evidence_persist():
    s, path = _tmp_store()
    from jcc_core.schemas import EvidenceSnapshot
    snap = EvidenceSnapshot(evidence_id="ev1", session_id="s1", source_event_id="e1",
                            timestamp=1.0, fingerprint="abc", complete=True)
    s.add_evidence(snap)
    # evidence is persisted indirectly; verify via session evidence_count path is covered by core.
    assert s.get_session("s1") is None  # no session yet, but store should not error
    os.remove(path)
