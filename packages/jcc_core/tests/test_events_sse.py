"""Wave 4 — Event bus + SSE contract tests.

Covers the three hard properties of the live stream:
  * the ten typed events are emitted with proper SSE framing (id/event/data);
  * ``Last-Event-ID`` resume replays only events AFTER the id (no duplicates);
  * re-publishing the same ``event_id`` is deduped (idempotent upstream retry);
  * a live event published AFTER connect is delivered without a refresh.

NOTE: Starlette's ``TestClient`` buffers the entire streamed body and never
delivers it incrementally, so the SSE route is driven directly as an async
generator (``FakeRequest``) instead of over a socket. The endpoint itself is
byte-identical to what a real ASGI server streams.
"""
from __future__ import annotations

import asyncio
import json

from jcc_core.actuator import Actuator
from jcc_core.config import Config, ServerConfig
from jcc_core.core import CoreEngine
from jcc_core.events import EVENT_TYPES, EventBus, format_sse
from jcc_core.providers import build_provider
from jcc_core.server import create_app, events_stream_generator
from jcc_core.state_store import StateStore

ALL_TYPES = sorted(EVENT_TYPES)


def _build(tmp_path):
    db_path = str(tmp_path / "sse.db")
    cfg = Config(server=ServerConfig(), state_store_db_path=db_path)
    store = StateStore(db_path)
    engine = CoreEngine(store, build_provider(cfg), Actuator(enabled=False), cfg)
    app = create_app(engine, cfg)
    return engine, cfg, app


class FakeRequest:
    """Minimal stand-in for a Starlette Request that drives the SSE generator.

    ``receive()`` blocks until ``disconnect()`` is called, mirroring a real
    client going away.
    """

    def __init__(self, last_id=None):
        self.headers = {"Last-Event-ID": last_id} if last_id else {}
        self._recv_fut = None

    async def receive(self):
        loop = asyncio.get_event_loop()
        self._recv_fut = loop.create_future()
        await self._recv_fut
        return {"type": "http.disconnect"}

    def disconnect(self):
        if self._recv_fut is not None and not self._recv_fut.done():
            self._recv_fut.set_result({"type": "http.disconnect"})


def _parse_sse(chunks):
    frames = []
    cur = {}
    for raw in chunks:
        text = raw if isinstance(raw, str) else raw.decode("utf-8")
        for ln in text.split("\n"):
            if ln == "":
                if cur:
                    frames.append(cur)
                cur = {}
                continue
            if ln.startswith(":"):
                cur.setdefault("comments", []).append(ln)
                continue
            if ln.startswith("id: "):
                cur["id"] = ln[4:]
            elif ln.startswith("event: "):
                cur["event"] = ln[7:]
            elif ln.startswith("data: "):
                cur["data"] = ln[6:]
    if cur:
        frames.append(cur)
    return frames


async def _drive(bus, engine, last_id, live_publish):
    """Drive the SSE generator: collect replay+connected, emit live events,
    then disconnect and drain."""
    fr = FakeRequest(last_id)
    gen = events_stream_generator(fr, engine)
    chunks = []
    # replay + initial keep-alive comment
    while True:
        try:
            c = await asyncio.wait_for(gen.__anext__(), timeout=3.0)
        except (StopAsyncIteration, asyncio.TimeoutError):
            break
        chunks.append(c)
        if c.strip() == ": connected":
            break
    # live events published after connect
    for etype, data, eid in live_publish:
        bus.publish(etype, data, event_id=eid)
        try:
            c = await asyncio.wait_for(gen.__anext__(), timeout=3.0)
        except (StopAsyncIteration, asyncio.TimeoutError):
            break
        chunks.append(c)
    # simulate client disconnect -> generator must terminate
    fr.disconnect()
    try:
        while True:
            c = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
            chunks.append(c)
    except (StopAsyncIteration, asyncio.TimeoutError):
        pass
    return chunks


# --------------------------------------------------------------------------- #
# Unit-level (bus) tests
# --------------------------------------------------------------------------- #
def test_eventbus_dedupe_same_event_id():
    bus = EventBus()
    a = bus.publish("audit.appended", {"x": 1}, event_id="dup")
    b = bus.publish("audit.appended", {"x": 2}, event_id="dup")
    assert a is not None
    assert b is None, "duplicate event_id must be a no-op"
    assert sum(1 for ev in bus._ring if ev["event_id"] == "dup") == 1


