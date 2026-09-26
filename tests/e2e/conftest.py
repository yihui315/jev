"""E2E test support for JEV COMMAND CENTER (slice E-02 / E-04, owner: trail-2).

Scope rule: only ``tests/e2e/**`` is edited. This conftest (a) makes the
``jcc_core`` package importable from this tests tree, (b) provides two REAL
HTTP server launchers -- an in-process uvicorn server for E-02 and a real
``python -m jcc_core.run`` subprocess for E-04 (crash + restart recovery) -- and
(c) writes the two evidence JSONs the acceptance gate consumes
(``artifacts/acceptance/api_e2e.json`` and ``.../restart_recovery.json``) from
the REAL run, never from hope.

Windows-safe: no global process kills; the subprocess is killed only by its own
PID (simulating the Windows TerminateProcess abrupt-kill path ADR-0006 covers).

Two defects this file used to carry, both found by measurement on 2026-09-26
--------------------------------------------------------------------------------
1. IT CLOBBERED ITS OWN EVIDENCE ON A COLLECT-ONLY PASS. ``pytest_sessionfinish``
   fires for ``--collect-only`` too, where no test ever reports; the old code
   then wrote every check as ``FAILED`` and overwrote the real PASS artifact.
   ``artifacts/acceptance/final_gate_run.py::collect_test_ids()`` collects over
   every declared scope path, ``tests/e2e`` included, so merely asking the gate
   "which tests exist?" destroyed the evidence the gate was about to read.
   Measured: one ``pytest --collect-only -q tests/e2e`` turned both artifacts
   from ``passed=True`` into ``passed=False, failed=12/1``.
   Fix: refuse to write when the session did not run tests, and refuse to write
   a file with no recorded report at all.

2. THE EVIDENCE WAS NOT A FUNCTION OF THE SOURCE. It embedded a wall-clock
   ``at`` timestamp, so the gate's own ``--full`` run (which re-runs the T-14
   scope and therefore rewrites these files) always left the tree dirty -- and
   the gate's P-01/P-05 read a dirty tree as an unexplained provenance failure.
   That made the stop condition *unsatisfiable* the moment the evidence was put
   under version control: committing the artifact guaranteed the next gate run
   would fail. Measured: the 2026-09-26T05:10 gate run reported
   ``PROJECT_COMPLETE=false`` with ``failed=['P-01','P-05']`` whose only cause
   was these two regenerated files.
   Fix: the payload is now derived from the SOURCE -- the sha256 of this
   generator and of the test module, the sorted node ids, and the per-test
   outcome -- with no clock and no commit id in it. Re-running the same scope on
   the same bytes now produces byte-identical evidence, so "the tree is clean
   after a gate run" is a property that can be tested rather than hoped for.
   WHAT IS DELIBERATELY NOT RECORDED: the HEAD commit. An artifact that names
   the commit that carries it can never be byte-stable -- committing it moves
   HEAD and invalidates its own content. Provenance is carried by the source
   hashes instead, which any reader can recompute from a clone.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

# --- make jcc_core importable from this tests/e2e tree --------------------- #
REPO = Path(__file__).resolve().parents[2]
PKGS = REPO / "packages"
if str(PKGS) not in sys.path:
    sys.path.insert(0, str(PKGS))

import uvicorn  # noqa: E402
from jcc_core.actuator import Actuator  # noqa: E402
from jcc_core.config import Config, ServerConfig  # noqa: E402
from jcc_core.core import CoreEngine  # noqa: E402
from jcc_core.providers import build_provider  # noqa: E402
from jcc_core.server import create_app  # noqa: E402
from jcc_core.state_store import StateStore  # noqa: E402

PY = str(REPO / ".venv" / "Scripts" / "python.exe")
CONTROL_TOKEN_ENV = "JCC_CONTROL_TOKEN"
SIGNING_KEY_ENV = "JCC_DIRECTIVE_SIGNING_KEY"

DEFAULT_CORS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

#: module-file stem -> (test module that owns it, evidence JSON the gate reads).
#: The module path is carried explicitly so the evidence can name the exact
#: source it was produced from; a stem-to-path guess would silently record the
#: wrong file the day a module is renamed.
EVIDENCE_FILES = {
    "test_api_e2e": (
        "tests/e2e/test_api_e2e.py",
        str(REPO / "artifacts" / "acceptance" / "api_e2e.json"),
    ),
    "test_restart_recovery": (
        "tests/e2e/test_restart_recovery.py",
        str(REPO / "artifacts" / "acceptance" / "restart_recovery.json"),
    ),
}

#: Frozen schema id for this evidence format. Bumped when the payload changes
#: shape, so a reader can tell an old artifact from a new one instead of
#: guessing from the presence of a key.
EVIDENCE_SCHEMA = "jcc-e2e-evidence/2"

_LOOPBACK_HOSTS = ("127.0.0.1", "::1", "127.0.0.2")


class Resp:
    """Minimal HTTP response wrapper (stdlib urllib only)."""

    def __init__(self, status: int, headers: dict, body: str) -> None:
        self.status = status
        self.headers = {k.lower(): v for k, v in headers.items()}
        self.body = body

    def json(self):
        return json.loads(self.body) if self.body else None


def _http(method: str, url: str, *, token=None, json_body=None, origin=None,
          headers=None, timeout=10) -> Resp:
    data = json.dumps(json_body).encode("utf-8") if json_body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token is not None:
        req.add_header("X-JCC-Token", token)
    if origin is not None:
        req.add_header("Origin", origin)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return Resp(resp.status, dict(resp.headers), resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return Resp(exc.code, dict(exc.headers), exc.read().decode("utf-8", "replace"))


# --------------------------------------------------------------------------- #
# In-process server (E-02): real app, real uvicorn, random loopback port.
# --------------------------------------------------------------------------- #
class InProcessServer:
    def __init__(self, db_path: str, token: str, cors_origins=None, signing_key=None):
        self.token = token
        self.db_path = db_path
        os.environ[CONTROL_TOKEN_ENV] = token
        if signing_key is not None:
            os.environ[SIGNING_KEY_ENV] = signing_key
        cfg = Config(
            server=ServerConfig(port=0, cors_origins=list(cors_origins or DEFAULT_CORS)),
            state_store_db_path=db_path,
            control_token_env=CONTROL_TOKEN_ENV,
        )
        store = StateStore(db_path)
        engine = CoreEngine(store, build_provider(cfg), Actuator(enabled=False), cfg)
        app = create_app(engine, cfg)
        self.app = app
        self.engine = engine
        self.store = store
        self.cfg = cfg
        # NOTE: this uvicorn build (0.53.0) has no `install_signal_handlers`
        # kwarg; its `capture_signals` already no-ops outside the main thread,
        # so running `server.run()` in a background thread installs nothing.
        uvcfg = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
        self._uv = uvicorn.Server(uvcfg)
        self._thread = threading.Thread(target=self._uv.run, daemon=True)
        self._thread.start()
        deadline = time.time() + 20
        while not self._uv.started and time.time() < deadline:
            time.sleep(0.02)
        if not self._uv.started:
            raise RuntimeError("in-process uvicorn did not start")
        sock = self._uv.servers[0].sockets[0]
        self.host, self.port = sock.getsockname()[0], sock.getsockname()[1]
        self.base_url = f"http://127.0.0.1:{self.port}"

    @property
    def bound_hosts(self):
        return [s.getsockname()[0] for s in self._uv.servers[0].sockets]

    def request(self, method, path, *, token=..., json_body=None, origin=None, headers=None):
        tk = token if token is not ... else self.token
        return _http(method, self.base_url + path, token=tk, json_body=json_body,
                     origin=origin, headers=headers)

    def stop(self):
        try:
            self._uv.should_exit = True
            self._thread.join(timeout=10)
            if self._thread.is_alive():
                self._uv.force_exit = True
                self._thread.join(timeout=5)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Subprocess server (E-04): real `python -m jcc_core.run`, crash + restart.
# --------------------------------------------------------------------------- #
def _free_loopback_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _write_config(path: Path, port: int, db_path: str) -> None:
    cfg = {
        "server": {
            "host": "127.0.0.1",
            "port": port,
            "cors_origins": DEFAULT_CORS,
        },
        "control": {"token_env": CONTROL_TOKEN_ENV},
        "state_store": {"db_path": db_path},
        "jev": {
            "provider": "mock",
            "model": "jev-1.13.0",
            "api_key_env": "JEV_API_KEY",
            "base_url": "https://api.typesafe.ai",
            "feature_flags": {"real_provider": False},
        },
        "actuation": {"enabled": False, "allowed_actions": []},
        "policy": {
            "version": "p3.0.0",
            "priority": ["ESCALATE", "PAUSE", "VERIFY", "STEER", "RETRY", "FINISH", "CONTINUE"],
            "thresholds": {"escalate_risk": 0.8, "pause_risk": 0.6, "verify_risk": 0.4},
        },
        "p3": {"shadow": {"enabled": False}},
    }
    path.write_text(__import__("yaml").safe_dump(cfg), encoding="utf-8")


class SubprocessServer:
    def __init__(self, runtime_dir: str, token: str, port: int):
        self.runtime_dir = runtime_dir
        self.db_path = os.path.join(runtime_dir, "jcc.db")
        self.token = token
        self.port = port
        self.log_path = os.path.join(runtime_dir, "server.log")
        self.cfg_path = os.path.join(runtime_dir, "jcc.yaml")
        self.marker_path = os.path.join(runtime_dir, "JCC-marker")
        self.proc = None
        # config is (re)written on each start() so every launch can use a
        # FRESH loopback port -- avoids racing a still-dying previous process
        # for the same port (and makes "restart" an unambiguous new process).
        _write_config(Path(self.cfg_path), port, self.db_path)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        self.port = _free_loopback_port()
        _write_config(Path(self.cfg_path), self.port, self.db_path)
        env = os.environ.copy()
        env[CONTROL_TOKEN_ENV] = self.token
        env["JCC_CONFIG"] = self.cfg_path
        env["JCC_DB_PATH"] = self.db_path
        env["JCC_PROVIDER"] = "mock"
        env["PYTHONPATH"] = str(PKGS)
        with open(self.log_path, "w", encoding="utf-8") as log:
            self.proc = subprocess.Popen(
                [PY, "-m", "jcc_core.run"], cwd=str(PKGS), env=env,
                stdout=log, stderr=subprocess.STDOUT,
            )
        self._wait_health()

    def _wait_health(self, timeout=30) -> None:
        deadline = time.time() + timeout
        last_err = None
        while time.time() < deadline:
            try:
                r = _http("GET", self.base_url + "/api/health", timeout=3)
                if r.status == 200:
                    return
            except Exception as exc:  # connection refused while starting
                last_err = exc
            time.sleep(0.2)
        raise RuntimeError(f"server not healthy on {self.base_url}: {last_err}")

    def request(self, method, path, *, token=..., json_body=None, origin=None, headers=None):
        tk = token if token is not ... else self.token
        return _http(method, self.base_url + path, token=tk, json_body=json_body,
                     origin=origin, headers=headers)

    def kill_abrupt(self) -> None:
        """Simulate a Windows abrupt kill (TerminateProcess) -- marker survives.

        TerminateProcess is async; we wait (retrying the kill) until the OS
        reports the process dead so the relaunch cannot silently bind the same
        port against a still-living predecessor.
        """
        if self.proc is None or self.proc.poll() is not None:
            return
        for _ in range(25):
            try:
                self.proc.kill()
            except Exception:
                pass
            if self.proc.poll() is not None:
                return
            time.sleep(0.2)
        # last resort: targeted PID kill (never a global /IM python.exe)
        try:
            subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/F"],
                           capture_output=True, timeout=10)
        except Exception:
            pass
        try:
            self.proc.wait(timeout=5)
        except Exception:
            pass

    def alive_pid(self):
        return self.proc.pid if self.proc and self.proc.poll() is None else None

    def read_marker_pid(self):
        try:
            for line in Path(self.marker_path).read_text(encoding="utf-8").splitlines():
                if line.startswith("pid="):
                    return int(line.split("=", 1)[1].strip())
        except Exception:
            return None
        return None

    def cleanup(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.kill()
            except Exception:
                pass
        try:
            import shutil
            shutil.rmtree(self.runtime_dir, ignore_errors=True)
        except Exception:
            pass


def external_ip() -> "str | None":
    """Best-effort non-loopback IP; None when undeterminable."""
    try:
        ip = socket.gethostbyname(socket.gethostname())
        return ip
    except Exception:
        return None


def pid_alive(pid: int) -> bool:
    """Windows-safe liveness probe (mirrors ``jcc_core.run._pid_alive``).

    On Windows ``os.kill(pid, 0)`` is UNSAFE -- it delivers TerminateProcess --
    so we use the read-only Win32 API (OpenProcess + GetExitCodeProcess). This
    lets the E-04 test assert the JCC-marker points at a LIVE process without
    risking killing it. Never raises; a non-running pid returns False.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            STILL_ACTIVE = 259
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.GetExitCodeProcess.restype = wintypes.BOOL
            kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            try:
                code = wintypes.DWORD()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return code.value == STILL_ACTIVE
                return False
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def api_server(tmp_path):
    """Real in-process app on a random loopback port, isolated temp DB."""
    db = tmp_path / "jcc.db"
    srv = InProcessServer(
        str(db),
        "e2e-control-token-xyz",
        cors_origins=DEFAULT_CORS,
        signing_key="e2e-signing-key-32-bytes-long!!",
    )
    yield srv
    srv.stop()


