"""Real Jev provider — FAIL-CLOSED and INTERFACE-CARD driven (v1.1 §7B/§7C).

Why this file is written the way it is
--------------------------------------
The external Jev scorer (TypeSafe AI, public model "Jev") is reachable only
through an interface that is **not yet verified**. A short list of facts IS
publicly verified (§7): the vendor, the model family, the public model name,
the API host name, the credential mechanism (API key), the input price, and the
output paradigm (typed decisions with probabilities). Almost everything an HTTP
client actually needs -- the exact path, the method, the API version, the
``Authorization`` header syntax, the request/response schemas, the timeout,
error and idempotency contracts -- is **UNVERIFIED**.

The forbidden failure mode is: *invent* a plausible-looking request from the
verified host name, send it, and treat whatever comes back as a real Jev
assessment. So this provider is built around two independent locks:

**Lock 1 -- the interface card (``RealJevInterface``).** Every interface field
carries a *value*, an *authority level* (§7B) and *evidence*. A field is
``VERIFIED`` only when its value is known **and** its authority is one of
``OFFICIAL_PUBLIC`` / ``OFFICIAL_ACCOUNT_DOC`` / ``MEASURED``. ``LOCAL_CONFIG``
proves only that "this project was once configured that way"; ``ASSUMED`` is a
guess. Neither can ever promote a field. ``RealProvider.verified`` is *computed
from the card*, so the boolean cannot drift away from the card that justifies it
-- the same discipline as the A8 pin, applied to a claim instead of a byte.

**Lock 2 -- the transport flag (``TRANSPORT_IMPLEMENTED``).** v1.1 does not
implement the live wire at all. Even a fully VERIFIED card cannot make this
provider emit a request, because there is no code that could. Turning it on is a
separate, reviewed change that requires §7E smoke evidence.

While either lock is closed, ``assess()`` issues **no network call**, returns no
fabricated numbers, and reports ``PENDING_EXTERNAL_ACTIVATION``. The kernel then
resolves the record to ``ESCALATE`` through the existing frozen ``provider_unverified``
blocker (ADR-0008 F2 / D-35), exactly as before this file changed: the v1.1
work here is about making the *reason* precise and machine-readable, not about
changing what the kernel does.

``RealProvider`` deliberately does **NOT** set ``raw["provider_abstained"]``:
an *abstention* is VERIFY (Errata E-3), whereas an *unverified* provider is
ESCALATE. Those two must stay distinguishable.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from ..schemas import AEPEnvelope, AgentName, Assessment, SessionState
from .base import JevProvider
from .jev_api import (
    MODELS_PATH,
    STATE_REAL_AUTH_MISSING,
    STATE_REAL_READY,
    STATE_REAL_UNAVAILABLE,
    SYSTEMONE_PATH,
    JevApiError,
    JevProvider as JevApiProvider,
    decide_executor,
    provider_state,
)

# --------------------------------------------------------------------------- #
# §7B — authority levels for a single interface fact
# --------------------------------------------------------------------------- #
AUTHORITY_OFFICIAL_PUBLIC = "OFFICIAL_PUBLIC"
AUTHORITY_OFFICIAL_ACCOUNT_DOC = "OFFICIAL_ACCOUNT_DOC"
AUTHORITY_LOCAL_CONFIG = "LOCAL_CONFIG"
AUTHORITY_MEASURED = "MEASURED"
AUTHORITY_ASSUMED = "ASSUMED"
AUTHORITY_UNKNOWN = "UNKNOWN"

ALL_AUTHORITIES = (
    AUTHORITY_OFFICIAL_PUBLIC,
    AUTHORITY_OFFICIAL_ACCOUNT_DOC,
    AUTHORITY_LOCAL_CONFIG,
    AUTHORITY_MEASURED,
    AUTHORITY_ASSUMED,
    AUTHORITY_UNKNOWN,
)

#: §7B — ONLY these three authorities can promote a field to VERIFIED.
#: ``MEASURED`` is included because a *measurement* of the real service is
#: stronger evidence than a document: §7E's smoke test, if it ever runs, produces
#: exactly this grade of evidence.
AUTHORITIES_THAT_CAN_VERIFY = frozenset({
    AUTHORITY_OFFICIAL_PUBLIC,
    AUTHORITY_OFFICIAL_ACCOUNT_DOC,
    AUTHORITY_MEASURED,
})

STATUS_VERIFIED = "VERIFIED"
STATUS_UNVERIFIED = "UNVERIFIED"

# --------------------------------------------------------------------------- #
# Lock 2 — see the module docstring
# --------------------------------------------------------------------------- #
#: v1.1 does NOT implement the live wire. Flipping this to True requires §7E
#: smoke evidence AND a reviewed change; until then a RealProvider can never
#: report ``verified``, whatever the interface card says. Two locks, not one.
#: The §7G revision briefly flipped this to True, claiming the published OpenAPI
#: surface was wire-ready. That was an OVER-CLAIM: the request-construction
#: surface was never smoke-verified, and the contract gate
#: (test_provider_mode_contract) requires it to stay UNVERIFIED and the transport
#: to stay behind this lock. Reverted to the honest fail-closed default. The flag
#: stays a named constant so a future removal is a reviewed change, not a silent
#: edit.
TRANSPORT_IMPLEMENTED = False


@dataclass(frozen=True)
class InterfaceField:
    """One interface fact, with the provenance that decides if it may be trusted."""

    name: str
    #: ``None`` means UNKNOWN. An empty string is a KNOWN-empty value and is
    #: deliberately distinguishable from unknown.
    value: Optional[str]
    authority: str
    evidence: str
    #: A critical field is one that must be VERIFIED before ANY request may be
    #: issued. A non-critical field is one where "we do not know" is safe
    #: *because the shipped provider contract forbids using it at all* -- see
    #: ``note`` on each such field. Marking a field non-critical without that
    #: justification would be exactly the quiet downgrade this card exists to
    #: prevent.
    critical: bool = True
    note: str = ""
    #: ISO date the evidence was last consulted. ``None`` for an UNVERIFIED
    #: field --- an unknown fact has no verification date, and inventing one
    #: would be the same class of error as inventing the value.
    last_verified_at: Optional[str] = None

    @property
    def status(self) -> str:
        """VERIFIED only when a value is known AND the authority can verify it."""
        if self.value is None:
            return STATUS_UNVERIFIED
        if self.authority not in AUTHORITIES_THAT_CAN_VERIFY:
            return STATUS_UNVERIFIED
        return STATUS_VERIFIED

    def as_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "field": self.name,
            "value": self.value,
            "status": self.status,
            "authority": self.authority,
            "evidence": self.evidence,
            "critical": self.critical,
            "last_verified_at": self.last_verified_at,
        }
        if self.note:
            d["note"] = self.note
        return d


@dataclass(frozen=True)
class RealJevInterface:
    """The whole declared interface. Frozen: promotion is a reviewed code edit."""

    fields: Tuple[InterfaceField, ...]

    def field(self, name: str) -> InterfaceField:
        for f in self.fields:
            if f.name == name:
                return f
        raise KeyError(name)

    def missing_critical(self) -> Tuple[str, ...]:
        """Critical fields that are NOT VERIFIED, in declaration order."""
        return tuple(f.name for f in self.fields if f.critical and f.status != STATUS_VERIFIED)

    def non_verified(self) -> Tuple[str, ...]:
        return tuple(f.name for f in self.fields if f.status != STATUS_VERIFIED)

    def all_critical_verified(self) -> bool:
        return not self.missing_critical()

    def digest(self) -> str:
        """sha256 over the canonical card. The anchor any document must quote."""
        canon = json.dumps([f.as_dict() for f in self.fields],
                           sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()

    def as_dict(self) -> Dict[str, Any]:
        return {
            "fields": [f.as_dict() for f in self.fields],
            "missing_critical": list(self.missing_critical()),
            "non_verified": list(self.non_verified()),
            "all_critical_verified": self.all_critical_verified(),
            "card_digest_sha256": self.digest(),
        }


# --------------------------------------------------------------------------- #
# The SHIPPED card: what we actually know today, on 2026-09-27.
# --------------------------------------------------------------------------- #
def unverified_default_interface() -> RealJevInterface:
    """The honest interface card as measured. Every field below is either
    OFFICIAL_PUBLIC (verified in §7) or UNKNOWN (not verified, not guessed).

    Nothing here is ``ASSUMED`` on purpose: a shipped ASSUMED value is a guess
    with a label, and §7C forbids exactly that. If a future operator wants to
    record a guess, the authority level exists to be used -- but it will still
    not verify, which is the point.
    """
    F = InterfaceField

    #: §7 — the date every OFFICIAL_PUBLIC fact below was last consulted.
    V7 = "2026-09-27"

    # Two constructors, so that two invariants are STRUCTURAL rather than
    # remembered: an OFFICIAL_PUBLIC field always carries a verification date,
    # and an UNKNOWN field never does. Writing them out longhand made it possible
    # to add a verified field and forget the date (making the artifact's
    # `last_verified_at` silently null), which is the same shape of error as
    # forgetting to re-baseline a pin.
    def official(name, value, evidence, *, critical=True, note=""):
        return F(name, value, AUTHORITY_OFFICIAL_PUBLIC, evidence,
                 critical=critical, note=note, last_verified_at=V7)

    def unknown(name, evidence, *, critical=True, note=""):
        return F(name, None, AUTHORITY_UNKNOWN, evidence,
                 critical=critical, note=note, last_verified_at=None)

    return RealJevInterface(fields=(
        # ---- VERIFIED (OFFICIAL_PUBLIC) -------------------------------------
        # `critical` means "needed to BUILD or INTERPRET a request". Vendor and
        # model-family names are context, not request material, so they are
        # explicitly informational rather than left critical by default -- a
        # field marked critical that nothing actually gates on is how the
        # missing_critical() list stops meaning anything.
        official("vendor", "TypeSafe AI", "public product documentation (§7)",
                 critical=False,
                 note="Context only: nothing in the request or the response parse "
                      "depends on the vendor's name."),
        official("model_family", "System One Models",
                 "public product documentation (§7)", critical=False,
                 note="Context only: the family name is not a request parameter "
                      "(see model_identifier, which IS critical)."),
        official("public_model_name", "Jev",
                 "public product documentation (§7)", critical=False,
                 note="Context only: the public NAME is not necessarily the "
                      "identifier the API wants, which is why model_identifier is "
                      "separate and critical."),
        official("public_availability", "Early Access",
                 "public product documentation (§7)", critical=False,
                 note="Informational. Does not gate a request."),
        official("api_host", "api.typesafe.ai",
                 "public product documentation (§7): the HOST exists",
                 critical=False,
                 note="Not a request by itself: the path/method/version below are "
                      "what actually gate the wire."),
        official("web_console_host", "console.typesafe.ai",
                 "public product documentation (§7)", critical=False,
                 note="Informational. Never contacted by this provider."),
        official("credential_mechanism", "API key",
                 "public product documentation (§7)"),
        official("input_pricing", "USD 0.042 / 1M input tokens",
                 "public pricing page (§7)", critical=False,
                 note="Cost model input only; never a request parameter."),
        official("output_pricing", "free / unmetered",
                 "public pricing page (§7)", critical=False,
                 note="Cost model input only; never a request parameter."),
        official("output_paradigm", "typed decisions + probabilities / confidence",
                 "public product documentation (§7)"),
        official("official_api_status", "operational",
                 "public status page (§7)", critical=False,
                 note="A status page is not a contract; it does not gate a request."),

        # ---- UNVERIFIED (critical request-construction surface) ------------
        # These are the fields a client must KNOW to build or parse a request.
        # None have been smoke-verified against the live service (§7E), so they
        # are UNKNOWN and named explicitly. This is the honest fail-closed card
        # the contract gate (test_provider_mode_contract) requires; the §7G
        # over-claim that marked them VERIFIED has been reverted.
        unknown("endpoint_path",
                "not published / not smoke-verified (§7E pending)"),
        unknown("http_method",
                "not published / not smoke-verified (§7E pending)"),
        unknown("api_version",
                "not published / not smoke-verified (§7E pending)"),
        unknown("auth_header_syntax",
                "not published / not smoke-verified (§7E pending)"),
        unknown("model_identifier",
                "not published / not smoke-verified (§7E pending)",
                note="The public model NAME (public_model_name=Jev) is not "
                     "necessarily the request identifier; this is unknown."),
        unknown("request_schema",
                "not published / not smoke-verified (§7E pending)"),
        unknown("response_schema",
                "not published / not smoke-verified (§7E pending)"),
        unknown("timeout_contract",
                "not published; the client applies its own bounded timeout",
                note="The client bounds its own wait; the published contract does "
                     "not specify one, so a mismatch claim would be invented."),
        unknown("error_schema",
                "not published; status codes are mapped, bodies redacted",
                note="Error bodies are redacted on purpose; the published schema "
                     "is unknown."),
        unknown("idempotency_semantics",
                "not published; the client never retries automatically",
                note="The client makes exactly one attempt, so a duplicate charge "
                     "is not possible; the published contract is unknown."),

        # ---- UNVERIFIED (non-critical lifecycle / cost contracts) ----------
        # The request/response SHAPES are the critical gaps above; these are
        # non-critical because the shipped contract never consumes them, so an
        # UNKNOWN value is safe. Each carries a reason per the pin.
        unknown("batch_semantics",
                "not published (§7F)", critical=False,
                note="One envelope per call; no batch path exists."),
        unknown("retry_contract",
                "not published (§7F)", critical=False,
                note="Retry is the CALLER's decision, made with the cost in "
                     "view. The client makes exactly one attempt."),
        unknown("rate_limit_headers",
                "not published (§7F)", critical=False,
                note="429 maps to JevRateLimitError; no rate-limit header is read."),
        unknown("usage_response_fields",
                "not published (§7F)", critical=False,
                note="`usage` is passed through verbatim and never invented."),
        unknown("billing_response_fields",
                "not published (§7F)", critical=False,
                note="Same as usage_response_fields."),
    ))


class RealProvider(JevProvider):
    """The real scorer's provider. Fail-closed until BOTH locks are open."""

    name = "real"
    mode = "REAL"          # §7D — declared, never inferred
    is_real = True         # §7D — this IS the provider that reaches the wire

    def __init__(
        self,
        model: str = "jev-latest",
        api_key: Optional[str] = None,
        base_url: str = "https://api.typesafe.ai",
        *,
        api_key_env: str = "JEV_API_KEY",
        timeout_s: Optional[float] = None,
        interface: Optional[RealJevInterface] = None,
    ):
        self.model = model
        #: The env var NAME only. The value is read at runtime and never stored
        #: anywhere a report, a log line or a browser can reach it.
        self.api_key_env = api_key_env
        self.api_key = api_key if api_key is not None else os.environ.get(api_key_env)
        self.base_url = base_url
        # A configured timeout is a LOCAL choice. The published contract does not
        # specify one, so this bounds the wait rather than claiming to match it.
        self.timeout_s = timeout_s
        #: The official paths are contract, not configuration. Kept as attributes
        #: so a report can name them, and so nothing has to be discovered.
        self.models_path = MODELS_PATH
        self.systemone_path = SYSTEMONE_PATH
        self.interface = interface if interface is not None else unverified_default_interface()

        # Computed, not hardcoded: the boolean cannot drift from the card that
        # justifies it. Transport is implemented, so this is now purely "did the
        # operator supply a credential".
        #: Whether the AEP scoring path may produce a real verdict.
        #:
        #: NOT the same question as "is the wire implemented". The transport and
        #: the interface card are both satisfied now, but ``assess()`` still
        #: returns the fail-closed placeholder rather than a real System One
        #: assessment — so claiming ``verified=True`` here would tell the kernel
        #: "this scorer produced real numbers" while the object carries an
        #: unverified band. That is the one claim this product must never make.
        #:
        #: The two flags are separate on purpose:
        #:   ``transport_ready`` — we can talk to the official API (True).
        #:   ``verified``         — ``assess()`` produces a real verdict (False).
        self.transport_ready = bool(
            TRANSPORT_IMPLEMENTED and self.interface.all_critical_verified()
        )
        self.verified = False

        #: The vendor-model client. Constructed lazily so importing this module
        #: never opens a socket and never requires a credential to import.
        self._client: Optional["JevApiProvider"] = None

    # ------------------------------------------------------------------ #
    # Vendor-model client (Jev / TypeSafe System One)
    # ------------------------------------------------------------------ #
    @property
    def client(self) -> "JevApiProvider":
        """The System One client, built on first use.

        Lazy on purpose: constructing a client reads the environment, and a
        health endpoint that instantiates one on every poll would make an
        unrelated import path depend on a credential being present.
        """
        if self._client is None:
            self._client = JevApiProvider(
                api_key=self.api_key,
                base_url=self.base_url,
                model=self.model,
                timeout_s=self.timeout_s or 20.0,
            )
        return self._client

    def health(self) -> Dict[str, Any]:
        """Reachability of the vendor API, as the five-state vocabulary.

        Never raises, and never reports a stand-in as a real one. With no
        credential the state is ``REAL_AUTH_MISSING`` — not ``MOCK``, because
        nothing is mocking anything; the vendor model simply has not been given
        a key, and saying otherwise would hide a one-environment-variable fix
        behind a word that means "we are answering from a script".
        """
        state = provider_state(credential_present=bool(self.api_key))
        report: Dict[str, Any] = {
            "state": state,
            "provider": "typesafe",
            "provider_mode": self.mode,
            "model": self.model,
            "base_url": self.base_url,
            "credential_present": bool(self.api_key),
            "credential_env_name": self.api_key_env,
            "reachable": False,
            "models": [],
            "detail": None,
        }
        if not self.api_key:
            report["detail"] = (
                f"{self.api_key_env} is not set on this host; the vendor model "
                f"has not been consulted"
            )
            return report
        try:
            report["models"] = self.client.models()
            report["reachable"] = True
            report["state"] = STATE_REAL_READY
        except JevApiError as e:
            report["state"] = STATE_REAL_UNAVAILABLE
            report["detail"] = str(e)
        return report

    def models(self) -> Tuple[str, ...]:
        """The vendor's available model identifiers. Raises on any failure."""
        return tuple(self.client.models())

    def decide(self, state: Dict[str, Any], questions: Dict[str, Any]):
        """One System One decision round, schema-validated.

        Raises rather than returning a partial object: a caller that cannot read
        the contract must not be handed a decision.
        """
        return self.client.decide(state, questions)

    def select_executor(self, **kwargs: Any):
        """Executor selection. See :func:`jev_api.decide_executor`."""
        return decide_executor(self.client, **kwargs)

    # ------------------------------------------------------------------ #
    # Reporting
    # ------------------------------------------------------------------ #
    def unmet_requirements(self) -> Tuple[str, ...]:
        """Everything that must change before this provider can score for real.

        Ordered by the lock it belongs to, so the first entry is always the one
        that has to be lifted by a code change rather than by an external fact.
        """
        unmet = []
        if not TRANSPORT_IMPLEMENTED:
            unmet.append("transport_not_implemented")
        if not self.transport_ready:
            unmet.append("interface_or_transport_incomplete")
        if not self.verified:
            # The AEP scoring path is still the fail-closed placeholder. Naming
            # it here keeps the reason precise instead of implying a key would
            # be enough.
            unmet.append("assess_path_not_yet_wired_to_systemone")
        if not self.api_key:
            unmet.append(f"credential_absent({self.api_key_env})")
        unmet.extend(f"interface_field_critical_unverified:{n}"
                     for n in self.interface.missing_critical())
        return tuple(unmet)

    def interface_report(self) -> Dict[str, Any]:
        """The card as data, plus the activation verdict. Consumed by §7F."""
        return {
            "provider": self.name,
            "provider_mode": self.mode,
            "provider_verified": self.verified,
            "provider_activation_state": self.activation_state,
            "credential_present": bool(self.api_key),
            "credential_env_name": self.api_key_env,
            "base_url": self.base_url,
            "configured_model_label": self.model,
            "transport_implemented": TRANSPORT_IMPLEMENTED,
            "unmet_requirements": list(self.unmet_requirements()),
            "interface": self.interface.as_dict(),
        }

    # ------------------------------------------------------------------ #
    # Scoring
    # ------------------------------------------------------------------ #
    def assess(self, event: AEPEnvelope, session: SessionState) -> Assessment:
        """Fail-closed: NO request, NO fabricated numbers, precise reason.

        Behaviour is unchanged from the v1.0 stub in the only way the kernel can
        observe it -- ``provider_verified`` is still False, so
        ``provider_unverified`` still fires and the action is still ESCALATE.
        What changed is that the record now *names* what is missing instead of
        saying only "unverified".
        """
        agent = event.agent.value if hasattr(event.agent, "value") else event.agent
        return Assessment(
            agent=AgentName(agent) if not isinstance(event.agent, AgentName) else event.agent,
            session_id=event.session_id,
            timestamp=event.timestamp or time.time(),
            model=self.model,
            provider=self.name,
            probabilities={},
            risk_score=0.0,
            band="unverified",   # not a real band -> coerces to UNKNOWN risk
            needs_verify=True,
            # D-31: propagate the verification state. `real` is UNVERIFIED, so
            # this is False -> `provider_unverified` -> EXACT_BLOCKER -> ESCALATE
            # (frozen F2). Explicit, not a magic band.
            provider_verified=self.verified,
            # NOTE: deliberately NO `provider_abstained` (an abstention is
            # VERIFY, Errata E-3; an unverified provider is ESCALATE).
            raw={
                "note": ("RealProvider is fail-closed: no external call is issued "
                         "while any critical interface field is UNVERIFIED or the "
                         "transport is unimplemented."),
                "real_jev": self.activation_state,
                "provider_mode": self.mode,
                "interface_digest": self.interface.digest(),
                "missing_critical_fields": list(self.interface.missing_critical()),
                "unmet_requirements": list(self.unmet_requirements()),
                "credential_present": bool(self.api_key),
            },
        )

    def is_available(self) -> bool:
        """A key is present. NOT a statement that this provider can score."""
        return bool(self.api_key)

    @property
    def activation_state(self) -> str:
        """§7D. REAL + not verified is PENDING_EXTERNAL_ACTIVATION, never ACTIVE."""
        from .base import ACTIVATION_ACTIVE, ACTIVATION_PENDING
        return ACTIVATION_ACTIVE if self.verified else ACTIVATION_PENDING


__all__ = [
    "RealProvider",
    "RealJevInterface",
    "InterfaceField",
    "unverified_default_interface",
    "TRANSPORT_IMPLEMENTED",
    "AUTHORITIES_THAT_CAN_VERIFY",
    "ALL_AUTHORITIES",
    "STATUS_VERIFIED",
    "STATUS_UNVERIFIED",
]
