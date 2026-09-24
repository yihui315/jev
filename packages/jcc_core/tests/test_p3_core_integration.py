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

from jcc_core import decision, policy
from jcc_core.actuator import Actuator
from jcc_core.config import ActuationConfig, Config, PolicyConfig, ServerConfig
from jcc_core.core import CoreEngine
from jcc_core.providers import build_provider
from jcc_core.providers.mock import DeterministicMockProvider
from jcc_core.schemas import AEPEnvelope, DirectiveAction
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


# --------------------------------------------------------------------------- #
# D-32 / R6-6 — characterization: engine-path ``policy.decide`` vs P2
# --------------------------------------------------------------------------- #
#: FROZEN P2 reference mapping, transcribed from the pre-D-32 code
#: (``providers/mock.py:55-69`` setting the flags + ``policy.py:30-49`` walking
#: them in priority order). These literals are the POINT of a characterization
#: test: they pin the P1/P2 mapping the live engine must keep.
_P2_CUTS = (0.8, 0.6, 0.4)   # escalate / pause / steer (mock.py:62-67)
_P2_FINISH_CUT = 0.1         # mock.py:68-69


def _p2_reference(risk: float, evidence_present: bool) -> DirectiveAction:
    """What P2's (mock + flag-walking policy) produced for this input."""
    if not evidence_present:
        return DirectiveAction.VERIFY            # mock.py:55-60 -> policy.py:33
    if risk >= _P2_CUTS[0]:
        return DirectiveAction.ESCALATE
    if risk >= _P2_CUTS[1]:
        return DirectiveAction.PAUSE
    if risk >= _P2_CUTS[2]:
        return DirectiveAction.STEER
    if risk < _P2_FINISH_CUT:
        return DirectiveAction.FINISH
    return DirectiveAction.CONTINUE              # policy.py:49 floor


def _cell(i: int, *, evidence: bool) -> AEPEnvelope:
    return AEPEnvelope(
        event_id=f"cell{i}",
        delivery_id=f"d{i}",
        session_id="s1",
        agent="workbuddy",
        project="p",
        event_type="tool_call",
        evidence=({"ok": True} if evidence else None),
    )


def test_engine_path_is_identical_to_p2_on_every_evidence_fixture():
    """ASSERTED EQUALITY (R6-6): zero divergent cells with evidence present.

    Also pins R6-1: ``policy.decide`` IS ``decision.decide_action`` (no second
    authority), so this characterization cannot be satisfied by a special case.
    """
    provider = DeterministicMockProvider()
    divergent = []
    n = 1500
    for i in range(n):
        env = _cell(i, evidence=True)
        assess = provider.assess(env, None)
        risk = assess.risk_score
        got = policy.decide(assess)
        ref = _p2_reference(risk, True)

        assert got is decision.decide_action(assess), "wrapper is not the kernel"
        if got is not ref:
            divergent.append((i, risk, ref.value, got.value))

    assert divergent == [], f"{len(divergent)}/{n} cells diverged: {divergent[:10]}"
    # sanity: the corpus really spans all four bands, else the test is vacuous
    bands = {provider.assess(_cell(i, evidence=True), None).band for i in range(n)}
    assert bands == {"low", "medium", "high", "critical"}, bands


def test_engine_path_missing_evidence_corner_is_measured_not_hidden():
    """The ONE known P2 divergence, measured explicitly (ADR-0008 D-35 vs D-32).

    P2 returned VERIFY for *every* missing-evidence event (it short-circuited
    before reading the risk). The kernel cannot: rows 1-4 are evaluated before
    the VERIFY row, so a missing-evidence event whose risk reaches the pause
    cut is absorbed by PAUSE/ESCALATE first.

    Below the pause cut the two agree exactly; at/above it they diverge and this
    test PINS the divergence instead of hiding it (the band must stay definite —
    a `band="unknown"` would escalate at every risk level, Add B).
    """
    provider = DeterministicMockProvider()
    agree, diverge = 0, []
    n = 1500
    for i in range(n):
        env = _cell(i, evidence=False)
        assess = provider.assess(env, None)
        risk = assess.risk_score

        # preconditions: the provider contract holds on the missing-evidence path
        assert assess.band in ("low", "medium", "high", "critical"), assess.band
        assert assess.raw["signals"]["needs_verification"] is True
        assert assess.raw["evidence_complete"] is False

        got = policy.decide(assess)
        ref = _p2_reference(risk, False)          # always VERIFY

        if got is ref:
            agree += 1
            assert got is DirectiveAction.VERIFY
        else:
            # only the documented corner: risk at/above the pause cut
            assert risk >= _P2_CUTS[1], (i, risk, got.value)
            assert got in (DirectiveAction.PAUSE, DirectiveAction.ESCALATE)
            diverge.append((i, risk, ref.value, got.value))

    # both sides must be exercised, or the measurement is vacuous
    assert agree > 0, "no missing-evidence cell reached VERIFY"
    assert diverge, "expected the documented high-risk missing-evidence corner"
    # every divergent cell is at/above the pause cut, and none is a resuming action
    assert all(r >= _P2_CUTS[1] for _, r, _, _ in diverge)
    assert {got for *_, got in diverge} <= {"PAUSE", "ESCALATE"}

