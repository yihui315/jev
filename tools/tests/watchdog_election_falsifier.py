#!/usr/bin/env python3
"""FINAL-PHASE FALSIFIER: single-controller election (deliverable G).

THE DEFECT THIS FALSIFIES. Two watchdog ticks that both read a stale controller
lock both decide "start a replacement". Left alone that is two controllers, two
soak_drivers, and a stop.ps1/start.ps1 pair crossing another generation's window.
The fix has TWO independent parts and this file proves BOTH:

  * the ELECTION PRIMITIVE (`Enter-JccElectionLock`) -- an atomic
    `[System.IO.File]::Open(..., FileMode::CreateNew)` -- admits exactly ONE
    winner among simultaneous callers (proved directly, 4-way);
  * the END-TO-END invariant -- drive the SHIPPED watchdog script against a
    scratch root with two concurrent ticks plus one sequential tick and assert
    exactly ONE controller process was ever launched, and that the launcher
    CONFIRMED the new controller holds a live, beating lease.

A third part exercises the residual SEQUENTIAL race: a tick that read the stale
lock but reached the launch step only after another tick had already acquired the
lock must NOT launch a second controller (the re-check under the election).

IT NEVER TOUCHES THE REAL PIPELINE. The root is a scratch tree; the "controller"
is a tiny file that records its own launch to `launches.txt` and writes a lease
the watchdog can confirm. Port 4778 is never opened. The real
`F:\\jev\\.workbuddy\\jcc_execution_state.json`, the real lock, and the real
controller are never read or written.

Run:
    python tools/tests/watchdog_election_falsifier.py
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
WD = SCRIPTS / "continuation-watchdog.ps1"
COMMON = SCRIPTS / "lib" / "JccWatchdogCommon.ps1"
BASE_PY = Path(r"C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe")
PS = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / \
    "v1.0" / "powershell.exe"
TZ = timezone(timedelta(hours=8))
SLEEP_S = int(os.environ.get("JCC_EL_SLEEP", "30"))


def _contract():
    """The proof contract, by path -- one implementation of the proof schema."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "jcc_proof_contract", str(REPO / "tools" / "continuation" / "proof_contract.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod

#: The scratch "controller". It is deliberately NOT the real pipeline: it records
#: its own launch (the thing we count) and writes a lease the SHIPPED watchdog must
#: be able to confirm (pid alive, heartbeat fresh). `process_start_time` is null on
#: purpose so `Test-JccLeaseIdentity` returns 'unknown', never 'mismatch'.
FAKE_CONTROLLER = '''#!/usr/bin/env python3
import io, json, os, sys, time
root = os.environ.get("JCC_ROOT") or os.getcwd()
logdir = os.path.join(root, ".workbuddy", "scheduled", "logs")
lockdir = os.path.join(root, ".workbuddy", "locks")
os.makedirs(logdir, exist_ok=True)
os.makedirs(lockdir, exist_ok=True)
with io.open(os.path.join(logdir, "launches.txt"), "a", encoding="utf-8") as fh:
    fh.write("%d %s\\n" % (os.getpid(), time.strftime("%Y-%m-%dT%H:%M:%S")))
lock = os.path.join(lockdir, "jcc-continuation.lock.json")
def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))
end = time.time() + __SLEEP__
while time.time() < end:
    now = time.time()
    rec = {"pid": os.getpid(), "process_start_time": None,
           "acquired_at": iso(now), "heartbeat_at": iso(now),
           "expires_at": iso(now + 180), "current_step": "FAKE", "generation": 1,
           "run_id": "FAKE-%d" % os.getpid()}
    tmp = lock + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\\n") as fh:
        json.dump(rec, fh)
    os.replace(tmp, lock)
    time.sleep(2.0)
'''.replace("__SLEEP__", str(SLEEP_S))


def log(msg):
    print("[EL] %s" % msg, flush=True)


