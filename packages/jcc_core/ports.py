"""Neutral interface Protocols for the JCC core (P3).

Per ADR-0008 D-2 / D-32, interfaces the engine needs are declared **here**, in
the neutral lower layer; implementations live in the supervisor plane
(``packages/supervisor``) and wiring lives in the composition root (``run.py``).
``jcc_core`` imports ONLY this module -- it must never import
``packages/supervisor`` (that would repeat the ADR-0007 dependency inversion).

This module is Protocols-only plus two trivial defaults, so it has no runtime
dependencies beyond the domain schemas.
"""
from __future__ import annotations

from typing import Protocol, TypedDict, runtime_checkable

from .schemas import Directive


class ActuationResult(TypedDict):
    """Return shape of a dispatch on any actuation port.

    Mirrors ``P3_ARCHITECTURE_SPEC.md`` §2.1. ``executable`` is carried
    additively so the D-40 falsifiers can assert ``executable is False`` on
    every port surface.
    """

    acted: bool
    reason: str
    directive_id: str


@runtime_checkable
class ActuationPort(Protocol):
    """The ENGINE seam only -- invoked at ``CoreEngine.ingest`` step 6.

    PERMANENTLY INERT: it is never an attach point, in P3 or later
    (ADR-0008 D-41). A future real actuator attaches at ``Sentinel._dispatch``
    (``supervisor/ports.DispatchPort``), NOT here. The default implementation
    is the disabled ``jcc_core.actuator.Actuator``.
    """

    def dispatch(self, directive: Directive) -> ActuationResult: ...


@runtime_checkable
class AssessmentBudgetGuard(Protocol):
    """Consulted by the engine BEFORE ``provider.assess`` (ADR-0008 D-5).

    The default is :class:`AllowAllGuard`, which preserves P0-P2 behaviour.
    ``BudgetGovernor`` (supervisor plane) also implements this Protocol.
    """

    def allow(self, session_id: str, units: int = 0) -> bool: ...

    def commit(self, session_id: str, units: int = 0) -> None: ...


class AllowAllGuard:
    """Default guard: allows everything (P0-P2 behaviour unchanged)."""

    def allow(self, session_id: str, units: int = 0) -> bool:
        return True

    def commit(self, session_id: str, units: int = 0) -> None:
        return None
