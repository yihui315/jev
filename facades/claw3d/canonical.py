"""Canonical, deterministic encoding for the Claw3D facade payloads.

Mirrors ``session_center/canonical.py``: a single, declared encoder so the
"same input state projects to byte-identical output" property is testable in
one place. No wall-clock, no pid, no ``uuid4``, no ``hash()``
(PYTHONHASHSEED-dependent), no ``dict`` iteration order — ``sort_keys=True``
makes the order the contract declares rather than whatever the process built.
"""
from __future__ import annotations

import json
from typing import Any

CANONICAL_KWARGS = {
    "sort_keys": True,
    "separators": (",", ":"),
    "ensure_ascii": True,
    "allow_nan": False,
    "default": str,
}


def canonical_dumps(obj: Any) -> str:
    """Serialise ``obj`` to the facade's canonical byte form (stable)."""
    return json.dumps(obj, **CANONICAL_KWARGS)  # type: ignore[arg-type]


def canonical_bytes(obj: Any) -> bytes:
    """The same canonical form as bytes — what a byte-identity test compares."""
    return canonical_dumps(obj).encode("utf-8", "strict")


def stable_int(text: str, width: int = 8) -> int:
    """Deterministic non-negative integer from ``text`` (sha256, not ``hash``).

    The 3D scene needs stable geometry; ``hash()`` is rejected because its value
    is salted per process, so two runs of the same session id would not agree on
    a node position.
    """
    import hashlib

    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return int.from_bytes(digest[:width], "big")


def round_floor(value: float, places: int = 6) -> float:
    """Round for projection WITHOUT introducing nondeterminism."""
    return float(round(float(value), places))
