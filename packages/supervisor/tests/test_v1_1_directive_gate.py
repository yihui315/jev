"""v1.1 §17 falsifiers for the nine-rule effect boundary.

The load-bearing test is ``test_every_single_failure_denies``: for EVERY one of
the nine checks it builds an otherwise-perfect directive, breaks exactly that
check, and asserts (a) the answer is DENY, (b) the failing check is named, and
(c) every check after it reports ``not_reached`` rather than ``ok``. (c) is the
one that is easy to get wrong and the one that matters: a boundary that marks
unrun checks as passed produces a verdict that looks like nine pieces of evidence
when it is one.

``test_the_verdict_is_deny_and_not_an_exception`` guards the other half: an
exception escaping the effect boundary is control flow that is not the word
"deny", and therefore is not audited as one.
"""
from __future__ import annotations

import inspect

import pytest

from jcc_core import signing as S
from jcc_core.nonce_cache import ReplayCache
from jcc_core.schemas import Directive, DirectiveAction
from supervisor import actuation_levels as AL
from supervisor import directive_gate as DG
from supervisor.actuation_levels import ActuationLevel as L

KEY = b"unit-test-key-not-a-real-secret"

GOOD_SESSION = "s-1"
GOOD_POLICY = "p3.0.0"
GOOD_TABLE = "p3.0.0"


def _directive(**over) -> Directive:
    kw = dict(
        directive_id="shadow_s-1_e1",
        session_id=GOOD_SESSION,
        timestamp=1000.0,
        action=DirectiveAction.STEER,
        target_actuator="noop",
        params={},
        policy_version=GOOD_POLICY,
        policy_table_version=GOOD_TABLE,
        evidence_hash="deadbeef",
        issued_at=1000.0,
        ttl_s=120.0,
        nonce="n1",
        signed=True,
        scope="session:s-1",
        requires_human=False,
    )
    kw.update(over)
    return Directive(**kw)


def _signed(**over) -> Directive:
    d = _directive(**over)
    d.signature = S.sign_directive(d, KEY, form=S.SIGNING_FORM_V2)
    return d


def _ctx(level: L = L.L2, **over) -> DG.GateContext:
    kw = dict(
        session_id=GOOD_SESSION,
        policy_version=GOOD_POLICY,
        policy_table_version=GOOD_TABLE,
        actuation_level=level,
        budget_allowed=True,
    )
    kw.update(over)
    return DG.GateContext(**kw)


def _run(directive=None, *, level=L.L2, replay=None, ctx=None):
    return DG.verify_effect(
        directive if directive is not None else _signed(),
        key=KEY,
        now=1000.0,
        context=ctx if ctx is not None else _ctx(level),
        replay=replay if replay is not None else ReplayCache(),
    )


# --------------------------------------------------------------------------- #
# The contract
# --------------------------------------------------------------------------- #
def test_nine_checks_in_the_mandated_order():
    assert DG.describe()["checks"] == list(S.VERIFICATION_CHECK_ORDER)
    assert DG.describe()["checks_total"] == 9
    assert DG.describe()["integrity_checks"] == list(S.INTEGRITY_CHECK_ORDER)
    assert DG.describe()["effect_checks"] == list(S.EFFECT_CHECK_ORDER)
    assert DG.describe()["deny_on_any_failure"] is True


def test_the_eleven_field_contract_is_pinned():
    assert DG.SIGNED_DIRECTIVE_FIELDS == (
        "directive_id", "session_id", "action", "scope", "evidence_hash",
        "policy_version", "issued_at", "expires_at", "nonce", "human_approval",
        "signature",
    )
    view = DG.signed_directive_view(_signed())
    assert set(view) == set(DG.SIGNED_DIRECTIVE_FIELDS), "the view must answer every field"
    # derived fields are actually derived, not passed through as None
    assert view["scope"] == "session:s-1"
    assert view["expires_at"] == 1120.0
    assert view["action"] == "STEER"
    # ``human_approval`` is genuinely optional, so None is the correct answer here
    assert view["human_approval"] is None
    assert DG.signed_directive_view(_signed(human_approval="OK"))["human_approval"] == "OK"


