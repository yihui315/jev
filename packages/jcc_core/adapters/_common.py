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
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, Union

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

#: Recursion cap for the scrub/redact passes. Beyond this depth a sub-tree is
#: replaced by ``DEPTH_LIMIT_MARK`` (fail-safe: never passed through raw). The
#: two functions share this constant so their behaviour is identical.
MAX_SCRUB_DEPTH = 8
DEPTH_LIMIT_MARK = "[DEPTH_LIMIT]"


# --------------------------------------------------------------------------- #
# Value-pattern secret scrubbing (P2 security gate finding C1)
# --------------------------------------------------------------------------- #
# Key-name matching alone is NOT sufficient: a secret can sit *inside* a
# free-form string under a non-secret key, e.g.
#     tool_input.command = "curl -H 'Authorization: Bearer <SECRET>' https://…"
# aegis-2 (P2 security gate) proved such a value was persisted verbatim into the
# jcc.db event payload. This second pass scrubs secret-looking *values* in every
# string leaf, while preserving keys and surrounding text so the audit still
# shows that a credential was present.
#
# Precision policy (deliberate -- see the handoff note):
#   * redacted: well-known token shapes; credential-labeled assignments/headers;
#     JWT; >=48-char bare hex runs; base64/url-safe runs that carry a base64
#     signal (`+`/`/`/`=` or `_`).
#   * NOT redacted: ordinary prose, file paths, UUIDs/session ids, dash-separated
#     ids (e.g. rollout names) and 40-char git SHAs -- redacting those destroys
#     audit value for no security gain. A 40-hex *credential* is still caught by
#     the labelled rule (token=<40hex>).
_MARK = "<redacted:{}>"


def _mk(name: str) -> str:
    return _MARK.format(name)


def _label_repl(m: "re.Match[str]") -> str:
    label = re.sub(r"[^a-z0-9]+", "_", m.group(1).lower()).strip("_") or "credential"
    return f"{m.group(1)}{m.group(2)}{m.group(3)}{_mk(label)}{m.group(3)}"


#: Applied in order: specific token shapes first, then labelled assignments.
_SCRUB_RULES: List[Tuple["re.Pattern[str]", Union[str, Callable[["re.Match[str]"], str]]]] = [
    # Bearer <token> -> keep the scheme word, mask the token
    (re.compile(r"(?i)\b(bearer)\s+([A-Za-z0-9._~+/=-]{8,})"), rf"\1 {_mk('bearer')}"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), _mk("api_key")),
    (re.compile(r"\b(?:ghp|gho|ghs|ghu|ghr)_[A-Za-z0-9]{20,}\b"), _mk("github_token")),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), _mk("github_token")),
    (re.compile(r"\bglpat-[A-Za-z0-9_-]{16,}\b"), _mk("gitlab_token")),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), _mk("aws_key_id")),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), _mk("slack_token")),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\b"), _mk("jwt")),
    # curl basic auth:  -u user:pass  /  --user user:pass
    (re.compile(r"(?i)((?:^|\s)(?:-u|--user)\s+)\S+"), rf"\1{_mk('basic_auth')}"),
    # labelled credential assignment / header value (`Authorization: …`,
    # `token=…`, `api_key=…`, `-H 'Authorization: …'`, `password=…`).
    (
        re.compile(
            r"(?i)\b(authorization|auth|token|api[_-]?key|apikey|secret|password|passwd|"
            r"passphrase|credential|credentials|client[_-]?secret|private[_-]?key|"
            r"access[_-]?token|refresh[_-]?token|signature|session[_-]?key)"
            r"(\s*[:=]\s*)(['\"]?)"
            r"(?!Bearer\b|Basic\b|Token\b|Digest\b|null\b|none\b|true\b|false\b)"
            r"([^\s'\";,]{4,})\3"
        ),
        _label_repl,
    ),
]

