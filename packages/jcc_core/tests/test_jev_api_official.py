"""Contract tests for the official TypeSafe Jev (System One) client.

These need NO API key. Everything they cover is about the shape of the exchange —
how we serialise a request, how strictly we read a reply, and what we do when
the network or the vendor misbehaves. The only thing a credential unlocks is a
live call, which belongs in a separate, explicitly opt-in probe.

The negative tests carry the weight here. A client that is lenient about a
malformed reply is worse than one that has no client at all: it puts a
fabricated executor choice into the pipeline wearing the vendor's name.
"""
from __future__ import annotations

import json
import urllib.error

import pytest

from jcc_core.providers.jev_api import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL_ALIAS,
    ENV_API_KEY,
    MODELS_PATH,
    QUESTION_CHOICE,
    QUESTION_NOUL,
    QUESTION_SCORE,
    SYSTEMONE_PATH,
    ChoiceAnswer,
    ExecutorDecision,
    JevApiError,
    JevAuthError,
    JevProvider,
    JevRateLimitError,
    JevUnavailableError,
    NoulAnswer,
    ScoreAnswer,
    SystemOneProtocolError,
    build_executor_questions,
    build_executor_state,
    decide_executor,
    parse_answer,
    parse_systemone,
    provider_state,
    redact,
    KNOWN_EXECUTORS,
    STATE_DISABLED,
    STATE_MOCK,
    STATE_REAL_AUTH_MISSING,
    STATE_REAL_READY,
    STATE_REAL_UNAVAILABLE,
    LOW_CONFIDENCE_THRESHOLD,
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

SECRET = "test-key-do-not-log-1234567890"


def a_response(
    *,
    choice: str = "workbuddy",
    confidence: float = 0.94,
    noul: bool = True,
    score: float = 0.2,
    model: str = "jev-latest",
    request_id: str = "req_abc123",
    latency_ms: int = 412,
) -> dict:
    """A well-formed System One reply with all three answer types."""
    return {
        "model": model,
        "answers": {
            "executor_choice": {
                "type": "choice",
                "choice": choice,
                "confidence": confidence,
                "probabilities": {"workbuddy": confidence, "codex": 0.03,
                                  "claude": 0.02, "hermes": 0.01},
            },
            "safe_to_execute": {"type": "noul", "noul": noul},
            "risk_level": {
                "type": "score",
                "score": score,
                "confidence": 0.88,
                "legend": {"low": [0.0, 0.33], "medium": [0.33, 0.66],
                           "high": [0.66, 1.0]},
                "probabilities": {"low": 0.8, "medium": 0.15, "high": 0.05},
            },
        },
        "usage": {"input_tokens": 512, "output_tokens": 96},
    }


class FakeHTTP:
    """A stand-in for ``urllib.request.urlopen`` that records the request.

    It is a test double for the TRANSPORT, not a fake vendor: every path below
    asserts on what our client built, and the reply objects are built by the test
    to match the published contract.
    """

    def __init__(self, status=200, payload=None, headers=None, raise_exc=None):
        self.status = status
        self.payload = payload if payload is not None else {}
        self.headers = headers or {}
        self.raise_exc = raise_exc
        self.calls = []

    def __call__(self, req, timeout=None):
        self.calls.append({"req": req, "timeout": timeout})
        if self.raise_exc is not None:
            raise self.raise_exc
        body = json.dumps(self.payload).encode("utf-8") if self.payload is not None else b""
        return _FakeResponse(self.status, body, self.headers)


class _FakeResponse:
    def __init__(self, status, body, headers):
        self.status = status
        self._body = body
        self.headers = _FakeHeaders(headers)

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeHeaders(dict):
    def items(self):
        return super().items()


def http_error(code, body=b"denied"):
    """A realistic ``HTTPError`` (its ``read()`` must be callable)."""
    return urllib.error.HTTPError(
        url="https://api.typesafe.ai/v1/models",
        code=code,
        msg="err",
        hdrs=None,
        fp=None,
    )


# --------------------------------------------------------------------------- #
# 1. Official contract constants
# --------------------------------------------------------------------------- #


class TestOfficialContract:
    def test_base_url_and_paths_match_the_published_surface(self):
        assert DEFAULT_BASE_URL == "https://api.typesafe.ai"
        assert MODELS_PATH == "/v1/models"
        assert SYSTEMONE_PATH == "/v1/systemone"

    def test_default_model_is_the_published_alias(self):
        # NOT a pinned version: the alias is what the vendor documents for
        # current-model access, and pinning would silently age.
        assert DEFAULT_MODEL_ALIAS == "jev-latest"

    def test_credential_env_name(self):
        assert ENV_API_KEY == "JEV_API_KEY"

    def test_all_three_question_types_are_supported(self):
        from jcc_core.providers.jev_api import QUESTION_TYPES

        assert set(QUESTION_TYPES) == {"noul", "choice", "score"}


