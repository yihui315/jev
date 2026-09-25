"""S-06 — secret-leakage tests.

No secret may reach a projected payload. The barrier is two-layered and both
layers are asserted:

* the EXCLUDED_FIELDS list means selection never picks a secret-bearing path;
* ``scrub_value_secrets`` runs on the WHOLE source BEFORE selection, so a secret
  planted inside a *declared* field value (free text) is still redacted.

The sentinel is planted the way an adversarial producer would: under a
secret-looking key AND embedded in free-form text.
"""
from __future__ import annotations

from jcc_core.redaction import scrub_value_secrets

from session_center import SessionCenterFacade

#: A sentinel that must never reach a facade payload. Mirrors the conftest
#: constant: planted into the source state the way an adversarial producer would.
PLANTED_SECRET = "sk-live-S06SENTINELdeadbeef0123456789abcdef"

#: Dotted field paths that are declared but would carry a secret if projected.
CONTROL_TOKEN_FIELDS = (
    "current_directive.signature",
    "current_directive.nonce",
    "current_directive.evidence_hash",
    "current_directive.params",
    "current_directive.verify_reason",
    "current_directive.human_approval",
    "current_directive.policy_config_hash",
)


def test_sentinel_never_reaches_payload(rich_state, store):
    # Plant the sentinel three ways into a real session state.
    rich_state.current_directive["signature"] = PLANTED_SECRET            # excluded field
    rich_state.current_directive["actuation_reason"] = "boom " + PLANTED_SECRET  # DECLARED field (free text)
    rich_state.current_assessment["raw"]["note"] = "leak " + PLANTED_SECRET      # excluded nested field

    store.upsert_session(rich_state)
    facade = SessionCenterFacade(store)
    blob = facade.project_all_bytes()

    assert PLANTED_SECRET.encode() not in blob, "sentinel secret leaked into the projected payload"
    # The declared field's free-text secret was scrubbed (not merely dropped).
    row = facade.get_session(rich_state.session_id)
    assert row is not None
    assert PLANTED_SECRET not in row["current_directive.actuation_reason"]
    assert "<redacted" in row["current_directive.actuation_reason"]


def test_excluded_secret_key_not_projected(rich_state, store):
    rich_state.current_directive["signature"] = PLANTED_SECRET
    store.upsert_session(rich_state)
    facade = SessionCenterFacade(store)
    doc = facade.project_all()
    for session in doc["sessions"]:
        # The excluded dotted path must be absent entirely (flat-key encoding).
        assert "current_directive.signature" not in session


def test_scrub_removes_sentinel_in_free_text():
    payload = {"reason": "user token was " + PLANTED_SECRET, "nested": {"log": PLANTED_SECRET}}
    cleaned = scrub_value_secrets(payload)
    assert PLANTED_SECRET not in str(cleaned)
    assert "<redacted" in str(cleaned)


def test_read_only_payload_has_no_control_token_field(rich_state, store):
    store.upsert_session(rich_state)
    facade = SessionCenterFacade(store)
    doc = facade.project_all()
    for session in doc["sessions"]:
        for forbidden in CONTROL_TOKEN_FIELDS:
            assert forbidden not in session, f"forbidden secret field {forbidden!r} projected"
