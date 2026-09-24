"""Append-only decision/event tape with a SHA-256 hash chain.

Tamper-evidence: every entry records ``prev_hash`` (the prior entry's
``entry_hash``) and its own ``entry_hash`` over a canonical, key-sorted
serialization. ``verify_chain`` recomputes the chain and returns
``(ok, entry_count)``. Any mutation of a historical row breaks the chain.

This module owns the ``audit_tape`` table schema and the append/verify logic.
``state_store.StateStore`` opens the connection, holds the write lock, and
delegates to these functions.
"""
from __future__ import annotations

import hashlib
import json
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
"""

GENESIS = "GENESIS"


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
    """Append one immutable audit entry, linked to the previous one."""
    cur = conn.execute(
        "SELECT entry_hash FROM audit_tape ORDER BY seq DESC LIMIT 1"
    )
    row = cur.fetchone()
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
    conn.execute(
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
    payload["seq"] = cur.lastrowid
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
