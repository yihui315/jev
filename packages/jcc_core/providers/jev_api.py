"""Official TypeSafe Jev API client — System One decision model.

NAMING (this distinction is load-bearing)
------------------------------------------
``JEV``  = this product. The AI Work Operating System that plans, supervises and
           reports. It is OUR system.
``Jev``  = TypeSafe's System One decision model. A VENDOR service we call.

They are different things that share a spelling. A module named ``jev_api.py``
therefore speaks about the VENDOR model, and every identifier in here is
prefixed to make the speaker unambiguous.

OFFICIAL CONTRACT (public, per the vendor's published OpenAPI surface)
----------------------------------------------------------------------
    BASE_URL  https://api.typesafe.ai
    GET       /v1/models
    POST      /v1/systemone
    AUTH      Authorization: Bearer <API_KEY>
    ALIAS     jev-latest

Request body::

    {"model": "jev-latest", "state": {...}, "questions": {...}}

Response (``SystemOneResponse``)::

    {"model": ..., "answers": {...}, "usage": {...}}

Answer types, exactly three::

    {"type": "noul",   "noul": bool}
    {"type": "choice", "choice": str, "confidence": float,
     "probabilities": {...}}
    {"type": "score",  "score": float, "confidence": float,
     "legend": {...}, "probabilities": {...}}

WHAT THIS MODULE REFUSES TO DO
------------------------------
* Guess a field. A response missing anything the schema requires raises
  :class:`SystemOneProtocolError` — it is never patched with a default.
* Retry automatically. A retry against a paid endpoint can double-charge, and
  the vendor publishes no idempotency contract, so retry policy is the CALLER's
  decision, made with full knowledge of the cost.
* Touch the API key beyond reading it from the environment. There is no logging
  call anywhere in this file that receives it, and ``__repr__`` of the client
  redacts it.
* Fabricate a decision. If Jev cannot answer, the answer is an error, and the
  caller must fail closed. There is no "default executor" hiding in here.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

# --------------------------------------------------------------------------- #
# Official contract constants
# --------------------------------------------------------------------------- #

#: Vendor base URL. Overridable for a staging account, never for a guess.
DEFAULT_BASE_URL = "https://api.typesafe.ai"

#: Published paths. These are contract, not discovery.
MODELS_PATH = "/v1/models"
SYSTEMONE_PATH = "/v1/systemone"

#: The vendor's moving model alias. Preferred over a pinned version because the
#: alias is what the vendor documents for current-model access.
DEFAULT_MODEL_ALIAS = "jev-latest"

#: Environment variables. Only NAMES live in code; values are read at runtime
#: from the process environment and never written anywhere.
ENV_API_KEY = "JEV_API_KEY"
ENV_BASE_URL = "JEV_BASE_URL"
ENV_MODEL = "JEV_MODEL"

#: Request timeout. A decision that arrives after the operator has moved on is
#: worse than one that never arrives, so this is bounded rather than default.
DEFAULT_TIMEOUT_S = 20.0

#: The three official question/answer types.
QUESTION_NOUL = "noul"
QUESTION_CHOICE = "choice"
QUESTION_SCORE = "score"
QUESTION_TYPES = (QUESTION_NOUL, QUESTION_CHOICE, QUESTION_SCORE)

#: What Jev IS. It decides; it does not generate prose or code. A provider that
#: is handed a generation probe is being tested on a capability it does not
#: have, and a provider that lacks one is not a broken provider.
DECISION_CAPABILITIES = ("decision", "routing", "risk", "verification")
GENERATION_CAPABILITIES = ("generation",)

#: The three axes a provider report carries. ``discovery`` is about the model
#: list; ``verification`` is about the capability that was actually exercised.
VERIFICATION_DECISION = "DECISION_VERIFIED"
VERIFICATION_GENERATION = "GENERATION_VERIFIED"
VERIFICATION_NOT_VERIFIED = "NOT_VERIFIED"


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class JevApiError(Exception):
    """Base for every failure this client raises. Never carries the API key."""


class JevAuthError(JevApiError):
    """401/403 — credential missing, wrong, or insufficient. Fail closed."""


class JevRateLimitError(JevApiError):
    """429 — throttled. The caller decides whether a retry is worth the cost."""


class JevValidationError(JevApiError):
    """422 — the request envelope was rejected.

    Deliberately its own type. A 422 means the credential was accepted, the
    endpoint answered, and the SHAPE was wrong — so the fix is to the request,
    not to the key or the network. Collapsing it into "unavailable" would send
    an operator to debug the wrong thing, and it is the error this client
    actually produced first: the service named the missing `criteria` fields.
    """


class JevUnavailableError(JevApiError):
    """5xx, DNS failure, timeout, or a transport that never reached the API."""


class SystemOneProtocolError(JevApiError):
    """The response did not match the published schema.

    This is deliberately its own type. A 200 with a body we cannot read is NOT a
    successful decision and NOT an outage — it is a contract change, and treating
    it as either would put a fabricated number into the executor pipeline.
    """


# --------------------------------------------------------------------------- #
# Provider state
# --------------------------------------------------------------------------- #

#: The five states a real Jev connection can be in. ``MOCK`` and ``DISABLED``
#: name the two stand-ins so a UI can never render a green "Jev decided" over a
#: provider that is neither real nor reachable.
STATE_REAL_READY = "REAL_READY"
STATE_REAL_AUTH_MISSING = "REAL_AUTH_MISSING"
STATE_REAL_UNAVAILABLE = "REAL_UNAVAILABLE"
STATE_MOCK = "MOCK"
STATE_DISABLED = "DISABLED"

PROVIDER_STATES = (
    STATE_REAL_READY,
    STATE_REAL_AUTH_MISSING,
    STATE_REAL_UNAVAILABLE,
    STATE_MOCK,
    STATE_DISABLED,
)


def provider_state(
    *,
    credential_present: bool,
    transport_ok: bool = True,
    mode: str = "real",
) -> str:
    """Derive the provider state. One function so the rule cannot drift.

    The distinction the spec cares about: a real provider with NO credential is
    ``REAL_AUTH_MISSING``, never ``MOCK``. Reporting "mock" there would tell an
    operator their decision came from a deterministic stand-in when in fact the
    system is blocked on one missing environment variable.
    """
    m = (mode or "real").lower()
    if m == "mock":
        return STATE_MOCK
    if m in ("disabled", "replay"):
        return STATE_DISABLED
    if not credential_present:
        return STATE_REAL_AUTH_MISSING
    if not transport_ok:
        return STATE_REAL_UNAVAILABLE
    return STATE_REAL_READY


# --------------------------------------------------------------------------- #
# Redaction
# --------------------------------------------------------------------------- #


def redact(text: str, secret: Optional[str]) -> str:
    """Remove a secret from a string that is about to be logged or returned.

    Used for every error path that could echo request headers. A secret that
    reaches a log or a browser is no longer a secret, and the cheapest place to
    stop that is the moment the string is built.
    """
    if not secret:
        return text
    out = text.replace(secret, "***REDACTED***")
    # A truncated form of the key can leak too (e.g. an error quoting the first
    # 8 chars), so the prefix is masked as well.
    if len(secret) >= 8:
        out = out.replace(secret[:8], "***RED***")
    return out


# --------------------------------------------------------------------------- #
# Request / response models
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class NoulAnswer:
    """A yes/no judgement. ``noul`` is the vendor's spelling, kept verbatim.

    MEASURED: the service answers with a PROBABILITY (0.0 … 1.0), not a boolean —
    one reply carried 1.0, the next 0.55. So the decision is the thresholded
    value and ``noul_probability`` keeps the model's own figure, because a 0.51 and
    a 0.99 both mean "yes" and a caller that only saw the boolean could not tell
    them apart.
    """

    noul: bool
    noul_probability: float = 1.0
    type: str = QUESTION_NOUL

    def as_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "noul": self.noul,
            "noul_probability": self.noul_probability,
        }


@dataclass(frozen=True)
class ChoiceAnswer:
    """A selection from a fixed option set, with the model's own confidence."""

    choice: str
    confidence: float
    probabilities: Dict[str, float] = field(default_factory=dict)

    type: str = QUESTION_CHOICE

    def as_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "choice": self.choice,
            "confidence": self.confidence,
            "probabilities": self.probabilities,
        }


