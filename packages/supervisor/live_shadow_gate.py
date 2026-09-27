"""The live-shadow observation gate (v1.1 §19) — the reason actuation stays OFF.

WHAT IT GATES
-------------
``actuation_levels`` says which rung the OPERATOR configured. ``live_shadow_gate``
says which rung the EVIDENCE supports. The effective rung is the more
restrictive of the two::

    effective_level = restrict(configured_level, granted_ceiling)

and ``granted_ceiling`` is ``L0`` unless the observation criteria are met AND the
criteria themselves name a higher ceiling. So there are **two independent
refusals** between "an operator wants effects on" and "effects happen", and
either one alone is sufficient to keep the system inert.

THE INVARIANT THIS EXISTS TO PROTECT
------------------------------------
The v1.1 plan's most important constraint, in its own words: *even if all v1.1
engineering completes, without meeting the live-shadow observation threshold, do
NOT force ``actuation_enabled`` open for the sake of "finishing".* Accordingly::

    JCC v1.1 = COMPLETE / Real Jev = PENDING_EXTERNAL_ACTIVATION /
    Actuation = False / Live Shadow Gate = PENDING

is a LAWFUL, SAFE completion. This module is what makes that sentence executable
rather than aspirational: on the shipped configuration it returns exactly
``PENDING_LIVE_SHADOW_GATE`` and ``actuation_enabled=False``, and there is no
parameter whose absence or default flips that. Turning effects on requires
(a) the criteria met, (b) a ceiling raised in the criteria, and (c) the operator's
own ``enabled`` flag — three separate true statements.

EVIDENCE, NOT ASSERTION
-----------------------
The gate reads **recorded shadow decisions off the tape** and re-verifies the hash
chain. It never accepts a caller's word for how many decisions were observed, for
how long, or for whether anything acted. A gate that took those as arguments would
be satisfiable by the very thing it is supposed to be checking.

Three-state discipline, applied here because this is a safety gate — and applied
at BOTH levels, because this gate learned the lesson twice:

* the database **cannot be read** at all ⇒ ``PENDING`` with the defect
  ``STORAGE_UNAVAILABLE`` and the exception preserved in ``read_failure``;
* the table has **never been created** ⇒ ``PENDING`` with ``SHADOW_TAPE_ABSENT``.
  That is a determinate finding, not a failure to look — the real runtime
  database (``runtime/jcc/jcc.db``) has no ``shadow_decisions`` table because the
  shadow plane has never run against it, and reporting that as "storage
  unavailable" would send an operator to debug a working database;
* the table exists and is **empty** ⇒ ``PENDING`` with ``NO_OBSERVATIONS``.

None of the three is ever ``SATISFIED``, and none is folded into another. The
project has already been bitten by a probe that wrote "it does not exist" when
the truth was "I could not look" (the scheduled-task check that could not load its
CIM provider); a safety gate is the worst possible place to repeat it.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from . import actuation_levels as AL

#: The gate has not been satisfied. The shipped, expected, lawful state.
GATE_PENDING = "PENDING_LIVE_SHADOW_GATE"
#: The criteria are met. Note this is NOT the same as "actuation is on": the
#: ceiling in ``LiveShadowGateCriteria`` and the operator's ``enabled`` flag are
#: still separate conditions.
GATE_SATISFIED = "SATISFIED"

#: Defect codes (frozen). Every one of them forces ``GATE_PENDING``.
DEFECT_NO_OBSERVATIONS = "no_observations"
DEFECT_INSUFFICIENT_OBSERVATIONS = "insufficient_observations"
DEFECT_INSUFFICIENT_WINDOW = "insufficient_observation_window"
DEFECT_ACTUATION_OBSERVED = "actuation_observed"
DEFECT_CHAIN_BROKEN = "shadow_chain_broken"
DEFECT_STORAGE_UNAVAILABLE = "storage_unavailable"
#: The database has no ``shadow_decisions`` table. This is a DETERMINATE finding
#: ("the tape has never been created here"), not a failure to look, so it must not
#: be reported as ``storage_unavailable``. Measured on the real runtime database:
#: ``runtime/jcc/jcc.db`` contains sessions/events/evidence/assessments/
#: directives/audit_tape and **no** shadow table — the shadow plane has never run
#: against it. Collapsing that into "storage unavailable" would send an operator
#: to debug a database that is working perfectly.
DEFECT_TAPE_ABSENT = "shadow_tape_absent"

#: The tape table this gate reads. Named so the existence probe and the reader
#: cannot disagree about what "the tape" is.
TAPE_TABLE = "shadow_decisions"

#: Observations the gate demands before it will even consider a rung above L0.
#: Chosen to be large enough that a handful of hand-fed events cannot satisfy it
#: by accident, and it is a NAMED constant so raising or lowering it is a visible
#: edit rather than a number embedded in a comparison.
DEFAULT_MIN_DECISIONS = 50
#: Wall-clock span the observations must cover. 24h: one full working cycle, so a
#: single burst cannot look like sustained supervision.
DEFAULT_MIN_WALL_CLOCK_S = 86_400.0


@dataclass(frozen=True)
class LiveShadowGateCriteria:
    """What must be TRUE of the recorded evidence.

    ``granted_ceiling`` is the rung the gate is willing to permit when satisfied.
    It defaults to ``L0`` deliberately: satisfying the criteria proves the shadow
    plane behaved, it does NOT prove that effects should be enabled, so the
    ceiling stays shut until somebody raises it in the criteria — a second,
    explicit act, separate from merely meeting the thresholds.
    """

    min_decisions: int = DEFAULT_MIN_DECISIONS
    min_wall_clock_s: float = DEFAULT_MIN_WALL_CLOCK_S
    granted_ceiling: AL.ActuationLevel = AL.ActuationLevel.L0

    def __post_init__(self) -> None:
        if self.min_decisions <= 0:
            raise ValueError("min_decisions must be positive; a 0-observation bar is not a bar")
        if self.min_wall_clock_s <= 0:
            raise ValueError("min_wall_clock_s must be positive; a 0-second window is not a window")


@dataclass(frozen=True)
class LiveShadowGateResult:
    """The gate's answer, with the evidence that produced it."""

    status: str
    actuation_enabled: bool
    configured_level: AL.ActuationLevel
    effective_level: AL.ActuationLevel
    granted_ceiling: AL.ActuationLevel
    observed_decisions: int = 0
    observed_span_s: float = 0.0
    actuated_observations: int = 0
    chain_intact: Optional[bool] = None
    defects: Tuple[str, ...] = field(default_factory=tuple)
    criteria: Optional[LiveShadowGateCriteria] = None
    evaluated_at: float = 0.0
    read_failure: Optional[str] = None

    @property
    def satisfied(self) -> bool:
        return self.status == GATE_SATISFIED

    @property
    def pending(self) -> bool:
        return self.status == GATE_PENDING

    @property
    def readings(self) -> Dict[str, Any]:
        """The raw observations this verdict was computed from."""
        return {
            "observed_decisions": self.observed_decisions,
            "observed_span_s": self.observed_span_s,
            "actuated_observations": self.actuated_observations,
            "chain_intact": self.chain_intact,
        }

    def as_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "actuation_enabled": self.actuation_enabled,
            "configured_level": self.configured_level.value,
            "effective_level": self.effective_level.value,
            "granted_ceiling": self.granted_ceiling.value,
            "satisfied": self.satisfied,
            "defects": list(self.defects),
            "read_failure": self.read_failure,
            "evaluated_at": self.evaluated_at,
            "thresholds": (
                None
                if self.criteria is None
                else {
                    "min_decisions": self.criteria.min_decisions,
                    "min_wall_clock_s": self.criteria.min_wall_clock_s,
                    "granted_ceiling": self.criteria.granted_ceiling.value,
                }
            ),
            **self.readings,
        }


