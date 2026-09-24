"""Shadow decision tape: append-only hash chain + mutable label child table.

ADR-0008 D-7/D-13/D-15/D-18/D-23. Two tables, deliberately different:

* ``shadow_decisions`` — **append-only**, hash-chained (``prev_hash`` →
  ``entry_hash``), mirroring ``jcc_core/audit.py``. Tamper-evident.
* ``shadow_labels`` — **mutable** child table keyed ``(decision_id,
  label_source)`` so oracle vs human labels can coexist and be re-adjudicated
  WITHOUT breaking the immutable chain.

Nothing here is a frozen P0–P2 table, so this is the only DDL P3 introduces
(D-23/D-25: no DDL on frozen tables).
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Dict, List, Optional, Tuple

SCHEMA = """
CREATE TABLE IF NOT EXISTS shadow_decisions (
  seq              INTEGER PRIMARY KEY AUTOINCREMENT,
  decision_id      TEXT UNIQUE,
  session_id       TEXT, event_id TEXT, agent TEXT, project TEXT,
  observed_at      REAL,
  model            TEXT, provider TEXT,
  assessment_probabilities TEXT, assessment_band TEXT, assessment_flags TEXT,
  session_status   TEXT, evidence_complete INTEGER,
  event_seq        INTEGER, progress_seq INTEGER, stall_events INTEGER,
  budget_snapshot  TEXT, thresholds_snapshot TEXT,
  policy_version   TEXT, policy_config_hash TEXT,
  action           TEXT, rationale TEXT, trace_hash TEXT,
  arbitration_inputs_hash TEXT,
  blocked          INTEGER, blocker_code TEXT, needs_human INTEGER,
  directive_id     TEXT, signed INTEGER, signature TEXT,
  signing_alg      TEXT, signature_verified INTEGER,
  acted            INTEGER, actuation_reason TEXT,
  would_have_acted_in_p4 TEXT,
  prev_hash        TEXT, entry_hash TEXT
);
CREATE TABLE IF NOT EXISTS shadow_labels (
  decision_id        TEXT,
  label_source       TEXT,
  label_value        TEXT,
  label_numeric      REAL,
  label_window_events INTEGER,
  labeler            TEXT,
  labeled_at         REAL,
  PRIMARY KEY (decision_id, label_source)
);
"""

GENESIS = "GENESIS"

#: Columns carried/echoed by ``append_shadow`` and hashed into ``entry_hash``.
#: ``seq`` (autoincrement) and ``entry_hash`` are excluded from the canonical
#: payload — the same discipline as ``audit.py`` (the row id is assigned by the
#: engine, not part of the signed content).
_HASH_COLUMNS: Tuple[str, ...] = (
    "decision_id", "session_id", "event_id", "agent", "project", "observed_at",
    "model", "provider", "assessment_probabilities", "assessment_band",
    "assessment_flags", "session_status", "evidence_complete", "event_seq",
    "progress_seq", "stall_events", "budget_snapshot", "thresholds_snapshot",
    "policy_version", "policy_config_hash", "action", "rationale", "trace_hash",
    "arbitration_inputs_hash", "blocked", "blocker_code", "needs_human",
    "directive_id", "signed", "signature", "signing_alg", "signature_verified",
    "acted", "actuation_reason", "would_have_acted_in_p4",
)

_INSERT_COLUMNS: Tuple[str, ...] = _HASH_COLUMNS + ("prev_hash", "entry_hash")


def _canon(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _coerce(name: str, value: Any) -> Any:
    """SQLite only stores ints/reals/text/None. Normalise bools and JSON blobs."""
    if value is None:
        return None
    if name in ("blocked", "needs_human", "signed", "signature_verified", "acted", "evidence_complete"):
        return 1 if value else 0
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return value


def append_shadow(conn, *, record: Dict[str, Any]) -> Dict[str, Any]:
    """Append one immutable shadow decision, linked to the previous one.

    ``record`` is a mapping of column→value; unknown keys are ignored, missing
    keys default to ``None``. Returns the stored row (with ``seq``,
    ``prev_hash`` and ``entry_hash`` filled in).
    """
    row = conn.execute("SELECT entry_hash FROM shadow_decisions ORDER BY seq DESC LIMIT 1").fetchone()
    prev_hash = row[0] if row else GENESIS

    payload = {name: _coerce(name, record.get(name)) for name in _HASH_COLUMNS}
    payload["prev_hash"] = prev_hash
    entry_hash = _hash(_canon(payload))

    columns = ", ".join(_INSERT_COLUMNS)
    placeholders = ",".join("?" for _ in _INSERT_COLUMNS)
    values = [payload[name] for name in _HASH_COLUMNS] + [prev_hash, entry_hash]
    cur = conn.execute(
        f"INSERT INTO shadow_decisions ({columns}) VALUES ({placeholders})",  # noqa: S608 — fixed column list
        values,
    )
    conn.commit()
    stored = dict(payload)
    stored["entry_hash"] = entry_hash
    stored["seq"] = cur.lastrowid
    return stored


def verify_shadow_chain(conn) -> Tuple[bool, int]:
    """Return ``(intact, count)``; on tamper ``(False, first_bad_seq)``."""
    rows = conn.execute(
        f"SELECT seq, {', '.join(_HASH_COLUMNS)}, prev_hash, entry_hash "
        "FROM shadow_decisions ORDER BY seq ASC"
    ).fetchall()
    prev = GENESIS
    count = 0
    for r in rows:
        values = r[1 : 1 + len(_HASH_COLUMNS)]
        payload = {name: values[i] for i, name in enumerate(_HASH_COLUMNS)}
        payload["prev_hash"] = r[1 + len(_HASH_COLUMNS)]
        expected = _hash(_canon(payload))
        stored_hash = r[2 + len(_HASH_COLUMNS)]
        stored_prev = r[1 + len(_HASH_COLUMNS)]
        if stored_hash != expected or stored_prev != prev:
            return (False, int(r[0]))
        prev = stored_hash
        count += 1
    return (True, count)


def _row_to_dict(columns: List[str], row) -> Dict[str, Any]:
    return {name: row[i] for i, name in enumerate(columns)}


def read_decisions(
    conn,
    *,
    session_id: Optional[str] = None,
    limit: int = 500,
    with_labels: bool = True,
) -> List[Dict[str, Any]]:
    """Read decisions (optionally one session), most recent last."""
    sql = "SELECT * FROM shadow_decisions"
    params: List[Any] = []
    if session_id is not None:
        sql += " WHERE session_id = ?"
        params.append(session_id)
    sql += " ORDER BY seq DESC LIMIT ?"
    params.append(int(limit))
    cur = conn.execute(sql, params)
    columns = [d[0] for d in cur.description]
    out = [_row_to_dict(columns, r) for r in cur.fetchall()]
    if with_labels:
        for row in out:
            row["labels"] = get_labels(conn, row.get("decision_id") or "")
    return out


def set_label(
    conn,
    decision_id: str,
    *,
    source: str,
    value: str,
    numeric: Optional[float] = None,
    window_events: Optional[int] = None,
    labeler: Optional[str] = None,
) -> None:
    """UPSERT a label on ``(decision_id, source)`` — mutable by design (D-13)."""
    conn.execute(
        "INSERT INTO shadow_labels "
        "(decision_id, label_source, label_value, label_numeric, label_window_events, labeler, labeled_at) "
        "VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(decision_id, label_source) DO UPDATE SET "
        "label_value=excluded.label_value, label_numeric=excluded.label_numeric, "
        "label_window_events=excluded.label_window_events, labeler=excluded.labeler, "
        "labeled_at=excluded.labeled_at",
        (decision_id, source, value, numeric, window_events, labeler, time.time()),
    )
    conn.commit()


def get_labels(conn, decision_id: str) -> Dict[str, Dict[str, Any]]:
    """Return ``{label_source: label_row}`` for one decision."""
    cur = conn.execute(
        "SELECT decision_id, label_source, label_value, label_numeric, "
        "label_window_events, labeler, labeled_at FROM shadow_labels WHERE decision_id = ?",
        (decision_id,),
    )
    columns = [d[0] for d in cur.description]
    return {r[1]: _row_to_dict(columns, r) for r in cur.fetchall()}


def ensure_schema(conn) -> None:
    """Create the two tables if absent (idempotent)."""
    conn.executescript(SCHEMA)
    conn.commit()
