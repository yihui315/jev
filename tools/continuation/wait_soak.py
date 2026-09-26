#!/usr/bin/env python3
"""Wait for the detached soak driver to finish, then report its verdict.

The DONE marker is at a fixed path, so its mere existence proves nothing -- the
previous round's marker is still sitting there. Freshness is therefore
established by mtime AND by the result's own finished_at, not by presence.
"""
import datetime
import io
import json
import os
import sys
import time

ROOT = r"F:\jev"
ACC = os.path.join(ROOT, "artifacts", "acceptance")
DONE = os.path.join(ROOT, ".workbuddy", "scratch", "soak", "DONE")
RES = os.path.join(ACC, "JCC_SOAK_RESULT.json")

start = time.time()
done_before = os.path.getmtime(DONE) if os.path.isfile(DONE) else None
res_before = os.path.getmtime(RES) if os.path.isfile(RES) else None
print("waiting since %s" % datetime.datetime.now().isoformat(timespec="seconds"))
print("DONE mtime before : %s" % done_before)
print("RES  mtime before : %s" % res_before)

for i in range(3600):                       # up to ~2 h
    d = os.path.getmtime(DONE) if os.path.isfile(DONE) else None
    r = os.path.getmtime(RES) if os.path.isfile(RES) else None
    if d and d != done_before and r and r != res_before:
        print("DONE refreshed after %.0f s" % (time.time() - start))
        break
    if i % 60 == 0 and i:
        log = os.path.join(ROOT, ".workbuddy", "scratch", "soak", "driver.log")
        tail = []
        if os.path.isfile(log):
            tail = io.open(log, encoding="utf-8", errors="replace").read().splitlines()[-2:]
        print("  %5.0f s | %s" % (time.time() - start, " / ".join(tail)[-150:]))
        sys.stdout.flush()
    time.sleep(10)
else:
    print("TIMED OUT waiting for the driver")
    raise SystemExit(2)

print("=" * 78)
print("DONE says:", io.open(DONE, encoding="utf-8").read().strip())
d = json.load(io.open(RES, encoding="utf-8"))
print("started  :", d.get("started_at"), "-> finished", d.get("finished_at"))
print("soak_ok  :", d.get("soak_ok"))
s = d.get("soak") or {}
print("soak     : polls=%s fails=%s longest_fail_run=%s mean=%s ms max=%s ms "
      "bursts=%s burst_failures=%s pid_stable=%s final=%s"
      % (s.get("polls"), s.get("health_failures"), s.get("longest_consecutive_failures"),
         s.get("latency_ms_mean"), s.get("latency_ms_max"), s.get("bursts"),
         s.get("burst_failures"), s.get("pid_stable"), s.get("final_health_status")))
ra = d.get("restart_acceptance") or {}
print("restart  : exit=%s pid %s->%s new_pid=%s health=%s status_exit=%s ok=%s"
      % (ra.get("restart_exit_code"), ra.get("pid_before"), ra.get("pid_after"),
         ra.get("new_pid"), ra.get("health_after"), ra.get("status_exit_code"), ra.get("ok")))
wp = d.get("watchdog_proof") or {}
print("T1       : exit=%s artifact_verdict=%s fresh=%s ok=%s"
      % (wp.get("exit_code"), wp.get("artifact_verdict"),
         wp.get("artifact_fresh_for_this_step"), wp.get("ok")))
print("T1 stderr tail:", (wp.get("stderr_tail") or "(empty)")[-300:])
t2 = d.get("watchdog_service_recovery") or {}
print("T2       : ok=%s detected=%s"
      % (t2.get("ok"), (t2.get("detection") or {}).get("outage_detected")))
print("VERDICT  :", d.get("verdict"))
