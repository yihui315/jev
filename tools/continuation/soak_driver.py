#!/usr/bin/env python3
"""PHASE Q + R + S + T -- controlled start, soak, restart acceptance, watchdog recovery.

ONE DETACHED PROCESS ON PURPOSE. Every phase below either starts or restarts the
service. If this driver ran inside the agent's Job Object, the service it starts
would be terminated by the kernel the moment the launching command returned --
ROOT_CAUSE_1 of this session, proved with canaries. So the whole sequence runs
outside the job, launched via WMI, and every phase inherits that.

Q: stop.ps1 then start.ps1 -- the product's own scripts -- then verify ownership
   and health.
R: soak for SOAK_MIN minutes. /api/health every 2 s, a 20-request burst every
   60 s, listener pid and RSS/threads/handles every 60 s. The question is not
   "is it fast" but "does it ever stop answering or get replaced".
S: restart acceptance on the soaked service: restart.ps1, a NEW pid, ownership
   re-verified, health back.
T: watchdog recovery. T1 proves the shipped watchdog's decision procedure in an
   isolated replica. T2 is the substantive one: stop the service, let status.ps1
   DETECT the outage, then recover with start.ps1 and prove a new pid.
"""
import hashlib, io, json, os, subprocess, sys, time, datetime

ROOT = r"F:\jev"
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
PS = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                  "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
PORT = 4778
URL = "http://127.0.0.1:%d/api/health" % PORT
SOAK_MIN = int(sys.argv[1]) if len(sys.argv) > 1 else 40
ACC = os.path.join(ROOT, "artifacts", "acceptance")
REL = os.path.join(ROOT, "artifacts", "release")
SCR = os.path.join(ROOT, ".workbuddy", "scratch", "soak")
LOG = os.path.join(SCR, "SOAK.log")
TRACE = os.path.join(ACC, "JCC_SOAK_TRACE.jsonl")
DONE = os.path.join(SCR, "DONE")

os.makedirs(SCR, exist_ok=True)
import urllib.request
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def log(msg):
    line = "%s %s" % (datetime.datetime.now().strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    with io.open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def now():
    return datetime.datetime.now().isoformat(timespec="milliseconds")


def env_clean():
    e = dict(os.environ)
    for k in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy",
              "ALL_PROXY", "all_proxy"):
        e.pop(k, None)
    e["NO_PROXY"] = "127.0.0.1,localhost,::1"
    return e


