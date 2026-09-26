#!/usr/bin/env python3
"""PHASE T -- watchdog recovery proof.

WHAT THIS PROVES, PRECISELY. tools/continuation-watchdog.ps1 is a DECISION
PROCEDURE with three rules; what is being proved here is that the procedure
recovers a dead controller and never resurrects a finished pipeline. The
controller itself is replaced by a stub that implements only the documented
lock handshake, because the real controller's next step is a full E-07 plus the
final gate -- running it here would contend with the controlled run under
measurement. So this is a proof of the RECOVERY MECHANISM, not of the
controller's pipeline; the pipeline is proved separately by the gate.

The replica root exists so the real repository's controller state and locks are
never touched. The only edit to the shipped script is $ProjectRoot, and the
script reports how many lines that edit touched.

WHAT THIS DOES NOT PROVE. That jcc_core itself is supervised. It is not: the
shipped watchdog watches the continuation controller, and no component in this
repository restarts the service if it dies. That gap is reported, not papered over.
"""
import io, json, os, re, shutil, subprocess, sys, time, datetime

ROOT = r"F:\jev"
# Line-buffer stdout. When a parent captures this script through a pipe, an
# abrupt death loses whatever was still sitting in the block buffer, which is
# exactly how the first in-driver run lost the tail of its own output.
#
# errors="backslashreplace" is not cosmetic. This host's stdout encoding is GBK
# (cp936), and every string this script relays from a child process -- taskkill,
# PowerShell -- arrives from an OEM-codepage writer, so decoding it produces
# U+FFFD. Printing U+FFFD to a GBK stream raises UnicodeEncodeError. That is
# exactly how a run whose five decision-rule cases had ALL passed exited 1, and
# it died at the cleanup print -- twenty-nine lines BEFORE the evidence write,
# so WATCHDOG_RECOVERY_PROOF.json was never produced. An instrument that can
# lose its own verdict to a diagnostic print is not trustworthy, so the failure
# mode is closed here for the whole script, not just at the one line that fired.
try:
    if sys.stdout.isatty():
        # On a console, keep the console's own encoding; only make it
        # unbreakable by undecodable input.
        sys.stdout.reconfigure(line_buffering=True, errors="backslashreplace")
        sys.stderr.reconfigure(errors="backslashreplace")
    else:
        # Redirected to a file or a pipe (both the WMI launch and the soak
        # driver's capture do this). The readers of those bytes decode UTF-8, so
        # writing cp936 here would turn correctly-decoded Chinese back into
        # mojibake in the stored evidence. Pin the wire format instead.
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True,
                               errors="backslashreplace")
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
except Exception:                                           # noqa: BLE001
    pass
# A FRESH root every run, never a path that has existed before. Deleting and
# re-creating one would have to go through the host's safe-delete guard, and
# working around that guard is not an option -- so nothing is ever deleted here.
STAMP = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
REP = os.path.join(ROOT, ".workbuddy", "scratch", "watchdog_proof", "root_" + STAMP)
SCRATCH = os.path.join(ROOT, ".workbuddy", "scratch", "watchdog_proof")
os.makedirs(SCRATCH, exist_ok=True)
PS = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                  "System32", "WindowsPowerShell", "v1.0", "powershell.exe")

STUB_SLEEP = 45          # long enough for the next tick to observe a LIVE controller

# Every raw capture this run writes, so the artifact can name its own inputs and
# a reader can re-derive any printed line from the bytes on disk.
RUN_FILES = []

STUB = r'''"""Stub controller: implements ONLY the documented lock handshake."""
import json, os, time, datetime, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCK = os.path.join(ROOT, ".workbuddy", "locks", "jcc-continuation.lock.json")
os.makedirs(os.path.dirname(LOCK), exist_ok=True)
try:
    cur = json.load(open(LOCK, encoding="utf-8"))
    pid = int(cur.get("pid", 0))
    if pid > 0:
        try:
            os.kill(pid, 0)
            print("stub controller: lock held by live pid %d; exiting" % pid)
            sys.exit(0)
        except OSError:
            pass
except (OSError, ValueError):
    pass
rec = {"pid": os.getpid(),
       "expires_at": (datetime.datetime.now() + datetime.timedelta(minutes=10)).isoformat(),
       "task_id": "watchdog-proof-stub"}
tmp = LOCK + ".tmp.%d" % os.getpid()
open(tmp, "w", encoding="utf-8").write(json.dumps(rec))
os.replace(tmp, LOCK)
print("stub controller: acquired lock pid=%d" % os.getpid())
time.sleep(int(os.environ.get("STUB_SLEEP", "6")))
print("stub controller: exiting")
'''


