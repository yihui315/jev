"""Contract tests for the capability-aware provider readiness.

The bug these pin: Jev (TypeSafe System One) is a DECISION provider. It was being
probed with a generation request, which tests a capability it does not have, and
the resulting "failure" was reported as a broken provider. Separately, the live
`/v1/models` response keys its entries by ``name``, and a parser that only knew
``id`` turned a working credential into UNAVAILABLE.

No API key needed: everything here is about what the code does with a given
wire shape, and the wire shapes used are the ones MEASURED from the service.
"""
from __future__ import annotations

import json

import pytest

from jcc_core.providers.generation import (
    DISCOVERY_SUPPORTED,
    DISCOVERY_UNSUPPORTED,
    STATE_AUTH_FAILED,
    STATE_DISCOVERY_UNSUPPORTED,
    STATE_UNAVAILABLE,
    VERIFICATION_DECISION,
    VERIFICATION_NOT_VERIFIED,
    DeepSeekProvider,
    MiniMaxProvider,
    QwenCodingPlanProvider,
)
from jcc_core.providers.jev_api import (
    DECISION_CAPABILITIES,
    SYSTEMONE_PATH,
    JevProvider,
    SystemOneProtocolError,
    _model_id,
    parse_answer,
)

SECRET = "test-key-abcdefghijklmnopqrstuvwxyz0123456789"

#: The entry shape the LIVE service returned, captured verbatim. Keyed by
#: "name" — NOT "id", which is what broke the original parser.
MEASURED_MODEL_ENTRY = {
    "name": "jev-latest",
    "description": "The latest iteration of TypeSafe's System One Model: Jev",
    "release_date": "2026-09-10T18:38:01.391457+00:00",
}


# --------------------------------------------------------------------------- #
# Transport doubles
# --------------------------------------------------------------------------- #


class _Resp:
    def __init__(self, status, body, headers=None):
        self.status = status
        self._body = json.dumps(body).encode()
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


def jev_models_ok(model_id="jev-latest"):
    """`GET /v1/models` answering 200 with the MEASURED entry shape."""
    entry = dict(MEASURED_MODEL_ENTRY)
    entry["name"] = model_id

    def opener(req, timeout=None):
        return _Resp(200, {"data": [entry]}, {"x-request-id": "req_models"})

    return opener


def systemone_ok(model="jev-latest", confidence=0.93):
    """`POST /v1/systemone` answering 200 with a schema-valid decision."""
    body = {
        "model": model,
        "answers": {
            "connectivity": {"type": "noul", "noul": 0.98},
            "severity": {
                "type": "choice", "choice": "trivial", "confidence": confidence,
                "probabilities": {"trivial": confidence, "moderate": 0.05, "severe": 0.02},
            },
            "confidence_score": {
                "type": "score", "score": 0.1, "confidence": 0.88,
                "legend": {"low": [0.0, 0.33]}, "probabilities": {"low": 0.9},
            },
        },
        "usage": {"input_tokens": 12, "output_tokens": 8, "total_tokens": 20},
    }

    def opener(req, timeout=None):
        return _Resp(200, body, {"x-request-id": "req_decision"})

    return opener


def both_ok(model="jev-latest"):
    def opener(req, timeout=None):
        return jev_models_ok(model)(req, timeout) if "models" in req.full_url \
            else systemone_ok(model)(req, timeout)

    return opener


def http_err(code, body=b"denied"):
    import urllib.error

    def opener(req, timeout=None):
        raise urllib.error.HTTPError("u", code, "m", _H({}), None)

    return opener


# --------------------------------------------------------------------------- #
# 1. The measured /v1/models shape
# --------------------------------------------------------------------------- #


