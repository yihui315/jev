#!/usr/bin/env python3
"""Launch lifetime_trace_v2.py via WMI so it survives this command's end.

WMI's Win32_Process.Create makes the process a child of WmiPrvSE, i.e. OUTSIDE
the agent Job Object inherited by everything the agent shell starts. Proven by
the C1/C2/C3 detach experiment (Start-Process died; WMI and schtasks survived).

Prints TRACER_PID=<pid> and returns immediately.
"""
import subprocess, os, io, json, time, ctypes, ctypes.wintypes as wt

ROOT = r"F:\jev"
PS = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                  "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
VENV_PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
SCRIPT = os.path.join(ROOT, ".workbuddy", "scratch", "continuation", "lifetime_trace_v2.py")
ACC = os.path.join(ROOT, "artifacts", "acceptance")
DURATION = os.environ.get("TRACE_DURATION", "1200")


def ps(s, t=120):
    r = subprocess.run([PS, "-NoProfile", "-NonInteractive", "-Command", s],
                       capture_output=True, timeout=t)
    return ((r.stdout or b"").decode("utf-8", "replace")
            + (r.stderr or b"").decode("utf-8", "replace")).strip()


def in_job(pid):
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


# fresh artifacts so we never read a previous run's rows
for f in ("JCC_LIFETIME_TRACE.jsonl", "JCC_LIFETIME_EVENTS.jsonl",
          "JCC_LIFETIME_HEARTBEAT.json", "JCC_LIFETIME_SUMMARY.json"):
    try:
        os.remove(os.path.join(ACC, f))
    except OSError:
        pass

cmdline = '"%s" "%s" %s' % (VENV_PY, SCRIPT, DURATION)
print("WMI CommandLine:", cmdline)
o = ps("$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
       "-Arguments @{ CommandLine = '%s'; CurrentDirectory = '%s' }; "
       "Write-Output $r.ProcessId" % (cmdline.replace("'", "''"), ROOT.replace("'", "''")))
pid = 0
for tok in reversed(o.replace("\r", "\n").split()):
    if tok.strip().isdigit():
        pid = int(tok.strip())
        break
print("TRACER_PID=%d" % pid)
print("in_job(tracer)=%s  self_ppid=%s" % (in_job(pid), os.getppid()))

# give it 12 s and confirm it is running and has reached the scene stage
time.sleep(12)
alive = ps("(Get-Process -Id %d -ErrorAction SilentlyContinue | Measure-Object).Count" % pid) if pid else "0"
print("tracer alive after 12s:", (alive.splitlines()[-1].strip() if alive else "?"))
anc = ps("Get-CimInstance Win32_Process -Filter 'ProcessId=%d' | "
         "Select-Object ProcessId,ParentProcessId,Name | ConvertTo-Json -Compress" % pid) if pid else ""
print("tracer proc:", anc[:300])
par = ps("$p = (Get-CimInstance Win32_Process -Filter 'ProcessId=%d').ParentProcessId; "
         "Get-CimInstance Win32_Process -Filter \"ProcessId=$p\" | "
         "Select-Object ProcessId,Name | ConvertTo-Json -Compress" % pid) if pid else ""
print("tracer parent:", par[:300])
ls = os.path.join(ROOT, "runtime", "jcc", "lifetime-trace.log")
if os.path.isfile(ls):
    print("--- own log tail ---")
    print(io.open(ls, encoding="utf-8", errors="replace").read()[-1200:])
