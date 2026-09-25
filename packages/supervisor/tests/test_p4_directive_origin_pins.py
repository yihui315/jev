"""P4 #82 follow-through — falsifier pins for S-1..S-4 and the ``origin`` write.

Every test here answers ONE question a reviewer would otherwise have to ask by
reading the store schema. The four S-clauses are the ones that were NOT covered
by the waiver pins in ``test_p4_waiver_hostile_pins.py``:

* **S-1** — an action that left no durable shadow trail must not come back as a
  completed directive.
* **S-2** — "was this economic event evaluated?" is answered from the store's
  own folding, and an event with no row counts as UN-evaluated on resume.
* **S-3** — an engine row and a shadow row are counted apart BY ``origin``,
  never by whether some field happens to be populated.
* **S-4** — a lost record is visible in the RETURN VALUE, not only in a log.

These are adversarial on purpose: each one is written so that the natural
"implement it badly" version of the same feature FAILS it.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from jcc_core import decision as decision_kernel
from jcc_core.schemas import AgentName, Assessment, Directive, DirectiveAction
from jcc_core.signing import DirectiveSigner
from jcc_core.state_store import (
    StateStore,
    directive_event_key,
    directive_plane_of,
    resolve_directive_origin,
)

#: The engine half of the pairing. ``jcc_core`` keeps these as module-private
#: (``_DIRECTIVE_ORIGINS`` / ``_ORIGIN_OF_PREFIX``), so the literal is written
#: here and validated by the store's own gate -- a typo fails a test rather than
#: being silently accepted as a third plane.
ENGINE_ORIGIN = "engine"
from supervisor.budget import COST_UNITS, BudgetGovernor, BudgetLimits
from supervisor.ports import ShadowActuationPort
from supervisor.sentinel import (
    SHADOW_DIRECTIVE_PREFIX,
    SUPERVISOR_ORIGIN,
    BlockerCode,
    Sentinel,
)

HERE = Path(__file__).resolve()


def _store(tmp_path) -> StateStore:
    return StateStore(str(tmp_path / "jcc.db"))


def _assessment(session_id: str = "s-1") -> Assessment:
    return Assessment(
        agent=AgentName.GENERIC, session_id=session_id, timestamp=1.0,
        probabilities={"risk": 0.05}, risk_score=0.05, band="low",
        provider_verified=True,
        raw={"signals": {"meaningful_progress": True, "worker_stuck": False,
                         "requirements_satisfied": True, "needs_verification": False,
                         "ready_to_finish": True, "needs_human": False},
             "evidence_complete": True},
    )


def _sentinel(store, *, limits=None):
    return Sentinel(
        store,
        signer=DirectiveSigner(b"k" * 16),
        # Production wires the governor to the SAME store the Sentinel writes to
        # (``run.py``). Leaving it on ``store=None`` silently takes the
        # in-memory-ledger branch of ``_persist_directive``, which returns early
        # and writes NO directive row -- the ``origin`` under test would simply
        # never appear, and the pin would go green for the wrong reason.
        budget=BudgetGovernor(limits or BudgetLimits(), store=store, clock=time.time),
        dispatch_port=ShadowActuationPort(),
        thresholds=decision_kernel.ThresholdsConfig(),
        policy_version="p3.0.0",
        clock=lambda: 1000.0,
        nonce_factory=lambda: "fixed-nonce",
    )


def _row(store, directive_id: str):
    with store.connection() as conn:
        (row,) = conn.execute(
            "SELECT origin, action FROM directives WHERE directive_id=?",
            (directive_id,),
        ).fetchall()
    return row


# --------------------------------------------------------------------------- #
# S-3 — the origin write, and the pairing it is half of
# --------------------------------------------------------------------------- #
def test_the_charging_row_is_attributed_to_the_supervisor_not_the_engine(tmp_path):
    """``budget.py`` must write ``origin="supervisor"``, not leave it implied.

    The store's ``add_directive`` takes ``origin`` keyword-only and required, so
    the old ``adder(directive)`` was a TypeError, not a default. If the call is
    ever reverted to the engine's plane the budget counters silently attribute
    the sentinel's charging to the engine's writer, and the collapse rule
    (one event, one row) starts counting the sentinel's substitutes as the
    engine's decisions.
    """
    store = _store(tmp_path)
    s = _sentinel(store)
    s.arbitrate_and_record(assessment=_assessment(), event_id="e1", agent="generic")

    origin, action = _row(store, f"{SHADOW_DIRECTIVE_PREFIX}s-1_e1")
    assert origin == SUPERVISOR_ORIGIN, (
        f"the charging row is attributed to {origin!r}; it is the sentinel's, "
        f"and it must say so (S-3)"
    )
    assert action == DirectiveAction.FINISH.value


def test_the_pairing_is_checked_at_the_write_site_and_fail_closed(tmp_path):
    """A row that names a plane its id does not prove is refused, not guessed."""
    assert Sentinel.directive_plane(
        f"{SHADOW_DIRECTIVE_PREFIX}s-1_e1", SUPERVISOR_ORIGIN
    ) == SUPERVISOR_ORIGIN
    # the store's own gate must agree with ours, or one of them is decorative
    assert resolve_directive_origin(f"{SHADOW_DIRECTIVE_PREFIX}s-1_e1", SUPERVISOR_ORIGIN)
    assert directive_plane_of(f"{SHADOW_DIRECTIVE_PREFIX}s-1_e1") == SUPERVISOR_ORIGIN


def test_an_unknown_origin_is_refused_and_a_mismatched_prefix_fails_closed():
    with pytest.raises(ValueError, match="unknown directive origin"):
        Sentinel.directive_plane("shadow_s-1_e1", "engine_plane_typo")

    with pytest.raises(RuntimeError, match="pinned prefix"):
        # says supervisor, but the id can only have been issued by the engine
        Sentinel.directive_plane("dir_s-1_e1", SUPERVISOR_ORIGIN)

    # and the reverse direction: the store refuses a cross-origin write outright
    with pytest.raises(ValueError, match="cross-origin"):
        resolve_directive_origin("dir_s-1_e1", SUPERVISOR_ORIGIN)


def test_an_unrecognised_id_shape_is_never_attributed():
    """Worst case of a rename must be the old over-count, never an under-count."""
    assert directive_plane_of("s-1_e1") is None
    assert directive_plane_of("") is None
    assert directive_plane_of("shadow_") is None          # bare prefix: no event
    with pytest.raises(ValueError, match="matches no prefix"):
        resolve_directive_origin("s-1_e1", SUPERVISOR_ORIGIN)


# --------------------------------------------------------------------------- #
# S-2 — one economic event, folded exactly like the store folds it
# --------------------------------------------------------------------------- #
def test_the_two_planes_agree_on_the_economic_event_key(tmp_path):
    """Engine row and shadow row for one event MUST fold to one key.

    This is the half of R1 that breaks silently: the engine writes
    ``dir_<session>_<event>`` and the sentinel writes
    ``shadow_<session>_<event>``. A second underscore after the prefix (i.e.
    ``shadow__<session>_<event>``) looks identical in a log and yields a
    different key, so the event counts twice and ``cost_units_used`` is billed
    twice -- the R2 failure mode.
    """
    sid, eid = "s-1", "e1"
    engine_key = directive_event_key(f"dir_{sid}_{eid}", sid)
    shadow_key = directive_event_key(f"{SHADOW_DIRECTIVE_PREFIX}{sid}_{eid}", sid)
    assert engine_key == shadow_key, (
        "the two planes disagree on the event identity; the fold will count this "
        "economic event twice (R1/R2)"
    )

    store = _store(tmp_path)
    s = _sentinel(store)
    s.arbitrate_and_record(assessment=_assessment(), event_id=eid, agent="generic")
    assert s.is_event_evaluated(sid, f"{SHADOW_DIRECTIVE_PREFIX}{sid}_{eid}") is True
    assert s.is_event_evaluated(sid, f"dir_{sid}_{eid}") is True, (
        "the engine's own id must also read as evaluated; answering from the id "
        "instead of the event is what makes a shadow row look like engine work"
    )


def test_an_event_with_no_row_counts_as_un_evaluated_on_resume(tmp_path):
    """A missing row is NOT an evaluation (S-2, safe direction)."""
    store = _store(tmp_path)
    s = _sentinel(store)
    assert s.evaluated_directive_events("s-1") == set()
    assert s.is_event_evaluated("s-1", f"{SHADOW_DIRECTIVE_PREFIX}s-1_e1") is False
    assert s.is_event_evaluated("s-1", "dir_s-1_e1") is False


def test_a_second_underscore_after_the_prefix_splits_the_event(tmp_path):
    """Falsifier for the id shape above: a divergent shape must NOT merge."""
    sid = "s-1"
    good = directive_event_key(f"{SHADOW_DIRECTIVE_PREFIX}{sid}_e1", sid)
    broken = directive_event_key(f"{SHADOW_DIRECTIVE_PREFIX}{sid}_e1".replace(
        SHADOW_DIRECTIVE_PREFIX, SHADOW_DIRECTIVE_PREFIX + "_"), sid)
    assert good != broken


def test_the_fold_takes_the_substituted_action_not_the_engine_action(tmp_path):
    """S2 scenario, pinned at the plane that substitutes it.

    When the priced gate refuses, the sentinel issues ESCALATE where the engine
    issued CONTINUE. The collapse keeps the LAST row written, so the surviving
    action must be the sentinel's ESCALATE -- otherwise an escalation is lost to
    the fold and ``cost_units_used`` bills the cheaper engine action.
    """
    store = _store(tmp_path)

    # The ENGINE row goes in FIRST, exactly as ``core.py`` writes it, and only
    # then does the sentinel run. Order is the whole point: the fold keeps the
    # LAST row written by ``rowid``, so writing the engine row afterwards would
    # make the ENGINE's CONTINUE survive and the escalation would be lost here
    # rather than at the point where it is actually decided.
    with store.connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO directives (directive_id, session_id, timestamp, "
            "action, target_actuator, params, policy_version, signed, signature, "
            "evidence_hash, acted, actuation_reason, origin) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("dir_s-1_e1", "s-1", 1000.0, "CONTINUE", "noop", "{}", "p3.0.0", 0,
             None, None, 0, None, ENGINE_ORIGIN),
        )
        conn.commit()

    s = _sentinel(store, limits=BudgetLimits(directives=0))
    d = s.arbitrate_and_record(assessment=_assessment(), event_id="e1", agent="generic")
    assert d.action is DirectiveAction.ESCALATE, "the priced gate must substitute"
    assert _row(store, "dir_s-1_e1")[0] == ENGINE_ORIGIN

    # the sentinel's charging row was written AFTER the engine's, so it survives
    derived = store.budget_snapshot("s-1")
    snap = gov_snapshot = BudgetGovernor(BudgetLimits(), store=store,
                                         clock=time.time).snapshot("s-1")
    assert derived["directives_used"] == 1, (
        f"two planes, one event: the fold counted {derived['directives_used']} "
        "directives instead of 1"
    )
    assert derived["escalations_used"] == 1, (
        "the surviving row must be the sentinel's substituted ESCALATE, not the "
        "engine's CONTINUE (R1/R2)"
    )
    assert gov_snapshot.cost_units_used == pytest.approx(
        COST_UNITS[DirectiveAction.ESCALATE]), (
        f"cost {gov_snapshot.cost_units_used} != the ESCALATE the sentinel issued"
    )
    assert snap.directives_used == 1, (
        f"two planes, one event: the fold counted {snap.directives_used} directives "
        "instead of 1"
    )


# --------------------------------------------------------------------------- #
# S-1 — an unpersisted directive is not a completed directive
# --------------------------------------------------------------------------- #
def test_a_record_that_did_not_durably_land_returns_no_decision(tmp_path):
    """S-1: never hand back a ShadowDecision whose action has no shadow trail.

    The degraded store keeps the record in a list on the heap. Returning a
    ``ShadowDecision`` for it is #54 with a decision bolted on: the caller reads
    ``decision.action`` and reasonably concludes the plane evaluated and
    escalated, while nothing an audit could find exists anywhere.
    """

    class _NoSurfaceStore:
        """Defines NOTHING (no transaction) — the degraded branch."""

    s = _sentinel(_NoSurfaceStore())
    d = s.arbitrate_and_record(
        assessment=_assessment(), event_id="e1", agent="generic",
        session_status="active",
    )
    assert d is None, (
        "a non-durable record came back as a decision; the caller will read its "
        "action as a completed shadow evaluation (S-1)"
    )
    assert s.persistence_attempts == 1
    assert s.persistence_failures == 1
    assert s.persistence_durable_writes == 0
    assert s.persistence_ok is False
    assert len(s._memory) == 1


def test_the_durable_path_still_returns_a_decision(tmp_path):
    """Non-vacuity for the pin above: a record that DID land must still return."""
    store = _store(tmp_path)
    s = _sentinel(store)
    d = s.arbitrate_and_record(
        assessment=_assessment(), event_id="e1", agent="generic", session_status="active"
    )
    assert d is not None and d.directive_id.startswith(SHADOW_DIRECTIVE_PREFIX)
    assert s.persistence_ok is True
    assert s.persistence_failures == 0


# --------------------------------------------------------------------------- #
# S-4 — the failure must be visible WITHOUT relying on the broadcast's logging
# --------------------------------------------------------------------------- #
def test_a_lost_record_cannot_be_read_as_a_shadow_trace_through_on_event(tmp_path):
    """S-4: ``on_event`` must not return a decision for a record that was lost.

    ``core._broadcast`` ends in a bare ``except Exception: pass``
    (``core.py:74-79``), so ANY exception raised out of here is swallowed with
    no signal to the ingest caller. A failure that lives only in a log line is
    therefore not a visible failure; the return value and the counters are what
    make it visible, and they must agree with each other.
    """

    class _FailingStore(StateStore):
        def transaction(self):  # noqa: D102
            raise RuntimeError("disk full")

    s = _sentinel(_FailingStore(str(tmp_path / "jcc.db")))
    recorded = s.on_event({
        "session_id": "s-1", "event_id": "e1",
        "assessment": _assessment().model_dump(mode="json"), "session_status": "active",
    })
    assert recorded is None, "a lost record must not look like a shadow trace (S-4)"
    assert s.persistence_failures == 1
    assert s.persistence_durable_writes == 0
    assert s.persistence_ok is False
    # and non-vacuous: the failure path really was the one taken
    assert s.persistence_attempts == 1


def test_on_event_returns_none_for_a_degraded_store_and_counts_once(tmp_path):
    """The degraded path counts exactly once, and returns no decision (S-4)."""

    class _DegradedStore:
        """No ``transaction``: the record is in-memory only."""

    s = _sentinel(_DegradedStore())
    recorded = s.on_event({
        "session_id": "s-1", "event_id": "e1",
        "assessment": _assessment().model_dump(mode="json"), "session_status": "active",
    })
    assert recorded is None
    assert s.persistence_attempts == 1
    assert s.persistence_failures == 1, (
        "the degraded branch and on_event's except must not both count the same "
        "lost record"
    )
    assert s.persistence_ok is False


def test_the_engine_ingest_path_does_not_lose_the_sentinel_row_to_the_broadcast(tmp_path):
    """End-to-end: a real ingest must leave a real shadow row, traced by origin."""
    from jcc_core.actuator import Actuator
    from jcc_core.config import ActuationConfig, Config, PolicyConfig, ServerConfig
    from jcc_core.core import CoreEngine
    from jcc_core.providers import build_provider
    from jcc_core.schemas import AEPEnvelope

    db = str(tmp_path / "jcc.db")
    cfg = Config(server=ServerConfig(), actuation=ActuationConfig(),
                 policy=PolicyConfig(), state_store_db_path=db)
    store = StateStore(db)
    engine = CoreEngine(store, build_provider(cfg), Actuator(enabled=False), cfg)
    s = _sentinel(store)
    engine.subscribe(s.on_event)

    env = AEPEnvelope(event_id="ev1", delivery_id="dl1", session_id="s1",
                      agent="claude", project="p", event_type="tool_call",
                      payload={}, evidence={"ok": True})
    out = engine.ingest(env)
    assert out.get("acted") is False
    assert s.persistence_ok is True, (
        "the engine swallowed a shadow failure; the plane must not report success "
        "into a broadcast that discards exceptions (S-4)"
    )
    origins = {r["origin"] for r in store.get_directives(session_id="s1")}
    assert SUPERVISOR_ORIGIN in origins, (
        "the sentinel's charging row never reached the directives table with its "
        "own origin (S-3)"
    )
    assert s.is_event_evaluated("s1", f"{SHADOW_DIRECTIVE_PREFIX}s1_ev1") is True


def test_the_blocker_code_is_still_escalate_under_the_new_origin_write(tmp_path):
    """Regression guard: the S-3 write must not disturb the priced gate."""
    store = _store(tmp_path)
    s = _sentinel(store, limits=BudgetLimits(directives=0))
    d = s.arbitrate_and_record(assessment=_assessment(), event_id="e1", agent="generic")
    assert d.action is DirectiveAction.ESCALATE
    assert d.blocker_code == BlockerCode.BUDGET_EXHAUSTED.value
    assert _row(store, f"{SHADOW_DIRECTIVE_PREFIX}s-1_e1")[0] == SUPERVISOR_ORIGIN
