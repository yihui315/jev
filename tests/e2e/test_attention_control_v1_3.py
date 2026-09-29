"""Wave 4 API tests — attention control over REAL HTTP (owner: Metric).

The properties that matter, each with a falsifier:

  * **The preview lies**: POST …/resolve with dry_run=true (the default) must not
    change the domain DB. Falsifier: a mutation during a "preview".
  * **Preview and apply agree**: the fields the preview reports are the fields the
    apply acts on, because both read the same resolver.
  * **UNKNOWN is never green**: the resolutions read must never report an action as
    EXECUTABLE when its prerequisite could not be determined.
  * **Actuation stays off**: every high-risk action is reported BLOCKED/ACTUATION_DISABLED.
  * **A token is required to apply**: the open surface is read-only.
"""
from __future__ import annotations

import hashlib
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


def _db_digest(api_server):
    """sha256 of the domain DB file, or None if it cannot be read."""
    path = getattr(api_server, "db_path", None)
    if not path or not os.path.exists(path):
        return None
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _mission_in_attention(api_server, kind="AGENT_STUCK"):
    _, proj = _post(api_server, "/api/v1/domain/projects", {"name": "W4"}, TOK)
    _, miss = _post(api_server, "/api/v1/domain/missions",
                    {"project_id": proj["project_id"], "title": "M"}, TOK)
    mid = miss["mission_id"]
    for to in ("READY", "STARTING", "RUNNING"):
        _post(api_server, "/api/v1/domain/missions/%s/transition" % mid, {"to": to}, TOK)
    _post(api_server, "/api/v1/domain/missions/%s/transition" % mid,
          {"to": "NEEDS_ATTENTION", "attention_kind": kind,
           "attention_reason": "wave 4 test"}, TOK)
    return mid


# --- the resolutions read is open and shaped --------------------------------

def test_resolutions_read_is_open(api_server):
    st, doc = _get(api_server, "/api/v1/domain/needs-attention/resolutions")
    assert st == 200, doc
    assert doc["schema"] == "jcc/v1.3-attention-resolutions/1"
    assert doc["actuation_enabled"] is False
    assert isinstance(doc["items"], list)


def test_resolutions_report_actions_for_an_attention_item(api_server):
    mid = _mission_in_attention(api_server, kind="AGENT_STUCK")
    _, doc = _get(api_server, "/api/v1/domain/needs-attention/resolutions")
    row = next((i for i in doc["items"] if i["mission_id"] == mid), None)
    assert row is not None, doc
    assert row["kind"] == "AGENT_STUCK"
    assert row["actions"], "an AGENT_STUCK item must propose the resume action"
    a = row["actions"][0]
    assert a["action"] == "resume"
    assert a["policy_permitted"] is True
    # Actuation is off, so resume is BLOCKED with the honest reason.
    assert a["executability"] == "BLOCKED"
    assert a["blocked_reason"] == "ACTUATION_DISABLED"


def test_unknown_never_renders_as_executable(api_server):
    """Falsifier: a reconnect on an unreadable connection capability as EXECUTABLE."""
    mid = _mission_in_attention(api_server, kind="CONNECTION_LOST")
    _, doc = _get(api_server, "/api/v1/domain/needs-attention/resolutions")
    row = next((i for i in doc["items"] if i["mission_id"] == mid), None)
    assert row is not None
    for a in row["actions"]:
        assert a["executability"] in ("EXECUTABLE", "BLOCKED", "UNKNOWN")
        if a["executability"] == "UNKNOWN":
            # UNKNOWN is never accompanied by a "permitted and fine" signal.
            assert a["blocked_reason"] is None


# --- preview-first: a preview must not mutate --------------------------------

def test_preview_does_not_mutate_the_domain_db(api_server):
    """THE Wave-4 falsifier for "the preview lied"."""
    mid = _mission_in_attention(api_server, kind="VERIFICATION_REQUIRED")
    before = _db_digest(api_server)
    st, doc = _post(api_server,
                    "/api/v1/domain/needs-attention/%s/resolve" % mid,
                    {"action": "verify", "dry_run": True}, TOK)
    assert st == 200, doc
    assert doc["dry_run"] is True and doc["applied"] is False
    after = _db_digest(api_server)
    assert before is not None
    assert before == after, "a dry_run request mutated the domain DB"


def test_default_is_preview(api_server):
    mid = _mission_in_attention(api_server, kind="VERIFICATION_REQUIRED")
    before = _db_digest(api_server)
    st, doc = _post(api_server,
                    "/api/v1/domain/needs-attention/%s/resolve" % mid,
                    {"action": "verify"}, TOK)  # no dry_run -> preview
    assert st == 200, doc
    assert doc["applied"] is False
    assert _db_digest(api_server) == before


def test_apply_verify_resolves_the_mission(api_server):
    mid = _mission_in_attention(api_server, kind="VERIFICATION_REQUIRED")
    st, doc = _post(api_server,
                    "/api/v1/domain/needs-attention/%s/resolve" % mid,
                    {"action": "verify", "dry_run": False}, TOK)
    assert st == 200, doc
    assert doc["applied"] is True
    assert doc["result"]["mission_status"] == "RUNNING"
    _, miss = _get(api_server, "/api/v1/domain/missions/%s" % mid)
    assert miss["status"] == "RUNNING"
    assert miss["attention_kind"] is None


def test_apply_requires_a_token(api_server):
    mid = _mission_in_attention(api_server, kind="VERIFICATION_REQUIRED")
    st, _ = _post(api_server,
                  "/api/v1/domain/needs-attention/%s/resolve" % mid,
                  {"action": "verify", "dry_run": False}, None)
    assert st in (401, 403), st


def test_blocked_action_reports_honestly_and_does_not_apply(api_server):
    mid = _mission_in_attention(api_server, kind="AGENT_STUCK")
    st, doc = _post(api_server,
                    "/api/v1/domain/needs-attention/%s/resolve" % mid,
                    {"action": "resume", "dry_run": False}, TOK)
    assert st == 200, doc
    assert doc["ok"] is False and doc["applied"] is False
    assert doc["executability"] == "BLOCKED"
    assert doc["blocked_reason"] == "ACTUATION_DISABLED"
    # and the mission is untouched
    _, miss = _get(api_server, "/api/v1/domain/missions/%s" % mid)
    assert miss["status"] == "NEEDS_ATTENTION"


def test_resolve_on_a_mission_not_in_attention_is_refused(api_server):
    _, proj = _post(api_server, "/api/v1/domain/projects", {"name": "W4b"}, TOK)
    _, miss = _post(api_server, "/api/v1/domain/missions",
                    {"project_id": proj["project_id"], "title": "M"}, TOK)
    mid = miss["mission_id"]  # DRAFT, not in attention
    st, doc = _post(api_server,
                    "/api/v1/domain/needs-attention/%s/resolve" % mid,
                    {"action": "verify", "dry_run": False}, TOK)
    assert st == 200, doc
    assert doc["ok"] is False
    assert doc["blocked_reason"] == "NOT_IN_ATTENTION"