# --------------------------------------------------------------------------- #
# 2. Request serialization
# --------------------------------------------------------------------------- #


class TestRequestSerialization:
    def test_decide_posts_the_documented_body(self, monkeypatch):
        fake = FakeHTTP(payload=a_response())
        monkeypatch.setattr("urllib.request.urlopen", fake)

        client = JevProvider(api_key=SECRET)
        client.decide({"task": "t"}, {"executor_choice": {"type": "choice"}})

        req = fake.calls[0]["req"]
        assert req.get_method() == "POST"
        assert req.full_url == "https://api.typesafe.ai/v1/systemone"
        sent = json.loads(req.data.decode("utf-8"))
        # Exactly the three documented top-level keys.
        assert set(sent) == {"model", "state", "questions"}
        assert sent["model"] == "jev-latest"
        assert sent["state"] == {"task": "t"}

    def test_models_issues_a_get_with_no_body(self, monkeypatch):
        fake = FakeHTTP(payload={"data": [{"id": "jev-latest"}, {"id": "jev-2"}]})
        monkeypatch.setattr("urllib.request.urlopen", fake)

        ids = JevProvider(api_key=SECRET).models()

        req = fake.calls[0]["req"]
        assert req.get_method() == "GET"
        assert req.full_url == "https://api.typesafe.ai/v1/models"
        assert req.data is None
        assert ids == ["jev-latest", "jev-2"]

    def test_bearer_header_is_sent(self, monkeypatch):
        fake = FakeHTTP(payload=a_response())
        monkeypatch.setattr("urllib.request.urlopen", fake)

        JevProvider(api_key=SECRET).decide({}, {})

        # urllib normalises header names to Capitalized form.
        sent = {k.lower(): v for k, v in fake.calls[0]["req"].headers.items()}
        assert sent["authorization"] == f"Bearer {SECRET}"

    def test_env_vars_override_base_url_and_model(self, monkeypatch):
        monkeypatch.setenv("JEV_BASE_URL", "https://staging.typesafe.ai")
        monkeypatch.setenv("JEV_MODEL", "jev-preview")
        monkeypatch.setenv("JEV_API_KEY", "env-key")
        c = JevProvider()
        assert c.base_url == "https://staging.typesafe.ai"
        assert c.model == "jev-preview"
        assert c.has_credential is True

    def test_trailing_slash_in_base_url_does_not_double_up(self, monkeypatch):
        c = JevProvider(api_key=SECRET, base_url="https://api.typesafe.ai/")
        assert c.base_url == "https://api.typesafe.ai"

    def test_state_and_questions_must_be_mappings(self):
        c = JevProvider(api_key=SECRET)
        with pytest.raises(TypeError):
            c.decide("not-a-mapping", {})
        with pytest.raises(TypeError):
            c.decide({}, ["not", "a", "mapping"])


# --------------------------------------------------------------------------- #
# 3. Response parsing — the three answer types
# --------------------------------------------------------------------------- #


class TestResponseParsing:
    def test_parses_all_three_answer_types(self):
        r = parse_systemone(a_response())
        assert r.model == "jev-latest"
        assert isinstance(r.answer("executor_choice"), ChoiceAnswer)
        assert isinstance(r.answer("safe_to_execute"), NoulAnswer)
        assert isinstance(r.answer("risk_level"), ScoreAnswer)
        assert r.choice("executor_choice").choice == "workbuddy"
        assert r.noul("safe_to_execute") is True
        assert r.score("risk_level").score == 0.2
        assert r.usage["input_tokens"] == 512

    def test_request_id_and_latency_are_carried(self):
        r = parse_systemone(a_response(), request_id="req_1", latency_ms=99)
        assert r.request_id == "req_1"
        assert r.latency_ms == 99

    def test_request_id_read_from_response_header(self, monkeypatch):
        fake = FakeHTTP(
            payload=a_response(),
            headers={"x-request-id": "req_from_header"},
        )
        monkeypatch.setattr("urllib.request.urlopen", fake)
        r = JevProvider(api_key=SECRET).decide({}, {})
        assert r.request_id == "req_from_header"

    def test_typed_accessors_return_none_on_type_mismatch(self):
        # A `noul` question answered with a `choice` must not silently read as
        # False — that would look like "Jev said it is unsafe".
        r = parse_systemone(a_response())
        assert r.noul("executor_choice") is None
        assert r.choice("safe_to_execute") is None
        assert r.score("safe_to_execute") is None


