#!/usr/bin/env python3
"""Re-run the T1 watchdog proof inside the SAME context the driver uses:
launched by Win32_Process.Create, so the environment is the WMI service's (not
the interactive shell's), and stdout AND stderr both go to files.

WHY THE OUTPUT PATHS ARE STAMPED. The first version of this launcher polled a
FIXED path for the sentinel "EXITCODE=" and treated its presence as "the run is
done". On the second invocation that condition was already true from the FIRST
run, so the launcher returned in 2 seconds printing the previous run's traceback
as if it were a fresh measurement. That is the same failure this project has now
hit four times: an instrument reporting a result while having observed nothing.
A stamped path cannot pre-exist, and the evidence file's mtime is asserted
against the launch time so a stale artifact cannot be read as this run's output.
"""
import ctypes
import ctypes.wintypes as wt
import datetime
import io
import os
import subprocess
import time

ROOT = r"F:\jev"
PS = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                  "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
SCR = os.path.join(ROOT, ".workbuddy", "scratch")
SOAK = os.path.join(SCR, "soak")
CONT = os.path.join(SCR, "continuation")
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
PROOF = os.path.join(CONT, "watchdog_proof.py")
EVIDENCE = os.path.join(ROOT, "artifacts", "acceptance", "WATCHDOG_RECOVERY_PROOF.json")

STAMP = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
WRAP = os.path.join(CONT, "run_t1_wmi_%s.cmd" % STAMP)
OUT = os.path.join(SOAK, "t1_wmi_%s.out.txt" % STAMP)
ERR = os.path.join(SOAK, "t1_wmi_%s.err.txt" % STAMP)

os.makedirs(SOAK, exist_ok=True)
for p in (WRAP, OUT, ERR):
    if os.path.exists(p):
        raise SystemExit("refusing to run: %s already exists (stale-evidence guard)" % p)

io.open(WRAP, "w", encoding="ascii", newline="").write(
    "@echo off\r\ncd /d %s\r\n"
    '"%s" "%s" > "%s" 2> "%s"\r\n'
    "echo EXITCODE=%%ERRORLEVEL%% >> \"%s\"\r\n"
    % (ROOT, PY, PROOF, OUT, ERR, OUT))


def job_check(pid):
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, int(pid))
    if not h:
        return None
    try:
        r = wt.BOOL(False)
        k.IsProcessInJob(wt.HANDLE(h), None, ctypes.byref(r))
        return bool(r.value)
    finally:
        k.CloseHandle(wt.HANDLE(h))


ev_before = os.path.getmtime(EVIDENCE) if os.path.isfile(EVIDENCE) else None
t_launch = time.time()

script = ("$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
          "-Arguments @{ CommandLine = 'cmd.exe /c \"%s\"'; CurrentDirectory = '%s' }; "
          "Write-Output $r.ProcessId" % (WRAP, ROOT))
p = subprocess.run([PS, "-NoProfile", "-NonInteractive", "-Command", script],
                   capture_output=True, timeout=120)
pid = 0
for tok in reversed((p.stdout or b"").decode("utf-8", "replace").replace("\r", "\n").split()):
    if tok.strip().isdigit():
        pid = int(tok.strip())
        break
print("T1_PID=%d in_job=%s" % (pid, job_check(pid)))
print("launched  : %s" % datetime.datetime.fromtimestamp(t_launch).isoformat(timespec="seconds"))
print("stdout    : %s" % OUT)
print("stderr    : %s" % ERR)

done = False
for _ in range(60):                      # 60 * 5 s
    if os.path.isfile(OUT) and "EXITCODE=" in io.open(
            OUT, encoding="utf-8", errors="replace").read():
        done = True
        break
    time.sleep(5)
print("finished  : %s" % ("yes" if done else "TIMED OUT"))

print("=" * 70)
print("STDOUT")
print("=" * 70)
print(io.open(OUT, encoding="utf-8", errors="replace").read()[-4000:]
      if os.path.isfile(OUT) else "(none)")
print("=" * 70)
print("STDERR  (the channel the first attempt discarded)")
print("=" * 70)
print(io.open(ERR, encoding="utf-8", errors="replace").read()[-4000:]
      if os.path.isfile(ERR) else "(none)")

exit_line = ""
if os.path.isfile(OUT):
    for ln in io.open(OUT, encoding="utf-8", errors="replace").read().splitlines():
        if ln.startswith("EXITCODE="):
            exit_line = ln.strip()
ev_after = os.path.getmtime(EVIDENCE) if os.path.isfile(EVIDENCE) else None
fresh = (ev_after is not None and ev_after >= t_launch)
print("=" * 70)
print("VERDICT LINES")
print("=" * 70)
print(exit_line or "EXITCODE=(missing)")
print("evidence  : %s" % EVIDENCE)
print("evidence mtime %s -> %s"
      % (datetime.datetime.fromtimestamp(ev_after).isoformat(timespec="seconds")
         if ev_after else "(absent)",
         "WRITTEN BY THIS RUN" if fresh else "STALE -- NOT this run's output"))
if not fresh:
    raise SystemExit(3)