def ps_run(script, env=None, drop=(), timeout=120, tag=None):
    """Invoke the watchdog with its stdout going to FILES, never a pipe.

    Measured A/B (probe_watchdog_pipe.py, evidence WATCHDOG_PIPE_PROBE.json): with
    the watchdog's own stdout on a pipe the call does not return until the
    controller it started exits -- 21.3 s against a controller that lived 20 s --
    while the identical call with file redirection returns in 3.1 s and leaves the
    controller running. That is the trap the watchdog's own header documents for
    the CHILD's stdout, reappearing one level up on the watchdog's OWN stdout, and
    it silently turned case B into "observe an already-dead controller".
    """
    e = dict(os.environ)
    for k in list(e):
        if k.lower() in tuple(d.lower() for d in drop):
            e.pop(k, None)
    e["STUB_SLEEP"] = str(STUB_SLEEP)
    if env:
        e.update(env)
    # One file pair PER CALL, stamped per run. With a single shared tag each case
    # overwrote the previous case's raw bytes, so the artifact could only ever
    # keep the last capture -- the artifact named inputs that no longer existed.
    base = "%s_%s" % (tag or "wd", STAMP)
    o = os.path.join(SCRATCH, base + ".out.txt")
    r = os.path.join(SCRATCH, base + ".err.txt")
    RUN_FILES.append((os.path.basename(o), os.path.basename(r)))
    with io.open(o, "wb") as fo, io.open(r, "wb") as fe:
        p = subprocess.run([PS, "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                            "Bypass", "-File", script],
                           stdout=fo, stderr=fe, env=e, timeout=timeout)
    return (p.returncode,
            decode_out(open(o, "rb").read()).strip(),
            decode_out(open(r, "rb").read()).strip())


def read_lock():
    p = os.path.join(REP, ".workbuddy", "locks", "jcc-continuation.lock.json")
    if not os.path.isfile(p):
        return None
    try:
        return json.load(io.open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return None


def pid_alive(pid):
    """In-process liveness check. Spawning a PowerShell per check costs ~2.5 s on
    this host, which is longer than the stub used to live -- that latency is what
    made case B observe a dead controller and fail for the wrong reason."""
    if not pid:
        return False
    import ctypes
    import ctypes.wintypes as wt
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, int(pid))        # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    try:
        code = wt.DWORD()
        if not k.GetExitCodeProcess(wt.HANDLE(h), ctypes.byref(code)):
            return False
        return code.value == 259                      # STILL_ACTIVE
    finally:
        k.CloseHandle(wt.HANDLE(h))


def _in_job():
    """True iff this process itself sits in a job object. The whole point of the
    launch path used for T1 is to be OUTSIDE the host's kill-on-close job, so the
    artifact records which context produced it instead of assuming one.

    A real handle to our own pid is opened rather than using the
    GetCurrentProcess pseudo-handle: ctypes defaults an undeclared return to
    C int, and the pseudo-handle is (HANDLE)-1, so passing it truncates and
    IsProcessInJob rejects the argument. That is why the first version of this
    field recorded null instead of a fact.
    """
    import ctypes
    import ctypes.wintypes as wt
    try:
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, os.getpid())   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return None
        try:
            k.IsProcessInJob.argtypes = [wt.HANDLE, wt.HANDLE, ctypes.POINTER(wt.BOOL)]
            k.IsProcessInJob.restype = wt.BOOL
            r = wt.BOOL(False)
            ok = k.IsProcessInJob(wt.HANDLE(h), None, ctypes.byref(r))
            return bool(r.value) if ok else None
        finally:
            k.CloseHandle(wt.HANDLE(h))
    except Exception:                                       # noqa: BLE001
        return None


def cmdline_of(pid):
    import ctypes
    import ctypes.wintypes as wt
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, int(pid))
    if not h:
        return ""
    try:
        n = wt.DWORD(0)
        k.QueryFullProcessImageNameW.restype = wt.BOOL
        buf = ctypes.create_unicode_buffer(2048)
        n.value = 2048
        k.QueryFullProcessImageNameW(wt.HANDLE(h), 0, buf, ctypes.byref(n))
        return buf.value
    finally:
        k.CloseHandle(wt.HANDLE(h))


