"""Append-only decision/event tape with a SHA-256 hash chain.

Tamper-evidence: every entry records ``prev_hash`` (the prior entry's
``entry_hash``) and its own ``entry_hash`` over a canonical, key-sorted
serialization. ``verify_chain`` recomputes the chain and returns
``(ok, entry_count)``. Any mutation of a historical row breaks the chain.

This module owns the ``audit_tape`` table schema and the append/verify logic.
``state_store.StateStore`` opens the connection, holds the write lock, and
delegates to these functions.

WRITE ATOMICITY / SINGLE WRITER (F-5)
-------------------------------------
``prev_hash`` is a read-modify-write over the tape tail: read the last
``entry_hash``, hash the new payload with it, insert. Two writers that read the
same tail both append a child of it and the hash chain **forks** — and a fork is
the one audit failure that is invisible until some later ``verify_chain``
inspects it. The guard has two halves, both of which must hold:

* **in-process** — ``StateStore.append_audit`` holds ``self._lock`` and
  ``StateStore.transaction()`` spans the caller's whole body;
* **cross-process** — a writer of this tape takes SQLite's RESERVED write lock
  *before* the tail read (``BEGIN IMMEDIATE`` as the first statement on its
  connection), so a peer process blocks at its own ``BEGIN`` instead of racing
  the read. ``IMMEDIATE`` (not ``DEFERRED``) is load-bearing: a deferred
  transaction only takes the write lock at the ``INSERT``, i.e. *after* the read
  that must be protected.

The same lock is also **claimed** here through the ``audit_writer`` row, which
makes the single-writer property explicit and observable (and additionally
upgrades a transaction inherited from a caller that had already begun one, where
``BEGIN IMMEDIATE`` would raise). See ``append_audit``.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import threading
import time
from typing import Any, Dict, Optional, Tuple

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_tape (
  seq           INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id      TEXT,
  timestamp     REAL,
  session_id    TEXT,
  agent         TEXT,
  project       TEXT,
  assessment    TEXT,
  directive     TEXT,
  evidence_hash TEXT,
  prev_hash     TEXT,
  entry_hash    TEXT
);
CREATE TABLE IF NOT EXISTS audit_writer (
  id       INTEGER PRIMARY KEY CHECK (id = 1),
  pid      INTEGER,
  hostname TEXT,
  thread   TEXT,
  held_at  REAL
);
"""

GENESIS = "GENESIS"

#: Single-writer claim row. Exactly one row exists (CHECK (id = 1)), so claiming
#: it is an exclusive-ownership marker rather than a lease: it is re-claimed by
#: every write and never released, which is why a crashed writer cannot wedge the
#: tape — the next writer takes the write lock (and with it the row) anyway.
_WRITER_DDL = (
    "CREATE TABLE IF NOT EXISTS audit_writer ("
    "id INTEGER PRIMARY KEY CHECK (id = 1), pid INTEGER, hostname TEXT, "
    "thread TEXT, held_at REAL)"
)
_WRITER_UPSERT = (
    "INSERT INTO audit_writer (id, pid, hostname, thread, held_at) "
    "VALUES (1, ?, ?, ?, ?) "
    "ON CONFLICT(id) DO UPDATE SET pid=excluded.pid, hostname=excluded.hostname, "
    "thread=excluded.thread, held_at=excluded.held_at"
)


def _hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:  # noqa: BLE001 - a sandbox may have no resolvable host
        return "unknown"


def writer_owner(conn) -> Optional[Dict[str, Any]]:
    """Return the current single-writer claim (``None`` if the tape never wrote).

    Diagnostic surface for operators: which process last claimed the write lock.
    Reading it takes no lock beyond SQLite's normal shared read.
    """
    row = conn.execute(
        "SELECT pid, hostname, thread, held_at FROM audit_writer WHERE id = 1"
    ).fetchone()
    if not row:
        return None
    return {
        "pid": row[0],
        "hostname": row[1],
        "thread": row[2],
        "held_at": row[3],
    }


