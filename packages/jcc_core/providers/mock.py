"""Deterministic mock Jev provider (DEFAULT — zero external keys).

Determinism: the risk score is derived from a SHA-256 of
``session_id:event_id``, so identical inputs always produce the identical
score/band/flags. This satisfies the "deterministic fixtures" requirement and
makes the acceptance harness reproducible.

Safety defaults (ADR-0003): an event whose evidence is missing or whose
adapter flagged ``schema_ok=False`` is routed to VERIFY (never auto-safe).
"""
from __future__ import annotations

import hashlib
import time

from ..schemas import AEPEnvelope, AgentName, Assessment, SessionState
from .base import JevProvider


class DeterministicMockProvider(JevProvider):
    name = "mock"
    verified = True  # deterministic, fully specified — safe to use in demo mode

    def __init__(self, model: str = "jev-1.13.0"):
        self.model = model

    def _risk(self, event: AEPEnvelope) -> float:
        seed = f"{event.session_id}:{event.event_id}".encode("utf-8")
        digest = hashlib.sha256(seed).digest()
        value = int.from_bytes(digest[:8], "big")
        return (value % 1000) / 1000.0  # deterministic in [0, 1)

    def assess(self, event: AEPEnvelope, session: SessionState) -> Assessment:
        risk = self._risk(event)
        agent = event.agent.value if hasattr(event.agent, "value") else event.agent

        band = (
            "critical" if risk >= 0.8
            else "high" if risk >= 0.6
            else "medium" if risk >= 0.4
            else "low"
        )

        assessment = Assessment(
            agent=AgentName(agent) if not isinstance(event.agent, AgentName) else event.agent,
            session_id=event.session_id,
            timestamp=event.timestamp or time.time(),
            model=self.model,
            provider=self.name,
            probabilities={"risk": risk},
            risk_score=risk,
            band=band,
        )

        evidence_present = bool(event.evidence) and event.schema_ok
        if not evidence_present:
            # Unknown/ambiguous evidence -> default-deny: VERIFY, never auto-safe.
            assessment.needs_verify = True
            assessment.band = "unknown"
            return assessment

        if risk >= 0.8:
            assessment.escalate = True
        elif risk >= 0.6:
            assessment.pause = True
        elif risk >= 0.4:
            assessment.steer = True
        else:
            assessment.finished = risk < 0.1  # only very low risk may be complete
        return assessment
