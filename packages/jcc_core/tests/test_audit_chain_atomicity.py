"""F-5: pin the write-atomicity / single-writer shape of ``append_audit``.

Why a shape test at all
-----------------------
``prev_hash`` is read-modify-write over the tape tail. Two writers that read the
same tail both append a child of it and the chain **forks**; nothing raises, the
row count is still right, and the corruption only surfaces when a *later*
``verify_chain`` walks the table. Three trivial-looking edits all reintroduce it:

1. move the ``SELECT`` above the ``BEGIN`` (the tail is read unlocked);
2. use ``BEGIN`` (== DEFERRED) instead of ``BEGIN IMMEDIATE`` (the write lock is
   then taken at the ``INSERT`` — after the read that must be protected);
3. drop the ``conn.in_transaction`` guard (a caller already inside a transaction
   then gets "cannot start a transaction within a transaction").

1 and 2 are invisible to value-level tests, so the ORDER is asserted directly by
recording every statement the function issues, and the cross-process half is
asserted with real competing writers (threads and processes), which is the only
thing that would actually expose a fork.

PROCESSES, NOT THREADS, IS THE LOAD-BEARING CASE. ``StateStore.append_audit``
holds ``self._lock``, so an in-process *thread* test that goes through the store
passes for the wrong reason — the lock, not this function. The tests below
therefore drive ``audit.append_audit`` with connections the function opened
itself, which is what a second process does.
"""
from __future__ import annotations

import multiprocessing
import os
import sqlite3
import threading

import pytest

from jcc_core import audit


class _RecordingConn:
    """Record every statement ``append_audit`` issues, in order, around a real conn.

    Only the surface ``append_audit`` uses is implemented; the connection itself
    is a real one, so SQLite's own errors and ``lastrowid`` semantics are real.
    """

    def __init__(self, real: sqlite3.Connection):
        self._real = real
        self.statements: list[str] = []
        #: ``in_transaction`` at the instant each statement was issued.
        self.in_transaction_at: list[bool] = []
        self.writer_claims: list[bool] = []
        self.tail_read_at: list[bool] = []

    def execute(self, sql, params=()):
        key = " ".join(sql.split()).upper()
        self.statements.append(key)
        self.in_transaction_at.append(bool(self._real.in_transaction))
        self.writer_claims.append(key.startswith("INSERT INTO AUDIT_WRITER"))
        self.tail_read_at.append(key.startswith("SELECT ENTRY_HASH FROM AUDIT_TAPE"))
        return self._real.execute(sql, params)

    @property
    def in_transaction(self) -> bool:
        return self._real.in_transaction

    def commit(self) -> None:
        self._real.commit()


def _tail_read_index(conn: _RecordingConn) -> int:
    hits = [i for i, t in enumerate(conn.tail_read_at) if t]
    assert hits, "the tape tail was never read"
    return hits[0]


# --------------------------------------------------------------------------- #
# Shape: the ordering that makes the read-modify-write atomic
# --------------------------------------------------------------------------- #
@pytest.fixture()
def conn(tmp_path):
    """A deliberately hostile caller: autocommit, no driver-level transaction."""
    real = sqlite3.connect(str(tmp_path / "audit.db"), isolation_level=None)
    real.executescript(audit.SCHEMA)
    yield _RecordingConn(real)
    real.close()


def test_begin_immediate_is_the_first_statement_and_precedes_the_tail_read(conn):
    audit.append_audit(conn, event_id="e1", timestamp=1.0, session_id="s",
                       agent="a", project="p")

    assert conn.statements, "append_audit issued no statements"
    assert conn.statements[0].startswith("BEGIN IMMEDIATE"), (
        "the first statement must take the RESERVED lock BEFORE anything is read; "
        f"got {conn.statements[0]!r} (order: {conn.statements})"
    )
    first_select = _tail_read_index(conn)
    assert 0 < first_select, "the tail read ran before any BEGIN — the read is unlocked"
    assert conn.statements[first_select].startswith(
        "SELECT ENTRY_HASH FROM AUDIT_TAPE"
    ), conn.statements[first_select]


