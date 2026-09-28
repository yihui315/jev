"""Wave 8 — Visual gate for the v1.2 Visual Command Center.

Exercises the REAL v1.2 ASGI app (the same ``create_app`` the production server
binds on 4779) in-process via FastAPI's ``TestClient`` — NO port is bound, so
the protected GREEN 4778 (and any running 4779 instance) are never touched.

Checks:
  * PERF   — /api/v1/scene HTTP latency under budget; build_scene over 500
             sessions under budget (bulk-insert path, avoids the sandbox
             resource kill that 1000 separate ingest() calls trigger).
  * CONTRACT — /api/v1/scene returns 200 and the jcc-scene/1 contract; after
             live telemetry is ingested the shadow section is LIVE with real data.
  * SSE    — /api/v1/events/stream delivers >=1 typed frame (driven directly
             with a FakeRequest, because TestClient buffers streamed bodies).
  * SECURITY — the open read surface contains NO mutating route; /api/pause
             without a token is refused (401/403).
  * FALLBACK — the WebGL2->Table fallback source exists and detects webgl2.

Run:  python tools/v1_2_visual_gate.py
Exit: 0 = PASS, 1 = FAIL (a real gate, not a green-washer).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))

from fastapi.testclient import TestClient  # noqa: E402

from jcc_core.actuator import Actuator  # noqa: E402
from jcc_core.config import Config, ServerConfig  # noqa: E402
from jcc_core.core import CoreEngine  # noqa: E402
from jcc_core.events import EventBus  # noqa: E402
from jcc_core.providers import build_provider  # noqa: E402
from jcc_core.schemas import AEPEnvelope, AgentName, EventType  # noqa: E402
from jcc_core.scene import build_scene  # noqa: E402
from jcc_core.server import OPEN_GET_ENDPOINTS, create_app  # noqa: E402
from jcc_core.state_store import StateStore  # noqa: E402

# Performance budgets (generous but real; Wave 3 measured 1000 sessions in 0.30s).
SCENE_HTTP_BUDGET_S = 0.40
SCENE_BUILD_BUDGET_S = 1.20
N_PERF_SESSIONS = 500

APP_SRC = ROOT / "apps" / "command_center_3d" / "src" / "lib" / "webgl.ts"

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {msg}")
    if not cond:
        failures.append(msg)


class FakeRequest:
    """Minimal ASGI receive()-able request for driving the SSE generator."""

    def __init__(self, headers: dict | None = None):
        self.headers = headers or {}
        self._disc = asyncio.Event()

    def disconnect(self) -> None:
        self._disc.set()

    async def receive(self):
        await self._disc.wait()
        return {"type": "http.disconnect"}


def _make_engine(db_dir: Path) -> CoreEngine:
    db_path = str(db_dir / "visual_gate.db")
    cfg = Config(server=ServerConfig(), state_store_db_path=db_path)
    store = StateStore(db_path)
    return CoreEngine(store, build_provider(cfg), Actuator(enabled=False), cfg)


def _ingest_live(engine: CoreEngine, session_id: str, agent: AgentName, n: int) -> None:
    for i in range(n):
        engine.ingest(AEPEnvelope(
            event_id=f"{session_id}-{i}", delivery_id=f"dl-{session_id}-{i}",
            session_id=session_id, agent=agent, project="proj", cwd="/tmp",
            timestamp=time.time(), event_type=EventType.TOOL_CALL,
            payload={"n": i}, evidence={"ok": True}, record_source="live",
        ))


async def _collect_sse(engine: CoreEngine, timeout: float = 3.0) -> list[str]:
    from jcc_core.server import events_stream_generator

    fr = FakeRequest(headers={})
    frames: list[str] = []

    async def _run():
        async for chunk in events_stream_generator(fr, engine):
            frames.append(chunk)

    task = asyncio.ensure_future(_run())
    # Give the generator time to emit `: connected`, then publish a live event.
    await asyncio.sleep(0.1)
    if getattr(engine, "event_bus", None) is None:
        engine.event_bus = EventBus()
    engine.event_bus.publish(
        "agent.changed",
        {"session_id": "s1", "agent": "workbuddy", "note": "visual gate probe"},
        event_id="gate-evt-1",
    )
    await asyncio.sleep(0.2)
    fr.disconnect()
    try:
        await asyncio.wait_for(task, timeout=timeout)
    except asyncio.TimeoutError:
        task.cancel()
    return frames


def main() -> int:
    print("WAVE 8 — VISUAL GATE")
    with tempfile.TemporaryDirectory() as d:
        db_dir = Path(d)
        engine = _make_engine(db_dir)
        app = create_app(engine, engine.config)
        client = TestClient(app)

        # 1) Scene HTTP latency + contract (no live telemetry yet -> shadow UNKNOWN)
        t0 = time.time()
        r = client.get("/api/v1/scene")
        scene_http_s = time.time() - t0
        check(r.status_code == 200, f"/api/v1/scene returns 200 (got {r.status_code})")
        check(scene_http_s < SCENE_HTTP_BUDGET_S,
              f"scene HTTP latency {scene_http_s*1000:.1f}ms < {SCENE_HTTP_BUDGET_S*1000:.0f}ms")
        if r.status_code == 200:
            doc = r.json()
            check(doc.get("schema_version") == "jcc-scene/1", "scene schema is jcc-scene/1")
            check("shadow" in doc and doc["shadow"]["source"] == "UNKNOWN",
                  "shadow is UNKNOWN before live telemetry (honest gap)")

        # 2) Ingest live telemetry -> shadow becomes LIVE with real data
        for ag in (AgentName.WORKBUDDY, AgentName.CLAUDE, AgentName.CODEX, AgentName.HERMES):
            _ingest_live(engine, f"v8-{ag.value}", ag, 2)
        r2 = client.get("/api/v1/scene")
        check(r2.status_code == 200, "/api/v1/scene 200 after live ingest")
        if r2.status_code == 200:
            doc2 = r2.json()
            sh = doc2.get("shadow", {})
            check(sh.get("source") == "LIVE", "shadow LIVE after live telemetry")
            data = sh.get("data") or {}
            check(data.get("live_record_count", 0) == 8,
                  f"shadow counts 8 live records (got {data.get('live_record_count')})")
            check(data.get("distinct_agent_count") == 4, "shadow counts all 4 agents")
            check(data.get("record_source_filter") == "live", "shadow gate is LIVE_ONLY")

        # 3) SSE delivers >=1 typed frame
        try:
            frames = asyncio.run(_collect_sse(engine))
        except Exception as exc:  # noqa: BLE001
            frames = []
            print(f"  [WARN] SSE drive raised: {exc}")
        # Frames are rendered as "id: ...\nevent: <type>\ndata: ...\n\n" by
        # format_sse(), so a typed frame CONTAINS the "event:" line; it does not
        # START with it. Counting with startswith("event:") would be a
        # false-negative (the gate itself would lie about a real delivery).
        typed = [f for f in frames if "event:" in f]
        check(len(typed) >= 1, f"SSE delivered >=1 typed frame (got {len(typed)})")

        # 4) Security: open surface has no mutating route; /api/pause gated
        check("/api/pause" not in OPEN_GET_ENDPOINTS,
              "/api/pause is NOT on the open read surface")
        check("/api/v1/events/stream" in OPEN_GET_ENDPOINTS,
              "/api/v1/events/stream declared open (read-only projection)")
        rp = client.post("/api/pause", json={"session_id": "x"})
        check(rp.status_code in (401, 403),
              f"/api/pause without token refused ({rp.status_code})")

        # 5) WebGL2 fallback source present + detects webgl2
        check(APP_SRC.exists(), f"WebGL2 fallback source exists ({APP_SRC.name})")
        if APP_SRC.exists():
            src = APP_SRC.read_text(encoding="utf-8")
            check("webgl2" in src.lower(), "webgl.ts detects a webgl2 context")
            check("fallbackViewForWebGL2" in src, "webgl.ts exposes the fallback view")

        # 6) Performance budget: build_scene over N sessions
        perf_engine = _make_engine(Path(d) / "perf")
        agents = ["workbuddy", "claude", "codex", "hermes"]
        with perf_engine.store.connection() as conn:
            conn.execute("BEGIN")
            for i in range(N_PERF_SESSIONS):
                conn.execute(
                    "INSERT INTO sessions (session_id, agent, project, cwd, status, "
                    "created_at, updated_at, last_event_id, event_count, evidence_count, "
                    "current_assessment, current_directive) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f"perf{i}", agents[i % 4], "p", None, "active", 0.0, 0.0,
                     f"e{i}", 1, 0, json.dumps({"band": "low", "risk_score": 0.1}), "{}"),
                )
            conn.commit()
        t1 = time.time()
        build_scene(perf_engine, perf_engine.config)
        build_s = time.time() - t1
        check(build_s < SCENE_BUILD_BUDGET_S,
              f"build_scene({N_PERF_SESSIONS}) {build_s*1000:.1f}ms < "
              f"{SCENE_BUILD_BUDGET_S*1000:.0f}ms")

    print(f"\nVISUAL GATE: {'PASS' if not failures else 'FAIL'} "
          f"({len(failures)} failure(s))")
    for f in failures:
        print(f"  - {f}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
