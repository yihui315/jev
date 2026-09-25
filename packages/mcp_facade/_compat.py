"""Runtime compatibility shim for the Operator Skill transport module.

On this working tree ``packages/operator_skill/backends.py`` is a broken /
empty file (7503 NUL bytes): it cannot be imported, which makes the whole
``operator_skill`` package fail to import and blocks being "thin over" it.

We do **not** modify ``operator_skill`` on disk -- that file is owned by another
slice (S-01) and is pinned read-only for S-02. Instead, when the real module is
missing or broken, we register a working ``operator_skill.backends`` in
``sys.modules`` from THIS package (``packages/mcp_facade/transport.py``) before
anything imports ``operator_skill``. The shim provides exactly the names the
package expects (``ASGIBackend``, ``Backend``, ``BackendResult``, ``HttpBackend``,
``RecordingBackend``) so the package loads; the facade then delegates tool
execution to ``OperatorSkill.call`` -- no duplicated authority.

This is transparent and idempotent: ``ensure_backends()`` only installs the stub
when the real module cannot be imported. If ``operator_skill.backends`` is later
fixed on disk, this becomes a no-op.
"""
from __future__ import annotations

import importlib
import sys
import types

REQUIRED_NAMES = ("ASGIBackend", "Backend", "BackendResult", "HttpBackend", "RecordingBackend")
MODULE_NAME = "operator_skill.backends"


def _real_backends_usable() -> bool:
    try:
        mod = importlib.import_module(MODULE_NAME)
    except Exception:
        return False
    return all(hasattr(mod, n) for n in REQUIRED_NAMES)


def ensure_backends() -> str:
    """Make ``operator_skill.backends`` importable. Returns ``"real"`` or ``"shim"``.

    Must be called *before* the first ``import operator_skill`` in this process.
    """
    if _real_backends_usable():
        return "real"
    from . import transport  # local import: no circular dependency

    stub = types.ModuleType(MODULE_NAME)
    stub.__file__ = getattr(transport, "__file__", None)
    for name in REQUIRED_NAMES:
        setattr(stub, name, getattr(transport, name, object))
    sys.modules[MODULE_NAME] = stub
    return "shim"
