"""Core orchestration engine.

Implements the P1 data-flow skeleton:

    Agent Event -> Adapter -> AEP -> Normalized Session State ->
    Evidence Snapshot -> Jev Assessment -> Deterministic Policy ->
    Signed(Unsigned in P1) Directive -> Actuator -> Visual Projection

Per ADR-0002/0004, Jev only scores; Policy (policy.py) is the sole actuation
authority; the directive is UNSIGNED in P1 (signing is P4/P5); and the actuator
is disabled (actuator.py), so every directive resolves to ``acted=False``.

The engine also maintains an in-process subscriber list so the server can push
live projections over WebSocket.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Callable, Dict, List, Optional

from . import policy
from .actuator import Actuator
from .config import Config
from .providers.base import JevProvider
from .schemas import (
    AEPEnvelope,
    Directive,
    DirectiveAction,
    EvidenceSnapshot,
    SessionState,
    SessionStatus,
)
from .state_store import StateStore


def _fingerprint(evidence: Dict[str, Any]) -> str:
    canon = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


class CoreEngine:
    def __init__(
        self,
        store: StateStore,
        provider: JevProvider,
        actuator: Actuator,
        config: Config,
    ):
        self.store = store
        self.provider = provider
        self.actuator = actuator
        self.config = config
        self._subscribers: List[Callable[[Dict[str, Any]], None]] = []

    # -- live projection subscribers ----------------------------------------
    def subscribe(self, cb: Callable[[Dict[str, Any]], None]) -> None:
        if cb not in self._subscribers:
            self._subscribers.append(cb)

    def _broadcast(self, msg: Dict[str, Any]) -> None:
        for cb in list(self._subscribers):
            try:
                cb(msg)
            except Exception:  # noqa: BLE001
                pass

    # -- ingest -------------------------------------------------------------
    def ingest(self, event: AEPEnvelope) -> Dict[str, Any]:
        # 1) Dedup on (session_id, event_id) and on delivery_id.
        if self.store.seen(event.session_id, event.event_id, event.delivery_id):
            return {
                "deduped": True,
                "session_id": event.session_id,
                "event_id": event.event_id,
            }

        now = time.time()
        existing = self.store.get_session(event.session_id)
        if existing is None:
            sess = SessionState(
                session_id=event.session_id,
                agent=event.agent,
                project=event.project,
                cwd=event.cwd,
                status=SessionStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            )
        else:
            sess = existing

        # 2) Persist the raw AEP event.
        self.store.add_event(event)

        # 3) Evidence snapshot (if provided) -> fingerprint.
        evidence_hash: Optional[str] = None
        if event.evidence is not None:
            fp = _fingerprint(event.evidence)
            snap = EvidenceSnapshot(
                evidence_id=f"ev_{event.event_id}",
                session_id=event.session_id,
                source_event_id=event.event_id,
                timestamp=now,
                fingerprint=fp,
                summary=json.dumps(event.evidence, sort_keys=True)[:200],
                complete=bool(event.schema_ok),
            )
            self.store.add_evidence(snap)
            evidence_hash = fp
            sess.evidence_count += 1

        # 4) Jev assessment (probabilities only).
        assessment = self.provider.assess(event, sess)

        # 5) Deterministic policy -> single directive action.
        action = policy.decide(assessment, sess.status)

        directive = Directive(
            directive_id=f"dir_{event.session_id}_{event.event_id}",
            session_id=event.session_id,
            timestamp=now,
            action=action,
            target_actuator="noop",
            policy_version=self.config.policy.version,
            evidence_hash=evidence_hash,
            signed=False,        # P1: UNSIGNED
            signature=None,
        )

        # 6) Actuate (disabled in P1 -> acted=False, reason=actuation_disabled).
        result = self.actuator.execute(directive)
        directive.acted = bool(result.get("acted", False))
        directive.actuation_reason = result.get("reason")

        # 7) Update normalized session state.
        if action == DirectiveAction.ESCALATE:
            sess.status = SessionStatus.ESCALATED
        elif action == DirectiveAction.PAUSE:
            sess.status = SessionStatus.PAUSED
        elif action == DirectiveAction.VERIFY:
            sess.status = SessionStatus.AWAIT
        elif action == DirectiveAction.FINISH:
            sess.status = SessionStatus.FINISHED
        sess.last_event_id = event.event_id
        sess.event_count += 1
        sess.updated_at = now
        sess.current_assessment = assessment.model_dump(mode="json")
        sess.current_directive = directive.model_dump(mode="json")
        self.store.upsert_session(sess)

        # 8) Persist assessment + directive + immutable audit tape (hash chain).
        self.store.add_assessment(assessment)
        self.store.add_directive(directive)
        self.store.append_audit(
            event_id=event.event_id,
            timestamp=now,
            session_id=event.session_id,
            agent=str(event.agent.value if hasattr(event.agent, "value") else event.agent),
            project=event.project,
            assessment=f"{assessment.band}:{assessment.risk_score:.3f}",
            directive=f"{directive.action.value}:{directive.directive_id}",
            evidence_hash=evidence_hash,
        )

        out = {
            "session_id": event.session_id,
            "event_id": event.event_id,
            "action": directive.action.value,
            "directive": directive.model_dump(mode="json"),
            "assessment": assessment.model_dump(mode="json"),
            "evidence_hash": evidence_hash,
            "acted": directive.acted,
            "actuation_reason": directive.actuation_reason,
        }

        # 9) Live projection.
        self._broadcast(out)
        return out