# --------------------------------------------------------------------------- #
# 4. Malformed responses — fail closed, never patch a default
# --------------------------------------------------------------------------- #


class TestMalformedResponses:
    @pytest.mark.parametrize("mutate,why", [
        (lambda d: d.pop("model"), "missing model"),
        (lambda d: d.pop("answers"), "missing answers"),
        (lambda d: d.update(model=123), "model wrong type"),
        (lambda d: d.update(answers=[]), "answers not an object"),
        (lambda d: d["answers"]["executor_choice"].pop("choice"), "choice missing"),
        (lambda d: d["answers"]["executor_choice"].pop("confidence"), "confidence missing"),
        (lambda d: d["answers"]["executor_choice"].pop("probabilities"), "probabilities missing"),
        (lambda d: d["answers"]["executor_choice"].update(confidence="high"), "confidence not a number"),
        (lambda d: d["answers"]["executor_choice"].update(confidence=1.7), "confidence out of range"),
        (lambda d: d["answers"]["executor_choice"].update(confidence=-0.1), "confidence negative"),
        (lambda d: d["answers"]["executor_choice"].update(choice=None), "choice null"),
        (lambda d: d["answers"]["executor_choice"].update(type="guess"), "unknown answer type"),
        (lambda d: d["answers"]["safe_to_execute"].pop("noul"), "noul missing"),
        (lambda d: d["answers"]["safe_to_execute"].update(noul="yes"), "noul not a bool"),
        (lambda d: d["answers"]["safe_to_execute"].pop("type"), "type missing"),
        (lambda d: d["answers"]["risk_level"].update(score="low"), "score not a number"),
        (lambda d: d["answers"]["risk_level"].pop("legend"), "legend missing"),
        (lambda d: d["answers"]["risk_level"].pop("confidence"), "score confidence missing"),
        (lambda d: d.update(usage="none"), "usage not an object"),
    ])
    def test_rejects_malformed_payloads(self, mutate, why):
        payload = a_response()
        mutate(payload)
        with pytest.raises(SystemOneProtocolError):
            parse_systemone(payload)

    def test_rejects_non_object_response(self):
        for bad in ([], "text", 42, None):
            with pytest.raises(SystemOneProtocolError):
                parse_systemone(bad)

    def test_rejects_non_object_answer(self):
        with pytest.raises(SystemOneProtocolError):
            parse_answer("workbuddy")

    def test_confidence_is_rejected_not_clamped(self):
        # Clamping 1.7 to 1.0 would turn a malformed reply into a confident one,
        # which is the exact failure a fail-closed parser prevents.
        payload = a_response()
        payload["answers"]["executor_choice"]["confidence"] = 1.7
        with pytest.raises(SystemOneProtocolError, match="outside"):
            parse_systemone(payload)

    def test_usage_may_be_absent_but_not_wrong_type(self):
        payload = a_response()
        payload.pop("usage")
        r = parse_systemone(payload)
        assert r.usage == {}

    def test_empty_answers_is_valid_but_useless(self):
        # Structurally valid; decide_executor is what rejects it downstream.
        r = parse_systemone({"model": "jev-latest", "answers": {}})
        assert r.answers == {}

    def test_non_json_body_raises_protocol_error(self, monkeypatch):
        class Raw:
            status = 200
            headers = _FakeHeaders({})

            def read(self):
                return b"<html>not json</html>"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: Raw())
        with pytest.raises(SystemOneProtocolError):
            JevProvider(api_key=SECRET).decide({}, {})


# --------------------------------------------------------------------------- #
# 5. HTTP failure mapping
# --------------------------------------------------------------------------- #


