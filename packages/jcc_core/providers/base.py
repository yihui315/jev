"""Jev provider abstraction (scorer-only, per ADR-0002 / ADR-0004).

A ``JevProvider`` turns an AEP event + current session state into an
``Assessment`` of PROBABILITIES ONLY. It MUST NOT emit directives or take
actions. Concrete providers: ``DeterministicMockProvider`` (default, zero
external keys), ``DisabledProvider`` (no-op), and ``RealProvider`` (UNVERIFIED
stub, disabled until key + feature flag present).
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..schemas import AEPEnvelope, Assessment, SessionState


class JevProvider(ABC):
    name = "base"
    verified: bool = False  # provider implementations set this

    @abstractmethod
    def assess(self, event: AEPEnvelope, session: SessionState) -> Assessment:
        """Return a probability-only Assessment for the given event."""
        raise NotImplementedError

    def is_available(self) -> bool:
        return True
