"""Arch-gate C1 / task #70 — the governor's action->cost charge must fail CLOSED.

Two independent properties, both load-bearing:

1. **ONE authority, by identity.** ``supervisor.budget.COST_UNITS`` must be the
   *same object* as ``jcc_core.policy.COST_UNITS_PER_ACTION`` — not a copy. The
   store-side derivation (``StateStore.budget_snapshot``) reads that same object,
   so identity is what stops the two halves of the cost table from drifting apart
   (D-34 / D-32: one mapping authority *by construction*, not by convention). A
   ``= dict(COST_UNITS_PER_ACTION)`` refactor would keep every value-level test
   green while re-introducing a second literal — this catches it.

2. **Fail CLOSED on an uncostable action.** ``BudgetGovernor.commit`` used to do
   ``COST_UNITS.get(action, 0.0)``: an action with no table row was silently
   priced at **0.0**, so the ``cost_units`` ceiling could never fire for it — the
   fail-OPEN mode D-5/D-34 forbid. It now indexes directly and raises, mirroring
   ``#63``'s store-side derivation. Reachability today is NIL (all seven
   ``DirectiveAction`` members are in the table — asserted below), so this is
   defence-in-depth, not a live defect: the falsifier is a *future* member added
   without a row, or a non-enum value.

The Sentinel containment is a THIRD fact, and it is why raising is safe: 
``Sentinel.on_event`` wraps the whole path in an observe-only guard that counts a
``persistence_failure`` and never lets the exception reach the engine's ingest
loop. Raising therefore *surfaces* the drift; it does not crash the daemon.
"""
from __future__ import annotations

import pytest

import jcc_core.policy as policy
import supervisor.budget as budget_mod
from jcc_core.schemas import DirectiveAction
from supervisor.budget import COST_UNITS, BudgetGovernor, BudgetLimits


# --------------------------------------------------------------------------- #
# 1. ONE authority by identity
# --------------------------------------------------------------------------- #
def test_cost_units_is_the_same_object_as_the_core_authority():
    """``is``, not ``==``: an alias, never a copy (D-34/D-32)."""
    assert budget_mod.COST_UNITS is policy.COST_UNITS_PER_ACTION, (
        "supervisor.budget.COST_UNITS is a COPY, not an alias of "
        "jcc_core.policy.COST_UNITS_PER_ACTION — that re-introduces a second "
        "action->cost authority (D-34/D-32) and lets the store and the governor "
        "drift apart while every value-level test stays green."
    )
    # And the store reads the same object, not merely the same values.
    from jcc_core import state_store as state_store_mod
    assert state_store_mod.COST_UNITS_PER_ACTION is policy.COST_UNITS_PER_ACTION


def test_every_current_directive_action_is_costable():
    """Pins WHY the fail-open is latent-only: the table covers every member.

    If a member is ever added without a row, this fails — which is the signal to
    add the row (or decide deliberately that the new action is uncostable).
    """
    assert {a.value for a in COST_UNITS} == {m.value for m in DirectiveAction}, (
        "COST_UNITS does not cover exactly the DirectiveAction members; an "
        "uncostable member would reach the fail-closed raise in commit()"
    )


# --------------------------------------------------------------------------- #
# 2. Fail CLOSED on an uncostable action
# --------------------------------------------------------------------------- #
def test_uncostable_action_raises_and_never_charges_zero():
    """The defect: ``.get(action, 0.0)`` charged 0.0. The fix: raise."""
    uncostable = "__NOT_IN_THE_FROZEN_COST_TABLE__"
    assert uncostable not in {a.value for a in COST_UNITS}, (
        "the sentinel must be uncostable for this falsifier to mean anything"
    )

    gov = BudgetGovernor(BudgetLimits())          # in-memory ledger (no store)
    with pytest.raises(ValueError) as exc:
        gov.commit("s", kind="directive", action=uncostable)
    assert "uncostable" in str(exc.value).lower(), exc.value


def test_costable_action_charges_its_exact_table_value():
    """Non-vacuity: the SAME commit path DOES charge a costable action.

    Without this, ``raises`` above could pass on a governor that raises for
    everything (including a valid action) — which would be a far worse bug.
    """
    gov = BudgetGovernor(BudgetLimits())
    gov.commit("s", kind="directive", action=DirectiveAction.ESCALATE)
    assert gov._counters("s")["cost_units_used"] == 10.0

    gov.commit("s", kind="directive", action=DirectiveAction.RETRY)
    assert gov._counters("s")["cost_units_used"] == 13.0   # 10 + RETRY(3)

    # CONTINUE is legitimately 0.0 IN THE TABLE — the fail-closed raise must NOT
    # fire for it; a real 0.0 row is not the same thing as a MISSING row.
    gov.commit("s", kind="directive", action=DirectiveAction.CONTINUE)
    assert gov._counters("s")["cost_units_used"] == 13.0


def test_assessment_commit_path_does_not_consult_the_cost_table():
    """The C1 guard is scoped to the directive/``action`` path. An assessment
    commit carries no action, so it must NOT hit the cost-table lookup (no raise),
    and its accounting is left exactly as it was.

    NOTE ``units`` is charged to ``cost_units_used`` on EVERY commit (the trailing
    line in ``commit``), regardless of kind — that is PRE-EXISTING behaviour,
    unrelated to and untouched by the C1 guard. Pinned here so the guard change is
    not misread as having altered it.
    """
    gov = BudgetGovernor(BudgetLimits())
    gov.commit("s", kind="assessment", units=4)
    c = gov._counters("s")
    assert c["assessments_used"] == 1
    assert c["model_units_used"] == 4.0
    assert c["cost_units_used"] == 4.0     # pre-existing: `units` hits cost too
