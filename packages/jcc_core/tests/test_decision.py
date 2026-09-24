"""Falsifiers for the deterministic arbitration kernel (ADR-0008 D-32).

Each test is named for the property it falsifies, not the code path it runs.
D1–D8 mirror ``P3_SENTINEL_DESIGN_INPUT.md`` §1.7.
"""
from __future__ import annotations

import ast
import math
from pathlib import Path

import pytest

from jcc_core import decision as D
from jcc_core.schemas import POLICY_PRIORITY, AgentName, Assessment, DirectiveAction

MODULE_PATH = Path(D.__file__)


def _assessment(**over) -> Assessment:
    """A valid, non-blocked assessment. Signals ride in ``raw['signals']``.

    Uses the documented ``raw`` fallback so this suite does not depend on the
    typed signal fields landing in ``schemas.py`` first.
    """
    signals = {
        "meaningful_progress": True,
        "worker_stuck": False,
        "work_off_track": False,
        "requirements_satisfied": True,
        "needs_verification": False,
        "ready_to_finish": True,
        "needs_human": False,
    }
    signals.update(over.pop("signals", {}))
    kwargs = dict(
        agent=AgentName.GENERIC,
        session_id="s",
        timestamp=1.0,
        probabilities={"risk": 0.05},
        risk_score=0.05,
        band="low",
        provider_verified=True,
        raw={"signals": signals, "evidence_complete": True},
    )
    kwargs.update(over)
    return Assessment(**kwargs)


def _construct(**over) -> Assessment:
    """Same shape as ``_assessment`` but bypasses pydantic validation.

    D2 asserts *kernel* totality: even an ``Assessment`` whose fields are the
    wrong type (``band=3``, ``probabilities={'risk': 'x'}``) must resolve to one
    action instead of raising. Pydantic would reject such an object at
    construction, so the fuzz has to build it with ``model_construct``.
    """
    signals = {
        "meaningful_progress": True,
        "worker_stuck": False,
        "work_off_track": False,
        "requirements_satisfied": True,
        "needs_verification": False,
        "ready_to_finish": True,
        "needs_human": False,
    }
    signals.update(over.pop("signals", {}))
    kwargs = dict(
        agent=AgentName.GENERIC,
        session_id="s",
        timestamp=1.0,
        probabilities={"risk": 0.05},
        risk_score=0.05,
        band="low",
        provider_verified=True,
        raw={"signals": signals, "evidence_complete": True},
    )
    kwargs.update(over)
    return Assessment.model_construct(**kwargs)


def _no_blockers() -> D.Signals:
    return D.Signals(
        meaningful_progress=True,
        worker_stuck=False,
        work_off_track=False,
        requirements_satisfied=True,
        needs_verification=False,
        ready_to_finish=True,
        needs_human=False,
        band=0,
        score=0.05,
        provider_verified=True,
        evidence_complete=True,
    )


# --------------------------------------------------------------------------- #
# D1 purity / repeatability
# --------------------------------------------------------------------------- #
def test_D1_decide_is_pure_repeatable():
    a = _assessment()
    first = D.decide_action(a)
    second = D.decide_action(a)
    assert first == second == DirectiveAction.FINISH

    s = _no_blockers()
    r1 = D.arbitrate(s)
    r2 = D.arbitrate(s)
    assert r1 == r2
    assert r1.matched_rows == r2.matched_rows


def test_D1_trace_hash_is_stable_and_sensitive():
    s = _no_blockers()
    t = D.Thresholds()
    result = D.arbitrate(s, t)
    h1 = D.trace_hash(s, t, result)
    h2 = D.trace_hash(s, t, D.arbitrate(s, t))
    assert h1 == h2, "identical inputs/thresholds/outcome must hash identically"

    # The thresholds are part of the hashed payload, so two different threshold
    # sets over the SAME signals must not collide — even when this particular
    # signal set happens to yield the same action.
    t_other = D.Thresholds(escalate_risk=0.5, pause_risk=0.4, steer_risk=0.3)
    assert D.trace_hash(s, t_other, D.arbitrate(s, t_other)) != h1

    # ...and so is the outcome: changing a signal changes the hash.
    s_bumped = D.Signals(**{**s.as_dict(), "worker_stuck": True})
    assert D.trace_hash(s_bumped, t, D.arbitrate(s_bumped, t)) != h1


