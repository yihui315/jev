"""Health / liveness object for the observability layer itself (S-03).

The point of this object
------------------------
The acceptance risk this package exists to close is *"the instrument says
success but did nothing"*. A swallowed exporter exception is the same defect
one layer down: whatever the observability layer had to say about the
supervision run is then only available inside a log line nobody reads (and, on
the ``core._broadcast`` path, not even there -- ``core.py:74-79`` ends in a
bare ``except Exception: pass``).

So a misbehaving exporter is a **counted** event, classified by reason:

    exporter_raised           the exporter called ``export()`` and raised
    exporter_returned_none    ``export()`` returned None instead of an ExportResult
    exporter_result_invalid   an ExportResult with a negative/implausible shape
    exporter_timeout          the export exceeded its deadline
    exporter_unavailable      the configured backend is not operable here
                              (e.g. ``otel``/``openinference`` with no SDK)
    exporter_partial          ``failed > 0``: some spans did not land
    exporter_queue_full       the span queue was full and spans were dropped
    exporter_unstarted        spans arrived before the worker was running

Every one of those is a key in ``exporter_errors``; ``healthy`` is False when
any is non-zero. A silent drop therefore CANNOT read as a green health object.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping, Optional

# -- reason codes ---------------------------------------------------------- #
REASON_EXPORT_RAISED = "exporter_raised"
REASON_RETURNED_NONE = "exporter_returned_none"
REASON_RESULT_INVALID = "exporter_result_invalid"
REASON_TIMEOUT = "exporter_timeout"
REASON_UNAVAILABLE = "exporter_unavailable"
REASON_PARTIAL = "exporter_partial"
REASON_QUEUE_FULL = "exporter_queue_full"
REASON_UNSTARTED = "exporter_unstarted"

#: The full, closed reason vocabulary. Anything a caller sees outside this set
#: is a bug in this package, not a new failure mode.
REASON_CODES: tuple = (
    REASON_EXPORT_RAISED,
    REASON_RETURNED_NONE,
    REASON_RESULT_INVALID,
    REASON_TIMEOUT,
    REASON_UNAVAILABLE,
    REASON_PARTIAL,
    REASON_QUEUE_FULL,
    REASON_UNSTARTED,
)


@dataclass
class ObservabilityHealth:
    """Liveness/counters for one observability stack. Not thread-safe on its
    own -- the :class:`Tracer` holds a lock around every mutation, and this is
    the object that lock protects."""

    enabled: bool = False
    exporter_kind: str = "none"
    #: False when the configured backend cannot actually run in this
    #: environment (e.g. ``otel`` without the SDK). Never inferred.
    exporter_available: bool = True
    #: Why ``exporter_available`` is False, if it is.
    degraded_reason: Optional[str] = None

    spans_emitted: int = 0
    #: Spans confidently handed to a sink.
    spans_exported: int = 0
    #: Spans that will never appear anywhere (dropped/failed batches).
    spans_dropped: int = 0

    exporter_calls: int = 0
    exporter_failures: int = 0
    exporter_timeouts: int = 0
    exporter_partial_failures: int = 0
    exporter_errors: Dict[str, int] = field(default_factory=dict)

    #: Longest single ``export()`` observed, milliseconds.
    max_export_latency_ms: float = 0.0
    last_error_at: Optional[float] = None
    #: Set once, at construction, so a reader can tell "never configured" from
    #: "configured with the default".
    started_at: float = field(default_factory=time.time)

    # -- recording ----------------------------------------------------------
    def record_error(self, reason: str, *, count: int = 1) -> None:
        if reason not in REASON_CODES:
            raise ValueError(
                f"unknown exporter error reason {reason!r}; the closed set is "
                f"{sorted(REASON_CODES)}"
            )
        self.exporter_errors[reason] = self.exporter_errors.get(reason, 0) + count
        self.exporter_failures += count
        self.last_error_at = time.time()

    def record_timeout(self, count: int = 1) -> None:
        self.exporter_timeouts += count
        self.record_error(REASON_TIMEOUT, count=count)

    def record_unavailable(self, reason: str) -> None:
        """Record that the backend cannot run here. Called once per batch, so
        a permanently-degraded exporter is countable rather than invisible."""
        self.degraded_reason = self.degraded_reason or reason
        self.exporter_available = False
        self.record_error(REASON_UNAVAILABLE)

    # -- reading ------------------------------------------------------------
    @property
    def healthy(self) -> bool:
        """False as soon as ANY exporter failure was counted.

        Deliberately not ``errors < N``: one lost span is a lost span, and the
        whole reason this object exists is that a zero-error run must be
        distinguishable from a run nobody looked at.
        """
        return not self.exporter_errors and self.exporter_available

    @property
    def total_errors(self) -> int:
        return sum(self.exporter_errors.values())

    def error_summary(self) -> Dict[str, int]:
        return dict(sorted(self.exporter_errors.items()))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "exporter_kind": self.exporter_kind,
            "exporter_available": self.exporter_available,
            "degraded_reason": self.degraded_reason,
            "healthy": self.healthy,
            "spans_emitted": self.spans_emitted,
            "spans_exported": self.spans_exported,
            "spans_dropped": self.spans_dropped,
            "exporter_calls": self.exporter_calls,
            "exporter_failures": self.exporter_failures,
            "exporter_timeouts": self.exporter_timeouts,
            "exporter_partial_failures": self.exporter_partial_failures,
            "exporter_errors": self.error_summary(),
            "max_export_latency_ms": round(self.max_export_latency_ms, 6),
            "last_error_at": self.last_error_at,
            "started_at": self.started_at,
        }

    # -- multi-process / multi-thread merge (for a supervisor aggregating
    #    several stacks) ----------------------------------------------------
    def merge(self, other: "ObservabilityHealth") -> "ObservabilityHealth":
        """Add ``other``'s counters into a copy of ``self`` (sums integers; the
        newest non-null ``degraded_reason``/``last_error_at`` win)."""
        out = ObservabilityHealth(
            enabled=self.enabled and other.enabled,
            exporter_kind=self.exporter_kind if other.spans_emitted == 0 else other.exporter_kind,
            exporter_available=self.exporter_available and other.exporter_available,
            degraded_reason=other.degraded_reason or self.degraded_reason,
            spans_emitted=self.spans_emitted + other.spans_emitted,
            spans_exported=self.spans_exported + other.spans_exported,
            spans_dropped=self.spans_dropped + other.spans_dropped,
            exporter_calls=self.exporter_calls + other.exporter_calls,
            exporter_failures=self.exporter_failures + other.exporter_failures,
            exporter_timeouts=self.exporter_timeouts + other.exporter_timeouts,
            exporter_partial_failures=(
                self.exporter_partial_failures + other.exporter_partial_failures
            ),
            max_export_latency_ms=max(self.max_export_latency_ms,
                                      other.max_export_latency_ms),
            started_at=min(self.started_at, other.started_at),
        )
        for reason in set(self.exporter_errors) | set(other.exporter_errors):
            out.exporter_errors[reason] = (
                self.exporter_errors.get(reason, 0) + other.exporter_errors.get(reason, 0)
            )
        if other.last_error_at is not None and (
            self.last_error_at is None or other.last_error_at > self.last_error_at
        ):
            out.last_error_at = other.last_error_at
        return out


class HealthRegistry:
    """Thread-safe holder for one health object (the Tracer's single writer)."""

    def __init__(self, health: Optional[ObservabilityHealth] = None) -> None:
        self._health = health if health is not None else ObservabilityHealth()
        self._lock = threading.Lock()

    @property
    def health(self) -> ObservabilityHealth:
        with self._lock:
            return self._snapshot()

    def raw(self) -> ObservabilityHealth:
        return self._health

    def _snapshot(self) -> ObservabilityHealth:
        import copy

        return copy.deepcopy(self._health)

    def record(self, fn) -> None:
        with self._lock:
            fn(self._health)

    def bump(self, field_name: str, amount: int = 1) -> None:
        with self._lock:
            setattr(self._health, field_name,
                    getattr(self._health, field_name) + amount)

    def record_error(self, reason: str, *, count: int = 1) -> None:
        with self._lock:
            self._health.record_error(reason, count=count)

    def record_timeout(self, count: int = 1) -> None:
        with self._lock:
            self._health.record_timeout(count)

    def record_unavailable(self, reason: str) -> None:
        with self._lock:
            self._health.record_unavailable(reason)

    def note_latency(self, ms: float) -> None:
        with self._lock:
            self._health.max_export_latency_ms = max(
                self._health.max_export_latency_ms, ms
            )

    def as_dict(self) -> Dict[str, Any]:
        return self.health.to_dict()


def merge_all(healths: Iterable[ObservabilityHealth]) -> ObservabilityHealth:
    """Merge several health objects (e.g. per-thread stacks) into one."""
    iterator = iter(list(healths))
    try:
        merged = next(iterator)
    except StopIteration:
        return ObservabilityHealth()
    for item in iterator:
        merged = merged.merge(item)
    return merged


def health_from(mapping: Optional[Mapping[str, Any]]) -> ObservabilityHealth:
    """Build a read-only health object from a dict (e.g. the JSON a caller got
    back). Unknown keys are ignored; ``exporter_errors`` is taken verbatim."""
    data = dict(mapping or {})
    out = ObservabilityHealth(
        enabled=bool(data.get("enabled", False)),
        exporter_kind=str(data.get("exporter_kind", "none")),
        exporter_available=bool(data.get("exporter_available", True)),
        degraded_reason=data.get("degraded_reason"),
        spans_emitted=int(data.get("spans_emitted", 0)),
        spans_exported=int(data.get("spans_exported", 0)),
        spans_dropped=int(data.get("spans_dropped", 0)),
        exporter_calls=int(data.get("exporter_calls", 0)),
        exporter_failures=int(data.get("exporter_failures", 0)),
        exporter_timeouts=int(data.get("exporter_timeouts", 0)),
        exporter_partial_failures=int(data.get("exporter_partial_failures", 0)),
        max_export_latency_ms=float(data.get("max_export_latency_ms", 0.0)),
        last_error_at=data.get("last_error_at"),
    )
    for reason, count in (data.get("exporter_errors") or {}).items():
        out.exporter_errors[str(reason)] = int(count)
    return out