def test_write_lock_is_already_held_when_the_tail_read_runs(conn):
    """The load-bearing half of IMMEDIATE: a transaction is open at read time.

    With ``BEGIN`` (DEFERRED) ``in_transaction`` is True as well, so this alone
    proves nothing; together with the ``IMMEDIATE`` keyword pinned above and the
    lock probe below it closes the window from three directions.
    """
    audit.append_audit(conn, event_id="e1", timestamp=1.0, session_id="s",
                       agent="a", project="p")

    assert conn.in_transaction_at[_tail_read_index(conn)] is True, (
        "the tail read ran outside a transaction — the write lock was not taken "
        "before the read (F-5)"
    )


def test_immediate_keeps_a_peer_writer_out_of_the_read_modify_write_window(
    conn, tmp_path
):
    """Falsifier for DEFERRED: the window is locked, not just the INSERT.

    Two real connections on one file. The first performs exactly what
    ``append_audit`` performs first (BEGIN IMMEDIATE + tail read); a second
    writer attempting BEGIN IMMEDIATE during that window must be blocked. Under
    DEFERRED it would still be able to read the tail and race the insert.
    """
    peer = sqlite3.connect(str(tmp_path / "audit.db"), isolation_level=None)

    conn._real.execute("BEGIN IMMEDIATE")
    conn._real.execute(
        "SELECT entry_hash FROM audit_tape ORDER BY seq DESC LIMIT 1"
    ).fetchone()

    with pytest.raises(sqlite3.OperationalError) as exc:
        peer.execute("BEGIN IMMEDIATE")
    assert "locked" in str(exc.value).lower(), exc.value
    conn._real.rollback()
    peer.close()


def test_caller_already_in_a_transaction_is_not_re_begun(conn):
    """SQLite rejects a nested BEGIN; an open transaction must be reused.

    But reusing it must NOT weaken the guard: the single-writer claim row is
    written first, so the inherited (deferred) transaction still takes the write
    lock before the tail read.
    """
    conn._real.execute("BEGIN")  # caller's own deferred transaction
    audit.append_audit(conn, event_id="e1", timestamp=1.0, session_id="s",
                       agent="a", project="p")

    begins = [s for s in conn.statements if s.startswith("BEGIN")]
    assert begins == [], f"nested BEGIN inside an open transaction: {begins}"

    first_select = _tail_read_index(conn)
    claim = next(i for i, c in enumerate(conn.writer_claims) if c)
    assert claim < first_select, (
        "inside an inherited transaction the write lock must be taken before the "
        "tail read — otherwise the fork window is still open"
    )
    conn._real.commit()


# --------------------------------------------------------------------------- #
# Outcome: real competing writers must leave ONE contiguous chain
# --------------------------------------------------------------------------- #
def _appends_keep_one_contiguous_chain(conn, expected: int) -> None:
    ok, count = audit.verify_chain(conn)
    rows = conn.execute(
        "SELECT seq, prev_hash, entry_hash FROM audit_tape ORDER BY seq ASC"
    ).fetchall()
    assert ok is True, (
        f"chain forked under concurrent writers: {len(rows)} rows, "
        f"verify_chain -> {ok}"
    )
    assert count == expected, f"expected {expected} entries, verify_chain says {count}"
    for prev_row, row in zip(rows, rows[1:]):
        assert row[1] == prev_row[2], (
            f"seq={row[0]}: prev_hash does not continue the chain "
            f"({row[1][:12]}... != {prev_row[2][:12]}...) — the chain forked"
        )


def _worker(db_path: str, count: int, barrier, errors) -> None:
    """Append ``count`` entries from this process using its own connection."""
    try:
        conn = sqlite3.connect(db_path, isolation_level=None)
        try:
            conn.execute("PRAGMA busy_timeout=30000")
            barrier.wait(timeout=120)
            pid = os.getpid()
            for i in range(count):
                audit.append_audit(
                    conn,
                    event_id=f"e{pid}-{i}",
                    timestamp=float(i) + float(pid % 1000),
                    session_id="s",
                    agent="a",
                    project="p",
                )
        finally:
            conn.close()
    except BaseException as exc:  # noqa: BLE001 - relay the failure to the parent
        errors.put(f"{type(exc).__name__}: {exc}")


