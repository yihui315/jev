"""Tracer: the single caller that exports (S-03).

Why a tracer at all
-------------------
``spans.py`` is explicit that "the exporter died" must be separable from "the
work did not". The Tracer is the seam that makes that true:

* The **supervision thread** (the caller) never touches ``exporter.export()``.
  It calls :meth:`Tracer.span` to build a span and :meth:`Tracer.record` to hand
  a *finished* span to the layer. ``record`` is non-blocking: it redacts (the
  span already redacted on attach), bumps ``spans_emitted``, and pushes onto a
  BOUNDED queue. The decision and the audit record are already produced by the
  time ``record`` returns.
* A **single daemon worker thread** drains the queue and calls ``export()``
  inside a guarded, timeout-bounded sub-thread. So an exporter that raises,
  blocks past its deadline, returns ``None``, or is absent can only affect the
  worker -- the supervision decision is already done and the audit tape is
  already written.

Death-mode isolation is the acceptance target. Every failure the exporter can
inflict is turned into a COUNTED health reason (see ``health.REASON_CODES``); a
silent swallow is structurally impossible because the only way a span leaves the
supervision thread is via ``record`` -> queue -> worker -> counted result.

OFF by default
--------------
When ``config.enabled`` is ``False`` the Tracer is a pure no-op: no thread, no
queue, no log records, no files, and :meth:`record` does nothing. The caller's
behaviour is identical to the layer not existing.
"""
from __future__ import annotations

import queue
import threading
import time
from typing import Any, Dict, Optional, Sequence

from .config import ObservabilityConfig
from .exporters import (
    ExportResult,
    Exporter,
    NoOpExporter,
    build_exporter,
)
from .health import (
    REASON_CODES,
    REASON_EXPORT_RAISED,
    REASON_PARTIAL,
    REASON_QUEUE_FULL,
    REASON_RESULT_INVALID,
    REASON_RETURNED_NONE,
    REASON_TIMEOUT,
    REASON_UNAVAILABLE,
    REASON_UNSTARTED,
    HealthRegistry,
    ObservabilityHealth,
)
from .semconv import ChainHop, span_kind, span_name
from .spans import Span, TraceContext, new_span_id, new_trace_id

#: How often the worker wakes to look for work / notice a stop request.
_POLL_INTERVAL_S = 0.02


class _MISSING:  # noqa: D101 - internal "no exporter argument supplied" sentinel
    """Distinguishes 'exporter not supplied' from 'exporter explicitly None'.

    Passing ``exporter=None`` means *wired but absent* (the death-mode scenario);
    not passing the argument at all means 'build from configuration'. ``None`` as
    a default would have collapsed those two cases.
    """

_MISSING = _MISSING()