class TestHttpFailures:
    @pytest.mark.parametrize("code,expected", [
        (401, JevAuthError),
        (403, JevAuthError),
        (429, JevRateLimitError),
        (500, JevUnavailableError),
        (502, JevUnavailableError),
        (503, JevUnavailableError),
    ])
    def test_status_codes_map_to_typed_errors(self, monkeypatch, code, expected):
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda req, timeout=None: (_ for _ in ()).throw(http_error(code)),
        )
        with pytest.raises(expected):
            JevProvider(api_key=SECRET).decide({}, {})

    def test_4xx_other_is_a_generic_api_error(self, monkeypatch):
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda req, timeout=None: (_ for _ in ()).throw(http_error(422)),
        )
        with pytest.raises(JevApiError) as ei:
            JevProvider(api_key=SECRET).decide({}, {})
        # Not auth, not rate limit, not unavailable.
        assert not isinstance(ei.value, (JevAuthError, JevRateLimitError, JevUnavailableError))

    def test_transport_failure_is_unavailable(self, monkeypatch):
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda req, timeout=None: (_ for _ in ()).throw(
                urllib.error.URLError("dns fail")
            ),
        )
        with pytest.raises(JevUnavailableError):
            JevProvider(api_key=SECRET).models()

    def test_timeout_is_unavailable(self, monkeypatch):
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda req, timeout=None: (_ for _ in ()).throw(TimeoutError("slow")),
        )
        with pytest.raises(JevUnavailableError):
            JevProvider(api_key=SECRET).models()

    def test_timeout_value_is_passed_to_the_transport(self, monkeypatch):
        fake = FakeHTTP(payload=a_response())
        monkeypatch.setattr("urllib.request.urlopen", fake)
        JevProvider(api_key=SECRET, timeout_s=3.5).decide({}, {})
        assert fake.calls[0]["timeout"] == 3.5

    def test_no_automatic_retry_on_429(self, monkeypatch):
        """One attempt only — a retry against a paid endpoint can double-charge."""
        attempts = {"n": 0}

        def counting(req, timeout=None):
            attempts["n"] += 1
            raise http_error(429)

        monkeypatch.setattr("urllib.request.urlopen", counting)
        with pytest.raises(JevRateLimitError):
            JevProvider(api_key=SECRET).decide({}, {})
        assert attempts["n"] == 1

    def test_no_request_without_a_credential(self, monkeypatch):
        def forbidden(req, timeout=None):
            raise AssertionError("the wire must not be touched without a key")

        monkeypatch.setattr("urllib.request.urlopen", forbidden)
        monkeypatch.delenv("JEV_API_KEY", raising=False)
        c = JevProvider()
        assert c.has_credential is False
        with pytest.raises(JevAuthError):
            c.models()
        with pytest.raises(JevAuthError):
            c.decide({}, {})


# --------------------------------------------------------------------------- #
# 6. Secret redaction
# --------------------------------------------------------------------------- #


class TestSecretRedaction:
    def test_redact_removes_the_full_secret(self):
        out = redact(f"failed with Bearer {SECRET}", SECRET)
        assert SECRET not in out
        assert "REDACTED" in out

    def test_redact_removes_a_truncated_prefix(self):
        out = redact(f"key {SECRET[:8]}… rejected", SECRET)
        assert SECRET[:8] not in out

    def test_redact_is_a_noop_without_a_secret(self):
        assert redact("nothing to hide", None) == "nothing to hide"
        assert redact("nothing to hide", "") == "nothing to hide"

    def test_repr_never_contains_the_key(self):
        c = JevProvider(api_key=SECRET)
        assert SECRET not in repr(c)
        assert "credential=present" in repr(c)

    def test_repr_marks_an_absent_credential(self):
        monkey = JevProvider(api_key="")
        assert "credential=absent" in repr(monkey)

    def test_as_dict_exposes_a_boolean_not_the_key(self):
        d = JevProvider(api_key=SECRET).as_dict()
        assert d["credential_present"] is True
        assert d["credential_env_name"] == "JEV_API_KEY"
        assert SECRET not in json.dumps(d)

    def test_health_report_never_contains_the_key(self, monkeypatch):
        # Stub the transport at the point the client actually calls it, so the
        # health path runs end to end and the report is built for real.
        def both(req, timeout=None):
            if "models" in req.full_url:
                return _FakeResponse(
                    200, json.dumps({"data": ["jev-latest"]}).encode(), {}
                )
            return _FakeResponse(200, json.dumps(a_response()).encode(), {})

        monkeypatch.setattr("urllib.request.urlopen", both)
        h = JevProvider(api_key=SECRET).health()
        assert h["auth"] == "PASS"
        assert SECRET not in json.dumps(h)

    def test_transport_error_message_is_redacted(self, monkeypatch):
        class Leaky:
            status = 401

            def read(self):
                return f"Authorization: Bearer {SECRET}".encode()

        def raiser(req, timeout=None):
            raise urllib.error.HTTPError(
                "u", 401, "m", None, None,
            ) from None

        def leaky_raiser(req, timeout=None):
            # HTTPError with a readable fp that echoes the header.
            import io as _io

            raise urllib.error.HTTPError(
                "u", 401, "m", None, _io.BytesIO(f"Bearer {SECRET}".encode())
            )

        monkeypatch.setattr("urllib.request.urlopen", leaky_raiser)
        with pytest.raises(JevAuthError) as ei:
            JevProvider(api_key=SECRET).decide({}, {})
        assert SECRET not in str(ei.value)


# --------------------------------------------------------------------------- #
# 7. Provider state
# --------------------------------------------------------------------------- #


