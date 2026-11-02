"""Wave 1 API tests -- the Project/Mission domain over REAL HTTP (owner: Metric).

These run against the in-process ``api_server`` fixture (a real uvicorn app on a
random loopback port with an isolated temp DB), so they measure the deployed
surface, not a mock. Two properties get falsifiers because they are the ones the
v1.3 order calls out:

  * reads are open (GET), writes are token-gated (POST) -- fail-closed;
  * the Mission state machine is enforced by the SERVER, so a client cannot
    invent a transition the domain refuses.
"""
from __future__ import annotations

import http.client
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from conftest import E2E_CONTROL_TOKEN  # noqa: E402


def _get(api_server, path: str):
    conn = http.client.HTTPConnection("127.0.0.1", api_server.port, timeout=10)
    conn.request("GET", path)
    r = conn.getresponse()
    body = r.read().decode("utf-8")
    conn.close()
    try:
        return r.status, json.loads(body)
    except ValueError:
        return r.status, body


def _post(api_server, path: str, body: dict, token: str | None):
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["X-JCC-Token"] = token
    conn = http.client.HTTPConnection("127.0.0.1", api_server.port, timeout=10)
    conn.request("POST", path, json.dumps(body), headers)
    r = conn.getresponse()
    raw = r.read().decode("utf-8")
    conn.close()
    try:
        return r.status, json.loads(raw)
    except ValueError:
        return r.status, raw


TOK = E2E_CONTROL_TOKEN


# --- open read surface -------------------------------------------------------

def test_home_is_open_and_honest(api_server):
    st, doc = _get(api_server, "/api/v1/domain/home")
    assert st == 200, doc
    assert doc["schema"] == "jcc/v1.3-domain-home/1"
    for k in ("active_projects", "running_missions", "needs_attention",
              "completed_missions", "recent_deliverables"):
        assert k in doc
    # honest constants must be present and correct
    assert doc["actuation_enabled"] is False
    assert doc["localhost_only"] is True


def test_empty_domain_reads_are_lists(api_server):
    for path in ("/api/v1/domain/projects", "/api/v1/domain/missions",
                 "/api/v1/domain/connections", "/api/v1/domain/needs-attention"):
        st, doc = _get(api_server, path)
        assert st == 200, (path, doc)
        assert isinstance(doc, list)


def test_unknown_project_and_mission_are_404(api_server):
    assert _get(api_server, "/api/v1/domain/projects/prj_nope")[0] == 404
    assert _get(api_server, "/api/v1/domain/missions/msn_nope")[0] == 404
    assert _get(api_server, "/api/v1/domain/missions/msn_nope/timeline")[0] == 404
    assert _get(api_server, "/api/v1/domain/missions/msn_nope/runs")[0] == 404


# --- token gate --------------------------------------------------------------

def test_create_project_requires_token(api_server):
    assert _post(api_server, "/api/v1/domain/projects", {"name": "X"}, None)[0] == 403
    assert _post(api_server, "/api/v1/domain/projects", {"name": "X"}, "wrong")[0] == 403
    assert _post(api_server, "/api/v1/domain/projects", {"name": "X"}, TOK)[0] == 200


def test_every_domain_write_is_token_gated(api_server):
    """A write attempted without a token must be refused, for every route."""
    writes = [
        ("/api/v1/domain/projects", {"name": "X"}),
        ("/api/v1/domain/missions", {"project_id": "prj_x", "title": "t"}),
        ("/api/v1/domain/missions/msn_x/transition", {"to": "READY"}),
        ("/api/v1/domain/missions/msn_x/runs", {"agent": "workbuddy"}),
        ("/api/v1/domain/missions/msn_x/artifacts", {"name": "a"}),
        ("/api/v1/domain/connections", {"agent": "claude"}),
        ("/api/v1/domain/connections/con_x/capability", {"kind": "UNKNOWN"}),
        ("/api/v1/domain/missions/msn_x/approvals", {"kind": "X"}),
        ("/api/v1/domain/approvals/apr_x/decide",
         {"state": "APPROVED", "decided_by": "op"}),
    ]
    for path, body in writes:
        st, _ = _post(api_server, path, body, None)
        assert st in (401, 403), (path, st)


# --- full mission lifecycle over HTTP ---------------------------------------