@dataclass(frozen=True)
class ScoreAnswer:
    """A graded judgement. ``legend`` carries the vendor's own band names."""

    score: float
    confidence: float
    legend: Dict[str, Any] = field(default_factory=dict)
    probabilities: Dict[str, float] = field(default_factory=dict)

    type: str = QUESTION_SCORE

    def as_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "score": self.score,
            "confidence": self.confidence,
            "legend": self.legend,
            "probabilities": self.probabilities,
        }


#: Every answer type the parser can return.
Answer = Any  # NoulAnswer | ChoiceAnswer | ScoreAnswer


@dataclass(frozen=True)
class SystemOneResponse:
    """A parsed, schema-validated System One reply."""

    model: str
    answers: Dict[str, Answer]
    usage: Dict[str, Any] = field(default_factory=dict)
    request_id: Optional[str] = None
    latency_ms: int = 0

    def answer(self, name: str) -> Optional[Answer]:
        return self.answers.get(name)

    def noul(self, name: str) -> Optional[bool]:
        a = self.answers.get(name)
        return a.noul if isinstance(a, NoulAnswer) else None

    def choice(self, name: str) -> Optional[ChoiceAnswer]:
        a = self.answers.get(name)
        return a if isinstance(a, ChoiceAnswer) else None

    def score(self, name: str) -> Optional[ScoreAnswer]:
        a = self.answers.get(name)
        return a if isinstance(a, ScoreAnswer) else None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "model": self.model,
            "answers": {k: v.as_dict() for k, v in self.answers.items()},
            "usage": self.usage,
            "request_id": self.request_id,
            "latency_ms": self.latency_ms,
        }


#: Every key a decision report carries, in every outcome. Fixed here so a caller
#: can rely on ``report["usage"]`` existing rather than on it being absent when
#: things go wrong — an absent key reads as a signal long before it reads as a
#: bug.
_REPORT_KEYS = (
    "state", "verification", "capabilities", "auth", "models", "systemone",
    "schema", "model", "models_list", "models_latency_ms", "endpoint",
    "latency_ms", "http_status", "error_class", "detail", "request_id",
    "usage", "answers_present", "credential_present",
)


def _stable_shape(report: Mapping[str, Any]) -> Dict[str, Any]:
    """Return ``report`` with exactly :data:`_REPORT_KEYS`.

    Absent keys become ``None`` rather than being dropped, so the shape a caller
    sees is identical whether the probe passed, failed on auth, or never ran.
    """
    return {k: report.get(k) for k in _REPORT_KEYS}


