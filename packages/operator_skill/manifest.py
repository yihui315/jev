"""The Operator Skill tool manifest -- the machine-readable contract for S-01/S-02.

This is the single source of truth for "what the skill offers". The MCP facade
reads it (``packages/mcp_facade/manifest.py``) rather than guessing at names, and
the evidence artifact is generated from it, so the gate reads capabilities from
the same object the server serves.

``mode``
    ``"read"``    -- cannot cause an actuation. The nine tools below are
                     read-only by default: they read the API/store, or -- for
                     ``jcc_assess`` -- ingest one AEP envelope whose resulting
                     directive is INERT (``Directive.executable is False``;
                     ``actuation_reason == "shadow_mode"``, pinned by the core
                     server's own surface tests). It records, it never acts.
    ``"gateway"`` -- ``jcc_pause`` / ``jcc_steer`` / ``jcc_retry``. These are
                     *requests*: they run the five gates in ``gates.py`` and
                     then forward to the real control-plane endpoint. They never
                     execute anything themselves (see ``skill.py``).

``can_actuate`` is False for EVERY tool, including the three gateway ones. That
is the property the "no direct execution authority" test asserts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from jcc_core.schemas import DirectiveAction

#: Capability strings. A principal holds a subset; a tool whose capability is not
#: in the principal's set is refused by the capability gate (``gates.py``).
CAP_READ = "jcc:read"
CAP_CONTROL = "jcc:control"
CAP_APPROVE = "jcc:approve"

#: The principal capability set of an OBSERVER: everything a dashboard or an
#: unprivileged tool caller may do, and nothing that can change a session.
OBSERVER_CAPABILITIES: Tuple[str, ...] = (CAP_READ,)

#: An operator additionally holds the mutation capability.
OPERATOR_CAPABILITIES: Tuple[str, ...] = (CAP_READ, CAP_CONTROL)

#: A human who may approve a directive.
APPROVER_CAPABILITIES: Tuple[str, ...] = (CAP_READ, CAP_CONTROL, CAP_APPROVE)

#: Session-id shape. Anything else is refused before it reaches the API: the id
#: is the primary identity (ADR-0003), so a malformed one is never passed on and
#: never interpolated blindly into a backend read.
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


@dataclass(frozen=True)
class ToolSpec:
    """One tool's declared contract."""

    name: str
    mode: str                       # "read" | "gateway"
    description: str
    endpoint: str                   # the real API path this tool drives
    http_method: str = "GET"
    capabilities: Tuple[str, ...] = (CAP_READ,)
    requires_control_token: bool = False
    requires_human_approval: bool = False
    #: Arguments, in declared order. Anything else is refused (strict binding) --
    #: this is what stops a caller smuggling an extra mutation argument into a
    #: read-only tool.
    arguments: Tuple[str, ...] = field(default_factory=tuple)
    #: True iff the tool can bring about an actuation. False for all 12 tools.
    can_actuate: bool = False
    #: What the tool may write. "" for pure reads.
    side_effect: str = ""
    #: The directive action a gateway tool asks for.
    requested_action: "DirectiveAction | None" = None

    def as_dict(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "mode": self.mode,
            "description": self.description,
            "endpoint": self.endpoint,
            "http_method": self.http_method,
            "capabilities": list(self.capabilities),
            "requires_control_token": self.requires_control_token,
            "requires_human_approval": self.requires_human_approval,
            "arguments": list(self.arguments),
            "can_actuate": self.can_actuate,
            "side_effect": self.side_effect,
        }


def _read(name, endpoint, description, arguments=(), requires_control_token=False,
          side_effect="", capabilities=(CAP_READ,)) -> ToolSpec:
    return ToolSpec(
        name=name,
        mode="read",
        description=description,
        endpoint=endpoint,
        http_method="GET",
        capabilities=tuple(capabilities),
        requires_control_token=requires_control_token,
        can_actuate=False,
        side_effect=side_effect,
        arguments=tuple(arguments),
    )


def _gateway(name, endpoint, action) -> ToolSpec:
    return ToolSpec(
        name=name,
        mode="gateway",
        description=(
            f"Gateway request for {action.value}. Re-validated through the "
            "capability, policy, signed-directive, human-approval and budget "
            "gates; the real control-plane endpoint performs the (inert) record. "
            "Never executes anything itself."
        ),
        endpoint=endpoint,
        http_method="POST",
        capabilities=(CAP_CONTROL,),
        requires_control_token=True,
        requires_human_approval=True,
        can_actuate=False,
        side_effect="records an inert control-plane directive",
        arguments=("session_id",),
        requested_action=action,
    )


