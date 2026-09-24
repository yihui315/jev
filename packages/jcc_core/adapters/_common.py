"""Shared helpers for the four-agent adapters (P2).

Design rules enforced here (ADR-0003 / core contract):
  * ``session_id`` is the PRIMARY identity. An event with no resolvable
    ``session_id`` raises :class:`UnidentifiableEvent` and is NEVER ingested
    (it must not be attributed to a cwd or a fabricated identity).
  * ``event_id`` / ``delivery_id`` are derived deterministically so at-least-once
    delivery + redelivery dedup works at the state store.
  * Secrets/PII are redacted before anything is persisted or logged. The
    redaction kernel now lives in the NEUTRAL module :mod:`jcc_core.redaction`
    (ADR-0007 D-3) so the telemetry plane (``jcc_core.core``) and the adapter
    plane both depend on it DOWNWARD, instead of core importing adapters.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any, Dict, Iterable, Optional

# Redaction kernel re-exported for backwards compatibility. Its canonical home
# is :mod:`jcc_core.redaction` (a neutral sibling of core and adapters); this is
# a DOWNWARD import (adapters -> neutral), and older callers that imported these
# names from ``adapters._common`` keep working unchanged.
from ..redaction import (  # noqa: F401
    DEPTH_LIMIT_MARK,
    MAX_SCRUB_DEPTH,
    MAX_TEXT_LEN,
    redact,
    scrub_str,
    scrub_value_secrets,
)

# --- status vocabulary for capability reports -------------------------------
STATUS_CONNECTED = "CONNECTED"
STATUS_DEGRADED = "DEGRADED"
STATUS_ADAPTER_DISABLED = "ADAPTER_DISABLED"
STATUS_UNVERIFIED = "UNVERIFIED"


class UnidentifiableEvent(ValueError):
    """Raised when an event carries no resolvable primary identity.

    Per ADR-0003 (``session_id`` > ``cwd``) and the P2 task rule
    "no session_id -> do not enter the state store", the adapter refuses to
    fabricate an identity. The caller must spool/drop the event and count it.
    """


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
