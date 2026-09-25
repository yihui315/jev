"""TRACE -- every invocation yields a trace record, and the record is valid.

Required by S-01 (and FINAL_STOP_CONDITIONS C / S-01). The trace tape is the
evidence the acceptance gate reads, so it is asserted structurally: presence,
schema validity (``validate_trace_record``), redaction of the control token, and
"one record per call" including refusals.
"""
from __future__ import annotations

import json
from pathlib import Path

from jcc_core.schemas import DirectiveAction
from operator_skill.refusals import Refusal
from operator_skill.trace import REDACTED, read_traces, validate_trace_record

from ._helpers import (
    CONTROL_TOKEN,
    MALFORMED_SESSION_ID,
    VALID_SESSION_ID,
    RouteBackend,
    make_skill,
    read_ok,
    session_snapshot_for,
)


def _count_records(path: Path) -> int:
    p = Path(path)
    if not p.exists():
        return 0
    return len(read_traces(p))


def test_trace_record_schema_ok_for_read(trace_path: Path) -> None:
    backend = RouteBackend(routes={("GET", "/api/health"): read_ok({"status": "ok"})})
    skill = make_skill(backend, trace_path)
    res = skill.call("jcc_status")
    assert res.ok is True
    records = read_traces(trace_path)
    assert len(records) == 1
    rec = records[0]
    problems = validate_trace_record(rec)
    assert problems == [], problems
    assert rec["tool"] == "jcc_status"
    assert rec["tool_mode"] == "read"
    assert rec["outcome"]["status"] == "ok"
    assert isinstance(rec["timestamp"], (int, float)) and not isinstance(rec["timestamp"], bool)
    assert isinstance(rec["iso_timestamp"], str) and rec["iso_timestamp"]
    assert rec["evidence_path"] == str(trace_path)
    assert rec["arguments"] == {}  # jcc_status takes no arguments
    assert "directive_ref" in rec  # referenced directive/signature (None for a read)
    assert rec["directive_ref"] is None


def test_trace_record_schema_ok_for_ingest(trace_path: Path) -> None:
    backend = RouteBackend(
        routes={
            ("POST", "/api/assess"): read_ok(
                {
                    "directive": {"directive_id": "d-x", "action": "VERIFY", "signature": None},
                    "acted": False,
                    "actuation_reason": "shadow_mode",
                }
            )
        }
    )
    skill = make_skill(backend, trace_path, control_token=CONTROL_TOKEN)
    res = skill.call("jcc_assess", {"session_id": VALID_SESSION_ID, "payload": {"k": "v"}})
    assert res.ok is True
    rec = read_traces(trace_path)[-1]
    assert validate_trace_record(rec) == []
    # An ingest records, it never executes -- pinned on the record.
    assert rec["outcome"]["executed"] is False
    assert rec["outcome"]["referenced"]["action"] == "VERIFY"


def test_trace_redacts_control_token_in_arguments(trace_path: Path) -> None:
    secret = CONTROL_TOKEN
    backend = RouteBackend(
        routes={
            ("POST", "/api/assess"): read_ok(
                {
                    "directive": {"directive_id": "d-x", "action": "VERIFY", "signature": None},
                    "acted": False,
                    "actuation_reason": "shadow_mode",
                }
            )
        }
    )
    skill = make_skill(backend, trace_path, control_token=secret)
    # The caller inadvertently embeds the control token inside an argument value.
    res = skill.call(
        "jcc_assess",
        {"session_id": VALID_SESSION_ID, "payload": {"note": f"oops leaked {secret}"}},
    )
    assert res.ok is True
    rec = read_traces(trace_path)[-1]
    serialized = json.dumps(rec, default=str, sort_keys=True)
    # The secret must never reach the tape in clear text; the mask must be present.
    assert secret not in serialized
    assert REDACTED in serialized


def test_refusal_is_traced(trace_path: Path) -> None:
    backend = RouteBackend(default=read_ok({}))
    skill = make_skill(backend, trace_path)
    before = _count_records(trace_path)
    res = skill.call("jcc_session", {"session_id": MALFORMED_SESSION_ID})
    after = _count_records(trace_path)
    assert after == before + 1
    rec = read_traces(trace_path)[-1]
    assert validate_trace_record(rec) == []
    assert rec["outcome"]["status"] == "refused"
    assert rec["tool"] == "jcc_session"


def test_trace_records_endpoint_and_http_status(trace_path: Path) -> None:
    backend = RouteBackend(routes={("GET", "/api/audit/verify"): read_ok({"intact": True, "entries": 4})})
    skill = make_skill(backend, trace_path)
    skill.call("jcc_verify")
    rec = read_traces(trace_path)[-1]
    assert rec["endpoint"] == "/api/audit/verify"
    assert rec["http_status"] == 200


def test_every_invocation_emits_one_valid_record(trace_path: Path) -> None:
    routes = {
        ("GET", "/api/health"): read_ok({"status": "ok"}),
        ("GET", "/api/sessions"): read_ok([]),
        ("GET", f"/api/sessions/{VALID_SESSION_ID}"): read_ok(
            session_snapshot_for(DirectiveAction.PAUSE)
        ),
        ("POST", "/api/assess"): read_ok(
            {
                "directive": {"directive_id": "d-x", "action": "VERIFY", "signature": None},
                "acted": False,
                "actuation_reason": "shadow_mode",
            }
        ),
        ("GET", "/api/audit/verify"): read_ok({"intact": True, "entries": 3}),
        ("GET", "/api/decisions"): read_ok([]),
    }
    backend = RouteBackend(routes=routes)
    skill = make_skill(backend, trace_path, control_token=CONTROL_TOKEN)
    calls = [
        ("jcc_status", None),
        ("jcc_sessions", None),
        ("jcc_session", {"session_id": VALID_SESSION_ID}),
        ("jcc_assess", {"session_id": VALID_SESSION_ID, "payload": {"k": "v"}}),
        ("jcc_verify", None),
        ("jcc_recent_decisions", None),
    ]
    for tool, args in calls:
        skill.call(tool, args)
    records = read_traces(trace_path)
    assert len(records) == len(calls), (len(records), len(calls))
    for rec in records:
        problems = validate_trace_record(rec)
        assert problems == [], (rec["tool"], problems)
    # And a refusal, too, yields exactly one record.
    n = _count_records(trace_path)
    skill.call("jcc_session", {"session_id": MALFORMED_SESSION_ID})
    assert _count_records(trace_path) == n + 1
    assert isinstance(read_traces(trace_path)[-1], dict)


def test_refusal_is_a_value_not_an_exception(trace_path: Path) -> None:
    backend = RouteBackend(default=read_ok({}))
    skill = make_skill(backend, trace_path)
    # If call() raised, this test would error, not "pass silently".
    res = skill.call("jcc_session", {"session_id": MALFORMED_SESSION_ID})
    assert isinstance(res, object)
    assert isinstance(res.refusal, Refusal)
    assert res.refusal.as_dict()["refused"] is True
