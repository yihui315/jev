"""P4 falsifiers for the budget plane (D-5 / F-3 / tasks #73, #75a, #75b).

Named for the property falsified, and every one of them is named for the state
that must NOT be reachable. Two rules from the §6 no-lying-fake rule apply here
too: a test double may not define an interface the real collaborator lacks, and a
test that would pass if the code did nothing must say so (each test below asserts
that the path it watches was actually entered).

The load-bearing test is ``test_cost_units_ceiling_fires_and_prices_the_real_action``:
before task #73 the ``cost_units`` ceiling could not fire on the action being
issued (the pre-check saw ``units=0`` and the pre-check ran before the action was
known), so the ceiling was decorative. It now fires at the exact projected
boundary.
"""
from __future__ import annotations

import pytest

from jcc_core import decision
from jcc_core.schemas import AgentName, Assessment, DirectiveAction
from jcc_core.signing import DirectiveSigner
from jcc_core.state_store import StateStore
from supervisor.budget import COST_UNITS, BudgetGovernor, BudgetLimits, CODE_COST_UNITS
from supervisor.ports import ShadowActuationPort
from supervisor.sentinel import BlockerCode, Sentinel


def _store(tmp_path) -> StateStore:
    """The REAL store — never a double (spec §6 no-lying-fake rule)."""
    return StateStore(str(tmp_path / "jcc.db"))


def _assessment(**over) -> Assessment:
    signals = {
        "meaningful_progress": True, "worker_stuck": False,
        "work_off_track": False, "requirements_satisfied": True,
        "needs_verification": False, "ready_to_finish": True, "needs_human": False,
    }
    signals.update(over.pop("signals", {}))
    kw = dict(
        agent=AgentName.GENERIC, session_id="s-1", timestamp=1.0,
        probabilities={"risk": 0.05}, risk_score=0.05, band="low",
        provider_verified=True,
        raw={"signals": signals, "evidence_complete": True},
    )
    kw.update(over)
    return Assessment(**kw)


def _sentinel(store, *, key=b"k" * 16, require_signature=False, limits=None) -> Sentinel:
    return Sentinel(
        store,
        signer=DirectiveSigner(key),
        budget=BudgetGovernor(limits or BudgetLimits(), store=None),
        dispatch_port=ShadowActuationPort(),
        thresholds=decision.ThresholdsConfig(),
        policy_version="p3.0.0",
        require_signature=require_signature,
        clock=lambda: 1000.0,
        nonce_factory=lambda: "fixed-nonce",
    )


#: Generous everywhere except the dimension under test — otherwise an unrelated
#: limit fires first and the cost ceiling never gets the chance to be proven live.
_WIDE = dict(
    directives=10_000, escalations=10_000, retries=10_000, assess_calls=10_000,
    events=10_000, directives_per_minute=10_000, wall_clock_s=10_000.0,
)


