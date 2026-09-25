"""P4 independent security review nails for #82 (aegis, P4 freeze gate).

Independent re-derivation of the four attack surfaces the lead asked about. Each
test ATTEMPTS to break the property and asserts the defense held -- the suite is
the evidence cited in ``docs/SECURITY-P3-REVIEW.md`` (ADDENDUM #82). These are
falsifiers, not re-statements of the source: every ``assert`` below is reached
only after the attack was actually performed against real code.

Nothing here touches product code. Product files under ``jcc_core/`` and
``supervisor/`` are only imported and exercised.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jcc_core import decision as decision_kernel
from jcc_core import state_store as ss
from jcc_core.actuator import Actuator
from jcc_core.config import ActuationConfig, Config, PolicyConfig, ServerConfig
from jcc_core.core import CoreEngine
from jcc_core.decision import ThresholdsConfig
from jcc_core.providers import build_provider
from jcc_core.schemas import AEPEnvelope, AgentName, Assessment, Directive, DirectiveAction
from jcc_core.signing import DirectiveSigner
from jcc_core.state_store import (
    StateStore,
    backfill_directive_origins,
    directive_event_key,
    directive_plane_of,
    resolve_directive_origin,
)

from supervisor.budget import BudgetGovernor, BudgetLimits
from supervisor.ports import ShadowActuationPort
from supervisor.sentinel import SHADOW_DIRECTIVE_PREFIX, SUPERVISOR_ORIGIN, Sentinel

HERE = Path(__file__).resolve()


# --------------------------------------------------------------------------- #
# shared fixtures
# --------------------------------------------------------------------------- #
def _store(tmp_path) -> StateStore:
    return StateStore(str(tmp_path / "jcc.db"))


def _mint(directive_id: str, action=DirectiveAction.PAUSE, session="s-origin", **kw) -> Directive:
    return Directive(
        directive_id=directive_id, session_id=session, timestamp=1.0,
        action=action, evidence_hash="deadbeef", **kw
    )


def _sentinel(store, *, limits=None):
    return Sentinel(
        store,
        signer=DirectiveSigner(b"k" * 16),
        budget=BudgetGovernor(limits or BudgetLimits(), store=store, clock=lambda: 1000.0),
        dispatch_port=ShadowActuationPort(),
        thresholds=ThresholdsConfig(),
        policy_version="p3.0.0",
        clock=lambda: 1000.0,
        nonce_factory=lambda: "fixed-nonce",
    )


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


class _FixedProvider:
    """Deterministic provider so two separate ingests yield the SAME engine
    decision -- isolating the shadow plane as the only variable under test."""

    def assess(self, event, sess):
        return _assessment(session_id=event.session_id)


def _raw_insert(s, directive_id, *, origin=None, action="PAUSE", session="s-x"):
    with s.connection() as conn:
        if origin is None:
            conn.execute(
                "INSERT INTO directives (directive_id, session_id, timestamp, action) "
                "VALUES (?,?,?,?)", (directive_id, session, 1.0, action))
        else:
            conn.execute(
                "INSERT INTO directives (directive_id, session_id, timestamp, action, "
                "origin) VALUES (?,?,?,?,?)", (directive_id, session, 1.0, action, origin))
        conn.commit()


# --------------------------------------------------------------------------- #
# POINT 1(a) -- caller omits `origin`: must be TypeError, never defaulted
# --------------------------------------------------------------------------- #
def test_p1a_omitting_origin_is_typeerror_not_a_default(tmp_path):
    """Attack: call add_directive with no origin and hope it defaults to engine.

    Expected defense: ``origin`` is keyword-only + required, so the call raises
    ``TypeError`` and NOTHING is persisted (no silent ``origin='engine'`` row).
    """
    import inspect

    sig = inspect.signature(StateStore.add_directive)
    p = sig.parameters["origin"]
    assert p.default is inspect.Parameter.empty, "origin must have NO default"
    assert p.kind is inspect.Parameter.KEYWORD_ONLY, "origin must be keyword-only"

    s = _store(tmp_path)
    with pytest.raises(TypeError, match="origin"):
        s.add_directive(_mint("dir_s-origin_e1"))            # omitted entirely
    with pytest.raises(TypeError, match="positional"):
        s.add_directive(_mint("dir_s-origin_e1"), "engine")  # positional attempt

    # the refusal is BEFORE the write -- the table is empty, nothing defaulted
    assert s.get_directives("s-origin") == [], "a defaulted origin would have landed here"


# --------------------------------------------------------------------------- #
# POINT 1(b) -- forge origin vs id prefix: F1 must still refuse
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "directive_id,origin",
    [
        ("dir_s-origin_e1", "supervisor"),   # engine namespace, sentinel name
        ("shadow_s-origin_e1", "engine"),    # sentinel namespace, engine name
        ("weird_s-origin_e1", "engine"),     # unrecognised shape + plausible origin
        ("weird_s-origin_e1", "supervisor"), # ... and the other plausible origin
        ("dir_", "engine"),                  # bare prefix: no event behind it
    ],
)
def test_p1b_a_forgedor_origin_is_refused_and_not_written(tmp_path, directive_id, origin):
    """Attack: mint an id that looks like one plane but stamp the other origin.

    Expected defense: ``resolve_directive_origin`` refuses (prefix != origin, or
    prefix unrecognised). The row must NOT hit the table.
    """
    s = _store(tmp_path)
    with pytest.raises(ValueError, match="origin|can only be issued|matches no prefix"):
        s.add_directive(_mint(directive_id), origin=origin)
    assert s.get_directives("s-origin") == [], "forged origin must not have persisted"


def test_p1b_two_legit_writers_are_still_accepted(tmp_path):
    s = _store(tmp_path)
    s.add_directive(_mint("dir_s-origin_e1"), origin="engine")
    s.add_directive(_mint("shadow_s-origin_e1"), origin="supervisor")
    rows = {r["directive_id"]: r["origin"] for r in s.get_directives()}
    assert rows == {"dir_s-origin_e1": "engine", "shadow_s-origin_e1": "supervisor"}


# --------------------------------------------------------------------------- #
# POINT 1(c) -- M1 backfill cannot be tricked into stamping an origin
# --------------------------------------------------------------------------- #
def test_p1c_backfill_refuses_to_fabricate_an_origin_for_unattributable_row(tmp_path):
    """Attack: leave a row whose shape proves nothing, run M1, hope it gets
    stamped ``engine`` (the old #82 laundering of provenance).

    Expected defense: ``backfill_directive_origins`` leaves it in ``refused`` /
    ``still_null`` and the stored ``origin`` REMAINS NULL -- it is never written
    to an invented value.
    """
    s = _store(tmp_path)
    _raw_insert(s, "weird_s-x_e1", origin=None, action="PAUSE", session="s-x")

    report = backfill_directive_origins(s.db_path)
    assert [r["directive_id"] for r in report["refused"]] == ["weird_s-x_e1"]
    assert report["still_null"] == ["weird_s-x_e1"]
    assert report["admissible_as_release_evidence"] is False

    # the row itself: origin is STILL NULL, not 'engine'/'supervisor'
    with s.connection() as conn:
        stored = conn.execute(
            "SELECT origin FROM directives WHERE directive_id='weird_s-x_e1'"
        ).fetchone()[0]
    assert stored is None, f"M1 fabricated provenance: origin={stored!r}"


def test_p1c_attributable_rows_are_derived_not_assigned(tmp_path):
    s = _store(tmp_path)
    _raw_insert(s, "dir_s-x_e1", origin=None, session="s-x")
    _raw_insert(s, "shadow_s-x_e1", origin=None, session="s-x")
    report = backfill_directive_origins(s.db_path)
    with s.connection() as conn:
        origins = dict(conn.execute(
            "SELECT directive_id, origin FROM directives").fetchall())
    assert origins == {"dir_s-x_e1": "engine", "shadow_s-x_e1": "supervisor"}
    assert report["attributed"] == 2 and report["refused"] == []


# --------------------------------------------------------------------------- #
# POINT 2 -- is "假成功" now truly visible? (S-1..S-4)
# --------------------------------------------------------------------------- #
class _FailingStore(StateStore):
    """Store whose lock-scoped transaction raises: the shadow write cannot land."""

    def transaction(self):
        raise RuntimeError("disk full")


def test_p2_on_event_returns_none_and_counts_the_failure_once(tmp_path):
    """S-4 falsifier: a lost record returns None, persistence_failures == 1,
    persistence_ok is False, and it is counted exactly once (not doubled by the
    except branch)."""
    s = _sentinel(_FailingStore(str(tmp_path / "jcc.db")))
    recorded = s.on_event({
        "session_id": "s-1", "event_id": "e1",
        "assessment": _assessment().model_dump(mode="json"), "session_status": "active",
    })
    assert recorded is None, "a lost record must not look like a shadow trace"
    assert s.persistence_attempts == 1
    assert s.persistence_failures == 1
    assert s.persistence_durable_writes == 0
    assert s.persistence_ok is False


def test_p2_the_engine_caller_cannot_tell_shadow_substitute_from_shadow_absent(tmp_path):
    """The crux the review must prove, not assume.

    Build TWO engine runs that are byte-identical on the engine side. Run A: the
    shadow plane writes fine (returns a ShadowDecision, persists its records). Run
    B: the shadow store's ``transaction()`` raises, so the shadow HASH-CHAIN
    (``shadow_decisions``) append fails.

    Measured result (the part a code-read would get WRONG): because the sentinel's
    ``directives`` charging row is written through ``add_directive`` -- which uses
    ``StateStore._conn()`` directly and NOT ``transaction()`` -- the charging row
    LANDS in BOTH runs. What differs between A and B is ONLY the
    ``shadow_decisions`` audit record and ``sentinel.persistence_ok``. The engine's
    ``out`` and the ``directives`` table are identical. So the engine's EXTERNAL
    caller, who sees only ``out`` and the folded counters, cannot tell the two
    apart. S-1..S-4 made the failure OBSERVABLE to an in-process sentinel
    observer (``persistence_ok``), but the engine boundary discards the signal
    (``core.py:_broadcast`` ignores the callback return value and would swallow any
    raise).
    """
    # ---- Run A: shadow writes fine ---------------------------------------- #
    db_a = str(tmp_path / "a" / "jcc.db")
    cfg = Config(server=ServerConfig(), actuation=ActuationConfig(),
                 policy=PolicyConfig(), state_store_db_path=db_a)
    store_a = StateStore(db_a)
    eng_a = CoreEngine(store_a, _FixedProvider(), Actuator(enabled=False), cfg)
    sent_a = _sentinel(store_a)
    eng_a.subscribe(sent_a.on_event)
    out_a = eng_a.ingest(AEPEnvelope(event_id="ev1", delivery_id="d1", session_id="sa",
                                      agent="workbuddy", project="p", event_type="tool_call",
                                      evidence={"ok": True}))
    assert sent_a.persistence_ok is True

    # ---- Run B: shadow hash-chain append fails ---------------------------- #
    db_b = str(tmp_path / "b" / "jcc.db")
    cfg_b = Config(server=ServerConfig(), actuation=ActuationConfig(),
                   policy=PolicyConfig(), state_store_db_path=db_b)
    store_b = _FailingStore(db_b)
    eng_b = CoreEngine(store_b, _FixedProvider(), Actuator(enabled=False), cfg_b)
    sent_b = _sentinel(store_b)
    eng_b.subscribe(sent_b.on_event)
    out_b = eng_b.ingest(AEPEnvelope(event_id="ev1", delivery_id="d1", session_id="sb",
                                      agent="workbuddy", project="p", event_type="tool_call",
                                      evidence={"ok": True}))

    # 1) the engine's OWN decision is identical in both runs (engine does not
    #    depend on the shadow plane), and the response carries NO field that would
    #    betray whether the shadow ran, substituted, or failed.
    assert out_a["action"] == out_b["action"], "engine action differs -- not shadow-independent"
    # 2) the folded budget COUNTERS the governor actually enforces are identical:
    #    the shadow hash-chain failure does not change directives_used /
    #    escalations_used / cost_units_used (the charging row still landed via
    #    add_directive, which bypasses transaction()).
    snap_a = store_a.budget_snapshot("sa")
    snap_b = store_b.budget_snapshot("sb")
    for key in ("directives_used", "escalations_used", "retries_used", "cost_units_used", "rate_used"):
        assert snap_a[key] == snap_b[key], f"budget counter {key!r} leaked the failure"
    # 3) the ONLY divergence is the sentinel's own telemetry -- which the engine
    #    boundary throws away (core.py:_broadcast ignores the callback return and
    #    would swallow any raise).
    assert sent_b.persistence_ok is False
    assert sent_b.persistence_failures == 1
    for k in ("shadow", "persistence", "escalate"):
        assert k not in out_b, f"engine out leaks the shadow signal via {k!r}"


def test_p2_directives_charge_survives_shadow_decision_failure(tmp_path):
    """Independent finding: the #82 attribution ROW (the sentinel's ``directives``
    charging copy, written via ``add_directive``) is written on a path that does
    NOT go through ``transaction()``. So a ``shadow_decisions`` append failure (the
    thing S-1..S-4 actually guard) does NOT lose the origin-attributed charge.

    This is GOOD for #82 (the attribution the defect was about survives), but it
    means "``on_event`` returned None / ``persistence_failures`` incremented"
    cannot be read as "no ``directives`` row was written". The two persistences
    are independent.
    """
    s = _sentinel(_FailingStore(str(tmp_path / "jcc.db")))
    s.on_event({
        "session_id": "s-1", "event_id": "e1",
        "assessment": _assessment().model_dump(mode="json"), "session_status": "active",
    })
    assert s.persistence_ok is False  # the shadow_decision append failed
    # ...yet the sentinel's OWN origin-attributed charging row IS present:
    rows = [r for r in s.store.get_directives("s-1")
            if r["directive_id"].startswith(SHADOW_DIRECTIVE_PREFIX)]
    assert len(rows) == 1
    assert rows[0]["origin"] == SUPERVISOR_ORIGIN


def test_p2_is_event_evaluated_does_NOT_go_false_on_shadow_only_failure(tmp_path):
    """Independent correction of the stated premise.

    The brief claims a shadow-persistence failure makes ``is_event_evaluated``
    false. It does NOT. Two reasons, both measured:

    * S-2 folds by ECONOMIC-EVENT key. The engine's own ``dir_`` row (or, here,
      the sentinel's own surviving ``directives`` row) already satisfies that key.
    * More fundamentally, the sentinel's ``directives`` charging row is written
      even when the ``shadow_decisions`` append fails (see
      ``test_p2_directives_charge_survives_shadow_decision_failure``), so the event
      genuinely HAS a row.

    So ``is_event_evaluated`` detects a MISSING event, not a shadow-only loss. The
    real shadow-failure signal is ``sentinel.persistence_ok``, which lives on the
    sentinel, not on the engine and not in S-2.
    """
    s = _sentinel(_FailingStore(str(tmp_path / "jcc.db")))
    s.on_event({
        "session_id": "s-1", "event_id": "e1",
        "assessment": _assessment().model_dump(mode="json"), "session_status": "active",
    })
    # the event IS evaluated: the sentinel's own directives row landed even though
    # the shadow_decision append failed.
    assert s.is_event_evaluated("s-1", f"{SHADOW_DIRECTIVE_PREFIX}s-1_e1") is True
    assert s.persistence_ok is False, (
        "the shadow failure shows in persistence_ok, NOT in S-2's is_event_evaluated"
    )



# --------------------------------------------------------------------------- #
# POINT 3 -- attribution-reversal timing dependency
# --------------------------------------------------------------------------- #
def test_p3_current_order_keeps_the_substituted_escalation(tmp_path):
    """The dependency, by construction: core.ingest writes the engine ``dir_``
    row at step 8 (state_store.add_directive(origin='engine')) and only THEN
    calls ``self._broadcast(out)``, which triggers the sentinel's shadow write.
    So the shadow row is always the LAST writer and survives the fold. Prove the
    intended order preserves the substituted ESCALATE.
    """
    store = _store(tmp_path)
    # engine CONTINUE FIRST, exactly as core.ingest does
    with store.connection() as conn:
        conn.execute(
            "INSERT INTO directives (directive_id, session_id, timestamp, action, "
            "target_actuator, params, policy_version, signed, signature, "
            "evidence_hash, acted, actuation_reason, origin) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("dir_s-1_e1", "s-1", 1000.0, "CONTINUE", "noop", "{}", "p3.0.0", 0,
             None, None, 0, None, "engine"))
        conn.commit()
    s = _sentinel(store, limits=BudgetLimits(directives=0))
    s.arbitrate_and_record(assessment=_assessment(), event_id="e1", agent="generic")
    snap = store.budget_snapshot("s-1")
    assert snap["directives_used"] == 1
    assert snap["escalations_used"] == 1, (
        "the LAST-written (shadow) row must survive the fold; otherwise the "
        "substituted escalation is lost")


def test_p3_reversed_order_loses_the_escalation_no_nail_blocks_it(tmp_path):
    """Falsifier for the dependency: if the shadow were written BEFORE the engine
    row (i.e. a reorder of core.ingest so _broadcast precedes add_directive),
    the engine's CONTINUE would be the LAST writer and the ESCALATE is lost.

    This demonstrates the dependency is LOAD-BEARING and is currently protected
    ONLY by code ordering -- there is no automated nail pinning "engine add
    precedes broadcast". The assertion below documents the failure mode that such
    a reorder would silently cause.
    """
    store = _store(tmp_path)
    # shadow ESCALATE FIRST (reversed order)
    s = _sentinel(store, limits=BudgetLimits(directives=0))
    s.arbitrate_and_record(assessment=_assessment(), event_id="e1", agent="generic")
    # then the engine's CONTINUE lands AFTER (simulating the reorder)
    with store.connection() as conn:
        conn.execute(
            "INSERT INTO directives (directive_id, session_id, timestamp, action, "
            "target_actuator, params, policy_version, signed, signature, "
            "evidence_hash, acted, actuation_reason, origin) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("dir_s-1_e1", "s-1", 1001.0, "CONTINUE", "noop", "{}", "p3.0.0", 0,
             None, None, 0, None, "engine"))
        conn.commit()
    snap = store.budget_snapshot("s-1")
    assert snap["escalations_used"] == 0, (
        "REVERSED ORDER: the engine CONTINUE overwrote the shadow ESCALATE in the "
        "fold. This is the unprotected dependency -- a reorder of core.ingest "
        "would silently lose escalations. No nail currently blocks it."
    )


# --------------------------------------------------------------------------- #
# POINT 4 -- K-fork directionality: over-count only, never under-count
# --------------------------------------------------------------------------- #
def test_p4_unrecognised_shapes_never_merge_and_only_over_count(tmp_path):
    """An id of an unrecognised shape gets a key unique to itself, so it can
    never be folded INTO a recognised event: the worst case is the old
    over-count, never an under-count (the miss-charge that #82 was about).
    """
    assert directive_event_key("zog_s_1", "s") != directive_event_key("dir_zog_s_1", "s")
    assert directive_event_key("zog_s_1", "s") == directive_event_key("zog_s_1", "s")
    assert "unrecognised" in directive_event_key("zog_s_1", "s")

    store = _store(tmp_path)
    _raw_insert(store, "zog_s_1", origin=None, action="PAUSE", session="s")
    snap = store.budget_snapshot("s")
    # the single unrecognised row is counted as one whole directive (over-count
    # direction) -- it did NOT vanish into a recognised event
    assert snap["directives_used"] == 1


def test_p4_attacker_cannot_inject_unrecognised_rows_through_the_write_path(tmp_path):
    """The over-count is NOT attacker-reachable: add_directive refuses any id
    whose shape it cannot attribute (F1). So a stream of unrecognised rows --
    the only thing that could permanently inflate the budget -- cannot be
    produced via the API. The over-count is bounded to legacy/pre-F1 residue.
    """
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        store.add_directive(_mint("zog_s_1"), origin="engine")
    with pytest.raises(ValueError):
        store.add_directive(_mint("zog_s_1"), origin="supervisor")
    # and a legacy unrecognised row does NOT grow on every event: each normal
    # event still folds to exactly one directive
    with store.connection() as conn:
        conn.execute(
            "INSERT INTO directives (directive_id, session_id, timestamp, action, "
            "origin) VALUES (?,?,?,?,?)",
            ("dir_s_ev1", "s", 1.0, "PAUSE", "engine"))
        conn.execute(
            "INSERT INTO directives (directive_id, session_id, timestamp, action, "
            "origin) VALUES (?,?,?,?,?)",
            (f"{SHADOW_DIRECTIVE_PREFIX}s_ev1", "s", 1.0, "PAUSE", "supervisor"))
        conn.commit()
    snap = store.budget_snapshot("s")
    assert snap["directives_used"] == 1, "two planes, one event -> still one directive"


def test_p4_a_corrupted_legacy_row_with_bad_action_raises_not_silent_undercount(tmp_path):
    """Secondary finding (conservative, fail-closed): a legacy unrecognised row
    carrying an uncostable action makes ``budget_snapshot`` RAISE, which the
    governor turns into ``CODE_CHECK_ERROR`` (exhausted). This is the fail-CLOSED
    direction (block rather than under-count); it is availability-impacting for
    already-corrupted data, not a new #82 attack surface. Reported as residual.
    """
    store = _store(tmp_path)
    _raw_insert(store, "zog_s_1", origin=None, action="NOT_A_REAL_ACTION", session="s")
    with pytest.raises(ValueError, match="uncostable"):
        store.budget_snapshot("s")
