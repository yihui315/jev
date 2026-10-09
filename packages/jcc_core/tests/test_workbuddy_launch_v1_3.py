"""B4 regression — WorkBuddy session capture must honour the requested cwd.

The launch template binds ``--add-dir`` AND ``cwd`` to the mission's project
root. ``capture_launch_event(cwd=...)`` is the bridge that turns "the operator
asked to launch in THIS directory" into "a real WorkBuddy session exists in THIS
directory". If it silently falls back to the freshest live session in a DIFFERENT
directory, a launch gets bound to a tree the WorkBuddy process never ran in --
exactly the scope escape the binding exists to prevent.

These tests pin the strict behaviour:
  * a requested cwd with a matching live session -> ok (captures that session);
  * a requested cwd with NO matching live session -> WORKBUDDY_SESSION_SCOPE_MISMATCH
    (never a wrong-cwd session);
  * no cwd requested -> preference behaviour unchanged (ok if any live session).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from jcc_core import workbuddy_launch as WL

_CWD_A = r"C:\proj\alpha"
_CWD_B = r"C:\proj\beta"
_CWD_C = r"C:\proj\gamma"  # requested but has no live session


def _live_record(session_id: str, cwd: str, *, pid: int, age_ms: float = 0.0) -> dict:
    return {
        "sessionId": session_id,
        "pid": pid,
        "cwd": cwd,
        "version": "test-cli",
        "lastHeartbeat": (time.time() * 1000.0) - age_ms,
    }


@pytest.fixture
def tmp_sessions(tmp_path, monkeypatch):
    d = tmp_path / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(WL, "sessions_dir", lambda: d)
    return d


def _write(d: Path, rec: dict) -> None:
    (d / (rec["sessionId"] + ".json")).write_text(
        json.dumps(rec), encoding="utf-8")


def test_cwd_match_is_captured(tmp_sessions, monkeypatch):
    pid = __import__("os").getpid()
    _write(tmp_sessions, _live_record("s-a", _CWD_A, pid=pid))
    _write(tmp_sessions, _live_record("s-b", _CWD_B, pid=pid))
    cap = WL.capture_launch_event(cwd=_CWD_A)
    assert cap["ok"] is True, cap
    assert cap["event"]["sessionId"] == "s-a"
    assert cap["source"]["session_id"] == "s-a"


def test_cwd_mismatch_is_rejected(tmp_sessions, monkeypatch):
    pid = __import__("os").getpid()
    _write(tmp_sessions, _live_record("s-a", _CWD_A, pid=pid))
    _write(tmp_sessions, _live_record("s-b", _CWD_B, pid=pid))
    cap = WL.capture_launch_event(cwd=_CWD_C)
    assert cap["ok"] is False
    assert cap["reason"] == "WORKBUDDY_SESSION_SCOPE_MISMATCH", cap
    assert cap["requested_cwd"] == _CWD_C
    # the only live session is in a different scope; never captured
    assert cap["captured_cwd"] in (_CWD_A, _CWD_B)
    assert "session_id" in cap and cap["session_id"] in ("s-a", "s-b")


def test_no_cwd_requested_keeps_preference(tmp_sessions, monkeypatch):
    pid = __import__("os").getpid()
    _write(tmp_sessions, _live_record("s-a", _CWD_A, pid=pid))
    _write(tmp_sessions, _live_record("s-b", _CWD_B, pid=pid))
    cap = WL.capture_launch_event()  # no cwd -> preference, never a scope violation
    assert cap["ok"] is True, cap
    assert cap["event"]["sessionId"] in ("s-a", "s-b")


def test_stale_session_is_not_captured_even_on_cwd_match(tmp_sessions, monkeypatch):
    pid = __import__("os").getpid()
    # a stale (old heartbeat) record in the requested cwd, plus a live one in B
    _write(tmp_sessions, _live_record("s-stale", _CWD_A, pid=pid,
                                      age_ms=(WL.HEARTBEAT_FRESH_SECONDS + 10) * 1000.0))
    _write(tmp_sessions, _live_record("s-b", _CWD_B, pid=pid))
    cap = WL.capture_launch_event(cwd=_CWD_A)
    # stale record in A is not live, and there is no live session in A -> mismatch
    assert cap["ok"] is False
    assert cap["reason"] == "WORKBUDDY_SESSION_SCOPE_MISMATCH", cap
