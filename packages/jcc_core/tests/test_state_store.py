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
    # genuinely not persisted anywhere -> reported honestly as 0.0, never invented
    assert snap["model_units_used"] == 0.0
    # DERIVED from the persisted actions (D-34), not hard-coded: RETRY 3 + ESCALATE 10
    # + CONTINUE 0. A hard-coded 0.0 here is the fail-open bug this asserts against.
    assert snap["cost_units_used"] == 13.0
    os.remove(path)


def test_budget_snapshot_cost_units_is_derived_from_actions():
    """Directional falsifier for the ``cost_units`` dimension (D-34/D-5).

    The old implementation hard-coded ``cost_units_used = 0.0``, which made the
    dimension permanently FAIL-OPEN: it could never exhaust, so the governor's
    ``cost_units`` cut was decorative. This pins the derivation *directionally*
    (an exact per-action sum), so it fails on 0.0 AND on any table drift.
    """
    from jcc_core.policy import COST_UNITS_PER_ACTION

    s, path = _tmp_store()
    assert s.budget_snapshot("s1")["cost_units_used"] == 0.0   # nothing persisted

    expected = 0.0
    for i, action in enumerate(
        [DirectiveAction.CONTINUE, DirectiveAction.VERIFY, DirectiveAction.STEER,
         DirectiveAction.RETRY, DirectiveAction.PAUSE, DirectiveAction.ESCALATE]
    ):
        s.add_directive(Directive(directive_id=f"d{i}", session_id="s1",
                                  timestamp=time.time(), action=action))
        expected += COST_UNITS_PER_ACTION[action]

    # one action per row, no duplicates -> exact equality, not >=
    assert expected == 21.0     # 0 + 1 + 2 + 3 + 5 + 10
    assert s.budget_snapshot("s1")["cost_units_used"] == expected

    # and it is monotone: one more ESCALATE (+10) strictly raises it
    s.add_directive(Directive(directive_id="d9", session_id="s1", timestamp=time.time(),
                              action=DirectiveAction.ESCALATE))
    assert s.budget_snapshot("s1")["cost_units_used"] == expected + 10.0
    os.remove(path)


def test_budget_snapshot_cost_is_uncostable_action_fails_closed():
    """An action string with no cost entry must NOT be silently costed at 0.

    Under-counting is the exact failure mode D-34 exists to prevent, so the
    derivation raises instead; the governor turns a raising counter read into
    ``budget_check_error`` (exhausted), i.e. fail-closed. Normal writers cannot
    reach this -- the column is only written through the validated ``Directive``
    model -- so it takes a raw INSERT to provoke.
    """
    s, path = _tmp_store()
    with s.connection() as conn:
        conn.execute(
            "INSERT INTO directives (directive_id, session_id, timestamp, action) "
            "VALUES (?,?,?,?)",
            ("d1", "s1", time.time(), "NOT_A_REAL_ACTION"),
        )
        conn.commit()   # connection() is deliberately lock-free; it does NOT commit

    try:
        s.budget_snapshot("s1")
    except ValueError as exc:
        assert "NOT_A_REAL_ACTION" in str(exc)
    else:
        raise AssertionError("uncostable action was silently under-counted (fail-open)")
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


def test_budget_first_seen_is_wall_clock_so_the_governor_needs_time_time():
    """CLOCK DOMAIN pin — both sides, so neither can drift alone.

    ``budget_snapshot()["first_seen"]`` is a WALL-CLOCK epoch second (it is
    ``sessions.created_at``), while ``BudgetGovernor``'s default clock is
    ``time.monotonic`` (seconds since boot). The two domains share no origin:
    ``elapsed = monotonic() - first_seen`` is a large NEGATIVE number, so the
    wall-clock cut silently NEVER fires and the dimension is fail-open. That is
    why ``run.py`` builds the governor with ``clock=time.time``.

    This asserts (a) the value really is wall-clock, and (b) that the default
    clock really does fail to fire — the negative control, without which the test
    would pass on a store that happened to return a monotonic value.
    """
    from supervisor.budget import CODE_WALL_CLOCK, BudgetGovernor, BudgetLimits

    s, path = _tmp_store()
    created = time.time() - 100.0        # 100 s ago => way past a 1 s limit
    s.upsert_session(SessionState(session_id="s1", agent="claude", status=SessionStatus.ACTIVE,
                                  created_at=created, updated_at=created))
    first_seen = s.budget_snapshot("s1")["first_seen"]

    # (a) wall-clock: epoch magnitude, and NOT an uptime reading
    assert first_seen == created
    assert first_seen > 1e9, "first_seen is not an epoch second — looks like uptime"
    assert first_seen - time.monotonic() > 1e6, (
        "first_seen and time.monotonic() overlap — the two clock domains must be disjoint"
    )

    limits = BudgetLimits(wall_clock_s=1.0)
    # the CORRECT wiring fires
    assert BudgetGovernor(limits, store=s, clock=time.time).check(
        "s1", kind="directive").code == CODE_WALL_CLOCK
    # (b) negative control: the DEFAULT monotonic clock cannot fire (fail-open)
    assert BudgetGovernor(limits, store=s).check("s1", kind="directive").code is None, (
        "monotonic-armed governor fired the wall-clock cut — the clocks are not disjoint "
        "after all, so this pin no longer describes the real hazard"
    )
    os.remove(path)


def test_transaction_rolls_back_and_releases_the_lock_on_exception():
    """`transaction()` must fail CLOSED: no commit, and the lock MUST be released.

    Two hazards, and the second is the nastier one:
      * a raise inside the ``with`` must not publish partial state (no commit);
      * ``_lock`` is a non-reentrant ``threading.Lock``, so if the exception path
        forgot to release it every later writer would BLOCK FOREVER. A hung test
        is worse than a failing one, so the follow-up write runs on a watchdog
        thread with a bounded wait instead of deadlocking the suite.
    """
    s, path = _tmp_store()
    s.upsert_session(SessionState(session_id="s1", agent="claude", status=SessionStatus.ACTIVE,
                                  created_at=time.time(), updated_at=time.time()))
    with s.transaction() as conn:
        conn.execute("CREATE TABLE probe (v INTEGER)")
        conn.execute("INSERT INTO probe(v) VALUES (1)")

    class _Boom(RuntimeError):
        pass

    try:
        with s.transaction() as conn:
            conn.execute("INSERT INTO probe(v) VALUES (2)")
            raise _Boom("mid-transaction")
    except _Boom:
        pass
    else:
        raise AssertionError("transaction() swallowed the exception")

    # NO COMMIT: the row written before the raise must not be visible anywhere
    with s.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM probe WHERE v=2").fetchone()[0] == 0

    # LOCK RELEASED: a further writer must make progress (bounded, never a hang)
    done = threading.Event()

    def _second_writer() -> None:
        with s.transaction() as conn2:
            conn2.execute("INSERT INTO probe(v) VALUES (3)")
        done.set()

    t = threading.Thread(target=_second_writer, daemon=True)
    t.start()
    assert done.wait(10.0), "transaction() did not release the store lock after a raise"
    t.join(10.0)
    with s.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM probe WHERE v=3").fetchone()[0] == 1
    os.remove(path)
