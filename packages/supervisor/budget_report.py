"""Budget reporting with cost PROVENANCE (v1.1 §18).

THE PROBLEM THIS SOLVES
-----------------------
A budget report that prints a number without saying where the number came from is
not a report, it is an assertion. The specific assertion this module forbids is
the most natural one a dashboard makes: printing ``0`` for a quantity that was
never measured. ``0`` is a perfectly good *measurement* -- "nothing was spent" --
and it is indistinguishable on screen from "we have no idea", which is the
reading that gets somebody to sign off on a spend they cannot see.

So every cost dimension in this module carries a **provenance label** alongside
its value, the label vocabulary is closed, and one combination is made
*unrepresentable* rather than merely discouraged::

    USAGE_UNAVAILABLE   no measurement exists        value MUST be None
    MOCK_COST           synthetic / notional units   value is a number, labelled
    REPLAY_COST         a replayed run costs 0       value IS 0.0

``USAGE_UNAVAILABLE`` with a numeric value raises in ``__post_init__``. That is
a stronger guarantee than a test that looks for fabrication: there is no code
path in this module, and no future caller, that can build the object. "We did not
measure this, so here is the number" is not a mistake this file can make.

WHY ``REPLAY_COST`` IS A LABEL AND NOT JUST ``0.0``
--------------------------------------------------
Because ``0.0`` alone is the ambiguous value. A replay of a recorded session
genuinely makes no provider call, so its provider cost is exactly zero and that
is a *fact about the run*, not a missing measurement. Labelling it
``REPLAY_COST`` is what lets a reader tell "we replayed, and replaying is free"
apart from "the accounting is broken and reported nothing".

WHICH DIMENSIONS EXIST, AND WHY THE TOKEN ONE IS ALWAYS UNAVAILABLE
-------------------------------------------------------------------
Two dimensions, and they have different owners:

* **directive cost units** — owned by the CONTROL PLANE and genuinely derivable:
  ``StateStore.budget_snapshot`` derives it from the persisted ``directives``
  rows through ``jcc_core.policy.COST_UNITS_PER_ACTION``. It is labelled
  ``MOCK_COST`` on purpose, because those units are *notional*: they are weights
  from a policy table, not currency, and ``ESCALATE = 10.0`` is a policy choice
  rather than a price anybody pays.
* **scorer / model tokens** — owned by the PROVIDER, and **always
  ``USAGE_UNAVAILABLE`` for a real provider**. No persisted column carries token
  usage, which is exactly why ``budget.WAIVED_DIMENSIONS`` waives the
  ``scorer_tokens`` ceiling (``#75b``, lead ruling). This module does not
  contradict that waiver by inventing a number for the dimension the waiver
  exists because of; it reports the absence, loudly and by name.

The mock provider's units are labelled ``MOCK_COST`` rather than
``USAGE_UNAVAILABLE`` because the mock *does* return deterministic units: the
measurement exists, it is simply synthetic. Calling that "unavailable" would
throw away information; calling it "measured" would overstate it.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

# -- the closed label vocabulary --------------------------------------------- #
#: No measurement exists. A value MUST NOT accompany this label.
PROVENANCE_USAGE_UNAVAILABLE = "USAGE_UNAVAILABLE"
#: The number exists but is synthetic (policy-weighted units, or a mock provider).
PROVENANCE_MOCK_COST = "MOCK_COST"
#: The run was a replay: no provider call happened, so the provider cost is 0.
PROVENANCE_REPLAY_COST = "REPLAY_COST"

#: The complete set. Pinned: a UI switch statement over these three is exhaustive,
#: and a new label must be a deliberate edit here rather than a string invented at
#: a call site.
COST_PROVENANCE_VALUES: Tuple[str, ...] = (
    PROVENANCE_USAGE_UNAVAILABLE,
    PROVENANCE_MOCK_COST,
    PROVENANCE_REPLAY_COST,
)

#: Provider-mode → provider-cost provenance. Anything not named explicitly is
#: ``USAGE_UNAVAILABLE``: the safe direction is to admit ignorance, never to
#: assume a mode we do not recognise is free or synthetic. Note this means a
#: misspelled mode cannot accidentally produce a number.
_PROVIDER_MODE_PROVENANCE: Dict[str, str] = {
    "replay": PROVENANCE_REPLAY_COST,
    "mock": PROVENANCE_MOCK_COST,
    "real": PROVENANCE_USAGE_UNAVAILABLE,
    "disabled": PROVENANCE_USAGE_UNAVAILABLE,
}

#: Dimension codes. ``scorer_tokens`` mirrors ``budget.CODE_SCORER_TOKENS`` (the
#: waived dimension); ``directive_cost_units`` mirrors ``budget.CODE_COST_UNITS``.
DIM_SCORER_TOKENS = "scorer_tokens"
DIM_DIRECTIVE_COST_UNITS = "directive_cost_units"

#: Counter-source labels. "A store was wired" and "the store actually answered"
#: are different facts, and the D-34 restart-safety claim rests on the second.
COUNTER_SOURCE_STORE = "store"
COUNTER_SOURCE_LEDGER = "ledger"
COUNTER_SOURCE_UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class DimensionReport:
    """One measured-or-not quantity, with the label that says which."""

    code: str
    provenance: str
    value: Optional[float]
    note: str = ""

    def __post_init__(self) -> None:
        if self.provenance not in COST_PROVENANCE_VALUES:
            raise ValueError(
                f"unknown cost provenance {self.provenance!r}; the closed set is "
                f"{list(COST_PROVENANCE_VALUES)}"
            )
        # THE anti-fabrication rule. Unrepresentable, not merely checked.
        if self.provenance == PROVENANCE_USAGE_UNAVAILABLE and self.value is not None:
            raise ValueError(
                f"dimension {self.code!r} is labelled {PROVENANCE_USAGE_UNAVAILABLE} "
                f"but carries value {self.value!r}: an unmeasured quantity must "
                "report no number, because a 0.0 there is the one reading that "
                "cannot be told apart from a real zero"
            )
        if self.provenance == PROVENANCE_REPLAY_COST and self.value != 0.0:
            raise ValueError(
                f"dimension {self.code!r} is labelled {PROVENANCE_REPLAY_COST} but "
                f"carries value {self.value!r}; a replay incurs no provider cost, "
                "so the only honest value is exactly 0.0"
            )

    @property
    def measured(self) -> bool:
        return self.value is not None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "provenance": self.provenance,
            "value": self.value,
            "measured": self.measured,
            "note": self.note,
        }


@dataclass(frozen=True)
class BudgetReport:
    """A budget snapshot plus the provenance of every number in it."""

    session_id: str
    provider_mode: str
    counter_source: str
    dimensions: Tuple[DimensionReport, ...]
    counters: Dict[str, float] = field(default_factory=dict)
    limits: Dict[str, float] = field(default_factory=dict)
    exhausted_code: Optional[str] = None
    waived_dimensions: Tuple[str, ...] = ()
    emitted_at: float = 0.0

    def dimension(self, code: str) -> Optional[DimensionReport]:
        for dim in self.dimensions:
            if dim.code == code:
                return dim
        return None

    @property
    def provider_cost(self) -> Optional[float]:
        """The provider-side cost, or ``None`` when it was never measured."""
        dim = self.dimension(DIM_SCORER_TOKENS)
        return None if dim is None else dim.value

    @property
    def any_unavailable(self) -> bool:
        return any(d.provenance == PROVENANCE_USAGE_UNAVAILABLE for d in self.dimensions)

    @property
    def fabricated(self) -> bool:
        """Always False. Kept as a named property so a test can assert the *claim*
        rather than reaching into ``__post_init__``'s implementation."""
        return any(
            d.provenance == PROVENANCE_USAGE_UNAVAILABLE and d.value is not None
            for d in self.dimensions
        )

    @property
    def healthy(self) -> bool:
        return self.exhausted_code is None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "provider_mode": self.provider_mode,
            "counter_source": self.counter_source,
            "healthy": self.healthy,
            "exhausted_code": self.exhausted_code,
            "waived_dimensions": list(self.waived_dimensions),
            "any_unavailable": self.any_unavailable,
            "fabricated": self.fabricated,
            "emitted_at": self.emitted_at,
            "counters": dict(self.counters),
            "limits": dict(self.limits),
            "dimensions": [d.as_dict() for d in self.dimensions],
        }


