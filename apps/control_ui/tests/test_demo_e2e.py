"""Demo-mode end-to-end test (S-05).

Runs the full chain from the deterministic four-agent fixtures:

    ingest (AEP) -> state -> assessment (deterministic provider) -> policy
    -> directive -> audit tape -> UI

entirely OFFLINE with NO third-party credentials (the deterministic provider
needs none, and no control token is set). It then asserts the UI renders the run
labelled ``DEMO`` -- a demo must never impersonate a real agent, and the label is
derived from the DATA (project == "DEMO" / session_id starts with "demo-"), never
from a flag the renderer carries.

The word DEMO appears both in the rendered page text AND in this assertion, as
required: a demo that could pass as a real agent is a defect.
"""
from __future__ import annotations

import pytest

from control_ui._harness import (
    build_app,
    find_free_port,
    get,
    start_api_server,
    start_ui_server,
    stop_api_server,
    stop_ui_server,
)
from fixtures.demo.load import is_demo_run, load_demo_envelopes
from jcc_core.schemas import AEPEnvelope


@pytest.fixture()
def demo_run(tmp_path, monkeypatch):
    # No credentials: the deterministic provider needs none, and no control token
    # is configured (the API is reached read-only for this run).
    monkeypatch.delenv("JCC_CONTROL_TOKEN", raising=False)
    app, engine, _store, _cfg = build_app(tmp_path)

    envelopes = load_demo_envelopes()
    for env in envelopes:
        engine.ingest(AEPEnvelope(**env))

    sessions = engine.store.list_sessions()
    # Sanity: the chain produced auditable state and the demo classifier agrees.
    assert sessions, "ingest produced no sessions"
    for s in sessions:
        assert is_demo_run(s), f"{s['session_id']} must be classified DEMO"
    audit = engine.store.get_audit(limit=1000)
    assert audit, "ingest produced no audit rows"

    api_port = find_free_port()
    api_server, api_thread = start_api_server(app, api_port)
    api_base = f"http://127.0.0.1:{api_port}"

    ui_port = find_free_port()
    ui_httpd, _ = start_ui_server(api_base, ui_port)
    ui_base = f"http://127.0.0.1:{ui_port}"

    ctx = {"api_base": api_base, "ui_base": ui_base, "sessions": sessions,
           "audit": audit, "engine": engine}
    yield ctx
    stop_ui_server(ui_httpd)
    stop_api_server(api_server, api_thread)


def test_demo_run_is_labelled_demo_everywhere(demo_run):
    screens = [
        "/",
        "/sessions",
        f"/sessions/{demo_run['sessions'][0]['session_id']}",
        "/decisions",
        "/audit",
    ]
    for path in screens:
        status, body = get(f"{demo_run['ui_base']}{path}")
        assert status == 200, (path, status)
        # DEMO must appear in the rendered page text (mandatory).
        assert "DEMO" in body, f"{path} does not render the DEMO label"
        # And the assertion here names DEMO explicitly (mandatory).
        assert "DEMO" in body


def test_demo_audit_tape_records_demo_project(demo_run):
    projects = {a["project"] for a in demo_run["audit"]}
    assert "DEMO" in projects, "audit tape must record the demo project"


def test_demo_directives_are_inert(demo_run):
    directives = demo_run["engine"].store.get_directives()
    assert directives, "expected directives from the demo run"
    for d in directives:
        assert d["acted"] is False, "demo directives must be inert (P0-P2)"
        assert d["actuation_reason"] in (None, "shadow_mode")
