"""Prove the proposed fix works, before recommending it to anyone.

THE FIX UNDER TEST. `test_p2_integration_smoke.py::_wait_health` allows 30 s for the
server to answer, and the server's `StateStore.__init__` blocks ~30 s on the FIRST
WAL close in the process (measured in
P2_HEALTH_FAILURE_ROOT_CAUSE.md). So the health deadline and the startup cost are
equal to within measurement error, and the test loses the race every time.

Two candidate fixes:
  (1) raise the health budget to 90 s -- simple, but it keeps the host's storage
      warm-up inside the measurement and just makes the race less likely to be lost;
  (2) pay the stall ONCE in a session-scoped fixture, so the server under test only
      has to cover real startup.

(2) is the honest one, so it is the one tested here. A recommendation that has not
been run is a guess, and this round has already produced three of those.

WHAT IS MEASURED. For each arm: warm-up cost, then the server's time-to-healthy
and whether it landed inside the ORIGINAL 30 s budget. The control arm (no warm-up)
must fail and the warm-up arm must pass, or the recommendation is wrong and gets
withdrawn rather than softened.

The child is spawned exactly as the test spawns it: `-m jcc_core.run`, cwd=packages,
its own JCC_CONFIG on a free port, log to a file. Health is polled with
`http.client` rather than `urllib.request` so the environment's HTTP_PROXY cannot
answer instead of the server -- that refuted hypothesis is documented in §2 of the
root-cause file, and a proof that used the proxy-aware path could be measuring the
proxy instead of the fix.
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import sqlite3
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
OUT = REPO / "artifacts" / "release" / "v1.3_final" / "p2_warmup_fix_proof.json"
WORK = REPO / "runtime" / "harbor_p2_fixproof"
HEALTH_BUDGET_S = 30.0          # the budget the test actually uses today

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


def warm_storage() -> float:
    """Pay the first-WAL-close stall once, exactly as a fixture would."""
    d = WORK / "warm"
    d.mkdir(parents=True, exist_ok=True)
    started = time.time()
    conn = sqlite3.connect(str(d / "warm.db"), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("CREATE TABLE IF NOT EXISTS t(x)")
    conn.commit()
    conn.close()
    return time.time() - started


def time_to_healthy(tag: str) -> dict:
    """Spawn the server as the test does; return how long until /api/health answers."""
    port = free_port()
    d = WORK / tag
    d.mkdir(parents=True, exist_ok=True)
    cfg = d / "jcc.yaml"
    cfg.write_text(CFG.format(port=port, db=d / "jcc.db"),
                   encoding="utf-8", newline="\n")
    env = dict(os.environ)
    env.update(JCC_CONFIG=str(cfg), JCC_CONTROL_TOKEN="fixproof-token",
               JCC_PROVIDER="mock", PYTHONUNBUFFERED="1")
    log = d / "server.log"
    started = time.time()
    with open(log, "wb") as fh:
        proc = subprocess.Popen(
            [str(REPO / ".venv" / "Scripts" / "python.exe"), "-m", "jcc_core.run"],
            cwd=str(REPO / "packages"), env=env, stdout=fh, stderr=subprocess.STDOUT)
        rec: dict = {"port": port, "pid": proc.pid, "log": str(log)}
        deadline = started + 120          # generous: we are MEASURING, not racing
        healthy_at = None
        while time.time() < deadline:
            if proc.poll() is not None:
                rec["child_exited_rc"] = proc.returncode
                break
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                conn.request("GET", "/api/health")
                resp = conn.getresponse()
                body = resp.read().decode("utf-8", "replace")
                conn.close()
                if resp.status == 200:
                    healthy_at = time.time() - started
                    rec["health_body"] = body[:180]
                    break
            except OSError:
                pass
            time.sleep(0.4)
        if healthy_at is not None:
            rec["time_to_healthy_s"] = round(healthy_at, 2)
            rec["within_original_30s_budget"] = healthy_at <= HEALTH_BUDGET_S
        else:
            rec["time_to_healthy_s"] = None
            rec["within_original_30s_budget"] = False
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:                  # pragma: no cover
                proc.kill()
        rec["log_bytes"] = log.stat().st_size
    return rec


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    out: dict = {"health_budget_s": HEALTH_BUDGET_S}

    # CONTROL: no warm-up. Expected to blow the 30 s budget -- that is the bug.
    out["control_no_warmup"] = time_to_healthy("control")

    # ARM: warm the storage layer first, in the same process that spawns the child.
    out["warmup_cost_s"] = round(warm_storage(), 2)
    out["with_warmup"] = time_to_healthy("warmed")

    ctl_ok = out["control_no_warmup"]["within_original_30s_budget"]
    fix_ok = out["with_warmup"]["within_original_30s_budget"]
    out["verdict"] = (
        "FIX VERIFIED: without a warm-up the server misses the 30 s health budget, "
        "and after paying the first-WAL-close stall once up front the same server "
        "answers well inside it. The test's failure is host storage warm-up, not JCC."
        if (not ctl_ok and fix_ok) else
        "FIX NOT VERIFIED: control=%s warmed=%s -- the recommendation must be "
        "withdrawn or revised, not softened." % (ctl_ok, fix_ok))

    OUT.write_text(json.dumps(out, indent=2, sort_keys=True, ensure_ascii=False),
                   encoding="utf-8", newline="\n")
    print(json.dumps(out, indent=2, sort_keys=True, ensure_ascii=False)[:3000])
    print("\nVERDICT: %s" % out["verdict"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
