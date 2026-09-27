"""Safe actuation ladder L0–L3 (v1.1 §16).

WHY A LADDER AND NOT A BOOLEAN
------------------------------
v1.0 shipped exactly one actuation switch: ``actuation.enabled`` (``config.py:58``)
and an :class:`~jcc_core.actuator.Actuator` whose ``can_act`` returns
``False`` for everything. A single boolean cannot express "we may dry-run the
verification gate but not apply anything", so the only two states available are
*off* and *everything* — the second of which is precisely the state a control
plane must never reach by accident. The ladder replaces that cliff with four
named rungs, and every rung below the top removes capability rather than
granting it.

The four rungs, stated as *capability*, not as intent::

    L0  OFF          nothing is ever applied; the effect boundary is not reached
    L1  DRY_RUN      the FULL 9-check gate runs and its verdict is recorded,
                     and the effect is still suppressed by the level itself
    L2  REVERSIBLE   only explicitly-reversible actions may be applied
    L3  FULL         every action may be applied

``L1`` is the load-bearing rung and the reason this is not just an integer. Its
whole purpose is that the gate can be *exercised against live traffic* and
proven to work **while the system still cannot act**. A ladder whose second rung
already grants an effect would make "we tested the gate in production" and "we
enabled production effects" the same act, which is what the live-shadow
observation window (v1.1 §19) exists to separate.

THE THREE RULES THAT MAKE THIS FAIL-CLOSED
------------------------------------------
1. **``L0`` is the default, and the default is not a choice.**
   ``parse_actuation_level`` raises on anything it does not recognise. It never
   returns a *higher* rung than the input implies, and it has no silent fallback
   — an unreadable or misspelled level is an error, never a permission. The
   shipped configuration names no level at all, so the effective level in a
   default deployment is ``L0``.

2. **A rung is a CEILING, never a grant.** ``restrict(a, b)`` returns the
   *lower* rung. Combining constraints can only ever *remove* capability, so a
   new constraint cannot accidentally widen an existing one. The live-shadow
   gate (§19) plugs in here as a second ceiling.

3. **``L2`` is classified, not guessed.** ``REVERSIBLE_ACTIONS`` /
   ``IRREVERSIBLE_ACTIONS`` partition the whole action space. The partition is
   asserted complete and disjoint at import time, so adding a ``DirectiveAction``
   member without classifying it makes this module fail loudly instead of
   silently defaulting the new action into (or out of) ``L2``.

Scope note (ADR-0008 D-3/D-41): the effect boundary lives in the SUPERVISOR plane
(``Sentinel._dispatch``), so the ladder is defined here and not in
``jcc_core.actuator`` — that module's engine port is permanently inert and is a
pinned A8 artifact besides.
"""
from __future__ import annotations

import os
from enum import Enum
from typing import Dict, FrozenSet, Iterable, Optional, Tuple

from jcc_core.schemas import DirectiveAction

# -- the ladder --------------------------------------------------------------- #
class ActuationLevel(str, Enum):
    """Capability rung. Ordered: ``L0 < L1 < L2 < L3``."""

    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


#: Ascending capability. The index in this tuple IS the rank, and
#: ``restrict`` is defined as a minimum over it — so the ordering cannot be
#: redefined in one place and assumed in another.
ACTUATION_LEVEL_ORDER: Tuple[ActuationLevel, ...] = (
    ActuationLevel.L0,
    ActuationLevel.L1,
    ActuationLevel.L2,
    ActuationLevel.L3,
)

#: The rung a deployment gets when nobody said anything. Most restrictive.
DEFAULT_ACTUATION_LEVEL = ActuationLevel.L0

#: Environment override, mirroring the ``JCC_PROVIDER`` / ``JCC_DB_PATH``
#: convention in ``config.py``. Consulted BEFORE ``config.raw`` so a test or an
#: operator can pin a rung without editing the pinned ``config/jcc.yaml``.
ACTUATION_LEVEL_ENV = "JCC_ACTUATION_LEVEL"

#: Where the rung may be declared inside ``config/jcc.yaml``. Read through
#: ``Config.raw`` so that adding it is a config change, not a SCHEMA change —
#: ``config/jcc.yaml`` is one of the twelve A8-pinned production artifacts and
#: must not be edited to introduce this feature.
CONFIG_PATH: Tuple[str, ...] = ("actuation", "level")