def _model_id(item: Any) -> Optional[str]:
    """Read one model identifier out of a `/v1/models` entry.

    MEASURED, not assumed. The live service returns objects keyed by ``name``::

        {"name": "jev-latest",
         "description": "The latest iteration of TypeSafe's System One Model: Jev",
         "release_date": "2026-09-10T18:38:01.391457+00:00"}

    An earlier parser accepted only ``id``, so a perfectly good response was
    reported as `unreadable model entry` and the provider was marked
    UNAVAILABLE — a green credential turned red by a field-name guess. All three
    shapes are therefore accepted, and an entry with none of them yields ``None``
    so it is SKIPPED rather than poisoning the whole list: one unexpected entry
    must not hide the models that were readable.
    """
    if isinstance(item, str):
        return item.strip() or None
    if not isinstance(item, dict):
        return None
    for key in ("id", "name", "model"):
        v = item.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _require(d: Mapping[str, Any], key: str, kind: str) -> Any:
    """Fetch a required field or raise. Never returns a default."""
    if key not in d:
        raise SystemOneProtocolError(f"missing required field {key!r}")
    v = d[key]
    if kind == "str" and not isinstance(v, str):
        raise SystemOneProtocolError(f"field {key!r} must be a string, got {type(v).__name__}")
    if kind == "bool" and not isinstance(v, bool):
        raise SystemOneProtocolError(f"field {key!r} must be a boolean, got {type(v).__name__}")
    if kind == "float":
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise SystemOneProtocolError(
                f"field {key!r} must be a number, got {type(v).__name__}"
            )
    if kind == "dict" and not isinstance(v, dict):
        raise SystemOneProtocolError(f"field {key!r} must be an object, got {type(v).__name__}")
    return v


def _noul_value(raw: Mapping[str, Any]) -> Tuple[bool, float]:
    """Read a `noul` answer, which is a PROBABILITY rather than a boolean.

    MEASURED, twice, and the first reading was wrong:
      * the first live reply carried ``noul: 1.0`` and a parser that demanded a
        real bool rejected it with "must be a boolean, got float";
      * after accepting 0/1, the next reply carried ``noul: 0.55``.

    So `noul` is a probabilistic yes/no: the model returns how likely it is, and
    the decision boundary is 0.5. Thresholding is therefore NOT a rounding of a
    boolean — 0.55 genuinely means "yes, weakly", and 0.4 genuinely means "no,
    weakly". Reporting `0.55 -> True` while keeping the number visible is honest;
    reporting only `True` would throw away the model's actual confidence.

    The raw probability is returned alongside the decision so no consumer can
    mistake a 0.51 for a 0.99.
    """
    if "noul" not in raw:
        raise SystemOneProtocolError("missing required field 'noul'")
    v = raw["noul"]
    if isinstance(v, bool):
        return v, (1.0 if v else 0.0)
    if isinstance(v, (int, float)):
        p = float(v)
        if not (0.0 <= p <= 1.0):
            raise SystemOneProtocolError(
                f"field 'noul' must be a probability in [0, 1], got {v!r}"
            )
        return p >= 0.5, p
    raise SystemOneProtocolError(
        f"field 'noul' must be a boolean or a probability, got {v!r}"
    )


def parse_answer(raw: Any) -> Answer:
    """Validate ONE answer object against the published schema.

    Raises :class:`SystemOneProtocolError` on anything malformed. The three
    branches are exhaustive by construction: an unrecognised ``type`` cannot
    fall through to a default, because the default would be a fabricated answer
    presented to the executor pipeline as if Jev had produced it.
    """
    if not isinstance(raw, dict):
        raise SystemOneProtocolError(f"answer must be an object, got {type(raw).__name__}")
    t = _require(raw, "type", "str")

    if t == QUESTION_NOUL:
        decision, probability = _noul_value(raw)
        return NoulAnswer(noul=decision, noul_probability=probability)

    if t == QUESTION_CHOICE:
        return ChoiceAnswer(
            choice=_require(raw, "choice", "str"),
            confidence=float(_require(raw, "confidence", "float")),
            probabilities=_require(raw, "probabilities", "dict"),
        )

    if t == QUESTION_SCORE:
        return ScoreAnswer(
            score=float(_require(raw, "score", "float")),
            confidence=float(_require(raw, "confidence", "float")),
            legend=_require(raw, "legend", "dict"),
            probabilities=_require(raw, "probabilities", "dict"),
        )

    raise SystemOneProtocolError(f"unknown answer type {t!r}")


def parse_systemone(payload: Any, *, request_id: Optional[str] = None, latency_ms: int = 0) -> SystemOneResponse:
    """Validate a full System One response body.

    Strict on purpose. Every field the contract declares is required, and a
    confidence outside ``[0, 1]`` is rejected rather than clamped: a clamped
    confidence would silently promote a malformed reply into a confident one,
    which is the exact failure mode a fail-closed parser exists to prevent.
    """
    if not isinstance(payload, dict):
        raise SystemOneProtocolError(f"response must be an object, got {type(payload).__name__}")

    model = _require(payload, "model", "str")
    answers_raw = _require(payload, "answers", "dict")
    usage = payload.get("usage", {})
    if not isinstance(usage, dict):
        raise SystemOneProtocolError("usage must be an object when present")

    answers: Dict[str, Answer] = {}
    for name, raw in answers_raw.items():
        answer = parse_answer(raw)
        conf = getattr(answer, "confidence", None)
        if conf is not None and not (0.0 <= conf <= 1.0):
            raise SystemOneProtocolError(
                f"answer {name!r} confidence {conf} is outside [0, 1]"
            )
        answers[name] = answer

    return SystemOneResponse(
        model=model,
        answers=answers,
        usage=usage,
        request_id=request_id,
        latency_ms=latency_ms,
    )


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #


