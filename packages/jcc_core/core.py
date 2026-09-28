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
import logging
import time
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

from . import policy
from .actuator import Actuator
from .config import Config
from .decision import ThresholdsConfig
from .providers.base import JevProvider
from .redaction import mask_secret_key_values, scrub_value_secrets
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


def _subscriber_label(cb: Any) -> str:
    """Best-effort human label for a subscriber callback, for logs / telemetry."""
    if hasattr(cb, "__qualname__"):
        return cb.__qualname__
    if hasattr(cb, "__name__"):
        return cb.__name__
    return repr(cb)


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
        #: Subscribers registered as the engine's OWN in-process supervision
        #: subscriber (``subscribe(cb, own=True)``). Their delivery failures are
        #: HARD errors for the caller; every other (observe-only / external)
        #: subscriber is dropped with a recorded ``BROADCAST_FAILED`` rather than
        #: silently passed (P5 residual, ADR-0008 D-2/D-41).
        self._own_subscribers: List[Callable[[Dict[str, Any]], None]] = []
        # -- broadcast delivery observability (P5 residual) ------------------
        #: Failures observed in the most recent ``_broadcast`` call (per-subscriber).
        self.last_broadcast_failures: int = 0
        #: Cumulative broadcast delivery failures since this engine was built.
        self.broadcast_failure_total: int = 0
        #: Outcome record of the most recent ``_broadcast`` (machine-readable).
        self.last_broadcast: Dict[str, Any] = {
            "event_id": None,
            "sent": 0,
            "failed": 0,
            "failures": [],
            "mode": "unknown",
            "timestamp": 0.0,
        }
        #: Telemetry history of every broadcast outcome (audit/telemetry stream).
        self._broadcast_events: List[Dict[str, Any]] = []

    # -- live projection subscribers ----------------------------------------
    def subscribe(
        self,
        cb: Callable[[Dict[str, Any]], None],
        *,
        own: bool = False,
    ) -> None:
        """Register a live-projection subscriber.

        ``own=True`` marks the subscriber as the engine's OWN in-process
        supervision subscriber (e.g. in-process arbitration). Its delivery
        failures are HARD errors for the caller -- ``_broadcast`` re-raises them.
        Every other subscriber (observe-only / external, e.g. the Sentinel or the
        WebSocket projection) is dropped with a recorded ``BROADCAST_FAILED``
        rather than silently passed (the P5 residual this closes).
        """
        if cb not in self._subscribers:
            self._subscribers.append(cb)
        if own and cb not in self._own_subscribers:
            self._own_subscribers.append(cb)

    def _is_observe_only(self) -> bool:
        """True when the platform is in observe-only mode (actuation disabled).

        Derived from ``config.actuation.enabled`` (ADR-0004: actuation OFF in
        P0-P2). A broadcast failure MUST be recorded even here -- never a silent
        success.
        """
        actuation = getattr(self.config, "actuation", None)
        return not bool(getattr(actuation, "enabled", True))

    def _broadcast(self, msg: Dict[str, Any]) -> None:
        """Deliver ``msg`` to every live subscriber WITHOUT swallowing a failure.

        A subscriber raising used to be caught by a bare ``except Exception: pass``
        so the failure read as success -- the same "instrument says success but did
        nothing" defect class ADR-0008 D-2/D-41 forbid, and a P5 residual that MUST
        be closed. Now every delivery is tracked per-subscriber; on failure we:

        * log at ERROR with the subscriber id, the message id, and the exception;
        * bump the engine's failure metrics (``last_broadcast_failures`` /
          ``broadcast_failure_total``);
        * append an audit/telemetry record to ``_broadcast_events`` (observable
          even in observe-only mode);
        * for the engine's OWN supervision subscriber (``subscribe(cb, own=True)``)
          re-raise so the caller sees the hard error;
        * for every other (observe-only / external) subscriber, drop with a recorded
          ``BROADCAST_FAILED`` and continue -- never silently pass.
        """
        msg_id = msg.get("event_id") or msg.get("session_id") or "<unknown>"
        sent = 0
        failures: List[Dict[str, Any]] = []
        reraise: Optional[BaseException] = None

        for cb in list(self._subscribers):
            is_own = cb in self._own_subscribers
            try:
                cb(msg)
                sent += 1
            except Exception as exc:  # noqa: BLE001 -- recorded, never swallowed
                label = _subscriber_label(cb)
                failures.append({
                    "subscriber": label,
                    "event_id": msg_id,
                    "error": f"{type(exc).__name__}: {exc}"[:400],
                })
                self.broadcast_failure_total += 1
                log.error(
                    "BROADCAST_FAILED subscriber=%s event_id=%s error=%s: %s",
                    label, msg_id, type(exc).__name__, exc,
                )
                if is_own and reraise is None:
                    reraise = exc
                continue

        mode = "observe-only" if self._is_observe_only() else "actuation"
        record: Dict[str, Any] = {
            "event_id": msg_id,
            "sent": sent,
            "failed": len(failures),
            "failures": failures,
            "mode": mode,
            "timestamp": time.time(),
        }
        self.last_broadcast_failures = len(failures)
        self.last_broadcast = record
        self._broadcast_events.append(record)
        if failures:
            log.error(
                "BROADCAST_FAILED summary event_id=%s sent=%d failed=%d mode=%s",
                msg_id, sent, len(failures), mode,
            )

        if reraise is not None:
            # The engine's own supervision subscriber failed: a HARD error, not a
            # best-effort drop. Re-raise so the caller cannot read it as success.
            raise reraise

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

        # 0) Secret redaction BEFORE anything is persisted (defense in depth).
        #    Two passes over BOTH payload and evidence, masked keys FIRST:
        #      (i)  key-name pass  -> mask the VALUE of any secret-NAMED key
        #           (api_key / token / secret / password / authorization / …);
        #      (ii) value-pattern pass -> mask secret-looking VALUES inside every
        #           string leaf (a secret embedded in free-form text under a
        #           non-secret key).
        #    The agent-side normalizers already key-name-redact via redact(), but
        #    POST /api/assess has NO adapter in its path, so this chokepoint is
        #    the ONLY protection for that ingress: every ingest path (HTTP +
        #    in-process) passes through it, and no producer may bypass it.
        #    Kernel: jcc_core.redaction (ADR-0007 D-1, D-3).
        event.payload = scrub_value_secrets(mask_secret_key_values(event.payload))
        if event.evidence is not None:
            event.evidence = scrub_value_secrets(mask_secret_key_values(event.evidence))

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

    # -- v1.2 Wave 7: Jev shadow surface -----------------------------------
    def shadow_summary(self) -> Optional[Dict[str, Any]]:
        """Jev shadow supervision summary over LIVE telemetry ONLY.

        The shadow gate counts ONLY ``record_source == 'live'`` events. Replay /
        demo / seed records are deliberately excluded so the supervision summary
        cannot be padded by synthetic data (the D-34-style "fake-clean" defect
        class). Returns ``None`` when there is no live telemetry at all — the
        scene layer maps that to an UNKNOWN section (an honest gap, never a
        coerced empty-clean dict), so a shadow with nothing to supervise is
        rendered as "unknown", not "verified none".

        This is a READ-ONLY projection of persisted events; it never writes.
        """
        rows = self.store.get_shadow_records("live")
        if not rows:
            return None

        by_agent: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            agent = str(r["agent"])
            bucket = by_agent.get(agent)
            if bucket is None:
                bucket = by_agent[agent] = {
                    "agent": agent,
                    "live_event_count": 0,
                    "session_ids": set(),
                    "event_types": {},
                    "latest_ts": 0.0,
                }
            bucket["live_event_count"] += 1
            if r["session_id"] is not None:
                bucket["session_ids"].add(r["session_id"])
            et = str(r["event_type"] or "unknown")
            bucket["event_types"][et] = bucket["event_types"].get(et, 0) + 1
            ts = r["timestamp"] or 0.0
            if ts > bucket["latest_ts"]:
                bucket["latest_ts"] = ts

        agents = [
            {
                "agent": a,
                "live_event_count": b["live_event_count"],
                "distinct_session_count": len(b["session_ids"]),
                "event_type_distribution": b["event_types"],
                "latest_live_ts": b["latest_ts"],
            }
            for a, b in sorted(by_agent.items())
        ]

        return {
            "live_record_count": len(rows),
            "distinct_agent_count": len(by_agent),
            "agents": agents,
            "gate": "LIVE_ONLY",
            "record_source_filter": "live",
            "excluded_record_sources": ["replay", "demo", "seed"],
            "observed_at": time.time(),
            "note": (
                "Jev shadow surface: supervision summary over LIVE telemetry only; "
                "replay/demo/seed records are excluded by the shadow gate."
            ),
        }
