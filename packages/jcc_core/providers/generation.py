"""Production model providers — the generation layer behind JEV.

THE FOUR ROLES, AND WHY THEY ARE NOT INTERCHANGEABLE
----------------------------------------------------
``JEV``       this product. Orchestrates, reports, owns the domain.
``Jev``       TypeSafe System One. DECIDES. Picks an executor, a model, a risk
              level. Never writes task prose or code.
``MiniMax``   / ``Qwen`` / ``DeepSeek``. GENERATE. They write code and prose.
``WorkBuddy`` / ``Codex`` / ``Hermes``. EXECUTE. They run tools.

The most expensive bug available to us is letting a generator decide, or letting
a decider write. :class:`JevSystemOneProvider` therefore exposes ``decide()`` and
deliberately has no ``generate()``; :class:`OpenAICompatProvider` exposes
``generate()`` and has no ``decide()``. The split is enforced by the interfaces,
not by convention.

A GENERATION PROVIDER, NOT A VENDOR
------------------------------------
Three of the four vendors expose an OpenAI-compatible ``/chat/completions``. So
:mod:`openai_compat` implements the wire ONCE, and MiniMax / Qwen / DeepSeek /
Custom are *configuration* — base url, model ids, headers, a couple of quirks.
That is what makes "GLM, Kimi, OpenRouter, local Ollama" work without a code
change, and it is why no business module hardcodes an endpoint.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

# --------------------------------------------------------------------------- #
# Provider states — six, because RATE_LIMITED and DEGRADED are not UNAVAILABLE
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# Provider states
#
# THE RULE THAT MATTERS MOST
# --------------------------
# ``READY`` may only be produced by a real, authenticated CAPABILITY call — one
# successful generation. It may NOT be inferred from any of the following, each
# of which was a way this product previously showed a green light for a service
# that could not actually run:
#
#   * a declared ``known_models`` preset. A preset is what WE believe exists; it
#     is not evidence the account can serve it. MiniMax declares ``MiniMax-M3``
#     and the account may be empty, expired, or on a different plan.
#   * a successful ``/models`` call. Listing models proves the credential works
#     and the endpoint answers; it does NOT prove a completion can be produced.
#   * a configured ``default_model`` that happens to appear in a model list.
#
# So the two facts are tracked separately and neither implies the other:
#   ``discovery`` — MODEL_DISCOVERY_SUPPORTED / _UNSUPPORTED
#   ``state``     — the seven states below
#
# WHY MODEL DISCOVERY IS ITS OWN AXIS
# -----------------------------------
# A provider with no ``/models`` endpoint is a fully working provider. MiniMax
# answers 404 on ``/models`` because the vendor does not expose it, which says
# nothing about ``/v1/text/chatcompletion_v2``. Reporting that as UNAVAILABLE
# would mark a healthy provider dead and send an operator hunting a fault that
# does not exist. So DISCOVERY_UNSUPPORTED is a normal, healthy-by-generation
# state — it is only disqualifying if generation also fails.
# --------------------------------------------------------------------------- #

#: No credential configured. Nothing has been attempted.
STATE_NOT_CONFIGURED = "NOT_CONFIGURED"
#: The credential was presented and the service rejected it (401/403). The one
#: state where retrying the same credential is pointless.
STATE_AUTH_FAILED = "AUTH_FAILED"
#: The provider has no usable ``/models`` endpoint. NOT a fault.
STATE_DISCOVERY_UNSUPPORTED = "DISCOVERY_UNSUPPORTED"
#: Proven working by at least one real authenticated generation.
STATE_READY = "READY"
#: 429. Distinct from UNAVAILABLE: waiting may fix it.
STATE_RATE_LIMITED = "RATE_LIMITED"
#: Reachable and authenticated, but behaving badly (default model absent from the
#: account, usage missing). Usable with a caveat.
STATE_DEGRADED = "DEGRADED"
#: Transport or server failure (5xx, DNS, timeout).
STATE_UNAVAILABLE = "UNAVAILABLE"

PROVIDER_STATES = (
    STATE_NOT_CONFIGURED,
    STATE_AUTH_FAILED,
    STATE_DISCOVERY_UNSUPPORTED,
    STATE_READY,
    STATE_RATE_LIMITED,
    STATE_DEGRADED,
    STATE_UNAVAILABLE,
)

#: The model-discovery axis, kept apart from the health axis.
DISCOVERY_SUPPORTED = "MODEL_DISCOVERY_SUPPORTED"
DISCOVERY_UNSUPPORTED = "MODEL_DISCOVERY_UNSUPPORTED"
DISCOVERY_UNKNOWN = "MODEL_DISCOVERY_UNKNOWN"

#: The verification axis. Orthogonal to `state`: a provider can be authenticated
#: and discoverable while its capability is still unverified.
VERIFICATION_DECISION = "DECISION_VERIFIED"
VERIFICATION_GENERATION = "GENERATION_VERIFIED"
VERIFICATION_NOT_VERIFIED = "NOT_VERIFIED"

#: Roles a provider can play. Used by the router and shown in the settings cards.
ROLE_DECISION = "DECISION"
ROLE_PRIMARY_CODING = "PRIMARY_CODING"
ROLE_NORMAL_CODING = "NORMAL_CODING"
ROLE_HIGH_REASONING = "HIGH_REASONING"
ROLE_FAST = "FAST"
ROLE_VERIFIER = "VERIFIER"


# --------------------------------------------------------------------------- #
# Errors — classified so the router can decide, and the log can stay quiet
# --------------------------------------------------------------------------- #


class ProviderError(Exception):
    """Base. ``failure_class`` is what the failover policy branches on."""

    failure_class = "unknown"

    def __init__(self, message: str, *, status: int = 0, retry_after_s: float = 0.0,
                 provider: str = ""):
        super().__init__(message)
        self.status = status
        self.retry_after_s = retry_after_s
        self.provider = provider


class ProviderAuthError(ProviderError):
    """401/403. NEVER retried against the same provider — a second attempt with
    the same rejected credential cannot succeed, and retrying it would burn
    latency to learn nothing."""

    failure_class = "auth"


class ProviderRateLimitError(ProviderError):
    """429. Honours ``Retry-After`` when the server sent one."""

    failure_class = "rate_limit"


class ProviderTimeoutError(ProviderError):
    failure_class = "timeout"


class ProviderNetworkError(ProviderError):
    failure_class = "network"


class ProviderServerError(ProviderError):
    """5xx. The only class that is worth a bounded retry before failing over."""

    failure_class = "server"


class ProviderInvalidResponseError(ProviderError):
    """A 2xx whose body did not match the contract. Never retried against the
    same provider: a provider that returned nonsense once will usually return
    nonsense again, and the retry would just double the bill."""

    failure_class = "invalid_response"


class PolicyDeniedError(ProviderError):
    """A policy refusal. The router must NOT fail over on this: a policy denial
    is a decision, and routing around it would defeat the policy."""

    failure_class = "policy_denied"


#: Classes that may be retried against the SAME provider, and how many times.
RETRYABLE_CLASSES = {"server": 2, "timeout": 1, "network": 1, "rate_limit": 0}

#: Failure classes the router must NOT fail over on.
#:
#: A policy denial is a decision that this work may not happen here. Trying the
#: next provider would make the policy advisory — the exact outcome a policy
#: exists to prevent — so it propagates instead of advancing the chain.
POLICY_DENIED_CLASSES = frozenset({"policy_denied"})


# --------------------------------------------------------------------------- #
# Redaction
# --------------------------------------------------------------------------- #

_MASK = "***REDACTED***"


def redact(text: Any, *secrets: Optional[str]) -> str:
    """Strip every known secret from a string that may be logged or returned."""
    s = str(text)
    for secret in secrets:
        if not secret or len(str(secret)) < 4:
            continue
        secret = str(secret)
        s = s.replace(secret, _MASK)
        # A truncated echo leaks too.
        if len(secret) >= 8:
            s = s.replace(secret[:8], "***RED***")
        if len(secret) >= 4:
            s = s.replace(secret[-4:], "***")
    return s


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Usage:
    """Token accounting. Every field is Optional because a provider that omits
    usage must report UNKNOWN, not zero — a fabricated 0 reads as "free"."""

    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_openai(cls, raw: Any) -> "Usage":
        if not isinstance(raw, Mapping):
            return cls()
        return cls(
            input_tokens=raw.get("prompt_tokens") if isinstance(raw.get("prompt_tokens"), int) else None,
            output_tokens=raw.get("completion_tokens") if isinstance(raw.get("completion_tokens"), int) else None,
            total_tokens=raw.get("total_tokens") if isinstance(raw.get("total_tokens"), int) else None,
            raw=dict(raw),
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass(frozen=True)
class GenerateResult:
    """One completion, with the provenance a UI needs to be honest."""

    text: str
    model: str
    provider: str
    usage: Usage = field(default_factory=Usage)
    latency_ms: int = 0
    request_id: Optional[str] = None
    finish_reason: Optional[str] = None
    #: Present when the model announced tool calls. Text alone is not a result.
    tool_calls: Sequence[Mapping[str, Any]] = ()

    def as_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "latency_ms": self.latency_ms,
            "usage": self.usage.as_dict(),
            "request_id": self.request_id,
            "finish_reason": self.finish_reason,
            "has_tool_calls": bool(self.tool_calls),
            "text_len": len(self.text),
        }


@dataclass(frozen=True)
class ProviderHealth:
    """Two independent facts about one provider.

    ``state``      — can it serve a completion, and if not why not.
    ``discovery``  — can we ask it which models it has. Orthogonal: a provider
                     with no ``/models`` endpoint can still be READY.

    ``generation_verified`` is the receipt for READY. It is set ONLY by a real
    authenticated completion. A preset model list, a successful ``/models``, or
    a configured default never sets it — which is what stops a UI from showing
    a green dot for a service that would fail on the first real request.
    """

    provider: str
    state: str
    discovery: str = DISCOVERY_UNKNOWN
    models: Sequence[str] = ()
    #: True only after a real authenticated generation succeeded.
    generation_verified: bool = False
    #: Set by a DECISION provider's probe (Jev). A decision model has no
    #: generation capability, so its proof arrives through a different route and
    #: must not be inferred from `generation_verified` — asking a decision model
    #: to produce a completion tests something it cannot do.
    decision_verified: bool = False
    latency_ms: Optional[int] = None
    detail: Optional[str] = None
    configured: bool = False
    base_url: str = ""
    default_model: str = ""
    #: True when ``models`` came from a declared preset rather than the service.
    models_are_preset: bool = False

    @property
    def ready(self) -> bool:
        return self.state == STATE_READY

    def as_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "state": self.state,
            "discovery": self.discovery,
            "models": list(self.models),
            "models_are_preset": self.models_are_preset,
            "generation_verified": self.generation_verified,
            "latency_ms": self.latency_ms,
            "detail": self.detail,
            "configured": self.configured,
            "base_url": self.base_url,
            "default_model": self.default_model,
        }


# --------------------------------------------------------------------------- #
# The generation interface
# --------------------------------------------------------------------------- #


class GenerationProvider:
    """A model that produces text (and maybe tool calls).

    Subclasses must implement :meth:`_post_chat`; everything else — retries,
    redaction, usage parsing, timeout mapping — lives here so no provider can
    forget it.

    ``verification_kind`` exists so a single entry point can probe a whole
    registry. It is "generation" here and "decision" for the System One client;
    :meth:`verify_capability` dispatches on it, so a caller never has to know
    which kind of provider it is holding.
    """

    #: Stable provider id, used in config, routes and the UI.
    id: str = "base"
    #: Human label for the settings card.
    display_name: str = "Provider"
    #: What this provider is FOR. The router reads this; a user reads the card.
    role: str = ROLE_NORMAL_CODING
    #: Which probe earns READY. "generation" here; a decision provider declares
    #: "decision" and is never asked to produce prose.
    verification_kind: str = "generation"
    #: Human label for the settings card.
    display_name: str = "Provider"
    #: What this provider is FOR. The router reads this; a user reads the card.
    role: str = ROLE_NORMAL_CODING
    #: Official base url. Overridable per deployment, never per business module.
    default_base_url: str = ""
    #: Model ids this provider is known to serve. Used as the FALLBACK list when
    #: discovery is unavailable — an empty list is not the same as "unknown".
    known_models: Sequence[str] = ()
    #: The model used when a caller does not name one. Each concrete provider
    #: sets this; the base stays EMPTY rather than defaulting to the first entry
    #: of ``known_models``, because "the first model we happen to list" is not a
    #: default and would change silently when the list is reordered.
    default_model: str = ""
    #: Whether this provider can return tool calls.
    supports_tools: bool = True
    #: Whether this provider can stream.
    supports_streaming: bool = False
    #: Whether ``/models`` is trustworthy here. A provider that answers /models
    #: with a marketing list is declared False and its preset is used instead —
    #: inventing a discovery result would be worse than none.
    supports_model_discovery: bool = True
    #: Extra headers this vendor requires. A plain class attribute, NOT a
    #: dataclass ``field()``: this class is not a dataclass, so ``field()`` would
    #: stay a Field object and iterating it would raise at request time.
    extra_headers: Mapping[str, str] = {}

    def __init__(
        self,
        secret: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout_s: float = 60.0,
        max_retries: int = 2,
    ):
        self._secret = secret
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        self.default_model = model or self.default_model
        self.timeout_s = timeout_s
        self.max_retries = max_retries

    # -- capability ------------------------------------------------------- #

    def capabilities(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "role": self.role,
            "base_url": self.base_url,
            "default_model": self.default_model,
            "supports_tools": self.supports_tools,
            "supports_streaming": self.supports_streaming,
            "supports_model_discovery": self.supports_model_discovery,
            "known_models": list(self.known_models),
            "timeout_s": self.timeout_s,
        }

    def has_credential(self) -> bool:
        return bool(self._secret and self._secret.strip())

    # -- transport -------------------------------------------------------- #

    def _headers(self) -> Dict[str, str]:
        if not self.has_credential():
            raise ProviderAuthError(
                f"{self.id}: no credential configured", provider=self.id, status=401
            )
        h = {
            "Authorization": f"Bearer {self._secret}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        h.update(self.extra_headers)
        return h

    def _request(
        self,
        method: str,
        url: str,
        body: Optional[Dict[str, Any]] = None,
        *,
        timeout_s: Optional[float] = None,
    ) -> tuple:
        """One HTTP attempt. Returns (status, parsed, headers, latency_ms).

        Every error path runs the message through :func:`redact` before raising,
        so an upstream proxy that echoes the Authorization header cannot carry
        the key into a traceback.
        """
        data = json.dumps(body, separators=(",", ":")).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        for k, v in self._headers().items():
            req.add_header(k, v)

        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout_s or self.timeout_s) as resp:
                raw = resp.read()
                status = resp.status
                headers = {k.lower(): v for k, v in resp.headers.items()}
        except urllib.error.HTTPError as e:
            latency = int((time.monotonic() - started) * 1000)
            try:
                detail_body = e.read()[:400].decode("utf-8", "replace")
            except Exception:
                detail_body = ""
            detail = redact(f"HTTP {e.code}: {detail_body}", self._secret)
            retry_after = _parse_retry_after(e.headers)
            if e.code in (401, 403):
                raise ProviderAuthError(detail, status=e.code, provider=self.id) from None
            if e.code == 429:
                raise ProviderRateLimitError(
                    detail, status=e.code, provider=self.id, retry_after_s=retry_after
                ) from None
            if e.code >= 500:
                raise ProviderServerError(detail, status=e.code, provider=self.id) from None
            raise ProviderError(detail, status=e.code, provider=self.id) from None
        except urllib.error.URLError as e:
            raise ProviderNetworkError(
                redact(f"transport failure: {e.reason}", self._secret), provider=self.id
            ) from None
        except TimeoutError:
            raise ProviderTimeoutError(f"{self.id}: request timed out", provider=self.id) from None
        except OSError as e:
            raise ProviderNetworkError(
                redact(f"socket failure: {e}", self._secret), provider=self.id
            ) from None

        latency = int((time.monotonic() - started) * 1000)
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError) as e:
            raise ProviderInvalidResponseError(
                f"{self.id}: response was not JSON ({e})", provider=self.id
            ) from None
        return status, parsed, headers, latency

    # -- public API ------------------------------------------------------- #

    def _discover_models(self) -> List[str]:
        """Ask the provider which models it serves. RAISES on any failure.

        This is the strict version, used by :meth:`health`. Discovery failure is
        a fact about the provider and must be reported as one; falling back to
        the preset here would turn a 5xx into a green dot.
        """
        if not self.supports_model_discovery:
            return list(self.known_models)
        if not self.has_credential():
            return list(self.known_models)
        _, body, _, _ = self._request("GET", f"{self.base_url}/models")
        if not isinstance(body, Mapping):
            raise ProviderInvalidResponseError(
                f"{self.id}: models response was not an object", provider=self.id
            )
        raw = body.get("data", body.get("models"))
        if not isinstance(raw, list):
            raise ProviderInvalidResponseError(
                f"{self.id}: models response had no 'data' array", provider=self.id
            )
        out: List[str] = []
        for item in raw:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, Mapping) and isinstance(item.get("id"), str):
                out.append(item["id"])
        return out or list(self.known_models)

    def models(self) -> List[str]:
        """Available model ids, for a UI that wants a list.

        Falls back to the declared preset when discovery fails, because a
        settings page that refuses to show any model on a transient 5xx is less
        useful than one that shows the known set labelled as a preset.
        :meth:`health` uses the strict path instead — see :meth:`_discover_models`.
        """
        if not self.supports_model_discovery or not self.has_credential():
            return list(self.known_models)
        try:
            return self._discover_models()
        except ProviderError:
            return list(self.known_models)

    def health(self) -> ProviderHealth:
        """Report what is true, separating two facts that used to be conflated.

        Never raises, and the returned shape is identical in every outcome, so a
        caller cannot read the presence of a key as a sign of success.

        THE CENTRAL RULE
        ----------------
        This method does NOT return READY. It cannot: nothing here has performed
        a generation, and READY is defined by a real authenticated completion.
        What it CAN do is rule states out:

        * no credential                      -> NOT_CONFIGURED
        * 401/403 from the service           -> AUTH_FAILED
        * 429                                -> RATE_LIMITED
        * 5xx / DNS / timeout                -> UNAVAILABLE
        * 404 (or no ``/models`` route)      -> DISCOVERY_UNSUPPORTED, and the
                                                 preset is shown LABELLED as a
                                                 preset. A vendor that does not
                                                 publish a model list is a normal
                                                 provider, not a dead one.
        * discovery worked, credential works -> DISCOVERY_SUPPORTED, and the state
                                                 is DEGRADED rather than READY
                                                 until :meth:`verify_generation`
                                                 has actually run.

        So the settings page can show "authenticated, model list available" and
        "can produce a completion" as the two different claims they are.
        """
        if not self.has_credential():
            return ProviderHealth(
                provider=self.id, state=STATE_NOT_CONFIGURED, configured=False,
                base_url=self.base_url, default_model=self.default_model,
                models=list(self.known_models), models_are_preset=True,
                discovery=DISCOVERY_UNKNOWN,
                detail="未配置 API Key",
            )

        # A provider that declares no discovery route is not asked.
        if not self.supports_model_discovery:
            return self._undiscovered(STATE_DISCOVERY_UNSUPPORTED,
                                      "该 Provider 不提供模型列表接口，使用已声明的默认模型")

        try:
            model_list = self._discover_models()
        except ProviderAuthError as e:
            return ProviderHealth(
                provider=self.id, state=STATE_AUTH_FAILED, configured=True,
                base_url=self.base_url, default_model=self.default_model,
                detail=redact(str(e), self._secret),
                discovery=DISCOVERY_UNKNOWN,
            )
        except ProviderRateLimitError as e:
            return ProviderHealth(
                provider=self.id, state=STATE_RATE_LIMITED, configured=True,
                base_url=self.base_url, default_model=self.default_model,
                detail=redact(str(e), self._secret),
                discovery=DISCOVERY_UNKNOWN,
            )
        except ProviderError as e:
            # A 404 on /models is the "no such route" case, which is a property
            # of the vendor's API surface and says NOTHING about whether the
            # completion endpoint works. Reporting UNAVAILABLE here is how a
            # perfectly good MiniMax account gets shown as a dead provider.
            if self._is_missing_route(e):
                return self._undiscovered(
                    STATE_DISCOVERY_UNSUPPORTED,
                    "该 Provider 不提供 /models 接口（已尝试，不影响生成）",
                )
            return ProviderHealth(
                provider=self.id, state=STATE_UNAVAILABLE, configured=True,
                base_url=self.base_url, default_model=self.default_model,
                detail=redact(str(e), self._secret),
                discovery=DISCOVERY_UNKNOWN,
            )

        if self.default_model and model_list and self.default_model not in model_list:
            # A configured default the account cannot serve is a real problem:
            # every request would 404. DEGRADED, never READY.
            return ProviderHealth(
                provider=self.id, state=STATE_DEGRADED, configured=True,
                models=model_list, models_are_preset=False,
                base_url=self.base_url, default_model=self.default_model,
                discovery=DISCOVERY_SUPPORTED,
                detail=f"默认模型 {self.default_model} 不在可用列表中",
            )
        # Authenticated and the list is readable — but STILL not READY, because
        # listing models is not the capability we need. The caller must run
        # verify_generation() to earn that state.
        return ProviderHealth(
            provider=self.id, state=STATE_DEGRADED, configured=True,
            models=model_list, models_are_preset=False,
            base_url=self.base_url, default_model=self.default_model,
            discovery=DISCOVERY_SUPPORTED,
            detail="凭据有效、模型列表可读；尚未验证真实生成",
        )

    def _undiscovered(self, state: str, detail: str) -> ProviderHealth:
        """The shape used whenever discovery is unavailable.

        The preset is returned so the UI can offer something to select, and
        ``models_are_preset=True`` is set so nothing downstream mistakes our
        declaration for the account's inventory.
        """
        return ProviderHealth(
            provider=self.id, state=state, configured=True,
            base_url=self.base_url, default_model=self.default_model,
            models=list(self.known_models), models_are_preset=True,
            discovery=DISCOVERY_UNSUPPORTED, detail=detail,
        )

    @staticmethod
    def _is_missing_route(e: ProviderError) -> bool:
        """Did the service answer 404 (no such route) rather than fail?

        Checked on the status code, not on the message: an HTML error page from
        a proxy is not evidence of anything except that the route is missing.
        """
        return getattr(e, "status", 0) == 404

    def verify_capability(self, **kw: Any) -> ProviderHealth:
        """Probe whatever capability this provider actually has.

        The single entry point a registry calls. It dispatches on
        ``verification_kind`` so the caller does not need to know whether it is
        holding a text generator or a decision model — asking a decision model to
        produce a completion would test a capability it does not have, and the
        resulting "failure" would be the probe's bug, not the provider's.
        """
        if self.verification_kind == "decision":
            # A decision provider is not a GenerationProvider in the first place;
            # reaching this branch means a subclass declared the wrong kind, and
            # the honest response is to say so rather than generate text.
            return ProviderHealth(
                provider=self.id, state=STATE_DEGRADED, configured=self.has_credential(),
                base_url=self.base_url, default_model=self.default_model,
                detail="该 Provider 声明为 decision，但未实现 verify_decision()",
            )
        return self.verify_generation(**kw)

    def verify_generation(
        self, prompt: str = "reply exactly JEV_OK", **kw: Any
    ) -> ProviderHealth:
        """The ONLY way to reach READY: one real authenticated completion.

        Uses the provider's own configured default model, so a preset id that
        the account does not serve fails here — which is the point. A provider
        that lists models but cannot complete is caught at this step instead of
        during a mission.

        The prompt is fixed and tiny. It is a capability probe, not a test of
        quality, and it must not be able to cost anything meaningful.
        """
        if not self.has_credential():
            return self.health()
        import time as _t
        _started = _t.monotonic()
        try:
            # A reasoning model spends budget on a <think> block before its
            # answer; 16 tokens truncated it mid-thought and the probe could not
            # tell "answered" from "cut off", because both succeed.
            self.generate(prompt, max_tokens=512, **kw)
        except ProviderAuthError as e:
            return ProviderHealth(
                provider=self.id, state=STATE_AUTH_FAILED, configured=True,
                base_url=self.base_url, default_model=self.default_model,
                discovery=self.health().discovery,
                models=list(self.known_models), models_are_preset=True,
                detail=redact(str(e), self._secret),
            )
        except ProviderRateLimitError as e:
            return ProviderHealth(
                provider=self.id, state=STATE_RATE_LIMITED, configured=True,
                base_url=self.base_url, default_model=self.default_model,
                discovery=self.health().discovery,
                detail=redact(str(e), self._secret),
            )
        except ProviderError as e:
            # A generation 404 is different from a discovery 404: the completion
            # route really is missing, or the model id is wrong. Either way the
            # provider cannot serve, so this IS unavailable.
            return ProviderHealth(
                provider=self.id, state=STATE_UNAVAILABLE, configured=True,
                base_url=self.base_url, default_model=self.default_model,
                discovery=self.health().discovery,
                models=list(self.known_models), models_are_preset=True,
                detail=f"真实生成失败：{redact(str(e), self._secret)}",
            )
        # Proven.
        base = self.health()
        return ProviderHealth(
            provider=self.id, state=STATE_READY, configured=True,
            models=list(base.models) or list(self.known_models),
            models_are_preset=base.models_are_preset,
            base_url=self.base_url, default_model=self.default_model,
            discovery=base.discovery,
            generation_verified=True,
            # The measured round trip, kept on the result. A record that says
            # "verified" with no timing cannot answer "how slow is it".
            latency_ms=int((_t.monotonic() - _started) * 1000),
            detail="真实生成已验证",
        )

    def usage(self) -> Usage:
        """Providers that meter usage override this. The base reports UNKNOWN,
        which is the honest default: no invented zeros."""
        return Usage()

    def generate(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        tools: Optional[Sequence[Mapping[str, Any]]] = None,
    ) -> GenerateResult:
        """One completion, with bounded retry on the retryable classes only."""
        target = model or self.default_model
        messages: List[Dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        body: Dict[str, Any] = {"model": target, "messages": messages}
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if temperature is not None:
            body["temperature"] = temperature
        if tools and self.supports_tools:
            body["tools"] = list(tools)
            body.setdefault("tool_choice", "auto")

        attempts = self.max_retries + 1
        last: Optional[ProviderError] = None
        for attempt in range(attempts):
            try:
                return self._post_chat(f"{self.base_url}/chat/completions", body, target)
            except ProviderAuthError:
                raise  # never retried
            except ProviderInvalidResponseError:
                raise  # never retried: nonsense once means nonsense again
            except PolicyDeniedError:
                raise  # never routed around
            except ProviderError as e:
                last = e
                budget = RETRYABLE_CLASSES.get(e.failure_class, 0)
                if attempt >= min(budget, self.max_retries):
                    break
                time.sleep(_backoff_s(attempt, e.retry_after_s))
        assert last is not None
        raise last

    def _post_chat(self, url: str, body: Dict[str, Any], model: str) -> GenerateResult:
        _, payload, headers, latency = self._request("POST", url, body)
        return parse_chat_completion(
            payload,
            provider=self.id,
            requested_model=model,
            latency_ms=latency,
            request_id=headers.get("x-request-id") or headers.get("request-id"),
            secret=self._secret,
        )


# --------------------------------------------------------------------------- #
# Shared parsing
# --------------------------------------------------------------------------- #


def parse_chat_completion(
    payload: Any,
    *,
    provider: str,
    requested_model: str,
    latency_ms: int,
    request_id: Optional[str] = None,
    secret: Optional[str] = None,
) -> GenerateResult:
    """Read an OpenAI-compatible ``/chat/completions`` reply.

    Raises :class:`ProviderInvalidResponseError` rather than returning empty
    text. An empty completion and a successful one are indistinguishable to a
    caller that does not check, and a silent empty string is how a pipeline ends
    up writing an empty file and calling it a result.
    """
    if not isinstance(payload, Mapping):
        raise ProviderInvalidResponseError(
            f"{provider}: response was not an object", provider=provider
        )
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ProviderInvalidResponseError(
            f"{provider}: response had no choices", provider=provider
        )
    first = choices[0]
    if not isinstance(first, Mapping):
        raise ProviderInvalidResponseError(
            f"{provider}: choice was not an object", provider=provider
        )
    message = first.get("message")
    if not isinstance(message, Mapping):
        raise ProviderInvalidResponseError(
            f"{provider}: choice had no message", provider=provider
        )
    content = message.get("content")
    tool_calls = message.get("tool_calls") or []
    if not isinstance(tool_calls, list):
        tool_calls = []
    # Content may legitimately be None when the model only emitted tool calls.
    if content is None and not tool_calls:
        raise ProviderInvalidResponseError(
            f"{provider}: message had neither content nor tool calls", provider=provider
        )
    return GenerateResult(
        # A reasoning model returns its chain of thought inside `content`, wrapped
        # in <think>…</think>, and the answer AFTER it. Handing that whole blob on
        # as the completion would put the model's private reasoning in front of a
        # user as if it were the answer, and would make a "reply exactly X" probe
        # fail even though the model complied perfectly.
        #
        # MEASURED: MiniMax-M3 answered "Return exactly: JEV_MINIMAX_OK" with
        # `<think>…</think>` first, so the literal token was absent from `text`
        # even though the request succeeded. Stripped here, once, so no consumer
        # has to know which vendors think out loud.
        text=strip_reasoning(content) if isinstance(content, str) else "",
        model=payload.get("model") if isinstance(payload.get("model"), str) else requested_model,
        provider=provider,
        usage=Usage.from_openai(payload.get("usage")),
        latency_ms=latency_ms,
        request_id=request_id,
        finish_reason=first.get("finish_reason") if isinstance(first.get("finish_reason"), str) else None,
        tool_calls=[c for c in tool_calls if isinstance(c, Mapping)],
    )


def _parse_retry_after(headers: Any) -> float:
    """Read ``Retry-After``. Both the seconds and the HTTP-date form are legal;
    an unparsable value yields 0, which the backoff then treats as "use our own"."""
    try:
        raw = headers.get("Retry-After")
    except Exception:
        return 0.0
    if not raw:
        return 0.0
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 0.0


def _backoff_s(attempt: int, retry_after_s: float = 0.0) -> float:
    """Bounded exponential backoff. The ceiling exists so a chain of 5xx retries
    cannot become a longer stall than the caller's patience."""
    if retry_after_s > 0:
        return min(retry_after_s, 30.0)
    return min(0.5 * (2 ** attempt), 8.0)


