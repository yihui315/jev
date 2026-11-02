"""v1.1 §16 falsifiers for the safe-actuation ladder.

Each test is named for the property that must NOT be reachable. The load-bearing
ones are:

* ``test_restrict_is_a_minimum_over_every_pair`` — combines ceilings exhaustively
  (4x4) and asserts the result is never wider than either input. This is what
  makes "add a constraint" safe without auditing every caller.
* ``test_parse_has_no_fallback_that_raises_capability`` — the parser's refusal set
  is the whole safety story of a configuration surface: a silent downgrade and a
  silent *upgrade* are both defects, and only one of them is obvious.
* ``test_the_shipped_configuration_names_no_level`` — proves the deployment
  default (L0) is reached without editing the A8-pinned ``config/jcc.yaml``.
"""
from __future__ import annotations

import pytest

from jcc_core.config import load_config
from jcc_core.schemas import DirectiveAction
from supervisor import actuation_levels as AL
from supervisor.actuation_levels import ActuationLevel as L


# --------------------------------------------------------------------------- #
# The default is OFF, and it is not a choice
# --------------------------------------------------------------------------- #
def test_the_default_rung_is_l0():
    assert AL.DEFAULT_ACTUATION_LEVEL is L.L0
    assert AL.LEVEL_CAPABILITIES[AL.DEFAULT_ACTUATION_LEVEL] == frozenset()


def test_the_shipped_configuration_names_no_level():
    """L0 is reached because nothing was declared — not because it was declared.

    Reads the REAL ``config/jcc.yaml``. If this test ever fails because the file
    grew an ``actuation.level`` key, that is a signal in itself: ``jcc.yaml`` is
    one of the twelve A8-pinned production artifacts, so adding the key is an
    A8 re-baseline event and must be deliberate.
    """
    cfg = load_config()
    declared = (cfg.raw.get("actuation") or {}).get("level")
    assert declared is None, (
        "config/jcc.yaml now declares an actuation level; that file is A8-pinned "
        "and changing it is a re-baseline, not a config tweak"
    )
    assert AL.resolve_configured_level(cfg, {}) is L.L0


def test_resolve_uses_env_then_config_then_l0():
    class Cfg:
        raw = {"actuation": {"level": "L2"}}

    assert AL.resolve_configured_level(None, {}) is L.L0
    assert AL.resolve_configured_level(Cfg(), {}) is L.L2
    assert AL.resolve_configured_level(Cfg(), {AL.ACTUATION_LEVEL_ENV: "L1"}) is L.L1
    assert AL.resolve_configured_level(Cfg(), {AL.ACTUATION_LEVEL_ENV: "  "}) is L.L2


def test_resolve_raises_on_an_invalid_declaration_rather_than_defaulting():
    class Cfg:
        raw = {"actuation": {"level": "L4"}}

    with pytest.raises(ValueError, match="unknown actuation level"):
        AL.resolve_configured_level(Cfg(), {})
    with pytest.raises(ValueError):
        AL.resolve_configured_level(Cfg(), {AL.ACTUATION_LEVEL_ENV: "yes-please"})


# --------------------------------------------------------------------------- #
# Parsing: refusal, never a fallback
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "value",
    ["L4", "l5", "", "  ", "OFF", 4, -1, 99, None, True, False, 1.0, ["L1"], {"level": "L1"}],
)
def test_parse_rejects_everything_it_does_not_recognise(value):
    with pytest.raises(ValueError):
        AL.parse_actuation_level(value)


@pytest.mark.parametrize(
    "value,expected",
    [("L0", L.L0), ("l1", L.L1), (" l2 ", L.L2), ("L3", L.L3), (0, L.L0), (1, L.L1), (2, L.L2), (3, L.L3),
     (L.L2, L.L2)],
)
def test_parse_accepts_the_closed_set(value, expected):
    assert AL.parse_actuation_level(value) is expected


def test_parse_has_no_fallback_that_raises_capability():
    """Whatever the parser accepts, the result is never WIDER than the input implies.

    Guards the specific mistake of writing ``except ValueError: return L0`` and
    then later "fixing" it to return something else: for every accepted input the
    rank must match the input's own rank, so there is no input for which parsing
    silently grants more than was written down.
    """
    for rank, token in enumerate(["L0", "L1", "L2", "L3"]):
        assert AL.level_rank(AL.parse_actuation_level(token)) == rank
        assert AL.level_rank(AL.parse_actuation_level(rank)) == rank