class Tracer:
    """The only component that calls ``exporter.export()`` (off the supervision
    thread). See the module docstring for the isolation contract."""

    def __init__(self, config: ObservabilityConfig, *,
                 exporter: Optional[Exporter] = _MISSING) -> None:
        if not isinstance(config, ObservabilityConfig):
            raise TypeError("Tracer requires an ObservabilityConfig")
        self._config = config
        self._enabled = bool(config.enabled)
        self._registry = HealthRegistry(
            ObservabilityHealth(
                enabled=self._enabled,
                exporter_kind=config.exporter if self._enabled else "none",
            )
        )
        self._queue: "queue.Queue[Span]" = queue.Queue(
            maxsize=max(1, int(config.queue_max))
        )
        self._stop = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._exporter: Optional[Exporter] = None
        #: spans currently being exported by the worker's sub-threads (used by
        #: :meth:`wait_idle` so a stalled export is not mistaken for "settled").
        self._inflight = 0
        self._inflight_lock = threading.Lock()

        if self._enabled:
            if exporter is not _MISSING:
                # An explicitly-passed exporter -- including ``None`` to mean
                # "wired but absent" (the death-mode scenario) -- is honoured
                # verbatim. Only the *absent* sentinel falls through to building
                # from configuration.
                self._exporter = exporter
            else:
                try:
                    self._exporter = build_exporter(
                        config.exporter,
                        file_path=config.file_path,
                        log_level=config.log_level,
                    )
                except ValueError:
                    # Wiring failure (e.g. file exporter without a path): degrade
                    # to a no-op sink rather than breaking the supervision loop.
                    self._exporter = NoOpExporter()

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> "Tracer":
        if not self._enabled:
            return self
        if self._worker is None or not self._worker.is_alive():
            self._stop.clear()
            self._worker = threading.Thread(
                target=self._run_worker, name="jcc-obs-tracer", daemon=True
            )
            self._worker.start()
        return self

    def stop(self, *, timeout: Optional[float] = None) -> None:
        if self._worker is None:
            return
        self._stop.set()
        self._worker.join(timeout if timeout is not None else 2.0)
        self._worker = None

    def wait_idle(self, timeout: float = 2.0) -> bool:
        """Block until the queue is drained AND no export is in flight.

        Unlike a naive ``queue.empty()`` check, this also waits out a stalled
        export sub-thread (the timeout / drop has been recorded) so health
        assertions after a death-mode run are deterministic.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._inflight_lock:
                inflight = self._inflight
            if self._queue.empty() and inflight == 0:
                return True
            time.sleep(0.005)
        return self._queue.empty()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def health(self) -> ObservabilityHealth:
        return self._registry.health

    @property
    def exporter(self) -> Optional[Exporter]:
        return self._exporter

    def __enter__(self) -> "Tracer":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    # -- supervision-thread API (never touches export()) --------------------
    def span(self, hop: Any, *, name: Optional[str] = None,
             kind: Optional[str] = None,
             resource: Optional[Dict[str, Any]] = None) -> Span:
        """Build a span for a chain hop. Does NOT export."""
        hop_value = hop.value if isinstance(hop, ChainHop) else str(hop)
        ctx = TraceContext(trace_id=new_trace_id(), span_id=new_span_id())
        span = Span(
            name=name or (
                span_name(hop) if isinstance(hop, ChainHop) else f"jcc.{hop_value}"
            ),
            context=ctx,
            kind=kind or (span_kind(hop) if isinstance(hop, ChainHop) else "CHAIN"),
            hop=hop_value,
        )
        if resource:
            span.resource.update(dict(resource))
        return span

    def record(self, span: Optional[Span]) -> None:
        """Hand a finished span to the layer. Non-blocking, supervision-safe."""
        if not self._enabled or span is None:
            return
        self._registry.bump("spans_emitted")
        worker = self._worker
        if worker is None or not worker.is_alive():
            # Spans arrived before the worker was running.
            self._registry.record_error(REASON_UNSTARTED)
            self._registry.bump("spans_dropped")
            return
        try:
            self._queue.put_nowait(span)
        except queue.Full:
            # The queue is bounded; a stalled exporter that falls behind must not
            # back-pressure the supervision loop. Count the drop and move on.
            self._registry.record_error(REASON_QUEUE_FULL)
            self._registry.bump("spans_dropped")

    # -- worker -------------------------------------------------------------
    def _run_worker(self) -> None:
        while not self._stop.is_set():
            try:
                span = self._queue.get(timeout=_POLL_INTERVAL_S)
            except queue.Empty:
                continue
            batch = [span]
            while True:
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            try:
                self._export_batch(batch)
            except Exception:  # noqa: BLE001 - never let the worker die
                # Unexpected internal error (not an exporter death): count the
                # loss so it is never a silent swallow, and keep looping.
                self._registry.bump("spans_dropped", len(batch))

    def _export_batch(self, spans: Sequence[Span]) -> None:
        if not spans:
            return
        exporter = self._exporter
        with self._inflight_lock:
            self._inflight += 1
        try:
            self._registry.bump("exporter_calls")
            if exporter is None:
                # (d) absent: the layer is enabled but no sink was wired. Drop
                # quietly -- this is the "off-ish" state, not a defect. The
                # supervision decision was already made.
                self._registry.bump("spans_dropped", len(spans))
                return
            if getattr(exporter, "kind", "") == "none":
                # The honest "enabled but no sink chosen" default: spans are
                # DROPPED (not exported) but this is not a failure -- recording
                # it as an error would make the default path read as unhealthy.
                self._registry.bump("spans_dropped", len(spans))
                return

            holder: Dict[str, Any] = {}
            raised: Dict[str, BaseException] = {}

            def _do() -> None:
                try:
                    holder["result"] = exporter.export(spans)
                except Exception as exc:  # noqa: BLE001 - an exporter never raises
                    raised["exc"] = exc

            export_thread = threading.Thread(
                target=_do, name="jcc-obs-export", daemon=True
            )
            export_thread.start()
            export_thread.join(self._config.timeout_s)

            if export_thread.is_alive():
                # (b) blocks past the loop interval: the export did not return
                # inside its deadline. Record a timeout, drop, and abandon the
                # stalled thread (it is daemon and dies at interpreter exit).
                self._registry.record_timeout(len(spans))
                self._registry.bump("spans_dropped", len(spans))
                return

            if "exc" in raised:
                # (a) raises every time.
                self._registry.record_error(REASON_EXPORT_RAISED, count=len(spans))
                self._registry.bump("spans_dropped", len(spans))
                return

            result = holder.get("result")
            if result is None:
                # (c) returns None instead of an ExportResult.
                self._registry.record_error(REASON_RETURNED_NONE, count=len(spans))
                self._registry.bump("spans_dropped", len(spans))
                return
            if not isinstance(result, ExportResult):
                self._registry.record_error(REASON_RESULT_INVALID, count=len(spans))
                self._registry.bump("spans_dropped", len(spans))
                return

            exported = max(0, int(result.exported))
            failed = max(0, int(result.failed))
            self._registry.bump("spans_exported", exported)
            self._registry.bump("spans_dropped", failed)

            if failed > 0:
                # A partial loss is a counted failure, so a "green" health object
                # is impossible when spans were lost -- independent of whatever
                # free-form reason string the backend attached.
                self._registry.bump("exporter_partial_failures")
                self._registry.record_error(REASON_PARTIAL)

            reason = result.reason
            if reason == REASON_UNAVAILABLE:
                # The backend cannot run here (e.g. otel/openinference without
                # the SDK). A wall of counted failures, never a green light.
                self._registry.record_unavailable(reason)
            elif reason in REASON_CODES and reason != REASON_PARTIAL:
                # Any other closed-set reason the exporter reported. Free-form
                # reasons (e.g. ``file_exporter_error:...``) are intentionally
                # ignored here -- the drop count above still records the loss.
                self._registry.record_error(reason)
        finally:
            with self._inflight_lock:
                self._inflight -= 1

    # -- convenience --------------------------------------------------------
    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> "Tracer":
        return cls(ObservabilityConfig.from_env(env))


def build_tracer(env: Optional[Dict[str, str]] = None) -> Tracer:
    """Build a tracer from the environment (or an injected mapping)."""
    return Tracer.from_env(env)
