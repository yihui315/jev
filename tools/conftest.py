"""Test-infra for ``tools/tests``: keep pytest's scratch inside the OS temp dir.

WHY THIS EXISTS. Every other test root in this tree already carries a conftest
that redirects pytest's basetemp into ``tempfile.gettempdir()`` (see
``packages/conftest.py`` for the full explanation). ``tools/tests`` did not, so
running it bare sent pytest's scratch to the default ``pytest-of-<user>``
location -- which on this host is ``C:\\Windows\\Temp``, NOT where
``tempfile.gettempdir()`` points. The safe-delete shim guards every delete
outside the latter, pytest prunes the previous run's numbered directories on
entry, and the resulting bulk delete trips
``SAFE_DELETE_BULK_CONFIRM_REQUIRED``. Measured 2026-09-26: 41 tests passed and
the run still exited non-zero.

That is environment noise, not a product signal -- and the fix is the same one
already used elsewhere in this tree, not a bypass of the guard. An explicit
``--basetemp`` (which the acceptance gate always passes) still wins.
"""
from __future__ import annotations

import tempfile
from pathlib import Path


def pytest_configure(config) -> None:
    if config.option.basetemp:  # an explicit --basetemp wins
        return
    # MUST live under tempfile.gettempdir(): that is the only path prefix the
    # safe-delete shim exempts from the bulk-delete guard.
    root = Path(tempfile.gettempdir()) / "jev-pytest"
    root.mkdir(parents=True, exist_ok=True)
    config.option.basetemp = str(root)