# --------------------------------------------------------------------------- #
# restrict is a MINIMUM, exhaustively
# --------------------------------------------------------------------------- #
def test_restrict_is_a_minimum_over_every_pair():
    for a in AL.ACTUATION_LEVEL_ORDER:
        for b in AL.ACTUATION_LEVEL_ORDER:
            got = AL.restrict(a, b)
            assert AL.level_rank(got) <= AL.level_rank(a)
            assert AL.level_rank(got) <= AL.level_rank(b)
            assert AL.capability_of(got) <= AL.capability_of(a)
            assert AL.capability_of(got) <= AL.capability_of(b)
    # and concretely, the direction that matters:
    assert AL.restrict(L.L3, L.L0) is L.L0
    assert AL.restrict(L.L0, L.L3) is L.L0
    assert AL.restrict(L.L2, L.L3) is L.L2


def test_restrict_of_nothing_is_the_default_rung():
    assert AL.restrict() is AL.DEFAULT_ACTUATION_LEVEL
    assert AL.ceiling_of([]) is AL.DEFAULT_ACTUATION_LEVEL
    assert AL.ceiling_of([L.L3, L.L1, L.L2]) is L.L1


# --------------------------------------------------------------------------- #
# The classification is complete, or nothing is permitted
# --------------------------------------------------------------------------- #
def test_the_action_classification_partitions_the_whole_action_space():
    """The falsifier for the import-time assertion: adding a ``DirectiveAction``
    without classifying it must fail HERE (and at import) rather than silently
    landing in neither set — which would quietly remove it from L2 and L3."""
    every = frozenset(DirectiveAction)
    assert AL.REVERSIBLE_ACTIONS | AL.IRREVERSIBLE_ACTIONS == every
    assert not (AL.REVERSIBLE_ACTIONS & AL.IRREVERSIBLE_ACTIONS)
    assert len(every) == len(AL.REVERSIBLE_ACTIONS) + len(AL.IRREVERSIBLE_ACTIONS)


def test_l0_and_l1_permit_nothing_and_differ_only_on_whether_the_gate_runs():
    assert AL.capability_of(L.L0) == frozenset()
    assert AL.capability_of(L.L1) == frozenset()
    assert AL.gate_runs(L.L0) is False
    assert AL.gate_runs(L.L1) is True


def test_l2_permits_exactly_the_reversible_set():
    assert AL.capability_of(L.L2) == AL.REVERSIBLE_ACTIONS
    for action in AL.IRREVERSIBLE_ACTIONS:
        assert AL.permits(L.L2, action) is False


def test_l3_permits_every_action():
    assert AL.capability_of(L.L3) == frozenset(DirectiveAction)
    for action in DirectiveAction:
        assert AL.permits(L.L3, action) is True


@pytest.mark.parametrize("level", list(AL.ACTUATION_LEVEL_ORDER))
def test_capability_is_monotonic_along_the_ladder(level):
    """A higher rung never permits less. Without this, "raise the level" could
    silently narrow capability and the ladder would not be a ladder."""
    idx = AL.ACTUATION_LEVEL_ORDER.index(level)
    for higher in AL.ACTUATION_LEVEL_ORDER[idx:]:
        assert AL.capability_of(level) <= AL.capability_of(higher)


# --------------------------------------------------------------------------- #
# deny_reason distinguishes the three refusals
# --------------------------------------------------------------------------- #
def test_deny_reason_distinguishes_off_from_dry_run_from_not_permitted():
    assert AL.deny_reason(L.L0, DirectiveAction.CONTINUE) == AL.REASON_LEVEL_OFF
    assert AL.deny_reason(L.L1, DirectiveAction.CONTINUE) == AL.REASON_LEVEL_DRY_RUN
    assert AL.deny_reason(L.L2, DirectiveAction.FINISH) == AL.REASON_ACTION_NOT_PERMITTED
    assert AL.deny_reason(L.L2, DirectiveAction.STEER) is None
    assert AL.deny_reason(L.L3, DirectiveAction.ESCALATE) is None
    assert len({AL.REASON_LEVEL_OFF, AL.REASON_LEVEL_DRY_RUN, AL.REASON_ACTION_NOT_PERMITTED}) == 3


# --------------------------------------------------------------------------- #
# describe() is emitted from the enforcement
# --------------------------------------------------------------------------- #
def test_describe_is_emitted_from_the_enforcement_tables():
    described = AL.describe()
    assert len(described) == len(AL.ACTUATION_LEVEL_ORDER)
    for row in described:
        level = AL.parse_actuation_level(row["level"])
        assert row["rank"] == AL.level_rank(level)
        assert row["gate_runs"] == AL.gate_runs(level)
        assert row["permits"] == sorted(a.value for a in AL.capability_of(level))
        # rank order matches tuple order, so the description cannot claim an
        # ordering the enforcement does not use
        assert row["rank"] == described.index(row)
