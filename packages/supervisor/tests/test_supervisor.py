"""Tests for the P3 supervisor plane (ADR-0008 D-7/D-13/D-34/D-35/D-40/D-41).

Named for the property falsified. The load-bearing ones are the D-40 shadow
invariant (never actuates; single dispatch site) and D-34 budget replacement.

**Normative test rule (ADR-0008 spec §6, from the #54 "records nothing" defect):
a test double MAY NOT define an interface the real collaborator lacks.** These
tests therefore exercise the **real ``StateStore``** on a temporary DB — there is
no ``_FakeStore``. The prior double held ``_conn`` as a *real connection
attribute* while the production store's ``_conn`` is a *method*, so the suite
green-lit an interface that did not exist and the shadow plane recorded nothing
against the real store. A green suite over a lying fake is not evidence.
"""
from __future__ import annotations

import ast
import time
from pathlib import Path

import pytest

from jcc_core import audit as audit_kernel
from jcc_core import decision
from jcc_core.schemas import AgentName, Assessment, DirectiveAction
from jcc_core.signing import DirectiveSigner
from jcc_core.state_store import StateStore
from supervisor import arbitration, evaluation, shadow_store
from supervisor.budget import BudgetGovernor, BudgetLimits, CODE_DIRECTIVES
from supervisor.ports import ShadowActuationPort
from supervisor.sentinel import BlockerCode, Sentinel

HERE = Path(__file__).resolve()
SENTINEL_SRC = (HERE.parents[1] / "sentinel.py").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #
def _store(tmp_path) -> StateStore:
    """The REAL store — never a double (spec §6 no-lying-fake rule)."""
    return StateStore(str(tmp_path / "jcc.db"))


def _rows(store, **kw):
    with store.connection() as conn:
        return shadow_store.read_decisions(conn, **kw)


def _chain(store):
    with store.connection() as conn:
        return shadow_store.verify_shadow_chain(conn)


def _assessment(**over) -> Assessment:
    signals = {
        "meaningful_progress": True,
        "worker_stuck": False,
        "work_off_track": False,
        "requirements_satisfied": True,
        "needs_verification": False,
        "ready_to_finish": True,
        "needs_human": False,
    }
    signals.update(over.pop("signals", {}))
    kw = dict(
        agent=AgentName.GENERIC, session_id="s-1", timestamp=1.0,
        probabilities={"risk": 0.05}, risk_score=0.05, band="low",
        provider_verified=True,
        raw={"signals": signals, "evidence_complete": True},
    )
    kw.update(over)
    return Assessment(**kw)


def _sentinel(store, *, key=b"k" * 16, require_signature=False, limits=None) -> Sentinel:
    return Sentinel(
        store,
        signer=DirectiveSigner(key),
        budget=BudgetGovernor(limits or BudgetLimits(), store=None),
        dispatch_port=ShadowActuationPort(),
        thresholds=decision.ThresholdsConfig(),
        policy_version="p3.0.0",
        require_signature=require_signature,
        clock=lambda: 1000.0,
        nonce_factory=lambda: "fixed-nonce",
    )


# --------------------------------------------------------------------------- #
# §2.6 store contract — the Sentinel must use the store's PUBLIC surface only
# --------------------------------------------------------------------------- #
def test_sentinel_never_reaches_for_a_private_connection():
    """Spec §2.6: no ``store._conn`` and no ``getattr(store, "_conn", None)``.

    The forbidden idiom returns a truthy *bound method* (``_conn`` is a method),
    which is the exact #54 defect. Static AST scan so it cannot silently return.
    """
    tree = ast.parse(SENTINEL_SRC)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr not in ("_conn", "_lock"), f"private store poke: .{node.attr}"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr":
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                assert "_conn" not in str(node.args[1].value)
    assert "_store_connection" not in SENTINEL_SRC


