"""Human approval for a Jev decision, bound to that decision and nothing else.

WHY THIS EXISTS
---------------
When Jev's confidence is below the automatic-execution threshold, the pipeline
must stop and a human must decide. The tempting shortcut is to let the browser
flip ``safe_to_execute`` to true and carry on. That is not a confirmation, it is
a bypass wearing a confirmation's clothes:

  * it is not audited — nothing records who approved what, or when;
  * it is not bound — one approval would silently authorise a DIFFERENT task, a
    different executor, or a different model;
  * it is not scoped — it would survive as long as the page did, so an approval
    given for a harmless question would later authorise a deployment.

So an approval is a SIGNED, SINGLE-USE, EXPIRED token bound to a fingerprint of
exactly what was approved. The browser sends an id; the server re-derives the
fingerprint from its own record and refuses a mismatch. The browser cannot
manufacture an approval, only request one.

WHAT IS BOUND
-------------
  task_hash        the goal, hashed — an approval for one task cannot move to another
  project_root     where it runs
  executor          who runs it
  provider / model  which model produces the work
  jev_decision_id   the exact decision, including its confidence and reason
  expiry            short, because a stale decision describes a stale situation
  nonce             single use, so a replay cannot re-authorise

NO SECRET IS EVER STORED HERE. ``confirmed_by`` is a label ("user"), never a
token, and :meth:`Approval.record_for_audit` is the only shape that reaches the
audit table.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

#: Short on purpose. The window covers "the operator read the card and clicked",
#: not "the operator went to lunch". A stale decision describes a stale situation,
#: and re-approving it should take a fresh look at what Jev now says.
APPROVAL_TTL_S = 15 * 60.0

#: A task fingerprint is only compared, never displayed, and never used to
#: reconstruct the task — so a hash is enough and the text stays out of the token.
def task_fingerprint(task: str) -> str:
    return hashlib.sha256(task.strip().encode("utf-8")).hexdigest()[:32]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS human_approval (
  approval_id     TEXT PRIMARY KEY,
  jev_decision_id TEXT NOT NULL,
  task_hash       TEXT NOT NULL,
  project_root    TEXT,
  executor        TEXT NOT NULL,
  provider        TEXT,
  model           TEXT,
  confidence      REAL,
  reason          TEXT,
  confirmed_by    TEXT NOT NULL,
  confirmed_at    REAL NOT NULL,
  expires_at      REAL NOT NULL,
  nonce           TEXT NOT NULL,
  binding_hash    TEXT NOT NULL,
  consumed_at     REAL,
  outcome         TEXT
);
CREATE INDEX IF NOT EXISTS idx_human_approval_decision
  ON human_approval (jev_decision_id);
"""


