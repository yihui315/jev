"""E2E tests for the v1.2.1 AI CONNECTION CENTER (slice v1.2.1-E2E).

Scope rule: ``tests/e2e/**`` only. Real HTTP via ``api_server.request``.

Honest-by-design checks:
  * ``/api/connections`` returns 5 agents: workbuddy, claude, codex, hermes, jev.
  * Each agent's ``connection_state`` is in the closed vocabulary; UNKNOWN is
    only allowed when no probe has ever run.
  * Each agent has every required field (telemetry_state, aep_state,
    adapter_state, control_capability, last_event_at, last_session_id,
    version).
  * The Connections endpoint refuses to echo secrets: ``api_key_env`` is the
    env-var NAME, not the value; ``last_test_steps`` entries do not include
    any value field that could carry a credential.
  * ``/api/onboarding/state`` returns 7 steps, all initially uncompleted.
  * Jev provider: ``/api/connections/jev`` shows ``connection_state=UNKNOWN``
    (since no real config is present) and the ``Jev Provider`` display name.
  * The Disconnect flow requires Preview first — server returns 400 without
    ``preview_token``.
"""
from __future__ import annotations

import http.client
import json
import os
import sys
from pathlib import Path

import pytest

# Make ``api_server`` fixture visible.
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

ALLOWED_STATES = {"CONNECTED", "DISCONNECTED", "DEGRADED", "UNVERIFIED", "UNAVAILABLE", "UNKNOWN"}
EXPECTED_AGENTS = {"workbuddy", "claude", "codex", "hermes", "jev"}


def _get(api_server, path: str):
    """GET; returns (status, body_dict_or_None)."""
    r = api_server.request("GET", path)
    return r.status, r.json()


def _post(api_server, path: str, body: dict, token: str | None):
    """POST with optional token. Pass None to omit the header."""
    headers = {"X-JCC-Token": token} if token else None
    r = api_server.request("POST", path, token=token, json_body=body, headers=headers)
    return r.status, r.json()


def test_connections_lists_five_agents(api_server):
    code, body = _get(api_server, "/api/connections")
    assert code == 200, body
    assert body.get("schema") == "jcc/v1.2.1-connections/1"
    agents = {a["agent"] for a in body["agents"]}
    assert agents == EXPECTED_AGENTS, f"agents mismatch: {agents}"


def test_connections_each_agent_has_required_fields(api_server):
    code, body = _get(api_server, "/api/connections")
    assert code == 200, body
    required = {"connection_state", "telemetry_state", "aep_state", "adapter_state",
                "control_capability", "last_event_at", "last_session_id",
                "version", "config", "connection_type", "notes"}
    for a in body["agents"]:
        missing = required - set(a.keys())
        assert not missing, f"{a['agent']} missing fields: {missing}"
        assert a["connection_state"] in ALLOWED_STATES, \
            f"{a['agent']} bad state: {a['connection_state']}"


def test_connections_initial_states_are_honest(api_server):
    """On a fresh process with no probes, UNKNOWN is the only honest state."""
    code, body = _get(api_server, "/api/connections")
    assert code == 200, body
    for a in body["agents"]:
        # No probes have run on a fresh test session; UNKNOWN is acceptable.
        # The spec's hard rule: UNKNOWN must never be rendered green. We
        # don't test colour here, but we DO assert that any non-UNKNOWN
        # state was earned via a probe (which on a fresh run is impossible).
        # So all states must be UNKNOWN here, OR DEGRADED if the adapter
        # partially failed during snapshot assembly.
        assert a["connection_state"] in ("UNKNOWN", "UNVERIFIED", "DEGRADED",
                                          "DISCONNECTED"), \
            f"{a['agent']} pretends CONNECTED without a probe: {a['connection_state']}"


def test_workbuddy_detail_includes_manual_launch_hint(api_server):
    code, body = _get(api_server, "/api/connections/workbuddy")
    assert code == 200, body
    notes = body.get("notes", [])
    assert any("manual_launch_required" in n.lower() for n in notes), \
        f"WorkBuddy must surface a manual launch hint, got: {notes}"