def _pending(
    *,
    criteria: LiveShadowGateCriteria,
    configured_level: AL.ActuationLevel,
    defects: Tuple[str, ...],
    evaluated_at: float,
    configured_enabled: bool,   # noqa: ARG001 — see _decide: it can only SUBTRACT
    observed_decisions: int = 0,
    observed_span_s: float = 0.0,
    actuated_observations: int = 0,
    chain_intact: Optional[bool] = None,
    read_failure: Optional[str] = None,
) -> LiveShadowGateResult:
    """A PENDING result. ``effective_level`` is forced to ``L0`` — always."""
    return LiveShadowGateResult(
        status=GATE_PENDING,
        actuation_enabled=False,   # never consulted: pending means OFF by definition
        configured_level=configured_level,
        effective_level=AL.restrict(configured_level, AL.ActuationLevel.L0),
        granted_ceiling=criteria.granted_ceiling,
        observed_decisions=observed_decisions,
        observed_span_s=observed_span_s,
        actuated_observations=actuated_observations,
        chain_intact=chain_intact,
        defects=defects,
        criteria=criteria,
        evaluated_at=evaluated_at,
        read_failure=read_failure,
    )


def _decide(
    *,
    criteria: LiveShadowGateCriteria,
    configured_level: AL.ActuationLevel,
    configured_enabled: bool,
    rows: Any,
    decisions: Tuple[Dict[str, Any], ...],
    chain_intact: bool,
    evaluated_at: float,
) -> LiveShadowGateResult:
    """Pure decision over already-read evidence. Separate from I/O so a test can
    drive every branch without a database."""
    count = len(decisions)
    actuated = 0
    times = []
    for row in decisions:
        if row.get("acted"):
            actuated += 1
        observed_at = row.get("observed_at")
        if isinstance(observed_at, (int, float)):
            times.append(float(observed_at))
    span = (max(times) - min(times)) if len(times) >= 2 else 0.0

    defects = []
    if not chain_intact:
        defects.append(DEFECT_CHAIN_BROKEN)
    if actuated > 0:
        # Something acted during what is supposed to be a shadow-only period.
        # This is not a threshold miss, it is a contradiction of the premise the
        # whole observation window rests on, so it is a defect at ANY count.
        defects.append(DEFECT_ACTUATION_OBSERVED)
    if count == 0:
        defects.append(DEFECT_NO_OBSERVATIONS)
    elif count < criteria.min_decisions:
        defects.append(DEFECT_INSUFFICIENT_OBSERVATIONS)
    if span < criteria.min_wall_clock_s:
        defects.append(DEFECT_INSUFFICIENT_WINDOW)

    if defects:
        return _pending(
            criteria=criteria,
            configured_level=configured_level,
            configured_enabled=configured_enabled,
            defects=tuple(defects),
            evaluated_at=evaluated_at,
            observed_decisions=count,
            observed_span_s=span,
            actuated_observations=actuated,
            chain_intact=chain_intact,
        )

    # Criteria met. NOTE the three separate conditions, and that `configured_enabled`
    # can only ever SUBTRACT capability here — never add it.
    ceiling = criteria.granted_ceiling
    effective = AL.restrict(configured_level, ceiling)
    enabled = bool(configured_enabled) and AL.gate_runs(effective)
    return LiveShadowGateResult(
        status=GATE_SATISFIED,
        actuation_enabled=enabled,
        configured_level=configured_level,
        effective_level=effective,
        granted_ceiling=ceiling,
        observed_decisions=count,
        observed_span_s=span,
        actuated_observations=actuated,
        chain_intact=chain_intact,
        defects=(),
        criteria=criteria,
        evaluated_at=evaluated_at,
    )