def _canon(entry: Dict[str, Any]) -> str:
    return json.dumps(entry, sort_keys=True, separators=(",", ":"))


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def append_audit(
    conn,
    *,
    event_id: str,
    timestamp: float,
    session_id: str,
    agent: str,
    project: str,
    assessment: Optional[str] = None,
    directive: Optional[str] = None,
    evidence_hash: Optional[str] = None,
) -> Dict[str, Any]:
    """Append one immutable audit entry, linked to the previous one.

    ATOMICITY (F-5). The tail read below and the insert at the bottom are one
    read-modify-write; two concurrent writers that both read the same tail fork
    the chain. The caller decides the connection's ``isolation_level`` —
    ``StateStore.transaction()`` yields a default-isolation connection, a raw
    ``sqlite3.connect(path)`` may be autocommit — so this function cannot rely on
    the driver: it takes the write lock itself, as the FIRST statement on the
    connection given to it.

      1. ``BEGIN IMMEDIATE`` (skipped when the caller already opened a
         transaction — SQLite rejects a nested BEGIN, and such a transaction
         already covers the window).
      2. claim the single-writer row and, if this is a pre-F-5 database, create
         it (DDL is transactional in SQLite; deliberately NOT
         ``executescript``, which would implicitly COMMIT the open transaction
         and surrender the lock we just took).
      3. read the tail — now strictly under the write lock.

    Step 2 is what makes the guard independent of step 1: inside a caller's
    already-open deferred transaction the upsert takes the write lock *before*
    the tail read, so the fork window is closed there too.
    """
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    conn.execute(_WRITER_DDL)
    conn.execute(
        _WRITER_UPSERT,
        (
            os.getpid(),
            _hostname(),
            threading.current_thread().name,
            time.time(),
        ),
    )

    tail = conn.execute(
        "SELECT entry_hash FROM audit_tape ORDER BY seq DESC LIMIT 1"
    )
    row = tail.fetchone()
    prev_hash = row[0] if row else GENESIS

    payload = {
        "event_id": event_id,
        "timestamp": timestamp,
        "session_id": session_id,
        "agent": agent,
        "project": project,
        "assessment": assessment,
        "directive": directive,
        "evidence_hash": evidence_hash,
        "prev_hash": prev_hash,
    }
    entry_hash = _hash(_canon(payload))
    inserted = conn.execute(
        "INSERT INTO audit_tape "
        "(event_id, timestamp, session_id, agent, project, assessment, "
        " directive, evidence_hash, prev_hash, entry_hash) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            event_id,
            timestamp,
            session_id,
            agent,
            project,
            assessment,
            directive,
            evidence_hash,
            prev_hash,
            entry_hash,
        ),
    )
    conn.commit()
    payload["entry_hash"] = entry_hash
    payload["seq"] = inserted.lastrowid
    return payload


def verify_chain(conn) -> Tuple[bool, int]:
    """Return (chain_intact, entry_count). Non-intact -> (False, first_bad_seq)."""
    rows = conn.execute(
        "SELECT seq, event_id, timestamp, session_id, agent, project, "
        "assessment, directive, evidence_hash, prev_hash, entry_hash "
        "FROM audit_tape ORDER BY seq ASC"
    ).fetchall()
    prev = GENESIS
    count = 0
    for r in rows:
        entry = {
            "event_id": r[1],
            "timestamp": r[2],
            "session_id": r[3],
            "agent": r[4],
            "project": r[5],
            "assessment": r[6],
            "directive": r[7],
            "evidence_hash": r[8],
            "prev_hash": r[9],
        }
        if r[10] != _hash(_canon(entry)):
            return (False, r[0])
        if r[9] != prev:
            return (False, r[0])
        prev = r[10]
        count += 1
    return (True, count)
