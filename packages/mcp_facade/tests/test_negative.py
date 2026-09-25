"""NEGATIVE -- malformed / refused inputs are refused as structured outcomes.

These mirror the Operator Skill's refusal tests at the MCP layer. A refusal is a
normal result (isError True), never an exception, never a silent success.
"""
from __future__ import annotations

from mcp_facade.facade import McpFacade

from helpers import make_recording_backend, make_skill


class TestNegative:
    def test_unknown_tool_refused(self, trace_path, verified_probe):
        skill = make_skill(trace_path, backend=make_recording_backend())
        facade = McpFacade(skill, probe=verified_probe)
        res = facade.call_tool("does_not_exist", {})
        assert res.is_error is True
        assert res.structured["outcome"]["code"] == "UNKNOWN_TOOL"

    def test_unknown_argument_refused(self, trace_path, verified_probe):
        skill = make_skill(trace_path, backend=make_recording_backend())
        facade = McpFacade(skill, probe=verified_probe)
        res = facade.call_tool("jcc_status", {"foo": "bar"})
        assert res.is_error is True
        assert res.structured["outcome"]["code"] == "MALFORMED_ARGUMENT"

    def test_missing_session_id_refused(self, trace_path, verified_probe):
        skill = make_skill(trace_path, backend=make_recording_backend())
        facade = McpFacade(skill, probe=verified_probe)
        res = facade.call_tool("jcc_session", {})
        assert res.is_error is True
        assert res.structured["outcome"]["code"] == "MALFORMED_ARGUMENT"

    def test_malformed_session_id_refused(self, trace_path, verified_probe):
        skill = make_skill(trace_path, backend=make_recording_backend())
        facade = McpFacade(skill, probe=verified_probe)
        res = facade.call_tool("jcc_session", {"session_id": "bad/id/with/slash"})
        assert res.is_error is True
        assert res.structured["outcome"]["code"] == "MALFORMED_SESSION_ID"

    def test_gateway_without_token_refused(self, trace_path, verified_probe):
        # probe verified, but no control token configured -> the Operator Skill
        # refuses (MISSING_CONTROL_TOKEN) before issuing any request.
        skill = make_skill(trace_path, backend=make_recording_backend(), control_token=None)
        facade = McpFacade(skill, probe=verified_probe)
        res = facade.call_tool("jcc_pause", {"session_id": "sess_1"})
        assert res.is_error is True
        assert res.structured["outcome"]["code"] == "MISSING_CONTROL_TOKEN"

    def test_assess_requires_token(self, trace_path, verified_probe):
        skill = make_skill(trace_path, backend=make_recording_backend(), control_token=None)
        facade = McpFacade(skill, probe=verified_probe)
        res = facade.call_tool(
            "jcc_assess", {"session_id": "sess_1", "event_id": "e1"}
        )
        assert res.is_error is True
        assert res.structured["outcome"]["code"] == "MISSING_CONTROL_TOKEN"