# --------------------------------------------------------------------------- #
# D2 totality (fuzz, pinned seed)
# --------------------------------------------------------------------------- #
def test_D2_decide_is_total_fuzz():
    import random

    rng = random.Random(20260924)  # pinned -> reproducible
    pool = [True, False, None, 0, 1, 2, -1, 0.0, 0.5, 1.0, 1.5, "true", "false",
            "TRUE", "no", "maybe", "", [], {}, math.nan]
    field_names = list(D.REQUIRED_SIGNAL_FIELDS)
    # Deliberately includes values pydantic would reject (int/None band, "x"
    # probability): the *kernel* must absorb them, so we build with
    # ``model_construct`` to get them past validation.
    bands = ["low", "medium", "high", "critical", "unknown", "", None, 3]
    probs = [0.0, 0.5, 1.0, math.nan, 1.5, "x", None]

    for _ in range(2000):
        signals = {name: rng.choice(pool) for name in field_names}
        assess = _construct(
            signals=signals,
            band=rng.choice(bands),
            risk_score=rng.choice([0.0, 0.5, 1.0, 0.99]),
            probabilities={"risk": rng.choice(probs)},
            provider_verified=rng.choice([True, False]),
        )
        result = D.decide_action(assess)          # must never raise
        assert isinstance(result, DirectiveAction)


def test_D2_normalize_totality_on_non_assessment():
    for junk in (None, 0, "nope", [], object()):
        signals = D.normalize(junk)               # must never raise
        assert signals.score is None
        result = D.arbitrate(signals, assessment=junk)
        assert result.action is DirectiveAction.ESCALATE
        assert result.blocked is True


# --------------------------------------------------------------------------- #
# D3 returned action == lowest-priority-index match
# --------------------------------------------------------------------------- #
def test_D3_decide_equals_lowest_priority_index_match():
    import random

    rng = random.Random(4242)
    for _ in range(1500):
        s = D.Signals(
            meaningful_progress=rng.choice([True, False]),
            worker_stuck=rng.choice([True, False]),
            work_off_track=rng.choice([True, False]),
            requirements_satisfied=rng.choice([True, False]),
            needs_verification=rng.choice([True, False]),
            ready_to_finish=rng.choice([True, False]),
            needs_human=rng.choice([True, False]),
            band=rng.choice([0, 1, 2, 3]),
            score=round(rng.random(), 6),
            provider_verified=True,
            evidence_complete=rng.choice([True, False]),
        )
        t = D.Thresholds()
        result = D.arbitrate(s, t)
        assert result.matched_rows, "row 9 is unconditional, so something must match"
        expected = min(
            (i for i, row in enumerate(D.ROWS) if row.predicate(s, t, result.blocker_reasons)),
            key=lambda i: D.priority_index(D.ROWS[i].action),
        )
        assert result.action == D.ROWS[expected].action
        assert result.action == D.ROWS[result.matched_rows[0]].action


# --------------------------------------------------------------------------- #
# D4 no I/O, no model, no clock/RNG in the kernel
# --------------------------------------------------------------------------- #
def test_D4_kernel_has_no_io_or_model_imports():
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    banned_modules = {
        "socket", "subprocess", "urllib", "requests", "http", "time", "random",
        "logging", "os", "sqlite3", "pathlib", "shutil", "tempfile",
    }
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not (imported & banned_modules), f"kernel imports forbidden module(s): {imported & banned_modules}"

    source = MODULE_PATH.read_text(encoding="utf-8")
    assert ".assess(" not in source, "kernel must never call a provider"
    # no provider/model reference at all
    for token in ("JevProvider", "providers.", "model_dump(", ".execute("):
        assert token not in source, f"kernel references forbidden token {token!r}"


