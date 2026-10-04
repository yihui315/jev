"""JCC Core entrypoint.

Loads configuration, wires the engine (StateStore + provider + actuator),
starts uvicorn on 127.0.0.1 ONLY (fail-closed bind), and writes the
ADR-0006 marker/PID lock under the runtime dir for clean stop/restart/rollback.

The loopback rule is a REFUSAL, not a preference (P4 #77.2): a configured
``server.host`` that is not loopback makes this entrypoint exit non-zero before
it builds or binds anything. A configuration that is silently "corrected" behind
a log line is a fail-open control.

The GREEN port rule is the same shape (CR-002): ``127.0.0.1:4778`` belongs to the
frozen GREEN baseline, so this entrypoint refuses to start a NON-GREEN instance
there. See :func:`check_green_port_ownership`.

Launch:
    F:/jev/.venv/Scripts/python -m jcc_core.run
Env:
    JCC_CONFIG        path to the config to run. Defaults to the v1.3 PRODUCT
                      config (config/jcc-v1.3.yaml, port 4779). The GREEN
                      baseline is a separate instance and must be named
                      explicitly (config/jcc.yaml, port 4778) -- in practice
                      via scripts/start.ps1, which does that for you.
    JCC_CONTROL_TOKEN shared secret for mutating control endpoints (REQUIRED for those)
    JCC_PROVIDER      mock | disabled | real (optional override)
    JCC_DB_PATH       override SQLite path (used by tests)
"""
from __future__ import annotations

import logging
import os
import signal
import socket
import sys
import time
from pathlib import Path
from typing import Optional

PKG = Path(__file__).resolve().parent
if str(PKG.parent) not in sys.path:
    sys.path.insert(0, str(PKG.parent))

from jcc_core.actuator import Actuator  # noqa: E402
from jcc_core.config import (  # noqa: E402
    DEFAULT_CONFIG_PATH,
    GREEN_PROTECTED_PORT,
    load_config,
)
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

#: Addresses the entrypoint is willing to BIND. Anything else (0.0.0.0, a routable
#: LAN address) is refused, not quietly corrected (P4 #77.2).
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "127.0.0.2", "localhost", "::1", "[::1]"})

MARKER_NAME = "JCC-marker"


def check_bind_host(config) -> None:
    """Fail-closed loopback guard for the SERVER ENTRYPOINT (P4 #77.2).

    ADR-0002/0004/0005: ``0.0.0.0`` is forbidden. This turns that rule into a
    refusal instead of a preference.

    The previous behaviour logged a warning and started anyway on
    ``BIND_HOST``. That is a **fail-open** control: the operator who asked for
    an exposed listener (or who pasted ``host: 0.0.0.0`` into ``jcc.yaml`` as a
    guess) gets a process that starts, reports healthy on ``/api/health``, and
    is reachable from the network -- the warning goes to a log nobody reads, and
    a supervisor restarts it straight back into the same exposure.

    Raises ``SystemExit`` (exit code 1, so a supervised restart does not loop on
    a permanently invalid configuration) BEFORE the store, the provider, the
    socket, or the marker are touched.
    """
    host = str(getattr(getattr(config, "server", None), "host", "") or "").strip()
    if host and host not in LOOPBACK_HOSTS:
        allowed = ", ".join(sorted(LOOPBACK_HOSTS))
        raise SystemExit(
            f"refusing to start: server.host={host!r} is not loopback; JCC Core "
            f"is a localhost-only control plane (ADR-0002/0004/0005). Set "
            f"server.host to one of {allowed}, or remove the key -- the entrypoint "
            f"binds {BIND_HOST!r} regardless of what is configured."
        )


def _resolve_port_via_getaddrinfo(port) -> Optional[int]:
    """Resolve a NON-NUMERIC port the way ``bind()`` would. Read-only.

    Returns the numeric port ``port`` denotes, or None when it denotes nothing
    bindable. ``socket.bind((host, "jev-green"))`` is legal and goes through
    ``getaddrinfo``, which accepts both numbers and service names -- so asking
    the same question the same way is what keeps this guard and the actual bind
    from disagreeing.

    Loopback only, and it opens nothing: ``getaddrinfo`` is a name lookup.
    """
    if isinstance(port, (bytes, bytearray)):
        try:
            port = port.decode("ascii")
        except (UnicodeDecodeError, AttributeError):
            return None
    if not isinstance(port, str) or not port.strip():
        return None
    try:
        infos = socket.getaddrinfo(
            BIND_HOST, port.strip(), type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, ValueError, OSError):
        return None
    for info in infos:
        sockaddr = info[4]
        if sockaddr and isinstance(sockaddr[1], int):
            return int(sockaddr[1])
    return None


