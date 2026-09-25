"""Shared fixtures for the S-02 MCP facade test suite.

Importing :mod:`mcp_facade` here triggers :func:`mcp_facade._compat.ensure_backends`,
which registers a working ``operator_skill.backends`` so the (broken on this tree)
Operator Skill package can be imported. All tests therefore run against the real
Operator Skill dispatch path -- the facade is genuinely thin.

Factory helpers live in :mod:`mcp_facade.tests.helpers` (importable from every
test module because pytest's prepend import mode adds the test directory to
``sys.path``).
"""
from __future__ import annotations

import pytest

# Ensure the Operator Skill package is importable before anything else imports it.
import mcp_facade  # noqa: F401  (runs ensure_backends() at import)


@pytest.fixture
def trace_path(tmp_path):
    return str(tmp_path / "mcp-facade-trace.jsonl")


@pytest.fixture
def verified_probe():
    from mcp_facade.capability import StubProbe

    return StubProbe(verified=True, status="CONNECTED")


@pytest.fixture
def unverified_probe():
    from mcp_facade.capability import StubProbe

    return StubProbe(verified=False, status="UNVERIFIED")
