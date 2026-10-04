#!/usr/bin/env python3
"""FINAL-05: detached survival, measured on the launch paths this platform uses.

THE PROPERTY. A scheduled task that returns while its child dies is worse than
no scheduled task: the tick reports success, the pipeline is gone, and the next
tick reports success again. So the claim to establish is behavioural -- *a
process this platform starts outlives the command that started it* -- and it must
be measured, because in an earlier phase of this project exactly this was gotten
wrong: the launching command returned and the worker died within ~9 s.

WHY ``in_job`` IS NOT THE CRITERION. That earlier conclusion quoted
``IsProcessInJob = False`` as proof of escaping the agent host's job object.
Measured then, and recorded in ``canary_survive.py``: the WMI-created ``cmd.exe``
reads ``False`` while the python it spawns reads ``True``, and that python still
outlives its arming command. ``IsProcessInJob(h, NULL, ...)`` answers "is this
process in ANY job". It is recorded here as information and is never what the
verdict rests on.

THE ARMS, AND WHY THERE ARE THREE OF THEM
-----------------------------------------
The platform does not have one launcher, it has a launcher per context, and the
measurement has to be taken where each one is actually used:

1. ``Start-JccDetachedPython`` -- what ``scripts/continuation-watchdog.ps1``
   calls on every tick, from the SYSTEM identity the scheduled task runs as.
   Measured here by a canary armed through the shipped function.
2. ``Win32_Process.Create`` (WMI) -- what the operator-run acceptance launchers
   use (``launch_soak.py``, ``launch_t1_wmi.py``, ``launch_trace_via_wmi.py``).
   MEASURED TO BE UNREACHABLE FROM THIS STEP, and the reason is not a guess.
   As ``NT AUTHORITY\\SYSTEM`` the provider answers ``ReturnValue=2``
   (ACCESS_DENIED) after a ~31 s round trip, over two independent provider paths
   (``Invoke-CimMethod`` and ``[wmiclass]``), while the byte-identical command
   line returns ``ReturnValue=0`` from ``YH\\Administrator`` minutes earlier on
   the same machine. The cause is in the token: ``whoami /priv`` shows
   ``SeAssignPrimaryTokenPrivilege`` and ``SeIncreaseQuotaPrivilege`` present but
   **disabled** under SYSTEM, and ``Win32_Process.Create`` creates the process
   with the caller's token, which needs both enabled. So this mechanism cannot be
   exercised by the pipeline, and the arm reports that as ``NOT EXERCISABLE``
   with the raw return value and the control that isolates identity as the
   variable -- it does NOT quietly become a pass, and rv=0 with no heartbeat is
   still a FAIL.
3. THE SCHEDULER ITSELF, in production. This is not a canary: it reads the
   watchdog's own audit log for the last tick that reported ``STARTED``, checks
   that a LATER tick ran (which can only happen if the launching tick returned,
   because ``MultipleInstances=IgnoreNew`` suppresses a hung one), and checks
   that a live controller still holds a fresh lock. That is the property, on the
   real pipeline, in the real identity.

The verdict therefore rests on arms 1 and 3 -- both measured here, both in the
SYSTEM identity the pipeline runs as -- and arm 2 is carried as a named scope
exclusion, never as evidence.

NO PIPES ANYWHERE NEAR THE MEASUREMENT. Every command started here writes its
stdout/stderr to FILES. This host has already produced one misleading number from
a pipe: a watchdog call returned at t=46.3 s, exactly the child's lifetime, while
the caller held PowerShell's stdout on a pipe. Measuring the launch path through a
pipe would be measuring the pipe.

Usage
-----
    python tools/continuation/detached_survival.py
    python tools/continuation/detached_survival.py --selftest
    python tools/continuation/detached_survival.py echo <beat-file>   # internal
"""
from __future__ import annotations

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
LIB = os.path.join(ROOT, "scripts", "lib", "JccWatchdogCommon.ps1")
WORK = os.path.join(ROOT, ".workbuddy", "scratch", "detached")
ACC = os.path.join(ROOT, "artifacts", "acceptance", "DETACHED_SURVIVAL_TEST.json")
WATCHDOG_LOG = os.path.join(ROOT, ".workbuddy", "scheduled", "logs",
                            "continuation-watchdog.jsonl")
