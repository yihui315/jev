"""The effect boundary: nine ordered checks, ANY failure ⇒ DENY (v1.1 §17).

WHAT THIS IS, AND WHAT IT IS NOT
--------------------------------
``jcc_core.signing.verify_directive`` decides the first four checks (integrity,
TTL/clock-skew, replay) and its docstring is explicit that it "cannot authorise
anything". This module is the other half: the boundary that turns a *verified*
directive into an *authorised* one, or refuses it. It is deliberately the only
place in the supervisor plane where the word ALLOW is produced, because the
project's whole safety story rests on there being exactly one such place.

The nine checks, in the mandated order (``signing.VERIFICATION_CHECK_ORDER``),
first failure wins, and the answer is DENY unless all nine pass::

    1 signed_present   the directive claims to be signed and carries the parts
    2 signature        the bytes are untouched under the REQUIRED form (v2)
    3 ttl              not expired; not issued in the future beyond clock skew
    4 replay           the nonce is unseen
    5 session          the directive's session IS the session being actuated
    6 capability       the actuation rung in force grants this action
    7 policy           policy_version AND policy_table_version match current
    8 budget           the budget governor allows one more directive
    9 approval         a human approval exists AND is covered by the signature

Checks 1–4 are NOT re-implemented. They are delegated to the kernel, which owns
them and their reason vocabulary; duplicating the comparisons here would give the
project two definitions of "the signature is valid", and the second one would be
the one that drifts.

THREE-STATE CHECK REPORTING
---------------------------
Each check reports ``ok`` as ``True`` / ``False`` / ``None``. ``None`` means
**not reached** — the run stopped at an earlier check. It is never collapsed into
``True``. The project has already paid for this lesson twice (a probe that could
not query a thing wrote "it does not exist"; a regex that silently matched fewer
cases than it should have) and the rule generalises: *a check that was not
performed must not be reported as a check that passed.* A reader of the verdict
can therefore tell "approved, and here is the evidence for each of nine rules"
apart from "refused at rule 2, and rules 3–9 never ran".

WHY THE SIGNING FORM IS A CONSTANT HERE
---------------------------------------
Every kernel call below passes ``form=SIGNING_FORM_V2``. This is not
configurable, not read from the directive, and not a parameter of this module,
because a configurable form here would be a **downgrade switch**: at ``v1`` the
``human_approval`` field is outside the signed bytes, so rule 9 could be
satisfied by writing an approval into a directive that was never approved. The
cost of fixing it at the call site instead is that a v1 signature is REFUSED
here — including, today, the ones ``Sentinel`` produces, because ``run.py``
builds its signer with the v1 default. That refusal is the intended coupling and
it is safe: while the live-shadow gate is pending the effective rung is ``L0``
and this gate is not reached at all (see ``live_shadow_gate``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from jcc_core import signing as signing_kernel
from jcc_core.nonce_cache import ReplayCache

from . import actuation_levels as AL

#: ``ok=None`` reason: the check was never evaluated because an earlier one
#: failed. Frozen string so a UI or a report can test for it by identity.
REASON_NOT_REACHED = "not_reached"

#: The eleven fields a signed directive must carry (§17). ``scope`` and
#: ``expires_at`` are DERIVED — ``scope`` falls back to the actuator and
#: ``expires_at`` is ``issued_at + ttl_s`` — so this list is the contract about
#: *what must be answerable*, not a claim that eleven columns exist.
SIGNED_DIRECTIVE_FIELDS: Tuple[str, ...] = (
    "directive_id",
    "session_id",
    "action",
    "scope",
    "evidence_hash",
    "policy_version",
    "issued_at",
    "expires_at",
    "nonce",
    "human_approval",
    "signature",
)

#: The signing form this boundary requires. A module constant on purpose (see
#: module docstring).
REQUIRED_SIGNING_FORM = signing_kernel.SIGNING_FORM_V2

#: Which of the kernel's reason codes belongs to which of the first four checks.
#: ``REASON_NO_KEY`` is attributed to ``signature`` and not to ``signed_present``
#: because check 1 (presence) has already passed by the time it can be returned —
#: the kernel's own comment calls it a "check 1/2 (cannot verify)" code, and
#: reporting it as a presence failure would tell an operator to fix the directive
#: when the actual fault is a missing key.
_KERNEL_FAILURE_CHECK: Dict[str, str] = {
    signing_kernel.REASON_UNSIGNED: "signed_present",
    signing_kernel.REASON_NO_KEY: "signature",
    signing_kernel.REASON_BAD_SIGNATURE: "signature",
    signing_kernel.REASON_EXPIRED: "ttl",
    signing_kernel.REASON_NOT_YET_VALID: "ttl",
    signing_kernel.REASON_REPLAY: "replay",
}


def signed_directive_view(directive: Any) -> Dict[str, Any]:
    """The eleven §17 fields of ``directive``, derived where they are derived.

    This is the single place the field NAMES of §17 are bound to the directive
    object, so the contract can be asserted against a real directive instead of
    against prose. ``expires_at`` is computed, and ``scope`` goes through
    ``signing.scope_token`` — the same function the signed bytes use — so the
    view and the signature cannot describe two different scopes.
    """
    issued = getattr(directive, "issued_at", None)
    ttl = getattr(directive, "ttl_s", None)
    expires_at: Optional[float] = (
        None if (issued is None or ttl is None) else float(issued) + float(ttl)
    )
    return {
        "directive_id": getattr(directive, "directive_id", None),
        "session_id": getattr(directive, "session_id", None),
        "action": getattr(getattr(directive, "action", None), "value", getattr(directive, "action", None)),
        "scope": signing_kernel.scope_token(directive),
        "evidence_hash": getattr(directive, "evidence_hash", None),
        "policy_version": getattr(directive, "policy_version", None),
        "issued_at": issued,
        "expires_at": expires_at,
        "nonce": getattr(directive, "nonce", None),
        "human_approval": getattr(directive, "human_approval", None),
        "signature": getattr(directive, "signature", None),
    }


@dataclass(frozen=True)
class GateContext:
    """Everything the boundary needs that the directive cannot supply itself.

    Held in a value object rather than passed as eight keyword arguments because
    the *complete* set matters: a check whose input is optional is a check that
    silently does not run when a caller forgets it. Here, omitting a field is a
    ``TypeError`` at construction, before any directive is judged.
    """

    session_id: str
    policy_version: str
    policy_table_version: str
    actuation_level: AL.ActuationLevel
    budget_allowed: bool
    budget_code: Optional[str] = None
    #: Force the approval requirement even when the directive does not ask for
    #: human involvement. An operator policy ("everything above L2 needs a
    #: signature on the approval") must be expressible, and it must be able to
    #: ADD a requirement, never remove one.
    require_approval: bool = False


@dataclass(frozen=True)
class CheckResult:
    """One of the nine checks. ``ok=None`` ⇒ NOT REACHED (never "passed")."""

    name: str
    ok: Optional[bool]
    reason: str = ""

    @property
    def reached(self) -> bool:
        return self.ok is not None


@dataclass(frozen=True)
class GateVerdict:
    """The boundary's answer. ``allow`` is True ONLY when all nine passed."""

    allow: bool
    reason: str
    checks: Tuple[CheckResult, ...] = field(default_factory=tuple)
    signing_form: str = REQUIRED_SIGNING_FORM
    actuation_level: AL.ActuationLevel = AL.ActuationLevel.L0

    @property
    def failed_checks(self) -> Tuple[CheckResult, ...]:
        return tuple(c for c in self.checks if c.ok is False)

    @property
    def unreached_checks(self) -> Tuple[CheckResult, ...]:
        return tuple(c for c in self.checks if c.ok is None)

    @property
    def reached_count(self) -> int:
        return sum(1 for c in self.checks if c.reached)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "allow": self.allow,
            "reason": self.reason,
            "signing_form": self.signing_form,
            "actuation_level": self.actuation_level.value,
            "checks_total": len(self.checks),
            "checks_reached": self.reached_count,
            "failed": [c.name for c in self.failed_checks],
            "not_reached": [c.name for c in self.unreached_checks],
            "checks": [
                {"name": c.name, "ok": c.ok, "reason": c.reason} for c in self.checks
            ],
        }