def evaluate_live_shadow_gate(
    conn: Any,
    *,
    criteria: Optional[LiveShadowGateCriteria] = None,
    configured_level: AL.ActuationLevel = AL.DEFAULT_ACTUATION_LEVEL,
    configured_enabled: bool = False,
    clock: Any = time.time,
) -> LiveShadowGateResult:
    """Evaluate the gate against the REAL shadow tape behind ``conn``.

    ``conn`` is a raw sqlite3 connection (the tape lives outside ``StateStore``,
    so there is no store method for this and inventing one would put
    ``jcc_core``→``supervisor`` knowledge in the wrong plane).

    A read that raises returns ``PENDING`` with ``storage_unavailable`` and the
    exception text preserved in ``read_failure``. It does NOT re-raise and it does
    NOT guess: an unreadable tape must never read as an satisfied one, and a gate
    that raises is a gate a caller can catch-and-ignore.
    """
    crit = criteria or LiveShadowGateCriteria()
    now = float(clock())

    # -- probe existence FIRST, so "absent" is never reported as "unreadable" ---
    try:
        from . import shadow_store  # local import: keeps module import cheap and explicit

        present = (
            conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (TAPE_TABLE,),
            ).fetchone()
            is not None
        )
    except Exception as exc:  # noqa: BLE001 — genuinely unreadable
        return _pending(
            criteria=crit,
            configured_level=configured_level,
            configured_enabled=configured_enabled,
            defects=(DEFECT_STORAGE_UNAVAILABLE,),
            evaluated_at=now,
            chain_intact=None,
            read_failure=f"{type(exc).__name__}: {exc}",
        )

    if not present:
        # A determinate answer, and a different one from "unreadable". No chain
        # exists to verify, so ``chain_intact`` is None (cannot know), NOT False
        # (known broken) — the same three-state rule, applied one level down.
        return _pending(
            criteria=crit,
            configured_level=configured_level,
            configured_enabled=configured_enabled,
            defects=(DEFECT_TAPE_ABSENT, DEFECT_NO_OBSERVATIONS),
            evaluated_at=now,
            chain_intact=None,
        )

    try:
        rows = shadow_store.read_decisions(conn, limit=1_000_000, with_labels=False)
        chain_intact, _ = shadow_store.verify_shadow_chain(conn)
    except Exception as exc:  # noqa: BLE001 — the tape exists but cannot be read
        return _pending(
            criteria=crit,
            configured_level=configured_level,
            configured_enabled=configured_enabled,
            defects=(DEFECT_STORAGE_UNAVAILABLE,),
            evaluated_at=now,
            chain_intact=None,
            read_failure=f"{type(exc).__name__}: {exc}",
        )
    return _decide(
        criteria=crit,
        configured_level=configured_level,
        configured_enabled=configured_enabled,
        rows=rows,
        decisions=tuple(rows),
        chain_intact=bool(chain_intact),
        evaluated_at=now,
    )


def describe() -> Dict[str, Any]:
    """Pinned description of the gate's vocabulary and defaults."""
    return {
        "statuses": [GATE_PENDING, GATE_SATISFIED],
        "pending_status": GATE_PENDING,
        "defect_codes": [
            DEFECT_NO_OBSERVATIONS,
            DEFECT_INSUFFICIENT_OBSERVATIONS,
            DEFECT_INSUFFICIENT_WINDOW,
            DEFECT_ACTUATION_OBSERVED,
            DEFECT_CHAIN_BROKEN,
            DEFECT_TAPE_ABSENT,
            DEFECT_STORAGE_UNAVAILABLE,
        ],
        "default_criteria": {
            "min_decisions": DEFAULT_MIN_DECISIONS,
            "min_wall_clock_s": DEFAULT_MIN_WALL_CLOCK_S,
            "granted_ceiling": AL.ActuationLevel.L0.value,
        },
        "pending_forces_actuation_enabled_false": True,
        "unreadable_tape_is_pending": True,
        "satisfied_alone_does_not_enable_actuation": True,
    }
