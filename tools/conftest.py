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
already used elsewhere in this tree, not a bypass of the guard.

CR-H1. This file used to compute its own FIXED ``gettempdir()/jev-pytest``
basetemp, duplicating ``packages/conftest.py``. pytest ``rm_rf``s a basetemp on
entry, so concurrent runs sharing that one path deleted each other's scratch --
measured 3 of 5 concurrent pairs failing on this host. Both conftests now call
ONE helper, so the derivation cannot drift and a fix cannot be applied to only
one of the two. An explicit ``--basetemp`` (which the acceptance gate always
passes) still wins.
"""
from __future__ import annotations

import sys
from pathlib import Path

# `tools/` is not on sys.path for this root, and jcc_core lives under
# `packages/`. Adding it here is what lets both conftests share one definition
# instead of keeping duplicate copies of a security-relevant rule.
_PKG = Path(__file__).resolve().parents[1] / "packages"
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))


def pytest_configure(config) -> None:
    from jcc_core.pytest_basetemp import configure_basetemp

    configure_basetemp(config)
