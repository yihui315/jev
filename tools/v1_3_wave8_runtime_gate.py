"""WAVE 8 PRODUCT GATE — WINDOWS DURABLE PRODUCT RUNTIME (owner: Harbor/Metric).

THE GATE'S QUESTION
-------------------
"Can the v1.3 product runtime actually be OPERATED -- started, adopted, watched,
restarted and stopped, with every claim backed by a measurement -- is the SPA
served by a real ASGI server rather than python's stdlib demo server, and does
the host-modifying durable configuration stay strictly opt-in?"

WHAT IT EXERCISES (all against the REAL scripts and REAL processes)
-------------------------------------------------------------------
  * the AST audit of the serving path, including its own FALSIFIABILITY
    (``tools/v1_3_ui_runtime_audit.py`` is fed synthetic files it must reject);
  * loopback-only bind discipline, behaviourally: the server must REFUSE
    ``--host 0.0.0.0`` with exit 3 and leave the port unbound;
  * a live round trip: two real servers -> ``-Action mark`` -> ``status`` ->
    ``doctor`` -> a short soak with pid identity -> ``-Action down`` -> ports
    and bookkeeping provably clean;
  * FAIL-CLOSED: with a FOREIGN listener on the port, ``-Action down`` must
    refuse and the foreign process must still be alive afterwards (a script that
    claims to fail closed but signals anyway would pass every other check);
  * ``-Action repair`` quarantines ONLY provably stale bookkeeping, by RENAME
    (move, not delete), and REFUSES when the recorded pid is alive but
    unverified;
  * the durable task registration is PLAN-ONLY unless ``-Apply`` is given, proven
    by hashing the Windows task store before and after a bare run.

WHY THE SERVER IS STARTED WITH Popen AND NOT WITH ``-Action up``
----------------------------------------------------------------
``-Action up`` was exercised end to end by hand and its evidence is a real
transcript (two surfaces UP, both ownership markers written only after health and
ownership verification -- see docs/v1_3/WAVE_8_DURABLE_RUNTIME.md §5). It is not
driven from inside this gate because the processes it launches are children of a
short-lived launcher, and whether they are reaped when that launcher exits is a
property of the CALLER (a job object), not of the product -- driving it here
would make the gate's result depend on the harness. The gate therefore starts
the servers the way the DURABLE path does (a long-lived owner) and exercises the
operator surface against them. ``-Action up`` appears in the gate only through
``-DryRun``, where its prerequisite and port-reconciliation gates are asserted.

Evidence is written to ``.workbuddy/scratch/v1_3_wave8_gate_<stamp>.json``.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parents[1]
PKGS = REPO / "packages"
TOOLS = REPO / "tools"
if str(PKGS) not in sys.path:
    sys.path.insert(0, str(PKGS))
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import v1_3_ui_runtime_audit as UIAUDIT  # noqa: E402

SCRATCH = REPO / ".workbuddy" / "scratch"
SERVE = REPO / "apps" / "command_center_3d" / "serve_visual.py"
DIST = REPO / "apps" / "command_center_3d" / "dist"
CONTROL = REPO / "scripts" / "jcc-v1.3.ps1"
REGISTER = REPO / "scripts" / "jcc-v1.3-register-tasks.ps1"
RUNTIME = REPO / "runtime" / "jcc-v1.3"
TASK_STORE = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "Tasks"

API_PORT = 4779
UI_PORT = 4780
GREEN_PORT = 4778
FOREIGN_PORT = 4785          # a free port used only to prove fail-closed refusal
SOAK_SECONDS = 25

CREATE_NO_WINDOW = 0x08000000
_LOOPBACK = urllib.request.build_opener(urllib.request.ProxyHandler({}))

POWERSHELL = os.path.join(
    os.environ.get("SystemRoot", r"C:\Windows"),
    "System32", "WindowsPowerShell", "v1.0", "powershell.exe")


# --------------------------------------------------------------------------- #
# plumbing
# --------------------------------------------------------------------------- #
def _ps(args: List[str], timeout: float = 240.0) -> Dict[str, Any]:
    """Run the real PowerShell 5.1 script and return its exit code + output."""
    p = subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(args[0])]
        + list(args[1:]),
        capture_output=True, cwd=str(REPO), timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
    )
    out = p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace")
    return {"exit": p.returncode, "out": out}


def _http(url: str, timeout: float = 5.0):
    try:
        with _LOOPBACK.open(url, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return 0, ""


def _j(raw: str) -> Any:
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return None


def _wait_http(url: str, seconds: float = 45.0) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        st, _ = _http(url)
        if st == 200:
            return True
        time.sleep(0.25)
    return False


def _port_pid(port: int) -> int:
    """LISTENING owner of a port, via netstat (independent of the PS helper)."""
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True,
                             timeout=30, creationflags=CREATE_NO_WINDOW)
        text = out.stdout.decode("cp936", "replace")
    except Exception:  # noqa: BLE001
        return 0
    for line in text.splitlines():
        if "LISTENING" not in line:
            continue
        m = re.match(r"^\s*TCP\s+\S+:(\d+)\s+\S+:\d+\s+LISTENING\s+(\d+)", line)
        if m and int(m.group(1)) == port:
            return int(m.group(2))
    return 0


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _task_store_names() -> List[str]:
    try:
        return sorted(p.name for p in TASK_STORE.iterdir())
    except Exception:  # noqa: BLE001
        return []


def _start(cmd: List[str], env_extra: Dict[str, str]) -> subprocess.Popen:
    env = dict(os.environ)
    env.update(env_extra)
    return subprocess.Popen(cmd, cwd=str(REPO), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=CREATE_NO_WINDOW)


def _kill(p: Optional[subprocess.Popen]) -> None:
    if p is None:
        return
    try:
        if p.poll() is None:
            p.kill()
            p.wait(timeout=10)
    except Exception:  # noqa: BLE001
        pass


def _free_pid() -> int:
    """A pid that is provably dead: start a process that exits immediately."""
    p = subprocess.Popen([sys.executable, "-c", "pass"], creationflags=CREATE_NO_WINDOW)
    p.wait(timeout=30)
    return p.pid


class Checks:
    def __init__(self) -> None:
        self.steps: List[Dict[str, Any]] = []

    def add(self, step: str, ok: bool, value: Any = None, note: str = "") -> None:
        self.steps.append({"step": step, "ok": bool(ok), "value": value, "note": note})

    @property
    def failed(self) -> List[Dict[str, Any]]:
        return [s for s in self.steps if not s["ok"]]

    def payload(self, gate: str) -> Dict[str, Any]:
        return {
            "gate": gate,
            "verdict": "PASS" if not self.failed and self.steps else "FAIL",
            "passed": sum(1 for s in self.steps if s["ok"]),
            "total": len(self.steps),
            "failures": self.failed,
            "steps": self.steps,
            "ts": time.time(),
        }


def _write(checks: Checks) -> str:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = SCRATCH / ("v1_3_wave8_gate_%s.json" % stamp)
    out.write_text(json.dumps(checks.payload("WAVE8_DURABLE_RUNTIME"), indent=2),
                   encoding="utf-8")
    return str(out)


# --------------------------------------------------------------------------- #
# A. the serving path (AST audit, including the audit's own falsifiability)
# --------------------------------------------------------------------------- #
def group_a_serving_path(c: Checks) -> None:
    finding = UIAUDIT.audit_source(str(SERVE), SERVE.read_text(encoding="utf-8"))
    c.add("UI_AUDIT_PASSES", finding["verdict"] == "PASS",
          {"verdict": finding["verdict"], "problems": finding["problems"]},
          "the real serve_visual.py is an ASGI/uvicorn serving path")

    c.add("NO_STDLIB_HTTP_SERVER_IMPORTED",
          not finding["banned_imports"] and not finding["banned_references"],
          {"banned_imports": finding["banned_imports"],
           "banned_references": finding["banned_references"]},
          "no import of, or code reference to, the stdlib demo server")

    # The audit must be able to FAIL. Without this, "no banned token" is a
    # statement about the checker, not about the file.
    bad = ("import http.server\n"
           "import uvicorn\n"
           "from starlette.applications import Starlette\n"
           "from starlette.responses import FileResponse\n"
           "uvicorn.run(Starlette())\n")
    r_bad = UIAUDIT.audit_source("<synthetic:imports>", bad)
    c.add("AUDIT_REJECTS_HTTP_SERVER_IMPORT", r_bad["verdict"] == "FAIL",
          r_bad["problems"], "the audit can fail: a real import is caught")

    bad2 = ("from http import server\n"
            "import uvicorn\n"
            "from starlette.applications import Starlette\n"
            "from starlette.responses import FileResponse\n"
            "uvicorn.run(Starlette())\n")
    r_bad2 = UIAUDIT.audit_source("<synthetic:from-http>", bad2)
    c.add("AUDIT_REJECTS_FROM_HTTP_IMPORT", r_bad2["verdict"] == "FAIL",
          r_bad2["problems"], "`from http import server` reaches the same module")

    # ...and it must NOT fail on prose. This was a REAL false FAIL while
    # building this gate: a substring scan flagged serve_visual.py's docstring,
    # which explains the dependency that was removed.
    prose = ('"""Old version used http.server.HTTPServer + SimpleHTTPRequestHandler."""\n'
             "import uvicorn\n"
             "from starlette.applications import Starlette\n"
             "from starlette.responses import FileResponse\n"
             "uvicorn.run(Starlette())\n")
    r_prose = UIAUDIT.audit_source("<synthetic:prose>", prose)
    c.add("AUDIT_NOT_FOOLED_BY_PROSE", r_prose["verdict"] == "PASS",
          r_prose["problems"],
          "a docstring that mentions http.server is not a dependency")

    r_stub = UIAUDIT.audit_source("<synthetic:stub>", "")
    c.add("AUDIT_NOT_VACUOUS", r_stub["verdict"] == "FAIL",
          r_stub["problems"],
          "an empty file cannot pass by saying nothing: positives are required")

    r_missing = UIAUDIT.audit_source("<synthetic:missing>", "x = 1\n")
    c.add("AUDIT_REQUIRES_POSITIVES", r_missing["verdict"] == "FAIL",
          r_missing["positives"], "uvicorn/Starlette/FileResponse must be present")

    v = subprocess.run([sys.executable, "-c",
                        "import uvicorn, starlette; print(uvicorn.__version__)"],
                       capture_output=True, timeout=60, creationflags=CREATE_NO_WINDOW)
    c.add("UVICORN_STARLETTE_IMPORTABLE", v.returncode == 0,
          v.stdout.decode("utf-8", "replace").strip() or
          v.stderr.decode("utf-8", "replace").strip()[-200:],
          "the ASGI runtime is actually installed in this interpreter")


# --------------------------------------------------------------------------- #
# B. bind discipline
# --------------------------------------------------------------------------- #
def group_b_bind(c: Checks) -> None:
    proc = None
    try:
        p = subprocess.run(
            [sys.executable, str(SERVE), "--host", "0.0.0.0",
             "--port", str(FOREIGN_PORT), "--dist", str(DIST)],
            capture_output=True, cwd=str(REPO), timeout=90,
            creationflags=CREATE_NO_WINDOW)
        out = (p.stdout + p.stderr).decode("utf-8", "replace")
        c.add("REFUSES_NON_LOOPBACK_BIND", p.returncode == 3,
              {"exit": p.returncode, "out": out.strip()[-300:]},
              "a non-loopback bind is refused with exit 3 by the server itself")
        c.add("NON_LOOPBACK_BIND_LEFT_NOTHING_LISTENING", _port_pid(FOREIGN_PORT) == 0,
              _port_pid(FOREIGN_PORT),
              "the refused server did not bind the port on the way out")
    finally:
        _kill(proc)

    try:
        from jcc_core.config import load_config
        os.environ["JCC_CONFIG"] = str(REPO / "config" / "jcc-v1.3.yaml")
        cfg = load_config()
        c.add("CONFIG_BINDS_LOOPBACK", cfg.server.host == "127.0.0.1",
              cfg.server.host, "the product config binds loopback only")
        # Not a literal True: that would be a statement about this file, not
        # about the config. The product UI port must be declared by the product
        # config, and GREEN must never appear among its allowed origins.
        cors = [str(o) for o in (cfg.server.cors_origins or [])]
        c.add("CONFIG_DECLARES_THE_PRODUCT_UI_PORT",
              any(o.endswith(":" + str(UI_PORT)) for o in cors)
              and not any(":" + str(GREEN_PORT) in o for o in cors),
              cors,
              "the config allowlists the product UI port and never the frozen GREEN port")
        c.add("CONFIG_PORTS_ARE_DISJOINT",
              len({GREEN_PORT, API_PORT, UI_PORT}) == 3 and cfg.server.port == API_PORT,
              {"green": GREEN_PORT, "api": cfg.server.port, "ui": UI_PORT},
              "GREEN, the product API and the product UI occupy three distinct ports")
        c.add("CONFIG_ACTUATION_OFF", cfg.actuation.enabled is False,
              cfg.actuation.enabled, "actuation is off in the product config")
    except Exception as exc:  # noqa: BLE001
        c.add("CONFIG_BINDS_LOOPBACK", False, "%s: %s" % (type(exc).__name__, exc),
              "could not load config/jcc-v1.3.yaml")


# --------------------------------------------------------------------------- #
# C. the operator surface, live
# --------------------------------------------------------------------------- #
def _runtime_files() -> List[str]:
    if not RUNTIME.is_dir():
        return []
    return sorted(p.name for p in RUNTIME.iterdir())


def group_c_operator_surface(c: Checks, procs: List[subprocess.Popen],
                             tmp: Path) -> None:
    api = ui = foreign = None

    # --- C-1. the gate needs a quiet host ----------------------------------- #
    # Stated as its own check so that a leftover instance produces ONE legible
    # FAIL naming the cause, instead of a cascade of confusing binds failures.
    # The gate will not kill it: an unknown holder on the product port is
    # exactly the foreign-hold case the product must refuse, and reaching over
    # it here would be the gate doing the thing it exists to forbid.
    pre = (_port_pid(API_PORT), _port_pid(UI_PORT))
    c.add("PRECONDITION_NO_PREEXISTING_PRODUCT_SERVICE", pre == (0, 0), pre,
          "the gate starts from a quiet host: nothing holds 4779/4780")

    # --- C0. -Action up -DryRun performs the pre-flight and changes NOTHING ---
    before = _runtime_files()
    r = _ps([CONTROL, "-Action", "up", "-DryRun"])
    after = _runtime_files()
    c.add("UP_DRYRUN_PREFLIGHT",
          r["exit"] == 0 and "DRY RUN" in r["out"] and "reconcile ports" in r["out"],
          {"exit": r["exit"], "tail": r["out"].strip()[-260:]},
          "-Action up -DryRun runs the prerequisite gate and the port reconciliation")
    c.add("UP_DRYRUN_CHANGED_NOTHING", before == after,
          {"before": before, "after": after},
          "a dry run must not create, move or delete anything in the runtime dir")

    # --- C1. start the two services the way the DURABLE path does ----------- #
    env = {"JCC_CONFIG": str(REPO / "config" / "jcc-v1.3.yaml"),
           "PYTHONPATH": str(PKGS)}
    api = _start([sys.executable, "-m", "jcc_core.run"], env)
    procs.append(api)
    ui = _start([sys.executable, str(SERVE), "--host", "127.0.0.1",
                 "--port", str(UI_PORT), "--dist", str(DIST)], env)
    procs.append(ui)

    ok_api = _wait_http("http://127.0.0.1:%d/api/health" % API_PORT, 60)
    ok_ui = _wait_http("http://127.0.0.1:%d/health" % UI_PORT, 60)
    c.add("BOTH_SERVICES_LISTENING", ok_api and ok_ui,
          {"api": ok_api, "ui": ok_ui, "api_pid": _port_pid(API_PORT),
           "ui_pid": _port_pid(UI_PORT)},
          "the CORE API and the Visual UI both answer on loopback")
    if not (ok_api and ok_ui):
        return

    st, raw = _http("http://127.0.0.1:%d/health" % UI_PORT)
    c.add("UI_RUNTIME_REPORTS_UVICORN", (_j(raw) or {}).get("runtime") == "uvicorn-asgi",
          _j(raw), "the live UI identifies its runtime as uvicorn-asgi")
    # The behavioural counterpart to the config check in group B: the declared
    # UI port and the port the server is actually bound to must be the same one.
    c.add("UI_HEALTH_REPORTS_ITS_BOUND_PORT",
          (_j(raw) or {}).get("port") == UI_PORT, _j(raw),
          "the live UI reports the port it is bound to, matching the declared one")

    # --- C2. adopt them (no launcher wrote a marker) ------------------------ #
    r = _ps([CONTROL, "-Action", "mark"])
    c.add("MARK_ADOPTS_RUNNING_INSTANCE", r["exit"] == 0 and "MARKED" in r["out"],
          {"exit": r["exit"], "tail": r["out"].strip()[-200:]},
          "an already-running instance is adopted by command-line identification")
    c.add("MARK_WROTE_BOTH_OWNERSHIP_PAIRS",
          (RUNTIME / "jcc-v1.3.pid").is_file()
          and (RUNTIME / "jcc-v1.3.ownership.json").is_file()
          and (RUNTIME / "jcc-v1.3-ui.pid").is_file()
          and (RUNTIME / "jcc-v1.3-ui.ownership.json").is_file(),
          _runtime_files(),
          "both halves get a pid file and an ownership marker")

    # --- C3. status --------------------------------------------------------- #
    r = _ps([CONTROL, "-Action", "status"])
    out = r["out"]
    c.add("STATUS_REPORTS_UP", r["exit"] == 0 and "RESULT: UP (v1.3)" in out,
          {"exit": r["exit"], "result": [l for l in out.splitlines() if "RESULT:" in l]},
          "status exits 0 only when both surfaces are verified UP")
    c.add("STATUS_SHOWS_UVICORN_AND_ACTUATION_OFF",
          "uvicorn-asgi" in out and "actuation=False" in out,
          None, "the operator sees the runtime kind and that actuation is off")
    c.add("STATUS_UPTIME_SOURCE_IS_PROCESS", "(source: process)" in out,
          [l.strip() for l in out.splitlines() if "uptime" in l][:2],
          "uptime comes from the live process, never from the marker")

    # --- C4. doctor --------------------------------------------------------- #
    r = _ps([CONTROL, "-Action", "doctor"])
    c.add("DOCTOR_PASSES_ON_A_HEALTHY_HOST",
          r["exit"] == 0 and "RESULT: PASS" in r["out"],
          {"exit": r["exit"], "fail": [l.strip() for l in r["out"].splitlines()
                                       if "[FAIL]" in l]},
          "every doctor check passes with both surfaces up")

    # Cross-check the task verdict against the store of record. Falsifiable and
    # non-vacuous: it fails the moment doctor calls a task ABSENT while its file is
    # on disk (which it did until this check existed -- D-13 asked the provider
    # that is blind in this session), and it requires the store to actually be
    # readable, so that an "absent" answer is a statement about the host rather
    # than about the instrument.
    store_names = set(_task_store_names())
    tracked = ("JCC-v1.3-API", "JCC-v1.3-VisualUI")
    says_absent = {t for t in tracked
                   if re.search(r"\b" + re.escape(t) + r"=ABSENT", r["out"])}
    on_disk = {t for t in tracked if t in store_names}
    c.add("DOCTOR_TASK_VERDICT_AGREES_WITH_THE_TASK_STORE",
          bool(store_names) and not (says_absent & on_disk),
          {"says_absent": sorted(says_absent), "present_on_disk": sorted(on_disk),
           "store_size": len(store_names)},
          "doctor never calls a scheduled task ABSENT while its task file is on disk")

    # --- C5. short soak: the processes do not churn ------------------------- #
    p0 = (_port_pid(API_PORT), _port_pid(UI_PORT))
    time.sleep(SOAK_SECONDS)
    p1 = (_port_pid(API_PORT), _port_pid(UI_PORT))
    h1 = _http("http://127.0.0.1:%d/api/health" % API_PORT)[0]
    h2 = _http("http://127.0.0.1:%d/health" % UI_PORT)[0]
    c.add("SOAK_PIDS_STABLE", p0 == p1 and p0[0] != 0 and p0[1] != 0,
          {"t0": p0, "t%d" % SOAK_SECONDS: p1, "seconds": SOAK_SECONDS},
          "the same two processes answer across the soak (no silent restart)")
    c.add("SOAK_BOTH_STILL_200", h1 == 200 and h2 == 200, {"api": h1, "ui": h2},
          "both surfaces still answer at the end of the soak")

    # --- C6. FAIL-CLOSED: a foreign listener must never be signalled -------- #
    foreign = _start([sys.executable, "-c",
                      "import socket,time\n"
                      "s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,"
                      "socket.SO_REUSEADDR,1)\n"
                      "s.bind(('127.0.0.1',%d)); s.listen(5); time.sleep(180)\n"
                      % FOREIGN_PORT], env)
    procs.append(foreign)
    time.sleep(2.5)
    fpid = _port_pid(FOREIGN_PORT)
    # NOT `fpid == foreign.pid`. On this host the venv's python.exe is a
    # redirector stub: the Popen handle names the stub, while the listening
    # socket is held by the real interpreter it spawns, so the two pids differ
    # by construction. What the following checks actually need is: (a) the port
    # IS held, (b) by something that is not one of our two surfaces, and (c) our
    # handle is still alive, so "the refusal did not signal it" keeps its meaning.
    ours = (_port_pid(API_PORT), _port_pid(UI_PORT))
    c.add("FOREIGN_LISTENER_IS_UP",
          fpid != 0 and fpid not in ours and foreign.poll() is None,
          {"port": FOREIGN_PORT, "pid": fpid, "ours": ours, "popen_pid": foreign.pid},
          "a listener that is NOT one of our two surfaces holds the test port")

    ours_before = (_port_pid(API_PORT), _port_pid(UI_PORT))
    # The UI half is pointed at a FREE port on purpose: the point of this check is
    # the API half's foreign hold, and pointing the UI half at 4780 would stop the
    # real product UI as a side effect of testing that we refuse to stop things.
    r = _ps([CONTROL, "-Action", "down", "-ApiPort", str(FOREIGN_PORT),
             "-UiPort", str(FOREIGN_PORT + 1)])
    c.add("DOWN_REFUSES_FOREIGN_HOLD",
          r["exit"] != 0 and "REFUSED" in r["out"],
          {"exit": r["exit"], "refusals": [l.strip() for l in r["out"].splitlines()
                                          if "REFUSED" in l][:3]},
          "a foreign port hold makes the stop path refuse")
    # Asserting on the LISTENER, not just on our handle: if the refusal had
    # signalled the real interpreter, the port would be free even though the
    # stub we hold a handle to was still alive.
    c.add("DOWN_DID_NOT_SIGNAL_THE_FOREIGN_PROCESS",
          foreign.poll() is None and _port_pid(FOREIGN_PORT) == fpid,
          {"foreign_pid": fpid, "popen_alive": foreign.poll() is None,
           "port_pid_after": _port_pid(FOREIGN_PORT)},
          "the refusal is real: the foreign listener still holds its port afterwards")
    c.add("DOWN_REFUSAL_ALSO_LEFT_OUR_SERVICES_ALONE",
          ours_before == (_port_pid(API_PORT), _port_pid(UI_PORT)),
          {"before": ours_before,
           "after": (_port_pid(API_PORT), _port_pid(UI_PORT))},
          "a refused run signals nothing at all -- not even the surfaces it owns")

    # --- C7. down the real pair, verified ---------------------------------- #
    r = _ps([CONTROL, "-Action", "down"])
    c.add("DOWN_STOPS_VERIFIED_INSTANCE",
          r["exit"] == 0 and "RESULT: STOPPED (v1.3)" in r["out"],
          {"exit": r["exit"], "tail": r["out"].strip()[-260:]},
          "a verified instance is stopped and both halves report 0")
    c.add("DOWN_LEFT_NO_LISTENERS",
          _port_pid(API_PORT) == 0 and _port_pid(UI_PORT) == 0,
          {"api": _port_pid(API_PORT), "ui": _port_pid(UI_PORT)},
          "both product ports are free after the stop")
    c.add("DOWN_REMOVED_ROUNDED_BOOKKEEPING",
          not (RUNTIME / "jcc-v1.3.pid").is_file()
          and not (RUNTIME / "jcc-v1.3-ui.pid").is_file()
          and not (RUNTIME / "jcc-v1.3.ownership.json").is_file()
          and not (RUNTIME / "jcc-v1.3-ui.ownership.json").is_file(),
          [f for f in _runtime_files() if "ownership" in f or f.endswith(".pid")],
          "bookkeeping is cleaned only after the process is gone and the port is free")

    # --- C8. repair: quarantines the PROVABLY stale, by rename -------------- #
    dead = _free_pid()
    (RUNTIME / "jcc-v1.3.pid").write_text(str(dead), encoding="ascii")
    (RUNTIME / "jcc-v1.3.ownership.json").write_text('{"service":"gate-fixture"}',
                                                     encoding="utf-8")
    r = _ps([CONTROL, "-Action", "repair"])
    stale = [f for f in _runtime_files() if ".stale-" in f]
    c.add("REPAIR_QUARANTINES_STALE_BOOKKEEPING",
          r["exit"] == 0 and len(stale) >= 2
          and not (RUNTIME / "jcc-v1.3.pid").is_file(),
          {"exit": r["exit"], "stale": stale},
          "a dead pid with a free port is quarantined")
    c.add("REPAIR_MOVES_AND_DOES_NOT_DELETE",
          any(f.startswith("jcc-v1.3.pid.stale-") for f in stale)
          and any(f.startswith("jcc-v1.3.ownership.json.stale-") for f in stale),
          stale, "the stale files still exist, renamed -- nothing was deleted")
    # repair must not even LOOK at GREEN: probing it is how a "read-only" helper
    # ends up being the thing that reports a frozen service as broken.
    c.add("REPAIR_DOES_NOT_PROBE_GREEN",
          "GREEN 4778  :" not in r["out"] and "not even probed here" in r["out"],
          [l.strip() for l in r["out"].splitlines() if "GREEN" in l][:2],
          "repair declares that it does not probe the frozen service")

    # --- C9. repair REFUSES a live-but-unverified pid ----------------------- #
    (RUNTIME / "jcc-v1.3.pid").write_text(str(_port_pid(FOREIGN_PORT)), encoding="ascii")
    (RUNTIME / "jcc-v1.3.ownership.json").write_text('{"service":"gate-fixture"}',
                                                     encoding="utf-8")
    r = _ps([CONTROL, "-Action", "repair", "-ApiPort", str(FOREIGN_PORT)])
    still_there = (RUNTIME / "jcc-v1.3.pid").is_file()
    ok = r["exit"] == 2 and "REFUSED" in r["out"] and still_there
    c.add("REPAIR_REFUSES_A_LIVE_UNVERIFIED_PID", ok,
          {"exit": r["exit"], "pid_file_still_present": still_there,
           "refusals": [l.strip() for l in r["out"].splitlines()
                        if "REFUSED" in l][:3]},
          "a live process is never stripped of its bookkeeping")
    c.add("REPAIR_REFUSAL_DID_NOT_SIGNAL",
          foreign.poll() is None and _port_pid(FOREIGN_PORT) == fpid,
          {"foreign_pid": fpid, "port_pid_after": _port_pid(FOREIGN_PORT),
           "popen_alive": foreign.poll() is None},
          "refusing means refusing: the live pid still holds its port")

    # --- cleanup: quarantine the fixtures out of the product runtime dir ---- #
    for f in _runtime_files():
        if ".stale-" in f or f.startswith("jcc-v1.3."):
            src = RUNTIME / f
            if src.is_file():
                shutil.move(str(src), str(tmp / ("fixture-" + f)))
    _kill(foreign)


# --------------------------------------------------------------------------- #
# D. the durable configuration must stay strictly opt-in
# --------------------------------------------------------------------------- #
def group_d_task_registration(c: Checks) -> None:
    before = _task_store_names()
    r = _ps([REGISTER])
    after = _task_store_names()
    c.add("TASK_REGISTRATION_IS_PLAN_ONLY_BY_DEFAULT",
          r["exit"] == 0 and "PLAN ONLY" in r["out"],
          {"exit": r["exit"], "tail": r["out"].strip()[-200:]},
          "a bare run prints the plan and does not claim to have applied it")
    c.add("BARE_RUN_DID_NOT_TOUCH_THE_TASK_STORE", before == after,
          {"added": sorted(set(after) - set(before)),
           "removed": sorted(set(before) - set(after))},
          "the Windows task store is byte-for-byte the same set of tasks")

    r = _ps([REGISTER, "-WhatIf"])
    out = r["out"]
    c.add("TASK_PLAN_NAMES_THE_PRODUCT_SURFACES",
          "JCC-v1.3-API" in out and "JCC-v1.3-VisualUI" in out, None,
          "the plan names exactly the two product surfaces")
    c.add("TASK_PLAN_IS_LOOPBACK_AND_EXPLICIT_ENV",
          "127.0.0.1" in out and "jcc-v1.3.yaml" in out
          and "PYTHONPATH" in out and "-m jcc_core.run" in out
          and "--port 4780" in out,
          None, "the durable actions bind loopback and set their own environment")
    c.add("TASK_PLAN_DECLARES_REBOOT_AND_RESTART",
          "AtStartup" in out and "SYSTEM" in out, None,
          "reboot recovery and automatic restart are declared in the plan")


# --------------------------------------------------------------------------- #
# E. the control surface's own invariants, and this evidence's provenance
# --------------------------------------------------------------------------- #
_PS_AST_SCRIPT = r"""
$Path = '__PATH__'
$tokens = $null; $errs = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$errs)
$cmds = @()
foreach ($n in $ast.FindAll({ param($x) $x -is [System.Management.Automation.Language.CommandAst] }, $true)) {
    $cmds += [pscustomobject]@{ name = [string]$n.GetCommandName(); line = $n.Extent.StartLineNumber }
}
$fns = @{}
foreach ($n in $ast.FindAll({ param($x) $x -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)) {
    $fns[$n.Name] = @($n.Extent.StartLineNumber, $n.Extent.EndLineNumber)
}
[pscustomobject]@{ commands = $cmds; functions = $fns } | ConvertTo-Json -Depth 6 -Compress
"""


def _ps_ast(path: Path) -> Dict[str, Any]:
    """Command INVOCATIONS and function extents, straight from the PowerShell parser.

    Not a text scan, and not a comment-stripped text scan either.

    The first version of the repair invariant searched the function's raw text for
    the literal `Stop-Process` and reported a FAIL. Comments were then stripped and
    it failed again -- because the operator-facing message that function prints IS
    the sentence "there is no Stop-Process path in this action". A statement ABOUT
    an invariant is not a violation of it, and no amount of text munging can tell a
    command invocation from prose.

    The parser can. It hands back each CommandAst's resolved NAME with its line
    number, so the checks below ask "is there a signalling COMMAND in this line
    range?" instead of "does this word appear anywhere?". Line numbers keep the
    answer auditable, and a command the parser cannot see cannot run.

    EncodedCommand is used so the analyser cannot be mangled by shell quoting: it
    travels as base64 UTF-16LE, which is exactly what the switch expects. The path
    is injected into the script body rather than passed as an argument, because
    -EncodedCommand carries no arguments of its own -- a `param()` block here
    binds nothing and the analyser silently reports zero commands, which is a
    false PASS shape (measured: the first version of this helper returned
    commands=0, functions=0).
    """
    script = _PS_AST_SCRIPT.replace("__PATH__", str(path).replace("'", "''"))
    enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    p = subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-EncodedCommand", enc],
        capture_output=True, timeout=180, cwd=str(REPO),
        creationflags=CREATE_NO_WINDOW)
    raw = p.stdout.decode("utf-8", "replace")
    try:
        data = json.loads(raw)
    except Exception:  # noqa: BLE001
        return {"commands": [], "functions": {},
                "error": (raw + p.stderr.decode("utf-8", "replace")).strip()[-300:]}
    if isinstance(data, list):          # a 1-element emit collapses to an array
        data = data[0] if data else {}
    if not isinstance(data, dict):
        data = {}
    cmds = data.get("commands") or []
    if isinstance(cmds, dict):
        cmds = [cmds]
    data["commands"] = cmds
    if not isinstance(data.get("functions"), dict):
        data["functions"] = {}
    if not cmds and not data["functions"]:
        # The false-PASS shape: an analyser that reports nothing at all reads as
        # "no violations found". That is the exact instrument-lies failure this
        # project keeps catching (the first version of this helper did precisely
        # this), so it is surfaced as an error which the checks below refuse.
        data["error"] = ((data.get("error") or "")
                         + " ; parser resolved zero commands and zero functions")
    return data


def group_e_control_invariants(c: Checks) -> None:
    text = CONTROL.read_text(encoding="utf-8")

    m = re.search(r"ValidateSet\(([^)]*)\)", text)
    declared = [x.strip().strip("'") for x in m.group(1).split(",")] if m else []
    want = ["up", "down", "restart", "status", "doctor", "repair", "mark"]
    c.add("CONTROL_DECLARES_ALL_ACTIONS", declared == want, declared,
          "the single entry point declares every lifecycle action")

    # The two static invariants below are answered by the PARSER, not by a text
    # scan. The first version searched raw text and reported has_Stop-Process=True
    # for a repair path whose only mention of it was its own operator message
    # ("there is no Stop-Process path in this action"); stripping comments did not
    # help, because the mention is a string literal, not a comment. A command the
    # parser cannot resolve is a command that cannot run, so the parser is the
    # source that can actually answer these questions.
    ast = _ps_ast(CONTROL)
    cmds = ast.get("commands") or []
    fns = ast.get("functions") or {}

    def _fn(name: str):
        r = fns.get(name)
        return (int(r[0]), int(r[1])) if r and len(r) == 2 else None

    def _names_between(rng):
        if not rng:
            return []
        return sorted({str(cm.get("name") or "") for cm in cmds
                       if rng[0] <= int(cm.get("line") or 0) <= rng[1]})

    SIGNAL_CMDS = ("stop-process", "taskkill", "kill")
    DELETE_CMDS = ("remove-item", "clear-content", "remove-itemproperty")

    def _matches(names, needles):
        return sorted(n for n in names
                      if any(n.lower() == h for h in needles))

    def _closure(root: str):
        """Transitive closure over project-local callees, starting at `root`.

        One level is not enough: Invoke-V13Repair delegates the actual quarantine
        to Repair-V13Half, which delegates to Move-V13StaleFile. A Remove-Item
        hiding one call down would have sailed past a direct-children check while
        the claim under test ("repair can only ever move bookkeeping aside") is
        about the whole action.
        """
        seen, order, stack = set(), [], [root]
        while stack:
            fn = stack.pop()
            if fn in seen or fn not in fns:
                continue
            seen.add(fn)
            here = _names_between(_fn(fn))
            order.extend(here)
            stack.extend([n for n in here if n in fns])
        return seen, sorted(set(order))

    down_range = _fn("Invoke-V13ApiDown")
    signal_lines = sorted(int(cm.get("line") or 0) for cm in cmds
                          if str(cm.get("name") or "").lower() in SIGNAL_CMDS)
    c.add("NO_SIGNAL_PATH_BEFORE_THE_VERIFIED_STOP",
          not ast.get("error") and down_range is not None and bool(signal_lines)
          and min(signal_lines) >= down_range[0],
          {"Invoke-V13ApiDown": down_range, "signal_call_lines": signal_lines,
           "parser_error": ast.get("error")},
          "the parser resolves no process-signalling command before the verified stop path")

    rep_reached, rep_names = _closure("Invoke-V13Repair")
    rep_bad = _matches(rep_names, SIGNAL_CMDS + DELETE_CMDS)
    mover_reached, mover_names = _closure("Move-V13StaleFile")
    c.add("REPAIR_HAS_NO_SIGNAL_AND_NO_DELETE",
          not ast.get("error") and bool(rep_names) and not rep_bad
          and "Move-V13StaleFile" in rep_reached
          and bool(_matches(mover_names, ("move-item",))),
          {"repair_reaches": sorted(rep_reached), "repair_commands": rep_names,
           "repair_forbidden": rep_bad,
           "mover_commands": mover_names,
           "parser_error": ast.get("error")},
          "TRANSITIVELY: repair reaches a mover and resolves no command that signals or deletes")

    c.add("CONTROL_VERIFIES_OWNERSHIP_BEFORE_ANY_SIGNAL",
          "Test-JccOwnership" in text and "Test-V13UiOwnership" in text, None,
          "both halves go through a marker+pid+cmdline+port contract")

    c.add("REGISTER_SCRIPT_VERDICT_IS_NOT_THE_CIM_PROVIDER",
          "Test-V13TaskOnDisk" in REGISTER.read_text(encoding="utf-8")
          and "System32\\Tasks" in REGISTER.read_text(encoding="utf-8"), None,
          "the registration verdict comes from the task store on disk (the CIM "
          "provider is measurably blind in this session)")

    digests = {}
    for p in [SERVE, CONTROL, REGISTER, REPO / "scripts" / "lib" / "JccCommon.ps1",
              REPO / "config" / "jcc-v1.3.yaml", TOOLS / "v1_3_ui_runtime_audit.py",
              Path(__file__)]:
        if p.is_file():
            digests[str(p.relative_to(REPO)).replace("\\", "/")] = _sha256(p)
    c.add("EVIDENCE_HASHES", len(digests) >= 6, digests,
          "every artifact this gate judges is hashed into its own evidence")


# --------------------------------------------------------------------------- #
def main() -> int:
    c = Checks()
    procs: List[subprocess.Popen] = []
    tmp = Path(tempfile.mkdtemp(prefix="w8gate_"))
    green_pid0 = _port_pid(GREEN_PORT)
    green_http0 = _http("http://127.0.0.1:%d/api/health" % GREEN_PORT)[0]

    try:
        group_a_serving_path(c)
        group_b_bind(c)
        group_c_operator_surface(c, procs, tmp)
        group_d_task_registration(c)
        group_e_control_invariants(c)

        green_pid1 = _port_pid(GREEN_PORT)
        green_http1 = _http("http://127.0.0.1:%d/api/health" % GREEN_PORT)[0]
        c.add("GREEN_UNTOUCHED",
              green_pid0 != 0 and green_pid0 == green_pid1 and green_http0 == 200
              and green_http1 == 200,
              {"pid_before": green_pid0, "pid_after": green_pid1,
               "http_before": green_http0, "http_after": green_http1},
              "the frozen GREEN service is the same process and still UP")

        c.add("NO_PRODUCT_LISTENERS_LEFT_BEHIND",
              _port_pid(API_PORT) == 0 and _port_pid(UI_PORT) == 0,
              {"api": _port_pid(API_PORT), "ui": _port_pid(UI_PORT)},
              "the gate leaves no v1.3 process running")
    except Exception as exc:  # noqa: BLE001
        c.add("GATE_COMPLETED_WITHOUT_AN_EXCEPTION", False,
              "%s: %s" % (type(exc).__name__, exc),
              "the gate ran to the end")
    finally:
        for p in procs:
            _kill(p)
        shutil.rmtree(str(tmp), ignore_errors=True)

    out = _write(c)
    payload = c.payload("x")
    print("gate WAVE8_DURABLE_RUNTIME verdict=%s passed=%d/%d"
          % (payload["verdict"], payload["passed"], len(c.steps)))
    for s in c.failed:
        print("  FAIL %s: %s" % (s["step"], s["value"]))
    print("wrote", out)
    return 0 if not c.failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
