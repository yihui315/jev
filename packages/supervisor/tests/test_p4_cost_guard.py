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

import time

import pytest

from jcc_core import decision
from jcc_core.schemas import AgentName, Assessment, DirectiveAction
from jcc_core.signing import DirectiveSigner
from jcc_core.state_store import StateStore
from supervisor.budget import (
    COST_UNITS, BudgetGovernor, BudgetLimits, CODE_ASSESS_CALLS, CODE_COST_UNITS,
    CODE_DIRECTIVES, CODE_ESCALATIONS, CODE_EVENTS, CODE_RATE, CODE_RETRIES,
    CODE_SCORER_TOKENS, CODE_WALL_CLOCK, WAIVED_DIMENSIONS,
)
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


def test_the_ceiling_also_fires_when_the_real_store_derives_the_counters(tmp_path):
    """#73 against the PRODUCTION counter path (D-34), not just the in-memory ledger.

    ``run.py`` builds the governor with ``store=``, and a store-backed governor
    reads every counter from ``StateStore.budget_snapshot`` — which derives them
    from the ``directives`` TABLE. Until the commit that charges a directive also
    PERSISTED it (``store.add_directive``), that table stayed empty, so
    ``cost_units_used`` read 0.0 forever and the ceiling could not fire in
    production no matter what the priced gate computed. The tests above all use
    the ledger, which is exactly how that stayed invisible.

    This is the same shape as the #54 "records nothing" defect: a green
    in-memory suite over a dead production path.
    """
    store = _store(tmp_path)
    WIDE = dict(
        directives=10_000, escalations=10_000, retries=10_000, assess_calls=10_000,
        events=10_000, directives_per_minute=10_000, wall_clock_s=10_000.0,
    )
    gov = BudgetGovernor(BudgetLimits(cost_units=10.0, **WIDE), store=store,
                         clock=time.time)
    s = Sentinel(
        store,
        signer=DirectiveSigner(b"k" * 16),
        budget=gov,
        dispatch_port=ShadowActuationPort(),
        thresholds=decision.ThresholdsConfig(),
        clock=time.time,
    )

    fired_at, cost = None, []
    with store.connection() as conn:
        conn.execute("INSERT INTO sessions (session_id, created_at) VALUES ('s-1', ?)",
                     (time.time(),))
        conn.commit()

    for i in range(14):
        d = s.arbitrate_and_record(
            assessment=_assessment(), event_id=f"p{i}", agent="generic"
        )
        cost.append(gov.snapshot("s-1").cost_units_used)
        if d.blocker_code == BlockerCode.BUDGET_EXHAUSTED.value and fired_at is None:
            fired_at = i

    assert fired_at is not None, (
        "the ceiling still does not fire with a store-backed governor: the charge "
        "is not reaching the persisted counter source"
    )
    assert fired_at == 10, f"store-backed ceiling fired at {fired_at}, expected 10"
    assert all(b >= a for a, b in zip(cost, cost[1:])), cost
    assert max(cost[:fired_at]) <= 10.0, cost

    # non-vacuity: the directive rows landed in the STORE, and the DERIVED cost
    # reads back off that table rather than off any in-memory ledger.
    with store.connection() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM directives WHERE session_id='s-1'"
        ).fetchone()[0]
    # 10 FINISH @ 1.0 before the refusal, then 4 ESCALATE @ 10.0 (the refusal plus
    # the three after it, which stay budget-refused). Independent of the table.
    assert n == 14, f"expected all 14 issued directives persisted, found {n}"
    expected_cost = (sum(COST_UNITS[DirectiveAction.FINISH] for _ in range(fired_at))
                     + sum(COST_UNITS[DirectiveAction.ESCALATE]
                           for _ in range(14 - fired_at)))
    assert gov.snapshot("s-1").cost_units_used == pytest.approx(expected_cost), (
        "the store-derived cost does not match the persisted directive rows"
    )


