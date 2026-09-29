"""Wave 5 API tests — deliverables over REAL HTTP (owner: Metric).

The properties that matter, each with a falsifier:

  * **A deliverable has identity**: the by-id read returns the exact artifact, and
    404s on an unknown id (never a fabricated row).
  * **PRODUCED is not VERIFIED**: acceptance is an explicit, token-gated, recorded
    human act; an unfinalized artifact cannot be re-decided.
  * **The aggregate is honest**: `verified` counts only accepted artifacts; a
    rejected artifact is not counted as accepted.
  * **A token is required to verify**: the open surface is read-only.
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


def _make_artifact(api_server, name="out.txt"):
    _, proj = _post(api_server, "/api/v1/domain/projects", {"name": "W5"}, TOK)
    _, miss = _post(api_server, "/api/v1/domain/missions",
                    {"project_id": proj["project_id"], "title": "Deliverable Mission"}, TOK)
    mid = miss["mission_id"]
    st, art = _post(api_server, "/api/v1/domain/missions/%s/artifacts" % mid,
                    {"name": name, "kind": "file", "uri": "F:/out/%s" % name,
                     "sha256": "a" * 64, "bytes": 42}, TOK)
    assert st == 200, art
    return mid, art


# --- identity ----------------------------------------------------------------

def test_deliverable_view_is_open_and_shaped(api_server):
    st, rows = _get(api_server, "/api/v1/domain/deliverables")
    assert st == 200, rows
    assert isinstance(rows, list)


def test_deliverable_by_id_returns_exact_artifact(api_server):
    mid, art = _make_artifact(api_server)
    st, got = _get(api_server, "/api/v1/domain/artifacts/%s" % art["artifact_id"])
    assert st == 200, got
    assert got["artifact_id"] == art["artifact_id"]
    assert got["mission_id"] == mid
    assert got["status"] == "PRODUCED"


def test_unknown_deliverable_is_404(api_server):
    """Falsifier: an unknown id must 404, never return a fabricated deliverable."""
    st, _ = _get(api_server, "/api/v1/domain/artifacts/art_does_not_exist")
    assert st == 404


def test_deliverable_view_carries_mission_title(api_server):
    """Falsifier: the server-side view must include the mission title."""
    mid, art = _make_artifact(api_server)
    st, rows = _get(api_server, "/api/v1/domain/deliverables?mission_id=%s" % mid)
    assert st == 200, rows
    assert len(rows) == 1
    assert rows[0]["mission_title"] == "Deliverable Mission"


# --- verify: explicit, token-gated, final ------------------------------------

def test_verify_requires_a_token(api_server):
    """Falsifier: recording a human verdict without the control token."""
    mid, art = _make_artifact(api_server)
    st, _ = _post(api_server, "/api/v1/domain/artifacts/%s/verify" % art["artifact_id"],
                  {"status": "VERIFIED", "decided_by": "alice"}, None)
    assert st in (401, 403), st
    # and the artifact is still unverified
    _, got = _get(api_server, "/api/v1/domain/artifacts/%s" % art["artifact_id"])
    assert got["status"] == "PRODUCED"


def test_verify_requires_decided_by(api_server):
    """Falsifier: a verdict that names no human must be refused."""
    mid, art = _make_artifact(api_server)
    st, body = _post(api_server, "/api/v1/domain/artifacts/%s/verify" % art["artifact_id"],
                     {"status": "VERIFIED"}, TOK)
    assert st == 422, body
    assert "decided_by" in str(body)


def test_verify_sets_verified_and_records_who(api_server):
    mid, art = _make_artifact(api_server)
    st, res = _post(api_server, "/api/v1/domain/artifacts/%s/verify" % art["artifact_id"],
                    {"status": "VERIFIED", "decided_by": "alice", "note": "ok"}, TOK)
    assert st == 200, res
    assert res["status"] == "VERIFIED"
    assert res["provenance"]["verified_by"] == "alice"


def test_verdict_is_final_over_http(api_server):
    """Falsifier: re-deciding an already-verified deliverable."""
    mid, art = _make_artifact(api_server)
    _post(api_server, "/api/v1/domain/artifacts/%s/verify" % art["artifact_id"],
          {"status": "VERIFIED", "decided_by": "alice"}, TOK)
    st, body = _post(api_server, "/api/v1/domain/artifacts/%s/verify" % art["artifact_id"],
                     {"status": "REJECTED", "decided_by": "bob"}, TOK)
    assert st == 422, body


# --- aggregate: honest -------------------------------------------------------

def test_home_verified_count_excludes_unverified(api_server):
    """Falsifier: an unfinalized artifact counted as an accepted deliverable."""
    mid, art = _make_artifact(api_server, name="one.txt")
    _, before = _get(api_server, "/api/v1/domain/home")
    v0 = before["deliverables"]["verified"]
    # a produced artifact must NOT increase verified
    _, home = _get(api_server, "/api/v1/domain/home")
    assert home["deliverables"]["verified"] == v0
    assert home["deliverables"]["total"] >= 1
    # after verifying, it must
    _post(api_server, "/api/v1/domain/artifacts/%s/verify" % art["artifact_id"],
          {"status": "VERIFIED", "decided_by": "alice"}, TOK)
    _, after = _get(api_server, "/api/v1/domain/home")
    assert after["deliverables"]["verified"] == v0 + 1


def test_deliverable_filter_excludes_unverified(api_server):
    """Falsifier: status=VERIFIED returning a PRODUCED row."""
    mid, art = _make_artifact(api_server, name="filter.txt")
    st, rows = _get(api_server, "/api/v1/domain/deliverables?status=VERIFIED")
    assert st == 200, rows
    ids = {r["artifact_id"] for r in rows}
    assert art["artifact_id"] not in ids, "a PRODUCED artifact must not pass status=VERIFIED"


def test_unknown_status_filter_is_422(api_server):
    """Falsifier: a filter that silently drops itself."""
    st, _ = _get(api_server, "/api/v1/domain/deliverables?status=NONSENSE")
    assert st == 422