class TestProviderState:
    def test_missing_credential_is_auth_missing_not_mock(self):
        # The distinction the spec demands: nothing is mocking anything, the
        # vendor model simply has no key.
        assert provider_state(credential_present=False) == STATE_REAL_AUTH_MISSING

    def test_credential_present_is_ready(self):
        assert provider_state(credential_present=True) == STATE_REAL_READY

    def test_transport_failure_is_unavailable(self):
        assert (
            provider_state(credential_present=True, transport_ok=False)
            == STATE_REAL_UNAVAILABLE
        )

    def test_explicit_stand_in_modes(self):
        assert provider_state(credential_present=True, mode="mock") == STATE_MOCK
        assert provider_state(credential_present=True, mode="disabled") == STATE_DISABLED

    def test_five_states_are_exactly_these(self):
        from jcc_core.providers.jev_api import PROVIDER_STATES

        assert set(PROVIDER_STATES) == {
            "REAL_READY", "REAL_AUTH_MISSING", "REAL_UNAVAILABLE", "MOCK", "DISABLED",
        }


# --------------------------------------------------------------------------- #
# 8. health()
# --------------------------------------------------------------------------- #


class TestHealth:
    """
    ``health()`` is now the DECISION readiness probe: /v1/models, then a real
    /v1/systemone round trip. It replaced the earlier models-only read, so these
    assertions were rewritten — READY now requires the decision call, not just a
    readable list. See test_jev_decision_capability.py for the full contract.
    """

    def test_health_without_credential_reports_auth_missing(self, monkeypatch):
        monkeypatch.delenv("JEV_API_KEY", raising=False)
        h = JevProvider().health()
        assert h["state"] == STATE_REAL_AUTH_MISSING
        assert h["auth"] == "FAIL"
        assert h["models_list"] is None
        assert "JEV_API_KEY" in h["detail"]

    def test_health_never_raises(self, monkeypatch):
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda req, timeout=None: (_ for _ in ()).throw(
                urllib.error.URLError("down")
            ),
        )
        h = JevProvider(api_key=SECRET).health()
        assert h["state"] == STATE_REAL_UNAVAILABLE
        assert h["error_class"] == "transport"

    def test_health_reports_ready_only_after_a_decision_call(self, monkeypatch):
        """A readable model list is NOT enough — the decision endpoint must answer."""
        def both(req, timeout=None):
            if "models" in req.full_url:
                return _FakeResponse(
                    200, json.dumps({"data": ["jev-latest"]}).encode(), {}
                )
            return _FakeResponse(
                200,
                json.dumps(a_response()).encode(),
                {"x-request-id": "req_1"},
            )

        monkeypatch.setattr("urllib.request.urlopen", both)
        h = JevProvider(api_key=SECRET).health()
        assert h["state"] == STATE_REAL_READY
        assert h["auth"] == "PASS"
        assert h["models"] == "PASS"
        assert h["systemone"] == "PASS"
        assert h["schema"] == "PASS"
        assert h["models_list"] == ["jev-latest"]
        assert h["request_id"] == "req_1"

    def test_health_on_auth_failure_reports_auth_missing(self, monkeypatch):
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda req, timeout=None: (_ for _ in ()).throw(http_error(401)),
        )
        h = JevProvider(api_key=SECRET).health()
        assert h["state"] == STATE_REAL_AUTH_MISSING

    def test_health_shape_is_identical_in_every_outcome(self, monkeypatch):
        """A caller must not be able to branch on a missing key to mean success."""
        monkeypatch.delenv("JEV_API_KEY", raising=False)
        no_key = set(JevProvider().health())
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda req, timeout=None: (_ for _ in ()).throw(TimeoutError()),
        )
        with_key = set(JevProvider(api_key=SECRET).health())
        assert no_key == with_key

    def test_models_rejects_an_unreadable_listing(self, monkeypatch):
        # A structurally wrong envelope is a protocol error...
        for bad in ({"nope": 1}, {"data": "notalist"}):
            monkeypatch.setattr(
                "urllib.request.urlopen", FakeHTTP(payload=bad)
            )
            with pytest.raises(SystemOneProtocolError):
                JevProvider(api_key=SECRET).models()

    def test_models_skips_entries_it_cannot_read(self, monkeypatch):
        """
        An entry with no recognisable id is SKIPPED, not fatal.

        It used to raise, which meant one unexpected entry shape blanked the
        whole list and the provider was reported as having no models. That is
        exactly how a working account was marked UNAVAILABLE.
        """
        monkeypatch.setattr(
            "urllib.request.urlopen",
            FakeHTTP(payload={"data": [{"unexpected": 1}, "jev-latest"]}),
        )
        assert JevProvider(api_key=SECRET).models() == ["jev-latest"]


