"""Entry point: ``python -m control_ui`` (run from ``F:/jev/apps``).

Starts the stdlib loopback UI server against the control plane named by
``JCC_API_BASE`` (default http://127.0.0.1:4778). The UI is read-only and never
holds the control token.
"""
from __future__ import annotations

import sys

from control_ui.bootstrap import ensure, load_config, resolve_api_base
from control_ui.server import serve


def main() -> None:
    ensure()
    api_base = resolve_api_base()
    config = load_config()
    httpd = serve(api_base, config, host="127.0.0.1", port=0)
    port = httpd.server_address[1]
    print(
        f"JCC Control UI  ->  http://127.0.0.1:{port}\n"
        f"Control plane   ->  {api_base}\n"
        f"(loopback only; read-only; no control token held)",
        file=sys.stderr,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
