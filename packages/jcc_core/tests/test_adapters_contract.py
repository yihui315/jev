"""P2 adapter contract tests: real fixture -> normalize() -> valid AEPEnvelope.

Asserts (ADR-0003 invariants):
  * every fixture normalizes into a schema-valid ``AEPEnvelope``;
  * identity is ``session_id`` (NOT ``cwd``);
  * same-cwd/different-session never collides; different-cwd/same-session does not
    create a second identity;
  * dedup keys are ``(session_id, event_id)`` and ``delivery_id``;
  * missing ``session_id`` -> ``UnidentifiableEvent`` (never fabricated);
  * secrets/PII are redacted out of payload/evidence.

Run: F:/jev/.venv/Scripts/python -m pytest jcc_core/tests/test_adapters_contract.py -q
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from jcc_core.adapters import (
    ClaudeAdapter,
    CodexAdapter,
    HermesAdapter,
    UnidentifiableEvent,
    WorkBuddyAdapter,
)
from jcc_core.schemas import AEPEnvelope, AgentName, EventType

FIXTURES = Path(__file__).resolve().parent / "fixtures"

ADAPTER_BY_PREFIX = {
    "workbuddy": WorkBuddyAdapter,
    "claude": ClaudeAdapter,
    "codex": CodexAdapter,
    "hermes": HermesAdapter,
}

ALL_FIXTURES = sorted(FIXTURES.glob("*.json"))

#: Fixtures that MUST raise UnidentifiableEvent (no native identity) - negative cases.
NEGATIVE_FIXTURES = {"codex_notify_agent_turn_complete.json"}
#: Fixtures whose identity is a documented synthetic process id (no native session_id field).
SYNTHETIC_IDENTITY_FIXTURES = {"hermes_gateway_state.json"}

POSITIVE_FIXTURES = [p for p in ALL_FIXTURES if p.name not in NEGATIVE_FIXTURES]
IDENTITY_CHECKED_FIXTURES = [
    p for p in ALL_FIXTURES if p.name not in NEGATIVE_FIXTURES | SYNTHETIC_IDENTITY_FIXTURES
]


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _adapter_for(path: Path):
    for prefix, cls in ADAPTER_BY_PREFIX.items():
        if path.name.startswith(prefix):
            return cls()
    raise AssertionError(f"no adapter maps fixture {path.name}")


def _norm(adapter, raw):
    return asyncio.run(adapter.normalize(raw))


def test_fixtures_exist():
    assert ALL_FIXTURES, "no fixtures found"
    names = {p.name for p in ALL_FIXTURES}
    for required in (
        "workbuddy_session_file.json",
        "workbuddy_hook_session_start.json",
        "claude_transcript_record.json",
        "claude_telemetry_event.json",
        "codex_rollout_session_meta.json",
        "hermes_hook_agent_step.json",
        "hermes_gateway_state.json",
    ):
        assert required in names, f"missing fixture {required}"


@pytest.mark.parametrize("path", POSITIVE_FIXTURES, ids=lambda p: p.name)
def test_fixture_normalizes_to_valid_envelope(path: Path):
    adapter = _adapter_for(path)
    raw = _load(path)
    env = _norm(adapter, raw)
    assert isinstance(env, AEPEnvelope)
    # re-validate through the pydantic model + JSON round-trip
    AEPEnvelope(**env.model_dump_api())
    assert env.version == "aep/v0.1"
    assert env.event_id and env.delivery_id
    assert env.session_id, "session_id must be present"
    assert env.agent == adapter.agent
    assert env.schema_ok is True


@pytest.mark.parametrize("path", IDENTITY_CHECKED_FIXTURES, ids=lambda p: p.name)
def test_identity_is_session_id_not_cwd(path: Path):
    """The envelope's identity field must equal the native session id, not cwd."""
    adapter = _adapter_for(path)
    raw = _load(path)
    env = _norm(adapter, raw)
    native = (
        raw.get("session_id")
        or raw.get("sessionId")
        or raw.get("_session_id")
        or (raw.get("payload") or {}).get("session_id")
        or (raw.get("context") or {}).get("session_id")
        or (raw.get("event_data") or {}).get("session_id")
    )
    assert native, f"{path.name}: fixture should carry a native session id"
    assert env.session_id == str(native)
    assert env.session_id != (env.cwd or ""), "cwd must not be the identity"


def test_same_cwd_different_sessions_do_not_collide():
    a = WorkBuddyAdapter()
    b = ClaudeAdapter()
    ra = {"_source": "hook", "hook_event_name": "SessionStart", "session_id": "S-A", "cwd": "C:/some/checkout"}
    rb = {"_source": "hook", "hook_event_name": "SessionStart", "session_id": "S-B", "cwd": "C:/some/checkout"}
    ea = _norm(a, ra)
    eb = _norm(b, rb)
    assert ea.session_id != eb.session_id
    assert ea.event_id != eb.event_id


def test_same_session_different_cwd_single_identity():
    a = WorkBuddyAdapter()
    e1 = _norm(a, {"_source": "hook", "hook_event_name": "SessionStart", "session_id": "S-1", "cwd": "C:/some/checkout"})
    e2 = _norm(a, {"_source": "hook", "hook_event_name": "Stop", "session_id": "S-1", "cwd": "C:/other/checkout"})
    assert e1.session_id == e2.session_id == "S-1"
    assert e1.cwd != e2.cwd  # metadata differs, identity stable


