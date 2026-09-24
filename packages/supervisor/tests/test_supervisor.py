"""Tests for the P3 supervisor plane (ADR-0008 D-7/D-13/D-34/D-35/D-40/D-41).

Named for the property falsified. The load-bearing ones are the D-40 shadow
invariant (never actuates; single dispatch site) and D-34 budget replacement.
"""
from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest

from jcc_core import audit as audit_kernel
from jcc_core import decision
from jcc_core.schemas import AgentName, Assessment, DirectiveAction
from supervisor import arbitration, evaluation, shadow_store
from supervisor.budget import BudgetGovernor, BudgetLimits, CODE_DIRECTIVES
from supervisor.ports import ShadowActuationPort
from supervisor.sentinel import BlockerCode, Sentinel
from jcc_core.signing import DirectiveSigner

HERE = Path(__file__).resolve()
SENTINEL_SRC = (HERE.parents[1] / "sentinel.py").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #
class _FakeStore:
    """Minimal store: exposes ``_conn`` so the Sentinel uses shadow_store."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn


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


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(audit_kernel.SCHEMA)
    shadow_store.ensure_schema(conn)
    return conn


def _sentinel(conn, *, key=b"k" * 16, require_signature=False, limits=None) -> Sentinel:
    return Sentinel(
        _FakeStore(conn),
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
def test_no_recorded_directive_is_ever_executable(over):
    conn = _conn()
    s = _sentinel(conn)
    decision_obj = s.arbitrate_and_record(assessment=_assessment(**over), event_id="e1", agent="generic")
    assert decision_obj.acted is False
    # no stored row may ever claim it acted (D-40 falsifier, on-disk form)
    rows = shadow_store.read_decisions(conn, limit=100)
    assert rows and all(r["acted"] == 0 for r in rows)


def test_blocked_decision_is_escalate_and_never_resuming():
    conn = _conn()
    s = _sentinel(conn)
    # an unknown required signal -> EXACT_BLOCKER (absorbing)
    d = s.arbitrate_and_record(
        assessment=_assessment(signals={"needs_human": None}), event_id="e1", agent="generic"
    )
    assert d.action is DirectiveAction.ESCALATE
    assert d.blocked is True
    assert d.blocker_code == BlockerCode.EXACT_BLOCKER.value
    assert d.needs_human is True


def test_healthy_decision_maps_through_the_kernel():
    conn = _conn()
    s = _sentinel(conn)
    d = s.arbitrate_and_record(assessment=_assessment(), event_id="e1", agent="generic")
    assert d.action is decision.decide_action(_assessment())
    assert d.blocked is False


# --------------------------------------------------------------------------- #
# D-34: budget exhaustion REPLACES with ESCALATE, never suppresses
# --------------------------------------------------------------------------- #
def test_budget_exhaustion_replaces_with_escalate():
    conn = _conn()
    s = _sentinel(conn, limits=BudgetLimits(directives=0))
    d = s.arbitrate_and_record(assessment=_assessment(), event_id="e1", agent="generic")
    assert d.action is DirectiveAction.ESCALATE
    assert d.blocked is True
    assert d.blocker_code == BlockerCode.BUDGET_EXHAUSTED.value
    # the record is STILL written (replace, never suppress)
    rows = shadow_store.read_decisions(conn)
    assert len(rows) == 1
    assert rows[0]["action"] == DirectiveAction.ESCALATE.value


def test_budget_check_error_fails_closed():
    class _BrokenStore:
        _conn = None

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
def test_signing_honest_when_no_key():
    conn = _conn()
    s = _sentinel(conn, key=None)
    d = s.arbitrate_and_record(assessment=_assessment(), event_id="e1", agent="generic")
    assert d.signed is False
    assert d.signature is None
    assert d.signature_status == "unavailable:no_key"
    (row,) = shadow_store.read_decisions(conn)
    assert row["signed"] == 0 and row["signature"] is None


def test_require_signature_without_key_escalates():
    conn = _conn()
    s = _sentinel(conn, key=None, require_signature=True)
    d = s.arbitrate_and_record(assessment=_assessment(), event_id="e1", agent="generic")
    assert d.action is DirectiveAction.ESCALATE
    assert d.blocker_code == BlockerCode.SIGNING_REQUIRED_UNAVAILABLE.value


def test_signing_with_key_is_verified():
    conn = _conn()
    s = _sentinel(conn, key=b"k" * 16)
    d = s.arbitrate_and_record(assessment=_assessment(), event_id="e1", agent="generic")
    assert d.signed is True
    assert d.signature.startswith("sha256=")
    assert d.signature_verified is True
    assert d.signature_status == "signed_verified"


# --------------------------------------------------------------------------- #
# D-7/D-13/D-23: shadow tape integrity + mutable labels
# --------------------------------------------------------------------------- #
def test_shadow_chain_verifies_and_detects_tamper():
    conn = _conn()
    s = _sentinel(conn)
    s.arbitrate_and_record(assessment=_assessment(), event_id="e1", agent="generic")
    s.arbitrate_and_record(assessment=_assessment(signals={"worker_stuck": True}), event_id="e2", agent="generic")
    ok, count = shadow_store.verify_shadow_chain(conn)
    assert ok is True and count == 2

    conn.execute("UPDATE shadow_decisions SET action='CONTINUE' WHERE seq=1")
    conn.commit()
    ok2, bad = shadow_store.verify_shadow_chain(conn)
    assert ok2 is False and bad == 1


def test_labels_are_mutable_and_off_the_chain():
    conn = _conn()
    s = _sentinel(conn)
    s.arbitrate_and_record(assessment=_assessment(), event_id="e1", agent="generic")
    did = shadow_store.read_decisions(conn)[0]["decision_id"]

    shadow_store.set_label(conn, did, source="human", value="warranted_pause", labeler="a")
    shadow_store.set_label(conn, did, source="human", value="false_finish", labeler="b")
    shadow_store.set_label(conn, did, source="oracle_next_event", value="escalate")
    labels = shadow_store.get_labels(conn, did)
    assert labels["human"]["label_value"] == "false_finish"   # UPSERT
    assert labels["oracle_next_event"]["label_value"] == "escalate"

    # labels live outside the chain: the tape still verifies
    ok, _ = shadow_store.verify_shadow_chain(conn)
    assert ok is True


# --------------------------------------------------------------------------- #
# D-7/D-16: Metric evaluation
# --------------------------------------------------------------------------- #
def test_evaluation_recompute_matches_stored_for_clean_record():
    conn = _conn()
    s = _sentinel(conn)
    s.arbitrate_and_record(assessment=_assessment(), event_id="e1", agent="generic")
    (row,) = shadow_store.read_decisions(conn)
    assert evaluation.verify_decision(row) is True
    action, trace = evaluation.recompute_decision(row)
    assert action is DirectiveAction.FINISH
    assert trace == row["trace_hash"]


def test_calibration_reports_steer_as_deferred():
    conn = _conn()
    s = _sentinel(conn)
    s.arbitrate_and_record(assessment=_assessment(), event_id="e1", agent="generic")
    report = evaluation.calibration_report(conn)
    assert report["steer_status"]["state"] == "DEFERRED"
    assert report["steer_status"]["reintroduction_trigger"] == "payload.goal_ref"
    assert report["mock_only_non_evidential"] == 1   # provider="mock" in our fixture


def test_integrity_report_covers_both_chains():
    conn = _conn()
    s = _sentinel(conn)
    s.arbitrate_and_record(assessment=_assessment(), event_id="e1", agent="generic")
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