def decode_out(b):
    """Decode a Windows tool's captured bytes without inventing U+FFFD.

    taskkill and PowerShell write in the console codepage (cp936 on this host),
    NOT utf-8. Decoding those bytes as utf-8 yields replacement characters, and
    a replacement character printed to a GBK stdout raises UnicodeEncodeError --
    the crash that discarded a fully passing run's evidence. Try the codepages
    that can actually be right, then fall back to a rendering that cannot raise.
    """
    if not b:
        return ""
    for enc in ("utf-8", "cp936", "mbcs", "latin-1"):
        try:
            return b.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return b.decode("ascii", "backslashreplace")


def kill_stub(pid):
    """Kill ONLY a process we started, after re-verifying its identity. The
    project rule is that nothing is signalled without proving identity first.

    Never allowed to fail the proof: cleanup is bookkeeping, not evidence, and a
    cleanup error must not be able to turn a 4/4 result into a non-zero exit.
    The absolute taskkill path is used because this script also runs from a
    WMI-launched parent, whose environment is not the interactive one.
    """
    if not pid or not pid_alive(pid):
        return "not running"
    try:
        img = cmdline_of(pid).lower()
        if "python" not in img:
            return "REFUSED: pid %d is %r, not a python process" % (pid, img)
        tk = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                          "System32", "taskkill.exe")
        p = subprocess.run([tk, "/T", "/F", "/PID", str(int(pid))],
                           capture_output=True, timeout=60)
        o = decode_out((p.stdout or b"") + (p.stderr or b"")).strip()
        return o[:200]
    except Exception as exc:                                # noqa: BLE001
        return "cleanup error for pid %s: %s: %s" % (pid, type(exc).__name__, exc)


def dead_pid():
    """A pid that is guaranteed dead: spawn a process and let it exit."""
    py = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
    p = subprocess.Popen([py, "-c", "pass"])
    pid = p.pid
    p.wait()
    time.sleep(0.4)
    return pid


# --- build the replica ---------------------------------------------------------
os.makedirs(os.path.join(REP, ".workbuddy", "locks"), exist_ok=True)
os.makedirs(os.path.join(REP, ".workbuddy", "scheduled", "logs"), exist_ok=True)
os.makedirs(os.path.join(REP, "tools"), exist_ok=True)
# The watchdog resolves its interpreter as <root>\.venv\Scripts\python.exe, so the
# replica needs one or every case would fail with "no python interpreter found".
# .venv\Scripts\python.exe is the Windows venv redirector and .venv\pyvenv.cfg
# carries an ABSOLUTE home, so copying both gives the replica a working launcher
# that points at the real base interpreter -- no fake interpreter is introduced.
os.makedirs(os.path.join(REP, ".venv", "Scripts"), exist_ok=True)
for rel in (os.path.join(".venv", "Scripts", "python.exe"),
            os.path.join(".venv", "pyvenv.cfg")):
    shutil.copyfile(os.path.join(ROOT, rel), os.path.join(REP, rel))
_probe = subprocess.run([os.path.join(REP, ".venv", "Scripts", "python.exe"),
                         "-c", "import sys;print(sys.executable)"],
                        capture_output=True, timeout=60)
print("replica interpreter: %s" % (decode_out(_probe.stdout).strip()
                                  or decode_out(_probe.stderr).strip()))

src = io.open(os.path.join(ROOT, "tools", "continuation-watchdog.ps1"),
              encoding="utf-8").read()
edited = src.replace("$ProjectRoot  = 'F:\\jev'", "$ProjectRoot  = '%s'" % REP)
lines_edited = sum(1 for a, b in zip(src.splitlines(), edited.splitlines()) if a != b)
assert lines_edited == 1, "the replica edit must touch exactly one line, touched %d" % lines_edited
io.open(os.path.join(REP, "tools", "continuation-watchdog.ps1"), "w",
        encoding="utf-8", newline="\r\n").write(edited)
io.open(os.path.join(REP, "tools", "jcc_continuation.py"), "w",
        encoding="utf-8", newline="\n").write(STUB)
WD = os.path.join(REP, "tools", "continuation-watchdog.ps1")
STATE = os.path.join(REP, ".workbuddy", "jcc_execution_state.json")

results = []
STARTED_PIDS = []


def first_line(s, n=130):
    """First line of a possibly-empty capture, never an IndexError. A missing
    child output must degrade to a reported fact, not to a traceback: this
    script's whole purpose is to still be alive at its verdict line."""
    line = (s or "").splitlines()
    return (line[0] if line else "(no output)")[:n]


