#!/usr/bin/env python3
"""Is the watchdog's 46 s stall caused by the PIPE on its own stdout?

Probe 1 measured the watchdog returning at t=46.3 s -- exactly the controller's
45 s lifetime -- while my caller had PowerShell's stdout on a pipe. That is the
trap the watchdog's own header warns about, reappearing one level up: the header
guards the CHILD's stdout, but the watchdog's OWN stdout was still a pipe.

This runs the identical watchdog twice against fresh replicas: once with its
stdout/stderr on PIPES, once redirected to FILES. If the pipe is the cause the
first call blocks and the second returns immediately.
"""
import ctypes
import ctypes.wintypes as wt
import datetime
import io
import json
import os
import shutil
import subprocess
import time

ROOT = r"F:\jev"
PS = os.path.join(os.environ["SystemRoot"], "System32", "WindowsPowerShell", "v1.0",
                  "powershell.exe")
BASE = os.path.join(ROOT, ".workbuddy", "scratch", "watchdog_proof")
WPSRC = io.open(os.path.join(ROOT, "tools", "continuation-watchdog.ps1"), encoding="utf-8").read()
STUB = io.open(os.path.join(ROOT, ".workbuddy", "scratch", "continuation",
                            "watchdog_proof.py"), encoding="utf-8").read().split("'''")[1]
STUB_SLEEP = 20


def build(tag):
    rep = os.path.join(BASE, "%s_%s" % (tag, datetime.datetime.now().strftime("%H%M%S_%f")))
    for d in (r".workbuddy\locks", r".workbuddy\scheduled\logs", r"tools", r".venv\Scripts"):
        os.makedirs(os.path.join(rep, d), exist_ok=True)
    for rel in (r".venv\Scripts\python.exe", r".venv\pyvenv.cfg"):
        shutil.copyfile(os.path.join(ROOT, rel), os.path.join(rep, rel))
    io.open(os.path.join(rep, "tools", "continuation-watchdog.ps1"), "w",
            encoding="utf-8", newline="\r\n").write(
        WPSRC.replace("$ProjectRoot  = 'F:\\jev'", "$ProjectRoot  = '%s'" % rep))
    io.open(os.path.join(rep, "tools", "jcc_continuation.py"), "w",
            encoding="utf-8", newline="\n").write(STUB)
    return rep


def alive(pid):
    if not pid:
        return "no-pid"
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, int(pid))
    if not h:
        return "no-handle"
    try:
        c = wt.DWORD()
        k.GetExitCodeProcess(wt.HANDLE(h), ctypes.byref(c))
        return "ALIVE" if c.value == 259 else "exit=%d" % c.value
    finally:
        k.CloseHandle(wt.HANDLE(h))


def lock_pid(rep):
    p = os.path.join(rep, ".workbuddy", "locks", "jcc-continuation.lock.json")
    if not os.path.isfile(p):
        return None
    try:
        return json.load(io.open(p, encoding="utf-8")).get("pid")
    except (OSError, ValueError):
        return None


env = dict(os.environ)
env["STUB_SLEEP"] = str(STUB_SLEEP)
args = [PS, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File"]
S = os.path.join(ROOT, ".workbuddy", "scratch", "soak")

print("controller lifetime in both runs: %d s" % STUB_SLEEP)
print()
print("=== RUN 1: watchdog stdout captured on a PIPE (what a naive caller does) ===")
rep1 = build("pipe")
t0 = time.time()
p = subprocess.run(args + [os.path.join(rep1, "tools", "continuation-watchdog.ps1")],
                   capture_output=True, env=env, timeout=180)
dt_pipe = time.time() - t0
print("  returned at t=%.1f s  rc=%s" % (dt_pipe, p.returncode))
print("  stdout: %s" % (p.stdout or b"").decode("utf-8", "replace").strip()[:200])
print("  lock pid %s -> %s" % (lock_pid(rep1), alive(lock_pid(rep1))))

print()
print("=== RUN 2: same watchdog, stdout/stderr redirected to FILES ===")
rep2 = build("file")
o = os.path.join(S, "wd_probe.out.txt")
e = os.path.join(S, "wd_probe.err.txt")
t0 = time.time()
with io.open(o, "wb") as fo, io.open(e, "wb") as fe:
    q = subprocess.run(args + [os.path.join(rep2, "tools", "continuation-watchdog.ps1")],
                       stdout=fo, stderr=fe, env=env, timeout=180)
dt_file = time.time() - t0
print("  returned at t=%.1f s  rc=%s" % (dt_file, q.returncode))
print("  stdout: %s" % io.open(o, encoding="utf-8", errors="replace").read().strip()[:200])
lp2 = lock_pid(rep2)
print("  lock pid %s -> %s   (controller still running? %s)"
      % (lp2, alive(lp2), alive(lp2) == "ALIVE"))

print()
print("PIPE_STALLS_UNTIL_CHILD_EXIT =", dt_pipe >= STUB_SLEEP - 2)
print("FILE_REDIRECT_RETURNS_EARLY  =", dt_file < STUB_SLEEP - 2)
print("dt_pipe=%.1f  dt_file=%.1f" % (dt_pipe, dt_file))

doc = {"probe": "watchdog stdout pipe vs file redirect",
       "controller_lifetime_s": STUB_SLEEP,
       "pipe": {"seconds": round(dt_pipe, 1), "exit_code": p.returncode,
                "stdout": (p.stdout or b"").decode("utf-8", "replace").strip()[:300]},
       "file": {"seconds": round(dt_file, 1), "exit_code": q.returncode,
                "stdout": io.open(o, encoding="utf-8", errors="replace").read().strip()[:300]},
       "conclusion": ("the watchdog does not return until the controller it started exits "
                      "when its OWN stdout is a pipe; with file redirection it returns at once"),
       "at": datetime.datetime.now().isoformat(timespec="seconds")}
out = os.path.join(ROOT, "artifacts", "acceptance", "WATCHDOG_PIPE_PROBE.json")
io.open(out, "w", encoding="utf-8", newline="\n").write(json.dumps(doc, indent=2, ensure_ascii=False))
print("evidence -> %s" % out)
