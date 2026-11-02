"""Fixture acceptance runner (§7 of FINAL PRODUCT UNBLOCK).

Starts the JEV First-Party Bridge (which writes the ACL-scoped descriptor file at
%LOCALAPPDATA%/WorkBuddy/jev-bridge.json), then drives the REAL production
adapter (discovering the bridge via that descriptor — the actual deployment
shape, no env override) against the final-acceptance fixture project. The bridge's
controlled agent reads README.md and writes output/release_acceptance.txt.

This is the genuine end-to-end proof of the bridge path on the fixture.
"""
from __future__ import annotations

import os
import sys
import time
import threading
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packages"))  # repo/packages
sys.path.insert(0, str(Path(__file__).resolve().parent))              # integrations/jev_bridge/

import uvicorn  # noqa: E402
from jcc_core.workbuddy_bridge import (  # noqa: E402
    BridgeExecutionResult, WorkBuddyBridgeAdapter, WorkbuddyBridgeUnavailable,
)
from bridge_server import build_for_test, DESCRIPTOR_PATH as DEFAULT_DESCRIPTOR  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[2] / ".workbuddy" / "fixtures" / "final_acceptance_project"


def _serve_in_thread(port: int) -> threading.Thread:
    app, _token = build_for_test(port)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    return t, server


def main() -> int:
    if not (FIXTURE / "README.md").is_file():
        print(f"FIXTURE README missing: {FIXTURE}")
        return 2
    port = 18771
    # The bridge writes the descriptor file at the production location.
    app, token = build_for_test(port)
    import json
    from bridge_server import BRIDGE_DIR, INSTANCE_ID
    import getpass
    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_DESCRIPTOR.write_text(json.dumps({
        "port": port, "capability_token": token, "instance_id": INSTANCE_ID,
        "bind": "127.0.0.1", "owner_user": getpass.getuser(), "created_at": time.time(),
    }), encoding="utf-8")

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(200):
        try:
            import httpx
            httpx.get(f"http://127.0.0.1:{port}/jev/v1/health", timeout=0.5)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.05)

    mission_id = "m-fix-" + uuid.uuid4().hex[:8]
    jcc_run_id = "r-fix-" + uuid.uuid4().hex[:8]
    correlation_id = "c-fix-" + uuid.uuid4().hex[:8]

    try:
        # Production discovery: adapter reads the descriptor file (no override).
        adapter = WorkBuddyBridgeAdapter()
        result: BridgeExecutionResult = adapter.run_workload(
            mission_id=mission_id, jcc_run_id=jcc_run_id, correlation_id=correlation_id,
            project_root=str(FIXTURE),
            task="Read README.md and create output/release_acceptance.txt",
        )
    except WorkbuddyBridgeUnavailable as exc:
        print(f"BRIDGE UNAVAILABLE (fail-closed): {exc}")
        return 3

    print("=== BRIDGE FIXTURE ACCEPTANCE ===")
    print(f"MISSION_ID        = {result.mission_id}")
    print(f"JCC_RUN_ID        = {result.jcc_run_id}")
    print(f"WORKBUDDY_WORKLOAD_ID = {result.workbuddy_workload_id}")
    print(f"WORKBUDDY_SESSION_ID = {result.workbuddy_session_id}")
    print(f"STATUS            = {result.status}")
    print(f"ARTIFACT_PATH     = {result.artifact_path}")
    print(f"NATIVE_EVENTS     = {[e['kind'] for e in result.native_events]}")
    print(f"T0-T6             = "
          f"{result.launch_requested_at:.3f} {result.bridge_request_sent_at:.3f} "
          f"{result.workload_created_at:.3f} {result.workload_started_at:.3f} "
          f"{result.first_native_event_at:.3f} {result.artifact_created_at:.3f} "
          f"{result.workload_completed_at:.3f}")

    if result.status != "COMPLETED":
        print("FAILED: bridge did not complete")
        return 4
    artifact = Path(result.artifact_path)
    if not artifact.is_file():
        print("FAILED: artifact missing")
        return 5
    content = artifact.read_text(encoding="utf-8")
    print("=== output/release_acceptance.txt ===")
    print(content)
    server.should_exit = True
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
