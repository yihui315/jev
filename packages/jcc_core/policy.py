"""Deterministic policy engine.

Jev returns probabilities only (ADR-0002/0004). The policy is the SOLE
actuation authority: it maps an ``Assessment`` (plus session status) to exactly
one ``DirectiveAction`` using a fixed, documented priority order.

Priority (highest precedence first), per the P1 spec:

    ESCALATE > PAUSE > VERIFY > STEER > RETRY > FINISH > CONTINUE

The mapping is pure and deterministic, so the same assessment always yields the
same directive. This is what makes P1 reproducible and testable.
"""
from __future__ import annotations

from .schemas import Assessment, DirectiveAction, SessionStatus

# Canonical priority (index 0 == highest precedence).
PRIORITY: list[DirectiveAction] = [
    DirectiveAction.ESCALATE,
    DirectiveAction.PAUSE,
    DirectiveAction.VERIFY,
    DirectiveAction.STEER,
    DirectiveAction.RETRY,
    DirectiveAction.FINISH,
    DirectiveAction.CONTINUE,
]

# Boolean flag on the Assessment that triggers each action, in priority order.
_TRIGGERS: list[tuple[DirectiveAction, str]] = [
    (DirectiveAction.ESCALATE, "escalate"),
    (DirectiveAction.PAUSE, "pause"),
    (DirectiveAction.VERIFY, "needs_verify"),
    (DirectiveAction.STEER, "steer"),
    (DirectiveAction.RETRY, "retry"),
    (DirectiveAction.FINISH, "finished"),
]


def decide(assessment: Assessment, status: "SessionStatus | None" = None) -> DirectiveAction:
    """Return the single highest-precedence action for an assessment.

    Ordering is fixed and deterministic. VERIFY is the safe-default for
    ambiguous/incomplete evidence (ADR-0003), and CONTINUE is the floor.
    """
    for action, flag in _TRIGGERS:
        if getattr(assessment, flag, False):
            return action
    return DirectiveAction.CONTINUE


def priority_index(action: DirectiveAction) -> int:
    """Lower index == higher precedence."""
    return PRIORITY.index(action)
