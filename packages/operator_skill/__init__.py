"""JCC Operator Skill (S-01) -- a read-by-default tool surface over the real API.

    from operator_skill.skill import OperatorSkill
    from operator_skill.backends import ASGIBackend

    skill = OperatorSkill(
        ASGIBackend(app, base_url="http://127.0.0.1:4778"),
        trace_path="runtime/jcc/operator-trace.jsonl",
    )
    result = skill.call("jcc_sessions")

A refusal is a normal, structured outcome -- never an exception, never a silent
success. See ``refusals.py`` for the vocabulary and ``trace.py`` for the tape.
"""
from __future__ import annotations

from .approvals import Approval, ApprovalRegistry
from .backends import ASGIBackend, Backend, BackendResult, HttpBackend, RecordingBackend
from .gates import (
    CapabilityAuthority,
    Gateway,
    GatewayContext,
    GatewayRequest,
    GatewayVerdict,
    PolicyAuthority,
    Principal,
    SignedDirectiveGate,
    check_budget,
    check_human_approval,
)
from .manifest import (
    OPERATOR_CAPABILITIES,
    OBSERVER_CAPABILITIES,
    TOOLS,
    ToolSpec,
    tool_names,
)
from .refusals import Refusal, gate_unavailable
from .skill import ToolResult, OperatorSkill
from .trace import TraceWriter, read_traces, validate_trace_record

__all__ = [
    "Approval",
    "ApprovalRegistry",
    "ASGIBackend",
    "Backend",
    "BackendResult",
    "CapabilityAuthority",
    "Gateway",
    "GatewayContext",
    "GatewayRequest",
    "GatewayVerdict",
    "HttpBackend",
    "OBSERVER_CAPABILITIES",
    "OPERATOR_CAPABILITIES",
    "OperatorSkill",
    "PolicyAuthority",
    "Principal",
    "RecordingBackend",
    "Refusal",
    "SignedDirectiveGate",
    "TOOLS",
    "ToolResult",
    "ToolSpec",
    "TraceWriter",
    "gate_unavailable",
    "read_traces",
    "tool_names",
    "validate_trace_record",
]
