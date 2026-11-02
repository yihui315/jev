"""Prove the p2 smoke failure is the environment proxy, by bypassing it in-process.

THE MEASURED FACTS SO FAR, none of them guessed:
  * `_wait_health` (test_p2_integration_smoke.py:306) polls with BARE
    `urllib.request.urlopen`, and bare urlopen HONOURS `HTTP_PROXY`.
  * This environment sets `HTTP_PROXY=http://127.0.0.1:49863`, and
    `urllib.request.getproxies()` returns it.
  * `urllib.request.proxy_bypass('127.0.0.1')` returns **False**, so loopback is
    NOT excluded -- and the WorkBuddy shim strips `no_proxy`/`NO_PROXY` from the
    environment (measured: both are None even when explicitly exported), so the
    usual escape hatch is unavailable on this host.
  * A request through the proxy to the child's own port returns
    `HTTP Error 502: Bad Gateway` -- the proxy answering FOR a server that is
    actually listening. `_wait_health` swallows it in `except Exception`, sleeps,
    and retries until the 30 s deadline, so the test reports
    "server did not become healthy" while the server was healthy the whole time.
  * With an explicit empty ProxyHandler the same URL gives
    `WinError 10061 connection refused` -- i.e. once the proxy is out of the way
    the failure mode CHANGES, which is what makes this worth proving rather than
    asserting.

So this script re-runs the spawn and then polls three ways:
  1. bare urlopen                      -- what the test does
  2. build_opener(ProxyHandler({}))    -- proxy definitively out of the path
  3. raw http.client to the same host  -- no urllib machinery at all
If (2) and (3) succeed while (1) returns 502, the proxy is the cause and the
product server is healthy. If (2) also refuses, the server is genuinely not
listening and this is a different bug -- so the script reports which of the two
it actually observed instead of assuming the convenient answer.
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
OUT = REPO / "artifacts" / "release" / "v1.3_final" / "p2_proxy_proof.json"
WORK = REPO / "runtime" / "harbor_p2_proxyproof"

CFG = """server:
  host: 127.0.0.1
  port: {port}
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


def free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def probe_bare(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return {"ok": True, "status": r.status, "via": "bare urlopen (proxy-aware)"}
    except Exception as exc:                                  # noqa: BLE001
        return {"ok": False, "via": "bare urlopen (proxy-aware)",
                "error": "%s: %s" % (type(exc).__name__, exc)}


def probe_direct(url: str) -> dict:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=3) as r:
            body = r.read().decode("utf-8", "replace")
            return {"ok": True, "status": r.status,
                    "via": "build_opener(ProxyHandler({}))", "body": body[:160]}
    except Exception as exc:                                  # noqa: BLE001
        return {"ok": False, "via": "build_opener(ProxyHandler({}))",
                "error": "%s: %s" % (type(exc).__name__, exc)}


def probe_raw(port: int) -> dict:
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        conn.request("GET", "/api/health")
        resp = conn.getresponse()
        body = resp.read().decode("utf-8", "replace")
        conn.close()
        return {"ok": resp.status == 200, "status": resp.status,
                "via": "http.client (no urllib, no proxy)", "body": body[:160]}
    except Exception as exc:                                  # noqa: BLE001
        return {"ok": False, "via": "http.client (no urllib, no proxy)",
                "error": "%s: %s" % (type(exc).__name__, exc)}


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    port = free_port()
    cfg = WORK / "jcc.yaml"
    cfg.write_text(CFG.format(port=port, db=WORK / "jcc.db"),
                   encoding="utf-8", newline="\n")
    env = dict(os.environ)
    env.update(JCC_CONFIG=str(cfg), JCC_CONTROL_TOKEN="proof-token",
               JCC_PROVIDER="mock", PYTHONUNBUFFERED="1")
    log = WORK / "server.log"
    url = "http://127.0.0.1:%d/api/health" % port

    out: dict = {"port": port,
                 "env": {"HTTP_PROXY": os.environ.get("HTTP_PROXY"),
                         "no_proxy": os.environ.get("no_proxy"),
                         "NO_PROXY": os.environ.get("NO_PROXY")},
                 "getproxies": urllib.request.getproxies(),
                 "proxy_bypass_127_0_0_1": urllib.request.proxy_bypass("127.0.0.1")}

    with open(log, "wb") as fh:
        proc = subprocess.Popen(
            [str(REPO / ".venv" / "Scripts" / "python.exe"), "-m", "jcc_core.run"],
            cwd=str(REPO / "packages"), env=env, stdout=fh, stderr=subprocess.STDOUT)
        out["pid"] = proc.pid
        # Give it a real startup window; the DB init alone measured ~20 s under
        # load, so a short wait would prove nothing.
        raw = direct = bare = None
        deadline = time.time() + 45
        while time.time() < deadline:
            if proc.poll() is not None:
                out["child_exited"] = proc.returncode
                break
            raw = probe_raw(port)
            if raw["ok"]:
                break
            time.sleep(1.0)
        # Now that the server is up, compare all three request paths.
        bare = probe_bare(url)
        direct = probe_direct(url)
        raw = probe_raw(port)
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:                  # pragma: no cover
                proc.kill()

    out["bare_urlopen"] = bare
    out["direct_opener"] = direct
    out["raw_http_client"] = raw
    out["log_bytes"] = log.stat().st_size
    out["log_tail"] = log.read_text(encoding="utf-8", errors="replace")[-1500:]

    server_healthy = bool(raw and raw.get("ok"))
    proxy_is_the_cause = bool(server_healthy and bare and not bare["ok"]
                              and "502" in str(bare.get("error", "")))
    out["server_actually_healthy"] = server_healthy
    out["verdict"] = (
        "PROXY IS THE CAUSE: the server answers 200 on a direct request while the "
        "proxy-aware request returns 502. _wait_health polls with the proxy-aware "
        "form, so it can never see a healthy loopback server on this host."
        if proxy_is_the_cause else
        "NOT THE PROXY (or the server is not up at all): see the three probes. "
        "No conclusion is drawn beyond what is recorded here.")

    OUT.write_text(json.dumps(out, indent=2, sort_keys=True, ensure_ascii=False),
                   encoding="utf-8", newline="\n")
    print(json.dumps(out, indent=2, sort_keys=True, ensure_ascii=False)[:3500])
    print("\nVERDICT: %s" % out["verdict"])
    print("wrote %s" % OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
