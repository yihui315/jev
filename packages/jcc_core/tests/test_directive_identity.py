"""P4 #82 -- one economic event MUST be one row, and one charge.

The bug, measured (this is a regression test, so the measurement is quoted):
two planes persist a row for the SAME issued directive -- the engine writes
``dir_<session>_<event>`` (``core.py:175``) and the sentinel's charging copy
writes ``shadow_<session>_<event>`` (``supervisor/budget.py::_persist_directive``).
Three scenarios were probed against the real store before any design was chosen:

  S1 shadow ON, unblocked    -> 2 events -> 4 rows -> directives_used read 4
                                 (must read 2), cost_units_used read 12.0
                                 (must be the single-charge value)
  S2 shadow ON, budget blown -> engine CONTINUE for the blocked event, sentinel
                                 re-issues ESCALATE. Counting the engine row
                                 there would LOSE that escalation, so the
                                 collapse must keep the SUBSTITUTED action.
  S3 shadow OFF              -> only engine rows exist. Any rule that counts
                                 "supervisor rows only" reads 0 here and every
                                 ceiling silently goes fail-open.

The third scenario is why the fix is a per-event collapse in the STORE and not
a deletion of either writer: neither "engine rows only" nor "shadow rows only"
is correct on its own.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]  # F:/jev


# --------------------------------------------------------------------------- #
# the id contract both planes must keep
# --------------------------------------------------------------------------- #
def test_the_two_plane_id_prefixes_are_the_identity_basis():
    """Pins the ONLY thing the two rows share that still distinguishes events.

    If a plane renames its id, this file's other tests will either collapse the
    wrong rows or stop collapsing them at all -- which is why the prefixes are
    pinned here rather than treated as an implementation detail.
    """
    from jcc_core import state_store as ss

    assert ss._DIRECTIVE_EVENT_PREFIXES == ("dir_", "shadow_")


def test_event_key_collapses_the_two_planes_and_keeps_events_apart():
    from jcc_core.state_store import directive_event_key

    # same event, two planes -> SAME key
    assert directive_event_key("dir_s-wire_w1", "s-wire") == \
        directive_event_key("shadow_s-wire_w1", "s-wire")
    assert directive_event_key("dir_s-wire_w2", "s-wire") == \
        directive_event_key("shadow_s-wire_w2", "s-wire")
    # different events -> different keys (this is what stops the wire test's two
    # ingests, which share an evidence_hash, from merging into one)
    assert directive_event_key("dir_s-wire_w1", "s-wire") != \
        directive_event_key("dir_s-wire_w2", "s-wire")
    # different sessions are never merged
    assert directive_event_key("dir_s1_w1", "s1") != directive_event_key("dir_s1_w1", "s2")


def test_unrecognised_id_shapes_are_never_merged():
    """Fail-safe direction: a naming change can only OVER-count, never lose a
    directive. An under-count here is the #73 disease wearing a new costume."""
    from jcc_core.state_store import directive_event_key

    a = directive_event_key("wat-1", "s")
    assert a != directive_event_key("wat-2", "s"), "distinct rows must stay distinct"
    assert a != directive_event_key("wat-1", "other"), "sessions never merge"
    assert a == directive_event_key("wat-1", "s"), "the key is stable"
    assert "unrecognised" in a
    # and an unrecognised row never collapses INTO a recognised one
    assert a != directive_event_key("dir_wat-1", "s")


# --------------------------------------------------------------------------- #
# S1 -- the regression the lead filed
# --------------------------------------------------------------------------- #
def _engine_with_shadow_wiring(tmp_path, enabled=True, budget=None):
    from jcc_core.actuator import Actuator
    from jcc_core.config import load_config
    from jcc_core.core import CoreEngine
    from jcc_core.providers import build_provider
    from jcc_core.state_store import StateStore

    from jcc_core.run import _wire_shadow  # local: importing run.py configures logging

    cfg = load_config(str(REPO_ROOT / "config" / "jcc.yaml"))
    cfg.state_store_db_path = str(tmp_path / "jcc.db")
    cfg.raw["p3"]["shadow"]["enabled"] = enabled
    if budget is not None:
        cfg.raw["p3"]["shadow"]["budget"] = budget
    store = StateStore(cfg.state_store_db_path)
    engine = CoreEngine(store, build_provider(cfg), Actuator(enabled=False), cfg)
    _wire_shadow(engine, store, cfg)
    return store, engine


