"""Shared fixtures and factories for the S-01 operator-skill test suite.

Everything here is TEST INFRASTRUCTURE only -- it builds the real
``OperatorSkill`` / ``Gateway`` / ``Assessment`` objects and a deterministic
``RouteBackend`` that stands in for the network. The skill code under test is the
production code in ``packages/operator_skill``; ``RouteBackend`` is the injected
transport seam (it receives the exact request the skill builds), so the
request-building and refusal logic is exercised exactly.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from jcc_core.decision import ThresholdsConfig, arbitrate, normalize
from jcc_core.schemas import AgentName, Assessment, Directive, DirectiveAction
from operator_skill.approvals import ApprovalRegistry
from operator_skill.backends import (
    Backend,
    BackendResult,
    CONTROL_TOKEN_HEADER,
    RecordingBackend,
)
from operator_skill.gates import (
    CapabilityAuthority,
    Gateway,
    GatewayContext,
    GatewayRequest,
    PolicyAuthority,
    Principal,
    SignedDirectiveGate,
)
from operator_skill.manifest import (
    CAP_APPROVE,
    CAP_CONTROL,
    CAP_READ,
    OPERATOR_CAPABILITIES,
    OBSERVER_CAPABILITIES,
)
from operator_skill.skill import OperatorSkill

VALID_SESSION_ID = "sess-a1b2c3d4"
MALFORMED_SESSION_ID = "../../etc/passwd"
INJECTION_SESSION_ID = "sess-inject' OR '1'='1"

CONTROL_TOKEN = "operator-control-token-XYZ"
STALE_DIRECTIVE_KEY = b"jcc-test-signing-key"


# --------------------------------------------------------------------------- #
# Principals
# --------------------------------------------------------------------------- #
def observer_principal() -> Principal:
    """A read-only caller. Holds ONLY jcc:read -- cannot hold a control tool."""
    return Principal(name="observer", capabilities=frozenset(OBSERVER_CAPABILITIES))


def operator_principal() -> Principal:
    """An operator. Holds jcc:read + jcc:control but NOT jcc:approve."""
    return Principal(name="operator", capabilities=frozenset(OPERATOR_CAPABILITIES))


def approver_principal() -> Principal:
    return Principal(
        name="approver",
        capabilities=frozenset({CAP_READ, CAP_CONTROL, CAP_APPROVE}),
    )


# --------------------------------------------------------------------------- #
# Transport double: routes by (method, path), records every call
# --------------------------------------------------------------------------- #
class RouteBackend(RecordingBackend):
    """A deterministic backend that returns a canned response per (method, path).

    Unmatched calls fall through to the inherited queue/default. Every call is
    recorded so a test can assert exactly what the skill asked for -- and that no
    mutation endpoint was ever touched when a refusal was expected.
    """

    def __init__(
        self,
        routes: Optional[Dict[Tuple[str, str], BackendResult]] = None,
        *,
        default: Optional[BackendResult] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(default=default, **kwargs)
        self._routes = routes or {}

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        token: Optional[str] = None,
    ) -> BackendResult:
        self.calls.append(
            {
                "method": method,
                "path": path,
                "json_body": json_body,
                "token": token,
                "headers": {"Accept": "application/json"}
                | ({CONTROL_TOKEN_HEADER: token} if token else {}),
                "ts": self.clock(),
            }
        )
        hit = self._routes.get((method, path))
        if hit is not None:
            return hit
        if self._queue:
            return self._queue.pop(0)
        return self.default


def read_ok(payload: Any) -> BackendResult:
    return BackendResult(status=200, payload=payload)


# --------------------------------------------------------------------------- #
# Session snapshots + policy-authorising assessments
# --------------------------------------------------------------------------- #
def _signals_for(action: DirectiveAction) -> Dict[str, Any]:
    """A signal set that the deterministic kernel maps to exactly ``action``.

    The policy table (jcc_core.decision.ROWS) is the single authority; these
    values are chosen so no earlier row wins. The helper is self-checking via
    :func:`assert_assessment_authorises`.
    """
    signals = {
        "meaningful_progress": True,
        "worker_stuck": False,
        "requirements_satisfied": True,
        "needs_verification": False,
        "ready_to_finish": False,
        "needs_human": False,
    }
    if action is DirectiveAction.PAUSE:
        signals["worker_stuck"] = True
    elif action is DirectiveAction.STEER:
        signals["meaningful_progress"] = False
    elif action is DirectiveAction.RETRY:
        signals["requirements_satisfied"] = False
    return signals


def assessment_for(action: DirectiveAction) -> Assessment:
    return Assessment(
        agent=AgentName.GENERIC,
        session_id=VALID_SESSION_ID,
        timestamp=1.0,
        risk_score=0.0,
        band="low",
        provider_verified=True,
        raw={"signals": _signals_for(action), "evidence_complete": True},
    )


def assessment_dict_for(action: DirectiveAction) -> Dict[str, Any]:
    return assessment_for(action).model_dump(mode="json")


def session_snapshot_for(action: DirectiveAction) -> Dict[str, Any]:
    """A session row whose stored assessment authorises ``action``."""
    return {
        "session_id": VALID_SESSION_ID,
        "status": "active",
        "current_assessment": assessment_dict_for(action),
        "current_directive": {
            "directive_id": "dir-existing",
            "action": action.value,
            "signature": None,
        },
    }


def assert_assessment_authorises(action: DirectiveAction) -> None:
    """Self-check: the helper assessment actually arbitrates to ``action``."""
    a = assessment_for(action)
    result = arbitrate(normalize(a), ThresholdsConfig(), assessment=a)
    assert result.action is action, (
        f"helper assessment for {action.value} arbitrated to "
        f"{result.action.value} (reason {result.reason_code}); fix _signals_for"
    )


# --------------------------------------------------------------------------- #
# Skill + gateway factories
# --------------------------------------------------------------------------- #
def make_skill(
    backend: Backend,
    trace_path: Path,
    *,
    control_token: Optional[str] = None,
    gateway: Optional[Gateway] = None,
    approvals: Optional[ApprovalRegistry] = None,
) -> OperatorSkill:
    return OperatorSkill(
        backend,
        trace_path=str(trace_path),
        gateway=gateway,
        approvals=approvals,
        control_token=control_token,
        clock=lambda: 1_700_000_000.0,
    )


def gateway_context(
    *,
    capabilities: Optional[CapabilityAuthority] = None,
    policy: Optional[PolicyAuthority] = None,
    signing: Optional[SignedDirectiveGate] = None,
    approvals: Optional[ApprovalRegistry] = None,
    budget: Any = None,
    capability_map: Optional[Dict[str, frozenset]] = None,
) -> GatewayContext:
    return GatewayContext(
        capabilities=capabilities,
        policy=policy,
        signing=signing,
        approvals=approvals,
        budget=budget,
        capability_map=capability_map,
    )


def fully_wired_context(
    *,
    signer_key: Optional[bytes] = STALE_DIRECTIVE_KEY,
    approvals: Optional[ApprovalRegistry] = None,
    budget: Any = None,
) -> GatewayContext:
    """All five gates present. ``signer_key=None`` => the signing gate is wired
    but UNAVAILABLE (so a directive cannot be signed -> UNSIGNED)."""
    return GatewayContext(
        capabilities=CapabilityAuthority(),
        policy=PolicyAuthority(),
        signing=SignedDirectiveGate(DirectiveSigner_or_none(signer_key)),
        approvals=approvals,
        budget=budget,
    )


def DirectiveSigner_or_none(key: Optional[bytes]):
    from jcc_core.signing import DirectiveSigner

    return DirectiveSigner(key) if key is not None else DirectiveSigner(None)


# --------------------------------------------------------------------------- #
# Fake budget governor (the .check interface the gateway actually calls)
# --------------------------------------------------------------------------- #
@dataclass
class _BudgetDecision:
    allowed: bool
    code: Optional[str] = None
    exhausted_code: Optional[str] = None


class FakeBudget:
    def __init__(self, *, exhausted: bool = False) -> None:
        self.exhausted = exhausted

    def check(self, session_id: str, *, kind: str = "directive", units: float = 0,
              action: Any = None) -> _BudgetDecision:
        if self.exhausted:
            return _BudgetDecision(False, code="BUDGET_EXHAUSTED",
                                  exhausted_code="BUDGET_EXHAUSTED")
        return _BudgetDecision(True, code=None, exhausted_code=None)

    def snapshot(self, session_id: str) -> Any:
        return _BudgetDecision(True)


# --------------------------------------------------------------------------- #
# Direct gateway evaluation helper (bypasses the HTTP seam)
# --------------------------------------------------------------------------- #
def evaluate_gateway(
    action: DirectiveAction,
    *,
    approvals_path: Path,
    budget_exhausted: bool = False,
    expired_directive: bool = False,
    session_snapshot: Optional[Dict[str, Any]] = None,
) -> Any:
    """Run the real Gateway.evaluate for ``action`` with all gates reachable.

    For the default path the directive is issued, signed and pre-approved so the
    run reaches the budget gate. Set ``expired_directive`` to feed a stale,
    already-signed directive (gate 3 should refuse with EXPIRED). Set
    ``budget_exhausted`` to make gate 5 refuse.
    """
    signer = DirectiveSigner_or_none(STALE_DIRECTIVE_KEY)
    registry = ApprovalRegistry(str(approvals_path))
    ctx = GatewayContext(
        capabilities=CapabilityAuthority(),
        policy=PolicyAuthority(),
        signing=SignedDirectiveGate(signer),
        approvals=registry,
        budget=FakeBudget(exhausted=budget_exhausted),
    )
    gw = Gateway(ctx)
    principal = operator_principal()
    snap = session_snapshot or session_snapshot_for(action)
    request = GatewayRequest(
        tool="jcc_" + action.value.lower(),
        session_id=VALID_SESSION_ID,
        requested_action=action,
        principal=principal,
        session_snapshot=snap,
        endpoint="/api/" + action.value.lower(),
        arguments={"session_id": VALID_SESSION_ID},
    )
    if expired_directive:
        directive = Directive(
            directive_id="dir-stale",
            session_id=VALID_SESSION_ID,
            timestamp=1.0,
            action=action,
            target_actuator="noop",
            params={"source": "test"},
            issued_at=1.0,
            ttl_s=10.0,
            nonce="nonce-stale",
            executable=False,
            requires_human=True,
            shadow=True,
        )
        # DirectiveSigner.sign is a pure helper (it does not mutate the directive),
        # so attach the signature explicitly before handing the stale directive to
        # the gateway -- otherwise check 1 (signed present) fails first.
        directive.signature = signer.sign(directive)
        directive.signed = True
        request.directive = directive
    else:
        directive = ctx.signing.issue(request, action)
        ctx.signing.sign(directive)
        registry.approve(directive.directive_id, VALID_SESSION_ID, "tester")
        request.directive = directive
    return gw.evaluate(request)
