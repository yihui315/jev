"""S-07 — contract conformance tests for the Claw3D read-only 3D-scene facade."""
from __future__ import annotations

from claw3d import (
    CONTRACT_NAME,
    CONTRACT_VERSION,
    EXCLUDED_FIELDS,
    FIELD_SPEC,
    Claw3DFacade,
    contract_path,
    load_contract,
)
from claw3d.external import probe_external_product
from claw3d.read_only import MUTATING_API


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
    assert facade.mutating_api == ()
    assert MUTATING_API == ()


def test_external_product_marked_unavailable(facade):
    """OPTIONAL VISUAL INTEGRATION: absent per contract, absent per live probe."""
    contract = load_contract()
    assert contract["external_product_available"] is False
    assert facade.external_product_available is False
    probe = probe_external_product()
    assert probe["available"] is False
    assert probe["reasons"]


def test_every_declared_field_is_projected(rich_state, store):
    store.upsert_session(rich_state)
    f = Claw3DFacade(store)
    row = f.project_session(rich_state)
    declared = {spec["path"] for spec in FIELD_SPEC}
    assert set(row.keys()) == declared
    # Geometry is derived (deterministic) and present for every node.
    assert isinstance(row["pos_x"], int) and isinstance(row["pos_y"], int) \
        and isinstance(row["pos_z"], int)
    # Primary identity + a real decision are projected.
    assert row["node_id"] == rich_state.session_id
    assert row["status"] == rich_state.status.value
    assert row["action"] in {
        "ESCALATE", "PAUSE", "VERIFY", "STEER", "RETRY", "FINISH", "CONTINUE",
    }


def test_excluded_fields_never_reach_payload(rich_state, store):
    store.upsert_session(rich_state)
    f = Claw3DFacade(store)
    doc = f.project_scene()
    for excluded in EXCLUDED_FIELDS:
        for node in doc["nodes"]:
            assert _walk(node, excluded) is None, \
                f"excluded field {excluded!r} leaked into the payload"


def test_contract_version_is_pinned():
    assert CONTRACT_VERSION == "1.0.0"
    assert CONTRACT_NAME == "jcc.claw3d"