def _ingest_twice(engine, session_id: str) -> None:
    from jcc_core.schemas import AEPEnvelope

    for i in (1, 2):
        engine.ingest(
            AEPEnvelope(event_id=f"w{i}", delivery_id=f"wd{i}", session_id=session_id,
                        agent="workbuddy", project="p", event_type="tool_call",
                        evidence={"ok": True})
        )


def test_two_ingests_charge_two_directives_not_four(tmp_path):
    """R1: `directives_used` == the directives actually issued (2), not 4."""
    from jcc_core.state_store import StateStore

    store, engine = _engine_with_shadow_wiring(tmp_path)
    _ingest_twice(engine, "s-wire")

    with store.connection() as conn:
        rows = conn.execute(
            "SELECT directive_id, action, actuation_reason FROM directives "
            "WHERE session_id='s-wire' ORDER BY rowid"
        ).fetchall()
    # the duplication itself is DIAGNOSTIC, not the defect: the defect is that the
    # counters read it as four directives.
    assert len(rows) == 4, "expected the two-plane duplication to still be there"

    snap = store.budget_snapshot("s-wire")
    assert snap["directives_used"] == 2, (
        f"one economic event must be counted once: {snap['directives_used']} != 2"
    )
    assert snap["rate_used"] == 2, "the rate ceiling sees the same real call count"
    assert snap["assessments_used"] == 2


def test_cost_is_charged_once_per_event_not_twice(tmp_path):
    """R2: the same economic event must not be billed twice.

    The expectation is derived from the cost table and from the rows that
    SURVIVE the collapse (the charging rows), not from a literal, so this cannot
    quietly drift if the table is retuned or the collapse rule changes.
    """
    from jcc_core.state_store import StateStore

    store, engine = _engine_with_shadow_wiring(tmp_path)
    _ingest_twice(engine, "s-wire")

    expected = _single_charge(store)
    assert expected is not None, "every surviving action must be costable"
    snap = store.budget_snapshot("s-wire")
    assert snap["cost_units_used"] == pytest.approx(expected), (
        f"a duplicated event must be billed once; got {snap['cost_units_used']}, "
        f"single charge is {expected}"
    )
    assert snap["cost_units_used"] < 2 * expected, "the duplicate must not bill twice"


def _directive_rows(store, session_id: str):
    with store.connection() as conn:
        return conn.execute(
            "SELECT directive_id, action, actuation_reason FROM directives "
            "WHERE session_id=? ORDER BY rowid", (session_id,)
        ).fetchall()


def _single_charge(store, session_id: str = "s-wire"):
    """The cost of the surviving rows only (shadow rows last), if determinable."""
    from jcc_core.policy import COST_UNITS_PER_ACTION
    from jcc_core.schemas import DirectiveAction

    rows = _directive_rows(store, session_id)
    shadow = [action for (_did, action, reason) in rows
              if reason == "shadow_no_actuation"]
    chosen = shadow or [action for (_did, action, _r) in rows]
    total = 0.0
    for action in chosen:
        if action is None:
            continue
        try:
            total += COST_UNITS_PER_ACTION[DirectiveAction(action)]
        except (ValueError, KeyError):
            return None
    return total


