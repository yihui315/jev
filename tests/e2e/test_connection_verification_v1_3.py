"""Wave 7 API tests — connection verification over REAL HTTP (owner: Metric).

The properties that matter, each with a falsifier:

  * **A capability is MEASURED, not asserted**: `POST /verify` runs a real probe
    and records what it OBSERVED; a `kind` in the body cannot reach the gate.
  * **Claim only what you can prove**: v1.3 actuation is off, so verification can
    never yield CONTROL_VERIFIED; the honest ceiling is stated in the response.
  * **Provenance is visible**: `GET /connections/{cid}` says whether each
    capability is MEASURED (probe) or ASSERTED (human).
  * **The effectful gate needs evidence**: a bare CONTROL_VERIFIED is refused
    (422) while a cited one is still accepted (so the fix is not a blanket ban).
  * **A token is required to verify**: the open surface is read-only.
"""
from __future__ import annotations


def _mk_connection(api_server, agent):
    r = api_server.request("POST", "/api/v1/domain/connections",
                           json_body={"agent": agent})
    assert r.status == 200, r.body
    return r.json()["connection_id"]


# --- read: one connection with provenance ------------------------------------

def test_connection_view_is_open(api_server):
    cid = _mk_connection(api_server, "workbuddy")
    r = api_server.request("GET", "/api/v1/domain/connections/%s" % cid, token=None)
    assert r.status == 200, r.body
    body = r.json()
    assert body["connection_id"] == cid
    assert body["control_verified"] is False


def test_unknown_connection_is_404(api_server):
    """Falsifier: a made-up connection id fabricating a row."""
    r = api_server.request("GET", "/api/v1/domain/connections/con_does_not_exist",
                           token=None)
    assert r.status == 404, r.body


def test_asserted_capability_shows_as_asserted(api_server):
    """Falsifier: a typed-in capability presented as if it were measured."""
    cid = _mk_connection(api_server, "codex")
    r = api_server.request("POST", "/api/v1/domain/connections/%s/capability" % cid,
                           json_body={"kind": "TELEMETRY_ONLY"})
    assert r.status == 200, r.body
    got = api_server.request("GET", "/api/v1/domain/connections/%s" % cid, token=None).json()
    prov = {c["kind"]: c["provenance"] for c in got["capabilities"]}
    assert prov.get("TELEMETRY_ONLY") == "ASSERTED"


# --- verify: real probe, caller cannot state the outcome ----------------------

def test_verify_requires_a_token(api_server):
    """Falsifier: verification writing without the control token."""
    cid = _mk_connection(api_server, "workbuddy")
    r = api_server.request("POST", "/api/v1/domain/connections/%s/verify" % cid,
                           token=None, json_body={})
    assert r.status in (401, 403), r.status
    after = api_server.request("GET", "/api/v1/domain/connections/%s" % cid, token=None).json()
    assert after["capabilities"] == [], "a tokenless verify must not have landed"


def test_verify_ignores_a_caller_supplied_kind(api_server):
    """Falsifier (Rule 1): the caller states the outcome instead of measuring it."""
    cid = _mk_connection(api_server, "mystery-agent")
    r = api_server.request("POST", "/api/v1/domain/connections/%s/verify" % cid,
                           json_body={"kind": "CONTROL_VERIFIED"})
    assert r.status == 200, r.body
    body = r.json()
    assert body["observed"]["kind"] == "UNKNOWN"
    assert body["observed"]["kind"] != "CONTROL_VERIFIED"
    assert body["observed"]["reason"] == "unknown_agent"
    assert body["control_verified"] is False
    assert body["health"] == "UNKNOWN"


def test_verify_records_measured_evidence(api_server, monkeypatch):
    """Falsifier: a 'verified' connection with no re-checkable evidence handle."""
    from jcc_core import adapters

    class _Ok:
        async def probe(self):
            return {"installed": True, "verified": True, "status": "CONNECTED",
                    "version": "9.9"}

    monkeypatch.setattr(adapters, "adapter_for", lambda agent: _Ok())
    cid = _mk_connection(api_server, "claude")
    r = api_server.request("POST", "/api/v1/domain/connections/%s/verify" % cid,
                           json_body={})
    assert r.status == 200, r.body
    body = r.json()
    assert body["observed"]["kind"] == "TELEMETRY_ONLY"
    assert body["observed"]["evidence_ref"].startswith("sha256:")
    measured = [c for c in body["capabilities"]
                if c["kind"] == "TELEMETRY_ONLY" and c["provenance"] == "MEASURED"]
    assert measured, body
    assert body["control_verified"] is False, "seeing an agent is not controlling it"


def test_verify_on_unknown_connection_is_404(api_server):
    """Falsifier: verifying a connection that does not exist."""
    r = api_server.request("POST", "/api/v1/domain/connections/con_nope/verify",
                           json_body={})
    assert r.status == 404, r.body


# --- hardening: CONTROL_VERIFIED needs evidence ------------------------------

def test_bare_control_verified_is_refused_over_http(api_server):
    """Falsifier: the effectful-action gate satisfied by a bare assertion."""
    cid = _mk_connection(api_server, "workbuddy")
    r = api_server.request("POST", "/api/v1/domain/connections/%s/capability" % cid,
                           json_body={"kind": "CONTROL_VERIFIED"})
    assert r.status == 422, r.body
    assert "evidence_ref" in r.body
    after = api_server.request("GET", "/api/v1/domain/connections/%s" % cid, token=None).json()
    assert after["control_verified"] is False


def test_cited_control_verified_is_still_accepted(api_server):
    """Non-vacuity: the hardening refuses a BARE assertion, not the kind itself."""
    cid = _mk_connection(api_server, "workbuddy")
    r = api_server.request("POST", "/api/v1/domain/connections/%s/capability" % cid,
                           json_body={"kind": "CONTROL_VERIFIED",
                                      "evidence_ref": "sha256:" + "e" * 64,
                                      "detail": "external control-path evidence"})
    assert r.status == 200, r.body
    assert r.json()["provenance"] == "MEASURED"
    after = api_server.request("GET", "/api/v1/domain/connections/%s" % cid, token=None).json()
    assert after["control_verified"] is True


def test_home_actuation_stays_false(api_server):
    """Boundary: verification must not have switched actuation on."""
    home = api_server.request("GET", "/api/v1/domain/home", token=None).json()
    assert home["actuation_enabled"] is False
