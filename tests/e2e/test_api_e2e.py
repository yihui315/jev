"""E-02: API end-to-end slice (owner: trail-2).

Starts the REAL app in-process on a random loopback port and drives it over
REAL HTTP (stdlib urllib, no new deps). Proves, at the process/HTTP level, the
contracts that ``test_server_surface.py`` pins at the declaration/shape level:

  * loopback-only bind (the process is not listening on any non-loopback addr)
  * every mutating endpoint refuses without the token and accepts with it
  * the read-only open surface matches ``/api/health``'s declaration
  * CORS is an explicit allowlist, never a wildcard
  * signed-directive round-trip (sign -> verify -> tamper -> fail)
  * audit chain is hash-linked AND tamper-detected
  * session isolation (a directive scoped to session A is not honoured for B)

These tests EXTEND the surface tests; they do not re-assert the same facts the
same way. Any declared-but-closed or served-but-undeclared route is reported as
a finding via an assertion that fails the run.
"""
from __future__ import annotations

import time

import pytest

from jcc_core.schemas import Directive, DirectiveAction
from jcc_core.signing import sign_directive, verify_directive

SIGN_KEY = b"e2e-signing-key-32-bytes-long!!"


def _valid_aep(session_id: str, event_id: str) -> dict:
    return {
        "version": "aep/v0.1",
        "event_id": event_id,
        "delivery_id": "dlv-" + event_id,
        "session_id": session_id,
        "agent": "workbuddy",
        "project": "e2e",
        "timestamp": time.time(),
        "event_type": "tool_call",
        "payload": {"step": event_id},
        "evidence": {"artifact": event_id},
        "schema_ok": True,
    }


# --------------------------------------------------------------------------- #
def test_loopback_bind_only(api_server, e2e_support):
    """The real process listens ONLY on loopback addresses."""
    hosts = api_server.bound_hosts
    assert hosts, "server reported no bound sockets"
    for h in hosts:
        assert h in ("127.0.0.1", "::1", "127.0.0.2"), (
            f"server is bound to a NON-loopback address {h!r} -- not loopback-only"
        )
    # health is reachable on 127.0.0.1 (the bound host)
    assert api_server.request("GET", "/api/health").status == 200

    # best-effort: a non-loopback interface must NOT reach the service.
    # Taken from the fixture, NOT ``__import__("conftest")``: with no
    # ``__init__.py`` under tests/ or tools/, that bare name resolves to
    # whichever conftest the loader reached LAST (measured: it breaks
    # ``pytest tests/e2e tools/tests``).
    ext = e2e_support.external_ip()
    if ext and not ext.startswith("127."):
        import urllib.error
        reached = False
        try:
            urllib.request.urlopen(f"http://{ext}:{api_server.port}/api/health", timeout=2)
            reached = True
        except Exception:
            reached = False
        assert not reached, (
            f"service reachable on external interface {ext}:{api_server.port} "
            "-- bind is not loopback-only"
        )


# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("endpoint", ["/api/assess", "/api/pause", "/api/steer", "/api/retry"])
def test_mutating_endpoints_refuse_without_token(api_server, endpoint):
    if endpoint == "/api/assess":
        body = _valid_aep("sess-refuse", "e-refuse")
    else:
        body = {"session_id": "sess-refuse"}
    # no token -> 403 (fail-closed, token configured)
    r = api_server.request("POST", endpoint, token=None, json_body=body)
    assert r.status == 403, f"{endpoint} accepted tokenless call ({r.status})"
    # wrong token -> 403
    r2 = api_server.request("POST", endpoint, token="wrong-token", json_body=body)
    assert r2.status == 403, f"{endpoint} accepted a wrong token ({r2.status})"
    # correct token -> 200
    r3 = api_server.request("POST", endpoint, json_body=body)
    assert r3.status == 200, f"{endpoint} refused a valid token ({r3.status})"


def test_mutating_endpoints_accept_bearer_token(api_server):
    body = {"session_id": "sess-bearer"}
    r = api_server.request(
        "POST", "/api/pause", token=None, json_body=body,
        headers={"Authorization": f"Bearer {api_server.token}"},
    )
    assert r.status == 200, f"Bearer token not honoured ({r.status})"


def test_mutating_endpoints_refuse_when_token_unset(api_server, monkeypatch):
    """ADR fail-closed: unset control token -> 401, not an open endpoint."""
    monkeypatch.setenv("JCC_CONTROL_TOKEN", "")
    r = api_server.request("POST", "/api/pause", json_body={"session_id": "x"})
    assert r.status == 401, f"unset token should lock control (got {r.status})"


# --------------------------------------------------------------------------- #
def test_open_endpoints_match_health_declaration(api_server):
    """Read-only surface == the declared one, and every declared route is open."""
    from fastapi.routing import APIRoute

    health = api_server.request("GET", "/api/health").json()
    declared = set(health["open_get_endpoints"])
    served = {
        r.path for r in api_server.app.routes
        if isinstance(r, APIRoute) and "GET" in r.methods
    }
    drift = declared ^ served
    assert not drift, (
        "open GET surface DRIFT between declaration and what FastAPI serves:\n"
        f"  declared-but-not-served: {sorted(declared - served)}\n"
        f"  served-but-not-declared (INVISIBLE open route): {sorted(served - declared)}"
    )
    # every declared open route must actually be reachable without a token
    for p in declared:
        path = p if "{sid}" not in p else p.replace("{sid}", "nope-sid")
        r = api_server.request("GET", path)
        assert r.status not in (401, 403), (
            f"{p} is declared open but refuses a tokenless read ({r.status})"
        )
        assert r.status in (200, 404), f"{p} returned unexpected {r.status}"