def test_the_audit_tape_and_the_counter_agree_on_how_many_were_sent(tmp_path):
    """The operational cost of the bug: the tape said 4 were sent, 2 were."""
    from jcc_core.state_store import StateStore

    store, engine = _engine_with_shadow_wiring(tmp_path)
    _ingest_twice(engine, "s-wire")

    snap = store.budget_snapshot("s-wire")
    with store.connection() as conn:
        tape_directives = {
            str(row[0]).split(":", 1)[-1]
            for row in conn.execute(
                "SELECT directive FROM audit_tape WHERE session_id='s-wire'"
            ).fetchall()
        }
    assert len(tape_directives) == 2, (
        f"the tape must report the directives actually issued: {tape_directives}"
    )
    assert snap["directives_used"] == 2


# --------------------------------------------------------------------------- #
# S2 -- the collapse must NOT lose a substituted action
# --------------------------------------------------------------------------- #
def test_a_substituted_directive_keeps_its_escalation(tmp_path):
    """R2 in its dangerous direction: when the sentinel re-issues a blocked
    event as ESCALATE, the engine row still says CONTINUE. Collapsing them must
    keep the ESCALATE, or `escalations_used` under-counts by design."""
    from jcc_core.state_store import StateStore

    store, engine = _engine_with_shadow_wiring(
        tmp_path, budget={"assess_calls": 1, "directives": 1}
    )
    _ingest_twice(engine, "s-budget")

    with store.connection() as conn:
        rows = conn.execute(
            "SELECT directive_id, action FROM directives "
            "WHERE session_id='s-budget' ORDER BY rowid"
        ).fetchall()
    substitution = [(d, a) for d, a in rows if a == "ESCALATE"]
    assert substitution, "the blocked event must have been re-issued as ESCALATE"

    snap = store.budget_snapshot("s-budget")
    assert snap["escalations_used"] >= 2, (
        "collapsing the substitution must not lose the escalation -- that is "
        f"exactly the under-count (#73) this fix exists to prevent; got "
        f"{snap['escalations_used']}"
    )
    assert snap["directives_used"] == 2, (
        "two events -> two directives, even though a substitution happened"
    )


# --------------------------------------------------------------------------- #
# S3 -- with the shadow plane OFF, engine rows are ALL there is
# --------------------------------------------------------------------------- #
def test_with_the_shadow_plane_off_engine_rows_still_count(tmp_path):
    """A rule of the form "count only the supervisor's rows" would read 0 here
    and make every ceiling fail-open. Measured: with `p3.shadow.enabled=false`
    the engine still writes both rows and nothing else does."""
    from jcc_core.state_store import StateStore

    store, engine = _engine_with_shadow_wiring(tmp_path, enabled=False)
    _ingest_twice(engine, "s-off")

    with store.connection() as conn:
        rows = conn.execute(
            "SELECT directive_id, action, actuation_reason FROM directives "
            "WHERE session_id='s-off' ORDER BY rowid"
        ).fetchall()
    assert len(rows) == 2, "the engine must persist its own directives"
    assert {reason for (_d, _a, reason) in rows} == {"shadow_mode"}, (
        "no charging row exists when the shadow plane is off -- if any did, this "
        "scenario would be measuring a different configuration than it claims"
    )

    snap = store.budget_snapshot("s-off")
    assert snap["directives_used"] == 2
    assert snap["rate_used"] == 2
    assert snap["cost_units_used"] > 0.0, "cost must not be lost either way"


# --------------------------------------------------------------------------- #
# the governor reads the SAME surface
# --------------------------------------------------------------------------- #
def test_the_governor_reads_the_deduplicated_counters(tmp_path):
    """The store is the counter source (D-34); the governor must not disagree
    with it, or the ceiling fires against a number nobody can audit."""
    store, engine = _engine_with_shadow_wiring(tmp_path)
    _ingest_twice(engine, "s-wire")

    sentinel = engine._subscribers[-1].__self__
    gs = sentinel.budget.snapshot("s-wire")
    snap = store.budget_snapshot("s-wire")

    assert gs.directives_used == snap["directives_used"] == 2
    assert gs.cost_units_used == pytest.approx(snap["cost_units_used"])
