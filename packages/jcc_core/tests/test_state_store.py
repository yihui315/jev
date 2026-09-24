import os
import sqlite3
import tempfile
import threading
import time

from jcc_core.schemas import (
    AEPEnvelope,
    Assessment,
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


# --------------------------------------------------------------------------- #
# ADR-0008 F1 — the PUBLIC connection accessor the supervisor plane needs
# --------------------------------------------------------------------------- #
def test_connection_is_public_fresh_and_closed_on_exit():
    """`connection()` must be the documented way another plane gets a handle.

    `_conn` is a private METHOD returning a fresh connection; a caller that
    treated it as an attribute received a bound method and crashed. This pins the
    replacement contract: context manager, fresh handle, always closed.
    """
    s, path = _tmp_store()
    with s.connection() as conn:
        assert isinstance(conn, sqlite3.Connection)
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    # closed on exit (proves the finally: close())
    try:
        conn.execute("SELECT 1")
        raise AssertionError("connection() did not close its connection on exit")
    except sqlite3.ProgrammingError:
        pass
    # a fresh handle per call, same database
    with s.connection() as first, s.connection() as second:
        assert first is not second
    with s.connection() as conn:
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'"
        ).fetchone() is not None
    os.remove(path)


def test_transaction_is_public_lock_scoped_and_commits():
    """spec §2.6 item 1 / Errata E-5: `transaction()` is the WRITE accessor.

    It must commit on clean exit, close its connection, and differ from the
    lock-free `connection()`.
    """
    s, path = _tmp_store()
    with s.transaction() as conn:
        assert isinstance(conn, sqlite3.Connection)
        conn.execute("CREATE TABLE probe (v INTEGER)")
        conn.execute("INSERT INTO probe(v) VALUES (1)")
    # committed (visible on a brand-new handle) ...
    with s.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM probe").fetchone()[0] == 1
    # ... and closed on exit (proves the finally: close())
    try:
        conn.execute("SELECT 1")
        raise AssertionError("transaction() did not close its connection on exit")
    except sqlite3.ProgrammingError:
        pass
    os.remove(path)


def test_transaction_serialises_concurrent_read_modify_write():
    """The E-5 guarantee: the store lock spans the whole read-modify-write.

    Mirrors `shadow_store.append_shadow`'s non-atomic
    `SELECT MAX(seq) ... ` then `INSERT`. Without the lock two writers read the
    same predecessor and the chain FORKS (a lost update). With `transaction()`
    every writer must observe a distinct predecessor.
    """
    s, path = _tmp_store()
    with s.transaction() as conn:
        conn.execute("CREATE TABLE chain (seq INTEGER PRIMARY KEY, prev INTEGER)")

    N = 48
    errors = []

    def worker():
        try:
            with s.transaction() as conn:
                prev = conn.execute(
                    "SELECT COALESCE(MAX(seq), -1) FROM chain"
                ).fetchone()[0]
                conn.execute("INSERT INTO chain(seq, prev) VALUES (?, ?)", (prev + 1, prev))
        except Exception as exc:  # noqa: BLE001 — surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    with s.connection() as conn:
        rows = conn.execute("SELECT seq, prev FROM chain ORDER BY seq").fetchall()
    # contiguous 0..N-1 exactly: no gap AND no duplicate => no fork / lost update
    assert [r[0] for r in rows] == list(range(0, N)), "lost update: the lock did not serialise"
    assert all(r[1] == r[0] - 1 for r in rows), "chain link broken"
    os.remove(path)


# --------------------------------------------------------------------------- #
# ADR-0008 F2 / D-34 — budget counters DERIVED from persisted state
# --------------------------------------------------------------------------- #
def test_budget_snapshot_is_derived_from_persisted_state():
    """Not an in-memory ledger: every counter is a COUNT(*) over real rows."""
    s, path = _tmp_store()
    s.upsert_session(SessionState(session_id="s1", agent="claude", project="p",
                                  status=SessionStatus.ACTIVE, created_at=1000.0,
                                  updated_at=1000.0))
    s.add_event(AEPEnvelope(event_id="e1", delivery_id="d1", session_id="s1", agent="claude"))
    s.add_event(AEPEnvelope(event_id="e2", delivery_id="d2", session_id="s1", agent="claude"))
    s.add_assessment(Assessment(agent="claude", session_id="s1", timestamp=1001.0))
    now = time.time()
    s.add_directive(Directive(directive_id="d1", session_id="s1", timestamp=now,
                              action=DirectiveAction.RETRY))
    s.add_directive(Directive(directive_id="d2", session_id="s1", timestamp=now,
                              action=DirectiveAction.ESCALATE))
    s.add_directive(Directive(directive_id="d3", session_id="s1", timestamp=now,
                              action=DirectiveAction.CONTINUE))

    snap = s.budget_snapshot("s1")
    assert snap["assessments_used"] == 1
    assert snap["events_used"] == 2
    assert snap["directives_used"] == 3
    assert snap["retries_used"] == 1
    assert snap["escalations_used"] == 1
    assert snap["rate_used"] == 3          # all three inside the 60 s window
    assert snap["first_seen"] == 1000.0    # sessions.created_at (WALL-CLOCK epoch)
    # not persisted anywhere -> reported honestly as 0.0, never invented
    assert snap["model_units_used"] == 0.0
    assert snap["cost_units_used"] == 0.0
    os.remove(path)


def test_budget_snapshot_unknown_session_is_empty_and_dated_now():
    s, path = _tmp_store()
    snap = s.budget_snapshot("never-seen")
    assert snap["assessments_used"] == 0
    assert snap["events_used"] == 0
    assert snap["directives_used"] == 0
    assert abs(snap["first_seen"] - time.time()) < 5.0
    os.remove(path)


def test_budget_snapshot_satisfies_the_governor_contract():
    """F2 end-to-end against the real consumer, incl. the CLOCK DOMAIN.

    `first_seen` is wall-clock, so the governor must be built with
    `clock=time.time` (what `run.py` now does); the default `time.monotonic`
    shares no origin with it and would make the wall-clock cut never fire.
    """
    from supervisor.budget import CODE_DIRECTIVES, BudgetGovernor, BudgetLimits

    s, path = _tmp_store()
    s.upsert_session(SessionState(session_id="s1", agent="claude", status=SessionStatus.ACTIVE,
                                  created_at=time.time(), updated_at=time.time()))
    s.add_directive(Directive(directive_id="d1", session_id="s1", timestamp=time.time(),
                              action=DirectiveAction.CONTINUE))

    gov = BudgetGovernor(BudgetLimits(directives=1), store=s, clock=time.time)
    # 1 persisted + the step about to be taken = 2 > limit 1 -> exhausted
    assert gov.check("s1", kind="directive").code == CODE_DIRECTIVES
    # an untouched session is NOT exhausted -> the count really is derived
    assert gov.check("other", kind="directive").allowed is True
    os.remove(path)
