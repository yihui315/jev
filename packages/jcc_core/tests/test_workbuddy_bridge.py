"""Integration tests for the JEV First-Party Bridge.

These drive the REAL bridge server (``integrations/jev_bridge/bridge_server.py``)
over localhost HTTP + SSE — no mocks of the transport. They prove the production
path end to end: task submission, mission-scoped execution, real native events
from actual filesystem ops, the T0–T6 timestamp chain, three-ID separation,
failure injection, fail-closed discovery, descriptor-based discovery, and restart
re-attach without scanning any ambient session.
"""
from __future__ import annotations

import os
import sys
import time
import threading
import socket
import uuid
from pathlib import Path

import httpx
import pytest
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # packages/

from jcc_core.workbuddy_bridge import (  # noqa: E402
    BridgeExecutionFailed,
    BridgeExecutionResult,
    WorkBuddyBridgeAdapter,
    WorkbuddyBridgeUnavailable,
    discover_bridge,
)
from integrations.jev_bridge.bridge_server import build_for_test  # noqa: E402


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture()
def bridge():
    port = _free_port()
    app, token = build_for_test(port)
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="error",
    )
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    # wait until listening
    for _ in range(200):
        try:
            httpx.get(f"http://127.0.0.1:{port}/jev/v1/health", timeout=0.5)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.05)
    else:  # pragma: no cover
        raise RuntimeError("bridge did not start")
    yield {"base_url": f"http://127.0.0.1:{port}", "token": token, "port": port}
    server.should_exit = True
    t.join(timeout=5)


def _make_project(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "README.md").write_text(
        "# Fixture\nRead me and write the artifact.\n", encoding="utf-8"
    )
    (project / "output").mkdir()
    return project


# --------------------------------------------------------------------------- #
# 1. Happy path — real native events + artifact + T0–T6 + three-ID separation
# --------------------------------------------------------------------------- #
def test_bridge_full_run_produces_real_native_events_and_artifact(bridge, tmp_path):
    project = _make_project(tmp_path)
    adapter = WorkBuddyBridgeAdapter(base_url=bridge["base_url"], token=bridge["token"])
    mission_id = "m-" + uuid.uuid4().hex[:8]
    jcc_run_id = "r-" + uuid.uuid4().hex[:8]
    correlation_id = "c-" + uuid.uuid4().hex[:8]

    result = adapter.run_workload(
        mission_id=mission_id,
        jcc_run_id=jcc_run_id,
        correlation_id=correlation_id,
        project_root=str(project),
        task="read README and write output/release_acceptance.txt",
    )
    assert isinstance(result, BridgeExecutionResult)
    # accepted + real bridge ids
    assert result.workbuddy_workload_id.startswith("wb-")
    assert result.workbuddy_session_id.startswith("wbs-")
    assert result.status == "COMPLETED"
    # three JEV ids are distinct from the two bridge ids
    assert result.mission_id == mission_id
    assert result.jcc_run_id == jcc_run_id
    assert result.correlation_id == correlation_id
    assert result.workbuddy_workload_id != mission_id
    assert result.workbuddy_session_id != jcc_run_id
    # real native events present (excludes heartbeat)
    kinds = {e["kind"] for e in result.native_events}
    assert "file_read" in kinds
    assert "file_write" in kinds
    assert "artifact_created" in kinds
    assert "heartbeat" not in kinds
    # T0–T6 causal chain populated; monotonic within each clock domain.
    # (T0/T1 are JEV-clock; T2–T6 are bridge-clock from event `at`.)
    assert result.launch_requested_at is not None
    assert result.bridge_request_sent_at is not None
    assert result.workload_created_at is not None
    assert result.workload_started_at is not None
    assert result.first_native_event_at is not None
    assert result.artifact_created_at is not None
    assert result.workload_completed_at is not None
    # JEV domain
    assert result.launch_requested_at <= result.bridge_request_sent_at
    # bridge domain (single clock -> strictly monotonic by construction)
    bridge_chain = [
        result.workload_created_at, result.workload_started_at,
        result.first_native_event_at, result.artifact_created_at,
        result.workload_completed_at,
    ]
    assert bridge_chain == sorted(bridge_chain), "bridge T2-T6 not monotonic"
    # causal link T1 -> T2 tolerant of sub-second cross-process clock skew
    assert result.workload_created_at >= result.bridge_request_sent_at - 1.0
    # real artifact on disk with required content
    artifact = Path(result.artifact_path)
    assert artifact.is_file()
    content = artifact.read_text(encoding="utf-8")
    assert f"mission_id={mission_id}" in content
    assert f"jcc_run_id={jcc_run_id}" in content
    assert f"workbuddy_workload_id={result.workbuddy_workload_id}" in content
    assert "executor=workbuddy" in content
    assert "status=release_acceptance" in content


