"""Wave 7 — REAL LIVE SHADOW gate contract tests (schema ``jcc-scene/1``).

These prove the Jev shadow surface (``engine.shadow_summary``) is gated to
``record_source == 'live'`` telemetry ONLY:

  * with NO live telemetry the shadow section is UNKNOWN (honest gap, data null);
  * with live telemetry from the four agents it is LIVE and counts every agent;
  * a ``demo`` / ``replay`` record is EXCLUDED from the gate (the count never
    includes it) — the D-34 "fake-clean" class is closed by construction.

The test builds envelopes directly (no external agent contact) so it is fast and
deterministic; the four-agent *real-format* telemetry path is exercised end to
end by ``tools/wave7_live_shadow.py``.
"""
from __future__ import annotations

import time

from jcc_core.actuator import Actuator
from jcc_core.config import Config, ServerConfig
from jcc_core.core import CoreEngine
from jcc_core.providers import build_provider
from jcc_core.schemas import AEPEnvelope, AgentName, EventType
from jcc_core.scene import build_scene
from jcc_core.state_store import StateStore


def _make_engine(tmp_path):
    db_path = str(tmp_path / "shadow.db")
    cfg = Config(server=ServerConfig(), state_store_db_path=db_path)
    store = StateStore(db_path)
    engine = CoreEngine(store, build_provider(cfg), Actuator(enabled=False), cfg)
    return engine, cfg


def _ingest(engine, session_id, agent, seq, *, record_source: str = "live",
            event_type: EventType = EventType.TOOL_CALL):
    engine.ingest(
        AEPEnvelope(
            event_id=f"e{seq}",
            delivery_id=f"d{seq}",
            session_id=session_id,
            agent=agent,
            project="proj",
            cwd="/tmp",
            timestamp=time.time(),
            event_type=event_type,
            payload={"n": seq},
            evidence={"ok": True},
            schema_ok=True,
            record_source=record_source,
        )
    )


def test_shadow_summary_none_without_live():
    """No live telemetry -> the gate has nothing to supervise (honest gap)."""
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as d:
        eng, _ = _make_engine(pathlib.Path(d))
        assert eng.shadow_summary() is None


def test_shadow_unknown_in_scene_without_live():
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as d:
        eng, cfg = _make_engine(pathlib.Path(d))
        scene = build_scene(eng, cfg)
        assert scene["shadow"]["source"] == "UNKNOWN"
        assert scene["shadow"]["data"] is None


def test_shadow_live_counts_four_agents():
    """Live telemetry from all four agents -> LIVE, every agent counted."""
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as d:
        eng, cfg = _make_engine(pathlib.Path(d))
        agents = [AgentName.WORKBUDDY, AgentName.CLAUDE, AgentName.CODEX, AgentName.HERMES]
        for i, a in enumerate(agents):
            _ingest(eng, f"sess-{a.value}", a, i)
        summary = eng.shadow_summary()
        assert summary is not None
        assert summary["gate"] == "LIVE_ONLY"
        assert summary["record_source_filter"] == "live"
        assert summary["live_record_count"] == 4
        assert summary["distinct_agent_count"] == 4
        counted = {a["agent"] for a in summary["agents"]}
        assert counted == {a.value for a in agents}
        # scene reflects it as LIVE with real data (not coerced)
        scene = build_scene(eng, cfg)
        assert scene["shadow"]["source"] == "LIVE"
        assert isinstance(scene["shadow"]["data"], dict)
        assert scene["shadow"]["data"]["live_record_count"] == 4


def test_shadow_gate_excludes_non_live():
    """A demo record must NOT be counted by the live-only shadow gate.

    This is the falsifier for the D-34 fake-clean class: if the gate leaked
    non-live records, ``live_record_count`` would be 2 here.
    """
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as d:
        eng, _ = _make_engine(pathlib.Path(d))
        _ingest(eng, "live-1", AgentName.WORKBUDDY, 1, record_source="live")
        _ingest(eng, "demo-1", AgentName.CLAUDE, 2, record_source="demo")
        _ingest(eng, "replay-1", AgentName.CODEX, 3, record_source="replay")
        summary = eng.shadow_summary()
        assert summary is not None
        assert summary["live_record_count"] == 1, summary
        assert summary["distinct_agent_count"] == 1
        assert {a["agent"] for a in summary["agents"]} == {"workbuddy"}
        # excluded sources are declared explicitly, never silently folded in
        assert "demo" in summary["excluded_record_sources"]
        assert "replay" in summary["excluded_record_sources"]


def test_shadow_only_demo_is_unknown():
    """When every record is non-live, the shadow is UNKNOWN (nothing to supervise)."""
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as d:
        eng, _ = _make_engine(pathlib.Path(d))
        _ingest(eng, "demo-1", AgentName.HERMES, 1, record_source="demo")
        assert eng.shadow_summary() is None
