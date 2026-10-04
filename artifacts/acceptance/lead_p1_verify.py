"""Lead INDEPENDENT cross-check of P1 CORE (JCC Core control plane).

NOT a substitute for the formal independent SRE gate (Harbor). Cross-check only.

Boots the real server and asserts (socket-based; no netstat dependency):
  loopback reachable / non-loopback NOT reachable, health, token 403/200
  (X-JCC-Token + Bearer), actuator refuses all real side-effects, audit
  hash-chain intact, CORS allowlist, WS gating, WAL restart recovery,
  clean shutdown. Writes lead_p1_verify.txt.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

#: Repo root, DERIVED from this file's location rather than hardcoded. The
#: literal this replaced (``r"F:\jev"``) names a drive that does not exist on the
#: current host, so every path built from it below resolved to nothing.
#: ``JCC_ROOT`` remains the operator override.
ROOT = os.environ.get("JCC_ROOT") or str(Path(__file__).resolve().parents[2])
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
PKG = os.path.join(ROOT, "packages")
DB = os.path.join(ROOT, "runtime", "jcc", "_lead_verify.db")
OUT = os.path.join(ROOT, "artifacts", "acceptance", "lead_p1_verify.txt")
SLOG = os.path.join(ROOT, "artifacts", "acceptance", "_lead_server.log")
BASE = "http://127.0.0.1:4778"
BINDPORT = 4778
TOKEN = "leadverify-" + format(int(time.time()), "x")

lines: list = []
results: list = []


def emit(s="") -> None:
    print(s)
    lines.append(str(s))


def rec(name, ok, detail="") -> None:
    results.append((name, bool(ok)))
    emit(f"[{'PASS' if ok else 'FAIL'}] {name} :: {detail}")


def port_open(host, port, timeout=1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:  # noqa: BLE001
        return False


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:  # noqa: BLE001
        return None
    finally:
        s.close()


def http(method, path, body=None, headers=None, timeout=6.0):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read().decode()
    except Exception as e:  # noqa: BLE001
        return None, {}, repr(e)


def pre_clean() -> None:
    ps = (
        "Get-NetTCPConnection -LocalPort 4778 -State Listen -ErrorAction SilentlyContinue | "
        "ForEach-Object { $p = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue; "
        "if ($p -and $p.ProcessName -like 'python*') { Stop-Process -Id $_.OwningProcess -Force } }"
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       capture_output=True, text=True, timeout=30)
    except Exception:  # noqa: BLE001
        pass
    time.sleep(1.0)


def start_server():
    env = dict(os.environ)
    env["PYTHONPATH"] = PKG
    env["JCC_CONTROL_TOKEN"] = TOKEN
    env["JCC_DB_PATH"] = DB
    fh = open(SLOG, "a", encoding="utf-8", errors="replace")
    fh.write(f"\n--- start {time.strftime('%H:%M:%S')} ---\n")
    fh.flush()
    p = subprocess.Popen([PY, "-m", "jcc_core.run"], cwd=PKG, env=env,
                         stdout=fh, stderr=subprocess.STDOUT)
    up = False
    for _ in range(60):
        if port_open("127.0.0.1", BINDPORT, timeout=1.0):
            s, _, _ = http("GET", "/api/health")
            if s == 200:
                up = True
                break
        if p.poll() is not None:
            break
        time.sleep(0.5)
    return p, up


def stop_server(p) -> None:
    try:
        p.terminate()
        try:
            p.wait(timeout=10)
        except Exception:  # noqa: BLE001
            p.kill()
    except Exception:  # noqa: BLE001
        pass


def wait_closed(host, port, tries=24) -> bool:
    for _ in range(tries):
        if not port_open(host, port, timeout=0.5):
            return True
        time.sleep(0.5)
    return False


def main() -> None:
    emit("=== LEAD INDEPENDENT CROSS-CHECK - P1 CORE (socket-based) ===")
    emit(f"time={time.strftime('%Y-%m-%d %H:%M:%S')} python={PY}")
    emit(f"token={TOKEN} db={DB}")

    pre_clean()
    emit(f"pre-clean: port4778 open? {port_open('127.0.0.1', BINDPORT)}")

    try:
        if os.path.exists(DB):
            os.remove(DB)
    except Exception as e:  # noqa: BLE001
        emit(f"db reset warn: {e!r}")

    # 0) unit + harness suite
    try:
        env = dict(os.environ)
        env["PYTHONPATH"] = PKG
        env["JCC_DB_PATH"] = DB
        r = subprocess.run(
            [PY, "-m", "pytest", os.path.join(PKG, "jcc_core", "tests"), "-q", "--no-header"],
            cwd=PKG, env=env, capture_output=True, text=True, timeout=300,
        )
        tail = "\n".join((r.stdout or "").strip().splitlines()[-4:])
        emit("--- pytest ---")
        emit(tail)
        rec("suite: pytest 29 passed", r.returncode == 0 and "29 passed" in (r.stdout or ""),
            tail.replace("\n", " | "))
    except Exception as e:  # noqa: BLE001
        rec("suite: pytest 29 passed", False, f"error {e!r}")

    # 1) boot
    p, up = start_server()
    rec("server boots / health 200", up, f"pid={p.pid}")
    if not up:
        try:
            with open(SLOG, encoding="utf-8", errors="replace") as f:
                emit("server log tail:")
                emit("\n".join(f.read().strip().splitlines()[-20:]))
        except Exception:  # noqa: BLE001
            pass
        stop_server(p)
        return

    # 2) bind
    try:
        ok_loop = port_open("127.0.0.1", BINDPORT)
        ip = lan_ip()
        lan_reach = port_open(ip, BINDPORT) if ip else None
        rec("bind: 127.0.0.1 reachable", ok_loop, "127.0.0.1:4778")
        rec("bind: non-loopback NOT reachable", lan_reach is False,
            f"lan_ip={ip} reachable={lan_reach}")
    except Exception as e:  # noqa: BLE001
        rec("bind check", False, f"error {e!r}")

    # 3) health
    try:
        s, _, b = http("GET", "/api/health")
        hj = json.loads(b) if b else {}
        rec("health 200", s == 200, f"status={s}")
        rec("health: provider=mock", hj.get("jev_provider") == "mock", str(hj.get("jev_provider")))
        rec("health: actuation_enabled=false", hj.get("actuation_enabled") is False,
            str(hj.get("actuation_enabled")))
        rec("health: wal=true", hj.get("wal") is True, str(hj.get("wal")))
    except Exception as e:  # noqa: BLE001
        rec("health", False, f"error {e!r}")

    # 4) token gate
    try:
        s, _, _ = http("POST", "/api/assess", body={"session_id": "x"})
        rec("assess no token -> 403", s == 403, f"status={s}")
        s, _, _ = http("POST", "/api/assess", body={"session_id": "x"}, headers={"X-JCC-Token": "wrong"})
        rec("assess wrong token -> 403", s == 403, f"status={s}")
    except Exception as e:  # noqa: BLE001
        rec("token gate", False, f"error {e!r}")

    env_body = {
        "event_id": "evt-lead-1", "delivery_id": "dlv-lead-1", "session_id": "sess-lead-1",
        "agent": "workbuddy", "project": "lead-verify", "cwd": r"F:\jev",
        "event_type": "session_start", "payload": {"note": "lead"}, "evidence": {"ok": True},
    }
    try:
        s, _, _ = http("POST", "/api/assess", body=env_body, headers={"X-JCC-Token": TOKEN})
        rec("assess X-JCC-Token -> 200", s == 200, f"status={s}")
        s, _, _ = http("POST", "/api/assess", body=env_body, headers={"Authorization": "Bearer " + TOKEN})
        rec("assess Bearer -> 200", s == 200, f"status={s}")
    except Exception as e:  # noqa: BLE001
        rec("assess positive", False, f"error {e!r}")

    # 5) actuator refuses
    try:
        s, _, b = http("POST", "/api/steer", body={"session_id": "sess-lead-1"}, headers={"X-JCC-Token": TOKEN})
        sj = json.loads(b) if b else {}
        act = sj.get("actuation", {}) if isinstance(sj, dict) else {}
        rec("steer -> 200", s == 200, f"status={s}")
        rec("actuator refused (acted=false/actuation_disabled)",
            act.get("acted") is False and act.get("reason") == "actuation_disabled", json.dumps(act))
    except Exception as e:  # noqa: BLE001
        rec("actuator", False, f"error {e!r}")

    # 6) audit
    try:
        s, _, b = http("GET", "/api/audit/verify")
        aj = json.loads(b) if b else {}
        rec("audit chain intact", aj.get("intact") is True, json.dumps(aj))
    except Exception as e:  # noqa: BLE001
        rec("audit", False, f"error {e!r}")

    # 7) CORS
    try:
        s, h, _ = http("GET", "/api/health", headers={"Origin": "http://localhost:3000"})
        rec("CORS allowed echoed", h.get("access-control-allow-origin") == "http://localhost:3000",
            str(h.get("access-control-allow-origin")))
        s, h, _ = http("GET", "/api/health", headers={"Origin": "http://evil.attacker.com"})
        rec("CORS evil not echoed", h.get("access-control-allow-origin") is None,
            str(h.get("access-control-allow-origin")))
    except Exception as e:  # noqa: BLE001
        rec("cors", False, f"error {e!r}")

    # 8) WS
    try:
        import asyncio

        import websockets

        async def ws_probe():
            res = {}
            try:
                async with websockets.connect("ws://127.0.0.1:4778/ws", open_timeout=5) as ws:
                    try:
                        await asyncio.wait_for(ws.recv(), timeout=2)
                        res["no_token"] = "unexpected-data"
                    except Exception as e:  # noqa: BLE001
                        res["no_token"] = f"closed:{type(e).__name__}"
            except Exception as e:  # noqa: BLE001
                res["no_token"] = f"handshake-failed:{type(e).__name__}"
            try:
                async with websockets.connect("ws://127.0.0.1:4778/ws", subprotocols=[TOKEN],
                                              open_timeout=5) as ws:
                    res["with_token"] = (await asyncio.wait_for(ws.recv(), timeout=3))[:100]
            except Exception as e:  # noqa: BLE001
                res["with_token"] = f"failed:{type(e).__name__}:{e}"
            return res

        res = asyncio.run(ws_probe())
        nt = str(res.get("no_token", ""))
        wt = str(res.get("with_token", ""))
        rec("ws no token rejected", nt.startswith("closed") or nt.startswith("handshake-failed"), nt)
        rec("ws with token gets snapshot", '"snapshot"' in wt, wt)
    except Exception as e:  # noqa: BLE001
        rec("ws", False, f"error {e!r}")

    # 9) restart recovery
    try:
        emit("--- restart recovery ---")
        stop_server(p)
        wait_closed("127.0.0.1", BINDPORT)
        p2, up2 = start_server()
        rec("restart: health 200", up2, f"pid={p2.pid}")
        s, _, b = http("GET", "/api/audit/verify")
        aj2 = json.loads(b) if b else {}
        rec("restart: audit intact", aj2.get("intact") is True, json.dumps(aj2))
        s, _, b = http("GET", "/api/sessions")
        sess = json.loads(b) if b else None
        rec("restart: sessions persisted", isinstance(sess, list) and len(sess) >= 1,
            f"n={len(sess) if isinstance(sess, list) else sess}")
        stop_server(p2)
    except Exception as e:  # noqa: BLE001
        rec("restart", False, f"error {e!r}")

    # 10) clean shutdown
    try:
        closed = wait_closed("127.0.0.1", BINDPORT)
        rec("no residual 4778 listener", closed, f"port_open={port_open('127.0.0.1', BINDPORT)}")
    except Exception as e:  # noqa: BLE001
        rec("residual", False, f"error {e!r}")

    npass = sum(1 for _, ok in results if ok)
    emit("")
    emit(f"=== SUMMARY: {npass}/{len(results)} PASS ===")
    for name, ok in results:
        emit(f"  {'PASS' if ok else 'FAIL'}  {name}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        emit(f"FATAL: {e!r}")
    try:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        with open(OUT, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        print("WROTE", OUT)
    except Exception as e:  # noqa: BLE001
        print("WRITE FAILED", repr(e))
    sys.exit(0)
