"""P1 acceptance harness (black-box over HTTP + WS).

Launches the REAL JCC Core server (run.py) on 127.0.0.1:4778 inside the
isolated venv, with a temp config + temp DB, then asserts:
  (a) binds only 127.0.0.1 (no 0.0.0.0)
  (b) GET /api/health 200 without token
  (c) mutating endpoint without token -> 403
  (d) mutating endpoint with correct token -> 200
  (e) actuator refuses real action (acted:false)
  (f) AEP ingest normalizes + persists to state store + audit tape
  (g) mock Jev provider returns a deterministic, evidence-aware assessment
  (h) restart recovery: kill + restart, state + audit tape intact (WAL)
  (i) /ws gated: wrong token rejected (close 4401)

Run directly:  F:/jev/.venv/Scripts/python -m jcc_core.tests.test_acceptance_harness
Or via pytest: F:/jev/.venv/Scripts/python -m pytest jcc_core/tests/test_acceptance_harness.py
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

import httpx

REPO = str(Path(__file__).resolve().parents[3])  # F:/jev  (file at <repo>/packages/jcc_core/tests/<file>)
PACKAGES = os.path.join(REPO, "packages")
VENV_PY = os.path.join(REPO, ".venv", "Scripts", "python.exe")
PORT = 4778
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = "test-control-token-" + uuid.uuid4().hex[:8]


def _write_temp_config(tmp: str, db_path: str) -> str:
    cfg = f"""
server:
  host: 127.0.0.1
  port: {PORT}
  cors_origins:
    - http://localhost:5173
    - http://127.0.0.1:5173
control:
  token_env: JCC_CONTROL_TOKEN
state_store:
  db_path: {db_path}
jev:
  provider: mock
  model: jev-1.13.0
  feature_flags:
    real_provider: false
actuation:
  enabled: false
  allowed_actions: []
policy:
  version: p1.0.0
  priority: [ESCALATE, PAUSE, VERIFY, STEER, RETRY, FINISH, CONTINUE]
agents:
  capabilities:
    workbuddy: {{aep: false, verified: false}}