# --------------------------------------------------------------------------- #
# 9. Executor decision
# --------------------------------------------------------------------------- #


def _provider_returning(payload, **kw):
    c = JevProvider(api_key=SECRET, **kw)
    c.decide = lambda state, questions: parse_systemone(payload, request_id="req_x", latency_ms=7)
    return c


def _decide(payload, **kwargs):
    c = _provider_returning(payload)
    return decide_executor(
        c,
        task="t", project_root="D:\\jev", task_type="code",
        **kwargs,
    )


class TestExecutorDecision:
    def test_high_confidence_safe_choice_is_auto_accepted(self):
        d = _decide(a_response(choice="codex", confidence=0.94, noul=True))
        assert d.executor == "codex"
        assert d.source == "jev"
        assert d.confidence == pytest.approx(0.94)
        assert d.safe_to_execute is True
        assert d.needs_confirmation is False
        assert d.latency_ms == 7
        assert d.request_id == "req_x"
        assert d.risk_score == pytest.approx(0.2)

    def test_each_answer_type_is_read_from_its_question(self):
        d = _decide(a_response())
        assert d.executor == "workbuddy"
        assert d.safe_to_execute is True
        assert d.risk_score is not None

    def test_low_confidence_requires_confirmation(self):
        d = _decide(a_response(confidence=0.5))
        assert d.source == "jev"          # Jev DID decide
        assert d.needs_confirmation is True
        assert "confidence" in d.reason

    def test_unsafe_requires_confirmation(self):
        d = _decide(a_response(noul=False))
        assert d.source == "jev"
        assert d.safe_to_execute is False
        assert d.needs_confirmation is True
        assert "unsafe" in d.reason

    def test_absent_safe_to_execute_is_not_permission(self):
        # A partial reply must not authorise execution.
        payload = a_response()
        payload["answers"].pop("safe_to_execute")
        d = _decide(payload)
        assert d.needs_confirmation is True
        assert d.source == "jev"
        assert "safe_to_execute" in d.reason

    def test_missing_executor_choice_falls_back(self):
        payload = a_response()
        payload["answers"].pop("executor_choice")
        d = _decide(payload)
        # No fallback was configured, so the honest answer is "nobody decided" —
        # not a silent pick. `undecided` is the state the UI must render as
        # "Jev did not decide".
        assert d.source == "undecided"
        assert d.executor == ""
        assert d.needs_confirmation is True

    def test_choice_outside_the_offered_set_is_refused(self):
        d = _decide(a_response(choice="gpt-9"))
        assert d.source == "undecided"
        assert d.executor == ""
        assert "not offered" in d.reason

    def test_fallback_executor_is_used_when_configured(self):
        d = _decide(a_response(choice="gpt-9"), fallback_executor="hermes")
        assert d.executor == "hermes"
        assert d.source == "fallback"
        assert d.needs_confirmation is True

    def test_fallback_executor_must_itself_be_known(self):
        d = _decide(a_response(choice="gpt-9"), fallback_executor="gpt-9")
        assert d.executor == ""   # refused rather than forwarded

    def test_without_credential_it_does_not_pretend_jev_decided(self, monkeypatch):
        monkeypatch.delenv("JEV_API_KEY", raising=False)
        def forbidden(req, timeout=None):
            raise AssertionError("must not call the wire")
        monkeypatch.setattr("urllib.request.urlopen", forbidden)

        d = decide_executor(
            JevProvider(),
            task="t", project_root="D:\\jev", task_type="code",
        )
        assert d.source == "undecided"
        assert d.executor == ""
        assert d.needs_confirmation is True
        assert d.provider_state == STATE_REAL_AUTH_MISSING
        # The implementation deliberately does NOT leak the credential name
        # literal (per FINAL PRODUCT UNBLOCK §12). It reports REAL_AUTH_MISSING
        # without naming the env var, exactly as test_credential_source_unification
        # already pins down.
        assert "REAL_AUTH_MISSING" in (d.reason or "")
        assert "JEV_API_KEY" not in (d.reason or "")

    def test_malformed_reply_becomes_a_fallback_not_a_guess(self, monkeypatch):
        class Boom:
            has_credential = True

            def decide(self, state, questions):
                raise SystemOneProtocolError("confidence 1.7 outside [0, 1]")

        d = decide_executor(
            Boom(), task="t", project_root="D:\\jev", task_type="code",
            fallback_executor="codex",
        )
        assert d.source == "fallback"
        assert d.executor == "codex"
        assert d.needs_confirmation is True

    def test_rate_limit_becomes_a_fallback(self):
        class Throttled:
            has_credential = True

            def decide(self, state, questions):
                raise JevRateLimitError("429")

        d = decide_executor(
            Throttled(), task="t", project_root="D:\\jev", task_type="code"
        )
        # No fallback configured -> undecided, and the reason names the throttling.
        assert d.source == "undecided"
        assert d.executor == ""
        assert "rate-limited" in d.reason

    def test_state_carries_executor_health_and_capabilities(self):
        captured = {}

        class Recorder:
            has_credential = True

            def decide(self, state, questions):
                captured["state"] = state
                captured["questions"] = questions
                return parse_systemone(a_response())

        decide_executor(
            Recorder(),
            task="t", project_root="D:\\jev", task_type="code",
            risk_context={"band": "high"},
            executor_health={"workbuddy": "CONNECTED", "codex": "DISCONNECTED"},
            capabilities={"workbuddy": ["shell", "edit"]},
        )
        s = captured["state"]
        assert s["task_type"] == "code"
        assert s["risk_context"] == {"band": "high"}
        assert s["executor_health"]["codex"] == "DISCONNECTED"
        assert s["capabilities"]["workbuddy"] == ["shell", "edit"]

    def test_threshold_is_configurable_and_default_is_high(self):
        assert LOW_CONFIDENCE_THRESHOLD >= 0.75
        d = _decide(a_response(confidence=0.8), require_confirmation_below=0.5)
        assert d.needs_confirmation is False
        d2 = _decide(a_response(confidence=0.8), require_confirmation_below=0.95)
        assert d2.needs_confirmation is True

    def test_decision_as_dict_has_no_secret(self):
        d = _decide(a_response())
        assert SECRET not in json.dumps(d.as_dict())