def test_deterministic_event_and_delivery_ids():
    a = WorkBuddyAdapter()
    raw = _load(FIXTURES / "workbuddy_hook_pre_tool_use.json")
    e1 = _norm(a, raw)
    e2 = _norm(a, dict(raw))  # redelivery
    assert e1.event_id == e2.event_id, "same logical event -> same event_id"
    assert e1.delivery_id == e2.delivery_id, "explicit _delivery_id is honoured"


def test_missing_session_id_raises_unidentifiable():
    a = WorkBuddyAdapter()
    with pytest.raises(UnidentifiableEvent):
        _norm(a, {"_source": "hook", "hook_event_name": "SessionStart", "cwd": "C:/some/checkout"})

    c = CodexAdapter()
    with pytest.raises(UnidentifiableEvent):
        _norm(c, _load(FIXTURES / "codex_notify_agent_turn_complete.json"))


@pytest.mark.parametrize("name", sorted(NEGATIVE_FIXTURES))
def test_negative_fixtures_raise_unidentifiable(name: str):
    path = FIXTURES / name
    adapter = _adapter_for(path)
    with pytest.raises(UnidentifiableEvent):
        _norm(adapter, _load(path))


def test_gateway_state_synthetic_identity_is_deterministic():
    h = HermesAdapter()
    raw = _load(FIXTURES / "hermes_gateway_state.json")
    e1 = _norm(h, raw)
    e2 = _norm(h, dict(raw))
    assert e1.session_id == e2.session_id
    assert e1.session_id.startswith("hermes-gateway")
    assert e1.session_id != (e1.cwd or "")


def test_codex_notify_with_transport_session_is_accepted():
    c = CodexAdapter()
    raw = _load(FIXTURES / "codex_notify_agent_turn_complete.json")
    raw["_session_id"] = "019d89b3-1219-7233-866e-9e9717cd8218"
    env = _norm(c, raw)
    assert env.session_id == "019d89b3-1219-7233-866e-9e9717cd8218"
    assert env.event_type == EventType.HEARTBEAT
    assert env.payload["input_message_count"] == 1


def test_secret_redaction_in_payload_and_evidence():
    """A planted secret must never survive into payload/evidence."""
    secret = "sk-live-DEADBEEFdeadbeef0123456789"
    c = ClaudeAdapter()
    raw = {
        "_source": "hook",
        "hook_event_name": "PreToolUse",
        "session_id": "S-redact",
        "cwd": "C:/some/checkout",
        "tool_name": "Bash",
        "tool_input": {"command": "curl -H 'Authorization: Bearer xyz'", "api_key": secret,
                       "password": "hunter2"},
        "authorization": f"Bearer {secret}",
    }
    env = _norm(c, raw)
    blob = json.dumps(env.model_dump_api(), ensure_ascii=False)
    assert secret not in blob, "planted api_key leaked"
    assert "hunter2" not in blob, "planted password leaked"
    assert "[REDACTED]" in blob
    assert env.payload["tool_input"]["api_key"] == "[REDACTED]"


def test_event_type_mapping_is_real():
    a = WorkBuddyAdapter()
    assert _norm(a, {"_source": "hook", "hook_event_name": "SessionStart", "session_id": "s"}).event_type == EventType.SESSION_START
    assert _norm(a, {"_source": "hook", "hook_event_name": "PreToolUse", "session_id": "s", "tool_name": "Edit"}).event_type == EventType.TOOL_CALL
    h = HermesAdapter()
    assert _norm(h, {"_source": "hook", "event": "session:start", "context": {"session_id": "h1"}}).event_type == EventType.SESSION_START
    assert _norm(h, {"_source": "hook", "event": "agent:step", "context": {"session_id": "h1", "iteration": 2}}).event_type == EventType.TOOL_CALL
    c = CodexAdapter()
    assert _norm(c, _load(FIXTURES / "codex_rollout_session_meta.json")).event_type == EventType.SESSION_START


@pytest.mark.parametrize("cls,agent", [
    (WorkBuddyAdapter, AgentName.WORKBUDDY),
    (ClaudeAdapter, AgentName.CLAUDE),
    (CodexAdapter, AgentName.CODEX),
    (HermesAdapter, AgentName.HERMES),
])
def test_probe_contract(cls, agent):
    inst = cls()
    assert inst.agent == agent
    report = asyncio.run(inst.probe())
    for key in ("agent", "installed", "version", "config_path", "hook_schema_kind", "verified", "status"):
        assert key in report, f"{agent}: probe missing {key}"
    assert report["status"] in ("CONNECTED", "DEGRADED", "ADAPTER_DISABLED", "UNVERIFIED")
    disc = asyncio.run(inst.discover())
    assert disc["agent"] == agent.value
    assert "status" in disc


def test_workbuddy_permission_request_not_hooked():
    """P2 hard rule: PermissionRequest must NOT be hooked for WorkBuddy."""
    from jcc_core.adapters.workbuddy import TELEMETRY_EVENTS

    assert "PermissionRequest" not in TELEMETRY_EVENTS
    report = asyncio.run(WorkBuddyAdapter().probe())
    assert report["permission_request_hooked"] is False


def test_codex_app_server_steering_disabled():
    report = asyncio.run(CodexAdapter().probe())
    assert report["app_server_steering"] is False
    assert report["push_verified"] is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