# --------------------------------------------------------------------------- #
# Concrete providers — CONFIGURATION, not code
# --------------------------------------------------------------------------- #


class MiniMaxProvider(GenerationProvider):
    """MiniMax M3. PRIMARY_CODING / LONG_CONTEXT / AGENTIC.

    THREE INDEPENDENT DIMENSIONS, because conflating any two of them sends the
    request to the wrong host and reports a valid key as invalid:

    ``plan_type``     what was bought   — pay_as_you_go / coding_subscription_cn
                                          / coding_subscription_global
    ``api_protocol``  how we speak      — openai_compatible / native
    ``region``        where the account lives — cn / global

    MEASURED, and this is the whole reason the model exists. A China Token Plan
    key (``sk-cp`` prefix) was being sent to the international pay-as-you-go host
    ``api.minimax.io`` and came back::

        {"type":"error","error":{"type":"authorized_error",
         "message":"invalid api key (2049)","http_code":"401"}}

    That is NOT an invalid key. It is a valid key presented to the wrong region,
    and reporting it as "invalid api key" sends an operator to reissue a
    perfectly good credential. The fix is to stop guessing the host from the
    plan and to require ``region`` and ``base_url`` to be stated.

    The quota endpoint below IDENTIFIES the account class. It is not a generation
    endpoint and success there is never enough for READY — only a real completion
    is.
    """

    id = "minimax"
    display_name = "MiniMax M3"
    role = ROLE_PRIMARY_CODING
    known_models = ("MiniMax-M3",)
    default_model = "MiniMax-M3"
    supports_streaming = True

    #: MiniMax publishes no ``/models`` route — it answers 404. Declaring that
    #: here means the client does not spend a doomed request on every health
    #: poll, and the provider reports DISCOVERY_UNSUPPORTED as a property of the
    #: API rather than as a failure discovered at runtime.
    supports_model_discovery = False

    # --- dimension 1: plan_type ------------------------------------------- #
    PLAN_PAY_AS_YOU_GO = "pay_as_you_go"
    PLAN_CODING_CN = "coding_subscription_cn"
    PLAN_CODING_GLOBAL = "coding_subscription_global"
    PLAN_TYPES = (PLAN_PAY_AS_YOU_GO, PLAN_CODING_CN, PLAN_CODING_GLOBAL)

    # --- dimension 2: api_protocol ---------------------------------------- #
    PROTOCOL_OPENAI = "openai_compatible"
    PROTOCOL_NATIVE = "native"
    PROTOCOLS = (PROTOCOL_OPENAI, PROTOCOL_NATIVE)

    # --- dimension 3: region ----------------------------------------------- #
    REGION_CN = "cn"
    REGION_GLOBAL = "global"
    REGIONS = (REGION_CN, REGION_GLOBAL)

    #: Host per region. There is no fallback between them: a CN subscription key
    #: sent to the global host is rejected as "invalid api key", which reads as a
    #: credential problem and is not one. That is why ``region`` is required
    #: rather than defaulted across the boundary.
    HOSTS = {
        REGION_CN: "https://api.minimaxi.com",
        REGION_GLOBAL: "https://api.minimax.io",
    }

    #: China Token Plan quota probe. IDENTIFIES the account class only.
    #: Never used to prove generation works.
    QUOTA_PATH_CN = "/v1/token_plan/remains"
    native_chat_path = "/v1/text/chatcompletion_v2"
    openai_chat_path = "/chat/completions"

    def __init__(
        self,
        *a: Any,
        plan_type: str = PLAN_CODING_CN,
        api_protocol: str = PROTOCOL_OPENAI,
        region: str = REGION_CN,
        base_url: Optional[str] = None,
        **kw: Any,
    ):
        # The default is the CN Coding Plan because that is the account this
        # product was configured against; the previous default was the
        # international pay-as-you-go host, which rejected it. An operator states
        # `region` explicitly, and `base_url` always wins over the host table.
        host = self.HOSTS.get(region, self.HOSTS[self.REGION_CN])
        super().__init__(*a, base_url=base_url or f"{host}/v1", **kw)
        self.plan_type = plan_type
        self.api_protocol = api_protocol
        self.region = region

    @property
    def host(self) -> str:
        return self.base_url.split("/v1")[0].rstrip("/")

    def capabilities(self) -> Dict[str, Any]:
        c = super().capabilities()
        # Three dimensions, reported separately. A consumer that needs the
        # protocol must not have to parse a plan name to find it.
        c["plan_type"] = self.plan_type
        c["plan_types"] = list(self.PLAN_TYPES)
        c["api_protocol"] = self.api_protocol
        c["api_protocols"] = list(self.PROTOCOLS)
        c["region"] = self.region
        c["regions"] = list(self.REGIONS)
        c["native_chat_path"] = self.native_chat_path
        c["openai_chat_path"] = self.openai_chat_path
        return c

    def check_subscription(self) -> Dict[str, Any]:
        """Ask the China Token Plan quota endpoint what account this key is.

        This is IDENTIFICATION, not verification. A 200 here means the key is a
        live China Token Plan credential; it says nothing about whether a
        generation would succeed, so it can never produce READY on its own. The
        separation is enforced by returning a distinct verdict rather than a
        state.
        """
        if not self.has_credential():
            return {
                "subscription_auth": "FAIL",
                "plan_type_detected": None,
                "error_class": "auth_missing",
                "endpoint": f"{self.HOSTS[self.REGION_CN]}{self.QUOTA_PATH_CN}",
                "detail": "未配置 API Key",
            }
        url = f"{self.HOSTS[self.REGION_CN]}{self.QUOTA_PATH_CN}"
        try:
            _, payload, _headers, _latency = self._request("GET", url, None)
        except ProviderAuthError as e:
            return {
                "subscription_auth": "FAIL",
                "plan_type_detected": None,
                "error_class": "auth",
                "http_status": 401,
                "endpoint": url,
                "detail": redact(str(e), self._secret),
            }
        except ProviderRateLimitError as e:
            return {
                "subscription_auth": "FAIL",
                "plan_type_detected": None,
                "error_class": "rate_limit",
                "http_status": 429,
                "endpoint": url,
                "detail": redact(str(e), self._secret),
            }
        except ProviderError as e:
            return {
                "subscription_auth": "FAIL",
                "plan_type_detected": None,
                "error_class": "transport",
                "endpoint": url,
                "detail": redact(str(e), self._secret),
            }
        return {
            "subscription_auth": "PASS",
            # Reported from the SERVICE, not inferred from the prefix. The caller
            # compares it with `plan_type` to spot a mismatch.
            "plan_type_detected": "coding_subscription_cn",
            "error_class": None,
            "http_status": 200,
            "endpoint": url,
            "quota": payload if isinstance(payload, Mapping) else None,
            "detail": None,
        }

    def _post_chat(self, url: str, body: Dict[str, Any], model: str) -> GenerateResult:
        if self.api_protocol == self.PROTOCOL_NATIVE:
            # The native path is absolute from the HOST, while base_url carries
            # the ``/v1`` used by the compatible endpoint — so it is rebuilt from
            # the host rather than appended to base_url, which would produce
            # ``/v1/v1/text/chatcompletion_v2``.
            native = dict(body)
            native["messages"] = body.get("messages", [])
            url = f"{self.host}{self.native_chat_path}"
            _, payload, headers, latency = self._request("POST", url, native)
            return _parse_minimax_native(
                payload, provider=self.id, requested_model=model,
                latency_ms=latency,
                request_id=headers.get("x-request-id"),
                secret=self._secret,
            )
        return super()._post_chat(url, body, model)


