"""v1.1 §19 falsifiers for the live-shadow observation gate.

The headline test is ``test_the_shipped_configuration_is_pending_and_disabled``:
on a tape that does not meet the criteria, with the shipped defaults, the gate
says ``PENDING_LIVE_SHADOW_GATE`` and ``actuation_enabled=False`` — the lawful,
safe completion the v1.1 plan names explicitly.

The second is ``test_satisfied_alone_does_not_enable_actuation``: meeting the
criteria is NOT the same as wanting effects, so even a satisfied gate leaves the
system off until the ceiling is raised in the criteria AND the operator's own
flag is set. Three separate true statements are required.

The third is ``test_an_unreadable_tape_is_pending_and_not_no_observations``: the
three-state rule. "I could not look" must never be recorded as "there is nothing
there", and on a SAFETY gate the collapse is exactly how a green light appears.
"""
from __future__ import annotations

import inspect
import sqlite3

import pytest

from supervisor import live_shadow_gate as LG
from supervisor import shadow_store
from supervisor.actuation_levels import ActuationLevel as L


# --------------------------------------------------------------------------- #
# Fixtures: a real tape, never a stub
# --------------------------------------------------------------------------- #
def _tape(tmp_path, n=None, *, acted=False, step=3600.0, t0=0.0):
    path = tmp_path / "shadow.db"
    conn = sqlite3.connect(str(path))
    shadow_store.ensure_schema(conn)
    for i in range(n or 0):
        shadow_store.append_shadow(
            conn,
            record={
                "decision_id": f"sd_{i}",
                "session_id": "s-1",
                "event_id": f"e{i}",
                "observed_at": t0 + i * step,
                "action": "STEER",
                "acted": acted,
                "signed": True,
                "signature": "sha256=" + "a" * 64,
            },
        )
    return conn


def _criteria(**over):
    kw = dict(min_decisions=50, min_wall_clock_s=86_400.0, granted_ceiling=L.L0)
    kw.update(over)
    return LG.LiveShadowGateCriteria(**kw)


# --------------------------------------------------------------------------- #
# The shipped default: PENDING and OFF
# --------------------------------------------------------------------------- #
def test_the_shipped_configuration_is_pending_and_disabled(tmp_path):
    """No level, no ceiling, no operator flag, an empty tape."""
    r = LG.evaluate_live_shadow_gate(_tape(tmp_path), clock=lambda: 1.0)
    assert r.status == LG.GATE_PENDING
    assert r.actuation_enabled is False
    assert r.effective_level is L.L0
    assert LG.DEFECT_NO_OBSERVATIONS in r.defects
    assert r.satisfied is False and r.pending is True


def test_the_pending_status_string_is_the_one_the_plan_names():
    assert LG.GATE_PENDING == "PENDING_LIVE_SHADOW_GATE"
    assert LG.describe()["pending_status"] == "PENDING_LIVE_SHADOW_GATE"
    assert LG.describe()["pending_forces_actuation_enabled_false"] is True


@pytest.mark.parametrize(
    "n,acted,step,expected",
    [
        (0, False, 3600.0, LG.DEFECT_NO_OBSERVATIONS),
        (10, False, 3600.0, LG.DEFECT_INSUFFICIENT_OBSERVATIONS),
        (60, False, 60.0, LG.DEFECT_INSUFFICIENT_WINDOW),
        (60, True, 3600.0, LG.DEFECT_ACTUATION_OBSERVED),
    ],
)
def test_each_defect_forces_pending(tmp_path, n, acted, step, expected):
    r = LG.evaluate_live_shadow_gate(_tape(tmp_path, n, acted=acted, step=step), clock=lambda: 1.0)
    assert r.status == LG.GATE_PENDING
    assert r.actuation_enabled is False
    assert expected in r.defects


def test_the_gate_reads_the_tape_so_the_counts_are_real(tmp_path):
    """Non-vacuity: the observed counts come from the rows, not from nothing."""
    r = LG.evaluate_live_shadow_gate(_tape(tmp_path, 17, step=3600.0), clock=lambda: 1.0)
    assert r.observed_decisions == 17
    assert r.observed_span_s == pytest.approx(16 * 3600.0)
    assert r.chain_intact is True


def test_a_broken_chain_forces_pending_even_when_every_threshold_is_met(tmp_path):
    conn = _tape(tmp_path, 60, step=3600.0)
    conn.execute("UPDATE shadow_decisions SET action='ESCALATE' WHERE seq=5")
    conn.commit()
    r = LG.evaluate_live_shadow_gate(conn, clock=lambda: 1.0)
    assert r.status == LG.GATE_PENDING
    assert r.chain_intact is False
    assert LG.DEFECT_CHAIN_BROKEN in r.defects
    assert r.observed_decisions == 60 and r.observed_span_s >= 86_400.0


