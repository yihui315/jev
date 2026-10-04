#!/usr/bin/env python3
"""Launch the Q+R+S+T runtime driver OUTSIDE the agent's Job Object.

ROOT_CAUSE_1 of this session: the agent host runs every command inside a Job
Object with KILL_ON_JOB_CLOSE, and Start-Process cannot break away from it. The
three canaries proved that a process started the ordinary way dies within ~9 s of
the launching command returning. WMI's Win32_Process.Create is the mechanism that
was measured to escape (in_job=false), so it is the one used here.
"""
import ctypes
import ctypes.wintypes as wt
import io
import os
import subprocess
import time

ROOT = str(Path(__file__).resolve().parents[2])
PS = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                  "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
SCR = os.path.join(ROOT, ".workbuddy", "scratch")
SOAK = os.path.join(SCR, "soak")
WRAPPER = os.path.join(SCR, "continuation", "run_soak_detached.cmd")
DLOG = os.path.join(SOAK, "driver.log")

os.makedirs(SOAK, exist_ok=True)

CMD = ('@echo off\r\n'
       'rem Detached Q+R+S+T runtime driver. Launched by WMI so the service it\r\n'
       'rem starts is NOT inside the agent Job Object. ASCII only.\r\n'
       'cd /d F:\\jev\r\n'
       'echo ==== SOAK DRIVER START %%DATE%% %%TIME%% ====>> "%s"\r\n'
       '"F:\\jev\\.venv\\Scripts\\python.exe" "F:\\jev\\.workbuddy\\scratch\\'
       'continuation\\soak_driver.py" 40 >> "%s" 2>&1\r\n'
       'echo EXITCODE=%%ERRORLEVEL%% >> "%s"\r\n'
       'echo ==== SOAK DRIVER END %%DATE%% %%TIME%% ====>> "%s"\r\n'
       % (DLOG, DLOG, DLOG, DLOG))
io.open(WRAPPER, "w", encoding="ascii", newline="").write(CMD)
print("wrapper  :", WRAPPER)
print("log      :", DLOG)

if os.path.isfile(DLOG):
    os.rename(DLOG, DLOG + ".prev.%d" % int(time.time()))
    print("rotated the previous driver log")


def job_check(pid):
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, int(pid))
    if not h:
        return None
    try:
        res = wt.BOOL(False)
        ok = k32.IsProcessInJob(wt.HANDLE(h), None, ctypes.byref(res))
        return bool(res.value) if ok else None
    finally:
        k32.CloseHandle(wt.HANDLE(h))


script = ("$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
          "-Arguments @{ CommandLine = 'cmd.exe /c \"%s\"'; CurrentDirectory = '%s' }; "
          "Write-Output $r.ProcessId" % (WRAPPER, ROOT))
p = subprocess.run([PS, "-NoProfile", "-NonInteractive", "-Command", script],
                   capture_output=True, timeout=180)
out = (p.stdout or b"").decode("utf-8", "replace")
pid = 0
for tok in reversed(out.replace("\r", "\n").split()):
    if tok.strip().isdigit():
        pid = int(tok.strip())
        break
print("DRIVER_PID=%d  in_job=%s" % (pid, job_check(pid)))
io.open(os.path.join(SOAK, "DRIVER_PID.txt"), "w").write("%d\n" % pid)

time.sleep(25)
print("=== early driver log ===")
print(io.open(DLOG, encoding="utf-8", errors="replace").read()[-2500:]
      if os.path.isfile(DLOG) else "(no log yet)")
