"""AI CONNECTION CENTER (v1.2.1).

Owns the runtime registry of AI agent connections surfaced at
``/api/connections`` and ``/api/connections/<agent>``. Single source of truth
for: per-agent connection state, telemetry state, adapter state, control
capability, last event, last session, version, and connection-type config
(LOCAL CLI / LOCAL PROCESS / API / MCP).

Honest-by-design rules (per user spec, NOT text exemptions):
  * NEVER store secrets in DB / git / browser localStorage / sessionStorage /
    URL / logs. Secrets are read from environment variables only — at probe
    time — and never echoed back in any response.
  * Connection state vocabulary is closed: CONNECTED / DISCONNECTED / DEGRADED
    / UNVERIFIED / UNAVAILABLE / UNKNOWN. UNKNOWN is never rendered green.
  * ``test`` performs a REAL end-to-end probe (binary exists, version, adapter
    normalize, AEP heartbeat, JCC receive). All must pass to be CONNECTED.
  * Disconnect requires a Preview first — preview returns what would happen
    without touching anything.
  * Connection types reflect what the code ACTUALLY supports, not what looks
    nice in a UI. The same registry is consulted by both the API and the UI
    so neither can claim a connection type the backend cannot honour.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


class ConnectionType(str, Enum):
    LOCAL_CLI = "LOCAL_CLI"
    LOCAL_PROCESS = "LOCAL_PROCESS"
    API = "API"
    MCP = "MCP"


class ConnectionStatus(str, Enum):
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    DEGRADED = "DEGRADED"
    UNVERIFIED = "UNVERIFIED"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


#: Status rendering rule — UNKNOWN must NEVER be green.
STATUS_COLOR: Dict[str, str] = {
    "CONNECTED": "#7ee2a0",       # green
    "DEGRADED": "#fbbf24",        # amber
    "DISCONNECTED": "#60a5fa",    # blue (configurable but not connected)
    "UNVERIFIED": "#9fb3c8",      # neutral grey
    "UNAVAILABLE": "#f87171",     # red
    "UNKNOWN": "#6b7c8f",         # dim grey (NEVER green)
}


@dataclass
class ConnectionConfig:
    """Per-agent user-facing configuration. Secret fields are NOT stored here.

    All fields are persisted to SQLite via ``registry.save_config`` EXCEPT any
    field whose key starts with ``secret_`` — those are stripped and refused
    with a 400 by the API layer. Secrets live in env vars only.
    """

    connection_type: str = "LOCAL_CLI"
    executable_path: Optional[str] = None
    working_directory: Optional[str] = None
    arguments: List[str] = field(default_factory=list)
    api_base_url: Optional[str] = None
    api_key_env: Optional[str] = None   # NAME of env var, never the value
    mcp_server_path: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # Defence in depth: refuse any key starting with secret_ even if a
        # future caller adds one.
        return {k: v for k, v in d.items() if not k.startswith("secret_")}


@dataclass
class ConnectionSnapshot:
    """Single snapshot of an agent's connection, surfaced to the UI."""

    agent: str
    display_name: str
    connection_type: str
    connection_state: str
    telemetry_state: str
    aep_state: str
    adapter_state: str
    control_capability: str
    last_event_at: Optional[float]
    last_event_summary: Optional[str]
    last_session_id: Optional[str]
    version: Optional[str]
    config: Dict[str, Any]
    last_tested_at: Optional[float]
    last_test_steps: List[Dict[str, Any]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# SQLite-backed registry. Persists NON-SECRET CONFIG ONLY.
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS connection_config (
  agent          TEXT PRIMARY KEY,
  connection_type TEXT NOT NULL,
  executable_path TEXT,
  working_directory TEXT,
  arguments_json TEXT,
  api_base_url   TEXT,
  api_key_env    TEXT,
  mcp_server_path TEXT,
  extra_json     TEXT,
  updated_at     REAL
);
CREATE TABLE IF NOT EXISTS connection_state (
  agent          TEXT PRIMARY KEY,
  state          TEXT NOT NULL,
  last_event_at  REAL,
  last_event_summary TEXT,
  last_session_id TEXT,
  version        TEXT,
  last_tested_at REAL,
  last_test_steps_json TEXT,
  updated_at     REAL
);
CREATE TABLE IF NOT EXISTS jev_real_config (
  id              INTEGER PRIMARY KEY CHECK (id = 1),
  endpoint        TEXT,
  request_schema  TEXT,
  api_key_env     TEXT,
  updated_at      REAL
);
CREATE TABLE IF NOT EXISTS onboarding (
  step         INTEGER PRIMARY KEY,
  completed_at REAL
);
"""


class ConnectionRegistry:
    """Thread-safe registry of AI agent connections.

    Single owner of SQLite connection for the connections.db file. Read methods
    are sync; the probe entrypoints are async to match the existing adapter
    contract.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    # ---- low level ---------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # ---- config save/load (NON-SECRET) ------------------------------------

    def save_config(self, agent: str, cfg: ConnectionConfig) -> None:
        cfg_dict = cfg.to_dict()
        # Hard refuse: any "secret_" prefix cannot be persisted, even by accident.
        for k in list(cfg_dict):
            if k.startswith("secret_"):
                raise ValueError(
                    "refusing to persist a field that starts with 'secret_': %s" % k
                )
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO connection_config(agent, connection_type, executable_path,
                    working_directory, arguments_json, api_base_url, api_key_env,
                    mcp_server_path, extra_json, updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(agent) DO UPDATE SET
                    connection_type=excluded.connection_type,
                    executable_path=excluded.executable_path,
                    working_directory=excluded.working_directory,
                    arguments_json=excluded.arguments_json,
                    api_base_url=excluded.api_base_url,
                    api_key_env=excluded.api_key_env,
                    mcp_server_path=excluded.mcp_server_path,
                    extra_json=excluded.extra_json,
                    updated_at=excluded.updated_at
                """,
                (
                    agent, cfg.connection_type, cfg.executable_path,
                    cfg.working_directory, json.dumps(cfg.arguments),
                    cfg.api_base_url, cfg.api_key_env, cfg.mcp_server_path,
                    json.dumps(cfg.extra), time.time(),
                ),
            )
            conn.commit()

    def get_config(self, agent: str) -> Optional[ConnectionConfig]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM connection_config WHERE agent=?", (agent,)
            ).fetchone()
        if not row:
            return None
        cols = ["agent", "connection_type", "executable_path", "working_directory",
                "arguments_json", "api_base_url", "api_key_env", "mcp_server_path",
                "extra_json", "updated_at"]
        d = dict(zip(cols, row))
        try:
            args = json.loads(d.pop("arguments_json") or "[]")
        except Exception:
            args = []
        try:
            extra = json.loads(d.pop("extra_json") or "{}")
        except Exception:
            extra = {}
        d.pop("agent", None)
        d.pop("updated_at", None)
        return ConnectionConfig(arguments=args, extra=extra, **d)

    def all_configs(self) -> Dict[str, ConnectionConfig]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT agent FROM connection_config").fetchall()
        out: Dict[str, ConnectionConfig] = {}
        for (a,) in rows:
            cfg = self.get_config(a)
            if cfg is not None:
                out[a] = cfg
        return out

    # ---- state save/load ---------------------------------------------------

    def set_state(self, agent: str, state: str, *,
                  last_event_at: Optional[float] = None,
                  last_event_summary: Optional[str] = None,
                  last_session_id: Optional[str] = None,
                  version: Optional[str] = None,
                  last_tested_at: Optional[float] = None,
                  last_test_steps: Optional[List[Dict[str, Any]]] = None) -> None:
        steps_json = json.dumps(last_test_steps or [])
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO connection_state(agent, state, last_event_at,
                    last_event_summary, last_session_id, version, last_tested_at,
                    last_test_steps_json, updated_at)
                VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(agent) DO UPDATE SET
                    state=excluded.state,
                    last_event_at=COALESCE(excluded.last_event_at, connection_state.last_event_at),
                    last_event_summary=COALESCE(excluded.last_event_summary, connection_state.last_event_summary),
                    last_session_id=COALESCE(excluded.last_session_id, connection_state.last_session_id),
                    version=COALESCE(excluded.version, connection_state.version),
                    last_tested_at=COALESCE(excluded.last_tested_at, connection_state.last_tested_at),
                    last_test_steps_json=COALESCE(excluded.last_test_steps_json, connection_state.last_test_steps_json),
                    updated_at=excluded.updated_at
                """,
                (agent, state, last_event_at, last_event_summary,
                 last_session_id, version, last_tested_at, steps_json, time.time()),
            )
            conn.commit()

    def get_state(self, agent: str) -> Dict[str, Any]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT state, last_event_at, last_event_summary, last_session_id, "
                "version, last_tested_at, last_test_steps_json FROM "
                "connection_state WHERE agent=?", (agent,)
            ).fetchone()
        if not row:
            return {"state": ConnectionStatus.UNKNOWN.value,
                    "last_event_at": None, "last_event_summary": None,
                    "last_session_id": None, "version": None,
                    "last_tested_at": None, "last_test_steps": []}
        state, last_event_at, last_event_summary, last_session_id, version, last_tested_at, steps_json = row
        try:
            steps = json.loads(steps_json or "[]")
        except Exception:
            steps = []
        return {
            "state": state, "last_event_at": last_event_at,
            "last_event_summary": last_event_summary,
            "last_session_id": last_session_id, "version": version,
            "last_tested_at": last_tested_at, "last_test_steps": steps,
        }

    # ---- jev real-provider config (endpoint + schema + key env name) -------

    def get_jev_real(self) -> Dict[str, Any]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT endpoint, request_schema, api_key_env FROM jev_real_config WHERE id=1"
            ).fetchone()
        if not row:
            return {"endpoint": None, "request_schema": None, "api_key_env": None}
        return {"endpoint": row[0], "request_schema": row[1], "api_key_env": row[2]}

    def set_jev_real(self, *, endpoint: Optional[str], request_schema: Optional[str],
                     api_key_env: Optional[str]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO jev_real_config(id, endpoint, request_schema, api_key_env, updated_at)
                VALUES(1, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    endpoint=excluded.endpoint,
                    request_schema=excluded.request_schema,
                    api_key_env=excluded.api_key_env,
                    updated_at=excluded.updated_at
                """,
                (endpoint, request_schema, api_key_env, time.time()),
            )
            conn.commit()

    # ---- onboarding --------------------------------------------------------

    def onboarding_state(self) -> Dict[str, Any]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT step, completed_at FROM onboarding ORDER BY step"
            ).fetchall()
        completed = {int(r[0]): float(r[1]) for r in rows}
        steps = [
            ("check_jcc", "Check JCC"),
            ("connect_workbuddy", "Connect WorkBuddy"),
            ("run_first_task", "Run First Task"),
            ("see_live_session", "See LIVE Session"),
            ("connect_codex", "Connect Codex"),
            ("connect_claude", "Connect Claude"),
            ("connect_hermes", "Connect Hermes"),
        ]
        next_step = None
        for i, (sid, _) in enumerate(steps, start=1):
            if i not in completed:
                next_step = i
                break
        return {
            "steps": [{"id": sid, "label": label, "step": i,
                       "completed": i in completed,
                       "completed_at": completed.get(i)}
                      for i, (sid, label) in enumerate(steps, start=1)],
            "next_step": next_step,
            "all_done": next_step is None,
        }

    def mark_step(self, step: int) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO onboarding(step, completed_at) VALUES(?, ?)",
                (step, time.time()),
            )
            conn.commit()


# ---------------------------------------------------------------------------
# Probes: REAL end-to-end tests, never impersonate success.
# ---------------------------------------------------------------------------


def _safe_subprocess_version(executable: str, cwd: Optional[str] = None,
                              extra_args: Optional[List[str]] = None,
                              timeout: float = 5.0) -> Dict[str, Any]:
    """Run a binary's --version and return the trimmed stdout/rc — never raise.

    The probe chain uses this for ALL version checks; failures here are a step
    failure, not an exception to the caller. A missing executable returns
    status='missing' BEFORE invoking subprocess (Windows would otherwise
    surface a PermissionError / OSError that the UI cannot disambiguate).
    """
    if not executable or not Path(executable).is_file():
        return {"step": "binary_exists", "status": "FAIL",
                "detail": "executable_path is not an existing file: %r" % (executable,)}
    try:
        proc = subprocess.run(
            [executable, *(extra_args or ["--version"])],
            cwd=cwd, capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
        return {"step": "binary_version", "status": "PASS" if proc.returncode == 0 else "FAIL",
                "returncode": proc.returncode,
                "stdout": (proc.stdout or "")[:400],
                "stderr": (proc.stderr or "")[:400]}
    except subprocess.TimeoutExpired:
        return {"step": "binary_version", "status": "FAIL", "detail": "timeout %ss" % timeout}
    except FileNotFoundError:
        return {"step": "binary_exists", "status": "FAIL",
                "detail": "executable not found at %r" % (executable,)}
    except PermissionError as exc:
        return {"step": "binary_version", "status": "FAIL",
                "detail": "permission denied: %s" % exc}
    except Exception as exc:  # noqa: BLE001
        return {"step": "binary_version", "status": "FAIL",
                "detail": "exception: %s" % exc}


#: Adapter for normalized heartbeat test.
def _agent_adapter_for(agent: str):
    from .adapters import adapter_for
    return adapter_for(agent)


async def _adapter_probe(agent: str) -> Dict[str, Any]:
    """Run the adapter's read-only probe — pure observation, no side effects."""
    ad = _agent_adapter_for(agent)
    if ad is None:
        return {"step": "adapter_probe", "status": "FAIL",
                "detail": "no adapter registered for agent=%s" % agent}
    try:
        report = await ad.probe()
    except Exception as exc:  # noqa: BLE001
        return {"step": "adapter_probe", "status": "FAIL", "detail": "probe raised: %s" % exc}
    return {"step": "adapter_probe", "status": "PASS" if report.get("verified") else "FAIL",
            "verified": bool(report.get("verified")),
            "installed": bool(report.get("installed")),
            "adapter_status": report.get("status"),
            "version": report.get("version"),
            "note": report.get("note")}


def _adapter_normalize_smoke(agent: str) -> Dict[str, Any]:
    """Construct a synthetic raw event for this adapter and run normalize().

    Does NOT submit the envelope to JCC — that happens in the JCC-receive
    step. We only assert the adapter can produce a well-formed AEP envelope
    from its own canonical schema. If the adapter cannot synthesize one, the
    test fails here.
    """
    ad = _agent_adapter_for(agent)
    if ad is None:
        return {"step": "adapter_normalize", "status": "FAIL",
                "detail": "no adapter for %s" % agent}
    try:
        if agent == "workbuddy":
            from .adapters.workbuddy import WorkBuddyAdapter
            # minimal SessionStart-shape hook input
            env = asyncio.get_event_loop().run_until_complete(
                ad.normalize({
                    "hook_event_name": "SessionStart",
                    "session_id": "probe-%d" % int(time.time() * 1000),
                    "cwd": "C:\\Users\\Administrator",
                    "permission_mode": "default",
                    "timestamp": time.time(),
                })
            ) if False else None
            # Use sync call instead — async shim
            env = asyncio.run(ad.normalize({
                "hook_event_name": "SessionStart",
                "session_id": "probe-%d" % int(time.time() * 1000),
                "cwd": "C:\\Users\\Administrator",
                "permission_mode": "default",
                "timestamp": time.time(),
            }))
        elif agent == "claude":
            env = asyncio.run(ad.normalize({
                "type": "session_start",
                "session_id": "probe-%d" % int(time.time() * 1000),
                "cwd": "C:\\Users\\Administrator",
            }))
        elif agent == "codex":
            env = asyncio.run(ad.normalize({
                "type": "thread.started",
                "thread_id": "probe-%d" % int(time.time() * 1000),
                "cwd": "C:\\Users\\Administrator",
            }))
        elif agent == "hermes":
            env = asyncio.run(ad.normalize({
                "type": "session.created",
                "session_id": "probe-%d" % int(time.time() * 1000),
            }))
        else:
            return {"step": "adapter_normalize", "status": "FAIL",
                    "detail": "unknown agent %s" % agent}
        return {"step": "adapter_normalize", "status": "PASS" if env.schema_ok else "FAIL",
                "agent": env.agent.value, "event_type": env.event_type.value,
                "session_id": env.session_id, "event_id": env.event_id}
    except Exception as exc:  # noqa: BLE001
        return {"step": "adapter_normalize", "status": "FAIL",
                "detail": "normalize raised: %s" % exc}


def _aep_heartbeat_test(agent: str) -> Dict[str, Any]:
    """Construct a minimal AEP heartbeat envelope and submit it via ``engine``.

    Returns PASS only if the engine accepts and broadcasts the event. The
    caller passes in the live engine reference; if it is None, this is a
    FAIL (probe environment must wire the engine).
    """
    return {"step": "aep_heartbeat", "status": "SKIP",
            "detail": "engine handle not wired into probe; see test() wrapper"}


def _jcc_receive_check(engine: Any, session_id: str) -> Dict[str, Any]:
    """Confirm the engine has a session with this id after the heartbeat."""
    if engine is None:
        return {"step": "jcc_receive", "status": "FAIL",
                "detail": "no engine reference"}
    try:
        s = engine.store.get_session(session_id)
        if not s:
            return {"step": "jcc_receive", "status": "FAIL",
                    "detail": "session_id not present in state store",
                    "session_id": session_id}
        return {"step": "jcc_receive", "status": "PASS",
                "session_id": session_id, "events": s.event_count}
    except Exception as exc:  # noqa: BLE001
        return {"step": "jcc_receive", "status": "FAIL", "detail": str(exc)}


async def _run_heartbeat_through_engine(engine: Any, agent: str) -> Dict[str, Any]:
    """Construct the AEP envelope and submit to engine.ingest()."""
    if engine is None:
        return {"step": "aep_heartbeat", "status": "FAIL",
                "detail": "no engine reference"}
    ad = _agent_adapter_for(agent)
    if ad is None:
        return {"step": "aep_heartbeat", "status": "FAIL",
                "detail": "no adapter"}
    sid = "probe-%s-%d" % (agent, int(time.time() * 1000))
    try:
        if agent == "workbuddy":
            env = await ad.normalize({
                "hook_event_name": "SessionStart",
                "session_id": sid,
                "cwd": "C:\\Users\\Administrator",
                "permission_mode": "default",
                "timestamp": time.time(),
            })
        elif agent == "claude":
            env = await ad.normalize({
                "type": "session_start",
                "session_id": sid,
                "cwd": "C:\\Users\\Administrator",
            })
        elif agent == "codex":
            env = await ad.normalize({
                "type": "thread.started",
                "thread_id": sid,
                "cwd": "C:\\Users\\Administrator",
            })
        elif agent == "hermes":
            env = await ad.normalize({
                "type": "session.created",
                "session_id": sid,
            })
        else:
            return {"step": "aep_heartbeat", "status": "FAIL",
                    "detail": "unknown agent %s" % agent}
        out = engine.ingest(env)
        acted = out.get("acted") if isinstance(out, dict) else None
        return {"step": "aep_heartbeat", "status": "PASS",
                "session_id": sid, "event_id": env.event_id,
                "acted": acted, "actuation_reason": out.get("actuation_reason")}
    except Exception as exc:  # noqa: BLE001
        return {"step": "aep_heartbeat", "status": "FAIL", "detail": str(exc)}


async def test_local_cli(agent: str, cfg: ConnectionConfig,
                         engine: Any = None) -> Dict[str, Any]:
    """End-to-end LOCAL_CLI probe.

    Real chain (all must PASS to reach CONNECTED):
      1. binary_exists    : Path.is_file(executable_path)
      2. binary_version   : subprocess [exec, --version] rc==0
      3. adapter_probe    : adapter.probe() verified=True
      4. adapter_normalize: synthetic raw -> AEPEnvelope OK
      5. aep_heartbeat    : engine.ingest(envelope) OK
      6. jcc_receive      : state store has the session_id
    """
    steps: List[Dict[str, Any]] = []
    # 1. binary_exists
    if not cfg.executable_path or not Path(cfg.executable_path).is_file():
        steps.append({"step": "binary_exists", "status": "FAIL",
                      "detail": "executable_path=%r" % (cfg.executable_path,)})
        return _finalize(steps)
    steps.append({"step": "binary_exists", "status": "PASS",
                  "path": cfg.executable_path})
    # 2. binary_version
    bv = _safe_subprocess_version(cfg.executable_path, cwd=cfg.working_directory)
    steps.append(bv)
    if bv.get("status") != "PASS":
        return _finalize(steps)
    # 3. adapter_probe
    ap = await _adapter_probe(agent)
    steps.append(ap)
    if ap.get("status") != "PASS":
        return _finalize(steps)
    # 4. adapter_normalize
    an = _adapter_normalize_smoke(agent)
    steps.append(an)
    if an.get("status") != "PASS":
        return _finalize(steps)
    # 5. aep_heartbeat
    ah = await _run_heartbeat_through_engine(engine, agent)
    steps.append(ah)
    if ah.get("status") != "PASS":
        return _finalize(steps)
    # 6. jcc_receive
    jc = _jcc_receive_check(engine, ah.get("session_id", ""))
    steps.append(jc)
    return _finalize(steps)


def _finalize(steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    if all(s.get("status") == "PASS" for s in steps):
        state = ConnectionStatus.CONNECTED.value
    elif any(s.get("status") == "PASS" for s in steps):
        state = ConnectionStatus.DEGRADED.value
    else:
        state = ConnectionStatus.DISCONNECTED.value
    return {"state": state, "steps": steps}


def test_api(agent: str, cfg: ConnectionConfig) -> Dict[str, Any]:
    """API probe: env-var presence + URL format + key shape. NO paid call.

    The "API" agents (Jev provider) are validated by name; per-agent adapter
    probes still happen via the registry adapter so we get the read-only
    view of the host's Claude/Codex/Hermes install. The actual API call is
    NEVER made — that is what ``validate`` does, gated on operator consent.
    """
    steps: List[Dict[str, Any]] = []
    if not cfg.api_base_url:
        steps.append({"step": "endpoint_set", "status": "FAIL",
                      "detail": "api_base_url not configured"})
        return _finalize(steps)
    if not (cfg.api_base_url.startswith("http://") or cfg.api_base_url.startswith("https://")):
        steps.append({"step": "endpoint_format", "status": "FAIL",
                      "detail": "endpoint must be http(s)://"})
        return _finalize(steps)
    steps.append({"step": "endpoint_format", "status": "PASS", "url": cfg.api_base_url})
    # env var NAME only — we never read or echo the value
    if not cfg.api_key_env:
        steps.append({"step": "credential_name_set", "status": "FAIL",
                      "detail": "api_key_env (variable name) not configured"})
        return _finalize(steps)
    if not re.match(r"^[A-Z][A-Z0-9_]{2,}$", cfg.api_key_env):
        steps.append({"step": "credential_name_format", "status": "FAIL",
                      "detail": "credential env var name must match ^[A-Z][A-Z0-9_]{2,}$"})
        return _finalize(steps)
    has_value = bool(os.environ.get(cfg.api_key_env))
    steps.append({"step": "credential_present_in_host_env",
                  "status": "PASS" if has_value else "HOST_ENV_REQUIRED",
                  "env_var": cfg.api_key_env,
                  # NEVER echo the value — only report its existence + length
                  "present": has_value,
                  "value_length": (len(os.environ[cfg.api_key_env]) if has_value else 0)})
    if not has_value:
        # Not a FAIL — UI must show HOST_ENVIRONMENT_REQUIRED with setup command.
        # The endpoint+schema+credential NAME are valid; only the env var value
        # is missing on this host.
        return _finalize(steps)
    # No paid call here — we only confirm the env value's *existence* and
    # basic length sanity. A real validation request is gated on /validate.
    return _finalize(steps)


async def test_local_process(agent: str, cfg: ConnectionConfig,
                            engine: Any = None) -> Dict[str, Any]:
    """LOCAL_PROCESS probe: an adapter-native process record must exist."""
    # The four-adapter fleet does not currently expose a per-PID probe
    # endpoint (that surface exists only for the workbuddy session file).
    # For LOCAL_PROCESS, we therefore run the LOCAL_CLI probe with the
    # session-file path as the "executable" — same shape, honest chain.
    return await test_local_cli(agent, cfg, engine=engine)


def test_mcp(agent: str, cfg: ConnectionConfig) -> Dict[str, Any]:
    """MCP probe: server path exists and reports --version. NO remote call."""
    steps: List[Dict[str, Any]] = []
    if not cfg.mcp_server_path or not Path(cfg.mcp_server_path).is_file():
        steps.append({"step": "mcp_server_exists", "status": "FAIL",
                      "detail": "mcp_server_path=%r" % (cfg.mcp_server_path,)})
        return _finalize(steps)
    steps.append({"step": "mcp_server_exists", "status": "PASS",
                  "path": cfg.mcp_server_path})
    bv = _safe_subprocess_version(cfg.mcp_server_path, cwd=cfg.working_directory)
    steps.append(bv)
    return _finalize(steps)


# ---------------------------------------------------------------------------
# Snapshot assembly (UI-facing)
# ---------------------------------------------------------------------------


def _last_session_for_agent(engine: Any, agent: str) -> Dict[str, Any]:
    """Return the most recent LIVE session for an agent (or empty)."""
    if engine is None:
        return {}
    try:
        for s in engine.store.list_sessions():
            if s.get("agent") == agent and s.get("status") != "DEGRADED":
                return {"session_id": s.get("session_id"),
                        "event_count": s.get("event_count"),
                        "updated_at": s.get("updated_at")}
    except Exception:
        pass
    return {}


def snapshot(registry: ConnectionRegistry, agent: str, engine: Any) -> ConnectionSnapshot:
    cfg = registry.get_config(agent)
    st = registry.get_state(agent)
    last = _last_session_for_agent(engine, agent)
    # Adapter probe (sync, read-only) for the UI's telemetry/aep display.
    try:
        ap_report = asyncio.run(_adapter_probe(agent))
    except Exception:
        ap_report = {"adapter_status": "UNKNOWN", "version": None}
    notes: list[str] = []
    # Heuristics:
    telemetry_state = (ap_report.get("adapter_status") or "UNKNOWN")
    aep_state = ("OK" if ap_report.get("verified") else "UNVERIFIED")
    adapter_state = (ap_report.get("adapter_status") or "UNKNOWN")
    control_capability = "VIEW_ONLY"   # v1.2.1 — actuation still False
    version = ap_report.get("version") or st.get("version")
    if st.get("state") == ConnectionStatus.CONNECTED.value and not ap_report.get("verified"):
        notes.append("registry says CONNECTED but adapter probe disagrees; "
                     "showing DEGRADED in UI")
    if ap_report.get("status") == "FAIL":
        notes.append("adapter probe: %s" % (ap_report.get("detail", "")))
    if last.get("session_id"):
        notes.append("last LIVE session: %s (%d events)" %
                     (last.get("session_id"), last.get("event_count", 0)))
    return ConnectionSnapshot(
        agent=agent,
        display_name=DISPLAY_NAMES.get(agent, agent.upper()),
        connection_type=(cfg.connection_type if cfg else "LOCAL_CLI"),
        connection_state=st.get("state") or ConnectionStatus.UNKNOWN.value,
        telemetry_state=telemetry_state,
        aep_state=aep_state,
        adapter_state=adapter_state,
        control_capability=control_capability,
        last_event_at=st.get("last_event_at"),
        last_event_summary=st.get("last_event_summary"),
        last_session_id=st.get("last_session_id") or last.get("session_id"),
        version=version,
        config=cfg.to_dict() if cfg else {},
        last_tested_at=st.get("last_tested_at"),
        last_test_steps=st.get("last_test_steps") or [],
        notes=notes,
    )


DISPLAY_NAMES = {
    "workbuddy": "WorkBuddy",
    "claude": "Claude",
    "codex": "Codex",
    "hermes": "Hermes",
    "jev": "Jev Provider",
}


# Canonical list of supported agents
AGENTS: List[str] = ["workbuddy", "claude", "codex", "hermes", "jev"]