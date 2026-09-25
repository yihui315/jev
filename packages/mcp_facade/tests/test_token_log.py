"""TOKEN-NEVER-LOGGED -- the control token is carried through but never logged.

The control token is the only credential the facade forwards to the control
plane. It must appear in: (a) the facade's own log output, and (b) the Operator
Skill trace tape. We capture the facade logger and read the trace file, and
assert the distinctive token string is absent from both, while confirming the
token WAS in play (the backend received it).
"""
from __future__ import annotations

import logging

from mcp_facade.facade import McpFacade

from helpers import LEAK_TOKEN, make_recording_backend, make_skill


class TestTokenNeverLogged:
    def test_token_absent_from_logs_and_trace(self, trace_path, verified_probe, caplog):
        be = make_recording_backend()
        skill = make_skill(trace_path, backend=be, control_token=LEAK_TOKEN)
        facade = McpFacade(skill, probe=verified_probe)

        with caplog.at_level(logging.INFO, logger="jcc.mcp_facade"):
            facade.call_tool(
                "jcc_assess",
                {"session_id": "sess_1", "event_id": "e1", "agent": "generic"},
            )
            facade.call_tool("jcc_pause", {"session_id": "sess_1"})

        # (a) The facade's own log output must not contain the token.
        assert LEAK_TOKEN not in caplog.text, "control token leaked into logs"
        # (b) The Operator Skill trace tape must not contain the token.
        trace_text = open(trace_path, encoding="utf-8").read()
        assert LEAK_TOKEN not in trace_text, "control token leaked into trace"
        # Sanity: the token really was in play (carried to the API as a header).
        assert any(c["token_present"] for c in be.calls)
