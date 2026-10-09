"""Durable record of what a provider was actually PROVEN to do.

WHY THIS EXISTS
---------------
Verification used to live in process memory only, so it evaporated on refresh
and on restart. A provider that had genuinely generated — MiniMax answering a
real completion, a decision round trip succeeding — came back as
「已配置 · 待验证」 the moment the page reloaded, which reads as "never worked"
even though it worked minutes ago. The operator's reasonable conclusion is that
the product is unreliable.

WHAT IS STORED, AND WHY IT IS SAFE
---------------------------------
Only the OUTCOME of a verification, never its inputs:

  * kept: provider id, when it passed, which model answered, the latency, the
    capability kind, and the discovery axis
  * never: an API key, an Authorization header, a request or response body, or
    any field that could echo a secret

That is why this is a separate table rather than a JSON blob of the probe
report: a blob would eventually carry whatever the probe happened to return.

LIVE vs LAST-VERIFIED — THE DISTINCTION THAT MATTERS
---------------------------------------------------
A remembered success is evidence that the provider COULD do the thing, not that
it can do it now. Presenting an hours-old pass as a live green is a lie with a
green dot on it, so a restored record is reported as ``STALE_VERIFIED`` — a
distinct state from ``CONNECTED``, which requires a check in this process.

The stored timestamp is what makes staleness checkable rather than a matter of
opinion: :func:`is_fresh` is the only thing that may promote a remembered record
to a live claim, and it refuses anything past its window.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

#: One row per provider, replaced on every successful verification. There is no
#: history table on purpose: the product shows a current claim, not a timeline,
#: and a growing log of past endpoints is one more place a secret could land.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS provider_verification (
  provider_id            TEXT PRIMARY KEY,
  verified_at            REAL    NOT NULL,
  verification_status    TEXT    NOT NULL,
  verification_kind      TEXT,
  verified_model         TEXT,
  latency_ms             INTEGER,
  generation_verified    INTEGER NOT NULL DEFAULT 0,
  decision_verified      INTEGER NOT NULL DEFAULT 0,
  discovery              TEXT,
  model_source           TEXT,
  base_url               TEXT,
  request_id             TEXT,
  -- Usage counts only. Token text is never persisted anywhere.
  input_tokens           INTEGER,
  output_tokens          INTEGER
);
"""

#: How long a remembered success still counts as a current claim. Past this the
#: record is reported as STALE_VERIFIED, not CONNECTED. Six hours is chosen so a
#: working day does not repeatedly flip a healthy provider back to "stale", while
#: a machine that has been off overnight does not claim live health on wake.
FRESH_WINDOW_S = 6 * 3600.0

#: Fields a caller may write. An allowlist rather than a denylist: a new field
#: added to a report cannot leak into storage by default, it has to be added here
#: on purpose.
_ALLOWED = (
    "provider_id", "verified_at", "verification_status", "verification_kind",
    "verified_model", "latency_ms", "generation_verified", "decision_verified",
    "discovery", "model_source", "base_url", "request_id",
    "input_tokens", "output_tokens",
)

#: Anything whose NAME suggests a credential is refused outright, whatever the
#: caller passes. Cheap insurance against a future caller passing a whole report.
_FORBIDDEN = ("key", "secret", "token_raw", "authorization", "password", "credential")