# --------------------------------------------------------------------------- #
# Three-state: unreadable is not empty
# --------------------------------------------------------------------------- #
def test_an_unreadable_tape_is_pending_and_not_no_observations():
    r = LG.evaluate_live_shadow_gate(object(), clock=lambda: 1.0)
    assert r.status == LG.GATE_PENDING
    assert r.actuation_enabled is False
    assert r.defects == (LG.DEFECT_STORAGE_UNAVAILABLE,)
    assert LG.DEFECT_NO_OBSERVATIONS not in r.defects, (
        "'I could not look' was recorded as 'there is nothing there'"
    )
    assert LG.DEFECT_TAPE_ABSENT not in r.defects, (
        "an unreadable database is not the same finding as an absent tape"
    )
    assert r.chain_intact is None, "an unknown chain state is not a broken one"
    assert r.read_failure and "AttributeError" in r.read_failure


def test_a_database_without_the_tape_is_reported_as_absent_not_unreadable(tmp_path):
    """The real runtime database has no ``shadow_decisions`` table. Measured here
    on an equivalent database: the finding is DETERMINATE (the tape was never
    created) and must not be reported as a storage failure, which would send an
    operator to debug a database that is working perfectly."""
    conn = sqlite3.connect(str(tmp_path / "bare.db"))
    conn.execute("CREATE TABLE sessions (session_id TEXT)")   # a DB, but no tape
    conn.commit()

    r = LG.evaluate_live_shadow_gate(conn, clock=lambda: 1.0)
    assert r.status == LG.GATE_PENDING
    assert r.actuation_enabled is False
    assert LG.DEFECT_TAPE_ABSENT in r.defects
    assert LG.DEFECT_NO_OBSERVATIONS in r.defects
    assert LG.DEFECT_STORAGE_UNAVAILABLE not in r.defects
    assert r.read_failure is None, "nothing failed to read; there is simply no tape"
    assert r.chain_intact is None
    assert r.observed_decisions == 0


def test_absent_empty_and_unreadable_are_three_distinct_findings(tmp_path):
    """The three-state discipline, asserted as a relationship rather than one
    case at a time: the three situations must not collapse into one another."""
    bare = sqlite3.connect(str(tmp_path / "bare.db"))
    bare.execute("CREATE TABLE sessions (session_id TEXT)")
    bare.commit()

    absent = LG.evaluate_live_shadow_gate(bare, clock=lambda: 1.0)
    empty = LG.evaluate_live_shadow_gate(_tape(tmp_path), clock=lambda: 1.0)
    unreadable = LG.evaluate_live_shadow_gate(object(), clock=lambda: 1.0)

    signatures = {
        tuple(sorted(absent.defects)),
        tuple(sorted(empty.defects)),
        tuple(sorted(unreadable.defects)),
    }
    assert len(signatures) == 3, f"the three findings collapsed: {signatures}"
    # and all three are PENDING, so none of them can be mistaken for a green light
    assert all(r.status == LG.GATE_PENDING for r in (absent, empty, unreadable))


def test_a_read_failure_does_not_raise():
    """A gate that raises is a gate a caller can catch and ignore."""
    r = LG.evaluate_live_shadow_gate(None, clock=lambda: 1.0)
    assert r.status == LG.GATE_PENDING


# --------------------------------------------------------------------------- #
# Pending forces L0 whatever was configured
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("configured", list(L))
@pytest.mark.parametrize("mode", ["empty", "acted", "broken", "unreadable"])
def test_pending_always_forces_the_effective_level_to_l0(tmp_path, configured, mode):
    if mode == "empty":
        conn = _tape(tmp_path, 0)
    elif mode == "acted":
        conn = _tape(tmp_path, 60, acted=True, step=3600.0)
    elif mode == "broken":
        conn = _tape(tmp_path, 60, step=3600.0)
        conn.execute("UPDATE shadow_decisions SET action='ESCALATE' WHERE seq=3")
        conn.commit()
    else:
        conn = object()
    r = LG.evaluate_live_shadow_gate(
        conn, configured_level=configured, configured_enabled=True, clock=lambda: 1.0
    )
    assert r.status == LG.GATE_PENDING
    assert r.effective_level is L.L0
    assert r.actuation_enabled is False


# --------------------------------------------------------------------------- #
# Satisfied is still not enabled
# --------------------------------------------------------------------------- #
def test_satisfied_alone_does_not_enable_actuation(tmp_path):
    """Criteria met, operator even claimed enabled — and it is STILL off, because
    the gate's own ceiling is L0 and the ceiling is a separate act."""
    conn = _tape(tmp_path, 60, step=3600.0)
    r = LG.evaluate_live_shadow_gate(
        conn, configured_level=L.L3, configured_enabled=True, clock=lambda: 1.0
    )
    assert r.status == LG.GATE_SATISFIED
    assert r.granted_ceiling is L.L0
    assert r.effective_level is L.L0
    assert r.actuation_enabled is False
    assert r.defects == ()