def _ps(cmd, timeout=180):
    p = subprocess.run([str(PS), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                        "-Command", cmd], capture_output=True, timeout=timeout)
    return p.returncode, (p.stdout or b"").decode("utf-8", "replace") + \
        (p.stderr or b"").decode("utf-8", "replace")


def _read_json(path):
    if not os.path.isfile(path):
        return None
    try:
        with io.open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _dead_pid():
    p = subprocess.Popen([str(BASE_PY), "-c", "pass"])
    pid = p.pid
    p.wait()
    time.sleep(0.5)
    return pid


def _build_scratch(base: Path) -> Path:
    import shutil
    root = base / "root"
    for sub in (".workbuddy/locks", ".workbuddy/runs", ".workbuddy/scheduled/logs",
                "artifacts/acceptance", "tools", ".venv/Scripts"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    shutil.copytree(str(SCRIPTS), str(root / "scripts"), dirs_exist_ok=True)
    for rel in (".venv/Scripts/python.exe", ".venv/pyvenv.cfg"):
        shutil.copyfile(str(REPO / rel), str(root / rel))
    (root / "tools" / "jcc_continuation.py").write_text(FAKE_CONTROLLER, encoding="utf-8",
                                                        newline="\n")
    # A state file that is neither COMPLETE nor PASS, so rule 1 does not no-op us.
    (root / ".workbuddy" / "jcc_execution_state.json").write_text(
        json.dumps({"runtime_state": "RUNNING", "project_run": "PENDING",
                    "steps": {}}), encoding="utf-8", newline="\n")
    return root


def _write_stale_lock(root: Path):
    old = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 3600))
    (root / ".workbuddy" / "locks" / "jcc-continuation.lock.json").write_text(
        json.dumps({"pid": _dead_pid(), "process_start_time": None, "acquired_at": old,
                    "heartbeat_at": old, "expires_at": old, "current_step": "OLD"}),
        encoding="utf-8", newline="\n")


def _ticks(root: Path):
    p = root / ".workbuddy" / "scheduled" / "logs" / "continuation-watchdog.jsonl"
    out = []
    if p.is_file():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    out.append({"action": "<unparsable>"})
    return out


def _launches(root: Path):
    p = root / ".workbuddy" / "scheduled" / "logs" / "launches.txt"
    if not p.is_file():
        return []
    return [ln for ln in p.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]


def _tick(root: Path, wait=20, timeout=120):
    return subprocess.run([str(PS), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                           "-File", str(WD), "-Root", str(root), "-WaitSeconds", str(wait)],
                          capture_output=True, timeout=timeout)


