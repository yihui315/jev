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


def _write_marker(config) -> Path:
    runtime_dir = os.path.dirname(config.state_store_db_path)
    os.makedirs(runtime_dir, exist_ok=True)
    marker = Path(runtime_dir) / "JCC-marker"
    marker.write_text(
        f"pid={os.getpid()}\ndb={config.state_store_db_path}\nstarted={time.time()}\n",
        encoding="utf-8",
    )
    return marker


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
    app = create_app(engine, config)

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
