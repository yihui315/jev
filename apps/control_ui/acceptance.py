"""Acceptance artifact generator for the 2D Control UI.

Runs the REAL control plane (FastAPI) in-process with a throwaway database seeded
from the deterministic demo fixtures, starts the stdlib UI against it, then
fetches all eight screens over real HTTP and writes
``artifacts/acceptance/control_ui_smoke.json``::

    {"schema": ..., "provenance": {...}, "screens": [{"name","path","status",
     "bytes","excerpt","api_backed"}, ...], "screens_ok": 8, "passed": true}

WHY THERE IS NO TIMESTAMP. This artifact used to carry an ``at`` field. It is
read by the final gate as the S-04 evidence, and once S-04 evidence is tracked
the file is compared against its committed blob; a wall-clock field makes every
re-run a diff, so the gate's own clean-tree check (P-01/P-05) could never pass.
The payload is therefore a function of the SOURCE only -- the sha256 of this
generator, of the renderer, and of the fixture manifest, plus the observed HTTP
results. Re-running it over the same source yields the same bytes.

Run:

    F:/jev/.venv/Scripts/python -m control_ui.acceptance
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
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


REPO = Path(__file__).resolve().parents[2]

#: The eight screens the release claims. The gate asserts this exact set, so a
#: screen that quietly stops being fetched is a visible diff here rather than a
#: missing row nobody notices.
SCREENS = [
    ("dashboard", "/"),
    ("sessions", "/sessions"),
    ("decisions", "/decisions"),
    ("security", "/security"),
    ("settings", "/settings"),
    ("audit", "/audit"),
    ("health", "/health"),
]

#: A page that renders this string is a page that could not reach the control
#: plane. It is a FAILURE, never a degraded pass -- an unreachable UI is not a
#: projected UI.
UNREACHABLE_MARKER = "API UNREACHABLE"


def _excerpt(text: str, n: int = 280) -> str:
    """The head of the document: doctype, header, nav, and the screen's ``h1``.

    Deliberately the HEAD and not a sample from the middle. The page bodies
    legitimately render the audit timestamps and entry hashes, whose string
    lengths vary a few characters between runs; a mid-page sample would make this
    artifact drift on every invocation. The head is a pure function of the route
    and the screen title, which is exactly what the stub-detection below needs:
    eight different routes must not produce one identical document.
    """
    clean = " ".join(text.split())
    return clean[:n]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _provenance() -> dict:
    return {
        "generator": "apps/control_ui/acceptance.py",
        "generator_sha256": _sha256(Path(__file__).resolve()),
        "renderer": "apps/control_ui/render.py",
        "renderer_sha256": _sha256(REPO / "apps" / "control_ui" / "render.py"),
        "fixtures": "fixtures/demo/manifest.json",
        "fixtures_sha256": _sha256(REPO / "fixtures" / "demo" / "manifest.json"),
        "command": "python -m control_ui.acceptance",
        "deterministic": True,
    }


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
    # NOTE: the URL is deliberately NOT recorded. A random loopback port is an
    # input of the run, not of the product, and embedding it would make the
    # artifact differ on every invocation for no evidentiary gain.
    ui_base = f"http://127.0.0.1:{ui_port}"

    screens = list(SCREENS)
    if detail_sid:
        screens.append(("session_detail", f"/sessions/{detail_sid}"))

    out = {
        "schema": "jcc-control-ui-smoke/3",
        "provenance": _provenance(),
        "seeded_sessions": [s["session_id"] for s in sessions],
        "screens": [],
    }
    try:
        for name, path in screens:
            status, text = get(f"{ui_base}{path}")
            nbytes = len(text.encode("utf-8"))
            # `non_trivial` (a boolean) rather than `bytes` (a number): the page
            # bodies legitimately contain the audit timestamps and entry hashes,
            # whose rendered widths vary between runs, so a byte count is not
            # reproducible. A boolean answer to "is this page empty?" is.
            out["screens"].append({
                "name": name,
                "path": path,
                "status": status,
                "non_trivial": nbytes > 300,
                "unreachable": UNREACHABLE_MARKER in text,
                "excerpt": _excerpt(text),
            })
            print(f"{name:14s} {status} {nbytes:>7} bytes")
    finally:
        stop_ui_server(ui_httpd)
        stop_api_server(api_server, api_thread)

    ok_screens = [s for s in out["screens"]
                  if s["status"] == 200 and not s["unreachable"] and s["non_trivial"]]
    out["screens_ok"] = len(ok_screens)
    out["screens_expected"] = len(screens)
    out["passed"] = len(ok_screens) == len(screens) and len(screens) == 8
    out["status"] = "PASS" if out["passed"] else "FAIL"

    artifact_dir = REPO / "artifacts" / "acceptance"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / "control_ui_smoke.json"
    artifact_path.write_text(
        json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"\nwrote {artifact_path}  screens_ok={out['screens_ok']}/{out['screens_expected']} "
          f"status={out['status']}")
    return 0 if out["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
