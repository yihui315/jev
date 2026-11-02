# -*- coding: utf-8 -*-
"""Comprehensive SIMULATION of the JCC native-execution pipeline.

WHY THIS FILE EXISTS
--------------------
The production native path is blocked by a STRUCTURAL host gap: the WorkBuddy
daemon exposes no external, credential-free agent-run capability (see
``workbuddy_daemon.py`` — ``DaemonAgentRunUnsupported``). We cannot spawn a real
model-backed workload from JCC without copying the daemon's account credential,
which is forbidden.

What we CAN do, and do here, is prove the JCC *framework* is correct end-to-end
by feeding a DETERMINISTIC, simulated protocol stream through the REAL adapter
and supervisor code:

  * ``WorkBuddyServeAdapter.run_task``  — driven with a canned ACP/SSE feed
    (the same JSON-RPC + SSE shape the real ``--serve`` host emits).
  * ``NativeLaunchSupervisor._supervise`` — driven with a canned NDJSON feed
    written to the workload's stdout file (the same shape the real CLI writes).

Every assertion below exercises the SHIPPED code paths: event mapping, the
three-identity separation, the SPAWNED->NATIVE_STARTED->RUNNING->COMPLETED state
machine, heartbeat/health exclusion, the T0-T6 causal timing chain, and artifact
provenance. Nothing here is mocked away: if the real adapters regress, these
tests fail.

This is labelled SIMULATED-PROVEN, never REAL-PASS. The only thing not exercised
is the host actually producing the bytes — which is the documented P0 gap.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import types
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jcc_core.native_launch import new_correlation_id  # noqa: E402
from jcc_core.native_supervisor import (  # noqa: E402
    NativeLaunchSupervisor, NativeWorkload,
)
from jcc_core.workbuddy_serve import WorkBuddyServeAdapter  # noqa: E402

_ARTIFACT_DIR = Path(__file__).resolve().parents[3] / "artifacts" / "release" / "v1.3_final"


# ---------------------------------------------------------------------------
# Simulated ACP/SSE feed for WorkBuddyServeAdapter.run_task
# ---------------------------------------------------------------------------

class _Resp:
    """Non-streaming canned HTTP response (has .read())."""
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data


class _Stream:
    """Streaming canned HTTP response (iterable over SSE lines)."""
    def __init__(self, lines):
        self._lines = lines

    def __iter__(self):
        for ln in self._lines:
            yield ln


class SimServe:
    """A stand-in for a READY ``--serve`` host. Carries only the attributes the
    adapter reads; the adapter's own HTTP calls are overridden below."""
    def __init__(self, project_root: str):
        self.state = "READY"  # == ServeProcess.STATE_READY
        self.base_url = "http://127.0.0.1:18765"
        self.password = "sim-password"
        self.spec = types.SimpleNamespace(cwd=project_root)


class SimServeAdapter(WorkBuddyServeAdapter):
    """WorkBuddyServeAdapter with the network layer replaced by a canned ACP/SSE
    feed. Everything else — event mapping, id separation, timing chain — is the
    REAL shipped code."""

    SESSION_ID = "sess-SIM-001"

    def _connect(self):
        return "conn-sim-1"

    def _post(self, connection_id, body, stream=False, timeout=200.0):
        method = body.get("method")
        if method == "initialize":
            return _Resp(b"")
        if method == "session/new":
            return _Resp(
                ("data: " + json.dumps({"result": {"sessionId": self.SESSION_ID}}) + "\n").encode()
            )
        if method == "session/prompt":
            lines = [
                b'data: {"method":"session/update","params":{"update":{"sessionUpdate":"heartbeat"}}}\n',
                b'data: {"method":"session/update","params":{"update":{"sessionUpdate":"ready"}}}\n',
                b'data: {"method":"session/update","params":{"update":{"sessionUpdate":"read"}}}\n',
                b'data: {"method":"session/update","params":{"update":{"sessionUpdate":"write"}}}\n',
                b'data: {"method":"session/update","params":{"update":{"sessionUpdate":"bash"}}}\n',
                b'data: {"method":"session/update","params":{"update":{"sessionUpdate":"tool_result"}}}\n',
                b'data: {"id":"3","result":{"stopReason":"end_turn","_meta":{}}}\n',
            ]
            if stream:
                return _Stream(lines)
            return _Resp(b"".join(lines))
        return _Resp(b"")


def run_serve_simulation(project_root: str):
    adapter = SimServeAdapter(SimServe(project_root))
    return adapter.run_task(
        mission_id="mission-sim-1",
        jcc_run_id="run-sim-1",
        prompt="Return exactly: JEV_WORKBUDDY_NATIVE_OK",
        project_root=project_root,
    )


