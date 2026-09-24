"""Neutral replay cache (ADR-0008 D-33; ADR-0007 D-9 hoist).

Why this module exists
----------------------
A nonce/delivery-id replay cache originally lived at
``integrations/_common/jcc_receiver.py`` (``ReplayCache``, lines 53-78). The P3
signing contract needs the *same* primitive in the neutral core, but
``packages/`` importing ``integrations/`` would be an **upward** dependency
inversion — exactly what ADR-0007 D-3 flags (and the P2 backlog item D-1,
``P2_ACCEPTANCE_REPORT.md:73``). So the cache is hoisted *down* into
``packages/jcc_core/``: the core owns it, and both ``adapters/`` and
``integrations/`` may import it downward (a later mechanical move).

Behaviour is intentionally identical to the hoisted original so the move is a
refactor, not a semantic change: bounded, TTL'd, insertion-ordered, thread-safe,
``check_and_add`` returns ``True`` for a first sighting and ``False`` for a
replay. No I/O, no clock of its own — ``now`` is supplied by the caller so the
cache is deterministic under test (the same discipline as ``decision.py``).
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Optional

#: Default entry lifetime. Long enough to cover a directive TTL plus retries,
#: short enough that memory cannot grow without bound in a long session.
DEFAULT_REPLAY_TTL_S = 600.0

#: Default hard cap on retained keys; the oldest entries are evicted first.
DEFAULT_REPLAY_MAXSIZE = 20000


class ReplayCache:
    """Bounded, TTL'd nonce replay cache (thread-safe).

    ``check_and_add(key, now)`` is the whole contract: it returns ``True`` the
    first time a key is seen (and records it), ``False`` every time after —
    within the TTL window and while the key has not been evicted by size.

    The cache is deliberately keyed by an opaque string, so the same instance
    can serve nonces, delivery ids, or any other one-shot token. Splitting by
    *purpose* is the caller's job (pass a namespaced key if both are mixed).
    """

    def __init__(self, ttl: float = DEFAULT_REPLAY_TTL_S, maxsize: int = DEFAULT_REPLAY_MAXSIZE):
        if ttl <= 0:
            raise ValueError(f"replay ttl must be > 0, got {ttl!r}")
        if maxsize <= 0:
            raise ValueError(f"replay maxsize must be > 0, got {maxsize!r}")
        self.ttl = float(ttl)
        self.maxsize = int(maxsize)
        self._seen: "OrderedDict[str, float]" = OrderedDict()
        self._lock = threading.Lock()

    # -- internals -----------------------------------------------------------
    def _prune(self, now: float) -> None:
        """Drop expired keys from the front, then enforce the size cap.

        Insertion order is the age order, so the front is always the oldest;
        a single forward scan terminates as soon as it meets a live key. The
        size cap is a *second*, independent bound for the burst case where many
        keys share one timestamp.

        The cap is made **exact** here (``>=`` so the post-insert length is
        ``<= maxsize``). The hoisted original (``jcc_receiver.py:62-68``) used
        ``>``, which retained ``maxsize + 1`` entries; that off-by-one is
        harmless but not a "cap", so the hoist tightens it and the docstring
        says so rather than implying a bound it did not hold.
        """
        while self._seen:
            key, ts = next(iter(self._seen.items()))
            if now - ts > self.ttl or len(self._seen) >= self.maxsize:
                self._seen.popitem(last=False)
            else:
                break

    # -- public API ----------------------------------------------------------
    def check_and_add(self, key: str, now: Optional[float] = None) -> bool:
        """Return ``True`` if ``key`` is NEW (accepted); ``False`` if replayed."""
        moment = time.time() if now is None else float(now)
        with self._lock:
            self._prune(moment)
            if key in self._seen:
                return False
            self._seen[key] = moment
            return True

    def seen(self, key: str) -> bool:
        """Membership test that does NOT record (no pruning side effects beyond TTL)."""
        with self._lock:
            return key in self._seen

    def clear(self) -> None:
        with self._lock:
            self._seen.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._seen)

    def __contains__(self, key: object) -> bool:
        with self._lock:
            return key in self._seen