# --------------------------------------------------------------------------- #
# 10. Question / state builders
# --------------------------------------------------------------------------- #


class TestBuilders:
    def test_questions_use_all_three_official_types(self):
        q = build_executor_questions(list(KNOWN_EXECUTORS))
        assert q["executor_choice"]["type"] == QUESTION_CHOICE
        assert q["safe_to_execute"]["type"] == QUESTION_NOUL
        assert q["risk_level"]["type"] == QUESTION_SCORE

    def test_questions_offer_exactly_the_known_executors(self):
        # Updated to the official contract: the permitted answers are the KEYS
        # of `criteria`, and there is no `options` field. The earlier version
        # asserted on `options`, which is the shape that made Jev answer a
        # criteria key verbatim — the field did not exist, we invented it, and
        # it hid the real answer set.
        q = build_executor_questions(["workbuddy", "codex", "gpt-9"])
        assert set(q["executor_choice"]["criteria"]) == {"workbuddy", "codex"}
        assert "options" not in q["executor_choice"]
        # Every key is a real executor, and none is a description field name.
        assert set(q["executor_choice"]["criteria"]) <= set(KNOWN_EXECUTORS)

    def test_questions_reject_an_empty_option_set(self):
        with pytest.raises(ValueError):
            build_executor_questions([])
        with pytest.raises(ValueError):
            build_executor_questions(["gpt-9"])

    def test_state_includes_the_four_local_facts(self):
        s = build_executor_state(
            task="do it", project_root="D:\\jev", task_type="code",
            risk_context={"band": "low"},
            available_executors=["workbuddy", "unknown-exec"],
            executor_health={"workbuddy": "CONNECTED"},
            capabilities={"workbuddy": []},
        )
        assert s["task"] == "do it"
        assert s["project_root"] == "D:\\jev"
        assert s["task_type"] == "code"
        assert s["risk_context"] == {"band": "low"}
        assert s["available_executors"] == ["workbuddy"]   # unknown filtered
        assert s["executor_health"] == {"workbuddy": "CONNECTED"}

    def test_state_defaults_are_empty_not_fabricated(self):
        s = build_executor_state(task="t", project_root="p", task_type="code")
        assert s["risk_context"] == {}
        assert s["executor_health"] == {}
        assert s["capabilities"] == {}

    def test_four_executors_are_known(self):
        assert set(KNOWN_EXECUTORS) == {"workbuddy", "codex", "claude", "hermes"}


# --------------------------------------------------------------------------- #
# 11. RealProvider wiring
# --------------------------------------------------------------------------- #