@dataclass
class JevProvider:
    """Client for the official TypeSafe System One API.

    Construct with ``api_key`` for an explicit credential, or leave it ``None``
    to read :data:`ENV_API_KEY` from the process environment. The key is held in
    a private attribute and never appears in ``repr``/``str``/``as_dict``.

    This is the VENDOR model client. It is deliberately NOT the product's
    orchestrator: choosing an executor is :func:`select_executor`'s job, and that
    function needs this client plus local facts it alone has.
    """

    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL_ALIAS
    timeout_s: float = DEFAULT_TIMEOUT_S
    _api_key: Optional[str] = None

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ):
        self.base_url = (base_url or os.environ.get(ENV_BASE_URL) or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or os.environ.get(ENV_MODEL) or DEFAULT_MODEL_ALIAS
        self.timeout_s = timeout_s
        self._api_key = api_key if api_key is not None else os.environ.get(ENV_API_KEY)

    # -- secret handling -------------------------------------------------- #

    @property
    def has_credential(self) -> bool:
        return bool(self._api_key)

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            f"JevProvider(base_url={self.base_url!r}, model={self.model!r}, "
            f"credential={'present' if self._api_key else 'absent'})"
        )

    def as_dict(self) -> Dict[str, Any]:
        """Report for a health endpoint. The key is a boolean, never a value."""
        return {
            "provider": "typesafe",
            "model": self.model,
            "base_url": self.base_url,
            "credential_present": self.has_credential,
            "credential_env_name": ENV_API_KEY,
            "state": provider_state(credential_present=self.has_credential),
        }

    # -- transport -------------------------------------------------------- #

    def _headers(self) -> Dict[str, str]:
        if not self._api_key:
            raise JevAuthError(
                f"{ENV_API_KEY} is not set; refusing to contact the Jev API"
            )
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(
        self, method: str, path: str, body: Optional[Dict[str, Any]] = None
    ) -> Tuple[int, Dict[str, Any], Dict[str, str], int]:
        """Issue one request. Returns (status, parsed_body, headers, latency_ms).

        Single attempt by design — see the module docstring on retries. Every
        error message is passed through :func:`redact` before it leaves here, so
        a header echoed by an upstream proxy cannot carry the key into a log.
        """
        url = f"{self.base_url}{path}"
        data = None
        if body is not None:
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method)
        for k, v in self._headers().items():
            req.add_header(k, v)

        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                raw = resp.read()
                status = resp.status
                headers = {k.lower(): v for k, v in resp.headers.items()}
        except urllib.error.HTTPError as e:
            latency = int((time.monotonic() - started) * 1000)
            detail = redact(f"HTTP {e.code}: {e.read()[:400]!r}", self._api_key)
            if e.code in (401, 403):
                raise JevAuthError(detail) from None
            if e.code == 429:
                raise JevRateLimitError(detail) from None
            if e.code in (400, 422):
                # The service told us what is wrong with the envelope. 400 is a
                # SEMANTIC rejection (the shape parsed, a rule was violated) and
                # 422 a STRUCTURAL one; both mean "fix the request", so both map
                # here rather than to "unavailable".
                raise JevValidationError(detail) from None
            if e.code >= 500:
                raise JevUnavailableError(detail) from None
            raise JevApiError(detail) from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            latency = int((time.monotonic() - started) * 1000)
            raise JevUnavailableError(
                redact(f"transport failure: {e}", self._api_key)
            ) from None

        latency = int((time.monotonic() - started) * 1000)
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError) as e:
            raise SystemOneProtocolError(f"response was not JSON: {e}") from None
        return status, parsed, headers, latency

    # -- public API ------------------------------------------------------- #

    def models(self) -> List[str]:
        """``GET /v1/models`` -> the available model identifiers.

        Raises rather than returning a placeholder: a caller that cannot list
        models cannot pick one, and an empty list would look like "this account
        has nothing", which is a different and wrong claim.
        """
        _, body, _, _ = self._request("GET", MODELS_PATH)
        if not isinstance(body, dict):
            raise SystemOneProtocolError("models response must be an object")
        raw = body.get("data", body.get("models"))
        if not isinstance(raw, list):
            raise SystemOneProtocolError("models response has no 'data' array")
        return [m for m in (_model_id(item) for item in raw) if m]

    def verify_decision(self, prompt: str = "") -> Dict[str, Any]:
        """Prove the DECISION capability: ``/v1/models`` then ``/v1/systemone``.

        Jev does not generate prose or code, so it must never be put through a
        generation probe — asking it to "reply exactly JEV_OK" tests a capability
        it does not have, and a provider that lacks one is not a broken provider.
        What it DOES have is a decision endpoint, so that is what gets exercised.

        The probe checks four things and cares about none of the answer's content:
        the model list is readable, a real model id is selected FROM THAT LIST
        (never assumed), the round trip returns 200, and the body matches the
        published schema.

        Returns a report rather than raising: the caller renders it, and a failed
        probe is a reportable state, not an exception.
        """
        report: Dict[str, Any] = {
            "state": STATE_REAL_UNAVAILABLE,
            "verification": VERIFICATION_NOT_VERIFIED,
            "capabilities": list(DECISION_CAPABILITIES),
            "auth": "FAIL",
            "models": "FAIL",
            "systemone": "FAIL",
            "schema": "FAIL",
            "model": None,
            "endpoint": f"{self.base_url}{SYSTEMONE_PATH}",
            "latency_ms": 0,
            "http_status": None,
            "error_class": None,
            "detail": None,
            # Never a secret: the report is served to a browser.
            "credential_present": self.has_credential,
        }
        if not self.has_credential:
            report["state"] = STATE_REAL_AUTH_MISSING
            report["error_class"] = "auth_missing"
            report["detail"] = f"{ENV_API_KEY} is not set; the vendor model was not consulted"
            return report

        # 1. MODELS — also the AUTH proof, since /v1/models is gated.
        models_started = time.monotonic()
        try:
            models = self.models()
        except JevAuthError as e:
            report["state"] = STATE_REAL_AUTH_MISSING
            report["error_class"] = "auth"
            report["http_status"] = 401
            report["detail"] = redact(str(e), self._api_key)
            return report
        except JevRateLimitError as e:
            report["state"] = STATE_REAL_UNAVAILABLE
            report["error_class"] = "rate_limit"
            report["http_status"] = 429
            report["detail"] = redact(str(e), self._api_key)
            return report
        except (JevUnavailableError, JevApiError) as e:
            report["state"] = STATE_REAL_UNAVAILABLE
            report["error_class"] = "transport"
            report["detail"] = redact(str(e), self._api_key)
            return report

        report["auth"] = "PASS"
        report["models"] = "PASS"
        report["models_list"] = models
        report["models_latency_ms"] = int((time.monotonic() - models_started) * 1000)

        # 2. Pick a model FROM THE LIST. Never assume the configured alias is
        #    present: the account may serve something else, and a request naming
        #    an unavailable model would fail for a reason unrelated to readiness.
        chosen = self.model if self.model in models else (models[0] if models else None)
        if not chosen:
            report["error_class"] = "no_model"
            report["detail"] = "该账号没有可用模型"
            return report
        report["model"] = chosen

        # 3. SYSTEMONE — the decision call, with a minimal safe envelope.
        #
        # SHAPE LEARNED FROM THE SERVICE, NOT ASSUMED. Every field below was
        # added because a real response demanded it, and the sequence is kept
        # because each error narrowed the next one:
        #
        #   1. `{"type","question","options"}`
        #      -> 422 missing  loc=…severity.choice.criteria
        #   2. `criteria` NESTED under `"choice"`
        #      -> 422 missing  (SAME loc) while the response's `input` echo
        #         showed it present — so `choice` is the ANSWER SLOT, not question
        #         config, and anything under it is invisible to the validator.
        #   3. `criteria` as a SIBLING, list value
        #      -> 422 dict_type on the choice question (value WAS echoed, so the
        #         position was right and only the type was wrong)
        #   4. `criteria` as a SIBLING, mapping value
        #      -> the choice question passed; score came back 422 list_type
        #   5. all three shapes correct
        #      -> 400 "Noul question must have criteria or instructions:
        #         connectivity"
        #         Schema validation passes end to end; what remains is a rule the
        #         JSON-schema layer cannot express, so it is only discoverable by
        #         asking. A `noul` needs EITHER `criteria` OR `instructions` —
        #         the message names both, and `criteria` alone was refused, so
        #         both are sent rather than guessing which one it wants.
        #
        # The content stays trivial — this probe measures whether the decision
        # path works, not what the model thinks about severity.
        # Built with the SAME three builders the router uses. The earlier version
        # hand-wrote its questions and the two sets drifted: this one was fixed
        # against the live service while the router's still 422'd, so a fix here
        # silently left the router broken. One builder, one shape.
        #
        # The severity choice lists its answers as criteria KEYS — the official
        # contract — with no separate `options` field, which is also what the
        # service demanded all along.
        questions = {
            "connectivity": build_noul_question(
                "Is this endpoint reachable?",
                {
                    "true": "The endpoint returned a valid response.",
                    "false": "The endpoint did not respond as expected.",
                },
            ),
            "severity": build_choice_question(
                "How severe is this connectivity check?",
                {
                    "trivial": "A connectivity check succeeds or fails; nothing else is at stake.",
                    "moderate": "Failure blocks the caller but no data is at risk.",
                    "severe": "Failure risks data loss or an outward-facing effect.",
                },
            ),
            "confidence_score": build_score_question(
                "How confident are you in this check?",
                SCORE_LEVELS_COMPLEXITY,
            ),
        }
        try:
            response = self.decide(
                {"note": prompt or "JEV live connectivity test."}, questions, model=chosen
            )
        except JevAuthError as e:
            report["state"] = STATE_REAL_AUTH_MISSING
            report["error_class"] = "auth"
            report["http_status"] = 401
            report["detail"] = redact(str(e), self._api_key)
            return report
        except JevRateLimitError as e:
            report["state"] = STATE_REAL_UNAVAILABLE
            report["error_class"] = "rate_limit"
            report["http_status"] = 429
            report["detail"] = redact(str(e), self._api_key)
            return report
        except JevValidationError as e:
            # 422: the credential and the endpoint are fine, the REQUEST SHAPE is
            # not. Its own class, because "our envelope is wrong" and "the
            # service is down" call for completely different fixes and must never
            # be reported as the same fault.
            report["state"] = STATE_REAL_UNAVAILABLE
            report["error_class"] = "request_rejected"
            report["http_status"] = 422
            report["detail"] = redact(str(e), self._api_key)
            return report
        except SystemOneProtocolError as e:
            # A 200 whose body does not match the contract is neither ready nor
            # a transport fault. Reported as its own class so a schema change is
            # distinguishable from an outage.
            report["state"] = STATE_REAL_UNAVAILABLE
            report["error_class"] = "schema"
            report["detail"] = redact(str(e), self._api_key)
            return report
        except (JevUnavailableError, JevApiError) as e:
            report["state"] = STATE_REAL_UNAVAILABLE
            report["error_class"] = "transport"
            report["detail"] = redact(str(e), self._api_key)
            return report

        # 4. SCHEMA — already enforced by parse_systemone, which raises rather
        #    than returning a partial object. Reaching here means the body
        #    matched; the extra checks confirm the parts this probe cares about.
        report["systemone"] = "PASS"
        report["schema"] = "PASS"
        report["http_status"] = 200
        report["latency_ms"] = response.latency_ms
        report["request_id"] = response.request_id
        report["usage"] = dict(response.usage) if response.usage else {}
        report["answers_present"] = sorted(response.answers.keys())

        if not response.answers:
            report["error_class"] = "schema"
            report["schema"] = "FAIL"
            report["detail"] = "响应缺少 answers"
            return report

        # Confidence and probabilities must be inside [0, 1] where present. The
        # parser already rejects out-of-range values, so this records the fact
        # rather than re-validating it.
        numeric_ok = True
        for name, ans in response.answers.items():
            conf = getattr(ans, "confidence", None)
            if conf is not None and not (0.0 <= float(conf) <= 1.0):
                numeric_ok = False
                report["error_class"] = "schema"
                report["detail"] = f"answer {name} confidence 超出 [0,1]"
        if not numeric_ok:
            report["schema"] = "FAIL"
            return report

        report["state"] = STATE_REAL_READY
        report["verification"] = VERIFICATION_DECISION
        return _stable_shape(report)

    def verify_decision_report(self) -> Dict[str, Any]:
        """The probe, with the key set NORMALISED.

        Every outcome returns the same keys. A success that grows extra keys
        breaks any caller doing ``report.get("usage")`` to decide something: in
        the failure case that key is absent, and absence starts to look like a
        signal. So the shape is fixed once, here, rather than at each return.
        """
        return _stable_shape(self.verify_decision())

    def health(self) -> Dict[str, Any]:
        """Readiness for a DECISION provider. Never raises.

        Delegates to :meth:`verify_decision`, which is the only definition of
        "Jev is ready" — so a card cannot claim READY from a model list alone
        while the decision endpoint is broken. One source of truth, because two
        probes for one capability is how they start disagreeing.

        READY here requires ALL of: auth accepted, models listed, a real decision
        round trip, and a schema-valid body. It does NOT require a generation,
        because Jev has no generation capability to verify — asking a decision
        model to "reply exactly JEV_OK" tests something it cannot do, and
        reporting that as a failure would be a bug in the probe, not in Jev.
        """
        return self.verify_decision_report()

    def decide(
        self,
        state: Mapping[str, Any],
        questions: Mapping[str, Any],
        *,
        model: Optional[str] = None,
    ) -> SystemOneResponse:
        """``POST /v1/systemone`` — one decision round.

        ``state`` and ``questions`` are passed through as given; this method does
        not invent, default, or reorder keys. The response is schema-validated
        before it is returned, so a caller holding a :class:`SystemOneResponse`
        is holding something that matched the published contract.

        ``model`` overrides the configured alias for this call only. The probe
        uses it to name a model the ACCOUNT actually serves, discovered from
        ``/v1/models`` rather than assumed.
        """
        if not isinstance(state, Mapping):
            raise TypeError("state must be a mapping")
        if not isinstance(questions, Mapping):
            raise TypeError("questions must be a mapping")

        body = {
            "model": model or self.model,
            "state": dict(state),
            "questions": dict(questions),
        }
        _, payload, headers, latency = self._request("POST", SYSTEMONE_PATH, body)
        return parse_systemone(
            payload,
            request_id=headers.get("x-request-id") or headers.get("request-id"),
            latency_ms=latency,
        )


