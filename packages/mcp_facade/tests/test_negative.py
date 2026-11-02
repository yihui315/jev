"""NEGATIVE -- malformed / refused inputs are refused as structured outcomes.

These mirror the Operator Skill's refusal tests at the MCP layer. A refusal is a
normal result (isError True), never an exception, never a silent success.
"""
from __future__ import annotations

from mcp_facade.facade import McpFacade
from mcp_facade.transport import BackendResult

from helpers import CONTROL_TOKEN, make_gateway, make_recording_backend, make_skill


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


class TestControlTokenGateSemantics:
    """Regression for the strict split between a missing token and an
    unavailable control gate.

    Before the fix, an ambient ``JCC_CONTROL_TOKEN`` in the environment silently
    overrode an explicitly-injected ``control_token=None`` (the test's "no
    token" intent), so a missing token was reported as ``GATE_UNAVAILABLE``
    instead of ``MISSING_CONTROL_TOKEN``. These lock the four outcomes apart.
    """

    def test_gate_available_but_token_none_is_missing(self, trace_path, verified_probe):
        skill = make_skill(
            trace_path, backend=make_recording_backend(),
            control_token=None, gateway=make_gateway(),
        )
        facade = McpFacade(skill, probe=verified_probe)
        res = facade.call_tool("jcc_pause", {"session_id": "sess_1"})
        assert res.is_error is True
        assert res.structured["outcome"]["code"] == "MISSING_CONTROL_TOKEN"

    def test_gate_available_but_token_empty_is_missing(self, trace_path, verified_probe):
        skill = make_skill(
            trace_path, backend=make_recording_backend(),
            control_token="", gateway=make_gateway(),
        )
        facade = McpFacade(skill, probe=verified_probe)
        res = facade.call_tool("jcc_pause", {"session_id": "sess_1"})
        assert res.is_error is True
        assert res.structured["outcome"]["code"] == "MISSING_CONTROL_TOKEN"

    def test_gate_unavailable_with_token_present_is_gate_unavailable(
        self, trace_path, verified_probe
    ):
        # Token IS configured, but the in-process control gate service is not
        # wired. That is the ONLY legitimate GATE_UNAVAILABLE cause.
        skill = make_skill(
            trace_path, backend=make_recording_backend(),
            control_token=CONTROL_TOKEN, gateway=None,
        )
        facade = McpFacade(skill, probe=verified_probe)
        res = facade.call_tool("jcc_pause", {"session_id": "sess_1"})
        assert res.is_error is True
        assert res.structured["outcome"]["code"] == "GATE_UNAVAILABLE"

    def test_wrong_token_is_invalid_control_token(self, trace_path, verified_probe):
        # Token present + gate wired, but the control plane rejects it (HTTP 403).
        # Must surface as INVALID_CONTROL_TOKEN, never MISSING / never GATE_UNAVAILABLE.
        backend = make_recording_backend(extra_script={
            ("GET", "/api/sessions/sess_1"): BackendResult(403, False, {}),
        })
        skill = make_skill(
            trace_path, backend=backend,
            control_token=CONTROL_TOKEN, gateway=make_gateway(),
        )
        facade = McpFacade(skill, probe=verified_probe)
        res = facade.call_tool("jcc_pause", {"session_id": "sess_1"})
        assert res.is_error is True
        assert res.structured["outcome"]["code"] == "INVALID_CONTROL_TOKEN"

    def test_valid_token_reaches_control_plane(self, trace_path, verified_probe):
        # A present, valid token must not be reported as missing or as a gate
        # outage, and the request must actually reach the control plane (the auth
        # layer accepted it and forwarded a request). Deeper policy gates may
        # still refuse the mutation -- that is a different, non-token outcome.
        backend = make_recording_backend()
        skill = make_skill(
            trace_path, backend=backend,
            control_token=CONTROL_TOKEN, gateway=make_gateway(),
        )
        facade = McpFacade(skill, probe=verified_probe)
        res = facade.call_tool("jcc_pause", {"session_id": "sess_1"})
        code = res.structured["outcome"]["code"]
        assert code != "MISSING_CONTROL_TOKEN"
        assert code != "GATE_UNAVAILABLE"
        assert code != "INVALID_CONTROL_TOKEN"
        assert len(backend.calls) >= 1, \
            "a valid token must let the request reach the control plane"
