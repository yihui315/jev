"""Discovery over the four known agent adapters.

Returns a capability map. In P1 every adapter reports ``verified=False`` /
``aep=False`` (UNVERIFIED, ADR-0003). This keeps the architecture complete
without fabricating per-agent probe results.
"""
from __future__ import annotations

import asyncio
from typing import Any

from ..schemas import AgentName
from .base import AgentAdapter
from .claude import ClaudeAdapter
from .codex import CodexAdapter
from .hermes import HermesAdapter
from .workbuddy import WorkBuddyAdapter

ADAPTERS: list[type[AgentAdapter]] = [
    WorkBuddyAdapter,
    ClaudeAdapter,
    CodexAdapter,
    HermesAdapter,
]


async def discover_all() -> dict[str, Any]:
    results: dict[str, Any] = {}
    for cls in ADAPTERS:
        inst = cls()
        try:
            info = await inst.discover()
        except Exception as exc:  # noqa: BLE001
            info = {"agent": str(inst.agent.value), "present": False, "error": str(exc)}
        results[str(inst.agent.value)] = info
    return {
        "adapters": results,
        "verified": False,  # architecture complete, probes UNVERIFIED (P2)
        "note": "Adapter contracts defined; per-agent probes are UNVERIFIED (P2).",
    }


def all_adapters() -> list[AgentAdapter]:
    return [cls() for cls in ADAPTERS]