# --------------------------------------------------------------------------- #
# #73 — the cost ceiling must fire on the ACTION BEING ISSUED
# --------------------------------------------------------------------------- #
def test_cost_units_ceiling_fires_and_prices_the_real_action(tmp_path):
    """#73 falsifier: ``cost_units`` must exhaust and yield forced_escalate.

    Feeds a session enough directives to blow the ``cost_units`` ceiling and then
    proves the whole chain observably: the ceiling FIRES, it fires at the exact
    projected boundary, the charged cost is monotone and never exceeds the
    ceiling for a directive that was not budget-refused, and the refused
    directive is charged exactly once.

    Every healthy ``_assessment()`` arbitrates to FINISH (cost 1.0), so with a
    10.0 ceiling the boundary is exact: 10 FINISH cost 10.0 and are ALLOWED; the
    11th would project 11 > 10 and must be REFUSED.
    """
    store = _store(tmp_path)
    s = _sentinel(store, limits=BudgetLimits(cost_units=10.0, **_WIDE))
    gov, session = s.budget, "s-1"

    cost, fired_at, fired_snapshot = [], None, None
    for i in range(14):
        d = s.arbitrate_and_record(
            assessment=_assessment(), event_id=f"e{i}", agent="generic"
        )
        cost.append(gov.snapshot(session).cost_units_used)
        if d.blocker_code == BlockerCode.BUDGET_EXHAUSTED.value:
            assert d.action is DirectiveAction.ESCALATE, (
                "budget refusal must REPLACE with ESCALATE (D-34), never suppress"
            )
            if fired_at is None:
                fired_at, fired_snapshot = i, gov.snapshot(session)

    # 1) THE CEILING FIRES — this is the D-5 property the fix restores.
    assert fired_at is not None, (
        "the cost_units ceiling never fired: it is still decorative (task #73)"
    )

    # 2) it fires at the exact projected boundary (10 FINISH @ 1.0 == ceiling 10.0)
    assert fired_at == 10, f"ceiling fired at directive {fired_at}, expected 10"

    # 3) charged cost is monotone non-decreasing, and strictly so while allowed
    assert all(b >= a for a, b in zip(cost, cost[1:])), cost
    assert all(b > a for a, b in zip(cost[:fired_at], cost[1:fired_at + 1])), cost

    # 4) NO non-refused directive ever pushed the committed total past the ceiling
    allowed_cost = cost[:fired_at]
    assert allowed_cost and max(allowed_cost) <= 10.0, allowed_cost

    # 5) the refused directive is charged ONCE, at its own ESCALATE cost (#75a)
    assert fired_snapshot.cost_units_used - cost[fired_at - 1] == (
        COST_UNITS[DirectiveAction.ESCALATE]
    )

    # 6) the published dimension is live, not a silent 0.0 (#75b)
    assert fired_snapshot.cost_units_used == 20.0, fired_snapshot
    assert gov.check(session, kind="directive").code == CODE_COST_UNITS

    # 7) the guard is STICKY: later directives stay budget-refused rather than
    #    quietly returning to a "healthy" reading.
    for j in range(2):
        d = s.arbitrate_and_record(
            assessment=_assessment(), event_id=f"z{j}", agent="generic"
        )
        assert d.blocker_code == BlockerCode.BUDGET_EXHAUSTED.value


def test_the_unpriced_pre_check_is_blind_to_the_pending_action():
    """Non-vacuity control: the PRE-#73 shape cannot see the cost it is deciding on.

    This is the exact gap the fix closes, stated as an executable difference. At
    the point where the priced gate correctly refuses a directive, the old
    shape (``check(..., units=0)``, no action) still reports ``allowed`` — that
    one-step blind spot is D-5.
    """
    gov = BudgetGovernor(BudgetLimits(cost_units=10.0), store=None)
    for _ in range(10):
        gov.commit("s", kind="directive", action=DirectiveAction.FINISH)
    assert gov.snapshot("s").cost_units_used == 10.0

    old_shape = gov.check("s", kind="directive", units=0)
    assert old_shape.allowed is True, (
        "the unpriced pre-check now refuses a directive the ceiling permits; the "
        "RED→GREEN claim below no longer isolates the #73 change"
    )

    new_shape = gov.check("s", kind="directive", units=0, action=DirectiveAction.FINISH)
    assert new_shape.allowed is False
    assert new_shape.code == CODE_COST_UNITS


# --------------------------------------------------------------------------- #
# #75(a) — a forced ESCALATE is issued AND recorded, so it is charged
# --------------------------------------------------------------------------- #
def test_signer_unavailable_does_not_grant_a_free_directive_budget(tmp_path):
    """#75a: SIGNING_REQUIRED_UNAVAILABLE must not bypass the cost meter."""
    store = _store(tmp_path)
    s = _sentinel(store, key=None, require_signature=True)
    gov = s.budget

    d = s.arbitrate_and_record(assessment=_assessment(), event_id="e1", agent="generic")

    # the directive really was issued and really was recorded …
    assert d.blocker_code == BlockerCode.SIGNING_REQUIRED_UNAVAILABLE.value
    assert d.action is DirectiveAction.ESCALATE
    with store.connection() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM shadow_decisions WHERE session_id=?", ("s-1",)
        ).fetchone()[0]
    assert n == 1, "the forced directive must still be recorded (replace, never suppress)"

    # … so it is charged, exactly once, as the ESCALATE it became.
    snap = gov.snapshot("s-1")
    assert snap.cost_units_used == COST_UNITS[DirectiveAction.ESCALATE], snap
    assert snap.directives_used == 1


def test_budget_exhaustion_does_not_grant_a_free_directive_budget(tmp_path):
    """#75a, BUDGET_EXHAUSTED arm of the same ruling (see the adjudication in code)."""
    store = _store(tmp_path)
    wide = {k: v for k, v in _WIDE.items() if k != "directives"}
    s = _sentinel(store, limits=BudgetLimits(directives=0, **wide))
    gov = s.budget

    d = s.arbitrate_and_record(assessment=_assessment(), event_id="e1", agent="generic")
    assert d.blocker_code == BlockerCode.BUDGET_EXHAUSTED.value
    assert gov.snapshot("s-1").cost_units_used == COST_UNITS[DirectiveAction.ESCALATE], (
        "the replacement ESCALATE was issued and recorded, so it must be charged"
    )


