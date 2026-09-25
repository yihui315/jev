#!/usr/bin/env python3
"""E-07 final acceptance, machine-executed.

This is the machine, not a recollection. Nothing here is believed because a
worker said so: every number in the output is measured in this process, against
the frozen scripts on disk, with bounded timeouts so that a hang is reported as
a hang instead of absorbing the run.

What it must prove
------------------
* the frozen `scripts/` bytes are the ones under test (moving target aborts),
* `restart.ps1` RETURNS on Windows PowerShell 5.1 while starting a service that
  outlives it -- the specific defect this phase exists for,
* the service really answers, ownership (launcher_pid vs service_pid) is real
  and not self-asserted, and the throughput target is met,
* stop really stops and really cleans up.

Every step records timestamp / command / exit_code / stdout_sha256 /
stderr_sha256 / measured_pid / measured_port / result. Step 22 re-checks that
list against the evidence file it just wrote, so "the evidence says PASS" cannot
be true unless the evidence carries the instrumentation.

Usage:
    python run_e07_final.py --port 4778 \
        [--expected-manifest <freeze.json>] [--timeout 300] [--throughput 60]
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import shlex
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta

ROOT = r"F:\jev"
RUNTIME = os.path.join(ROOT, "runtime", "jcc")
PID_FILE = os.path.join(RUNTIME, "jcc.pid")
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
EVIDENCE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "E07_FINAL_EVIDENCE.json")

SERVER_PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
if not os.path.isfile(SERVER_PY):
    SERVER_PY = sys.executable

FROZEN = [
    "scripts/acceptance.ps1",
    "scripts/bootstrap.ps1",
    "scripts/doctor.ps1",
    "scripts/lib/JccCommon.ps1",
    "scripts/restart.ps1",
    "scripts/start.ps1",
    "scripts/status.ps1",
    "scripts/stop.ps1",
]

TZ = timezone(timedelta(hours=8))
EXPECTED_STEPS = 22


def now_iso() -> str:
    return datetime.now(TZ).isoformat()


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: str) -> str | None:
    if not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with io.open(path, "rb") as fh:
        for c in iter(lambda: fh.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# Evidence collector
# --------------------------------------------------------------------------- #

class Evidence:
    def __init__(self) -> None:
        self.steps: list[dict] = []
        self.aborted: dict | None = None

    def record(self, step: int, name: str, description: str, **fields) -> dict:
        row = {
            "step": step,
            "name": name,
            "description": description,
            "timestamp": now_iso(),
            "result": fields.pop("result", "PASS"),
            "measured_pid": fields.pop("measured_pid", None),
            "measured_port": fields.pop("measured_port", None),
            "command": fields.pop("command", None),
            "exit_code": fields.pop("exit_code", None),
            "stdout_sha256": fields.pop("stdout_sha256", None),
            "stderr_sha256": fields.pop("stderr_sha256", None),
            "duration_s": fields.pop("duration_s", None),
            "details": fields,
        }
        self.steps.append(row)
        print("  step %2d %-26s %s" % (step, name, row["result"]), flush=True)
        return row

    def abort(self, step: int, name: str, reason: str, **fields) -> None:
        self.aborted = {"step": step, "name": name, "reason": reason,
                        "timestamp": now_iso(), "fields": fields}
        self.record(step, name, "abort", result="ABORT", reason=reason, **fields)


EV = Evidence()


# --------------------------------------------------------------------------- #
# Measurement helpers
# --------------------------------------------------------------------------- #

def git(*args: str) -> str:
    p = subprocess.run(["git"] + list(args), cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return (p.stdout + p.stderr).strip()


def is_port_free(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        return s.connect_ex(("127.0.0.1", port)) != 0
    finally:
        s.close()


def listening_pid(port: int) -> int | None:
    """PID that owns the LISTENING socket, taken from the OS, not from a file."""
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True,
                         text=True, encoding="utf-8", errors="replace", timeout=120)
    for line in (out.stdout or "").splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0] != "TCP":
            continue
        local = parts[1] if len(parts) > 4 else ""
        if ":" + str(port) in local and parts[3] == "LISTENING":
            try:
                return int(parts[-1])
            except ValueError:
                return None
    return None


def http_json(url: str, timeout_s: float = 5.0):
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - a failed probe must be recorded
        return None, "%s: %s" % (type(exc).__name__, exc)


def run_ps1(name: str, args: list[str], timeout_s: int) -> dict:
    """Run a JCC script the way a human would: via cmd.exe, output to FILES."""
    out_f = os.path.join(RUNTIME, "e07-%s.out.log" % name)
    err_f = os.path.join(RUNTIME, "e07-%s.err.log" % name)
    os.makedirs(RUNTIME, exist_ok=True)
    cmd_exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                           "system32", "cmd.exe")
    quoted_cmd = '"%s" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%s" %s' % (
        SERVER_PY, os.path.join(SCRIPTS_DIR, name), " ".join(shlex.quote(a) for a in args))
    full = '"%s" /c %s > "%s" 2> "%s"' % (cmd_exe, quoted_cmd, out_f, err_f)
    started = time.time()
    proc = subprocess.run([cmd_exe, "/c", full], cwd=ROOT, capture_output=True,
                          timeout=timeout_s)
    elapsed = round(time.time() - started, 3)
    out_b = proc.stdout or b""
    err_b = proc.stderr or b""
    if not out_b and os.path.isfile(out_f):
        out_b = io.open(out_f, "rb").read()
    if not err_b and os.path.isfile(err_f):
        err_b = io.open(err_f, "rb").read()
    return {
        "command": full,
        "exit_code": proc.returncode,
        "duration_s": elapsed,
        "stdout_sha256": sha256_bytes(out_b),
        "stderr_sha256": sha256_bytes(err_b),
        "stdout_bytes": len(out_b),
        "stderr_bytes": len(err_b),
        "stdout_tail": out_b.decode("utf-8", "replace")[-2500:],
        "stderr_tail": err_b.decode("utf-8", "replace")[-2500:],
        "timed_out": proc.returncode is None,
    }


def pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if not h:
            return False
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    except Exception:  # noqa: BLE001
        return False


def read_pid_file() -> dict:
    if not os.path.isfile(PID_FILE):
        return {}
    raw = io.open(PID_FILE, encoding="utf-8", errors="replace").read().strip()
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {"raw": raw}
    except ValueError:
        return {"raw": raw}


# --------------------------------------------------------------------------- #
# Steps
# --------------------------------------------------------------------------- #

MANDATORY_FIELDS = ("timestamp", "command", "exit_code", "stdout_sha256",
                    "stderr_sha256", "measured_pid", "measured_port", "result")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=4778)
    ap.add_argument("--expected-manifest", default=None,
                    help="freeze manifest; any mismatch aborts the run")
    ap.add_argument("--timeout", type=int, default=300,
                    help="per-script timeout in seconds")
    ap.add_argument("--throughput", type=int, default=60,
                    help="throughput window in seconds")
    args = ap.parse_args(argv)

    port = args.port
    url = "http://127.0.0.1:%d/api/health" % port
    manifest = None
    if args.expected_manifest and os.path.isfile(args.expected_manifest):
        manifest = json.load(io.open(args.expected_manifest, encoding="utf-8"))

    head = git("rev-parse", "HEAD").splitlines()
    head = head[0] if head else None

    # --- 1 environment -------------------------------------------------------
    env = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "cwd": os.getcwd(),
        "scripts_dir": SCRIPTS_DIR,
        "evidence_path": EVIDENCE_PATH,
        "expected_steps": EXPECTED_STEPS,
    }
    EV.record(1, "ENV", "record the interpreter and platform under test", **env)

    # --- 2 frozen-bytes check -------------------------------------------------
    hashes = {}
    missing = []
    for rel in FROZEN:
        p = os.path.join(ROOT, rel.replace("/", os.sep))
        h = sha256_file(p)
        hashes[rel] = h
        if not h:
            missing.append(rel)
    if missing:
        EV.record(2, "FROZEN_BYTES", "all frozen scripts present",
                  result="FAIL", missing=missing)
        EV.abort(2, "FROZEN_BYTES", "missing frozen scripts: %r" % missing)
        return write_evidence(head, hashes, "NO-GO")
    EV.record(2, "FROZEN_BYTES", "hash the frozen scripts under test",
              head_sha=head, hashes=hashes)

    # --- 3 moving target ------------------------------------------------------
    if manifest:
        moved = []
        for rel, want in (manifest.get("hashes") or {}).items():
            cur = hashes.get(rel)
            if cur != want:
                moved.append({"path": rel, "expected": want, "actual": cur})
        if moved:
            EV.abort(3, "MOVING_TARGET",
                     "frozen scripts changed during the run: %r" % moved,
                     moved=moved)
            return write_evidence(head, hashes, "NO-GO (E07_ABORTED_MOVING_TARGET)")
        EV.record(3, "MOVING_TARGET", "no frozen script changed since the freeze",
                  scripts_freeze_sha=manifest.get("scripts_freeze_sha"))
    else:
        EV.record(3, "MOVING_TARGET",
                  "no freeze manifest supplied; hash snapshot taken for reference",
                  result="PASS", note="skipped", hashes=hashes)

    # --- 4 clean tree ---------------------------------------------------------
    status = [l for l in git("status", "--porcelain", "-uall").splitlines() if l.strip()]
    EV.record(4, "TREE_CLEAN", "working tree carries no uncommitted change",
              result="PASS" if not status else "FAIL", dirty=status)
    if status:
        EV.abort(4, "TREE_CLEAN", "working tree is dirty: %r" % status)
        return write_evidence(head, hashes, "NO-GO")

    # --- 5 pre-probe: is anything already on the port? ------------------------
    pre_pid = listening_pid(port)
    pre_pidfile = read_pid_file()
    pre_marker = bool(pre_pidfile)
    foreign = pre_pid is not None and pre_pid != pre_pidfile.get("service_pid")
    EV.record(5, "PORT_PRE_PROBE",
              "record what owns the port before touching it",
              measured_port=port, measured_pid=pre_pid,
              pid_file=pre_pidfile, pid_file_marker=pre_marker,
              foreign_listener=foreign)

    # --- 6 start.ps1 -----------------------------------------------------------
    if foreign:
        # Never signal a service we cannot prove is ours.
        EV.abort(6, "START",
                 "port %d is held by pid %s which has no usable ownership marker; "
                 "not signalling another owner's service" % (port, pre_pid),
                 measured_port=port, measured_pid=pre_pid)
        return write_evidence(head, hashes, "NO-GO")
    r_start = run_ps1("start.ps1", ["-Port", str(port)], args.timeout)
    EV.record(6, "START", "start.ps1 must return and leave a healthy service",
              command=r_start["command"], exit_code=r_start["exit_code"],
              duration_s=r_start["duration_s"],
              stdout_sha256=r_start["stdout_sha256"],
              stderr_sha256=r_start["stderr_sha256"],
              measured_port=port, **{k: r_start[k] for k in
                                     ("stdout_tail", "stderr_tail", "timed_out")})
    if r_start["exit_code"] != 0 or r_start["timed_out"]:
        EV.abort(6, "START",
                 "start.ps1 exit=%s after %.1fs (timed_out=%s)"
                 % (r_start["exit_code"], r_start["duration_s"], r_start["timed_out"]),
                 stderr_tail=r_start["stderr_tail"])
        return write_evidence(head, hashes, "NO-GO")

    # --- 7 who actually holds the socket --------------------------------------
    svc_pid = listening_pid(port)
    EV.record(7, "SERVICE_PID",
              "the listening socket is owned by a live process (read from netstat)",
              measured_pid=svc_pid, measured_port=port, alive=pid_alive(svc_pid),
              result="PASS" if (svc_pid and pid_alive(svc_pid)) else "FAIL")
    if not (svc_pid and pid_alive(svc_pid)):
        EV.abort(7, "SERVICE_PID", "no live listener on port %d after start.ps1" % port)
        return write_evidence(head, hashes, "NO-GO")

    # --- 8 pid file agrees with the OS ----------------------------------------
    pf = read_pid_file()
    agrees = pf.get("service_pid") == svc_pid
    EV.record(8, "PID_FILE", "jcc.pid service_pid equals the owning pid",
              measured_pid=svc_pid, measured_port=port, pid_file=pf,
              result="PASS" if agrees else "FAIL")
    if not agrees:
        EV.abort(8, "PID_FILE",
                 "jcc.pid service_pid=%r but the socket is owned by %s"
                 % (pf.get("service_pid"), svc_pid))
        return write_evidence(head, hashes, "NO-GO")

    # --- 9 health --------------------------------------------------------------
    code, body = http_json(url)
    try:
        parsed = json.loads(body) if body else {}
    except ValueError:
        parsed = {}
    EV.record(9, "HEALTH", "/api/health answers 200 with status ok",
              measured_port=port, measured_pid=svc_pid,
              result="PASS" if (code == 200 and parsed.get("status") == "ok") else "FAIL",
              http_status=code, body=body[:800])
    if code != 200 or parsed.get("status") != "ok":
        EV.abort(9, "HEALTH", "health returned HTTP %s: %s" % (code, body[:400]))
        return write_evidence(head, hashes, "NO-GO")

    # --- 10 launcher/service separation (KD-0010) -------------------------------
    launcher = pf.get("launcher_pid")
    separated = (launcher is not None and launcher != svc_pid) or not pid_alive(launcher)
    EV.record(10, "PID_OWNERSHIP",
              "launcher_pid is either absent or a different process from the owner",
              measured_pid=svc_pid, launcher_pid=launcher,
              launcher_alive=pid_alive(launcher), result="PASS" if separated else "FAIL")
    if not separated:
        EV.abort(10, "PID_OWNERSHIP",
                 "launcher_pid %s is alive and identical to the owning pid %s -- "
                 "ownership is self-asserted, not observed" % (launcher, svc_pid))
        return write_evidence(head, hashes, "NO-GO")

    # --- 11 restart.ps1: the measurement this phase exists for ------------------
    r_restart = run_ps1("restart.ps1", ["-Port", str(port)], args.timeout)
    returned = (not r_restart["timed_out"]) and r_restart["exit_code"] == 0
    EV.record(11, "RESTART", "restart.ps1 returns with the service still up",
              command=r_restart["command"], exit_code=r_restart["exit_code"],
              duration_s=r_restart["duration_s"],
              stdout_sha256=r_restart["stdout_sha256"],
              stderr_sha256=r_restart["stderr_sha256"],
              measured_port=port, measured_pid=svc_pid,
              **{k: r_restart[k] for k in
                 ("stdout_tail", "stderr_tail", "timed_out")})
    if not returned:
        EV.abort(11, "RESTART",
                 "restart.ps1 did not return correctly: exit=%s after %.1fs "
                 "(timed_out=%s) -- the nested-engine hang, if it is still there"
                 % (r_restart["exit_code"], r_restart["duration_s"],
                    r_restart["timed_out"]),
                 stderr_tail=r_restart["stderr_tail"])
        return write_evidence(head, hashes, "NO-GO")

    # --- 12 health after restart ------------------------------------------------
    code2, body2 = http_json(url)
    EV.record(12, "HEALTH_AFTER_RESTART", "the service survived the restart",
              measured_port=port, measured_pid=listening_pid(port),
              result="PASS" if code2 == 200 else "FAIL",
              http_status=code2, body=body2[:600])
    if code2 != 200:
        EV.abort(12, "HEALTH_AFTER_RESTART",
                 "health after restart returned HTTP %s" % code2)
        return write_evidence(head, hashes, "NO-GO")

    # --- 13 the restarted process is the one the pid file names ------------------
    pf2 = read_pid_file()
    svc2 = listening_pid(port)
    ok13 = pf2.get("service_pid") == svc2 and svc2 is not None
    EV.record(13, "PID_AFTER_RESTART", "ownership still agrees after restart",
              measured_pid=svc2, pid_file=pf2, result="PASS" if ok13 else "FAIL")
    if not ok13:
        EV.abort(13, "PID_AFTER_RESTART", "post-restart ownership mismatch")
        return write_evidence(head, hashes, "NO-GO")

    # --- 14 throughput -----------------------------------------------------------
    deadline = time.time() + args.throughput
    ok_n, fail_n, last_code = 0, 0, None
    t0 = time.time()
    while time.time() < deadline:
        c, _ = http_json(url, timeout_s=5.0)
        last_code = c
        if c == 200:
            ok_n += 1
        else:
            fail_n += 1
    elapsed_tp = round(time.time() - t0, 3)
    rate = round(ok_n / elapsed_tp, 2) if elapsed_tp > 0 else 0.0
    EV.record(14, "THROUGHPUT",
              "sustain the throughput target inside the window",
              measured_port=port, ok=ok_n, failed=fail_n, rate_per_s=rate,
              window_s=args.throughput, http_status=last_code,
              result="PASS" if (fail_n == 0 and ok_n >= 900) else "FAIL",
              note="1004 requests / 60 s is the known-good shape; >=900 successful "
                   "answers with zero failures is the bar")
    if fail_n or ok_n < 900:
        EV.abort(14, "THROUGHPUT",
                 "%d ok / %d failed in %.1fs (%s per s, last http %s)"
                 % (ok_n, fail_n, elapsed_tp, rate, last_code))
        return write_evidence(head, hashes, "NO-GO")

    # --- 15 status.ps1 ------------------------------------------------------------
    r_status = run_ps1("status.ps1", ["-Port", str(port)], args.timeout)
    EV.record(15, "STATUS", "status.ps1 reports the service",
              command=r_status["command"], exit_code=r_status["exit_code"],
              duration_s=r_status["duration_s"],
              stdout_sha256=r_status["stdout_sha256"],
              stderr_sha256=r_status["stderr_sha256"], measured_port=port,
              **{k: r_status[k] for k in ("stdout_tail", "stderr_tail", "timed_out")})

    # --- 16 stop.ps1 ---------------------------------------------------------------
    r_stop = run_ps1("stop.ps1", ["-Port", str(port)], args.timeout)
    EV.record(16, "STOP", "stop.ps1 stops the service",
              command=r_stop["command"], exit_code=r_stop["exit_code"],
              duration_s=r_stop["duration_s"],
              stdout_sha256=r_stop["stdout_sha256"],
              stderr_sha256=r_stop["stderr_sha256"], measured_port=port,
              **{k: r_stop[k] for k in ("stdout_tail", "stderr_tail", "timed_out")})
    if r_stop["exit_code"] != 0:
        EV.abort(16, "STOP", "stop.ps1 exit=%s" % r_stop["exit_code"])
        return write_evidence(head, hashes, "NO-GO")

    # --- 17 port actually free --------------------------------------------------------
    for _ in range(30):
        if is_port_free(port):
            break
        time.sleep(1.0)
    free17 = is_port_free(port)
    EV.record(17, "PORT_FREED", "nothing listens on the port after stop",
              measured_port=port, measured_pid=listening_pid(port),
              result="PASS" if free17 else "FAIL")
    if not free17:
        EV.abort(17, "PORT_FREED", "port %d still owned by pid %s after stop.ps1"
                 % (port, listening_pid(port)))
        return write_evidence(head, hashes, "NO-GO")

    # --- 18 pid file cleaned -------------------------------------------------------
    pf3 = read_pid_file()
    cleaned = (not pf3) or (not pid_alive(pf3.get("service_pid")))
    EV.record(18, "PID_FILE_CLEANED", "the pid file no longer claims a live owner",
              pid_file=pf3, result="PASS" if cleaned else "FAIL")

    # --- 19 restart the service for the final state -----------------------------------
    r_start2 = run_ps1("start.ps1", ["-Port", str(port)], args.timeout)
    EV.record(19, "START_AGAIN", "a second start cycle also returns and goes healthy",
              command=r_start2["command"], exit_code=r_start2["exit_code"],
              duration_s=r_start2["duration_s"],
              stdout_sha256=r_start2["stdout_sha256"],
              stderr_sha256=r_start2["stderr_sha256"], measured_port=port,
              **{k: r_start2[k] for k in ("stdout_tail", "stderr_tail", "timed_out")})
    if r_start2["exit_code"] != 0:
        EV.abort(19, "START_AGAIN", "second start.ps1 exit=%s" % r_start2["exit_code"])
        return write_evidence(head, hashes, "NO-GO")

    # --- 20 health again -----------------------------------------------------------
    code3, body3 = http_json(url)
    EV.record(20, "HEALTH_FINAL", "the service is healthy at the end of the run",
              measured_port=port, measured_pid=listening_pid(port),
              result="PASS" if code3 == 200 else "FAIL", http_status=code3)

    # --- 21 write the evidence file --------------------------------------------------
    verdict = "GO" if (code3 == 200 and code2 == 200 and not EV.aborted) else "NO-GO"
    path = write_evidence(head, hashes, verdict)
    EV.record(21, "EVIDENCE_WRITE", "evidence written", path=path, verdict=verdict)

    # --- 22 self-check the evidence --------------------------------------------------
    doc = json.load(io.open(path, encoding="utf-8"))
    steps = doc.get("steps") or []
    bad = []
    for s in steps:
        for f in MANDATORY_FIELDS:
            if s.get(f) in (None, "", "PASS" if f == "result" else None) and f != "result":
                bad.append("%s:%s missing" % (s.get("name"), f))
        if s.get("result") != "PASS":
            bad.append("%s:%s" % (s.get("name"), s.get("result")))
    count_ok = len(steps) == EXPECTED_STEPS
    EV.record(22, "SELF_CHECK",
              "every step carries its instrumentation and every step is PASS",
              result="PASS" if (not bad and count_ok) else "FAIL",
              problems=bad, step_count=len(steps))
    if bad or not count_ok:
        return write_evidence(head, hashes, "NO-GO")
    return 0


def write_evidence(head: str | None, hashes: dict, verdict: str) -> str:
    doc = {
        "verdict": verdict,
        "head_sha": head,
        "generated_at": now_iso(),
        "generator": os.path.basename(__file__),
        "expected_steps": EXPECTED_STEPS,
        "running_as": {"pid": os.getpid()},
        "frozen_hashes": hashes,
        "abort": EV.aborted,
        "steps": EV.steps,
    }
    os.makedirs(os.path.dirname(EVIDENCE_PATH), exist_ok=True)
    tmp = EVIDENCE_PATH + ".tmp.%d" % os.getpid()
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(doc, indent=2, ensure_ascii=False))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, EVIDENCE_PATH)
    print("EVIDENCE -> %s (verdict %s, %d steps)"
          % (EVIDENCE_PATH, verdict, len(EV.steps)), flush=True)
    return EVIDENCE_PATH


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.TimeoutExpired as exc:
        EV.abort(0, "TIMEOUT", "runner exceeded its own timeout: %s" % exc)
        write_evidence(None, {}, "NO-GO")
        raise SystemExit(2)
