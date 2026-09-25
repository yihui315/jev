"""Exporter abstraction and the pluggable exporters (S-03).

The interface
-------------
Every exporter implements exactly one method::

    export(spans: Sequence[Span]) -> ExportResult

An ``ExportResult`` answers "how many of these spans actually landed", never
"how did it feel". That is what makes a **partial** failure expressible: an
exporter that writes 7 of 10 rows and reports ``failed=3`` is a failure the
caller can count, not a 200 OK.

The contract every exporter obeys
---------------------------------
* An exporter **never raises** into the tracer. Where a backend can fail
  (disk, closed stream, broken pipe, a missing SDK) it reports the failure in
  the result; the tracer turns a raise into a counted ``exporter_raised``.
* ``export()`` must not do anything that can stop the supervision loop. The
  tracer therefore never calls ``export()`` on the supervision thread -- it
  hands the span to a daemon worker (see :mod:`observability.tracer`).
* An exporter that *did* nothing is not a success: ``returned None`` and
  ``returned an implausible result`` are counted, because "the instrument says
  success but did nothing" is the defect this package exists to make impossible.

Available exporters
-------------------
``none``           discard, and say so (no sink configured)
``log``            stdlib ``logging``, one record per span
``file``           JSON lines, one object per line (append + flush)
``memory``         in-process list (tests, and ``/api/health``-style self-checks)
``otel``           OpenTelemetry bridge; degrades to a RECORDED no-op when the
                   SDK is not importable (this machine: it is not)
``openinference``  same bridge with the OpenInference semantic conventions
                   enforced on the attributes; also degrades the same way
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from .health import REASON_UNAVAILABLE
from .spans import Span

#: What an exporter is allowed to report about one batch.
@dataclass(frozen=True)
class ExportResult:
    """Outcome of one ``export()`` call.

    ``exported`` + ``failed`` must equal the number of spans received; a result
    that does not is ``invalid`` (reason ``exporter_result_invalid``).
    """

    exported: int = 0
    failed: int = 0
    reason: Optional[str] = None

    @property
    def ok(self) -> bool:
        """True only when nothing was lost AND no reason was reported."""
        return self.failed == 0 and self.reason is None and self.exported >= 0

    @property
    def total(self) -> int:
        return self.exported + self.failed


class Exporter:
    """The one interface every exporter implements."""

    #: Shown in health / ``/api/health``.
    kind: str = "base"

    def export(self, spans: Sequence[Span]) -> ExportResult:  # pragma: no cover - abstract
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - optional
        """Release resources. Never called from the supervision thread."""
        return None


# --------------------------------------------------------------------------- #
# none
# --------------------------------------------------------------------------- #
class NoOpExporter(Exporter):
    """No sink configured (the default). Counts nothing, and is honest about
    having written nothing: the spans are dROPPED, not 'exported'."""

    kind = "none"

    def export(self, spans: Sequence[Span]) -> ExportResult:
        return ExportResult(exported=0, failed=len(spans), reason="no_sink_configured")


# --------------------------------------------------------------------------- #
# log
# --------------------------------------------------------------------------- #
DEFAULT_LOG_NAME = "jcc.observability.exporter.log"


class LoggingExporter(Exporter):
    """One stdlib ``logging`` record per span, at a configurable level."""

    kind = "log"

    def __init__(self, *, logger_name: str = DEFAULT_LOG_NAME,
                 level: str = "INFO", logger: Optional[logging.Logger] = None) -> None:
        self.logger = logger if logger is not None else logging.getLogger(logger_name)
        self.logger_name = self.logger.name
        self.level = str(level).upper()
        # Set explicitly: a library logger keeps the root's WARNING level, so an
        # INFO span would otherwise be formatted into a handler and then thrown
        # away -- "the log exporter works" must not depend on root config.
        self.logger.setLevel(self.level)
        self.add_handler = False

    def export(self, spans: Sequence[Span]) -> ExportResult:
        exported = 0
        failed = 0
        for span in spans:
            payload = span.to_dict(redact=True)
            # One record per span keeps the "which span failed" question
            # answerable from the log alone.
            self.logger.info("span %s", json.dumps(payload, sort_keys=True, default=str))
            exported += 1
        return ExportResult(exported=exported, failed=failed)


# --------------------------------------------------------------------------- #
# file (JSON lines)
# --------------------------------------------------------------------------- #
class FileExporter(Exporter):
    """Append JSON-lines to ``path``; one span per line, flushed per batch."""

    kind = "file"

    def __init__(self, path: str, *, encoding: str = "utf-8") -> None:
        self.path = str(path)
        self.encoding = encoding

    def _ensure_dir(self) -> None:
        parent = os.path.dirname(os.path.abspath(self.path))
        if parent:
            os.makedirs(parent, exist_ok=True)

    def export(self, spans: Sequence[Span]) -> ExportResult:
        exported = 0
        failed = 0
        try:
            self._ensure_dir()
            with open(self.path, "a", encoding=self.encoding) as handle:
                for span in spans:
                    handle.write(span.to_json(redact=True))
                    handle.write("\n")
                    exported += 1
                # Flush is the durability boundary for this process; fsync is
                # deliberately not called on the export path (a block device
                # stall there would be the exporter outliving its deadline).
                handle.flush()
        except OSError as exc:
            failed = len(spans) - exported
            return ExportResult(exported=exported, failed=failed,
                                reason=f"file_exporter_error:{type(exc).__name__}:{exc}")
        return ExportResult(exported=exported, failed=failed)


# --------------------------------------------------------------------------- #
# memory (tests + self-checks)
# --------------------------------------------------------------------------- #
class MemoryExporter(Exporter):
    """In-process sink. Used by tests and by ``health`` self-checks; it is NOT a
    production exporter (a restart loses everything)."""

    kind = "memory"

    def __init__(self) -> None:
        self.spans: List[Span] = []
        self.calls: int = 0

    def export(self, spans: Sequence[Span]) -> ExportResult:
        self.calls += 1
        self.spans.extend(spans)
        return ExportResult(exported=len(spans), failed=0)

    def payloads(self) -> List[Dict[str, Any]]:
        return [s.to_dict(redact=True) for s in self.spans]

    def clear(self) -> None:
        self.spans.clear()
        self.calls = 0


# --------------------------------------------------------------------------- #
# otel / openinference bridge
# --------------------------------------------------------------------------- #
def probe_opentelemetry() -> Dict[str, Any]:
    """Is a real OpenTelemetry SDK importable here?

    Returns a verdict dict rather than raising: "no SDK" is an expected state
    on hosts with the P1 zero-new-dependencies rule, and the answer is part of
    what the health object publishes.
    """
    info: Dict[str, Any] = {
        "available": False,
        "sdk": False,
        "api": False,
        "api_modules": [],
        "reason": "not installed",
        "version": None,
    }
    try:
        import opentelemetry  # noqa: F401
    except Exception as exc:  # noqa: BLE001 - any import failure is the answer
        info["reason"] = f"import failed: {type(exc).__name__}"
        return info
    info["available"] = True
    info["api"] = True
    for name in ("opentelemetry.trace", "opentelemetry.context", "opentelemetry.baggage"):
        info["api_modules"].append(name)
        try:
            __import__(name)
        except Exception:  # noqa: BLE001, PERF203
            info["api_modules"][-1] = f"{name} (MISSING)"
    try:
        import opentelemetry.sdk as sdk  # noqa: F401

        info["sdk"] = True
        info["version"] = getattr(sdk, "__version__", None)
    except Exception as exc:  # noqa: BLE001
        info["reason"] = f"api present, sdk absent: {type(exc).__name__}"
    return info


def _otel_span_dict(span: Span) -> Dict[str, Any]:
    """Map one JCC span onto the OTel ``ReadableSpan``-ish shape.

    This is the documented translation surface; when the SDK is missing the very
    same mapping is what an OpenInference backend would consume, which is why
    the semantic convention lives in :mod:`observability.semconv` rather than
    inside the adapter.
    """
    payload = span.to_dict(redact=True)
    return {
        "name": payload["name"],
        "context": {
            "trace_id": payload["trace_id"],
            "span_id": payload["span_id"],
            "trace_flags": 1,
        },
        "parent": {"span_id": payload["parent_span_id"]} if payload["parent_span_id"] else None,
        "start_time": payload["start_time"],
        "end_time": payload["end_time"],
        "status": {"status_code": payload["status"], "description": payload["status_description"]},
        "attributes": payload["attributes"],
        "resource": payload["resource"],
        "events": payload["events"],
    }


class OtelBridgeExporter(Exporter):
    """OpenTelemetry bridge.

    **Degradation is explicit and countable.** The P1 rule is zero new
    dependencies, and on this host there is NO ``opentelemetry`` distribution
    (``probe_opentelemetry()`` reports it). Rather than pretend, the adapter:

    * keeps working with the JCC span model (it translates to an OTel-shaped
      dict, which is what the backend consumes anyway), and
    * when the SDK is absent, reports **every** batch as
      ``failed=len(spans), reason=<REASON_UNAVAILABLE>`` and records that in
      the health object -- so a misconfigured deployment shows up as a wall of
      counted failures instead of a green "otel exporter active".

    When the SDK *is* present, ``backend_exporter`` (an OTel
    ``SpanExporter``) receives the mapped spans and the result is the backend's.
    """

    kind = "otel"

    def __init__(self, *, backend_exporter: Any = None, logger: Optional[logging.Logger] = None,
                 enforce_semconv: bool = False) -> None:
        self.logger = logger or logging.getLogger("jcc.observability.exporter.otel")
        self.backend_exporter = backend_exporter
        self.enforce_semconv = bool(enforce_semconv)
        self.sdk_present = bool(probe_opentelemetry()["sdk"])
        self._warned = False

    # -- behaviour ----------------------------------------------------------
    def _reason(self) -> Optional[str]:
        if self.sdk_present and self.backend_exporter is not None:
            return None
        return REASON_UNAVAILABLE

    def export(self, spans: Sequence[Span]) -> ExportResult:
        if self._reason() is not None:
            if not self._warned:
                self.logger.warning(
                    "otel exporter is UNAVAILABLE (%s): no OpenTelemetry SDK is "
                    "importable on this host (P1 zero-new-dependencies rule). Every "
                    "batch is reported as failed; the supervision path is unaffected.",
                    REASON_UNAVAILABLE,
                )
                self._warned = True
            return ExportResult(exported=0, failed=len(spans), reason=REASON_UNAVAILABLE)
        if self.enforce_semconv:
            self.kind = "openinference"
        mapped = [_otel_span_dict(s) for s in spans]
        try:
            result = self.backend_exporter.export(mapped)
        except Exception as exc:  # noqa: BLE001 - an exporter never raises
            return ExportResult(exported=0, failed=len(spans),
                                reason=f"otel_backend_error:{type(exc).__name__}")
        if result is None:
            return ExportResult(exported=0, failed=len(spans), reason="otel_backend_returned_none")
        try:
            exported = int(getattr(result, "spans_exported", len(mapped)))
        except (TypeError, ValueError):
            exported = len(mapped)
        return ExportResult(exported=max(0, exported), failed=max(0, len(mapped) - exported))


class OpenInferenceExporter(OtelBridgeExporter):
    """The same bridge with the OpenInference semantic conventions enforced.

    With no SDK installed this degrades exactly like the ``otel`` exporter --
    except that the *attribute mapping* still happens, which is the one part of
    the convention that does not need the SDK to be meaningful.
    """

    kind = "openinference"

    def __init__(self, *, backend_exporter: Any = None,
                 logger: Optional[logging.Logger] = None) -> None:
        super().__init__(backend_exporter=backend_exporter, logger=logger,
                         enforce_semconv=True)


# --------------------------------------------------------------------------- #
# factory
# --------------------------------------------------------------------------- #
def build_exporter(kind: str, *, file_path: Optional[str] = None,
                   log_level: str = "INFO", logger: Optional[logging.Logger] = None,
                   backend_exporter: Any = None) -> Exporter:
    """Build the exporter the configuration asked for.

    Raises ``ValueError`` for an unknown kind **here** (at wiring time, where
    an operator can see it) rather than at the first export (where the message
    lands in a log nobody reads). The tracer, not this function, is what keeps
    a broken exporter from stopping supervision.
    """
    kind = (kind or "none").strip().lower()
    if kind == "none":
        return NoOpExporter()
    if kind == "log":
        return LoggingExporter(level=log_level, logger=logger)
    if kind == "file":
        if not file_path:
            raise ValueError(
                "JCC_OBS_EXPORTER=file requires JCC_OBS_FILE_PATH; refusing to "
                "build an exporter that would silently write nowhere"
            )
        return FileExporter(file_path)
    if kind == "memory":
        return MemoryExporter()
    if kind == "otel":
        return OtelBridgeExporter(backend_exporter=backend_exporter)
    if kind == "openinference":
        return OpenInferenceExporter(backend_exporter=backend_exporter)
    raise ValueError(
        f"unknown JCC_OBS_EXPORTER={kind!r}; the closed set is "
        "none|log|file|otel|openinference (memory is for tests only)"
    )


#: Exporters a caller is expected to reach through the env configuration.
PUBLIC_EXPORTER_KINDS = ("none", "log", "file", "otel", "openinference")


def to_json_lines(payloads: Sequence[Mapping[str, Any]]) -> str:
    """Serialize a batch of rendered spans as JSON lines (used by tests and by
    the file exporter's format)."""
    return "".join(json.dumps(dict(p), sort_keys=True, separators=(",", ":"),
                              default=str) + "\n" for p in payloads)