def record(name, expect, rc, out, err, extra=None):
    hit = expect in out
    results.append({"case": name, "expect": expect, "exit_code": rc,
                    "matched": hit, "stdout": out[:600], "stderr": err[:300],
                    "extra": extra or {}})
    print("  %-46s %s  rc=%s" % (name, "OK" if hit else "*** UNEXPECTED ***", rc))
    print("      %s" % (out.splitlines()[0][:150] if out else "(no stdout)"))


print("replica root : %s" % REP)
print("script edit  : 1 line ($ProjectRoot)  [verified: %d]" % lines_edited)
print()

# --- Case A: Rule 3, no controller at all --------------------------------------
print("A. no controller, no state -> must start one (Rule 3)")
rc, out, err = ps_run(WD, tag="A_rule3")
record("A Rule 3 starts a replacement", "started controller", rc, out, err)
time.sleep(0.5)
lk = read_lock()
a_pid = int((lk or {}).get("pid", 0))
STARTED_PIDS.append(a_pid)
print("      lock now names pid=%s alive=%s" % (a_pid, pid_alive(a_pid)))
results[-1]["extra"] = {"lock_pid": a_pid, "lock_pid_alive": pid_alive(a_pid)}

# --- Case B: Rule 2, live controller already running ---------------------------
print()
print("B. live controller -> must do NOTHING (Rule 2, idempotent tick)")
rc, out, err = ps_run(WD, tag="B_rule2")
record("B Rule 2 no-ops on a live controller", "already running", rc, out, err)
lk2 = read_lock()
same = int((lk2 or {}).get("pid", 0)) == a_pid
print("      lock pid unchanged=%s (%s)" % (same, (lk2 or {}).get("pid")))
results[-1]["extra"] = {"lock_pid": (lk2 or {}).get("pid"), "unchanged": same}

# wait for the stub to exit, then leave a stale lock
time.sleep(7)

# --- Case D: stale lock naming a dead pid -> replace (the recovery proof) ------
print()
print("D. stale lock for a DEAD pid -> must start a replacement (recovery)")
d = dead_pid()
io.open(os.path.join(REP, ".workbuddy", "locks", "jcc-continuation.lock.json"), "w",
        encoding="utf-8").write(json.dumps(
            {"pid": d, "expires_at": (datetime.datetime.now()
                                      + datetime.timedelta(minutes=10)).isoformat()}))
print("      planted stale lock naming dead pid %d (alive=%s)" % (d, pid_alive(d)))
rc, out, err = ps_run(WD, tag="D_recovery")
record("D recovery replaces a dead controller", "will start a replacement", rc, out, err)
time.sleep(0.5)
lk3 = read_lock()
d_pid = int((lk3 or {}).get("pid", 0))
STARTED_PIDS.append(d_pid)
replaced = (d_pid != d) and pid_alive(d_pid)
print("      lock now names pid=%s (was %s) alive=%s replaced=%s"
      % (d_pid, d, pid_alive(d_pid), replaced))
results[-1]["extra"] = {"dead_pid": d, "new_pid": d_pid, "replaced": replaced,
                        "started_line": [l for l in out.splitlines() if "started controller" in l]}

# --- Case C: Rule 1, project complete -> permanent no-op -----------------------
print()
print("C. project_complete=true -> must be a PERMANENT no-op (Rule 1)")
io.open(STATE, "w", encoding="utf-8").write(json.dumps(
    {"current_step": "S_COMPLETE", "project_complete": True,
     "steps_completed": [], "head_sha": "", "scripts_freeze_sha": ""}))
try:
    os.remove(os.path.join(REP, ".workbuddy", "locks", "jcc-continuation.lock.json"))
except OSError:
    pass
rc, out, err = ps_run(WD, tag="C_rule1")
record("C Rule 1 never resurrects a finished pipeline", "already complete", rc, out, err)
no_lock = read_lock() is None
print("      no new controller started (lock absent)=%s" % no_lock)
results[-1]["extra"] = {"lock_absent": no_lock}

# --- host conditioning: the proxy pair ---------------------------------------
print()
print("E. host conditioning: Start-Process + proxy env (measured, not assumed)")
both = {k: v for k, v in os.environ.items() if k.lower() in
        ("http_proxy", "https_proxy", "all_proxy")}
print("      proxy vars present: %s" % sorted(both))
io.open(STATE, "w", encoding="utf-8").write(json.dumps(
    {"current_step": "S_INIT", "project_complete": False}))
try:
    os.remove(os.path.join(REP, ".workbuddy", "locks", "jcc-continuation.lock.json"))
except OSError:
    pass