def test_a_store_that_cannot_be_written_refuses_the_charge(tmp_path):
    """A store that derives counters but cannot persist them must NOT be charged."""

    class _ReadOnlyStore(StateStore):
        add_directive = None  # attribute present, not callable

    gov = BudgetGovernor(BudgetLimits(cost_units=10.0), store=_ReadOnlyStore(str(tmp_path / "jcc.db")))
    with pytest.raises(RuntimeError, match="add_directive"):
        gov.commit("s", kind="directive", action=DirectiveAction.FINISH,
                   directive=object())


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
# #75(b), uniform waiver — ONE enforced dimension set in EVERY counter-source mode
# --------------------------------------------------------------------------- #
class _TokenBearingStore:
    """An adversarial counter source that DOES report token usage.

    This is not a stand-in for ``StateStore`` (§6 no-lying-fake rule): it exposes
    exactly the one surface the governor reads, and it is built to prove the
    waiver does not depend on the counter source's abilities. A waiver that
    resurrects on a store capable of reporting tokens would be a POLICY THAT
    DEPENDS ON THE WIRING, which is the defect being closed.
    """

    def __init__(self, tokens: float):
        self._tokens = tokens

    def budget_snapshot(self, session_id: str) -> dict:
        return {
            "assessments_used": 0.0, "model_units_used": self._tokens,
            "events_used": 0.0, "directives_used": 0.0, "retries_used": 0.0,
            "escalations_used": 0.0, "cost_units_used": 0.0, "rate_used": 0.0,
            "first_seen": 0.0,
        }


def test_the_enforced_dimension_set_is_identical_across_counter_sources(tmp_path):
    """#75b: ledger / real store / token-bearing store — same enforced set.

    Pre-ruling the set depended on the wiring: the in-memory ledger let
    ``scorer_tokens`` fire (``commit`` filled ``model_units_used``) while the
    store-backed governor — the one ``run.py`` actually ships — could never fire
    it. So production policed FEWER ceilings than the harness that tests it, and
    which ceiling you had was decided by a constructor argument nobody reads.
    """
    ledger = BudgetGovernor(BudgetLimits(), store=None, clock=lambda: 0.0)
    real = BudgetGovernor(BudgetLimits(), store=_store(tmp_path), clock=lambda: 0.0)
    tokens = BudgetGovernor(BudgetLimits(scorer_tokens=1.0),
                            store=_TokenBearingStore(1_000_000.0), clock=lambda: 0.0)

    sets = {ledger.enforced_dimensions(), real.enforced_dimensions(),
            tokens.enforced_dimensions()}
    assert len(sets) == 1, f"the enforced set is deployment-dependent: {sets}"
    enforced = sets.pop()

    assert CODE_SCORER_TOKENS not in enforced
    # Non-vacuous: it is the FULL live set, not an empty one that is trivially
    # equal across modes. This is the mirror of jcc_core's 8-dimension matrix.
    assert enforced == (
        CODE_ASSESS_CALLS, CODE_EVENTS, CODE_DIRECTIVES, CODE_RETRIES,
        CODE_ESCALATIONS, CODE_WALL_CLOCK, CODE_COST_UNITS, CODE_RATE,
    )

    # The waiver is not a store-capability check: a source that CAN report tokens
    # does not get the dimension back — 1,000,000 observed against a limit of 1.0.
    d = tokens.check("s", kind="none")
    assert d.code != CODE_SCORER_TOKENS, "the waived dimension came back into play"
    assert d.code is None, f"something else fired: {d.code}"
    assert d.snapshot.model_units_used == 1_000_000.0, "the counter is still REPORTED"


