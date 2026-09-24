"""Agent adapter abstraction (ADR-0002 layer chain: Agent -> Adapter -> AEP).

An ``AgentAdapter`` normalizes a raw agent signal into an AEP v0.1
``AEPEnvelope`` and can report discovery status. In P1 the concrete adapters are
UNVERIFIED stubs: the per-agent AEP payload shapes are not yet specified (ADR-0003
marks them UNVERIFIED; P2 fills real probes). We therefore define the CONTRACT
and a safe no-op discovery, but ``normalize`` refuses to fabricate a mapping.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..schemas import AEPEnvelope, AgentName


class AgentAdapter(ABC):
    agent: AgentName = AgentName.GENERIC
    verified: bool = False  # UNVERIFIED until P2 implements real probes

    @abstractmethod
    async def discover(self) -> dict[str, Any]:
        """Report presence/capabilities. Safe no-op in P1."""
        raise NotImplementedError

    @abstractmethod
    async def normalize(self, raw: dict[str, Any]) -> AEPEnvelope:
        """Convert a raw agent signal into an AEP v0.1 envelope.

        UNVERIFIED in P1: concrete adapters must NOT fabricate payload mappings.
        """
        raise NotImplementedError
