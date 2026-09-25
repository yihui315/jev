"""S-07 — secret-leakage tests for the Claw3D facade.

No secret may reach a projected payload. The EXCLUDED_FIELDS barrier means
selection never picks a secret-bearing path; ``scrub_value_secrets`` runs on the
whole source BEFORE selection, so a secret planted inside a *declared* field
value (free text, e.g. the scene ``cluster`` label) is still redacted.
"""
from __future__ import annotations

from jcc_core.redaction import scrub_value_secrets

from claw3d import Claw3DFacade

PLANTED_SECRET = "sk-live-S07SENTINELdeadbeef0123456789abcdef"

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
    # Plant the sentinel two ways: a free-text declared field AND an excluded field.
    rich_state.project = "proj " + PLANTED_SECRET                       # DECLARED field (free text)
    rich_state.current_directive["signature"] = PLANTED_SECRET         # excluded field

    store.upsert_session(rich_state)
    facade = Claw3DFacade(store)
    blob = facade.project_scene_bytes()

    assert PLANTED_SECRET.encode() not in blob, "sentinel secret leaked into the projected payload"
    node = facade.get_node(rich_state.session_id)
    assert node is not None
    assert PLANTED_SECRET not in node["cluster"]
    assert "<redacted" in node["cluster"]


def test_excluded_secret_key_not_projected(rich_state, store):
    rich_state.current_directive["signature"] = PLANTED_SECRET
    store.upsert_session(rich_state)
    facade = Claw3DFacade(store)
    doc = facade.project_scene()
    for node in doc["nodes"]:
        assert "current_directive.signature" not in node


def test_scrub_removes_sentinel_in_free_text():
    payload = {"cluster": "team " + PLANTED_SECRET, "nested": {"log": PLANTED_SECRET}}
    cleaned = scrub_value_secrets(payload)
    assert PLANTED_SECRET not in str(cleaned)
    assert "<redacted" in str(cleaned)


def test_read_only_payload_has_no_control_token_field(rich_state, store):
    store.upsert_session(rich_state)
    facade = Claw3DFacade(store)
    doc = facade.project_scene()
    for node in doc["nodes"]:
        for forbidden in CONTROL_TOKEN_FIELDS:
            assert forbidden not in node, f"forbidden secret field {forbidden!r} projected"