def test_the_happy_path_allows_and_reaches_all_nine():
    v = _run()
    assert v.allow is True
    assert v.reason == S.REASON_OK
    assert v.reached_count == 9
    assert v.failed_checks == ()
    assert v.unreached_checks == ()
    assert [c.name for c in v.checks] == list(S.VERIFICATION_CHECK_ORDER)


def test_all_nine_pass_and_only_then_allow():
    """Non-vacuity: every one of the nine is individually necessary."""
    for name in S.VERIFICATION_CHECK_ORDER:
        directive, ctx, replay = _BREAK[name]()
        now = 10 ** 6 if name == "ttl" else 1000.0
        v = DG.verify_effect(directive, key=KEY, now=now, context=ctx, replay=replay)
        assert v.allow is False, f"breaking {name!r} still produced ALLOW"


# --------------------------------------------------------------------------- #
# One mutation per check
# --------------------------------------------------------------------------- #
def _break_signed_present():
    return _signed(signed=False, signature=None), _ctx(), ReplayCache()


def _break_signature():
    d = _directive()  # correct fields, WRONG form -> BAD_SIGNATURE
    d.signature = S.sign_directive(d, KEY, form=S.SIGNING_FORM_V1)
    return d, _ctx(), ReplayCache()


def _break_ttl():
    return _signed(), _ctx(), ReplayCache()          # paired with now=10**6 below


def _break_replay():
    d = _signed()
    replay = ReplayCache()
    replay.check_and_add("n1", 1000.0)               # pre-poison: the nonce is seen
    return d, _ctx(), replay


def _break_session():
    return _signed(), _ctx(session_id="a-different-session"), ReplayCache()


def _break_capability():
    return _signed(action=DirectiveAction.FINISH), _ctx(level=L.L2), ReplayCache()


def _break_policy():
    return _signed(), _ctx(policy_version="p9.9.9"), ReplayCache()


def _break_budget():
    return _signed(), _ctx(budget_allowed=False), ReplayCache()


def _break_approval():
    return _signed(requires_human=True, human_approval=None), _ctx(level=L.L3), ReplayCache()


_BREAK = {
    "signed_present": _break_signed_present,
    "signature": _break_signature,
    "ttl": _break_ttl,
    "replay": _break_replay,
    "session": _break_session,
    "capability": _break_capability,
    "policy": _break_policy,
    "budget": _break_budget,
    "approval": _break_approval,
}

_EXPECTED_REASON = {
    "signed_present": S.REASON_UNSIGNED,
    "signature": S.REASON_BAD_SIGNATURE,
    "ttl": S.REASON_EXPIRED,
    "replay": S.REASON_REPLAY,
    "session": S.REASON_SESSION_MISMATCH,
    "capability": AL.REASON_ACTION_NOT_PERMITTED,
    "policy": S.REASON_POLICY_MISMATCH,
    "budget": S.REASON_BUDGET_EXHAUSTED,
    "approval": S.REASON_APPROVAL_MISSING,
}


def test_the_break_table_covers_every_check():
    """Self-checking: a new check without a break case fails HERE, not silently."""
    assert set(_BREAK) == set(S.VERIFICATION_CHECK_ORDER)
    assert set(_EXPECTED_REASON) == set(S.VERIFICATION_CHECK_ORDER)


@pytest.mark.parametrize("check", list(S.VERIFICATION_CHECK_ORDER))
def test_every_single_failure_denies(check):
    directive, ctx, replay = _BREAK[check]()
    now = 10 ** 6 if check == "ttl" else 1000.0
    v = DG.verify_effect(directive, key=KEY, now=now, context=ctx, replay=replay)

    assert v.allow is False, f"{check!r} did not deny"
    assert [c.name for c in v.failed_checks] == [check], "exactly the broken check must fail"
    assert v.reason == _EXPECTED_REASON[check]

    order = list(S.VERIFICATION_CHECK_ORDER)
    idx = order.index(check)
    # every check BEFORE it passed...
    for name in order[:idx]:
        result = next(c for c in v.checks if c.name == name)
        assert result.ok is True, f"{name!r} precedes {check!r} and must have passed"
    # ...and every check AFTER it was NOT REACHED, never "passed"
    assert [c.name for c in v.unreached_checks] == order[idx + 1:]
    assert v.reached_count == idx + 1