def health(timeout=4.0):
    t0 = time.perf_counter()
    try:
        with _OPENER.open(URL, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
        return r.status, body, (time.perf_counter() - t0) * 1000.0, None
    except Exception as exc:                                   # noqa: BLE001
        return None, "", (time.perf_counter() - t0) * 1000.0, "%s: %s" % (type(exc).__name__, exc)


def decode_out(b):
    """Decode a Windows tool's bytes without inventing U+FFFD.

    PowerShell and taskkill write in the console codepage (cp936 here), not
    UTF-8. Decoding their output as UTF-8 put replacement characters into the
    stored evidence tails -- and printing one of those to a GBK stream raised
    UnicodeEncodeError and cost a fully passing watchdog proof its exit code and
    its artifact. Relayed text is part of the evidence, so it is decoded with the
    codepage it was actually written in.
    """
    if not b:
        return ""
    if isinstance(b, str):
        return b
    for enc in ("utf-8", "cp936", "mbcs", "latin-1"):
        try:
            return b.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return b.decode("ascii", "backslashreplace")


def ps(cmd, timeout=90):
    p = subprocess.run([PS, "-NoProfile", "-NonInteractive", "-Command", cmd],
                       capture_output=True, timeout=timeout, env=env_clean())
    return (decode_out(p.stdout) + decode_out(p.stderr)).strip()


def run_ps1(name, timeout=300, extra=""):
    out = os.path.join(SCR, "%s.out.log" % name)
    err = os.path.join(SCR, "%s.err.log" % name)
    cmd = "& '%s' -Port %d %s" % (os.path.join(ROOT, "scripts", name), PORT, extra)
    with io.open(out, "wb") as fo, io.open(err, "wb") as fe:
        p = subprocess.run([PS, "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                            "Bypass", "-Command", cmd],
                           cwd=ROOT, stdout=fo, stderr=fe, timeout=timeout, env=env_clean())
    so = decode_out(open(out, "rb").read())
    se = decode_out(open(err, "rb").read())
    return {"script": name, "exit_code": p.returncode,
            "stdout_tail": so[-1500:], "stderr_tail": se[-1500:], "stdout": so, "stderr": se}


def listener_pid():
    o = ps("$c = Get-NetTCPConnection -LocalPort %d -State Listen -ErrorAction SilentlyContinue; "
           "if ($c) { $c | Select-Object -First 1 -ExpandProperty OwningProcess } else { '' }" % PORT)
    for tok in o.replace("\r", "\n").split():
        if tok.isdigit():
            return int(tok)
    return None


def proc_stats(pid):
    o = ps("$p = Get-Process -Id %d -ErrorAction SilentlyContinue; "
           "if ($p) { '{0}|{1}|{2}' -f [int]($p.WorkingSet64/1MB), $p.Threads.Count, $p.HandleCount }"
           % int(pid))
    parts = [x.strip() for x in o.split("|")]
    if len(parts) == 3 and all(x.isdigit() for x in parts):
        return {"rss_mb": int(parts[0]), "threads": int(parts[1]), "handles": int(parts[2])}
    return None


def wait_health(seconds=60):
    dl = time.time() + seconds
    c, b, m, e = None, "", 0.0, "not tried"
    while time.time() < dl:
        c, b, m, e = health()
        if c == 200:
            break
        time.sleep(1)
    return c, b, m, e


def ownership():
    # utf-8-sig, not utf-8. The marker is written by scripts/lib/JccCommon.ps1,
    # and Windows PowerShell 5.1's `-Encoding UTF8` prepends a BOM. Reading with
    # plain utf-8 makes json.load raise, the `except` below turns that into
    # None, and the soak report then says "ownership marker written: False" for
    # a marker that exists and is correct -- a false negative in the very
    # evidence FINAL-08 rests on. The writer no longer emits a BOM; this stays
    # tolerant so a marker produced before that fix still reads as present.
    p = os.path.join(ROOT, "runtime", "jcc", "jcc.ownership.json")
    if not os.path.isfile(p):
        return None
    try:
        return json.load(io.open(p, encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def main():
    R = {"phase": "Q+R+S+T", "started_at": datetime.datetime.now().isoformat(timespec="seconds"),
         "soak_minutes": SOAK_MIN, "port": PORT}

    # ---------------- Q: controlled start ----------------
    log("=== PHASE Q: controlled start ===")
    R["stop"] = run_ps1("stop.ps1")
    log("stop.ps1 exit=%s" % R["stop"]["exit_code"])
    time.sleep(3)
    R["start"] = run_ps1("start.ps1")
    log("start.ps1 exit=%s" % R["start"]["exit_code"])
    code, body, ms, err = wait_health(60)
    pid = listener_pid()
    R["controlled_start"] = {"health_status": code, "health_ms": round(ms, 2),
                             "health_error": err, "health_body": body[:400],
                             "listener_pid": pid, "ownership": ownership(),
                             "start_ok": code == 200 and pid is not None}
    log("controlled start: health=%s pid=%s ok=%s" % (code, pid, R["controlled_start"]["start_ok"]))
    if not R["controlled_start"]["start_ok"]:
        R["verdict"] = "FAIL: the controlled start never became healthy"
        return finish(R, 1)
    base_pid = pid
    R["base_pid"] = base_pid
    R["proc_stats_at_start"] = proc_stats(base_pid)

    # ---------------- R: soak ----------------
    log("=== PHASE R: soak %d min ===" % SOAK_MIN)
    tf = io.open(TRACE, "w", encoding="utf-8", newline="\n")
    t_end = time.time() + SOAK_MIN * 60
    polls = fails = fail_run = worst_fail_run = 0
    lat_max = lat_sum = 0.0
    pid_changes, bursts, samples = [], [], []
    next_burst = next_sample = time.time() + 60
    last_progress = time.time()
    while time.time() < t_end:
        code, body, ms, err = health()
        polls += 1
        lat_sum += ms
        lat_max = max(lat_max, ms)
        rec = {"at": now(), "code": code, "ms": round(ms, 2)}
        if code != 200:
            fails += 1
            fail_run += 1
            worst_fail_run = max(worst_fail_run, fail_run)
            rec["error"] = err
            log("HEALTH FAILURE #%d: %s" % (fails, err))
        else:
            fail_run = 0
        tf.write(json.dumps(rec) + "\n")
        tf.flush()
        if time.time() >= next_sample:
            next_sample = time.time() + 60
            lp = listener_pid()
            st = proc_stats(lp) if lp else None
            s = {"at": now(), "listener_pid": lp}
            if st:
                s.update(st)
            samples.append(s)
            if lp and lp != base_pid and lp not in [c["to"] for c in pid_changes]:
                pid_changes.append({"at": now(), "from": base_pid, "to": lp})
                log("LISTENER PID CHANGED -> %s" % lp)
        if time.time() >= next_burst:
            next_burst = time.time() + 60
            okn = badn = 0
            b0 = time.perf_counter()
            for _ in range(20):
                c2, _b, _m, _e = health()
                okn += (c2 == 200)
                badn += (c2 != 200)
            el = (time.perf_counter() - b0) * 1000
            bursts.append({"at": now(), "ok": okn, "failed": badn, "elapsed_ms": round(el, 1)})
            log("BURST20 %d/20 ok in %.0f ms" % (okn, el))
        if time.time() - last_progress > 300:
            last_progress = time.time()
            log("progress: %d polls, %d failures, mean %.2f ms" % (polls, fails, lat_sum / max(polls, 1)))
        time.sleep(2)
    tf.close()
    c_final, _b, _m, _e = health()
    R["soak"] = {"polls": polls, "health_failures": fails,
                 "longest_consecutive_failures": worst_fail_run,
                 "latency_ms_mean": round(lat_sum / max(polls, 1), 3),
                 "latency_ms_max": round(lat_max, 2),
                 "bursts": len(bursts), "burst_failures": sum(b["failed"] for b in bursts),
                 "pid_changes": pid_changes, "pid_stable": not pid_changes,
                 "final_health_status": c_final, "final_listener_pid": listener_pid(),
                 "rss_first": (samples[0] if samples else None),
                 "rss_last": (samples[-1] if samples else None)}
    soak_ok = (fails == 0 and c_final == 200 and not pid_changes)
    R["soak_ok"] = soak_ok
    log("soak: polls=%d failures=%d pid_changes=%d final=%s"
        % (polls, fails, len(pid_changes), c_final))

    # ---------------- S: restart acceptance ----------------
    log("=== PHASE S: restart acceptance ===")
    pid_before = listener_pid()
    r = run_ps1("restart.ps1")
    code, _b, ms, err = wait_health(75)
    pid_after = listener_pid()
    st_after = run_ps1("status.ps1")
    R["restart_acceptance"] = {
        "restart_exit_code": r["exit_code"], "restart_stdout_tail": r["stdout_tail"][-700:],
        "pid_before": pid_before, "pid_after": pid_after,
        "new_pid": bool(pid_after and pid_after != pid_before),
        "health_after": code, "health_after_ms": round(ms, 2), "health_after_error": err,
        "status_exit_code": st_after["exit_code"],
        "status_stdout_tail": st_after["stdout_tail"][-700:],
        "ownership_after": ownership(),
    }
    s_ok = (r["exit_code"] == 0 and code == 200 and st_after["exit_code"] == 0
            and R["restart_acceptance"]["new_pid"])
    R["restart_acceptance"]["ok"] = s_ok
    log("restart: exit=%s pid %s->%s health=%s status_exit=%s ok=%s"
        % (r["exit_code"], pid_before, pid_after, code, st_after["exit_code"], s_ok))

    # ---------------- T1: watchdog decision procedure ----------------
    log("=== PHASE T1: watchdog decision procedure (replica) ===")
    # The TRACKED instrument, not a scratch copy, and its own artifact. It used to
    # run `.workbuddy/scratch/continuation/watchdog_proof.py` and to write
    # WATCHDOG_RECOVERY_PROOF.json -- which FINAL-09 also writes. So this step's
    # recorded claim named a copy that is not in version control and pointed at
    # bytes a later step then replaced: unreproducible, and unverifiable after the
    # fact. A soak report is not allowed to be either.
    t1_script = os.path.join(ROOT, "tools", "continuation", "watchdog_proof.py")
    t1_script_sha = hashlib.sha256(open(t1_script, "rb").read()).hexdigest()
    wr_path = os.path.join(ACC, "SOAK_WATCHDOG_PROOF.json")
    wr_before = os.path.getmtime(wr_path) if os.path.isfile(wr_path) else None
    t1_start = time.time()
    wr = subprocess.run([PY, t1_script], capture_output=True, timeout=600)
    w_out = decode_out(wr.stdout)
    w_err = decode_out(wr.stderr)
    # BOTH streams are kept. The first run of this step exited non-zero and only
    # stdout was stored, so the traceback -- which is the whole diagnosis -- was
    # thrown away. An instrument that discards the error channel cannot explain
    # its own failure.
    #
    # AND the exit code is reconciled against the artifact. This project has been
    # burned four times by an instrument whose return value disagreed with what it
    # actually did, so neither is trusted alone: exit code 0 is only accepted if
    # the proof file was written BY THIS STEP and says PASS.
    t1_doc = None
    if os.path.isfile(wr_path):
        try:
            t1_doc = json.load(io.open(wr_path, encoding="utf-8"))
        except (OSError, ValueError):
            t1_doc = None
    t1_fresh = (t1_doc is not None and os.path.getmtime(wr_path) >= t1_start
                and os.path.getmtime(wr_path) != wr_before)
    t1_verdict = (t1_doc or {}).get("verdict")
    t1_ok = (wr.returncode == 0 and t1_fresh and t1_verdict == "PASS")
    R["watchdog_proof"] = {"exit_code": wr.returncode, "stdout_tail": w_out[-2500:],
                           "stderr_tail": w_err[-2500:],
                           "instrument": "tools/continuation/watchdog_proof.py",
                           "instrument_sha256": t1_script_sha,
                           "artifact": wr_path,
                           "artifact_sha256": (hashlib.sha256(open(wr_path, "rb").read())
                                               .hexdigest() if os.path.isfile(wr_path) else None),
                           "artifact_fresh_for_this_step": t1_fresh,
                           "artifact_verdict": t1_verdict,
                           "artifact_run_stamp": (t1_doc or {}).get("run_stamp"),
                           "artifact_context": (t1_doc or {}).get("context"),
                           "ok": t1_ok}
    log("watchdog replica proof exit=%s artifact_verdict=%s fresh=%s ok=%s"
        % (wr.returncode, t1_verdict, t1_fresh, t1_ok))

    # ---------------- T2: service detect-and-recover (the substantive one) --------
    log("=== PHASE T2: service detect-and-recover ===")
    pid_pre = listener_pid()
    stop = run_ps1("stop.ps1")
    time.sleep(2)
    st_down = run_ps1("status.ps1")
    down_detected = (listener_pid() is None) and ("DOWN" in st_down["stdout"].upper())
    _c, _b, _m, e_down = health(timeout=3)
    rec = run_ps1("start.ps1")
    code, _b, ms, err = wait_health(75)
    pid_post = listener_pid()
    st_up = run_ps1("status.ps1")
    R["watchdog_service_recovery"] = {
        "pid_before": pid_pre,
        "stop_exit_code": stop["exit_code"],
        "detection": {"status_exit_code": st_down["exit_code"],
                      "status_stdout_tail": st_down["stdout_tail"][-700:],
                      "port_free": listener_pid() is None,
                      "health_error_while_down": e_down,
                      "outage_detected": down_detected},
        "recovery": {"start_exit_code": rec["exit_code"], "health_after": code,
                     "pid_after": pid_post, "new_pid": bool(pid_post and pid_post != pid_pre),
                     "health_after_ms": round(ms, 2), "health_after_error": err,
                     "status_exit_code": st_up["exit_code"],
                     "status_stdout_tail": st_up["stdout_tail"][-700:],
                     "ownership_after": ownership()},
    }
    t2_ok = (down_detected and rec["exit_code"] == 0 and code == 200
             and st_up["exit_code"] == 0 and pid_post != pid_pre)
    R["watchdog_service_recovery"]["ok"] = t2_ok
    log("detect-and-recover: detected=%s start_exit=%s health=%s pid %s->%s ok=%s"
        % (down_detected, rec["exit_code"], code, pid_pre, pid_post, t2_ok))

    R["verdict"] = ("PASS: controlled start ok; %d-min soak with %d health failure(s) and %d "
                    "pid change(s); restart acceptance ok=%s; watchdog decision procedure "
                    "ok=%s (exit %s, artifact %s); detect-and-recover ok=%s"
                    % (SOAK_MIN, fails, len(pid_changes), s_ok, t1_ok, wr.returncode,
                       t1_verdict, t2_ok))
    if not (soak_ok and s_ok and t1_ok and t2_ok):
        R["verdict"] = "FAIL: " + R["verdict"]
    log(R["verdict"])
    return finish(R, 0 if (soak_ok and s_ok and t1_ok and t2_ok) else 1)


def finish(R, rc):
    R["finished_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    io.open(os.path.join(ACC, "JCC_SOAK_RESULT.json"), "w", encoding="utf-8",
            newline="\n").write(json.dumps(R, indent=2, ensure_ascii=False))
    s = R.get("soak") or {}
    cs = R.get("controlled_start") or {}
    ra = R.get("restart_acceptance") or {}
    wr = R.get("watchdog_service_recovery") or {}
    md = ["# JCC SOAK REPORT", "",
          "**Phases:** Q controlled start · R soak · S restart acceptance · T watchdog recovery",
          "**Port:** %d · **soak window:** %s min · **finished:** %s"
          % (PORT, R.get("soak_minutes"), R.get("finished_at")), "",
          "## Verdict", "", R.get("verdict", "(aborted)"), "",
          "## Q — controlled start (product scripts only)", "",
          "| measure | value |", "| --- | --- |",
          "| stop.ps1 exit | %s |" % (R.get("stop") or {}).get("exit_code"),
          "| start.ps1 exit | %s |" % (R.get("start") or {}).get("exit_code"),
          "| /api/health | %s in %.1f ms |" % (cs.get("health_status"), cs.get("health_ms") or 0),
          "| listener pid | %s |" % cs.get("listener_pid"),
          "| ownership marker written | %s |" % bool(cs.get("ownership")), "",
          "## R — soak", "", "| measure | value |", "| --- | --- |",
          "| polls (every 2 s) | %s |" % s.get("polls"),
          "| health failures | %s |" % s.get("health_failures"),
          "| longest run of consecutive failures | %s |" % s.get("longest_consecutive_failures"),
          "| latency mean / max (ms) | %s / %s |" % (s.get("latency_ms_mean"), s.get("latency_ms_max")),
          "| 20-request bursts | %s (failures in them: %s) |" % (s.get("bursts"), s.get("burst_failures")),
          "| listener pid changes | %s |" % len(s.get("pid_changes") or []),
          "| final health | %s |" % s.get("final_health_status"),
          "", "## S — restart acceptance", "", "| measure | value |", "| --- | --- |",
          "| restart.ps1 exit | %s |" % ra.get("restart_exit_code"),
          "| pid before -> after | %s -> %s |" % (ra.get("pid_before"), ra.get("pid_after")),
          "| new pid | %s |" % ra.get("new_pid"),
          "| /api/health after | %s in %.1f ms |" % (ra.get("health_after"), ra.get("health_after_ms") or 0),
          "| status.ps1 exit after | %s |" % ra.get("status_exit_code"), "",
          "## T — watchdog recovery", "",
          "**T1 decision procedure** (isolated replica; the controller is replaced by a stub "
          "implementing only the lock handshake): exit `%s`, artifact verdict `%s`, artifact "
          "written by this step: `%s`."
          % ((R.get("watchdog_proof") or {}).get("exit_code"),
             (R.get("watchdog_proof") or {}).get("artifact_verdict"),
             (R.get("watchdog_proof") or {}).get("artifact_fresh_for_this_step")), ""]
    if wr:
        d = wr.get("detection") or {}
        rec_r = wr.get("recovery") or {}
        md += ["**T2 detect-and-recover** (the substantive proof, product scripts only):", "",
               "| measure | value |", "| --- | --- |",
               "| stop.ps1 exit | %s |" % wr.get("stop_exit_code"),
               "| outage detected by status.ps1 | %s (exit %s) |" % (d.get("outage_detected"), d.get("status_exit_code")),
               "| port free while down | %s |" % d.get("port_free"),
               "| health while down | %s |" % (d.get("health_error_while_down") or "")[:90],
               "| start.ps1 exit | %s |" % rec_r.get("start_exit_code"),
               "| /api/health after | %s in %.1f ms |" % (rec_r.get("health_after"), rec_r.get("health_after_ms") or 0),
               "| pid before -> after | %s -> %s |" % (wr.get("pid_before"), rec_r.get("pid_after")),
               "| status.ps1 exit after | %s |" % rec_r.get("status_exit_code"),
               "| ok | %s |" % wr.get("ok"), ""]
    md += ["## Scope limits (stated, not implied)", "",
           "* The shipped watchdog supervises the **continuation controller**, not the service. "
           "Nothing in this repository restarts `jcc_core` if it dies; the service recovery above "
           "is an operator-invoked cycle through the product's own scripts.",
           "* T1 proves the watchdog's decision procedure against a stub controller, so it is "
           "evidence about the recovery *mechanism*, not about the controller's pipeline.",
           "* The scheduled task `JEV-JCC-Continuation-Watchdog` is not registered on this host; "
           "registering it needs elevation, which was not requested.", "",
           "Trace: `artifacts/acceptance/JCC_SOAK_TRACE.jsonl` · "
           "machine record: `artifacts/acceptance/JCC_SOAK_RESULT.json`", ""]
    io.open(os.path.join(REL, "JCC_SOAK_REPORT.md"), "w", encoding="utf-8",
            newline="\n").write("\n".join(md))
    io.open(DONE, "w", encoding="utf-8").write("rc=%d\n" % rc)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:                                   # noqa: BLE001
        import traceback
        log("DRIVER CRASHED: %s" % exc)
        log(traceback.format_exc()[-2500:])
        try:
            finish({"phase": "Q+R+S+T", "verdict": "FAIL: driver crashed: %s" % exc,
                    "soak_minutes": SOAK_MIN}, 3)
        except Exception:                                       # noqa: BLE001
            pass
        raise SystemExit(3)
