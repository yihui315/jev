"""Stdlib ``http.server`` for the 2D Control UI (loopback only).

A :class:`http.server.ThreadingHTTPServer` bound to 127.0.0.1 that renders the
eight screens from LIVE control-plane data via :mod:`control_ui.api_client`. It
holds no control token and never calls a mutating endpoint except the anonymous
Security probe (which deliberately omits the token to prove the gate).

Launch (from ``F:/jev/apps`` so ``control_ui`` resolves):

    F:/jev/.venv/Scripts/python -m control_ui

or point it at an already-running control plane via ``JCC_API_BASE``.
"""
from __future__ import annotations

import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from control_ui import model
from control_ui.api_client import ApiClient
from control_ui.bootstrap import load_config
from control_ui.render import (
    render_audit,
    render_dashboard,
    render_decisions,
    render_health,
    render_notfound,
    render_security,
    render_session_detail,
    render_sessions,
    render_settings,
)

log = logging.getLogger("control_ui.server")


def make_handler(api_base: str, config):
    """Build a request handler bound to a concrete API base + config.

    A factory (not class attributes) so two UI instances in one process never
    share mutable handler state.
    """

    class UIHandler(BaseHTTPRequestHandler):
        # -- plumbing -------------------------------------------------------
        def log_message(self, fmt, *args):  # silence default stderr logging
            log.debug("%s - %s", self.address_string(), fmt % args)

        def _send(self, html: str, status: int = 200) -> None:
            data = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                self.wfile.write(data)
            except BrokenPipeError:
                pass

        def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
            api = ApiClient(api_base)
            parsed = urlparse(self.path)
            path = parsed.path
            try:
                if path in ("/", "/dashboard"):
                    self._send(render_dashboard(model.dashboard_model(api)))
                elif path == "/sessions":
                    self._send(render_sessions(model.sessions_model(api)))
                elif path.startswith("/sessions/"):
                    sid = unquote(path[len("/sessions/"):])
                    self._send(render_session_detail(
                        model.session_detail_model(api, sid), sid))
                elif path == "/decisions":
                    q = parse_qs(parsed.query)
                    sid = q.get("sid", [None])[0]
                    self._send(render_decisions(model.decisions_model(api, sid)))
                elif path == "/security":
                    self._send(render_security(model.security_model(api)))
                elif path == "/settings":
                    self._send(render_settings(model.settings_model()))
                elif path == "/audit":
                    self._send(render_audit(model.audit_model(api)))
                elif path == "/health":
                    self._send(render_health(model.health_model(api)))
                else:
                    self._send(render_notfound(path), status=404)
            except Exception as exc:  # noqa: BLE001 -- render an error page, never crash the server
                log.exception("render failed for %s", path)
                self._send(render_notfound(f"render error: {exc}"), status=500)

    return UIHandler


def serve(api_base: str, config=None, host: str = "127.0.0.1", port: int = 0):
    """Create (but do not start) a loopback UI server.

    ``port=0`` lets the OS assign a free port; read it back via
    ``httpd.server_address[1]`` before calling ``serve_forever``.
    """
    if config is None:
        config = load_config()
    handler = make_handler(api_base, config)
    httpd = ThreadingHTTPServer((host, port), handler)
    return httpd
