"""Deterministic mock Jev provider (DEFAULT — zero external keys).

Determinism: the risk score is derived from a SHA-256 of
``session_id:event_id``, so identical inputs always produce identical signals —
the fixtures are reproducible.

Role after ADR-0008 D-32
-----------------------
The provider NO LONGER chooses the directive. It emits the *evidence the
arbitration kernel needs* and nothing else:

* the six required boolean signals (``decision.REQUIRED_SIGNAL_FIELDS``),
* a definite ``band`` — never ``"unknown"`` (an unknown band is not "no band";
  it would be read as ``band is None`` and become an EXACT_BLOCKER at EVERY risk
  level, hijacking the VERIFY path),
* ``risk_score`` identical to ``probabilities["risk"]`` — one numeric channel,
  so ``find_blockers`` can never see the provider contradict itself,
* ``provider_verified=True`` populated from ``JevProvider.verified`` (D-31).

It deliberately does **not** set the P2 directive-shaped booleans
(``escalate``/``pause``/``steer``/``finished``) and does **not** duplicate the
policy's cut points as literals: the band is classified against the ONE
threshold source (``jcc_core.decision.Thresholds``), so the provider's coarse
band and the policy's score cuts cannot disagree (R6-1/R6-2 — the live engine's
mapping is unchanged).

Safety default (ADR-0003): missing / incomplete evidence sets
``needs_verification=True`` — never auto-safe. The band stays definite, so the
risk signal is still honest: a missing-evidence event whose risk reaches the
pause/escalate cut is still absorbed by rule rows 1–4 (see the characterization
corner in ``tests/test_p3_core_integration.py``).
"""
from __future__ import annotations

import hashlib
import time

from ..decision import Thresholds
from ..schemas import AEPEnvelope, AgentName, Assessment, SessionState
from .base import JevProvider

#: Ordered low -> critical; the index is the severity ordinal understood by
#: ``decision._BAND_ORDINALS``. Both ends are DEFINITE — "unknown" is never
#: emitted (schemas.py documents "unknown"; this provider must not use it).
_BANDS: tuple[str, ...] = ("low", "medium", "high", "critical")


def _band_for(score: float, thresholds: Thresholds) -> str:
    """Classify ``score`` against the ONE threshold source — never hard-coded.

    Cuts are inclusive from below, exactly as ``decision.Thresholds`` documents:
    ``>= escalate_risk`` -> critical, ``>= pause_risk`` -> high,
    ``>= steer_risk`` -> medium, else low. Reading the cuts from the kernel's
    own ``Thresholds`` is what makes provider band == policy band by
    construction (no duplicated, drift-prone constants).
    """
    if score >= thresholds.escalate_risk:
        return _BANDS[3]
    if score >= thresholds.pause_risk:
        return _BANDS[2]
    if score >= thresholds.steer_risk:
        return _BANDS[1]
    return _BANDS[0]


class DeterministicMockProvider(JevProvider):
    name = "mock"
    mode = "MOCK"    # §7D — declared, never inferred
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
        evidence_complete = bool(event.evidence) and bool(event.schema_ok)

        # The six required booleans + the recorded-only field (D-16/R5-7/R6-3).
        # ``work_off_track`` rides along for recording and is read by NO rule.
        signals = {
            "meaningful_progress": True,
            "worker_stuck": False,
            "work_off_track": False,
            "requirements_satisfied": True,
            "needs_verification": not evidence_complete,
            "ready_to_finish": True,
            "needs_human": False,
        }

        return Assessment(
            agent=AgentName(agent) if not isinstance(event.agent, AgentName) else event.agent,
            session_id=event.session_id,
            timestamp=event.timestamp or time.time(),
            model=self.model,
            provider=self.name,
            probabilities={"risk": risk},
            risk_score=risk,                       # == probabilities["risk"]
            band=_band_for(risk, Thresholds()),    # ALWAYS definite
            provider_verified=bool(self.verified),  # D-31: explicit, not a magic band
            # P1 field name kept for back-compat (the acceptance harness reads it);
            # the kernel prefers the P3 ``needs_verification`` signal above.
            needs_verify=not evidence_complete,
            raw={
                "signals": signals,
                "evidence_complete": evidence_complete,
            },
        )
