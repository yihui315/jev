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
from .decision import ThresholdsConfig
from .providers.base import JevProvider
from .redaction import scrub_value_secrets
from .schemas import (
    AEPEnvelope,
    Assessment,
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
        thresholds: Optional[ThresholdsConfig] = None,
    ):
        self.store = store
        self.provider = provider
        self.actuator = actuator
        self.config = config
        #: The ONE threshold surface this engine resolves against (ADR-0008
        #: R6-1 / F-p3-4). ``None`` keeps the documented defaults — which is
        #: exactly why the P0-P2 path is byte-unchanged while the shadow plane is
        #: not wired. The composition root (``run._wire_shadow``) injects the
        #: SAME ``ThresholdsConfig`` object it hands the Sentinel, so the engine's
        #: action and the recorded shadow action cannot resolve from two different
        #: surfaces: with a non-empty ``per_agent`` map they used to diverge.
        self.thresholds: Optional[ThresholdsConfig] = thresholds
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

        ingest_started = time.time()

        # 0) Value-pattern scrub BEFORE anything is persisted (defense in depth):
        #    the agent-side normalizers already key-name-redact, but a secret
        #    embedded inside a free-form string under a non-secret key must also
        #    be masked. This is the single chokepoint all ingest paths (HTTP +
        #    in-process) pass through, so no producer can bypass it.
        #    Kernel: jcc_core.redaction (ADR-0007 D-3).
        event.payload = scrub_value_secrets(event.payload)
        if event.evidence is not None:
            event.evidence = scrub_value_secrets(event.evidence)

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

        # 4) Jev assessment (probabilities only). A provider exception must NOT
        #    leave the event persisted with no assessment/directive/audit row
        #    (ADR-0008 D-26): synthesize an EXACT_BLOCKER assessment -> ESCALATE,
        #    and the audit row at step 8 is still appended (no tape hole).
        try:
            assessment = self.provider.assess(event, sess)
        except Exception as exc:  # noqa: BLE001
            assessment = Assessment(
                agent=event.agent,
                session_id=event.session_id,
                timestamp=now,
                model="n/a",
                provider="error",
                band="unknown",
                needs_verify=True,
                escalate=True,
                blocker_kind="EXACT",
                blocker_reasons=["provider_error"],
                raw={"error": str(exc)[:200]},
            )

        # 4b) Latency + strict-join keys (ADR-0008 D-17 / D-22). The ingest
        #     bound is owned by core; the jev_* keys are owned by the provider
        #     and MUST NOT be clobbered.
        if assessment.raw is None:
            assessment.raw = {}
        timing = assessment.raw.setdefault("timing", {})
        timing["ingest_started_at"] = ingest_started
        assessment.raw.setdefault("event_id", event.event_id)

        # 5) Deterministic policy -> single directive action.
        #    R6-1 / F-p3-4: resolve through THIS engine's injected surface, not
        #    the module default. ``self.thresholds is None`` for P0-P2 callers,
        #    which reproduces the previous behaviour exactly.
        action = policy.decide(assessment, sess.status, thresholds=self.thresholds)

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

        # 6) Engine actuation port (ADR-0008 D-27 / D-41): PERMANENTLY INERT.
        #    The ingest path now dispatches through jcc_core.ports.ActuationPort
        #    (here: the disabled Actuator) and can no longer reach
        #    Actuator.execute. Always acted=False, executable=False,
        #    reason="shadow_mode".
        result = self.actuator.dispatch(directive)
        directive.acted = bool(result.get("acted", False))
        directive.actuation_reason = result.get("reason")
        directive.executable = bool(result.get("executable", False))

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
        # F1 (ADR-0008 P4): the engine is the writer of a ``dir_<session>_<event>``
        # id, and says so. ``origin`` is keyword-only by design -- a default there
        # would turn a forgotten argument into an unattributed row.
        self.store.add_directive(directive, origin="engine")
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
            # ADR-0008 §4: let an observe-only subscriber (Sentinel) arbitrate
            # without re-reading the store.
            "session_status": sess.status.value,
        }

        # 9) Live projection.
        self._broadcast(out)
        return out
