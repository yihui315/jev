"""PRIVILEGE-ESCALATION -- a read-only (observer) principal cannot mutate.

The facade delegates to the Operator Skill, whose capability gate (gate 1) refuses
any principal that does not hold the specific control capability. This proves the
MCP surface cannot be used to escalate a read-only caller into a mutator.
"""
from __future__ import annotations

from operator_skill.gates import CapabilityAuthority, Gateway, GatewayContext

from mcp_facade.facade import McpFacade

from helpers import (
    LEAK_TOKEN,
    make_gateway,
    make_recording_backend,
    make_skill,
    observer_principal,
    operator_principal,
)


class TestPrivilegeEscalation:
    def test_observer_refused_on_gateway_capability_gate(self, trace_path, verified_probe):
        # Gateway wired with ONLY the capability gate so the refusal is unambiguously
        # the capability gate firing first.
        gw = Gateway(GatewayContext(capabilities=CapabilityAuthority()))
        skill = make_skill(
            trace_path, backend=make_recording_backend(), control_token=LEAK_TOKEN, gateway=gw
        )
        facade = McpFacade(skill, probe=verified_probe)  # default principal = observer
        res = facade.call_tool("jcc_pause", {"session_id": "sess_1"})
        assert res.is_error is True
        assert res.structured["outcome"]["code"] == "CAPABILITY_DENIED"

    def test_read_tool_succeeds_for_observer(self, trace_path, verified_probe):
        # The same observer can READ but cannot MUTATE -- privilege is enforced by
        # the capability the principal actually holds, not by the caller's say-so.
        skill = make_skill(trace_path, backend=make_recording_backend())
        facade = McpFacade(skill, probe=verified_probe)
        res = facade.call_tool("jcc_sessions", {})
        assert res.is_error is False
        assert res.structured["outcome"]["status"] == "ok"

    def test_operator_principal_not_denied_at_capability_gate(self, trace_path, verified_probe):
        # An operator (holding jcc:control:pause) is NOT refused by the capability
        # gate: it proceeds to the deeper gates. With no further gates wired it
        # hits GATE_UNAVAILABLE -- proving the capability gate itself passed.
        gw = Gateway(GatewayContext(capabilities=CapabilityAuthority()))
        skill = make_skill(
            trace_path, backend=make_recording_backend(), control_token=LEAK_TOKEN, gateway=gw
        )
        facade = McpFacade(skill, probe=verified_probe, principal=operator_principal())
        res = facade.call_tool("jcc_pause", {"session_id": "sess_1"})
        assert res.is_error is True
        assert res.structured["outcome"]["code"] != "CAPABILITY_DENIED"
        assert res.structured["outcome"]["code"] == "GATE_UNAVAILABLE"