def strip_reasoning(text: str) -> str:
    """Remove a reasoning block, keeping the answer that follows it.

    Several vendors return chain-of-thought inline, wrapped in <think>…</think>
    or <reasoning>…</reasoning>. That reasoning is NOT the answer, so it must not
    reach a caller as one — and a "reply exactly X" probe fails even though the
    model complied perfectly.

    Deliberately forgiving about an UNTERMINATED block: a truncated response often
    has a <think> with no closing tag, and keeping the tail would leak the
    reasoning as if it were the answer. An unterminated block therefore yields
    only what preceded it — a missing answer is visible, leaked reasoning is not.
    """
    if not text:
        return text
    for open_tag, close_tag in (("<think>", "</think>"), ("<reasoning>", "</reasoning>")):
        start = text.find(open_tag)
        if start == -1:
            continue
        end = text.find(close_tag, start + len(open_tag))
        text = text[:start] if end == -1 else text[:start] + text[end + len(close_tag):]
    return text.strip()


def _parse_minimax_native(
    payload: Any, *, provider: str, requested_model: str, latency_ms: int,
    request_id: Optional[str], secret: Optional[str],
) -> GenerateResult:
    """Read the native ``chatcompletion_v2`` envelope.

    Kept separate from the OpenAI parser because the field names differ, and
    quietly reading the OpenAI paths out of this shape would return an empty
    completion that LOOKS like a successful one.
    """
    if not isinstance(payload, Mapping):
        raise ProviderInvalidResponseError(
            f"{provider}: native response was not an object", provider=provider
        )
    # Native puts the reply under choices[].message, same as OpenAI, OR under a
    # flat reply field depending on the account tier. Both are checked; neither
    # is assumed.
    if isinstance(payload.get("choices"), list) and payload["choices"]:
        return parse_chat_completion(
            payload, provider=provider, requested_model=requested_model,
            latency_ms=latency_ms, request_id=request_id, secret=secret,
        )
    choices = payload.get("choices")
    raise ProviderInvalidResponseError(
        f"{provider}: native response had no usable choices "
        f"(keys={sorted(payload)[:6]})",
        provider=provider,
    )