rc, out, err = ps_run(WD, tag="E_as_is")
STARTED_PIDS.append(int((read_lock() or {}).get("pid", 0)))
print("      with env as-is          : rc=%s  %s" % (rc, first_line(out or err)))
as_is = {"exit_code": rc, "stdout": out[:400], "stderr": err[:400]}
PROXY = ("http_proxy", "https_proxy", "all_proxy", "no_proxy")
try:
    os.remove(os.path.join(REP, ".workbuddy", "locks", "jcc-continuation.lock.json"))
except OSError:
    pass
rc2, out2, err2 = ps_run(WD, drop=PROXY, tag="E_proxy_cleared")
STARTED_PIDS.append(int((read_lock() or {}).get("pid", 0)))
print("      with proxy vars cleared : rc=%s  %s" % (rc2, first_line(out2 or err2)))
cleaned = {"exit_code": rc2, "stdout": out2[:400], "stderr": err2[:400]}
results.append({"case": "E host conditioning: proxy env vs Start-Process",
                "expect": "-", "exit_code": rc, "matched": None,
                "stdout": out[:400], "stderr": err[:400],
                "extra": {"with_env_as_is": as_is, "with_proxy_cleared": cleaned}})

# --- cleanup: the stubs we started are ours to end, after proving identity ----
# Structured so that cleanup can never destroy the result it is bookkeeping for.
# The first in-driver run proved the necessity: an exception here aborted the
# script before the evidence write, so a 4/4 pass left no artifact on disk.
try:
    CLEANUP = [kill_stub(p) for p in STARTED_PIDS]
    for p in STARTED_PIDS:
        if pid_alive(p):
            CLEANUP.append("pid %d STILL ALIVE after taskkill" % p)
except Exception as exc:                                    # noqa: BLE001
    CLEANUP = ["cleanup aborted: %s: %s" % (type(exc).__name__, exc)]
print()
try:
    print("cleanup: %s" % "; ".join(CLEANUP))
except Exception as exc:                                    # noqa: BLE001
    print("cleanup: (unprintable: %s)" % type(exc).__name__)

# --- verdict ------------------------------------------------------------------
print()
ok = [r for r in results if r["matched"] is True]
bad = [r for r in results if r["matched"] is False]
print("WATCHDOG RECOVERY PROOF: %d/%d decision-rule cases as expected" % (len(ok), len(ok) + len(bad)))
for r in bad:
    print("  FAILED CASE: %s" % r["case"])
print("WATCHDOG_RECOVERY =", "PASS" if not bad else "FAIL")

doc = {
    "phase": "T -- watchdog recovery",
    "at": datetime.datetime.now().isoformat(timespec="seconds"),
    "run_stamp": STAMP,
    "context": {
        "in_job": None if _in_job() is None else bool(_in_job()),
        "note": ("in_job is reported as a READING of IsProcessInJob for this process, and "
                 "it answers 'is this process in ANY job' -- not 'is it outside the agent "
                 "host's job'. Measured on this very launch chain: the WMI-created cmd.exe "
                 "reads False while the python it spawns reads True, and that python still "
                 "outlives its launching command. It is therefore NOT used here as evidence "
                 "of escaping anything. The load-bearing evidence for the launch path is "
                 "behavioural survival, measured in "
                 "artifacts/acceptance/CANARY_SURVIVAL.json."),
    },
    "raw_captures": sorted(set(RUN_FILES)),
    "proves": ("the shipped watchdog's decision procedure replaces a dead controller and "
               "never resurrects a finished pipeline"),
    "does_not_prove": ("that jcc_core is supervised -- the shipped watchdog watches the "
                       "continuation controller, and nothing in this repository restarts "
                       "the service if it dies"),
    "replica_root": REP,
    "script_edit": {"file": "tools/continuation-watchdog.ps1",
                    "lines_changed": lines_edited,
                    "what": "$ProjectRoot reassigned to the replica root",
                    "controller": "replaced by a stub implementing only the lock handshake"},
    "cases": results,
    "stub_lifetime_s": STUB_SLEEP,
    "cleanup_of_our_own_stubs": CLEANUP,
    "verdict": "PASS" if not bad else "FAIL",
}
out_path = os.path.join(ROOT, "artifacts", "acceptance", "WATCHDOG_RECOVERY_PROOF.json")
io.open(out_path, "w", encoding="utf-8", newline="\n").write(
    json.dumps(doc, indent=2, ensure_ascii=False))
print("evidence -> %s" % out_path)
sys.exit(1 if bad else 0)
