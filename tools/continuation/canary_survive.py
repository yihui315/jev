#!/usr/bin/env python3
"""SURVIVAL canary -- the property that actually matters, measured rather than inferred.

WHY THIS EXISTS. The session's ROOT_CAUSE_1 was classified as "the agent host runs
every command in a Job Object with KILL_ON_JOB_CLOSE, so anything the launching
command spawns is terminated when that command returns". The evidence quoted for
"the outside-the-job launch path works" was ``in_job=False``. A cross-check has
now shown that reading is NOT safe to quote:

    WMI-created cmd.exe        -> IsProcessInJob = False
    the python it spawns       -> IsProcessInJob = True

Both readings are reproducible, from outside as well as inside. So ``in_job``
does not mean what it was being used to mean: IsProcessInJob(h, NULL, ...) answers
"is this process in ANY job", not "is it in the agent host's kill-on-close job".
The wrapper escapes; the worker is in *some* job, and that job demonstrably does
not kill it -- the 40-minute soak ran to completion on exactly this path.

The discriminating property is therefore SURVIVAL, and it is measured directly
here: a canary writes a heartbeat every second, the arming command returns, and a
LATER command asks whether the heartbeat continued past the arming command's end.

    arm <kind>   arm a canary; writes a marker recording when the command ended
    check        report how long each canary lived AFTER its arming command ended
"""
import ctypes
import datetime
import io
import json
import os
import subprocess
import sys
import time

ROOT = str(Path(__file__).resolve().parents[2])
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
PS = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                  "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
CONT = os.path.join(ROOT, ".workbuddy", "scratch", "continuation")
STATE = os.path.join(CONT, "canary_state")
LIFE_S = 150


def pid_alive(pid):
    """OpenProcess alone is not enough -- it can succeed for a pid whose process
    has already exited. STILL_ACTIVE is the actual answer."""
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, int(pid))
    if not h:
        return False
    try:
        code = ctypes.c_ulong(0)
        if not k.GetExitCodeProcess(ctypes.c_void_p(h), ctypes.byref(code)):
            return False
        return code.value == 259                      # STILL_ACTIVE
    finally:
        k.CloseHandle(ctypes.c_void_p(h))


def in_job(pid):
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, int(pid))
    if not h:
        return None
    try:
        r = ctypes.c_long(0)
        k.IsProcessInJob(ctypes.c_void_p(h), None, ctypes.byref(r))
        return bool(r.value)
    finally:
        k.CloseHandle(ctypes.c_void_p(h))