class TestModelIdParsing:
    def test_measured_name_keyed_entry(self):
        """The regression: the live service keys entries by NAME."""
        assert _model_id(MEASURED_MODEL_ENTRY) == "jev-latest"

    def test_id_keyed_entry_still_works(self):
        assert _model_id({"id": "jev-1.13.0"}) == "jev-1.13.0"

    def test_model_keyed_entry_works(self):
        assert _model_id({"model": "x"}) == "x"

    def test_plain_string_works(self):
        assert _model_id("jev-latest") == "jev-latest"

    def test_unknown_shape_yields_none(self):
        assert _model_id({"unexpected": 1}) is None
        assert _model_id(123) is None

    def test_blank_values_are_rejected(self):
        assert _model_id("   ") is None
        assert _model_id({"name": ""}) is None

    def test_models_endpoint_accepts_the_measured_shape(self, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", jev_models_ok())
        assert JevProvider(api_key=SECRET).models() == ["jev-latest"]

    def test_one_unreadable_entry_does_not_hide_the_rest(self, monkeypatch):
        """One odd entry must not blank the list — that is how a working account
        was reported as having no models at all."""
        def opener(req, timeout=None):
            return _Resp(200, {"data": [{"weird": 1}, dict(MEASURED_MODEL_ENTRY)]})

        monkeypatch.setattr("urllib.request.urlopen", opener)
        assert JevProvider(api_key=SECRET).models() == ["jev-latest"]


# --------------------------------------------------------------------------- #
# 2. Jev is a DECISION provider
# --------------------------------------------------------------------------- #


class TestJevIsDecisionOnly:
    def test_declares_decision_kind(self):
        assert "decision" in DECISION_CAPABILITIES
        assert "generation" not in DECISION_CAPABILITIES

    def test_no_credential_is_auth_missing_not_unavailable(self, monkeypatch):
        monkeypatch.delenv("JEV_API_KEY", raising=False)
        r = JevProvider(api_key=None).health()
        assert r["state"] == "REAL_AUTH_MISSING"
        assert r["verification"] == VERIFICATION_NOT_VERIFIED
        assert r["error_class"] == "auth_missing"

    def test_never_asks_for_generation(self, monkeypatch):
        """A decision probe must not issue a completion request.

        The only calls allowed are /v1/models and /v1/systemone. A request to a
        chat endpoint from this client would be the bug this whole change is
        about, so it is asserted rather than assumed.
        """
        seen = []

        def opener(req, timeout=None):
            seen.append(req.full_url)
            return both_ok()(req, timeout)

        monkeypatch.setattr("urllib.request.urlopen", opener)
        JevProvider(api_key=SECRET).health()
        assert seen, "no request was issued"
        for url in seen:
            assert "/chat/completions" not in url
            assert url.endswith("/v1/models") or url.endswith(SYSTEMONE_PATH)

    def test_full_pass_reaches_ready(self, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", both_ok())
        r = JevProvider(api_key=SECRET).health()
        assert r["state"] == "REAL_READY"
        assert r["verification"] == VERIFICATION_DECISION
        assert r["auth"] == "PASS"
        assert r["models"] == "PASS"
        assert r["systemone"] == "PASS"
        assert r["schema"] == "PASS"
        assert r["model"] == "jev-latest"
        assert r["http_status"] == 200
        assert r["request_id"] == "req_decision"
        assert r["latency_ms"] >= 0

    def test_model_is_taken_from_the_list_not_assumed(self, monkeypatch):
        """The account serves something other than the configured alias.

        Naming the un-served alias would fail for a reason unrelated to
        readiness, so the probe picks from what /v1/models actually returned.
        """
        monkeypatch.setattr("urllib.request.urlopen", both_ok("jev-2.0-rc1"))
        r = JevProvider(api_key=SECRET, model="jev-latest").health()
        assert r["model"] == "jev-2.0-rc1"
        assert r["state"] == "REAL_READY"

    def test_prefers_the_configured_alias_when_the_account_has_it(self, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", both_ok("jev-latest"))
        r = JevProvider(api_key=SECRET, model="jev-latest").health()
        assert r["model"] == "jev-latest"

    def test_usage_is_recorded(self, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", both_ok())
        r = JevProvider(api_key=SECRET).health()
        assert r["usage"].get("input_tokens") == 12
        assert r["usage"].get("output_tokens") == 8

    def test_answer_types_are_all_exercised(self, monkeypatch):
        """One noul, one choice, one score — the parser must handle all three on
        a single round trip."""
        monkeypatch.setattr("urllib.request.urlopen", both_ok())
        r = JevProvider(api_key=SECRET).health()
        assert set(r["answers_present"]) == {"connectivity", "severity", "confidence_score"}

    def test_report_never_contains_the_key(self, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", both_ok())
        r = JevProvider(api_key=SECRET).health()
        assert SECRET not in json.dumps(r)


class TestJevFailureModes:
    def test_401_is_auth_failure(self, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", http_err(401))
        r = JevProvider(api_key=SECRET).health()
        assert r["state"] == "REAL_AUTH_MISSING"
        assert r["auth"] == "FAIL"
        assert r["error_class"] == "auth"
        assert r["http_status"] == 401

    def test_429_is_reported_as_rate_limit(self, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", http_err(429))
        r = JevProvider(api_key=SECRET).health()
        assert r["error_class"] == "rate_limit"
        assert r["http_status"] == 429

    def test_5xx_is_unavailable(self, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", http_err(503))
        r = JevProvider(api_key=SECRET).health()
        assert r["state"] == "REAL_UNAVAILABLE"
        assert r["error_class"] == "transport"

    def test_schema_violation_is_its_own_class(self, monkeypatch):
        """A 200 whose answers are malformed is neither ready nor a transport
        fault — a schema change must be distinguishable from an outage."""
        def opener(req, timeout=None):
            if "models" in req.full_url:
                return jev_models_ok()(req, timeout)
            return _Resp(200, {"model": "jev-latest", "answers": {
                "severity": {"type": "choice", "choice": "trivial", "confidence": 1.7,
                             "probabilities": {}},
            }})

        monkeypatch.setattr("urllib.request.urlopen", opener)
        r = JevProvider(api_key=SECRET).health()
        assert r["systemone"] == "FAIL"
        assert r["error_class"] == "schema"
        assert r["state"] != "REAL_READY"

    def test_empty_answers_is_a_schema_failure(self, monkeypatch):
        def opener(req, timeout=None):
            if "models" in req.full_url:
                return jev_models_ok()(req, timeout)
            return _Resp(200, {"model": "jev-latest", "answers": {}})

        monkeypatch.setattr("urllib.request.urlopen", opener)
        r = JevProvider(api_key=SECRET).health()
        assert r["schema"] == "FAIL"
        assert r["state"] != "REAL_READY"

    def test_account_with_no_models_is_reported(self, monkeypatch):
        def opener(req, timeout=None):
            return _Resp(200, {"data": []})

        monkeypatch.setattr("urllib.request.urlopen", opener)
        r = JevProvider(api_key=SECRET).health()
        assert r["error_class"] == "no_model"
        assert r["state"] != "REAL_READY"

    def test_422_is_its_own_class_not_a_transport_fault(self, monkeypatch):
        """
        The error this client actually produced first.

        The live service rejected the ENVELOPE with 422 and named the missing
        fields (`questions.severity.choice.criteria`, and the score equivalent).
        That is a request bug — the key was accepted and /v1/models answered — so
        it must not be reported as "unavailable", which would send an operator to
        debug the credential or the network.

        The stub therefore lets /v1/models succeed and fails only the decision
        call, which is where the 422 actually happened.
        """
        def opener(req, timeout=None):
            if "models" in req.full_url:
                return jev_models_ok()(req, timeout)
            return http_err(422)(req, timeout)

        monkeypatch.setattr("urllib.request.urlopen", opener)
        r = JevProvider(api_key=SECRET).health()
        assert r["error_class"] == "request_rejected"
        assert r["http_status"] == 422
        assert r["auth"] == "PASS"       # models succeeded before the call
        assert r["models"] == "PASS"
        assert r["state"] != "REAL_READY"

    def test_probe_questions_carry_criteria_with_per_type_shape(self, monkeypatch):
        """
        `criteria` is a SIBLING of `type`, and its TYPE depends on the question:
        a mapping for `choice`, a list for `score`.

        Four real 422s produced that rule, and each step is worth keeping because
        the error narrowed rather than repeated:

          * nesting under `"choice"` -> same `missing` loc, yet the response echo
            showed the object present, so `choice` is the ANSWER SLOT;
          * sibling + list -> `dict_type` on the choice question;
          * sibling + mapping -> the choice question PASSED and the score
            question came back `list_type`.

        Asserted on the wire, because a refactor could quietly collapse the two
        types back into one and only another 422 would reveal it.
        """
        sent = {}

        def opener(req, timeout=None):
            if "models" in req.full_url:
                return jev_models_ok()(req, timeout)
            sent["body"] = json.loads(req.data.decode())
            return systemone_ok()(req, timeout)

        monkeypatch.setattr("urllib.request.urlopen", opener)
        r = JevProvider(api_key=SECRET).health()
        assert r["state"] == "REAL_READY"
        q = sent["body"]["questions"]

        # All THREE types need criteria. The `noul` one is not expressible in the
        # JSON schema — the service rejected it with a 400 that only appears when
        # the request actually reaches the rule ("Noul question must have criteria
        # or instructions"), so it can only be pinned by a live call.
        for name in ("connectivity", "severity", "confidence_score"):
            assert "criteria" in q[name], name
            assert "choice" not in q[name], name
            assert "score" not in q[name], name

        # Per-question type: the service rejects the other one.
        assert isinstance(q["severity"]["criteria"], dict)
        assert isinstance(q["confidence_score"]["criteria"], list)

    def test_noul_is_a_probability_and_keeps_its_figure(self):
        """
        MEASURED twice, and the first reading was wrong.

        The first live reply carried `noul: 1.0` and a parser demanding a real
        bool rejected it ("must be a boolean, got float"). Accepting 0/1 then
        revealed the truth: the next reply carried `noul: 0.55`. So `noul` is a
        probabilistic yes/no with a 0.5 boundary.

        Thresholding here is not a rounding of a boolean — 0.55 means "yes,
        weakly" — and the raw probability is preserved so a 0.51 cannot be
        mistaken for a 0.99.
        """
        a = parse_answer({"type": "noul", "noul": 0.55})
        assert a.noul is True                     # 0.55 >= 0.5
        assert a.noul_probability == 0.55         # and the figure survives

        b = parse_answer({"type": "noul", "noul": 0.45})
        assert b.noul is False
        assert b.noul_probability == 0.45

        c = parse_answer({"type": "noul", "noul": 1.0})
        assert c.noul is True and c.noul_probability == 1.0

        d = parse_answer({"type": "noul", "noul": 0.0})
        assert d.noul is False and d.noul_probability == 0.0

        e = parse_answer({"type": "noul", "noul": True})
        assert e.noul is True and e.noul_probability == 1.0

    def test_noul_rejects_values_outside_the_probability_range(self):
        """Outside [0,1] is malformed and stays rejected.

        The earlier version of this test asserted that 0.7 must be refused. That
        was wrong — it was written before the service was observed returning 0.55,
        and it would have thrown away a real decision. The line is the RANGE, not
        "is it a clean zero or one".
        """
        for bad in (-0.1, 1.5, 2, -1, "yes", None):
            with pytest.raises(SystemOneProtocolError):
                parse_answer({"type": "noul", "noul": bad})

    def test_noul_requires_the_field(self):
        with pytest.raises(SystemOneProtocolError):
            parse_answer({"type": "noul"})

    def test_error_detail_never_carries_the_key(self, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", http_err(401))
        r = JevProvider(api_key=SECRET).health()
        assert SECRET not in json.dumps(r)

    def test_probe_report_shape_is_stable(self, monkeypatch):
        """Every outcome must carry the same keys, so a caller cannot read a
        missing field as a success."""
        monkeypatch.delenv("JEV_API_KEY", raising=False)
        no_key = set(JevProvider(api_key=None).health())
        monkeypatch.setattr("urllib.request.urlopen", both_ok())
        with_key = set(JevProvider(api_key=SECRET).health())
        monkeypatch.setattr("urllib.request.urlopen", http_err(500))
        failed = set(JevProvider(api_key=SECRET).health())
        assert no_key == with_key == failed


# --------------------------------------------------------------------------- #
# 3. Generators keep the generation requirement
# --------------------------------------------------------------------------- #


class TestGeneratorsUnchanged:
    @pytest.mark.parametrize("cls", [MiniMaxProvider, QwenCodingPlanProvider, DeepSeekProvider])
    def test_generation_kind(self, cls):
        assert cls().verification_kind == "generation"

    def test_verify_capability_dispatches_to_generation(self, monkeypatch):
        """The single entry point must actually reach the generation probe."""
        monkeypatch.setattr("urllib.request.urlopen", _chat_ok())
        h = QwenCodingPlanProvider(secret=SECRET).verify_capability()
        assert h.state == "READY"
        assert h.generation_verified is True

    def test_generation_is_still_required_for_ready(self, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", http_err(401))
        h = QwenCodingPlanProvider(secret=SECRET).verify_capability()
        assert h.state == STATE_AUTH_FAILED
        assert h.generation_verified is False

    def test_minimax_discovery_stays_unsupported(self, monkeypatch):
        h = MiniMaxProvider(secret=SECRET).health()
        assert h.state == STATE_DISCOVERY_UNSUPPORTED
        assert h.discovery == DISCOVERY_UNSUPPORTED
        assert h.models_are_preset is True
        assert h.generation_verified is False

    def test_discovery_supported_is_separate_from_ready(self, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", _models_ok("qwen3.7-plus"))
        h = QwenCodingPlanProvider(secret=SECRET).health()
        assert h.discovery == DISCOVERY_SUPPORTED
        assert h.state != "READY"


def _chat_ok():
    def opener(req, timeout=None):
        return _Resp(200, {
            "model": "m",
            "choices": [{"message": {"content": "JEV_OK"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        })
    return opener


def _models_ok(*ids):
    def opener(req, timeout=None):
        return _Resp(200, {"data": [{"id": i} for i in ids]})
    return opener
