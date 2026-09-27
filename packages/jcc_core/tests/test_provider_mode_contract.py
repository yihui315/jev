"""v1.1 §7B / §7C / §7D — the provider MODE and ACTIVATION contract.

This file exists because the four fields it pins are DECLARATIONS, and a
declaration is exactly the kind of thing a small, plausible edit can change
while every value-level test still passes. The specific failure this file is
built to make impossible:

    a stand-in provider (mock / replay / disabled) renders as REAL

Nothing in the P1-P3 suite can catch that: the mock's numbers are valid, the
record's ``provider`` string is valid, and the kernel still resolves actions
correctly. It only becomes visible if something asserts, positively, that the
mode is declared and that ``is_real`` is the single bit a consumer may trust.

Three independent angles, on purpose:

  1. **Declared, not inferred.** A provider that forgets ``mode`` must NOT be
     guessable from its ``name``. Measured with a subclass that lies in ``name``.
  2. **Measured on the wire.** ``/api/health`` is asserted as the actual JSON
     body, for all four reachable configurations, including the fail-closed
     fallback -- which is the case that most easily reads as a deliberate choice.
  3. **The real provider cannot fake readiness.** Its ``verified`` is computed
     from an interface card plus a transport lock, so it is asserted to stay
     False by construction AND asserted to issue no network call at all.
"""
from __future__ import annotations

import json
import socket

import pytest
from fastapi.testclient import TestClient

import jcc_core.providers.base as base_mod
from jcc_core.actuator import Actuator
from jcc_core.config import Config, JevConfig, ServerConfig
from jcc_core.core import CoreEngine
from jcc_core.providers import build_provider
from jcc_core.providers.base import (
    PROVIDER_MODES,
    JevProvider,
    activation_state_for_requested,
)
from jcc_core.providers.disabled import DisabledProvider
from jcc_core.providers.mock import DeterministicMockProvider
from jcc_core.providers.real import (
    AUTHORITIES_THAT_CAN_VERIFY,
    AUTHORITY_ASSUMED,
    AUTHORITY_LOCAL_CONFIG,
    AUTHORITY_MEASURED,
    AUTHORITY_OFFICIAL_ACCOUNT_DOC,
    AUTHORITY_OFFICIAL_PUBLIC,
    AUTHORITY_UNKNOWN,
    STATUS_UNVERIFIED,
    STATUS_VERIFIED,
    TRANSPORT_IMPLEMENTED,
    InterfaceField,
    RealJevInterface,
    RealProvider,
    unverified_default_interface,
)
from jcc_core.providers.record_replay import RecordReplayProvider
from jcc_core.schemas import AEPEnvelope
from jcc_core.server import create_app
from jcc_core.state_store import StateStore

TOKEN = "provider-mode-test-token"

SHIPPED_PROVIDER_CLASSES = (
    DeterministicMockProvider,
    DisabledProvider,
    RealProvider,
    RecordReplayProvider,
)


def _env(**kw):
    base = dict(event_id="e1", delivery_id="d1", session_id="s1", agent="workbuddy")
    base.update(kw)
    return AEPEnvelope(**base)


def _client(tmp_path, monkeypatch, jev: JevConfig):
    monkeypatch.setenv("JCC_CONTROL_TOKEN", TOKEN)
    cfg = Config(
        server=ServerConfig(),
        state_store_db_path=str(tmp_path / "provmode.db"),
        jev=jev,
    )
    store = StateStore(cfg.state_store_db_path)
    engine = CoreEngine(store, build_provider(cfg), Actuator(enabled=False), cfg)
    return TestClient(create_app(engine, cfg)), engine


# --------------------------------------------------------------------------- #
# 1. Declared, never inferred
# --------------------------------------------------------------------------- #
def test_every_shipped_provider_class_declares_a_legal_mode():
    """No shipped provider may leave its mode at the base default.

    The base default is deliberately NOT a legal mode, so this test is the thing
    that turns "someone added a provider and forgot the contract" into a red
    build rather than a silent UNKNOWN badge in production.
    """
    for cls in SHIPPED_PROVIDER_CLASSES:
        assert cls.mode in PROVIDER_MODES, (
            f"{cls.__name__}.mode == {cls.mode!r}, which is not one of "
            f"{PROVIDER_MODES}. Declare it explicitly -- see §7D."
        )