# --------------------------------------------------------------------------- #
# Three-state reporting
# --------------------------------------------------------------------------- #
def test_unreached_checks_are_never_reported_as_passed():
    v = _run(_signed(signed=False, signature=None))
    assert [c.name for c in v.failed_checks] == ["signed_present"]
    assert v.reached_count == 1, "only check 1 is reached, and it FAILED"
    assert [c.name for c in v.unreached_checks] == list(S.VERIFICATION_CHECK_ORDER)[1:]
    # the eight unrun checks are None, not True — this is the whole point
    assert all(c.ok is None for c in v.unreached_checks)
    assert sum(1 for c in v.checks if c.ok is True) == 0


def test_l0_returns_zero_reached_checks():
    v = _run(level=L.L0)
    assert v.allow is False
    assert v.reason == AL.REASON_LEVEL_OFF
    assert v.reached_count == 0
    assert len(v.checks) == 9
    assert v.actuation_level is L.L0


def test_l0_denies_even_a_flawless_directive():
    assert _run(level=L.L0).allow is False


def test_l1_dry_run_stops_at_capability_with_the_dry_run_reason():
    """L1's whole point: the gate RUNS against live traffic and the effect is
    still suppressed. It proves checks 1–5 and says exactly where it stopped."""
    v = _run(level=L.L1)
    assert v.allow is False
    assert v.reason == AL.REASON_LEVEL_DRY_RUN
    assert [c.name for c in v.failed_checks] == ["capability"]
    assert v.reached_count == 6
    assert [c.name for c in v.unreached_checks] == ["policy", "budget", "approval"]


# --------------------------------------------------------------------------- #
# The ladder integration
# --------------------------------------------------------------------------- #
def test_l2_denies_irreversible_and_l3_permits_it():
    finish = _signed(action=DirectiveAction.FINISH)
    assert _run(finish, level=L.L2).allow is False
    assert _run(finish, level=L.L2).reason == AL.REASON_ACTION_NOT_PERMITTED
    assert _run(finish, level=L.L3).allow is True


def test_l2_permits_every_reversible_action():
    for action in sorted(AL.REVERSIBLE_ACTIONS):
        v = _run(_signed(action=action), level=L.L2)
        assert v.allow is True, f"L2 refused the reversible action {action.value}"


# --------------------------------------------------------------------------- #
# Signing-form coupling
# --------------------------------------------------------------------------- #
def test_a_v1_signed_directive_is_refused_by_the_boundary():
    d = _directive()
    d.signature = S.sign_directive(d, KEY, form=S.SIGNING_FORM_V1)
    v = _run(d)
    assert v.allow is False
    assert v.reason == S.REASON_BAD_SIGNATURE
    assert [c.name for c in v.failed_checks] == ["signature"]


def test_approval_present_but_signed_under_v1_is_refused():
    """The pair that makes rule 9 more than decorative: an approval that is not
    inside the signed bytes is not an approval."""
    d = _directive(requires_human=True, human_approval="APPROVED")
    d.signature = S.sign_directive(d, KEY, form=S.SIGNING_FORM_V1)
    v = _run(d, level=L.L3)
    assert v.allow is False
    assert v.reason == S.REASON_BAD_SIGNATURE


def test_approval_present_and_signed_under_v2_is_accepted():
    v = _run(_signed(requires_human=True, human_approval="APPROVED"), level=L.L3)
    assert v.allow is True
    assert v.reached_count == 9


def test_the_approval_rule_reads_the_signed_field_not_a_merely_present_one():
    """Both directions of rule 9, in one place."""
    absent = _run(_signed(requires_human=True, human_approval=None), level=L.L3)
    assert absent.reason == S.REASON_APPROVAL_MISSING
    blank = _run(_signed(requires_human=True, human_approval="   "), level=L.L3)
    assert blank.reason == S.REASON_APPROVAL_MISSING
    present = _run(_signed(requires_human=True, human_approval="APPROVED"), level=L.L3)
    assert present.allow is True