# --------------------------------------------------------------------------- #
# D5 table order == the mandated priority
# --------------------------------------------------------------------------- #
def test_D5_rule_table_matches_mandated_priority():
    assert D.table_actions() == list(POLICY_PRIORITY)
    assert D.table_actions() == [
        DirectiveAction.ESCALATE,
        DirectiveAction.PAUSE,
        DirectiveAction.VERIFY,
        DirectiveAction.STEER,
        DirectiveAction.RETRY,
        DirectiveAction.FINISH,
        DirectiveAction.CONTINUE,
    ]
    assert len(D.ROWS) >= len(POLICY_PRIORITY)


def test_D5_priority_index_ordering():
    assert D.priority_index(DirectiveAction.ESCALATE) < D.priority_index(DirectiveAction.CONTINUE)


# --------------------------------------------------------------------------- #
# D6 thresholds are a single, live source of truth
# --------------------------------------------------------------------------- #
def test_D6_thresholds_change_the_outcome():
    """D6: changing a live threshold changes the mapping (R6 retarget).

    Same signals, two ``p3.shadow.thresholds.default`` sets: the default
    ``pause_risk=0.6`` leaves a 0.5-risk event to the STEER band (0.4), while
    ``pause_risk=0.4`` promotes it to PAUSE.
    """
    s = D.Signals(
        meaningful_progress=True, worker_stuck=False, work_off_track=False,
        requirements_satisfied=True, needs_verification=False, ready_to_finish=True,
        needs_human=False, band=1, score=0.5, provider_verified=True, evidence_complete=True,
    )
    default = D.Thresholds()                                   # pause 0.6 -> STEER
    strict = D.Thresholds(pause_risk=0.4)                      # pause 0.4 -> PAUSE
    assert D.arbitrate(s, default).action is DirectiveAction.STEER
    assert D.arbitrate(s, strict).action is DirectiveAction.PAUSE

    # ...and the escalate cut is live too.
    hi = D.Signals(**{**s.as_dict(), "band": 1, "score": 0.8})
    assert D.arbitrate(hi, default).action is DirectiveAction.ESCALATE


def test_D6_mock_no_longer_hardcodes_threshold_cuts():
    """D-32: the provider must stop choosing the directive.

    Skips (rather than passes) while the coupled mock change is pending, so a
    green suite cannot be mistaken for "D-32 is complete".
    """
    mock_src = (MODULE_PATH.parent / "providers" / "mock.py").read_text(encoding="utf-8")
    emits_directive_flags = all(
        f"assessment.{flag} = " in mock_src
        for flag in ("escalate", "pause", "steer", "finished")
    )
    if emits_directive_flags:
        pytest.skip(
            "providers/mock.py still emits directive-shaped booleans (D-32 coupling 1 "
            "not yet landed); kernel is the sole authority but mock has not been cut over"
        )
    assert "0.8" not in mock_src and "0.6" not in mock_src and "0.4" not in mock_src


# --------------------------------------------------------------------------- #
# D7 boundaries are inclusive and documented
# --------------------------------------------------------------------------- #
def test_D7_boundary_is_inclusive():
    t = D.Thresholds()
    assert D.coerce_bool(0.5) is True          # p == 0.5 -> True
    assert D.coerce_bool(0.499999) is False
    s = D.Signals(
        meaningful_progress=True, worker_stuck=False, work_off_track=False,
        requirements_satisfied=True, needs_verification=False, ready_to_finish=True,
        needs_human=False, band=3, score=t.escalate_risk,
        provider_verified=True, evidence_complete=True,
    )
    assert D.arbitrate(s, t).action is DirectiveAction.ESCALATE


def test_D7_unrepresentable_values_are_unknown():
    assert D.coerce_bool("maybe") is None
    assert D.coerce_bool(2) is None
    assert D.coerce_bool(1.5) is None
    assert D.coerce_bool(math.nan) is None
    assert D.coerce_score(math.nan) is None
    assert D.coerce_score(1.5) is None
    assert D.coerce_score(True) is None       # bool is not a score
    assert D.coerce_band("CRITICAL") == 3
    assert D.coerce_band("nope") is None