# --------------------------------------------------------------------------- #
# Evidence JSON emission -- written from the REAL run, and ONLY from a real run.
# --------------------------------------------------------------------------- #
_RESULTS = {}


def pytest_runtest_makereport(item, call):
    d = _RESULTS.setdefault(item.nodeid, {})
    d[call.when] = "passed" if call.excinfo is None else "failed"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _collect_only(session) -> bool:
    """True when this session was asked to collect and not to run.

    Read from the config rather than from ``len(_RESULTS)`` alone: both signals
    are used (see ``pytest_sessionfinish``), because a run that dies before the
    first report is indistinguishable from a collect-only pass by ``_RESULTS``
    and must not be allowed to overwrite a good artifact either.
    """
    try:
        return bool(session.config.option.collectonly)
    except AttributeError:
        return False


def _evidence_payload(module_rel: str, checks: list) -> dict:
    """Build the evidence body as a PURE FUNCTION of the product source.

    No clock, no commit id, no pid, no temp path: two runs over the same source
    produce the same bytes. ``checks`` is sorted by node id for the same reason
    -- pytest's item order is an implementation detail of path argument order,
    and evidence that reorders itself between runs is evidence nobody can diff.
    """
    ordered = sorted(checks, key=lambda c: c["node_id"])
    failed = [c for c in ordered if not c["ok"]]
    generator = Path(__file__).resolve()
    module = (REPO / module_rel).resolve()
    return {
        "schema": EVIDENCE_SCHEMA,
        "provenance": {
            "generator": "tests/e2e/conftest.py",
            "generator_sha256": _sha256_file(generator),
            "module": module_rel,
            "module_sha256": _sha256_file(module),
            "command": "pytest tests/e2e",
            "deterministic": True,
        },
        "node_ids": [c["node_id"] for c in ordered],
        "checks": [
            {"name": c["node_id"].split("::")[-1], "node_id": c["node_id"],
             "ok": c["ok"], "detail": c["detail"]}
            for c in ordered
        ],
        "check_count": len(ordered),
        "passed": not failed and len(ordered) > 0,
        "failed": len(failed),
        "status": "PASS" if (not failed and ordered) else "FAIL",
    }


def pytest_sessionfinish(session, exitstatus):
    if _collect_only(session):
        # A collect-only pass runs no test. Writing here would overwrite real
        # evidence with a fabricated all-FAIL verdict -- which is what used to
        # happen, and what made the gate's own collect step destroy the
        # artifacts the gate then judged.
        return
    for mod, (module_rel, path) in EVIDENCE_FILES.items():
        collected = [it for it in session.items if mod in it.nodeid]
        if not collected:
            continue
        checks = []
        for it in collected:
            whens = _RESULTS.get(it.nodeid, {})
            if not whens:
                # No report for this item: the session did not get to it. That
                # is not a FAIL, it is an ABSENCE, and an absence must never be
                # laundered into a verdict. Refuse the whole file.
                print("[jcc-evidence] refusing to write %s: no report recorded for %s"
                      % (os.path.basename(path), it.nodeid))
                checks = []
                break
            ok = all(v == "passed" for v in whens.values())
            bad = [w for w, v in whens.items() if v != "passed"]
            detail = "passed" if ok else "FAILED during: " + ",".join(sorted(bad))
            checks.append({"node_id": it.nodeid, "ok": ok, "detail": detail})
        if not checks:
            continue
        payload = _evidence_payload(module_rel, checks)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")
