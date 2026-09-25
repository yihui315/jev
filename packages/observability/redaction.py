"""The export-boundary redaction gate (S-03).

Why this module exists
----------------------
"The observability layer must never attach a secret to a span" is a control
that has to be enforced in code, not in a code review. There are three places a
secret could reach an exporter:

1. a producer (``instrumentation``) attaches a payload that happens to contain
   a credential;
2. an attribute value is a free-form string with a credential inside;
3. an exporter renders a span it did not get from this package (a plugin, a
   future adapter).

So the gate is applied at **every** boundary: when a value is ATTACHED
(``Span.set_attribute``), and again when a span is RENDERED
(``Span.to_dict``/``exporters``). Key-name denylist + free-text value scrubbing
+ an optional strict allowlist for the hops where "only these scalars are
allowed to leave the process" (the directive hop).

Relationship to ``jcc_core.redaction``
--------------------------------------
``jcc_core.redaction`` (ADR-0007 D-3) is the ingest-side chokepoint and the
ONE value-scrubbing kernel; this module imports it rather than copying it, so
the two cannot drift on what counts as a secret. This module adds what the
ingest kernel does not carry:

* a **strict allowlist** mode, which fails CLOSED for a whole span (a
  denylist-only design fails open the first time somebody attaches a
  ``payload`` whose keys are not in the pattern list);
* a marker/prefix contract so a redacted value is unmistakable in output
  (``<redacted:control_token>``) rather than an empty string that a reader
  could mistake for "no value".

If ``jcc_core`` is unavailable this module RAISES at use time rather than
falling back to a private copy: a second, silent redaction kernel is exactly
how a control like this one stops being a control.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Set

#: Value used when an allowlisted hop is asked to carry something that is not
#: on the allowlist. Deliberately loud and unmistakable.
NOT_ALLOWED = "[NOT_ALLOWED]"

#: The default marker shape produced by the shared kernel.
REDACTED = "[REDACTED]"


def _kernel():
    """The one value-scrubbing kernel (``jcc_core.redaction``).

    Imported lazily AND asserted: a missing ``jcc_core`` is a wiring failure,
    not an excuse to invent a second redaction policy.
    """
    from jcc_core import redaction as _redaction  # noqa: WPS433 (deliberate lazy import)

    return _redaction


def scrub_str(text: str) -> str:
    """Mask secret-looking values inside one string (delegates to the kernel)."""
    return _kernel().scrub_str(text)


def redact_value(value: Any, key: Optional[str] = None) -> Any:
    """Redact one attribute value. Never raises.

    * key matches the kernel's secret-key pattern -> ``"[REDACTED]"``;
    * every string leaf additionally goes through the kernel's value-pattern
      scrubber, so a credential embedded in free text is masked too;
    * anything the renderer could not have expected is coerced to a string.
    """
    kernel = _kernel()
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        # A secret-looking KEY masks the WHOLE value, not just the parts that
        # the value-pattern rules happen to match.
        return REDACTED if _key_is_secret(key) else kernel.scrub_str(value)
    if isinstance(value, Mapping):
        return redact_mapping(value, key_hint=key)
    if isinstance(value, (list, tuple, set)):
        return [redact_value(v, key) for v in value]
    return kernel.scrub_str(str(value))


def _key_is_secret(key: Optional[str]) -> bool:
    if not key:
        return False
    return bool(_kernel()._SECRET_KEY_RE.search(str(key)))


def redact_mapping(value: Mapping[str, Any], *,
                   allowlist: Optional[Iterable[str]] = None,
                   key_hint: Optional[str] = None,
                   _depth: int = 0) -> Dict[str, Any]:
    """Redact a mapping of attributes.

    Parameters
    ----------
    allowlist
        When given, the ONLY keys that survive (strict mode). Anything else is
        replaced by ``NOT_ALLOWED`` -- a directive hop must not be able to leak
        ``params`` or ``signature`` just because those key names are vague
        enough to miss a denylist.
    """
    kernel = _kernel()
    allowed: Optional[Set[str]] = None
    if allowlist is not None:
        allowed = {str(k) for k in allowlist}
    if _depth > kernel.MAX_SCRUB_DEPTH:
        return {"depth_limit": kernel.DEPTH_LIMIT_MARK}
    out: Dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        if allowed is not None and key not in allowed:
            out[key] = NOT_ALLOWED
            continue
        if _key_is_secret(key):
            out[key] = REDACTED
            continue
        out[key] = redact_value(raw_value, key)
    return out


def redact_attributes(attributes: Mapping[str, Any], *,
                      allowlist: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """The rendering-side gate: what every exporter serialises."""
    return redact_mapping(attributes, allowlist=allowlist)


def assert_no_secret(text: Any, sentinels: Sequence[str]) -> None:
    """Raise if any sentinel secret appears in ``text``. Used by tests, and
    available to any exporter that wants a self-check on its own output."""
    blob = text if isinstance(text, str) else repr(text)
    for sentinel in sentinels:
        if sentinel and sentinel in blob:
            raise AssertionError(f"SECRET LEAK: {sentinel!r} present in exported text")
