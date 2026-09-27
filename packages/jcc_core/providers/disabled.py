"""Disabled Jev provider — a *declared non-scoring abstention* (safe no-op).

Used when ``jev.provider=disabled`` or when ``real`` is requested but the key /
feature flag are absent (fail-closed fallback).

It makes **no assessment**: it supplies no numeric channels (empty
``probabilities``, falsy ``score``) and **none** of the six required signal
booleans — those are unknowable to it, and choosing values to reach an action
would re-introduce provider-side directive selection that ADR-0008 D-32 removed.
Instead it **declares** non-scoring abstention in the free-form ``raw`` map
(``raw["provider_abstained"] is True``).

Per ADR-0008 *Errata E-3* (spec §2.3) that declaration makes the absence of the
six signals *incomplete evidence*, so the kernel resolves it to **VERIFY**
(row 5) — **not** an exact blocker. Contrast ``real`` (``verified=False``, not
declared-abstaining), which stays ``ESCALATE`` via ``provider_unverified``.
"""
from __future__ import annotations

import time

from ..schemas import AEPEnvelope, AgentName, Assessment, SessionState
from .base import JevProvider


class DisabledProvider(JevProvider):
    name = "disabled"
    mode = "DISABLED"   # §7D — declared, never inferred
    verified = True

    def assess(self, event: AEPEnvelope, session: SessionState) -> Assessment:
        agent = event.agent.value if hasattr(event.agent, "value") else event.agent
        return Assessment(
            agent=AgentName(agent) if not isinstance(event.agent, AgentName) else event.agent,
            session_id=event.session_id,
            timestamp=event.timestamp or time.time(),
            model="disabled",
            provider=self.name,
            probabilities={},             # no numeric channel supplied
            risk_score=0.0,               # falsy score — still non-scoring
            band="disabled",              # coerces to UNKNOWN risk (not a band pick)
            # D-31: propagate this provider's verification state onto the record.
            # For `disabled` this is True, which is *why* `provider_unverified`
            # does not fire (Errata E-3).
            provider_verified=self.verified,
            # Errata E-3: the DECLARATION that makes this an abstention, not a
            # silent gap. Deliberately NO signal booleans (D-32).
            raw={"provider_abstained": True},
        )

    def is_available(self) -> bool:
        return False
