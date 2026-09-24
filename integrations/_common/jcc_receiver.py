"""Hardened inbound webhook receiver for JCC (Hermes / any signed remote sender).

Security controls (all fail-closed; see ADR-0005 + P2 task rules):
  1. Body size limit          -> ``payload_too_large``
  2. Required headers         -> ``missing_header``
  3. Timestamp window (+-skew)-> ``timestamp_out_of_window``
  4. HMAC-SHA256 (constant-time compare) over
     ``f"{timestamp}.{delivery_id}.{agent}." + raw_body`` -> ``bad_signature``
  5. delivery_id replay cache (checked AFTER signature) -> ``replay``
  6. JSON parse               -> ``bad_json``
  7. SESSION ISOLATION: a ``session_id`` may be bound to exactly ONE agent
     source; any cross-agent reuse is rejected -> ``session_isolation``
  8. Adapter normalize + ingest (identity must be ``session_id``).

Nothing reaches the engine unless ALL checks pass. Rejections are recorded
(reason only, never the raw secret/body).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Dict, Mapping, Optional

HDR_SIGNATURE = "X-JCC-Signature"
HDR_TIMESTAMP = "X-JCC-Timestamp"
HDR_DELIVERY = "X-JCC-Delivery-Id"
HDR_AGENT = "X-JCC-Agent"

SUPPORTED_AGENTS = ("workbuddy", "claude", "codex", "hermes")

#: Rejection reasons (also returned in the audit-safe result).
REASON_SIZE = "payload_too_large"
REASON_MISSING = "missing_header"
REASON_TS = "timestamp_out_of_window"
REASON_SIG = "bad_signature"
REASON_REPLAY = "replay"
REASON_JSON = "bad_json"
REASON_ISOLATION = "session_isolation"
REASON_AGENT = "unsupported_agent"
REASON_IDENTITY = "unidentifiable"


class ReceiverRejection(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class ReplayCache:
    """Bounded, TTL'd delivery_id replay cache (thread-safe)."""

    def __init__(self, ttl: float = 600.0, maxsize: int = 20000):
        self.ttl = ttl
        self.maxsize = maxsize
        self._seen: "OrderedDict[str, float]" = OrderedDict()
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        while self._seen:
            key, ts = next(iter(self._seen.items()))
            if now - ts > self.ttl or len(self._seen) > self.maxsize:
                self._seen.popitem(last=False)
            else:
                break

    def check_and_add(self, key: str, now: Optional[float] = None) -> bool:
        """True if NEW (accepted); False if this is a replay."""
        now = now if now is not None else time.time()
        with self._lock:
            self._prune(now)
            if key in self._seen:
                return False
            self._seen[key] = now
            return True


class SessionIsolation:
    """Binds each ``session_id`` to exactly one agent source."""

    def __init__(self):
        self._map: Dict[str, str] = {}
        self._lock = threading.Lock()

    def check_and_bind(self, agent: str, session_id: str) -> bool:
        with self._lock:
            owner = self._map.get(session_id)
            if owner is None:
                self._map[session_id] = agent
                return True
            return owner == agent


