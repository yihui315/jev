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
* An action with no entry in the cost table ⇒ RAISES, in BOTH the pre-decision
  gate (``check``) and the post-decision writer (``commit``); it is never
  silently charged 0.0. A 0.0 charge is fail-OPEN — the ``cost_units`` ceiling
  could then never fire for that action. Mirrors the store-side derivation (#63).
* ``check`` prices the pending ``action`` through the SAME table, so the
  pre-decision gate cannot be blind to cost (D-5: an unpriced pre-check is what
  made the ``cost_units`` ceiling decorative). It never "looks up a cost and
  finds nothing, so treats it as 0" — that branch does not exist here.
* A dimension is REPORTED, never left at a silent 0.0: a dimension pinned at 0.0
  is what makes a ceiling *look* dead. ``cost_units_used`` is published on the
  snapshot; ``scorer_tokens`` has no persisted column and is therefore INERT
  against a store-backed governor (``StateStore.budget_snapshot`` says so), so
  it is LABELLED ``scorer_tokens_inert`` instead of masquerading as "0 of 200000,
  healthy" (#75b).
* There is **no** ``budget.enabled`` off-switch. A flippable off-switch is how a
  budget becomes decorative.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

from jcc_core.policy import COST_UNITS_PER_ACTION
from jcc_core.schemas import Directive, DirectiveAction

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

#: Static cost table (D-34). BOTH ``check`` (pre-decision gate, task #73) and
#: ``commit`` (post-decision writer) price an action through ``_unit_cost``, which
#: indexes this DIRECTLY (``COST_UNITS[action]``, never ``.get(action, 0.0)``) so
#: an uncostable action fails CLOSED — a missing entry raises instead of being
#: silently priced at 0.0, which would let the ``cost_units`` ceiling never fire
#: (fail-open). The table itself is defined once, in ``jcc_core.policy``, and
#: imported here as an ALIAS (identity pinned in tests): ``jcc_core`` must not
#: import ``supervisor`` (D-2), so the shared authority lives core-side, and
#: ``StateStore.budget_snapshot`` derives the persisted ``cost_units_used`` from
#: that same object. A second literal would be a drift surface.
COST_UNITS: Dict[DirectiveAction, float] = COST_UNITS_PER_ACTION


def _unit_cost(action: Optional[DirectiveAction]) -> float:
    """The ONE action → cost lookup (D-34 single cost authority).

    ``COST_UNITS`` is an ALIAS of ``jcc_core.policy.COST_UNITS_PER_ACTION`` and
    this is the ONLY place the supervisor plane reads it — no second literal
    table, no ``.get(action, 0.0)``. An uncostable action **RAISES** here, which
    is the same fail-closed read the store-side derivation performs
    (``StateStore.budget_snapshot``) and the same one ``commit`` performs, so the
    pre-decision gate, the post-decision writer and the persisted row cannot
    disagree about what a directive cost.

    ``action=None`` (no action priced, e.g. a pure status read) is 0.0 — that is
    "nothing is being charged", not "this action is free".
    """
    if action is None:
        return 0.0
    try:
        return COST_UNITS[action]
    except (KeyError, ValueError) as exc:
        raise ValueError(
            f"uncostable directive action {action!r}: cost_units_used cannot be "
            "derived, and under-counting would make the cost dimension fail-open"
        ) from exc


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
    # -- #75b: a dimension is either LIVE or LABELED INERT. A bare 0.0 is the
    # one reading that cannot be told apart from "0 used, ceiling healthy", and
    # a run of those is exactly what makes a ceiling look like decoration.
    cost_units_used: float = 0.0
    scorer_tokens_inert: bool = False

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
    def check(self, session_id: str, *, kind: str, units: int = 0,
              action: Optional[DirectiveAction] = None) -> BudgetDecision:
        """Return whether a further ``kind`` step is allowed under ``session_id``.

        ``units`` is the numeric cost of the step; ``action`` is the *directive
        action* about to be issued, priced through the ONE cost table. Passing
        neither is a pure status read and behaves exactly as it always did.

        WHY THIS CANNOT DOUBLE-BILL ``commit``
        --------------------------------------
        ``check`` is a pure READ. ``cost_units_used`` has exactly one writer,
        ``commit``, which the caller invokes once per *issued and recorded*
        directive. ``check`` does not reserve, place-hold or decrement anything:
        it projects the counter onto the limit dimensions, that projection
        includes the pending action's cost so the ceiling is judged on the
        post-decision state, and the counter itself is untouched. ``commit`` then
        adds that same cost once. Net effect per directive: **one** charge.

        The projection therefore over-states the pending action's cost for the
        duration of a single step, which trips the ceiling EARLY — i.e. in the
        fail-CLOSED direction. It can never trip late, which is the direction
        that makes a budget decorative.
        """
        try:
            counters = self._counters(session_id)
        except Exception:  # noqa: BLE001 — a counter read that raises is exhausted
            snap = BudgetSnapshot(
                assessments_used=0, directives_used=0, rate_used=0,
                model_units_used=0.0, limits=self.limits, exhausted_code=CODE_CHECK_ERROR,
                cost_units_used=0.0, scorer_tokens_inert=self._scorer_tokens_inert(),
            )
            return BudgetDecision(allowed=False, code=CODE_CHECK_ERROR, snapshot=snap)

        observed = self._observed(counters, kind, units, action)
        exhausted = self._first_exhausted(observed)
        snap = BudgetSnapshot(
            assessments_used=int(counters.get("assessments_used", 0)),
            directives_used=int(counters.get("directives_used", 0)),
            rate_used=int(counters.get("rate_used", 0)),
            model_units_used=float(counters.get("model_units_used", 0.0)),
            limits=self.limits,
            exhausted_code=exhausted,
            cost_units_used=float(counters.get("cost_units_used", 0.0)),
            scorer_tokens_inert=self._scorer_tokens_inert(),
        )
        return BudgetDecision(allowed=exhausted is None, code=exhausted, snapshot=snap)

    def _scorer_tokens_inert(self) -> bool:
        """True iff ``scorer_tokens`` CANNOT fire for this governor's counters.

        Against the in-memory ledger ``commit`` fills ``model_units_used``, so the
        dimension is live. Against a store the value is whatever the store
        derives, and ``StateStore.budget_snapshot`` reports ``model_units_used``
        as a hard 0.0 because no persisted column carries scorer token usage.
        That is a WAIVED dimension, not a broken one — so it is labelled, never
        left to read like "0 of 200000, healthy" (#75b).
        """
        return self.store is not None and hasattr(self.store, "budget_snapshot")

    def _observed(self, counters: Dict[str, float], kind: str, units: int,
                  action: Optional[DirectiveAction] = None) -> Dict[str, float]:
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
            CODE_COST_UNITS: (
                counters.get("cost_units_used", 0.0) + float(units) + _unit_cost(action)
            ),
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
               action: Optional[DirectiveAction] = None,
               directive: Optional[Directive] = None) -> BudgetSnapshot:
        """Record a completed step. Returns the post-commit snapshot.

        ``directive`` is the full issued ``Directive``; it is required whenever a
        store is wired (see ``_persist_directive``).

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
                # D-34 fail-CLOSED, via the SAME reader ``check`` uses (task #73).
                # An uncostable action RAISES instead of being silently priced at
                # 0.0, which mirrors the store-side derivation (#63,
                # ``StateStore.budget_snapshot``) that raises on the same input, so
                # the pre-decision gate, this writer and the persisted row all read
                # the ONE cost table identically. Contained by ``Sentinel.on_event``
                # (observe-only: it counts a persistence failure and never breaks
                # the engine's ingest loop), so this SURFACES rather than crashing
                # the daemon. This is the ONLY writer of ``cost_units_used``.
                row["cost_units_used"] += _unit_cost(action)
            if action is DirectiveAction.RETRY:
                row["retries_used"] += 1
            if action is DirectiveAction.ESCALATE:
                row["escalations_used"] += 1
            self._persist_directive(session_id, directive)
        row["cost_units_used"] += float(units)
        return self.snapshot(session_id)

    def _persist_directive(self, session_id: str,
                           directive: Optional[Directive]) -> None:
        """Make the charge DURABLE when the counters are store-derived (D-34).

        With a store wired, ``_counters`` reads the counters from
        ``store.budget_snapshot(session_id)`` and the ledger is never consulted —
        which means a plain ledger write was a no-op against the numbers that
        actually govern. Worse, the store derives ``cost_units_used``,
        ``directives_used``, ``retries_used``, ``escalations_used`` and
        ``rate_used`` from the ``directives`` TABLE, and nothing in the supervisor
        plane ever wrote to it: every one of those dimensions read 0.0 forever and
        the ceilings could not fire in production no matter what ``check`` priced.

        So the commit that charges a directive has to persist the directive. This
        uses the store's PUBLIC ``add_directive`` (same surface class as
        ``transaction()``/``budget_snapshot``) — not a private connection, and no
        hand-rolled SQL against the store's schema.

        If the store cannot be written, the charge CANNOT be made durable and this
        RAISES rather than silently accounting against nothing. Charging against a
        counter source we do not control is the #54 defect ("records nothing")
        wearing a budget costume.
        """
        if self.store is None or not hasattr(self.store, "budget_snapshot"):
            return  # the in-memory ledger IS the counter source; nothing to persist
        adder = getattr(self.store, "add_directive", None)
        if not callable(adder):
            raise RuntimeError(
                f"store {type(self.store).__name__} derives budget counters but "
                "exposes no add_directive(); the cost charge could not be made "
                "durable, so it is NOT being recorded"
            )
        if directive is None:
            raise RuntimeError(
                "a store-backed budget commit for a directive needs the issued "
                "Directive to persist the charge; refusing to charge against a "
                "counter source this governor does not control"
            )
        adder(directive)

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
                cost_units_used=float(counters.get("cost_units_used", 0.0)),
                scorer_tokens_inert=self._scorer_tokens_inert(),
            )
        except Exception:  # noqa: BLE001
            return BudgetSnapshot(
                0, 0, 0, 0.0, self.limits, CODE_CHECK_ERROR,
                scorer_tokens_inert=self._scorer_tokens_inert(),
            )

    # -- AssessmentBudgetGuard shim (jcc_core.ports) -------------------------
    def allow(self, session_id: str, units: int = 0) -> bool:
        return self.check(session_id, kind="assessment", units=units).allowed

    def as_guard(self) -> "BudgetGovernor":
        return self
