"""v1.1 §18 falsifiers for budget reporting with provenance.

The load-bearing tests are the trio:
``test_usage_unavailable_carries_no_number`` (absence is reported as absence),
``test_replay_cost_is_exactly_zero`` (a real zero is a fact and is labelled as
one), and ``test_fabrication_is_unrepresentable`` (the ambiguous combination
cannot be constructed at all, so no future caller can produce it).

The distinction they defend is the whole point of the section: on a dashboard
"0" and "unknown" look identical, and only one of them is a reason to believe a
budget is under control.
"""
from __future__ import annotations

import pytest

from supervisor import budget_report as BR
from supervisor.budget import BudgetGovernor, BudgetLimits, BudgetSnapshot


def _snapshot(**over) -> BudgetSnapshot:
    kw = dict(
        assessments_used=7,
        directives_used=3,
        rate_used=3,
        model_units_used=1234.0,
        limits=BudgetLimits(),
        cost_units_used=6.0,
        exhausted_code=None,
    )
    kw.update(over)
    return BudgetSnapshot(**kw)


def _report(mode: str, *, snapshot=None, governor=None):
    return BR.build_budget_report(
        provider_mode=mode,
        session_id="s-1",
        governor=governor if governor is not None else BudgetGovernor(clock=lambda: 1000.0),
        snapshot=snapshot if snapshot is not None else _snapshot(),
        clock=lambda: 42.0,
    )


# --------------------------------------------------------------------------- #
# The three labels
# --------------------------------------------------------------------------- #
def test_usage_unavailable_carries_no_number():
    r = _report("real")
    dim = r.dimension(BR.DIM_SCORER_TOKENS)
    assert dim.provenance == BR.PROVENANCE_USAGE_UNAVAILABLE
    assert dim.value is None
    assert dim.measured is False
    assert r.provider_cost is None
    assert r.any_unavailable is True
    assert r.fabricated is False


def test_replay_cost_is_exactly_zero():
    dim = _report("replay").dimension(BR.DIM_SCORER_TOKENS)
    assert dim.provenance == BR.PROVENANCE_REPLAY_COST
    assert dim.value == 0.0
    assert dim.measured is True, "a replayed run's zero IS a measurement"
    # the label is what stops this zero reading as "accounting is broken"
    assert dim.provenance != BR.PROVENANCE_USAGE_UNAVAILABLE


def test_mock_cost_is_labelled_mock_and_keeps_its_number():
    dim = _report("mock").dimension(BR.DIM_SCORER_TOKENS)
    assert dim.provenance == BR.PROVENANCE_MOCK_COST
    assert dim.value == 1234.0
    assert dim.measured is True


def test_the_disabled_provider_does_not_claim_a_zero():
    dim = _report("disabled").dimension(BR.DIM_SCORER_TOKENS)
    assert dim.provenance == BR.PROVENANCE_USAGE_UNAVAILABLE
    assert dim.value is None


@pytest.mark.parametrize("mode", ["", "unknown", "MOCKISH", "reall", None, "  "])
def test_an_unrecognised_mode_degrades_to_unavailable_never_to_a_number(mode):
    r = _report(mode)
    dim = r.dimension(BR.DIM_SCORER_TOKENS)
    assert dim.provenance == BR.PROVENANCE_USAGE_UNAVAILABLE
    assert dim.value is None


def test_mode_matching_is_case_and_whitespace_insensitive():
    assert BR.provider_cost_provenance("  REPLAY ") == BR.PROVENANCE_REPLAY_COST
    assert BR.provider_cost_provenance("Mock") == BR.PROVENANCE_MOCK_COST
    assert BR.provider_cost_provenance("REAL") == BR.PROVENANCE_USAGE_UNAVAILABLE


# --------------------------------------------------------------------------- #
# Fabrication is unrepresentable
# --------------------------------------------------------------------------- #
def test_fabrication_is_unrepresentable():
    with pytest.raises(ValueError, match="unmeasured quantity must report no number"):
        BR.DimensionReport(code="x", provenance=BR.PROVENANCE_USAGE_UNAVAILABLE, value=0.0)
    with pytest.raises(ValueError):
        BR.DimensionReport(code="x", provenance=BR.PROVENANCE_USAGE_UNAVAILABLE, value=1.0)


def test_a_replay_dimension_may_not_carry_a_non_zero_value():
    with pytest.raises(ValueError, match="only honest value is exactly 0.0"):
        BR.DimensionReport(code="x", provenance=BR.PROVENANCE_REPLAY_COST, value=3.0)
    BR.DimensionReport(code="x", provenance=BR.PROVENANCE_REPLAY_COST, value=0.0)  # fine


def test_an_unknown_provenance_label_is_refused():
    with pytest.raises(ValueError, match="unknown cost provenance"):
        BR.DimensionReport(code="x", provenance="FREE_LUNCH", value=None)


@pytest.mark.parametrize("mode", ["mock", "replay", "real", "disabled", "garbage"])
def test_no_mode_can_produce_a_fabricated_report(mode):
    r = _report(mode)
    assert r.fabricated is False
    for dim in r.dimensions:
        if dim.provenance == BR.PROVENANCE_USAGE_UNAVAILABLE:
            assert dim.value is None, f"{dim.code} fabricated under mode={mode}"


