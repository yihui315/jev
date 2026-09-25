"""Acceptance artifact generator for the 2D Control UI.

Runs the REAL control plane (FastAPI) in-process with a throwaway database seeded
from the deterministic demo fixtures, starts the stdlib UI against it, then
fetches all eight screens over real HTTP and writes
``artifacts/acceptance/control_ui_smoke.json``:

    {"at": <iso>, "screens": [{"name","url","status","bytes","excerpt"}, ...]}

Run:

    F:/jev/.venv/Scripts/python -m control_ui.acceptance
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from control_ui._harness import (
    build_app,
    find_free_port,
    get,
    start_api_server,
    start_ui_server,
    stop_api_server,
    stop_ui_server,
)
from control_ui.bootstrap import ensure

ensure()

from fixtures.demo.load import load_demo_envelopes  # noqa: E402
from jcc_core.schemas import AEPEnvelope  # noqa: E402


SCREENS = [
    ("dashboard", "/"),
    ("sessions", "/sessions"),
    ("decisions", "/decisions"),
    ("security", "/security"),
    ("settings", "/settings"),
    ("audit", "/audit"),
    ("health", "/health"),
]


def _excerpt(text: str, n: int = 280) -> str:
    clean = " ".join(text.split())
    return clean[:n]


def main() -> int:
    os.environ.setdefault("JCC_CONTROL_TOKEN", "acceptance-token")
    tmp = Path(tempfile.mkdtemp(prefix="jcc-ui-acc-"))
    app, engine, _store, _cfg = build_app(tmp)

    # Seed the real chain from the deterministic demo fixtures.
    for env in load_demo_envelopes():
        engine.ingest(AEPEnvelope(**env))

    sessions = engine.store.list_sessions()
    detail_sid = sessions[0]["session_id"] if sessions else None

    api_port = find_free_port()
    api_server, api_thread = start_api_server(app, api_port)
    api_base = f"http://127.0.0.1:{api_port}"

    ui_port = find_free_port()
    ui_httpd, _ = start_ui_server(api_base, ui_port)
    ui_base = f"http://127.0.0.1:{ui_port}"

    screens = list(SCREENS)
    if detail_sid:
        screens.append(("session_detail", f"/sessions/{detail_sid}"))

    out = {"at": datetime.now(timezone.utc).isoformat(), "screens": []}
    try:
        for name, path in screens:
            status, text = get(f"{ui_base}{path}")
            out["screens"].append({
                "name": name,
                "url": f"{ui_base}{path}",
                "status": status,
                "bytes": len(text.encode("utf-8")),
                "excerpt": _excerpt(text),
            })
            print(f"{name:14s} {status} {len(text):>7} bytes")
    finally:
        stop_ui_server(ui_httpd)
        stop_api_server(api_server, api_thread)

    artifact_dir = Path(__file__).resolve().parents[2] / "artifacts" / "acceptance"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / "control_ui_smoke.json"
    artifact_path.write_text(json.dumps(out, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nwrote {artifact_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
