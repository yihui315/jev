"""JCC v1.3 Visual Command Center -- production SPA server (ASGI / uvicorn).

WHY THIS FILE WAS REWRITTEN (v1.3 Wave 8)
-----------------------------------------
It used to be a ``http.server.HTTPServer`` + ``SimpleHTTPRequestHandler``. That is
the standard library's *demo* server: single-threaded, no HTTP/1.1 keep-alive
correctness for concurrent asset loads, no graceful shutdown, no access to the
connection lifecycle, and it is explicitly documented by Python as "not
recommended for production, only for basic testing".

Wave 8's brief is that the product runtime must not be ``python http.server``.
So the SPA is now served by a real ASGI application run under **uvicorn** -- the
same server already running the control-plane API, so there is ONE HTTP runtime in
the product instead of two.

CONTRACT (unchanged from the v1.2 server, so every operator script keeps working)
---------------------------------------------------------------------------------
  * loopback-only bind (127.0.0.1) -- never 0.0.0.0. Refused at startup.
  * ``GET /health`` and ``GET /api/health`` -> 200 JSON, including the port.
  * any real file under ``--dist`` is served with its own content type.
  * any other path -> ``index.html`` (SPA fallback). The app is hash-routed
    (``#/missions``), so this only matters for a hard refresh on ``/``.
  * path traversal outside ``dist`` is impossible: the resolved target must stay
    inside the dist root, else the request falls back to index.html.
  * no control token is held here; no secrets; it never touches GREEN 4778.

OPTIONAL AUTO CONTROL TOKEN (off by default)
--------------------------------------------
Set ``JCC_UI_AUTO_TOKEN=1`` **and** ``JCC_CONTROL_TOKEN=<token>`` to have this
server answer ``GET /api/config`` with that token, so the UI can auto-fill the
control-token field instead of the operator pasting it (see
``src/lib/autoToken.ts``). Rationale and limits:

  * it is OPT-IN -- with the flag unset the endpoint reports ``None`` and nothing
    about the shipped behaviour changes;
  * the token is never written into ``dist/`` (a build artifact) or into browser
    storage, only handed to a page that this loopback-only server just served;
  * this is defensible only because the product is loopback-only and
    single-operator: it must stay that way, and the flag must stay off in any
    deployment that is not.

Usage:
  python serve_visual.py [--host 127.0.0.1] [--port 4780] [--dist <dir>]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.routing import Route

#: The only host this server will bind. A non-loopback value is refused below.
LOOPBACK = "127.0.0.1"

#: Loopback aliases that are still "local only" and therefore acceptable.
_LOOPBACK_ALIASES = {"127.0.0.1", "localhost", "::1"}


def _safe_target(dist: Path, rel: str) -> Path | None:
    """Resolve ``rel`` under ``dist``, or None when it escapes / does not exist.

    ``Path.resolve()`` is not enough on its own: on Windows a URL segment can
    still carry a drive letter or an UNC prefix. The final containment check is
    the actual gate, and it is done on resolved paths so a symlink cannot be used
    to climb out of the dist root.
    """
    rel = (rel or "").lstrip("/\\")
    if not rel:
        return None
    try:
        candidate = (dist / rel).resolve()
        root = dist.resolve()
    except (OSError, ValueError):
        return None
    if candidate == root or root not in candidate.parents:
        return None
    return candidate if candidate.is_file() else None


def build_app(dist_dir: str, port: int, auto_token: str | None = None) -> Starlette:
    dist = Path(dist_dir).resolve()
    index = dist / "index.html"

    async def health(_request):
        return JSONResponse({
            "status": "ok",
            "service": "jcc-v1.3-visual-ui",
            "runtime": "uvicorn-asgi",
            "port": port,
            "dist": str(dist),
        })

    async def config(_request):
        """Report the auto control token, or ``null`` when the feature is off.

        ``None`` is the honest answer whenever the operator did not opt in, so a
        client cannot tell "disabled" from "this server is older" -- and does not
        need to, because both mean the same thing: prompt the human.
        """
        return JSONResponse({"controlToken": auto_token})

    async def spa(request):
        """Serve a real file, else fall back to the SPA shell."""
        target = _safe_target(dist, request.path_params.get("path") or "")
        if target is not None:
            return FileResponse(target)
        if index.is_file():
            return FileResponse(index, media_type="text/html; charset=utf-8")
        return PlainTextResponse(
            "index.html not found in the dist directory; build the UI first "
            "(npm ci && npm run build)\n", status_code=500)

    routes = [
        Route("/health", health),
        Route("/api/health", health),
        # Auto control token (opt-in). Same origin as the SPA, so no CORS entry.
        Route("/api/config", config),
        # Catch-all LAST: Starlette matches in order, so the routes above win.
        Route("/{path:path}", spa),
    ]
    return Starlette(routes=routes, debug=False)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="JCC v1.3 Visual Command Center SPA server")
    ap.add_argument("--host", default=LOOPBACK)
    ap.add_argument("--port", type=int, default=4780)
    ap.add_argument("--dist", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "dist"))
    ns = ap.parse_args(argv)

    if ns.host not in _LOOPBACK_ALIASES:
        sys.stderr.write(
            "[visual-ui] REFUSED: --host %r is not loopback. This server only ever "
            "binds %s; the product is local-only.\n" % (ns.host, LOOPBACK))
        return 3

    if not os.path.isdir(ns.dist) or not os.path.isfile(os.path.join(ns.dist, "index.html")):
        sys.stderr.write("[visual-ui] dist directory (or index.html) not found: %s\n" % ns.dist)
        sys.stderr.write("[visual-ui] build it first: cd apps/command_center_3d && npm ci && npm run build\n")
        return 2

    import uvicorn

    # OPT-IN: serve the control token only when explicitly enabled. Default off,
    # so the shipped behaviour is unchanged.
    auto_on = os.environ.get("JCC_UI_AUTO_TOKEN", "") == "1"
    if auto_on:
        auto_token = os.environ.get("JCC_CONTROL_TOKEN") or None
    else:
        auto_token = None
    if auto_on and not auto_token:
        sys.stderr.write(
            "[visual-ui] JCC_UI_AUTO_TOKEN=1 but JCC_CONTROL_TOKEN is unset; "
            "/api/config will report no token\n")

    app = build_app(ns.dist, ns.port, auto_token)
    sys.stderr.write(
        "[visual-ui] JCC v1.3 Visual Command Center -> http://%s:%d/\n"
        "[visual-ui] runtime: uvicorn (ASGI); serving dist: %s\n"
        "[visual-ui] loopback only; does NOT touch GREEN 4778\n"
        "[visual-ui] auto control token: %s\n"
        % (ns.host, ns.port, ns.dist,
           "ON (from JCC_CONTROL_TOKEN)" if auto_token else "off"))
    sys.stderr.flush()

    uvicorn.run(app, host=ns.host, port=ns.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
