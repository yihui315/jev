"""GATEWAY REFUSAL -- jcc_pause / jcc_steer / jcc_retry must refuse when any of
the five re-validation gates is absent or fails, naming the gate that stopped it.

The five gates (in order): capability, policy, signed_directive, human_approval,
budget. Each is asserted to produce a STRUCTURED refusal that names the gate.

The first four are reachable through the real tool path (with the earlier gates
satisfied). The budget gate sits behind the human-approval gate, whose directive
id is generated fresh inside the tool and therefore can never pre-exist in the
approval registry -- so budget is exercised directly through the gateway's real
evaluate() with a pre-approved directive.
"""
from __future__ import annotations

from jcc_core.schemas import Directive, DirectiveAction
from jcc_core.signing import DirectiveSigner
from operator_skill.gates import (
    CapabilityAuthority,
    Gateway,
    GatewayContext,
    GatewayRequest,
    PolicyAuthority,
    SignedDirectiveGate,
)
from operator_skill.refusals import (
    CODE_APPROVAL_MISSING,
    CODE_BUDGET_EXHAUSTED,
    CODE_CAPABILITY_DENIED,
    CODE_GATE_UNAVAILABLE,
    CODE_MALFORMED_SESSION_ID,
    CODE_POLICY_REFUSED,
)
from operator_skill.approvals import ApprovalRegistry

from ._helpers import (
    CONTROL_TOKEN,
    VALID_SESSION_ID,
    DirectiveSigner_or_none,
    GatewayContext,  # noqa: F401
    RouteBackend,
    evaluate_gateway,
    gateway_context,
    make_skill,
    operator_principal,
    read_ok,
    session_snapshot_for,
)


def _tool_level_skill(trace_path, ctx, snapshot_for_action=DirectiveAction.PAUSE):
    routes = {("GET", f"/api/sessions/{VALID_SESSION_ID}"): read_ok(
        session_snapshot_for(snapshot_for_action))}
    backend = RouteBackend(routes=routes, default=read_ok({}))
    return make_skill(
        backend, trace_path, control_token=CONTROL_TOKEN, gateway=Gateway(ctx)
    ), backend


def test_no_gateway_wired(trace_path) -> None:
    backend = RouteBackend(default=read_ok(session_snapshot_for(DirectiveAction.PAUSE)))
    skill = make_skill(backend, trace_path, control_token=CONTROL_TOKEN)  # gateway=None
    res = skill.call("jcc_pause", {"session_id": VALID_SESSION_ID},
                    principal=operator_principal())
    assert res.refusal is not None
    assert res.refusal.gate is None
    assert res.refusal.code == CODE_GATE_UNAVAILABLE
    assert backend.calls_for("POST", "/api/pause") == []


def test_capability_gate_absent(trace_path) -> None:
    ctx = gateway_context(
        capabilities=None,
        policy=PolicyAuthority(),
        signing=SignedDirectiveGate(DirectiveSigner_or_none(CONTROL_TOKEN.encode())),
    )
    skill, backend = _tool_level_skill(trace_path, ctx)
    res = skill.call("jcc_pause", {"session_id": VALID_SESSION_ID},
                    principal=operator_principal())
    assert res.refusal is not None
    assert res.refusal.gate == "capability"
    assert res.refusal.code == CODE_GATE_UNAVAILABLE
    assert backend.calls_for("POST", "/api/pause") == []


def test_policy_gate_absent(trace_path) -> None:
    ctx = gateway_context(
        capabilities=CapabilityAuthority(),
        policy=None,
        signing=SignedDirectiveGate(DirectiveSigner_or_none(CONTROL_TOKEN.encode())),
    )
    skill, backend = _tool_level_skill(trace_path, ctx)
    res = skill.call("jcc_pause", {"session_id": VALID_SESSION_ID},
                    principal=operator_principal())
    assert res.refusal is not None
    assert res.refusal.gate == "policy"
    assert res.refusal.code == CODE_GATE_UNAVAILABLE


def test_policy_refused_no_assessment(trace_path) -> None:
    # A session with no stored assessment cannot be authorised by policy.
    ctx = gateway_context(
        capabilities=CapabilityAuthority(),
        policy=PolicyAuthority(),
        signing=SignedDirectiveGate(DirectiveSigner_or_none(CONTROL_TOKEN.encode())),
    )
    snapshot_no_assessment = {"session_id": VALID_SESSION_ID, "status": "active",
                              "current_assessment": None}
    routes = {("GET", f"/api/sessions/{VALID_SESSION_ID}"): read_ok(snapshot_no_assessment)}
    backend = RouteBackend(routes=routes, default=read_ok({}))
    skill = make_skill(
        backend, trace_path, control_token=CONTROL_TOKEN, gateway=Gateway(ctx)
    )
    res = skill.call("jcc_pause", {"session_id": VALID_SESSION_ID},
                    principal=operator_principal())
    assert res.refusal is not None
    assert res.refusal.gate == "policy"
    assert res.refusal.code == CODE_POLICY_REFUSED


