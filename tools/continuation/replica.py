#!/usr/bin/env python3
"""A throwaway copy of the repository that the shipped watchdogs can be driven against.

WHY A REPLICA IS THE ONLY HONEST WAY TO TEST A WATCHDOG. Every rule in
``scripts/continuation-watchdog.ps1`` and ``scripts/service-watchdog.ps1`` is
"look at disk state, then maybe start or stop a process". Driving those rules
against the real root means racing the real controller: the test either wins and
destroys a live pipeline, or loses and measures nothing. A test that can only be
run by racing the product is a test nobody runs twice -- and this project has
already had one rule (the launcher's stdout pipe) that looked fine until it was
measured on the real thing.

So the rules are driven against ``<scratch>/replica/<stamp>`` instead. Three
properties make that a real test rather than a simulation of one:

  * ``scripts/`` is copied VERBATIM, and the replica's own copy is what gets
    executed. Nothing about the shipped script is edited; the tick is told where
    the root is through ``-Root``, which is a parameter the product already has
    (and which the product's own tests use for the same reason).
  * the replica gets a working ``.venv\\Scripts\\python.exe`` by copying the venv
    redirector and its ``pyvenv.cfg``, whose ``home`` is absolute. That is the
    trick ``watchdog_proof.py`` established: the replica launches the real base
    interpreter, so no fake interpreter is introduced and "the launcher cannot
    find python" never masks the rule under test.
  * the controller is replaced by a STUB that implements only the documented lock
    handshake. The real controller's next step is a full gate run; running it
    inside a decision-rule test would contend with the thing being measured, and
    the controller's own behaviour is proved by the pipeline, not here. The stub
    is declared as a stub in every artifact that mentions it.
"""
from __future__ import annotations

import io
import os
from pathlib import Path
import shutil
import subprocess
import time

ROOT = str(Path(__file__).resolve().parents[2])
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")