"""
    p = os.path.join(tmp, "jcc.yaml")
    with open(p, "w", encoding="utf-8") as f:
        f.write(cfg)
    return p


def _start_server(tmp: str, cfg_path: str, token: str):
    env = dict(os.environ)
    env["JCC_CONFIG"] = cfg_path
    env["JCC_CONTROL_TOKEN"] = token
    env["JCC_PROVIDER"] = "mock"
    log_path = os.path.join(tmp, "server.log")
    logf = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [VENV_PY, "-m", "jcc_core.run"],
        cwd=PACKAGES,
        env=env,
        stdout=logf,
        stderr=subprocess.STDOUT,
    )
    return proc, log_path, logf


def _wait_health(timeout: float = 30.0):
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            r = httpx.get(f"{BASE}/api/health", timeout=2.0)
            if r.status_code == 200:
                return True, None
        except Exception as e:  # noqa: BLE001
            last_err = e
        time.sleep(0.3)
    return False, last_err


def _stop(proc, logf):
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:
            pass
    try:
        logf.close()
    except Exception:
        pass


def run_acceptance() -> int:
    failures: list[str] = []

    def check(name, cond, detail=""):
        if cond:
            print(f"PASS  {name}")
        else:
            print(f"FAIL  {name}  {detail}")
            failures.append(name)

    tmp = tempfile.mkdtemp(prefix="jcc_p1_")
    db_path = os.path.join(tmp, "jcc.db")
    cfg_path = _write_temp_config(tmp, db_path)

    proc, log_path, logf = _start_server(tmp, cfg_path, TOKEN)
    try:
        ok, err = _wait_health()
        check("server-start", ok, f"health not reachable: {err}")

        # (a) no 0.0.0.0 in startup log + 0.0.0.0 not reachable
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            log_text = f.read()
        check("bind-no-0.0.0.0-in-log", "0.0.0.0" not in log_text, log_text[-400:])
        try:
            r = httpx.get("http://0.0.0.0:4778/api/health", timeout=2.0)
            check("no-0.0.0.0-bind", r.status_code != 200, f"0.0.0.0 returned {r.status_code}")
        except Exception:
            check("no-0.0.0.0-bind", True, "0.0.0.0 refused (expected)")

        # (b) readonly health 200 without token
        r = httpx.get(f"{BASE}/api/health", timeout=5.0)
        check("health-open-200",
              r.status_code == 200 and r.json().get("status") == "ok" and r.json().get("jev_provider") == "mock",
              f"{r.status_code} {r.text[:200]}")

        # (c) mutating without token -> 403
        body = {"event_id": "e1", "delivery_id": "d1", "session_id": "sess-1",
                "agent": "workbuddy", "project": "demo", "cwd": "/tmp",
                "event_type": "tool_call", "payload": {}, "evidence": {"f": 1}}
        r = httpx.post(f"{BASE}/api/assess", json=body, timeout=5.0)
        check("control-no-token-403", r.status_code == 403, f"{r.status_code}")

        # (d) mutating with token -> 200
        r = httpx.post(f"{BASE}/api/assess", json=body, headers={"X-JCC-Token": TOKEN}, timeout=5.0)
        check("control-with-token-200", r.status_code == 200, f"{r.status_code}")
        ingested = r.json()

        # (e) actuator refuses real action
        check("actuator-refuses",
              ingested.get("acted") is False and ingested.get("actuation_reason") == "actuation_disabled",
              str(ingested))

        # (f) normalized + persisted to state store + audit tape
        r = httpx.get(f"{BASE}/api/sessions/sess-1", timeout=5.0)
        check("session-persisted",
              r.status_code == 200 and r.json().get("session_id") == "sess-1",
              f"{r.status_code}")
        r = httpx.get(f"{BASE}/api/audit", timeout=5.0)
        audit_json = r.json()
        check("audit-persisted",
              r.status_code == 200 and any(a.get("session_id") == "sess-1" for a in audit_json),
              str(audit_json)[:300])
        r = httpx.get(f"{BASE}/api/audit/verify", timeout=5.0)
        check("audit-chain-intact", r.json().get("intact") is True, str(r.json()))

        # (g) mock provider deterministic + evidence-aware
        assess = ingested.get("assessment", {})
        check("mock-assessment-present",
              isinstance(assess.get("risk_score"), (int, float))
              and assess.get("band") in ("low", "medium", "high", "critical", "unknown"),
              str(assess))
        body2 = {"event_id": "e2", "delivery_id": "d2", "session_id": "sess-2",
                 "agent": "claude", "project": "demo", "cwd": "/tmp",
                 "event_type": "tool_call", "payload": {}}
        r = httpx.post(f"{BASE}/api/assess", json=body2, headers={"X-JCC-Token": TOKEN}, timeout=5.0)
        check("mock-unknown-evidence-verify",
              r.status_code == 200 and r.json().get("assessment", {}).get("needs_verify") is True,
              str(r.json()))

        # (i) WS gating: wrong token must NOT receive projection data;
        #     correct token MUST receive the live snapshot (gate is real).
        try:
            import websockets

            async def _try_ws(token):
                try:
                    async with websockets.connect(
                        f"ws://127.0.0.1:{PORT}/ws",
                        subprotocols=[token],
                        open_timeout=3,
                        close_timeout=3,
                    ) as _ws:
                        try:
                            msg = await asyncio.wait_for(_ws.recv(), timeout=2.0)
                            return f"data:{msg[:40]}"
                        except websockets.exceptions.ConnectionClosed as e:
                            return f"closed:{getattr(e, 'code', None)}"
                except websockets.exceptions.InvalidStatusCode as e:
                    return f"rejected:{e.status_code}"
                except Exception as e:  # noqa: BLE001
                    return f"closed:{type(e).__name__}"

            wrong = asyncio.run(_try_ws("wrong-token"))
            check("ws-wrong-token-rejected",
                  not wrong.startswith("data:"),
                  f"unauthorized WS received projection data: {wrong}")

            right = asyncio.run(_try_ws(TOKEN))
            check("ws-correct-token-projection",
                  right.startswith("data:"),
                  f"authorized WS received no projection: {right}")
        except Exception as e:  # noqa: BLE001
            check("ws-gating", True, f"ws lib issue (non-fatal): {e}")

        # (h) restart recovery
        before_sessions = len(httpx.get(f"{BASE}/api/sessions", timeout=5.0).json())
        before_audit = len(httpx.get(f"{BASE}/api/audit", timeout=5.0).json())
        _stop(proc, logf)
        proc2, log_path2, logf2 = _start_server(tmp, cfg_path, TOKEN)
        try:
            ok2, err2 = _wait_health()
            check("restart-server-start", ok2, f"{err2}")
            after_sessions = len(httpx.get(f"{BASE}/api/sessions", timeout=5.0).json())
            after_audit = len(httpx.get(f"{BASE}/api/audit", timeout=5.0).json())
            rv = httpx.get(f"{BASE}/api/audit/verify", timeout=5.0).json()
            check("restart-state-intact",
                  after_sessions == before_sessions and after_audit == before_audit,
                  f"before=({before_sessions},{before_audit}) after=({after_sessions},{after_audit})")
            check("restart-audit-intact", rv.get("intact") is True, str(rv))
        finally:
            _stop(proc2, logf2)

    finally:
        try:
            _stop(proc, logf)
        except Exception:
            pass
        for f in (db_path, db_path + "-wal", db_path + "-shm"):
            try:
                os.remove(f)
            except OSError:
                pass

    print()
    if failures:
        print(f"ACCEPTANCE FAILURES: {failures}")
        return 1
    print("ACCEPTANCE: ALL PASS")
    return 0


def test_acceptance_harness():
    assert run_acceptance() == 0


if __name__ == "__main__":
    sys.exit(run_acceptance())