def test_eventbus_replay_since():
    bus = EventBus()
    for i in range(5):
        bus.publish("audit.appended", {"i": i}, event_id=f"id{i}")
    assert len(bus.replay_since(None)) == 5
    after = bus.replay_since("id2")
    # range(5) -> id0..id4; replay returns events strictly AFTER id2.
    assert [ev["event_id"] for ev in after] == ["id3", "id4"]
    assert len(bus.replay_since("nope")) == 5


def test_eventbus_rejects_unknown_type():
    bus = EventBus()
    try:
        bus.publish("not.a.real.type", {})
    except ValueError:
        pass
    else:
        raise AssertionError("unknown event type should be refused")


def test_feed_engine_out_emits_the_eight_live_types():
    bus = EventBus()
    out = {
        "session_id": "s1",
        "event_id": "e1",
        "action": "VERIFY",
        "assessment": {"agent": "workbuddy", "band": "high", "risk_score": 0.9,
                       "escalate": True, "provider_verified": False},
        "directive": {"directive_id": "dir_e1", "policy_version": "p3.0.0", "signed": False},
        "evidence_hash": "abc",
        "acted": False,
        "actuation_reason": "shadow_mode",
        "session_status": "active",
    }
    bus.feed_engine_out(out)
    types = {ev["type"] for ev in bus._ring}
    for t in ("session.created", "assessment.created", "directive.created",
              "audit.appended", "budget.changed", "shadow.changed", "agent.changed"):
        assert t in types
    bus.feed_engine_out({**out, "event_id": "e2"})
    assert "session.updated" in {ev["type"] for ev in bus._ring}


def test_format_sse_roundtrip():
    s = format_sse({"type": "agent.changed", "data": {"agent": "claude"}, "event_id": "x1"})
    assert s.startswith("id: x1\n")
    assert "event: agent.changed" in s
    assert '"agent": "claude"' in s
    assert s.endswith("\n\n")


# --------------------------------------------------------------------------- #
# Streaming (generator-driven) tests
# --------------------------------------------------------------------------- #
def test_sse_stream_contains_all_ten_types(tmp_path):
    engine, _cfg, _app = _build(tmp_path)
    bus = engine.event_bus
    for t in ALL_TYPES:
        bus.publish(t, {"marker": t}, event_id=f"ev-{t}")

    chunks = asyncio.run(_drive(bus, engine, None, []))
    frames = _parse_sse(chunks)
    seen = set()
    for f in frames:
        if "event" not in f:
            continue
        seen.add(f["event"])
        assert f.get("id"), "every event frame needs id (Last-Event-ID)"
        assert f.get("data"), "every event frame needs data"
        json.loads(f["data"])
    assert seen == set(ALL_TYPES), seen
    assert any(
        c.strip() == ": connected"
        for c in chunks
        if isinstance(c, str)
    )


def test_sse_resume_respects_last_event_id(tmp_path):
    engine, _cfg, _app = _build(tmp_path)
    bus = engine.event_bus
    for i in range(5):
        bus.publish("audit.appended", {"i": i}, event_id=f"rid{i}")

    chunks = asyncio.run(_drive(bus, engine, "rid2", []))
    frames = _parse_sse(chunks)
    replayed = [f["id"] for f in frames if f.get("event")]
    assert replayed == ["rid3", "rid4"], replayed


def test_sse_dedupe_over_the_wire(tmp_path):
    engine, _cfg, _app = _build(tmp_path)
    bus = engine.event_bus
    bus.publish("audit.appended", {"x": 1}, event_id="same")
    bus.publish("audit.appended", {"x": 2}, event_id="same")  # deduped

    chunks = asyncio.run(_drive(bus, engine, None, []))
    frames = _parse_sse(chunks)
    event_frames = [f for f in frames if f.get("event")]
    assert len(event_frames) == 1, "duplicate event_id must yield one frame"
    assert event_frames[0]["id"] == "same"


def test_sse_live_delivery_after_connect(tmp_path):
    engine, _cfg, _app = _build(tmp_path)
    bus = engine.event_bus
    bus.publish("audit.appended", {"seed": 1}, event_id="seed1")

    chunks = asyncio.run(_drive(
        bus, engine, None, [("assessment.created", {"live": True}, "live1")]
    ))
    frames = _parse_sse(chunks)
    ids = [f["id"] for f in frames if f.get("event")]
    assert "seed1" in ids
    assert "live1" in ids, "event published after connect must be delivered live"
