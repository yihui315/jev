"""Supervisor dispatch port — THE P4 attach point (ADR-0008 D-3/D-41).

The supervisor plane owns exactly one actuation seam, ``Sentinel._dispatch``.
The future real actuator attaches by swapping the port injected there. This is a
**distinct type** from the engine's ``jcc_core.ports.ActuationPort`` on purpose:

* ``ActuationPort`` is invoked at ``CoreEngine.ingest`` step 6 (``core.py:143``)
  and is **permanently inert** — never an attach point, in P3 or later.
* ``DispatchPort`` (here) is invoked at ``Sentinel._dispatch`` and is the **only**
  place a real actuator may be attached.

Because Python Protocols with the same method+return are structurally
interchangeable, keeping the two types distinct is what lets the D-41 falsifier
("exactly one call site per distinct type") actually mean something.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from jcc_core.ports import ActuationResult
from jcc_core.schemas import Directive

#: Reason a shadow dispatch always returns. Never "acted".
SHADOW_NO_ACTUATION = "shadow_no_actuation"


@runtime_checkable
class DispatchPort(Protocol):
    """The supervisor actuation seam. One method, one call site (D-41)."""

    def dispatch(self, directive: Directive) -> ActuationResult: ...


class ShadowActuationPort:
    """P3 default: records intent, never acts.

    Returns ``acted=False`` and ``executable=False`` for every directive, with a
    stable reason, so the shadow plane can prove the seam is *wired* without the
    wire being *live*. ``would_have_acted`` is observational only and grants no
    capability.
    """

    def dispatch(self, directive: Directive) -> ActuationResult:
        return {
            "acted": False,
            "reason": SHADOW_NO_ACTUATION,
            "directive_id": getattr(directive, "directive_id", ""),
            "executable": False,
        }