# --------------------------------------------------------------------------- #
# #54 regression: rows must ACTUALLY land in the real store (non-vacuous D-40)
# --------------------------------------------------------------------------- #
def test_shadow_plane_persists_against_the_real_state_store(tmp_path):
    """NON-VACUOUS D-40: rows must actually land in the REAL ``StateStore``.

    If the plane is a silent no-op, ``rows == 1`` fails here instead of the
    "every row is inert" assertion passing vacuously over an empty set.
    """
    store = _store(tmp_path)
    s = _sentinel(store)
    d = s.arbitrate_and_record(assessment=_assessment(), event_id="e1", agent="generic")

    rows = _rows(store)
    assert len(rows) == 1, "shadow plane recorded NOTHING against the real StateStore"
    assert rows[0]["acted"] == 0
    assert d.acted is False
    ok, count = _chain(store)
    assert ok is True and count == 1


def test_on_event_records_against_the_real_state_store(tmp_path):
    """The engine-broadcast entry point must also persist (not just the direct call)."""
    store = _store(tmp_path)
    s = _sentinel(store)
    assessment = _assessment()
    out = {
        "session_id": assessment.session_id, "event_id": "e9",
        "action": "FINISH", "evidence_hash": None,
        "assessment": assessment.model_dump(mode="json"),
        "session_status": "active",
    }
    recorded = s.on_event(out)
    assert recorded is not None, "on_event swallowed a failure (no record produced)"
    assert len(_rows(store)) == 1


def test_engine_ingest_records_shadow_row_against_real_store(tmp_path):
    """End-to-end through the REAL engine broadcast — forge's exact scenario.

    Reproduces what ``run._wire_shadow`` does (``engine.subscribe(sentinel.on_event)``)
    and asserts a shadow row actually lands. With the #54 bug, ``engine.ingest``
    succeeded, the audit chain stayed intact, and the shadow table was simply
    never created.
    """
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
    engine.subscribe(_sentinel(store).on_event)   # exactly what run._wire_shadow does

    env = AEPEnvelope(event_id="ev1", delivery_id="dl1", session_id="s1",
                      agent="claude", project="p", event_type="tool_call",
                      payload={}, evidence={"ok": True})
    out = engine.ingest(env)
    assert out.get("acted") is False      # engine path itself stays inert

    rows = _rows(store)
    assert len(rows) == 1, "engine broadcast did NOT persist a shadow row (run.py wiring gap)"
    assert rows[0]["acted"] == 0
    ok, count = _chain(store)
    assert ok is True and count == 1


# --------------------------------------------------------------------------- #
# D-41: the port is inert
# --------------------------------------------------------------------------- #
def test_shadow_port_never_acts():
    from jcc_core.schemas import Directive
    d = Directive(directive_id="d", session_id="s", timestamp=0.0, action=DirectiveAction.ESCALATE)
    result = ShadowActuationPort().dispatch(d)
    assert result["acted"] is False
    assert result["executable"] is False
    assert result["directive_id"] == "d"


def test_there_is_exactly_one_dispatch_call_site():
    """D-41: ``DispatchPort.dispatch`` has exactly ONE call site in the plane."""
    tree = ast.parse(SENTINEL_SRC)
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "dispatch"
    ]
    assert len(calls) == 1, f"expected one dispatch call site, found {len(calls)}"
    # it must be the seam attribute, and it must never be ``execute``
    assert isinstance(calls[0].func.value, ast.Attribute)
    assert calls[0].func.value.attr == "dispatch_port"
    assert ".execute(" not in SENTINEL_SRC


# --------------------------------------------------------------------------- #
# D-40: never actuates; every record is non-executable
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("over", [
    {},
    {"signals": {"needs_human": True}},
    {"signals": {"worker_stuck": True}},
    {"provider_verified": False},
    {"signals": {"ready_to_finish": False, "requirements_satisfied": False}},
])
def test_no_recorded_directive_is_ever_executable(tmp_path, over):
    store = _store(tmp_path)
    s = _sentinel(store)
    decision_obj = s.arbitrate_and_record(assessment=_assessment(**over), event_id="e1", agent="generic")
    assert decision_obj.acted is False
    # no stored row may ever claim it acted (D-40 falsifier, on-disk form)
    rows = _rows(store, limit=100)
    assert rows and all(r["acted"] == 0 for r in rows)


