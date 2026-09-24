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

``COST_UNITS_PER_ACTION`` below is the single action->cost authority (D-34). It
is a *cost* table, not decision-mapping logic, so it does not weaken the
"no mapping logic here" rule above; it lives here because both the store
(deriving the persisted counter) and the supervisor budget governor need it and
must not each carry a private copy.

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

#: Static action -> cost table (ADR-0008 D-34, spec §2.4). **ONE authority, by
#: construction.** ``supervisor.budget`` imports this exact object (its
#: ``COST_UNITS`` is an alias, not a copy) and ``StateStore.budget_snapshot``
#: derives the persisted ``cost_units_used`` counter from the
#: ``directives.action`` column *through this dict* -- so the budget check and
#: the counter it reads cannot drift, and there is no second table to update.
#: Values are FROZEN (the acceptance gate pins them); `CONTINUE` is free because
#: it is the no-op action.
COST_UNITS_PER_ACTION: dict[DirectiveAction, float] = {
    DirectiveAction.CONTINUE: 0.0,
    DirectiveAction.VERIFY: 1.0,
    DirectiveAction.FINISH: 1.0,
    DirectiveAction.STEER: 2.0,
    DirectiveAction.RETRY: 3.0,
    DirectiveAction.PAUSE: 5.0,
    DirectiveAction.ESCALATE: 10.0,
}


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
