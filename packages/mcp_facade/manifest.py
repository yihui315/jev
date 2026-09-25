"""Build the machine-readable capabilities manifest (``mcp_facade_capabilities.json``).

The gate reads capabilities without guessing: the manifest is derived directly
from the Operator Skill's single source of truth (``operator_skill.manifest``),
so the facade never invents a tool, a mode, or a capability.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict

from operator_skill.manifest import TOOLS


def build_capabilities_manifest() -> Dict[str, Any]:
    """Return ``name -> {mode, capabilities, requires_approval, ...}`` for every tool."""
    tools: Dict[str, Any] = {}
    for name, s in TOOLS.items():
        tools[name] = {
            "mode": s.mode,  # read | gateway
            "capabilities": list(s.capabilities),
            "requires_approval": s.requires_human_approval,
            "can_actuate": s.can_actuate,
            "requires_control_token": s.requires_control_token,
        }
    return {
        "schema_version": "jcc-mcp-capabilities/1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "operator_skill.manifest.TOOLS",
        "protocol": "mcp",
        "tools": tools,
    }


def write_capabilities_manifest(path: str) -> Dict[str, Any]:
    data = build_capabilities_manifest()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    return data