def _not_reached(name: str) -> CheckResult:
    return CheckResult(name=name, ok=None, reason=REASON_NOT_REACHED)


def _verdict(
    order: Tuple[str, ...],
    results: Dict[str, CheckResult],
    level: AL.ActuationLevel,
) -> GateVerdict:
    """Assemble a verdict from whatever ran, filling the rest as not-reached."""
    checks = tuple(results.get(name) or _not_reached(name) for name in order)
    failed = next((c for c in checks if c.ok is False), None)
    if failed is not None:
        return GateVerdict(False, failed.reason, checks, REQUIRED_SIGNING_FORM, level)
    return GateVerdict(True, signing_kernel.REASON_OK, checks, REQUIRED_SIGNING_FORM, level)


def verify_effect(
    directive: Any,
    *,
    key: Optional[bytes],
    now: float,
    context: GateContext,
    replay: Optional[ReplayCache] = None,
    clock_skew_s: float = signing_kernel.DEFAULT_CLOCK_SKEW_S,
) -> GateVerdict:
    """Run the nine checks over ``directive``. ``allow`` means MAY BE APPLIED.

    Never raises for a bad directive: a malformed or hostile directive is a DENY
    verdict, not an exception, because an exception escaping the effect boundary
    is a control-flow path that is *not* the word "deny" and therefore is not
    audited as one. (Programming errors still propagate — a ``TypeError`` from a
    missing context field should be loud.)
    """
    order = signing_kernel.VERIFICATION_CHECK_ORDER
    level = context.actuation_level
    results: Dict[str, CheckResult] = {}

    # -- rung L0: the boundary is not reached at all -------------------------
    if not AL.gate_runs(level):
        return GateVerdict(
            allow=False,
            reason=AL.REASON_LEVEL_OFF,
            checks=tuple(_not_reached(n) for n in order),
            signing_form=REQUIRED_SIGNING_FORM,
            actuation_level=level,
        )

    # -- checks 1–4: delegated to the kernel, under the REQUIRED form --------
    kernel_ok, kernel_reason = signing_kernel.verify_directive(
        directive,
        key,
        now=now,
        replay=replay,
        clock_skew_s=clock_skew_s,
        form=REQUIRED_SIGNING_FORM,
    )
    if kernel_ok:
        for name in signing_kernel.INTEGRITY_CHECK_ORDER:
            results[name] = CheckResult(name=name, ok=True, reason=signing_kernel.REASON_OK)
    else:
        failed_at = _KERNEL_FAILURE_CHECK.get(kernel_reason)
        if failed_at is None:
            # An unrecognised kernel reason means the kernel grew a code this
            # boundary does not know how to attribute. Refuse — but refuse as a
            # SIGNATURE failure attributed to the kernel's own vocabulary, so the
            # verdict is still a DENY and still names a real reason.
            failed_at = "signature"
            kernel_reason = f"unmapped_kernel_reason:{kernel_reason}"
        for name in signing_kernel.INTEGRITY_CHECK_ORDER:
            if name == failed_at:
                results[name] = CheckResult(name=name, ok=False, reason=kernel_reason)
                break
            results[name] = CheckResult(name=name, ok=True, reason=signing_kernel.REASON_OK)
        return _verdict(order, results, level)

    # -- check 5: session ---------------------------------------------------
    declared_session = getattr(directive, "session_id", None)
    if not declared_session or declared_session != context.session_id:
        results["session"] = CheckResult("session", False, signing_kernel.REASON_SESSION_MISMATCH)
        return _verdict(order, results, level)
    results["session"] = CheckResult("session", True, signing_kernel.REASON_OK)

    # -- check 6: capability -------------------------------------------------
    action = getattr(directive, "action", None)
    capability_reason = AL.deny_reason(level, action)
    if capability_reason is not None:
        results["capability"] = CheckResult("capability", False, capability_reason)
        return _verdict(order, results, level)
    if not signing_kernel.scope_token(directive):
        # A directive that names no scope at all authorises nothing; it cannot be
        # audited ("what was this allowed to touch?") and it must not be applied.
        results["capability"] = CheckResult("capability", False, signing_kernel.REASON_CAPABILITY_DENIED)
        return _verdict(order, results, level)
    results["capability"] = CheckResult("capability", True, signing_kernel.REASON_OK)

    # -- check 7: policy ----------------------------------------------------
    if (
        getattr(directive, "policy_version", None) != context.policy_version
        or getattr(directive, "policy_table_version", None) != context.policy_table_version
    ):
        results["policy"] = CheckResult("policy", False, signing_kernel.REASON_POLICY_MISMATCH)
        return _verdict(order, results, level)
    results["policy"] = CheckResult("policy", True, signing_kernel.REASON_OK)

    # -- check 8: budget ----------------------------------------------------
    if not context.budget_allowed:
        results["budget"] = CheckResult(
            "budget", False, context.budget_code or signing_kernel.REASON_BUDGET_EXHAUSTED
        )
        return _verdict(order, results, level)
    results["budget"] = CheckResult("budget", True, signing_kernel.REASON_OK)

    # -- check 9: approval --------------------------------------------------
    needs_approval = bool(getattr(directive, "requires_human", False)) or bool(
        context.require_approval
    )
    approval = getattr(directive, "human_approval", None)
    if needs_approval and (approval is None or str(approval).strip() == ""):
        results["approval"] = CheckResult("approval", False, signing_kernel.REASON_APPROVAL_MISSING)
        return _verdict(order, results, level)
    results["approval"] = CheckResult("approval", True, signing_kernel.REASON_OK)

    return _verdict(order, results, level)


