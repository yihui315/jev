"""AEP v0.1 wire + domain schemas for the JCC Core control plane.

Implements ADR-0003 invariants:
  * ``session_id`` is the PRIMARY identity; ``cwd`` is metadata only.
  * ``event_id`` is unique per emitted event; ``delivery_id`` is unique per
    delivery attempt (at-least-once delivery / redelivery). Consumers dedup
    on both.
  * Unknown / incomplete evidence is NEVER auto-marked safe/completed. The
    policy maps it to VERIFY (await) by default-deny on ambiguity.

Jev (per ADR-0002 / ADR-0004) is scorer-only: ``Assessment`` carries
probabilities and a band, never a directive. The deterministic ``Policy``
produces the directive.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AgentName(str, Enum):
    WORKBUDDY = "workbuddy"
    CLAUDE = "claude"
    CODEX = "codex"
    HERMES = "hermes"
    GENERIC = "generic"


class EventType(str, Enum):
    SESSION_START = "session_start"
    TOOL_CALL = "tool_call"
    AGENT_MESSAGE = "agent_message"
    EVIDENCE = "evidence"
    ERROR = "error"
    HEARTBEAT = "heartbeat"
    UNKNOWN = "unknown"


class SessionStatus(str, Enum):
    ACTIVE = "active"
    AWAIT = "await"            # evidence missing/incomplete -> need VERIFY
    QUARANTINE = "quarantine"
    ESCALATED = "escalated"
    PAUSED = "paused"
    FINISHED = "finished"


class DirectiveAction(str, Enum):
    # Deterministic priority order (highest first): ESCALATE > PAUSE > VERIFY >
    # STEER > RETRY > FINISH > CONTINUE.  See policy.py.
    ESCALATE = "ESCALATE"
    PAUSE = "PAUSE"
    VERIFY = "VERIFY"
    STEER = "STEER"
    RETRY = "RETRY"
    FINISH = "FINISH"
    CONTINUE = "CONTINUE"


class AEPEnvelope(BaseModel):
    """AEP v0.1 wire event emitted by an external-agent adapter."""

    version: str = "aep/v0.1"
    event_id: str
    delivery_id: str
    session_id: str
    agent: AgentName
    project: str = "unknown"
    cwd: Optional[str] = None          # metadata ONLY — never primary identity
    timestamp: Optional[float] = None
    event_type: EventType = EventType.UNKNOWN
    payload: Dict[str, Any] = Field(default_factory=dict)
    evidence: Optional[Dict[str, Any]] = None   # required for safe completion
    schema_ok: bool = True             # adapter marks parse success

    def model_dump_api(self) -> Dict[str, Any]:
        return self.model_dump(mode="json")


class SessionState(BaseModel):
    session_id: str
    agent: AgentName
    project: str = "unknown"
    cwd: Optional[str] = None
    status: SessionStatus = SessionStatus.ACTIVE
    created_at: float = 0.0
    updated_at: float = 0.0
    last_event_id: Optional[str] = None
    event_count: int = 0
    evidence_count: int = 0
    current_assessment: Optional[Dict[str, Any]] = None
    current_directive: Optional[Dict[str, Any]] = None


class EvidenceSnapshot(BaseModel):
    evidence_id: str
    session_id: str
    source_event_id: str
    timestamp: float
    fingerprint: str          # sha256 of canonical evidence
    summary: str = ""
    complete: bool = False


class Assessment(BaseModel):
    """Jev output. Probabilities ONLY — never a directive (ADR-0002/0004)."""

    agent: AgentName
    session_id: str
    timestamp: float
    model: str = "mock"
    provider: str = "mock"
    probabilities: Dict[str, float] = Field(default_factory=dict)
    risk_score: float = 0.0
    band: str = "unknown"           # low | medium | high | critical | unknown
    needs_verify: bool = False
    escalate: bool = False
    pause: bool = False
    steer: bool = False
    retry: bool = False
    finished: bool = False
    raw: Dict[str, Any] = Field(default_factory=dict)


class Directive(BaseModel):
    directive_id: str
    session_id: str
    timestamp: float
    action: DirectiveAction
    target_actuator: str = "noop"
    params: Dict[str, Any] = Field(default_factory=dict)
    policy_version: str = "p1.0.0"
    signed: bool = False            # P4/P5 signing; UNSIGNED in P1
    signature: Optional[str] = None
    evidence_hash: Optional[str] = None
    acted: bool = False
    actuation_reason: Optional[str] = None


class AuditEntry(BaseModel):
    seq: int
    event_id: str
    timestamp: float
    session_id: str
    agent: str
    project: str
    assessment: Optional[str] = None
    directive: Optional[str] = None
    evidence_hash: Optional[str] = None
    prev_hash: str
    entry_hash: str


# Convenience ordered list of actions (used by tests + docs).
POLICY_PRIORITY: List[DirectiveAction] = [
    DirectiveAction.ESCALATE,
    DirectiveAction.PAUSE,
    DirectiveAction.VERIFY,
    DirectiveAction.STEER,
    DirectiveAction.RETRY,
    DirectiveAction.FINISH,
    DirectiveAction.CONTINUE,
]