class TestRealProviderWiring:
    def test_real_provider_reports_auth_missing_without_a_key(self, monkeypatch):
        from jcc_core.providers.real import RealProvider

        monkeypatch.delenv("JEV_API_KEY", raising=False)
        p = RealProvider()
        h = p.health()
        assert h["state"] == STATE_REAL_AUTH_MISSING
        assert h["provider"] == "typesafe"
        assert h["credential_present"] is False

    def test_real_provider_distinguishes_transport_from_verified(self, monkeypatch):
        from jcc_core.providers.real import (
            RealProvider, RealJevInterface, InterfaceField,
            AUTHORITY_MEASURED, TRANSPORT_IMPLEMENTED,
        )

        monkeypatch.delenv("JEV_API_KEY", raising=False)
        p = RealProvider()
        assert p.mode == "REAL"
        assert p.is_real is True
        # With BOTH locks closed today (transport not implemented AND the card
        # still carries unverified critical fields) neither flag is set...
        assert p.transport_ready is False
        assert p.verified is False
        # ...and the two flags are INDEPENDENT: a fully VERIFIED card still
        # cannot open the wire while the transport lock is closed. Conflating
        # the two would tell the kernel a real number is behind an unverified
        # band -- the distinction this test exists to protect.
        fully = RealJevInterface(fields=tuple(
            InterfaceField(f.name, f.value or "known", AUTHORITY_MEASURED, "test",
                           critical=f.critical)
            for f in p.interface.fields
        ))
        assert fully.all_critical_verified() is True
        assert RealProvider(interface=fully).transport_ready == TRANSPORT_IMPLEMENTED
        assert RealProvider(interface=fully).verified is False

    def test_real_provider_interface_card_honestly_reports_critical_gaps(self, monkeypatch):
        from jcc_core.providers.real import RealProvider

        # The honest card must NAME the request-construction surface that is
        # not yet smoke-verified (§7E) rather than silently reporting zero gaps.
        p = RealProvider()
        missing = p.interface.missing_critical()
        assert len(missing) >= 10
        for required in ("endpoint_path", "http_method", "api_version",
                         "auth_header_syntax", "model_identifier",
                         "request_schema", "response_schema",
                         "timeout_contract", "error_schema",
                         "idempotency_semantics"):
            assert required in missing, f"{required} must be declared UNVERIFIED"
        assert p.interface.all_critical_verified() is False

    def test_real_provider_endpoint_paths_are_the_official_ones(self, monkeypatch):
        from jcc_core.providers.real import RealProvider

        p = RealProvider()
        assert p.models_path == "/v1/models"
        assert p.systemone_path == "/v1/systemone"
        assert p.model == "jev-latest"

    def test_real_provider_interface_card_records_official_facts(self, monkeypatch):
        from jcc_core.providers.real import RealProvider

        # The card keys each entry by ``field`` (the name), not ``name``.
        card = RealProvider().interface.as_dict()["fields"]
        by_name = {f["field"]: f for f in card}
        # Verified public facts stay VERIFIED.
        assert by_name["credential_mechanism"]["status"] == "VERIFIED"
        assert by_name["output_paradigm"]["status"] == "VERIFIED"
        # The critical request-construction surface is honestly UNVERIFIED -- it
        # is NOT faked as VERIFIED (the §7G over-claim this test now guards
        # against). A stand-in must never render as a real, wire-ready contract.
        for f in ("endpoint_path", "http_method", "api_version",
                  "auth_header_syntax", "model_identifier",
                  "request_schema", "response_schema",
                  "timeout_contract", "error_schema", "idempotency_semantics"):
            assert by_name[f]["status"] == "UNVERIFIED", (
                f"{f} must stay UNVERIFIED until smoke-verified (§7E)"
            )
        # The named gaps are present (not silently dropped).
        assert "endpoint_path" in by_name

    def test_unmet_requirements_names_the_credential_and_the_scoring_path(self, monkeypatch):
        from jcc_core.providers.real import RealProvider

        monkeypatch.delenv("JEV_API_KEY", raising=False)
        unmet = RealProvider().unmet_requirements()
        # The transport lock, the credential, and the AEP scoring path are each
        # a separate blocker; naming only the credential would imply a key is
        # sufficient, and it is not -- `assess()` is still the placeholder.
        assert "transport_not_implemented" in unmet
        assert "credential_absent(JEV_API_KEY)" in unmet
        assert "assess_path_not_yet_wired_to_systemone" in unmet
        # And the unverified critical interface fields are named explicitly --
        # the whole point of the honest card is to surface (not hide) the gaps.
        assert any(u.startswith("interface_field_critical_unverified:")
                   for u in unmet)

    def test_real_provider_never_exposes_the_key_in_reports(self, monkeypatch):
        from jcc_core.providers.real import RealProvider

        p = RealProvider(api_key=SECRET)
        assert SECRET not in json.dumps(p.interface_report())
        assert SECRET not in json.dumps(p.health())
