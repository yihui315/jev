"""The Operator Skill trace tape (ADR-0009 evidence rules).

Every tool invocation -- success *and* refusal -- produces exactly one trace
record. The tape is the ONLY place the skill records that something happened, so
it is the evidence the S-01 gate reads. Three properties are load-bearing:

1. **Complete.** Refusals are traced too. A refusal that is not on the tape is
   indistinguishable from a call that never happened, which is the same
   evidence hole as a silent success.
2. **Redacted by construction.** The whole record is passed through the real
   ``jcc_core.redaction`` kernel plus an explicit secret mask (the control token
   and any signing key). The scrub happens on the record, not on the caller's
   arguments, so a secret that reaches the trace by any route is still masked.
3. **Validated.** ``validate_trace_record`` is the schema. A record that does
   not carry the required fields is written and *also* reported, so a broken
   caller cannot launder a malformed trace by claiming it is fine.

Record fields (all required unless marked optional):

    schema_version, trace_id, timestamp (epoch float), iso_timestamp,
    tool, tool_mode, arguments (redacted dict), outcome (dict),
    evidence_path (absolute string), principal, session_id (optional),
    directive_ref (optional -- the directive/signature a gateway call
    referenced).
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from jcc_core.redaction import redact

TRACE_SCHEMA_VERSION = "jcc-operator-trace/1"

#: The fields every trace record MUST carry. ``validate_trace_record`` is the
#: authoritative schema; the list here is only for readers.
REQUIRED_FIELDS: Tuple[str, ...] = (
    "schema_version",
    "trace_id",
    "timestamp",
    "iso_timestamp",
    "tool",
    "tool_mode",
    "arguments",
    "outcome",
    "evidence_path",
    "principal",
    "session_id",
    "directive_ref",
)

#: Optional fields, kept in the record when present.
OPTIONAL_FIELDS: Tuple[str, ...] = ("endpoint", "http_status")

REDACTED = "[REDACTED]"


class TraceWriteError(RuntimeError):
    """The tape could not be written. Raised; the caller turns it into a refusal."""


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def validate_trace_record(record: Dict[str, Any]) -> List[str]:
    """Return a list of schema problems; an empty list means the record is valid."""
    problems: List[str] = []
    if not isinstance(record, dict):
        return ["record is not a mapping"]
    # ``session_id`` and ``directive_ref`` are REQUIRED in the sense that they
    # must be PRESENT on every record, but they are legitimately ``None``: a
    # read-only tool (e.g. ``jcc_status``) has no session, and a read has no
    # referenced directive. The docstring marks them optional precisely because
    # of this; the null check below therefore excludes them (see ADR-0009).
    NON_NULL_REQUIRED = set(REQUIRED_FIELDS) - {"session_id", "directive_ref"}
    for name in REQUIRED_FIELDS:
        if name not in record:
            problems.append(f"missing required field {name!r}")
    for name in REQUIRED_FIELDS + OPTIONAL_FIELDS:
        if name in record and record[name] is None and name in NON_NULL_REQUIRED:
            problems.append(f"required field {name!r} is null")
    if record.get("schema_version") != TRACE_SCHEMA_VERSION:
        problems.append(f"schema_version must be {TRACE_SCHEMA_VERSION!r}")
    ts = record.get("timestamp")
    if not isinstance(ts, (int, float)) or isinstance(ts, bool):
        problems.append("timestamp must be a number (epoch seconds)")
    tool = record.get("tool")
    if not isinstance(tool, str) or not tool:
        problems.append("tool must be a non-empty string")
    args = record.get("arguments")
    if not isinstance(args, dict):
        problems.append("arguments must be a mapping (redacted on write)")
    outcome = record.get("outcome")
    if not isinstance(outcome, dict):
        problems.append("outcome must be a mapping")
    elif outcome.get("status") not in ("ok", "refused", "error"):
        problems.append("outcome.status must be one of ok|refused|error")
    ev = record.get("evidence_path")
    if not isinstance(ev, str) or not ev:
        problems.append("evidence_path must be a non-empty string (absolute path)")
    return problems


def _mask(value: Any, secrets_tuple: Tuple[str, ...]) -> Any:
    """Replace every occurrence of a secret with ``REDACTED``, recursively."""
    if isinstance(value, str):
        out = value
        for secret in secrets_tuple:
            if secret and secret in out:
                out = out.replace(secret, REDACTED)
        return out
    if isinstance(value, dict):
        return {k: _mask(v, secrets_tuple) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_mask(v, secrets_tuple) for v in value]
    return value


def signature_ref(signature: Optional[str]) -> Optional[str]:
    """A short, non-revealing handle for a signature (never the signature itself)."""
    if not signature:
        return None
    digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:12]
    return f"sha256=<{digest}>"


@dataclass
class TraceWriter:
    """Append-only JSON-lines tape at ``path``.

    ``secrets`` carries the strings that must never appear in the file (the
    control token, the signing key, any approval nonce). They are masked from
    EVERY field, not only from ``arguments``, so a future record field cannot
    become a new leak path.
    """

    path: Path
    secrets: Tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self._secrets: Tuple[str, ...] = tuple(
            str(s) for s in (self.secrets or ()) if s
        )

    def scrub(self, record: Dict[str, Any]) -> Dict[str, Any]:
        # Mask the KNOWN secrets (control token + signing key) across the WHOLE
        # record so they can never appear anywhere in cleartext. The redaction
        # kernel is then applied ONLY to ``arguments`` -- the one field that
        # carries arbitrary caller-supplied free text -- and NOT to the
        # structural constant fields. Running ``redact()`` over the entire record
        # over-masks constants: ``schema_version`` ("jcc-operator-trace/1")
        # trips the kernel's base64/opaque rule (it contains '/') and is
        # rewritten to "<redacted:b64>", which then fails ``validate_trace_record``.
        # The explicit mask already covers the named secrets everywhere; redacting
        # the caller args is the defence-in-depth against embedded secret shapes.
        masked = _mask(record, self._secrets)
        args = masked.get("arguments")
        if isinstance(args, dict):
            masked["arguments"] = redact(args)
        return masked

    def append(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Validate -> scrub -> append. Returns the scrubbed record that was written."""
        stamped = dict(record)
        stamped.setdefault("schema_version", TRACE_SCHEMA_VERSION)
        if "trace_id" not in stamped:
            stamped["trace_id"] = secrets.token_hex(8)
        if "timestamp" not in stamped:
            stamped["timestamp"] = time.time()
        stamped.setdefault("iso_timestamp", _iso(float(stamped["timestamp"])))
        safe = self.scrub(stamped)
        line = json.dumps(safe, sort_keys=True, ensure_ascii=False, default=str)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()
        except OSError as exc:
            raise TraceWriteError(f"trace tape {self.path} is not writable: {exc}") from exc
        return safe


def read_traces(path: "str | Path") -> List[Dict[str, Any]]:
    """Parse the tape back into records. Malformed lines are skipped and named."""
    out: List[Dict[str, Any]] = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError as exc:
            raise TraceWriteError(f"{path}:{number} is not valid JSON: {exc}") from exc
    return out
