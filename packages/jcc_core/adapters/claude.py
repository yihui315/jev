"""Claude Code agent adapter — UNVERIFIED stub (P2 fills real probe)."""
from __future__ import annotations

from typing import Any

from ..schemas import AgentName
from .base import AgentAdapter


class ClaudeAdapter(AgentAdapter):
    agent = AgentName.CLAUDE
    verified = False  # UNVERIFIED per ADR-0003 (P2)

    async def discover(self) -> dict[str, Any]:
        return {
            "agent": "claude",
            "present": False,
            "verified": False,
            "aep": False,
            "note": "UNVERIFIED stub — P2 implements real discovery/probe.",
        }

    async def normalize(self, raw: dict[str, Any]):
        raise NotImplementedError(
            "Claude AEP normalization is UNVERIFIED (P2). Refusing to "
            "fabricate a payload mapping."
        )