def test_D7_score_quantisation_is_deterministic():
    assert D.coerce_score(0.7999999999999) == D.coerce_score(0.8)


# --------------------------------------------------------------------------- #
# D8 every required field is genuinely required (EXACT_BLOCKER)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("field_name", D.REQUIRED_SIGNAL_FIELDS)
def test_D8_unknown_required_field_is_exact_blocker(field_name):
    assess = _assessment(signals={field_name: None})
    signals = D.normalize(assess)
    blockers = D.find_blockers(signals, assess, D.Thresholds())
    assert D.BLOCKER_REQUIRED_SIGNAL_UNKNOWN in blockers
    result = D.arbitrate(signals, assessment=assess)
    assert result.blocked is True
    assert result.action is DirectiveAction.ESCALATE
    assert result.reason_code == "exact_blocker"
    assert result.matched_rows[0] == 0


def test_D8_unverified_provider_is_exact_blocker():
    assess = _assessment(provider_verified=False)
    result = D.arbitrate(D.normalize(assess), assessment=assess)
    assert D.BLOCKER_PROVIDER_UNVERIFIED in result.blocker_reasons
    assert result.action is DirectiveAction.ESCALATE


def test_D8_conflicting_numeric_risk_channels_is_exact_blocker():
    # risk_score says 0.1 while probabilities['risk'] says 0.9: two numeric
    # representations of the SAME quantity, from the SAME provider, disagree.
    # This check is threshold-independent (Round 5 makes cuts per-agent, so a
    # score-vs-band check would misfire for every non-default agent).
    assess = _assessment(risk_score=0.1, probabilities={"risk": 0.9})
    signals = D.normalize(assess)
    blockers = D.find_blockers(signals, assess, D.Thresholds())
    assert D.BLOCKER_CONTRADICTORY_SIGNALS in blockers

    result = D.arbitrate(signals, assessment=assess)
    assert result.action is DirectiveAction.ESCALATE
    assert result.reason_code == "exact_blocker"


def test_D8_agreeing_numeric_risk_channels_are_not_a_blocker():
    assess = _assessment(risk_score=0.3, probabilities={"risk": 0.3})
    result = D.arbitrate(D.normalize(assess), assessment=assess)
    assert D.BLOCKER_CONTRADICTORY_SIGNALS not in result.blocker_reasons


def test_D8_declared_provider_error_is_exact_blocker():
    assess = _assessment(blocker_kind="EXACT", blocker_reasons=["provider_error"])
    result = D.arbitrate(D.normalize(assess), assessment=assess)
    assert "provider_error" in result.blocker_reasons
    assert result.action is DirectiveAction.ESCALATE


def test_D8_blocker_reasons_are_sorted_and_deduplicated():
    assess = _assessment(signals={"worker_stuck": None, "needs_human": None}, provider_verified=False)
    result = D.arbitrate(D.normalize(assess), assessment=assess)
    reasons = list(result.blocker_reasons)
    assert reasons == sorted(set(reasons))
    assert reasons.count(D.BLOCKER_REQUIRED_SIGNAL_UNKNOWN) == 1
    assert len(reasons) > 1


def test_D8_blockers_never_yield_a_resuming_action():
    """D-35: EXACT_BLOCKER must never yield CONTINUE/STEER/RETRY/FINISH/PAUSE."""
    resuming = {
        DirectiveAction.CONTINUE, DirectiveAction.STEER,
        DirectiveAction.RETRY, DirectiveAction.FINISH, DirectiveAction.PAUSE,
    }
    corpus = [
        _assessment(signals={"needs_human": None}),
        _assessment(signals={"meaningful_progress": None}, band="critical"),
        _assessment(provider_verified=False, band="low"),
        _assessment(band="unknown"),
        None,
        0,
    ]
    for assess in corpus:
        result = D.arbitrate(D.normalize(assess), assessment=assess)
        if result.blocked:
            assert result.action is DirectiveAction.ESCALATE
            assert result.action not in resuming


