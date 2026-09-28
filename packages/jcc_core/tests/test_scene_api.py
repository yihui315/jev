"""Wave 3 — Scene API contract tests (schema ``jcc-scene/1``).

Six state scenarios, all asserting the SAME anti-fabrication invariant:

  * the document has exactly the ten labelled sections;
  * every section's ``source`` ∈ {LIVE, RELEASE, DERIVED, UNKNOWN};
  * ANY section whose ``source`` is UNKNOWN has ``data is None`` — never
    ``0`` / ``False`` / ``[]`` / ``"healthy"`` masquerading as a clean gap;
  * the projection NEVER writes and NEVER raises on malformed store state.

The route is open (no token) and is declared on ``OPEN_GET_ENDPOINTS``.
"""
from __future__ import annotations

import sqlite3
import time

import pytest

from fastapi.testclient import TestClient

from jcc_core.actuator import Actuator
from jcc_core.config import Config, ServerConfig
from jcc_core.core import CoreEngine
from jcc_core.providers import build_provider
from jcc_core.scene import build_scene
from jcc_core.schemas import AEPEnvelope, AgentName, EventType
from jcc_core.server import OPEN_GET_ENDPOINTS, create_app
from jcc_core.state_store import StateStore

SECTION_KEYS = [
    "system", "agents", "sessions", "assessments", "directives",
    "risks", "capabilities", "shadow", "budget", "audit_summary",
]
VALID_SOURCES = {"LIVE", "RELEASE", "DERIVED", "UNKNOWN"}


def _make_engine(tmp_path):
    db_path = str(tmp_path / "scene.db")
    cfg = Config(server=ServerConfig(), state_store_db_path=db_path)
    store = StateStore(db_path)
    engine = CoreEngine(store, build_provider(cfg), Actuator(enabled=False), cfg)
    return engine, cfg


def _ingest(engine, session_id, agent, seq, *, evidence=True):
    engine.ingest(
        AEPEnvelope(
            event_id=f"e{seq}",
            delivery_id=f"d{seq}",
            session_id=session_id,
            agent=agent,
            project="proj",
            cwd="/tmp",
            timestamp=time.time(),
            event_type=EventType.TOOL_CALL,
            payload={"n": seq},
            evidence={"ok": True} if evidence else None,
            schema_ok=True,
        )
    )


def _assert_scene_contract(scene: dict):
    assert scene["schema_version"] == "jcc-scene/1", scene.get("schema_version")
    assert set(scene["source_legend"]) == VALID_SOURCES
    for key in SECTION_KEYS:
        assert key in scene, f"missing section {key}"
        sec = scene[key]
        assert sec["source"] in VALID_SOURCES, f"{key}.source={sec['source']}"
        # Anti-fabrication: an UNKNOWN gap is NEVER a clean-looking value.
        if sec["source"] == "UNKNOWN":
            assert sec["data"] is None, f"{key} UNKNOWN but data={sec['data']!r}"
    # No invented health boolean anywhere in the LIVE system facts.
    if scene["system"]["source"] == "LIVE":
        assert "healthy" not in scene["system"]["data"], "system invented a healthy flag"


def test_scene_route_is_open_and_declared():
    """The route is declared on the open surface (reachability covered below)."""
    assert "/api/v1/scene" in OPEN_GET_ENDPOINTS


def test_empty_state(tmp_path):
    """Zero sessions: every section present, no fabricated content."""
    engine, cfg = _make_engine(tmp_path)
    scene = build_scene(engine, cfg)
    _assert_scene_contract(scene)
    assert scene["sessions"]["data"] == []
    assert scene["agents"]["data"]["distinct_agent_count"] == 0
    # capabilities is RELEASE because the real artifact exists on disk.
    assert scene["capabilities"]["source"] in ("RELEASE", "UNKNOWN")
    # shadow is UNKNOWN until Wave 7 — and honestly so.
    assert scene["shadow"]["source"] == "UNKNOWN"
    assert scene["shadow"]["data"] is None


def test_live_state(tmp_path):
    """Real ingested events populate LIVE sections."""
    engine, cfg = _make_engine(tmp_path)
    for i in range(5):
        _ingest(engine, f"sess{i}", AgentName.WORKBUDDY, i)
    for i in range(3):
        _ingest(engine, f"claude{i}", AgentName.CLAUDE, 100 + i)
    scene = build_scene(engine, cfg)
    _assert_scene_contract(scene)
    assert scene["sessions"]["source"] == "LIVE"
    assert len(scene["sessions"]["data"]) == 8
    assert scene["assessments"]["source"] == "LIVE"
    assert len(scene["assessments"]["data"]) == 8
    assert scene["directives"]["source"] == "LIVE"
    assert len(scene["directives"]["data"]) == 8
    assert scene["risks"]["source"] == "DERIVED"
    assert scene["risks"]["data"]["escalation_count"] == 0
    assert scene["audit_summary"]["source"] == "LIVE"
    assert scene["audit_summary"]["data"]["chain_intact"] is True
    assert scene["system"]["data"]["provider_is_real"] is False  # mock provider, LIVE truth