_HEX_LONG_RE = re.compile(r"(?<![0-9A-Za-z])[0-9a-fA-F]{48,}(?![0-9A-Za-z])")
_OPAQUE_RE = re.compile(r"(?<![A-Za-z0-9+/_-])[A-Za-z0-9+/_-]{20,}={0,2}(?![A-Za-z0-9+/=_-])")
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)
_HEXONLY_RE = re.compile(r"[0-9a-fA-F]+\Z")


def _opaque_is_secret(token: str) -> bool:
    """A base64/url-safe run is only a secret if it carries a base64 signal.

    Excludes UUIDs and pure-hex (handled by the hex rule) so that session ids,
    git SHAs and dash-separated rollout names survive.
    """
    if _UUID_RE.fullmatch(token) or _HEXONLY_RE.fullmatch(token):
        return False
    if any(c in token for c in "+/="):
        return True
    if "_" in token:  # a lone dash is too common (dates, paths) to trust
        classes = sum(bool(re.search(p, token)) for p in (r"[a-z]", r"[A-Z]", r"[0-9]"))
        return classes >= 2
    return False


def scrub_str(text: str) -> str:
    """Mask secret-looking VALUES inside a single free-form string.

    Deterministic, stdlib ``re`` only. Idempotent (markers never re-match).
    """
    if not text:
        return text
    out = text
    for pattern, repl in _SCRUB_RULES:
        out = pattern.sub(repl, out)
    out = _HEX_LONG_RE.sub(_mk("hex"), out)
    out = _OPAQUE_RE.sub(
        lambda m: _mk("b64") if _opaque_is_secret(m.group(0)) else m.group(0), out
    )
    return out


def scrub_value_secrets(value: Any, *, _depth: int = 0) -> Any:
    """Recursively scrub every STRING leaf (dict keys are preserved).

    Fail-safe depth cap: at ``_depth > MAX_SCRUB_DEPTH`` the sub-tree is replaced
    by ``DEPTH_LIMIT_MARK`` (the SAME marker ``redact()`` uses) instead of being
    passed through raw. This closes the P2 gate F-1 path where a secret nested
    deeper than the cap was persisted unscrubbed -- the cap is a *collapsing*
    guard, never a raw passthrough. A legitimately deeper-than-cap payload is
    therefore collapsed to the marker (data loss, but fail-safe and identical to
    the adapter path's ``redact()`` behaviour). Cost is flat: recursion stops at
    the cap, so nothing beyond it is walked.
    """
    if _depth > MAX_SCRUB_DEPTH:
        return DEPTH_LIMIT_MARK
    if isinstance(value, str):
        return scrub_str(value)
    if isinstance(value, dict):
        return {k: scrub_value_secrets(v, _depth=_depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub_value_secrets(v, _depth=_depth + 1) for v in value]
    return value


class UnidentifiableEvent(ValueError):
    """Raised when an event carries no resolvable primary identity.

    Per ADR-0003 (``session_id`` > ``cwd``) and the P2 task rule
    "no session_id -> do not enter the state store", the adapter refuses to
    fabricate an identity. The caller must spool/drop the event and count it.
    """


def redact(value: Any, *, _depth: int = 0) -> Any:
    """Return a JSON-safe, secret-free, PII-light copy of ``value``.

    * dict keys matching the secret pattern -> ``"[REDACTED]"``
    * EVERY string leaf additionally gets a value-pattern scrub
      (:func:`scrub_str`) so a secret embedded in free-form text is masked too
    * free-text keys -> truncated to ``MAX_TEXT_LEN`` chars + ``sha256:`` tag
    * deeper than ``MAX_SCRUB_DEPTH`` -> collapsed to ``DEPTH_LIMIT_MARK``
      (fail-safe; same marker as :func:`scrub_value_secrets`)
    * never raises; unknown types are coerced to ``str`` (truncated)
    """
    if _depth > MAX_SCRUB_DEPTH:
        return DEPTH_LIMIT_MARK
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        s = scrub_str(value)
        if len(s) > MAX_TEXT_LEN:
            digest = hashlib.sha256(s.encode("utf-8", "replace")).hexdigest()[:16]
            return f"{s[:MAX_TEXT_LEN]}…[sha256:{digest}]"
        return s
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
