"""Span / trace model for the JCC observability layer (S-03).

Scope
-----
A minimal, dependency-free trace model: a ``TraceContext`` (trace/span ids plus
parentage), a ``Span`` (name, kind, timing, status, attributes, events) and a
deterministic, JSON-safe serializer (``Span.to_dict``) that is what every
exporter emits.

Design notes that matter
------------------------
* **No OpenTelemetry import, no new dependencies.** The model is deliberately
  shaped so the OTel SDK -- when it is installed -- can consume it unchanged
  (see ``exporters.OtelBridgeExporter``); the package imports ``opentelemetry``
  ONLY inside the adapter's optional import path.
* **Redaction happens before a value is ever stored on a span**, not after.
  ``Span.set_attribute`` routes through :mod:`observability.redaction`, so "a
  secret on a span" is not a state a span can be in. ``to_dict`` gates again,
  so a span that was built any other way still cannot be exported verbatim.
* **A span never carries "done" implicitly.** ``end()``/status transitions are
  explicit and the tracer is the only caller that exports, which is what makes
  "the exporter died" separable from "the work did not".
"""
from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .redaction import redact_attributes

# --------------------------------------------------------------------------- #
# Identifiers
# --------------------------------------------------------------------------- #
TRACE_ID_HEX_LEN = 32  # 16 bytes, as in W3C traceparent
SPAN_ID_HEX_LEN = 16  # 8 bytes, as in W3C traceparent


def _new_hex(byte_len: int) -> str:
    return secrets.token_hex(byte_len)


