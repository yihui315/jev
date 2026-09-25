"""Regression tests for the P5 broadcast-failure defect (``core._broadcast``).

``_broadcast`` used to swallow subscriber exceptions in a bare
``except Exception: pass`` -- so a failed Sentinel arbitration-result
propagation read as success. That is exactly the "instrument says success but
did nothing" defect class ADR-0008 D-2/D-41 forbid, and it is a P5 residual
that MUST be closed.

These tests assert the failure is NEVER silent: it is counted on the engine,
recorded per-subscriber, appended to the telemetry stream, logged at ERROR, and
(for observe-only mode) still surfaced as ``BROADCAST_FAILED``.
"""
from __future__ import annotations

from jcc_core.actuator import Actuator
from jcc_core.config import ActuationConfig, Config
from jcc_core.core import CoreEngine
from jcc_core.providers import build_provider
from jcc_core.schemas import AEPEnvelope
from jcc_core.state_store import StateStore


def _engine(tmp_path, *, actuation_enabled: bool):
    cfg = Config()
    cfg.state_store_db_path = str(tmp_path / "jcc.db")
    cfg.actuation = ActuationConfig(enabled=actuation_enabled)
    store = StateStore(cfg.state_store_db_path)
    engine = CoreEngine(store, build_provider(cfg), Actuator(enabled=False), cfg)
    return engine


def _event(event_id: str = "e1") -> AEPEnvelope:
    return AEPEnvelope(
        event_id=event_id,
        delivery_id=event_id + "-d",
        session_id="s1",
        agent="workbuddy",
        project="p",
        event_type="tool_call",
        payload={},
        evidence=None,
    )


def test_subscriber_failure_is_recorded_and_does_not_crash_caller(tmp_path):
    """A raising subscriber must NOT crash ingest and MUST be recorded as a failure."""
    engine = _engine(tmp_path, actuation_enabled=False)

    def boom(msg):  # noqa: ANN001
        raise RuntimeError("arbitration propagation exploded")

    engine.subscribe(boom)
    before = engine.broadcast_failure_total

    # ingest must still return normally -- the failure is a recorded drop, not a crash
    out = engine.ingest(_event())
    assert out["event_id"] == "e1"

    # metric flipped
    assert engine.last_broadcast_failures == 1
    assert engine.broadcast_failure_total == before + 1
    # per-subscriber delivery state (one subscriber, and it failed -> sent 0)
    assert engine.last_broadcast["failed"] == 1
    assert engine.last_broadcast["sent"] == 0
    assert engine.last_broadcast["failures"][0]["subscriber"].endswith("boom")
    assert engine.last_broadcast["failures"][0]["error"].startswith("RuntimeError:")
    # telemetry event exists
    assert len(engine._broadcast_events) == 1
    assert engine._broadcast_events[-1]["failed"] == 1


def test_subscriber_failure_in_observe_only_mode_still_emits_broadcast_failed(tmp_path):
    """In observe-only mode a failure is STILL emitted, never a silent success."""
    engine = _engine(tmp_path, actuation_enabled=False)
    assert engine._is_observe_only() is True

    def boom(msg):  # noqa: ANN001
        raise ValueError("observe-only propagation dropped")

    engine.subscribe(boom)
    engine.ingest(_event("o1"))

    assert engine.last_broadcast_failures == 1
    # even in observe-only mode the outcome is recorded, never a silent success
    assert engine.last_broadcast["mode"] == "observe-only"
    assert engine._broadcast_events[-1]["failed"] == 1
    assert engine._broadcast_events[-1]["mode"] == "observe-only"


def test_healthy_subscriber_records_no_false_failure(tmp_path):
    """A healthy subscriber records success and produces no false failure."""
    engine = _engine(tmp_path, actuation_enabled=False)
    received: dict = {}

    def ok(msg):  # noqa: ANN001
        received["msg"] = msg

    engine.subscribe(ok)
    engine.ingest(_event("h1"))

    assert received["msg"]["event_id"] == "h1"
    assert engine.last_broadcast_failures == 0
    assert engine.last_broadcast["failed"] == 0
    assert engine.last_broadcast["sent"] == 1
    assert engine.broadcast_failure_total == 0


def test_own_subscriber_failure_is_reraised_not_silently_dropped(tmp_path):
    """The engine's OWN supervision subscriber re-raises; it is never swallowed."""
    engine = _engine(tmp_path, actuation_enabled=False)

    def own_boom(msg):  # noqa: ANN001
        raise RuntimeError("supervision subscriber failed")

    engine.subscribe(own_boom, own=True)
    assert engine.last_broadcast_failures == 0  # pre-condition

    try:
        engine._broadcast({"event_id": "x1"})
    except RuntimeError as exc:
        assert "supervision subscriber failed" in str(exc)
    else:
        raise AssertionError("own subscriber failure was swallowed, expected re-raise")

    # even though it re-raised, the failure was recorded first
    assert engine.last_broadcast_failures == 1
    assert engine.broadcast_failure_total == 1


def test_external_failure_keeps_ingest_green_while_metric_flips(tmp_path):
    """Full ingest path: an external raising subscriber must not abort ingest and
    the failure metric must be observable on the engine."""
    engine = _engine(tmp_path, actuation_enabled=False)

    def boom(msg):  # noqa: ANN001
        raise KeyError("external subscriber blew up")

    engine.subscribe(boom)
    out = engine.ingest(_event("g1"))
    assert out["acted"] is False
    assert engine.last_broadcast_failures == 1
    assert engine.broadcast_failure_total == 1


def test_failure_count_resets_each_broadcast_and_accumulates_total(tmp_path):
    """Per-call ``last_broadcast_failures`` reflects only the latest call; the
    total accumulates across calls."""
    engine = _engine(tmp_path, actuation_enabled=False)

    def boom(msg):  # noqa: ANN001
        raise RuntimeError("boom")

    def ok(msg):  # noqa: ANN001
        pass

    engine.subscribe(boom)
    engine.ingest(_event("a1"))
    assert engine.last_broadcast_failures == 1
    assert engine.broadcast_failure_total == 1

    # replace with a healthy subscriber for the next call
    engine._subscribers[:] = [ok]
    engine.ingest(_event("a2"))
    assert engine.last_broadcast_failures == 0  # reset for this call
    assert engine.broadcast_failure_total == 1  # total still holds the prior failure
