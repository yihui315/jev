"""JCC Core entrypoint.

Loads configuration, wires the engine (StateStore + provider + actuator),
starts uvicorn on 127.0.0.1 ONLY (fail-closed bind), and writes the
ADR-0006 marker/PID lock under the runtime dir for clean stop/restart/rollback.

Launch:
    F:/jev/.venv/Scripts/python -m jcc_core.run
Env:
    JCC_CONFIG        path to jcc.yaml (default F:/jev/config/jcc.yaml)
    JCC_CONTROL_TOKEN shared secret for mutating control endpoints (REQUIRED for those)
    JCC_PROVIDER      mock | disabled | real (optional override)
    JCC_DB_PATH       override SQLite path (used by tests)
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Optional

PKG = Path(__file__).resolve().parent
if str(PKG.parent) not in sys.path:
    sys.path.insert(0, str(PKG.parent))

from jcc_core.actuator import Actuator  # noqa: E402
from jcc_core.config import load_config  # noqa: E402
from jcc_core.core import CoreEngine  # noqa: E402
from jcc_core.providers import build_provider  # noqa: E402
from jcc_core.server import create_app  # noqa: E402
from jcc_core.state_store import StateStore  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [jcc] %(message)s",
)
log = logging.getLogger("jcc.run")

# Force localhost-only. ADR-0002/0004/0005: never 0.0.0.0.
BIND_HOST = "127.0.0.1"

MARKER_NAME = "JCC-marker"


def _marker_path(config) -> Path:
    return Path(os.path.dirname(config.state_store_db_path)) / MARKER_NAME


def _pid_alive(pid: int) -> bool:
    """Best-effort liveness check for ``pid``. stdlib only; NEVER kills.

    Windows caveat: ``os.kill(pid, 0)`` is UNSAFE -- any signal other than
    CTRL_C_EVENT/CTRL_BREAK_EVENT is delivered via TerminateProcess, so a naive
    ``os.kill(pid, 0)`` would TERMINATE the process. We therefore use the
    read-only Win32 API (OpenProcess + GetExitCodeProcess) on Windows.
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

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.GetExitCodeProcess.restype = wintypes.BOOL
            kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False  # no such process (or not queryable) -> treat as dead
            try:
                code = wintypes.DWORD()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return code.value == STILL_ACTIVE
                return False
            finally:
                kernel32.CloseHandle(handle)
        except Exception:  # noqa: BLE001
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


def read_marker_pid(marker: Path) -> Optional[int]:
    """Parse the ``pid=`` line out of a JCC marker, or None."""
    try:
        for line in marker.read_text(encoding="utf-8").splitlines():
            if line.startswith("pid="):
                return int(line.split("=", 1)[1].strip())
    except Exception:  # noqa: BLE001
        return None
    return None


def reconcile_marker(config) -> Optional[int]:
    """Startup reconciliation for an orphaned ``JCC-marker`` (ADR-0006 sec.4).

    On Windows an abrupt kill is ``TerminateProcess``, so the SIGTERM/SIGINT
    handler never runs and the marker survives pointing at a dead PID -- a
    phantom "running" state. This inspects a pre-existing marker:

      * recorded PID dead (or unparsable) -> STALE: log it and return None so the
        caller overwrites it.
      * recorded PID alive and not us    -> log a warning and return that PID
        (possible second instance). The marker is left untouched.

    Read-only + log-only: never removes a marker owned by a live PID. Returns the
    PID of a still-running previous owner, else None.
    """
    marker = _marker_path(config)
    if not marker.exists():
        return None
    old_pid = read_marker_pid(marker)
    if old_pid and old_pid != os.getpid() and _pid_alive(old_pid):
        log.warning("JCC-marker is owned by a LIVE process pid=%s -- possible second instance", old_pid)
        return old_pid
    log.info("reconciling stale JCC-marker (recorded pid=%s not running) -> overwriting", old_pid)
    return None


def _write_marker(config) -> Path:
    runtime_dir = os.path.dirname(config.state_store_db_path)
    os.makedirs(runtime_dir, exist_ok=True)
    marker = _marker_path(config)
    marker.write_text(
        f"pid={os.getpid()}\ndb={config.state_store_db_path}\nstarted={time.time()}\n",
        encoding="utf-8",
    )
    return marker