def provider_cost_provenance(provider_mode: Optional[str]) -> str:
    """Provenance label for provider-side cost under ``provider_mode``.

    Unknown or absent mode ⇒ ``USAGE_UNAVAILABLE``. Note the asymmetry: this
    function is *allowed* to return the label that means "no number", so a mode
    nobody has heard of degrades into honesty rather than into a figure.
    """
    return _PROVIDER_MODE_PROVENANCE.get(str(provider_mode or "").strip().lower(),
                                         PROVENANCE_USAGE_UNAVAILABLE)


def _counter_source(governor: Any) -> str:
    """Which surface the counters came from — stated, never inferred."""
    if governor is None:
        return COUNTER_SOURCE_UNAVAILABLE
    store = getattr(governor, "store", None)
    if store is not None and callable(getattr(store, "budget_snapshot", None)):
        return COUNTER_SOURCE_STORE
    return COUNTER_SOURCE_LEDGER


def build_budget_report(
    *,
    provider_mode: Optional[str],
    session_id: str,
    governor: Any = None,
    snapshot: Any = None,
    clock: Any = time.time,
) -> BudgetReport:
    """Build a :class:`BudgetReport` for one session.

    ``snapshot`` may be a :class:`~supervisor.budget.BudgetSnapshot` supplied by
    the caller (the usual path, since the governor already produced one); when it
    is absent and a ``governor`` is given, one is taken from the governor. Both
    absent ⇒ the counters are reported as ``COUNTER_SOURCE_UNAVAILABLE`` with the
    cost dimensions still labelled — because "we have no snapshot" is a fact
    worth reporting and NOT a licence to print zeros.
    """
    source = _counter_source(governor)
    if snapshot is None and governor is not None:
        reader = getattr(governor, "snapshot", None)
        if callable(reader):
            snapshot = reader(session_id)

    counters: Dict[str, float] = {}
    limits: Dict[str, float] = {}
    exhausted: Optional[str] = None
    waived: Tuple[str, ...] = ()
    model_units: Optional[float] = None
    #: Whether a counter source actually ANSWERED. "We were given no snapshot" and
    #: "the snapshot said zero" are different facts, and only the second one
    #: licenses printing a plane-cost number. Without this flag the plane
    #: dimension would read ``0.0`` for a session nobody ever measured — the exact
    #: fabrication this module exists to forbid, arriving through the back door
    #: of ``dict.get(..., 0.0)``.
    have_counters = False

    if snapshot is not None:
        have_counters = True
        model_units = float(getattr(snapshot, "model_units_used", 0.0) or 0.0)
        counters = {
            "assessments_used": float(getattr(snapshot, "assessments_used", 0) or 0),
            "directives_used": float(getattr(snapshot, "directives_used", 0) or 0),
            "rate_used": float(getattr(snapshot, "rate_used", 0) or 0),
            "model_units_used": model_units,
            "cost_units_used": float(getattr(snapshot, "cost_units_used", 0.0) or 0.0),
        }
        limits_obj = getattr(snapshot, "limits", None)
        if limits_obj is not None:
            limits = {
                name: float(getattr(limits_obj, name))
                for name in dir(limits_obj)
                if not name.startswith("_") and isinstance(getattr(limits_obj, name), (int, float))
            }
        exhausted = getattr(snapshot, "exhausted_code", None)
        waived = tuple(getattr(snapshot, "waived_dimensions", ()) or ())

    provenance = provider_cost_provenance(provider_mode)

    # -- provider-side dimension ---------------------------------------------
    if provenance == PROVENANCE_USAGE_UNAVAILABLE:
        provider_value: Optional[float] = None
        provider_note = (
            "no persisted column carries provider token usage, so no number is "
            "reported; this is the dimension budget.WAIVED_DIMENSIONS excludes"
        )
    elif provenance == PROVENANCE_REPLAY_COST:
        provider_value = 0.0
        provider_note = "replayed run: no provider was called, so provider cost is exactly 0"
    else:  # MOCK_COST
        provider_value = model_units
        provider_note = (
            "synthetic units from the mock provider: the measurement exists, the "
            "quantity it measures does not"
        )

    dimensions = (
        DimensionReport(
            code=DIM_SCORER_TOKENS,
            provenance=provenance,
            value=provider_value,
            note=provider_note,
        ),
        DimensionReport(
            code=DIM_DIRECTIVE_COST_UNITS,
            provenance=PROVENANCE_MOCK_COST if have_counters else PROVENANCE_USAGE_UNAVAILABLE,
            value=float(counters.get("cost_units_used", 0.0)) if have_counters else None,
            note=(
                "notional policy-weighted units derived from persisted directives "
                "through jcc_core.policy.COST_UNITS_PER_ACTION -- weights, not currency"
                if have_counters
                else "no counter source answered for this session, so the plane cost "
                     "is NOT reported; a 0.0 here would be indistinguishable from a "
                     "measured zero"
            ),
        ),
    )

    return BudgetReport(
        session_id=session_id,
        provider_mode=str(provider_mode or "unknown"),
        counter_source=source,
        dimensions=dimensions,
        counters=counters,
        limits=limits,
        exhausted_code=exhausted,
        waived_dimensions=waived,
        emitted_at=float(clock()),
    )


def describe() -> Dict[str, Any]:
    """Pinned description of the reporting vocabulary (docs / UI / release gate)."""
    return {
        "provenance_values": list(COST_PROVENANCE_VALUES),
        "unavailable_implies_no_value": True,
        "replay_value_is_exactly_zero": True,
        "fabrication_is_unrepresentable": True,
        "dimensions": [DIM_SCORER_TOKENS, DIM_DIRECTIVE_COST_UNITS],
        "provider_mode_provenance": dict(_PROVIDER_MODE_PROVENANCE),
        "counter_sources": [
            COUNTER_SOURCE_STORE,
            COUNTER_SOURCE_LEDGER,
            COUNTER_SOURCE_UNAVAILABLE,
        ],
    }
