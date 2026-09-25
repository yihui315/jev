"""Bootstrap for the 2D Control UI.

Resolves the import paths (via :mod:`control_ui.paths`), the base URL of the
control-plane API, and the real on-disk configuration. The UI is a read-only
surface: it NEVER reads or renders the control token, and it reads configuration
only through ``jcc_core.config`` (the same file the server reads) so the two
never disagree about what a non-secret setting is.
"""
from __future__ import annotations

import os

from control_ui import paths
from control_ui.api_client import DEFAULT_API_BASE


def ensure() -> None:
    """Make the product + core packages importable (idempotent)."""
    paths.ensure()


def resolve_api_base() -> str:
    """Loopback base URL of the control plane (overridable for a UI-pinned port)."""
    return os.environ.get("JCC_API_BASE", DEFAULT_API_BASE)


def load_config():
    """Load the real ``config/jcc.yaml`` through ``jcc_core.config``.

    SECURITY: returns the *config object*, never the token value. The UI renders
    only non-secret fields (env-var NAMES, not values) and derives everything
    else from live API responses.
    """
    from jcc_core.config import load_config as _load

    return _load()
