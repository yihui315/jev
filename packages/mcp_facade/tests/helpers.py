"""Factory helpers for the S-02 MCP facade tests.

Importable from every test module under this directory (pytest's prepend import
mode adds the directory to ``sys.path``).
"""
from __future__ import annotations

from jcc_core.schemas import Assessment
from mcp_facade.capability import StubProbe
from mcp_facade.transport import BackendResult, RecordingBackend
from operator_skill.manifest import (
    CAP_CONTROL,
    CAP_READ,
    OPERATOR_CAPABILITIES,
    OBSERVER_CAPABILITIES,
)
from operator_skill.skill import OperatorSkill

CONTROL_TOKEN = "SUPER_SECRET_CONTROL_TOKEN_ZZZ"

# A distinctive, definitely-not-in-the-wild token used to prove it never leaks.
LEAK_TOKEN = "LEAK_TOKEN_DO_NOT_LOG_9f3c2a"


def craft_assessment(session_id: str):
    """An Assessment the deterministic kernel maps to PAUSE (for jcc_pause)."""
    return Assessment(
        agent="generic",
        session_id=session_id,
        timestamp=123.0,
        provider="mock",
        probabilities={},
        risk_score=0.3,
        band="medium",
        needs_verify=False,
        escalate=False,
        pause=False,
        steer=False,
        retry=False,
        finished=False,
        raw={
            "signals": {
                "worker_stuck": True,
                "meaningful_progress": True,
                "requirements_satisfied": True,
                "needs_verification": False,
                "ready_to_finish": False,
                "needs_human": False,
            }
        },
        provider_verified=True,
        blocker_kind="NONE",
        blocker_reasons=[],
    )


def make_recording_backend(session_detail=None, extra_script=None, default_payload=None):
    """A RecordingBackend pre-scripted for the standard Operator Skill paths."""
    script = {
        ("GET", "/api/health"): BackendResult(
            200, True,
            {"jev_provider": "mock", "actuation_enabled": False,
             "open_get_endpoints": ["/api/health"]},
        ),
        ("GET", "/api/sessions"): BackendResult(200, True, [
            {"session_id": "sess_1", "status": "active"},
            {"session_id": "sess_2", "status": "await"},
        ]),
        ("GET", "/api/audit/verify"): BackendResult(
            200, True, {"intact": True, "entries": 0}
        ),
        ("POST", "/api/assess"): BackendResult(
            200, True,
            {"directive": {"directive_id": "dir_assess_1", "action": "CONTINUE"},
             "acted": False, "actuation_reason": "shadow_mode"},
        ),
        ("POST", "/api/pause"): BackendResult(
            200, True,
            {"directive_id": "dir_manual_pause", "action": "PAUSE",
             "acted": False, "actuation_reason": "shadow_mode"},
        ),
        ("POST", "/api/steer"): BackendResult(
            200, True,
            {"directive_id": "dir_manual_steer", "action": "STEER",
             "acted": False, "actuation_reason": "shadow_mode"},
        ),
        ("POST", "/api/retry"): BackendResult(
            200, True,
            {"directive_id": "dir_manual_retry", "action": "RETRY",
             "acted": False, "actuation_reason": "shadow_mode"},
        ),
    }
    if session_detail is not None:
        script[("GET", "/api/sessions/sess_1")] = BackendResult(200, True, session_detail)
    if extra_script:
        script.update(extra_script)
    return RecordingBackend(script=script, default_payload=default_payload)


def make_skill(trace_path, backend=None, control_token=None, gateway=None, approvals=None):
    return OperatorSkill(
        backend if backend is not None else make_recording_backend(),
        trace_path=trace_path,
        control_token=control_token,
        gateway=gateway,
        approvals=approvals,
    )


class AlwaysApproved:
    """Test double for the human-approval gate: every directive is approved.

    Used only as scaffolding for the positive gateway-forward test; the real
    Operator Skill ``Gateway`` is unchanged.
    """

    def holds(self, directive_id, session_id):
        return True

    def find(self, directive_id, session_id):
        return True


def make_gateway(signing_key: bytes = b"test-signing-key"):
    """A fully-wired Operator Skill gateway (capability/policy/signing/approval/budget)."""
    from jcc_core.decision import ThresholdsConfig
    from jcc_core.signing import DirectiveSigner
    from operator_skill.gates import (
        CapabilityAuthority,
        Gateway,
        GatewayContext,
        PolicyAuthority,
        SignedDirectiveGate,
    )
    from supervisor.budget import BudgetGovernor

    return Gateway(GatewayContext(
        capabilities=CapabilityAuthority(),
        policy=PolicyAuthority(thresholds=ThresholdsConfig()),
        signing=SignedDirectiveGate(DirectiveSigner(signing_key)),
        approvals=AlwaysApproved(),
        budget=BudgetGovernor(),
    ))


def operator_principal():
    from operator_skill.gates import Principal

    return Principal(
        name="operator",
        capabilities=frozenset({CAP_READ, CAP_CONTROL, "jcc:control:pause"}),
    )


def observer_principal():
    from operator_skill.gates import Principal

    return Principal(name="observer", capabilities=frozenset(OBSERVER_CAPABILITIES))
