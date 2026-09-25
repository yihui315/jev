"""MCP facade -- a THIN transport surface over the Operator Skill.

It owns NO authority:

* tool definitions come from ``operator_skill.manifest`` (single source of truth);
* tool execution is delegated to ``operator_skill.OperatorSkill.call``;
* gateway (mutation) tools add exactly ONE extra pre-check -- refuse when the
  control capability is not VERIFIED -- and then delegate to the same
  ``OperatorSkill`` path the skill itself re-validates through its five gates.

The control token is carried through to the API (and the Operator Skill's
backend) as a header and is never logged; the Operator Skill's trace writer
redacts it by construction.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from operator_skill.manifest import TOOLS, spec
from operator_skill.refusals import CODE_UNKNOWN_TOOL
from operator_skill.skill import OperatorSkill

from .capability import CapabilityProbe, AdapterCapabilityProbe

logger = logging.getLogger("jcc.mcp_facade")

CODE_CAPABILITY_UNVERIFIED = "CAPABILITY_UNVERIFIED"
GATE_CONTROL_CAPABILITY = "control_capability"


@dataclass
class McpContent:
    type: str = "text"
    text: str = ""


@dataclass
class McpToolResult:
    is_error: bool
    content: List[McpContent] = field(default_factory=list)
    structured: Dict[str, Any] = field(default_factory=dict)


class McpFacade:
    """Thin MCP surface over the Operator Skill."""

    def __init__(
        self,
        skill: OperatorSkill,
        probe: Optional[CapabilityProbe] = None,
        principal: Optional[Any] = None,
    ) -> None:
        self.skill = skill
        self.probe = probe if probe is not None else AdapterCapabilityProbe()
        self.principal = principal  # None => OperatorSkill uses its default (observer)

    # -- tool listing ----------------------------------------------------- #
    def list_mcp_tools(self) -> List[Dict[str, Any]]:
        tools: List[Dict[str, Any]] = []
        for name, s in TOOLS.items():
            tools.append(
                {
                    "name": name,
                    "description": s.description,
                    "mode": s.mode,  # read | gateway -- declared machine-readably
                    "inputSchema": self._input_schema(s),
                    "annotations": {
                        "mode": s.mode,
                        "capabilities": list(s.capabilities),
                        "requires_approval": s.requires_human_approval,
                        "can_actuate": s.can_actuate,
                    },
                }
            )
        return tools

    @staticmethod
    def _input_schema(s: Any) -> Dict[str, Any]:
        props: Dict[str, Any] = {}
        required: List[str] = []
        for arg in s.arguments:
            props[arg] = {"type": "string", "title": arg}
            if arg == "session_id":
                required.append(arg)
        return {"type": "object", "properties": props, "required": required}

    # -- tool call -------------------------------------------------------- #
    def call_tool(
        self, name: str, arguments: Optional[Dict[str, Any]] = None
    ) -> McpToolResult:
        # Declared unknown tool -> explicit error (never silently succeed).
        try:
            s = spec(name)
        except KeyError:
            logger.info("mcp call tool=%s -> unknown", name)
            return McpToolResult(
                is_error=True,
                content=[
                    McpContent(
                        text=json.dumps(
                            {
                                "status": "refused",
                                "code": CODE_UNKNOWN_TOOL,
                                "tool": name,
                                "message": f"unknown tool {name!r}",
                            },
                            default=str,
                        )
                    )
                ],
                structured={
                    "tool": name,
                    "mode": "",
                    "ok": False,
                    "status": "refused",
                    "outcome": {
                        "status": "refused",
                        "code": CODE_UNKNOWN_TOOL,
                        "tool": name,
                        "message": f"unknown tool {name!r}",
                    },
                },
            )

        # Gateway pre-check: refuse unless the underlying capability is VERIFIED.
        # This is the ONLY extra gate the facade adds; the Operator Skill still
        # re-validates every gateway call through its own five gates.
        if s.mode == "gateway":
            verdict = self.probe.verdict_for(name)
            if not verdict.get("verified"):
                logger.info(
                    "mcp call tool=%s mode=gateway -> refused (capability %s)",
                    name,
                    verdict.get("status"),
                )
                return McpToolResult(
                    is_error=True,
                    content=[
                        McpContent(
                            text=json.dumps(
                                {
                                    "status": "refused",
                                    "code": CODE_CAPABILITY_UNVERIFIED,
                                    "gate": GATE_CONTROL_CAPABILITY,
                                    "tool": name,
                                    "message": (
                                        f"control capability not verified "
                                        f"({verdict.get('status')}); refusing gateway "
                                        f"tool before it reaches the control plane"
                                    ),
                                    "details": verdict,
                                },
                                default=str,
                            )
                        )
                    ],
                    structured={
                        "tool": name,
                        "mode": s.mode,
                        "ok": False,
                        "status": "refused",
                        "outcome": {
                            "status": "refused",
                            "code": CODE_CAPABILITY_UNVERIFIED,
                            "gate": GATE_CONTROL_CAPABILITY,
                            "tool": name,
                            "message": (
                                f"control capability not verified "
                                f"({verdict.get('status')}); refusing gateway tool "
                                f"before it reaches the control plane"
                            ),
                            "details": verdict,
                        },
                    },
                )

        # Delegate to the Operator Skill (no duplicated authority).
        result = self.skill.call(name, arguments, principal=self.principal)
        # Log at tool-granularity only; never the token, never secret arguments.
        logger.info("mcp call tool=%s mode=%s -> %s", name, s.mode, result.status)
        return McpToolResult(
            is_error=not result.ok,
            content=[McpContent(text=json.dumps(result.as_dict(), default=str))],
            structured=result.as_dict(),
        )

    # -- capabilities manifest ------------------------------------------- #
    def capabilities_manifest(self) -> Dict[str, Any]:
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
            "source": "operator_skill.manifest.TOOLS",
            "tools": tools,
        }
