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

from jcc_core.schemas import Directive, DirectiveAction

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
# origin attribution -- F1 (namespace ownership) and F2 (no cross-plane overwrite)
# --------------------------------------------------------------------------- #
def _mint(directive_id: str, action=DirectiveAction.PAUSE, **kw) -> Directive:
    return Directive(
        directive_id=directive_id, session_id="s-origin", timestamp=1.0,
        action=action, evidence_hash="deadbeef", **kw
    )


def test_origin_is_required_and_keyword_only(tmp_path):
    """F1, first line: a writer that cannot say who it is may not write.

    There is deliberately NO default. A default does not produce an
    unattributed row by accident -- it produces one *every time someone
    forgets the argument*, which is the silent path #82 took.
    """
    import inspect

    from jcc_core.state_store import StateStore

    s = StateStore(str(tmp_path / "o.db"))
    sig = inspect.signature(StateStore.add_directive)
    origin = sig.parameters["origin"]
    assert origin.default is inspect.Parameter.empty, (
        "origin MUST have no default: a default makes the attribution optional"
    )
    assert origin.kind is inspect.Parameter.KEYWORD_ONLY, (
        "origin MUST be keyword-only, so a caller cannot pass it positionally "
        "and silently be attributed to the wrong writer"
    )

    with pytest.raises(TypeError, match="origin"):
        s.add_directive(_mint("dir_s-origin_e1"))
    with pytest.raises(TypeError, match="positional"):
        s.add_directive(_mint("dir_s-origin_e1"), "engine")  # positional -> no longer


def test_an_origin_outside_the_whitelist_is_refused(tmp_path):
    """Fail-closed whitelist: '' , None, a wrong-but-plausible name and a
    case-variant are ALL refused, not coerced or defaulted."""
    from jcc_core.state_store import StateStore

    s = StateStore(str(tmp_path / "o.db"))
    for bad in ("", "sentinel", "Engine", "ENGINE", "supervisor ", None, "unknown"):
        with pytest.raises(ValueError, match="origin") as exc:
            s.add_directive(_mint("dir_s-origin_e1"), origin=bad)
        assert bad != "engine", "the only accepted value must stay accepted"


@pytest.mark.parametrize(
    "directive_id, origin",
    [
        ("dir_s-origin_e1", "supervisor"),     # engine's namespace, sentinel's name
        ("shadow_s-origin_e1", "engine"),      # sentinel's namespace, engine's name
        ("plain-id", "engine"),                # unrecognised shape, any name
        ("plain-id", "supervisor"),            #  ... and the other one too
        ("", "engine"),                        # empty id is no id
        ("dir_", "engine"),                    # prefix with nothing behind it
    ],
)
def test_f1_an_id_may_only_be_written_by_the_plane_that_owns_its_prefix(
        tmp_path, directive_id, origin):
    """F1: namespace ownership. The prefix is the evidence that the writer had
    the right to mint that id; without it, nothing distinguishes the planes."""
    from jcc_core.state_store import StateStore

    s = StateStore(str(tmp_path / "o.db"))
    with pytest.raises(ValueError, match="origin"):
        s.add_directive(_mint(directive_id), origin=origin)
    # and the refusal must be BEFORE the write: nothing landed
    assert s.get_directives("s-origin") == []


def test_the_two_accepted_writers_are_the_only_two(tmp_path):
    """The whitelist itself is the contract; pin it so it cannot quietly grow."""
    from jcc_core.state_store import StateStore, _DIRECTIVE_ORIGINS

    assert set(_DIRECTIVE_ORIGINS) == {"engine", "supervisor"}
    s = StateStore(str(tmp_path / "o.db"))
    # both are accepted -- otherwise the two probes above would be vacuous
    s.add_directive(_mint("dir_s-origin_e1"), origin="engine")
    s.add_directive(_mint("shadow_s-origin_e1"), origin="supervisor")
    rows = {r["directive_id"]: r["origin"] for r in s.get_directives()}
    assert rows == {"dir_s-origin_e1": "engine",
                    "shadow_s-origin_e1": "supervisor"}


def test_f2_a_cross_plane_overwrite_is_refused_and_leaves_the_row_alone(tmp_path):
    """F2: ``INSERT OR REPLACE`` must NOT silently swap the other plane's record.

    With F1 in front of it, the cross-origin case that F2 alone would catch is
    the one where the PREVIOUS row carries no attribution at all -- a database
    written before ``origin`` existed. Everything else F1 already refuses; F2 is
    the second line that keeps a row of unknown provenance from being replaced
    by whichever writer shows up next, which is precisely how #82's two rows
    ended up indistinguishable.

    The same origin MAY still replace its own row (the idempotent retry), which
    is why the gate is on origin and not on "id already exists".
    """
    from jcc_core.state_store import StateStore

    s = StateStore(str(tmp_path / "o.db"))
    s.add_directive(_mint("dir_s-origin_e1", action=DirectiveAction.PAUSE),
                    origin="engine")

    # 1) F1 refuses the cross-plane write before F2 is even reached
    with pytest.raises(ValueError, match="can only be issued by"):
        s.add_directive(_mint("dir_s-origin_e1", action=DirectiveAction.ESCALATE),
                        origin="supervisor")
    row = s.get_directives("s-origin")[0]
    assert row["action"] == "PAUSE", "the refused write must not have replaced it"

    # 2) F2: a row with NO attribution cannot be overwritten by anyone
    s2 = StateStore(str(tmp_path / "legacy.db"))
    with s2.connection() as conn:
        conn.execute(
            "INSERT INTO directives (directive_id, session_id, timestamp, action) "
            "VALUES (?,?,?,?)",
            ("dir_s-origin_e1", "s-origin", 1.0, "PAUSE"),
        )
        conn.commit()
    with pytest.raises(ValueError, match="cross-origin"):
        s2.add_directive(_mint("dir_s-origin_e1", action=DirectiveAction.ESCALATE),
                         origin="engine")
    with s2.connection() as conn:
        only = conn.execute(
            "SELECT action, origin FROM directives WHERE directive_id='dir_s-origin_e1'"
        ).fetchone()
    assert only == ("PAUSE", None), "the refused write must not have replaced it"

    # 3) same origin, same id -> allowed, and this IS the retry path
    s.add_directive(_mint("dir_s-origin_e1", action=DirectiveAction.ESCALATE),
                    origin="engine")
    row = s.get_directives("s-origin")[0]
    assert row["action"] == "ESCALATE"
    assert len(s.get_directives("s-origin")) == 1


