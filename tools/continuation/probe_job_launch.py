#!/usr/bin/env python3
"""Run probe_job_context.py down both launch paths and compare the measurements.

Path 1: launched from this Bash-spawned python  -> the agent host's job
Path 2: launched by Win32_Process.Create (WMI)  -> claimed to escape the job
For each, the process reports its own job membership and is ALSO measured from
outside by the launcher's exact code. Agreement/disagreement is the answer.
"""
import ctypes
import ctypes.wintypes as wt
import datetime
import io
import json
import os
import subprocess
import time

ROOT = r"F:\jev"
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
PS = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                  "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
CONT = os.path.join(ROOT, ".workbuddy", "scratch", "continuation")
PROBE = os.path.join(CONT, "probe_job_context.py")
STAMP = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
R_INSIDE = os.path.join(CONT, "jobctx_bash_%s.json" % STAMP)
R_WMI = os.path.join(CONT, "jobctx_wmi_%s.json" % STAMP)
WRAP = os.path.join(CONT, "run_jobctx_%s.cmd" % STAMP)


def ext_job(pid):
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


# --- path 1: ordinary child of this (Bash-launched) process --------------------
p1 = subprocess.Popen([PY, PROBE, "inside", R_INSIDE])
time.sleep(6)
d1 = json.load(io.open(R_INSIDE, encoding="utf-8"))
ext1 = ext_job(d1["pid"])

# --- path 2: WMI-created -------------------------------------------------------
io.open(WRAP, "w", encoding="ascii", newline="").write(
    "@echo off\r\ncd /d %s\r\n\"%s\" \"%s\" inside \"%s\"\r\n"
    % (ROOT, PY, PROBE, R_WMI))
script = ("$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
          "-Arguments @{ CommandLine = 'cmd.exe /c \"%s\"'; CurrentDirectory = '%s' }; "
          "Write-Output $r.ProcessId" % (WRAP, ROOT))
pw = subprocess.run([PS, "-NoProfile", "-NonInteractive", "-Command", script],
                    capture_output=True, timeout=120)
wmi_wrap_pid = 0
for tok in reversed((pw.stdout or b"").decode("utf-8", "replace").replace("\r", "\n").split()):
    if tok.strip().isdigit():
        wmi_wrap_pid = int(tok.strip())
        break
for _ in range(24):
    if os.path.isfile(R_WMI):
        break
    time.sleep(5)
d2 = json.load(io.open(R_WMI, encoding="utf-8")) if os.path.isfile(R_WMI) else None
ext2 = ext_job(d2["pid"]) if d2 else None

print("=" * 78)
print("PATH 1  bash-spawned python  (this process IS in the agent host's job)")
print("=" * 78)
print("wrapper/child pid        :", d1["pid"])
print("self via own handle      :", d1["self_via_own_handle"])
print("external style on self   :", d1["external_style_on_self"])
print("measured from outside    :", {"in_job": ext1})
print("chain (pid, ppid, in_job from outside):")
for hop in d1["chain"]:
    print("    ", hop)
print()
print("=" * 78)
print("PATH 2  WMI-created cmd.exe -> python")
print("=" * 78)
print("wmi wrapper pid          :", wmi_wrap_pid, "-> in_job(ext) =", ext_job(wmi_wrap_pid))
if d2:
    print("python pid               :", d2["pid"])
    print("self via own handle      :", d2["self_via_own_handle"])
    print("external style on self   :", d2["external_style_on_self"])
    print("measured from outside    :", {"in_job": ext2})
    print("chain (pid, ppid, in_job from outside):")
    for hop in d2["chain"]:
        print("    ", hop)
else:
    print("(probe record never appeared)")

# stop the sleepers so nothing is left running
for d in (d1, d2):
    if not d:
        continue
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x0001, False, int(d["pid"]))     # PROCESS_TERMINATE
    if h:
        k.TerminateProcess(wt.HANDLE(h), 0)
        k.CloseHandle(wt.HANDLE(h))
p1.wait()
print()
print("both sleepers terminated")