# --------------------------------------------------------------------------- #
# Ruling F / D-16: work_off_track is recorded but never a decision input
# --------------------------------------------------------------------------- #
def test_work_off_track_never_affects_the_decision():
    base = dict(
        meaningful_progress=True, worker_stuck=False, requirements_satisfied=True,
        needs_verification=False, ready_to_finish=True, needs_human=False,
        band=0, score=0.05, provider_verified=True, evidence_complete=True,
    )
    outcomes = set()
    for value in (True, False, None):
        signals = D.Signals(work_off_track=value, **base)
        result = D.arbitrate(signals)
        outcomes.add((result.action, result.reason_code, result.blocked))
    # None must be as harmless as True/False: work_off_track is recorded-only
    # (R6-3/R5-7), so an unknown value cannot trigger the EXACT_BLOCKER gate.
    assert len(outcomes) == 1, f"work_off_track leaked into arbitration: {outcomes}"
    assert next(iter(outcomes))[0] is DirectiveAction.FINISH


# --------------------------------------------------------------------------- #
# ThresholdsConfig — Round 6 shape (D-24 / R6-2), fail-closed parsing
# --------------------------------------------------------------------------- #
def test_thresholds_defaults_match_documented_cuts():
    t = D.Thresholds()
    assert (t.escalate_risk, t.pause_risk, t.steer_risk, t.finish_risk) == (0.8, 0.6, 0.4, 0.1)
    assert not hasattr(t, "verify_risk"), "verify_risk is RETIRED by R6-2"


def test_from_config_accepts_round6_shape():
    cfg = D.ThresholdsConfig.from_config(
        {"default": {"escalate_risk": 0.9, "pause_risk": 0.7, "steer_risk": 0.5, "finish_risk": 0.2},
         "per_agent": {"workbuddy": {"pause_risk": 0.55}}}
    )
    assert cfg.default.escalate_risk == 0.9
    assert cfg.for_agent("workbuddy").pause_risk == 0.55
    assert cfg.for_agent("workbuddy").escalate_risk == 0.9      # inherited
    assert cfg.for_agent("codex").pause_risk == 0.7             # falls back to default
    assert cfg.for_agent(None).pause_risk == 0.7
    assert cfg.for_agent("workbuddy").steer_risk == 0.5
    assert cfg.for_agent("workbuddy").finish_risk == 0.2


def test_from_config_empty_per_agent_inherits_default():
    cfg = D.ThresholdsConfig.from_config({"default": {"pause_risk": 0.42}, "per_agent": {"claude": {}}})
    assert cfg.for_agent("claude").pause_risk == 0.42


def test_from_config_rejects_retired_keys():
    """The committed pre-Round-5/6 yaml must fail LOUDLY, not be mis-read."""
    with pytest.raises(ValueError, match="retired"):
        D.ThresholdsConfig.from_config({"enabled": False, "default": {"pause_risk": 0.6}})
    with pytest.raises(ValueError, match="retired"):
        D.ThresholdsConfig.from_config({"enabled": False})
    # verify_risk is retired by R6-2 — even inside a per-agent node.
    with pytest.raises(ValueError, match="retired"):
        D.ThresholdsConfig.from_config({"default": {"verify_risk": 0.4}})
    with pytest.raises(ValueError, match="retired"):
        D.ThresholdsConfig.from_config({"per_agent": {"codex": {"verify_risk": 0.4}}})
    # the exact pre-Round-5 committed shape (flat per-agent keys) is rejected
    with pytest.raises(ValueError):
        D.ThresholdsConfig.from_config({
            "enabled": False,
            "default": {"escalate_risk": 0.8, "pause_risk": 0.6, "steer_risk": 0.4, "finish_risk": 0.1},
            "workbuddy": {}, "claude": {}, "codex": {}, "hermes": {},
        })
    # flat per-agent keys WITHOUT the toggle are still rejected (unknown top-level)
    with pytest.raises(ValueError, match="unknown top-level"):
        D.ThresholdsConfig.from_config({
            "default": {"escalate_risk": 0.8, "pause_risk": 0.6},
            "workbuddy": {},
        })