def test_base_mode_default_is_not_a_legal_mode():
    """The fail-closed default must be OBSERVABLY undeclared."""
    assert base_mod.JevProvider.mode == "UNKNOWN"
    assert base_mod.JevProvider.mode not in PROVIDER_MODES


def test_mode_is_declared_never_inferred_from_name():
    """A provider named ``real`` that never declared a mode is still UNKNOWN.

    This is the falsifier for the whole contract: if ``mode`` were derived from
    ``name`` (uppercased, say), the mock could be relabelled REAL by a rename and
    every other assertion in this file would still pass.
    """

    class Impostor(JevProvider):
        name = "real"          # the lie
        verified = True        # ...told twice

        def assess(self, event, session):  # pragma: no cover - never called
            raise AssertionError("not used")

    impostor = Impostor()
    assert impostor.mode == "UNKNOWN"
    assert impostor.mode != "REAL"
    assert impostor.activation_state == base_mod.ACTIVATION_UNAVAILABLE
    assert not impostor.is_real


# --------------------------------------------------------------------------- #
# 2. Measured on the wire — /api/health, all four reachable configurations
# --------------------------------------------------------------------------- #
REQUIRED_HEALTH_FIELDS = (
    "jev_provider",
    "provider_mode",
    "provider_verified",
    "provider_activation_state",
)


def test_health_carries_the_four_required_provider_fields(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch, JevConfig(provider="mock"))
    body = client.get("/api/health").json()
    for field in REQUIRED_HEALTH_FIELDS:
        assert field in body, f"/api/health lost the required field {field!r} (§7D)"


def test_mock_configuration_reads_as_mock_and_active(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch, JevConfig(provider="mock"))
    b = client.get("/api/health").json()
    assert b["provider_mode"] == "MOCK"
    assert b["provider_verified"] is True
    assert b["provider_activation_state"] == "ACTIVE"
    assert b["provider_is_real"] is False
    assert b["provider_requested_mode"] == "MOCK"


def test_real_requested_with_a_credential_reads_as_real_pending(tmp_path, monkeypatch):
    """The §7D example, verbatim: REAL / verified=false / PENDING_EXTERNAL_ACTIVATION."""
    monkeypatch.setenv("JEV_API_KEY", "dummy-not-a-real-key")
    client, engine = _client(
        tmp_path, monkeypatch,
        JevConfig(provider="real", feature_flags={"real_provider": True}),
    )
    assert engine.provider.name == "real", "precondition: the real provider is live"
    b = client.get("/api/health").json()
    assert b["jev_provider"] == "real"
    assert b["provider_mode"] == "REAL"
    assert b["provider_verified"] is False
    assert b["provider_activation_state"] == "PENDING_EXTERNAL_ACTIVATION"
    assert b["provider_is_real"] is True
    monkeypatch.delenv("JEV_API_KEY", raising=False)


def test_real_requested_without_a_credential_is_pending_not_plainly_disabled(
    tmp_path, monkeypatch
):
    """The fallback must not render as a deliberate choice.

    ``build_provider`` fail-closes to ``DisabledProvider`` here, whose own state
    is correctly DISABLED. Reporting ONLY that would tell an operator "real Jev
    is switched off on purpose", when the truth is "real Jev was requested and
    is blocked on a credential". Same bytes on the wire, opposite response.
    """
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    client, engine = _client(
        tmp_path, monkeypatch,
        JevConfig(provider="real", feature_flags={"real_provider": True}),
    )
    assert engine.provider.name == "disabled", "precondition: fail-closed fallback"
    b = client.get("/api/health").json()
    assert b["provider_mode"] == "DISABLED"          # what is actually scoring
    assert b["provider_requested_mode"] == "REAL"    # what was asked for
    assert b["provider_activation_state"] == "PENDING_EXTERNAL_ACTIVATION"
    assert b["provider_is_real"] is False


def test_disabled_requested_reads_as_disabled(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch, JevConfig(provider="disabled"))
    b = client.get("/api/health").json()
    assert b["provider_mode"] == "DISABLED"
    assert b["provider_activation_state"] == "DISABLED"
    assert b["provider_requested_mode"] == "DISABLED"