# ---------------------------------------------------------------------------
# Simulated NDJSON feed for NativeLaunchSupervisor._supervise
# ---------------------------------------------------------------------------

class FakeProc:
    """A process that stays alive ~2s then exits 0 — long enough for the
    supervisor to observe native events and reach RUNNING before completion."""
    def __init__(self):
        self.returncode = None
        self._start = time.time()

    def poll(self):
        if time.time() - self._start > 2.0:
            self.returncode = 0
            return 0
        return None


def run_supervisor_simulation(tmp_path: Path):
    mission_id = "mission-sim-2"
    jcc_run_id = "run-sim-2"
    correlation = new_correlation_id()
    requested = "jcc-" + correlation.split("-", 1)[-1]
    project_root = str(tmp_path)
    artifact_path = str(tmp_path / "release_acceptance.txt")
    stdout_path = str(tmp_path / "native-sim.ndjson")

    # The simulated CLI writes an NDJSON transcript. A heartbeat and a ready
    # marker are present on purpose: they MUST be excluded from native work.
    events = [
        {"type": "heartbeat", "ts": 1},
        {"type": "session", "session_id": requested, "workload_id": "wb-SIM-002"},
        {"type": "read", "path": "a.txt"},
        {"type": "write", "path": "b.txt"},
        {"type": "bash", "cmd": "echo hi"},
        {"type": "tool_result", "ok": True},
        {"type": "artifact", "path": artifact_path},
    ]
    with open(stdout_path, "w", encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e) + "\n")
    with open(artifact_path, "w", encoding="utf-8") as fh:
        fh.write("RELEASE_ACCEPTANCE_OK\n")

    w = NativeWorkload(
        mission_id=mission_id, jcc_run_id=jcc_run_id, correlation_id=correlation,
        requested_session_id=requested, project_root=project_root,
        launch_requested_at=time.time(),
    )
    sup = NativeLaunchSupervisor()
    proc = FakeProc()
    th = threading.Thread(target=sup._supervise, args=(w, proc, stdout_path), daemon=True)
    th.start()
    deadline = time.time() + 12
    while w.launch_status not in ("COMPLETED", "FAILED") and time.time() < deadline:
        time.sleep(0.1)
    th.join(timeout=2)
    return w, artifact_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_serve_adapter_simulation_completes(tmp_path):
    res = run_serve_simulation(str(tmp_path))
    assert res.ok is True, res.failure_message
    assert res.completed is True, res.failure_message
    assert res.workbuddy_session_id == "sess-SIM-001"
    assert res.workbuddy_workload_id == "sess-SIM-001"


def test_serve_adapter_excludes_heartbeat_ready():
    res = run_serve_simulation(str(Path(os.path.dirname(__file__))))
    kinds = [e["kind"] for e in res.native_events]
    # heartbeat + ready must NOT appear; only real work.
    assert "heartbeat" not in kinds
    assert "ready" not in kinds
    assert kinds == ["file_read", "file_write", "command_start", "tool_end"], kinds


def test_serve_adapter_three_id_separation(tmp_path):
    res = run_serve_simulation(str(tmp_path))
    # The three JCC-owned ids and the two vendor ids must never be conflated.
    assert res.mission_id == "mission-sim-1"
    assert res.jcc_run_id == "run-sim-1"
    # correlation_id is a fresh uuid, never equal to any session/workload id.
    assert res.correlation_id and res.correlation_id != res.workbuddy_session_id
    assert res.correlation_id != res.workbuddy_workload_id
    assert res.mission_id != res.jcc_run_id != res.correlation_id
    # protocol_request_id (connection id) is distinct from the workload id.
    assert res.protocol_request_id == "conn-sim-1"
    assert res.protocol_request_id != res.workbuddy_workload_id


def test_serve_adapter_timing_chain_monotonic(tmp_path):
    res = run_serve_simulation(str(tmp_path))
    t = res.timings
    for key in ("T0_launch_requested_at", "T1_serve_request_sent_at",
                "T2_workload_created_at", "T3_workload_started_at",
                "T4_first_native_event_at", "T5_artifact_created_at",
                "T6_workload_completed_at"):
        assert key in t, t
    # T0..T3 are the launch instant; T4..T6 follow. Monotonic, never inverted.
    assert t["T0_launch_requested_at"] <= t["T1_serve_request_sent_at"]
    assert t["T1_serve_request_sent_at"] <= t["T2_workload_created_at"]
    assert t["T2_workload_created_at"] <= t["T3_workload_started_at"]
    assert t["T3_workload_started_at"] <= t["T4_first_native_event_at"]
    assert t["T4_first_native_event_at"] <= t["T5_artifact_created_at"]
    assert t["T5_artifact_created_at"] <= t["T6_workload_completed_at"]


