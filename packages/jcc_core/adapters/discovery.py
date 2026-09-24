"""Discovery over the four known agent adapters (P2: REAL probes).

Returns a capability map built from each adapter's read-only ``probe()``.
Status vocabulary per agent: CONNECTED / DEGRADED / ADAPTER_DISABLED /
UNVERIFIED. A missing or unverifiable agent NEVER stops the platform — it is
reported and the platform still runs (NEVER fake a connection).
"""
from __future__ import annotations

from typing import Any

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
            report = await inst.probe()
        except Exception as exc:  # noqa: BLE001
            report = {
                "agent": str(inst.agent.value),
                "installed": False,
                "verified": False,
                "status": "UNVERIFIED",
                "note": f"probe raised: {exc}",
            }
        results[report.get("agent", str(inst.agent.value))] = report
    statuses = {k: v.get("status") for k, v in results.items()}
    return {
        "adapters": results,
        "statuses": statuses,
        "connected": [k for k, s in statuses.items() if s == "CONNECTED"],
        "degraded": [k for k, s in statuses.items() if s == "DEGRADED"],
        "disabled": [k for k, s in statuses.items() if s == "ADAPTER_DISABLED"],
        "unverified": [k for k, s in statuses.items() if s == "UNVERIFIED"],
        "verified": all(v.get("verified") for v in results.values()),
        "actuation_enabled": False,  # P0-P2
        "note": "Per-agent probes executed read-only on this host (P2).",
    }


def all_adapters() -> list[AgentAdapter]:
    return [cls() for cls in ADAPTERS]


def adapter_for(agent: str) -> AgentAdapter | None:
    for cls in ADAPTERS:
        if cls.agent.value == agent:
            return cls()
    return None