def _spawn_workers(tmp_path, procs: int, per_proc: int):
    path = str(tmp_path / "concurrent.db")
    seed = sqlite3.connect(path, isolation_level=None)
    seed.executescript(audit.SCHEMA)
    seed.commit()
    seed.close()

    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(procs, timeout=180)
    errors = ctx.Queue()
    workers = [
        ctx.Process(target=_worker, args=(path, per_proc, barrier, errors))
        for _ in range(procs)
    ]
    for w in workers:
        w.start()
    for w in workers:
        w.join(timeout=300)
    assert all(not w.is_alive() for w in workers), "a writer process never exited"
    relayed = []
    while not errors.empty():
        relayed.append(errors.get())
    assert relayed == [], f"writer process failed: {relayed}"

    return sqlite3.connect(path, isolation_level=None)


def test_multiprocess_appends_keep_one_contiguous_chain(tmp_path):
    """The cross-process case F-5 is about: two processes, one tape.

    RED before the fix (verified): without BEGIN IMMEDIATE both processes read
    the same tail and the chain forks — verify_chain returns False while every
    row is present and no error is raised.
    """
    reader = _spawn_workers(tmp_path, procs=2, per_proc=60)
    try:
        _appends_keep_one_contiguous_chain(reader, expected=120)
    finally:
        reader.close()


def test_threaded_appends_keep_one_contiguous_chain(tmp_path):
    """Same property, in-process: many connections, no store lock involved."""
    path = str(tmp_path / "threads.db")
    seed = sqlite3.connect(path, isolation_level=None)
    seed.executescript(audit.SCHEMA)
    seed.commit()
    seed.close()

    errors: list[str] = []
    barrier = threading.Barrier(8)

    def run(n: int) -> None:
        try:
            conn = sqlite3.connect(path, isolation_level=None)
            try:
                conn.execute("PRAGMA busy_timeout=30000")
                barrier.wait(timeout=120)
                for i in range(n):
                    audit.append_audit(
                        conn,
                        event_id=f"t{threading.get_ident()}-{i}",
                        timestamp=float(i),
                        session_id="s",
                        agent="a",
                        project="p",
                    )
            finally:
                conn.close()
        except BaseException as exc:  # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=run, args=(25,)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=300)
    assert errors == [], f"a writer thread failed: {errors}"

    reader = sqlite3.connect(path, isolation_level=None)
    try:
        _appends_keep_one_contiguous_chain(reader, expected=8 * 25)
    finally:
        reader.close()


# --------------------------------------------------------------------------- #
# The single-writer claim itself
# --------------------------------------------------------------------------- #
def test_writer_claim_names_the_writing_process(tmp_path):
    real = sqlite3.connect(str(tmp_path / "claim.db"), isolation_level=None)
    real.executescript(audit.SCHEMA)
    assert audit.writer_owner(real) is None, "no claim before the tape was written"

    audit.append_audit(real, event_id="e1", timestamp=1.0, session_id="s",
                       agent="a", project="p")
    owner = audit.writer_owner(real)
    assert owner is not None
    assert owner["pid"] == os.getpid()
    assert owner["thread"] == threading.current_thread().name
    assert owner["hostname"] and owner["hostname"] != "unknown"
    real.close()


def test_both_isolation_levels_produce_a_single_contiguous_chain(tmp_path):
    """The caller picks the isolation_level; the guard must not depend on it."""
    for iso, label in ((None, "autocommit"), ("", "default")):
        path = str(tmp_path / f"iso_{label}.db")
        conn = sqlite3.connect(path, isolation_level=iso)
        try:
            # Same prerequisite as production: the store owns the DDL (SCHEMA).
            conn.executescript(audit.SCHEMA)
            conn.commit()
            for i in range(5):
                audit.append_audit(
                    conn, event_id=f"e{i}", timestamp=float(i), session_id="s",
                    agent="a", project="p",
                )
            ok, count = audit.verify_chain(conn)
            assert ok is True and count == 5, f"isolation_level={iso!r}: {ok},{count}"
        finally:
            conn.close()