def test_from_config_rejects_unknown_key_instead_of_ignoring():
    with pytest.raises(ValueError, match="unknown threshold key"):
        D.ThresholdsConfig.from_config({"default": {"escalate_risk": 0.8, "nonsense": 1}})


def test_from_config_rejects_misordered_or_out_of_range():
    with pytest.raises(ValueError, match="escalate_risk >= pause_risk"):
        D.ThresholdsConfig.from_config(
            {"default": {"escalate_risk": 0.2, "pause_risk": 0.9, "steer_risk": 0.1}}
        )
    with pytest.raises(ValueError, match="finish_risk <= steer_risk"):
        D.ThresholdsConfig.from_config(
            {"default": {"escalate_risk": 0.8, "pause_risk": 0.6, "steer_risk": 0.2, "finish_risk": 0.5}}
        )
    with pytest.raises(ValueError, match=r"within \[0, 1\]"):
        D.ThresholdsConfig.from_config({"default": {"escalate_risk": 1.5}})
    with pytest.raises(TypeError):
        D.ThresholdsConfig.from_config({"default": {"escalate_risk": "high"}})


def test_from_config_none_returns_documented_defaults():
    assert D.ThresholdsConfig.from_config(None) == D.ThresholdsConfig()
    assert D.ThresholdsConfig.from_config({}) == D.ThresholdsConfig()


def test_config_hash_is_stable_and_sensitive():
    a = D.ThresholdsConfig.from_config({"default": {"pause_risk": 0.6}})
    b = D.ThresholdsConfig.from_config({"default": {"pause_risk": 0.6}})
    c = D.ThresholdsConfig.from_config({"default": {"pause_risk": 0.61}})
    assert a.config_hash() == b.config_hash()
    assert a.config_hash() != c.config_hash()
    assert len(a.config_hash()) == 64


def test_snapshot_is_json_serialisable():
    import json

    cfg = D.ThresholdsConfig.from_config({"default": {}, "per_agent": {"hermes": {"pause_risk": 0.5}}})
    assert json.loads(json.dumps(cfg.snapshot(), sort_keys=True)) == cfg.snapshot()


# --------------------------------------------------------------------------- #
# Reachability: every action can actually be produced
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "signals,expected",
    [
        (dict(needs_human=True), DirectiveAction.ESCALATE),
        (dict(band=3), DirectiveAction.ESCALATE),
        (dict(score=0.8), DirectiveAction.ESCALATE),
        (dict(band=2), DirectiveAction.PAUSE),
        (dict(worker_stuck=True), DirectiveAction.PAUSE),
        (dict(score=0.6), DirectiveAction.PAUSE),
        (dict(needs_verification=True), DirectiveAction.VERIFY),
        (dict(evidence_complete=False), DirectiveAction.VERIFY),
        (dict(meaningful_progress=False), DirectiveAction.STEER),
        (dict(score=0.4), DirectiveAction.STEER),
        (dict(requirements_satisfied=False, ready_to_finish=False), DirectiveAction.RETRY),
        (dict(), DirectiveAction.FINISH),
    ],
)
def test_every_action_is_reachable(signals, expected):
    base = dict(
        meaningful_progress=True, worker_stuck=False, work_off_track=False,
        requirements_satisfied=True, needs_verification=False, ready_to_finish=True,
        needs_human=False, band=0, score=0.05, provider_verified=True, evidence_complete=True,
    )
    base.update(signals)
    assert D.arbitrate(D.Signals(**base)).action is expected


def test_continue_floor_is_reachable():
    # reachable only when FINISH's gates fail but nothing else fires: band is
    # medium and the score sits at the finish_risk boundary (not < it).
    s = D.Signals(
        meaningful_progress=True, worker_stuck=False, work_off_track=False,
        requirements_satisfied=True, needs_verification=False, ready_to_finish=True,
        needs_human=False, band=1, score=0.1, provider_verified=True, evidence_complete=True,
    )
    assert D.arbitrate(s).action is DirectiveAction.CONTINUE


