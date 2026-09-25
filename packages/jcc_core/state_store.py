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
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Tuple

from . import audit as audit_mod
from .policy import COST_UNITS_PER_ACTION
from .schemas import (
    Assessment,
    Directive,
    DirectiveAction,
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
  actuation_reason TEXT,
  origin          TEXT
);
""" + audit_mod.SCHEMA


#: Id prefixes of the two planes that persist a row for the SAME economic event
#: (P4 #82). The engine issues ``dir_<session>_<event>``
#: (``jcc_core.core.CoreEngine.ingest``) and the sentinel's charging copy issues
#: ``shadow_<session>_<event>`` (``supervisor.budget.BudgetGovernor._persist_directive``,
#: which exists so the charge survives D-34's store-derived counters). Dropping the
#: prefix yields the event identity both rows carry -- the only thing the two rows
#: share that also distinguishes separate events. This list is PINNED by
#: ``tests/test_directive_identity.py``: a plane that renames its id must fail a
#: test here, not silently change what the budget counters mean.
_DIRECTIVE_EVENT_PREFIXES = ("dir_", "shadow_")


def directive_event_key(directive_id: str, session_id: str) -> str:
    """Economic-event identity of a ``directives`` row (P4 #82 / R1).

    Two rows describing the same issued directive map to the SAME key even though
    their ``directive_id`` differs by plane prefix. A row of an unrecognised shape
    maps to a key unique to itself, so the worst case of a naming change is the
    old over-count, never an under-count.
    """
    for prefix in _DIRECTIVE_EVENT_PREFIXES:
        if directive_id.startswith(prefix):
            rest = directive_id[len(prefix):]
            if rest:
                return f"{session_id}\x1f{rest}"
    return f"{session_id}\x1f!unrecognised:{directive_id}"


#: The closed set of writers a ``directives`` row may be attributed to (F1).
#: Anything else -- including the empty string and ``None`` -- is REFUSED, not
#: defaulted: an unattributed row is exactly the thing that let two planes
#: overwrite each other unnoticed (#82), and a default would make the
#: attribution disappear the moment a caller forgets it.
_DIRECTIVE_ORIGINS = ("engine", "supervisor")

#: Which prefix belongs to which writer. One prefix per origin, so the
#: namespace itself is the first line of the attribution.
_ORIGIN_OF_PREFIX = (("dir_", "engine"), ("shadow_", "supervisor"))


def directive_plane_of(directive_id: str) -> Optional[str]:
    """Writer a ``directive_id`` shape can ONLY have been issued by (F1).

    ``None`` means the shape is not attributable to either writer. A bare
    prefix (``"dir_"``) is NOT attributable: it identifies no event, so it
    carries no evidence either -- the same guard ``directive_event_key`` uses.
    """
    for prefix, origin in _ORIGIN_OF_PREFIX:
        if directive_id.startswith(prefix) and len(directive_id) > len(prefix):
            return origin
    return None


def resolve_directive_origin(directive_id: str, origin: str) -> str:
    """Attribution gate for a ``directives`` row (F1, fail-closed).

    Two independent refusals, both of which must hold:

    1. ``origin`` itself must be one of :data:`_DIRECTIVE_ORIGINS`. The empty
       string, ``None`` and any typo are REFUSED rather than coerced.
    2. ``directive_id`` must carry a prefix owned by that ``origin``. A
       ``dir_`` id can only be an engine directive and a ``shadow_`` id can only
       be the sentinel's charging copy; an id of an UNRECOGNISED shape is
       refused no matter what ``origin`` says, because there is nothing in the
       id that could tell the two planes apart -- which is precisely how #82
       produced two rows that looked like four directives.

    Raises ``ValueError`` -- never warns, never defaults. A writer that cannot
    say who it is must not write.
    """
    if origin not in _DIRECTIVE_ORIGINS:
        raise ValueError(
            f"directive {directive_id!r} was attributed to origin {origin!r}; "
            f"only {_DIRECTIVE_ORIGINS} may persist a directive, and an "
            "unattributed write is refused rather than defaulted"
        )
    plane = directive_plane_of(directive_id)
    if plane is None:
        raise ValueError(
            f"directive_id {directive_id!r} matches no prefix owned by a writer "
            f"({[p for p, _o in _ORIGIN_OF_PREFIX]}), so origin={origin!r} cannot "
            "be substantiated; refusing an unattributable directive"
        )
    if plane != origin:
        raise ValueError(
            f"directive_id {directive_id!r} can only be issued by {plane!r}, but "
            f"origin={origin!r} -- refusing this cross-origin write"
        )
    return origin


# -- M1: attribution of pre-F1 rows (ADR-0011) ----------------------------- #


def backfill_directive_origins(db_path: str, *, report_path: Optional[str] = None) -> Dict[str, Any]:
    """Attribute the pre-F1 ``directives`` rows of ONE database (ADR-0011, M1).

    A row written before F1/F2 has no ``origin``. M1 gives it one by
    DERIVATION, never by assignment, and only in the three clauses the ADR
    permits:

    1. ONLY ``origin IS NULL`` rows are touched. A row that already carries an
       origin is never rewritten -- whatever its id prefix suggests. A stored
       origin is the one artefact that distinguishes a pre-F1 row from a
       post-F1 one, and overwriting it erases the evidence of what M1 did.
    2. The value is ``directive_plane_of(directive_id)`` -- the SAME lookup F1
       calls, so M1 and F1 cannot disagree about what a shape means.
    3. An unattributable shape is not attributed. ``directive_plane_of``
       returning ``None`` leaves the row NULL; it stays refused by F2's
       NULL-prior gate, and it is listed under ``refused``. ``"engine"`` is NOT
       the fallback -- fabricating provenance for a row whose provenance the id
       cannot record is the #82 defect, laundered.
    4. A census report is written. ``admissible`` is False whenever ``refused``
       is non-empty, because a database M1 could not attribute is not release
       evidence (ADR-0009).

    THIS IS NOT AN AUTOMATIC SIDE EFFECT AND MUST NOT BECOME ONE. Nothing on
    the read or write path calls it: no import, no ``__init__``, no schema
    upgrade, no ``add_directive``. A migration is code expected to run exactly
    once, unattended, over data it has never seen, so it is invoked by an
    operator, against an explicit path, and the report it leaves behind is the
    record that it ran.

    One database per call; ``databases_visited`` exists so that a caller
    walking a corpus can concatenate the reports rather than invent a second
    format. Report is a run artefact, not a recomputable one: it carries the
    wall-clock time and the database mtime on purpose.
    """
    store = StateStore(db_path)  # idempotent schema; also validates the path
    report: Dict[str, Any] = {
        "db_path": str(db_path),
        "database_mtime": os.path.getmtime(db_path),
        "generated_at": time.time(),
        "databases_visited": [str(db_path)],
        "directive_rows": 0,
        "null_priors": 0,
        "attributed": 0,
        "refused": [],       # unattributable shapes, clause 3
        "still_null": [],    # NULL priors that were refused, i.e. left alone
        "admissible_as_release_evidence": True,
    }
    with store.transaction() as conn:
        rows = conn.execute(
            "SELECT directive_id FROM directives WHERE origin IS NULL"
        ).fetchall()
        report["null_priors"] = len(rows)
        for (directive_id,) in rows:
            plane = directive_plane_of(directive_id)
            if plane is None:
                report["refused"].append(
                    {"directive_id": directive_id,
                     "reason": "no prefix owned by a writer, or a bare prefix "
                               "naming no event (ADR-0011 clause 3): left NULL, "
                               "left refused by the F2 gate"}
                )
                report["still_null"].append(directive_id)
                continue
            cur = conn.execute(
                "UPDATE directives SET origin=? WHERE directive_id=? "
                "AND origin IS NULL",
                (plane, directive_id),
            )
            report["attributed"] += cur.rowcount
        report["directive_rows"] = conn.execute(
            "SELECT COUNT(*) FROM directives"
        ).fetchone()[0]
    report["admissible_as_release_evidence"] = not report["refused"]
    if report_path:
        parent = os.path.dirname(os.path.abspath(report_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, sort_keys=True)
    return report


def _action_matches(action: Optional[str], name: str) -> bool:
    """Compare a persisted ``action`` column against a ``DirectiveAction`` value.

    The column is written from ``Directive.action.value`` (a plain string) by
    ``add_directive``, but rows can only reach the table through the validated
    model -- this tolerates the enum's repr as well, and nothing else.
    """
    if not action:
        return False
    value = str(action).strip()
    if value.startswith("DirectiveAction."):
        value = value.split(".", 1)[1]
    return value.strip().upper() == name


def _directive_events(conn, session_id: str) -> Dict[str, Tuple[str, Optional[float]]]:
    """Collapse ``directives`` rows to ONE entry per economic event.

    ORDER MATTERS: ``ORDER BY rowid`` (insertion order) and a later row simply
    overwrites, so the surviving row is the LAST written for that event -- the
    sentinel's charging record, and the one carrying a substituted action when a
    budget-blocked event is re-issued as ESCALATE.
    """
    events: Dict[str, Tuple[str, Optional[float]]] = {}
    for directive_id, action, ts in conn.execute(
        "SELECT directive_id, action, timestamp FROM directives "
        "WHERE session_id=? ORDER BY rowid",
        (session_id,),
    ).fetchall():
        events[directive_event_key(directive_id, session_id)] = (action, ts)
    return events


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

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Yield a FRESH connection and always close it — the PUBLIC accessor.

        WHY THIS EXISTS (ADR-0008 D-2 + the P3 shadow-persistence defect): the
        supervisor plane owns its own tables (``shadow_decisions``, spec §2.6) and
        therefore needs a connection to this database. It previously did
        ``getattr(store, "_conn")`` — reaching into this class's PRIVATE method.
        ``_conn`` is a METHOD returning a fresh connection, so the supervisor
        received a truthy *bound method* and its ``executescript`` raised; the
        failure was swallowed and the shadow plane recorded nothing at all. Worse,
        the supervisor's test double exposed ``_conn`` as an *attribute*, so the
        suite defined an interface the real store never had. A generic public
        accessor makes the dependency explicit and pointing DOWNWARD
        (``supervisor`` -> ``jcc_core``, never the reverse).

        Deliberately does NOT take ``self._lock``: the supervisor writes only to
        its own tables, and ``_lock`` is a non-reentrant ``threading.Lock``, so
        holding it here would deadlock any re-entrant core call made inside the
        ``with`` block.
        """
        conn = self._conn()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection while HOLDING the store lock; commit then close.

        Public, **lock-scoped** accessor for a caller that must run a whole
        read-modify-write atomically (spec §2.6 item 1, Errata **E-5**). The
        shadow plane's append is exactly that: ``shadow_store.append_shadow``
        does a non-atomic ``SELECT … ORDER BY seq DESC LIMIT 1`` then ``INSERT``
        (``shadow_store.py:102-116``); two concurrent appends could therefore
        read the same ``prev_hash`` and **fork the hash chain**. ``connection()``
        is lock-free by design and does NOT cover that — this does.

        Discipline mirrors ``append_audit`` (``state_store.py:393-399``: take
        ``self._lock``, fresh conn, close in ``finally``) but it **yields** the
        connection instead of delegating to a sibling module — deliberately,
        because ``shadow_store`` lives in ``packages/supervisor`` and a
        ``jcc_core`` -> ``supervisor`` import would violate ADR-0008 **D-2**.
        The lock spans the caller's whole ``with`` body, so keep it SHORT and do
        not call back into ``StateStore`` from inside (``_lock`` is a
        non-reentrant ``threading.Lock``).

        Commits on clean exit; on an exception it closes without committing
        (fail-closed — no partial state is published).

        **IN-PROCESS ONLY.** ``_lock`` is a single-instance ``threading.Lock``,
        so it serialises threads in ONE process. Closing the chain-fork across
        *processes* needs a SECOND mechanism owned by the writer: the sibling
        write must issue an explicit ``BEGIN IMMEDIATE`` as the **first**
        statement on its connection (spec §2.6 item 1 / Errata E-5). Ordering is
        load-bearing — a ``BEGIN`` issued after any DML on this module's
        default-isolation connection raises ``OperationalError: cannot start a
        transaction within a transaction`` (verified empirically).

        Use ``connection()`` for lock-free READS; use this for the write.
        """
        with self._lock:
            conn = self._conn()
            try:
                yield conn
                conn.commit()
            finally:
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
    def add_directive(self, d: Directive, *, origin: str) -> None:
        """Persist an issued directive. ``origin`` is KEYWORD-ONLY AND REQUIRED.

        ``origin`` names the writer that issued ``d`` (``"engine"`` or
        ``"supervisor"``) and is validated against the directive's id prefix
        (F1) before anything is written. It has NO default on purpose: a
        default turns a forgotten argument into an unattributed row, and an
        unattributed row is what let the two planes write the same economic
        event twice (#82), and what ``directive_event_key`` has to guess at.

        F2 -- the cross-origin overwrite gate. ``INSERT OR REPLACE`` would let
        a second writer silently replace the first's row, so the existing
        attribution is checked first: same ``directive_id`` AND a DIFFERENT
        origin is REFUSED; the same origin may still replace its own row, which
        is the idempotent retry path. This is also what makes the #82
        per-event collapse sound -- after F2, both rows of an event are known
        to come from ONE writer, so collapsing them cannot merge two planes'
        views of the same event.

        Raises ``ValueError`` on any of those refusals; the write is not
        attempted, so a refused directive leaves the table as it was.
        """
        resolved = resolve_directive_origin(d.directive_id, origin)
        with self._lock:
            conn = self._conn()
            try:
                prior = conn.execute(
                    "SELECT origin FROM directives WHERE directive_id=?",
                    (d.directive_id,),
                ).fetchone()
                if prior is not None and prior[0] != resolved:
                    existing = prior[0]
                    note = (
                        "an UNATTRIBUTED row (origin NULL: written by a "
                        "pre-F1 database, so its plane is unknown)"
                        if existing is None
                        else f"{existing!r}"
                    )
                    raise ValueError(
                        f"directive {d.directive_id!r} is already persisted as "
                        f"{note} and cannot be replaced by {resolved!r}; a "
                        "cross-origin overwrite would destroy the other writer's "
                        "record of the same issued directive"
                    )
                conn.execute(
                    "INSERT OR REPLACE INTO directives "
                    "(directive_id, session_id, timestamp, action, target_actuator, "
                    "params, policy_version, signed, signature, evidence_hash, "
                    "acted, actuation_reason, origin) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                        resolved,
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
                        "signature, evidence_hash, acted, actuation_reason, "
                        "origin "
                        "FROM directives "
                        "WHERE session_id=? ORDER BY timestamp ASC",
                        (session_id,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT directive_id, session_id, timestamp, action, "
                        "target_actuator, params, policy_version, signed, "
                        "signature, evidence_hash, acted, actuation_reason, "
                        "origin "
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
            "origin": r[12],
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

    # -- P3 budget surface (ADR-0008 D-34 / spec §2.4) -----------------------
    #: Window used to derive the per-minute directive rate (D-34
    #: ``directives_per_minute``).
    RATE_WINDOW_S = 60.0

    def budget_snapshot(self, session_id: str) -> Dict[str, float]:
        """Derive the D-34 budget counters from PERSISTED state (restart-safe).

        ADR-0008 D-34 / spec §2.4: *"Counters are DERIVED from persisted state
        … so a restart cannot reset an escalation."* Every value is a ``COUNT(*)``
        over the frozen P1/P2 tables (plus ``sessions.created_at``), so this needs
        no P3 knowledge and remains valid for pre-P3 rows. Absent this surface the
        governor silently falls back to its in-memory ledger, which is exactly the
        restart-unsafe source of truth D-34 forbade.

        RATE USED COUNTS EVENTS, NOT ROWS. ``rate_used`` is the number of
        directives *issued* in the last ``RATE_WINDOW_S`` seconds, so it is
        DERIVED OVER THE COLLAPSED EVENT SET BELOW, never as ``COUNT(*)
        FROM directives``. Two planes persist one row each for the same issued
        directive (engine ``dir_<sid>_<eid>``, sentinel's charging copy
        ``shadow_<sid>_<eid>``), so a row count reads TWICE the real call rate:
        the ``directives_per_minute`` ceiling then fires at half the true rate.
        Measured on the #82 probe (2 ingests, shadow on): rows = 4, events = 2,
        and ``rate_used`` used to report 4. Rows are collapsed by
        ``directive_event_key`` BEFORE any counter below is derived, so
        ``rate_used`` == ``directives_used`` in shape; the semantic change from
        "rows" to "events" lands here and is recorded in the commit message.

        CLOCK DOMAIN (load-bearing): ``first_seen`` is a WALL-CLOCK epoch second
        (``sessions.created_at``, written from ``time.time()``). A store-backed
        ``BudgetGovernor`` MUST therefore be built with a wall-clock ``clock``
        (``clock=time.time``); its default ``time.monotonic`` shares no origin
        with this value, so the wall-clock dimension would silently never fire.

        NOT DERIVABLE vs DERIVED -- these are two different things and must not be
        conflated:

        * ``cost_units_used`` IS derived. The persisted ``directives.action``
          column is the source of truth and it is costed through
          ``jcc_core.policy.COST_UNITS_PER_ACTION`` (D-34/D-5). A hard-coded 0.0
          made the ``cost_units`` dimension permanently FAIL-OPEN -- it could
          never exhaust, and a restart could not reset it because nothing was
          ever counted. An action string with no cost entry cannot be costed, so
          this raises rather than silently under-counting (fail-closed, mirroring
          D-34's ``budget_check_error``); in practice the column is written only
          through the validated ``Directive`` model.
        * ``model_units_used`` (the ``scorer_tokens`` dimension) is genuinely NOT
          derivable: no persisted column carries scorer token usage, so this
          stays 0.0 and that dimension remains INERT BY DESIGN (waived, and
          asserted as such by the gate) -- reported honestly rather than faked.
        """
        now = time.time()
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    "SELECT created_at FROM sessions WHERE session_id=?", (session_id,)
                ).fetchone()
                first_seen = float(row[0]) if row and row[0] is not None else now
                assessments_used = conn.execute(
                    "SELECT COUNT(*) FROM assessments WHERE session_id=?", (session_id,)
                ).fetchone()[0]
                events_used = conn.execute(
                    "SELECT COUNT(*) FROM events WHERE session_id=?", (session_id,)
                ).fetchone()[0]
                # ONE ROW PER ECONOMIC EVENT (P4 #82 / R1-R4). Previously
                # every count below was a bare COUNT(*) over ``directives``
                # rows, and TWO planes write a row per directive: the
                # engine (``core.py`` -> ``dir_<session>_<event>``) and the
                # sentinel's charging copy (``BudgetGovernor._persist_directive``
                # -> ``shadow_<session>_<event>``). Same event, two rows, so
                # directives_used / rate_used / escalations_used / retries_used
                # all read 2x and cost_units_used was billed twice -- the
                # ``directives_per_minute`` ceiling then fires at half the
                # real call rate and the audit tape over-reports what was sent.
                #
                # The two rows are collapsed by the economic-event identity
                # they carry (``directive_event_key``); the surviving row is
                # the LAST one written for that event, i.e. the sentinel's
                # charging record -- which is also the one that carries a
                # substituted action (a budget-blocked event is re-issued as
                # ESCALATE), so no escalation can be lost to the collapse.
                # Rows of an UNRECOGNISED shape are never merged: the worst
                # case is the old over-count, never an under-count.
                events = _directive_events(conn, session_id)
                directives_used = len(events)
                retries_used = sum(
                    1 for action, _ts in events.values()
                    if _action_matches(action, "RETRY")
                )
                escalations_used = sum(
                    1 for action, _ts in events.values()
                    if _action_matches(action, "ESCALATE")
                )
                rate_used = sum(
                    1 for _action, ts in events.values()
                    if ts is not None and ts >= now - self.RATE_WINDOW_S
                )
                # D-34: cost is DERIVED from persisted actions, never in-memory.
                # One charge per event, not one per duplicate row.
                cost_units_used = 0.0
                for action_value, _ts in events.values():
                    try:
                        unit_cost = COST_UNITS_PER_ACTION[DirectiveAction(action_value)]
                    except (ValueError, KeyError) as exc:  # unknown action string
                        raise ValueError(
                            f"uncostable directive action {action_value!r} for session "
                            f"{session_id!r}: cost_units_used cannot be derived, and "
                            "under-counting would make the cost dimension fail-open"
                        ) from exc
                    cost_units_used += unit_cost
            finally:
                conn.close()
        return {
            "assessments_used": float(assessments_used),
            "directives_used": float(directives_used),
            "events_used": float(events_used),
            "retries_used": float(retries_used),
            "escalations_used": float(escalations_used),
            "rate_used": float(rate_used),
            "model_units_used": 0.0,
            "cost_units_used": cost_units_used,
            "first_seen": first_seen,
        }
