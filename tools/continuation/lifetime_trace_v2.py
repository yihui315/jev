#!/usr/bin/env python3
"""PHASE B + PHASE M: instrumented JCC lifetime trace, run OUTSIDE the agent job.

WHY OUTSIDE THE JOB
-------------------
Proven in this session: every command the agent host runs executes inside a Job
Object with KILL_ON_JOB_CLOSE. `scripts/start.ps1` launches the service with
`Start-Process` WITHOUT CREATE_BREAKAWAY_FROM_JOB, so the service inherits that
job and is TerminateProcess'ed the instant the launching command returns. A trace
started the same way measures nothing but its own murder. Therefore:

  * this script is launched by WMI (Win32_Process.Create), not by the agent shell
  * it records its own parent chain + IsProcessInJob as PROOF of detachment
  * the service it starts inherits the WMI context, not the agent's job

WHAT IT MEASURES (poll every 2 s for DURATION s, default 1200):
  timestamp elapsed listener_pid alive in_job rss_mb cpu_total threads handles
  ppid health_http health_status latency_ms db_size wal_size

  + every 60 s a 20-request sequential burst (ok/fail/mean lat)
  + once at t>=600 s a 60 s standalone throughput test in a background THREAD
    (fresh connection per request, same shape as E-07 step 14) -> the real
    answer to "can this service sustain >=900 ok / 0 fail in 60 s".

OUTPUTS
  artifacts/acceptance/JCC_LIFETIME_TRACE.jsonl   (per-poll rows)
  artifacts/acceptance/JCC_LIFETIME_EVENTS.jsonl  (log deltas + notable events)
  artifacts/acceptance/JCC_LIFETIME_SUMMARY.json  (final rollup)
  runtime/jcc/lifetime-trace.log                  (this script's own stdout)
"""
import io
import json
import os
import statistics
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = str(Path(__file__).resolve().parents[2])
PS = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                  "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
RUNTIME = os.path.join(ROOT, "runtime", "jcc")
ACC = os.path.join(ROOT, "artifacts", "acceptance")
TRACE = os.path.join(ACC, "JCC_LIFETIME_TRACE.jsonl")
EVENTS = os.path.join(ACC, "JCC_LIFETIME_EVENTS.jsonl")
SUMMARY = os.path.join(ACC, "JCC_LIFETIME_SUMMARY.json")
HEARTBEAT = os.path.join(ACC, "JCC_LIFETIME_HEARTBEAT.json")
OWN_LOG = os.path.join(RUNTIME, "lifetime-trace.log")
PORT = 4778
DURATION = int(sys.argv[1]) if len(sys.argv) > 1 else 1200
INTERVAL = 2.0
BURST_EVERY = 60
THROUGHPUT_AT = 600

# ---- own log (a WMI-launched process has no console) -------------------------
os.makedirs(ACC, exist_ok=True)
os.makedirs(RUNTIME, exist_ok=True)
_logfh = io.open(OWN_LOG, "a", encoding="utf-8", buffering=1)
sys.stdout = _logfh
sys.stderr = _logfh


def log(msg):
    print("[%s] %s" % (time.strftime("%Y-%m-%dT%H:%M:%S"), msg), flush=True)


def jline(path, obj):
    with io.open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def ps(s, t=60):
    try:
        r = subprocess.run([PS, "-NoProfile", "-NonInteractive", "-Command", s],
                           capture_output=True, timeout=t)
        return ((r.stdout or b"").decode("utf-8", "replace")
                + (r.stderr or b"").decode("utf-8", "replace")).strip()
    except Exception as e:
        return "PS_ERROR %s: %s" % (type(e).__name__, e)


def health(timeout=5.0):
    t = time.perf_counter()
    try:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with op.open(urllib.request.Request("http://127.0.0.1:%d/api/health" % PORT,
                                            method="GET"), timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            return r.status, body, (time.perf_counter() - t) * 1000.0
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, e), (time.perf_counter() - t) * 1000.0


def listener(port):
    o = ps("(Get-NetTCPConnection -LocalPort %d -State Listen -ErrorAction SilentlyContinue).OwningProcess" % port)
    for x in o.split():
        if x.strip().isdigit():
            return int(x)
    return 0


