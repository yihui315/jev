"""Actuator — fail-closed refusing stub (ADR-0004: actuation OFF P0-P2).

Core safety guarantee: this actuator refuses ALL real side-effects. It never
runs a shell, opens a browser, deletes, sends externally, pushes git, or writes
to a production DB. Every attempted real action is logged as DENIED and returns
``{"acted": False, "reason": "actuation_disabled"}`` (HTTP 200 by the server).

Real actuation is P3+ shadow-only and requires human approval; it is NOT
implemented here. ``can_act`` returns False for every action while disabled.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from .schemas import Directive

logger = logging.getLogger("jcc.actuator")

# The exhaustive set of side-effects this control plane must NEVER perform in P1.
FORBIDDEN_EFFECTS = (
    "shell_exec",
    "browser_open",
    "file_delete",
    "external_send",
    "git_push",
    "prod_db_write",
    "network_bind",
)


class Actuator:
    def __init__(
        self,
        enabled: bool = False,
        allowed_actions: Optional[list[str]] = None,
        audit_cb: Optional[Callable[[str], None]] = None,
    ):
        self.enabled = enabled
        self.allowed_actions = set(allowed_actions or [])
        self.audit_cb = audit_cb

    def can_act(self, directive: Directive) -> bool:
        """P1: always False. Real actuation is disabled (ADR-0004)."""
        if not self.enabled:
            return False
        if directive.action.value not in self.allowed_actions:
            return False
        return True

    def execute(self, directive: Directive) -> dict:
        if not self.enabled:
            return self._deny(directive, "actuation_disabled")
        if directive.action.value not in self.allowed_actions:
            return self._deny(directive, "action_not_allowed")
        # P3+ real actuation would be implemented here and ONLY reached when
        # explicitly enabled + allowed. Deliberately absent in P1.
        return self._deny(directive, "real_actuation_not_implemented")

    # -- engine actuation port (ADR-0008 D-27 / D-41) -----------------------
    def dispatch(self, directive: Directive) -> dict:
        """Implement ``jcc_core.ports.ActuationPort`` — the ENGINE seam.

        PERMANENTLY INERT (D-27 / D-41): verify-only, never a side-effect,
        and never an attach point (a real actuator attaches at
        ``Sentinel._dispatch`` instead). ``CoreEngine.ingest`` step 6 calls
        this, NOT :meth:`execute`; the ingest path can no longer reach
        ``execute`` at all. Always returns ``acted=False`` with
        ``reason="shadow_mode"`` regardless of ``enabled``.
        """
        return {
            "acted": False,
            "executable": False,
            "reason": "shadow_mode",
            "directive_id": directive.directive_id,
        }

    def _deny(self, directive: Directive, reason: str) -> dict:
        msg = (
            f"DENIED actuation action={directive.action.value} "
            f"directive={directive.directive_id} reason={reason}"
        )
        logger.warning(msg)
        if self.audit_cb:
            try:
                self.audit_cb(msg)
            except Exception:  # noqa: BLE001
                pass
        return {
            "acted": False,
            "reason": reason,
            "directive_id": directive.directive_id,
        }