# --------------------------------------------------------------------------- #
# Executor selection
# --------------------------------------------------------------------------- #

#: The four executors Jev may choose between. A choice outside this set is
#: refused rather than forwarded: the mission runner would not know what to do
#: with it, and sending it would spend a paid call to learn nothing.
KNOWN_EXECUTORS: Tuple[str, ...] = ("workbuddy", "codex", "claude", "hermes")

#: Question names. Constants because the request and every log line must agree.
Q_EXECUTOR_CHOICE = "executor_choice"
Q_SAFE_TO_EXECUTE = "safe_to_execute"
Q_RISK_LEVEL = "risk_level"

#: Confidence below this requires a human, never a silent auto-pick. Chosen so a
#: genuinely split decision (0.6/0.4) cannot pass as decided.
LOW_CONFIDENCE_THRESHOLD = 0.75


@dataclass(frozen=True)
class ExecutorDecision:
    """The outcome of one selection round, with its provenance attached.

    ``source`` is the field that makes the UI honest: it is ``"jev"`` only when a
    real Jev call produced this, and never when a fallback did.
    """

    executor: str
    source: str
    confidence: float
    safe_to_execute: Optional[bool] = None
    risk_score: Optional[float] = None
    needs_confirmation: bool = False
    reason: Optional[str] = None
    request_id: Optional[str] = None
    latency_ms: int = 0
    provider_state: str = STATE_REAL_READY

    def as_dict(self) -> Dict[str, Any]:
        return {
            "executor": self.executor,
            "source": self.source,
            "confidence": self.confidence,
            "safe_to_execute": self.safe_to_execute,
            "risk_score": self.risk_score,
            "needs_confirmation": self.needs_confirmation,
            "reason": self.reason,
            "request_id": self.request_id,
            "latency_ms": self.latency_ms,
            "provider_state": self.provider_state,
        }


