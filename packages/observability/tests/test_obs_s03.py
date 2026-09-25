"""S-03 acceptance tests: observability exporter-abstraction + death-mode isolation.

The acceptance target (per the lead brief) is that an exporter that DIES -- raises
every time, blocks past the loop interval, returns None, or is absent -- must NOT
stop Core: the supervision decision and the audit record are produced regardless,
and the exporter failure is a COUNTED health event (never a silent swallow).

The "supervision loop" here is a representative harness
(:func:`run_supervision`) that walks the seven real chain hops
(ingest -> aep -> state -> assessment -> policy -> directive -> audit, pinned in
``observability.semconv.ChainHop`` and verified against the shipped call sites in
``jcc_core``) and, like the real ``CoreEngine``, emits a decision and an audit
record as its real work. The harness does NOT modify ``jcc_core`` (those files
are A8-pinned); it proves the isolation property at the tracer boundary, which is
exactly where the defect would live.
"""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, Tuple

# Make ``observability`` and ``jcc_core`` importable (mirrors the other packages'
# test layout).
REPO = Path(__file__).resolve().parents[3]
PACKAGES = REPO / "packages"
for _p in (str(PACKAGES),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from observability.config import ObservabilityConfig
from observability.exporters import (
    ExportResult,
    Exporter,
    FileExporter,
    LoggingExporter,
    MemoryExporter,
    OtelBridgeExporter,
)
from observability.health import (
    REASON_EXPORT_RAISED,
    REASON_PARTIAL,
    REASON_RETURNED_NONE,
    REASON_TIMEOUT,
    REASON_UNAVAILABLE,
)
from observability.semconv import ChainHop
from observability.spans import Span, TraceContext, new_span_id, new_trace_id
from observability.tracer import Tracer

# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #
def run_supervision(tracer: Tracer, event_id: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """A representative supervision loop: walk the seven real hops, record a span
    per hop, and -- as the REAL work -- produce a decision and an audit record.

    Returns ``(decision, audit_record)``. The point of the death-mode tests is
    that this returns a populated decision + audit even when the exporter is dead.
    """
    decision: Dict[str, Any] = {}
    for hop in ChainHop:  # definition order == chain order
        span = tracer.span(hop, resource={"service.name": "jcc-core"})
        span.set_attribute("jcc.event_id", event_id)
        if hop is ChainHop.POLICY:
            decision = {"directive_id": f"d-{event_id}", "acted": False}
        tracer.record(span)
    audit_record = {"hash": f"h-{event_id}", "tape": "append", "event_id": event_id}
    return decision, audit_record


def _cfg(**overrides: Any) -> ObservabilityConfig:
    base = dict(
        enabled=True,
        exporter="memory",
        exporter_timeout_ms=100.0,
        queue_max=1024,
    )
    base.update(overrides)
    return ObservabilityConfig(**base)


# --------------------------------------------------------------------------- #
# Death-mode exporters (the four ways an exporter can die)
# --------------------------------------------------------------------------- #
class RaisingExporter(Exporter):
    kind = "raise"

    def export(self, spans):
        raise RuntimeError("exporter is on fire")


class BlockingExporter(Exporter):
    kind = "block"

    def __init__(self) -> None:
        self.calls = 0

    def export(self, spans):
        self.calls += 1
        time.sleep(5.0)  # far longer than any supervision tick
        return ExportResult(exported=len(spans))


class NoneReturningExporter(Exporter):
    kind = "none_return"

    def export(self, spans):
        return None


class PartialExporter(Exporter):
    kind = "partial"

    def export(self, spans):
        n = len(spans)
        exported = max(0, n - 1)
        return ExportResult(exported=exported, failed=n - exported, reason=None)


# --------------------------------------------------------------------------- #
# 1. Exporter OFF: zero behaviour change + zero output
# --------------------------------------------------------------------------- #
def test_exporter_off_zero_behaviour_change():
    cfg = ObservabilityConfig(enabled=False)
    t = Tracer(cfg)
    assert t.enabled is False
    assert t.health.enabled is False

    # Capture anything the exporter loggers might emit.
    captured: list = []
    handler = logging.Handler()
    handler.emit = lambda rec: captured.append(rec.getMessage())
    logging.getLogger("jcc.observability.exporter").addHandler(handler)

    decision, audit = run_supervision(t, "off-1")
    # Even with the tracer present, the supervisor's real work happens.
    assert decision == {"directive_id": "d-off-1", "acted": False}
    assert audit["event_id"] == "off-1"

    # start/record/stop on a disabled tracer are pure no-ops.
    t.start()
    t.record(t.span(ChainHop.INGEST))
    t.stop()

    assert t.health.spans_emitted == 0
    assert t.health.spans_exported == 0
    assert t.health.spans_dropped == 0
    assert t._worker is None
    # No exporter was ever constructed, so nothing was logged.
    assert captured == []


# --------------------------------------------------------------------------- #
# 2. Exporter UNAVAILABLE (otel / openinference without the SDK)
# --------------------------------------------------------------------------- #
def test_exporter_unavailable_records_failure():
    cfg = _cfg(exporter="otel")
    t = Tracer(cfg)
    assert isinstance(t.exporter, OtelBridgeExporter)
    # This host has no OpenTelemetry SDK (P1 zero-new-dependencies).
    assert t.exporter.sdk_present is False

    t.start()
    decision, audit = run_supervision(t, "unavail-1")
    assert decision and audit
    t.wait_idle()
    t.stop()

    h = t.health
    assert h.exporter_available is False
    assert h.exporter_errors.get(REASON_UNAVAILABLE, 0) >= 1
    assert h.spans_dropped == 7  # every span failed to land
    assert h.spans_exported == 0
    assert h.healthy is False  # a wall of counted failures, never green


# --------------------------------------------------------------------------- #
# 3. Exporter TIMEOUT (blocks past its deadline)
# --------------------------------------------------------------------------- #
def test_exporter_timeout_records_failure():
    cfg = _cfg(exporter="memory", exporter_timeout_ms=100.0)
    t = Tracer(cfg, exporter=BlockingExporter())

    t.start()
    start = time.time()
    decision, audit = run_supervision(t, "timeout-1")  # 7 spans queued, non-blocking
    elapsed = time.time() - start
    t.wait_idle(timeout=2.0)
    t.stop(timeout=0.5)

    # The supervision loop was NOT blocked by the exporter (record is async).
    assert decision and audit
    assert elapsed < 1.0, f"supervision was blocked by the exporter ({elapsed:.2f}s)"
    h = t.health
    assert h.exporter_errors.get(REASON_TIMEOUT, 0) >= 1
    assert h.exporter_timeouts >= 1
    assert h.spans_dropped == 7


# --------------------------------------------------------------------------- #
# 4. Exporter RAISES
# --------------------------------------------------------------------------- #
def test_exporter_raises_records_failure():
    cfg = _cfg(exporter="memory")
    t = Tracer(cfg, exporter=RaisingExporter())

    t.start()
    decision, audit = run_supervision(t, "raise-1")
    t.wait_idle()
    t.stop()

    assert decision and audit  # real work survived the exploding exporter
    h = t.health
    assert h.exporter_errors.get(REASON_EXPORT_RAISED, 0) >= 1
    assert h.spans_dropped == 7


# --------------------------------------------------------------------------- #
# 5. Partial export failure
# --------------------------------------------------------------------------- #
def test_partial_export_failure():
    # (a) Deterministic, direct: one batch of three, two land, one lost.
    cfg = _cfg(exporter="memory")
    t = Tracer(cfg, exporter=PartialExporter())
    spans = [t.span(ChainHop.INGEST) for _ in range(3)]
    t._export_batch(spans)
    h = t.health
    assert h.spans_exported == 2
    assert h.spans_dropped == 1
    assert h.exporter_partial_failures == 1
    assert h.exporter_errors.get(REASON_PARTIAL, 0) == 1

    # (b) Through the worker (robust to batching): total accounted = 3, at least
    #     one partial recorded.
    t2 = Tracer(_cfg(exporter="memory"), exporter=PartialExporter())
    t2.start()
    for _ in range(3):
        t2.record(t2.span(ChainHop.INGEST))
    t2.wait_idle()
    t2.stop()
    h2 = t2.health
    assert h2.spans_exported + h2.spans_dropped == 3
    assert h2.exporter_errors.get(REASON_PARTIAL, 0) >= 1


# --------------------------------------------------------------------------- #
# 6. Default path (enabled, no sink chosen => drops, no crash, not "unhealthy")
# --------------------------------------------------------------------------- #
def test_default_path_drops_without_error():
    cfg = _cfg(exporter="none")
    t = Tracer(cfg)
    assert t.enabled is True
    assert t.exporter is not None and t.exporter.kind == "none"

    t.start()
    decision, audit = run_supervision(t, "default-1")
    t.wait_idle()
    t.stop()

    assert decision and audit
    h = t.health
    assert h.spans_dropped == 7
    assert h.spans_exported == 0
    # The honest "no sink" default is NOT counted as a failure.
    assert h.exporter_errors == {}
    assert h.healthy is True


# --------------------------------------------------------------------------- #
# 7. The four death-mode scenarios: supervision MUST still emit its real output
# --------------------------------------------------------------------------- #
def test_deathmode_raises_supervision_survives():
    t = Tracer(_cfg(exporter="memory"), exporter=RaisingExporter())
    t.start()
    decision, audit = run_supervision(t, "dm-raise")
    t.wait_idle()
    t.stop()
    assert decision == {"directive_id": "d-dm-raise", "acted": False}
    assert audit["event_id"] == "dm-raise"
    assert t.health.spans_emitted == 7  # layer received every span


def test_deathmode_blocks_supervision_survives():
    t = Tracer(_cfg(exporter="memory", exporter_timeout_ms=100.0),
               exporter=BlockingExporter())
    t.start()
    start = time.time()
    decision, audit = run_supervision(t, "dm-block")
    elapsed = time.time() - start
    t.wait_idle(timeout=2.0)
    t.stop(timeout=0.5)
    assert decision and audit
    assert elapsed < 1.0
    assert t.health.exporter_errors.get(REASON_TIMEOUT, 0) >= 1


def test_deathmode_returns_none_supervision_survives():
    t = Tracer(_cfg(exporter="memory"), exporter=NoneReturningExporter())
    t.start()
    decision, audit = run_supervision(t, "dm-none")
    t.wait_idle()
    t.stop()
    assert decision and audit
    assert t.health.exporter_errors.get(REASON_RETURNED_NONE, 0) >= 1
    assert t.health.spans_dropped == 7


def test_deathmode_absent_supervision_survives():
    # The exporter was never wired (passed explicitly as None) but the layer is
    # enabled: spans are dropped quietly, and -- crucially -- nothing raises.
    t = Tracer(_cfg(exporter="memory"), exporter=None)
    t.start()
    decision, audit = run_supervision(t, "dm-absent")
    t.wait_idle()
    t.stop()
    assert decision and audit
    assert t.health.spans_dropped == 7
    # Absent is the "off-ish" state, not an error: no raised/timeout recorded.
    assert REASON_EXPORT_RAISED not in t.health.exporter_errors
    assert REASON_TIMEOUT not in t.health.exporter_errors


# --------------------------------------------------------------------------- #
# 8. Redaction: a sentinel secret must never reach exported text
# --------------------------------------------------------------------------- #
SENTINEL = "LEAK_SENTINEL_7f3c9b2a"


def test_redaction_set_attribute_strips_secret():
    s = Span(name="x", context=TraceContext(new_trace_id(), new_span_id()))
    s.set_attribute("token", f"Bearer {SENTINEL}")  # secret KEY -> whole value masked
    s.set_attribute("jcc.event_id", "e-ok")          # safe scalar survives
    assert s.attributes["token"] == "[REDACTED]"
    assert s.attributes["jcc.event_id"] == "e-ok"


def test_redaction_render_gate_strips_secret():
    # Second gate: a span populated by assigning attributes directly (the
    # "exporter renders a span it did not get from this package" path) is still
    # redacted on render.
    s = Span(name="x", context=TraceContext(new_trace_id(), new_span_id()))
    s.attributes["api_key"] = SENTINEL
    s.attributes["jcc.directive_id"] = "d-ok"
    rendered = s.to_dict()  # redact=True by default
    blob = json.dumps(rendered)
    assert SENTINEL not in blob
    assert rendered["attributes"]["jcc.directive_id"] == "d-ok"


def test_redaction_memory_exporter_no_secret():
    mem = MemoryExporter()
    t = Tracer(_cfg(exporter="memory"), exporter=mem)
    t.start()
    span = t.span(ChainHop.DIRECTIVE)
    span.set_attribute("token", f"Bearer {SENTINEL}")
    span.set_attribute("command", f"curl -H 'Authorization: Bearer {SENTINEL}' x")
    span.set_attribute("jcc.directive_id", "d-ok")
    t.record(span)
    t.wait_idle()
    t.stop()
    for p in mem.payloads():
        assert SENTINEL not in json.dumps(p), f"SECRET LEAK in {p}"
    assert any(p.get("attributes", {}).get("jcc.directive_id") == "d-ok"
               for p in mem.payloads())


def test_redaction_file_exporter_no_secret(tmp_path):
    path = tmp_path / "spans.jsonl"
    fe = FileExporter(str(path))
    t = Tracer(_cfg(exporter="file"), exporter=fe)
    t.start()
    span = t.span(ChainHop.DIRECTIVE)
    span.set_attribute("password", SENTINEL)
    span.set_attribute("jcc.event_id", "e-ok")
    t.record(span)
    t.wait_idle()
    t.stop()
    text = path.read_text(encoding="utf-8")
    assert SENTINEL not in text
    assert "e-ok" in text


def test_redaction_log_exporter_no_secret():
    logger = logging.getLogger(f"test.obs.log.{id(SENTINEL)}")
    records: list = []
    handler = logging.Handler()
    handler.emit = lambda rec: records.append(rec.getMessage())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    le = LoggingExporter(logger=logger, level="INFO")
    t = Tracer(_cfg(exporter="log"), exporter=le)
    t.start()
    span = t.span(ChainHop.DIRECTIVE)
    span.set_attribute("api_key", SENTINEL)
    t.record(span)
    t.wait_idle()
    t.stop()
    blob = "\n".join(records)
    assert SENTINEL not in blob


# --------------------------------------------------------------------------- #
# 9. Determinism
# --------------------------------------------------------------------------- #
def _fixed_span() -> Span:
    ctx = TraceContext(trace_id="a" * 32, span_id="b" * 16)
    s = Span(name="jcc.chain.ingest", context=ctx, kind="CHAIN", hop="ingest")
    s.set_attribute("jcc.event_id", "e")
    s.set_attribute("list", [3, 1, 2])
    s.set_attribute("nested", {"z": 1, "a": 2})
    s.start(1.0)
    s.end(2.0)
    return s


def test_determinism_span_serialization():
    a, b = _fixed_span(), _fixed_span()
    assert a.to_dict() == b.to_dict()          # identical content -> identical view
    assert a.to_dict() == a.to_dict()          # stable across calls
    assert a.to_json() == b.to_json()          # sorted keys -> byte-identical JSON
    assert a.to_json() == a.to_json()
    # to_json is sorted-key deterministic (independent of dict insertion order).
    assert json.loads(a.to_json()) == json.loads(b.to_json())


def test_determinism_tracer_health_stable():
    t = Tracer(_cfg(exporter="memory"), exporter=MemoryExporter())
    t.start()
    for _ in range(3):
        t.record(t.span(ChainHop.INGEST))
    t.wait_idle()
    t.stop()
    d1 = t.health.to_dict()
    d2 = t.health.to_dict()
    assert d1 == d2
    assert d1["spans_emitted"] == 3
