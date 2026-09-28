"""Wave 4 — JCC live event bus + SSE projection.

A small, thread-safe in-process pub/sub that turns the engine's internal
broadcast (``CoreEngine._broadcast``) into the TEN typed events the Visual
Command Center consumes over ``GET /api/v1/events/stream``:

    session.created, session.updated, assessment.created, directive.created,
    directive.acted, audit.appended, provider.changed, budget.changed,
    shadow.changed, agent.changed

Design properties
----------------
* **Replay / resume** — every event carries a stable ``event_id`` and is kept
  in a bounded ring buffer. A reconnecting client sends ``Last-Event-ID``; the
  server replays only the events AFTER that id, so the client never sees a
  duplicate it already applied.
* **Dedupe** — re-publishing the same ``event_id`` is a no-op (idempotent
  upstream retries cannot flood the stream).
* **Projection, not truth** — the bus only READS engine output and forwards it;
  it never writes state.
* **No hang on connect** — the SSE endpoint emits an immediate ``: connected``
  comment and periodic ``: ping`` heartbeats, so a client with no pending
  events still receives bytes (keeps proxies / test clients alive).
"""
from __future__ import annotations

import asyncio
import collections
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

#: The ten event types the v1.2 UI subscribes to.
EVENT_TYPES = {
    "session.created",
    "session.updated",
    "assessment.created",
    "directive.created",
    "directive.acted",
    "audit.appended",
    "provider.changed",
    "budget.changed",
    "shadow.changed",
    "agent.changed",
}


def format_sse(ev: Dict[str, Any]) -> str:
    """Render one event as an SSE frame (id / event / data)."""
    lines = []
    if ev.get("event_id"):
        lines.append(f"id: {ev['event_id']}")
    lines.append(f"event: {ev['type']}")
    lines.append(f"data: {_dumps(ev['data'])}")
    return "\n".join(lines) + "\n\n"


def _dumps(data: Any) -> str:
    import json

    return json.dumps(data, default=str)


class EventBus:
    """Thread-safe pub/sub with bounded replay + idempotent dedupe."""

    def __init__(self, maxlen: int = 2000) -> None:
        self._lock = threading.Lock()
        self._subscribers: set[Callable[[Dict[str, Any]], None]] = set()
        self._ring: "collections.deque[Dict[str, Any]]" = collections.deque(maxlen=maxlen)
        self._seen_ids: set[str] = set()
        self._seen_sessions: set[str] = set()

    # -- publish / subscribe ------------------------------------------------
    def publish(
        self,
        etype: str,
        data: Any,
        event_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Publish one typed event. Returns the event, or None if deduped."""
        if etype not in EVENT_TYPES:
            # Fail-closed on a typo'd type rather than silently broadcasting an
            # unknown event the client cannot route.
            raise ValueError(f"unknown event type: {etype!r}")
        eid = event_id or uuid.uuid4().hex
        with self._lock:
            if eid in self._seen_ids:
                return None
            self._seen_ids.add(eid)
            ev = {"type": etype, "data": data, "event_id": eid, "ts": time.time()}
            self._ring.append(ev)
            subs = list(self._subscribers)
        for s in subs:
            s(ev)
        return ev

    def subscribe(self, cb: Callable[[Dict[str, Any]], None]) -> Callable[[Dict[str, Any]], None]:
        with self._lock:
            self._subscribers.add(cb)
        return cb

    def unsubscribe(self, cb: Callable[[Dict[str, Any]], None]) -> None:
        with self._lock:
            self._subscribers.discard(cb)

    # -- resume -------------------------------------------------------------
    def replay_since(self, last_event_id: Optional[str]) -> List[Dict[str, Any]]:
        """Events in the ring AFTER ``last_event_id`` (all of them if unknown).

        An unknown ``last_event_id`` (evicted from the bounded ring, or a stale
        reconnect) returns the FULL buffer so the client can re-sync; the client
        dedupes by ``event_id`` on its side. Returning nothing would leave a
        reconnecting client silently stalled with no events to apply.
        """
        with self._lock:
            ring = list(self._ring)
        if not last_event_id:
            return ring
        out: List[Dict[str, Any]] = []
        found = False
        for ev in ring:
            if found:
                out.append(ev)
            elif ev["event_id"] == last_event_id:
                found = True
        if not found:
            return ring
        return out

    # -- engine translation -------------------------------------------------
    def feed_engine_out(self, out: Dict[str, Any]) -> None:
        """Translate one engine broadcast (``CoreEngine.ingest`` output) into
        the typed events the UI consumes. Idempotent per engine event id."""
        sid = out.get("session_id")
        action = out.get("action")
        assessment = out.get("assessment") or {}
        directive = out.get("directive") or {}
        ev_hash = out.get("evidence_hash")
        acted = out.get("acted")
        reason = out.get("actuation_reason")
        sess_status = out.get("session_status")
        eid_base = out.get("event_id") or uuid.uuid4().hex
        agent = assessment.get("agent") or directive.get("agent")

        # session.created vs session.updated
        if sid:
            kind = "updated" if sid in self._seen_sessions else "created"
            self._seen_sessions.add(sid)
            self.publish(
                f"session.{kind}",
                {"session_id": sid, "status": sess_status, "event_id_src": eid_base},
                event_id=f"{eid_base}:session.{kind}",
            )

        if assessment:
            self.publish(
                "assessment.created",
                {
                    "session_id": sid,
                    "agent": agent,
                    "band": assessment.get("band"),
                    "risk_score": assessment.get("risk_score"),
                    "escalate": assessment.get("escalate"),
                    "provider_verified": assessment.get("provider_verified"),
                    "evidence_hash": ev_hash,
                },
                event_id=f"{eid_base}:assessment",
            )

        if directive:
            self.publish(
                "directive.created",
                {
                    "session_id": sid,
                    "action": action,
                    "directive_id": directive.get("directive_id"),
                    "policy_version": directive.get("policy_version"),
                    "signed": directive.get("signed"),
                },
                event_id=f"{eid_base}:directive.created",
            )

        if acted:
            self.publish(
                "directive.acted",
                {
                    "session_id": sid,
                    "directive_id": directive.get("directive_id"),
                    "acted": acted,
                    "reason": reason,
                },
                event_id=f"{eid_base}:directive.acted",
            )

        self.publish(
            "audit.appended",
            {
                "session_id": sid,
                "event_id": eid_base,
                "assessment": assessment.get("band") if assessment else None,
                "directive": action,
            },
            event_id=f"{eid_base}:audit",
        )
        self.publish(
            "budget.changed",
            {"session_id": sid, "action": action},
            event_id=f"{eid_base}:budget",
        )
        self.publish(
            "shadow.changed",
            {"session_id": sid, "shadow": True, "acted": acted, "reason": reason},
            event_id=f"{eid_base}:shadow",
        )
        if agent:
            self.publish(
                "agent.changed",
                {"agent": agent, "session_id": sid, "status": sess_status},
                event_id=f"{eid_base}:agent",
            )

    def publish_provider_changed(self, provider: Dict[str, Any]) -> None:
        """Explicit provider-mode change (rare in this build; type supported)."""
        self.publish(
            "provider.changed",
            provider,
            event_id=f"provider:{provider.get('mode', 'unknown')}:{time.time()}",
        )
