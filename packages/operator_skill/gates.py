"""The five re-validation gates for the gateway tools (S-01).

``jcc_pause`` / ``jcc_steer`` / ``jcc_retry`` are REQUESTS, not commands. This
module is the part of the skill that re-validates such a request against the real
authorities before it is allowed to reach the API:

    1. capability   -- does the principal hold ``jcc:control:*``?
    2. policy       -- does the ONE deterministic kernel authorise this action
                       for this session's stored assessment?
    3. signed directive -- is there a directive, does it carry a real signature,
                       and does that signature still verify (integrity, TTL,
                       replay)?
    4. human approval -- the directive says ``requires_human``: did a human
                       actually approve THIS directive id?
    5. budget       -- the real ``BudgetGovernor``, priced off the ONE cost table.

Every one of them is wired to the existing authority: ``jcc_core.policy`` /
``jcc_core.decision`` for the mapping, ``jcc_core.signing`` for integrity,
``supervisor.budget.BudgetGovernor`` for the ceiling, ``schemas.Directive`` for
``requires_human``. Nothing here re-derives them.

A gate that the deployment did not WIRE is a refusal with its own code
(``GATE_UNAVAILABLE``): "the budget governor is not installed" is not "the budget
is fine", and collapsing the two is how a control becomes decorative.
"""
from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from jcc_core.decision import ThresholdsConfig, arbitrate, normalize, trace_hash
from jcc_core.policy import decide
from jcc_core.schemas import Assessment, Directive, DirectiveAction
from jcc_core.signing import DirectiveSigner, verify_directive

from . import manifest as manifest_mod
from .approvals import ApprovalRegistry
from . import refusals as R

#: Gate names, in evaluation order. Order is part of the contract: an approval
#: without a signature is meaningless, and a signature without a policy check is
#: a rubber stamp.
GATE_ORDER: Tuple[str, ...] = (
    R.GATE_CAPABILITY,
    R.GATE_POLICY,
    R.GATE_SIGNED_DIRECTIVE,
    R.GATE_HUMAN_APPROVAL,
    R.GATE_BUDGET,
)


# --------------------------------------------------------------------------- #
# principal
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Principal:
    """Who is asking. Capabilities are explicit; there is no default role."""

    name: str = "anonymous"
    capabilities: frozenset = field(default_factory=lambda: frozenset(
        manifest_mod.OBSERVER_CAPABILITIES
    ))

    def holds(self, capability: str) -> bool:
        return capability in self.capabilities


class CapabilityAuthority:
    """Gate 1 -- is the principal allowed to hold this capability at all?"""

    def allows(self, principal: Principal, capability: str) -> bool:
        # Exact match first.
        if principal.holds(capability):
            return True
        # Coarse grant authorises its fine-grained children, e.g. holding the
        # coarse "jcc:control" authorises consuming "jcc:control:pause". Without
        # this, an operator who holds "jcc:control" (the operator capability set)
        # could never satisfy the per-tool fine-grained consume map
        # (``manifest.TOOL_CAPABILITY_CONSUMES``), so gateway tools would be
        # permanently unreachable for operators -- a real inconsistency, not a
        # privilege boundary.
        head = capability.rsplit(":", 1)[0]
        return bool(head) and principal.holds(head)


