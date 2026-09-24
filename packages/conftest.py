"""Test-infra: keep pytest scratch inside the project and off the user profile.

Two sandbox / user-profile hazards are neutralised here:

1. Base temp dir. The default pytest basetemp lives under the system TEMP dir
   (pytest-of-<user>); pytest prunes the previous run's numbered dirs there by
   bulk-deleting them. In this sandbox a >50-file bulk delete trips the
   SAFE_DELETE guard, which raises AFTER the run, making pytest exit 1 even when
   every test passed. ``pytest_configure`` redirects basetemp into a
   project-owned, gitignored directory.

2. Spool dir. ``jcc_emit.spool()`` defaults to ``~/.jcc/spool``
   (``JCC_SPOOL_DIR`` unset) and writes there whenever delivery fails -- which
   the P2 failure-path tests deliberately trigger. An autouse, session-scoped
   fixture points ``JCC_SPOOL_DIR`` at a temp dir for the whole session so tests
   never touch the user's real profile; subprocesses inherit ``os.environ``, so
   the override propagates to spawned jcc_emit.py / jcc-codex-notify.py too.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest


def pytest_configure(config) -> None:
    if config.option.basetemp:  # explicit --basetemp wins
        return
    root = Path(__file__).resolve().parents[1] / "runtime" / "pytest-tmp"
    root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    config.option.basetemp = str(root / f"{stamp}-{os.getpid()}")


@pytest.fixture(autouse=True, scope="session")
def _isolate_jcc_spool(tmp_path_factory):
    """Point JCC_SPOOL_DIR at a per-session temp dir, restoring it on teardown.

    JCC_ROOT is intentionally NOT redirected: jcc_emit uses JCC_ROOT only to
    locate the ``packages`` dir for sys.path (its default, F:/jev, is already
    correct) and never writes to it -- redirecting it would break imports. Only
    the spool dir is a write target and needs isolation.
    """
    previous = os.environ.get("JCC_SPOOL_DIR")
    spool_dir = tmp_path_factory.mktemp("jcc-spool")
    os.environ["JCC_SPOOL_DIR"] = str(spool_dir)
    try:
        yield spool_dir
    finally:
        if previous is None:
            os.environ.pop("JCC_SPOOL_DIR", None)
        else:
            os.environ["JCC_SPOOL_DIR"] = previous
