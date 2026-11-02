"""Wave 3 API tests — the mission launch bridge over REAL HTTP (owner: Metric).

Runs against the in-process ``api_server`` fixture (a real uvicorn app with an
isolated temp DB), so it measures the deployed surface. Two properties matter:

  * a launch with no real event is REFUSED (never fabricated);
  * a launch that says ``ok`` also says ``acted: false`` -- the product never
    reports a live mission and an actuation in the same breath.
"""
from __future__ import annotations

import http.client
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from conftest import E2E_CONTROL_TOKEN  # noqa: E402

TOK = E2E_CONTROL_TOKEN


def _get(api_server, path):
    conn = http.client.HTTPConnection("127.0.0.1", api_server.port, timeout=15)
    conn.request("GET", path)
    r = conn.getresponse()
    body = r.read().decode("utf-8")
    conn.close()
    try:
        return r.status, json.loads(body)
    except ValueError:
        return r.status, body


def _post(api_server, path, body, token):
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["X-JCC-Token"] = token
    conn = http.client.HTTPConnection("127.0.0.1", api_server.port, timeout=20)
    conn.request("POST", path, json.dumps(body), headers)
    r = conn.getresponse()
    raw = r.read().decode("utf-8")
    conn.close()
    try:
        return r.status, json.loads(raw)
    except ValueError:
        return r.status, raw


def _mission(api_server, agent="workbuddy"):
    _, proj = _post(api_server, "/api/v1/domain/projects", {"name": "W3"}, TOK)
    _, miss = _post(api_server, "/api/v1/domain/missions",
                    {"project_id": proj["project_id"], "title": "M", "agent": agent}, TOK)
    return miss["mission_id"]


# --- probe is open and honest -------------------------------------------------

def test_launch_probe_is_open(api_server):
    st, doc = _get(api_server, "/api/v1/domain/launch/probe")
    assert st == 200, doc
    assert doc["schema"] == "jcc/v1.3-launch-probe/1"
    assert doc["actuation"] is False
    assert "workbuddy" in doc
    assert "workbuddy" in doc["launchable_agents"]


def test_launch_probe_reports_observed_sessions(api_server):
    _, doc = _get(api_server, "/api/v1/domain/launch/probe")
    wb = doc["workbuddy"]
    # The probe must always state a count, even zero -- never omit the field.
    assert isinstance(wb["session_count"], int)
    assert isinstance(wb["observed"], list)
    if wb["observed"]:
        for o in wb["observed"]:
            assert o["state"] in ("live", "stale", "dead")
            assert isinstance(o["reason"], str) and o["reason"]


# --- fail-closed --------------------------------------------------------------

def test_launch_requires_token(api_server):
    mid = _mission(api_server)
    st, _ = _post(api_server, "/api/v1/domain/missions/%s/launch" % mid, {}, None)
    assert st in (401, 403)


def test_launch_without_event_is_refused_not_faked(api_server):
    """capture=false and no event must yield ok=false, not a fabricated launch."""
    mid = _mission(api_server)
    st, doc = _post(api_server, "/api/v1/domain/missions/%s/launch" % mid,
                    {"capture": False}, TOK)
    assert st == 200, doc
    assert doc["ok"] is False
    assert doc["ingested"] is False
    assert doc["run_id"] is None
    # And the mission was not advanced.
    _, m = _get(api_server, "/api/v1/domain/missions/%s" % mid)
    assert m["status"] == "DRAFT"


def test_unlaunchable_agent_is_refused(api_server):
    mid = _mission(api_server, agent="claude")
    st, doc = _post(api_server, "/api/v1/domain/missions/%s/launch" % mid,
                    {"capture": False, "event": {"some": "event"}}, TOK)
    assert st == 422, doc


def test_launch_unknown_mission_is_404(api_server):
    st, _ = _post(api_server, "/api/v1/domain/missions/msn_nope/launch",
                  {"capture": False}, TOK)
    assert st == 404


# --- workload ----------------------------------------------------------------

def test_workload_of_unlaunched_mission_is_empty(api_server):
    mid = _mission(api_server)
    st, doc = _get(api_server, "/api/v1/domain/missions/%s/workload" % mid)
    assert st == 200, doc
    assert doc["run_count"] == 0
    assert doc["live_session_count"] == 0
    assert doc["actuation"] is False


def test_workload_unknown_mission_is_404(api_server):
    st, _ = _get(api_server, "/api/v1/domain/missions/msn_nope/workload")
    assert st == 404


# --- the real launch (only when the host has a live WorkBuddy) ----------------

def _live_session_exists() -> bool:
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "packages"))
        from jcc_core.workbuddy_launch import capture_launch_event
        return bool(capture_launch_event().get("ok"))
    except Exception:  # noqa: BLE001
        return False


def test_real_launch_produces_real_telemetry(api_server):
    """End-to-end: launch captures a real session and ingests real AEP."""
    if not _live_session_exists():
        import pytest
        pytest.skip("no live WorkBuddy session on this host")

    mid = _mission(api_server)
    st, doc = _post(api_server, "/api/v1/domain/missions/%s/launch" % mid,
                    {"capture": True, "cwd": r"C:\some\checkout"}, TOK)
    assert st == 200, doc
    assert doc["ok"] is True, doc
    assert doc["ingested"] is True
    assert doc["ingest"]["acted"] is False, "a launch must never actuate"

    _, m = _get(api_server, "/api/v1/domain/missions/%s" % mid)
    assert m["status"] == "RUNNING"
    assert m["session_id"] == doc["session_id"]

    _, wl = _get(api_server, "/api/v1/domain/missions/%s/workload" % mid)
    assert wl["live_session_count"] >= 1
    assert wl["actuation"] is False


def test_launch_routes_are_declared_open_where_they_are_get(api_server):
    """The two new GET routes must be in the declared open surface."""
    _, health = _get(api_server, "/api/health")
    declared = set(health["open_get_endpoints"])
    assert "/api/v1/domain/launch/probe" in declared
    assert "/api/v1/domain/missions/{mid}/workload" in declared