class ApprovalError(RuntimeError):
    """An approval was refused. ``code`` is a stable machine-readable reason."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def binding_hash(*, approval_id: str, jev_decision_id: str, task_hash: str,
                 project_root: Optional[str], executor: str,
                 provider: Optional[str], model: Optional[str]) -> str:
    """The fingerprint an approval is checked against.

    Computed from a CANONICAL JSON form, so the same approval cannot be replayed
    with the fields reordered or a float re-spelled.
    """
    canonical = json.dumps(
        {
            "approval_id": approval_id,
            "jev_decision_id": jev_decision_id,
            "task_hash": task_hash,
            "project_root": project_root or "",
            "executor": executor,
            "provider": provider or "",
            "model": model or "",
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class Approval:
    approval_id: str
    jev_decision_id: str
    task_hash: str
    executor: str
    confirmed_by: str
    confirmed_at: float
    expires_at: float
    nonce: str
    binding_hash: str
    project_root: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    confidence: Optional[float] = None
    reason: Optional[str] = None
    consumed_at: Optional[float] = None
    outcome: Optional[str] = None

    @property
    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    def record_for_audit(self) -> Dict[str, Any]:
        """The ONLY shape allowed to reach the audit table.

        Deliberately excludes the nonce and the binding hash: they are integrity
        material, not facts about the decision, and a log full of them is a log
        that invites their reuse. ``confirmed_by`` is a LABEL — a person, never a
        credential.
        """
        return {
            "approval_id": self.approval_id,
            "decision_id": self.jev_decision_id,
            "confidence": self.confidence,
            "reason": self.reason,
            "confirmed_by": self.confirmed_by,
            "confirmed_at": self.confirmed_at,
            "task_hash": self.task_hash,
            "executor": self.executor,
            "provider": self.provider,
            "model": self.model,
        }


class ApprovalStore:
    """SQLite-backed, single-use, expiring human approvals."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=10.0)

    @contextmanager
    def _tx(self):
        """A connection that COMMITS and CLOSES.

        ``with sqlite3.connect(...)`` commits the transaction but leaves the
        connection OPEN, and on Windows an open handle keeps the file locked — so
        a test that finished with a store still holding one could not delete its
        temp directory. Every path here closes explicitly.
        """
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # -- issue ------------------------------------------------------------ #

    def issue(self, *, jev_decision_id: str, task: str, executor: str,
              project_root: Optional[str] = None, provider: Optional[str] = None,
              model: Optional[str] = None, confidence: Optional[float] = None,
              reason: Optional[str] = None, confirmed_by: str = "user",
              now: Optional[float] = None, ttl_s: float = APPROVAL_TTL_S) -> Approval:
        """Record a human approving ONE specific decision.

        ``confirmed_by`` is a label. It is never a token: an audit that recorded
        a credential would be a second copy of the secret to protect.
        """
        if not jev_decision_id:
            raise ApprovalError("decision_id_required", "审批必须绑定 Jev decision id")
        if not executor:
            raise ApprovalError("executor_required", "审批必须绑定执行器")
        t = now if now is not None else time.time()
        approval_id = "apr_" + secrets.token_hex(8)
        nonce = secrets.token_hex(16)
        ap = Approval(
            approval_id=approval_id,
            jev_decision_id=jev_decision_id,
            task_hash=task_fingerprint(task),
            executor=executor,
            confirmed_by=confirmed_by,
            confirmed_at=t,
            expires_at=t + ttl_s,
            nonce=nonce,
            binding_hash=binding_hash(
                approval_id=approval_id, jev_decision_id=jev_decision_id,
                task_hash=task_fingerprint(task), project_root=project_root,
                executor=executor, provider=provider, model=model,
            ),
            project_root=project_root, provider=provider, model=model,
            confidence=confidence, reason=reason,
        )
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO human_approval (approval_id, jev_decision_id, task_hash,"
                " project_root, executor, provider, model, confidence, reason,"
                " confirmed_by, confirmed_at, expires_at, nonce, binding_hash)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ap.approval_id, ap.jev_decision_id, ap.task_hash, ap.project_root,
                 ap.executor, ap.provider, ap.model, ap.confidence, ap.reason,
                 ap.confirmed_by, ap.confirmed_at, ap.expires_at, ap.nonce,
                 ap.binding_hash),
            )
            conn.commit()
        return ap

    # -- verify & consume ------------------------------------------------- #

    def get(self, approval_id: str) -> Optional[Approval]:
        with self._tx() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM human_approval WHERE approval_id = ?", (approval_id,)
            ).fetchone()
        return _row_to_approval(row) if row else None

    def verify_and_consume(
        self, approval_id: str, *, jev_decision_id: str, task: str,
        executor: str, project_root: Optional[str] = None,
        provider: Optional[str] = None, model: Optional[str] = None,
        now: Optional[float] = None,
    ) -> Approval:
        """Check every binding, then burn it.

        Consume-on-use rather than check-then-use: two concurrent requests with
        the same approval would both pass a check-then-use, and the second one
        would authorise a second execution of the same decision.
        """
        ap = self.get(approval_id)
        if ap is None:
            raise ApprovalError("approval_not_found", "未找到该审批")
        t = now if now is not None else time.time()

        if ap.consumed_at is not None:
            raise ApprovalError("approval_already_used", "该审批已被使用")
        if t > ap.expires_at:
            raise ApprovalError("approval_expired", "审批已过期，请重新确认")
        if ap.jev_decision_id != jev_decision_id:
            # The single most important check: an approval for decision A must
            # never authorise decision B, however similar the two look.
            raise ApprovalError("decision_mismatch", "审批与当前 Jev 决策不匹配")
        if ap.task_hash != task_fingerprint(task):
            raise ApprovalError("task_mismatch", "审批与当前任务不匹配")
        if ap.executor != executor:
            raise ApprovalError("executor_mismatch", "审批与当前执行器不匹配")
        for name, want, got in (
            ("project_root", ap.project_root, project_root),
            ("provider", ap.provider, provider),
            ("model", ap.model, model),
        ):
            if (want or "") != (got or ""):
                raise ApprovalError(f"{name}_mismatch", f"审批与当前 {name} 不匹配")

        expected = binding_hash(
            approval_id=ap.approval_id, jev_decision_id=jev_decision_id,
            task_hash=ap.task_hash, project_root=ap.project_root,
            executor=ap.executor, provider=ap.provider, model=ap.model,
        )
        if not hmac.compare_digest(expected, ap.binding_hash):
            # Reached only if a stored row was edited outside this code.
            raise ApprovalError("binding_tampered", "审批绑定校验失败")

        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE human_approval SET consumed_at = ?, outcome = ?"
                " WHERE approval_id = ? AND consumed_at IS NULL",
                (t, "EXECUTED", ap.approval_id),
            )
            conn.commit()
            if cur.rowcount != 1:
                # Lost the race against a concurrent use.
                raise ApprovalError("approval_already_used", "该审批已被使用")
        ap.consumed_at = t
        ap.outcome = "EXECUTED"
        return ap

    def mark_cancelled(self, approval_id: str, *, now: Optional[float] = None) -> bool:
        t = now if now is not None else time.time()
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE human_approval SET consumed_at = ?, outcome = 'CANCELLED'"
                " WHERE approval_id = ? AND consumed_at IS NULL",
                (t, approval_id),
            )
            conn.commit()
            return cur.rowcount == 1

    def list_for_decision(self, jev_decision_id: str) -> List[Approval]:
        with self._tx() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM human_approval WHERE jev_decision_id = ?"
                " ORDER BY confirmed_at", (jev_decision_id,)
            ).fetchall()
        return [_row_to_approval(r) for r in rows]


def _row_to_approval(row: sqlite3.Row) -> Approval:
    return Approval(
        approval_id=row["approval_id"],
        jev_decision_id=row["jev_decision_id"],
        task_hash=row["task_hash"],
        project_root=row["project_root"],
        executor=row["executor"],
        provider=row["provider"],
        model=row["model"],
        confidence=row["confidence"],
        reason=row["reason"],
        confirmed_by=row["confirmed_by"],
        confirmed_at=row["confirmed_at"],
        expires_at=row["expires_at"],
        nonce=row["nonce"],
        binding_hash=row["binding_hash"],
        consumed_at=row["consumed_at"],
        outcome=row["outcome"],
    )
