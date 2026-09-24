"""P3 core integration test — the D-26 ingest-boundary matrix (ADR-0008 D-26,
extended to the server boundary in Round 5).

Why this file exists
--------------------
The ``POST /api/assess`` boundary previously had **no** test coverage for a
malformed body: ``AEPEnvelope(**body)`` raised an unhandled ``ValidationError``
-> HTTP 500 with **no audit row** (a hole in the authoritative tape). Round 5
extended D-26 to this boundary: a malformed/unauthorised envelope must NOT 500
silently; it returns a structured 4xx that still writes its audit row.

This is the regression test for that path. It enumerates the **full
token x body-validity matrix** and states the audit-row expectation for **every**
cell explicitly (not left implied):

    cell                                | status | audit row written
    ------------------------------------|--------|------------------
    valid   token  + valid     body     |  200   | YES (engine.ingest)
    valid   token  + malformed body     |  422   | YES (boundary records the rejection)
    no/inv. token  + valid     body     |  403   | NO  (rejected before the handler)
    no/inv. token  + malformed body     |  403   | NO  (rejected before the handler)

The chain must verify intact after every cell.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from jcc_core.actuator import Actuator
from jcc_core.config import ActuationConfig, Config, PolicyConfig, ServerConfig
from jcc_core.core import CoreEngine
from jcc_core.providers import build_provider
from jcc_core.server import create_app
from jcc_core.state_store import StateStore

TOKEN = "p3-core-integration-token"

VALID_BODY = {
    "event_id": "e1",
    "delivery_id": "d1",
    "session_id": "s1",
    "agent": "claude",
    "project": "p",
    "event_type": "tool_call",
    "payload": {},
}

# Missing the required AEP fields (event_id/delivery_id/agent) -> ValidationError.
MALFORMED_BODY = {"session_id": "s1"}

# (id, headers, body, expected_status, expect_audit_row)
CASES = [
    ("valid-token+valid-body", {"X-JCC-Token": TOKEN}, VALID_BODY, 200, True),
    ("valid-token+malformed-body", {"X-JCC-Token": TOKEN}, MALFORMED_BODY, 422, True),
    ("no-token+valid-body", {}, VALID_BODY, 403, False),
    ("no-token+malformed-body", {}, MALFORMED_BODY, 403, False),
    ("bad-token+valid-body", {"X-JCC-Token": "wrong"}, VALID_BODY, 403, False),
    ("bad-token+malformed-body", {"X-JCC-Token": "wrong"}, MALFORMED_BODY, 403, False),
]


@pytest.fixture()
def client_and_store(tmp_path, monkeypatch):
    monkeypatch.setenv("JCC_CONTROL_TOKEN", TOKEN)
    db_path = str(tmp_path / "jcc.db")
    cfg = Config(
        server=ServerConfig(),
        actuation=ActuationConfig(),
        policy=PolicyConfig(),
        state_store_db_path=db_path,
    )
    store = StateStore(db_path)
    engine = CoreEngine(store, build_provider(cfg), Actuator(enabled=False), cfg)
    return TestClient(create_app(engine, cfg)), store


@pytest.mark.parametrize(
    "case_id,headers,body,expected_status,expect_audit_row",
    CASES,
    ids=[case[0] for case in CASES],
)
def test_assess_boundary_matrix(
    client_and_store, case_id, headers, body, expected_status, expect_audit_row
):
    client, store = client_and_store
    before = len(store.get_audit())

    r = client.post("/api/assess", json=body, headers=headers)

    wrote = len(store.get_audit()) - before

    # 1) exact HTTP status for the cell
    assert r.status_code == expected_status, (case_id, r.status_code, r.text[:200])
    # 2) never a silent 500 (D-26 extended)
    assert r.status_code != 500, (case_id, "unhandled server error")

    # 3) the audit-row expectation for THIS cell
    if expect_audit_row:
        assert wrote == 1, (case_id, "record must never be dropped", wrote)
    else:
        assert wrote == 0, (case_id, "rejected before the handler; no record", wrote)

    # 4) the hash chain stays intact after every cell
    ok, _count = store.verify_audit()
    assert ok is True, (case_id, "audit chain broken")


def test_malformed_rejection_is_recorded_as_exact_blocker(client_and_store):
    """The rejection is auditable: it is labelled, not silently dropped."""
    client, store = client_and_store
    r = client.post("/api/assess", json=MALFORMED_BODY, headers={"X-JCC-Token": TOKEN})
    assert r.status_code == 422
    row = store.get_audit()[-1]
    assert row.get("assessment") == "rejected:EXACT_BLOCKER", row
    assert row.get("directive") == "NONE:rejected", row


def test_valid_ingest_reports_inert_engine_reason(client_and_store):
    """The engine ingest path is permanently inert (D-27): acted=False,
    reason=shadow_mode -- asserted here so the reason string cannot drift silently."""
    client, store = client_and_store
    r = client.post("/api/assess", json=VALID_BODY, headers={"X-JCC-Token": TOKEN})
    assert r.status_code == 200, r.text[:200]
    payload = r.json()
    assert payload.get("acted") is False
    assert payload.get("actuation_reason") == "shadow_mode", payload.get("actuation_reason")
