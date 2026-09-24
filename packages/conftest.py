"""Test-infra: keep pytest scratch inside the project and off the user profile.

Two sandbox / user-profile hazards are neutralised here:

1. Base temp dir. The default pytest basetemp lives under the system TEMP dir
   (pytest-of-<user>); pytest prunes the previous run's numbered dirs there by
   bulk-deleting them. In this sandbox a >50-file bulk delete trips the
   SAFE_DELETE guard, which raises after the run, making pytest exit 1 even when
   every test passed. ``pytest_configure`` redirects basetemp into a
   single, stable directory *inside the OS temp dir*.

   The location is NOT optional and NOT cosmetic -- it is load-bearing:
   the safe-delete shim (``sitecustomize.py``) short-circuits every delete that
   resolves under ``tempfile.gettempdir()`` (see ``_is_under_os_tmp_dir`` /
   ``_should_bypass_safe_delete``) and only routes *other* paths through the
   bulk-delete guard helper. A basetemp anywhere else -- including the
   project tree and including ``C:\\Windows\\TEMP`` on hosts where
   ``tempfile.gettempdir()`` is the per-user ``AppData\\Local\\Temp`` -- is
   guarded, so pytest's scratch cleanup aborts the run with
   ``SAFE_DELETE_BULK_CONFIRM_REQUIRED`` (observed: ``count:171,
   threshold:50``) and every ``tmp_path`` fixture errors out.

   The target is therefore derived from ``tempfile.gettempdir()`` at runtime
   rather than hard-coded, so it stays correct when TEMP/TMP differ per host or
   per shell. It is deliberately stable (no timestamp): pytest ``rm_rf``s a
   user-supplied basetemp on entry, which keeps scratch from accumulating
   across runs.

2. Spool dir. ``jcc_emit.spool()`` defaults to ``~/.jcc/spool``
   (``JCC_SPOOL_DIR`` unset) and writes there whenever delivery fails -- which
   the P2 failure-path tests deliberately trigger. An autouse, session-scoped
   fixture points ``JCC_SPOOL_DIR`` at a temp dir for the whole session so tests
   never touch the user's real profile; subprocesses inherit ``os.environ``, so
   the override propagates to spawned jcc_emit.py / jcc-codex-notify.py too.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


def pytest_configure(config) -> None:
    if config.option.basetemp:  # explicit --basetemp wins
        return
    # MUST live under tempfile.gettempdir(): that is the only path prefix the
    # safe-delete shim exempts from the bulk-delete guard. See module docstring.
    root = Path(tempfile.gettempdir()) / "jev-pytest"
    root.mkdir(parents=True, exist_ok=True)
    config.option.basetemp = str(root)


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