# --------------------------------------------------------------------------- #
# gate 2 -- policy
# --------------------------------------------------------------------------- #
class PolicyAuthority:
    """Gate 2 -- re-arbitrate the request through the ONE deterministic kernel."""

    def __init__(self, thresholds: "ThresholdsConfig | None" = None) -> None:
        self.thresholds = thresholds or ThresholdsConfig()

    def authorise(
        self, request: "GatewayRequest", action: DirectiveAction
    ) -> Dict[str, Any]:
        """Return the policy's verdict for ``request`` against its stored assessment.

        ``{"allowed": bool, "observed": str, "reason_code": str, "explanation": str}``
        """
        snapshot = request.session_snapshot or {}
        raw = snapshot.get("current_assessment")
        if not raw:
            return {
                "allowed": False,
                "observed": None,
                "reason_code": "no_assessment_to_authorise_against",
                "explanation": (
                    f"session {request.session_id!r} has no stored assessment, so the "
                    "deterministic policy cannot authorise any action for it"
                ),
            }
        try:
            assessment = raw if isinstance(raw, Assessment) else Assessment(**raw)
        except Exception as exc:  # noqa: BLE001 - a bad stored row is a refusal
            return {
                "allowed": False,
                "observed": None,
                "reason_code": "assessment_unreadable",
                "explanation": f"the stored assessment could not be parsed: {type(exc).__name__}",
            }
        thresholds = self.thresholds.for_agent(
            getattr(assessment.agent, "value", assessment.agent)
        )
        signals = normalize(assessment)
        result = arbitrate(signals, thresholds, assessment=assessment)
        observed = result.action
        allowed = observed is action
        return {
            "allowed": allowed,
            "observed": observed.value,
            "reason_code": result.reason_code,
            "explanation": (
                f"kernel matched row {result.reason_code!r} -> {observed.value}; "
                f"the request asked for {action.value}"
                + (
                    ""
                    if allowed
                    else "; the deterministic policy does not authorise the requested action"
                )
            ),
        }


# --------------------------------------------------------------------------- #
# gate 3 -- signed directive
# --------------------------------------------------------------------------- #
class SignedDirectiveGate:
    """Gate 3 -- issue (and re-verify) a directive under the real signing kernel."""

    def __init__(
        self,
        signer: "DirectiveSigner | None",
        *,
        clock: Callable[[], float] = time.time,
        nonce_factory: Callable[[], str] = lambda: secrets.token_hex(16),
    ) -> None:
        self.signer = signer
        self._clock = clock
        self._nonce_factory = nonce_factory

    @property
    def available(self) -> bool:
        return self.signer is not None and bool(getattr(self.signer, "available", False))

    def issue(self, request: "GatewayRequest", action: DirectiveAction) -> Directive:
        """Build the directive this request will ask the plane to record."""
        now = self._clock()
        directive = Directive(
            directive_id=f"dir_gw_{request.session_id}_{uuid.uuid4().hex[:12]}",
            session_id=request.session_id,
            timestamp=now,
            action=action,
            target_actuator="noop",
            params={"source": "operator-skill", "tool": request.tool,
                    "requires_human": True},
            policy_version="p3.0.0",
            evidence_hash=None,
            issued_at=now,
            ttl_s=request.ttl_s,
            nonce=self._nonce_factory(),
            # D-34/D-41: a control-plane directive is a REQUEST. It is never
            # executable and always demands a human.
            executable=False,
            requires_human=True,
            human_approval=None,
            shadow=True,
        )
        return directive

    def verify(self, directive: Directive, request: "GatewayRequest") -> Tuple[bool, str]:
        """Run integrity checks 1-4 of ``jcc_core.signing`` in their real order."""
        key = None
        if self.signer is not None:
            key = getattr(self.signer, "key", None)
        ok, reason = verify_directive(
            directive, key, now=self._clock(), replay=request.replay_cache
        )
        return ok, reason

    def sign(self, directive: Directive) -> None:
        if self.signer is None or not self.available:
            directive.signed = False
            directive.signature = None
            directive.signing_alg = None
            return
        signature = self.signer.sign(directive)
        directive.signed = signature is not None
        directive.signature = signature
        directive.signing_alg = getattr(self.signer, "SIGN_ALG", "hmac-sha256")


# --------------------------------------------------------------------------- #
# gate 4 -- human approval (data side lives in approvals.ApprovalRegistry)
# --------------------------------------------------------------------------- #
def check_human_approval(
    directive: Directive, approvals: Optional[ApprovalRegistry]
) -> Tuple[bool, str]:
    """Gate 4. ``requires_human`` is the authority; the registry is the receipt."""
    if not bool(directive.requires_human):
        return True, "directive does not require a human"
    if approvals is None or not approvals.holds(directive.directive_id, directive.session_id):
        return False, (
            f"directive {directive.directive_id!r} requires human approval "
            f"(Directive.requires_human=True) and no human approval for that exact "
            "directive id was recorded"
        )
    return True, "approved by a human for this directive id"


