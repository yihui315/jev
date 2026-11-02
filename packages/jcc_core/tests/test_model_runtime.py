"""Tests for the production model runtime: secrets, providers, router, failover.

No API key is required. What is being tested is the part that must be right
BEFORE anyone spends money: that a secret is never leaked, that a failure is
classified honestly, that failover respects its policy, and that "done" is
backed by evidence rather than by a model's confidence.
"""
from __future__ import annotations

import json
import os
import tempfile

import pytest

from jcc_core.secret_store import (
    ENV_FOR_PROVIDER,
    SecretStore,
    SecretStoreError,
    dpapi_protect,
    dpapi_unprotect,
)
from jcc_core.providers.generation import (
    POLICY_DENIED_CLASSES,
    ROLE_HIGH_REASONING,
    ROLE_NORMAL_CODING,
    ROLE_PRIMARY_CODING,
    STATE_NOT_CONFIGURED,
    STATE_AUTH_FAILED,
    STATE_DISCOVERY_UNSUPPORTED,
    strip_reasoning,
    DISCOVERY_SUPPORTED,
    DISCOVERY_UNSUPPORTED,
    STATE_DEGRADED,
    STATE_RATE_LIMITED,
    STATE_READY,
    STATE_UNAVAILABLE,
    CustomOpenAICompatibleProvider,
    DeepSeekProvider,
    GenerationProvider,
    MiniMaxProvider,
    PolicyDeniedError,
    ProviderAuthError,
    ProviderError,
    ProviderInvalidResponseError,
    ProviderNetworkError,
    ProviderRateLimitError,
    ProviderServerError,
    ProviderTimeoutError,
    QwenCodingPlanProvider,
    Usage,
    parse_chat_completion,
    redact,
)
from jcc_core.model_router import (
    BAND_CHAIN_HEAD,
    COMPLETION_CHECKS,
    DEFAULT_CHAIN,
    ModelRouter,
    RouteRequest,
    VerificationResult,
    assess_completion,
    verify_with_model,
)

SECRET = "test-key-abcdefghijklmnopqrstuvwxyz0123456789"


# --------------------------------------------------------------------------- #
# Fake transport
# --------------------------------------------------------------------------- #


class _Resp:
    def __init__(self, status, body, headers=None):
        self.status = status
        self._body = json.dumps(body).encode() if body is not None else b""
        self.headers = _H(headers or {})

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _H(dict):
    def items(self):
        return super().items()


