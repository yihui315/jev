"""S-06 — contract conformance tests.

The facade contract is the single source of truth (``contract.json``); these
tests assert the running code agrees with that file and that the contract keeps
its read-only / no-secret promises.
"""
from __future__ import annotations

from session_center import (
    CONTRACT_NAME,
    CONTRACT_VERSION,
    EXCLUDED_FIELDS,
    FIELD_SPEC,
    SessionCenterFacade,
    contract_path,
    load_contract,
)
from session_center.external import probe_external_product
from session_center.read_only import MUTATING_API


def _walk(node, dotted):
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def test_contract_file_matches_module():
    on_disk = load_contract()
    assert on_disk["name"] == CONTRACT_NAME
    assert on_disk["version"] == CONTRACT_VERSION
    # The module constants are derived from the file, never hand-copied.
    assert len(FIELD_SPEC) == len(on_disk["fields"])
    assert len(EXCLUDED_FIELDS) == len(on_disk["excluded_fields"])
    for spec in FIELD_SPEC:
        assert {"path", "source", "type"} <= set(spec), spec


def test_contract_path_is_on_disk():
    p = contract_path()
    assert p.exists() and p.name == "contract.json"


def test_contract_is_read_only(facade):
    contract = load_contract()
    assert contract["read_only"] is True
    assert facade.read_only is True
    # The facade's public surface advertises NO mutating method.
    assert facade.mutating_api == ()
    assert MUTATING_API == ()


def test_external_product_marked_unavailable(facade):
    """OPTIONAL VISUAL INTEGRATION: the contract says absent, the live probe agrees."""
    contract = load_contract()
    assert contract["external_product_available"] is False
    assert facade.external_product_available is False
    probe = probe_external_product()
    assert probe["available"] is False
    assert probe["reasons"]  # absence is explained, not assumed


def test_every_declared_field_is_projected(rich_state, store):
    store.upsert_session(rich_state)
    f = SessionCenterFacade(store)
    row = f.project_session(rich_state)
    declared = {spec["path"] for spec in FIELD_SPEC}
    assert set(row.keys()) == declared
    # Primary identity survives the projection.
    assert row["session_id"] == rich_state.session_id
    assert row["status"] == rich_state.status.value
    assert row["current_directive.action"] in {
        "ESCALATE", "PAUSE", "VERIFY", "STEER", "RETRY", "FINISH", "CONTINUE",
    }


def test_excluded_fields_never_reach_payload(rich_state, store):
    """The secret barrier: every excluded dotted path is absent from the output."""
    store.upsert_session(rich_state)
    f = SessionCenterFacade(store)
    doc = f.project_all()
    for excluded in EXCLUDED_FIELDS:
        for session in doc["sessions"]:
            assert _walk(session, excluded) is None, (
                f"excluded field {excluded!r} leaked into the payload"
            )


def test_contract_version_is_pinned():
    assert CONTRACT_VERSION == "1.0.0"
    assert CONTRACT_NAME == "jcc.session_center"
