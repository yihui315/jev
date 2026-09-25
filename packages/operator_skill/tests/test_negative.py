"""NEGATIVE -- missing token, wrong token, malformed id, stale directive, budget.

Each negative case must resolve to a STRUCTURED refusal (a value, never an
exception; never a silent success). The two that cannot be reached through the
tool's random-id flow (stale directive, budget) are exercised through the
gateway's real refusal machinery -- the same kernels the tool delegates to.
"""
from __future__ import annotations

from jcc_core.schemas import Directive, DirectiveAction
from jcc_core.signing import REASON_EXPIRED, DirectiveSigner, verify_directive
from operator_skill.backends import BackendResult
from operator_skill.refusals import (
    CODE_BUDGET_EXHAUSTED,
    CODE_GATE_UNAVAILABLE,
    CODE_INVALID_CONTROL_TOKEN,
    CODE_MALFORMED_SESSION_ID,
    CODE_MISSING_CONTROL_TOKEN,
    Refusal,
)

from ._helpers import (
    CONTROL_TOKEN,
    MALFORMED_SESSION_ID,
    STALE_DIRECTIVE_KEY,
    VALID_SESSION_ID,
    DirectiveSigner_or_none,
    RouteBackend,
    evaluate_gateway,
    make_skill,
    read_ok,
    session_snapshot_for,
)


def test_missing_token_refused(trace_path) -> None:
    backend = RouteBackend(default=read_ok({}))
    skill = make_skill(backend, trace_path)  # no control token configured
    res = skill.call("jcc_assess", {"session_id": VALID_SESSION_ID, "payload": {}})
    assert isinstance(res.refusal, Refusal)
    assert res.refusal.code == CODE_MISSING_CONTROL_TOKEN
    assert res.ok is False and res.outcome["status"] == "refused"
    # A read tool still works without a token (open surface, by design).
    assert skill.call("jcc_status").ok is True


def test_gateway_tool_refuses_without_token(trace_path) -> None:
    backend = RouteBackend(default=read_ok(session_snapshot_for(DirectiveAction.PAUSE)))
    skill = make_skill(backend, trace_path)  # no token; gateway not wired
    res = skill.call("jcc_pause", {"session_id": VALID_SESSION_ID})
    assert res.refusal is not None and res.refusal.code == CODE_MISSING_CONTROL_TOKEN
    # Refused before any request is issued to the control plane.
    assert backend.call_count() == 0


def test_wrong_token_refused(trace_path) -> None:
    # The real control plane answers 403 to a bad/missing token (fail-closed).
    backend = RouteBackend(
        routes={
            ("POST", "/api/assess"): BackendResult(
                status=403, payload={"detail": "Invalid or missing control token"}
            )
        }
    )
    skill = make_skill(backend, trace_path, control_token=CONTROL_TOKEN)
    res = skill.call("jcc_assess", {"session_id": VALID_SESSION_ID, "payload": {"x": 1}})
    assert isinstance(res.refusal, Refusal)
    assert res.refusal.code == CODE_INVALID_CONTROL_TOKEN
    assert res.ok is False


def test_malformed_session_id_refused(trace_path) -> None:
    backend = RouteBackend(default=read_ok({}))
    skill = make_skill(backend, trace_path)
    res = skill.call("jcc_session", {"session_id": MALFORMED_SESSION_ID})
    assert res.refusal is not None
    assert res.refusal.code == CODE_MALFORMED_SESSION_ID
    # The hostile id is refused BEFORE any request is built/interpolated.
    assert backend.call_count() == 0


def test_expired_directive_refused_by_kernel() -> None:
    # The signed-directive kernel is the authority: a stale directive fails.
    signer = DirectiveSigner_or_none(STALE_DIRECTIVE_KEY)
    directive = Directive(
        directive_id="dir-stale",
        session_id=VALID_SESSION_ID,
        timestamp=1.0,
        action=DirectiveAction.PAUSE,
        target_actuator="noop",
        params={"source": "test"},
        issued_at=1.0,
        ttl_s=10.0,
        nonce="nonce-stale",
        executable=False,
        requires_human=True,
        shadow=True,
    )
    directive.signature = signer.sign(directive)  # DirectiveSigner.sign is pure; attach it
    directive.signed = True
    ok, reason = verify_directive(directive, signer.key, now=1000.0)
    assert ok is False and reason == REASON_EXPIRED


def test_expired_directive_refused_by_gateway(approvals_path) -> None:
    verdict = evaluate_gateway(
        DirectiveAction.PAUSE, approvals_path=approvals_path, expired_directive=True
    )
    assert verdict.allowed is False
    assert verdict.gate == "signed_directive"
    assert verdict.code == REASON_EXPIRED


def test_budget_exceeded_refused_by_gateway(approvals_path) -> None:
    verdict = evaluate_gateway(
        DirectiveAction.PAUSE, approvals_path=approvals_path, budget_exhausted=True
    )
    assert verdict.allowed is False
    assert verdict.gate == "budget"
    assert verdict.code == CODE_BUDGET_EXHAUSTED


def test_no_negative_scenario_escapes_as_exception(trace_path) -> None:
    # If any refusal path raised instead of returning a value, this would error.
    backend = RouteBackend(default=read_ok({}))
    skill = make_skill(backend, trace_path, control_token=CONTROL_TOKEN)
    scenarios = [
        # jcc_assess with a malformed session id is refused at argument binding.
        lambda: skill.call("jcc_assess", {"session_id": MALFORMED_SESSION_ID, "payload": {}}),
        lambda: skill.call("jcc_session", {"session_id": MALFORMED_SESSION_ID}),
        # jcc_pause with no gateway wired is refused at the gateway gate.
        lambda: skill.call("jcc_pause", {"session_id": VALID_SESSION_ID}),
    ]
    for scenario in scenarios:
        res = scenario()
        assert isinstance(res, object)
        assert isinstance(res.refusal, Refusal)
        assert res.refusal.as_dict()["refused"] is True
