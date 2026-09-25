"""Capability probe: the MCP facade refuses gateway (mutation) tools unless the
underlying control capability is VERIFIED.

The "underlying capability" is the control plane's mutation authority. By default
we consult ``packages/jcc_core/adapters`` (read-only probes) plus the
control-token configuration, and synthesise a single control-capability verdict.
Tests inject a :class:`StubProbe` to make the verdict deterministic.
"""
from __future__ import annotations

import os
from typing import Any, Dict

CONTROL_TOKEN_ENV = "JCC_CONTROL_TOKEN"

# Status vocabulary mirrors jcc_core.adapters (_common): CONNECTED / DEGRADED /
# ADAPTER_DISABLED / UNVERIFIED. "VERIFIED" in the task means the synthesised
# control capability is both reachable and authorised (token configured + at
# least one verified agent adapter).
STATUS_VERIFIED = "CONNECTED"
STATUS_UNVERIFIED = "UNVERIFIED"
STATUS_LOCKED = "LOCKED_NO_TOKEN"


def _discover() -> Any:
    """Run the read-only agent adapter discovery. Returns the dict or None on failure."""
    try:
        import asyncio

        from jcc_core.adapters.discovery import discover_all

        return asyncio.run(discover_all())
    except Exception:
        return None


class CapabilityProbe:
    """Synthesises a control-capability verdict for a given gateway tool."""

    def verdict_for(self, tool_name: str) -> Dict[str, Any]:
        raise NotImplementedError


class StubProbe(CapabilityProbe):
    """Deterministic probe for tests."""

    def __init__(
        self,
        verified: bool,
        status: str = STATUS_VERIFIED,
        note: str = "",
    ) -> None:
        self._verified = verified
        self._status = status
        self._note = note

    def verdict_for(self, tool_name: str) -> Dict[str, Any]:
        return {
            "tool": tool_name,
            "verified": self._verified,
            "status": self._status,
            "note": self._note,
        }


class AdapterCapabilityProbe(CapabilityProbe):
    """Default probe: verifies the control capability from adapter discovery + token.

    Fail-closed: without a control token the plane is locked (401), so the control
    capability is not verified. Without at least one verified agent adapter the
    control surface is considered unverified too. Any discovery failure yields
    UNVERIFIED rather than a guessed "ok".
    """

    def __init__(self, token_env: str = CONTROL_TOKEN_ENV, cache: bool = True) -> None:
        self.token_env = token_env
        self._cache = cache
        self._cached: Any = None

    def _discovery(self) -> Any:
        if self._cache and self._cached is not None:
            return self._cached
        disc = _discover()
        if self._cache:
            self._cached = disc
        return disc

    def verdict_for(self, tool_name: str) -> Dict[str, Any]:
        token_present = bool(os.environ.get(self.token_env))
        disc = self._discovery()
        if disc is None:
            return {
                "tool": tool_name,
                "verified": False,
                "status": STATUS_UNVERIFIED,
                "note": "adapter discovery failed; control capability not verified",
            }
        statuses = disc.get("statuses", {}) or {}
        connected = [a for a, s in statuses.items() if s == STATUS_VERIFIED]
        if not token_present:
            return {
                "tool": tool_name,
                "verified": False,
                "status": STATUS_LOCKED,
                "note": "control token not configured (control plane locked)",
                "adapters": statuses,
            }
        if not connected:
            return {
                "tool": tool_name,
                "verified": False,
                "status": STATUS_UNVERIFIED,
                "note": "no verified agent adapter; control capability not verified",
                "adapters": statuses,
            }
        return {
            "tool": tool_name,
            "verified": True,
            "status": STATUS_VERIFIED,
            "note": "control capability verified from token + adapter discovery",
            "adapters": statuses,
        }