def test_replay_never_claims_to_be_real(tmp_path, monkeypatch):
    """A recording that NAMES ``real`` must still surface as REPLAY.

    The recording deliberately stores ``provider: "real"`` inside the assessment,
    because that is the impersonation this test exists to catch: the recorded
    bytes say real, and the live surface must not agree.
    """
    from jcc_core.providers.record_replay import build_entry, write_recording

    recorded = _env(evidence={"ok": True})
    recording = tmp_path / "rec.jsonl"
    write_recording(recording, [build_entry(
        envelope=recorded,
        assessment={
            "agent": "workbuddy", "session_id": "s1", "timestamp": 1700000000.0,
            "model": "jev-1.13.0", "provider": "real",   # <-- the lie
            "probabilities": {"risk": 0.1}, "risk_score": 0.1, "band": "low",
            "needs_verify": False, "raw": {"source": "test"},
            "confidence": None, "confidence_kind": "none",
            "provider_verified": True, "blocker_kind": "NONE",
            "blocker_reasons": [],
        },
        session=None,
        recording_id="rec-impostor",
        created_at="2026-09-27T00:00:00Z",
        provider_source="fixture",
        captured_by="test",
    )])
    monkeypatch.setenv("JCC_REPLAY_RECORDING", str(recording))
    client, engine = _client(tmp_path, monkeypatch, JevConfig(provider="replay"))
    assert isinstance(engine.provider, RecordReplayProvider)
    b = client.get("/api/health").json()
    assert b["provider_mode"] == "REPLAY"
    assert b["provider_is_real"] is False
    assert b["jev_provider"] == "replay"

    # and the scored record itself is rewritten to "replay", not left as "real"
    replayed = engine.provider.assess(recorded, None)
    assert replayed.provider == "replay"
    assert replayed.raw["replay_provenance"]["schema_version"] == "jcc-replay/2"


def test_no_non_real_provider_can_set_is_real():
    """Anti-impersonation, asserted over every shipped class at once."""
    assert RealProvider.is_real is True
    for cls in SHIPPED_PROVIDER_CLASSES:
        if cls is RealProvider:
            continue
        assert cls.is_real is False, f"{cls.__name__} claims to be the real provider"
        assert cls.mode != "REAL", f"{cls.__name__} claims the REAL mode"


def test_activation_state_for_requested_defers_when_real_is_not_asked_for():
    """Non-REAL requests must pass through to the live provider's own state."""
    assert activation_state_for_requested("mock", DeterministicMockProvider()) == "ACTIVE"
    assert activation_state_for_requested("disabled", DisabledProvider()) == "DISABLED"
    assert activation_state_for_requested(
        "", DeterministicMockProvider()
    ) == "ACTIVE", "an empty requested mode must not invent a pending state"


# --------------------------------------------------------------------------- #
# 3. The real provider cannot fake readiness
# --------------------------------------------------------------------------- #
def test_authority_level_is_what_verifies_a_field():
    """§7B: a VALUE is not evidence. Authority decides, and only 3 levels pass."""
    for authority in sorted(AUTHORITIES_THAT_CAN_VERIFY):
        assert InterfaceField("x", "v", authority, "e").status == STATUS_VERIFIED, authority
    for authority in (AUTHORITY_LOCAL_CONFIG, AUTHORITY_ASSUMED, AUTHORITY_UNKNOWN):
        f = InterfaceField("x", "v", authority, "e")
        assert f.status == STATUS_UNVERIFIED, (
            f"authority {authority} promoted a field to VERIFIED; §7B forbids it"
        )
    # and an UNKNOWN value never verifies, whatever the authority claims
    assert InterfaceField("x", None, AUTHORITY_MEASURED, "e").status == STATUS_UNVERIFIED


@pytest.mark.parametrize("authority", [AUTHORITY_OFFICIAL_PUBLIC,
                                       AUTHORITY_OFFICIAL_ACCOUNT_DOC,
                                       AUTHORITY_MEASURED])
def test_official_and_measured_authorities_can_verify(authority):
    assert InterfaceField("x", "v", authority, "e").status == STATUS_VERIFIED