class SecureWebhookReceiver:
    def __init__(
        self,
        secret: str,
        *,
        max_skew: float = 300.0,
        max_body: int = 64 * 1024,
        replay_ttl: float = 600.0,
    ):
        if not secret:
            raise ValueError("webhook secret is required (fail-closed)")
        self._secret = secret.encode("utf-8")
        self.max_skew = max_skew
        self.max_body = max_body
        self.replays = ReplayCache(ttl=replay_ttl)
        self.isolation = SessionIsolation()
        self.rejections: Dict[str, int] = {}

    # -- signing -------------------------------------------------------------
    @staticmethod
    def canonical(timestamp: str, delivery_id: str, agent: str, body: bytes) -> bytes:
        return f"{timestamp}.{delivery_id}.{agent}.".encode("utf-8") + body

    def sign(self, timestamp: str, delivery_id: str, agent: str, body: bytes) -> str:
        mac = hmac.new(self._secret, self.canonical(timestamp, delivery_id, agent, body), hashlib.sha256)
        return "sha256=" + mac.hexdigest()

    def sender_headers(
        self, agent: str, body: bytes, *, delivery_id: Optional[str] = None, timestamp: Optional[float] = None
    ) -> Dict[str, str]:
        ts = str(int(timestamp if timestamp is not None else time.time()))
        did = delivery_id or os_urandom_hex()
        return {
            HDR_AGENT: agent,
            HDR_TIMESTAMP: ts,
            HDR_DELIVERY: did,
            HDR_SIGNATURE: self.sign(ts, did, agent, body),
            "Content-Type": "application/json",
        }

    # -- verification --------------------------------------------------------
    def _hdr(self, headers: Mapping[str, str], name: str) -> Optional[str]:
        for k, v in headers.items():
            if k.lower() == name.lower():
                return v
        return None

    def verify(self, *, headers: Mapping[str, str], body: bytes, now: Optional[float] = None) -> Dict[str, Any]:
        """Verify authenticity/integrity/replay. Returns the parsed payload."""
        now = now if now is not None else time.time()
        if len(body) > self.max_body:
            raise ReceiverRejection(REASON_SIZE)
        agent = self._hdr(headers, HDR_AGENT)
        ts_raw = self._hdr(headers, HDR_TIMESTAMP)
        did = self._hdr(headers, HDR_DELIVERY)
        sig = self._hdr(headers, HDR_SIGNATURE)
        if not all([agent, ts_raw, did, sig]):
            raise ReceiverRejection(REASON_MISSING)
        if agent not in SUPPORTED_AGENTS:
            raise ReceiverRejection(REASON_AGENT)
        try:
            ts = float(ts_raw)
        except (TypeError, ValueError):
            raise ReceiverRejection(REASON_TS)
        if abs(now - ts) > self.max_skew:
            raise ReceiverRejection(REASON_TS)
        expected = self.sign(ts_raw, str(did), str(agent), body)
        if not hmac.compare_digest(expected, str(sig)):
            raise ReceiverRejection(REASON_SIG)
        # replay check AFTER signature (unauthenticated traffic can't poison cache)
        if not self.replays.check_and_add(f"{agent}:{did}", now):
            raise ReceiverRejection(REASON_REPLAY)
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:  # noqa: BLE001
            raise ReceiverRejection(REASON_JSON)
        if not isinstance(payload, dict):
            raise ReceiverRejection(REASON_JSON)
        return payload

    def handle(
        self,
        *,
        headers: Mapping[str, str],
        body: bytes,
        engine: Any = None,
        now: Optional[float] = None,
        dispatcher: Optional[Callable[[str, Dict[str, Any], Any], Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Full pipeline: verify -> isolate -> normalize -> ingest."""
        agent = (self._hdr(headers, HDR_AGENT) or "").lower()
        did = self._hdr(headers, HDR_DELIVERY)
        try:
            payload = self.verify(headers=headers, body=body, now=now)
        except ReceiverRejection as exc:
            self.rejections[exc.reason] = self.rejections.get(exc.reason, 0) + 1
            return {"accepted": False, "reason": exc.reason}

        # session isolation is enforced on the resolved identity
        session_id = (
            payload.get("session_id")
            or payload.get("sessionId")
            or (payload.get("context") or {}).get("session_id")
            or (payload.get("event_data") or {}).get("session_id")
            or (payload.get("payload") or {}).get("session_id")
        )
        if session_id is not None and not self.isolation.check_and_bind(agent, str(session_id)):
            self.rejections[REASON_ISOLATION] = self.rejections.get(REASON_ISOLATION, 0) + 1
            return {"accepted": False, "reason": REASON_ISOLATION}

        payload = dict(payload)
        payload["_delivery_id"] = did
        if dispatcher is not None:
            res = dispatcher(agent, payload, engine)
        else:
            # works whether imported as a package module or as a top-level module
            try:
                from .jcc_emit import emit_event
            except ImportError:  # pragma: no cover - top-level import path
                from jcc_emit import emit_event

            res = emit_event(agent, payload, engine=engine)
        if res.get("status") == "dropped":
            self.rejections[REASON_IDENTITY] = self.rejections.get(REASON_IDENTITY, 0) + 1
            return {"accepted": False, "reason": REASON_IDENTITY, "detail": res.get("detail")}
        return {"accepted": True, "delivery_id": did, "agent": agent, "result": res}


def os_urandom_hex(nbytes: int = 16) -> str:
    import os

    return os.urandom(nbytes).hex()


def http_request_size_guard(content_length: Optional[str], max_body: int) -> bool:
    """Pre-read guard used by the ASGI route (reject before buffering)."""
    if content_length is None:
        return True
    try:
        return int(content_length) <= max_body
    except (TypeError, ValueError):
        return False