def describe() -> Dict[str, Any]:
    """Pinned, machine-readable description of this boundary (docs / UI / gate).

    Emitted from the same constants the enforcement reads, so a document or a UI
    cannot describe a boundary that differs from the one in force.
    """
    return {
        "checks": list(signing_kernel.VERIFICATION_CHECK_ORDER),
        "checks_total": len(signing_kernel.VERIFICATION_CHECK_ORDER),
        "integrity_checks": list(signing_kernel.INTEGRITY_CHECK_ORDER),
        "effect_checks": list(signing_kernel.EFFECT_CHECK_ORDER),
        "required_signing_form": REQUIRED_SIGNING_FORM,
        "signed_directive_fields": list(SIGNED_DIRECTIVE_FIELDS),
        "signed_directive_fields_total": len(SIGNED_DIRECTIVE_FIELDS),
        "deny_on_any_failure": True,
        "reasons": {
            "unsigned": signing_kernel.REASON_UNSIGNED,
            "bad_signature": signing_kernel.REASON_BAD_SIGNATURE,
            "expired": signing_kernel.REASON_EXPIRED,
            "not_yet_valid": signing_kernel.REASON_NOT_YET_VALID,
            "replay": signing_kernel.REASON_REPLAY,
            "session_mismatch": signing_kernel.REASON_SESSION_MISMATCH,
            "capability_denied": signing_kernel.REASON_CAPABILITY_DENIED,
            "policy_mismatch": signing_kernel.REASON_POLICY_MISMATCH,
            "budget_exhausted": signing_kernel.REASON_BUDGET_EXHAUSTED,
            "approval_missing": signing_kernel.REASON_APPROVAL_MISSING,
            "not_reached": REASON_NOT_REACHED,
        },
    }
