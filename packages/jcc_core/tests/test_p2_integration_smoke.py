"""P2 integration smoke test: four agents -> JCC Core, no cross-talk.

Proves (per the P2 task acceptance criteria):
  * real distinction ``agent=workbuddy|claude|codex|hermes`` in the state store;
  * per-agent sessions do NOT cross-talk;
  * no duplicate state advance on redelivery (dedup on event_id + delivery_id);
  * no cwd identity collision (same cwd, different sessions stay distinct);
  * NO secret leakage (planted secret searched in the whole SQLite DB bytes);
  * the Hermes inbound receiver enforces HMAC + timestamp + delivery_id replay
    + size limit + session isolation.

Part A runs in-process against a real CoreEngine (isolated temp SQLite).
Part B unit-tests the hardened receiver.
Part C runs the REAL JCC server over HTTP and emits one event per agent.

Run: F:/jev/.venv/Scripts/python -m pytest jcc_core/tests/test_p2_integration_smoke.py -q
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]  # F:/jev
PACKAGES = REPO / "packages"
_INTEG_COMMON = REPO / "integrations" / "_common"
for p in (str(PACKAGES), str(_INTEG_COMMON)):
    if p not in sys.path:
        sys.path.insert(0, p)

from jcc_core.actuator import Actuator
from jcc_core.config import Config, load_config
from jcc_core.core import CoreEngine
from jcc_core.providers import build_provider
from jcc_core.state_store import StateStore
from jcc_core.adapters import ClaudeAdapter, CodexAdapter, HermesAdapter, WorkBuddyAdapter

from jcc_emit import emit_event  # noqa: E402
from jcc_receiver import (  # noqa: E402
    HDR_AGENT,
    HDR_DELIVERY,
    HDR_SIGNATURE,
    HDR_TIMESTAMP,
    ReceiverRejection,
    SecureWebhookReceiver,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PLANTED_SECRET = "sk-live-PLANTEDSECRETdeadbeef0123456789"


def _engine(tmp: str) -> CoreEngine:
    cfg = load_config(os.environ.get("JCC_CONFIG"))
    store = StateStore(os.path.join(tmp, "jcc.db"))
    provider = build_provider(cfg)
    actuator = Actuator(enabled=False, allowed_actions=[])
    return CoreEngine(store, provider, actuator, cfg)


def _raw(agent: str, name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _norm(adapter, raw: dict):
    return asyncio.run(adapter.normalize(raw))


# --------------------------------------------------------------------------- #
# Part A — in-process engine ingest, four agents
# --------------------------------------------------------------------------- #
# one identified fixture per agent, with a shared cwd to force a collision test
PER_AGENT_FIXTURE = {
    "workbuddy": ("workbuddy_hook_session_start.json", WorkBuddyAdapter),
    "claude": ("claude_transcript_record.json", ClaudeAdapter),
    "codex": ("codex_rollout_session_meta.json", CodexAdapter),
    "hermes": ("hermes_hook_session_start.json", HermesAdapter),
}
SHARED_CWD = "F:\\jev"  # deliberately identical across agents


def _force_cwd(agent: str, raw: dict) -> dict:
    """Inject the shared cwd at the location each native schema stores it."""
    if agent == "hermes":
        raw.setdefault("context", {})["cwd"] = SHARED_CWD
    elif agent == "codex":
        raw.setdefault("payload", {})["cwd"] = SHARED_CWD
    else:
        raw["cwd"] = SHARED_CWD
    return raw


def test_four_agents_distinct_and_no_crosstalk_and_no_duplicate_advance():
    with tempfile.TemporaryDirectory(prefix="jcc_p2a_") as tmp:
        engine = _engine(tmp)
        seen_sessions = {}
        for agent, (fname, cls) in PER_AGENT_FIXTURE.items():
            raw = _force_cwd(agent, _raw(agent, fname))  # force same-cwd collision pressure
            env = _norm(cls(), raw)
            out = engine.ingest(env)
            assert out.get("deduped") is not True, f"{agent} first ingest should not dedup"
            seen_sessions[agent] = env.session_id

            # redelivery of the exact same event must NOT advance state again
            s_before = engine.store.get_session(env.session_id)
            out2 = engine.ingest(env)
            assert out2.get("deduped") is True, f"{agent} redelivery must dedup"
            s_after = engine.store.get_session(env.session_id)
            assert s_after.event_count == s_before.event_count, f"{agent} duplicate state advance!"

        # distinctness: 4 agents -> 4 distinct session ids, all present
        assert len(set(seen_sessions.values())) == 4, seen_sessions
        rows = {r["session_id"]: r for r in engine.store.list_sessions()}
        for agent, sid in seen_sessions.items():
            assert sid in rows, f"missing session for {agent}"
            assert rows[sid]["agent"] == agent, f"cross-talk: {sid} agent={rows[sid]['agent']} != {agent}"

        # no cwd identity collision: 4 rows share cwd but are 4 sessions
        shared = [r for r in rows.values() if r.get("cwd") == SHARED_CWD]
        assert len(shared) == 4, f"expected 4 same-cwd sessions, got {len(shared)}"
        assert len({r["session_id"] for r in shared}) == 4

        # audit tape intact
        ok, n = engine.store.verify_audit()
        assert ok is True and n >= 4


def test_no_secret_leakage_in_state_store_bytes():
    """Plant a secret in a PreToolUse event; assert it never reaches disk."""
    with tempfile.TemporaryDirectory(prefix="jcc_p2secret_") as tmp:
        engine = _engine(tmp)
        raw = _raw("claude", "claude_hook_pre_tool_use.json")
        raw["tool_input"] = {"command": "curl", "api_key": PLANTED_SECRET, "password": "hunter2-" + PLANTED_SECRET}
        raw["authorization"] = "Bearer " + PLANTED_SECRET
        env = _norm(ClaudeAdapter(), raw)
        engine.ingest(env)

        blob = b""
        for suffix in ("", "-wal", "-shm"):
            f = Path(tmp) / ("jcc.db" + suffix)
            if f.exists():
                blob += f.read_bytes()
        assert PLANTED_SECRET.encode() not in blob, "PLANTED SECRET LEAKED to the state store!"
        assert b"hunter2" not in blob, "planted password leaked"
        # sanity: the store did receive the event
        assert engine.store.get_session(env.session_id) is not None


# --------------------------------------------------------------------------- #
# Part B — hardened receiver controls
# --------------------------------------------------------------------------- #
def _signed_headers(rcv: SecureWebhookReceiver, agent: str, body: bytes, **kw):
    return rcv.sender_headers(agent, body, **kw)


def test_receiver_accepts_valid_and_ingests():
    with tempfile.TemporaryDirectory(prefix="jcc_p2b_") as tmp:
        engine = _engine(tmp)
        rcv = SecureWebhookReceiver("unit-secret")
        raw = _raw("hermes", "hermes_hook_agent_step.json")
        body = json.dumps(raw).encode()
        headers = _signed_headers(rcv, "hermes", body)
        res = rcv.handle(headers=headers, body=body, engine=engine)
        assert res["accepted"] is True, res
        assert engine.store.get_session(raw["context"]["session_id"]) is not None


def test_receiver_rejects_bad_signature():
    rcv = SecureWebhookReceiver("unit-secret")
    body = b'{"event":"agent:step","context":{"session_id":"s1"}}'
    headers = _signed_headers(rcv, "hermes", body)
    headers[HDR_SIGNATURE] = "sha256=" + "0" * 64
    res = rcv.handle(headers=headers, body=body)
    assert res == {"accepted": False, "reason": "bad_signature"}, res


def test_receiver_rejects_stale_timestamp():
    rcv = SecureWebhookReceiver("unit-secret", max_skew=60)
    body = b'{"event":"agent:step","context":{"session_id":"s1"}}'
    headers = rcv.sender_headers("hermes", body, timestamp=time.time() - 1000)
    res = rcv.handle(headers=headers, body=body)
    assert res["reason"] == "timestamp_out_of_window", res


def test_receiver_rejects_replay():
    with tempfile.TemporaryDirectory(prefix="jcc_p2replay_") as tmp:
        engine = _engine(tmp)
        rcv = SecureWebhookReceiver("unit-secret")
        body = b'{"event":"agent:step","context":{"session_id":"s-replay","iteration":1}}'
        headers = rcv.sender_headers("hermes", body, delivery_id="fixed-1")
        assert rcv.handle(headers=headers, body=body, engine=engine)["accepted"] is True
        second = rcv.handle(headers=headers, body=body, engine=engine)
        assert second == {"accepted": False, "reason": "replay"}, second


def test_receiver_rejects_oversized_body():
    rcv = SecureWebhookReceiver("unit-secret", max_body=64)
    body = b'{"event":"agent:step","context":{"session_id":"' + b"x" * 200 + b'"}}'
    headers = _signed_headers(rcv, "hermes", body)
    res = rcv.handle(headers=headers, body=body)
    assert res["reason"] == "payload_too_large", res


def test_receiver_rejects_missing_headers():
    rcv = SecureWebhookReceiver("unit-secret")
    body = b"{}"
    res = rcv.handle(headers={}, body=body)
    assert res["reason"] == "missing_header", res


def test_receiver_enforces_session_isolation():
    with tempfile.TemporaryDirectory(prefix="jcc_p2iso_") as tmp:
        engine = _engine(tmp)
        rcv = SecureWebhookReceiver("unit-secret")
        # bind session to workbuddy
        b1 = json.dumps({"hook_event_name": "SessionStart", "session_id": "SHARED-S", "cwd": "F:\\jev"}).encode()
        assert rcv.handle(headers=_signed_headers(rcv, "workbuddy", b1), body=b1, engine=engine)["accepted"] is True
        # a claude source must NOT be able to reuse the same session id
        b2 = json.dumps({"event": "session:start", "context": {"session_id": "SHARED-S"}}).encode()
        res = rcv.handle(headers=_signed_headers(rcv, "claude", b2), body=b2, engine=engine)
        assert res == {"accepted": False, "reason": "session_isolation"}, res


def test_receiver_rejects_unidentifiable_event():
    rcv = SecureWebhookReceiver("unit-secret")
    body = json.dumps({"event": "unknown", "context": {"platform": "x"}}).encode()
    res = rcv.handle(headers=_signed_headers(rcv, "hermes", body), body=body)
    assert res["accepted"] is False and res["reason"] == "unidentifiable", res


# --------------------------------------------------------------------------- #
# Part C — real HTTP server end-to-end
# --------------------------------------------------------------------------- #
PORT = 4799
TOKEN = "p2-token-" + uuid.uuid4().hex[:8]


def _write_cfg(tmp: str, db: str) -> str:
    cfg = f"""