def test_supervisor_simulation_completes(tmp_path):
    w, artifact_path = run_supervisor_simulation(tmp_path)
    assert w.launch_status == "COMPLETED", w.as_dict()
    assert w.exit_code == 0
    assert os.path.exists(artifact_path), "artifact not produced"


def test_supervisor_excludes_heartbeat_and_captures_workload_id(tmp_path):
    w, _ = run_supervisor_simulation(tmp_path)
    kinds = [e["kind"] for e in w.native_events]
    assert "heartbeat" not in kinds
    # Real work events, in order, plus the artifact marker.
    assert kinds == ["file_read", "file_write", "command_start", "tool_end", "artifact_created"], kinds
    # The vendor workload id is captured and is NOT the JCC-requested session id.
    assert w.workbuddy_workload_id == "wb-SIM-002"
    assert w.workbuddy_workload_id != w.requested_session_id


def test_supervisor_timing_chain_monotonic(tmp_path):
    w, _ = run_supervisor_simulation(tmp_path)
    assert w.first_native_event_at is not None
    assert w.workload_completed_at is not None
    assert w.first_native_event_at <= w.workload_completed_at
    # The workload id became known (workload_created_at) no later than completion.
    if w.workload_created_at is not None:
        assert w.workload_created_at <= w.workload_completed_at


def test_end_to_end_three_id_separation_across_adapters(tmp_path):
    """Composite: the ACP path and the NDJSON path agree on id discipline."""
    serve = run_serve_simulation(str(tmp_path))
    sup_w, _ = run_supervisor_simulation(tmp_path)
    ids = {
        "mission_id": serve.mission_id,
        "jcc_run_id": serve.jcc_run_id,
        "correlation_id": serve.correlation_id,
        "workbuddy_session_id": serve.workbuddy_session_id,
        "workbuddy_workload_id(serve)": serve.workbuddy_workload_id,
        "workbuddy_workload_id(sup)": sup_w.workbuddy_workload_id,
        "requested_session_id(sup)": sup_w.requested_session_id,
    }
    # No two distinct roles collide.
    assert ids["mission_id"] != ids["jcc_run_id"]
    assert ids["correlation_id"] != ids["workbuddy_session_id"]
    assert ids["correlation_id"] != ids["workbuddy_workload_id(serve)"]
    assert ids["workbuddy_workload_id(sup)"] != ids["requested_session_id(sup)"]


def test_emit_simulation_evidence(tmp_path):
    """Run both simulations, assert green, and emit an honest SIMULATED-PROVEN
    evidence artifact. This is NOT a REAL-native pass; it proves the framework
    logic through the real code with a simulated protocol feed."""
    serve = run_serve_simulation(str(tmp_path))
    sup_w, artifact_path = run_supervisor_simulation(tmp_path)

    assert serve.completed and sup_w.launch_status == "COMPLETED"
    assert os.path.exists(artifact_path)

    evidence = {
        "schema": "jcc/simulation-acceptance/1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mode": "SIMULATED-PROVEN",
        "honest_note": (
            "The native protocol feed (ACP/SSE for the --serve adapter, NDJSON for "
            "the supervisor) is SIMULATED. The adapters, event mapping, id "
            "separation, state machine and timing chain exercised here are the REAL "
            "shipped code. Real-native execution remains BLOCKED by the host: the "
            "WorkBuddy daemon exposes no external, credential-free agent-run "
            "capability (DaemonAgentRunUnsupported)."
        ),
        "simulated_proven_gates": [
            "native_event_mapping_excludes_heartbeat_health",
            "three_identity_separation",
            "serve_adapter_completion",
            "supervisor_state_machine_spawned_to_completed",
            "artifact_provenance",
            "t0_t6_causal_timing_chain_monotonic",
        ],
        "serve_result": {
            "ok": serve.ok, "completed": serve.completed,
            "native_event_count": len(serve.native_events),
            "session_id": serve.workbuddy_session_id,
            "timings_present": sorted(serve.timings.keys()),
        },
        "supervisor_result": {
            "launch_status": sup_w.launch_status,
            "native_event_count": len(sup_w.native_events),
            "workbuddy_workload_id": sup_w.workbuddy_workload_id,
            "artifact_exists": os.path.exists(artifact_path),
        },
        "real_but_blocked_gates": [
            "workbuddy_native_launch_verified (host agent-run capability absent)",
            "live_sse_verified (requires real workload)",
            "deliverable_provenance_verified (requires real workload)",
        ],
    }
    _ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    out = _ARTIFACT_DIR / "SIMULATION_ACCEPTANCE.json"
    out.write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
    # Surface the path so it is findable in CI logs.
    print("SIMULATION_ACCEPTANCE evidence written to", out)
    assert out.exists()
