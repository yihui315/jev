"""Shared helpers for the four-agent adapters (P2).

Design rules enforced here (ADR-0003 / core contract):
  * ``session_id`` is the PRIMARY identity. An event with no resolvable
    ``session_id`` raises :class:`UnidentifiableEvent` and is NEVER ingested
    (it must not be attributed to a cwd or a fabricated identity).
  * ``event_id`` / ``delivery_id`` are derived deterministically so at-least-once
    delivery + redelivery dedup works at the state store.
  * Secrets/PII are redacted before anything is persisted or logged. Long
    free-text is truncated and hashed.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional

# --- status vocabulary for capability reports -------------------------------
STATUS_CONNECTED = "CONNECTED"
STATUS_DEGRADED = "DEGRADED"
STATUS_ADAPTER_DISABLED = "ADAPTER_DISABLED"
STATUS_UNVERIFIED = "UNVERIFIED"

# Keys whose VALUES are treated as secrets and never persisted.
_SECRET_KEY_RE = re.compile(
    r"(token|secret|password|passwd|api[_-]?key|authorization|auth|bearer|"
    r"cookie|credential|private|signature|session[_-]?key|apikey|"
    r"access[_-]?token|refresh[_-]?token|client[_-]?secret)",
    re.IGNORECASE,
)
# Keys whose values may contain free-text/PII -> truncate + hash only.
_FREETEXT_KEY_RE = re.compile(
    r"(prompt|message|content|text|response|output|input|command|args|"
    r"instruction|reason|details|body|stdout|stderr|transcript)",
    re.IGNORECASE,
)

MAX_TEXT_LEN = 240


class UnidentifiableEvent(ValueError):
    """Raised when an event carries no resolvable primary identity.

    Per ADR-0003 (``session_id`` > ``cwd``) and the P2 task rule
    "no session_id -> do not enter the state store", the adapter refuses to
    fabricate an identity. The caller must spool/drop the event and count it.
    """


def redact(value: Any, *, _depth: int = 0) -> Any:
    """Return a JSON-safe, secret-free, PII-light copy of ``value``.

    * dict keys matching the secret pattern -> ``"[REDACTED]"``
    * free-text keys -> truncated to ``MAX_TEXT_LEN`` chars + ``sha256:`` tag
    * never raises; unknown types are coerced to ``str`` (truncated)
    """
    if _depth > 8:
        return "[DEPTH_LIMIT]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) > MAX_TEXT_LEN:
            digest = hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:16]
            return f"{value[:MAX_TEXT_LEN]}…[sha256:{digest}]"
        return value
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for k, v in value.items():
            key = str(k)
            if _SECRET_KEY_RE.search(key):
                out[key] = "[REDACTED]"
            elif _FREETEXT_KEY_RE.search(key) and isinstance(v, str):
                out[key] = redact(v, _depth=_depth + 1)
            else:
                out[key] = redact(v, _depth=_depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(v, _depth=_depth + 1) for v in value][:50]
    return redact(str(value), _depth=_depth + 1)


def stable_event_id(agent: str, native_id: Optional[str], *parts: Any) -> str:
    """Deterministic per-emitted-event id.

    Prefer the agent-native unique id; otherwise hash the identifying tuple so
    the same logical event always maps to the same ``event_id`` (redelivery
    dedup still works).
    """
    if native_id:
        return f"{agent}-{native_id}"
    canon = json.dumps([agent, *parts], sort_keys=True, default=str)
    return f"{agent}-{hashlib.sha256(canon.encode('utf-8')).hexdigest()[:24]}"


def delivery_id(*, provided: Optional[str] = None, event_id: str) -> str:
    """Unique per delivery ATTEMPT. Transport supplies it when available."""
    if provided:
        return str(provided)
    return f"dlv-{uuid.uuid4().hex[:16]}"


def now_ts(value: Any = None) -> float:
    """Coerce a timestamp (epoch s/ms or ISO-8601) to epoch seconds."""
    if value is None:
        return time.time()
    if isinstance(value, (int, float)):
        v = float(value)
        # heuristics: ms if it looks like ms
        if v > 1e12:
            v /= 1000.0
        return v
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return time.time()
        try:
            return float(s)
        except ValueError:
            pass
        try:
            from datetime import datetime, timezone

            iso = s.replace("Z", "+00:00")
            dt = datetime.fromisoformat(iso)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except Exception:  # noqa: BLE001
            return time.time()
    return time.time()


def pick(d: Dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def deep_get(d: Any, *path: str) -> Any:
    cur = d
    for p in path:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return None
    return cur


def evidence_bundle(kind: str, native: Dict[str, Any], fields: Iterable[str]) -> Dict[str, Any]:
    """Build a redacted evidence dict for an AEP envelope."""
    chosen: Dict[str, Any] = {"raw_kind": kind}
    for f in fields:
        if f in native:
            chosen[f] = native[f]
    return redact(chosen)
