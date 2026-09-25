"""#82b — HOSTILE pins for the waived-dimension semantics (#75b).

Everything here is written to attack the waiver, not to confirm it. The claim
that ``scorer_tokens`` is waived rests on one sentence in ``budget.py``; these
tests are the attempt to break it. Each pin targets a different way a waiver can
quietly become a lie:

1. the counter source reports a token spend that would blow the ceiling **5000x**;
2. the limit is set to **0**, then to a **negative** number — the classic
   "excluded because the counter happened to read 0.0" hole, which no limit at or
   below zero can rescue if the dimension is still being evaluated;
3. the waiver is **published** and answerable BY CODE, so a reader is told which
   ceiling is not being enforced;
4. the observed ``exhausted_code`` VOCABULARY is identical with the store wired
   and unwired — not "the results happen to agree", which a deployment-dependent
   waiver would also produce whenever the waived counter reads 0.0 in both modes.

Non-vacuity is asserted in each: a pin that would pass if the counter were never
read is not evidence. So the hostile store counts how often it was consulted, and
each test that could pass without being read says so.
"""
from __future__ import annotations

import pytest

from jcc_core.schemas import DirectiveAction
from supervisor.budget import (
    CODE_CHECK_ERROR, CODE_COST_UNITS, CODE_DIRECTIVES, CODE_SCORER_TOKENS,
    BudgetGovernor, BudgetLimits,
)

#: 5000x the frozen default ceiling (200_000). Any implementation that still
#: evaluates this dimension against the limit trips on this immediately.
HOSTILE_TOKENS = 1_000_000_000.0

#: Two limits that DO fire, so the vocabulary comparisons below cannot be
#: satisfied by "nothing ever fires" in either mode.
_LIVE = dict(directives=10_000, cost_units=10.0)


class _HostileStore:
    """A counter source that reports a token spend no ceiling could survive.

    Not a stand-in for ``StateStore`` (spec §6 no-lying-fake rule): it exposes
    exactly the one surface the governor reads, and it is built to be
    unhelpfully truthful. Two properties make it hostile rather than merely odd —
    it reports a token figure that WOULD exhaust the dimension if the dimension
    were evaluated, and it records whether it was consulted, so a test that would
    pass if the governor never looked cannot hide behind that silence.
    """

    def __init__(self, tokens: float = HOSTILE_TOKENS) -> None:
        self._tokens = tokens
        self.consulted = 0

    def budget_snapshot(self, session_id: str) -> dict:
        self.consulted += 1
        return {
            "assessments_used": 0.0, "model_units_used": self._tokens,
            "events_used": 0.0, "directives_used": 0.0, "retries_used": 0.0,
            "escalations_used": 0.0, "cost_units_used": 0.0, "rate_used": 0.0,
            "first_seen": 0.0,
        }


def _hostile(limits: BudgetLimits = None) -> BudgetGovernor:
    return BudgetGovernor(
        limits or BudgetLimits(), store=_HostileStore(), clock=lambda: 0.0
    )


# --------------------------------------------------------------------------- #
# 1 — a counter the ceiling could never survive
# --------------------------------------------------------------------------- #
def test_a_billion_reported_tokens_do_not_exhaust_a_waived_dimension():
    """The store says 1,000,000 tokens against a 200,000 ceiling. Still allowed.

    Hostile in the precise sense that matters: the ONLY counter the source
    reports is the waived one, so the honest readings are "allowed" or "exhausted
    on some OTHER dimension". A ``budget_check_error`` here would mean an
    unreadable-counter fallback swallowed the decision — the other way a waiver
    gets hidden behind a code that never says what it is about.
    """
    gov = _hostile()
    store = gov.store
    decision = gov.check("s", kind="none")

    assert decision.allowed is True, (
        f"a waived dimension decided the call: code={decision.code!r}, "
        f"exhausted={decision.snapshot.exhausted_code!r}"
    )
    assert decision.snapshot.exhausted_code != CODE_SCORER_TOKENS
    assert decision.snapshot.exhausted_code != CODE_CHECK_ERROR
    assert decision.code is None, f"unexpected code: {decision.code!r}"

    # non-vacuity: the hostile figure WAS read, and is still REPORTED.
    assert store.consulted >= 1, "the counter source was never consulted"
    assert gov.snapshot("s").model_units_used == HOSTILE_TOKENS


# --------------------------------------------------------------------------- #
# 2 — limits that cannot rescue the "0.0 > limit is False" reading
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("limit", [0.0, -1.0, -1_000_000.0])
def test_a_zero_or_negative_limit_cannot_rearm_the_waived_dimension(limit):
    """The exclusion is by CONSTRUCTION, not by the counter reading 0.

    With a limit at or below zero, no value of ``model_units_used`` — not 0.0,
    not a billion — can keep an EVALUATED dimension quiet. So these passing
    proves the exclusion is not the flimsy "0.0 > limit is False" variety.
    """
    gov = _hostile(BudgetLimits(scorer_tokens=limit))
    gov.commit("s", kind="assessment", units=HOSTILE_TOKENS)

    snapped = gov.snapshot("s")
    assert snapped.exhausted_code != CODE_SCORER_TOKENS, snapped
    assert snapped.exhausted_code is None, (
        f"an unrelated dimension fired (limit={limit!r}): {snapped.exhausted_code!r}"
    )
    assert gov.check("s", kind="none").allowed is True
    assert gov.check("s", kind="assessment", units=0).allowed is True