def test_signed_directive_gate_unavailable(trace_path) -> None:
    # Signer wired but UNAVAILABLE (no key) => the directive cannot be signed =>
    # the signed-directive gate refuses. Policy (PAUSE) must pass first.
    ctx = gateway_context(
        capabilities=CapabilityAuthority(),
        policy=PolicyAuthority(),
        signing=SignedDirectiveGate(DirectiveSigner_or_none(None)),
    )
    skill, backend = _tool_level_skill(trace_path, ctx)
    res = skill.call("jcc_pause", {"session_id": VALID_SESSION_ID},
                    principal=operator_principal())
    assert res.refusal is not None
    assert res.refusal.gate == "signed_directive"
    assert backend.calls_for("POST", "/api/pause") == []


def test_human_approval_missing(trace_path, approvals_path) -> None:
    # All earlier gates pass; the freshly-issued directive id is not (and cannot
    # be) pre-approved, so the human-approval gate refuses.
    ctx = gateway_context(
        capabilities=CapabilityAuthority(),
        policy=PolicyAuthority(),
        signing=SignedDirectiveGate(DirectiveSigner_or_none(CONTROL_TOKEN.encode())),
        approvals=ApprovalRegistry(str(approvals_path)),
    )
    skill, backend = _tool_level_skill(trace_path, ctx)
    res = skill.call("jcc_pause", {"session_id": VALID_SESSION_ID},
                    principal=operator_principal())
    assert res.refusal is not None
    assert res.refusal.gate == "human_approval"
    assert res.refusal.code == CODE_APPROVAL_MISSING
    assert backend.calls_for("POST", "/api/pause") == []


def test_budget_gate_absent_refuses(approvals_path) -> None:
    signer = DirectiveSigner(CONTROL_TOKEN.encode())
    registry = ApprovalRegistry(str(approvals_path))
    ctx = GatewayContext(
        capabilities=CapabilityAuthority(),
        policy=PolicyAuthority(),
        signing=SignedDirectiveGate(signer),
        approvals=registry,
        budget=None,  # budget gate NOT wired
    )
    gw = Gateway(ctx)
    snap = session_snapshot_for(DirectiveAction.PAUSE)
    request = GatewayRequest(
        tool="jcc_pause",
        session_id=VALID_SESSION_ID,
        requested_action=DirectiveAction.PAUSE,
        principal=operator_principal(),
        session_snapshot=snap,
        endpoint="/api/pause",
        arguments={"session_id": VALID_SESSION_ID},
    )
    directive = ctx.signing.issue(request, DirectiveAction.PAUSE)
    ctx.signing.sign(directive)
    registry.approve(directive.directive_id, VALID_SESSION_ID, "tester")
    request.directive = directive
    verdict = gw.evaluate(request)
    assert verdict.allowed is False
    assert verdict.gate == "budget"
    assert verdict.code == CODE_GATE_UNAVAILABLE


def test_budget_gate_exhausted_refuses(approvals_path) -> None:
    signer = DirectiveSigner(CONTROL_TOKEN.encode())
    registry = ApprovalRegistry(str(approvals_path))
    ctx = GatewayContext(
        capabilities=CapabilityAuthority(),
        policy=PolicyAuthority(),
        signing=SignedDirectiveGate(signer),
        approvals=registry,
        budget=_ExhaustedBudget(),
    )
    gw = Gateway(ctx)
    snap = session_snapshot_for(DirectiveAction.PAUSE)
    request = GatewayRequest(
        tool="jcc_pause",
        session_id=VALID_SESSION_ID,
        requested_action=DirectiveAction.PAUSE,
        principal=operator_principal(),
        session_snapshot=snap,
        endpoint="/api/pause",
        arguments={"session_id": VALID_SESSION_ID},
    )
    directive = ctx.signing.issue(request, DirectiveAction.PAUSE)
    ctx.signing.sign(directive)
    registry.approve(directive.directive_id, VALID_SESSION_ID, "tester")
    request.directive = directive
    verdict = gw.evaluate(request)
    assert verdict.allowed is False
    assert verdict.gate == "budget"
    assert verdict.code == CODE_BUDGET_EXHAUSTED


class _ExhaustedBudget:
    """Minimal stand-in for a real BudgetGovernor that is exhausted."""

    def check(self, session_id, *, kind="directive", units=0, action=None):
        return _Decision(False, code="BUDGET_EXHAUSTED")

    def snapshot(self, session_id):
        return _Decision(True)


class _Decision:
    def __init__(self, allowed, code=None):
        self.allowed = allowed
        self.code = code


def test_helper_assessment_authorises_intended_action() -> None:
    # Self-check: the fixture assessment really arbitrates to the intended action,
    # otherwise the policy-gate tests above would be testing the wrong thing.
    from ._helpers import assert_assessment_authorises

    for action in (DirectiveAction.PAUSE, DirectiveAction.STEER, DirectiveAction.RETRY):
        assert_assessment_authorises(action)
