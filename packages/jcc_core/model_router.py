"""ModelRouter — chooses an executor AND a model, then runs the chain.

THE TWO-CHOICE PROBLEM
----------------------
"JEV 自动选择" used to pick one thing: an executor. That is not enough to run a
task, because a capable executor with no reachable model still does nothing. So
the router produces a :class:`RouteDecision` carrying both:

    executor = workbuddy
    model    = MiniMax-M3
    confidence = 0.93

``Jev`` (the vendor decision kernel) supplies the *preference*; this module owns
the *mechanics* — which providers are healthy, what falls back to what, and what
happens when a call fails. Keeping the two apart is what lets the preference come
from a paid model while the guarantees come from code that cannot be talked out
of them.

FAILOVER IS A POLICY, NOT A LOOP
--------------------------------
The chain is MiniMax-M3 -> qwen coding plan -> DeepSeek. What each failure class
does is fixed here, in code, and NOT delegated to the router:

    timeout / network / 5xx / 429  -> try the next provider
    invalid_response                -> try the next provider
    auth                            -> do NOT retry this provider, try the next
    policy_denied                   -> STOP. Never fall over.

That last row is the one that matters most. A policy denial is a decision about
whether this work may happen; routing around it would make the policy advisory.
A test asserts it.

PRODUCER / VERIFIER
-------------------
A model verifying its own output is not verification. :func:`route_with_verifier`
refuses to pick the producer's own model as the verifier, and
:func:`verify_result` treats "the model said it was done" as not-done. Real
success needs tests, an artifact, an exit code, domain state and the verifier —
all of which :func:`assess_completion` enumerates.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from .providers.generation import (
    POLICY_DENIED_CLASSES,
    ROLE_FAST,
    ROLE_HIGH_REASONING,
    ROLE_NORMAL_CODING,
    ROLE_PRIMARY_CODING,
    ROLE_VERIFIER,
    STATE_DISCOVERY_UNSUPPORTED,
    STATE_READY,
    GenerationProvider,
    PolicyDeniedError,
    ProviderAuthError,
    ProviderError,
    ProviderInvalidResponseError,
    ProviderNetworkError,
    ProviderRateLimitError,
    ProviderServerError,
    ProviderTimeoutError,
)

# --------------------------------------------------------------------------- #
# Cost / latency preference
# --------------------------------------------------------------------------- #

COST_SENSITIVE = "cost_sensitive"
COST_BALANCED = "balanced"
COST_QUALITY = "quality"

LATENCY_SENSITIVE = "latency_sensitive"
LATENCY_BALANCED = "balanced"
LATENCY_PATIENT = "patient"


# --------------------------------------------------------------------------- #
# Tasks
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RouteRequest:
    """Everything the router is allowed to reason from.

    No field is "the answer". The router never receives a caller-supplied model
    or executor and treats it as correct; a requested model is a *preference* and
    is still health-checked.

    ``band`` is DERIVED from ``task_type`` when the caller does not name one.
    Without that, every request silently landed in ``NORMAL_CODING`` and a
    refactor was routed to the cheap tier — the dataclass default was quietly
    winning over the mapping table.
    """

    task: str
    task_type: str = "general"
    #: Which decision band the task falls into. See :data:`TASK_BANDS`.
    band: Optional[str] = None
    context_size: int = 0
    complexity: str = "medium"
    risk: str = "low"
    required_capabilities: Sequence[str] = ()
    provider_health: Mapping[str, str] = field(default_factory=dict)
    quota: Mapping[str, Any] = field(default_factory=dict)
    cost_preference: str = COST_BALANCED
    latency_preference: str = LATENCY_BALANCED
    #: A model the caller would prefer, subject to health. Not an override.
    preferred_model: Optional[str] = None
    preferred_provider: Optional[str] = None

    @classmethod
    def from_task_type(cls, task: str, task_type: str, **kw: Any) -> "RouteRequest":
        return cls(task=task, task_type=task_type,
                   band=TASK_BANDS.get(task_type, "NORMAL_CODING"), **kw)

    @property
    def effective_band(self) -> str:
        """The band actually used. Resolved here rather than in ``__init__`` so a
        caller who passes an explicit band keeps it, and one who does not gets
        the mapping."""
        return self.band or TASK_BANDS.get(self.task_type, "NORMAL_CODING")


#: task_type -> decision band. A lookup rather than an if-chain in the router so
#: adding a band is a one-line change with a test, not a new branch.
TASK_BANDS: Dict[str, str] = {
    "decision": "DECISION",
    "planning": "DECISION",
    "simple": "FAST",
    "trivial": "FAST",
    "classification": "FAST",
    "summarize": "FAST",
    "code": "NORMAL_CODING",
    "bugfix": "NORMAL_CODING",
    "test": "NORMAL_CODING",
    # A refactor touches many files and must not land in the cheap tier just
    # because it reads like ordinary coding.
    "refactor": "COMPLEX_CODING",
    "architecture": "COMPLEX_CODING",
    "agentic": "COMPLEX_CODING",
    "repo_analysis": "COMPLEX_CODING",
    "long_context": "COMPLEX_CODING",
    "reasoning": "HIGH_REASONING",
    "research": "HIGH_REASONING",
    "verification": "VERIFICATION",
}

#: Band -> provider roles we will accept, most-preferred first.
BAND_ROLES: Dict[str, Sequence[str]] = {
    "FAST": (ROLE_FAST, ROLE_NORMAL_CODING, ROLE_PRIMARY_CODING),
    "NORMAL_CODING": (ROLE_NORMAL_CODING, ROLE_PRIMARY_CODING, ROLE_FAST),
    "COMPLEX_CODING": (ROLE_PRIMARY_CODING, ROLE_HIGH_REASONING, ROLE_NORMAL_CODING),
    "HIGH_REASONING": (ROLE_HIGH_REASONING, ROLE_PRIMARY_CODING, ROLE_NORMAL_CODING),
    "DECISION": (ROLE_PRIMARY_CODING, ROLE_HIGH_REASONING),
    "VERIFICATION": (ROLE_HIGH_REASONING, ROLE_NORMAL_CODING, ROLE_PRIMARY_CODING),
}

#: The production chain. Ordered; the router walks it in order.
DEFAULT_CHAIN: Sequence[str] = ("minimax", "qwen_coding_plan_cn", "deepseek")

#: Which chain position each band prefers to start from.
BAND_CHAIN_HEAD: Dict[str, str] = {
    "FAST": "qwen_coding_plan_cn",
    "NORMAL_CODING": "qwen_coding_plan_cn",
    "COMPLEX_CODING": "minimax",
    "HIGH_REASONING": "deepseek",
    "DECISION": "minimax",
    "VERIFICATION": "deepseek",
}


# --------------------------------------------------------------------------- #
# Circuit breaker
# --------------------------------------------------------------------------- #


@dataclass
class _Breaker:
    """Per-provider circuit breaker.

    A provider that has just failed three times in a row is skipped for
    ``cooldown_s`` without being contacted. This is what stops a dead upstream
    from adding its timeout to every request in the chain — the difference
    between a 2-second stall and a 40-second one.
    """

    failures: int = 0
    opened_at: float = 0.0
    threshold: int = 3
    cooldown_s: float = 30.0

    def is_open(self, now: float) -> bool:
        if self.failures < self.threshold:
            return False
        if now - self.opened_at >= self.cooldown_s:
            # Half-open: let one call through to see if it recovered.
            self.failures = 0
            return False
        return True

    def record_failure(self, now: float) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = now

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = 0.0


# --------------------------------------------------------------------------- #
# Decision
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RouteDecision:
    """What the router chose, and why — with the provenance kept explicit."""

    decision_provider: str
    execution_provider: str
    execution_model: str
    verification_provider: Optional[str] = None
    verification_model: Optional[str] = None
    executor: str = ""
    confidence: float = 0.0
    #: The ordered chain that will actually be tried.
    chain: Sequence[str] = ()
    #: True when Jev (the vendor kernel) supplied the preference.
    jev_consulted: bool = False
    jev_request_id: Optional[str] = None
    jev_latency_ms: int = 0
    reason: str = ""
    #: Why the preference could not be honoured, when it could not.
    fallback_reason: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "decision_provider": self.decision_provider,
            "execution_provider": self.execution_provider,
            "execution_model": self.execution_model,
            "verification_provider": self.verification_provider,
            "verification_model": self.verification_model,
            "executor": self.executor,
            "confidence": self.confidence,
            "chain": list(self.chain),
            "jev_consulted": self.jev_consulted,
            "jev_request_id": self.jev_request_id,
            "jev_latency_ms": self.jev_latency_ms,
            "reason": self.reason,
            "fallback_reason": self.fallback_reason,
        }


# --------------------------------------------------------------------------- #
# Router
# --------------------------------------------------------------------------- #


class ModelRouter:
    """Chooses executor + model, and runs the production chain under policy."""

    def __init__(
        self,
        providers: Sequence[GenerationProvider],
        *,
        chain: Sequence[str] = DEFAULT_CHAIN,
        jev_decider: Optional[Any] = None,
        clock: Callable[[], float] = time.monotonic,
        verification_lookup: Optional[Callable[[str], bool]] = None,
    ):
        self._by_id = {p.id: p for p in providers}
        self._chain = tuple(chain)
        self._jev = jev_decider          # any object with decide()
        self._clock = clock
        self._breakers: Dict[str, _Breaker] = {}
        # "Has this provider ever completed a real request?" — see `_usable`.
        # Optional, so a router built without a database behaves as before.
        self._verified = verification_lookup

    # -- inspection ------------------------------------------------------- #

    def provider(self, pid: str) -> Optional[GenerationProvider]:
        return self._by_id.get(pid)

    def health_snapshot(self) -> Dict[str, str]:
        """State per provider. Cached breakers are folded in, because a provider
        that is currently open is not a provider we would route to."""
        now = self._clock()
        out: Dict[str, str] = {}
        for pid, p in self._by_id.items():
            breaker = self._breakers.setdefault(pid, _Breaker())
            if breaker.is_open(now):
                out[pid] = "CIRCUIT_OPEN"
                continue
            try:
                out[pid] = p.health().state
            except Exception:
                out[pid] = "UNAVAILABLE"
        return out

    def _usable(self, pid: str, req: RouteRequest) -> bool:
        """May this provider serve this request?

        Every gate here can only REMOVE a provider. None of them can add one, so
        a bug in this function cannot invent capacity.
        """
        p = self._by_id.get(pid)
        if p is None:
            return False
        now = self._clock()
        if self._breakers.setdefault(pid, _Breaker()).is_open(now):
            return False

        # Request-supplied health is authoritative when present: a health cache
        # from a moment ago must not override what the operator just told us.
        declared = (req.provider_health or {}).get(pid)
        state = declared or p.health().state
        if state != STATE_READY:
            # A provider that publishes no model list is in DISCOVERY_UNSUPPORTED
            # FOREVER, because health() is the cheap read and a real generation is
            # what earns READY. Treating that as "unusable" made the router
            # permanently exclude every such provider — MiniMax included — even
            # after it had demonstrably answered.
            #
            # So a recorded real completion counts. It is evidence the provider
            # CAN serve, and requiring a live probe before every routing decision
            # would make routing cost a token each time. Note the direction of the
            # exception: it can only ADD a provider that has actually proven
            # itself, never one that has not.
            if not (state == STATE_DISCOVERY_UNSUPPORTED and self._ever_verified(pid)):
                return False
        if not p.has_credential():
            return False
        needed = set(req.required_capabilities or ())
        if needed:
            caps = p.capabilities()
            have = {k for k, v in caps.items() if v is True}
            if not needed.issubset(have | set(caps.get("roles", []))):
                return False
        return True

    def _ever_verified(self, pid: str) -> bool:
        """Has a real request through this provider ever succeeded?

        False whenever no lookup was supplied or it fails — an unknown is not a
        yes, so the exception above cannot be reached by accident.
        """
        if self._verified is None:
            return False
        try:
            return bool(self._verified(pid))
        except Exception:  # noqa: BLE001 - never let bookkeeping add capacity
            return False

    # -- selection -------------------------------------------------------- #

    def _chain_for(self, req: RouteRequest) -> List[str]:
        """The provider order to try.

        ``BAND_CHAIN_HEAD`` is authoritative: it says where the band STARTS. The
        role list only breaks ties WITHIN the remaining chain, because letting
        the role list re-sort the head would silently override the band
        definition — an earlier version did exactly that, and every
        ``COMPLEX_CODING`` task started on the normal-coding provider because
        ``NORMAL_CODING`` appeared first in its role tuple.

        So: the head is first (if it is in the chain), then the rest ordered by
        role preference.
        """
        head = BAND_CHAIN_HEAD.get(req.effective_band, "minimax")
        rest = [p for p in self._chain if p not in (head,)]
        allowed_roles = BAND_ROLES.get(req.effective_band, ())

        def role_rank(pid: str) -> int:
            p = self._by_id.get(pid)
            if p is None:
                return len(allowed_roles)
            role = p.role
            return allowed_roles.index(role) if role in allowed_roles else len(allowed_roles)

        # Stable sort keeps DEFAULT_CHAIN order inside one role rank, so the
        # fallback sequence is the documented one.
        ordered_rest = sorted(rest, key=role_rank)
        if head in self._chain:
            return [head] + ordered_rest
        return ordered_rest

    def route(
        self,
        req: RouteRequest,
        *,
        executors: Sequence[str] = ("workbuddy", "codex", "claude", "hermes"),
        executor_health: Mapping[str, str] = None,
    ) -> RouteDecision:
        """Pick executor + model. Never raises: an empty decision is a valid,
        honest answer that the UI must render as "nothing was chosen"."""
        chain = self._chain_for(req)
        usable = [pid for pid in chain if self._usable(pid, req)]

        # 1. Jev's preference, when it is reachable. It is advisory: a preference
        #    for a provider that cannot serve right now is not honoured.
        pref_provider = req.preferred_provider
        pref_model = req.preferred_model
        jev_consulted = False
        jev_request_id: Optional[str] = None
        jev_latency = 0
        fallback_reason: Optional[str] = None

        if self._jev is not None:
            try:
                advice = self._ask_jev(req, executors, usable)
                jev_consulted = True
                jev_request_id = advice.get("request_id")
                jev_latency = int(advice.get("latency_ms") or 0)
                if advice.get("model_provider") in usable:
                    pref_provider = advice["model_provider"]
                    pref_model = advice.get("model")
                else:
                    fallback_reason = advice.get("reason")
            except PolicyDeniedError:
                raise
            except Exception as e:  # noqa: BLE001 - advice is best effort
                fallback_reason = f"Jev 不可用：{e}"

        if not usable:
            return RouteDecision(
                decision_provider="jev" if jev_consulted else "local_policy",
                execution_provider="", execution_model="", executor="",
                confidence=0.0, chain=[], jev_consulted=jev_consulted,
                jev_request_id=jev_request_id, jev_latency_ms=jev_latency,
                reason="没有可用的模型 Provider",
                fallback_reason=fallback_reason or "所有候选 Provider 均未就绪",
            )

        # 2. Execution model.
        exec_provider = pref_provider if pref_provider in usable else usable[0]
        if pref_provider and pref_provider not in usable:
            fallback_reason = (
                fallback_reason
                or f"首选 Provider {pref_provider} 当前不可用，已改用 {exec_provider}"
            )
        exec_model = self._pick_model(exec_provider, pref_model, req)

        # 3. Verification model — MUST differ from the producer.
        ver_provider, ver_model = self._pick_verifier(exec_provider, exec_model, usable, req)

        # 4. Executor. Health-filtered, then the first available.
        healthy = [e for e in executors if (executor_health or {}).get(e, "ready") != "down"]
        executor = healthy[0] if healthy else (executors[0] if executors else "")

        confidence = 0.93 if (jev_consulted and pref_provider == exec_provider) else (
            0.75 if exec_provider == usable[0] else 0.6
        )
        return RouteDecision(
            decision_provider="jev" if jev_consulted else "local_policy",
            execution_provider=exec_provider,
            execution_model=exec_model,
            verification_provider=ver_provider,
            verification_model=ver_model,
            executor=executor,
            confidence=confidence,
            chain=usable,
            jev_consulted=jev_consulted,
            jev_request_id=jev_request_id,
            jev_latency_ms=jev_latency,
            reason=f"{req.effective_band} → {exec_provider}/{exec_model}",
            fallback_reason=fallback_reason,
        )

    def _ask_jev(
        self, req: RouteRequest, executors: Sequence[str], usable: Sequence[str]
    ) -> Dict[str, Any]:
        """Ask the vendor kernel. Kept thin so a Jev failure is one exception."""
        from .providers.jev_api import decide_executor

        return decide_executor(
            self._jev,
            task=req.task,
            project_root="",
            task_type=req.task_type,
            available_executors=list(executors),
        ).as_dict()

    def _pick_model(self, pid: str, preferred: Optional[str], req: RouteRequest) -> str:
        p = self._by_id.get(pid)
        if p is None:
            return preferred or ""
        if preferred and (not p.known_models or preferred in p.known_models):
            return preferred
        if req.cost_preference == COST_SENSITIVE and len(p.known_models) > 1:
            # Cost-sensitive picks the smallest-declared family, which is the
            # provider's own ordering, not a guess about price.
            return p.known_models[-1]
        return p.default_model or (p.known_models[0] if p.known_models else "")

    def _pick_verifier(
        self, exec_provider: str, exec_model: str, usable: Sequence[str], req: RouteRequest
    ) -> tuple:
        """A verifier that is not the producer.

        Returns ("", "") when nothing else is available — and the caller is
        expected to treat that as "no independent verification", not as "verified".
        """
        for pid in usable:
            if pid == exec_provider:
                continue
            p = self._by_id[pid]
            if p.role in (ROLE_VERIFIER, ROLE_HIGH_REASONING) or pid != exec_provider:
                return pid, self._pick_model(pid, None, req)
        return "", ""

    # -- execution -------------------------------------------------------- #

    def execute(
        self,
        decision: RouteDecision,
        prompt: str,
        *,
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> Any:
        """Run the chain under the failover policy.

        Returns the successful :class:`GenerateResult`. Raises the last provider
        error only when the WHOLE chain is exhausted, so a caller never receives
        a half-result.
        """
        if not decision.chain:
            raise ProviderError(
                "no usable provider in the chain", provider="router"
            )
        last: Optional[ProviderError] = None
        for pid in decision.chain:
            p = self._by_id.get(pid)
            if p is None:
                continue
            now = self._clock()
            breaker = self._breakers.setdefault(pid, _Breaker())
            if breaker.is_open(now):
                continue
            # The first choice in the chain gets the requested model; a later
            # fallback uses ITS default, because the requested id belongs to a
            # different vendor's namespace.
            model = decision.execution_model if pid == decision.execution_provider else p.default_model
            try:
                result = p.generate(
                    prompt, system=system, model=model,
                    max_tokens=max_tokens, temperature=temperature,
                )
            except PolicyDeniedError:
                # A policy denial is a decision. Falling over would make policy
                # advisory, so this propagates immediately.
                raise
            except ProviderAuthError as e:
                # No retry against this provider, but the chain may still help.
                last = e
                breaker.record_failure(now)
                continue
            except ProviderError as e:
                last = e
                breaker.record_failure(now)
                continue
            breaker.record_success()
            return result
        raise last or ProviderError("chain exhausted", provider="router")

    def record_outcome(self, provider_id: str, ok: bool) -> None:
        """Feed a result back into the breaker. Called by the caller too, so a
        provider that succeeded on a later attempt is credited."""
        b = self._breakers.setdefault(provider_id, _Breaker())
        if ok:
            b.record_success()
        else:
            b.record_failure(self._clock())


# --------------------------------------------------------------------------- #
# Verification
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class VerificationResult:
    """Whether the work is actually done. Never a boolean on its own."""

    passed: bool
    verifier: str = ""
    verifier_model: str = ""
    checks: Mapping[str, bool] = field(default_factory=dict)
    missing: Sequence[str] = ()
    reason: str = ""
    request_id: Optional[str] = None
    latency_ms: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "verifier": self.verifier,
            "verifier_model": self.verifier_model,
            "checks": dict(self.checks),
            "missing": list(self.missing),
            "reason": self.reason,
            "request_id": self.request_id,
            "latency_ms": self.latency_ms,
        }


#: The concrete signals that make "done" a fact rather than an assertion.
COMPLETION_CHECKS = ("tests_passed", "artifact_exists", "exit_code_zero",
                     "domain_state", "jev_verification")


def assess_completion(
    evidence: Mapping[str, Any],
    *,
    verifier: VerificationResult = None,
    require_all: Sequence[str] = COMPLETION_CHECKS,
) -> VerificationResult:
    """Decide whether a task is complete, from evidence.

    A model's own claim is not evidence. ``text`` in the evidence mapping is
    ignored entirely; the function only looks at the five signals above, and
    reports exactly which are missing so the UI can say what to do next.
    """
    checks: Dict[str, bool] = {}
    for name in require_all:
        if name == "jev_verification":
            # The verifier's JUDGEMENT is what feeds this signal — but only after
            # it has been combined with the mechanical ones, which is why the
            # verifier's own `passed` is False by construction.
            checks[name] = bool(
                verifier and verifier.checks.get("model_judgement") is True
            )
        else:
            checks[name] = evidence.get(name) is True
    missing = [k for k, v in checks.items() if not v]
    return VerificationResult(
        passed=not missing,
        verifier=verifier.verifier if verifier else "",
        verifier_model=verifier.verifier_model if verifier else "",
        checks=checks,
        missing=missing,
        reason="证据齐全" if not missing else f"缺少证据：{', '.join(missing)}",
        request_id=verifier.request_id if verifier else None,
        latency_ms=verifier.latency_ms if verifier else 0,
    )


def verify_with_model(
    router: ModelRouter,
    decision: RouteDecision,
    subject: str,
    *,
    require_all: Sequence[str] = COMPLETION_CHECKS,
) -> VerificationResult:
    """Ask the verifier model to judge, then combine with hard evidence.

    The model contributes a judgement; it does not get to decide on its own.
    :func:`assess_completion` still requires the mechanical signals, so a model
    that says "looks good" cannot turn a missing artifact into a pass.
    """
    if not decision.verification_provider:
        return VerificationResult(
            passed=False, checks={}, missing=list(require_all),
            reason="没有独立验证模型（Producer 不能验证自己）",
        )
    p = router.provider(decision.verification_provider)
    if p is None:
        return VerificationResult(
            passed=False, missing=list(require_all),
            reason=f"验证 Provider {decision.verification_provider} 不可用",
        )
    prompt = (
        "你是独立验证者。请判断以下工作是否真的完成，"
        "不要因为文字描述听起来完整就判定通过。\n\n"
        f"任务：{subject}\n\n"
        f"请只回答 JSON：{{\"complete\": true|false, \"reason\": \"...\"}}"
    )
    try:
        result = p.generate(
            prompt, model=decision.verification_model, temperature=0,
            max_tokens=800,
        )
    except PolicyDeniedError:
        raise
    except ProviderError as e:
        return VerificationResult(
            passed=False, missing=list(require_all),
            reason=f"验证模型调用失败：{e}",
        )

    model_says_complete = _parse_verdict(result.text)
    # The model contributes a JUDGEMENT, never a verdict. `passed` stays False
    # here no matter what the model said, because the mechanical evidence does
    # not exist yet — and a VerificationResult whose `passed` can be flipped by
    # a paragraph of text is exactly the object this design exists to avoid.
    # The caller raises it to a pass through `assess_completion`, which is the
    # only place that may say "done".
    return VerificationResult(
        passed=False,
        verifier=decision.verification_provider,
        verifier_model=decision.verification_model,
        checks={
            "model_judgement": model_says_complete,
            "tests_passed": False,
            "artifact_exists": False,
            "exit_code_zero": False,
            "domain_state": False,
            "jev_verification": False,
        },
        missing=list(require_all),
        request_id=result.request_id,
        latency_ms=result.latency_ms,
        reason=(
            "模型判断通过，等待机械证据" if model_says_complete
            else "模型判断不通过"
        ),
    )


def _parse_verdict(text: str) -> bool:
    """Read the verifier's JSON verdict. A reply we cannot read is NOT a pass."""
    import json as _json

    s = (text or "").strip()
    if not s:
        return False
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end <= start:
        return False
    try:
        doc = _json.loads(s[start:end + 1])
    except ValueError:
        return False
    return doc.get("complete") is True
