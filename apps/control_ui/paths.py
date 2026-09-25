"""Path bootstrap.

``apps/`` is not a package the project installs, and ``jcc_core`` / ``supervisor``
live in ``packages/``. This makes that import work the same way ``jcc_core.run``
does (it inserts its own parent directory) rather than requiring a ``pip install``
that would quietly pin a copy of the core the operator does not control.
"""
from __future__ import annotations

import sys
from pathlib import Path

#: F:/jev
REPO_ROOT = Path(__file__).resolve().parents[2]
#: F:/jev/apps  -- so ``import control_ui`` resolves under ``python -m control_ui``
APPS = REPO_ROOT / "apps"
#: F:/jev/packages
PACKAGES = REPO_ROOT / "packages"
#: F:/jev/fixtures
FIXTURES = REPO_ROOT / "fixtures"


def _add(path: Path) -> None:
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)


def ensure() -> None:
    """Make ``control_ui``, ``jcc_core``, ``supervisor`` and the demo fixtures importable."""
    _add(APPS)
    _add(PACKAGES)
    _add(FIXTURES)