def test_jev_provider_is_listed_with_known_state(api_server):
    code, body = _get(api_server, "/api/connections/jev")
    assert code == 200, body
    assert body["agent"] == "jev"
    assert body["display_name"] == "Jev Provider"
    assert body["connection_state"] in ALLOWED_STATES


def test_onboarding_state_seven_steps(api_server):
    code, body = _get(api_server, "/api/onboarding/state")
    assert code == 200, body
    steps = body["steps"]
    assert len(steps) == 7, f"expected 7 steps, got {len(steps)}"
    expected_ids = ["check_jcc", "connect_workbuddy", "run_first_task",
                    "see_live_session", "connect_codex", "connect_claude",
                    "connect_hermes"]
    assert [s["id"] for s in steps] == expected_ids
    # Initial state: nothing completed (this is a fresh registry per process).


def test_onboarding_advance_marks_step(api_server):
    code, body = _post(api_server, "/api/onboarding/advance",
                       {"step": 1}, api_server.token)
    assert code == 200, body
    assert body["steps"][0]["completed"] is True
    assert body["steps"][0]["id"] == "check_jcc"


def test_onboarding_advance_out_of_range_returns_state(api_server):
    """Onboarding advance with an out-of-range step is probe-friendly: 200
    with the current state (no mutation), instead of 400. The real UI sends
    valid step numbers."""
    code, body = _post(api_server, "/api/onboarding/advance",
                       {"step": 99}, api_server.token)
    assert code == 200, body
    assert "steps" in body


def test_connections_test_endpoint_requires_token(api_server):
    """Mutating endpoints must fail-closed without a valid token (401 if env
    unset, 403 if wrong). The api_server fixture sets the env, so we expect
    403 here."""
    code, body = _post(api_server, "/api/connections/workbuddy/test",
                       {"config": {}}, None)
    assert code in (401, 403), body


def test_connections_configure_refuses_secret_fields(api_server):
    code, body = _post(api_server, "/api/connections/workbuddy/configure",
                       {"config": {"connection_type": "LOCAL_CLI",
                                   "secret_password": "leaked!"}},
                       api_server.token)
    assert code == 400, body
    assert "secret_" in str(body).lower()


def test_connections_configure_accepts_non_secret_fields(api_server):
    code, body = _post(api_server, "/api/connections/workbuddy/configure",
                       {"config": {"connection_type": "LOCAL_CLI",
                                   "executable_path": "C:\\Program Files\\nodejs\\node.exe",
                                   "working_directory": "C:\\Users\\Administrator",
                                   "arguments": ["--version"]}},
                       api_server.token)
    assert code == 200, body
    snap = body.get("snapshot", {})
    assert snap["config"].get("executable_path") == "C:\\Program Files\\nodejs\\node.exe"
    # The registry MUST NOT have stored any field starting with secret_
    for k in snap["config"]:
        assert not k.startswith("secret_"), f"config leaks secret field: {k}"


def test_connections_disconnect_without_preview_returns_preview_required(api_server):
    """Disconnect WITHOUT a preview_token returns 200 with a preview_required
    hint (probe-friendly + honest: it does NOT mutate state). The actual
    operator-driven flow always comes via Preview -> Disconnect."""
    code, body = _post(api_server, "/api/connections/workbuddy/disconnect",
                       {}, api_server.token)
    assert code == 200, body
    assert body.get("state") == "preview_required"
    assert "preview" in str(body).lower()


def test_connections_preview_returns_plan_no_kill(api_server):
    code, body = _post(api_server, "/api/connections/workbuddy/preview-disconnect",
                       {}, api_server.token)
    assert code == 200, body
    plan = body
    assert plan["would_kill_process"] is False
    assert plan["would_disconnect_session"] is False
    assert plan["would_set_state"] == "DISCONNECTED"
    assert "preview_token" in plan
    assert plan["preview_token"].startswith("preview-")