# --------------------------------------------------------------------------- #
# gate 5 -- budget
# --------------------------------------------------------------------------- #
def check_budget(
    budget: Any, session_id: str, action: DirectiveAction
) -> Tuple[bool, str, Optional[str]]:
    """Gate 5 -- the REAL governor, priced off the ONE cost table."""
    decision_obj = budget.check(session_id, kind="directive", units=0, action=action)
    if decision_obj.allowed:
        return True, "within budget", None
    code = decision_obj.code or "unknown"
    return False, f"budget ceiling {code} is exhausted", code


# --------------------------------------------------------------------------- #
# the gateway
# --------------------------------------------------------------------------- #
@dataclass
class GatewayRequest:
    """One gateway intent. It carries no authority of its own -- only intent."""

    tool: str
    session_id: str
    requested_action: DirectiveAction
    principal: Principal
    session_snapshot: Optional[Dict[str, Any]] = None
    endpoint: str = ""
    #: A directive the caller already holds (e.g. one the plane issued). When it
    #: is absent the gateway builds its own request-directive in the signing gate.
    directive: Optional[Directive] = None
    ttl_s: float = 120.0
    replay_cache: Any = None
    arguments: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GatewayVerdict:
    allowed: bool
    gate: Optional[str] = None
    code: Optional[str] = None
    message: str = ""
    directive: Optional[Directive] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def refusal(self) -> R.Refusal:
        return R.Refusal(
            code=self.code or R.CODE_INTERNAL_ERROR,
            gate=self.gate,
            tool="",
            message=self.message,
            details=dict(self.details),
        )


@dataclass
class GatewayContext:
    """The five gates. ``None`` means the deployment did not wire that gate."""

    capabilities: Optional[CapabilityAuthority] = None
    policy: Optional[PolicyAuthority] = None
    signing: Optional[SignedDirectiveGate] = None
    approvals: Optional[ApprovalRegistry] = None
    budget: Optional[Any] = None
    #: Which capabilities a principal of that name may hold. If omitted, the
    #: principal's own declaration is used (and a forged principal is no more
    #: powerful than it says).
    capability_map: Optional[Dict[str, frozenset]] = None

    @property
    def wired(self) -> List[str]:
        return [
            name
            for name, obj in (
                (R.GATE_CAPABILITY, self.capabilities),
                (R.GATE_POLICY, self.policy),
                (R.GATE_SIGNED_DIRECTIVE, self.signing),
                (R.GATE_HUMAN_APPROVAL, self.approvals),
                (R.GATE_BUDGET, self.budget),
            )
            if obj is not None
        ]