def test_shipped_card_ships_with_no_assumed_values():
    """A shipped ASSUMED value is a guess wearing a label (§7C forbids it)."""
    card = unverified_default_interface()
    assert all(f.authority != AUTHORITY_ASSUMED for f in card.fields), (
        "the default card must carry no guesses; use AUTHORITY_UNKNOWN with a "
        "value of None instead"
    )


def test_verified_fields_carry_a_date_and_unknown_fields_never_do():
    """``last_verified_at`` is part of the §7F record, so it must not be noise.

    Two symmetric errors would make the published artifact's timestamp column
    meaningless: a VERIFIED field with a null date (nobody can tell when it was
    checked) and an UNVERIFIED field with a date (a fact that was never
    established appears to have been checked).
    """
    card = unverified_default_interface()
    for f in card.fields:
        if f.status == STATUS_VERIFIED:
            assert f.last_verified_at, f"{f.name} is VERIFIED but has no date"
        else:
            assert f.last_verified_at is None, (
                f"{f.name} is UNVERIFIED yet carries date {f.last_verified_at!r}; "
                "an unknown fact has no verification date"
            )


def test_shipped_card_names_the_critical_gaps_rather_than_saying_unverified():
    """The whole point of §7F: the record names WHAT is missing."""
    card = unverified_default_interface()
    missing = card.missing_critical()
    for required in ("endpoint_path", "http_method", "api_version",
                     "auth_header_syntax", "model_identifier",
                     "request_schema", "response_schema",
                     "timeout_contract", "error_schema",
                     "idempotency_semantics"):
        assert required in missing, f"{required} must be declared UNVERIFIED today"
    assert card.all_critical_verified() is False


#: The non-critical set, PINNED. Downgrading a field to non-critical is the one
#: edit that could silently shrink ``missing_critical()`` while every other
#: assertion in this file still passes, so the set is pinned by name: a sixth
#: downgrade cannot happen without editing this tuple in a reviewed commit.
#:
#: NOTE ON WHAT THIS ASSERTS: an earlier version of this test checked that each
#: note contained the literal word "Non-critical". That measured PHRASING, not
#: the property -- it would have passed on a note reading "Non-critical" with no
#: reason given, and failed on a good reason phrased differently. The property
#: that matters is "the set is explicit and every member explains itself", so
#: that is what is measured here.
PINNED_NON_CRITICAL_FIELDS = (
    "vendor",
    "model_family",
    "public_model_name",
    "public_availability",
    "api_host",
    "web_console_host",
    "input_pricing",
    "output_pricing",
    "official_api_status",
    "batch_semantics",
    "retry_contract",
    "rate_limit_headers",
    "usage_response_fields",
    "billing_response_fields",
)


def test_the_non_critical_set_is_pinned_and_every_member_justifies_itself():
    card = unverified_default_interface()
    declared = tuple(f.name for f in card.fields if not f.critical)
    assert declared == PINNED_NON_CRITICAL_FIELDS, (
        "the non-critical set changed.\n"
        f"  was: {PINNED_NON_CRITICAL_FIELDS}\n"
        f"  now: {declared}\n"
        "  A field is non-critical only when not knowing it is safe BECAUSE the "
        "shipped contract never uses it. If that is now true, add it here WITH a "
        "note; if it is not, leave it critical."
    )
    for f in card.fields:
        if f.critical:
            continue
        assert f.note and len(f.note) > 20, (
            f"non-critical field {f.name!r} carries no real justification: {f.note!r}"
        )


def test_missing_critical_is_exactly_critical_minus_verified():
    """The list §7F publishes must mean what it says.

    ``missing_critical()`` is what the release artifact reports as "what stops
    real Jev from scoring". If it ever drifted from "critical fields that are not
    VERIFIED" -- e.g. by also dropping verified fields, or by keeping unverified
    non-critical ones -- the artifact would over- or under-state the gap, and
    nothing else in the suite would notice. This asserts the equivalence.
    """
    card = unverified_default_interface()
    missing = set(card.missing_critical())
    critical = {f.name for f in card.fields if f.critical}
    expected = {f.name for f in card.fields if f.critical and f.status != STATUS_VERIFIED}
    assert missing == expected

    # Non-vacuity: the gap is concrete, and it is the request-construction set.
    assert len(missing) >= 10, (
        f"only {len(missing)} critical gaps reported; the interface cannot be "
        "this complete -- re-check the card rather than trusting this number"
    )
    for needed in ("endpoint_path", "http_method", "api_version",
                   "auth_header_syntax", "request_schema", "response_schema",
                   "timeout_contract", "error_schema", "idempotency_semantics"):
        assert needed in missing, f"{needed} must still be an open critical gap"