#: The stub controller. It speaks the lock handshake the watchdog checks for --
#: acquire, hold, release -- and it records what it was asked to resume, because
#: "did the tick tell it to continue from the persisted step?" is one of the
#: facts a reboot-recovery claim rests on.
STUB_CONTROLLER = '''#!/usr/bin/env python3
"""Replica-only stub controller. NOT the product's controller.

Implements only the lock handshake in ``tools/jcc_continuation.py``: take the
lock, keep it fresh for a while so a following tick observes a live controller,
then release it. It also records the ``current_step`` it read from the state
file, which is how the caller learns that the tick resumed from disk.
"""
import datetime, json, os, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCK = os.path.join(ROOT, ".workbuddy", "locks", "jcc-continuation.lock.json")
STATE = os.path.join(ROOT, ".workbuddy", "jcc_execution_state.json")
NOTE = os.path.join(ROOT, ".workbuddy", "stub_note.json")
SLEEP_S = float(os.environ.get("JCC_STUB_SLEEP", "12"))
TZ = datetime.timezone(datetime.timedelta(hours=8))


def iso(dt=None):
    return (dt or datetime.datetime.now(TZ)).isoformat()


def main():
    step = None
    try:
        with open(STATE, "r", encoding="utf-8-sig") as fh:
            step = json.load(fh).get("current_step")
    except Exception:
        step = None
    os.makedirs(os.path.dirname(LOCK), exist_ok=True)
    rec = {"owner": "STUB", "pid": os.getpid(), "acquired_at": iso(),
           "expires_at": iso(datetime.datetime.now(TZ)
                             + datetime.timedelta(seconds=int(SLEEP_S) + 600)),
           "resumed_from_current_step": step}
    tmp = LOCK + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, LOCK)
    with open(NOTE, "w", encoding="utf-8") as fh:
        json.dump({"pid": os.getpid(), "resumed_from_current_step": step,
                   "started_at": iso()}, fh, indent=2)
    time.sleep(SLEEP_S)
    try:
        os.remove(LOCK)
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def build_replica(rep: str, *, with_stub_controller: bool = True,
                  stub_sleep_s: int = 12) -> dict:
    """Materialise a replica at ``rep``. Never touches anything under ROOT."""
    os.makedirs(rep, exist_ok=True)
    for sub in (os.path.join(".workbuddy", "locks"),
                os.path.join(".workbuddy", "scheduled", "logs"),
                os.path.join(".workbuddy", "scheduled", "steps"),
                os.path.join("runtime", "jcc"),
                "tools"):
        os.makedirs(os.path.join(rep, sub), exist_ok=True)

    # scripts/ verbatim -- the tick that runs is the replica's own copy.
    shutil.copytree(os.path.join(ROOT, "scripts"), os.path.join(rep, "scripts"),
                    dirs_exist_ok=True)

    # The venv redirector + its absolute-home pyvenv.cfg. No fake interpreter.
    os.makedirs(os.path.join(rep, ".venv", "Scripts"), exist_ok=True)
    for rel in (os.path.join(".venv", "Scripts", "python.exe"),
                os.path.join(".venv", "pyvenv.cfg")):
        shutil.copyfile(os.path.join(ROOT, rel), os.path.join(rep, rel))

    info = {"replica_root": rep, "scripts_copied_verbatim": True,
            "controller_stub": bool(with_stub_controller), "stub_sleep_s": stub_sleep_s}
    if with_stub_controller:
        with io.open(os.path.join(rep, "tools", "jcc_continuation.py"), "w",
                     encoding="utf-8", newline="\n") as fh:
            fh.write(STUB_CONTROLLER)

    probe = subprocess.run(
        [os.path.join(rep, ".venv", "Scripts", "python.exe"), "-c",
         "import sys;print(sys.version_info[0],sys.version_info[1])"],
        capture_output=True, text=True, timeout=120)
    info["replica_interpreter"] = (probe.stdout or probe.stderr or "").strip()[:80]
    info["replica_interpreter_works"] = probe.returncode == 0
    return info


def ps(args, timeout=300):
    """Run Windows PowerShell 5.1. Callers put the streams on files themselves
    when the command under test starts a long-lived child: a pipe's write end is
    inheritable, so the child would hold the reader open."""
    exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                       "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    return subprocess.run([exe, "-NoProfile", "-NonInteractive",
                           "-ExecutionPolicy", "Bypass"] + list(args),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


def ps_to_files(args, out_path, err_path, timeout=300):
    exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                       "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    with io.open(out_path, "wb") as out, io.open(err_path, "wb") as err:
        started = time.time()
        rc = subprocess.run([exe, "-NoProfile", "-NonInteractive",
                             "-ExecutionPolicy", "Bypass"] + list(args),
                            stdout=out, stderr=err, timeout=timeout).returncode
    return rc, time.time() - started


def read_json(path):
    import json
    if not os.path.isfile(path):
        return None
    try:
        with io.open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def read_ticks(path):
    """The watchdog's audit log, as records. Tolerant of a BOM, because a BOM in
    a .jsonl file is exactly how this project once produced a false negative."""
    import json
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
                out.append({"action": "<unparsable>", "raw": line[:200]})
    return out


def alive(pid):
    """Fail-safe liveness: only a definite absence answers False."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    r = subprocess.run(
        [os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                      "System32", "WindowsPowerShell", "v1.0", "powershell.exe"),
         "-NoProfile", "-NonInteractive", "-Command",
         "(Get-Process -Id %d -ErrorAction SilentlyContinue) -ne $null" % pid],
        capture_output=True, text=True, timeout=60)
    text = (r.stdout or "").strip()
    if text:
        return "True" in text
    return True


def dead_pid():
    """A pid guaranteed not to be alive: spawn something and let it exit."""
    p = subprocess.Popen([PY, "-c", "pass"])
    pid = p.pid
    p.wait()
    time.sleep(0.4)
    return pid