# --------------------------------------------------------------------------- #
def test_cors_is_allowlist_not_wildcard(api_server):
    disallowed = api_server.request("GET", "/api/health", origin="https://evil.example.com")
    assert "access-control-allow-origin" not in disallowed.headers, (
        "disallowed Origin was echoed -> CORS is not an allowlist"
    )
    allowed = api_server.request("GET", "/api/health", origin="http://localhost:5173")
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:5173", (
        "allowed Origin was not echoed exactly -> allowlist broken"
    )
    preflight = api_server.request(
        "OPTIONS", "/api/health", origin="http://localhost:5173",
        headers={"Access-Control-Request-Method": "GET"},
    )
    acao = preflight.headers.get("access-control-allow-origin")
    assert acao in (None, "http://localhost:5173"), (
        f"preflight leaked an unexpected ACAO: {acao!r}"
    )
    # the one hard rule: never a wildcard
    for resp in (disallowed, allowed, preflight):
        assert resp.headers.get("access-control-allow-origin") != "*", (
            "CORS returned a wildcard '*' -> allowlist violated"
        )


# --------------------------------------------------------------------------- #
def test_signed_directive_roundtrip(api_server):
    """sign -> store -> read back over HTTP -> verify; then tamper -> fail."""
    now = time.time()
    d = Directive(
        directive_id="dir_sX_e1",
        session_id="sX",
        timestamp=float(now),
        action=DirectiveAction.PAUSE,
        target_actuator="noop",
        policy_version="p3.0.0",
        issued_at=float(now),
        ttl_s=120.0,
        nonce="nonce-1",
        policy_table_version="pt-v1",
        evidence_hash=None,
        params={"source": "e2e"},
    )
    sig = sign_directive(d, SIGN_KEY)
    assert sig.startswith("sha256="), "signature wire format wrong"
    d.signature = sig
    d.signed = True
    api_server.engine.store.add_directive(d, origin="engine")

    rows = api_server.request("GET", "/api/decisions?sid=sX").json()
    match = [x for x in rows if x["directive_id"] == "dir_sX_e1"]
    assert match, "signed directive not retrievable over real HTTP"
    assert match[0]["signed"] is True and match[0]["signature"] == sig, (
        "stored signature does not match the signed directive"
    )

    ok, reason = verify_directive(d, SIGN_KEY, now=float(now))
    assert ok and reason == "ok", "valid signature failed verification"

    tampered = d.model_copy(deep=True)
    tampered.action = DirectiveAction.STEER
    ok2, reason2 = verify_directive(tampered, SIGN_KEY, now=float(now))
    assert (not ok2) and reason2 == "BAD_SIGNATURE", (
        "tampered directive still verified -- integrity check is broken"
    )


# --------------------------------------------------------------------------- #
def test_audit_chain_hash_linked_and_tamper_detected(api_server):
    for i in range(2):
        r = api_server.request("POST", "/api/assess", json_body=_valid_aep("sess-audit", f"ae-{i}"))
        assert r.status == 200
    before = api_server.request("GET", "/api/audit/verify").json()
    assert before["intact"] is True, "fresh chain should be intact"
    assert before["entries"] >= 2, "audit rows missing"

    # tamper a historical row directly in the SQLite store
    import sqlite3
    conn = sqlite3.connect(api_server.db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("UPDATE audit_tape SET directive='TAMPERED' WHERE seq=1")
        conn.commit()
    finally:
        conn.close()

    after = api_server.request("GET", "/api/audit/verify").json()
    assert after["intact"] is False, "hash chain did NOT detect the tamper"


# --------------------------------------------------------------------------- #
def test_session_isolation(api_server):
    """A directive scoped to session A is not honoured for session B."""
    for sid, eid in [("sess-A", "a1"), ("sess-B", "b1")]:
        r = api_server.request("POST", "/api/assess", json_body=_valid_aep(sid, eid))
        assert r.status == 200

    da = api_server.request("GET", "/api/decisions?sid=sess-A").json()
    db = api_server.request("GET", "/api/decisions?sid=sess-B").json()
    ids_a = {x["directive_id"] for x in da}
    ids_b = {x["directive_id"] for x in db}
    assert ids_a and ids_b, "expected directives for both sessions"
    assert ids_a.isdisjoint(ids_b), "directive ids leaked across sessions"

    sb = api_server.request("GET", "/api/sessions/sess-B").json()
    sa = api_server.request("GET", "/api/sessions/sess-A").json()
    assert sb["session_id"] == "sess-B" and sa["session_id"] == "sess-A"
    cd = sb.get("current_directive")
    if cd:
        assert cd.get("session_id") == "sess-B", (
            "session B is carrying session A's current directive -- isolation broken"
        )

    # a directive explicitly scoped to A must be invisible under B
    now = time.time()
    scoped = Directive(
        directive_id="dir_sess-A_scoped", session_id="sess-A", timestamp=float(now),
        action=DirectiveAction.PAUSE, target_actuator="noop", policy_version="p3.0.0",
        issued_at=float(now), ttl_s=120.0, nonce="nonce-scope",
        policy_table_version="pt-v1", evidence_hash=None, params={},
    )
    scoped.signature = sign_directive(scoped, SIGN_KEY)
    scoped.signed = True
    api_server.engine.store.add_directive(scoped, origin="engine")
    db_after = api_server.request("GET", "/api/decisions?sid=sess-B").json()
    assert not any(x["directive_id"] == "dir_sess-A_scoped" for x in db_after), (
        "session-A-scoped directive is visible under session B"
    )
