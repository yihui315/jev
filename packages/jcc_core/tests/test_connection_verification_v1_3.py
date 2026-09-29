"""Wave 7 tests — agent connection verification: MEASURED, not asserted.

Owner: Metric. The property that matters is the one the whole project hangs on:
*a claim is only as good as the evidence behind it.* Wave 7 replaces "a human
types CONTROL_VERIFIED" with "a probe observes something and records what it saw".

Two rules, each with its own falsifiers:

  Rule 1 — a capability is MEASURED, not asserted.
    * the caller cannot state the outcome (POST /verify ignores a body `kind`);
    * a bare CONTROL_VERIFIED assertion is refused (422) unless it cites evidence;
    * a probe result carries an `evidence_ref` (a digest of the observation).

  Rule 2 — claim only what you can prove, and say what you cannot.
    * `probe_connection` NEVER returns CONTROL_VERIFIED (actuation is off, so a
      control path was never exercised);
    * an unobservable agent yields UNKNOWN WITH A REASON, never a healthy reading;
    * UNKNOWN never maps to a CONNECTED health.

Every test names its falsifier in the docstring.
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from jcc_core import capability_probe as P  # noqa: E402
from jcc_core import domain as D  # noqa: E402

TOKEN = "w7-surface-token"


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture()
def store(tmp_path):
    return D.DomainStore(str(tmp_path / "jcc_w7.db"))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("JCC_CONTROL_TOKEN", TOKEN)
    from jcc_core.actuator import Actuator
    from jcc_core.config import Config, ServerConfig
    from jcc_core.core import CoreEngine
    from jcc_core.providers import build_provider
    from jcc_core.server import create_app
    from jcc_core.state_store import StateStore

    db = str(tmp_path / "surface.db")
    cfg = Config(server=ServerConfig(), state_store_db_path=db)
    engine = CoreEngine(StateStore(db), build_provider(cfg), Actuator(enabled=False), cfg)
    return TestClient(create_app(engine, cfg))


def _conn(store, agent="workbuddy", **kw):
    return store.upsert_connection(agent, **kw)


# --------------------------------------------------------------------------- #
# Rule 2 — the probe can never claim more than it did
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("agent", ["workbuddy", "claude", "codex", "hermes",
                                   "generic", "", "  ", "WORKBUDDY", "nope"])
def test_probe_never_returns_control_verified(agent):
    """Falsifier: the probe over-claims a control path it never exercised.

    Actuation is DISABLED in v1.3, so no probe can honestly observe a working
    control path. If this ever returns CONTROL_VERIFIED the probe is lying.
    """
    res = P.probe_connection(agent)
    assert res.kind != D.CapabilityKind.CONTROL_VERIFIED, res


def test_probe_never_control_verified_across_host_states(monkeypatch):
    """Falsifier: CONTROL_VERIFIED leaking in through a *live* host state.

    The tempting bug is "a live session means we control it". A live session is
    SEEING, not controlling. Sweep the whole state space explicitly.
    """
    from jcc_core import workbuddy_launch as wl

    states = [
        ([], []),                                             # nothing at all
        ([{"sessionId": "s1", "pid": os.getpid()}], ["dead"]),
        ([{"sessionId": "s1", "pid": os.getpid()}], ["stale"]),
        ([{"sessionId": "s1", "pid": os.getpid()}], ["live"]),
        ([{"sessionId": "s1"}, {"sessionId": "s2"}], ["live", "live"]),
    ]
    for sessions, live_states in states:
        it = iter(live_states)
        monkeypatch.setattr(wl, "list_sessions", lambda s=sessions: list(s))
        monkeypatch.setattr(wl, "liveness",
                            lambda rec, it=it: {"state": next(it, "dead")})
        res = P.probe_connection("workbuddy")
        assert res.kind != D.CapabilityKind.CONTROL_VERIFIED, (sessions, res)


def test_live_workbuddy_session_is_adapter_contract_live_not_control(monkeypatch):
    """Falsifier: a live session silently upgrading to CONTROL_VERIFIED.

    The honest ceiling with a live session is ADAPTER_CONTRACT_LIVE: we can see
    it and speak its contract, nothing more.
    """
    from jcc_core import workbuddy_launch as wl

    monkeypatch.setattr(wl, "list_sessions",
                        lambda: [{"sessionId": "s1", "pid": os.getpid()}])
    monkeypatch.setattr(wl, "liveness", lambda rec: {"state": "live"})
    res = P.probe_connection("workbuddy")
    assert res.kind == D.CapabilityKind.ADAPTER_CONTRACT_LIVE
    assert res.evidence["live_sessions"] == 1
    assert "not CONTROL_VERIFIED" in res.detail


def test_sessions_but_none_live_is_telemetry_only(monkeypatch):
    """Falsifier: a stale session reported as a live link."""
    from jcc_core import workbuddy_launch as wl

    monkeypatch.setattr(wl, "list_sessions",
                        lambda: [{"sessionId": "s1", "pid": 424242}])
    monkeypatch.setattr(wl, "liveness", lambda rec: {"state": "stale"})
    res = P.probe_connection("workbuddy")
    assert res.kind == D.CapabilityKind.TELEMETRY_ONLY
    assert res.evidence["live_sessions"] == 0


def test_no_sessions_is_unknown_with_a_reason(monkeypatch):
    """Falsifier: UNKNOWN coerced into a healthy reading when nothing was seen."""
    from jcc_core import workbuddy_launch as wl

    monkeypatch.setattr(wl, "list_sessions", lambda: [])
    res = P.probe_connection("workbuddy")
    assert res.kind == D.CapabilityKind.UNKNOWN
    assert res.reason == "no_sessions_found"


def test_unknown_agent_is_unknown_with_reason():
    """Falsifier: an agent we cannot probe reported as fine."""
    res = P.probe_connection("mystery-agent")
    assert res.kind == D.CapabilityKind.UNKNOWN
    assert res.reason == "unknown_agent"


def test_adapter_probe_raise_becomes_unknown_not_healthy(monkeypatch):
    """Falsifier: an exception in the adapter probe swallowed into a green.

    A probe that raised observed NOTHING; it must not be reported as anything
    but UNKNOWN, and the reason must name the failure.
    """
    from jcc_core import adapters

    class _Boom:
        async def probe(self):
            raise RuntimeError("nlp")

    monkeypatch.setattr(adapters, "adapter_for", lambda agent: _Boom())
    res = P.probe_connection("claude")
    assert res.kind == D.CapabilityKind.UNKNOWN
    assert res.reason == "probe_raised:RuntimeError"


def test_adapter_non_dict_report_is_unknown(monkeypatch):
    """Falsifier: a malformed probe result treated as a capability."""
    from jcc_core import adapters

    class _Bad:
        async def probe(self):
            return ["not", "a", "dict"]

    monkeypatch.setattr(adapters, "adapter_for", lambda agent: _Bad())
    res = P.probe_connection("codex")
    assert res.kind == D.CapabilityKind.UNKNOWN
    assert res.reason == "probe_bad_shape"


def test_adapter_verified_contract_is_telemetry_only(monkeypatch):
    """Falsifier: a verified adapter contract escalated to CONTROL_VERIFIED."""
    from jcc_core import adapters

    class _Ok:
        async def probe(self):
            return {"installed": True, "verified": True, "status": "CONNECTED",
                    "version": "1.2.3"}

    monkeypatch.setattr(adapters, "adapter_for", lambda agent: _Ok())
    res = P.probe_connection("hermes")
    assert res.kind == D.CapabilityKind.TELEMETRY_ONLY
    assert res.evidence["verified"] is True


def test_probe_result_evidence_ref_is_a_digest():
    """Falsifier: a 'measured' capability with no re-checkable evidence handle."""
    res = P.probe_connection("mystery-agent")
    assert res.evidence_ref is not None
    assert res.evidence_ref.startswith("sha256:")
    assert len(res.evidence_ref) == len("sha256:") + 64


# --------------------------------------------------------------------------- #
# Rule 1 — provenance: measured vs asserted
# --------------------------------------------------------------------------- #
def test_health_mapping_never_greens_unknown():
    """Falsifier: UNKNOWN rendered as CONNECTED."""
    assert D.health_for_capability(D.CapabilityKind.UNKNOWN) == "UNKNOWN"
    assert D.health_for_capability(D.CapabilityKind.UNKNOWN) != "CONNECTED"
    assert D.health_for_capability(D.CapabilityKind.UNAVAILABLE) == "UNAVAILABLE"
    assert D.health_for_capability(D.CapabilityKind.TELEMETRY_ONLY) == "DEGRADED"


def test_provenance_measured_vs_asserted(store):
    """Falsifier: an assertion presented the same as a measurement."""
    c = _conn(store)
    measured = store.set_capability(c.connection_id, D.CapabilityKind.TELEMETRY_ONLY,
                                    evidence_ref="sha256:" + "a" * 64)
    asserted = store.set_capability(c.connection_id, D.CapabilityKind.ADAPTER_CONTRACT_LIVE)
    assert store.capability_provenance(measured) == "MEASURED"
    assert store.capability_provenance(asserted) == "ASSERTED"


def test_record_probe_writes_capability_from_the_probe(store):
    """Falsifier: the recorded capability not matching the observation."""
    c = _conn(store)
    res = P.ProbeResult(kind=D.CapabilityKind.TELEMETRY_ONLY, detail="seen once",
                        evidence={"x": 1}, evidence_ref="sha256:" + "b" * 64)
    updated = store.record_probe(c.connection_id, res)
    assert updated.health == "DEGRADED"
    caps = store.list_capabilities(c.connection_id)
    assert any(cap.kind == D.CapabilityKind.TELEMETRY_ONLY and cap.evidence_ref
               for cap in caps)
    assert not store.is_control_verified(c.connection_id)


def test_record_probe_does_not_grant_control_verified(store):
    """Falsifier: verification silently unlocking the effectful-action gate."""
    c = _conn(store)
    for kind in (D.CapabilityKind.ADAPTER_CONTRACT_LIVE, D.CapabilityKind.TELEMETRY_ONLY,
                 D.CapabilityKind.UNKNOWN, D.CapabilityKind.UNAVAILABLE):
        res = P.ProbeResult(kind=kind, detail="", evidence={"k": kind.value},
                            evidence_ref="sha256:" + "c" * 64)
        store.record_probe(c.connection_id, res)
        assert not store.is_control_verified(c.connection_id), kind


def test_record_probe_unknown_connection_raises(store):
    """Falsifier: a probe result recorded against a non-existent connection."""
    res = P.ProbeResult(kind=D.CapabilityKind.UNKNOWN, detail="x")
    with pytest.raises(ValueError):
        store.record_probe("con_nope", res)


# --------------------------------------------------------------------------- #
# API — GET one connection with provenance
# --------------------------------------------------------------------------- #
def test_get_connection_is_open_and_carries_provenance(client):
    """Falsifier: the UI cannot see WHY a capability is believed."""
    r = client.post("/api/v1/domain/connections", json={"agent": "workbuddy"},
                    headers={"X-JCC-Token": TOKEN})
    assert r.status_code == 200, r.text
    cid = r.json()["connection_id"]

    before = client.get("/api/v1/domain/connections/%s" % cid)
    assert before.status_code == 200, before.text
    assert before.json()["control_verified"] is False

    # an asserted capability shows up as ASSERTED, not MEASURED
    client.post("/api/v1/domain/connections/%s/capability" % cid,
                json={"kind": "TELEMETRY_ONLY"}, headers={"X-JCC-Token": TOKEN})
    body = client.get("/api/v1/domain/connections/%s" % cid).json()
    prov = {cap["kind"]: cap["provenance"] for cap in body["capabilities"]}
    assert prov.get("TELEMETRY_ONLY") == "ASSERTED"


def test_get_unknown_connection_is_404(client):
    """Falsifier: a made-up connection id fabricating a row."""
    assert client.get("/api/v1/domain/connections/con_nope").status_code == 404


# --------------------------------------------------------------------------- #
# API — POST verify runs a REAL probe; the caller cannot state the outcome
# --------------------------------------------------------------------------- #
def test_verify_requires_a_token(client):
    """Falsifier: verification writing without a human/token."""
    r = client.post("/api/v1/domain/connections", json={"agent": "workbuddy"},
                    headers={"X-JCC-Token": TOKEN})
    cid = r.json()["connection_id"]
    assert client.post("/api/v1/domain/connections/%s/verify" % cid,
                       json={}).status_code in (401, 403)
    # and nothing got recorded
    assert client.get("/api/v1/domain/connections/%s" % cid).json()["capabilities"] == []


def test_verify_ignores_the_caller_supplied_kind(client):
    """Falsifier (Rule 1): the caller states the outcome instead of measuring it.

    POST {kind: CONTROL_VERIFIED} to /verify: the recorded capability must be
    what the PROBE observed, never the body. The body cannot even reach the gate.
    """
    r = client.post("/api/v1/domain/connections", json={"agent": "mystery-agent"},
                    headers={"X-JCC-Token": TOKEN})
    cid = r.json()["connection_id"]
    resp = client.post("/api/v1/domain/connections/%s/verify" % cid,
                       json={"kind": "CONTROL_VERIFIED", "probe": "auto"},
                       headers={"X-JCC-Token": TOKEN})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["observed"]["kind"] != "CONTROL_VERIFIED"
    assert body["control_verified"] is False
    # an unknown agent observes UNKNOWN, and that is what got written
    assert body["observed"]["kind"] == "UNKNOWN"
    assert body["observed"]["reason"] == "unknown_agent"
    assert body["health"] == "UNKNOWN"
    assert all(cap["kind"] != "CONTROL_VERIFIED" for cap in body["capabilities"])


def test_verify_records_measured_evidence(client, monkeypatch):
    """Falsifier: a 'verified' connection with no re-checkable evidence handle."""
    from jcc_core import adapters

    class _Ok:
        async def probe(self):
            return {"installed": True, "verified": True, "status": "CONNECTED",
                    "version": "9.9"}

    monkeypatch.setattr(adapters, "adapter_for", lambda agent: _Ok())
    r = client.post("/api/v1/domain/connections", json={"agent": "claude"},
                    headers={"X-JCC-Token": TOKEN})
    cid = r.json()["connection_id"]
    body = client.post("/api/v1/domain/connections/%s/verify" % cid,
                       json={}, headers={"X-JCC-Token": TOKEN}).json()
    assert body["observed"]["kind"] == "TELEMETRY_ONLY"
    assert body["observed"]["evidence_ref"].startswith("sha256:")
    measured = [c for c in body["capabilities"]
                if c["kind"] == "TELEMETRY_ONLY" and c["provenance"] == "MEASURED"]
    assert measured, body
    assert body["control_verified"] is False


def test_verify_unknown_connection_is_404(client):
    """Falsifier: verifying a connection that does not exist."""
    assert client.post("/api/v1/domain/connections/con_nope/verify",
                       json={}, headers={"X-JCC-Token": TOKEN}).status_code == 404


# --------------------------------------------------------------------------- #
# API — hardening: CONTROL_VERIFIED can no longer be asserted bare
# --------------------------------------------------------------------------- #
def test_bare_control_verified_assertion_is_refused(client):
    """Falsifier: the effectful-action gate satisfied by a bare assertion."""
    r = client.post("/api/v1/domain/connections", json={"agent": "workbuddy"},
                    headers={"X-JCC-Token": TOKEN})
    cid = r.json()["connection_id"]
    resp = client.post("/api/v1/domain/connections/%s/capability" % cid,
                       json={"kind": "CONTROL_VERIFIED"}, headers={"X-JCC-Token": TOKEN})
    assert resp.status_code == 422, resp.text
    assert "evidence_ref" in str(resp.json())
    # and the gate did NOT open
    assert client.get("/api/v1/domain/connections/%s" % cid).json()["control_verified"] is False


def test_control_verified_with_evidence_is_accepted(client):
    """Non-vacuity for the hardening: a *cited* CONTROL_VERIFIED still lands.

    The hardening must refuse a bare assertion, NOT the kind itself -- otherwise
    the "fix" would be a blanket ban and could not be distinguished from a
    regression that disabled the route.
    """
    r = client.post("/api/v1/domain/connections", json={"agent": "workbuddy"},
                    headers={"X-JCC-Token": TOKEN})
    cid = r.json()["connection_id"]
    resp = client.post("/api/v1/domain/connections/%s/capability" % cid,
                       json={"kind": "CONTROL_VERIFIED",
                             "evidence_ref": "sha256:" + "d" * 64,
                             "detail": "external control-path evidence"},
                       headers={"X-JCC-Token": TOKEN})
    assert resp.status_code == 200, resp.text
    assert resp.json()["provenance"] == "MEASURED"
    assert client.get("/api/v1/domain/connections/%s" % cid).json()["control_verified"] is True


def test_other_kinds_still_assertable_without_evidence(client):
    """Falsifier: the hardening over-reaching and breaking ordinary recording."""
    r = client.post("/api/v1/domain/connections", json={"agent": "codex"},
                    headers={"X-JCC-Token": TOKEN})
    cid = r.json()["connection_id"]
    resp = client.post("/api/v1/domain/connections/%s/capability" % cid,
                       json={"kind": "TELEMETRY_ONLY"}, headers={"X-JCC-Token": TOKEN})
    assert resp.status_code == 200, resp.text
    assert resp.json()["provenance"] == "ASSERTED"


def test_capability_kind_is_still_validated(client):
    """Falsifier: the new harden-check masking the existing kind validation."""
    r = client.post("/api/v1/domain/connections", json={"agent": "codex"},
                    headers={"X-JCC-Token": TOKEN})
    cid = r.json()["connection_id"]
    resp = client.post("/api/v1/domain/connections/%s/capability" % cid,
                       json={"kind": "NOT_A_KIND"}, headers={"X-JCC-Token": TOKEN})
    assert resp.status_code == 422
