"""JCC-marker lifecycle tests (ADR-0006 sec.4; Harbor final-SRE-pass finding).

On Windows an abrupt kill is ``TerminateProcess``, so ``run.py``'s SIGTERM/SIGINT
handler never runs and the ``JCC-marker`` survives pointing at a dead PID -- a
phantom "running" state that weakens ADR-0006's "prevents concurrent/orphan runs"
claim. These tests pin the READ-ONLY reconciliation helpers that make an orphaned
marker detectable and safe to overwrite, and prove the liveness check never kills
(``os.kill(pid, 0)`` would TerminateProcess on Windows -- see ``run._pid_alive``).
"""
from __future__ import annotations

import os
import types
from pathlib import Path

import pytest

from jcc_core import run as jcc_run


def _cfg(db_path: Path):
    return types.SimpleNamespace(state_store_db_path=str(db_path))


def _write_marker(db_path: Path, pid: int) -> Path:
    marker = db_path.parent / jcc_run.MARKER_NAME
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"pid={pid}\ndb={db_path}\nstarted=0\n", encoding="utf-8")
    return marker


@pytest.fixture()
def sleeper():
    """A live child process we fully control (terminate + reap in teardown)."""
    import subprocess
    import sys

    procs = []

    def _spawn():
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        procs.append(p)
        return p

    yield _spawn
    for p in procs:
        if p.poll() is None:
            p.terminate()
        try:
            p.wait(timeout=10)
        except Exception:  # noqa: BLE001
            p.kill()


# --------------------------------------------------------------------------- #
# _pid_alive: read-only liveness, never kills
# --------------------------------------------------------------------------- #
def test_pid_alive_true_for_running_process(sleeper):
    p = sleeper()
    assert jcc_run._pid_alive(p.pid) is True
    assert jcc_run._pid_alive(os.getpid()) is True


def test_pid_alive_false_for_dead_process(sleeper):
    p = sleeper()
    pid = p.pid
    p.terminate()          # Windows: TerminateProcess (the abrupt-kill case)
    p.wait(timeout=10)
    assert jcc_run._pid_alive(pid) is False


def test_pid_alive_handles_bogus_pids():
    assert jcc_run._pid_alive(0) is False
    assert jcc_run._pid_alive(-1) is False
    assert jcc_run._pid_alive(None) is False
    assert jcc_run._pid_alive("not-a-pid") is False


# --------------------------------------------------------------------------- #
# reconcile_marker: identify + report, never destroy a live owner's marker
# --------------------------------------------------------------------------- #
def test_reconcile_none_when_no_marker(tmp_path):
    assert jcc_run.reconcile_marker(_cfg(tmp_path / "jcc.db")) is None


def test_reconcile_reports_live_other_owner(sleeper, tmp_path):
    db = tmp_path / "jcc.db"
    p = sleeper()
    _write_marker(db, p.pid)
    assert jcc_run.reconcile_marker(_cfg(db)) == p.pid          # alive and != us
    assert (db.parent / jcc_run.MARKER_NAME).exists()           # left untouched


def test_reconcile_stale_marker_returns_none_and_keeps_file(sleeper, tmp_path):
    db = tmp_path / "jcc.db"
    p = sleeper()
    pid = p.pid
    p.terminate()
    p.wait(timeout=10)
    _write_marker(db, pid)
    assert jcc_run.reconcile_marker(_cfg(db)) is None           # dead -> stale
    assert (db.parent / jcc_run.MARKER_NAME).exists()           # reconcile never deletes


def test_reconcile_ignores_own_pid(tmp_path):
    db = tmp_path / "jcc.db"
    _write_marker(db, os.getpid())
    assert jcc_run.reconcile_marker(_cfg(db)) is None


def test_stale_marker_is_reclaimed_by_next_start(sleeper, tmp_path):
    """End-to-end of the reconciliation: after an abrupt kill the next start
    overwrites the orphaned marker with the new (live) PID."""
    db = tmp_path / "jcc.db"
    p = sleeper()
    pid = p.pid
    p.terminate()
    p.wait(timeout=10)
    _write_marker(db, pid)

    cfg = _cfg(db)
    assert jcc_run.reconcile_marker(cfg) is None                # detected stale
    marker = jcc_run._write_marker(cfg)                         # claim it
    assert jcc_run.read_marker_pid(marker) == os.getpid()
    assert jcc_run._pid_alive(os.getpid()) is True
