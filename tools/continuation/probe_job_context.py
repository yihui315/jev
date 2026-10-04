#!/usr/bin/env python3
"""Cross-check job membership from INSIDE a process and from OUTSIDE it.

The launcher measures the process it created (external handle -> OpenProcess ->
IsProcessInJob) and has consistently reported in_job=False for the WMI-created
wrapper. The proof script now measures ITSELF with the same API and reported
in_job=True. Both cannot describe the same process, so one of the two code paths
is wrong -- and which one matters, because the whole reason this session's
service died is that the agent host runs commands in a kill-on-close job.

Usage:
    probe_job_context.py inside <outfile>   # write pid + self-measurement, sleep
    probe_job_context.py outside <pid>      # measure someone else, exactly as the launcher does
"""
import ctypes
import ctypes.wintypes as wt
import io
import json
import os
from pathlib import Path
import sys
import time

ROOT = str(Path(__file__).resolve().parents[2])


def external_job_check(pid):
    """Verbatim the launcher's method: external handle, NULL job handle."""
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, int(pid))          # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return {"open_ok": False, "in_job": None}
    try:
        r = wt.BOOL(False)
        ok = k.IsProcessInJob(wt.HANDLE(h), None, ctypes.byref(r))
        return {"open_ok": True, "call_ok": bool(ok), "in_job": bool(r.value)}
    finally:
        k.CloseHandle(wt.HANDLE(h))


def self_job_check():
    """Verbatim what watchdog_proof._in_job does: a handle to our OWN pid."""
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, os.getpid())
    if not h:
        return {"open_ok": False, "in_job": None}
    try:
        k.IsProcessInJob.argtypes = [wt.HANDLE, wt.HANDLE, ctypes.POINTER(wt.BOOL)]
        k.IsProcessInJob.restype = wt.BOOL
        r = wt.BOOL(False)
        ok = k.IsProcessInJob(wt.HANDLE(h), None, ctypes.byref(r))
        return {"open_ok": True, "call_ok": bool(ok), "in_job": bool(r.value)}
    finally:
        k.CloseHandle(wt.HANDLE(h))


def parent_chain():
    """(pid, ppid) pairs walking up, so the launch path is recorded, not assumed."""
    import subprocess
    out = []
    pid = os.getpid()
    # Spawn the SAME interpreter that is running this probe. The literal this
    # replaced ('F:\\jev\\.venv\\Scripts\\python.exe') made the probe fail on any
    # host without an F: volume -- subprocess.run raises FileNotFoundError and
    # the parent chain silently came back all-zeroes, which reads as "no parent
    # found" rather than "this tool cannot run here". sys.executable cannot be
    # wrong about where python is, and it is the interpreter whose job-object
    # membership we actually want to measure.
    _PY = sys.executable or os.path.join(ROOT, ".venv", "Scripts", "python.exe")
    for _ in range(6):
        p = subprocess.run(
            [_PY, "-c",
             "import ctypes,ctypes.wintypes as wt,sys\n"
             "k=ctypes.windll.kernel32\n"
             "class PE32(ctypes.Structure):\n"
             "    _fields_=[('a',ctypes.c_void_p),('b',ctypes.c_void_p),('c',ctypes.c_void_p),('d',ctypes.c_void_p)]\n"
             "class PBI(ctypes.Structure):\n"
             "    _fields_=[('Reserved1',ctypes.c_void_p),('PebBaseAddress',ctypes.c_void_p),"
             "('Reserved2',ctypes.c_void_p*2),('UniqueProcessId',ctypes.c_void_p),"
             "('InheritedFromUniqueProcessId',ctypes.c_void_p)]\n"
             "h=k.OpenProcess(0x1000,False,int(sys.argv[1]))\n"
             "if not h: print('0'); raise SystemExit\n"
             "pbi=PBI(); n=ctypes.c_ulong()\n"
             "k.NtQueryInformationProcess(h,0,ctypes.byref(pbi),ctypes.sizeof(pbi),ctypes.byref(n))\n"
             "print(int(pbi.InheritedFromUniqueProcessId or 0))\n",
             str(pid)], capture_output=True, timeout=60)
        toks = [t for t in (p.stdout or b"").decode().split() if t.isdigit()]
        ppid = int(toks[0]) if toks else 0
        out.append({"pid": pid, "ppid": ppid,
                    "in_job_external": external_job_check(pid)["in_job"]})
        if ppid <= 0:
            break
        pid = ppid
    return out


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "inside":
        rec = {"pid": os.getpid(),
               "self_via_own_handle": self_job_check(),
               "external_style_on_self": external_job_check(os.getpid()),
               "chain": parent_chain()}
        io.open(sys.argv[2], "w", encoding="utf-8").write(json.dumps(rec, indent=2))
        time.sleep(120)                       # stay alive to be measured from outside
    else:
        print(json.dumps(external_job_check(int(sys.argv[2])), indent=2))