def test_the_priced_gate_is_load_bearing(tmp_path):
    """Non-vacuity, end-to-end: WITHOUT it the ceiling fires ONE directive LATE.

    ``_BlindGovernor`` is the PRE-#73 shape — a ``check`` that never sees the
    action. Running the very same loop through it must fail the ceiling's own
    assertion (assertion 4): it lets an 11th FINISH through, pushing the committed
    total to 11 against a 10.0 ceiling. So the falsifier above is genuinely
    sensitive to the #73 change and not passing on fumes.
    """
    class _BlindGovernor(BudgetGovernor):
        def check(self, session_id, *, kind, units=0, action=None):  # noqa: D102
            return super().check(session_id, kind=kind, units=units)

    store = _store(tmp_path)
    s = Sentinel(
        store,
        signer=DirectiveSigner(b"k" * 16),
        budget=_BlindGovernor(BudgetLimits(cost_units=10.0, **_WIDE), store=None),
        dispatch_port=ShadowActuationPort(),
        thresholds=decision.ThresholdsConfig(),
        clock=lambda: 1000.0,
        nonce_factory=lambda: "fixed-nonce",
    )
    cost, fired_at = [], None
    for i in range(14):
        d = s.arbitrate_and_record(
            assessment=_assessment(), event_id=f"b{i}", agent="generic"
        )
        cost.append(s.budget.snapshot("s-1").cost_units_used)
        if d.blocker_code == BlockerCode.BUDGET_EXHAUSTED.value and fired_at is None:
            fired_at = i

    assert fired_at == 11, "the pre-#73 shape is expected to overshoot by one directive"
    assert max(cost[:fired_at]) > 10.0, (
        "the blind gate did NOT overshoot — the falsifier above would pass even "
        "without the #73 fix (it would be vacuous)"
    )


def test_a_directive_is_never_charged_twice(tmp_path):
    """#73 + #75a together: exactly one charge per issued directive.

    ``check`` reserves nothing and ``commit`` is the single writer, so the
    charged total must equal the sum of the cost table over the actions actually
    issued — not that sum plus a look-ahead that got committed by accident.
    """
    store = _store(tmp_path)
    s = _sentinel(store, limits=BudgetLimits(cost_units=10.0, **_WIDE))
    issued = []
    for i in range(6):
        d = s.arbitrate_and_record(
            assessment=_assessment(), event_id=f"e{i}", agent="generic"
        )
        issued.append(d.action)

    expected = sum(COST_UNITS[a] for a in issued)
    assert s.budget.snapshot("s-1").cost_units_used == pytest.approx(expected)


# --------------------------------------------------------------------------- #
# #75(b) — a dimension is either LIVE or LABELED INERT, never a silent 0.0
# --------------------------------------------------------------------------- #
def test_scorer_tokens_is_labelled_inert_against_a_store(tmp_path):
    """#75b: the waived dimension says so; it does not masquerade as healthy."""
    store = _store(tmp_path)
    gov = BudgetGovernor(BudgetLimits(), store=store)

    assert gov._scorer_tokens_inert() is True
    assert gov.snapshot("s-1").scorer_tokens_inert is True
    assert gov.check("s-1", kind="directive").snapshot.scorer_tokens_inert is True


def test_scorer_tokens_is_live_against_the_in_memory_ledger():
    """Against the ledger ``commit`` fills ``model_units_used``, so it CAN fire."""
    gov = BudgetGovernor(BudgetLimits(scorer_tokens=5.0), store=None)
    assert gov._scorer_tokens_inert() is False
    assert gov.snapshot("s").scorer_tokens_inert is False


def test_cost_units_is_published_not_left_at_zero(tmp_path):
    """#75b: the cost dimension must be readable, or a 0.0 looks like 'unused'."""
    store = _store(tmp_path)
    gov = BudgetGovernor(BudgetLimits(), store=None)
    assert gov.snapshot("s").cost_units_used == 0.0      # genuinely zero, pre-charge
    gov.commit("s", kind="directive", action=DirectiveAction.ESCALATE)
    snap = gov.snapshot("s")
    assert snap.cost_units_used == pytest.approx(COST_UNITS[DirectiveAction.ESCALATE])
    assert snap.exhausted_code is None                    # 10 of 500
