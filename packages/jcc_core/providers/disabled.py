"""Disabled Jev provider — a safe no-op.

Used when ``jev.provider=disabled`` or when ``real`` is requested but the key /
feature flag are absent (fail-closed fallback). It returns an empty, neutral
Assessment so the policy degrades to VERIFY (safe-default) rather than acting.
"""
from __future__ import annotations

import time

from ..schemas import AEPEnvelope, AgentName, Assessment, SessionState
from .base import JevProvider


class DisabledProvider(JevProvider):
    name = "disabled"
    verified = True

    def assess(self, event: AEPEnvelope, session: SessionState) -> Assessment:
        agent = event.agent.value if hasattr(event.agent, "value") else event.agent
        return Assessment(
            agent=AgentName(agent) if not isinstance(event.agent, AgentName) else event.agent,
            session_id=event.session_id,
            timestamp=event.timestamp or time.time(),
            model="disabled",
            provider=self.name,
            probabilities={},
            risk_score=0.0,
            band="disabled",
            needs_verify=True,  # safe-default: nothing scored -> verify
        )

    def is_available(self) -> bool:
        return False