def test_blocked_decision_is_escalate_and_never_resuming(tmp_path):
    store = _store(tmp_path)
    s = _sentinel(store)
    # an unknown required signal -> EXACT_BLOCKER (absorbing)
    d = s.arbitrate_and_record(
        assessment=_assessment(signals={"needs_human": None}), event_id="e1", agent="generic"
    )
    assert d.action is DirectiveAction.ESCALATE
    assert d.blocked is True
    assert d.blocker_code == BlockerCode.EXACT_BLOCKER.value
    assert d.needs_human is True


def test_healthy_decision_maps_through_the_kernel(tmp_path):
    store = _store(tmp_path)
    s = _sentinel(store)
    d = s.arbitrate_and_record(assessment=_assessment(), event_id="e1", agent="generic")
    assert d.action is decision.decide_action(_assessment())
    assert d.blocked is False


# --------------------------------------------------------------------------- #
# D-34: budget exhaustion REPLACES with ESCALATE, never suppresses
# --------------------------------------------------------------------------- #
def test_budget_exhaustion_replaces_with_escalate(tmp_path):
    store = _store(tmp_path)
    s = _sentinel(store, limits=BudgetLimits(directives=0))
    d = s.arbitrate_and_record(assessment=_assessment(), event_id="e1", agent="generic")
    assert d.action is DirectiveAction.ESCALATE
    assert d.blocked is True
    assert d.blocker_code == BlockerCode.BUDGET_EXHAUSTED.value
    # the record is STILL written (replace, never suppress)
    rows = _rows(store)
    assert len(rows) == 1
    assert rows[0]["action"] == DirectiveAction.ESCALATE.value


def test_budget_counters_are_derived_from_the_real_store(tmp_path):
    """D-34: a store-backed governor reads DERIVED counters, not an in-memory ledger.

    Proves ``StateStore.budget_snapshot`` is wired and restart-safe: the count
    comes from a PERSISTED row, not from anything the governor remembers.
    """
    store = _store(tmp_path)
    with store.connection() as conn:
        conn.execute(
            "INSERT INTO assessments (session_id, timestamp, agent, model, provider, data) "
            "VALUES (?,?,?,?,?,?)",
            ("s-1", 1.0, "generic", "m", "mock", "{}"),
        )
        conn.commit()
    gov = BudgetGovernor(BudgetLimits(assess_calls=0), store=store, clock=time.time)
    d = gov.check("s-1", kind="assessment")
    assert d.allowed is False and d.code == "assess_calls"
    assert d.snapshot.assessments_used == 1   # derived from the persisted row


def test_budget_check_error_fails_closed():
    class _BrokenStore:
        @staticmethod
        def budget_snapshot(session_id):
            raise RuntimeError("boom")

    gov = BudgetGovernor(BudgetLimits(), store=_BrokenStore())
    decision_obj = gov.check("s", kind="directive")
    assert decision_obj.allowed is False
    assert decision_obj.code == "budget_check_error"


def test_budget_never_returns_continue_semantics():
    gov = BudgetGovernor(BudgetLimits(directives=0), store=None)
    first = gov.check("s", kind="directive")
    assert first.allowed is False
    assert first.code == CODE_DIRECTIVES


# --------------------------------------------------------------------------- #
# D-33: signing honesty
# --------------------------------------------------------------------------- #
def test_signing_honest_when_no_key(tmp_path):
    store = _store(tmp_path)
    s = _sentinel(store, key=None)
    d = s.arbitrate_and_record(assessment=_assessment(), event_id="e1", agent="generic")
    assert d.signed is False
    assert d.signature is None
    assert d.signature_status == "unavailable:no_key"
    (row,) = _rows(store)
    assert row["signed"] == 0 and row["signature"] is None


def test_require_signature_without_key_escalates(tmp_path):
    store = _store(tmp_path)
    s = _sentinel(store, key=None, require_signature=True)
    d = s.arbitrate_and_record(assessment=_assessment(), event_id="e1", agent="generic")
    assert d.action is DirectiveAction.ESCALATE
    assert d.blocker_code == BlockerCode.SIGNING_REQUIRED_UNAVAILABLE.value


