"""Budget governor (ADR-0008 D-34, revising D-5).

Design choice that matters
--------------------------
Counters are **DERIVED from persisted state**, not kept in memory, so a restart
cannot silently reset an escalation (ADR-0006). The governor asks the store for
a snapshot (``store.budget_snapshot(session_id)``) when a store is wired. When no
store is available it falls back to an in-memory ledger — which is **restart-
unsafe by definition** and therefore only acceptable for unit tests; the wiring
in ``run.py`` always passes the store.

Fail-closed rules (all from D-34):
* Any exhausted dimension ⇒ ``allowed=False`` with a code. The caller **replaces**
  the action with ``ESCALATE`` — never suppresses it, never ``CONTINUE``.
* A counter read that raises ⇒ exhausted with ``budget_check_error``.
* An action with no entry in the cost table ⇒ ``commit`` RAISES; it is never
  silently charged 0.0. A 0.0 charge is fail-OPEN — the ``cost_units`` ceiling
  could then never fire for that action. Mirrors the store-side derivation (#63).
* There is **no** ``budget.enabled`` off-switch. A flippable off-switch is how a
  budget becomes decorative.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

from jcc_core.policy import COST_UNITS_PER_ACTION
from jcc_core.schemas import DirectiveAction

log = logging.getLogger("jcc.supervisor.budget")

#: Counter keys ``BudgetGovernor`` expects from ``store.budget_snapshot(session_id)``.
#: (The store surface is D-34's; this is the exact contract, so the two halves
#: cannot disagree about names.) Missing keys default to 0. ``first_seen`` is the
#: session start used for the wall-clock dimension.
BUDGET_SNAPSHOT_KEYS: Tuple[str, ...] = (
    "assessments_used",   # -> assess_calls limit
    "model_units_used",   # -> scorer_tokens limit
    "events_used",        # -> events limit
    "directives_used",    # -> directives limit
    "retries_used",       # -> retries limit
    "escalations_used",   # -> escalations limit
    "cost_units_used",    # -> cost_units limit
    "rate_used",          # -> directives_per_minute limit
    "first_seen",         # -> wall_clock_s limit (session start)
)

#: Static cost table (D-34). ``commit`` indexes it DIRECTLY (``COST_UNITS[action]``,
#: never ``.get(action, 0.0)``) so an uncostable action fails CLOSED — a missing
#: entry raises instead of being silently priced at 0.0, which would let the
#: ``cost_units`` ceiling never fire (fail-open). The table itself is defined once,
#: in ``jcc_core.policy``, and imported here as an ALIAS (identity pinned in tests):
#: ``jcc_core`` must not import ``supervisor`` (D-2), so the shared authority lives
#: core-side, and ``StateStore.budget_snapshot`` derives the persisted
#: ``cost_units_used`` from that same object. A second literal would be a drift
#: surface.
COST_UNITS: Dict[DirectiveAction, float] = COST_UNITS_PER_ACTION

CODE_ASSESS_CALLS = "assess_calls"
CODE_SCORER_TOKENS = "scorer_tokens"
CODE_EVENTS = "events"
CODE_DIRECTIVES = "directives"
CODE_RETRIES = "retries"
CODE_ESCALATIONS = "escalations"
CODE_WALL_CLOCK = "wall_clock_s"
CODE_COST_UNITS = "cost_units"
CODE_RATE = "directives_per_minute"
CODE_CHECK_ERROR = "budget_check_error"


class BudgetExhausted(Exception):
    """Raised only where callers opt into exceptions; ``check`` returns instead."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class BudgetLimits:
    """D-34 defaults. Field names match ``config/jcc.yaml`` ``p3.shadow.budget``."""

    assess_calls: int = 200
    scorer_tokens: int = 200_000
    events: int = 2000
    directives: int = 200
    retries: int = 5
    escalations: int = 3
    wall_clock_s: float = 28800.0
    cost_units: float = 500.0
    cost_units_per_hour: float = 2000.0
    directives_per_minute: int = 600


@dataclass(frozen=True)
class BudgetSnapshot:
    assessments_used: int
    directives_used: int
    rate_used: int
    model_units_used: float
    limits: BudgetLimits
    exhausted_code: Optional[str] = None

    @property
    def healthy(self) -> bool:
        return self.exhausted_code is None


