"""JCC v1.3 Product Edition -- Project/Mission domain (Wave 1).

This module adds the smallest domain that turns JCC from a *session viewer* into
an *AI work operating system*: a user creates a **Project**, launches a
**Mission** against an **Agent** with a **Connection**, the run emits a
**Session** (already modelled by ``schemas.SessionState``), and the work produces
**Artifacts**. ``Capability`` and ``Approval`` are the two gates the control
plane needs before any effectful action.

DESIGN BOUNDARIES (why this is a separate module and not a rewrite)
-------------------------------------------------------------------
* **Additive only.** ``schemas.py`` / ``state_store.py`` are unchanged; the
  existing ``sessions`` / ``events`` / ``assessments`` / ``directives`` /
  ``audit_tape`` tables keep their exact shapes. A pre-v1.3 database opens
  unmodified; Wave 1's tables are created beside them (idempotent ``CREATE
  TABLE IF NOT EXISTS``), so a v1.2 database is forward-compatible.
* **Mission state is authoritative HERE.** The frontend is a projection; it may
  never invent a state transition. Every transition goes through
  :func:`transition_mission`, which validates against :data:`MISSION_TRANSITIONS`.
* **No effectful control.** This module records intent (a mission, a connection,
  an approval) and status. It never launches a process, never signs, never
  actuates. Arming and acting stay in the policy/actuator path.
* **UNKNOWN is never green.** ``MissionStatus`` and ``ConnectionHealth`` share
  the honest-status discipline of ``connections.ConnectionStatus``: an unknown
  value is a value, not a default to "ok".
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Tuple

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class MissionStatus(str, Enum):
    """The ten states a Mission may occupy (v1.3 order, Wave 1).

    ORDER IS NOT PRIORITY -- it is the lifecycle. Transitions are constrained by
    :data:`MISSION_TRANSITIONS`; anything not listed there is refused.
    """

    DRAFT = "DRAFT"
    READY = "READY"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"
    PAUSED = "PAUSED"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


#: Terminal states: no outgoing transition except an explicit re-open is allowed.
MISSION_TERMINAL: Tuple[MissionStatus, ...] = (
    MissionStatus.COMPLETED,
    MissionStatus.FAILED,
    MissionStatus.CANCELLED,
)

#: Legal transitions. Read as "from -> {allowed destinations}".
#: Deliberately permissive where the operator must be able to intervene
#: (RUNNING <-> PAUSED, any live state -> FAILED/CANCELLED) and deliberately
#: strict about not skipping the launch handshake
#: (DRAFT -> RUNNING is NOT allowed; a mission must pass through READY and
#: STARTING, so "launched" can never be claimed without the launch step).
MISSION_TRANSITIONS: Dict[MissionStatus, Tuple[MissionStatus, ...]] = {
    MissionStatus.DRAFT: (MissionStatus.READY, MissionStatus.CANCELLED),
    MissionStatus.READY: (MissionStatus.STARTING, MissionStatus.DRAFT,
                          MissionStatus.CANCELLED),
    MissionStatus.STARTING: (MissionStatus.RUNNING, MissionStatus.FAILED,
                             MissionStatus.CANCELLED),
    MissionStatus.RUNNING: (MissionStatus.NEEDS_ATTENTION, MissionStatus.PAUSED,
                            MissionStatus.VERIFYING, MissionStatus.COMPLETED,
                            MissionStatus.FAILED, MissionStatus.CANCELLED),
    MissionStatus.NEEDS_ATTENTION: (MissionStatus.RUNNING, MissionStatus.PAUSED,
                                    MissionStatus.VERIFYING,
                                    MissionStatus.COMPLETED,
                                    MissionStatus.FAILED,
                                    MissionStatus.CANCELLED),
    MissionStatus.PAUSED: (MissionStatus.RUNNING, MissionStatus.CANCELLED,
                           MissionStatus.FAILED),
    MissionStatus.VERIFYING: (MissionStatus.COMPLETED, MissionStatus.FAILED,
                              MissionStatus.RUNNING, MissionStatus.CANCELLED),
    # terminal states: CANCELLED is reachable from anywhere live; a terminal
    # state may be RE-OPENED to DRAFT only (an explicit operator action, never
    # an automatic retry) so a completed mission can be cloned into a new draft.
    MissionStatus.COMPLETED: (MissionStatus.DRAFT,),
    MissionStatus.FAILED: (MissionStatus.DRAFT,),
    MissionStatus.CANCELLED: (MissionStatus.DRAFT,),
}


class AttentionKind(str, Enum):
    """Closed vocabulary for why a Mission is NEEDS_ATTENTION (Wave 4 will render
    these; Wave 1 only needs the taxonomy to be honest about the reason)."""

    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    AGENT_STUCK = "AGENT_STUCK"
    POLICY_ESCALATION = "POLICY_ESCALATION"
    VERIFICATION_REQUIRED = "VERIFICATION_REQUIRED"
    BUDGET_WARNING = "BUDGET_WARNING"
    CONNECTION_LOST = "CONNECTION_LOST"
    DELIVERABLE_READY = "DELIVERABLE_READY"


class CapabilityKind(str, Enum):
    """What an agent can actually do, as MEASURED -- never as assumed.

    ``CONTROL_VERIFIED`` is the only value that permits effectful action, and it
    may only be set by a probe that exercised the control path. The remaining
    values are honest distinctions between "we can see it", "we can talk to it",
    and "we can act through it".
    """

    UNKNOWN = "UNKNOWN"
    TELEMETRY_ONLY = "TELEMETRY_ONLY"
    ADAPTER_CONTRACT_LIVE = "ADAPTER_CONTRACT_LIVE"
    CONTROL_VERIFIED = "CONTROL_VERIFIED"
    UNAVAILABLE = "UNAVAILABLE"


#: The connection ``health`` a MEASURED capability implies (Wave 7). Kept here so
#: the domain does not import the probe module (which imports this one). Note that
#: ADAPTER_CONTRACT_LIVE -> CONNECTED is a claim about SEEING the agent, a strictly
#: weaker claim than CONTROL_VERIFIED; the two axes are reported separately and
#: must never be merged into one green light.
CAPABILITY_HEALTH: Dict[CapabilityKind, str] = {
    CapabilityKind.CONTROL_VERIFIED: "CONNECTED",
    CapabilityKind.ADAPTER_CONTRACT_LIVE: "CONNECTED",
    CapabilityKind.TELEMETRY_ONLY: "DEGRADED",
    CapabilityKind.UNAVAILABLE: "UNAVAILABLE",
    CapabilityKind.UNKNOWN: "UNKNOWN",
}


def health_for_capability(kind: CapabilityKind) -> str:
    return CAPABILITY_HEALTH.get(CapabilityKind(kind), "UNKNOWN")


class ArtifactKind(str, Enum):
    FILE = "file"
    DOCUMENT = "document"
    CODE = "code"
    PATCH = "patch"
    REPORT = "report"
    SCREENSHOT = "screenshot"
    TEST_RESULT = "test_result"
    BUILD = "build"
    DATASET = "dataset"
    OTHER = "other"


class ArtifactStatus(str, Enum):
    PRODUCED = "PRODUCED"        # exists
    VERIFIED = "VERIFIED"        # independently checked
    REJECTED = "REJECTED"        # checked and refused
    SUPERSEDED = "SUPERSEDED"    # replaced by a later artifact


class ApprovalState(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


def _now() -> float:
    return time.time()


def _uid(prefix: str) -> str:
    return "%s_%s" % (prefix, uuid.uuid4().hex[:16])


class Project(BaseModel):
    project_id: str
    name: str
    root_path: str = ""
    description: str = ""
    created_at: float = Field(default_factory=_now)
    updated_at: float = Field(default_factory=_now)
    archived: bool = False


class Mission(BaseModel):
    mission_id: str
    project_id: str
    title: str
    goal: str = ""
    agent: str = "workbuddy"
    connection_ref: Optional[str] = None
    working_directory: str = ""
    context: str = ""
    permissions: List[str] = Field(default_factory=list)
    budget_units: float = 0.0
    status: MissionStatus = MissionStatus.DRAFT
    attention_kind: Optional[AttentionKind] = None
    attention_reason: Optional[str] = None
    created_at: float = Field(default_factory=_now)
    updated_at: float = Field(default_factory=_now)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    session_id: Optional[str] = None      # the AgentRun's Session, once launched


class AgentRun(BaseModel):
    """One launch of a Mission against one agent connection.

    A Mission may be launched more than once (a retry is a new AgentRun), so the
    run -- not the mission -- is the unit that maps to a Session and to AEP
    telemetry.
    """

    run_id: str
    mission_id: str
    agent: str
    connection_ref: Optional[str] = None
    session_id: Optional[str] = None
    status: MissionStatus = MissionStatus.DRAFT
    launched_at: Optional[float] = None
    ended_at: Optional[float] = None
    exit_reason: Optional[str] = None
    created_at: float = Field(default_factory=_now)


class Connection(BaseModel):
    """A named, persisted link to an agent. Secrets are NEVER stored here."""

    connection_id: str
    agent: str
    display_name: str = ""
    connection_type: str = "LOCAL_CLI"
    endpoint: str = ""
    secret_env_var: Optional[str] = None   # the NAME, never the value
    health: str = "UNKNOWN"
    last_probe_at: Optional[float] = None
    last_event_at: Optional[float] = None
    last_session_id: Optional[str] = None
    detail: Dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=_now)
    updated_at: float = Field(default_factory=_now)


class Capability(BaseModel):
    """Measured capability of a connection for one axis."""

    capability_id: str
    connection_id: str
    kind: CapabilityKind = CapabilityKind.UNKNOWN
    detail: str = ""
    evidence_ref: Optional[str] = None
    measured_at: Optional[float] = None
    created_at: float = Field(default_factory=_now)


class Approval(BaseModel):
    approval_id: str
    mission_id: str
    kind: str
    reason: str = ""
    state: ApprovalState = ApprovalState.PENDING
    requested_by: str = "system"
    decided_by: Optional[str] = None
    decided_at: Optional[float] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=_now)


class Artifact(BaseModel):
    artifact_id: str
    mission_id: str
    run_id: Optional[str] = None
    session_id: Optional[str] = None
    kind: ArtifactKind = ArtifactKind.OTHER
    name: str
    uri: str = ""
    sha256: Optional[str] = None
    bytes: int = 0
    status: ArtifactStatus = ArtifactStatus.PRODUCED
    provenance: Dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# Transition validation
# ---------------------------------------------------------------------------


def can_transition(src: MissionStatus, dst: MissionStatus) -> bool:
    return dst in MISSION_TRANSITIONS.get(src, ())


def validate_transition(src: MissionStatus, dst: MissionStatus) -> None:
    """Raise ``ValueError`` on an illegal transition -- never coerce, never warn."""
    if src == dst:
        return
    if not can_transition(src, dst):
        raise ValueError(
            "illegal mission transition %s -> %s; allowed from %s: %s"
            % (src.value, dst.value, src.value,
               [s.value for s in MISSION_TRANSITIONS.get(src, ())])
        )


def is_terminal(s: MissionStatus) -> bool:
    return s in MISSION_TERMINAL


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

DOMAIN_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
  project_id  TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  root_path   TEXT,
  description TEXT,
  created_at  REAL,
  updated_at  REAL,
  archived    INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS missions (
  mission_id        TEXT PRIMARY KEY,
  project_id        TEXT NOT NULL,
  title             TEXT NOT NULL,
  goal              TEXT,
  agent             TEXT,
  connection_ref    TEXT,
  working_directory TEXT,
  context           TEXT,
  permissions       TEXT,
  budget_units      REAL DEFAULT 0,
  status            TEXT,
  attention_kind    TEXT,
  attention_reason  TEXT,
  created_at        REAL,
  updated_at        REAL,
  started_at        REAL,
  finished_at       REAL,
  session_id        TEXT
);
CREATE INDEX IF NOT EXISTS idx_missions_project ON missions(project_id);
CREATE INDEX IF NOT EXISTS idx_missions_status ON missions(status);
CREATE TABLE IF NOT EXISTS agent_runs (
  run_id         TEXT PRIMARY KEY,
  mission_id     TEXT NOT NULL,
  agent          TEXT,
  connection_ref TEXT,
  session_id     TEXT,
  status         TEXT,
  launched_at    REAL,
  ended_at       REAL,
  exit_reason    TEXT,
  created_at     REAL
);
CREATE INDEX IF NOT EXISTS idx_runs_mission ON agent_runs(mission_id);
CREATE TABLE IF NOT EXISTS domain_connections (
  connection_id  TEXT PRIMARY KEY,
  agent          TEXT NOT NULL,
  display_name   TEXT,
  connection_type TEXT,
  endpoint       TEXT,
  secret_env_var TEXT,
  health         TEXT,
  last_probe_at  REAL,
  last_event_at  REAL,
  last_session_id TEXT,
  detail         TEXT,
  created_at     REAL,
  updated_at     REAL
);
CREATE TABLE IF NOT EXISTS capabilities (
  capability_id  TEXT PRIMARY KEY,
  connection_id  TEXT NOT NULL,
  kind           TEXT,
  detail         TEXT,
  evidence_ref   TEXT,
  measured_at    REAL,
  created_at     REAL,
  UNIQUE(connection_id, kind)
);
CREATE TABLE IF NOT EXISTS approvals (
  approval_id  TEXT PRIMARY KEY,
  mission_id   TEXT NOT NULL,
  kind         TEXT,
  reason       TEXT,
  state        TEXT,
  requested_by TEXT,
  decided_by   TEXT,
  decided_at   REAL,
  payload      TEXT,
  created_at   REAL
);
CREATE INDEX IF NOT EXISTS idx_approvals_mission ON approvals(mission_id);
CREATE INDEX IF NOT EXISTS idx_approvals_state ON approvals(state);
CREATE TABLE IF NOT EXISTS artifacts (
  artifact_id TEXT PRIMARY KEY,
  mission_id  TEXT NOT NULL,
  run_id      TEXT,
  session_id  TEXT,
  kind        TEXT,
  name        TEXT,
  uri         TEXT,
  sha256      TEXT,
  bytes       INTEGER DEFAULT 0,
  status      TEXT,
  provenance  TEXT,
  created_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_artifacts_mission ON artifacts(mission_id);
CREATE TABLE IF NOT EXISTS mission_events (
  seq         INTEGER PRIMARY KEY AUTOINCREMENT,
  mission_id  TEXT NOT NULL,
  timestamp   REAL,
  from_status TEXT,
  to_status   TEXT,
  reason      TEXT
);
CREATE INDEX IF NOT EXISTS idx_mission_events ON mission_events(mission_id, seq);
"""