#: WHY THERE ARE NO MORE FREE-FORM CRITERIA CONSTANTS
#: ---------------------------------------------------
#: The official contract: for a ``choice`` question, **the KEYS of ``criteria``
#: are the permitted answers**. There is no separate ``options`` field.
#:
#: MEASURED, and this is the whole bug: with ``criteria={"note": "..."}`` Jev
#: answered ``note``; with ``criteria={"basis": "..."}`` it answered ``basis``.
#: Both were CORRECT behaviour — we had invented a field name and presented it
#: as a candidate. The parser then (rightly) rejected it as un-offered, and the
#: router reported "no usable provider" for a question we had mis-authored.
#:
#: So a criteria key is never a description. If a key is not a legitimate answer,
#: it must not exist.


def build_choice_question(
    instructions: str, choices: Mapping[str, str]
) -> Dict[str, Any]:
    """A ``choice`` question. The criteria KEYS are the allowed answers.

    ``choices`` maps an answer to the condition under which it applies — the
    official ``{"choice_a": "When choice A applies"}`` shape. A question with no
    permitted answer cannot be answered, so an empty mapping is rejected here
    rather than sent to earn a confusing 422.
    """
    if not choices:
        raise ValueError("choice question needs at least one permitted answer")
    return {
        "type": QUESTION_CHOICE,
        "instructions": instructions,
        "criteria": {str(k): str(v) for k, v in choices.items()},
    }


