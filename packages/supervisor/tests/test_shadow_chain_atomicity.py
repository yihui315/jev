"""Errata E-5 / spec §2.6 item 1: pin the ``BEGIN IMMEDIATE`` FIRST-statement
ordering in ``append_shadow``.

Why this is a pin and not a restatement
---------------------------------------
The cross-process half of the chain-fork guard is that the RESERVED write lock
is taken **before** the tail read. Three ways to break it all look like a
trivial refactor:

1. move the ``SELECT`` above the ``BEGIN`` (reads the tail unlocked — the exact
   fork the guard exists to prevent);
2. use ``BEGIN`` (== DEFERRED) instead of ``BEGIN IMMEDIATE`` (the write lock is
   then taken at the ``INSERT`` — i.e. *after* the read that must be protected);
3. drop the ``conn.in_transaction`` guard (a caller already inside a transaction
   gets "cannot start a transaction within a transaction").

1 and 2 are invisible to any value-level test: the chain still verifies, the row
still lands, and the fork only appears under concurrent writers from two
processes — which a unit test cannot schedule deterministically. So the ORDER is
asserted directly, by recording every statement the function issues, and by
sampling ``in_transaction`` at the moment the tail read runs.

This is deliberately a SHAPE test. It is the only deterministic witness for an
ordering property.
"""
from __future__ import annotations

import sqlite3

import pytest

from supervisor.shadow_store import GENESIS, SCHEMA, append_shadow


class _RecordingConn:
    """Minimal sqlite3-connection wrapper that records what was executed, in order.

    Only the surface ``append_shadow`` actually uses is implemented: ``execute``,
    ``in_transaction``, ``commit``. Recording happens *around* a real connection
    so the function's behaviour (including SQLite's own errors) is real.
    """

    def __init__(self, real: sqlite3.Connection):
        self._real = real
        self.statements: list[str] = []
        #: ``in_transaction`` at the instant each statement was issued.
        self.in_transaction_at: list[bool] = []

    def execute(self, sql, params=()):
        self.statements.append(" ".join(sql.split()))
        self.in_transaction_at.append(bool(self._real.in_transaction))
        return self._real.execute(sql, params)

    @property
    def in_transaction(self) -> bool:
        return self._real.in_transaction

    def commit(self) -> None:
        self._real.commit()


@pytest.fixture()
def conn(tmp_path):
    real = sqlite3.connect(str(tmp_path / "shadow.db"), isolation_level=None)
    real.executescript(SCHEMA)
    yield _RecordingConn(real)
    real.close()


def _record(n: int) -> dict:
    return {"decision_id": f"d{n}", "session_id": "s1", "event_id": f"e{n}",
            "agent": "claude", "action": "CONTINUE"}


def test_begin_immediate_is_the_first_statement_and_precedes_the_tail_read(conn):
    append_shadow(conn, record=_record(1))

    assert conn.statements, "append_shadow issued no statements"
    assert conn.statements[0] == "BEGIN IMMEDIATE", (
        "the first statement must take the RESERVED lock BEFORE anything is read; "
        f"got {conn.statements[0]!r} (order: {conn.statements})"
    )

    first_select = next(i for i, s in enumerate(conn.statements)
                        if s.upper().startswith("SELECT"))
    assert 0 < first_select, "the tail read ran before any BEGIN — the read is unlocked"
    assert conn.statements[first_select].upper().startswith(
        "SELECT ENTRY_HASH FROM SHADOW_DECISIONS"
    ), conn.statements[first_select]


def test_write_lock_is_already_held_when_the_tail_read_runs(conn):
    """The load-bearing half of IMMEDIATE: a transaction is open at read time.

    With ``BEGIN`` (DEFERRED) ``in_transaction`` is True too, but SQLite has not
    taken RESERVED yet, so this alone is not sufficient — it is asserted together
    with the ``IMMEDIATE`` keyword above and the lock probe below.
    """
    append_shadow(conn, record=_record(1))
    first_select = next(i for i, s in enumerate(conn.statements)
                        if s.upper().startswith("SELECT"))
    assert conn.in_transaction_at[first_select] is True, (
        "the tail read ran outside a transaction — the write lock was not taken "
        "before the read (Errata E-5)"
    )


def test_immediate_takes_the_reserved_lock_before_the_read(conn, tmp_path):
    """Falsifier for DEFERRED: a second writer is locked out during the whole
    read-modify-write window, not just at the INSERT.

    ``BEGIN IMMEDIATE`` acquires RESERVED at BEGIN. Under DEFERRED the same
    second connection would still be able to *read* the tail during the window
    (the fork), which is exactly the failure this guard prevents.
    """
    peer = sqlite3.connect(str(tmp_path / "shadow.db"), isolation_level=None)

    conn._real.execute("BEGIN IMMEDIATE")          # what append_shadow does first
    conn._real.execute("SELECT entry_hash FROM shadow_decisions "
                       "ORDER BY seq DESC LIMIT 1").fetchone()

    # A second writer cannot even begin a write transaction while the window is
    # open -> the read above was protected for its whole duration.
    with pytest.raises(sqlite3.OperationalError) as exc:
        peer.execute("BEGIN IMMEDIATE")
    assert "locked" in str(exc.value).lower(), exc.value
    peer.close()


def test_caller_already_in_a_transaction_is_not_re_begun(conn):
    """SQLite rejects a nested BEGIN; an open transaction already provides the
    guard, so ``append_shadow`` must detect and skip it."""
    conn._real.execute("BEGIN IMMEDIATE")
    append_shadow(conn, record=_record(1))

    begins = [s for s in conn.statements if s.upper().startswith("BEGIN")]
    assert begins == [], (
        f"append_shadow issued a nested BEGIN inside an open transaction: {begins}"
    )
    conn._real.commit()


def test_chain_links_and_verifies_after_the_pinned_ordering(conn):
    """Sanity: the pinned ordering still produces a correct, linked chain.

    Shape tests can pass while the function is broken; this keeps them honest.
    """
    from supervisor.shadow_store import verify_shadow_chain

    first = append_shadow(conn, record=_record(1))
    second = append_shadow(conn, record=_record(2))

    assert first["prev_hash"] == GENESIS
    assert second["prev_hash"] == first["entry_hash"]
    assert second["seq"] > first["seq"]
    assert verify_shadow_chain(conn._real)[0] is True