@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool
    code: Optional[str]
    snapshot: BudgetSnapshot


#: Deterministic check order — first violated dimension wins.
_CHECK_ORDER: Tuple[str, ...] = (
    CODE_ASSESS_CALLS, CODE_SCORER_TOKENS, CODE_EVENTS, CODE_DIRECTIVES,
    CODE_RETRIES, CODE_ESCALATIONS, CODE_WALL_CLOCK, CODE_COST_UNITS, CODE_RATE,
)


class BudgetGovernor:
    """Derive-and-check budget governor.

    Parameters
    ----------
    limits
        The frozen limit set.
    store
        Optional object exposing ``budget_snapshot(session_id) -> dict`` with the
        derived counters. Without it, an in-memory (restart-unsafe) ledger is used.
    clock
        The clock used for the wall-clock dimension. It MUST share an origin with
        ``budget_snapshot()["first_seen"]``, which is a WALL-CLOCK epoch second
        (``sessions.created_at``) — so a store-backed governor MUST be built with
        ``clock=time.time`` (what ``run.py`` does). The ``time.monotonic`` default
        is only valid for the in-memory ledger, whose ``first_seen`` it writes
        itself; against a real store it makes ``elapsed`` a large negative number
        and the wall-clock cut silently never fires.
    """

    def __init__(
        self,
        limits: Optional[BudgetLimits] = None,
        *,
        store: Any = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limits = limits if limits is not None else BudgetLimits()
        self.store = store
        self._clock = clock
        self._ledger: Dict[str, Dict[str, float]] = {}
        self._warned_no_snapshot = False

    # -- counter source ------------------------------------------------------
    def _ledger_row(self, session_id: str) -> Dict[str, float]:
        row = self._ledger.get(session_id)
        if row is None:
            row = {
                "assessments_used": 0.0,
                "directives_used": 0.0,
                "retries_used": 0.0,
                "escalations_used": 0.0,
                "model_units_used": 0.0,
                "cost_units_used": 0.0,
                "events_used": 0.0,
                "rate_used": 0.0,
                "first_seen": self._clock(),
            }
            self._ledger[session_id] = row
        return row

    def _counters(self, session_id: str) -> Dict[str, float]:
        """Derive the counter set. A read failure is surfaced, not swallowed."""
        if self.store is not None and hasattr(self.store, "budget_snapshot"):
            raw = self.store.budget_snapshot(session_id)
            if not isinstance(raw, dict):
                raise TypeError("budget_snapshot must return a dict")
            return {k: float(v) for k, v in raw.items()}
        if self.store is not None and not self._warned_no_snapshot:
            # A store was wired but has no budget_snapshot(): D-34's restart-safety
            # is then DECORATIVE (we fall back to the restart-unsafe ledger). Fail
            # LOUDLY once rather than silently pretending the budget is durable.
            log.warning(
                "store %r has no budget_snapshot(); budget counters are IN-MEMORY "
                "(restart-unsafe) — D-34 restart-safety is decorative until the "
                "store surface lands (see BUDGET_SNAPSHOT_KEYS)",
                type(self.store).__name__,
            )
            self._warned_no_snapshot = True
        return dict(self._ledger_row(session_id))

    # -- evaluation ----------------------------------------------------------
    def check(self, session_id: str, *, kind: str, units: int = 0) -> BudgetDecision:
        """Return whether a further ``kind`` step is allowed under ``session_id``."""
        try:
            counters = self._counters(session_id)
        except Exception:  # noqa: BLE001 — a counter read that raises is exhausted
            snap = BudgetSnapshot(
                assessments_used=0, directives_used=0, rate_used=0,
                model_units_used=0.0, limits=self.limits, exhausted_code=CODE_CHECK_ERROR,
            )
            return BudgetDecision(allowed=False, code=CODE_CHECK_ERROR, snapshot=snap)

        observed = self._observed(counters, kind, units)
        exhausted = self._first_exhausted(observed)
        snap = BudgetSnapshot(
            assessments_used=int(counters.get("assessments_used", 0)),
            directives_used=int(counters.get("directives_used", 0)),
            rate_used=int(counters.get("rate_used", 0)),
            model_units_used=float(counters.get("model_units_used", 0.0)),
            limits=self.limits,
            exhausted_code=exhausted,
        )
        return BudgetDecision(allowed=exhausted is None, code=exhausted, snapshot=snap)

    def _observed(self, counters: Dict[str, float], kind: str, units: int) -> Dict[str, float]:
        """Project the counters onto the limit dimensions, *prospectively*.

        The counts are read **before** the current step is recorded (the D-34
        off-by-one note): ``units`` is the cost of the step we are about to take.
        """
        elapsed = self._clock() - float(counters.get("first_seen", self._clock()))
        return {
            CODE_ASSESS_CALLS: counters.get("assessments_used", 0) + (1 if kind == "assessment" else 0),
            CODE_SCORER_TOKENS: counters.get("model_units_used", 0.0),
            CODE_EVENTS: counters.get("events_used", 0.0),
            CODE_DIRECTIVES: counters.get("directives_used", 0) + (1 if kind == "directive" else 0),
            CODE_RETRIES: counters.get("retries_used", 0.0),
            CODE_ESCALATIONS: counters.get("escalations_used", 0.0),
            CODE_WALL_CLOCK: elapsed,
            CODE_COST_UNITS: counters.get("cost_units_used", 0.0) + float(units),
            CODE_RATE: counters.get("rate_used", 0.0),
        }

    def _first_exhausted(self, observed: Dict[str, float]) -> Optional[str]:
        for code in _CHECK_ORDER:
            limit = getattr(self.limits, code)
            if float(observed.get(code, 0.0)) > float(limit):
                return code
        return None

    # -- mutation ------------------------------------------------------------
    def commit(self, session_id: str, units: int = 0, *, kind: Optional[str] = None,
               action: Optional[DirectiveAction] = None) -> BudgetSnapshot:
        """Record a completed step. Returns the post-commit snapshot.

        Also satisfies ``jcc_core.ports.AssessmentBudgetGuard.commit`` (which is
        called with ``(session_id, units)`` and ignores the return value).
        """
        row = self._ledger_row(session_id)
        if kind == "assessment":
            row["assessments_used"] += 1
            row["model_units_used"] += float(units)
        elif kind == "directive":
            row["directives_used"] += 1
            row["rate_used"] += 1
            if action is not None:
                try:
                    unit_cost = COST_UNITS[action]
                except (KeyError, ValueError) as exc:
                    # D-34 fail-CLOSED: an uncostable action must NOT be silently
                    # priced at 0.0, or the ``cost_units`` ceiling can never fire for
                    # it (fail-OPEN — the mode D-5/D-34 forbid). This mirrors the
                    # store-side derivation (#63, ``StateStore.budget_snapshot``),
                    # which raises on the same input, so the two halves of the ONE
                    # cost authority cannot disagree. Contained by
                    # ``Sentinel.on_event`` (observe-only: it counts a persistence
                    # failure and never breaks the engine's ingest loop), so this
                    # SURFACES rather than crashing the daemon.
                    raise ValueError(
                        f"uncostable directive action {action!r}: cost_units_used "
                        "cannot be derived, and under-counting would make the cost "
                        "dimension fail-open"
                    ) from exc
                row["cost_units_used"] += unit_cost
            if action is DirectiveAction.RETRY:
                row["retries_used"] += 1
            if action is DirectiveAction.ESCALATE:
                row["escalations_used"] += 1
        row["cost_units_used"] += float(units)
        return self.snapshot(session_id)

    def snapshot(self, session_id: str) -> BudgetSnapshot:
        try:
            counters = self._counters(session_id)
            code = self._first_exhausted(self._observed(counters, kind="none", units=0))
            return BudgetSnapshot(
                assessments_used=int(counters.get("assessments_used", 0)),
                directives_used=int(counters.get("directives_used", 0)),
                rate_used=int(counters.get("rate_used", 0)),
                model_units_used=float(counters.get("model_units_used", 0.0)),
                limits=self.limits,
                exhausted_code=code,
            )
        except Exception:  # noqa: BLE001
            return BudgetSnapshot(0, 0, 0, 0.0, self.limits, CODE_CHECK_ERROR)

    # -- AssessmentBudgetGuard shim (jcc_core.ports) -------------------------
    def allow(self, session_id: str, units: int = 0) -> bool:
        return self.check(session_id, kind="assessment", units=units).allowed

    def as_guard(self) -> "BudgetGovernor":
        return self