class DomainStore:
    """Project/Mission domain persistence, sharing the control-plane database.

    Opens the SAME SQLite file as :class:`~jcc_core.state_store.StateStore`, so a
    Mission and its Session live in one database and one backup. Uses the same
    WAL + lock discipline. Pass ``db_path`` explicitly; never hard-coded here.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.executescript(DOMAIN_SCHEMA)
                conn.commit()
            finally:
                conn.close()

    # -- projects -----------------------------------------------------------

    def create_project(self, name: str, *, root_path: str = "",
                       description: str = "") -> Project:
        p = Project(project_id=_uid("prj"), name=name, root_path=root_path,
                    description=description)
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT INTO projects (project_id, name, root_path, description, "
                    "created_at, updated_at, archived) VALUES (?,?,?,?,?,?,0)",
                    (p.project_id, p.name, p.root_path, p.description,
                     p.created_at, p.updated_at),
                )
                conn.commit()
            finally:
                conn.close()
        return p

    def get_project(self, project_id: str) -> Optional[Project]:
        with self._lock:
            conn = self._conn()
            try:
                r = conn.execute(
                    "SELECT project_id, name, root_path, description, created_at, "
                    "updated_at, archived FROM projects WHERE project_id=?",
                    (project_id,),
                ).fetchone()
            finally:
                conn.close()
        return self._row_project(r) if r else None

    def list_projects(self, *, include_archived: bool = False) -> List[Project]:
        with self._lock:
            conn = self._conn()
            try:
                q = ("SELECT project_id, name, root_path, description, created_at, "
                     "updated_at, archived FROM projects")
                if not include_archived:
                    q += " WHERE archived=0"
                q += " ORDER BY updated_at DESC"
                rows = conn.execute(q).fetchall()
            finally:
                conn.close()
        return [self._row_project(r) for r in rows]

    @staticmethod
    def _row_project(r) -> Project:
        return Project(project_id=r[0], name=r[1], root_path=r[2] or "",
                       description=r[3] or "", created_at=r[4], updated_at=r[5],
                       archived=bool(r[6]))

    # -- missions -----------------------------------------------------------

    def create_mission(self, project_id: str, title: str, *, goal: str = "",
                       agent: str = "workbuddy", working_directory: str = "",
                       context: str = "", permissions: Optional[Iterable[str]] = None,
                       budget_units: float = 0.0,
                       connection_ref: Optional[str] = None) -> Mission:
        if self.get_project(project_id) is None:
            raise ValueError("cannot create a mission for unknown project %r"
                             % project_id)
        m = Mission(mission_id=_uid("msn"), project_id=project_id, title=title,
                    goal=goal, agent=agent, working_directory=working_directory,
                    context=context, permissions=sorted(set(permissions or [])),
                    budget_units=budget_units, connection_ref=connection_ref)
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT INTO missions (mission_id, project_id, title, goal, agent, "
                    "connection_ref, working_directory, context, permissions, "
                    "budget_units, status, attention_kind, attention_reason, "
                    "created_at, updated_at, started_at, finished_at, session_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (m.mission_id, m.project_id, m.title, m.goal, m.agent,
                     m.connection_ref, m.working_directory, m.context,
                     json.dumps(m.permissions), m.budget_units, m.status.value,
                     None, None, m.created_at, m.updated_at, None, None, None),
                )
                conn.execute(
                    "INSERT INTO mission_events (mission_id, timestamp, from_status, "
                    "to_status, reason) VALUES (?,?,?,?,?)",
                    (m.mission_id, m.created_at, None, m.status.value, "created"),
                )
                conn.commit()
            finally:
                conn.close()
        return m

    def get_mission(self, mission_id: str) -> Optional[Mission]:
        with self._lock:
            conn = self._conn()
            try:
                r = conn.execute("SELECT * FROM missions WHERE mission_id=?",
                                 (mission_id,)).fetchone()
                cols = [c[0] for c in conn.execute("SELECT * FROM missions LIMIT 0").description]
            finally:
                conn.close()
        return self._row_mission(dict(zip(cols, r))) if r else None

    def list_missions(self, *, project_id: Optional[str] = None,
                      status: Optional[str] = None,
                      limit: int = 200) -> List[Mission]:
        q = "SELECT * FROM missions"
        clauses, params = [], []
        if project_id:
            clauses.append("project_id=?")
            params.append(project_id)
        if status:
            clauses.append("status=?")
            params.append(status)
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        q += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute(q, params)
                cols = [c[0] for c in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            finally:
                conn.close()
        return [self._row_mission(d) for d in rows]

    @staticmethod
    def _row_mission(d: Dict[str, Any]) -> Mission:
        return Mission(
            mission_id=d["mission_id"], project_id=d["project_id"],
            title=d["title"], goal=d.get("goal") or "",
            agent=d.get("agent") or "workbuddy",
            connection_ref=d.get("connection_ref"),
            working_directory=d.get("working_directory") or "",
            context=d.get("context") or "",
            permissions=json.loads(d.get("permissions") or "[]"),
            budget_units=float(d.get("budget_units") or 0.0),
            status=d.get("status") or MissionStatus.DRAFT.value,
            attention_kind=d.get("attention_kind"),
            attention_reason=d.get("attention_reason"),
            created_at=float(d.get("created_at") or 0.0),
            updated_at=float(d.get("updated_at") or 0.0),
            started_at=d.get("started_at"),
            finished_at=d.get("finished_at"),
            session_id=d.get("session_id"),
        )

    def transition_mission(self, mission_id: str, to: MissionStatus, *,
                           reason: str = "",
                           attention_kind: Optional[AttentionKind] = None,
                           attention_reason: Optional[str] = None) -> Mission:
        """Move a mission to ``to``, validating the transition. Fail-closed.

        Refuses unknown missions and illegal transitions with ``ValueError``; the
        row is left exactly as it was. Records the transition in
        ``mission_events`` so the timeline is derivable from the store (the
        frontend never has to remember a transition it did not see).
        """
        to = MissionStatus(to)
        with self._lock:
            conn = self._conn()
            try:
                r = conn.execute("SELECT status FROM missions WHERE mission_id=?",
                                 (mission_id,)).fetchone()
                if r is None:
                    raise ValueError("unknown mission %r" % mission_id)
                src = MissionStatus(r[0])
                validate_transition(src, to)
                now = _now()
                sets = ["status=?", "updated_at=?", "attention_kind=?",
                        "attention_reason=?"]
                vals: List[Any] = [to.value, now,
                                   attention_kind.value if attention_kind else None,
                                   attention_reason]
                if to == MissionStatus.STARTING and src != MissionStatus.STARTING:
                    sets.append("started_at=?")
                    vals.append(now)
                if is_terminal(to):
                    sets.append("finished_at=?")
                    vals.append(now)
                vals.append(mission_id)
                conn.execute("UPDATE missions SET %s WHERE mission_id=?" % ", ".join(sets),
                             vals)
                conn.execute(
                    "INSERT INTO mission_events (mission_id, timestamp, from_status, "
                    "to_status, reason) VALUES (?,?,?,?,?)",
                    (mission_id, now, src.value, to.value, reason or ""),
                )
                conn.commit()
            finally:
                conn.close()
        return self.get_mission(mission_id)  # type: ignore[return-value]

    def mission_timeline(self, mission_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT seq, timestamp, from_status, to_status, reason "
                    "FROM mission_events WHERE mission_id=? ORDER BY seq ASC",
                    (mission_id,),
                ).fetchall()
            finally:
                conn.close()
        return [{"seq": r[0], "timestamp": r[1], "from_status": r[2],
                 "to_status": r[3], "reason": r[4]} for r in rows]

    def attach_session(self, mission_id: str, session_id: str) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute("UPDATE missions SET session_id=?, updated_at=? "
                             "WHERE mission_id=?", (session_id, _now(), mission_id))
                conn.commit()
            finally:
                conn.close()

    # -- agent runs ---------------------------------------------------------

    def create_run(self, mission_id: str, agent: str, *,
                   connection_ref: Optional[str] = None) -> AgentRun:
        if self.get_mission(mission_id) is None:
            raise ValueError("cannot create a run for unknown mission %r" % mission_id)
        r = AgentRun(run_id=_uid("run"), mission_id=mission_id, agent=agent,
                     connection_ref=connection_ref)
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT INTO agent_runs (run_id, mission_id, agent, connection_ref, "
                    "session_id, status, launched_at, ended_at, exit_reason, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (r.run_id, r.mission_id, r.agent, r.connection_ref, None,
                     r.status.value, None, None, None, r.created_at),
                )
                conn.commit()
            finally:
                conn.close()
        return r

    def list_runs(self, mission_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT run_id, mission_id, agent, connection_ref, session_id, "
                    "status, launched_at, ended_at, exit_reason, created_at "
                    "FROM agent_runs WHERE mission_id=? ORDER BY created_at ASC",
                    (mission_id,),
                ).fetchall()
            finally:
                conn.close()
        keys = ["run_id", "mission_id", "agent", "connection_ref", "session_id",
                "status", "launched_at", "ended_at", "exit_reason", "created_at"]
        return [dict(zip(keys, r)) for r in rows]

    def attach_run_session(self, run_id: str, session_id: str) -> None:
        """Bind an AgentRun to the Session its telemetry arrives under.

        Wave 3. The run -- not the mission -- owns the Session, so this is the
        column the mission runtime must fill the moment it knows the adapter's
        session id. Kept here rather than attached from the runtime module: an
        import-side-effect helper is invisible to any caller that imports only
        ``domain``, which is exactly how a run ended up with a null session while
        the mission carried the id (a Wave 3 gate finding).
        """
        with self._lock:
            conn = self._conn()
            try:
                conn.execute("UPDATE agent_runs SET session_id=? WHERE run_id=?",
                             (session_id, run_id))
                conn.commit()
            finally:
                conn.close()

    def mark_run_launched(self, run_id: str,
                          status: MissionStatus = MissionStatus.RUNNING) -> None:
        """Record that a run's telemetry actually reached the engine."""
        now = _now()
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "UPDATE agent_runs SET status=?, launched_at=? WHERE run_id=?",
                    (MissionStatus(status).value, now, run_id))
                conn.commit()
            finally:
                conn.close()

    def end_run(self, run_id: str, *, reason: str = "",
                status: MissionStatus = MissionStatus.FAILED) -> None:
        """Close a run that did not reach (or no longer deserves) RUNNING."""
        now = _now()
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "UPDATE agent_runs SET status=?, ended_at=?, exit_reason=? "
                    "WHERE run_id=?",
                    (MissionStatus(status).value, now, reason, run_id))
                conn.commit()
            finally:
                conn.close()

    # -- connections (domain-side; secrets never persisted) ----------------

    def upsert_connection(self, agent: str, *, display_name: str = "",
                          connection_type: str = "LOCAL_CLI", endpoint: str = "",
                          secret_env_var: Optional[str] = None,
                          connection_id: Optional[str] = None) -> Connection:
        if secret_env_var and secret_env_var.strip().lower().startswith("secret_"):
            raise ValueError("secret_env_var must be the ENV VAR NAME (e.g. "
                             "JCC_CLAUDE_TOKEN), never a secret_-prefixed value")
        with self._lock:
            conn = self._conn()
            try:
                existing = None
                if connection_id:
                    existing = conn.execute(
                        "SELECT connection_id FROM domain_connections WHERE connection_id=?",
                        (connection_id,)).fetchone()
                if existing is None:
                    existing = conn.execute(
                        "SELECT connection_id FROM domain_connections WHERE agent=?",
                        (agent,)).fetchone()
                now = _now()
                if existing:
                    cid = existing[0]
                    conn.execute(
                        "UPDATE domain_connections SET display_name=?, connection_type=?, "
                        "endpoint=?, secret_env_var=?, updated_at=? WHERE connection_id=?",
                        (display_name, connection_type, endpoint, secret_env_var, now, cid))
                else:
                    cid = connection_id or _uid("con")
                    conn.execute(
                        "INSERT INTO domain_connections (connection_id, agent, "
                        "display_name, connection_type, endpoint, secret_env_var, "
                        "health, last_probe_at, last_event_at, last_session_id, detail, "
                        "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (cid, agent, display_name, connection_type, endpoint,
                         secret_env_var, "UNKNOWN", None, None, None,
                         json.dumps({}), now, now))
                conn.commit()
            finally:
                conn.close()
        return self.get_connection(cid)  # type: ignore[return-value]

    def set_connection_health(self, connection_id: str, health: str, *,
                              last_session_id: Optional[str] = None,
                              detail: Optional[Dict[str, Any]] = None) -> None:
        with self._lock:
            conn = self._conn()
            try:
                sets = ["health=?", "last_probe_at=?", "updated_at=?"]
                vals: List[Any] = [health, _now(), _now()]
                if last_session_id is not None:
                    sets.append("last_session_id=?")
                    vals.append(last_session_id)
                    sets.append("last_event_at=?")
                    vals.append(_now())
                if detail is not None:
                    sets.append("detail=?")
                    vals.append(json.dumps(detail, sort_keys=True))
                vals.append(connection_id)
                conn.execute("UPDATE domain_connections SET %s WHERE connection_id=?"
                             % ", ".join(sets), vals)
                conn.commit()
            finally:
                conn.close()

    def get_connection(self, connection_id: str) -> Optional[Connection]:
        with self._lock:
            conn = self._conn()
            try:
                r = conn.execute(
                    "SELECT connection_id, agent, display_name, connection_type, "
                    "endpoint, secret_env_var, health, last_probe_at, last_event_at, "
                    "last_session_id, detail, created_at, updated_at "
                    "FROM domain_connections WHERE connection_id=?", (connection_id,),
                ).fetchone()
            finally:
                conn.close()
        return self._row_connection(r) if r else None

    def list_connections(self) -> List[Connection]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT connection_id, agent, display_name, connection_type, "
                    "endpoint, secret_env_var, health, last_probe_at, last_event_at, "
                    "last_session_id, detail, created_at, updated_at "
                    "FROM domain_connections ORDER BY agent ASC").fetchall()
            finally:
                conn.close()
        return [self._row_connection(r) for r in rows]

    @staticmethod
    def _row_connection(r) -> Connection:
        return Connection(
            connection_id=r[0], agent=r[1], display_name=r[2] or "",
            connection_type=r[3] or "LOCAL_CLI", endpoint=r[4] or "",
            secret_env_var=r[5], health=r[6] or "UNKNOWN",
            last_probe_at=r[7], last_event_at=r[8], last_session_id=r[9],
            detail=json.loads(r[10]) if r[10] else {},
            created_at=r[11] or 0.0, updated_at=r[12] or 0.0,
        )

    # -- capabilities -------------------------------------------------------

    def set_capability(self, connection_id: str, kind: CapabilityKind, *,
                       detail: str = "", evidence_ref: Optional[str] = None,
                       measured_at: Optional[float] = None) -> Capability:
        kind = CapabilityKind(kind)
        with self._lock:
            conn = self._conn()
            try:
                cap_id = _uid("cap")
                conn.execute(
                    "INSERT INTO capabilities (capability_id, connection_id, kind, "
                    "detail, evidence_ref, measured_at, created_at) VALUES (?,?,?,?,?,?,?) "
                    "ON CONFLICT(connection_id, kind) DO UPDATE SET detail=excluded.detail, "
                    "evidence_ref=excluded.evidence_ref, measured_at=excluded.measured_at",
                    (cap_id, connection_id, kind.value, detail, evidence_ref,
                     measured_at if measured_at is not None else _now(), _now()),
                )
                conn.commit()
            finally:
                conn.close()
        return self.get_capability(connection_id, kind)  # type: ignore[return-value]

    def get_capability(self, connection_id: str,
                       kind: CapabilityKind) -> Optional[Capability]:
        with self._lock:
            conn = self._conn()
            try:
                r = conn.execute(
                    "SELECT capability_id, connection_id, kind, detail, evidence_ref, "
                    "measured_at, created_at FROM capabilities "
                    "WHERE connection_id=? AND kind=?",
                    (connection_id, CapabilityKind(kind).value),
                ).fetchone()
            finally:
                conn.close()
        if not r:
            return None
        return Capability(capability_id=r[0], connection_id=r[1],
                          kind=r[2], detail=r[3] or "", evidence_ref=r[4],
                          measured_at=r[5], created_at=r[6] or 0.0)

    def list_capabilities(self, connection_id: Optional[str] = None) -> List[Capability]:
        with self._lock:
            conn = self._conn()
            try:
                if connection_id:
                    rows = conn.execute(
                        "SELECT capability_id, connection_id, kind, detail, evidence_ref, "
                        "measured_at, created_at FROM capabilities WHERE connection_id=?",
                        (connection_id,)).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT capability_id, connection_id, kind, detail, evidence_ref, "
                        "measured_at, created_at FROM capabilities").fetchall()
            finally:
                conn.close()
        return [Capability(capability_id=r[0], connection_id=r[1], kind=r[2],
                           detail=r[3] or "", evidence_ref=r[4], measured_at=r[5],
                           created_at=r[6] or 0.0) for r in rows]

    def is_control_verified(self, connection_id: str) -> bool:
        """The single gate for effectful action: ``CONTROL_VERIFIED`` and nothing else.

        TELEMETRY_ONLY / ADAPTER_CONTRACT_LIVE are NOT sufficient: seeing events
        and speaking the adapter contract are not the same as having exercised a
        control path. A missing row means UNKNOWN, which is also not verified.
        """
        cap = self.get_capability(connection_id, CapabilityKind.CONTROL_VERIFIED)
        if cap is None:
            return False
        return CapabilityKind(cap.kind) == CapabilityKind.CONTROL_VERIFIED

    @staticmethod
    def capability_provenance(cap: Capability) -> str:
        """Was a capability MEASURED by a probe or ASSERTED by a human?

        A capability carrying evidence (``evidence_ref``) was observed by a probe
        and can be re-checked. One without is a bare assertion -- and the UI must
        never present the two as the same thing. This is the Wave 7 distinction:
        ``is_control_verified`` answers "is the claim present?", this answers
        "how did it get here?".
        """
        return "MEASURED" if cap.evidence_ref else "ASSERTED"

    def record_probe(self, connection_id: str, result: Any) -> Connection:
        """Record what a probe OBSERVED on this connection (Wave 7).

        The capability written here comes from ``result`` (the probe), never from
        a caller-supplied value. Also moves the connection's ``health`` to the
        honest reading for the observed capability.
        """
        if self.get_connection(connection_id) is None:
            raise ValueError("unknown connection %r" % connection_id)
        kind = CapabilityKind(result.kind)
        self.set_capability(
            connection_id, kind, detail=str(getattr(result, "detail", "") or ""),
            evidence_ref=getattr(result, "evidence_ref", None),
            measured_at=float(getattr(result, "measured_at", _now()) or _now()),
        )
        self.set_connection_health(
            connection_id, health_for_capability(kind),
            detail={"probe": getattr(result, "evidence", {}) or {},
                    "reason": getattr(result, "reason", None)},
        )
        conn = self.get_connection(connection_id)
        assert conn is not None  # we just checked it exists
        return conn

    # -- approvals ----------------------------------------------------------

    def create_approval(self, mission_id: str, kind: str, *, reason: str = "",
                        requested_by: str = "system",
                        payload: Optional[Dict[str, Any]] = None) -> Approval:
        if self.get_mission(mission_id) is None:
            raise ValueError("cannot request an approval for unknown mission %r"
                             % mission_id)
        a = Approval(approval_id=_uid("apr"), mission_id=mission_id, kind=kind,
                     reason=reason, requested_by=requested_by,
                     payload=payload or {})
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT INTO approvals (approval_id, mission_id, kind, reason, "
                    "state, requested_by, decided_by, decided_at, payload, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (a.approval_id, a.mission_id, a.kind, a.reason, a.state.value,
                     a.requested_by, None, None, json.dumps(a.payload, sort_keys=True),
                     a.created_at),
                )
                conn.commit()
            finally:
                conn.close()
        return a

    def decide_approval(self, approval_id: str, state: ApprovalState, *,
                        decided_by: str) -> Approval:
        """Record a final decision on an approval, and couple it to the mission.

        Wave 4 coupling rule (deliberately narrow; see
        ``docs/v1_3/WAVE_4_ATTENTION_CONTROL.md`` §4):

          * On an APPROVE, the mission returns from NEEDS_ATTENTION to RUNNING
            **iff no OTHER approval on that mission is still PENDING**. We do not
            guess about live sessions here: an approval is a human saying "yes,
            go", and the resume itself is a separate, preview-first action. Coupling
            the decision to the resume would make a "yes" silently start work.
          * On a REJECT the mission stays NEEDS_ATTENTION. A rejection is not a
            resolution; the operator must choose what to do next, visibly.

        The decision and the coupling happen under one lock so the "any other
        pending?" check cannot race a concurrent create_approval.
        """
        state = ApprovalState(state)
        if state == ApprovalState.PENDING:
            raise ValueError("a decision cannot be PENDING")
        with self._lock:
            conn = self._conn()
            try:
                r = conn.execute("SELECT state, mission_id FROM approvals "
                                 "WHERE approval_id=?", (approval_id,)).fetchone()
                if r is None:
                    raise ValueError("unknown approval %r" % approval_id)
                if ApprovalState(r[0]) != ApprovalState.PENDING:
                    raise ValueError("approval %r is already %s; a decision is "
                                     "final" % (approval_id, r[0]))
                mission_id = r[1]
                conn.execute("UPDATE approvals SET state=?, decided_by=?, decided_at=? "
                             "WHERE approval_id=?",
                             (state.value, decided_by, _now(), approval_id))

                if state == ApprovalState.APPROVED:
                    others = conn.execute(
                        "SELECT COUNT(*) FROM approvals WHERE mission_id=? AND "
                        "state=? AND approval_id<>?",
                        (mission_id, ApprovalState.PENDING.value, approval_id),
                    ).fetchone()[0]
                    if not others:
                        mrow = conn.execute(
                            "SELECT status, attention_kind, attention_reason "
                            "FROM missions WHERE mission_id=?",
                            (mission_id,)).fetchone()
                        # Only an approval-gated mission is released by this rule.
                        if (mrow is not None
                                and MissionStatus(mrow[0])
                                == MissionStatus.NEEDS_ATTENTION
                                and mrow[1] == AttentionKind.APPROVAL_REQUIRED.value):
                            now = _now()
                            conn.execute(
                                "UPDATE missions SET status=?, updated_at=?, "
                                "attention_kind=NULL, attention_reason=NULL "
                                "WHERE mission_id=?",
                                (MissionStatus.RUNNING.value, now, mission_id))
                            conn.execute(
                                "INSERT INTO mission_events (mission_id, timestamp, "
                                "from_status, to_status, reason) VALUES (?,?,?,?,?)",
                                (mission_id, now, MissionStatus.NEEDS_ATTENTION.value,
                                 MissionStatus.RUNNING.value,
                                 "approval %s approved; no other pending approval"
                                 % approval_id))
                conn.commit()
            finally:
                conn.close()
        return self.get_approval(approval_id)  # type: ignore[return-value]

    def get_approval(self, approval_id: str) -> Optional[Approval]:
        with self._lock:
            conn = self._conn()
            try:
                r = conn.execute(
                    "SELECT approval_id, mission_id, kind, reason, state, requested_by, "
                    "decided_by, decided_at, payload, created_at FROM approvals "
                    "WHERE approval_id=?", (approval_id,)).fetchone()
            finally:
                conn.close()
        return self._row_approval(r) if r else None

    def list_approvals(self, *, mission_id: Optional[str] = None,
                       state: Optional[str] = None) -> List[Approval]:
        q = ("SELECT approval_id, mission_id, kind, reason, state, requested_by, "
             "decided_by, decided_at, payload, created_at FROM approvals")
        clauses, params = [], []
        if mission_id:
            clauses.append("mission_id=?")
            params.append(mission_id)
        if state:
            clauses.append("state=?")
            params.append(state)
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        q += " ORDER BY created_at DESC"
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(q, params).fetchall()
            finally:
                conn.close()
        return [self._row_approval(r) for r in rows]

    @staticmethod
    def _row_approval(r) -> Approval:
        return Approval(approval_id=r[0], mission_id=r[1], kind=r[2],
                        reason=r[3] or "", state=r[4] or ApprovalState.PENDING.value,
                        requested_by=r[5] or "system", decided_by=r[6],
                        decided_at=r[7],
                        payload=json.loads(r[8]) if r[8] else {},
                        created_at=r[9] or 0.0)

    # -- artifacts ----------------------------------------------------------

    def add_artifact(self, mission_id: str, name: str, *, kind: ArtifactKind = ArtifactKind.OTHER,
                     uri: str = "", data: Optional[bytes] = None,
                     sha256: Optional[str] = None, bytes_: int = 0,
                     run_id: Optional[str] = None,
                     session_id: Optional[str] = None,
                     provenance: Optional[Dict[str, Any]] = None) -> Artifact:
        if self.get_mission(mission_id) is None:
            raise ValueError("cannot attach an artifact to unknown mission %r"
                             % mission_id)
        digest = sha256
        size = bytes_
        if data is not None:
            digest = hashlib.sha256(data).hexdigest()
            size = len(data)
        a = Artifact(artifact_id=_uid("art"), mission_id=mission_id, run_id=run_id,
                     session_id=session_id, kind=ArtifactKind(kind), name=name,
                     uri=uri, sha256=digest, bytes=size,
                     provenance=provenance or {})
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT INTO artifacts (artifact_id, mission_id, run_id, session_id, "
                    "kind, name, uri, sha256, bytes, status, provenance, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (a.artifact_id, a.mission_id, a.run_id, a.session_id, a.kind.value,
                     a.name, a.uri, a.sha256, a.bytes, a.status.value,
                     json.dumps(a.provenance, sort_keys=True), a.created_at),
                )
                conn.commit()
            finally:
                conn.close()
        return a

    def list_artifacts(self, *, mission_id: Optional[str] = None) -> List[Artifact]:
        with self._lock:
            conn = self._conn()
            try:
                if mission_id:
                    rows = conn.execute(
                        "SELECT artifact_id, mission_id, run_id, session_id, kind, name, "
                        "uri, sha256, bytes, status, provenance, created_at FROM artifacts "
                        "WHERE mission_id=? ORDER BY created_at ASC", (mission_id,)).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT artifact_id, mission_id, run_id, session_id, kind, name, "
                        "uri, sha256, bytes, status, provenance, created_at FROM artifacts "
                        "ORDER BY created_at ASC").fetchall()
            finally:
                conn.close()
        return [self._row_artifact(r) for r in rows]

    @staticmethod
    def _row_artifact(r) -> Artifact:
        return Artifact(artifact_id=r[0], mission_id=r[1], run_id=r[2],
                        session_id=r[3], kind=r[4] or ArtifactKind.OTHER.value,
                        name=r[5], uri=r[6] or "", sha256=r[7], bytes=r[8] or 0,
                        status=r[9] or ArtifactStatus.PRODUCED.value,
                        provenance=json.loads(r[10]) if r[10] else {},
                        created_at=r[11] or 0.0)

    # -- deliverables (Wave 5) ---------------------------------------------
    #
    # A "deliverable" is an artifact a human can accept. PRODUCED means the agent
    # emitted it (a fact about the machine); VERIFIED means a human looked at it
    # and said yes (a fact about judgement). These are different claims and the
    # system must never render one as the other -- so acceptance is an explicit,
    # recorded, human-gated act and never inferred from "the mission completed".

    _ARTIFACT_COLS = ("artifact_id, mission_id, run_id, session_id, kind, name, "
                      "uri, sha256, bytes, status, provenance, created_at")

    def get_artifact(self, artifact_id: str) -> Optional[Artifact]:
        """One deliverable by identity. ``None`` if unknown (never a fabricated row)."""
        with self._lock:
            conn = self._conn()
            try:
                r = conn.execute(
                    "SELECT %s FROM artifacts WHERE artifact_id=?"
                    % self._ARTIFACT_COLS, (artifact_id,)).fetchone()
            finally:
                conn.close()
        return None if r is None else self._row_artifact(r)

    def verify_artifact(self, artifact_id: str, status: ArtifactStatus, *,
                        decided_by: str, note: str = "") -> Artifact:
        """Record a human verdict on a deliverable.

        Only ``VERIFIED`` / ``REJECTED`` are accepted; a verdict is final and an
        already-final artifact cannot be re-decided (same rule as Wave 4's
        approvals). The verdict is stored in the artifact's own ``provenance`` --
        it is a fact about *this* artifact, and ``provenance`` is the agreed place
        for per-artifact evidence, so no new column is justified.
        """
        status = ArtifactStatus(status)
        if status == ArtifactStatus.PRODUCED:
            raise ValueError("a verdict cannot be PRODUCED; choose VERIFIED or REJECTED")
        if status not in (ArtifactStatus.VERIFIED, ArtifactStatus.REJECTED):
            raise ValueError("only VERIFIED or REJECTED are verdicts, not %s"
                             % status.value)
        with self._lock:
            conn = self._conn()
            try:
                r = conn.execute(
                    "SELECT %s FROM artifacts WHERE artifact_id=?"
                    % self._ARTIFACT_COLS, (artifact_id,)).fetchone()
                if r is None:
                    raise ValueError("unknown artifact %r" % artifact_id)
                current = ArtifactStatus(r[9] or ArtifactStatus.PRODUCED.value)
                if current in (ArtifactStatus.VERIFIED, ArtifactStatus.REJECTED,
                               ArtifactStatus.SUPERSEDED):
                    raise ValueError("artifact %r is already %s; a verdict is final"
                                     % (artifact_id, current.value))
                prov = json.loads(r[10]) if r[10] else {}
                prov["verified_by"] = decided_by
                prov["verified_at"] = _now()
                prov["verify_note"] = note
                conn.execute(
                    "UPDATE artifacts SET status=?, provenance=? WHERE artifact_id=?",
                    (status.value, json.dumps(prov, sort_keys=True), artifact_id))
                conn.commit()
                row = conn.execute(
                    "SELECT %s FROM artifacts WHERE artifact_id=?"
                    % self._ARTIFACT_COLS, (artifact_id,)).fetchone()
            finally:
                conn.close()
        return self._row_artifact(row)

    def list_deliverables(self, *, status: Optional[ArtifactStatus] = None,
                          mission_id: Optional[str] = None,
                          limit: int = 200) -> List[Dict[str, Any]]:
        """Server-side deliverable view: artifacts joined to their mission title.

        This replaces the client-side fan-out (which capped at 50 missions and
        silently dropped the rest). Each row carries the mission title so the UI
        does not have to re-fetch per mission.
        """
        status = ArtifactStatus(status) if status is not None else None
        clauses: List[str] = []
        params: List[Any] = []
        if status is not None:
            clauses.append("a.status=?")
            params.append(status.value)
        if mission_id:
            clauses.append("a.mission_id=?")
            params.append(mission_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(max(1, min(int(limit), 1000)))
        sql = (
            "SELECT a.artifact_id, a.mission_id, a.run_id, a.session_id, a.kind, "
            "a.name, a.uri, a.sha256, a.bytes, a.status, a.provenance, a.created_at, "
            "m.title FROM artifacts a LEFT JOIN missions m ON a.mission_id=m.mission_id"
            + where + " ORDER BY a.created_at DESC LIMIT ?")
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(sql, params).fetchall()
            finally:
                conn.close()
        out: List[Dict[str, Any]] = []
        for r in rows:
            a = self._row_artifact(r)
            out.append({**a.model_dump(mode="json"),
                        "mission_title": r[12] or ""})
        return out

    def deliverable_counts(self) -> Dict[str, int]:
        """produced / verified / rejected / total.

        ``produced`` counts everything that was emitted but not yet accepted
        (PRODUCED *and* REJECTED -- a rejected artifact is still "produced but not
        accepted"), and ``verified`` is the number the product headline uses,
        because it is the one the system must not inflate.
        """
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT status, COUNT(*) FROM artifacts GROUP BY status"
                ).fetchall()
            finally:
                conn.close()
        by_status = {str(r[0] or ArtifactStatus.PRODUCED.value): int(r[1])
                     for r in rows}
        verified = by_status.get(ArtifactStatus.VERIFIED.value, 0)
        rejected = by_status.get(ArtifactStatus.REJECTED.value, 0)
        produced_only = by_status.get(ArtifactStatus.PRODUCED.value, 0)
        superseded = by_status.get(ArtifactStatus.SUPERSEDED.value, 0)
        total = sum(by_status.values())
        return {
            "produced": produced_only + rejected + superseded,
            "verified": verified,
            "rejected": rejected,
            "total": total,
        }

    # -- aggregate views (Home / Needs Attention) --------------------------

    def home_summary(self) -> Dict[str, Any]:
        """The five Home counters + the Needs Attention list, from persisted state."""
        with self._lock:
            conn = self._conn()
            try:
                active_projects = conn.execute(
                    "SELECT COUNT(*) FROM projects WHERE archived=0").fetchone()[0]
                running = conn.execute(
                    "SELECT COUNT(*) FROM missions WHERE status IN "
                    "('STARTING','RUNNING','VERIFYING')").fetchone()[0]
                needs = conn.execute(
                    "SELECT COUNT(*) FROM missions WHERE status='NEEDS_ATTENTION'"
                ).fetchone()[0]
                completed = conn.execute(
                    "SELECT COUNT(*) FROM missions WHERE status='COMPLETED'").fetchone()[0]
                recent_deliverables = conn.execute(
                    "SELECT COUNT(*) FROM artifacts").fetchone()[0]
                attention_rows = conn.execute(
                    "SELECT mission_id, title, project_id, attention_kind, "
                    "attention_reason, updated_at FROM missions "
                    "WHERE status='NEEDS_ATTENTION' ORDER BY updated_at DESC LIMIT 20"
                ).fetchall()
            finally:
                conn.close()
        return {
            "active_projects": active_projects,
            "running_missions": running,
            "needs_attention": needs,
            "completed_missions": completed,
            "recent_deliverables": recent_deliverables,
            # Wave 5: the scalar above counts every artifact regardless of verdict,
            # so it cannot distinguish accepted from rejected. This object does,
            # and `verified` is the honest headline: PRODUCED means the agent
            # emitted it, VERIFIED means a human accepted it.
            "deliverables": self.deliverable_counts(),
            "attention_items": [
                {"mission_id": r[0], "title": r[1], "project_id": r[2],
                 "kind": r[3], "reason": r[4], "updated_at": r[5]}
                for r in attention_rows
            ],
        }

    def needs_attention(self) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT mission_id, title, project_id, attention_kind, "
                    "attention_reason, updated_at, agent, connection_ref FROM missions "
                    "WHERE status='NEEDS_ATTENTION' ORDER BY updated_at DESC"
                ).fetchall()
            finally:
                conn.close()
        return [{"mission_id": r[0], "title": r[1], "project_id": r[2],
                 "kind": r[3], "reason": r[4], "updated_at": r[5],
                 "agent": r[6], "connection_ref": r[7]} for r in rows]