def build_score_question(
    instructions: str, levels: Sequence[str]
) -> Dict[str, Any]:
    """A ``score`` question. ``criteria`` is a LIST; the index is the level.

    The list form is official — an object is not an accepted substitute, and a
    mapping here would be read as a set of keys rather than an ordered scale.
    """
    if not levels:
        raise ValueError("score question needs at least one level")
    return {
        "type": QUESTION_SCORE,
        "instructions": instructions,
        "criteria": [str(x) for x in levels],
    }


def build_noul_question(
    instructions: str, criteria: Optional[Mapping[str, str]] = None
) -> Dict[str, Any]:
    """A ``noul`` question, whose answer is a probability.

    ``criteria`` is optional in the official contract, and when present its keys
    are the permitted answers. The answer itself is a [0, 1] probability and is
    NOT thresholded here — deciding what counts as "safe enough" is Jev's call
    and belongs to the caller that has the policy, not to the transport.
    """
    q: Dict[str, Any] = {"type": QUESTION_NOUL, "instructions": instructions}
    if criteria:
        q["criteria"] = {str(k): str(v) for k, v in criteria.items()}
    return q


#: The four complexity levels Jev is asked to rate against, most to least
#: complex. The index is the score, so the ORDER is the contract.
SCORE_LEVELS_RISK = [
    "0.0 means no risk of harm, 1.0 means severe risk of irreversible harm",
    "0.2 means minor and reversible",
    "0.5 means significant but bounded",
    "1.0 means severe and irreversible",
]
SCORE_LEVELS_COMPLEXITY = ["Simple", "Moderate", "Complex", "Very complex"]


def _executor_criteria(available_executors: Sequence[str]) -> Dict[str, str]:
    """Map each AVAILABLE executor to the condition under which it applies.

    Keyed by executor name — the name IS the answer, so it must be the key.
    Only the executors actually on offer appear: offering criteria for an
    executor that cannot run would invite an answer that then fails.
    """
    known = {
        "workbuddy": "Available native executor for local repository execution, tools, tests and artifact delivery.",
        "codex": "Available native executor for repository-scoped code changes and verification runs.",
        "claude": "Available native executor for reasoning-heavy analysis and code review.",
        "hermes": "Available native executor for message delivery and external communication tasks.",
    }
    return {e: known[e] for e in available_executors if e in KNOWN_EXECUTORS}


#: The permitted answers for the safety question. The keys ``true``/``false``
#: are the contract, not a description pair.
CRITERIA_SAFE_TO_EXECUTE = {
    "true": "Safe to execute under current policy.",
    "false": "Requires confirmation or must not execute.",
}


def build_executor_questions(available_executors: Sequence[str]) -> Dict[str, Any]:
    """The three executor questions, built from the shared builders.

    ``executor_choice`` is a ``choice`` whose criteria keys ARE the executors,
    ``safe_to_execute`` a ``noul`` answering true/false, and ``risk_level`` a
    ``score`` over an ordered list — one of each type, which is also why the
    parser is exercised against all three on every call.

    Every question goes through :func:`build_choice_question` /
    :func:`build_score_question` / :func:`build_noul_question`, so the readiness
    probe and the router cannot drift apart again — which they did, and which is
    why one was fixed against the live service while the other still 422'd.
    """
    options = [e for e in available_executors if e in KNOWN_EXECUTORS]
    if not options:
        raise ValueError(
            f"no usable executor; available={list(available_executors)!r} "
            f"known={list(KNOWN_EXECUTORS)!r}"
        )
    return {
        Q_EXECUTOR_CHOICE: build_choice_question(
            "Which executor should run this task?",
            _executor_criteria(options),
        ),
        Q_SAFE_TO_EXECUTE: build_noul_question(
            "Is it safe to execute this task now?",
            CRITERIA_SAFE_TO_EXECUTE,
        ),
        Q_RISK_LEVEL: build_score_question(
            "How risky is executing this task?",
            SCORE_LEVELS_RISK,
        ),
    }


