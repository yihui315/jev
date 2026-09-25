"""PROMPT-INJECTION -- a caller cannot smuggle mutation authority into a read.

The strict argument binding (the Operator Skill's first line of defence) refuses
any argument a tool does not declare, so a read tool cannot be turned into a
mutation by injecting ``action`` / ``approve`` / ``tool``, and a hostile
``session_id`` is refused before it is used as an identity or interpolated into a
request path.
"""
from __future__ import annotations

from mcp_facade.facade import McpFacade

from helpers import make_recording_backend, make_skill


class TestPromptInjection:
    def test_read_tool_rejects_injected_mutation_arguments(self, trace_path, verified_probe):
        skill = make_skill(trace_path, backend=make_recording_backend())
        facade = McpFacade(skill, probe=verified_probe)
        # Attempt to smuggle a pause action + approval into a pure read tool.
        res = facade.call_tool(
            "jcc_session",
            {"session_id": "sess_1", "action": "PAUSE", "approve": "yes"},
        )
        assert res.is_error is True
        assert res.structured["outcome"]["code"] == "MALFORMED_ARGUMENT"
        # It never reached the API as a mutation.
        assert res.structured["tool"] == "jcc_session"

    def test_session_id_injection_refused(self, trace_path, verified_probe):
        skill = make_skill(trace_path, backend=make_recording_backend())
        facade = McpFacade(skill, probe=verified_probe)
        res = facade.call_tool("jcc_session", {"session_id": "../../etc/passwd"})
        assert res.is_error is True
        assert res.structured["outcome"]["code"] == "MALFORMED_SESSION_ID"

    def test_no_escalation_via_tool_argument(self, trace_path, verified_probe):
        skill = make_skill(trace_path, backend=make_recording_backend())
        facade = McpFacade(skill, probe=verified_probe)
        # A read tool is asked to "become" the pause tool by passing tool=.
        res = facade.call_tool(
            "jcc_status",
            {"tool": "jcc_pause", "session_id": "sess_1", "action": "PAUSE"},
        )
        assert res.is_error is True
        assert res.structured["outcome"]["code"] == "MALFORMED_ARGUMENT"
        assert res.structured["tool"] == "jcc_status"
