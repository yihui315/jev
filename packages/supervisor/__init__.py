"""P3 supervisor plane (ADR-0008).

Observe-only. It reads the engine's broadcast, re-arbitrates through the ONE
kernel (``jcc_core.decision``), signs, dispatches through a single inert port,
and records a hash-chained shadow decision. It never actuates.

Plane boundary (D-2): ``jcc_core`` must never import this package; wiring lives
only in the composition root (``jcc_core/run.py::_wire_shadow``).
"""
from __future__ import annotations

__all__ = ["arbitration", "budget", "evaluation", "ports", "sentinel", "shadow_store"]