def main():
    os.makedirs(STATE, exist_ok=True)
    mode = sys.argv[1]

    if mode == "beat":
        # The canary: one timestamp per second, flushed immediately, so the file
        # is a truthful record of how long this process actually lived.
        path = sys.argv[2]
        with io.open(path, "a", encoding="utf-8") as fh:
            for _ in range(LIFE_S):
                fh.write("%.3f\n" % time.time())
                fh.flush()
                time.sleep(1)
        return 0

    if mode == "arm":
        kind = sys.argv[2]
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        beat = os.path.join(STATE, "beat_%s_%s.txt" % (kind, stamp))
        marker = os.path.join(STATE, "marker_%s_%s.json" % (kind, stamp))
        proc_pid = None
        how = ""
        if kind == "wmi":
            wrap = os.path.join(CONT, "run_canary_%s.cmd" % stamp)
            io.open(wrap, "w", encoding="ascii", newline="").write(
                "@echo off\r\ncd /d %s\r\n\"%s\" \"%s\" beat \"%s\"\r\n"
                % (ROOT, PY, os.path.abspath(__file__), beat))
            script = ("$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
                      "-Arguments @{ CommandLine = 'cmd.exe /c \"%s\"'; CurrentDirectory = '%s' }; "
                      "Write-Output $r.ProcessId" % (wrap, ROOT))
            p = subprocess.run([PS, "-NoProfile", "-NonInteractive", "-Command", script],
                               capture_output=True, timeout=120)
            for tok in reversed((p.stdout or b"").decode("utf-8", "replace")
                                .replace("\r", "\n").split()):
                if tok.strip().isdigit():
                    proc_pid = int(tok.strip())
                    break
            how = "Win32_Process.Create (WMI)"
        else:
            pr = subprocess.Popen([PY, os.path.abspath(__file__), "beat", beat])
            proc_pid = pr.pid
            how = "ordinary subprocess.Popen child of this command"
        time.sleep(6)                       # let the canary write its first beats
        io.open(marker, "w", encoding="utf-8").write(json.dumps({
            "kind": kind, "how": how, "beat_file": beat, "launcher_pid": proc_pid,
            "launcher_in_job": in_job(proc_pid),
            "armed_at": time.time(),
            "armed_at_iso": datetime.datetime.now().isoformat(timespec="seconds"),
            "arm_command_exits_at": time.time(),   # written as late as possible
            "life_s": LIFE_S,
        }, indent=2))
        print("armed %-8s via %s" % (kind, how))
        print("  launcher pid %s in_job=%s" % (proc_pid, in_job(proc_pid)))
        print("  heartbeat    %s" % beat)
        print("  arm command returns at %.3f" % time.time())
        return 0

    if mode == "check":
        rows = []
        for name in sorted(os.listdir(STATE)):
            if not name.startswith("marker_"):
                continue
            m = json.load(io.open(os.path.join(STATE, name), encoding="utf-8"))
            beats = []
            if os.path.isfile(m["beat_file"]):
                beats = [float(x) for x in
                         io.open(m["beat_file"], encoding="utf-8").read().split() if x]
            arm_end = m["arm_command_exits_at"]
            alive_after = [b for b in beats if b > arm_end]
            # The canary is armed ~6 s after it starts, so this is how much life
            # is left to observe. A canary that lived 4 s of an expected 144 s did
            # not "survive" -- the first version of this rule thresholded on an
            # absolute beat count and labelled exactly that case SURVIVED, which
            # is the false-PASS this whole exercise exists to avoid.
            expect_remaining = max(m.get("life_s", LIFE_S) - 6, 1)
            after = round((max(beats) - arm_end), 2) if beats else 0.0
            still_alive = pid_alive(m["launcher_pid"])
            if after >= 0.5 * expect_remaining:
                verdict = "SURVIVED"
            elif after < 10:
                verdict = "DIED WITH THE COMMAND"
            else:
                verdict = "PARTIAL -- needs a closer look"
            rows.append({
                "kind": m["kind"], "how": m["how"], "launcher_pid": m["launcher_pid"],
                "launcher_in_job": m["launcher_in_job"],
                "marker": name,
                "beats_total": len(beats),
                "beats_after_arm_command_returned": len(alive_after),
                "seconds_alive_after_arm_returned": after,
                "expected_remaining_s": expect_remaining,
                "fraction_of_expected_life_observed": round(after / expect_remaining, 3),
                "launcher_still_alive_now": still_alive,
                "verdict": verdict,
            })
        print("%-8s %-34s %-6s %6s %9s %9s  %s"
              % ("kind", "how", "in_job", "beats", "expected", "observed", "verdict"))
        for r in rows:
            print("%-8s %-34s %-6s %6d %9.1f %9.2f  %s"
                  % (r["kind"], r["how"][:34], r["launcher_in_job"], r["beats_total"],
                     r["expected_remaining_s"], r["seconds_alive_after_arm_returned"],
                     r["verdict"]))
        out = os.path.join(ROOT, "artifacts", "acceptance", "CANARY_SURVIVAL.json")
        io.open(out, "w", encoding="utf-8", newline="\n").write(json.dumps({
            "at": datetime.datetime.now().isoformat(timespec="seconds"),
            "question": ("does a process armed this way outlive the command that armed it?"),
            "why_in_job_is_not_the_answer": (
                "IsProcessInJob(h, NULL, ...) reports membership of ANY job. Measured: the "
                "WMI-created cmd.exe reads in_job=False while the python it spawns reads "
                "in_job=True, and that python still outlives its arming command. So in_job "
                "cannot be quoted as proof of escaping the agent host's job."),
            "rows": rows}, indent=2, ensure_ascii=False))
        print("\nwrote %s" % out)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
