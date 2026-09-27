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

#: Internal DOMAIN-MODEL version (ADR-0008 D-37). Names the shape of the internal
#: models (``Assessment`` / ``Directive`` / ...) so a reader of a persisted row can
#: tell which internal schema wrote it. Deliberately separate from
#: ``decision.POLICY_TABLE_VERSION`` (the mapping kernel's version, which is part
#: of the signed payload).
#:
#: NOT a wire version: the wire contract is ``AEPEnvelope.version == "aep/v0.1"``
#: and is FROZEN (D-25 / D-38) -- never bump it through this constant.
SCHEMA_INTERNAL_VERSION = "p3.0.0"


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
    # -- P3 additive (ADR-0008 D-12/D-20/D-31/D-35; all defaulted) ----------
    confidence: Optional[float] = None          # D-20: Metric G-8
    confidence_kind: str = "none"               # D-20: none | heuristic | model
    provider_verified: bool = False             # D-31: explicit, replaces magic band
    blocker_kind: str = "NONE"                  # D-35: NONE | EXACT
    blocker_reasons: List[str] = Field(default_factory=list)  # D-35: sorted+deduped


class Directive(BaseModel):
    directive_id: str
    session_id: str
    timestamp: float
    action: DirectiveAction
    target_actuator: str = "noop"
    params: Dict[str, Any] = Field(default_factory=dict)
    policy_version: str = "p3.0.0"   # ADR-0008 D-37 (was p1.0.0 through P2)
    signed: bool = False            # P4/P5 signing; UNSIGNED in P1
    signature: Optional[str] = None
    evidence_hash: Optional[str] = None
    acted: bool = False
    actuation_reason: Optional[str] = None
    # -- P3 additive (ADR-0008 D-12/D-33; all defaulted). Signing columns
    #    already exist in the `directives` table (state_store.py:88-89). ----
    signing_alg: Optional[str] = None                     # e.g. "hmac-sha256"
    signature_status: str = "unsigned"                    # unsigned|signed_verified|unavailable:no_key|required_missing
    signature_verified: bool = False                      # D-33: self-verify result at issuance
    issued_at: Optional[float] = None                     # D-33: inside the signed payload
    ttl_s: Optional[float] = None                         # D-33: inside the signed payload
    nonce: Optional[str] = None                           # D-33: inside the signed payload
    policy_table_version: Optional[str] = None            # D-33
    executable: bool = False                              # D-41: always inert in P3
    requires_human: bool = False                          # D-34/D-35
    verify_reason: Optional[str] = None                   # D-33
    shadow: bool = True                                   # D-33
    human_approval: Optional[str] = None                  # D-33
    # -- v1.1 §17 additive ------------------------------------------------
    # ``scope`` is the named authority of the directive: what it is allowed to
    # affect. It is DELIBERATELY separate from ``target_actuator`` — the actuator
    # says where the effect would land, the scope says what was authorised — and
    # the signing form ``v2`` covers it explicitly (``v1`` covered only the
    # actuator, so an explicit scope could be rewritten under a valid v1
    # signature). Defaulted and unpersisted, so it is additive in every sense:
    # no column, no migration, and the twelve A8-pinned production artifacts are
    # untouched. When unset, ``scope`` falls back to ``target_actuator`` — see
    # ``signing.scope_token``, the one place that fallback is defined.
    scope: Optional[str] = None


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


class RuleTrace(BaseModel):
    """One row of the arbitration rule table, as evaluated (spec §3)."""

    rule: str                     # e.g. "exact_blocker", "needs_human", "floor"
    value: Any = None             # the observed value that decided the match
    matched: bool = False


class ShadowDecision(BaseModel):
    """A recorded shadow decision (mirrors ``shadow_decisions``; D-7/D-15/D-18).

    This is a *record*, never an authorisation: ``acted`` is always False in P3
    and ``would_have_acted_in_p4`` documents intent for the future P4 actuator
    without granting it.
    """

    decision_id: str
    session_id: str
    event_id: str
    observed_at: float
    event_seq: int = 0
    progress_seq: int = 0
    stall_events: int = 0
    action: DirectiveAction
    rationale: List[RuleTrace] = Field(default_factory=list)
    trace_hash: str = ""
    blocked: bool = False
    blocker_code: Optional[str] = None
    needs_human: bool = False
    directive_id: Optional[str] = None
    signed: bool = False
    signature: Optional[str] = None
    signing_alg: Optional[str] = None
    signature_verified: bool = False
    signature_status: str = "unsigned"
    acted: bool = False
    actuation_reason: Optional[str] = None
    would_have_acted_in_p4: Optional[str] = None
    policy_version: Optional[str] = None
    policy_table_version: Optional[str] = None
    policy_config_hash: Optional[str] = None
    prev_hash: Optional[str] = None
    entry_hash: Optional[str] = None


class ShadowLabel(BaseModel):
    """Mutable child-table row (D-13): labels live OUTSIDE the immutable tape."""

    decision_id: str
    label_source: str                        # oracle_next_event | human | ...
    label_value: str
    label_numeric: Optional[float] = None
    label_window_events: Optional[int] = None
    labeler: Optional[str] = None
    labeled_at: float = 0.0


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