# -- action classification ---------------------------------------------------- #
#: Actions a rung-L2 deployment may apply: each one can be countermanded by a
#: later directive within the same session, and none of them notifies an
#: outside party or ends the session.
REVERSIBLE_ACTIONS: FrozenSet[DirectiveAction] = frozenset(
    {
        DirectiveAction.CONTINUE,   # let the agent proceed
        DirectiveAction.VERIFY,     # ask for evidence and wait
        DirectiveAction.STEER,      # nudge the agent's next step
        DirectiveAction.RETRY,      # re-run a step
        DirectiveAction.PAUSE,      # withhold progress
    }
)

#: Actions a rung-L2 deployment must NOT apply: they are terminal or they leave
#: the control plane (an escalation reaches a human and cannot be un-sent).
IRREVERSIBLE_ACTIONS: FrozenSet[DirectiveAction] = frozenset(
    {
        DirectiveAction.FINISH,     # ends the session
        DirectiveAction.ESCALATE,   # notifies a human; not retractable
    }
)

#: Import-time completeness proof. A new ``DirectiveAction`` member that nobody
#: classified lands here and raises — which is the fail-CLOSED direction (the
#: action is in neither set, so no rung permits it) but would be a *silent* one,
#: and a silent fail-closed is indistinguishable from a forgotten action. So the
#: assertion converts it into a loud, immediate error.
_ALL_ACTIONS: FrozenSet[DirectiveAction] = frozenset(DirectiveAction)
assert REVERSIBLE_ACTIONS | IRREVERSIBLE_ACTIONS == _ALL_ACTIONS, (
    "the action classification is incomplete: "
    f"unclassified = {sorted(a.value for a in _ALL_ACTIONS - (REVERSIBLE_ACTIONS | IRREVERSIBLE_ACTIONS))}"
)
assert not (REVERSIBLE_ACTIONS & IRREVERSIBLE_ACTIONS), (
    "an action is in both reversibility classes: "
    f"{sorted(a.value for a in REVERSIBLE_ACTIONS & IRREVERSIBLE_ACTIONS)}"
)

#: Per-rung capability. ``L0`` and ``L1`` are both EMPTY on purpose — the
#: difference between them is not *what* they may apply but *whether the gate
#: runs at all* (see ``gate_runs``).
LEVEL_CAPABILITIES: Dict[ActuationLevel, FrozenSet[DirectiveAction]] = {
    ActuationLevel.L0: frozenset(),
    ActuationLevel.L1: frozenset(),
    ActuationLevel.L2: REVERSIBLE_ACTIONS,
    ActuationLevel.L3: _ALL_ACTIONS,
}


# -- reason vocabulary (frozen strings) --------------------------------------- #
REASON_LEVEL_OFF = "actuation_level_off"
REASON_LEVEL_DRY_RUN = "actuation_level_dry_run"
REASON_ACTION_NOT_PERMITTED = "actuation_action_not_permitted_at_level"


# -- parsing ------------------------------------------------------------------ #
def parse_actuation_level(value: object) -> ActuationLevel:
    """Coerce ``value`` to an :class:`ActuationLevel`, or RAISE.

    Accepts the enum, the string ``"L0".."L3"`` (case-insensitive, surrounding
    whitespace ignored), and the integers ``0..3``.

    Refuses everything else. There is deliberately **no** ``default=`` argument
    and no "unknown ⇒ L0" branch: an unknown level is a configuration defect and
    a defect must be visible. A silent downgrade would be the mirror image of the
    real hazard — it would look safe here while hiding that the operator's
    intended configuration was never applied, and the next reader of a
    "level=L0" log line would conclude the *choice* was made deliberately.
    """
    if isinstance(value, ActuationLevel):
        return value
    if isinstance(value, bool):  # bool is an int subclass; never a level
        raise ValueError(f"actuation level must not be a bool, got {value!r}")
    if isinstance(value, int):
        if 0 <= value < len(ACTUATION_LEVEL_ORDER):
            return ACTUATION_LEVEL_ORDER[value]
        raise ValueError(f"actuation level int must be 0..{len(ACTUATION_LEVEL_ORDER) - 1}, got {value!r}")
    if isinstance(value, str):
        token = value.strip().upper()
        for level in ACTUATION_LEVEL_ORDER:
            if token == level.value:
                return level
        raise ValueError(
            f"unknown actuation level {value!r}; the closed set is "
            f"{[lvl.value for lvl in ACTUATION_LEVEL_ORDER]}"
        )
    raise ValueError(
        f"actuation level must be ActuationLevel, str or int, got {type(value).__name__}"
    )


def level_rank(level: ActuationLevel) -> int:
    """Ascending capability rank; ``L0`` is 0."""
    return ACTUATION_LEVEL_ORDER.index(level)