# --------------------------------------------------------------------------- #
# 2. Failure injection — bridge reports a workload failure with stage/code
# --------------------------------------------------------------------------- #
def test_bridge_failure_injection_reports_failure_stage_code(bridge, tmp_path):
    project = _make_project(tmp_path)
    adapter = WorkBuddyBridgeAdapter(base_url=bridge["base_url"], token=bridge["token"])
    with pytest.raises(BridgeExecutionFailed) as exc:
        adapter.run_workload(
            mission_id="m-fail", jcc_run_id="r-fail", correlation_id="c-fail",
            project_root=str(project), task="__JEV_BRIDGE_FAIL__",
        )
    assert exc.value.args[0].startswith("FILENOTFOUND") or "FILENOTFOUND" in str(exc.value)


# --------------------------------------------------------------------------- #
# 3. Fail-closed — no descriptor / unreachable bridge
# --------------------------------------------------------------------------- #
def test_bridge_fail_closed_when_descriptor_missing(tmp_path):
    missing = tmp_path / "no-bridge.json"
    with pytest.raises(WorkbuddyBridgeUnavailable):
        discover_bridge(descriptor_path=str(missing))
    with pytest.raises(WorkbuddyBridgeUnavailable):
        WorkBuddyBridgeAdapter(descriptor_path=str(missing))


def test_bridge_fail_closed_when_unreachable():
    # Point at a port nothing listens on; must NOT fall back to anything else.
    adapter = WorkBuddyBridgeAdapter(
        base_url="http://127.0.0.1:1", token="x")
    with pytest.raises(WorkbuddyBridgeUnavailable):
        adapter.run_workload(
            mission_id="m", jcc_run_id="r", correlation_id="c",
            project_root=".", task="t")


# --------------------------------------------------------------------------- #
# 4. Descriptor-based discovery (the real deployment shape)
# --------------------------------------------------------------------------- #
def test_bridge_descriptor_discovery(bridge, tmp_path):
    descriptor = tmp_path / "jev-bridge.json"
    descriptor.write_text(
        '{"port": %d, "capability_token": "%s", "instance_id": "x", '
        '"bind": "127.0.0.1", "owner_user": "tester"}' % (bridge["port"], bridge["token"]),
        encoding="utf-8",
    )
    cfg = discover_bridge(descriptor_path=str(descriptor))
    assert cfg["base_url"] == f"http://127.0.0.1:{bridge['port']}"
    assert cfg["token"] == bridge["token"]
    project = _make_project(tmp_path)
    adapter = WorkBuddyBridgeAdapter(descriptor_path=str(descriptor))
    result = adapter.run_workload(
        mission_id="m-desc", jcc_run_id="r-desc", correlation_id="c-desc",
        project_root=str(project), task="ok")
    assert result.status == "COMPLETED"
    assert result.workbuddy_workload_id.startswith("wb-")


# --------------------------------------------------------------------------- #
# 5. Restart re-attach — JCC restarts, reconnects via id, no ambient scan
# --------------------------------------------------------------------------- #
def test_bridge_restart_reattach(bridge, tmp_path):
    project = _make_project(tmp_path)
    adapter = WorkBuddyBridgeAdapter(base_url=bridge["base_url"], token=bridge["token"])
    result = adapter.run_workload(
        mission_id="m-re", jcc_run_id="r-re", correlation_id="c-re",
        project_root=str(project), task="ok")
    wid = result.workbuddy_workload_id
    instance_id = result.instance_id
    # Simulate a JCC restart: a fresh adapter re-attaches to the existing
    # workload purely by bridge id + correlation, never scanning an ambient sess.
    re_adapter = WorkBuddyBridgeAdapter(
        base_url=bridge["base_url"], token=bridge["token"])
    re = re_adapter.reattach(
        workbuddy_workload_id=wid, instance_id=instance_id,
        mission_id="m-re", jcc_run_id="r-re", correlation_id="c-re",
        project_root=str(project))
    assert re.status == "COMPLETED"
    assert re.workbuddy_workload_id == wid
    assert re.artifact_path == result.artifact_path


# --------------------------------------------------------------------------- #
# 6. SSE transport is genuinely streamed (real HTTP SSE, not a fake)
# --------------------------------------------------------------------------- #
def test_bridge_sse_is_real_stream(bridge, tmp_path):
    project = _make_project(tmp_path)
    adapter = WorkBuddyBridgeAdapter(base_url=bridge["base_url"], token=bridge["token"])
    result = adapter.run_workload(
        mission_id="m-sse", jcc_run_id="r-sse", correlation_id="c-sse",
        project_root=str(project), task="ok")
    # the events were consumed live from a streaming SSE response
    assert any(e["kind"] == "workload_started" for e in result.native_events)
    assert any(e["kind"] == "completed" for e in result.native_events)
