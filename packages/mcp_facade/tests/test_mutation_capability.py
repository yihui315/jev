"""MUTATION REQUIRES VERIFIED CAPABILITY + the gateway forward path.

The MCP facade offers ``jcc_pause`` / ``jcc_steer`` / ``jcc_retry`` as
gateway-only: it refuses them unless the underlying control capability is
VERIFIED, and never executes anything itself (it forwards a REQUEST to the real
control plane, which is the only writer).
"""
from __future__ import annotations

from mcp_facade.facade import McpFacade

from helpers import (
    LEAK_TOKEN,
    craft_assessment,
    make_gateway,
    make_recording_backend,
    make_skill,
    operator_principal,
)


class TestMutationRequiresVerifiedCapability:
    def test_gateway_refused_when_capability_unverified(self, trace_path, unverified_probe):
        be = make_recording_backend()
        skill = make_skill(trace_path, backend=be, control_token=LEAK_TOKEN)
        facade = McpFacade(skill, probe=unverified_probe)
        res = facade.call_tool("jcc_pause", {"session_id": "sess_1"})
        assert res.is_error is True
        assert res.structured["outcome"]["code"] == "CAPABILITY_UNVERIFIED"
        # Refused BEFORE any request reached the control plane.
        assert be.calls == []

    def test_gateway_proceeds_when_capability_verified(self, trace_path, verified_probe):
        be = make_recording_backend()
        # Verified probe + token, but gateway NOT wired -> the facade gate is
        # bypassed and the Operator Skill refuses at GATE_UNAVAILABLE.
        skill = make_skill(trace_path, backend=be, control_token=LEAK_TOKEN)
        facade = McpFacade(skill, probe=verified_probe)
        res = facade.call_tool("jcc_pause", {"session_id": "sess_1"})
        assert res.is_error is True
        assert res.structured["outcome"]["code"] != "CAPABILITY_UNVERIFIED"
        assert res.structured["outcome"]["code"] == "GATE_UNAVAILABLE"
        # The facade did delegate (a session read was issued), so the gate was passed.
        assert any(c["method"] == "GET" for c in be.calls)

    def test_gateway_forwards_request_and_records_only(self, trace_path, verified_probe):
        # Verified capability + fully-wired gateway + authorising session. The
        # facade forwards a REQUEST and the result is INERT (executed=False):
        # the MCP surface never actuates.
        session_detail = {
            "session_id": "sess_1",
            "status": "active",
            "current_assessment": craft_assessment("sess_1"),
        }
        be = make_recording_backend(session_detail=session_detail)
        skill = make_skill(
            trace_path, backend=be, control_token=LEAK_TOKEN, gateway=make_gateway()
        )
        facade = McpFacade(skill, probe=verified_probe, principal=operator_principal())
        res = facade.call_tool("jcc_pause", {"session_id": "sess_1"})
        assert res.is_error is False
        assert res.structured["outcome"]["status"] == "ok"
        assert res.structured["outcome"]["executed"] is False
        # The token was carried to the control plane (header), not logged.
        assert any(
            c["path"] == "/api/pause" and c["token_present"] for c in be.calls
        )