def restrict(*levels: ActuationLevel) -> ActuationLevel:
    """The MOST RESTRICTIVE of ``levels`` — a minimum, never a maximum.

    Any number of ceilings can be combined, and the result can only ever be less
    capable than each input. This is what makes it safe to add a constraint
    (``restrict(configured, gate_ceiling)``) without auditing every existing
    caller: there is no argument order and no argument count for which a wider
    rung comes out.
    """
    if not levels:
        return DEFAULT_ACTUATION_LEVEL
    return min(levels, key=level_rank)


def ceiling_of(levels: Iterable[ActuationLevel]) -> ActuationLevel:
    """``restrict`` over an iterable; empty iterable ⇒ ``DEFAULT_ACTUATION_LEVEL``."""
    return restrict(*tuple(levels))


# -- capability queries ------------------------------------------------------- #
def capability_of(level: ActuationLevel) -> FrozenSet[DirectiveAction]:
    """The action set ``level`` may apply. Read-only copy of the pinned table."""
    return LEVEL_CAPABILITIES[level]


def permits(level: ActuationLevel, action: DirectiveAction) -> bool:
    """Whether ``level`` may APPLY ``action``.

    Plain membership: ``DirectiveAction`` is a ``str`` enum, so a bare
    ``"ESCALATE"`` and ``DirectiveAction.ESCALATE`` hash and compare equal and
    the one test covers both spellings. No coercion branch exists, because a
    coercion branch is where an unrecognised value would get a second chance to
    be read as something permissive.
    """
    return action in LEVEL_CAPABILITIES[level]


def gate_runs(level: ActuationLevel) -> bool:
    """Whether the effect boundary is reached at all at ``level``.

    ``L0`` short-circuits: the directive is refused *before* the 9-check gate is
    evaluated, so an L0 deployment cannot even be probed through the gate. Every
    rung above ``L0`` runs the gate in full and records its verdict — including
    ``L1``, whose only purpose is to run it.
    """
    return level is not ActuationLevel.L0


def deny_reason(level: ActuationLevel, action: DirectiveAction) -> Optional[str]:
    """Why ``level`` refuses to APPLY ``action`` — or ``None`` when it permits it.

    The three reasons are distinguished on purpose. "The system is off" and
    "this action is reversible-classified and you are at L2" are different
    facts, and collapsing them into one ``DENIED`` would hide the one that an
    operator can actually fix ("raise the rung") inside the one they cannot.
    """
    if level is ActuationLevel.L0:
        return REASON_LEVEL_OFF
    if not LEVEL_CAPABILITIES[level]:
        return REASON_LEVEL_DRY_RUN
    if not permits(level, action):
        return REASON_ACTION_NOT_PERMITTED
    return None


# -- configuration ------------------------------------------------------------ #
def resolve_configured_level(config: object = None, env: Optional[dict] = None) -> ActuationLevel:
    """The CONFIGURED rung: env override, else ``config.raw``, else ``L0``.

    Precedence mirrors ``config.load_config`` (env beats file). An absent
    declaration is ``L0`` — the shipped default — and a *present but invalid*
    declaration RAISES via :func:`parse_actuation_level` rather than being
    ignored, so a typo cannot silently leave the system at a rung the operator
    did not pick.

    This reads ``Config.raw`` (the whole parsed YAML mapping) instead of a
    dedicated ``ActuationConfig`` field because ``config/jcc.yaml`` is an
    A8-pinned production artifact: introducing this feature must not require
    editing it. Reading through ``raw`` means the key works the moment an
    operator adds it, and the default deployment is byte-identical.
    """
    environ = os.environ if env is None else env
    declared = None
    override = environ.get(ACTUATION_LEVEL_ENV)
    if override is not None and str(override).strip() != "":
        return parse_actuation_level(override)
    if config is not None:
        node: object = getattr(config, "raw", None)
        for key in CONFIG_PATH:
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(key)
        declared = node
    if declared is None:
        return DEFAULT_ACTUATION_LEVEL
    return parse_actuation_level(declared)


def describe() -> Tuple[Dict[str, object], ...]:
    """Pinned, machine-readable description of the ladder (for docs and the UI).

    Emitted from the same tables the enforcement reads, so a UI cannot describe
    a ladder that differs from the one in force.
    """
    return tuple(
        {
            "level": level.value,
            "rank": level_rank(level),
            "gate_runs": gate_runs(level),
            "permits": sorted(a.value for a in capability_of(level)),
            "capability_count": len(capability_of(level)),
        }
        for level in ACTUATION_LEVEL_ORDER
    )