def test_enabling_requires_all_three_conditions(tmp_path):
    conn = _tape(tmp_path, 60, step=3600.0)
    raised = _criteria(granted_ceiling=L.L2)

    # all three -> on, and the level is the restricted one
    on = LG.evaluate_live_shadow_gate(
        conn, criteria=raised, configured_level=L.L3, configured_enabled=True, clock=lambda: 1.0
    )
    assert on.status == LG.GATE_SATISFIED
    assert on.actuation_enabled is True
    assert on.effective_level is L.L2

    # remove the operator flag -> off
    no_flag = LG.evaluate_live_shadow_gate(
        conn, criteria=raised, configured_level=L.L3, configured_enabled=False, clock=lambda: 1.0
    )
    assert no_flag.status == LG.GATE_SATISFIED
    assert no_flag.actuation_enabled is False

    # remove the raised ceiling -> off
    no_ceiling = LG.evaluate_live_shadow_gate(
        conn, criteria=_criteria(), configured_level=L.L3, configured_enabled=True, clock=lambda: 1.0
    )
    assert no_ceiling.actuation_enabled is False

    # remove the evidence -> off and PENDING
    sub = tmp_path / "sub"
    sub.mkdir()
    empty = LG.evaluate_live_shadow_gate(
        _tape(sub), criteria=raised, configured_level=L.L3,
        configured_enabled=True, clock=lambda: 1.0,
    )
    assert empty.status == LG.GATE_PENDING
    assert empty.actuation_enabled is False


def test_the_gate_ceiling_can_only_restrict_the_configured_level(tmp_path):
    conn = _tape(tmp_path, 60, step=3600.0)
    for configured in L:
        for ceiling in L:
            r = LG.evaluate_live_shadow_gate(
                conn, criteria=_criteria(granted_ceiling=ceiling),
                configured_level=configured, configured_enabled=True, clock=lambda: 1.0,
            )
            assert LG.AL.level_rank(r.effective_level) <= min(
                LG.AL.level_rank(configured), LG.AL.level_rank(ceiling)
            )


def test_a_satisfied_gate_with_a_zero_ceiling_is_the_shipped_expected_state():
    """Documented explicitly: SATISFIED + OFF is a NORMAL, lawful outcome, not a
    failure to finish. Nothing about reaching the threshold turns effects on."""
    assert LG.describe()["satisfied_alone_does_not_enable_actuation"] is True
    assert LG.LiveShadowGateCriteria().granted_ceiling is L.L0


# --------------------------------------------------------------------------- #
# Evidence cannot be asserted, only observed
# --------------------------------------------------------------------------- #
def test_the_gate_takes_no_observation_arguments():
    """Structural pin: if a caller could pass the counts, the gate would be
    satisfiable by the very thing it is meant to check."""
    params = set(inspect.signature(LG.evaluate_live_shadow_gate).parameters)
    assert params == {"conn", "criteria", "configured_level", "configured_enabled", "clock"}
    for banned in ("observed_decisions", "observed_span_s", "rows", "decisions", "evidence"):
        assert banned not in params


def test_degenerate_criteria_are_refused():
    with pytest.raises(ValueError, match="not a bar"):
        LG.LiveShadowGateCriteria(min_decisions=0)
    with pytest.raises(ValueError, match="not a window"):
        LG.LiveShadowGateCriteria(min_wall_clock_s=0)


def test_a_lower_count_bar_does_not_lower_the_window_bar(tmp_path):
    """The two thresholds are independent: relaxing one must not relax the other."""
    conn = _tape(tmp_path, 60, step=60.0)   # 60 rows, ~1h
    r = LG.evaluate_live_shadow_gate(
        conn, criteria=_criteria(min_decisions=1), clock=lambda: 1.0
    )
    assert r.status == LG.GATE_PENDING
    assert LG.DEFECT_INSUFFICIENT_WINDOW in r.defects
    assert LG.DEFECT_INSUFFICIENT_OBSERVATIONS not in r.defects


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def test_the_result_carries_the_readings_it_was_computed_from(tmp_path):
    r = LG.evaluate_live_shadow_gate(_tape(tmp_path, 60, acted=True, step=3600.0), clock=lambda: 1.0)
    payload = r.as_dict()
    assert payload["status"] == LG.GATE_PENDING
    assert payload["actuation_enabled"] is False
    assert payload["observed_decisions"] == 60
    assert payload["actuated_observations"] == 60
    assert payload["chain_intact"] is True
    assert payload["thresholds"]["min_decisions"] == 50
    assert payload["thresholds"]["granted_ceiling"] == "L0"


def test_describe_pins_the_whole_vocabulary():
    described = LG.describe()
    assert described["statuses"] == [LG.GATE_PENDING, LG.GATE_SATISFIED]
    assert set(described["defect_codes"]) == {
        LG.DEFECT_NO_OBSERVATIONS, LG.DEFECT_INSUFFICIENT_OBSERVATIONS,
        LG.DEFECT_INSUFFICIENT_WINDOW, LG.DEFECT_ACTUATION_OBSERVED,
        LG.DEFECT_CHAIN_BROKEN, LG.DEFECT_TAPE_ABSENT, LG.DEFECT_STORAGE_UNAVAILABLE,
    }
    assert described["default_criteria"]["granted_ceiling"] == "L0"
    assert described["unreadable_tape_is_pending"] is True
