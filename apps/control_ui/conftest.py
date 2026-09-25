"""Test-infra: put ``apps``, ``packages`` and ``fixtures`` on ``sys.path`` so the
UI tests can ``import control_ui`` and ``import jcc_core`` regardless of cwd.

The UI tests need both the product package (``apps/control_ui``) and the core
package (``packages/jcc_core``) importable; the runtime path bootstrap
(``control_ui.paths``) handles this for the running UI, but pytest collection
needs it set before any test module is imported, which is what this conftest does.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # F:/jev
for sub in ("apps", "packages", "fixtures"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)
