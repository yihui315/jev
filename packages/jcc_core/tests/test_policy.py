from jcc_core import policy
from jcc_core.schemas import Assessment, DirectiveAction


def _make(**kw):
    return Assessment(agent="generic", session_id="s", timestamp=1.0, **kw)


def test_priority_escalate_highest():
    a = _make(escalate=True, pause=True, needs_verify=True, steer=True, retry=True, finished=True)
    assert policy.decide(a) == DirectiveAction.ESCALATE


def test_priority_pause_over_verify():
    a = _make(pause=True, needs_verify=True, steer=True)
    assert policy.decide(a) == DirectiveAction.PAUSE


def test_priority_verify_over_steer():
    a = _make(needs_verify=True, steer=True)
    assert policy.decide(a) == DirectiveAction.VERIFY


def test_priority_steer_over_retry():
    a = _make(steer=True, retry=True)
    assert policy.decide(a) == DirectiveAction.STEER


def test_priority_retry_over_finish():
    a = _make(retry=True, finished=True)
    assert policy.decide(a) == DirectiveAction.RETRY


def test_priority_finish_over_continue():
    a = _make(finished=True)
    assert policy.decide(a) == DirectiveAction.FINISH


def test_continue_floor():
    assert policy.decide(_make()) == DirectiveAction.CONTINUE


def test_priority_index_ordering():
    assert policy.priority_index(DirectiveAction.ESCALATE) < policy.priority_index(DirectiveAction.CONTINUE)
    assert policy.priority_index(DirectiveAction.VERIFY) < policy.priority_index(DirectiveAction.FINISH)