def build_executor_state(
    *,
    task: str,
    project_root: str,
    task_type: str,
    risk_context: Optional[Mapping[str, Any]] = None,
    available_executors: Sequence[str] = KNOWN_EXECUTORS,
    executor_health: Optional[Mapping[str, Any]] = None,
    capabilities: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble the ``state`` object from the four local facts Jev cannot see.

    Executor health and capabilities are included because a choice among four
    names is only meaningful alongside "which of them can actually run right
    now" — without it, Jev is picking blind and the operator cannot tell.
    """
    return {
        "task": task,
        "project_root": project_root,
        "task_type": task_type,
        "risk_context": dict(risk_context or {}),
        "available_executors": [
            e for e in available_executors if e in KNOWN_EXECUTORS
        ],
        "executor_health": dict(executor_health or {}),
        "capabilities": dict(capabilities or {}),
    }


def decide_executor(
    provider: JevProvider,
    *,
    task: str,
    project_root: str,
    task_type: str,
    available_executors: Sequence[str] = KNOWN_EXECUTORS,
    risk_context: Optional[Mapping[str, Any]] = None,
    executor_health: Optional[Mapping[str, Any]] = None,
    capabilities: Optional[Mapping[str, Any]] = None,
    fallback_executor: Optional[str] = None,
    require_confirmation_below: float = LOW_CONFIDENCE_THRESHOLD,
) -> ExecutorDecision:
    """Ask Jev which executor to run, and decide what to do with the answer.

    THE THRESHOLD IS NOT "TOP CHOICE WINS"
    -------------------------------------
    A decision is auto-accepted only when ALL of these hold:
      * the reply parsed against the schema,
      * the chosen executor is one we offered,
      * ``safe_to_execute`` is present and True,
      * ``confidence >= require_confirmation_below``.

    Failing any of them yields ``needs_confirmation=True`` — or a ``fallback``
    result carrying ``source="fallback"`` when the caller configured one. What it
    never yields is a confident-looking pick that Jev did not make.

    WHEN JEV IS UNAVAILABLE
    -----------------------
    No credential, a 4xx/5xx, a timeout, or a malformed body all produce a
    ``fallback`` decision whose ``source`` says so. ``fallback_executor`` is
    honoured ONLY because the caller passed it explicitly; with no fallback
    configured the function returns a decision with ``executor=""`` and
    ``needs_confirmation=True``, which the UI must render as "Jev did not decide"
    rather than as any particular executor.
    """
    options = [e for e in available_executors if e in KNOWN_EXECUTORS]
    state = build_executor_state(
        task=task,
        project_root=project_root,
        task_type=task_type,
        risk_context=risk_context,
        available_executors=options,
        executor_health=executor_health,
        capabilities=capabilities,
    )
    questions = build_executor_questions(options)

    def fallback(reason: str) -> ExecutorDecision:
        if fallback_executor and fallback_executor in KNOWN_EXECUTORS:
            return ExecutorDecision(
                executor=fallback_executor,
                source="fallback",
                confidence=0.0,
                needs_confirmation=True,
                reason=reason,
                provider_state=provider_state(
                    credential_present=provider.has_credential
                ),
            )
        return ExecutorDecision(
            executor="",
            source="undecided",
            confidence=0.0,
            needs_confirmation=True,
            reason=reason,
            provider_state=provider_state(
                credential_present=provider.has_credential
            ),
        )

    if not provider.has_credential:
        # Name the provider's STATE, not an environment variable.
        #
        # The message used to read "JEV_API_KEY is not set", which sent the
        # operator to the environment when the real cause was elsewhere: the
        # key had been saved through the settings page and was in the DPAPI
        # store, and this path simply was not looking there. A credential can
        # arrive from a store or a bootstrap import, so an unset variable is not
        # a diagnosis. The provider's own state is.
        return fallback(
            "TypeSafe Jev has no credential (REAL_AUTH_MISSING); the vendor model did not decide"
        )

    try:
        response = provider.decide(state, questions)
    except JevAuthError as e:
        return fallback(f"Jev rejected the credential: {e}")
    except JevRateLimitError as e:
        return fallback(f"Jev rate-limited the request: {e}")
    except (JevUnavailableError, JevApiError, SystemOneProtocolError) as e:
        return fallback(f"Jev did not return a usable decision: {e}")

    choice = response.choice(Q_EXECUTOR_CHOICE)
    safe = response.noul(Q_SAFE_TO_EXECUTE)
    risk = response.score(Q_RISK_LEVEL)

    if choice is None:
        return fallback(
            f"Jev reply had no usable '{Q_EXECUTOR_CHOICE}' answer"
        )
    if choice.choice not in options:
        return fallback(
            f"Jev chose {choice.choice!r}, which was not offered {options!r}"
        )

    # A noul we did not receive is not a "no". Treating absence as permission
    # would let a partial reply authorise execution.
    if safe is None:
        return ExecutorDecision(
            executor=choice.choice,
            source="jev",
            confidence=choice.confidence,
            safe_to_execute=None,
            risk_score=risk.score if risk else None,
            needs_confirmation=True,
            reason="Jev did not answer safe_to_execute; execution needs a human",
            request_id=response.request_id,
            latency_ms=response.latency_ms,
            provider_state=STATE_REAL_READY,
        )

    if not safe:
        return ExecutorDecision(
            executor=choice.choice,
            source="jev",
            confidence=choice.confidence,
            safe_to_execute=False,
            risk_score=risk.score if risk else None,
            needs_confirmation=True,
            reason="Jev judged it unsafe to execute right now",
            request_id=response.request_id,
            latency_ms=response.latency_ms,
            provider_state=STATE_REAL_READY,
        )

    if choice.confidence < require_confirmation_below:
        return ExecutorDecision(
            executor=choice.choice,
            source="jev",
            confidence=choice.confidence,
            safe_to_execute=True,
            risk_score=risk.score if risk else None,
            needs_confirmation=True,
            reason=(
                f"confidence {choice.confidence:.2f} is below "
                f"{require_confirmation_below:.2f}; a human must confirm"
            ),
            request_id=response.request_id,
            latency_ms=response.latency_ms,
            provider_state=STATE_REAL_READY,
        )

    return ExecutorDecision(
        executor=choice.choice,
        source="jev",
        confidence=choice.confidence,
        safe_to_execute=True,
        risk_score=risk.score if risk else None,
        needs_confirmation=False,
        reason=None,
        request_id=response.request_id,
        latency_ms=response.latency_ms,
        provider_state=STATE_REAL_READY,
    )
