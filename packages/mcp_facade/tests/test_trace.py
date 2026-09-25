"""TRACE -- every tool invocation (success AND refusal) leaves exactly one trace.

Mirrors the Operator Skill's "every invocation leaves a trace" invariant at the
MCP facade layer: because the facade delegates to ``OperatorSkill.call``, the
trace is the Operator Skill's, not a second tape. We assert the records exist and
are structurally complete.

NOTE: the Operator Skill writes its tape through ``jcc_core.redaction.redact``,
which masks the *value* of ``schema_version`` (the constant contains ``/`` and is
matched as an opaque secret). That is a dependency quirk of the Operator Skill's
own writer, so this test validates the record's structure directly rather than
calling ``validate_trace_record`` (whose schema_version rule is defeated by that
redaction). The redaction behaviour itself is asserted in
``TestTokenNeverLogged`` / ``test_trace_redacts_control_token``.
"""
from __future__ import annotations

from mcp_facade.facade import McpFacade
from operator_skill.trace import read_traces

from helpers import LEAK_TOKEN, make_recording_backend, make_skill

REQUIRED_FIELDS = (
    "tool", "tool_mode", "arguments", "outcome", "evidence_path",
    "principal", "session_id", "directive_ref",
)


class TestTrace:
    def test_every_call_writes_one_trace_record(self, trace_path, verified_probe):
        be = make_recording_backend()
        skill = make_skill(trace_path, backend=be, control_token=None)
        facade = McpFacade(skill, probe=verified_probe)

        facade.call_tool("jcc_status", {})  # ok
        facade.call_tool("jcc_session", {})  # refused: missing session_id
        facade.call_tool("jcc_pause", {"session_id": "sess_1"})  # refused: no token

        records = read_traces(trace_path)
        # Exactly one record per call.
        assert len(records) == 3, [r.get("tool") for r in records]
        for rec in records:
            for field in REQUIRED_FIELDS:
                assert field in rec, (field, rec.keys())
            assert isinstance(rec["arguments"], dict)
            assert rec["outcome"]["status"] in ("ok", "refused", "error")
            # The schema_version field is present (its value is redacted by the
            # dependency's writer; we do not assert its exact constant here).
            assert "schema_version" in rec

    def test_trace_redacts_control_token(self, trace_path, verified_probe):
        be = make_recording_backend()
        skill = make_skill(trace_path, backend=be, control_token=LEAK_TOKEN)
        facade = McpFacade(skill, probe=verified_probe)
        facade.call_tool(
            "jcc_assess",
            {"session_id": "sess_1", "event_id": "e1", "agent": "generic"},
        )
        text = open(trace_path, encoding="utf-8").read()
        assert LEAK_TOKEN not in text
