"""Provider assembly — turn configuration + stored secrets into live providers.

ONE PLACE BUILDS THEM
---------------------
Every route, the router and the live probe all need the same provider objects
with the same credentials and the same overrides. Building them in each caller
is how a probe ends up testing a differently-configured provider than the one
the mission will use — a bug that produces a green tick and a failing task.

So this module is the only constructor. It reads, in order:
  1. the operator's overrides (base url, model, timeout) from config,
  2. the credential from the environment, or the DPAPI store.

NO ENDPOINT IS BUILT HERE
-------------------------
A provider's base url comes from configuration; the defaults live on the
provider classes. Nothing here knows a vendor URL, so adding a deployment-
specific gateway is a config change and not a code change.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, List, Optional

from .providers.generation import (
    DISCOVERY_UNSUPPORTED,
    STATE_AUTH_FAILED,
    STATE_NOT_CONFIGURED,
    STATE_RATE_LIMITED,
    STATE_UNAVAILABLE,
    CustomOpenAICompatibleProvider,
    DeepSeekProvider,
    GenerationProvider,
    MiniMaxProvider,
    ProviderError,
    QwenCodingPlanProvider,
)
from .providers.jev_api import (
    DECISION_CAPABILITIES,
    STATE_REAL_AUTH_MISSING,
    VERIFICATION_DECISION,
)
from .providers.jev_api import JevProvider as JevApiProvider
from .secret_store import SecretStore
from .verification_store import VerificationStore, restore

#: Provider id -> the class that implements it. The order is the DISPLAY order
#: on the settings page, and it is data so a new provider is a one-line add.
PROVIDER_CLASSES = {
    "typesafe_jev": None,                       # Jev, handled separately
    "minimax": MiniMaxProvider,
    "qwen_coding_plan_cn": QwenCodingPlanProvider,
    "deepseek": DeepSeekProvider,
    "custom": CustomOpenAICompatibleProvider,
}

#: What each provider is FOR, in the user's language. Shown on the card; the
#: router uses the machine-readable role from the class.
PROVIDER_PURPOSE = {
    "typesafe_jev": "决策 · 路由 · 风险 · 验证",
    "minimax": "主力编码 · 长上下文 · 智能体任务",
    "qwen_coding_plan_cn": "日常编码 · 快速任务",
    "deepseek": "深度推理 · 编码兜底 · 验证",
    "custom": "任何 OpenAI 兼容服务",
}

#: Plan types an operator can pick, per provider. An empty tuple means the
#: provider has a single account shape.
PROVIDER_PLANS = {
    "minimax": ("pay_as_you_go", "coding_subscription"),
    "qwen_coding_plan_cn": ("coding_plan",),
    "deepseek": ("pay_as_you_go",),
    "typesafe_jev": (),
    "custom": (),
}

#: The display states. Deliberately NOT the same vocabulary as the wire `state`
#: values, because a wire value is a diagnostic and a display value is a
#: promise to the operator. The one rule that matters:
#:
#:   「已连接」 is shown ONLY when a real generation has succeeded.
#:
#: A provider with a valid credential, an unreachable model list and no proven
#: completion is 「已配置 · 待验证」 — never 「已连接」. The earlier build showed
#: 「已连接（无模型列表接口）」 on the strength of a PRESET model name, which is
#: our own declaration rather than anything the service confirmed.
DISPLAY_CONFIGURED_PENDING = "CONFIGURED_PENDING"
DISPLAY_CONNECTED = "CONNECTED"
#: A real verification is on record but was NOT re-checked in this process. Kept
#: deliberately distinct from CONNECTED: a remembered pass proves the provider
#: could do the thing, not that it can do it now, and a green dot for an hours-old
#: success is a lie with a colour on it.
DISPLAY_STALE_VERIFIED = "STALE_VERIFIED"
DISPLAY_AUTH_FAILED = "AUTH_FAILED"
DISPLAY_ENDPOINT_MISMATCH = "ENDPOINT_MISMATCH"
DISPLAY_PLAN_MISMATCH = "PLAN_MISMATCH"
DISPLAY_RATE_LIMITED = "RATE_LIMITED"
DISPLAY_UNAVAILABLE = "UNAVAILABLE"
DISPLAY_NOT_CONFIGURED = "NOT_CONFIGURED"


def _display_state(h: Any, p: Any, restored: Optional[Dict[str, Any]] = None) -> str:
    """The one place a provider's shown state is decided.

    Shared by the card and the probe so a summary line and a card can never
    report different things about the same provider at the same moment — the
    defect this replaces.

    ``restored`` is a previously RECORDED verification. On its own it can raise a
    provider from "never verified" to STALE_VERIFIED, and nothing further. When
    it carries ``live_verified`` — a check that succeeded in THIS process — the
    card is allowed to say CONNECTED, because a real completion just happened
    here; a remembered pass never is.
    """
    if not p.has_credential():
        return DISPLAY_NOT_CONFIGURED
    if h.generation_verified or getattr(h, "decision_verified", False):
        return DISPLAY_CONNECTED
    state = h.state
    if state == STATE_AUTH_FAILED:
        return DISPLAY_AUTH_FAILED
    if state == STATE_RATE_LIMITED:
        return DISPLAY_RATE_LIMITED
    if state == STATE_UNAVAILABLE:
        # A provider can be UNAVAILABLE because the endpoint is wrong, which is
        # a different fix from the service being down. The detail carries the
        # classification; see _display_reason.
        reason = _failure_reason(h, p)
        if reason in ("endpoint_mismatch", "plan_mismatch"):
            return DISPLAY_ENDPOINT_MISMATCH if reason == "endpoint_mismatch" else DISPLAY_PLAN_MISMATCH
        return DISPLAY_UNAVAILABLE
    # A pass proven in THIS process outranks a remembered one: the card may say
    # 已连接 because a real completion just happened here.
    if restored and restored.get("live_verified"):
        return DISPLAY_CONNECTED
    # Nothing proven in this process. If a real pass is on record, say so — and
    # say that it is remembered rather than current.
    if restored and restored.get("restored"):
        return DISPLAY_STALE_VERIFIED
    # DISCOVERY_UNSUPPORTED, DEGRADED and anything else mean the same thing to an
    # operator looking at a card: configured, not yet proven. `discovery` is
    # reported on its own axis and must not be read as a fault.
    return DISPLAY_CONFIGURED_PENDING


def _failure_reason(h: Any, p: Any) -> str:
    """Classify an UNAVAILABLE provider: wrong endpoint, or wrong plan?

    Both look identical to an operator otherwise, and they have opposite fixes.
    """
    detail = str(h.detail or "")
    if "invalid api key" in detail and getattr(p, "plan_type", "").startswith("coding_"):
        # A subscription key rejected as an invalid key is nearly always the
        # wrong HOST for that key's region, not a bad credential.
        return "endpoint_mismatch"
    if "invalid api key" in detail:
        return "auth"
    return "unavailable"


def _display_reason(h: Any, p: Any, restored: Optional[Dict[str, Any]] = None) -> str:
    """A short, honest sentence for the shown state. No inference from a preset."""
    d = _display_state(h, p, restored)
    if d == DISPLAY_CONNECTED:
        # The cheap card `health()` carries no latency; an in-process live pass
        # does, and it is the more specific truth when both are available.
        lat = h.latency_ms if h.latency_ms is not None else (restored or {}).get("live_latency_ms")
        return f"真实生成已验证 · 延迟 {lat} ms" if lat is not None else "真实生成已验证"
    if d == DISPLAY_STALE_VERIFIED:
        when = (restored or {}).get("last_verified_at")
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(when)) if when else "未知时间"
        return f"上次验证通过（{stamp}）· 本次未重新检测"
    if d == DISPLAY_CONFIGURED_PENDING:
        if h.discovery == DISCOVERY_UNSUPPORTED:
            return "已配置；该 Provider 无模型列表接口，且尚未完成真实生成验证"
        return "已配置；尚未完成真实生成验证"
    if d == DISPLAY_NOT_CONFIGURED:
        return "未配置 API Key"
    return str(h.detail or d)


PROVIDER_REGIONS = {
    "typesafe_jev": "global",
    "minimax": "global",
    "qwen_coding_plan_cn": "cn-beijing",
    "deepseek": "global",
    "custom": "custom",
}


class ProviderRegistry:
    """Builds and caches provider instances, and reports their state.

    Cached because a probe must measure the SAME object the router will use, and
    because rebuilding a provider per request would re-read the environment and
    the DPAPI blob on every call.
    """

    def __init__(
        self,
        store: SecretStore,
        overrides: Optional[Dict[str, Dict[str, Any]]] = None,
        verification_store: Optional["VerificationStore"] = None,
    ):
        self._store = store
        self._overrides = dict(overrides or {})
        self._lock = threading.RLock()
        self._cache: Dict[str, GenerationProvider] = {}
        # Providers whose GENERATION capability was proven LIVE in THIS process
        # (pid -> latency_ms). The card reads the cheap `health()`, which by
        # design never generates, so without this a provider that just passed a
        # real 「测试连接」 would still render 「上次验证通过」 — the card and the
        # probe disagreeing about the same provider is the exact defect the
        # shared `_display_state` exists to prevent.
        self._live_verified: Dict[str, Any] = {}
        # Optional so the registry keeps working in a test or a tool that has no
        # database. When present it carries a proven verification across refresh
        # and restart, which is the difference between 「已配置 · 待验证」 and
        # 「上次验证通过」.
        self._verification = verification_store

    # -- configuration ---------------------------------------------------- #

    def set_override(self, provider_id: str, **values: Any) -> None:
        """Record a non-secret setting (base url, model, timeout, plan type)."""
        with self._lock:
            cur = dict(self._overrides.get(provider_id, {}))
            for k, v in values.items():
                if v is not None:
                    cur[k] = v
            self._overrides[provider_id] = cur
            # A changed base url or model must take effect, so the cached
            # instance is dropped. Credentials are untouched.
            self._cache.pop(provider_id, None)

    def overrides(self, provider_id: str) -> Dict[str, Any]:
        return dict(self._overrides.get(provider_id, {}))

    def _setting(self, provider_id: str, key: str, default: Any = None) -> Any:
        """Operator override, else environment, else the declared default.

        The environment is consulted here so a CI job can configure a provider
        without touching the store — the same precedence the SecretStore uses
        for the key, kept consistent on purpose.
        """
        ov = self._overrides.get(provider_id, {})
        if key in ov:
            return ov[key]
        env_name = {
            "base_url": f"{provider_id.upper()}_BASE_URL",
            "model": f"{provider_id.upper()}_MODEL",
            "timeout_s": f"{provider_id.upper()}_TIMEOUT_S",
        }.get(key)
        if env_name:
            from_env = os.environ.get(env_name)
            if from_env:
                if key == "timeout_s":
                    try:
                        return float(from_env)
                    except ValueError:
                        pass
                else:
                    return from_env
        return default

    # -- construction ----------------------------------------------------- #

    def secret_for(self, provider_id: str) -> Optional[str]:
        return self._store.get_secret(provider_id)

    def get(self, provider_id: str) -> Optional[GenerationProvider]:
        """The live provider, built on first use.

        ``custom`` is special: it has no default base url, so it is only
        constructible once the operator has supplied one. Returning ``None``
        there is better than instantiating a provider pointed at nothing.
        """
        cls = PROVIDER_CLASSES.get(provider_id)
        if cls is None:
            return None
        with self._lock:
            if provider_id in self._cache:
                return self._cache[provider_id]

            base_url = self._setting(provider_id, "base_url")
            model = self._setting(provider_id, "model")
            timeout = self._setting(provider_id, "timeout_s", 60.0)
            secret = self.secret_for(provider_id)

            kwargs: Dict[str, Any] = {
                "base_url": base_url,
                "model": model,
                "timeout_s": float(timeout) if timeout else 60.0,
            }
            if provider_id == "minimax":
                # Three INDEPENDENT dimensions, each read separately. They used
                # to be collapsed into one `api_style` whose value was a plan
                # name, which is why a China Coding Plan key was sent to the
                # international pay-as-you-go host and came back as
                # "invalid api key (2049)" — a valid key reported as a bad one.
                kwargs["plan_type"] = self._setting(
                    "minimax", "plan_type", "coding_subscription_cn"
                )
                kwargs["api_protocol"] = self._setting(
                    "minimax", "api_protocol", "openai_compatible"
                )
                # Default CN: the account this product was configured against.
                # There is deliberately NO fallback to the global host — a wrong
                # region reads as an invalid key, so it must be stated, not
                # guessed.
                kwargs["region"] = self._setting("minimax", "region", "cn")
            if provider_id == "deepseek":
                kwargs["reasoning_effort"] = self._setting(
                    "deepseek", "reasoning_effort", "high"
                )
            if provider_id == "custom":
                if not base_url:
                    return None
                kwargs["display_name"] = self._setting("custom", "display_name", "Custom")

            p = cls(secret, **kwargs)
            self._cache[provider_id] = p
            return p

    def invalidate(self, provider_id: str) -> None:
        """Drop the cached instance. Called after a credential changes, so the
        next call picks the new key up without a restart."""
        with self._lock:
            self._cache.pop(provider_id, None)

    def jev(self) -> JevApiProvider:
        """The Jev (System One) client. Built fresh because its credential may
        have just been saved, and it is cheap to construct."""
        return JevApiProvider(
            api_key=self.secret_for("typesafe_jev"),
            base_url=self._setting("typesafe_jev", "base_url"),
            model=self._setting("typesafe_jev", "model"),
        )

    def generation_providers(self) -> List[GenerationProvider]:
        """Every constructible generation provider, in display order."""
        out = []
        for pid in PROVIDER_CLASSES:
            if pid == "typesafe_jev":
                continue
            p = self.get(pid)
            if p is not None:
                out.append(p)
        return out

    # -- reporting -------------------------------------------------------- #

    def health(self) -> Dict[str, Any]:
        """State for every provider. Never raises, and never includes a secret.

        The shape is identical whether a provider is configured or not, so a
        caller cannot infer success from the presence of a key.
        """
        jev = self.jev()
        jev_health = jev.health()
        cards: List[Dict[str, Any]] = [
            {
                "id": "typesafe_jev",
                "display_name": "TypeSafe Jev",
                # Jev DECIDES. It has no generation capability, so its card must
                # never be able to display a generation receipt.
                "verification_kind": "decision",
                "capabilities": list(DECISION_CAPABILITIES),
                "verification": jev_health.get("verification"),
                "decision_verified": jev_health.get("verification") == VERIFICATION_DECISION,
                "generation_verified": False,
                # The SAME display vocabulary every generation card uses, so the
                # UI has one state space instead of one per provider kind. Jev
                # needed this explicitly: its hand-written card path never went
                # through _display_state, which is why it was the one provider
                # with no `display_state` at all.
                "display_state": (
                    DISPLAY_CONNECTED
                    if jev_health.get("verification") == VERIFICATION_DECISION
                    else (
                        DISPLAY_NOT_CONFIGURED
                        if jev_health.get("state") == STATE_REAL_AUTH_MISSING
                        else DISPLAY_CONFIGURED_PENDING
                    )
                ),
                "display_state_reason": (
                    "真实决策已验证"
                    if jev_health.get("verification") == VERIFICATION_DECISION
                    else str(jev_health.get("detail") or "")
                ),
                "model_source": "PRESET" if not jev_health.get("models_list") else "ACCOUNT",
                "purpose": PROVIDER_PURPOSE["typesafe_jev"],
                "region": PROVIDER_REGIONS["typesafe_jev"],
                "state": jev_health["state"],
                "base_url": jev.base_url,
                "default_model": jev_health.get("model") or jev.model,
                "models": list(jev_health.get("models_list") or []),
                "models_are_preset": not jev_health.get("models_list"),
                "discovery": ("SUPPORTED" if jev_health.get("models") == "PASS"
                              else "UNKNOWN"),
                "detail": jev_health.get("detail"),
                "latency_ms": jev_health.get("latency_ms"),
                "is_jev": True,
                "plan_types": list(PROVIDER_PLANS["typesafe_jev"]),
            }
        ]
        for pid in PROVIDER_CLASSES:
            if pid == "typesafe_jev":
                continue
            cards.append(self._card(pid))
        return {"schema": "jcc/providers/1", "providers": cards}

    def _restored(self, provider_id: str) -> Dict[str, Any]:
        """A recorded verification for this provider, or a NOT_VERIFIED marker.

        Never raises: a missing database is a normal configuration, and a card
        that cannot read history should still render.
        """
        if self._verification is None:
            return {"restored": False, "verification_source": "NOT_VERIFIED"}
        try:
            return restore(provider_id, self._verification)
        except Exception:  # pragma: no cover - a card must not fail over history
            return {"restored": False, "verification_source": "NOT_VERIFIED"}

    def forget_verification(self, provider_id: str) -> bool:
        """Drop a recorded verification — used when a credential changes.

        A remembered pass belongs to the key that earned it. After the operator
        saves a different key, the old pass describes a credential that is no
        longer configured, and keeping it would be a false claim.
        """
        # The in-process live pass belongs to the same key, so it is dropped too.
        with self._lock:
            self._live_verified.pop(provider_id, None)
        if self._verification is None:
            return False
        return self._verification.forget(provider_id)

    def _card(self, pid: str) -> Dict[str, Any]:
        """One settings card. Assembled from facts, never from a template that
        could imply a connection that does not exist."""
        cred = self._store.describe(pid)
        ov = self.overrides(pid)
        p = self.get(pid)
        if p is None:
            # `custom` with no base url: honest about being unconfigured rather
            # than defaulting to a vendor.
            return {
                "id": pid,
                # Every card carries the field, including this early return. A
                # provider that reached this branch once and a provider that has
                # no base url must not differ in SHAPE, or the frontend has to
                # invent a fallback and the two paths drift apart again.
                "display_state": DISPLAY_NOT_CONFIGURED,
                "display_state_reason": (
                    "未配置 Base URL" if pid == "custom" else "未配置 API Key"
                ),
                "verification_kind": "generation",
                "generation_verified": False,
                "model_source": "PRESET",
                "display_name": ov.get("display_name") or _display_name(pid),
                "purpose": PROVIDER_PURPOSE[pid],
                "region": PROVIDER_REGIONS[pid],
                "state": STATE_NOT_CONFIGURED,
                "base_url": ov.get("base_url") or "",
                "default_model": ov.get("model") or (p.default_model if p else ""),
                "models": [],
                "detail": "未配置 Base URL" if pid == "custom" else "未配置 API Key",
                "is_jev": False,
                "plan_types": list(PROVIDER_PLANS[pid]),
                "credential": cred,
                "supports_tools": p.supports_tools if p else False,
                "supports_streaming": p.supports_streaming if p else False,
            }
        # health() is the cheap read; the card shows what is TRUE, not what a
        # generation probe would prove. verify_capability() is what earns READY
        # and is only called from probe().
        h = p.health()
        # ONE definition of "displayed state", shared by the card and the probe,
        # so the summary line and the card can never disagree. The previous code
        # let the summary read `state` while the card re-derived its own wording,
        # which is how a provider appeared as 「无法连接」 in one place and
        # 「已连接」 in another at the same moment.
        restored = self._restored(pid)
        # If THIS process already proved the provider live, the card may claim
        # 已连接 (LIVE_READY) rather than the remembered 上次验证通过. Keyed by
        # pid, so the card and the probe describe the same provider the same way.
        if pid in self._live_verified:
            restored = {**restored, "restored": True, "live_verified": True,
                        "verification_source": "LIVE_READY",
                        "live_latency_ms": self._live_verified[pid]}
        shown = _display_state(h, p, restored)
        return {
            "id": pid,
            "verification_kind": p.verification_kind,
            "display_state": shown,
            "display_state_reason": _display_reason(h, p, restored),
            # The remembered evidence, stated so the UI can say WHEN rather than
            # implying a live check. `verification_source` is the discriminator:
            # LIVE_READY vs LAST_VERIFIED_READY vs NOT_VERIFIED.
            "verification_source": restored.get("verification_source"),
            "last_verified_at": restored.get("last_verified_at"),
            "last_verified_model": restored.get("last_verified_model"),
            "last_latency_ms": restored.get("last_latency_ms"),
            "last_verified_age_s": restored.get("last_verified_age_s"),
            "stored_discovery": restored.get("stored_discovery"),
            "stored_model_source": restored.get("stored_model_source"),
            # Three independent dimensions, straight from the provider object.
            # Reported separately so a consumer never has to parse one to infer
            # another — collapsing them is what produced a CN key on the global
            # host and the bogus "invalid api key" that followed.
            "plan_type": getattr(p, "plan_type", None),
            "api_protocol": getattr(p, "api_protocol", None),
            "capabilities": [p.role] if p.role else [],
            "discovery": h.discovery,
            "generation_verified": h.generation_verified,
            "models_are_preset": h.models_are_preset,
            # Where the models list came from. A preset is OUR declaration, and a
            # UI must be able to say so — `models: ["MiniMax-M3"]` alone is
            # indistinguishable from the account's own inventory, and treating
            # the two alike is how a preset was once read as proof of READY.
            "model_source": "PRESET" if h.models_are_preset else "ACCOUNT",
            "display_name": p.display_name,
            "purpose": PROVIDER_PURPOSE[pid],
            # The provider's OWN region, not the static table. The table said
            # `global` for MiniMax while the object was configured `cn`, so the
            # card contradicted the endpoint it was actually going to call.
            "region": getattr(p, "region", None) or PROVIDER_REGIONS[pid],
            "state": h.state,
            "base_url": p.base_url,
            "default_model": p.default_model,
            "models": list(h.models or []),
            "latency_ms": h.latency_ms,
            "detail": h.detail,
            "is_jev": False,
            "plan_types": list(PROVIDER_PLANS[pid]),
            "credential": cred,
            "supports_tools": p.supports_tools,
            "supports_streaming": p.supports_streaming,
            "role": p.role,
        }

    def probe(self, pid: str) -> Dict[str, Any]:
        """Reachability + timing for one provider. Records no secret.

        The result deliberately includes ``usage`` and ``request_id`` as
        possibly-None rather than omitting them: a probe that answered but
        returned no usage has still told us something true.
        """
        import time

        if pid == "typesafe_jev":
            # Jev is a DECISION provider. It is probed with a decision round
            # trip — /v1/models then /v1/systemone — and NEVER with a
            # generation probe. Asking a model that does not write prose to
            # "reply exactly JEV_OK" tests a capability it does not have, and
            # the resulting failure would say nothing about Jev.
            jev = self.jev()
            started = time.monotonic()
            h = jev.health()          # == verify_decision()
            decision_ok = h.get("verification") == VERIFICATION_DECISION
            out: Dict[str, Any] = {
                "provider": pid,
                "state": h["state"],
                "verification": h.get("verification"),
                "decision_verified": decision_ok,
                "capabilities": list(h.get("capabilities") or []),
                "auth": h.get("auth"),
                "models_result": h.get("models"),
                "systemone": h.get("systemone"),
                "schema": h.get("schema"),
                "error_class": h.get("error_class"),
                "http_status": h.get("http_status"),
                "endpoint": h.get("endpoint"),
                "base_url": jev.base_url,
                "model": h.get("model"),
                "models": list(h.get("models_list") or []),
                "latency_ms": int((time.monotonic() - started) * 1000),
                "systemone_latency_ms": h.get("latency_ms"),
                "request_id": h.get("request_id"),
                "usage": h.get("usage"),
                "detail": h.get("detail"),
            }
            # Persist a real DECISION pass so a refresh or restart does not
            # report a provider that demonstrably decided as 「已配置 · 待验证」.
            # This is the decision-side mirror of the generation record below:
            # the earlier build let MiniMax's generation pass persist but dropped
            # Jev's decision pass, which is exactly why /api/health read MOCK
            # while the Provider Center had already proven Jev REAL_READY.
            # ONLY a pass is written; a failure leaves a previously working
            # provider proven.
            if self._verification is not None and decision_ok:
                out["verification_recorded"] = bool(
                    self._verification.record(
                        provider_id=pid,
                        verified_at=time.time(),
                        verification_status="PASS",
                        verification_kind="decision",
                        verified_model=h.get("model") or jev.model,
                        latency_ms=h.get("latency_ms"),
                        generation_verified=False,
                        decision_verified=True,
                        discovery="SUPPORTED" if h.get("models") == "PASS" else "UNKNOWN",
                        model_source="PRESET" if not h.get("models_list") else "ACCOUNT",
                        base_url=jev.base_url,
                    )
                )
            # The SAME display state the card uses, so a summary and a card can
            # never disagree about the same provider at the same instant.
            out["display_state"] = (
                DISPLAY_CONNECTED if decision_ok else DISPLAY_CONFIGURED_PENDING
            )
            out["display_state_reason"] = (
                "真实决策已验证" if decision_ok else str(h.get("detail") or "")
            )
            return out

        p = self.get(pid)
        if p is None:
            return {
                "provider": pid, "state": STATE_NOT_CONFIGURED, "base_url": "",
                "model": "", "models": [], "latency_ms": 0,
                "detail": "未配置 Base URL", "request_id": None, "usage": None,
            }
        started = time.monotonic()
        # ``verify_generation`` is the ONLY thing that can return READY, and it
        # performs one real authenticated completion. The operator pressing
        # 「测试连接」 is asking "can this actually run?", and a model list cannot
        # answer that.
        h = p.verify_capability()
        out: Dict[str, Any] = {
            "provider": pid,
            "state": h.state,
            "discovery": h.discovery,
            "generation_verified": h.generation_verified,
            "models_are_preset": h.models_are_preset,
            "base_url": p.base_url,
            "model": p.default_model,
            "models": list(h.models or []),
            "latency_ms": int((time.monotonic() - started) * 1000),
            "detail": h.detail,
            "request_id": None,
            "usage": None,
        }

        # Persist a real pass so a refresh or a restart does not report a provider
        # that demonstrably worked as 「已配置 · 待验证」. ONLY a pass is written:
        # a failure does not make a previously working provider unproven.
        if self._verification is not None and (
            h.generation_verified or getattr(h, "decision_verified", False)
        ):
            out["verification_recorded"] = bool(
                self._verification.record(
                    provider_id=pid,
                    verified_at=time.time(),
                    verification_status="PASS",
                    verification_kind=p.verification_kind,
                    verified_model=p.default_model,
                    latency_ms=h.latency_ms,
                    generation_verified=h.generation_verified,
                    decision_verified=bool(getattr(h, "decision_verified", False)),
                    discovery=h.discovery,
                    model_source="PRESET" if h.models_are_preset else "ACCOUNT",
                    base_url=p.base_url,
                )
            )
        # Remember, for the LIFETIME OF THIS PROCESS, that a real completion just
        # succeeded, so the card can render 已连接 (LIVE_READY) instead of a
        # remembered 上次验证通过. A failure never clears a prior live pass.
        if h.generation_verified:
            with self._lock:
                self._live_verified[pid] = getattr(h, "latency_ms", None)
        # The SAME display state the card uses. Two code paths deriving their own
        # wording is what produced 「无法连接」 in the summary next to 「已连接」
        # on the card for the same provider at the same instant.
        out["display_state"] = _display_state(h, p)
        out["display_state_reason"] = _display_reason(h, p)
        out["model_source"] = "PRESET" if h.models_are_preset else "ACCOUNT"
        if pid == "minimax":
            # The three dimensions, reported separately, plus the subscription
            # probe. `subscription_auth` is IDENTIFICATION: a 200 from the quota
            # endpoint says the key is a live China Token Plan credential and
            # nothing more, which is why it never contributes to `state`.
            out["plan_type"] = p.plan_type
            out["api_protocol"] = p.api_protocol
            out["region"] = p.region
            out["subscription"] = p.check_subscription()
        return out

    def live_call(self, pid: str, prompt: str = "ping") -> Dict[str, Any]:
        """A MINIMAL real generation, for the operator's own verification.

        Deliberately separate from :meth:`probe`: this spends a token, so it is
        only reachable from an explicitly token-gated route. A probe that quietly
        cost money on every settings-page refresh would be a bad neighbour.
        """
        p = self.get(pid)
        if p is None:
            return {"provider": pid, "ok": False, "error": "未配置该 Provider"}
        if not p.has_credential():
            return {"provider": pid, "ok": False, "error": "未配置 API Key"}
        try:
            # 16 tokens was chosen when every provider returned a bare answer.
            # A REASONING model spends its budget on a <think> block first and
            # gets cut off before the answer, so the probe reported a healthy
            # provider that had not actually said anything — the failure mode is
            # invisible because the request succeeded. Measured: MiniMax-M3
            # needed ~78 characters to emit "JEV_MINIMAX_OK" after its reasoning.
            r = p.generate(prompt, max_tokens=512)
        except ProviderError as e:
            return {"provider": pid, "ok": False, "error": str(e)}
        return {
            "provider": pid,
            "ok": True,
            "model": r.model,
            "latency_ms": r.latency_ms,
            "request_id": r.request_id,
            "usage": r.usage.as_dict(),
            "text_len": len(r.text),
            # The generated text itself. This route is token-gated, the prompt is
            # supplied by the operator, and `text_len` alone cannot show WHETHER
            # the model returned what was asked — a length is compatible with any
            # content, so a caller verifying a real completion has nothing to
            # assert on without it. The response body is the operator's own
            # prompt's output; no credential is in it (redaction happens upstream
            # in the provider).
            "text": r.text,
        }


def _display_name(pid: str) -> str:
    return {
        "minimax": "MiniMax M3",
        "qwen_coding_plan_cn": "Qwen Coding Plan",
        "deepseek": "DeepSeek",
        "custom": "Custom OpenAI Compatible",
    }.get(pid, pid)
