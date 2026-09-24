"""Adapter package: four-agent contract (UNVERIFIED stubs) + discovery."""
from .base import AgentAdapter
from .claude import ClaudeAdapter
from .codex import CodexAdapter
from .discovery import all_adapters, discover_all
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
]
