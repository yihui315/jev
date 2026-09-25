"""Live-server harness for the Control UI tests and acceptance run.

Builds the REAL FastAPI control-plane app (same code as ``jcc_core.run``) in a
tmp database, and starts it on a random loopback port with ``uvicorn`` — a real
socket, so the UI is driven over real HTTP with ``urllib`` exactly as in
production. Also starts the stdlib UI server against it.

This is test/acceptance infrastructure living under ``apps/control_ui``; it is
not imported by the shipped UI.
"""
from __future__ import annotations

import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Tuple

from control_ui.bootstrap import ensure

ensure()

from jcc_core.actuator import Actuator  # noqa: E402
from jcc_core.config import Config, ServerConfig  # noqa: E402
from jcc_core.core import CoreEngine  # noqa: E402
from jcc_core.providers import build_provider  # noqa: E402
from jcc_core.server import create_app  # noqa: E402
from jcc_core.state_store import StateStore  # noqa: E402


def build_app(tmp_path: Path) -> Tuple[Any, CoreEngine, StateStore, Config]:
    """Construct the real control-plane app over a throwaway database."""
    db_path = str(Path(tmp_path) / "jcc_smoke.db")
    cfg = Config(server=ServerConfig(host="127.0.0.1", port=0), state_store_db_path=db_path)
    store = StateStore(db_path)
    engine = CoreEngine(store, build_provider(cfg), Actuator(enabled=False), cfg)
    app = create_app(engine, cfg)
    return app, engine, store, cfg


def find_free_port() -> int:
    """Bind an ephemeral loopback port and return it (best-effort)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for(url: str, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.HTTPError, urllib.error.URLError, OSError):
            time.sleep(0.1)
    return False


def start_api_server(app, port: int) -> Tuple[Any, threading.Thread]:
    """Start the real FastAPI app on 127.0.0.1:port via uvicorn (background)."""
    import uvicorn

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    if not _wait_for(f"http://127.0.0.1:{port}/api/health"):
        raise RuntimeError(f"API server did not become ready on port {port}")
    return server, thread


def stop_api_server(server, thread=None) -> None:
    # uvicorn's Server has no close(); signalling should_exit lets its serve loop
    # break cleanly. The thread is daemon, but we join to release the port fast.
    server.should_exit = True
    if thread is not None:
        thread.join(timeout=5)


def start_ui_server(api_base: str, port: int):
    """Start the stdlib UI server on 127.0.0.1:port (background)."""
    from control_ui.bootstrap import load_config
    from control_ui.server import serve

    httpd = serve(api_base, load_config(), host="127.0.0.1", port=port)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    if not _wait_for(f"http://127.0.0.1:{port}/"):
        raise RuntimeError(f"UI server did not become ready on port {port}")
    return httpd, thread


def stop_ui_server(httpd) -> None:
    try:
        httpd.shutdown()
    except Exception:
        pass
    try:
        httpd.server_close()
    except Exception:
        pass


def get(url: str, timeout: float = 5.0) -> Tuple[int, str]:
    """Fetch a URL with urllib, returning (status, text)."""
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", "replace")


def post(url: str, body: bytes, timeout: float = 5.0) -> Tuple[int, str]:
    """POST JSON-ish bytes; returns (status, text). Raises HTTPError->caught by caller."""
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