def test_f2_the_independently_load_bearing_case_a_stored_origin_that_contradicts_its_prefix(
        tmp_path):
    """F2 falsifier: the ONE row M1 cannot backfill, and why it must stay red.

    F1 and F2 both refuse "the wrong plane writes this id". They refuse for
    DIFFERENT reasons and only one of the two can fire on any given row:

      * F1 refuses a row the prefix does not send to this caller;
      * F2 refuses a row whose STORED origin differs from this caller's.

    Consider a row that is mis-attributed rather than un-attributed: written by
    a buggy or half-migrated writer as ``origin='supervisor'`` under a
    ``dir_...`` id. F1 is SILENT on it -- the prefix says plane=``engine`` and
    the caller IS ``engine``, so the namespace rule is satisfied -- therefore the
    only thing standing between that row and ``INSERT OR REPLACE`` is F2.

    This matters because of the NULL-prior migration (lead's M1): it backfills
    ``origin`` where it IS NULL, and a NULL prior is then exactly the case F2
    alone catches. Once M1 has run, that branch is gone -- a NULL origin is
    filled from the prefix and the row is no longer "unknown provenance". The
    contradiction row below is what is LEFT when the migration is done, and it
    is the reason F2 is not dead code.

    *** THIS NAIL MUST STILL BE RED AFTER M1 COMPLETES. ***
    It goes green in exactly two circumstances, both of which are the defect
    that M2 is there to catch: (a) the migration rewrote a stored origin on the
    strength of its prefix -- i.e. it fabricated provenance for a row whose
    provenance is not recoverable from the id alone; or (b) F2 was removed.
    Green here is a REGRESSION SIGNAL, not a passing state.

    FALSIFICATION, MEASURED (no production backdoor, no test switch) -- see
    ``.workbuddy/scratch/forge_falsifier_f2.py``, two passes:

      * release F1 ONLY (``resolve_directive_origin`` accepts every
        (id, origin) pair), F2 intact -> THIS TEST STAYS GREEN. That is the
        point of step 0 above: this row is not an F1 case at all;
      * release F2 ONLY (rebuild ``add_directive`` without the prior-origin
        gate, F1 kept) -> THIS TEST GOES RED: nothing refuses the write,
        ``INSERT OR REPLACE`` overwrites the row and its mis-attribution
        disappears with it.

    "Release F1 and watch it go red" is the WRONG falsification for this nail
    and was tried first: it stays green, because F2 still holds. Documented here
    because the natural guess is the wrong one.
    """
    from jcc_core.state_store import StateStore, resolve_directive_origin

    s = StateStore(str(tmp_path / "o.db"))
    # A historical row written by the wrong plane, straight to SQL: a `dir_`
    # id carrying `origin='supervisor'`. Not NULL, not missing -- contradictory,
    # which is why a NULL-backfill cannot fix it.
    with s.connection() as conn:
        conn.execute(
            "INSERT INTO directives (directive_id, session_id, timestamp, action, "
            "origin) VALUES (?,?,?,?,?)",
            ("dir_s-contra_e1", "s-contra", 1.0, "PAUSE", "supervisor"),
        )
        conn.commit()

    # 0) PROOF THAT F1 DOES NOT FIRE HERE, i.e. that F2 is the only gate: the
    #    namespace rule is satisfied by this (id, origin) pair.
    assert resolve_directive_origin("dir_s-contra_e1", "engine") == "engine"

    # 1) F2 refuses the overwrite -- and the message must be F2's, not F1's.
    #    "already persisted as" is the F2 wording; "can only be issued by" is
    #    F1's and would mean this proof-of-exclusivity was wrong.
    with pytest.raises(ValueError, match="already persisted as"):
        s.add_directive(_mint("dir_s-contra_e1", action=DirectiveAction.ESCALATE),
                        origin="engine")

    # 2) the refused write left the row exactly as it found it
    with s.connection() as conn:
        row = conn.execute(
            "SELECT action, origin FROM directives "
            "WHERE directive_id='dir_s-contra_e1'"
        ).fetchone()
    assert row == ("PAUSE", "supervisor"), (
        "the mis-attributed row must survive unmodified: rewriting a stored "
        "origin from the id prefix would be inventing provenance"
    )


def test_the_persisted_row_carries_the_attribution_it_was_written_with(tmp_path):
    """R3 in the concrete form: provenance is a COLUMN, not a side-channel, and
    it survives a read back through the public surface."""
    from jcc_core.state_store import StateStore

    s = StateStore(str(tmp_path / "o.db"))
    s.add_directive(_mint("dir_s-origin_e1"), origin="engine")
    with s.connection() as conn:
        stored = conn.execute(
            "SELECT origin FROM directives WHERE directive_id='dir_s-origin_e1'"
        ).fetchone()[0]
    assert stored == "engine", "the writer must be recorded, not just checked"
    assert s.get_directives("s-origin")[0]["origin"] == "engine"


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