def proc_metrics(pid):
    if not pid:
        return {}
    o = ps("Get-CimInstance Win32_Process -Filter 'ProcessId=%d' | Select-Object "
           "WorkingSetSize,UserModeTime,KernelModeTime,ThreadCount,HandleCount,"
           "ParentProcessId,SessionId | ConvertTo-Json -Compress" % pid)
    try:
        d = json.loads(o.splitlines()[0])
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def ancestry(pid, depth=5):
    chain, cur = [], pid
    for _ in range(depth):
        if not cur:
            break
        o = ps("Get-CimInstance Win32_Process -Filter 'ProcessId=%d' | Select-Object "
               "ProcessId,ParentProcessId,Name | ConvertTo-Json -Compress" % int(cur))
        try:
            d = json.loads(o.splitlines()[0])
        except Exception:
            break
        if not isinstance(d, dict):
            break
        chain.append({"pid": d.get("ProcessId"), "name": d.get("Name")})
        cur = d.get("ParentProcessId")
    return chain


def fsize(p):
    try:
        return os.path.getsize(p)
    except OSError:
        return None


# ============================ PHASE A: freeze scene ========================
import hashlib
from pathlib import Path

def sha(p):
    if not os.path.isfile(p):
        return None
    h = hashlib.sha256()
    with io.open(p, "rb") as fh:
        for c in iter(lambda: fh.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


FROZEN = ["scripts/acceptance.ps1", "scripts/bootstrap.ps1", "scripts/doctor.ps1",
          "scripts/lib/JccCommon.ps1", "scripts/restart.ps1", "scripts/start.ps1",
          "scripts/status.ps1", "scripts/stop.ps1"]

scene = {
    "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "tracer_pid": os.getpid(),
    "tracer_ppid": os.getppid(),
    "tracer_ancestry": ancestry(os.getpid()),
    "HEAD": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                           capture_output=True, text=True).stdout.strip(),
    "git_status": [l for l in subprocess.run(["git", "status", "--porcelain", "-uall"],
                   cwd=ROOT, capture_output=True, text=True).stdout.splitlines() if l.strip()],
    "FROZEN": {r: sha(os.path.join(ROOT, r.replace("/", "\\"))) for r in FROZEN},
    "port_free_before": listener(PORT) == 0,
    "jcc_pid_file_before": (io.open(os.path.join(RUNTIME, "jcc.pid"), encoding="utf-8",
                                    errors="replace").read().strip()
                            if os.path.isfile(os.path.join(RUNTIME, "jcc.pid")) else None),
}
with io.open(os.path.join(ACC, "JCC_SCENE.json"), "w", encoding="utf-8") as fh:
    json.dump(scene, fh, indent=2, ensure_ascii=False)
log("[scene] HEAD=%s tree_dirty=%s port_free=%s" %
    (scene["HEAD"][:12], bool(scene["git_status"]), scene["port_free_before"]))
log("[scene] tracer ancestry=%s" % json.dumps(scene["tracer_ancestry"]))

for f in (TRACE, EVENTS):
    try:
        os.remove(f)
    except OSError:
        pass

jline(EVENTS, {"ts": scene["captured_at"], "event": "TRACE_START",
               "tracer_pid": os.getpid(), "tracer_ppid": os.getppid(),
               "tracer_ancestry": scene["tracer_ancestry"], "duration_s": DURATION,
               "head": scene["HEAD"], "tree_dirty": bool(scene["git_status"]),
               "frozen": scene["FROZEN"]})

# ============================ start the service ============================
env = dict(os.environ)
for k in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
    env.pop(k, None)
env["NO_PROXY"] = "127.0.0.1,localhost,::1"

t0 = time.time()
with io.open(os.path.join(RUNTIME, "life-start.out.log"), "wb") as fo, \
     io.open(os.path.join(RUNTIME, "life-start.err.log"), "wb") as fe:
    p = subprocess.run([PS, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                        "-Command", "& '%s' -Port %d" % (os.path.join(ROOT, "scripts", "start.ps1"), PORT)],
                       cwd=ROOT, stdout=fo, stderr=fe, timeout=300, env=env)
