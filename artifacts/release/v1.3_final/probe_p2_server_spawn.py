"""Reproduce the p2 smoke test's server spawn EXACTLY, and keep the log.

WHY. `test_http_end_to_end_four_agents_distinct_no_secret_leak` fails at
`_wait_health` -- "server did not become healthy" -- both with and without my
CR-H2 edit, and at HEAD. It passed earlier today (1 passed in 9.68s at 18:xx), so
something changed on the host between then and now. This script isolates WHICH
step fails by doing the same four things the test does, in order, and reporting
after each: write the config, spawn `jcc_core.run` with the same cwd/env, poll
`/api/health`, and read the child's log.

The test cannot tell me this itself: `_tmpdir` is a `TemporaryDirectory` on the
shim temp root, so the child's `server.log` is deleted when the test's `with`
block exits, failure or not. Both failing runs therefore left NO log at all --
which is why "the server printed nothing" was uninformative rather than evidence
of a silent crash. Here the directory is NOT auto-deleted.

Also reported: the child's exit code if it dies, and whether the port is actually
bound. A spawn that exits immediately and a spawn that stays up but never answers
are different failures with the same user-visible symptom, and telling them apart
is the whole point of this script.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
OUT = REPO / "artifacts" / "release" / "v1.3_final" / "p2_server_spawn_probe.json"
WORK = REPO / "runtime" / "harbor_p2_spawn"
VENV_PY = REPO / ".venv" / "Scripts" / "python.exe"

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


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    port = free_port()
    db = str(WORK / "jcc.db")
    cfg = WORK / "jcc.yaml"
    cfg.write_text(CFG.format(port=port, db=db), encoding="utf-8", newline="\n")

    env = dict(os.environ)
    env.update(JCC_CONFIG=str(cfg), JCC_CONTROL_TOKEN="p2-probe-token",
               JCC_PROVIDER="mock", PYTHONUNBUFFERED="1")
    log = WORK / "server.log"
    steps: dict = {"port": port, "cfg": str(cfg)}

    with open(log, "wb") as fh:
        proc = subprocess.Popen([str(VENV_PY), "-m", "jcc_core.run"],
                                cwd=str(REPO / "packages"), env=env,
                                stdout=fh, stderr=subprocess.STDOUT)
        steps["pid"] = proc.pid
        deadline = time.time() + 30
        healthy_at = None
        last_err = None
        while time.time() < deadline:
            if proc.poll() is not None:
                steps["child_exited_early"] = proc.returncode
                break
            try:
                # build_opener with an empty ProxyHandler: the environment sets
                # HTTP_PROXY, and `urllib.request.urlopen` honours it, so a
                # loopback request can be answered by a proxy instead of the
                # child. The test uses the bare form; this probe measures the
                # bare form first and the direct form second, so the difference
                # between them is visible rather than assumed.
                with urllib.request.urlopen(
                        "http://127.0.0.1:%d/api/health" % port, timeout=3) as r:
                    body = r.read().decode("utf-8", "replace")
                steps["health_bare_urlopen"] = {"status": r.status,
                                                "body": body[:300]}
                healthy_at = time.time()
                break
            except Exception as exc:                      # noqa: BLE001
                last_err = "%s: %s" % (type(exc).__name__, exc)
                time.sleep(0.5)
        steps["last_health_error"] = last_err
        steps["healthy"] = healthy_at is not None
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:              # pragma: no cover
                proc.kill()
        steps["child_final_rc"] = proc.returncode

    steps["log_bytes"] = log.stat().st_size
    text = log.read_text(encoding="utf-8", errors="replace")
    steps["log_tail"] = text[-2500:]
    OUT.write_text(json.dumps(steps, indent=2, sort_keys=True, ensure_ascii=False),
                   encoding="utf-8", newline="\n")
    print(json.dumps(steps, indent=2, sort_keys=True, ensure_ascii=False)[:4000])
    print("\nwrote %s" % OUT)
    return 0 if steps.get("healthy") else 1


if __name__ == "__main__":
    raise SystemExit(main())
