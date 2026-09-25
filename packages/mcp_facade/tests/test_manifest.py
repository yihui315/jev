"""MANIFEST -- the tool manifest declares mode (read|gateway) per tool.

The gate reads capabilities without guessing: the manifest is derived from the
Operator Skill's single source of truth, so the facade never invents a tool, a
mode, or a capability.
"""
from __future__ import annotations

import json

from mcp_facade.facade import McpFacade
from mcp_facade.manifest import (
    build_capabilities_manifest,
    write_capabilities_manifest,
)

from helpers import make_recording_backend, make_skill

GATEWAY_TOOLS = ("jcc_pause", "jcc_steer", "jcc_retry")


class TestManifestDeclaresMode:
    def test_list_mcp_tools_declares_mode(self, trace_path, verified_probe):
        skill = make_skill(trace_path, backend=make_recording_backend())
        facade = McpFacade(skill, probe=verified_probe)
        tools = facade.list_mcp_tools()
        assert len(tools) == 12
        for tool in tools:
            assert tool["mode"] in ("read", "gateway")
            assert tool["annotations"]["mode"] == tool["mode"]
            if tool["name"] in GATEWAY_TOOLS:
                assert tool["mode"] == "gateway"
                assert tool["annotations"]["requires_approval"] is True
            else:
                assert tool["mode"] == "read"
                assert tool["annotations"]["requires_approval"] is False

    def test_capabilities_json_maps_name_to_mode_and_requires_approval(self):
        data = build_capabilities_manifest()
        tools = data["tools"]
        assert set(tools) == {
            "jcc_status", "jcc_health", "jcc_sessions", "jcc_session",
            "jcc_assess", "jcc_explain", "jcc_verify", "jcc_recent_decisions",
            "jcc_budget", "jcc_pause", "jcc_steer", "jcc_retry",
        }
        for name, spec in tools.items():
            assert "mode" in spec
            assert "capabilities" in spec
            assert "requires_approval" in spec
            assert spec["mode"] in ("read", "gateway")
            if name in GATEWAY_TOOLS:
                assert spec["requires_approval"] is True
            else:
                assert spec["requires_approval"] is False

    def test_capabilities_json_writable(self, tmp_path):
        out = str(tmp_path / "caps.json")
        write_capabilities_manifest(out)
        loaded = json.loads(open(out, encoding="utf-8").read())
        assert "tools" in loaded
        assert loaded["tools"]["jcc_pause"]["mode"] == "gateway"