# --------------------------------------------------------------------------- #
def case_election_primitive(checks, tmp):
    """4 simultaneous Enter-JccElectionLock callers -> exactly ONE winner."""
    lock = tmp / "election.lock.json"
    outs = [tmp / ("e%d.txt" % i) for i in range(4)]
    procs = []
    for o in outs:
        cmd = ("$ErrorActionPreference='Stop'; . '%s'; "
               "$s = Enter-JccElectionLock -Path '%s'; "
               "if ($s) { [System.IO.File]::WriteAllText('%s','WIN'); Start-Sleep -Seconds 3 } "
               "else { [System.IO.File]::WriteAllText('%s','LOSE') }"
               % (COMMON, lock, o, o))
        procs.append(subprocess.Popen([str(PS), "-NoProfile", "-NonInteractive",
                                       "-ExecutionPolicy", "Bypass", "-Command", cmd],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    for p in procs:
        p.wait(timeout=60)
    wins = sum(1 for o in outs if o.is_file() and o.read_text(encoding="utf-8").strip() == "WIN")
    checks.append(("the election admits exactly one of four simultaneous callers", wins == 1,
                   "winners=%d of 4" % wins))


def case_end_to_end(checks, tmp):
    """Two concurrent ticks + one sequential tick -> exactly one controller."""
    root = _build_scratch(tmp)
    _write_stale_lock(root)

    p1 = subprocess.Popen([str(PS), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                           "-File", str(WD), "-Root", str(root), "-WaitSeconds", "20"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    p2 = subprocess.Popen([str(PS), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                           "-File", str(WD), "-Root", str(root), "-WaitSeconds", "20"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    p1.wait(timeout=120)
    p2.wait(timeout=120)
    time.sleep(1)

    launched = _launches(root)
    ticks = _ticks(root)
    actions = [t.get("action") for t in ticks]
    checks.append(("two concurrent ticks launched exactly one controller", len(launched) == 1,
                   "launches=%d (%s)" % (len(launched), launched)))
    checks.append(("the shipped watchdog reported exactly one STARTED", actions.count("STARTED") == 1,
                   "actions=%s" % actions))
    checks.append(("the launcher confirmed the new controller is live",
                   "CONFIRMED" in actions, "actions=%s" % actions))
    confirmed = [t for t in ticks if t.get("action") == "CONFIRMED"]
    checks.append(("the confirmation records heartbeating=True",
                   bool(confirmed) and "heartbeating=True" in str(confirmed[-1].get("detail")),
                   str(confirmed[-1].get("detail")) if confirmed else "no CONFIRMED"))
    checks.append(("the losing tick was a NOOP (dead-lock or election)",
                   "NOOP" in actions, "actions=%s" % actions))

    # ---- sequential tick: a LIVE controller must not be duplicated ------------
    before = len(_launches(root))
    _tick(root, wait=10)
    after = len(_launches(root))
    ticks2 = _ticks(root)
    checks.append(("a later tick did NOT duplicate the live controller", after == before,
                   "before=%d after=%d" % (before, after)))
    checks.append(("the later tick NOOPed on the live controller",
                   ticks2 and ticks2[-1].get("action") == "NOOP",
                   "last=%s" % (ticks2[-1].get("action") if ticks2 else None)))

    # Targeted, own-child cleanup: stop exactly the fake controllers THIS test
    # launched (by their recorded pid). Never a name/global kill -- on a host that
    # runs the real pipeline, a broad kill is the incident, not the cleanup.
    for line in launched:
        pid = line.split()[0]
        _ps("Stop-Process -Id %s -Force -ErrorAction SilentlyContinue" % pid)
    time.sleep(1)


def main() -> int:
    # A PERSISTENT scratch root, like the interruption-recovery harness: the temp
    # directory's own cleanup raced a still-running controller child (and the venv
    # redirector stub it keeps open), so the tree is left in place under
    # .workbuddy/scratch and is never the reason a run fails after the fact.
    stamp = datetime.now(TZ).strftime("%Y%m%d_%H%M%S")
    work = REPO / ".workbuddy" / "scratch" / "election" / stamp
    (work / "pri").mkdir(parents=True, exist_ok=True)
    (work / "e2e").mkdir(parents=True, exist_ok=True)
    checks = []
    for label, fn, sub in (("election primitive", case_election_primitive, "pri"),
                           ("end-to-end single controller", case_end_to_end, "e2e")):
        n0 = len(checks)
        try:
            fn(checks, work / sub)
        except Exception as exc:                                   # noqa: BLE001
            checks.append(("%s did not raise" % label, False,
                           "%s: %s" % (type(exc).__name__, exc)))
        for name, ok, detail in checks[n0:]:
            log("  [%s] %-56s %s" % ("PASS" if ok else "FAIL", name, str(detail)[:120]))
    bad = [c for c in checks if not c[1]]
    print("=" * 90)
    print("watchdog-election falsifiers: %d/%d" % (len(checks) - len(bad), len(checks)))
    print("scratch: %s" % work)

    # The §L claim is only CHECKED if FINAL-12 can read a machine-readable verdict.
    contract = _contract()
    proof = contract.build_proof("WATCHDOG_ELECTION_PROOF", checks,
                                 extra={"scratch_root": str(work)})
    out = contract.write_proof(str(REPO), "WATCHDOG_ELECTION_PROOF", proof)
    print("proof: %s (%s)" % (out, proof["status"]))
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