# --------------------------------------------------------------------------- #
# 3 — the waiver is published, and answerable by code
# --------------------------------------------------------------------------- #
def test_the_waiver_is_published_and_answerable_by_code():
    """A reader must be able to NAME the ceiling that is not being enforced.

    ``waived_dimensions`` is for the human reading a snapshot; ``is_enforced`` is
    for the machine deciding whether to trust it. The expected value is written
    OUT (an explicit tuple) rather than compared against the constant, so this
    pin also fails if the waiver list itself is emptied.

    The ledger half matters: the ledger is the one counter source the governor
    itself fills, and it is exactly the shape that let this dimension fire before
    #75b. So the ledger is charged a billion tokens and then asked.
    """
    snapped = _hostile().check("s", kind="none").snapshot
    assert snapped.waived_dimensions == (CODE_SCORER_TOKENS,), snapped.waived_dimensions
    assert CODE_SCORER_TOKENS in snapped.waived_dimensions
    assert snapped.is_enforced(CODE_SCORER_TOKENS) is False
    assert snapped.is_enforced(CODE_COST_UNITS) is True
    assert snapped.is_enforced(CODE_DIRECTIVES) is True

    ledger = BudgetGovernor(BudgetLimits(), store=None, clock=lambda: 0.0)
    ledger.commit("s", kind="assessment", units=HOSTILE_TOKENS)
    led = ledger.snapshot("s")
    # the ledger really is holding a blown token counter — the waiver is load-bearing
    assert led.model_units_used == HOSTILE_TOKENS, led
    assert led.waived_dimensions == (CODE_SCORER_TOKENS,), led.waived_dimensions
    assert led.is_enforced(CODE_SCORER_TOKENS) is False
    assert led.exhausted_code != CODE_SCORER_TOKENS, led


# --------------------------------------------------------------------------- #
# 4 — one code vocabulary, whichever counter source is wired
# --------------------------------------------------------------------------- #
#: (kind, units, action) probes. Two of them are shaped to trip a live ceiling
#: (an ESCALATE costs 10 against the 10.0 limit), so these comparisons cannot be
#: satisfied by "nothing ever fires" on either side.
_PROBES = (
    ("none", 0, None),
    ("assessment", 0, None),
    ("directive", 0, None),
    ("directive", 7, DirectiveAction.FINISH),
    ("directive", 7, DirectiveAction.ESCALATE),
)


def _code_vocabulary(gov: BudgetGovernor) -> set:
    return {
        gov.check("s", kind=kind, units=units, action=action).snapshot.exhausted_code
        for kind, units, action in _PROBES
    }


def test_the_code_vocabulary_is_the_same_wired_and_unwired():
    """Identical SETS, not identical outcomes — the waiver is not a switch.

    A deployment-dependent waiver would also produce "the same results" by
    accident, whenever the waived counter reads 0.0 in both modes. So the SETS of
    codes observed across the same battery are compared: an extra ``scorer_tokens``
    on one side (a waiver that depends on the counter source) or a missing live
    code on either side (a waiver that overreached) fails.
    """
    limits = BudgetLimits(**_LIVE)
    vocabularies = {
        "ledger": _code_vocabulary(BudgetGovernor(limits, store=None, clock=lambda: 0.0)),
        "hostile_store": _code_vocabulary(_hostile(limits)),
    }

    assert vocabularies["ledger"] == vocabularies["hostile_store"], (
        "the enforced set depends on the counter source: "
        f"ledger={sorted(vocabularies['ledger'])} vs "
        f"store={sorted(vocabularies['hostile_store'])}"
    )

    vocabulary = vocabularies["ledger"]
    assert CODE_COST_UNITS in vocabulary, f"non-vacuous: {sorted(vocabulary)}"
    assert CODE_CHECK_ERROR not in vocabulary, sorted(vocabulary)
    assert CODE_SCORER_TOKENS not in vocabulary, sorted(vocabulary)
    assert len(vocabulary) >= 2, sorted(vocabulary)


def test_the_shipped_counter_source_and_an_adversarial_one_agree(tmp_path):
    """``run.py`` wires the store, so the crafted case is not the one that counts.

    The real ``StateStore`` and an invented counter source that reports a billion
    tokens must not disagree about which ceilings exist. This is the same claim as
    the ledger/store comparison above, made against the source that actually
    decides production rather than the one nobody ships.
    """
    from jcc_core.state_store import StateStore  # noqa: PLC0415

    limits = BudgetLimits(**_LIVE)
    real = StateStore(str(tmp_path / "hostile_real.db"))
    vocabularies = {
        "real_store": _code_vocabulary(
            BudgetGovernor(limits, store=real, clock=lambda: 0.0)
        ),
        "hostile_store": _code_vocabulary(_hostile(limits)),
    }

    assert vocabularies["real_store"] == vocabularies["hostile_store"], (
        f"real_store={sorted(vocabularies['real_store'])} vs "
        f"hostile_store={sorted(vocabularies['hostile_store'])}"
    )
    assert CODE_COST_UNITS in vocabularies["real_store"]
    assert CODE_SCORER_TOKENS not in vocabularies["real_store"]