def test_require_approval_can_only_add_a_requirement():
    d = _signed(requires_human=False, human_approval=None)
    assert _run(d).allow is True
    assert _run(d, ctx=_ctx(require_approval=True)).reason == S.REASON_APPROVAL_MISSING


# --------------------------------------------------------------------------- #
# Scope
# --------------------------------------------------------------------------- #
def test_a_directive_with_no_scope_at_all_is_denied():
    """Scope unset falls back to the actuator; a directive whose actuator is ALSO
    empty authorises nothing and cannot be audited."""
    d = _signed(target_actuator="", scope=None)
    assert DG.signed_directive_view(d)["scope"] == ""
    v = _run(d)
    assert v.allow is False
    assert v.reason == S.REASON_CAPABILITY_DENIED
    assert [c.name for c in v.failed_checks] == ["capability"]


def test_a_rewritten_scope_breaks_the_signature_before_the_gate_sees_it():
    d = _signed(scope="session:s-1")
    d.scope = "actuator:everything"
    v = _run(d)
    assert v.allow is False
    assert v.reason == S.REASON_BAD_SIGNATURE


# --------------------------------------------------------------------------- #
# Hygiene
# --------------------------------------------------------------------------- #
def test_the_verdict_is_deny_and_not_an_exception_for_a_malformed_directive():
    class Hostile:
        pass

    v = DG.verify_effect(
        Hostile(), key=KEY, now=1000.0, context=_ctx(), replay=ReplayCache()
    )
    assert v.allow is False
    assert v.reason == S.REASON_UNSIGNED
    assert [c.name for c in v.failed_checks] == ["signed_present"]


def test_a_missing_context_field_is_a_loud_type_error():
    """The other half: a PROGRAMMING error must not be silently turned into DENY,
    because that would hide the caller's bug inside the safety answer."""
    with pytest.raises(TypeError):
        DG.GateContext(session_id="s-1")  # type: ignore[call-arg]


def test_no_key_is_reported_as_a_signature_failure_not_a_presence_failure():
    v = DG.verify_effect(
        _signed(), key=None, now=1000.0, context=_ctx(), replay=ReplayCache()
    )
    assert v.allow is False
    assert [c.name for c in v.failed_checks] == ["signature"]
    assert v.reason == S.REASON_NO_KEY


def test_the_budget_reason_code_is_carried_through():
    v = _run(ctx=_ctx(budget_allowed=False, budget_code="cost_units"))
    assert v.reason == "cost_units"
    assert [c.name for c in v.failed_checks] == ["budget"]


def test_the_verdict_serialises_with_its_evidence():
    payload = _run().as_dict()
    assert payload["allow"] is True
    assert payload["checks_total"] == 9
    assert payload["checks_reached"] == 9
    assert payload["failed"] == [] and payload["not_reached"] == []
    assert len(payload["checks"]) == 9
    assert [c["name"] for c in payload["checks"]] == list(S.VERIFICATION_CHECK_ORDER)


def test_the_boundary_has_no_form_parameter_and_no_bypass_flag():
    """Structural pin: nothing a caller passes may weaken a rule."""
    params = set(inspect.signature(DG.verify_effect).parameters)
    assert params == {"directive", "key", "now", "context", "replay", "clock_skew_s"}
    for banned in ("form", "skip", "dry_run", "force", "allow", "bypass"):
        assert banned not in params


def test_verify_effect_is_the_only_allow_producer_in_the_module():
    """The word ALLOW is produced in exactly one place — the verdict constructor.
    A second producer is a second answer to a security question, and the second
    one is the one nobody reviews."""
    import pathlib

    source = pathlib.Path(DG.__file__).read_text(encoding="utf-8")
    body = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    assert body.count("GateVerdict(True") == 1, "a second ALLOW site appeared in the boundary"
    assert "allow=True" not in body, "the keyword form of ALLOW must not be used at all"
