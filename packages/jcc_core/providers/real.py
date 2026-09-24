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
        # UNVERIFIED stub: would call the external scorer here. Until verified it
        # supplies NO signals and stays unverified, so the kernel fails closed to
        # EXACT_BLOCKER -> ESCALATE via `provider_unverified` (ADR-0008 F2 / D-35).
        # It must NOT declare `raw["provider_abstained"]`: an *abstention* is
        # VERIFY (Errata E-3), whereas an *unverified* provider is ESCALATE.
        agent = event.agent.value if hasattr(event.agent, "value") else event.agent
        return Assessment(
            agent=AgentName(agent) if not isinstance(event.agent, AgentName) else event.agent,
            session_id=event.session_id,
            timestamp=event.timestamp or time.time(),
            model=self.model,
            provider=self.name,
            probabilities={},
            risk_score=0.0,
            band="unverified",   # not a real band -> coerces to UNKNOWN risk
            needs_verify=True,
            # D-31: propagate this provider's verification state onto the record.
            # `real` is UNVERIFIED, so this is False -> `provider_unverified`
            # fires -> EXACT_BLOCKER -> ESCALATE (frozen F2). Behaviour-invariant
            # with the prior default, but made explicit per spec §2.3.
            provider_verified=self.verified,
            raw={"note": "RealProvider is UNVERIFIED scaffolding; no external call made."},
        )

    def is_available(self) -> bool:
        return bool(self.api_key)