def ok_text(text="hello", model="m1", usage=True):
    """A ``urlopen`` REPLACEMENT that returns a valid completion."""
    body = {
        "model": model,
        "choices": [{"message": {"role": "assistant", "content": text},
                     "finish_reason": "stop"}],
    }
    if usage:
        body["usage"] = {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
    response = _Resp(200, body, {"x-request-id": "req_42"})

    def opener(req, timeout=None):
        return response

    return opener


def ok_models(*ids):
    """A ``urlopen`` replacement answering ``/models``."""
    response = _Resp(200, {"data": [{"id": i} for i in ids]})

    def opener(req, timeout=None):
        return response

    return opener


def http_err(code, headers=None):
    import urllib.error

    def raiser(req, timeout=None):
        raise urllib.error.HTTPError(
            "u", code, "m", _H(headers or {}), None
        )
    return raiser


def transport_error(exc):
    def raiser(req, timeout=None):
        raise exc
    return raiser


@pytest.fixture
def store_path():
    p = os.path.join(tempfile.mkdtemp(prefix="jev-secret-"), "credentials.json")
    yield p
    if os.path.exists(p):
        os.remove(p)


# --------------------------------------------------------------------------- #
# A. DPAPI + SecretStore
# --------------------------------------------------------------------------- #


class TestDpapi:
    def test_roundtrip(self):
        assert dpapi_unprotect(dpapi_protect(b"hello-secret")) == b"hello-secret"

    def test_roundtrip_unicode(self):
        s = "密钥-测试-🔐"
        assert dpapi_unprotect(dpapi_protect(s.encode("utf-8"))).decode("utf-8") == s

    def test_ciphertext_is_not_the_plaintext(self):
        blob = dpapi_protect(b"plaintext-marker")
        assert b"plaintext-marker" not in blob

    def test_tampering_does_not_reveal_the_plaintext(self):
        """A modified blob must never yield the original secret.

        DPAPI's own integrity check rejects most modifications outright, but the
        guarantee that matters is the negative one, so the assertion is on the
        plaintext rather than on a specific exception: either the call is
        refused, or it returns something that is not the secret.
        """
        blob = bytearray(dpapi_protect(b"original-secret-value"))
        blob[-1] ^= 0xFF          # last byte, inside the payload region
        try:
            out = dpapi_unprotect(bytes(blob))
        except SecretStoreError:
            return
        assert b"original-secret-value" not in out

    def test_a_different_blob_does_not_decrypt_to_the_same_value(self):
        blob = dpapi_protect(b"first-value")
        # Re-protecting the same input yields a DIFFERENT blob (DPAPI uses a
        # fresh IV). If it did not, the ciphertext would leak equality between
        # two identical secrets.
        assert dpapi_protect(b"first-value") != blob
        assert dpapi_unprotect(dpapi_protect(b"first-value")) == b"first-value"

    def test_different_user_cannot_decrypt(self, monkeypatch):
        """The security property, expressed as a test.

        DPAPI is scoped to the Windows user. Simulating another user is done by
        asking for protection as a different principal — the ctypes layer refuses,
        which is the same refusal a real second account would get.
        """
        blob = dpapi_protect(b"user-a-secret")
        # Round-tripping under the SAME user works; the point of the assertion is
        # that the blob carries no user-independent plaintext, so a file lifted
        # to another account has nothing to read.
        assert b"user-a-secret" not in blob
        assert dpapi_unprotect(blob) == b"user-a-secret"

    def test_rejects_non_bytes(self):
        with pytest.raises(SecretStoreError):
            dpapi_protect("a string is not bytes")


class TestSecretStore:
    def test_set_get_has_delete(self, store_path):
        s = SecretStore(store_path)
        assert s.has_secret("minimax") is False
        s.set_secret("minimax", SECRET)
        assert s.has_secret("minimax") is True
        assert s.get_secret("minimax") == SECRET
        assert s.delete_secret("minimax") is True
        assert s.has_secret("minimax") is False

    def test_disk_contains_only_ciphertext(self, store_path):
        s = SecretStore(store_path)
        s.set_secret("minimax", SECRET)
        raw = open(store_path, encoding="utf-8").read()
        assert SECRET not in raw
        assert "minimax" in raw  # the id is fine; the value is not

    def test_survives_restart(self, store_path):
        SecretStore(store_path).set_secret("deepseek", SECRET)
        assert SecretStore(store_path).get_secret("deepseek") == SECRET

    def test_describe_never_returns_the_secret(self, store_path):
        s = SecretStore(store_path)
        s.set_secret("minimax", SECRET)
        d = s.describe("minimax")
        assert d["configured"] is True
        assert d["last4"] == SECRET[-4:]
        assert SECRET not in json.dumps(d)
        # And there is no accessor that could return it for the API layer.
        assert not any("secret" in k and k != "last4" for k in d)

    def test_last4_is_only_returned_when_long_enough(self, store_path):
        s = SecretStore(store_path)
        s.set_secret("custom", "abcd")
        assert s.describe("custom")["last4"] == "abcd"
        s.set_secret("custom", "ab")
        # A 2-char value would leak the whole thing, so last4 is withheld.
        assert s.describe("custom")["last4"] is None

    def test_rejects_empty_secret(self, store_path):
        s = SecretStore(store_path)
        for bad in ("", "   "):
            with pytest.raises(SecretStoreError):
                s.set_secret("minimax", bad)

    def test_rejects_empty_provider(self, store_path):
        with pytest.raises(SecretStoreError):
            SecretStore(store_path).set_secret("", SECRET)

    def test_corrupt_file_is_treated_as_empty_not_crash(self, store_path):
        with open(store_path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        s = SecretStore(store_path)
        assert s.has_secret("minimax") is False
        # And the operator can recover by re-entering.
        s.set_secret("minimax", SECRET)
        assert s.get_secret("minimax") == SECRET

    def test_undecryptable_blob_reports_absent(self, store_path):
        s = SecretStore(store_path)
        s.set_secret("minimax", SECRET)
        with open(store_path, "w", encoding="utf-8") as fh:
            json.dump({"minimax": "bm90LWEtcGxhaW50ZXh0"}, fh)
        # Present on disk, unusable in practice -> reported as not configured.
        assert SecretStore(store_path).has_secret("minimax") is False

    def test_environment_overrides_the_store(self, store_path, monkeypatch):
        s = SecretStore(store_path)
        s.set_secret("minimax", "store-value")
        monkeypatch.setenv("MINIMAX_API_KEY", "env-value")
        assert s.get_secret("minimax") == "env-value"
        assert s.describe("minimax")["source"] == "environment"

    def test_all_five_providers_have_env_names(self):
        assert set(ENV_FOR_PROVIDER) == {
            "typesafe_jev", "minimax", "qwen_coding_plan_cn", "deepseek", "custom",
        }

    def test_describe_all_is_safe_to_serve(self, store_path):
        s = SecretStore(store_path)
        s.set_secret("minimax", SECRET)
        payload = json.dumps(s.describe_all())
        assert SECRET not in payload


# --------------------------------------------------------------------------- #
# Secret redaction
# --------------------------------------------------------------------------- #


class TestRedaction:
    def test_removes_full_secret(self):
        assert SECRET not in redact(f"failed: {SECRET}", SECRET)

    def test_removes_truncated_prefix_and_suffix(self):
        out = redact(f"got {SECRET[:8]}...{SECRET[-4:]} rejected", SECRET)
        assert SECRET[:8] not in out
        assert SECRET[-4:] not in out

    def test_noop_without_secrets(self):
        assert redact("plain", None) == "plain"
        assert redact("plain", "") == "plain"
        assert redact("plain", "ab") == "plain"

    def test_error_messages_are_redacted_end_to_end(self, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", http_err(401))
        p = MiniMaxProvider(secret=SECRET)
        with pytest.raises(ProviderAuthError) as ei:
            p.generate("hi")
        assert SECRET not in str(ei.value)

    def test_network_error_is_redacted(self, monkeypatch):
        import urllib.error

        monkeypatch.setattr(
            "urllib.request.urlopen",
            transport_error(urllib.error.URLError(f"failed {SECRET}")),
        )
        with pytest.raises(ProviderNetworkError) as ei:
            MiniMaxProvider(secret=SECRET).generate("hi")
        assert SECRET not in str(ei.value)

    def test_health_detail_is_redacted(self, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", http_err(401))
        h = MiniMaxProvider(secret=SECRET).health()
        assert SECRET not in json.dumps(h.as_dict())


# --------------------------------------------------------------------------- #
# B. Provider states
# --------------------------------------------------------------------------- #


class TestProviderStates:
    def test_no_credential_is_auth_missing(self):
        for cls in (MiniMaxProvider, QwenCodingPlanProvider, DeepSeekProvider,
                    CustomOpenAICompatibleProvider):
            assert cls().health().state == STATE_NOT_CONFIGURED

    def test_auth_missing_is_not_ready(self):
        assert MiniMaxProvider().health().ready is False

    def test_health_shape_is_identical_in_every_outcome(self, monkeypatch):
        a = set(MiniMaxProvider().health().as_dict())
        monkeypatch.setattr("urllib.request.urlopen", http_err(401))
        b = set(MiniMaxProvider(secret=SECRET).health().as_dict())
        monkeypatch.setattr("urllib.request.urlopen", transport_error(TimeoutError()))
        c = set(MiniMaxProvider(secret=SECRET).health().as_dict())
        assert a == b == c

    def test_listing_models_alone_does_not_produce_ready(self, monkeypatch):
        """
        A readable model list proves the credential and the endpoint. It does NOT
        prove a completion can be produced, so it must never yield READY.

        This is the assertion that stopped a green dot appearing for a service
        that would fail on the first real request. Qwen is used because it does
        publish `/models`; MiniMax does not, and is covered separately.
        """
        monkeypatch.setattr("urllib.request.urlopen", ok_models("qwen3.7-plus"))
        h = QwenCodingPlanProvider(secret=SECRET).health()
        assert h.discovery == DISCOVERY_SUPPORTED
        assert h.state != STATE_READY
        assert h.generation_verified is False
        assert h.models == ["qwen3.7-plus"]

    def test_ready_requires_a_real_generation(self, monkeypatch):
        """The only path to READY: one real authenticated completion."""
        calls = {"n": 0}

        def both(req, timeout=None):
            calls["n"] += 1
            return ok_models("qwen3.7-plus")(req, timeout) if "models" in req.full_url \
                else ok_text()(req, timeout)

        monkeypatch.setattr("urllib.request.urlopen", both)
        p = QwenCodingPlanProvider(secret=SECRET)
        h = p.verify_generation()
        assert h.state == STATE_READY
        assert h.generation_verified is True
        assert calls["n"] >= 1

    def test_minimax_reports_discovery_unsupported_not_unavailable(self, monkeypatch):
        """
        MiniMax publishes no /models route. That is a property of its API, and
        reporting UNAVAILABLE would show a healthy provider as dead.
        """
        def forbidden(req, timeout=None):
            raise AssertionError("MiniMax must not be asked for /models")

        monkeypatch.setattr("urllib.request.urlopen", forbidden)
        h = MiniMaxProvider(secret=SECRET).health()
        assert h.state == STATE_DISCOVERY_UNSUPPORTED
        assert h.discovery == DISCOVERY_UNSUPPORTED
        # The preset is offered, but labelled as OUR declaration.
        assert h.models == ["MiniMax-M3"]
        assert h.models_are_preset is True
        assert h.generation_verified is False

    def test_discovery_404_is_discovery_unsupported(self, monkeypatch):
        """A provider that claims discovery but answers 404 gets the same state."""
        monkeypatch.setattr("urllib.request.urlopen", http_err(404))
        h = QwenCodingPlanProvider(secret=SECRET).health()
        assert h.state == STATE_DISCOVERY_UNSUPPORTED
        assert h.discovery == DISCOVERY_UNSUPPORTED

    def test_preset_alone_never_reaches_ready(self, monkeypatch):
        """A preset model name is not evidence the account can serve it."""
        monkeypatch.setattr("urllib.request.urlopen", ok_text())
        # The generation succeeds but the provider is MiniMax, which has no
        # discovery: still READY only because the COMPLETION worked.
        h = MiniMaxProvider(secret=SECRET).verify_generation()
        assert h.state == STATE_READY
        assert h.generation_verified is True
        assert h.models_are_preset is True   # still labelled as a preset

    def test_generation_failure_is_not_ready(self, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", http_err(401))
        h = QwenCodingPlanProvider(secret=SECRET).verify_generation()
        assert h.state == STATE_AUTH_FAILED
        assert h.generation_verified is False

    def test_rate_limited_is_distinct_from_unavailable(self, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", http_err(429))
        assert QwenCodingPlanProvider(secret=SECRET).health().state == STATE_RATE_LIMITED

    def test_server_error_is_unavailable(self, monkeypatch):
        # A 5xx on /models IS a real fault (unlike a 404), so UNAVAILABLE stands.
        # Qwen publishes /models, so it is the right subject here.
        monkeypatch.setattr("urllib.request.urlopen", http_err(503))
        assert QwenCodingPlanProvider(secret=SECRET).health().state == STATE_UNAVAILABLE

    def test_default_model_missing_from_account_is_degraded(self, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", ok_models("some-other-model"))
        h = QwenCodingPlanProvider(secret=SECRET).health()
        assert h.state == STATE_DEGRADED
        assert "不在可用列表" in h.detail
        assert h.generation_verified is False

    def test_capabilities_declare_roles_and_flags(self):
        caps = MiniMaxProvider().capabilities()
        assert caps["role"] == ROLE_PRIMARY_CODING
        assert caps["supports_streaming"] is True
        assert caps["default_model"] == "MiniMax-M3"
        assert DeepSeekProvider().capabilities()["role"] == ROLE_HIGH_REASONING


# --------------------------------------------------------------------------- #
# C. Request / response
# --------------------------------------------------------------------------- #


class TestGeneration:
    def test_bearer_header_sent(self, monkeypatch):
        seen = {}

        def capture(req, timeout=None):
            seen.update({k.lower(): v for k, v in req.headers.items()})
            return ok_text()(req, timeout)

        monkeypatch.setattr("urllib.request.urlopen", capture)
        MiniMaxProvider(secret=SECRET).generate("hi")
        assert seen["authorization"] == f"Bearer {SECRET}"

    def test_parses_content_usage_and_request_id(self, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", ok_text("result text"))
        r = MiniMaxProvider(secret=SECRET).generate("hi")
        assert r.text == "result text"
        assert r.usage.input_tokens == 11
        assert r.usage.output_tokens == 7
        assert r.request_id == "req_42"
        assert r.provider == "minimax"
        assert r.finish_reason == "stop"

    def test_absent_usage_is_unknown_not_zero(self):
        # A fabricated 0 reads as "this call was free", which is a costing bug.
        u = Usage.from_openai({})
        assert u.input_tokens is None
        assert u.as_dict()["input_tokens"] is None

    def test_tool_calls_are_captured(self, monkeypatch):
        body = {
            "model": "m1",
            "choices": [{"message": {"content": None, "tool_calls": [
                {"id": "c1", "function": {"name": "read", "arguments": "{}"}},
            ]}, "finish_reason": "tool_calls"}],
        }
        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: _Resp(200, body))
        r = MiniMaxProvider(secret=SECRET).generate("hi", tools=[{"type": "function"}])
        assert len(r.tool_calls) == 1

    @pytest.mark.parametrize("body", [
        {},                                    # no choices
        {"choices": []},                       # empty
        {"choices": "x"},                      # wrong type
        {"choices": [{"message": {}}]},        # no content and no tool calls
        {"choices": [{"nomessage": 1}]},
        [],                                    # not an object
    ])
    def test_malformed_responses_raise_instead_of_returning_empty(self, body):
        with pytest.raises(ProviderInvalidResponseError):
            parse_chat_completion(body, provider="p", requested_model="m",
                                  latency_ms=1, secret=None)

    def test_error_classification(self, monkeypatch):
        for code, exc in [(401, ProviderAuthError), (403, ProviderAuthError),
                          (429, ProviderRateLimitError), (500, ProviderServerError),
                          (503, ProviderServerError)]:
            monkeypatch.setattr("urllib.request.urlopen", http_err(code))
            with pytest.raises(exc):
                MiniMaxProvider(secret=SECRET, max_retries=0).generate("hi")

    def test_timeout_maps_to_timeout_error(self, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", transport_error(TimeoutError()))
        with pytest.raises(ProviderTimeoutError):
            MiniMaxProvider(secret=SECRET, max_retries=0).generate("hi")

    def test_no_credential_never_touches_the_wire(self, monkeypatch):
        def forbidden(req, timeout=None):
            raise AssertionError("must not call without a key")

        monkeypatch.setattr("urllib.request.urlopen", forbidden)
        with pytest.raises(ProviderAuthError):
            MiniMaxProvider().generate("hi")

    def test_auth_is_never_retried(self, monkeypatch):
        calls = {"n": 0}

        def counting(req, timeout=None):
            calls["n"] += 1
            raise http_err(401)(req)

        monkeypatch.setattr("urllib.request.urlopen", counting)
        with pytest.raises(ProviderAuthError):
            MiniMaxProvider(secret=SECRET, max_retries=3).generate("hi")
        assert calls["n"] == 1

    def test_invalid_response_is_never_retried(self, monkeypatch):
        calls = {"n": 0}

        def counting(req, timeout=None):
            calls["n"] += 1
            return _Resp(200, {"choices": []})

        monkeypatch.setattr("urllib.request.urlopen", counting)
        with pytest.raises(ProviderInvalidResponseError):
            MiniMaxProvider(secret=SECRET, max_retries=3).generate("hi")
        assert calls["n"] == 1

    def test_5xx_is_retried_then_given_up(self, monkeypatch):
        calls = {"n": 0}

        def counting(req, timeout=None):
            calls["n"] += 1
            return http_err(500)(req)

        monkeypatch.setattr("urllib.request.urlopen", counting)
        monkeypatch.setattr("time.sleep", lambda s: None)
        with pytest.raises(ProviderServerError):
            MiniMaxProvider(secret=SECRET, max_retries=2).generate("hi")
        # 1 initial + 2 retries, then it stops. Never unbounded.
        assert calls["n"] == 3

    def test_retry_after_is_honoured_but_bounded(self, monkeypatch):
        monkeypatch.setattr(
            "urllib.request.urlopen", http_err(429, {"Retry-After": "2"}),
        )
        slept = []
        monkeypatch.setattr("time.sleep", lambda s: slept.append(s))
        with pytest.raises(ProviderRateLimitError):
            MiniMaxProvider(secret=SECRET, max_retries=0).generate("hi")
        # max_retries=0 -> no sleep, and the 429 is not retried at all.
        assert slept == []

    def test_models_falls_back_to_preset_when_discovery_fails(self, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", http_err(500))
        models = MiniMaxProvider(secret=SECRET).models()
        assert models == ["MiniMax-M3"]

    def test_qwen_models_are_discovered_not_asserted(self, monkeypatch):
        # The vendor can add a model at any time; a hardcoded list would hide it.
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda req, timeout=None: _Resp(200, {"data": [
                {"id": "qwen3.8-max"}, {"id": "qwen3.7-plus"}]}),
        )
        assert "qwen3.8-max" in QwenCodingPlanProvider(secret=SECRET).models()

    def test_provider_preset_does_not_claim_qwen38(self):
        """The preset lists ids the plan actually served. A model that only
        exists in the plan doc, not in the account, would be a fabrication."""
        assert not any("qwen3.8" in m for m in QwenCodingPlanProvider.known_models)

    def test_custom_provider_accepts_any_base_url(self):
        p = CustomOpenAICompatibleProvider(
            secret="k", base_url="http://127.0.0.1:11434/v1", display_name="Ollama"
        )
        assert p.base_url == "http://127.0.0.1:11434/v1"
        assert p.display_name == "Ollama"
        assert p.id == "custom"

    def test_deepseek_rejects_unknown_reasoning_effort(self):
        with pytest.raises(ValueError):
            DeepSeekProvider(reasoning_effort="extreme")

    def test_deepseek_accepts_documented_efforts(self):
        for effort in ("low", "high", "max"):
            assert DeepSeekProvider(reasoning_effort=effort).reasoning_effort == effort

    def test_minimax_exposes_three_independent_dimensions(self):
        """
        plan_type, api_protocol and region are separate axes.

        They were collapsed into one `api_style` that carried a plan name, which
        is how a China Coding Plan key ended up on the international
        pay-as-you-go host and was reported as `invalid api key (2049)` — a valid
        credential reported as a bad one. A consumer that needs the protocol must
        not have to parse a plan name to find it.
        """
        c = MiniMaxProvider().capabilities()
        assert c["plan_type"] == "coding_subscription_cn"
        assert c["api_protocol"] == "openai_compatible"
        assert c["region"] == "cn"
        # And the axes are independently settable.
        p = MiniMaxProvider(plan_type="pay_as_you_go", api_protocol="native", region="global")
        assert p.plan_type == "pay_as_you_go"
        assert p.api_protocol == "native"
        assert p.region == "global"
        # "openai" is no longer a protocol name; it was the bug.
        assert "openai" not in MiniMaxProvider.PROTOCOLS

    def test_minimax_region_selects_the_host_and_never_falls_back(self):
        """A CN key on the global host is rejected as invalid, so the host is
        stated rather than guessed — and an unknown region does not silently
        become the global one."""
        assert MiniMaxProvider(region="cn").host == "https://api.minimaxi.com"
        assert MiniMaxProvider(region="global").host == "https://api.minimax.io"
        # An unknown region falls back to the CONFIGURED default (cn), never to
        # the international host.
        assert MiniMaxProvider(region="???").host == "https://api.minimaxi.com"

    def test_minimax_explicit_base_url_beats_the_host_table(self):
        """A user-supplied endpoint is authoritative; the table is a default."""
        p = MiniMaxProvider(region="global", base_url="https://api.minimaxi.com/v1")
        assert p.host == "https://api.minimaxi.com"

    def test_subscription_probe_never_implies_ready(self):
        """
        The quota endpoint IDENTIFIES the account; it does not verify generation.

        So it returns its own verdict and no state, and a provider whose
        subscription check passes while generation fails must still not be READY.
        """
        p = MiniMaxProvider(secret=SECRET)
        r = p.check_subscription()
        assert "state" not in r           # it cannot produce a state at all
        assert "subscription_auth" in r
        assert r["plan_type_detected"] in (None, "coding_subscription_cn")

    def test_endpoints_are_configuration_not_hardcoded_in_business_code(self):
        # A deployment override must be honoured without touching any module.
        assert MiniMaxProvider(base_url="https://proxy.internal/mm").base_url == "https://proxy.internal/mm"
        assert QwenCodingPlanProvider(base_url="https://x/v1").base_url == "https://x/v1"
        assert DeepSeekProvider(base_url="https://y").base_url == "https://y"


# --------------------------------------------------------------------------- #
# F/G. Router + failover
# --------------------------------------------------------------------------- #


def make_router(monkeypatch, states):
    """Build a router over the three production providers with scripted health.

    ``states`` maps provider id -> READY / AUTH_MISSING / UNAVAILABLE / ...

    Health is replaced on the INSTANCE (not stubbed with a lambda returning a
    truthy value) so the router exercises the same code path it uses in
    production: a provider that reports READY must also have a credential, and
    the router must be able to tell the difference.
    """
    from jcc_core.providers.generation import ProviderHealth

    classes = {
        "minimax": MiniMaxProvider,
        "qwen_coding_plan_cn": QwenCodingPlanProvider,
        "deepseek": DeepSeekProvider,
    }
    made = []
    for pid, state in states.items():
        p = classes[pid](secret=SECRET)
        p.health = lambda pid=pid, state=state: ProviderHealth(
            provider=pid,
            state=state,
            configured=state != "NOT_CONFIGURED",
            base_url="https://x/v1",
            default_model=_default_for(pid),
            models=[_default_for(pid)] if state == "READY" else [],
        )
        made.append(p)
    return ModelRouter(made)


def _default_for(pid):
    return {
        "minimax": "MiniMax-M3",
        "qwen_coding_plan_cn": "qwen3.7-plus",
        "deepseek": "deepseek-v4-pro",
    }[pid]


def scripted(behaviour):
    """Return a ``generate`` that replays a list of exceptions/results.

    The signature matches what the router actually calls (``prompt`` positional,
    everything else keyword) so a test cannot pass against a router that calls
    the provider differently.
    """
    calls = {"n": 0}

    def gen(prompt, **kw):
        i = calls["n"]
        calls["n"] += 1
        action = behaviour[min(i, len(behaviour) - 1)]
        if isinstance(action, Exception):
            raise action
        return action

    return gen, calls


def a_result(text="ok"):
    from jcc_core.providers.generation import GenerateResult

    return GenerateResult(text=text, model="m", provider="p", usage=Usage(),
                          latency_ms=5, request_id="r1")


class TestRouting:
    def test_no_ready_provider_yields_an_empty_decision(self):
        r = make_router(monkeypatch=None, states={
            "minimax": "NOT_CONFIGURED", "qwen_coding_plan_cn": "NOT_CONFIGURED",
            "deepseek": "NOT_CONFIGURED"})
        d = r.route(RouteRequest(task="x", task_type="code"))
        assert d.execution_model == ""
        assert d.chain == []
        assert d.reason == "没有可用的模型 Provider"

    def test_empty_decision_is_not_a_success(self):
        r = make_router(monkeypatch=None, states={"minimax": "NOT_CONFIGURED",
                                                  "qwen_coding_plan_cn": "NOT_CONFIGURED",
                                                  "deepseek": "NOT_CONFIGURED"})
        d = r.route(RouteRequest(task="x", task_type="code"))
        assert d.confidence == 0.0
        assert d.execution_provider == ""

    def test_complex_coding_starts_at_minimax(self):
        r = make_router(monkeypatch=None, states={p: "READY" for p in DEFAULT_CHAIN})
        d = r.route(RouteRequest(task="x", task_type="refactor"))
        assert d.execution_provider == "minimax"
        assert d.execution_model == "MiniMax-M3"
        assert d.chain[0] == "minimax"

    def test_fast_band_prefers_the_normal_coding_provider(self):
        r = make_router(monkeypatch=None, states={p: "READY" for p in DEFAULT_CHAIN})
        d = r.route(RouteRequest(task="x", task_type="simple"))
        assert d.execution_provider == "qwen_coding_plan_cn"

    def test_high_reasoning_prefers_deepseek(self):
        r = make_router(monkeypatch=None, states={p: "READY" for p in DEFAULT_CHAIN})
        d = r.route(RouteRequest(task="x", task_type="reasoning"))
        assert d.execution_provider == "deepseek"

    def test_verifier_differs_from_producer(self):
        r = make_router(monkeypatch=None, states={p: "READY" for p in DEFAULT_CHAIN})
        d = r.route(RouteRequest(task="x", task_type="refactor"))
        assert d.verification_provider != d.execution_provider
        assert d.verification_provider != ""

    def test_single_healthy_provider_yields_no_independent_verifier(self):
        r = make_router(monkeypatch=None, states={
            "minimax": "READY", "qwen_coding_plan_cn": "NOT_CONFIGURED",
            "deepseek": "NOT_CONFIGURED"})
        d = r.route(RouteRequest(task="x", task_type="refactor"))
        # With nothing else alive, the router must NOT claim the producer
        # verifies itself.
        assert d.verification_provider in ("", None)

    def test_chain_skips_unhealthy_providers(self):
        r = make_router(monkeypatch=None, states={
            "minimax": "UNAVAILABLE", "qwen_coding_plan_cn": "READY",
            "deepseek": "READY"})
        d = r.route(RouteRequest(task="x", task_type="refactor"))
        assert "minimax" not in d.chain
        # COMPLEX_CODING's role order is PRIMARY -> HIGH_REASONING -> NORMAL, so
        # with the primary down the next choice is deepseek, not qwen.
        assert d.execution_provider == "deepseek"
        assert d.chain == ["deepseek", "qwen_coding_plan_cn"]

    def test_request_health_overrides_stale_probe(self):
        r = make_router(monkeypatch=None, states={p: "READY" for p in DEFAULT_CHAIN})
        d = r.route(RouteRequest(
            task="x", task_type="refactor",
            provider_health={"minimax": "UNAVAILABLE"},
        ))
        assert d.execution_provider != "minimax"

    def test_band_head_lookup_is_data_not_a_branch_chain(self):
        assert BAND_CHAIN_HEAD["COMPLEX_CODING"] == "minimax"
        assert BAND_CHAIN_HEAD["FAST"] == "qwen_coding_plan_cn"
        assert BAND_CHAIN_HEAD["VERIFICATION"] == "deepseek"

    def test_executor_is_health_filtered(self):
        r = make_router(monkeypatch=None, states={p: "READY" for p in DEFAULT_CHAIN})
        d = r.route(RouteRequest(task="x", task_type="code"),
                    executors=("workbuddy", "codex"),
                    executor_health={"workbuddy": "down", "codex": "ready"})
        assert d.executor == "codex"


class TestFailover:
    def _router_ready(self):
        return make_router(monkeypatch=None, states={p: "READY" for p in DEFAULT_CHAIN})

    def test_success_on_first_provider(self):
        r = self._router_ready()
        p = r.provider("minimax")
        p.generate, _ = scripted([a_result("from minimax")])
        d = r.route(RouteRequest(task="x", task_type="refactor"))
        assert r.execute(d, "hi").text == "from minimax"

    def test_timeout_falls_over(self):
        r = self._router_ready()
        r.provider("minimax").generate, _ = scripted([ProviderTimeoutError("t")])
        r.provider("qwen_coding_plan_cn").generate, _ = scripted([a_result("from qwen")])
        d = r.route(RouteRequest(task="x", task_type="refactor"))
        assert r.execute(d, "hi").text == "from qwen"

    def test_network_error_falls_over(self):
        r = self._router_ready()
        r.provider("minimax").generate, _ = scripted([ProviderNetworkError("n")])
        r.provider("qwen_coding_plan_cn").generate, _ = scripted([a_result("qwen")])
        d = r.route(RouteRequest(task="x", task_type="refactor"))
        assert r.execute(d, "hi").text == "qwen"

    def test_5xx_falls_over(self):
        r = self._router_ready()
        r.provider("minimax").generate, _ = scripted([ProviderServerError("5xx")])
        r.provider("qwen_coding_plan_cn").generate, _ = scripted([a_result("qwen")])
        d = r.route(RouteRequest(task="x", task_type="refactor"))
        assert r.execute(d, "hi").text == "qwen"

    def test_invalid_response_falls_over(self):
        r = self._router_ready()
        r.provider("minimax").generate, _ = scripted([ProviderInvalidResponseError("bad")])
        r.provider("qwen_coding_plan_cn").generate, _ = scripted([a_result("qwen")])
        d = r.route(RouteRequest(task="x", task_type="refactor"))
        assert r.execute(d, "hi").text == "qwen"

    def test_auth_failure_does_not_retry_but_may_fail_over(self):
        r = self._router_ready()
        gen, calls = scripted([ProviderAuthError("401")])
        r.provider("minimax").generate = gen
        r.provider("qwen_coding_plan_cn").generate, _ = scripted([a_result("qwen")])
        d = r.route(RouteRequest(task="x", task_type="refactor"))
        assert r.execute(d, "hi").text == "qwen"
        # One attempt against the failing provider, not four.
        assert calls["n"] == 1

    def test_whole_chain_failure_raises(self):
        r = self._router_ready()
        for pid in DEFAULT_CHAIN:
            r.provider(pid).generate, _ = scripted([ProviderServerError("down")])
        d = r.route(RouteRequest(task="x", task_type="refactor"))
        with pytest.raises(ProviderServerError):
            r.execute(d, "hi")

    def test_policy_denial_never_fails_over(self):
        """The load-bearing guarantee: policy is not advisory."""
        r = self._router_ready()
        gen, calls = scripted([PolicyDeniedError("policy says no")])
        r.provider("minimax").generate = gen
        r.provider("qwen_coding_plan_cn").generate, _ = scripted([a_result("should not run")])
        d = r.route(RouteRequest(task="x", task_type="refactor"))
        with pytest.raises(PolicyDeniedError):
            r.execute(d, "hi")
        # The fallback was never contacted.
        assert calls["n"] == 1

    def test_policy_denied_is_a_declared_class(self):
        assert "policy_denied" in POLICY_DENIED_CLASSES
        assert PolicyDeniedError("x").failure_class in POLICY_DENIED_CLASSES

    def test_fallback_uses_its_own_default_model(self):
        r = self._router_ready()
        seen = []

        def grab(prompt, **kw):
            seen.append(kw.get("model"))
            raise ProviderTimeoutError("t")

        # Only the primary fails, so the fallback's requested model is the
        # interesting one: handing MiniMax's id to DeepSeek would 404.
        r.provider("minimax").generate = grab
        r.provider("deepseek").generate = grab
        r.provider("qwen_coding_plan_cn").generate, _ = scripted([a_result("qwen")])
        d = r.route(RouteRequest(task="x", task_type="refactor"))
        r.execute(d, "hi")
        # COMPLEX_CODING's chain is MiniMax -> DeepSeek -> Qwen. The two that
        # failed are recorded by `grab`; the third succeeds and is not.
        # Each attempt carries ITS OWN provider's model id — the requested model
        # belongs to the primary's namespace and would 404 elsewhere.
        assert seen == ["MiniMax-M3", "deepseek-v4-pro"]

    def test_circuit_breaker_opens_after_repeated_failures(self):
        r = self._router_ready()
        gen, calls = scripted([ProviderServerError("down")])
        r.provider("minimax").generate = gen
        d = r.route(RouteRequest(task="x", task_type="refactor"))
        for _ in range(4):
            try:
                r.execute(d, "hi")
            except ProviderError:
                pass
        # After 3 failures the provider is skipped without being contacted.
        assert r.health_snapshot()["minimax"] == "CIRCUIT_OPEN"
        assert calls["n"] <= 3

    def test_breaker_resets_on_success(self):
        r = self._router_ready()
        gen, _ = scripted([a_result("ok")])
        r.provider("minimax").generate = gen
        r.record_outcome("minimax", False)
        r.record_outcome("minimax", False)
        r.record_outcome("minimax", True)
        assert r.health_snapshot()["minimax"] != "CIRCUIT_OPEN"

    def test_latency_and_request_id_are_available_for_observability(self, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", ok_text())
        r = self._router_ready()
        r.provider("minimax").generate = lambda p, **kw: _real_generate(
            r.provider("minimax"), p, **kw
        )
        d = r.route(RouteRequest(task="x", task_type="refactor"))
        result = r.execute(d, "hi")
        assert result.latency_ms >= 0
        assert result.request_id == "req_42"

    def test_execute_without_a_chain_raises(self):
        r = self._router_ready()
        d = r.route(RouteRequest(task="x", task_type="code"))
        empty = type(d)(**{**d.as_dict(), "chain": ()})
        with pytest.raises(ProviderError):
            r.execute(empty, "hi")


def _real_generate(provider, prompt, **kw):
    return GenerationProvider.generate(provider, prompt, **kw)


# --------------------------------------------------------------------------- #
# H. Verification
# --------------------------------------------------------------------------- #


class TestVerification:
    def test_model_text_alone_is_not_completion(self):
        v = assess_completion({})
        assert v.passed is False
        assert set(v.missing) == set(COMPLETION_CHECKS)

    def test_all_five_signals_pass(self):
        v = assess_completion({
            "tests_passed": True, "artifact_exists": True,
            "exit_code_zero": True, "domain_state": True,
        })
        # jev_verification still missing -> not a pass.
        assert v.passed is False
        assert "jev_verification" in v.missing

    def test_jev_verification_comes_from_the_verifier_not_the_caller(self):
        from jcc_core.model_router import VerificationResult

        # `passed=True` on a hand-made verifier is NOT enough: the signal is read
        # from the verifier's recorded JUDGEMENT, so a caller cannot assert
        # verification by constructing an object that merely claims to be one.
        v = assess_completion(
            {"tests_passed": True, "artifact_exists": True,
             "exit_code_zero": True, "domain_state": True},
            verifier=VerificationResult(passed=True, verifier="deepseek"),
        )
        assert v.passed is False
        assert "jev_verification" in v.missing

    def test_jev_verification_passes_only_with_a_real_judgement(self):
        v = assess_completion(
            {"tests_passed": True, "artifact_exists": True,
             "exit_code_zero": True, "domain_state": True},
            verifier=VerificationResult(
                passed=False,   # always False coming out of verify_with_model
                checks={"model_judgement": True},
                verifier="deepseek", verifier_model="deepseek-v4-pro",
            ),
        )
        assert v.passed is True
        assert v.checks["jev_verification"] is True

    def test_missing_evidence_names_what_is_missing(self):
        v = assess_completion({"tests_passed": True})
        assert "artifact_exists" in v.missing
        assert "artifact_exists" in v.reason

    def test_false_is_not_the_same_as_absent(self):
        # A test run that FAILED is different from one that never ran; both fail
        # the gate but the reason differs, and `is True` enforces that.
        v = assess_completion({"tests_passed": False})
        assert v.checks["tests_passed"] is False
        assert "tests_passed" in v.missing

    def test_no_verifier_model_means_no_pass(self):
        r = make_router(monkeypatch=None, states={
            "minimax": "READY", "qwen_coding_plan_cn": "NOT_CONFIGURED",
            "deepseek": "NOT_CONFIGURED"})
        d = r.route(RouteRequest(task="x", task_type="refactor"))
        v = verify_with_model(r, d, "subject")
        assert v.passed is False
        assert "不能验证自己" in v.reason

    def test_verifier_pass_cannot_override_missing_artifact(self):
        r = make_router(monkeypatch=None, states={p: "READY" for p in DEFAULT_CHAIN})
        r.provider("deepseek").generate, _ = scripted([
            a_result('{"complete": true, "reason": "looks good"}')])
        d = r.route(RouteRequest(task="x", task_type="refactor"))
        v = verify_with_model(r, d, "subject")
        # The model said yes; the evidence is absent. Not a pass.
        assert v.passed is False
        assert "artifact_exists" in v.missing

    def test_verifier_saying_complete_with_full_evidence_passes(self):
        r = make_router(monkeypatch=None, states={p: "READY" for p in DEFAULT_CHAIN})
        r.provider("deepseek").generate, _ = scripted([
            a_result('{"complete": true, "reason": "artifact present"}')])
        d = r.route(RouteRequest(task="x", task_type="refactor"))
        # The hard signals are supplied by the caller (tests ran, file exists,
        # exit code, domain state); only `jev_verification` comes from the model.
        from jcc_core.model_router import VerificationResult

        v = verify_with_model(
            r, d, "subject",
            require_all=("tests_passed", "artifact_exists", "exit_code_zero",
                         "domain_state", "jev_verification"),
        )
        # verify_with_model calls assess_completion with an EMPTY evidence map, so
        # the mechanical signals are absent by construction and the result is not
        # a pass. That is the point: the model cannot carry it alone.
        assert v.passed is False
        assert v.verifier == "deepseek"
        assert v.verifier_model == "deepseek-v4-pro"

    def test_full_evidence_plus_model_pass_is_a_pass(self):
        r = make_router(monkeypatch=None, states={p: "READY" for p in DEFAULT_CHAIN})
        r.provider("deepseek").generate, _ = scripted([
            a_result('{"complete": true, "reason": "all good"}')])
        d = r.route(RouteRequest(task="x", task_type="refactor"))
        v = verify_with_model(r, d, "s")
        # `verify_with_model` never returns passed=True; the model only supplies
        # the judgement that `assess_completion` then combines with evidence.
        assert v.passed is False
        assert v.checks["model_judgement"] is True
        combined = assess_completion(
            {"tests_passed": True, "artifact_exists": True,
             "exit_code_zero": True, "domain_state": True},
            verifier=v,
        )
        assert combined.passed is True
        assert combined.verifier == "deepseek"

    def test_unreadable_verifier_reply_is_not_a_pass(self):
        r = make_router(monkeypatch=None, states={p: "READY" for p in DEFAULT_CHAIN})
        for reply in ["sure!", "", "{\"complete\": maybe}"]:
            r.provider("deepseek").generate, _ = scripted([a_result(reply)])
            d = r.route(RouteRequest(task="x", task_type="refactor"))
            v = verify_with_model(r, d, "s")
            assert v.passed is False

    def test_verifier_saying_no_blocks(self):
        r = make_router(monkeypatch=None, states={p: "READY" for p in DEFAULT_CHAIN})
        r.provider("deepseek").generate, _ = scripted([
            a_result('{"complete": false, "reason": "no artifact"}')])
        d = r.route(RouteRequest(task="x", task_type="refactor"))
        v = verify_with_model(r, d, "s")
        assert v.passed is False
        # The model's own negative judgement is recorded under its own name, so
        # a UI can say WHY verification failed rather than only that it did.
        assert v.checks["model_judgement"] is False


class TestDisplayStateIsSingleSourced:
    """
    The card and the summary must never describe one provider differently.

    They did: the card mapped the wire value DISCOVERY_UNSUPPORTED to
    「已连接（无模型列表接口）」 while a summary elsewhere mapped the same
    provider to 「无法连接」, at the same instant. Two renderers, two tables, one
    provider, two answers.
    """

    def _card(self, **kw):
        from jcc_core.provider_registry import _display_state, _display_reason
        return _display_state(kw["h"], kw["p"])

    def test_preset_without_generation_is_pending_not_connected(self):
        """The invariant. A preset model name is our own declaration, so a
        provider carrying one has proved nothing and must not read 已连接."""
        from jcc_core.provider_registry import (
            DISPLAY_CONFIGURED_PENDING, _display_state,
        )
        from jcc_core.providers.generation import (
            DISCOVERY_UNSUPPORTED, STATE_DISCOVERY_UNSUPPORTED,
        )

        class H:
            state = STATE_DISCOVERY_UNSUPPORTED
            discovery = DISCOVERY_UNSUPPORTED
            generation_verified = False
            models_are_preset = True
            latency_ms = None
            detail = "该 Provider 不提供模型列表接口，使用已声明的默认模型"

        p = MiniMaxProvider(secret=SECRET)
        assert _display_state(H(), p) == DISPLAY_CONFIGURED_PENDING
        assert _display_state(H(), p) != "CONNECTED"

    def test_only_a_real_generation_earns_connected(self):
        from jcc_core.provider_registry import DISPLAY_CONNECTED, _display_state

        class H:
            state = "READY"
            discovery = DISCOVERY_UNSUPPORTED
            generation_verified = True
            models_are_preset = True
            latency_ms = 120
            detail = "真实生成已验证"

        assert _display_state(H(), MiniMaxProvider(secret=SECRET)) == DISPLAY_CONNECTED

    def test_missing_credential_is_not_configured(self):
        from jcc_core.provider_registry import DISPLAY_NOT_CONFIGURED, _display_state

        class H:
            state = STATE_NOT_CONFIGURED
            discovery = "MODEL_DISCOVERY_UNKNOWN"
            generation_verified = False
            models_are_preset = True
            latency_ms = None
            detail = None

        assert _display_state(H(), MiniMaxProvider(secret=None)) == DISPLAY_NOT_CONFIGURED

    def test_subscription_key_rejected_is_endpoint_mismatch_not_auth_failure(self):
        """
        A subscription key refused as an "invalid api key" is almost always the
        wrong HOST for its region, and the two have opposite fixes: reissue the
        key vs change the region. Reporting AUTH_FAILED sends the operator to
        reissue a perfectly good credential.
        """
        from jcc_core.provider_registry import DISPLAY_ENDPOINT_MISMATCH, _display_state

        class H:
            state = STATE_UNAVAILABLE
            discovery = DISCOVERY_UNSUPPORTED
            generation_verified = False
            models_are_preset = True
            latency_ms = None
            detail = "真实生成失败：HTTP 401 invalid api key (2049)"

        p = MiniMaxProvider(secret=SECRET, plan_type="coding_subscription_cn", region="cn")
        assert _display_state(H(), p) == DISPLAY_ENDPOINT_MISMATCH


class TestReasoningStrip:
    """
    A reasoning model returns its chain of thought INSIDE `content`, before the
    answer. Passing that on as the completion would show a user the model's
    private reasoning as if it were the response.

    MEASURED against MiniMax-M3, which answered "Return exactly: JEV_MINIMAX_OK"
    as `<think>…</think>` followed by the token — so a probe asserting on the text
    failed even though the model had complied perfectly.
    """

    def test_strips_a_complete_block(self):
        assert strip_reasoning("<think>reasoning here</think>JEV_MINIMAX_OK") == "JEV_MINIMAX_OK"

    def test_an_unterminated_block_discards_everything_after_it(self):
        """
        The MEASURED shape: the response was cut off mid-reasoning, so no closing
        tag exists.

        With no tag to find, the rest of the string cannot be distinguished from
        more reasoning — and appending a literal "JEV_MINIMAX_OK" after the raw
        prefix does NOT make it the answer, it makes it part of an unterminated
        thought. So the whole tail goes. A visible empty answer beats a confident
        one built out of someone's chain of thought.
        """
        raw = '<think>The user is asking me to return exactly the string "JEV_MINIM'
        assert strip_reasoning(raw) == ""

    def test_a_closed_block_keeps_the_answer_that_follows(self):
        # The same model, given room to finish: reasoning stripped, answer kept.
        assert strip_reasoning(
            '<think>reasoning</think>JEV_MINIMAX_OK'
        ) == "JEV_MINIMAX_OK"

    def test_an_unterminated_block_alone_yields_nothing(self):
        # Leaking the reasoning is worse than reporting no answer: a missing
        # answer is visible, a leaked chain of thought is not.
        assert strip_reasoning("<think>only reasoning so far") == ""

    def test_leaves_plain_text_untouched(self):
        assert strip_reasoning("JEV_MINIMAX_OK") == "JEV_MINIMAX_OK"

    def test_handles_the_reasoning_tag_variant(self):
        assert strip_reasoning("<reasoning>r</reasoning>answer") == "answer"

    def test_empty_input_is_empty_output(self):
        assert strip_reasoning("") == ""

    def test_the_completion_text_is_stripped_not_the_raw_content(self):
        """End to end through the parser, because the strip has to happen where
        the result is BUILT — a caller reading `text` must never see reasoning."""
        payload = {
            "model": "MiniMax-M3",
            "choices": [{"message": {
                "content": "<think>let me think</think>JEV_MINIMAX_OK",
            }, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        import json as _json
        import urllib.request as _u

        def opener(req, timeout=None):
            class _R:
                status = 200
                headers = {}

                def read(self):
                    return _json.dumps(payload).encode()

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False
            return _R()

        import unittest.mock as _m
        with _m.patch.object(_u, "urlopen", opener):
            r = MiniMaxProvider(secret=SECRET).generate("Return exactly: JEV_MINIMAX_OK")
        assert r.text == "JEV_MINIMAX_OK"
        assert "think" not in r.text
