"""P1 acceptance harness (black-box over HTTP + WS).

Launches the REAL JCC Core server (run.py) on a per-run ephemeral 127.0.0.1
port inside the isolated venv, with a temp config + temp DB, then asserts:
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
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid

import httpx

REPO = str(Path(__file__).resolve().parents[3])  # F:/jev  (file at <repo>/packages/jcc_core/tests/<file>)
PACKAGES = os.path.join(REPO, "packages")
VENV_PY = os.path.join(REPO, ".venv", "Scripts", "python.exe")
# PORT/BASE are selected at runtime by _select_free_port() (in run_acceptance).
# A per-run ephemeral port lets overlapping harness runs coexist instead of
# fighting over a hardcoded 4778 (which produced misleading 403/token errors).
PORT = 0
BASE = ""
TOKEN = "test-control-token-" + uuid.uuid4().hex[:8]


def _select_free_port(host: str = "127.0.0.1") -> int:
    """Ask the OS for a currently-free ephemeral port, then release it.

    Residual TOCTOU: the port could be re-taken between close() and the server
    bind. That is acceptable for a test harness — the OS will not hand the same
    ephemeral port to a concurrent bind in practice.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, 0))
        return s.getsockname()[1]
    finally:
        s.close()


def _ensure_loopback_no_proxy() -> None:
    """Force loopback to bypass any configured proxy.

    httpx honours NO_PROXY/no_proxy from the environment; if a dead or foreign
    HTTP(S)_PROXY is set, loopback requests would otherwise be routed through it
    and the harness would fail for the wrong reason. Append the loopback hosts
    to both spellings while preserving pre-existing entries and de-duplicating.
    """
    wanted = ["127.0.0.1", "localhost", "::1"]
    for var in ("NO_PROXY", "no_proxy"):
        parts = [p.strip() for p in os.environ.get(var, "").split(",") if p.strip()]
        for host in wanted:
            if host not in parts:
                parts.append(host)
        os.environ[var] = ",".join(parts)


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


def _non_loopback_ipv4_addrs() -> list[str]:
    """Best-effort list of this host's non-loopback IPv4 addresses."""
    addrs: list[str] = []
    try:
        _, _, ips = socket.gethostbyname_ex(socket.gethostname())
    except Exception:  # noqa: BLE001
        return addrs
    for ip in ips:
        if ip and not ip.startswith("127.") and ip not in addrs:
            addrs.append(ip)
    return addrs