class VerificationStore:
    """SQLite-backed record of proven provider capability."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock = threading.Lock()
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                conn.executescript(_SCHEMA)
                conn.commit()
            finally:
                conn.close()

    @staticmethod
    def _scrub(record: Dict[str, Any]) -> Dict[str, Any]:
        """Drop anything that looks like a credential, then allowlist the rest."""
        out: Dict[str, Any] = {}
        for k, v in record.items():
            if any(bad in k.lower() for bad in _FORBIDDEN):
                continue
            if k not in _ALLOWED:
                continue
            out[k] = v
        return out

    def record(self, **fields: Any) -> Dict[str, Any]:
        """Record a verification result. Only a real pass is worth storing.

        A failure is not persisted: the store answers "was this ever proven, and
        when", and a provider that just failed has not become unproven.
        """
        status = str(fields.get("verification_status") or "")
        gen = bool(fields.get("generation_verified"))
        dec = bool(fields.get("decision_verified"))
        if status != "PASS" and not gen and not dec:
            return {}
        row = self._scrub(fields)
        row.setdefault("provider_id", "")
        if not row["provider_id"]:
            return {}
        row.setdefault("verified_at", time.time())
        row["generation_verified"] = int(gen)
        row["decision_verified"] = int(dec)

        cols = [c for c in _ALLOWED if c in row]
        placeholders = ",".join("?" for _ in cols)
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute(
                    f"INSERT OR REPLACE INTO provider_verification ({','.join(cols)}) "
                    f"VALUES ({placeholders})",
                    [row[c] for c in cols],
                )
                conn.commit()
            finally:
                conn.close()
        return row

    def get(self, provider_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cur = conn.execute(
                    "SELECT * FROM provider_verification WHERE provider_id = ?",
                    (provider_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                cols = [d[0] for d in cur.description]
            finally:
                conn.close()
        rec = dict(zip(cols, row))
        rec["generation_verified"] = bool(rec.get("generation_verified"))
        rec["decision_verified"] = bool(rec.get("decision_verified"))
        return rec

    def all(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cur = conn.execute("SELECT * FROM provider_verification")
                cols = [d[0] for d in cur.description]
                rows = cur.fetchall()
            finally:
                conn.close()
        out: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            rec = dict(zip(cols, r))
            rec["generation_verified"] = bool(rec.get("generation_verified"))
            rec["decision_verified"] = bool(rec.get("decision_verified"))
            out[rec["provider_id"]] = rec
        return out

    def forget(self, provider_id: str) -> bool:
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cur = conn.execute(
                    "DELETE FROM provider_verification WHERE provider_id = ?",
                    (provider_id,),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()


def is_fresh(record: Optional[Dict[str, Any]], *, now: Optional[float] = None,
             window_s: float = FRESH_WINDOW_S) -> bool:
    """Is a remembered verification recent enough to state as current?

    Refuses a record with no timestamp: a pass with no time attached cannot be
    aged, and an un-ageable pass is not evidence about now.
    """
    if not record:
        return False
    ts = record.get("verified_at")
    if not isinstance(ts, (int, float)) or ts <= 0:
        return False
    return ((now if now is not None else time.time()) - float(ts)) <= window_s


def restore(provider_id: str, store: VerificationStore, *,
            now: Optional[float] = None) -> Dict[str, Any]:
    """Build the display fields a provider card needs from a remembered pass.

    Returns a dict that says WHICH KIND of claim is being made:

      ``LIVE_READY``          verified in this process — the strongest claim
      ``LAST_VERIFIED_READY`` a real pass, recorded earlier; age stated
      ``NOT_VERIFIED``        nothing on record

    The middle case is the whole point. Reporting it as ready would pass a
    remembered success off as live health; reporting nothing would deny a
    success that actually happened.
    """
    rec = store.get(provider_id)
    if not rec:
        return {
            "restored": False,
            "verification_source": "NOT_VERIFIED",
            "last_verified_at": None,
        }
    age = None if now is None else (now - float(rec.get("verified_at") or 0))
    return {
        "restored": True,
        # A remembered pass is NEVER promoted to LIVE. Promotion requires a check
        # in this process, which is the caller's job, not this function's.
        "verification_source": "LAST_VERIFIED_READY",
        "last_verified_at": rec.get("verified_at"),
        "last_verified_age_s": age,
        "last_verified_model": rec.get("verified_model"),
        "last_latency_ms": rec.get("latency_ms"),
        "verification_kind": rec.get("verification_kind"),
        "stored_generation_verified": rec.get("generation_verified"),
        "stored_decision_verified": rec.get("decision_verified"),
        "stored_discovery": rec.get("discovery"),
        "stored_model_source": rec.get("model_source"),
        "fresh": is_fresh(rec, now=now),
    }
