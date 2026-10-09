"""Human approval must be bound, single-use, expiring and audited.

THE BYPASS THIS PREVENTS
------------------------
When Jev's confidence is below the automatic-execution threshold, the pipeline
stops. The tempting shortcut is to let the browser set ``safe_to_execute`` to
true and continue. That is not a confirmation:

  * it is unaudited — nothing records who approved what;
  * it is unbound — one approval silently authorises a different task;
  * it is replayable — it survives as long as the page does.

So an approval is a bound, expiring, single-use token, and the SERVER re-derives
every binding from what it holds rather than from the request body.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time

import pytest

from jcc_core.approvals import (
    APPROVAL_TTL_S,
    ApprovalError,
    ApprovalStore,
    binding_hash,
    task_fingerprint,
)

TASK = "run the dual model test"


@pytest.fixture()
def store():
    # A plain temp dir rather than tmp_path: pytest cannot delete tmp_path while
    # SQLite's WAL sidecar files are still attached, and the resulting
    # PermissionError is reported against a test that PASSED — which is a very
    # confusing way to learn that a fixture owns a file handle.
    d = tempfile.mkdtemp()
    s = ApprovalStore(os.path.join(d, "approvals.db"))
    try:
        yield s
    finally:
        # Checkpoint and drop the WAL so the directory can be removed.
        with s._tx() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        shutil.rmtree(d, ignore_errors=True)


def _issue(store, **over):
    kw = dict(
        jev_decision_id="jev-dec-1", task=TASK, executor="workbuddy",
        project_root="D:\\jev", provider="minimax", model="MiniMax-M3",
        confidence=0.37, reason="below threshold",
    )
    kw.update(over)
    return store.issue(**kw)


def _consume(store, ap, **over):
    kw = dict(
        jev_decision_id=ap.jev_decision_id, task=TASK, executor=ap.executor,
        project_root=ap.project_root, provider=ap.provider, model=ap.model,
    )
    kw.update(over)
    return store.verify_and_consume(ap.approval_id, **kw)


# --------------------------------------------------------------------------- #
# The happy path
# --------------------------------------------------------------------------- #


class TestApprovalWorks:
    def test_a_matching_confirmation_is_accepted(self, store):
        ap = _issue(store)
        used = _consume(store, ap)
        assert used.approval_id == ap.approval_id
        assert used.outcome == "EXECUTED"
        assert used.consumed_at is not None

    def test_the_approval_carries_what_was_approved(self, store):
        ap = _issue(store)
        rec = ap.record_for_audit()
        assert rec["executor"] == "workbuddy"
        assert rec["provider"] == "minimax"
        assert rec["model"] == "MiniMax-M3"
        assert rec["confidence"] == 0.37
        assert rec["confirmed_by"] == "user"

    def test_the_audit_shape_hides_integrity_material(self, store):
        """The nonce and binding hash are integrity material, not facts about the
        decision. A log full of them invites their reuse."""
        rec = _issue(store).record_for_audit()
        assert "nonce" not in rec
        assert "binding_hash" not in rec
        assert "api_key" not in rec and "token" not in rec

    def test_the_task_hash_does_not_reconstruct_the_task(self, store):
        """Hashed so an approval id on its own reveals nothing about the work."""
        h = task_fingerprint(TASK)
        assert TASK not in h
        assert task_fingerprint(TASK + " ") == h      # whitespace-insensitive
        assert task_fingerprint(TASK + "x") != h


# --------------------------------------------------------------------------- #
# Binding — the core of the fix
# --------------------------------------------------------------------------- #


class TestApprovalIsBound:
    def test_it_cannot_move_to_another_decision(self, store):
        """The most important check: an approval for decision A must never
        authorise decision B, however similar the two look."""
        ap = _issue(store)
        with pytest.raises(ApprovalError) as e:
            _consume(store, ap, jev_decision_id="jev-dec-2")
        assert e.value.code == "decision_mismatch"

    def test_it_cannot_move_to_another_task(self, store):
        ap = _issue(store)
        with pytest.raises(ApprovalError) as e:
            _consume(store, ap, task="deploy to production")
        assert e.value.code == "task_mismatch"

    def test_it_cannot_move_to_another_executor(self, store):
        ap = _issue(store)
        with pytest.raises(ApprovalError) as e:
            _consume(store, ap, executor="claude")
        assert e.value.code == "executor_mismatch"

    @pytest.mark.parametrize("field,other", [
        ("project_root", "C:\\other"),
        ("provider", "deepseek"),
        ("model", "some-other-model"),
    ])
    def test_it_cannot_widen_its_own_scope(self, store, field, other):
        ap = _issue(store)
        with pytest.raises(ApprovalError) as e:
            _consume(store, ap, **{field: other})
        assert e.value.code == f"{field}_mismatch"

    def test_a_failed_binding_does_not_burn_the_approval(self, store):
        """A mismatch means the operator or the client got something wrong, not
        that the decision changed. Burning it would force a pointless re-approval.
        """
        ap = _issue(store)
        with pytest.raises(ApprovalError):
            _consume(store, ap, task="something else")
        # The original is still usable.
        assert _consume(store, ap).outcome == "EXECUTED"


# --------------------------------------------------------------------------- #
# Single use, expiry, tamper
# --------------------------------------------------------------------------- #


class TestApprovalCannotBeReplayed:
    def test_a_second_use_is_refused(self, store):
        ap = _issue(store)
        _consume(store, ap)
        with pytest.raises(ApprovalError) as e:
            _consume(store, ap)
        assert e.value.code == "approval_already_used"

    def test_an_expired_approval_is_refused(self, store):
        ap = _issue(store, ttl_s=1.0)
        with pytest.raises(ApprovalError) as e:
            _consume(store, ap, now=time.time() + 5)
        assert e.value.code == "approval_expired"

    def test_the_ttl_is_short_by_design(self):
        """It covers 'read the card and clicked', not 'went to lunch'. A stale
        decision describes a stale situation."""
        assert APPROVAL_TTL_S <= 30 * 60

    def test_an_unknown_id_is_refused(self, store):
        with pytest.raises(ApprovalError) as e:
            store.verify_and_consume(
                "apr_nope", jev_decision_id="d", task=TASK, executor="workbuddy"
            )
        assert e.value.code == "approval_not_found"

    def test_a_tampered_row_is_refused(self, store):
        """Reached only if the stored row was edited outside this code — which is
        exactly the case worth refusing rather than trusting."""
        import sqlite3

        ap = _issue(store)
        with sqlite3.connect(store.db_path) as conn:
            conn.execute(
                "UPDATE human_approval SET executor = ? WHERE approval_id = ?",
                ("claude", ap.approval_id),
            )
            conn.commit()
        with pytest.raises(ApprovalError) as e:
            store.verify_and_consume(
                ap.approval_id, jev_decision_id=ap.jev_decision_id, task=TASK,
                executor="workbuddy", project_root=ap.project_root,
                provider=ap.provider, model=ap.model,
            )
        assert e.value.code in ("binding_tampered", "executor_mismatch")

    def test_cancelling_burns_it(self, store):
        ap = _issue(store)
        assert store.mark_cancelled(ap.approval_id) is True
        with pytest.raises(ApprovalError) as e:
            _consume(store, ap)
        assert e.value.code == "approval_already_used"


# --------------------------------------------------------------------------- #
# Issuance preconditions
# --------------------------------------------------------------------------- #


class TestIssueRequiresABinding:
    def test_a_decision_id_is_required(self, store):
        with pytest.raises(ApprovalError) as e:
            _issue(store, jev_decision_id="")
        assert e.value.code == "decision_id_required"

    def test_an_executor_is_required(self, store):
        """An approval with no executor authorises nothing concrete."""
        with pytest.raises(ApprovalError) as e:
            _issue(store, executor="")
        assert e.value.code == "executor_required"

    def test_the_binding_hash_is_canonical(self):
        """Field order must not change the fingerprint, or a re-ordered request
        would produce a different value and defeat the check."""
        a = binding_hash(approval_id="a", jev_decision_id="d", task_hash="t",
                         project_root="p", executor="e", provider="v", model="m")
        b = binding_hash(approval_id="a", jev_decision_id="d", task_hash="t",
                         project_root="p", executor="e", provider="v", model="m")
        assert a == b
        c = binding_hash(approval_id="a", jev_decision_id="d", task_hash="t",
                         project_root="p", executor="e", provider="v", model="OTHER")
        assert a != c

    def test_history_is_listable_for_the_decision(self, store):
        ap = _issue(store)
        _consume(store, ap)
        rows = store.list_for_decision("jev-dec-1")
        assert len(rows) == 1
        assert rows[0].outcome == "EXECUTED"
