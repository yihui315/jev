"""Agent adapter abstraction (ADR-0002 layer chain: Agent -> Adapter -> AEP).

P2 status: the four concrete adapters now implement a REAL read-only
``probe()`` and a REAL ``normalize()`` mapping. ``verified`` / status reflects
what was actually observable on this Windows host (see
``artifacts/acceptance/P2_AGENT_TELEMETRY_ACCEPTANCE_REPORT.md``).

Contract:
  * ``probe()``      -> capability report ``{installed, version, config_path,
                        hook_schema_kind, verified, status, notes}``
  * ``discover()``   -> thin back-compat wrapper over ``probe()``
  * ``normalize()``  -> raw native event dict -> ``AEPEnvelope``; raises
                        :class:`~jcc_core.adapters._common.UnidentifiableEvent`
                        when no primary identity (session_id) can be resolved.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict

from ..schemas import AEPEnvelope, AgentName


class AgentAdapter(ABC):
    agent: AgentName = AgentName.GENERIC
    #: True only when a real probe succeeded AND the mapping is real.
    verified: bool = False
    #: Human-readable kind of the native hook/config schema.
    hook_schema_kind: str = "unknown"

    @abstractmethod
    async def probe(self) -> Dict[str, Any]:
        """Read-only detection of the agent's real presence/config on this host."""
        raise NotImplementedError

    async def discover(self) -> Dict[str, Any]:
        """Back-compat wrapper returning the probe-derived capability summary."""
        try:
            report = await self.probe()
        except Exception as exc:  # noqa: BLE001
            return {
                "agent": str(self.agent.value),
                "present": False,
                "verified": False,
                "aep": False,
                "status": "UNVERIFIED",
                "note": f"probe failed: {exc}",
            }
        return {
            "agent": str(self.agent.value),
            "present": bool(report.get("installed")),
            "verified": bool(report.get("verified")),
            "aep": bool(report.get("verified")),
            "status": report.get("status"),
            "version": report.get("version"),
            "config_path": report.get("config_path"),
            "hook_schema_kind": report.get("hook_schema_kind"),
            "note": report.get("note", ""),
        }

    @abstractmethod
    async def normalize(self, raw: Dict[str, Any]) -> AEPEnvelope:
        """Convert a raw native agent event into an AEP v0.1 envelope."""
        raise NotImplementedError