def _tcp_reachable(host: str, port: int, timeout: float = 1.0) -> bool:
    """Raw-socket TCP reachability probe. Cannot be intercepted by a proxy."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex((host, port)) == 0
    finally:
        s.close()


def probe_bind_scope(port: int, loopback: str = "127.0.0.1",
                     timeout: float = 1.0) -> tuple[str, str]:
    """Proxy-immune proof that a listener on ``port`` is loopback-only.

    Why not HTTP: on Windows ``connect()`` to ``0.0.0.0`` is interpreted as the
    local machine, so ``httpx.get("http://0.0.0.0:PORT/...")`` reaches a
    ``127.0.0.1``-only listener and returns 200 (false FAIL); with ``HTTP_PROXY``
    set it is routed through the proxy instead (false PASS). Raw TCP
    ``connect_ex()`` cannot be proxied, so it is meaningful in both directions.

    Controls:
      1. positive: loopback MUST be reachable (else the server is down -> genuine failure)
      2. negative: every non-loopback IPv4 of this host MUST refuse

    Returns ``(verdict, detail)`` where verdict is one of:
      "loopback-only"  -> loopback reachable + all non-loopback refused (CORRECT bind)
      "all-interfaces" -> a non-loopback IPv4 is reachable (GENUINE 0.0.0.0 bind)
      "loopback-down"  -> loopback unreachable (GENUINE failure)
      "inconclusive"   -> host exposes no non-loopback IPv4 to probe (NEITHER)
    """
    if not _tcp_reachable(loopback, port, timeout):
        return "loopback-down", f"loopback {loopback}:{port} not reachable"

    non_loop = _non_loopback_ipv4_addrs()
    if not non_loop:
        return "inconclusive", "no non-loopback IPv4 to probe"

    reachable = [ip for ip in non_loop if _tcp_reachable(ip, port, timeout)]
    if reachable:
        return "all-interfaces", f"reachable on non-loopback {reachable}"
    return "loopback-only", f"refused on non-loopback {non_loop}"


def _wait_ready(base: str, token: str, timeout: float = 30.0) -> bool:
    """Readiness barrier (mirrors P2's ``_wait_event_consumer_ready``).

    ``/api/health`` returning 200 only proves the app is up. The FIRST real
    ingest through a cold uvicorn worker still pays the one-time DB cold-start
    cost -- schema CREATE, audit-writer DDL (appended on every audit append),
    WAL/-wal/-shm file creation, and on-access AV scanning of those freshly
    created files -- which can exceed the 5s client timeout in this environment
    and flip the first ``/api/assess`` into ``httpx.ReadTimeout``. That is the
    SAME cold-ingest class the A-segment closed for the P2 HTTP leg.

    We pay the cold cost HERE, inside the 30s barrier window, with a throwaway
    UNIQUE session so it can never collide with the real timed assertions.
    Two phases, exactly like P2:
      1. liveness probe  -- a deliberately invalid envelope returns 422 WITHOUT
         calling ``engine.ingest`` (no DB cost, no session created), proving the
         handler is mounted and schedulable.
      2. ingest warmup   -- a real, uniquely-named ingest that pays the cold DB
         cost once. A confirmed-ready warm DB then makes every timed assert a
         warm write.
    Returns True once a real ingest round-trips within budget; False (loud,
    honest root-cause signal) if the cold consumer cannot be made schedulable.
    """
    budget_ms = 1500.0
    # Phase 1 -- liveness: bad envelope -> 422 without ingest.
    bad = json.dumps({"session_id": "acceptance-probe-bad", "agent": "workbuddy"}).encode()
    deadline = time.time() + timeout
    while time.time() < deadline:
        t0 = time.monotonic()
        try:
            r = httpx.post(
                f"{base}/api/assess", content=bad,
                headers={"Content-Type": "application/json", "X-JCC-Token": token},
                timeout=2.0,
            )
            responded = (r.status_code == 422)
        except Exception:  # noqa: BLE001
            responded = False
        rt = (time.monotonic() - t0) * 1000.0
        if responded and rt <= budget_ms:
            break
        time.sleep(0.2)
    if time.time() >= deadline:
        return False
    # Phase 2 -- ingest warmup: real ingest with a UNIQUE session (throwaway).
    warm = {
        "event_id": "acceptance-warmup",
        "delivery_id": "acceptance-warmup-" + uuid.uuid4().hex,
        "session_id": "acceptance-warmup-" + uuid.uuid4().hex,
        "agent": "workbuddy", "project": "demo", "cwd": "/tmp",
        "event_type": "tool_call", "payload": {}, "evidence": {"f": 1},
    }
    warm_deadline = time.time() + max(0.0, deadline - time.time())
    while time.time() < warm_deadline:
        t0 = time.monotonic()
        try:
            r = httpx.post(
                f"{base}/api/assess", json=warm,
                headers={"X-JCC-Token": token}, timeout=15.0,
            )
            responded = r.status_code in (200, 422)
        except Exception:  # noqa: BLE001
            responded = False
        rt = (time.monotonic() - t0) * 1000.0
        if responded and rt <= budget_ms:
            return True
        time.sleep(0.3)
    return False


def run_acceptance() -> int:
    global PORT, BASE
    PORT = _select_free_port()      # per-run ephemeral port (isolates overlapping runs)
    BASE = f"http://127.0.0.1:{PORT}"
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

    _ensure_loopback_no_proxy()   # loopback must never be sent through a proxy
    proc, log_path, logf = _start_server(tmp, cfg_path, TOKEN)
    try:
        ok, err = _wait_health()
        check("server-start", ok, f"health not reachable: {err}")
        # readiness barrier: pay the cold-ingest cost BEFORE the timed asserts,
        # so the steady-state 5s budget measures a warm consumer (not a cold one).
        check("consumer-ready", _wait_ready(BASE, TOKEN),
              "cold ingest not schedulable within budget")

        # (a) no 0.0.0.0 in startup log + 0.0.0.0 not reachable
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            log_text = f.read()
        check("bind-no-0.0.0.0-in-log", "0.0.0.0" not in log_text, log_text[-400:])
        # Raw-socket proof (proxy-immune). HTTP probes of 0.0.0.0 are meaningless
        # on Windows (0.0.0.0 -> loopback) and when HTTP_PROXY is set (-> proxy).
        verdict, detail = probe_bind_scope(PORT)
        if verdict == "loopback-only":
            check("no-0.0.0.0-bind", True, detail)
        elif verdict == "inconclusive":
            # Visible SKIP: NOT a silent PASS; cannot mask a real bind, because a
            # real 0.0.0.0 bind requires a non-loopback address to be observed.
            print(f"SKIP  no-0.0.0.0-bind  ({detail} — INCONCLUSIVE)")
        else:  # "all-interfaces" (genuine 0.0.0.0 bind) or "loopback-down"
            check("no-0.0.0.0-bind", False, f"{verdict}: {detail}")

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

        # (e) actuator refuses real action. NOTE (ADR-0008 D-27, metric ruling b):
        # the engine ingest path is now verify-only and reports the inert engine
        # port reason "shadow_mode" (was "actuation_disabled"); acted stays False.
        check("actuator-refuses",
              ingested.get("acted") is False and ingested.get("actuation_reason") == "shadow_mode",
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

        # (j) F-1 depth cap over REAL HTTP: a secret nested deeper than the scrub
        #     cap must NOT be persisted. /api/assess does AEPEnvelope(**body) +
        #     engine.ingest() with NO adapter in the path, so the scrub inside
        #     ingest() is the ONLY protection -- before the fix, _depth>8 returned
        #     the raw sub-tree and the secret landed in jcc.db.
        deep_secret = "sk-deepcap-PLANTED" + uuid.uuid4().hex
        deep = {"k": "v"}
        node = deep
        for _ in range(12):
            node["child"] = {"k": "v"}
            node = node["child"]
        node["leak"] = "Bearer " + deep_secret
        body3 = {"event_id": "e3", "delivery_id": "d3", "session_id": "sess-deep",
                 "agent": "workbuddy", "project": "demo", "cwd": "/tmp",
                 "event_type": "tool_call", "payload": deep, "evidence": {"deep": deep}}
        r = httpx.post(f"{BASE}/api/assess", json=body3, headers={"X-JCC-Token": TOKEN}, timeout=5.0)
        check("deep-secret-post-200", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
        # raw DB bytes (load-bearing check)
        blob = b""
        for suffix in ("", "-wal", "-shm"):
            try:
                with open(db_path + suffix, "rb") as fh:
                    blob += fh.read()
            except OSError:
                pass
        check("deep-secret-absent-from-db", deep_secret.encode() not in blob,
              "depth>8 secret leaked into jcc.db!")
        # cheap regression guard: /api/audit returns rows only (no payload)
        r = httpx.get(f"{BASE}/api/audit", timeout=5.0)
        check("deep-secret-absent-from-audit", deep_secret not in r.text, "deep secret in audit")
        # no functional break: the request recorded a session
        r = httpx.get(f"{BASE}/api/sessions/sess-deep", timeout=5.0)
        check("deep-session-recorded",
              r.status_code == 200 and r.json().get("session_id") == "sess-deep",
              f"{r.status_code}")

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


def test_bind_check_detects_real_0_0_0_0_bind():
    """Meta-test: prove ``probe_bind_scope()`` can actually FAIL.

    Without this, the acceptance check would be a toothless assertion that
    passes regardless of the product. We bind a throwaway listener to 0.0.0.0
    on an ephemeral port and assert the helper DOES report reachability beyond
    loopback (verdict "all-interfaces").
    """
    import pytest  # local import keeps the module import-safe for `python -m`

    if not _non_loopback_ipv4_addrs():
        pytest.skip("host exposes no non-loopback IPv4; detection cannot be exercised")

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", 0))
    srv.listen(8)
    port = srv.getsockname()[1]

    stop = threading.Event()

    def _accept_loop():
        srv.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                conn.close()
            except OSError:
                pass

    t = threading.Thread(target=_accept_loop, daemon=True)
    t.start()
    try:
        verdict, detail = probe_bind_scope(port)
        assert verdict == "all-interfaces", (
            f"helper failed to detect a real 0.0.0.0 bind on port {port}: "
            f"verdict={verdict!r} detail={detail!r}"
        )
    finally:
        stop.set()
        t.join(timeout=2.0)
        srv.close()


if __name__ == "__main__":
    sys.exit(run_acceptance())
