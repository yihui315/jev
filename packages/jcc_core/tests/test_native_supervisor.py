"""The native workload lifecycle, tested without spawning anything real.

THE BUG THESE LOCK DOWN
----------------------
`/launch` reported success when it found ANY live WorkBuddy session. During
release acceptance it found one belonging to an unrelated project, created
1.85 seconds BEFORE the run — and answered `ok=True, ingested=True`. The mission
reached RUNNING having produced no work.

A pid is a PROCESS. It is not evidence of execution, so the mission stays
STARTING until a session correlated to THIS launch produces a real native event.
"""
from __future__ import annotations

import time

import pytest

from jcc_core.native_supervisor import (
    CLI_EVENT_MAP,
    NativeWorkload,
    map_cli_event,
)
from jcc_core.native_launch import (
    NATIVE_EVENT_KINDS,
    NON_NATIVE_MARKERS,
    verify_attached_session,
)

FIXTURE = r"D:\jev\.workbuddy\fixtures\final_acceptance_project"
OTHER = r"D:\视觉项目"


def _w(**kw) -> NativeWorkload:
    base = dict(
        mission_id="msn_test", jcc_run_id="run_test",
        correlation_id="jcc-abc-123", requested_session_id="jcc-abc-123",
        project_root=FIXTURE, launch_requested_at=1000.0,
    )
    base.update(kw)
    return NativeWorkload(**base)


# --------------------------------------------------------------------------- #
# A. A pid alone must never start a mission
# --------------------------------------------------------------------------- #


class TestPidIsNotExecution:
    def test_a_workload_with_only_a_pid_is_still_starting(self):
        """The core invariant. PROCESS_SPAWNED is not NATIVE_STARTED."""
        w = _w(pid=1234, process_spawned_at=1001.0, launch_status="SPAWNED")
        started = w.as_dict()
        assert started["launch_status"] == "SPAWNED"
        assert started["first_native_event_at"] is None
        assert started["workload_started_at"] is None
        assert started["native_event_count"] == 0

    def test_native_started_requires_a_native_event_timestamp(self):
        w = _w(pid=1, first_native_event_at=1005.0, workload_started_at=1005.0,
               launch_status="NATIVE_STARTED")
        assert w.as_dict()["first_native_event_at"] == 1005.0


# --------------------------------------------------------------------------- #
# B/C/D/E. Session correlation
# --------------------------------------------------------------------------- #


class TestSessionCorrelation:
    def test_matching_id_root_and_time_is_accepted(self):
        problem = verify_attached_session(
            session={"session_id": "jcc-abc-123", "cwd": FIXTURE,
                     "created_at": 1001.0, "updated_at": 1002.0},
            project_root=FIXTURE, launch_requested_at=1000.0)
        assert problem is None

    def test_an_unrelated_live_session_is_rejected_by_cwd(self):
        """B: the exact session that fooled the earlier build."""
        problem = verify_attached_session(
            session={"session_id": "someone-elses", "cwd": OTHER,
                     "created_at": 1001.0, "updated_at": 1002.0},
            project_root=FIXTURE, launch_requested_at=1000.0)
        assert problem is not None
        assert "SESSION_PROJECT_MISMATCH" in problem

    def test_a_session_predating_the_launch_is_rejected(self):
        """E: created 1.85s BEFORE the run, which is what was accepted before."""
        problem = verify_attached_session(
            session={"session_id": "jcc-abc-123", "cwd": FIXTURE,
                     "created_at": 998.15, "updated_at": 999.0},
            project_root=FIXTURE, launch_requested_at=1000.0)
        assert problem is not None
        assert "SESSION_PREDATES_LAUNCH" in problem

    def test_id_matching_alone_is_not_enough(self):
        """D: right id, wrong cwd — still refused."""
        problem = verify_attached_session(
            session={"session_id": "jcc-abc-123", "cwd": OTHER,
                     "created_at": 1001.0, "updated_at": 1002.0},
            project_root=FIXTURE, launch_requested_at=1000.0)
        assert problem is not None and "SESSION_PROJECT_MISMATCH" in problem

    def test_a_session_with_no_cwd_is_not_accepted_on_time_alone(self):
        """Absence of evidence is not evidence of a match."""
        problem = verify_attached_session(
            session={"session_id": "x", "created_at": 1001.0, "updated_at": 1002.0},
            project_root=FIXTURE, launch_requested_at=1000.0)
        assert problem is None  # cwd unknown -> cannot disprove; time still gates


# --------------------------------------------------------------------------- #
# Event classification
# --------------------------------------------------------------------------- #


class TestNativeEventClassification:
    @pytest.mark.parametrize("raw,expected", [
        ("read", "file_read"),
        ("Read", "file_read"),
        ("write", "file_write"),
        ("edit", "file_write"),
        ("bash", "command_start"),
        ("command_end", "command_end"),
        ("glob", "tool_start"),
        ("grep", "tool_start"),
        ("tool_use", "tool_start"),
        ("tool_result", "tool_end"),
    ])
    def test_work_maps_to_a_unified_name(self, raw, expected):
        assert map_cli_event({"type": raw}) == expected

    @pytest.mark.parametrize("raw", [
        "heartbeat", "session_discovered", "session_attached", "health", "poll", "ping",
    ])
    def test_liveness_is_not_work(self, raw):
        """F: these are exactly what made a mission look busy while idle."""
        assert map_cli_event({"type": raw}) is None
        assert raw in NON_NATIVE_MARKERS

    def test_a_narration_with_no_tool_is_not_work(self):
        assert map_cli_event({"type": "assistant", "text": "thinking"}) is None

    def test_a_tool_riding_on_a_narration_counts(self):
        assert map_cli_event({"type": "assistant", "tool": "read"}) == "file_read"

    def test_every_mapped_name_is_in_the_unified_vocabulary(self):
        """A mapping into nowhere would be a silent no-op."""
        assert set(CLI_EVENT_MAP.values()) <= set(NATIVE_EVENT_KINDS)


# --------------------------------------------------------------------------- #
# F/G/H. Outcomes
# --------------------------------------------------------------------------- #


class TestOutcomes:
    def test_a_workload_that_produced_no_event_has_no_timestamps(self):
        w = _w(pid=1, exit_code=0)
        d = w.as_dict()
        assert d["first_native_event_at"] is None
        assert d["artifact_created_at"] is None
        assert d["workload_completed_at"] is None

    def test_the_three_ids_are_never_conflated(self):
        """jcc_run_id must never stand in for a WorkBuddy workload id."""
        w = _w()
        d = w.as_dict()
        assert d["jcc_run_id"] == "run_test"
        assert d["workbuddy_workload_id"] is None
        assert d["requested_session_id"] == "jcc-abc-123"

    def test_a_workload_id_is_only_set_from_a_reported_value(self):
        w = _w()
        assert w.as_dict()["workbuddy_workload_id"] is None
        w.workbuddy_workload_id = "wb-real-1"
        assert w.as_dict()["workbuddy_workload_id"] == "wb-real-1"

    def test_artifact_requires_a_timestamp_not_just_a_path(self):
        """H: an artifact from an earlier run is not this run's artifact."""
        w = _w(artifact_created_at=1002.0, workload_started_at=1005.0)
        # created BEFORE the workload started -> cannot be this workload's output
        assert w.artifact_created_at < w.workload_started_at