def test_demo_state_uses_release_capability_matrix(tmp_path):
    """When the released capability matrix is reachable it is RELEASE, not UNKNOWN."""
    engine, cfg = _make_engine(tmp_path)
    _ingest(engine, "s1", AgentName.WORKBUDDY, 1)
    scene = build_scene(engine, cfg)
    _assert_scene_contract(scene)
    assert scene["capabilities"]["source"] == "RELEASE"
    assert isinstance(scene["capabilities"]["data"], dict)
    assert "agents" in scene["capabilities"]["data"]


def test_mixed_state_has_all_four_sources(tmp_path):
    """LIVE + DERIVED + RELEASE + UNKNOWN all coexist honestly."""
    engine, cfg = _make_engine(tmp_path)
    _ingest(engine, "s1", AgentName.HERMES, 1)
    _ingest(engine, "s2", AgentName.CODEX, 2)
    scene = build_scene(engine, cfg)
    _assert_scene_contract(scene)
    sources = {scene[k]["source"] for k in SECTION_KEYS}
    assert sources == VALID_SOURCES, f"expected all four sources, got {sources}"


def test_hostile_state_never_crashes_and_never_coerces(tmp_path):
    """Malformed store rows degrade ONE section, never crash, never fake-clean."""
    engine, cfg = _make_engine(tmp_path)
    # Seed one well-formed session first.
    _ingest(engine, "good", AgentName.WORKBUDDY, 1)
    # Now inject a row with an INVALID current_assessment JSON (hostile store).
    conn = sqlite3.connect(engine.store.db_path)
    conn.execute(
        "INSERT INTO sessions (session_id, agent, project, cwd, status, "
        "created_at, updated_at, last_event_id, event_count, evidence_count, "
        "current_assessment, current_directive) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("broken", "claude", "p", None, "active", 0.0, 0.0, "e", 0, 0,
         '{"band":', None),  # invalid JSON
    )
    conn.commit()
    conn.close()

    scene = build_scene(engine, cfg)  # must NOT raise
    _assert_scene_contract(scene)
    # The sessions read failed to parse -> degraded to UNKNOWN honestly.
    assert scene["sessions"]["source"] == "UNKNOWN"
    assert scene["sessions"]["data"] is None
    # shadow stays UNKNOWN (not coerced to a false-clean value)
    assert scene["shadow"]["data"] is None
    # assessments derive defensively even from the good row
    assert scene["assessments"]["source"] in ("LIVE", "UNKNOWN")


def test_hostile_assessment_payload_is_skipped_not_crashed():
    """Per-row assessment extraction tolerates non-dict / missing leaves."""
    from jcc_core.scene import _assessments_from_sessions

    rows = [
        {"session_id": "a", "agent": "workbuddy", "current_assessment": {"band": "high"}},
        {"session_id": "b", "agent": "claude", "current_assessment": "not-a-dict"},
        {"session_id": "c", "agent": "codex", "current_assessment": None},
        {"session_id": "d", "agent": "hermes", "current_assessment": {"band": "high"}},  # missing risk_score -> None
    ]
    out = _assessments_from_sessions(rows)
    assert len(out) == 2
    assert out[1]["risk_score"] is None  # missing leaf kept as None, not 0


def test_large_state_performance(tmp_path):
    """1000 sessions build fast and count exactly.

    The projection is O(sessions); we bulk-insert 1000 rows in ONE transaction
    (validating scale, not the ingest hot path, which is covered elsewhere and
    is far heavier per row).
    """
    import json as _json

    engine, cfg = _make_engine(tmp_path)
    agents = ["workbuddy", "claude", "codex", "hermes"]
    with engine.store.connection() as conn:
        conn.execute("BEGIN")
        for i in range(1000):
            conn.execute(
                "INSERT INTO sessions (session_id, agent, project, cwd, status, "
                "created_at, updated_at, last_event_id, event_count, evidence_count, "
                "current_assessment, current_directive) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"big{i}", agents[i % 4], "p", None, "active", 0.0, 0.0,
                 f"e{i}", 1, 0, _json.dumps({"band": "low", "risk_score": 0.1}), "{}"),
            )
        conn.commit()

    t1 = time.time()
    scene = build_scene(engine, cfg)
    build_s = time.time() - t1

    _assert_scene_contract(scene)
    assert len(scene["sessions"]["data"]) == 1000
    assert scene["agents"]["data"]["distinct_agent_count"] == 4
    # The projection is O(sessions) and must stay cheap.
    assert build_s < 5.0, f"scene build too slow at 1000 sessions: {build_s:.2f}s"


def test_scene_route_serves_the_document(client):
    """End-to-end: GET /api/v1/scene returns 200 and the contract document."""
    r = client.get("/api/v1/scene")
    assert r.status_code == 200, (r.status_code, r.text)
    _assert_scene_contract(r.json())


# Reuse the surface-test client fixture shape by building our own.
@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("JCC_CONTROL_TOKEN", "scene-test-token")
    engine, cfg = _make_engine(tmp_path)
    app = create_app(engine, cfg)
    return TestClient(app)
