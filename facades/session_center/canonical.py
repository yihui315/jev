"""Canonical, deterministic encoding for facade payloads.

"Deterministic projection: the same input state must project to a byte-identical
payload" is only testable if the byte form is defined once, in one place, and
nobody re-implements it. That is this module: the encoding is part of the
facade's contract (``contract["encoding"]`` is literally the keyword set used
here), so an encoder that drifts from the declaration is a contract change and
not a formatting preference.

No wall-clock, no pid, no ``uuid4``, no ``hash()`` (PYTHONHASHSEED-dependent),
no ``dict`` iteration order — ``sort_keys=True`` makes the order the contract
declares rather than the one the process happened to build.
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

    Used wherever a facade has to turn an identifier into geometry or a seed.
    ``hash()`` is rejected on purpose: its value is salted per process, so two
    runs of the same input would not agree.
    """
    import hashlib

    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return int.from_bytes(digest[:width], "big")


def round_floor(value: float, places: int = 6) -> float:
    """Round for projection WITHOUT introducing nondeterminism.

    ``round`` is deterministic, but a float can still render as ``1e-06``;
    formatting is left to ``json`` (RFC 8259 shortest round-trip), which is
    deterministic for the same float.
    """
    return float(round(float(value), places))