def test_signing_with_key_is_verified(tmp_path):
    store = _store(tmp_path)
    s = _sentinel(store, key=b"k" * 16)
    d = s.arbitrate_and_record(assessment=_assessment(), event_id="e1", agent="generic")
    assert d.signed is True
    assert d.signature.startswith("sha256=")
    assert d.signature_verified is True
    assert d.signature_status == "signed_verified"


# --------------------------------------------------------------------------- #
# D-7/D-13/D-23: shadow tape integrity + mutable labels
# --------------------------------------------------------------------------- #
def test_shadow_chain_verifies_and_detects_tamper(tmp_path):
    store = _store(tmp_path)
    s = _sentinel(store)
    s.arbitrate_and_record(assessment=_assessment(), event_id="e1", agent="generic")
    s.arbitrate_and_record(assessment=_assessment(signals={"worker_stuck": True}), event_id="e2", agent="generic")
    ok, count = _chain(store)
    assert ok is True and count == 2

    with store.connection() as conn:
        conn.execute("UPDATE shadow_decisions SET action='CONTINUE' WHERE seq=1")
        conn.commit()
    ok2, bad = _chain(store)
    assert ok2 is False and bad == 1


def test_labels_are_mutable_and_off_the_chain(tmp_path):
    store = _store(tmp_path)
    s = _sentinel(store)
    s.arbitrate_and_record(assessment=_assessment(), event_id="e1", agent="generic")
    did = _rows(store)[0]["decision_id"]

    with store.connection() as conn:
        shadow_store.set_label(conn, did, source="human", value="warranted_pause", labeler="a")
        shadow_store.set_label(conn, did, source="human", value="false_finish", labeler="b")
        shadow_store.set_label(conn, did, source="oracle_next_event", value="escalate")
        labels = shadow_store.get_labels(conn, did)
    assert labels["human"]["label_value"] == "false_finish"   # UPSERT
    assert labels["oracle_next_event"]["label_value"] == "escalate"

    # labels live outside the chain: the tape still verifies
    ok, _ = _chain(store)
    assert ok is True


# --------------------------------------------------------------------------- #
# D-7/D-16: Metric evaluation
# --------------------------------------------------------------------------- #
def test_evaluation_recompute_matches_stored_for_clean_record(tmp_path):
    store = _store(tmp_path)
    s = _sentinel(store)
    s.arbitrate_and_record(assessment=_assessment(), event_id="e1", agent="generic")
    (row,) = _rows(store)
    assert evaluation.verify_decision(row) is True
    action, trace = evaluation.recompute_decision(row)
    assert action is DirectiveAction.FINISH
    assert trace == row["trace_hash"]


def test_calibration_reports_steer_as_deferred(tmp_path):
    store = _store(tmp_path)
    s = _sentinel(store)
    s.arbitrate_and_record(assessment=_assessment(), event_id="e1", agent="generic")
    with store.connection() as conn:
        report = evaluation.calibration_report(conn)
    assert report["steer_status"]["state"] == "DEFERRED"
    assert report["steer_status"]["reintroduction_trigger"] == "payload.goal_ref"
    assert report["mock_only_non_evidential"] == 1   # provider="mock" in our fixture


def test_integrity_report_covers_both_chains(tmp_path):
    store = _store(tmp_path)
    s = _sentinel(store)
    s.arbitrate_and_record(assessment=_assessment(), event_id="e1", agent="generic")
    with store.connection() as conn:
        audit_kernel.append_audit(conn, event_id="e1", timestamp=1.0, session_id="s-1", agent="generic", project="")
        report = evaluation.integrity_report(conn)
    assert report == {"audit_ok": True, "shadow_ok": True, "broken_at": None}


# --------------------------------------------------------------------------- #
# D-32: adapter is thin — same action as the kernel
# --------------------------------------------------------------------------- #
def test_adapter_action_equals_kernel_action():
    a = _assessment(signals={"worker_stuck": True})
    assert arbitration.arbitrate(a).action is decision.decide_action(a)
    assert arbitration.rule_table_actions() == decision.table_actions()
