"""Human approval records (the ``human_approval`` gate's data side).

ADR-0008 D-34/D-35: a directive carrying ``requires_human`` is a request for a
person, never a machine instruction. The flag itself already lives on the
authoritative ``Directive`` schema (``schemas.Directive.requires_human``); what
does not exist anywhere in the tree yet is somewhere to record THAT A HUMAN
SAID YES, and for which directive. This module is that record.

Deliberate properties:
* An approval names the ``directive_id`` it approves. An approval that does not
  name a directive approves nothing.
* It is written only by an explicit ``approve()`` call. Nothing in the tool path
  can create one, and no tool exposes an approval argument (``manifest.TOOLS``
  pins the gateway argument lists to ``("session_id",)``) -- so a caller cannot
  grant itself an approval by passing one.
* The record is human-legible JSONL, append-only, and never holds a token.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .trace import REDACTED


@dataclass(frozen=True)
class Approval:
    directive_id: str
    session_id: str
    approver: str
    approved_at: float
    note: str = ""

    def matches(self, directive_id: str, session_id: str) -> bool:
        return self.directive_id == directive_id and self.session_id == session_id

    def as_dict(self) -> Dict[str, Any]:
        return {
            "directive_id": self.directive_id,
            "session_id": self.session_id,
            "approver": self.approver,
            "approved_at": self.approved_at,
            "note": self.note,
        }


class ApprovalRegistry:
    """Append-only approval tape keyed by ``directive_id`` + ``session_id``."""

    def __init__(self, path: "str | Path" = "runtime/jcc/approvals.jsonl") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._records: List[Approval] = []
        self._reload()

    def _reload(self) -> None:
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                self._records.append(
                    Approval(
                        directive_id=str(data.get("directive_id", "")),
                        session_id=str(data.get("session_id", "")),
                        approver=str(data.get("approver", "unknown")),
                        approved_at=float(data.get("approved_at", 0.0)),
                        note=str(data.get("note", "")),
                    )
                )

    @staticmethod
    def _scrub(note: str) -> str:
        return note.replace("token=", f"token={REDACTED}")

    def approve(
        self, directive_id: str, session_id: str, approver: str, note: str = ""
    ) -> Approval:
        """Record one human approval. Requires an explicit human identity."""
        approval = Approval(
            directive_id=str(directive_id),
            session_id=str(session_id),
            approver=str(approver),
            approved_at=time.time(),
            note=self._scrub(note),
        )
        self._records.append(approval)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(approval.as_dict(), sort_keys=True) + "\n")
                fh.flush()
        except OSError:
            # Losing the durable copy is recorded in memory; the in-memory tape is
            # what the gate consults this process, and a missing file means the
            # operator can pre-approve again rather than be silently approved.
            pass
        return approval

    def find(self, directive_id: str, session_id: str) -> Optional[Approval]:
        for record in reversed(self._records):
            if record.matches(directive_id, session_id):
                return record
        return None

    def holds(self, directive_id: str, session_id: str) -> bool:
        return self.find(directive_id, session_id) is not None

    def all(self) -> List[Approval]:
        return list(self._records)
