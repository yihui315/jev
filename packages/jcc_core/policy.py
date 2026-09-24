"""Deterministic policy engine — a THIN WRAPPER over the ONE kernel.

ADR-0008 D-32 (with R6-1/R6-2): the mapping authority is
``jcc_core.decision.arbitrate``. This module holds **no mapping logic of its
own**; it exists so the engine's call site (``core.py``:159) keeps its stable
interface ``policy.decide(assessment) -> DirectiveAction``.

Priority (highest precedence first) is owned by ``decision.ROWS``:

    ESCALATE > PAUSE > VERIFY > STEER > RETRY > FINISH > CONTINUE

Equivalence is **by construction** (R6-1): the engine path and the shadow plane
call the SAME function, so ``policy.decide`` cannot drift from
``decision.arbitrate`` — there is literally one authority.

What changed vs P1/P2
---------------------
P2 ``decide`` walked the provider's directive-shaped booleans
(``escalate``/``pause``/``steer``/``finished``/``needs_verify``). D-32 removes
that: the provider now emits signals, and the kernel maps them to an action.
``status`` is still accepted for call-site compatibility but is NOT read — the
kernel is a pure function of the assessment.
"""
from __future__ import annotations

from . import decision
from .decision import ThresholdsConfig
from .schemas import Assessment, DirectiveAction, SessionStatus

#: The threshold surface the engine path resolves against. Defaults to
#: ``Thresholds()`` with an EMPTY ``per_agent`` map, i.e. every agent resolves to
#: the documented defaults — which is precisely what keeps the P1/P2 engine
#: mapping byte-identical (R6-2). A configured surface is injected by the
#: composition root / tests only; the engine path itself stays on the defaults.
_DEFAULT_THRESHOLDS = ThresholdsConfig()

#: Canonical priority (index 0 == highest precedence). Re-exported from the
#: kernel's row table so the two can never disagree (D-5).
PRIORITY: list[DirectiveAction] = list(decision.table_actions())


def decide(
    assessment: Assessment,
    status: "SessionStatus | None" = None,
    *,
    thresholds: "ThresholdsConfig | None" = None,
) -> DirectiveAction:
    """Return the single highest-precedence action for an assessment.

    Delegates to ``jcc_core.decision.decide_action`` after resolving the
    per-agent threshold set (``per_agent[agent]`` else ``default``).

    ``status`` is accepted for call-site compatibility and is deliberately NOT
    read. ``thresholds`` is an explicit injection point (tests / composition
    root); omitted => the documented defaults.
    """
    cfg = _DEFAULT_THRESHOLDS if thresholds is None else thresholds
    agent = getattr(assessment, "agent", None)
    if hasattr(agent, "value"):  # AgentName is a str-Enum; the kernel wants str
        agent = agent.value
    return decision.decide_action(assessment, cfg.for_agent(agent))


def priority_index(action: DirectiveAction) -> int:
    """Lower index == higher precedence."""
    return decision.priority_index(action)
