from jcc_core.actuator import Actuator
from jcc_core.schemas import Directive, DirectiveAction


def _dir(action=DirectiveAction.PAUSE):
    return Directive(directive_id="d1", session_id="s1", timestamp=1.0, action=action)


def test_disabled_refuses_all():
    a = Actuator(enabled=False)
    assert a.can_act(_dir()) is False
    res = a.execute(_dir())
    assert res["acted"] is False
    assert res["reason"] == "actuation_disabled"


def test_every_action_refused_when_disabled():
    a = Actuator(enabled=False)
    for act in DirectiveAction:
        res = a.execute(_dir(act))
        assert res["acted"] is False


def test_enabled_but_action_not_allowed():
    a = Actuator(enabled=True, allowed_actions=["CONTINUE"])
    assert a.can_act(_dir(DirectiveAction.PAUSE)) is False
    res = a.execute(_dir(DirectiveAction.PAUSE))
    assert res["acted"] is False
    assert res["reason"] == "action_not_allowed"


def test_enabled_allowed_action_still_not_implemented_in_p1():
    # Even if enabled + allowed, P1 has no real actuation code.
    a = Actuator(enabled=True, allowed_actions=["CONTINUE"])
    assert a.can_act(_dir(DirectiveAction.CONTINUE)) is True
    res = a.execute(_dir(DirectiveAction.CONTINUE))
    assert res["acted"] is False
    assert res["reason"] == "real_actuation_not_implemented"