def test_full_project_mission_lifecycle(api_server):
    st, proj = _post(api_server, "/api/v1/domain/projects",
                     {"name": "Alpha", "root_path": r"C:\some\checkout"}, TOK)
    assert st == 200, proj
    pid = proj["project_id"]

    st, miss = _post(api_server, "/api/v1/domain/missions",
                     {"project_id": pid, "title": "Ship v1.3",
                      "goal": "live mission", "agent": "workbuddy",
                      "permissions": ["read", "write"]}, TOK)
    assert st == 200, miss
    mid = miss["mission_id"]
    assert miss["status"] == "DRAFT"

    # walk the happy path
    for to in ("READY", "STARTING", "RUNNING", "VERIFYING", "COMPLETED"):
        st, doc = _post(api_server, "/api/v1/domain/missions/%s/transition" % mid,
                        {"to": to}, TOK)
        assert st == 200, (to, doc)
        assert doc["status"] == to

    st, tl = _get(api_server, "/api/v1/domain/missions/%s/timeline" % mid)
    assert st == 200
    assert [t["to_status"] for t in tl] == [
        "DRAFT", "READY", "STARTING", "RUNNING", "VERIFYING", "COMPLETED"]

    # an artifact produces a deliverable
    st, art = _post(api_server, "/api/v1/domain/missions/%s/artifacts" % mid,
                    {"name": "report.md", "kind": "report",
                     "sha256": "a" * 64, "bytes": 128}, TOK)
    assert st == 200, art
    assert art["sha256"] == "a" * 64

    st, arts = _get(api_server, "/api/v1/domain/missions/%s/artifacts" % mid)
    assert st == 200 and len(arts) == 1

    # home reflects the completed mission + deliverable
    st, home = _get(api_server, "/api/v1/domain/home")
    assert home["completed_missions"] == 1
    assert home["recent_deliverables"] == 1
    assert home["running_missions"] == 0


def test_server_refuses_an_illegal_transition(api_server):
    """DRAFT -> RUNNING must be refused BY THE SERVER, not just the model."""
    _, proj = _post(api_server, "/api/v1/domain/projects", {"name": "P"}, TOK)
    _, miss = _post(api_server, "/api/v1/domain/missions",
                    {"project_id": proj["project_id"], "title": "M"}, TOK)
    mid = miss["mission_id"]
    st, doc = _post(api_server, "/api/v1/domain/missions/%s/transition" % mid,
                    {"to": "RUNNING"}, TOK)
    assert st == 422, doc
    # and the mission must be untouched
    st, cur = _get(api_server, "/api/v1/domain/missions/%s" % mid)
    assert cur["status"] == "DRAFT"


def test_transition_to_unknown_state_is_422(api_server):
    _, proj = _post(api_server, "/api/v1/domain/projects", {"name": "P"}, TOK)
    _, miss = _post(api_server, "/api/v1/domain/missions",
                    {"project_id": proj["project_id"], "title": "M"}, TOK)
    st, doc = _post(api_server,
                    "/api/v1/domain/missions/%s/transition" % miss["mission_id"],
                    {"to": "TELEPORTED"}, TOK)
    assert st == 422, doc


def test_create_mission_for_unknown_project_is_422(api_server):
    st, doc = _post(api_server, "/api/v1/domain/missions",
                    {"project_id": "prj_nope", "title": "M"}, TOK)
    assert st == 422, doc


def test_create_project_requires_a_name(api_server):
    st, doc = _post(api_server, "/api/v1/domain/projects", {"name": "  "}, TOK)
    assert st == 422, doc


# --- needs attention ---------------------------------------------------------

def test_needs_attention_surfaces_the_reason(api_server):
    _, proj = _post(api_server, "/api/v1/domain/projects", {"name": "P"}, TOK)
    _, miss = _post(api_server, "/api/v1/domain/missions",
                    {"project_id": proj["project_id"], "title": "M"}, TOK)
    mid = miss["mission_id"]
    for to in ("READY", "STARTING", "RUNNING"):
        _post(api_server, "/api/v1/domain/missions/%s/transition" % mid,
              {"to": to}, TOK)
    st, doc = _post(api_server, "/api/v1/domain/missions/%s/transition" % mid,
                    {"to": "NEEDS_ATTENTION",
                     "attention_kind": "APPROVAL_REQUIRED",
                     "attention_reason": "directive needs human approval"}, TOK)
    assert st == 200, doc
    assert doc["attention_kind"] == "APPROVAL_REQUIRED"

    st, na = _get(api_server, "/api/v1/domain/needs-attention")
    assert st == 200 and len(na) == 1
    assert na[0]["kind"] == "APPROVAL_REQUIRED"
    assert "approval" in na[0]["reason"]


# --- connections + capability over HTTP -------------------------------------

