"""Isolate why the p2 smoke test's spawned server never becomes healthy.

SYMPTOM. `test_http_end_to_end_four_agents_distinct_no_secret_leak` fails at
`_wait_health` -- "server did not become healthy" -- at HEAD, with no edit of
mine, reproducibly (90.95 s, three separate runs). It passed earlier today
(1 passed in 9.68 s). Line 399, the CR-H2 `.decode()`, is never reached: the
assertion is at line 374, inside `_wait_health`.

WHAT IS ALREADY RULED OUT, by measurement, so this script does not redo it:
  * the child does not crash on a config error -- the same command in the
    FOREGROUND with the same JCC_CONFIG runs until the 30 s timeout kills it
    (exit 124), i.e. it starts fine;
  * `subprocess.Popen` with a PIPE survives, and with a file handle survives;
  * writing the config into the shim temp dir works and the child survives there
    too;
  * `HTTP_PROXY` is set in this environment and `urllib.request.urlopen` honours
    it, but clearing the proxy variables did not make the test pass.

That last combination is the clue: the server starts, and something about the
REQUEST path is what fails. So this script separates the two by using an opener
with an explicitly empty ProxyHandler -- `urllib.request.build_opener(
urllib.request.ProxyHandler({})).open(...)` -- which bypasses the environment
proxy entirely, and compares it against the bare `urlopen` the test uses.

If the direct opener succeeds and the bare one does not, the fault is the proxy,
the test is asserting against a proxied request path, and the fix belongs in the
test's HTTP helper. If BOTH fail, the server is genuinely not answering and the
log will say why -- this script keeps the temp dir (the test's own
`TemporaryDirectory` deletes the log on the failure path, which is why every
failing run so far left no evidence at all).
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
OUT = REPO / "artifacts" / "release" / "v1.3_final" / "p2_spawn_isolation.json"
KEEP = REPO / "runtime" / "harbor_p2_isolation"

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
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def probe(url: str, direct: bool) -> dict:
    """One health probe. `direct` bypasses the environment proxy."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if direct else None
    try:
        with (opener.open(url, timeout=3) if direct else urllib.request.urlopen(url, timeout=3)) as r:
            return {"ok": True, "status": r.status,
                    "body": r.read().decode("utf-8", "replace")[:200]}
    except Exception as exc:                                  # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}


def main() -> int:
    KEEP.mkdir(parents=True, exist_ok=True)
    port = free_port()
    db = KEEP / "jcc.db"
    cfg = KEEP / "jcc.yaml"
    cfg.write_text(CFG.format(port=port, db=db), encoding="utf-8", newline="\n")
    log = KEEP / "server.log"

    env = dict(os.environ)
    env.update(JCC_CONFIG=str(cfg), JCC_CONTROL_TOKEN="isolation-token",
               JCC_PROVIDER="mock", PYTHONUNBUFFERED="1")

    result: dict = {"port": port, "cfg": str(cfg),
                    "env_proxies": urllib.request.getproxies()}
    url = "http://127.0.0.1:%d/api/health" % port

    with open(log, "wb") as fh:
        proc = subprocess.Popen(
            [str(REPO / ".venv" / "Scripts" / "python.exe"), "-m", "jcc_core.run"],
            cwd=str(REPO / "packages"), env=env, stdout=fh,
            stderr=subprocess.STDOUT)
        result["pid"] = proc.pid
        deadline = time.time() + 25
        bare = direct = None
        while time.time() < deadline and proc.poll() is None:
            bare = probe(url, direct=False)
            if bare["ok"]:
                break
            direct = probe(url, direct=True)
            if direct["ok"]:
                break
            time.sleep(0.7)
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:                  # pragma: no cover
                proc.kill()
        result["child_rc"] = proc.returncode
    result["bare_urlopen"] = bare
    result["direct_opener"] = direct
    fh_bytes = log.stat().st_size
    result["log_bytes"] = fh_bytes
    result["log_tail"] = log.read_text(encoding="utf-8", errors="replace")[-3000:]
    result["log_kept_at"] = str(log)
    # Did anything actually bind the port? A proxy answering 502 would not.
    try:
        import socket
        s = socket.create_connection(("127.0.0.1", port), timeout=3)
        s.close()
        result["port_reachable"] = True
    except OSError as exc:
        result["port_reachable"] = False
        result["port_error"] = str(exc)

    OUT.write_text(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False),
                   encoding="utf-8", newline="\n")
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False)[:3500])
    print("\nwrote %s" % OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
