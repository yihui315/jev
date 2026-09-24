"""Test-infra: keep pytest scratch inside the project.

The default basetemp lives under %TEMP%\\pytest-of-<user>\\; pytest prunes the
previous run's numbered dirs there by bulk-deleting them. In this sandbox a
>50-file bulk delete trips the SAFE_DELETE guard, which raises AFTER the run,
making pytest exit 1 even when every test passed. Redirecting basetemp to a
project-owned, gitignored directory removes that interaction entirely.
"""
from __future__ import annotations

import os
import time
from pathlib import Path


def pytest_configure(config) -> None:
    if config.option.basetemp:  # explicit --basetemp wins
        return
    root = Path(__file__).resolve().parents[1] / "runtime" / "pytest-tmp"
    root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    config.option.basetemp = str(root / f"{stamp}-{os.getpid()}")