def test_priority_escalate_beats_everything():
    s = D.Signals(
        meaningful_progress=False, worker_stuck=True, work_off_track=True,
        requirements_satisfied=False, needs_verification=True, ready_to_finish=False,
        needs_human=False, band=3, score=0.99, provider_verified=True, evidence_complete=False,
    )
    assert D.arbitrate(s).action is DirectiveAction.ESCALATE


def _abstaining_assessment(**over) -> Assessment:
    """A provider that DECLARES non-scoring abstention and supplies no channels.

    Mirrors ``DisabledProvider`` (Errata E-3): empty probabilities, falsy score,
    no signal booleans, ``raw["provider_abstained"] is True``, ``verified=True``.
    """
    kwargs = dict(
        agent=AgentName.GENERIC,
        session_id="s",
        timestamp=1.0,
        probabilities={},
        risk_score=0.0,
        band="disabled",
        provider_verified=True,
        raw={"provider_abstained": True},
    )
    kwargs.update(over)
    return Assessment(**kwargs)


def test_provider_abstention_scoping():
    """Errata E-3 / spec §6.16: a DECLARED non-scoring abstention (no numeric
    channels) is *incomplete evidence* -> VERIFY (row 5), NOT an exact blocker;
    an UNDECLARED absence and an UNVERIFIED provider still ESCALATE."""
    # (a) declared abstention + no numeric channels -> scoped away -> VERIFY
    abstaining = _abstaining_assessment()
    sig = D.normalize(abstaining)
    assert D.find_blockers(sig, abstaining) == (), "E-3 must scope the absence blockers"
    res = D.arbitrate(sig, assessment=abstaining)
    assert res.action is DirectiveAction.VERIFY
    assert res.blocked is False
    assert res.matched_rows[0] == 4, "VERIFY is row 5 (index 4)"

    # (b) the SAME signals, UNDECLARED -> the absence IS an exact blocker
    undeclared = abstaining.model_copy(update={"raw": {}})
    sig2 = D.normalize(undeclared)
    reasons2 = D.find_blockers(sig2, undeclared)
    assert D.BLOCKER_REQUIRED_SIGNAL_UNKNOWN in reasons2
    assert D.BLOCKER_RISK_LEVEL_UNKNOWN in reasons2
    assert D.arbitrate(sig2, assessment=undeclared).action is DirectiveAction.ESCALATE

    # (c) declared abstention but UNVERIFIED -> provider_unverified is NOT scoped
    unverified = abstaining.model_copy(update={"provider_verified": False})
    sig3 = D.normalize(unverified)
    assert D.BLOCKER_PROVIDER_UNVERIFIED in D.find_blockers(sig3, unverified)
    assert D.arbitrate(sig3, assessment=unverified).action is DirectiveAction.ESCALATE

    # (d) a DECLARATION that DID supply a numeric channel is not an abstention
    scoring = abstaining.model_copy(
        update={"probabilities": {"risk": 0.2}, "risk_score": 0.2}
    )
    sig4 = D.normalize(scoring)
    assert D.BLOCKER_RISK_LEVEL_UNKNOWN in D.find_blockers(sig4, scoring), (
        "a provider that supplied evidence (but no band) is not scoped"
    )

    # (e) a declaration that is NOT a real boolean does not count (fail closed)
    sneaky = abstaining.model_copy(update={"raw": {"provider_abstained": "true"}})
    assert D.arbitrate(D.normalize(sneaky), assessment=sneaky).action is DirectiveAction.ESCALATE

    # (f) spec §2.3 clause (ii): a declared abstention whose score channel is
    # ABSENT (`None`, not falsy 0.0) is NOT scoped — `score_unknown` is not an
    # E-3 blocker, so it still ESCALATEs. Fail-closed; `disabled` emits 0.0.
    no_score = abstaining.model_copy(update={"probabilities": {}, "risk_score": None})
    sig5 = D.normalize(no_score)
    assert sig5.score is None
    assert D.BLOCKER_SCORE_UNKNOWN in D.find_blockers(sig5, no_score)
    assert D.arbitrate(sig5, assessment=no_score).action is DirectiveAction.ESCALATE
