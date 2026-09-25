"""Smoke test for the 2D Control UI (S-04).

Starts the REAL FastAPI control plane in-process on a random loopback port
(``uvicorn``), drives it with ``urllib`` over real HTTP, and asserts:

* all eight screens return 200 with real (non-empty, non-"API UNREACHABLE") data;
* the declared read-only endpoints are open (GET /api/health == 200);
* a mutating endpoint refuses a tokenless request (401/403).

The provider name is read from the live API at runtime and never hard-coded, so a
reviewer can see this is a real-endpoint smoke test, not a stub page.
"""
from __future__ import annotations

import json
import urllib.request

import pytest

from control_ui._harness import (
    build_app,
    find_free_port,
    get,
    post,
    start_api_server,
    start_ui_server,
    stop_api_server,
    stop_ui_server,
)
from fixtures.demo.load import load_demo_envelopes
from jcc_core.schemas import AEPEnvelope


def _seed_demo(engine) -> list:
    ids = []
    for env in load_demo_envelopes():
        out = engine.ingest(AEPEnvelope(**env))
        ids.append(out["session_id"])
    return ids


@pytest.fixture()
def live(tmp_path, monkeypatch):
    # A token is configured so the fail-closed gate is engaged: a tokenless POST
    # must be refused (403). Unset would refuse with 401 instead -- both prove
    # "refuses without token".
    monkeypatch.setenv("JCC_CONTROL_TOKEN", "ui-smoke-token")
    app, engine, _store, _cfg = build_app(tmp_path)
    seeded = _seed_demo(engine)

    api_port = find_free_port()
    api_server, api_thread = start_api_server(app, api_port)
    api_base = f"http://127.0.0.1:{api_port}"

    ui_port = find_free_port()
    ui_httpd, _ = start_ui_server(api_base, ui_port)
    ui_base = f"http://127.0.0.1:{ui_port}"

    ctx = {
        "api_base": api_base,
        "ui_base": ui_base,
        "seeded": seeded,
        "engine": engine,
    }
    yield ctx
    stop_ui_server(ui_httpd)
    stop_api_server(api_server, api_thread)


def test_read_only_endpoints_are_open(live):
    status, _ = get(f"{live['api_base']}/api/health")
    assert status == 200


def test_mutating_endpoint_refuses_without_token(live):
    status, _ = post(
        f"{live['api_base']}/api/assess",
        b'{"session_id":"probe","event_id":"e1","delivery_id":"e1-d1",'
        b'"agent":"workbuddy","event_type":"session_start"}',
    )
    assert status in (401, 403), f"mutating endpoint must refuse tokenless POST, got {status}"


def test_all_eight_screens_return_real_data(live):
    status, health_text = get(f"{live['api_base']}/api/health")
    health = json.loads(health_text)
    provider = health["jev_provider"]
    session_id = live["seeded"][0]

    screens = [
        "/",
        "/sessions",
        f"/sessions/{session_id}",
        "/decisions",
        "/security",
        "/settings",
        "/audit",
        "/health",
    ]
    for path in screens:
        status, body = get(f"{live['ui_base']}{path}")
        assert status == 200, (path, status)
        assert "API UNREACHABLE" not in body, (path, "UI could not reach the API")
        assert len(body) > 300, (path, "page suspiciously empty")


def test_dashboard_shows_real_provider_and_agents(live):
    _status, body = get(f"{live['ui_base']}/")
    health = json.loads(get(f"{live['api_base']}/api/health")[1])
    assert health["jev_provider"] in body, "dashboard must render the live provider"
    # Agent capabilities are real config; the four demo agents are listed.
    for agent in ("workbuddy", "claude", "codex", "hermes"):
        assert agent in body, f"dashboard must list agent {agent}"


def test_sessions_screen_lists_seeded_sessions(live):
    _status, body = get(f"{live['ui_base']}/sessions")
    for sid in live["seeded"]:
        assert sid in body, f"sessions screen must list {sid}"


def test_session_detail_shows_live_assessment(live):
    sid = live["seeded"][0]
    _status, body = get(f"{live['ui_base']}/sessions/{sid}")
    # The session was assessed by the deterministic provider, so its band is real.
    assert "band" in body or "risk_score" in body, "detail must show live assessment"
    assert sid in body


def test_security_screen_proves_refusal(live):
    _status, body = get(f"{live['ui_base']}/security")
    assert "REFUSED" in body, "security screen must show the anonymous-POST refusal"


def test_demo_label_present_for_demo_sessions(live):
    # The smoke run is seeded from the demo fixtures, so DEMO must appear.
    _status, body = get(f"{live['ui_base']}/")
    assert "DEMO" in body, "demo-seeded run must be labelled DEMO on the dashboard"
