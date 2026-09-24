"""Adapter package: four-agent contract (P2: real probes + normalizers)."""
from ..redaction import redact
from ._common import (
    STATUS_ADAPTER_DISABLED,
    STATUS_CONNECTED,
    STATUS_DEGRADED,
    STATUS_UNVERIFIED,
    UnidentifiableEvent,
)
from .base import AgentAdapter
from .claude import ClaudeAdapter
from .codex import CodexAdapter
from .discovery import adapter_for, all_adapters, discover_all
from .hermes import HermesAdapter
from .workbuddy import WorkBuddyAdapter

__all__ = [
    "AgentAdapter",
    "WorkBuddyAdapter",
    "ClaudeAdapter",
    "CodexAdapter",
    "HermesAdapter",
    "discover_all",
    "all_adapters",
    "adapter_for",
    "UnidentifiableEvent",
    "redact",
    "STATUS_CONNECTED",
    "STATUS_DEGRADED",
    "STATUS_ADAPTER_DISABLED",
    "STATUS_UNVERIFIED",
]