def test_a_waived_dimension_cannot_enter_the_code_even_when_the_limit_is_blown():
    """Exclusion is by CONSTRUCTION, not by the counter happening to read 0.0.

    A waived ceiling must survive a limit the projection always exceeds (a
    negative one), otherwise the exemption is really just "0.0 > limit is False"
    and it silently re-arms the moment anything is reported.
    """
    for gov in (
        BudgetGovernor(BudgetLimits(scorer_tokens=-1.0), store=None, clock=lambda: 0.0),
        BudgetGovernor(BudgetLimits(scorer_tokens=-1.0),
                       store=_TokenBearingStore(1_000_000.0), clock=lambda: 0.0),
    ):
        gov.commit("s", kind="assessment", units=5.0)
        snapped = gov.snapshot("s")
        assert snapped.exhausted_code != CODE_SCORER_TOKENS, snapped
        assert gov.check("s", kind="assessment", units=0.0).code != CODE_SCORER_TOKENS
        # and it is still the ONLY thing violated, so nothing masked the refusal
        assert snapped.exhausted_code is None
        assert gov.check("s", kind="none").allowed is True, (
            "the waived dimension is leaking into the decision"
        )


def test_the_enforced_set_is_not_vacuously_empty_in_either_mode(tmp_path):
    """The waiver must not have quietly disabled the dimensions that stay.

    Same governor shape in both modes, with a ``scorer_tokens`` limit that is
    already blown so the waived code would win by CHECK ORDER if it were still
    enforced. Both modes must answer with the LIVE code instead.
    """
    limits = BudgetLimits(directives=0, scorer_tokens=-1.0)
    ledger = BudgetGovernor(limits, store=None, clock=lambda: 0.0)
    assert ledger.enforced_dimensions()
    assert ledger.check("s", kind="directive").code == CODE_DIRECTIVES, (
        "in-memory mode stopped enforcing directives — the waiver overreached"
    )

    store = BudgetGovernor(limits, store=_store(tmp_path), clock=lambda: 0.0)
    assert store.enforced_dimensions()
    assert store.check("s", kind="directive").code == CODE_DIRECTIVES, (
        "store-backed mode stopped enforcing directives — the waiver overreached"
    )


def test_the_waiver_is_published_on_every_snapshot_surface(tmp_path):
    """#75b: name the non-enforced ceilings, on each surface that reports one."""

    class _ExplodingStore:
        def budget_snapshot(self, session_id):
            raise RuntimeError("store is gone")

    for gov in (
        BudgetGovernor(BudgetLimits(), store=None, clock=lambda: 0.0),
        BudgetGovernor(BudgetLimits(), store=_store(tmp_path), clock=lambda: 0.0),
        BudgetGovernor(BudgetLimits(), store=_ExplodingStore(), clock=lambda: 0.0),
    ):
        assert gov.snapshot("s").waived_dimensions == WAIVED_DIMENSIONS
        assert gov.check("s", kind="none").snapshot.waived_dimensions == WAIVED_DIMENSIONS
    # the surfaces that saw an unreadable counter still name the waiver
    assert WAIVED_DIMENSIONS == (CODE_SCORER_TOKENS,)
    assert gov.snapshot("s").exhausted_code == "budget_check_error"

    # and the published waiver is CONSULTABLE, by code, not just by eye
    snap = BudgetGovernor(BudgetLimits(), store=None, clock=lambda: 0.0).snapshot("s")
    assert snap.is_enforced(CODE_SCORER_TOKENS) is False
    assert snap.is_enforced(CODE_DIRECTIVES) is True


def test_cost_units_is_published_not_left_at_zero(tmp_path):
    """#75b: the cost dimension must be readable, or a 0.0 looks like 'unused'."""
    store = _store(tmp_path)
    gov = BudgetGovernor(BudgetLimits(), store=None)
    assert gov.snapshot("s").cost_units_used == 0.0      # genuinely zero, pre-charge
    gov.commit("s", kind="directive", action=DirectiveAction.ESCALATE)
    snap = gov.snapshot("s")
    assert snap.cost_units_used == pytest.approx(COST_UNITS[DirectiveAction.ESCALATE])
    assert snap.exhausted_code is None                    # 10 of 500
