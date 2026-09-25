"""JEV COMMAND CENTER observability layer (S-03).

Dependency-free span/export model plus a feature-flagged, fail-closed tracer
whose exporter can die without stopping the supervision loop. See the module
docstrings of :mod:`observability.tracer`, :mod:`observability.exporters` and
:mod:`observability.health` for the isolation contract.
"""
from __future__ import annotations

__all__ = ["spans", "exporters", "health", "redaction", "semconv", "config", "tracer"]