server:
  host: 127.0.0.1
  port: {PORT}
  cors_origins: [http://127.0.0.1:5173]
control:
  token_env: JCC_CONTROL_TOKEN
state_store:
  db_path: {db}
jev:
  provider: mock
  model: jev-1.13.0
  feature_flags: {{real_provider: false}}
actuation:
  enabled: false
  allowed_actions: []
policy:
  version: p1.0.0
  priority: [ESCALATE, PAUSE, VERIFY, STEER, RETRY, FINISH, CONTINUE]
agents:
  capabilities:
    workbuddy: {{aep: true, verified: true}}
"""
    p = os.path.join(tmp, "jcc.yaml")
    Path(p).write_text(cfg, encoding="utf-8")
    return p


def _wait_health(base: str, timeout: float = 30.0) -> bool:
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/api/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    return False


@pytest.mark.skipif(os.environ.get("JCC_SKIP_HTTP") == "1", reason="HTTP leg disabled")
def test_http_end_to_end_four_agents_distinct_no_secret_leak():
    import urllib.request

    venv_py = str(REPO / ".venv" / "Scripts" / "python.exe")
    if not Path(venv_py).exists():
        pytest.skip("venv python unavailable")

    with tempfile.TemporaryDirectory(prefix="jcc_p2c_") as tmp:
        db = os.path.join(tmp, "jcc.db")
        cfg = _write_cfg(tmp, db)
        env = dict(os.environ)
        env.update(JCC_CONFIG=cfg, JCC_CONTROL_TOKEN=TOKEN, JCC_PROVIDER="mock")
        logf = open(os.path.join(tmp, "server.log"), "w", encoding="utf-8")
        proc = subprocess.Popen(
            [venv_py, "-m", "jcc_core.run"], cwd=str(PACKAGES), env=env,
            stdout=logf, stderr=subprocess.STDOUT,
        )
        base = f"http://127.0.0.1:{PORT}"
        try:
            assert _wait_health(base), "server did not become healthy"
            # emit one identified event per agent over HTTP
            for agent, (fname, cls) in PER_AGENT_FIXTURE.items():
                raw = _force_cwd(agent, _raw(agent, fname))
                res = emit_event(agent, raw, url=base + "/api/assess", token=TOKEN)
                assert res["status"] == "sent", f"{agent}: {res}"

            # plant a secret through a 5th (claude) event and confirm it is absent
            sraw = _raw("claude", "claude_hook_pre_tool_use.json")
            sraw["tool_input"] = {"api_key": PLANTED_SECRET}
            sraw["session_id"] = "http-secret-session"
            emit_event("claude", sraw, url=base + "/api/assess", token=TOKEN)

            with urllib.request.urlopen(base + "/api/sessions", timeout=5) as r:
                sessions = json.loads(r.read())
            agents = {s["agent"] for s in sessions}
            assert {"workbuddy", "claude", "codex", "hermes"} <= agents, agents

            with urllib.request.urlopen(base + "/api/audit?limit=500", timeout=5) as r:
                audit = r.read().decode()
            assert PLANTED_SECRET not in audit, "secret leaked into the audit endpoint"

            # no cwd collision among the four agent sessions
            four_sids = set()
            for agent, (fname, cls) in PER_AGENT_FIXTURE.items():
                env = _norm(cls(), _force_cwd(agent, _raw(agent, fname)))
                four_sids.add(env.session_id)
            four = [s for s in sessions if s["session_id"] in four_sids]
            assert len({s["session_id"] for s in four}) == 4, four

            with urllib.request.urlopen(base + "/api/audit/verify", timeout=5) as r:
                assert json.loads(r.read())["intact"] is True

            blob = b""
            for suffix in ("", "-wal", "-shm"):
                f = Path(db + suffix)
                if f.exists():
                    blob += f.read_bytes()
            assert PLANTED_SECRET.encode() not in blob, "secret leaked into the HTTP state store"
        finally:
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                except Exception:
                    pass
            logf.close()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
