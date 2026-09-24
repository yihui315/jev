"""Thin arbitration ADAPTER over the ONE kernel (ADR-0008 D-32).

This module is **not** a second authority. It turns an ``Assessment`` into the
kernel's ``Signals``, calls ``jcc_core.decision.arbitrate``, and attaches the
human-readable ``RuleTrace`` rationale the shadow tape and the Metric API want.
All mapping semantics live in ``jcc_core.decision`` (R6-1: engine ≡ shadow by
construction, because both call the same function).

If you are tempted to add a rule here: don't. Add a row to
``jcc_core.decision.ROWS`` so there stays exactly one mapping authority.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

from jcc_core import decision
from jcc_core.schemas import DirectiveAction

#: Blocker code surfaced when the kernel's EXACT_BLOCKER rule fires (D-35).
BLOCKER_EXACT = "EXACT_BLOCKER"


@dataclass(frozen=True)
class RuleTrace:
    """One evaluated rule row (spec §2.3)."""

    rule: str
    value: Any
    matched: bool


@dataclass(frozen=True)
class ArbitrationResult:
    """Adapter result: the kernel's decision plus its rationale."""

    action: DirectiveAction
    rationale: Tuple[RuleTrace, ...]
    trace_hash: str
    blocked: bool
    blocker_code: Optional[str]
    needs_human: bool
    reason_code: str
    matched_rows: Tuple[int, ...]
    policy_table_version: str = field(default=decision.POLICY_TABLE_VERSION)


def build_signals(assessment: Any) -> decision.Signals:
    """Normalise an ``Assessment`` into kernel ``Signals`` (pure)."""
    return decision.normalize(assessment)


def arbitrate(
    assessment: Any,
    thresholds: Optional[decision.Thresholds] = None,
    *,
    resolved: Optional[decision.Signals] = None,
) -> ArbitrationResult:
    """Arbitrate ``assessment`` through the kernel and attach the rationale.

    ``resolved`` lets a caller pass already-normalised signals (e.g. when the
    shadow record already holds them) so normalisation is not done twice and
    cannot silently diverge.
    """
    signals = resolved if resolved is not None else decision.normalize(assessment)
    t = thresholds if thresholds is not None else decision.Thresholds()
    kernel = decision.arbitrate(signals, t, assessment=assessment)

    matched = set(kernel.matched_rows)
    rationale = tuple(
        RuleTrace(rule=row.reason_code, value=row.action.value, matched=(idx in matched))
        for idx, row in enumerate(decision.ROWS)
    )
    blocked = bool(kernel.blocked)
    return ArbitrationResult(
        action=kernel.action,
        rationale=rationale,
        trace_hash=decision.trace_hash(signals, t, kernel),
        blocked=blocked,
        blocker_code=BLOCKER_EXACT if blocked else None,
        needs_human=blocked or kernel.action is DirectiveAction.ESCALATE,
        reason_code=kernel.reason_code,
        matched_rows=kernel.matched_rows,
    )


def rule_table_actions() -> List[DirectiveAction]:
    """Expose the kernel's row actions (for the D5 coherence test)."""
    return decision.table_actions()
