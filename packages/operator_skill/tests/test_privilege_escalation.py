"""PRIVILEGE ESCALATION -- a read-only caller must not reach a mutation path by
passing a mutated argument.

Two enforced mechanisms defend this:
  1. Strict argument binding: a read-only tool refuses any argument it did not
     declare, so a mutation argument cannot be smuggled into a read.
  2. The capability gate: a principal that does not hold the control capability
     is refused before any request is issued.

A read-only principal is denied at the capability gate; an operator principal is
still blocked by binding if it tries to mutate through a read tool.
"""
from __future__ import annotations

from jcc_core.schemas import DirectiveAction
from operator_skill.gates import (
    CapabilityAuthority,
    Gateway,
    PolicyAuthority,
    SignedDirectiveGate,
)
from operator_skill.refusals import (
    CODE_CAPABILITY_DENIED,
    CODE_GATE_UNAVAILABLE,
    CODE_MALFORMED_ARGUMENT,
)

from ._helpers import (
    CONTROL_TOKEN,
    VALID_SESSION_ID,
    DirectiveSigner_or_none,
    GatewayContext,
    RouteBackend,
    gateway_context,
    make_skill,
    observer_principal,
    operator_principal,
    read_ok,
    session_snapshot_for,
)


def _present_context() -> GatewayContext:
    """All five gates present (signer wired & available)."""
    return gateway_context(
        capabilities=CapabilityAuthority(),
        policy=PolicyAuthority(),
        signing=SignedDirectiveGate(DirectiveSigner_or_none(CONTROL_TOKEN.encode())),
    )


def _wired_skill(trace_path, ctx, snapshot):
    routes = {("GET", f"/api/sessions/{VALID_SESSION_ID}"): read_ok(snapshot)}
    backend = RouteBackend(routes=routes, default=read_ok({}))
    skill = make_skill(
        backend, trace_path, control_token=CONTROL_TOKEN, gateway=Gateway(ctx)
    )
    return skill, backend


def test_read_tool_rejects_smuggled_mutation_arg(trace_path) -> None:
    backend = RouteBackend(
        routes={
            ("GET", f"/api/sessions/{VALID_SESSION_ID}"): read_ok(
                session_snapshot_for(DirectiveAction.PAUSE)
            )
        }
    )
    skill = make_skill(backend, trace_path)
    res = skill.call(
        "jcc_session",
        {"session_id": VALID_SESSION_ID, "action": "pause", "tool": "jcc_pause"},
    )
    assert res.refusal is not None
    assert res.refusal.code == CODE_MALFORMED_ARGUMENT
    assert backend.calls_for("POST", "/api/pause") == []


def test_read_only_principal_cannot_call_gateway_tool(trace_path) -> None:
    # Default principal is read-only (observer). Calling a control tool is denied
    # at the capability gate, before any request is issued.
    skill, backend = _wired_skill(trace_path, _present_context(),
                                  session_snapshot_for(DirectiveAction.PAUSE))
    res = skill.call("jcc_pause", {"session_id": VALID_SESSION_ID})  # no principal -> observer
    assert res.refusal is not None
    assert res.refusal.code == CODE_CAPABILITY_DENIED
    assert res.refusal.gate == "capability"
    assert backend.calls_for("POST", "/api/pause") == []


def test_read_only_principal_explicit(trace_path) -> None:
    skill, backend = _wired_skill(trace_path, _present_context(),
                                  session_snapshot_for(DirectiveAction.STEER))
    res = skill.call(
        "jcc_steer", {"session_id": VALID_SESSION_ID}, principal=observer_principal()
    )
    assert res.refusal is not None
    assert res.refusal.code == CODE_CAPABILITY_DENIED
    assert res.refusal.gate == "capability"
    assert backend.calls_for("POST", "/api/steer") == []


def test_operator_principal_cannot_make_read_tool_mutate(trace_path) -> None:
    # Even an operator cannot convert a read tool into a mutation via arguments.
    backend = RouteBackend(
        routes={
            ("GET", f"/api/sessions/{VALID_SESSION_ID}"): read_ok(
                session_snapshot_for(DirectiveAction.PAUSE)
            )
        }
    )
    skill = make_skill(backend, trace_path, control_token=CONTROL_TOKEN)
    res = skill.call(
        "jcc_session",
        {"session_id": VALID_SESSION_ID, "action": "steer"},
        principal=operator_principal(),
    )
    assert res.refusal is not None
    assert res.refusal.code == CODE_MALFORMED_ARGUMENT
    assert backend.calls_for("POST") == []


def test_capability_gate_absent_refuses(trace_path) -> None:
    # The capability gate itself not wired => the request cannot be evaluated.
    ctx = gateway_context(
        capabilities=None,
        policy=PolicyAuthority(),
        signing=SignedDirectiveGate(DirectiveSigner_or_none(CONTROL_TOKEN.encode())),
    )
    skill, backend = _wired_skill(trace_path, ctx, session_snapshot_for(DirectiveAction.PAUSE))
    res = skill.call("jcc_pause", {"session_id": VALID_SESSION_ID},
                    principal=operator_principal())
    assert res.refusal is not None
    assert res.refusal.gate == "capability"
    assert res.refusal.code == CODE_GATE_UNAVAILABLE
