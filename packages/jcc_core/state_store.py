"""State Store: persistent, append-only-leaning storage for the control plane.

Stores (in a single SQLite database using WAL journaling at
``config.state_store_db_path``, default ``F:/jev/runtime/jcc/jcc.db``):

  * ``sessions``      — normalized session state (identity = session_id)
  * ``events``       — raw AEP envelopes (dedup key: (session_id, event_id);
                       also indexed on delivery_id)
  * ``evidence``     — evidence fingerprints/snapshots
  * ``assessments``  — Jev scoring outputs (probabilities only)
  * ``directives``   — policy decisions (UNSIGNED in P1)
  * ``audit_tape``   — immutable, hash-chained decision/event log (audit.py)

WAL mode gives crash/restart durability: a kill+restart leaves state and the
audit tape intact. All writes go through a single ``threading.Lock`` so the
store is safe to share across uvicorn worker threads.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Any, Dict, List, Optional

from . import audit as audit_mod
from .schemas import (
    Assessment,
    Directive,
    EvidenceSnapshot,
    SessionState,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  session_id        TEXT PRIMARY KEY,
  agent             TEXT,
  project           TEXT,
  cwd               TEXT,
  status            TEXT,
  created_at        REAL,
  updated_at        REAL,
  last_event_id     TEXT,
  event_count       INTEGER,
  evidence_count    INTEGER,
  current_assessment TEXT,
  current_directive  TEXT
);
CREATE TABLE IF NOT EXISTS events (
  event_id    TEXT,
  session_id  TEXT,
  delivery_id TEXT,
  agent       TEXT,
  project     TEXT,
  timestamp   REAL,
  event_type  TEXT,
  payload     TEXT,
  evidence    TEXT,
  PRIMARY KEY (session_id, event_id)
);
CREATE INDEX IF NOT EXISTS idx_events_delivery ON events(delivery_id);
CREATE TABLE IF NOT EXISTS evidence (
  evidence_id     TEXT PRIMARY KEY,
  session_id      TEXT,
  source_event_id TEXT,
  timestamp       REAL,
  fingerprint     TEXT,
  summary         TEXT,
  complete        INTEGER
);
CREATE TABLE IF NOT EXISTS assessments (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT,
  timestamp  REAL,
  agent      TEXT,
  model      TEXT,
  provider   TEXT,
  data       TEXT
);
CREATE TABLE IF NOT EXISTS directives (
  directive_id    TEXT PRIMARY KEY,
  session_id      TEXT,
  timestamp       REAL,
  action          TEXT,
  target_actuator TEXT,
  params          TEXT,
  policy_version  TEXT,
  signed          INTEGER,
  signature       TEXT,
  evidence_hash   TEXT,
  acted           INTEGER,
  actuation_reason TEXT
);
""" + audit_mod.SCHEMA


class StateStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    # -- connection / schema -------------------------------------------------
    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._conn()
            conn.executescript(SCHEMA)
            conn.commit()
            conn.close()

    @property
    def is_wal(self) -> bool:
        with self._lock:
            conn = self._conn()
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            conn.close()
            return mode.upper() == "WAL"

    # -- dedup ---------------------------------------------------------------
    def seen(self, session_id: str, event_id: str, delivery_id: str) -> bool:
        with self._lock:
            conn = self._conn()
            try:
                r1 = conn.execute(
                    "SELECT 1 FROM events WHERE session_id=? AND event_id=?",
                    (session_id, event_id),
                ).fetchone()
                r2 = conn.execute(
                    "SELECT 1 FROM events WHERE delivery_id=?", (delivery_id,)
                ).fetchone()
                return bool(r1) or bool(r2)
            finally:
                conn.close()

    # -- events --------------------------------------------------------------
    def add_event(self, event) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO events "
                    "(event_id, session_id, delivery_id, agent, project, "
                    " timestamp, event_type, payload, evidence) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        event.event_id,
                        event.session_id,
                        event.delivery_id,
                        str(event.agent.value if hasattr(event.agent, "value") else event.agent),
                        event.project,
                        event.timestamp or 0.0,
                        str(event.event_type.value if hasattr(event.event_type, "value") else event.event_type),
                        json.dumps(event.payload or {}, sort_keys=True),
                        json.dumps(event.evidence, sort_keys=True) if event.evidence is not None else None,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    # -- sessions ------------------------------------------------------------
    def upsert_session(self, s: SessionState) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO sessions "
                    "(session_id, agent, project, cwd, status, created_at, "
                     "updated_at, last_event_id, event_count, evidence_count, "
                     "current_assessment, current_directive) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        s.session_id,
                        str(s.agent.value if hasattr(s.agent, "value") else s.agent),
                        s.project,
                        s.cwd,
                        str(s.status.value if hasattr(s.status, "value") else s.status),
                        s.created_at,
                        s.updated_at,
                        s.last_event_id,
                        s.event_count,
                        s.evidence_count,
                        json.dumps(s.current_assessment or {}, sort_keys=True),
                        json.dumps(s.current_directive or {}, sort_keys=True),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def get_session(self, session_id: str) -> Optional[SessionState]:
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    "SELECT session_id, agent, project, cwd, status, created_at, "
                    "updated_at, last_event_id, event_count, evidence_count, "
                    "current_assessment, current_directive FROM sessions WHERE session_id=?",
                    (session_id,),
                ).fetchone()
            finally:
                conn.close()
        if not row:
            return None
        return self._row_to_session(row)

    def list_sessions(self) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT session_id, agent, project, cwd, status, created_at, "
                    "updated_at, last_event_id, event_count, evidence_count, "
                    "current_assessment, current_directive FROM sessions ORDER BY updated_at DESC"
                ).fetchall()
            finally:
                conn.close()
        return [self._row_to_session(r).model_dump(mode="json") for r in rows]

    @staticmethod
    def _row_to_session(r) -> SessionState:
        return SessionState(
            session_id=r[0],
            agent=r[1],
            project=r[2],
            cwd=r[3],
            status=r[4],
            created_at=r[5],
            updated_at=r[6],
            last_event_id=r[7],
            event_count=r[8],
            evidence_count=r[9],
            current_assessment=json.loads(r[10]) if r[10] else None,
            current_directive=json.loads(r[11]) if r[11] else None,
        )

    # -- evidence ------------------------------------------------------------
    def add_evidence(self, e: EvidenceSnapshot) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO evidence "
                    "(evidence_id, session_id, source_event_id, timestamp, "
                     "fingerprint, summary, complete) VALUES (?,?,?,?,?,?,?)",
                    (
                        e.evidence_id,
                        e.session_id,
                        e.source_event_id,
                        e.timestamp,
                        e.fingerprint,
                        e.summary,
                        int(e.complete),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    # -- assessments ---------------------------------------------------------
    def add_assessment(self, a: Assessment) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT INTO assessments "
                    "(session_id, timestamp, agent, model, provider, data) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        a.session_id,
                        a.timestamp,
                        str(a.agent.value if hasattr(a.agent, "value") else a.agent),
                        a.model,
                        a.provider,
                        json.dumps(a.model_dump(mode="json"), sort_keys=True),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    # -- directives ----------------------------------------------------------
    def add_directive(self, d: Directive) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO directives "
                    "(directive_id, session_id, timestamp, action, target_actuator, "
                     "params, policy_version, signed, signature, evidence_hash, "
                     "acted, actuation_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        d.directive_id,
                        d.session_id,
                        d.timestamp,
                        str(d.action.value if hasattr(d.action, "value") else d.action),
                        d.target_actuator,
                        json.dumps(d.params, sort_keys=True),
                        d.policy_version,
                        int(d.signed),
                        d.signature,
                        d.evidence_hash,
                        int(d.acted),
                        d.actuation_reason,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def get_directives(self, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._conn()
            try:
                if session_id:
                    rows = conn.execute(
                        "SELECT directive_id, session_id, timestamp, action, "
                        "target_actuator, params, policy_version, signed, "
                        "signature, evidence_hash, acted, actuation_reason "
                        "FROM directives "
                        "WHERE session_id=? ORDER BY timestamp ASC",
                        (session_id,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT directive_id, session_id, timestamp, action, "
                        "target_actuator, params, policy_version, signed, "
                        "signature, evidence_hash, acted, actuation_reason "
                        "FROM directives "
                        "ORDER BY timestamp ASC"
                    ).fetchall()
            finally:
                conn.close()
        return [self._row_to_directive(r) for r in rows]

    @staticmethod
    def _row_to_directive(r) -> Dict[str, Any]:
        return {
            "directive_id": r[0],
            "session_id": r[1],
            "timestamp": r[2],
            "action": r[3],
            "target_actuator": r[4],
            "params": json.loads(r[5]) if r[5] else {},
            "policy_version": r[6],
            "signed": bool(r[7]),
            "signature": r[8],
            "evidence_hash": r[9],
            "acted": bool(r[10]),
            "actuation_reason": r[11],
        }

    # -- audit tape (delegates to audit.py) ----------------------------------
    def append_audit(self, **kwargs) -> Dict[str, Any]:
        with self._lock:
            conn = self._conn()
            try:
                return audit_mod.append_audit(conn, **kwargs)
            finally:
                conn.close()

    def get_audit(self, limit: int = 200) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT seq, event_id, timestamp, session_id, agent, project, "
                    "assessment, directive, evidence_hash, prev_hash, entry_hash "
                    "FROM audit_tape ORDER BY seq DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            finally:
                conn.close()
        return [
            {
                "seq": r[0],
                "event_id": r[1],
                "timestamp": r[2],
                "session_id": r[3],
                "agent": r[4],
                "project": r[5],
                "assessment": r[6],
                "directive": r[7],
                "evidence_hash": r[8],
                "prev_hash": r[9],
                "entry_hash": r[10],
            }
            for r in rows
        ]

    def verify_audit(self) -> tuple[bool, int]:
        with self._lock:
            conn = self._conn()
            try:
                return audit_mod.verify_chain(conn)
            finally:
                conn.close()