def test_real_provider_verified_is_computed_from_the_card():
    """``verified`` cannot drift away from the card that justifies it."""
    p = RealProvider(api_key="dummy")
    assert p.verified == bool(TRANSPORT_IMPLEMENTED and p.interface.all_critical_verified())
    assert p.verified is False
    assert p.activation_state == "PENDING_EXTERNAL_ACTIVATION"
    # a fully VERIFIED card still cannot open the wire while the transport lock
    # is closed -- two locks, not one
    fully = RealJevInterface(fields=tuple(
        InterfaceField(f.name, f.value or "known", AUTHORITY_MEASURED, "test",
                       critical=f.critical)
        for f in unverified_default_interface().fields
    ))
    assert fully.all_critical_verified() is True
    assert RealProvider(api_key="dummy", interface=fully).verified is False


def test_real_provider_names_every_unmet_requirement():
    p = RealProvider(api_key=None)
    unmet = p.unmet_requirements()
    assert "transport_not_implemented" in unmet
    assert any(u.startswith("credential_absent(") for u in unmet)
    assert any(u.startswith("interface_field_critical_unverified:endpoint_path")
               for u in unmet)
    rep = p.interface_report()
    assert rep["provider_activation_state"] == "PENDING_EXTERNAL_ACTIVATION"
    assert rep["credential_present"] is False
    # the report is JSON-serialisable and carries no credential value anywhere
    assert "dummy" not in json.dumps(rep)


def test_real_provider_issues_no_network_call(monkeypatch):
    """The strongest falsifier here: make any socket use explode.

    ``assess`` must fail closed by NOT calling out. Patching the socket factory
    (and ``create_connection``) means a future edit that quietly adds a request
    turns this test red instead of silently billing someone.
    """

    class _NoNetwork:
        def __init__(self, *a, **k):
            raise AssertionError("RealProvider opened a socket while UNVERIFIED")

    monkeypatch.setattr(socket, "socket", _NoNetwork)
    monkeypatch.setattr(socket, "create_connection", _NoNetwork)

    p = RealProvider(api_key="dummy", model="jev-1.13.0")
    a = p.assess(_env(evidence={"x": 1}), None)

    assert a.provider_verified is False
    assert a.provider == "real"
    # an UNVERIFIED provider is an ESCALATE, never a declared abstention
    assert "provider_abstained" not in a.raw
    assert a.raw["real_jev"] == "PENDING_EXTERNAL_ACTIVATION"
    assert a.raw["interface_digest"] == p.interface.digest()


def test_real_provider_assessment_still_escalates_exactly_as_before(monkeypatch):
    """The v1.0 kernel behaviour is UNCHANGED by this file's additions.

    v1.1 made the *reason* precise; it must not have changed the action. If this
    ever fails, the change is a behaviour change, not a reporting change.
    """
    from jcc_core import decision
    from jcc_core.schemas import DirectiveAction

    p = RealProvider(api_key="dummy")
    a = p.assess(_env(evidence={"x": 1}), None)
    blockers = decision.find_blockers(decision.normalize(a), a)
    assert decision.BLOCKER_PROVIDER_UNVERIFIED in blockers
    assert decision.decide_action(a) is DirectiveAction.ESCALATE


def test_interface_digest_is_stable_and_sensitive_to_the_card():
    a = unverified_default_interface()
    b = unverified_default_interface()
    assert a.digest() == b.digest(), "the card digest must be reproducible"
    changed = RealJevInterface(fields=a.fields[:-1])
    assert changed.digest() != a.digest(), "dropping a field must move the digest"
    assert len(a.digest()) == 64  # sha256 hex, so a document can quote it