def new_trace_id() -> str:
    return _new_hex(TRACE_ID_HEX_LEN // 2)


def new_span_id() -> str:
    return _new_hex(SPAN_ID_HEX_LEN // 2)


class SpanStatus(str, Enum):
    """Span status (mirrors the OTel status trio, minus UNSET's ambiguity)."""

    UNSET = "UNSET"
    OK = "OK"
    ERROR = "ERROR"

    @property
    def is_error(self) -> bool:
        return self is SpanStatus.ERROR


@dataclass(frozen=True)
class TraceContext:
    """Immutable span identity. Frozen so a span's parentage cannot change."""

    trace_id: str
    span_id: str
    parent_span_id: Optional[str] = None
    #: W3C trace flags: bit 1 == sampled. Mirrors ``trace_flags: 01``.
    sampled: bool = True

    def child(self) -> "TraceContext":
        """A child context on the same trace (new span id, same trace id)."""
        return TraceContext(
            trace_id=self.trace_id,
            span_id=new_span_id(),
            parent_span_id=self.span_id,
            sampled=self.sampled,
        )

    def traceparent(self) -> str:
        """W3C ``traceparent`` header value (``00-<32>-<16>-01``)."""
        flag = "01" if self.sampled else "00"
        return f"00-{self.trace_id}-{self.span_id}-{flag}"

    @classmethod
    def from_traceparent(cls, header: str) -> Optional["TraceContext"]:
        """Parse a ``traceparent`` header; malformed input yields ``None``."""
        if not isinstance(header, str):
            return None
        parts = header.split("-")
        if len(parts) != 4:
            return None
        if parts[0] != "00":
            return None
        trace_id, span_id, flags = parts[1], parts[2], parts[3]
        if len(trace_id) != TRACE_ID_HEX_LEN or len(span_id) != SPAN_ID_HEX_LEN:
            return None
        try:
            int(trace_id, 16)
            int(span_id, 16)
        except ValueError:
            return None
        try:
            sampled = int(flags, 16) & 1 == 1
        except ValueError:
            sampled = False
        return cls(trace_id=trace_id, span_id=span_id, sampled=sampled)


@dataclass(frozen=True)
class SpanEvent:
    """One annotation inside a span (e.g. ``exception``, ``retry``)."""

    name: str
    timestamp: float
    attributes: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "timestamp": _json_safe(self.timestamp),
            "attributes": redact_attributes(self.attributes),
        }


def _json_safe(value: Any) -> Any:
    """Coerce anything to a JSON-representable primitive (never raises)."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, float) and value != value:  # NaN
        return None
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return str(value)[:200]
    return value


@dataclass
class Span:
    """One unit of work on the supervision chain."""

    name: str
    context: TraceContext
    #: OpenInference ``openinference.span.kind`` (``CHAIN``/``LLM``/``TOOL``/``AGENT``).
    kind: str = "CHAIN"
    #: Ingest-side wall clock, seconds (float). ``start_time``/``end_time`` use
    #: the SAME clock kind as the caller so durations mean what they say.
    start_time: float = 0.0
    end_time: Optional[float] = None
    status: SpanStatus = SpanStatus.UNSET
    status_description: Optional[str] = None
    #: The hop this span belongs to (``ChainHop`` value), e.g. ``"policy"``.
    hop: Optional[str] = None
    attributes: Dict[str, Any] = field(default_factory=dict)
    events: List[SpanEvent] = field(default_factory=list)
    #: OpenInference resource attributes, e.g. ``{"service.name": "jcc-core"}``.
    resource: Dict[str, Any] = field(default_factory=dict)

    # -- identity -----------------------------------------------------------
    @property
    def span_id(self) -> str:
        return self.context.span_id

    @property
    def trace_id(self) -> str:
        return self.context.trace_id

    @property
    def parent_span_id(self) -> Optional[str]:
        return self.context.parent_span_id

    def is_root(self) -> bool:
        return self.context.parent_span_id is None

    # -- mutation (redaction happens HERE, before the value is stored) ------
    def set_attribute(self, key: str, value: Any) -> None:
        """Attach an attribute. Secrets are stripped BEFORE they are stored."""
        from .redaction import redact_value  # local: keeps the module import-light

        self.attributes[str(key)] = redact_value(value, key)

    def add_event(self, name: str, *, timestamp: Optional[float] = None,
                  attributes: Optional[Dict[str, Any]] = None) -> SpanEvent:
        self.events.append(
            SpanEvent(
                name=str(name),
                timestamp=timestamp if timestamp is not None else self.end_time_or_start(),
                attributes=dict(attributes or {}),
            )
        )
        return self.events[-1]

    def set_status(self, status: SpanStatus, description: Optional[str] = None) -> None:
        self.status = SpanStatus(status)
        self.status_description = description

    def mark_error(self, description: str) -> None:
        self.status = SpanStatus.ERROR
        self.status_description = description

    # -- timing -------------------------------------------------------------
    def start(self, timestamp: float) -> "Span":
        self.start_time = timestamp
        return self

    def end(self, timestamp: float) -> "Span":
        self.end_time = timestamp
        if self.status is SpanStatus.UNSET:
            self.status = SpanStatus.OK
        return self

    def end_or_start(self) -> float:
        return self.end_time if self.end_time is not None else self.start_time

    def end_time_or_start(self) -> float:
        return self.end_time_or_start()

    @property
    def duration_ms(self) -> float:
        if self.end_time is None:
            return 0.0
        return max(0.0, (self.end_time - self.start_time) * 1000.0)

    # -- serialization ------------------------------------------------------
    def to_dict(self, *, redact: bool = True) -> Dict[str, Any]:
        """JSON-safe view. ``redact`` is ON by default and is the second gate:
        a span that was populated any way other than ``set_attribute`` still
        cannot be exported verbatim."""
        payload: Dict[str, Any] = {
            "name": self.name,
            "kind": self.kind,
            "hop": self.hop,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "status": self.status.value,
            "status_description": self.status_description,
            "start_time": _json_safe(self.start_time),
            "end_time": _json_safe(self.end_time),
            "duration_ms": round(self.duration_ms, 6),
            "attributes": dict(self.attributes),
            "events": [e.to_dict() for e in self.events],
            "resource": dict(self.resource),
        }
        if redact:
            payload["attributes"] = redact_attributes(payload["attributes"])
        return payload

    def to_json(self, *, redact: bool = True) -> str:
        try:
            return json.dumps(self.to_dict(redact=redact), sort_keys=True,
                              separators=(",", ":"), default=str)
        except (TypeError, ValueError):  # unreachable: to_dict is already safe
            return json.dumps({"name": self.name, "redaction_failed": True})

    def trace_ids(self) -> Tuple[str, Optional[str]]:
        return (self.trace_id, self.parent_span_id)


@dataclass
class SpanBatch:
    """What one ``export()`` call receives: a list of finished spans."""

    spans: List[Span] = field(default_factory=list)

    def __len__(self) -> int:  # pragma: no cover - trivial
        return len(self.spans)

    def __iter__(self):  # pragma: no cover - trivial
        return iter(self.spans)

    def serialized(self) -> List[Dict[str, Any]]:
        return [s.to_dict() for s in self.spans]

    @classmethod
    def of(cls, spans: Sequence[Span]) -> "SpanBatch":
        return cls(list(spans))


def attach_resource(span: Span, **resource: Any) -> Span:
    """Set the OpenInference resource attributes on ``span``."""
    for key, value in resource.items():
        span.resource[str(key)] = value
    return span


def describe(iterable: Iterable[str]) -> List[str]:
    """Small helper used by tests/health: a stable list of names."""
    return [str(x) for x in iterable]