LOCK = os.path.join(ROOT, ".workbuddy", "locks", "jcc-continuation.lock.json")

#: The canary's lifetime. Long enough that "still beating after the arming command
#: returned" cannot be explained by a slow arming command, short enough that this
#: step stays well inside its 900 s budget.
LIFE_S = 24
#: How long to let both canaries run before reading their heartbeats.
OBSERVE_S = 26
#: The ticks that report a live controller. Anything else (ERROR, STALE, a bare
#: STARTED) is not evidence that the controller survived.
LIVE_ACTIONS = ("NOOP", "CONFIRMED")


def log(msg):
    line = "[detached] %s" % msg
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", "backslashreplace").decode("ascii"), flush=True)


def pid_alive(pid):
    """OpenProcess is not enough: it succeeds for a pid whose process has exited.
    STILL_ACTIVE from GetExitCodeProcess is the actual answer.

    Fail-safe on the permission branch the same way ``Test-JccPidAlive`` is: a
    handle we cannot open is not proof of death, and reading it as death is how a
    second controller gets started on top of a live one.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, pid)
    if not h:
        # May mean "no such pid" or "no permission". Only the first is death, and
        # the two are indistinguishable through this API, so ask the process table.
        try:
            out = subprocess.run(
                [PS, "-NoProfile", "-NonInteractive", "-Command",
                 "(Get-Process -Id %d -ErrorAction SilentlyContinue) -ne $null" % pid],
                capture_output=True, text=True, timeout=30)
            return "True" in (out.stdout or "")
        except (OSError, subprocess.SubprocessError):
            return True
    try:
        code = ctypes.c_ulong(0)
        if not k.GetExitCodeProcess(ctypes.c_void_p(h), ctypes.byref(code)):
            return False
        return code.value == 259
    finally:
        k.CloseHandle(ctypes.c_void_p(h))


def classify(beats, arm_exit, life_s):
    """How much of the canary's expected post-arming life was actually observed.

    Extracted so ``--selftest`` can prove the rule REJECTS a canary that died with
    its launcher. A survival rule that only ever says SURVIVED is not a
    measurement, it is a decoration -- and this project has already paid for one
    of those: an absolute beat count that labelled a 4-second canary SURVIVED.
    """
    if not beats:
        return {"verdict": "NOT OBSERVED", "expected_remaining_s": 0,
                "seconds_alive_after_arm_returned": 0.0, "fraction": 0.0,
                "canary_lifetime_s": 0.0, "beats": 0,
                "why": "the canary wrote no heartbeat at all: nothing was measured"}
    started, last = min(beats), max(beats)
    expected_remaining = max(life_s - (arm_exit - started), 1.0)
    after = last - arm_exit
    fraction = after / expected_remaining
    if after >= 0.5 * expected_remaining:
        verdict = "SURVIVED"
    elif after < 10:
        verdict = "DIED WITH THE LAUNCHING COMMAND"
    else:
        verdict = "PARTIAL"
    return {"verdict": verdict, "expected_remaining_s": round(expected_remaining, 2),
            "seconds_alive_after_arm_returned": round(after, 2),
            "fraction": round(fraction, 3),
            "canary_lifetime_s": round(last - started, 2),
            "beats": len(beats)}


#: The two privileges ``Win32_Process.Create`` needs enabled in the caller's
#: token. Named here so the artifact states the cause rather than the symptom.
PRIVILEGES_WMI_NEEDS = ("SeAssignPrimaryTokenPrivilege", "SeIncreaseQuotaPrivilege")

#: The WMI provider's return codes we are willing to read as "this caller is not
#: allowed to use this mechanism", i.e. a caller-side permission outcome rather
#: than a property of the launched work. Anything else is reported verbatim.
NOT_EXERCISABLE_RV = {2: "ACCESS_DENIED"}


def classify_wmi(return_value, beats, arm_exit, life_s, threw=None):
    """Decide the WMI arm on its own terms.

    Three outcomes, and the middle one is the delicate one. ``NOT EXERCISABLE``
    is only ever returned when the *provider refused the call*; the moment the
    provider accepts it, the arm is back under the same survival rule as every
    other arm, so a launcher that stops working cannot hide behind "this
    environment cannot do that".
    """
    if threw:
        return {"verdict": "NOT EXERCISABLE", "reason": "CALL_THREW",
                "return_value": None, "throw": str(threw)[:400],
                "why": ("the provider call itself failed, so nothing was launched and "
                        "nothing was measured")}
    if return_value in NOT_EXERCISABLE_RV:
        return {"verdict": "NOT EXERCISABLE",
                "reason": NOT_EXERCISABLE_RV[return_value],
                "return_value": return_value,
                "why": ("the provider refused the call with ReturnValue=%d (ACCESS_DENIED); "
                        "no process was created, so this arm carries no survival claim"
                        % return_value)}
    if return_value not in (0,):
        return {"verdict": "NOT OBSERVED", "reason": "UNEXPECTED_RETURN_VALUE",
                "return_value": return_value,
                "why": ("ReturnValue=%r is not a code this arm knows how to read as a "
                        "caller-side refusal" % (return_value,))}
    out = dict(classify(beats, arm_exit, life_s))
    out["return_value"] = return_value
    return out


def classify_production(records, lock_pid, lock_alive, now):
    """The scheduler arm: the real pipeline, not a canary.

    Falsifiable in both directions. It fails if the log has no ``STARTED`` tick,
    if no tick ran after it (a hung tick plus ``MultipleInstances=IgnoreNew``
    makes every later tick disappear), if no later tick observed a live
    controller, or if nothing live holds the lock right now.
    """
    started_idx = None
    for i, r in enumerate(records):
        if r.get("action") == "STARTED":
            started_idx = i
    if started_idx is None:
        return {"verdict": "NOT OBSERVED", "beats": 0,
                "why": ("the watchdog audit log contains no STARTED tick: the scheduled "
                        "task has never launched a controller, so there is nothing to "
                        "have survived")}
    started = records[started_idx]
    after = records[started_idx + 1:]
    live = [r for r in after
            if r.get("action") in LIVE_ACTIONS and int(r.get("pid") or 0) > 0]
    if not after:
        return {"verdict": "NOT OBSERVED", "beats": 0, "started": started,
                "why": ("no tick ran after the STARTED tick: the launching tick never "
                        "returned (MultipleInstances=IgnoreNew would suppress the next "
                        "one)")}
    if not live:
        return {"verdict": "NOT OBSERVED", "beats": 0, "started": started,
                "later_ticks": after[:4],
                "why": ("ticks ran after the STARTED one, but none of them reported a "
                        "live controller: %s" % ", ".join(
                            str(r.get("action")) for r in after[:4]))}
    elapsed = None
    try:
        t0 = datetime.datetime.fromisoformat(started["at"])
        # A naive `now` and an aware `at` cannot be subtracted; align them rather
        # than silently dropping the only number that says the tick is in the past.
        if t0.tzinfo is None and now.tzinfo is not None:
            t0 = t0.replace(tzinfo=now.tzinfo)
        here = now if now.tzinfo is not None else now.replace(tzinfo=t0.tzinfo)
        elapsed = (here - t0).total_seconds()
    except (KeyError, TypeError, ValueError):
        pass
    if not lock_alive:
        return {"verdict": "NOT OBSERVED", "beats": len(live), "started": started,
                "later_live_tick": live[0], "lock_pid": lock_pid,
                "why": ("no live process holds the controller lock now (lock pid=%r), so "
                        "whatever the tick reported is not still true" % (lock_pid,))}
    return {"verdict": "SURVIVED", "beats": len(live), "started": started,
            "later_live_tick": live[0], "lock_pid": lock_pid,
            "lock_alive": True, "seconds_since_the_starting_tick": round(elapsed or 0.0, 1),
            "why": ("a tick started the controller, %d later tick(s) on schedule "
                    "reported a live controller, and pid %d still holds a fresh lock "
                    "%.0fs on" % (len(live), lock_pid or 0, elapsed or 0.0))}


def decide(arm1, arm2, arm3):
    """The run's verdict, as a pure function so ``--selftest`` can falsify it.

    ``ok`` requires BOTH measured arms. Arm 2 under ``NOT EXERCISABLE`` is
    recorded in ``not_claimed`` and changes nothing -- which is the point: it is
    neither evidence nor an excuse.
    """
    problems = []
    not_claimed = []
    if arm1.get("verdict") != "SURVIVED":
        problems.append("Start-JccDetachedPython: %s (%s)" % (
            arm1.get("verdict"), arm1.get("why")
            or "%ss alive after the arming command returned, expected ~%ss"
            % (arm1.get("seconds_alive_after_arm_returned"),
               arm1.get("expected_remaining_s"))))
    if arm3.get("verdict") != "SURVIVED":
        problems.append("the scheduler (production): %s (%s)"
                        % (arm3.get("verdict"), arm3.get("why")))
    if arm2.get("verdict") == "NOT EXERCISABLE":
        not_claimed.append({
            "arm": "Win32_Process.Create (WMI)",
            "reason": arm2.get("reason"),
            "return_value": arm2.get("return_value"),
            "why": arm2.get("why"),
            "where_it_is_used": ("the operator-run acceptance launchers, which run as "
                                 "the interactive administrator; the pipeline itself "
                                 "never calls it"),
        })
    elif arm2.get("verdict") != "SURVIVED":
        problems.append("Win32_Process.Create (WMI): %s (%s)"
                        % (arm2.get("verdict"), arm2.get("why")
                           or "%ss alive after the arming command returned"
                           % arm2.get("seconds_alive_after_arm_returned")))
    return problems, not_claimed


def _run_to_files(cmd, out_path, err_path, timeout):
    """Run a command with BOTH streams on files. Never a pipe: see the module
    docstring -- a pipe around the launch path measures the pipe."""
    with io.open(out_path, "wb") as out, io.open(err_path, "wb") as err:
        started = time.time()
        proc = subprocess.run(cmd, stdout=out, stderr=err, timeout=timeout)
    return proc.returncode, time.time() - started


def _read_text(path):
    if not os.path.isfile(path):
        return ""
    with io.open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read().strip()


def _read_beats(path):
    if not os.path.isfile(path):
        return []
    with io.open(path, "r", encoding="utf-8", errors="replace") as fh:
        return [float(x) for x in fh.read().split() if x]


def _read_json(path):
    if not os.path.isfile(path):
        return None
    try:
        with io.open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _read_jsonl(path):
    out = []
    if not os.path.isfile(path):
        return out
    with io.open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        for line in fh.read().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                out.append({"action": "<unparsable>"})
    return out


def _arm2_script(wrapper, report_path, root):
    """The WMI arm, as a FILE rather than an inline -Command string.

    Both deliveries were measured to agree on this host, so this is not a fix --
    it is a choice: a report that has to survive Python's quoting, then
    PowerShell's command-line parsing, and then a cipher of backslashes is a
    report whose failures cannot be attributed. The .ps1 is written verbatim.
    """
    return (
        "# Win32_Process.Create: the mechanism the operator-run launchers use.\n"
        "# Called from whatever identity this step runs as. It reports; it does\n"
        "# not judge -- the verdict is decided in classify_wmi().\n"
        "$ErrorActionPreference = 'Continue'\n"
        "$lines = New-Object System.Collections.Generic.List[string]\n"
        "$lines.Add('caller_identity=' + [System.Security.Principal.WindowsIdentity]::GetCurrent().Name)\n"
        "$rv = $null\n"
        "$createdPid = 0\n"
        "try {\n"
        "    $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{\n"
        "        CommandLine = 'cmd.exe /c \"%s\"'\n"
        "        CurrentDirectory = '%s'\n"
        "    }\n"
        "    $rv = $r.ReturnValue\n"
        "    $createdPid = [int]$r.ProcessId\n"
        "} catch {\n"
        "    $lines.Add('provider_threw=' + $_.Exception.GetType().FullName + ':: ' + $_.Exception.Message)\n"
        "}\n"
        "$lines.Add('return_value=' + $rv)\n"
        "$lines.Add('created_pid=' + $createdPid)\n"
        "if ($rv -ne 0) {\n"
        "    # A second, independent provider path. If the refusal is a property of\n"
        "    # the identity it must reproduce here; if only one path refuses, the\n"
        "    # finding is about the cmdlet and not about the environment.\n"
        "    try {\n"
        "        $m = [wmiclass]'\\\\.\\root\\cimv2:Win32_Process'\n"
        "        $r2 = $m.Create('cmd.exe /c \"%s\"')\n"
        "        $lines.Add('second_provider_return_value=' + $r2.ReturnValue)\n"
        "    } catch {\n"
        "        $lines.Add('second_provider_threw=' + $_.Exception.Message)\n"
        "    }\n"
        "}\n"
        "try {\n"
        "    $priv = & $env:SystemRoot\\System32\\whoami.exe /priv 2>&1\n"
        "    foreach ($l in $priv) {\n"
        "        foreach ($name in @('SeAssignPrimaryTokenPrivilege', 'SeIncreaseQuotaPrivilege')) {\n"
        "            if ($l -match $name) { $lines.Add('privilege|' + ($l -replace '\\s+', ' ').Trim()) }\n"
        "        }\n"
        "    }\n"
        "} catch {\n"
        "    $lines.Add('privilege_query_threw=' + $_.Exception.Message)\n"
        "}\n"
        "$enc = New-Object System.Text.UTF8Encoding($false)\n"
        "[System.IO.File]::WriteAllLines('%s', $lines, $enc)\n"
        "foreach ($l in $lines) { Write-Output $l }\n"
        % (wrapper, root, wrapper, report_path))


def _parse_report(path):
    """``key=value`` lines plus ``privilege|`` rows, as a dict. Unknown keys are
    kept: an arm that only surfaces the fields someone remembered to expect is an
    arm that can hide a new outcome."""
    data = {"privileges": []}
    for line in _read_text(path).splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("privilege|"):
            data["privileges"].append(line.split("|", 1)[1].strip())
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            data[k.strip()] = v.strip()
        else:
            data.setdefault("unparsed", []).append(line)
    return data


def run() -> int:
    os.makedirs(WORK, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    here = os.path.join(WORK, stamp)
    os.makedirs(here, exist_ok=True)
    me = os.path.abspath(__file__)

    beat_sp = os.path.join(here, "beat_startprocess.txt")
    beat_wmi = os.path.join(here, "beat_wmi.txt")
    sp_out, sp_err = os.path.join(here, "arm_sp.out.txt"), os.path.join(here, "arm_sp.err.txt")
    wmi_out, wmi_err = os.path.join(here, "arm_wmi.out.txt"), os.path.join(here, "arm_wmi.err.txt")
    wmi_report = os.path.join(here, "arm_wmi.report.txt")
    child_out, child_err = os.path.join(here, "canary_sp.out.txt"), os.path.join(here, "canary_sp.err.txt")
    ps_script = os.path.join(here, "arm_startprocess.ps1")
    wmi_script = os.path.join(here, "arm_wmi.ps1")

    with io.open(ps_script, "w", encoding="ascii", newline="\r\n") as fh:
        fh.write(
            "# Arms the canary through the SHIPPED launcher, then exits. The whole\n"
            "# point is what happens AFTER this process is gone.\n"
            "$ErrorActionPreference = 'Stop'\n"
            ". '%s'\n"
            "$r = Start-JccDetachedPython -PythonExe '%s' `\n"
            "    -Arguments @('%s', 'echo', '%s') `\n"
            "    -OutLog '%s' -ErrLog '%s' -WorkingDirectory '%s'\n"
            "Write-Output ('ARMED ok=' + $r.ok + ' pid=' + $r.pid)\n"
            % (LIB, PY, me, beat_sp, child_out, child_err, ROOT))

    # --- arm 1: Start-JccDetachedPython ---------------------------------------
    log("arming via Start-JccDetachedPython")
    rc, took = _run_to_files(
        [PS, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", ps_script], sp_out, sp_err, timeout=180)
    arm_exit_sp = time.time()
    text = (_read_text(sp_out) + " " + _read_text(sp_err)).strip()
    log("  arming command returned after %.1fs, rc=%s, out=%r" % (took, rc, text[:200]))
    sp_pid = 0
    for token in text.split():
        if token.startswith("pid=") and token[4:].strip().isdigit():
            sp_pid = int(token[4:].strip())
    arm1 = {"launcher": "Start-JccDetachedPython",
            "how": "Start-Process, both streams redirected to files",
            "where_it_is_used": "scripts/continuation-watchdog.ps1, every tick, as SYSTEM",
            "armed_here_as": _read_text(sp_out).splitlines()[0] if _read_text(sp_out) else "",
            "beat_file": beat_sp, "arm_rc": rc, "arm_seconds": round(took, 2),
            "arm_command_output": text[:400], "armed_pid": sp_pid,
            "arm_command_exited_at": arm_exit_sp,
            "child_stream_file_exists": os.path.isfile(child_out)}

    # --- arm 2: Win32_Process.Create ------------------------------------------
    log("arming via Win32_Process.Create (WMI)")
    wrapper = os.path.join(here, "canary_wmi.cmd")
    with io.open(wrapper, "w", encoding="ascii", newline="") as fh:
        fh.write("@echo off\r\ncd /d %s\r\n\"%s\" \"%s\" echo \"%s\"\r\n"
                 % (ROOT, PY, me, beat_wmi))
    with io.open(wmi_script, "w", encoding="ascii", newline="\r\n") as fh:
        fh.write(_arm2_script(wrapper, wmi_report, ROOT))
    rc2, took2 = _run_to_files(
        [PS, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", wmi_script], wmi_out, wmi_err, timeout=300)
    arm_exit_wmi = time.time()
    text2 = (_read_text(wmi_out) + " " + _read_text(wmi_err)).strip()
    log("  arming command returned after %.1fs, rc=%s" % (took2, rc2))
    report = _parse_report(wmi_report)
    log("  report: %r" % ({k: v for k, v in report.items() if k != "privileges"},))
    arm2 = {"launcher": "Win32_Process.Create (WMI)",
            "how": "cmd.exe wrapper, provider creates the process",
            "where_it_is_used": ("tools/continuation/launch_soak.py and the other "
                                 "operator-run acceptance launchers"),
            "beat_file": beat_wmi, "arm_rc": rc2, "arm_seconds": round(took2, 2),
            "arm_command_output": text2[-400:],
            "provider_report": report,
            "wrapper": os.path.relpath(wrapper, ROOT).replace(os.sep, "/"),
            "arm_command_exited_at": arm_exit_wmi}

    log("waiting %ds so the heartbeats run well past both arming commands" % OBSERVE_S)
    time.sleep(OBSERVE_S)

    arm1.update(classify(_read_beats(beat_sp), arm_exit_sp, LIFE_S))
    arm1["armed_pid_alive_now"] = pid_alive(arm1["armed_pid"])
    arm1["note_on_in_job"] = ("recorded for information only; measured on this host to "
                              "be False for the WMI wrapper and True for the python it "
                              "spawns, so it is NOT the criterion")

    threw = report.get("provider_threw")
    rv = report.get("return_value")
    try:
        rv_int = int(rv) if rv not in (None, "") else None
    except ValueError:
        rv_int = None
        threw = threw or "unparsable return_value=%r" % (rv,)
    arm2.update(classify_wmi(rv_int, _read_beats(beat_wmi), arm_exit_wmi, LIFE_S,
                            threw=threw))
    arm2["armed_pid_alive_now"] = pid_alive(report.get("created_pid"))
    arm2["note_on_in_job"] = ("recorded for information only -- see the module "
                              "docstring on why membership of ANY job is not the "
                              "criterion")
    arm2["provider_paths_disagree"] = (
        str(report.get("second_provider_return_value")) != str(rv)
        if "second_provider_return_value" in report else None)

    # --- arm 3: the scheduler, in production ----------------------------------
    records = _read_jsonl(WATCHDOG_LOG)
    lock = _read_json(LOCK) or {}
    lock_pid = lock.get("pid")
    lock_alive = pid_alive(lock_pid)
    lock_fresh = None
    try:
        lock_fresh = str(lock.get("expires_at") or "") > datetime.datetime.now(
            datetime.timezone.utc).astimezone().isoformat()
    except (TypeError, ValueError):
        pass
    arm3 = classify_production(records, lock_pid, bool(lock_alive and lock_fresh),
                               datetime.datetime.now(datetime.timezone.utc).astimezone())
    arm3["launcher"] = "the task scheduler itself"
    arm3["how"] = "the watchdog's own audit log, read live"
    arm3["where_it_is_used"] = "production; this is the arm the pipeline depends on"
    arm3["lock_expires_at"] = lock.get("expires_at")
    arm3["audit_records_read"] = len(records)

    problems, not_claimed = decide(arm1, arm2, arm3)
    payload = {
        "ok": not problems,
        "status": "PASS" if not problems else "FAIL",
        "verdict": ("PASS: the shipped launcher produced a process that outlived the "
                    "command that started it, and the scheduled task that starts the "
                    "controller did the same in production"
                    if not problems else "FAIL: " + "; ".join(problems)),
        "checked_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "life_s": LIFE_S,
        "observe_s": OBSERVE_S,
        "question": ("does a process this platform starts outlive the command that "
                     "started it? answered by heartbeats and by the production audit "
                     "log, not by pids and not by in_job"),
        "why_this_is_not_in_job": (
            "IsProcessInJob(h, NULL, ...) reports membership of ANY job: the WMI "
            "wrapper reads False while the python it spawns reads True, and that "
            "python still outlives its arming command. Survival is the property the "
            "scheduled task depends on, so survival is what is measured."),
        "claims": {
            "Start-JccDetachedPython": arm1.get("verdict"),
            "the scheduler (production)": arm3.get("verdict"),
            "Win32_Process.Create (WMI)": arm2.get("verdict"),
        },
        "not_claimed": not_claimed,
        "arms": [arm1, arm2, arm3],
        "artifacts_dir": os.path.relpath(here, ROOT).replace(os.sep, "/"),
    }
    os.makedirs(os.path.dirname(ACC), exist_ok=True)
    with io.open(ACC, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False, sort_keys=True)
        fh.write("\n")
    for arm in (arm1, arm2, arm3):
        log("  %-32s %-16s %s" % (arm.get("launcher"), arm.get("verdict"),
                                  arm.get("why") or
                                  "alive_after_arm=%ss beats=%s"
                                  % (arm.get("seconds_alive_after_arm_returned"),
                                     arm.get("beats"))))
    log("verdict=%s -> %s" % (payload["status"], ACC))
    return 0 if payload["ok"] else 1


def selftest() -> int:
    """Prove the survival rule can say something other than SURVIVED, and that
    ``decide`` cannot be talked into a pass by a refused arm."""
    cases = []

    def case(name, ok, detail=""):
        cases.append((name, ok, detail))

    now = 1_000_000.0
    cls = classify([now + i for i in range(25)], now + 3.0, 24)
    case("accepts_a_canary_that_outlived_its_launcher", cls["verdict"] == "SURVIVED", str(cls))

    cls = classify([now + i * 0.1 for i in range(20)], now + 3.0, 24)
    case("rejects_a_canary_that_died_with_the_launcher",
         cls["verdict"] == "DIED WITH THE LAUNCHING COMMAND", str(cls))

    cls = classify([], now, 24)
    case("rejects_a_canary_that_never_beat", cls["verdict"] == "NOT OBSERVED", str(cls))

    # The exact false-PASS this rule was written for: an absolute beat count said
    # "20 beats, plenty" for a canary that lived 4 s of an expected 144.
    cls = classify([now + i for i in range(4)], now + 25.0, 144)
    case("rejects_a_short_life_an_absolute_count_would_pass",
         cls["verdict"] == "DIED WITH THE LAUNCHING COMMAND", str(cls))

    cls = classify([now + i for i in range(14)], now + 1.0, 40)
    case("names_a_partial_life_instead_of_passing_it", cls["verdict"] == "PARTIAL", str(cls))

    # --- the WMI arm's own rule ---------------------------------------------
    w = classify_wmi(2, [], now, 24)
    case("a_refused_provider_call_is_not_exercised_not_survived",
         w["verdict"] == "NOT EXERCISABLE" and w["reason"] == "ACCESS_DENIED", str(w))

    w = classify_wmi(0, [], now, 24)
    case("an_accepted_call_with_no_heartbeat_is_still_a_failure",
         w["verdict"] == "NOT OBSERVED", str(w))

    w = classify_wmi(0, [now + i for i in range(20)], now + 3.0, 24)
    case("an_accepted_call_is_judged_by_the_same_survival_rule",
         w["verdict"] == "SURVIVED", str(w))

    w = classify_wmi(9, [], now, 24)
    case("an_unknown_return_value_is_reported_as_unread_not_excused",
         w["verdict"] == "NOT OBSERVED" and w["return_value"] == 9, str(w))

    w = classify_wmi(None, [], now, 24, threw="RuntimeError: boom")
    case("a_provider_that_throws_is_not_exercised",
         w["verdict"] == "NOT EXERCISABLE" and w["reason"] == "CALL_THREW", str(w))

    # --- decide() must not be movable ---------------------------------------
    survived = {"verdict": "SURVIVED"}
    refused = classify_wmi(2, [], now, 24)
    dead = {"verdict": "NOT OBSERVED", "why": "no heartbeat"}

    problems, nc = decide(survived, refused, survived)
    case("a_refused_arm_does_not_fail_the_run", not problems, str(problems))
    case("and_it_is_named_in_not_claimed",
         len(nc) == 1 and nc[0]["reason"] == "ACCESS_DENIED", str(nc))

    problems, _ = decide(dead, refused, survived)
    case("a_refusal_does_not_excuse_a_dead_shipped_launcher", bool(problems), str(problems))

    problems, _ = decide(survived, refused, dead)
    case("a_refusal_does_not_excuse_a_dead_scheduler", bool(problems), str(problems))

    problems, _ = decide(survived, {"verdict": "PARTIAL"}, survived)
    case("an_accepted_but_short_lived_wmi_child_fails_the_run",
         bool(problems), str(problems))

    prod = classify_production([], 1, True, datetime.datetime.now())
    case("no_started_tick_is_not_observed", prod["verdict"] == "NOT OBSERVED", str(prod))

    started = {"action": "STARTED", "pid": 100, "at": "2026-01-01T00:00:00+08:00"}
    only = classify_production([started], 100, True, datetime.datetime.now())
    case("a_started_tick_with_no_later_tick_is_not_observed",
         only["verdict"] == "NOT OBSERVED", str(only))

    noop = {"action": "NOOP", "pid": 100, "detail": "controller pid 100 already live"}
    prod = classify_production([started, noop], 100, False, datetime.datetime.now())
    case("a_dead_lock_makes_the_scheduler_arm_fail",
         prod["verdict"] == "NOT OBSERVED", str(prod))

    prod = classify_production([started, noop], 100, True, datetime.datetime.now())
    case("a_later_live_tick_and_a_live_lock_is_survival",
         prod["verdict"] == "SURVIVED", str(prod))

    err = {"action": "ERROR", "pid": 0, "detail": "boom"}
    prod = classify_production([started, err], 100, True, datetime.datetime.now())
    case("an_error_tick_is_not_evidence_of_survival",
         prod["verdict"] == "NOT OBSERVED", str(prod))

    print("=" * 78)
    for name, ok, detail in cases:
        print("  [%s] %-58s %s" % ("PASS" if ok else "FAIL", name, detail[:110]))
    bad = [c for c in cases if not c[1]]
    print("=" * 78)
    print("detached_survival selftest: %d/%d" % (len(cases) - len(bad), len(cases)))
    return 0 if not bad else 1


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv == ["--selftest"]:
        return selftest()
    if argv == ["--help"] or argv == ["-h"]:
        print(__doc__)
        return 0
    # The canary mode. The wrappers that launch it are generated above, so this
    # argument shape is internal -- but it is parsed strictly, because an
    # unrecognised argument silently falling through to `run()` would start a
    # nested measurement inside the measurement.
    if len(argv) == 2 and argv[0] == "echo":
        with io.open(argv[1], "a", encoding="utf-8") as fh:
            for _ in range(LIFE_S):
                fh.write("%.3f\n" % time.time())
                fh.flush()
                time.sleep(1)
        return 0
    if argv:
        print("unexpected arguments: %r" % (argv,))
        return 2
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