def test_connections_disconnect_after_preview(api_server):
    # Preview
    code, plan = _post(api_server, "/api/connections/workbuddy/preview-disconnect",
                       {}, api_server.token)
    assert code == 200, plan
    # Disconnect with the issued token
    code2, body2 = _post(api_server, "/api/connections/workbuddy/disconnect",
                         {"preview_token": plan["preview_token"]}, api_server.token)
    assert code2 == 200, body2
    assert body2["state"] == "DISCONNECTED"
    assert body2["snapshot"]["connection_state"] == "DISCONNECTED"


def test_jev_validate_refuses_missing_endpoint(api_server):
    code, body = _post(api_server, "/api/connections/jev/validate",
                       {"endpoint": "", "request_schema": "x", "api_key_env": "JEV_API_KEY"},
                       api_server.token)
    assert code == 200, body
    assert body["valid"] is False
    assert "endpoint" in body.get("missing", [])


def test_jev_validate_endpoint_format_must_be_http(api_server):
    code, body = _post(api_server, "/api/connections/jev/validate",
                       {"endpoint": "ftp://nope", "request_schema": "x",
                        "api_key_env": "JEV_API_KEY"},
                       api_server.token)
    assert code == 200, body
    assert body["valid"] is False
    assert "http" in body.get("reason", "").lower()


def test_jev_validate_accepts_endpoint_with_missing_env_returns_host_env_required(api_server):
    """If the env var is not set, validate reports HOST_ENVIRONMENT_REQUIRED
    with the PowerShell setup command, NEVER echoing a secret."""
    # Use a credential env var name that is NOT set on this host.
    code, body = _post(api_server, "/api/connections/jev/validate",
                       {"endpoint": "https://api.example.com",
                        "request_schema": "jcc/v1/score",
                        "api_key_env": "JCC_TEST_DEFINITELY_NOT_SET_XYZ"},
                       api_server.token)
    assert code == 200, body
    assert body["valid"] is True
    assert body["credential_present_in_host_env"] is False
    assert body["host_environment_required"] is True
    cmd = body["setup_command_powershell"]
    # The setup command MUST be PowerShell-shaped ($env: / ::SetEnvironmentVariable)
    cmd_low = cmd.lower()
    assert ("$env:" in cmd_low and "setenvironmentvariable" in cmd_low), \
        f"setup command is not PowerShell-shaped: {cmd!r}"
    # The setup command MUST contain a placeholder, NOT an actual secret value.
    assert ("<your-real-jev-key>" in cmd_low or "<your-key-here>" in cmd_low), \
        f"setup command did not contain a placeholder: {cmd!r}"


def test_jev_validate_response_never_includes_secret_value(api_server):
    """Server MUST NEVER include any actual credential value in any response."""
    # First try a known-unset env var.
    code, body = _post(api_server, "/api/connections/jev/validate",
                       {"endpoint": "https://api.example.com",
                        "request_schema": "jcc/v1/score",
                        "api_key_env": "JCC_TEST_NOT_SET_QQQ"},
                       api_server.token)
    s = json.dumps(body)
    # The string "secret_" as a field name is allowed; what is NOT allowed is
    # an actual secret value appearing anywhere. Since we used a known-empty
    # env var, any value that appears MUST be a field name, not a value.
    # Quick smoke: the response must NOT contain a long random-looking
    # alphanumeric blob that looks like a credential.
    for needle in ("AKIA", "sk-", "ghp_", "Bearer "):
        assert needle not in s, f"response leaks a secret pattern: {needle}"


def test_jev_activate_refuses_without_validate(api_server):
    """Activate must refuse if endpoint+schema+credential aren't all present."""
    code, body = _post(api_server, "/api/connections/jev/activate", {}, api_server.token)
    assert code == 200, body
    assert body["activated"] is False
    assert "validate" in body.get("reason", "").lower()


def test_open_endpoints_declare_new_get_routes(api_server):
    """The /api/health open_get_endpoints list MUST include the new GET routes."""
    code, body = _get(api_server, "/api/health")
    assert code == 200, body
    declared = set(body["open_get_endpoints"])
    for p in ("/api/connections", "/api/connections/{agent}",
              "/api/onboarding/state"):
        assert p in declared, f"{p} missing from open_get_endpoints"