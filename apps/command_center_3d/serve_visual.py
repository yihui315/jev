"""JCC v1.2 Visual Command Center -- production static SPA server.

Serves the built SPA from apps/command_center_3d/dist/ on 127.0.0.1:4780.

This is the ONLY supported way to run the Visual UI in production. The Vite
dev server (npm run dev, port 4781) is for local debugging ONLY and must never
be used as the served UI, because it holds an open websocket/HMR channel and is
not a durable, cache-friendly static surface.

Design:
  * loopback-only bind (127.0.0.1) -- never 0.0.0.0
  * SPA fallback: any route without a matching file falls back to index.html
    so client-side routing works on hard refresh
  * /health returns 200 JSON (consumed by scripts\status-jcc-v1.2.ps1)
  * pure stdlib http.server -- no third-party dependencies, no build step

Usage:
  python serve_visual.py [--host 127.0.0.1] [--port 4780] [--dist <dir>]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))


class SpaHandler(SimpleHTTPRequestHandler):
    # Serve from dist/; SPA fallback to index.html for unknown non-asset routes.

    def __init__(self, *args, directory: str = ".", **kwargs):
        self._directory = directory
        super().__init__(*args, directory=directory, **kwargs)

    def end_headers(self) -> None:
        # loopback-only SPA; harmless permissive CORS for same-machine fetches.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/health" or path == "/api/health":
            self._send_json(200, {
                "status": "ok",
                "service": "jcc-v1.2-visual-ui",
                "port": getattr(self.server, "jcc_port", 4780),
            })
            return

        # Resolve the file on disk.
        rel = path.lstrip("/")
        candidate = os.path.normpath(os.path.join(self._directory, rel))
        # Prevent path traversal outside dist/.
        if not candidate.startswith(os.path.abspath(self._directory)):
            self._send_index()
            return

        if os.path.isfile(candidate):
            return super().do_GET()

        # SPA fallback: unknown route -> index.html (extensionless or unknown).
        self._send_index()

    def _send_index(self) -> None:
        index_path = os.path.join(self._directory, "index.html")
        try:
            with open(index_path, "rb") as f:
                body = f.read()
        except FileNotFoundError:
            self.send_error(404, "index.html not found in dist/")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # quiet
        sys.stderr.write("[visual-ui] " + (fmt % args) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=4780)
    ap.add_argument("--dist", default=os.path.join(HERE, "dist"))
    ns = ap.parse_args()

    if not os.path.isdir(ns.dist):
        sys.stderr.write(f"[visual-ui] dist directory not found: {ns.dist}\n")
        sys.stderr.write("[visual-ui] build it first: npm ci && npm run build\n")
        return 2

    handler = partial(SpaHandler, directory=ns.dist)
    httpd = HTTPServer((ns.host, ns.port), handler)
    httpd.jcc_port = ns.port  # type: ignore[attr-defined]
    sys.stderr.write(
        f"[visual-ui] JCC v1.2 Visual Command Center -> http://{ns.host}:{ns.port}/\n"
        f"[visual-ui] serving dist: {ns.dist}\n"
        f"[visual-ui] loopback only; no control token held; does NOT touch GREEN 4778\n"
    )
    sys.stderr.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