#: The 12 tools: 9 read-only + 3 gateway. ``jcc_assess`` is the only read tool
#: that writes anything, and what it writes is an inert assessment record.
TOOLS: Dict[str, ToolSpec] = {
    "jcc_status": _read(
        "jcc_status",
        "/api/health",
        "Operator status: control-plane health plus control-token configuration "
        "state and the tool surface (never the token value).",
    ),
    "jcc_health": _read(
        "jcc_health",
        "/api/health",
        "Raw JCC Core health payload, including the declared open GET surface.",
    ),
    "jcc_sessions": _read(
        "jcc_sessions",
        "/api/sessions",
        "List of known sessions (identity is session_id; cwd is metadata only).",
    ),
    "jcc_session": _read(
        "jcc_session",
        "/api/sessions/{session_id}",
        "One session's state, assessment and current directive.",
        arguments=("session_id",),
    ),
    "jcc_assess": _read(
        "jcc_assess",
        "/api/assess",
        "Ingest one AEP v0.1 envelope for assessment. The resulting directive is "
        "INERT -- it records, it never actuates. Rejects malformed envelopes "
        "without issuing an assessment.",
        arguments=("session_id", "event_id", "delivery_id", "agent", "payload",
                   "evidence", "event_type", "project"),
        requires_control_token=True,
        side_effect="one inert assessment + audit row",
    ),
    "jcc_explain": _read(
        "jcc_explain",
        "/api/sessions/{session_id}",
        "Explain the stored assessment: the exact arbitration rows that matched, "
        "through the ONE deterministic kernel.",
        arguments=("session_id",),
    ),
    "jcc_verify": _read(
        "jcc_verify",
        "/api/audit/verify",
        "Verify the immutable audit tape's hash chain.",
    ),
    "jcc_recent_decisions": _read(
        "jcc_recent_decisions",
        "/api/decisions",
        "Recently issued directives, optionally filtered by session.",
        arguments=("session_id",),
    ),
    "jcc_budget": _read(
        "jcc_budget",
        "(supervisor)",
        "Budget counters DERIVED from persisted state (restart-safe), with the "
        "waived dimensions published rather than reported as 0.",
        arguments=("session_id",),
    ),
    "jcc_pause": _gateway("jcc_pause", "/api/pause", DirectiveAction.PAUSE),
    "jcc_steer": _gateway("jcc_steer", "/api/steer", DirectiveAction.STEER),
    "jcc_retry": _gateway("jcc_retry", "/api/retry", DirectiveAction.RETRY),
}


def tool_names() -> List[str]:
    return list(TOOLS)


def read_tools() -> List[str]:
    return [n for n, s in TOOLS.items() if s.mode == "read"]


def gateway_tools() -> List[str]:
    return [n for n, s in TOOLS.items() if s.mode == "gateway"]


def spec(name: str) -> ToolSpec:
    try:
        return TOOLS[name]
    except KeyError as exc:
        raise KeyError(f"unknown tool {name!r}; known tools: {sorted(TOOLS)}") from exc


def is_valid_session_id(session_id: object) -> bool:
    return isinstance(session_id, str) and bool(SESSION_ID_RE.fullmatch(session_id))


ALL_CAPABILITIES: Tuple[str, ...] = (
    CAP_READ,
    CAP_CONTROL,
    CAP_APPROVE,
    "jcc:read:health",
    "jcc:read:sessions",
    "jcc:read:session",
    "jcc:read:decisions",
    "jcc:read:audit",
    "jcc:read:budget",
    "jcc:control:pause",
    "jcc:control:steer",
    "jcc:control:retry",
)

#: Which capability the tool actually consumes -- the vector the MCP facade puts
#: in the machine-readable manifest.
TOOL_CAPABILITY_CONSUMES: Dict[str, str] = {
    **{name: CAP_READ for name in read_tools()},
    "jcc_pause": "jcc:control:pause",
    "jcc_steer": "jcc:control:steer",
    "jcc_retry": "jcc:control:retry",
}
