"""Fixtures for the operator-skill test suite."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def trace_path(tmp_path: Path) -> Path:
    """A fresh trace tape per test (the skill writes it on first invocation)."""
    return tmp_path / "operator-trace.jsonl"


@pytest.fixture
def approvals_path(tmp_path: Path) -> Path:
    """A fresh approval tape per test (the ApprovalRegistry appends here)."""
    return tmp_path / "approvals.jsonl"