class QwenCodingPlanProvider(GenerationProvider):
    """Alibaba Model Studio Coding Plan (China region).

    The model list is DISCOVERED, never asserted. A previous revision of this
    plan hardcoded ``qwen3.8-*``; those ids are not what the plan serves today,
    and a hardcoded list is a lie the moment the vendor changes it. So the preset
    below is a *fallback for when discovery is unavailable*, and the card tells
    the user which of the two they are looking at.
    """

    id = "qwen_coding_plan_cn"
    display_name = "Qwen Coding Plan"
    role = ROLE_NORMAL_CODING
    default_base_url = "https://coding.dashscope.aliyuncs.com/v1"
    #: Preset only. Real availability comes from ``/models``.
    default_model = "qwen3.7-plus"
    known_models = (
        "qwen3.7-plus",
        "qwen3-coder-plus",
        "qwen3-coder-next",
    )
    supports_streaming = True
    region = "cn-beijing"

    def capabilities(self) -> Dict[str, Any]:
        c = super().capabilities()
        c["region"] = self.region
        c["model_source"] = "discovery" if self.supports_model_discovery else "preset"
        return c


class DeepSeekProvider(GenerationProvider):
    """DeepSeek. HIGH_REASONING / CODING_FALLBACK / VERIFIER."""

    id = "deepseek"
    display_name = "DeepSeek"
    role = ROLE_HIGH_REASONING
    default_base_url = "https://api.deepseek.com"
    known_models = ("deepseek-v4-pro", "deepseek-flash")
    default_model = "deepseek-v4-pro"
    supports_streaming = True

    #: Reasoning effort is vendor-specific, so it is a declared capability
    #: rather than a field invented at the call site.
    reasoning_efforts = ("low", "high", "max")

    def __init__(self, *a: Any, reasoning_effort: str = "high", **kw: Any):
        super().__init__(*a, **kw)
        if reasoning_effort not in self.reasoning_efforts:
            raise ValueError(
                f"reasoning_effort must be one of {self.reasoning_efforts}, "
                f"got {reasoning_effort!r}"
            )
        self.reasoning_effort = reasoning_effort

    def capabilities(self) -> Dict[str, Any]:
        c = super().capabilities()
        c["reasoning_efforts"] = list(self.reasoning_efforts)
        c["reasoning_effort"] = self.reasoning_effort
        return c


class CustomOpenAICompatibleProvider(GenerationProvider):
    """Anything that speaks OpenAI ``/chat/completions``.

    The escape hatch. GLM, Kimi, OpenRouter, a local Ollama — none of them need
    a code change, only a base url, a key and a model id.
    """

    id = "custom"
    display_name = "Custom"
    role = ROLE_NORMAL_CODING
    known_models = ()
    supports_model_discovery = True

    def __init__(self, *a: Any, display_name: str = "Custom", **kw: Any):
        super().__init__(*a, **kw)
        self.display_name = display_name or "Custom"
