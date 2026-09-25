"""Structured refusals.

A refusal is a RETURN VALUE, never an exception and never a silent success. The
skill converts every failure -- validation, HTTP status, a missing gate, an
unexpected exception -- into a :class:`Refusal` that names the gate (or the
reason) that stopped the call, so the caller can act on it and the trace can
record it.

The vocabulary is deliberately closed and duplicated nowhere: the reason codes
that come from the real kernels (``EXPIRED``, ``BAD_SIGNATURE``, ``REPLAY``,
``CAPABILITY_DENIED``, ``BUDGET_EXHAUSTED``, ...) are imported from
``jcc_core.signing``/``jcc_core.schemas`` rather than re-spelled here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

#: The gate that stopped a gateway request.
GATE_CAPABILITY = "capability"
GATE_POLICY = "policy"
GATE_SIGNED_DIRECTIVE = "signed_directive"
GATE_HUMAN_APPROVAL = "human_approval"
GATE_BUDGET = "budget"
GATE_CONTROL_TOKEN = "control_token"


#: Plain string constants on purpose: the values either come from the pinned
#: kernels (``jcc_core.signing``) or are ours, and there is a test that pins the
#: closed set so a new code cannot appear without a line in the manifest.
CODE_MISSING_CONTROL_TOKEN = "MISSING_CONTROL_TOKEN"
CODE_INVALID_CONTROL_TOKEN = "INVALID_CONTROL_TOKEN"
CODE_MALFORMED_SESSION_ID = "MALFORMED_SESSION_ID"
CODE_UNKNOWN_SESSION = "UNKNOWN_SESSION"
CODE_MALFORMED_ARGUMENT = "MALFORMED_ARGUMENT"
CODE_UNKNOWN_TOOL = "UNKNOWN_TOOL"
CODE_NOTHING_TO_EXPLAIN = "NOTHING_TO_EXPLAIN"
CODE_BUDGET_UNAVAILABLE = "BUDGET_UNAVAILABLE"
CODE_UPSTREAM_ERROR = "UPSTREAM_ERROR"
CODE_TRACE_UNAVAILABLE = "TRACE_UNAVAILABLE"
CODE_INTERNAL_ERROR = "INTERNAL_ERROR"

#: The codes a gateway gate can produce. Imported values keep their spelling.
#: A gate that the deployment did not wire is DISTINCT from a gate that FIRED:
#: "you did not install the budget governor" and "the budget is exhausted" are
#: different facts and must never share a code.
CODE_CAPABILITY_DENIED = "CAPABILITY_DENIED"
CODE_POLICY_REFUSED = "POLICY_REFUSED"
CODE_APPROVAL_MISSING = "APPROVAL_MISSING"
CODE_BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
CODE_GATE_UNAVAILABLE = "GATE_UNAVAILABLE"


@dataclass(frozen=True)
class Refusal(Exception):
    """A structured refusal.

    It subclasses ``Exception`` ONLY as an internal signal, so that a handler can
    ``raise`` the refusal it already built and have ``OperatorSkill.call`` turn it
    back into a value. The public contract is the other way round: ``call()``
    NEVER propagates one -- a refusal is a return value, and the test suite asserts
    that no tool call escapes as an exception.
    """

    code: str
    message: str
    tool: str = ""
    gate: Optional[str] = None
    session_id: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_refusal(self) -> bool:
        return True

    def as_dict(self) -> Dict[str, Any]:
        return {
            "refused": True,
            "code": self.code,
            "gate": self.gate,
            "tool": self.tool,
            "session_id": self.session_id,
            "message": self.message,
            "details": dict(self.details),
        }

    def __str__(self) -> str:  # pragma: no cover - display only
        where = f" gate={self.gate}" if self.gate else ""
        return f"{self.code}({self.tool}{where}): {self.message}"


def refusal(code: str, message: str, **kwargs: Any) -> Refusal:
    return Refusal(code=code, message=message, **kwargs)


def gate_unavailable(gate: str, tool: str) -> Refusal:
    return Refusal(
        code=CODE_GATE_UNAVAILABLE,
        gate=gate,
        tool=tool,
        message=(
            f"gate {gate!r} is not wired for tool {tool!r}: the request will not "
            "be evaluated, so it cannot be sent. Refusing rather than forwarding "
            "an unvalidated request."
        ),
        details={"gate": gate},
    )