def check_green_port_ownership(config) -> None:
    """Fail-closed: only the GREEN baseline itself may hold GREEN's port (CR-002).

    Master directive 0.2 makes ``127.0.0.1:4778`` a protected baseline: read and
    observe only, never stop/kill/rewrite/migrate/mutate. A product instance that
    binds it does not merely risk a conflict -- it makes the port *look* healthy
    while serving v1.3, so a probe cannot tell the baseline from an impostor, and
    every GREEN-protection assertion downstream becomes unfalsifiable.

    This is the same shape as :func:`check_bind_host`, and for the same reason: a
    control that only warns is fail-open. A supervisor would restart the process
    straight back into the violation.

    IDENTITY IS THE CONFIG FILE, NOT A FLAG. The tempting implementation is a
    ``--edition green`` switch or a ``JCC_INSTANCE`` env var, but then GREEN only
    starts if whoever launched it remembered to pass the flag -- and the failure
    mode is GREEN refusing to come up, which is worse than the bug being fixed.
    GREEN already declares itself: ``scripts/start.ps1`` sets
    ``JCC_CONFIG=config/jcc.yaml`` explicitly, and no GREEN launch path reads the
    default. So the file that was loaded IS the identity, GREEN keeps working
    with no change to its launcher, and a product instance pointed at 4778 by a
    typo, a copied config, or a stale default is refused.

    Non-zero exit, before the store/provider/socket/marker are touched.

    THE PORT IS COMPARED AS AN INT, NOT AS-LOADED (verified bypass, 2026-10-04).
    The first version of this guard read ``config.server.port`` and compared it
    to ``GREEN_PROTECTED_PORT`` directly. ``ServerConfig`` is a bare dataclass and
    ``load_config`` splats ``**data["server"]`` straight into it, so YAML type
    survives all the way to the bind. A config with a QUOTED

        server:
          port: '4778'          # a str, not an int

    therefore produced ``port == '4778'``, and ``'4778' != 4778`` is True, so the
    guard returned early and the process went on to bind GREEN's port. Measured
    before the fix: ``port='4778' -> REFUSED=False`` while ``port=4778 ->
    REFUSED=True``. The quoted form is exactly what a hand-edited or
    template-copied config tends to produce, and the failure is invisible: the
    impostor then answers ``/api/health`` with 200.

    Fixing this at the COMPARISON rather than only in ``load_config`` is
    deliberate. Normalising in the loader would leave every other reader of
    ``config.server.port`` -- ``Get-JccPort`` in ``scripts/lib/JccCommon.ps1``,
    which regex-matches ``^\\d+$``, and uvicorn's own bind -- still seeing
    whatever the YAML said. Coercing here means the guard asks "is this process
    asking for port 4778?" and cannot be fooled by how the number was spelled.

    ``int()`` also collapses ``4778.0`` and ``" 4778 "``, which is why the float
    form is refused too.

    WHAT IS *NOT* PROVEN BY "int() FAILED, THEREFORE IT IS NOT 4778" (residual
    risk found by metric-verify, closed here). ``socket.bind((host, port))``
    accepts a STRING port and resolves it through ``getaddrinfo``, which also
    accepts a SERVICE NAME. So a config carrying ``port: jev-green`` would pass
    an ``int()``-based guard, and on a host where some service name happens to be
    registered against 4778 it would still land on the protected port. Measured
    on this host: ``getservbyport(4778)`` raises ``OSError`` and none of the
    plausible names resolve to 4778 -- so the hole is not reachable HERE, but
    that is a property of this machine's service database, not of this code.

    Rather than trust the host, a non-numeric port is now RESOLVED and refused if
    it lands on the protected port. Resolution is done by asking the OS the same
    way ``bind`` will, so the two cannot disagree. The lookup is read-only, and
    if resolution itself fails the value is left alone -- an unresolvable port
    cannot be bound, so it fails at bind time with an error the operator can see.
    """
    port = getattr(getattr(config, "server", None), "port", None)
    try:
        port_number = int(port)
    except (TypeError, ValueError):
        # Not a number. It still might be a SERVICE NAME that resolves to the
        # protected port, so ask the OS exactly as bind() would.
        port_number = _resolve_port_via_getaddrinfo(port)
        if port_number is None:
            # Unresolvable => cannot be bound => not the protected port. Do not
            # invent a verdict here; uvicorn will reject it with a real message.
            return
    if port_number != GREEN_PROTECTED_PORT:
        return
    if getattr(config, "is_green_instance", False):
        return
    raise SystemExit(
        f"refusing to start: server.port={GREEN_PROTECTED_PORT} is the GREEN "
        f"baseline's protected port (master directive 0.2 -- read/observe only, "
        f"never stop/kill/rewrite/migrate/mutate), and this process is NOT the "
        f"GREEN instance. It was started from "
        f"{getattr(config, 'config_path', '') or '<built-in defaults>'}"
        f"{' (explicitly named)' if getattr(config, 'config_is_explicit', False) else ' (default)'}"
        f". A product instance on this port would be indistinguishable from the "
        f"baseline to a health probe and would invalidate every GREEN-protection "
        f"assertion. Use the product port (4779 via config/jcc-v1.3.yaml), or, "
        f"if you genuinely intend to start GREEN, use its own launcher "
        f"scripts/start.ps1 (or set JCC_CONFIG to config/jcc.yaml)."
    )


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
    # SERVER ENTRYPOINT guard (ADR-0008 D-30 / F-p3-5). ``load_config`` tolerates a
    # MISSING default file so the library stays usable without a filesystem, but a
    # SERVER must never come up on built-in defaults: that is a silent downgrade to
    # a policy nobody chose. Only the entrypoint can say "do not start", so the
    # refusal lives here. Non-zero exit (SystemExit("msg") == code 1), per the
    # Windows-safe-ops rule that a failed script must not exit 0.
    if not os.environ.get("JCC_CONFIG") and not os.path.exists(DEFAULT_CONFIG_PATH):
        raise SystemExit(
            f"refusing to start: no config at {DEFAULT_CONFIG_PATH} and JCC_CONFIG "
            "is unset; built-in defaults are not an acceptable server configuration "
            "(fail-closed, ADR-0008 D-30)"
        )
    config = load_config()
    # LOOPBACK-ONLY, fail-closed (P4 #77.2). Refuse a non-loopback request here,
    # before anything is built or bound; anything loopback-shaped binds BIND_HOST
    # unconditionally. See `check_bind_host`.
    check_bind_host(config)
    # GREEN PORT OWNERSHIP, fail-closed (CR-002). Only the GREEN baseline may
    # hold 4778. Same placement rationale: before the store, the provider, the
    # socket, or the marker. See `check_green_port_ownership`.
    check_green_port_ownership(config)

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
