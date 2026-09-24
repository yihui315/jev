"""Real Jev provider STUB — UNVERIFIED, disabled by default (ADR-0004).

This is a placeholder for the future real scorer (e.g. typesafe / jev-1.13.0
``/v1/jev/{model}/decide`` contract referenced in ADR-0001). It is intentionally
NOT wired into the running system unless BOTH:
    * ``config.jev.feature_flags.real_provider`` is true, AND
    * the API key env var (``JEV_API_KEY``) is present.
Until then it is never instantiated by ``build_provider``.

VERIFICATION STATE: UNVERIFIED. Do not assume this works; it is scaffolding.
"""
from __future__ import annotations

import os
import time
from typing import Optional

from ..schemas import AEPEnvelope, AgentName, Assessment, SessionState
from .base import JevProvider


class RealProvider(JevProvider):
    name = "real"
    verified = False  # UNVERIFIED — scaffolding only

    def __init__(
        self,
        model: str = "jev-1.13.0",
        api_key: Optional[str] = None,
        base_url: str = "https://api.typesafe.ai",
    ):
        self.model = model
        self.api_key = api_key or os.environ.get("JEV_API_KEY")
        self.base_url = base_url

    def assess(self, event: AEPEnvelope, session: SessionState) -> Assessment:
        # UNVERIFIED stub: would call the external scorer here. Until verified,
        # refuse to fabricate a score — degrade to a VERIFY-leaning assessment.
        agent = event.agent.value if hasattr(event.agent, "value") else event.agent
        return Assessment(
            agent=AgentName(agent) if not isinstance(event.agent, AgentName) else event.agent,
            session_id=event.session_id,
            timestamp=event.timestamp or time.time(),
            model=self.model,
            provider=self.name,
            probabilities={},
            risk_score=0.0,
            band="unverified",
            needs_verify=True,
            raw={"note": "RealProvider is UNVERIFIED scaffolding; no external call made."},
        )

    def is_available(self) -> bool:
        return bool(self.api_key)