def _wire_shadow(engine, store, config) -> None:
    """Composition-root wiring for the P3 shadow plane (ADR-0008 spec §4).

    ``jcc_core`` never imports ``supervisor`` (D-2); this is the ONLY place the
    two planes meet, and it is observe-only (``engine.subscribe``). Inert while
    ``config.raw["p3"]["shadow"]["enabled"]`` is false, so the core server runs
    unchanged before the supervisor package lands.
    """
    sh = (config.raw.get("p3") or {}).get("shadow") or {}
    if not sh.get("enabled"):
        log.info("p3.shadow disabled -- shadow plane not wired")
        return

    # Imported HERE (composition root only) so core never depends on supervisor.
    from supervisor.budget import BudgetGovernor, BudgetLimits
    from supervisor.ports import ShadowActuationPort
    from supervisor.sentinel import Sentinel
    # Signing is a NEUTRAL kernel in jcc_core (spec §2.5 / D-33): the supervisor
    # package has no signing module. Importing it from `supervisor.signing` would
    # hard-fail at activation.
    from jcc_core.signing import DirectiveSigner, load_signing_key

    from jcc_core.decision import ThresholdsConfig

    signing = sh.get("signing") or {}
    thresholds = sh.get("thresholds") or {}
    # R6-1 / F-p3-4: build the threshold surface EXACTLY ONCE and inject the same
    # object into both planes. Previously the configured surface reached only the
    # Sentinel, while `policy.decide` (core.py) fell back to the kernel's
    # module-private defaults — so with a non-empty `per_agent` override the
    # engine executed a DIFFERENT action than the one the shadow plane recorded,
    # making every label describe a policy that never ran.
    threshold_cfg = ThresholdsConfig.from_config(thresholds)
    signer = DirectiveSigner(
        load_signing_key(signing.get("key_env", "JCC_DIRECTIVE_SIGNING_KEY"))
    )
    budget = BudgetGovernor(
        # D-34 / spec §2.4: counters are DERIVED from persisted state (a restart
        # must not reset an escalation), so the governor is given the REAL store.
        # `clock=time.time` is REQUIRED, not cosmetic: StateStore.budget_snapshot
        # reports `first_seen` as a wall-clock epoch second (sessions.created_at)
        # while the governor defaults to time.monotonic — different origins, which
        # would make the wall-clock dimension silently never fire.
        BudgetLimits(**(sh.get("budget") or {})),
        store=store,
        clock=time.time,
    )
    sentinel = Sentinel(
        store,
        signer=signer,
        budget=budget,
        dispatch_port=ShadowActuationPort(),          # D-41: the Sentinel seam
        thresholds=threshold_cfg,                     # the SAME object as below
        policy_version=config.policy.version,
        require_signature=bool(signing.get("required", False)),
    )
    # Hand the engine the identical surface BEFORE it can be driven. Assignment
    # (rather than a constructor argument) is deliberate: `main()` builds the
    # engine before the shadow block is parsed, and while the plane is disabled
    # this line is never reached, leaving `engine.thresholds is None` — i.e. the
    # P0-P2 path is untouched.
    engine.thresholds = threshold_cfg
    engine.subscribe(sentinel.on_event)
    log.info("p3.shadow ENABLED -- Sentinel subscribed (observe-only), "
             "ONE threshold resolver (config_hash=%s)", threshold_cfg.config_hash())


def main() -> None:
    config = load_config()
    if config.server.host != BIND_HOST:
        log.warning("config.server.host=%s overridden to %s (localhost-only)", config.server.host, BIND_HOST)

    store = StateStore(config.state_store_db_path)
    provider = build_provider(config)
    actuator = Actuator(
        enabled=config.actuation.enabled,
        allowed_actions=config.actuation.allowed_actions,
    )
    engine = CoreEngine(store, provider, actuator, config)
    _wire_shadow(engine, store, config)
    app = create_app(engine, config)

    # Reconcile any orphaned marker from a previous abrupt kill BEFORE we claim
    # the marker (ADR-0006 sec.4 "prevents concurrent/orphan runs").
    reconcile_marker(config)
    marker = _write_marker(config)
    log.info("JCC Core starting on %s:%s (provider=%s, actuation=%s, wal=%s)",
             BIND_HOST, config.server.port, provider.name, actuator.enabled, store.is_wal)

    def _cleanup(*_args):
        try:
            marker.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass

    signal.signal(signal.SIGTERM, _cleanup)
    signal.signal(signal.SIGINT, _cleanup)

    import uvicorn

    uvicorn.run(app, host=BIND_HOST, port=config.server.port)


if __name__ == "__main__":
    main()