# --------------------------------------------------------------------------- #
# The two dimensions
# --------------------------------------------------------------------------- #
def test_the_plane_dimension_is_always_labelled_mock_cost():
    """The control plane's units are weights from a policy table, not currency,
    and saying so is the difference between a report and a price list."""
    for mode in ("mock", "replay", "real", "disabled"):
        dim = _report(mode).dimension(BR.DIM_DIRECTIVE_COST_UNITS)
        assert dim.provenance == BR.PROVENANCE_MOCK_COST
        assert dim.value == 6.0
        assert "notional" in dim.note


def test_without_a_counter_source_the_plane_cost_is_not_reported():
    """The defect this test was written to catch: ``dict.get(..., 0.0)`` made a
    session nobody measured read as "0 spent"."""
    r = BR.build_budget_report(
        provider_mode="mock", session_id="s-1", governor=None, snapshot=None, clock=lambda: 42.0
    )
    dim = r.dimension(BR.DIM_DIRECTIVE_COST_UNITS)
    assert dim.provenance == BR.PROVENANCE_USAGE_UNAVAILABLE
    assert dim.value is None
    assert r.counters == {}
    assert r.fabricated is False


def test_a_zero_reading_from_a_real_snapshot_is_still_reported_as_zero():
    """The mirror case: a snapshot that genuinely says 0 must NOT be suppressed.
    Suppressing it would be the same conflation in the other direction."""
    r = _report("mock", snapshot=_snapshot(cost_units_used=0.0, model_units_used=0.0))
    assert r.dimension(BR.DIM_DIRECTIVE_COST_UNITS).value == 0.0
    assert r.dimension(BR.DIM_DIRECTIVE_COST_UNITS).provenance == BR.PROVENANCE_MOCK_COST
    assert r.dimension(BR.DIM_SCORER_TOKENS).value == 0.0
    assert r.fabricated is False


# --------------------------------------------------------------------------- #
# Counter source is stated, not inferred
# --------------------------------------------------------------------------- #
def test_counter_source_is_stated_not_inferred():
    class StoreWithSnapshot:
        """Only the surface the governor reads. Nothing else — a double that
        defined more than the real collaborator does would prove nothing."""

        def budget_snapshot(self, session_id):  # noqa: ARG002
            return {k: 0.0 for k in
                    ("assessments_used", "model_units_used", "events_used",
                     "directives_used", "retries_used", "escalations_used",
                     "cost_units_used", "rate_used", "first_seen")}

        def add_directive(self, directive, *, origin):  # noqa: ARG002
            return None

    store_backed = BudgetGovernor(store=StoreWithSnapshot(), clock=lambda: 0.0)
    ledger = BudgetGovernor(clock=lambda: 0.0)

    given = BR.build_budget_report(
        provider_mode="mock", session_id="s-1", governor=store_backed,
        snapshot=_snapshot(), clock=lambda: 0.0,
    )
    ledger_only = BR.build_budget_report(
        provider_mode="mock", session_id="s-1", governor=ledger,
        snapshot=_snapshot(), clock=lambda: 0.0,
    )
    none = BR.build_budget_report(
        provider_mode="mock", session_id="s-1", governor=None, snapshot=None, clock=lambda: 0.0
    )
    assert given.counter_source == BR.COUNTER_SOURCE_STORE
    assert ledger_only.counter_source == BR.COUNTER_SOURCE_LEDGER
    assert none.counter_source == BR.COUNTER_SOURCE_UNAVAILABLE
    # non-vacuity: the three are distinguishable, so the assertion above means something
    assert len({given.counter_source, ledger_only.counter_source, none.counter_source}) == 3


def test_a_governor_snapshot_is_pulled_when_none_is_supplied():
    gov = BudgetGovernor(limits=BudgetLimits(), clock=lambda: 1000.0)
    gov.commit("s-1", kind="directive", units=0, action=None)
    r = BR.build_budget_report(
        provider_mode="mock", session_id="s-1", governor=gov, clock=lambda: 42.0
    )
    assert r.counter_source == BR.COUNTER_SOURCE_LEDGER
    assert r.dimension(BR.DIM_DIRECTIVE_COST_UNITS).value == 0.0


# --------------------------------------------------------------------------- #
# Carried-through fields
# --------------------------------------------------------------------------- #
def test_exhaustion_and_waivers_are_carried_through():
    r = _report("real", snapshot=_snapshot(exhausted_code="cost_units"))
    assert r.exhausted_code == "cost_units"
    assert r.healthy is False
    assert r.waived_dimensions == ("scorer_tokens",)
    assert r.limits["escalations"] == 3.0


def test_the_report_serialises_with_its_labels():
    payload = _report("real").as_dict()
    assert payload["fabricated"] is False
    assert payload["any_unavailable"] is True
    assert [d["provenance"] for d in payload["dimensions"]] == [
        BR.PROVENANCE_USAGE_UNAVAILABLE, BR.PROVENANCE_MOCK_COST,
    ]
    assert payload["provider_mode"] == "real"


def test_describe_pins_the_whole_vocabulary():
    described = BR.describe()
    assert described["provenance_values"] == [
        BR.PROVENANCE_USAGE_UNAVAILABLE, BR.PROVENANCE_MOCK_COST, BR.PROVENANCE_REPLAY_COST,
    ]
    assert described["unavailable_implies_no_value"] is True
    assert described["replay_value_is_exactly_zero"] is True
    assert described["fabrication_is_unrepresentable"] is True
    assert set(described["counter_sources"]) == {
        BR.COUNTER_SOURCE_STORE, BR.COUNTER_SOURCE_LEDGER, BR.COUNTER_SOURCE_UNAVAILABLE,
    }