start_elapsed = round(time.time() - t0, 2)
log("[start.ps1] rc=%s in %.1fs" % (p.returncode, start_elapsed))
jline(EVENTS, {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": "START_PS1",
               "rc": p.returncode, "duration_s": start_elapsed,
               "stdout": io.open(os.path.join(RUNTIME, "life-start.out.log"),
                                 encoding="utf-8", errors="replace").read()[-1500:],
               "stderr": io.open(os.path.join(RUNTIME, "life-start.err.log"),
                                 encoding="utf-8", errors="replace").read()[-1500:]})

svc0 = listener(PORT)
log("[start.ps1] listener pid=%s" % svc0)
jline(EVENTS, {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": "SERVICE_LISTENING",
               "listener_pid": svc0, "start_rc": p.returncode,
               "ancestry": ancestry(svc0) if svc0 else []})

# log-tail offsets
logs = {}
for name in ("server.log", "server.err.log"):
    fp = os.path.join(RUNTIME, name)
    logs[name] = {"path": fp, "off": os.path.getsize(fp) if os.path.isfile(fp) else 0}


def pump_logs():
    for name, st in logs.items():
        fp = st["path"]
        if not os.path.isfile(fp):
            continue
        sz = os.path.getsize(fp)
        if sz > st["off"]:
            with io.open(fp, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(st["off"])
                new = fh.read()
                st["off"] = fh.tell()
            for ln in new.splitlines():
                if ln.strip():
                    jline(EVENTS, {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                   "stream": name, "line": ln})
        elif sz < st["off"]:
            st["off"] = 0


# ============================ throughput thread ============================
tp_result = {}
tp_done = threading.Event()


def throughput_probe():
    """60 s of back-to-back /api/health, exactly the shape of E-07 step 14."""
    ok = fail = 0
    deadline = time.time() + 60.0
    lats = []
    while time.time() < deadline:
        c, _b, lat = health(timeout=5.0)
        lats.append(round(lat, 2))
        if c == 200:
            ok += 1
        else:
            fail += 1
    el = 60.0
    tp_result.update({
        "ok": ok, "failed": fail, "window_s": el,
        "rate_per_s": round(ok / el, 2),
        "latency_mean_ms": round(statistics.mean(lats), 2) if lats else None,
        "latency_p50_ms": round(statistics.median(lats), 2) if lats else None,
        "latency_max_ms": max(lats) if lats else None,
        "bar_met": (ok >= 900 and fail == 0),
    })
    tp_done.set()


# ============================ PHASE B: the trace ===========================
log("[trace] begin duration=%ds interval=%.1fs" % (DURATION, INTERVAL))
start = time.time()
death = None
last_burst = 0.0
last_summary = 0.0
n_rows = 0
svc_pid_seen = set()
tp_started = False

while time.time() - start < DURATION:
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    el = round(time.time() - start, 1)
    lp = listener(PORT)
    m = proc_metrics(lp) if lp else {}
    code, body, lat = health()

    row = {
        "ts": ts, "elapsed_s": el, "listener_pid": lp,
        "alive": bool(lp and m),
        "rss_mb": round((m.get("WorkingSetSize") or 0) / 1048576.0, 1) if m else None,
        "cpu_total": ((m.get("UserModeTime") or 0) + (m.get("KernelModeTime") or 0)) if m else None,
        "threads": m.get("ThreadCount"), "handles": m.get("HandleCount"),
        "ppid": m.get("ParentProcessId"), "session_id": m.get("SessionId"),
        "health_http": code, "health_status": None, "latency_ms": round(lat, 1),
        "db_size": fsize(os.path.join(RUNTIME, "jcc.db")),
        "wal_size": fsize(os.path.join(RUNTIME, "jcc.db-wal")),
        "tracer_alive": True,
    }
    if body:
        try:
            row["health_status"] = json.loads(body).get("status")
        except Exception:
            row["health_status"] = str(body)[:60]

    if lp:
        svc_pid_seen.add(lp)
        row["svc_pid_changes"] = len(svc_pid_seen)

    jline(TRACE, row)
    n_rows += 1
    pump_logs()

    with io.open(HEARTBEAT, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"tracer_pid": os.getpid(), "at": ts, "elapsed_s": el,
                             "rows": n_rows, "listener_pid": lp,
                             "health_http": code, "latency_ms": row["latency_ms"]}))

    # ---- death detection
    if code != 200 and death is None:
        death = {"ts": ts, "elapsed_s": el, "listener_pid": lp, "code": code,
                 "body": str(body)[:300], "alive": row["alive"]}
        log("[trace] HEALTH FAILURE t=%ss listener=%s code=%s alive=%s" % (el, lp, code, row["alive"]))
        jline(EVENTS, {"ts": ts, "event": "HEALTH_FAILURE", **death})
        jline(EVENTS, {"ts": ts, "event": "DEATH_EVIDENCE",
                       "server_log_tail": io.open(os.path.join(RUNTIME, "server.log"), encoding="utf-8",
                                                  errors="replace").read()[-2500:]
                       if os.path.isfile(os.path.join(RUNTIME, "server.log")) else None,
                       "server_err_tail": io.open(os.path.join(RUNTIME, "server.err.log"), encoding="utf-8",
                                                  errors="replace").read()[-2500:]
                       if os.path.isfile(os.path.join(RUNTIME, "server.err.log")) else None,
                       "pid_file": io.open(os.path.join(RUNTIME, "jcc.pid"), encoding="utf-8",
                                           errors="replace").read().strip()
                       if os.path.isfile(os.path.join(RUNTIME, "jcc.pid")) else None,
                       "last_listener": lp})

    # ---- consecutive-request burst every 60 s (PHASE M)
    if time.time() - start - last_burst >= BURST_EVERY:
        last_burst = time.time() - start
        b_ok = b_fail = 0
        b_lat = []
        for _ in range(20):
            c, _b, l2 = health()
            b_lat.append(l2)
            if c == 200:
                b_ok += 1
            else:
                b_fail += 1
        jline(EVENTS, {"ts": ts, "event": "BURST_20", "elapsed_s": el,
                       "ok": b_ok, "failed": b_fail,
                       "mean_ms": round(statistics.mean(b_lat), 1) if b_lat else None})

    # ---- the minute-10 standalone throughput test (PHASE M)
    if not tp_started and el >= THROUGHPUT_AT:
        tp_started = True
        log("[trace] starting 60s throughput probe at t=%ss" % el)
        jline(EVENTS, {"ts": ts, "event": "THROUGHPUT_BEGIN", "elapsed_s": el})
        threading.Thread(target=throughput_probe, daemon=True).start()

    if tp_done.is_set() and tp_result and not tp_result.get("_logged"):
        tp_result["_logged"] = True
        log("[trace] throughput=%s" % json.dumps({k: v for k, v in tp_result.items()
                                                  if not k.startswith("_")}))
        jline(EVENTS, {"ts": ts, "event": "THROUGHPUT_RESULT",
                       **{k: v for k, v in tp_result.items() if not k.startswith("_")}})

    if time.time() - start - last_summary >= 60:
        last_summary = time.time() - start
        log("[trace] t=%4ss pid=%s rss=%sMB thr=%s hnd=%s lat=%sms http=%s db=%s" %
            (int(el), row["listener_pid"], row["rss_mb"], row["threads"],
             row["handles"], row["latency_ms"], row["health_http"], row["db_size"]))

    time.sleep(INTERVAL)

if not tp_done.is_set():
    tp_result["note"] = "throughput probe never ran or did not finish"

summary = {
    "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "duration_s": DURATION, "interval_s": INTERVAL, "rows": n_rows,
    "port": PORT,
    "start_ps1_rc": p.returncode, "start_ps1_s": start_elapsed,
    "service_listener_first": svc0,
    "service_pids_observed": sorted(svc_pid_seen),
    "svc_pid_changes": len(svc_pid_seen),
    "first_health_failure": death,
    "service_survived_full_window": death is None,
    "throughput_60s": {k: v for k, v in tp_result.items() if not k.startswith("_")},
    "scene": {"head": scene["HEAD"], "tree_dirty": bool(scene["git_status"])},
}
with io.open(SUMMARY, "w", encoding="utf-8") as fh:
    json.dump(summary, fh, indent=2, ensure_ascii=False, default=str)
jline(EVENTS, {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": "TRACE_END",
               "rows": n_rows, "survived": death is None, "summary": summary})
log("[trace] END survived=%s rows=%d throughput=%s" %
    (death is None, n_rows, json.dumps(summary["throughput_60s"])))
sys.stdout.flush()
_logfh.flush()
