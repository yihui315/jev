"""``policy.decide`` is a thin wrapper over the ONE kernel (ADR-0008 D-32).

D-32 moved the mapping authority to ``jcc_core.decision``; the P1
flag-walking tests that used to live here were rewritten against the kernel
(spec §6.11). Priority is now asserted through the kernel's row table, and the
wrapper is pinned to be the SAME function (R6-1: equivalence by construction).

Signals ride in ``raw['signals']`` (the documented fallback used by
``decision.normalize``), so this suite depends on no new typed fields.
"""
from __future__ import annotations

from jcc_core import decision, policy
from jcc_core.schemas import POLICY_PRIORITY, AgentName, Assessment, DirectiveAction

_BASELINE_SIGNALS = {
    "meaningful_progress": True,
    "worker_stuck": False,
    "work_off_track": False,
    "requirements_satisfied": True,
    "needs_verification": False,
    "ready_to_finish": True,
    "needs_human": False,
}


def _make(signals=None, *, band="low", score=0.05, evidence_complete=True, **over):
    """A valid, non-blocked assessment whose signals are the decision input."""
    merged = dict(_BASELINE_SIGNALS)
    merged.update(signals or {})
    kwargs = dict(
        agent=AgentName.GENERIC,
        session_id="s",
        timestamp=1.0,
        probabilities={"risk": score},
        risk_score=score,
        band=band,
        provider_verified=True,
        raw={"signals": merged, "evidence_complete": evidence_complete},
    )
    kwargs.update(over)
    return Assessment(**kwargs)


# --------------------------------------------------------------------------- #
# Priority order — now exercised through the kernel's rule table
# --------------------------------------------------------------------------- #
def test_priority_escalate_highest():
    a = _make(
        {
            "needs_human": True,
            "worker_stuck": True,
            "needs_verification": True,
            "meaningful_progress": False,
            "requirements_satisfied": False,
            "ready_to_finish": False,
        }
    )
    assert policy.decide(a) == DirectiveAction.ESCALATE


def test_priority_pause_over_verify():
    a = _make({"worker_stuck": True, "needs_verification": True})
    assert policy.decide(a) == DirectiveAction.PAUSE


def test_priority_verify_over_steer():
    a = _make({"needs_verification": True, "meaningful_progress": False})
    assert policy.decide(a) == DirectiveAction.VERIFY


def test_priority_steer_over_retry():
    a = _make({"meaningful_progress": False, "requirements_satisfied": False, "ready_to_finish": False})
    assert policy.decide(a) == DirectiveAction.STEER


def test_priority_retry_over_finish():
    a = _make({"requirements_satisfied": False, "ready_to_finish": False})
    assert policy.decide(a) == DirectiveAction.RETRY


def test_priority_finish_over_continue():
    assert policy.decide(_make()) == DirectiveAction.FINISH


def test_continue_floor():
    # FINISH's gates fail (band is not "low" and the score is not < finish_risk)
    # while nothing higher fires.
    assert policy.decide(_make({}, band="medium", score=0.1)) == DirectiveAction.CONTINUE


def test_priority_index_ordering():
    assert policy.priority_index(DirectiveAction.ESCALATE) < policy.priority_index(DirectiveAction.CONTINUE)
    assert policy.priority_index(DirectiveAction.VERIFY) < policy.priority_index(DirectiveAction.FINISH)


def test_priority_list_matches_the_kernel_row_table():
    assert POLICY_PRIORITY == policy.PRIORITY == decision.table_actions()


# --------------------------------------------------------------------------- #
# R6-1: the wrapper IS the kernel (equivalence by construction)
# --------------------------------------------------------------------------- #
def test_decide_is_the_same_authority_as_arbitrate():
    """No second mapping authority: ``policy.decide`` == ``decision.decide_action``."""
    corpus = [
        _make(),
        _make({}, band="critical"),
        _make({}, band="high"),
        _make({}, band="medium", score=0.45),
        _make({"needs_verification": True}),
        _make({"needs_human": True}),
        _make({"requirements_satisfied": False, "ready_to_finish": False}),
        _make({"meaningful_progress": False}),
        _make({}, evidence_complete=False),
        _make({}, band="unknown"),                 # -> blocker (risk_level_unknown)
        _make(provider_verified=False),            # -> blocker (provider_unverified)
        Assessment(agent=AgentName.GENERIC, session_id="s", timestamp=1.0),  # bare
    ]
    for a in corpus:
        assert policy.decide(a) is decision.decide_action(a)


def test_status_argument_is_accepted_and_ignored():
    a = _make()
    assert policy.decide(a) is policy.decide(a, None)