def test_connection_and_capability_endpoints(api_server):
    st, con = _post(api_server, "/api/v1/domain/connections",
                    {"agent": "claude", "display_name": "Claude Code",
                     "secret_env_var": "JCC_CLAUDE_TOKEN"}, TOK)
    assert st == 200, con
    cid = con["connection_id"]
    assert con["health"] == "UNKNOWN"

    st, lst = _get(api_server, "/api/v1/domain/connections")
    assert st == 200 and len(lst) == 1
    assert lst[0]["control_verified"] is False

    st, cap = _post(api_server, "/api/v1/domain/connections/%s/capability" % cid,
                    {"kind": "TELEMETRY_ONLY"}, TOK)
    assert st == 200, cap
    st, lst = _get(api_server, "/api/v1/domain/connections")
    assert lst[0]["control_verified"] is False  # telemetry is NOT control

    # v1.3 Wave 7 hardening: CONTROL_VERIFIED gates effectful action, so it can no
    # longer be satisfied by a BARE assertion. The same body that used to open the
    # gate now 422s, and the gate must still be shut.
    st, cap = _post(api_server, "/api/v1/domain/connections/%s/capability" % cid,
                    {"kind": "CONTROL_VERIFIED", "detail": "exercised pause"}, TOK)
    assert st == 422, cap
    assert "evidence_ref" in str(cap)
    st, lst = _get(api_server, "/api/v1/domain/connections")
    assert lst[0]["control_verified"] is False  # a refused assertion opens nothing

    # ...and the route is still ALIVE: a CITED assertion is honoured, so the
    # refusal above is a narrowing of what counts as proof, not a dead endpoint.
    st, cap = _post(api_server, "/api/v1/domain/connections/%s/capability" % cid,
                    {"kind": "CONTROL_VERIFIED", "detail": "exercised pause",
                     "evidence_ref": "sha256:" + "a" * 64}, TOK)
    assert st == 200, cap
    assert cap["provenance"] == "MEASURED"
    st, lst = _get(api_server, "/api/v1/domain/connections")
    assert lst[0]["control_verified"] is True


def test_connection_refuses_secret_value_over_http(api_server):
    st, doc = _post(api_server, "/api/v1/domain/connections",
                    {"agent": "codex", "secret_env_var": "secret_leak"}, TOK)
    assert st == 422, doc


def test_connection_response_never_carries_a_secret_value(api_server):
    _, con = _post(api_server, "/api/v1/domain/connections",
                   {"agent": "hermes", "secret_env_var": "JCC_HERMES_TOKEN"}, TOK)
    blob = json.dumps(con)
    assert "JCC_HERMES_TOKEN" in blob          # the NAME is returned
    assert "sk-" not in blob and "Bearer " not in blob


def test_capability_for_unknown_connection_is_404(api_server):
    st, doc = _post(api_server, "/api/v1/domain/connections/con_nope/capability",
                    {"kind": "UNKNOWN"}, TOK)
    assert st == 404, doc


# --- approvals over HTTP -----------------------------------------------------

def test_approval_flow_over_http(api_server):
    _, proj = _post(api_server, "/api/v1/domain/projects", {"name": "P"}, TOK)
    _, miss = _post(api_server, "/api/v1/domain/missions",
                    {"project_id": proj["project_id"], "title": "M"}, TOK)
    mid = miss["mission_id"]
    st, apr = _post(api_server, "/api/v1/domain/missions/%s/approvals" % mid,
                    {"kind": "DIRECTIVE", "reason": "high risk"}, TOK)
    assert st == 200, apr
    assert apr["state"] == "PENDING"

    st, dec = _post(api_server, "/api/v1/domain/approvals/%s/decide" % apr["approval_id"],
                    {"state": "APPROVED", "decided_by": "operator"}, TOK)
    assert st == 200, dec
    assert dec["state"] == "APPROVED"

    # a second decision must be refused -- final means final
    st, again = _post(api_server, "/api/v1/domain/approvals/%s/decide" % apr["approval_id"],
                      {"state": "REJECTED", "decided_by": "op2"}, TOK)
    assert st == 422, again


# --- open-endpoint declaration drift ----------------------------------------

def test_domain_routes_are_declared_open(api_server):
    """Every GET domain route must be in the declared open surface."""
    st, health = _get(api_server, "/api/health")
    declared = set(health["open_get_endpoints"])
    for route in ("/api/v1/domain/home", "/api/v1/domain/projects",
                  "/api/v1/domain/missions", "/api/v1/domain/connections",
                  "/api/v1/domain/needs-attention"):
        assert route in declared, route