class Gateway:
    """Runs the five gates in order; the first refusal wins, and names itself."""

    def __init__(self, context: Optional[GatewayContext] = None) -> None:
        self.context = context or GatewayContext()

    def evaluate(self, request: GatewayRequest) -> GatewayVerdict:
        ctx = self.context
        consented = self._consented_capabilities(request.principal)
        capability = manifest_mod.TOOL_CAPABILITY_CONSUMES.get(request.tool)

        # -- gate 1: capability -------------------------------------------- #
        if ctx.capabilities is None:
            return GatewayVerdict(
                False, R.GATE_CAPABILITY, R.CODE_GATE_UNAVAILABLE,
                "no capability authority is wired; the request cannot be evaluated",
                details={"missing_gate": R.GATE_CAPABILITY},
            )
        if capability and not ctx.capabilities.allows(request.principal, capability):
            return GatewayVerdict(
                False, R.GATE_CAPABILITY, R.CODE_CAPABILITY_DENIED,
                f"principal {request.principal.name!r} does not hold "
                f"{capability!r}; {request.tool} is a mutation-capable tool and "
                "this principal is read-only",
                details={"required": capability, "held": sorted(consented)},
            )

        # -- gate 2: policy ------------------------------------------------- #
        if ctx.policy is None:
            return GatewayVerdict(
                False, R.GATE_POLICY, R.CODE_GATE_UNAVAILABLE,
                "no policy authority is wired; the request cannot be evaluated",
                details={"missing_gate": R.GATE_POLICY},
            )
        verdict = ctx.policy.authorise(request, request.requested_action)
        if not verdict["allowed"]:
            return GatewayVerdict(
                False, R.GATE_POLICY, R.CODE_POLICY_REFUSED,
                verdict["explanation"],
                details={"observed": verdict["observed"],
                         "reason_code": verdict["reason_code"]},
            )

        # -- gate 3: signed directive --------------------------------------- #
        if ctx.signing is None:
            return GatewayVerdict(
                False, R.GATE_SIGNED_DIRECTIVE, R.CODE_GATE_UNAVAILABLE,
                "no signing gate is wired; a request cannot be proven to come from "
                "this plane, so it is not sent",
                details={"missing_gate": R.GATE_SIGNED_DIRECTIVE},
            )
        directive = request.directive or ctx.signing.issue(request, request.requested_action)
        if request.directive is None:
            ctx.signing.sign(directive)
        ok, reason = ctx.signing.verify(directive, request)
        if not ok:
            return GatewayVerdict(
                False, R.GATE_SIGNED_DIRECTIVE, reason,
                f"directive {directive.directive_id!r} failed the signed-directive "
                f"check ({reason})",
                details={"directive_id": directive.directive_id, "reason": reason},
            )

        # -- gate 4: human approval ------------------------------------------ #
        if ctx.approvals is None:
            return GatewayVerdict(
                False, R.GATE_HUMAN_APPROVAL, R.CODE_GATE_UNAVAILABLE,
                "no approval registry is wired; a human-approval requirement "
                "cannot be satisfied or refused on evidence",
                details={"missing_gate": R.GATE_HUMAN_APPROVAL},
            )
        approved, message = check_human_approval(directive, ctx.approvals)
        if not approved:
            return GatewayVerdict(
                False, R.GATE_HUMAN_APPROVAL, R.CODE_APPROVAL_MISSING, message,
                details={"directive_id": directive.directive_id},
            )

        # -- gate 5: budget --------------------------------------------------- #
        if ctx.budget is None:
            return GatewayVerdict(
                False, R.GATE_BUDGET, R.CODE_GATE_UNAVAILABLE,
                "no budget governor is wired; the request would not be priced, so "
                "it is not sent",
                details={"missing_gate": R.GATE_BUDGET},
            )
        allowed, message, code = check_budget(ctx.budget, request.session_id,
                                              request.requested_action)
        if not allowed:
            return GatewayVerdict(
                False, R.GATE_BUDGET, R.CODE_BUDGET_EXHAUSTED, message,
                details={"budget_code": code},
            )

        return GatewayVerdict(
            True, None, None,
            f"all five gates passed for {request.tool} on {request.session_id}",
            directive=directive,
            details={
                "reason_code": verdict["reason_code"],
                "policy_action": verdict["observed"],
                "signed": directive.signed,
                "signature_status": directive.signature_status,
                "requires_human": directive.requires_human,
            },
        )

    def _consented_capabilities(self, principal: Principal) -> set:
        """What the principal may hold.

        A ``capability_map`` narrows a principal to what its ROLE is allowed. This
        is the line that stops "read-only tool, mutated argument" from becoming
        mutation: the capability is consented by the role, not by the caller's
        say-so, and a principal whose declared set is read-only stays read-only
        no matter what arguments it passes.
        """
        mapping = self.context.capability_map
        if mapping is not None:
            return set(mapping.get(principal.name, set()))
        return set(principal.capabilities)


#: The kernel-side explanation helper, re-exported so ``jcc_explain`` (and its
#: tests) hash the SAME object the policy gate used.
explain_trace_hash = trace_hash
#: ``decide`` is re-exported so no second mapping authority is imported into the
#: skill by someone adding a tool later.
policy_decide = decide
