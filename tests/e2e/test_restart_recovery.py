"""E-04: Windows restart recovery (owner: trail-2).

Drives the REAL server entrypoint (``python -m jcc_core.run``) as a subprocess,
ingests state, then simulates a Windows abrupt kill (``TerminateProcess`` -- the
exact path ADR-0006 covers, where the SIGTERM/SIGINT cleanup never runs and the
JCC-marker is orphaned) and RELAUNCHES the same service against the SAME
database. Asserts state survives: session rows, the hash-chained audit tape, and
an in-flight directive are all readable and intact after restart, with no silent
re-baseline or discarded history.

No new deps; HTTP is stdlib urllib. The subprocess is killed only by its own
PID (never a global process kill).
"""
from __future__ import annotations

import time

import conftest
from conftest import SubprocessServer, _free_loopback_port

TOKEN = "restart-control-token"


def _valid_aep(session_id: str, event_id: str) -> dict:
    return {
        "version": "aep/v0.1",
        "event_id": event_id,
        "delivery_id": "dlv-" + event_id,
        "session_id": session_id,
        "agent": "workbuddy",
        "project": "e2e",
        "timestamp": time.time(),
        "event_type": "tool_call",
        "payload": {"step": event_id},
        "evidence": {"artifact": event_id},
        "schema_ok": True,
    }


def test_state_survives_restart(tmp_path):
    rt = tmp_path / "runtime"
    rt.mkdir(exist_ok=True)
    port = _free_loopback_port()
    srv = SubprocessServer(str(rt), TOKEN, port)

    try:
        # --- first life ---------------------------------------------------
        srv.start()
        pid1 = srv.proc.pid
        for i in range(2):
            r = srv.request("POST", "/api/assess", json_body=_valid_aep("sess-R", f"re-{i}"))
            assert r.status == 200, f"ingest failed ({r.status}): {r.body}"

        sess_before = srv.request("GET", "/api/sessions").json()
        audit_before = srv.request("GET", "/api/audit").json()
        verify_before = srv.request("GET", "/api/audit/verify").json()
        assert verify_before["intact"] is True, "chain not intact before restart"
        dirs_before = srv.request("GET", "/api/decisions?sid=sess-R").json()
        assert dirs_before, "no directive recorded before restart"
        target_id = dirs_before[0]["directive_id"]
        target_content = dirs_before[0]

        # ADR-0006 marker state BEFORE the abrupt kill -- the product records the
        # live uvicorn server process pid (not the `python -m` launcher pid), so we
        # capture the actual marker value to prove it is reconciled afterward.
        marker_before = srv.read_marker_pid()
        assert marker_before is not None, "JCC-marker absent before restart"
        assert conftest.pid_alive(marker_before), "JCC-marker not live before restart"

        # --- abrupt kill (Windows TerminateProcess; marker is orphaned) ----
        srv.kill_abrupt()
        assert srv.proc.poll() is not None, "abrupt kill did not terminate the process"

        # --- restart the SAME service against the SAME db -----------------
        srv.start()
        pid2 = srv.proc.pid
        assert pid2 != pid1, "restart produced the same PID -- not a real restart"

        sess_after = srv.request("GET", "/api/sessions").json()
        audit_after = srv.request("GET", "/api/audit").json()
        verify_after = srv.request("GET", "/api/audit/verify").json()
        dirs_after = srv.request("GET", "/api/decisions?sid=sess-R").json()

        # --- no data lost ------------------------------------------------
        assert len(sess_after) == len(sess_before), (
            f"session rows lost across restart: {len(sess_before)} -> {len(sess_after)}"
        )
        assert len(audit_after) == len(audit_before), (
            f"audit rows lost across restart: {len(audit_before)} -> {len(audit_after)}"
        )
        assert verify_after["intact"] is True, "audit chain broken after restart"
        assert verify_after["entries"] == verify_before["entries"], (
            "audit entry count changed across restart (history discarded)"
        )

        # --- in-flight directive readable + intact -----------------------
        match = [x for x in dirs_after if x["directive_id"] == target_id]
        assert match, "in-flight directive not readable after restart"
        assert match[0] == target_content, "directive content mutated across restart"

        # --- no silent re-baseline ---------------------------------------
        seqs = sorted(x["seq"] for x in audit_after)
        assert seqs and seqs[0] == 1, (
            f"audit tape re-baselined after restart (first seq="
            f"{seqs[0] if seqs else None}, expected 1 -- history was discarded)"
        )

        # --- marker reconciled to the live (new) PID (ADR-0006) ----------
        # ADR-0006 sec.4: a Windows abrupt kill (TerminateProcess) leaves the
        # SIGTERM/SIGINT cleanup unhooked, so the JCC-marker is orphaned pointing
        # at a dead PID. On restart the entrypoint MUST detect that stale marker
        # and overwrite it with the new live server process pid -- never left
        # pointing at the killed run ("phantom running" state) and never refusing
        # to start.
        #
        # NOTE (test artifact, not a product defect): the product records the
        # *uvicorn server process* pid in the marker, which is a child of the
        # `python -m jcc_core.run` launcher this test launches. Comparing the
        # marker against the launcher pid (srv.proc.pid) is therefore wrong; the
        # correct ADR-0006 assertions are: (1) the marker points at a LIVE process,
        # and (2) it was OVERWRITTEN -- i.e. it no longer points at the killed
        # run's server pid.
        assert srv.alive_pid() is not None, "restarted launcher is not alive"
        marker_after = srv.read_marker_pid()
        assert marker_after is not None, "JCC-marker missing after restart"
        assert conftest.pid_alive(marker_after), (
            f"JCC-marker pid {marker_after} is not a live process after restart "
            "-- orphaned marker not reconciled (ADR-0006)"
        )
        assert marker_after != marker_before, (
            f"JCC-marker still points at pre-restart pid {marker_before} after "
            "restart -- stale orphaned marker was NOT reconciled/overwritten (ADR-0006)"
        )
    finally:
        srv.cleanup()
