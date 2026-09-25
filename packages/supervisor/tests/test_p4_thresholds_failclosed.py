"""P4 falsifiers for the threshold injection point (task #74).

``thresholds or ThresholdsConfig()`` meant an omitted argument silently ran the
plane on the kernel's module-private defaults. That is worse than a crash: the
recorded ``policy_config_hash`` (D-21) describes a policy nobody chose, and the
shadow tape then looks authoritative while describing a different policy than the
one that ran. These tests pin both halves — that the omission RAISES, and that the
injected/resolved surfaces are recorded as such rather than implied.
"""
from __future__ import annotations

import pytest

from jcc_core import decision
from jcc_core.schemas import AgentName, Assessment
from jcc_core.state_store import StateStore
from supervisor.ports import ShadowActuationPort
from supervisor.sentinel import Sentinel


def _store(tmp_path) -> StateStore:
    return StateStore(str(tmp_path / "jcc.db"))


def _assessment() -> Assessment:
    return Assessment(
        agent=AgentName.GENERIC, session_id="s-1", timestamp=1.0,
        probabilities={"risk": 0.05}, risk_score=0.05, band="low",
        provider_verified=True,
        raw={
            "signals": {
                "meaningful_progress": True, "worker_stuck": False,
                "work_off_track": False, "requirements_satisfied": True,
                "needs_verification": False, "ready_to_finish": True,
                "needs_human": False,
            },
            "evidence_complete": True,
        },
    )


def _no_signer():
    class _NoKey:
        available = False

    return _NoKey()



# --------------------------------------------------------------------------- #
# #74 — explicit resolution, fail closed
# --------------------------------------------------------------------------- #
def test_missing_thresholds_source_raises_at_construction(tmp_path):
    """No injection AND no resolver ⇒ RAISE, in the constructor, before any work."""
    with pytest.raises(ValueError, match="no thresholds source"):
        Sentinel(_store(tmp_path), signer=_no_signer(), budget=None,
                 dispatch_port=ShadowActuationPort())


def test_injected_thresholds_are_recorded_as_injected(tmp_path):
    store = _store(tmp_path)
    cfg = decision.ThresholdsConfig()
    s = Sentinel(store, signer=_no_signer(), budget=None,
                 dispatch_port=ShadowActuationPort(), thresholds=cfg)
    assert s.thresholds is cfg
    assert s.thresholds_source == "injected"


def test_resolver_sourced_thresholds_are_recorded_as_resolver(tmp_path):
    store = _store(tmp_path)
    resolved = {}

    def _resolver():
        resolved["called"] = True
        return {"default": {"escalate_risk": 0.9, "pause_risk": 0.8,
                            "steer_risk": 0.7, "finish_risk": 0.1}}

    s = Sentinel(store, signer=_no_signer(), budget=None,
                 dispatch_port=ShadowActuationPort(), thresholds_resolver=_resolver)
    assert resolved["called"] is True, "the resolver was never invoked (vacuous pass)"
    assert s.thresholds_source == "resolver"
    # the raw config was parsed through the canonical parser, not glossed over
    assert s.thresholds.default.escalate_risk == 0.9


def test_a_resolver_may_hand_back_an_already_parsed_surface(tmp_path):
    store = _store(tmp_path)
    cfg = decision.ThresholdsConfig()
    s = Sentinel(store, signer=_no_signer(), budget=None,
                 dispatch_port=ShadowActuationPort(),
                 thresholds_resolver=lambda: cfg)
    assert s.thresholds is cfg
    assert s.thresholds_source == "resolver"


def test_a_resolver_returning_malformed_config_fails_closed(tmp_path):
    """The parse error must surface HERE, not first-use, far from the cause."""
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="retired key"):
        Sentinel(store, signer=_no_signer(), budget=None,
                 dispatch_port=ShadowActuationPort(),
                 thresholds_resolver=lambda: {"enabled": True, "default": {}})


def test_a_non_callable_resolver_is_rejected(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(TypeError, match="must be callable"):
        Sentinel(store, signer=_no_signer(), budget=None,
                 dispatch_port=ShadowActuationPort(), thresholds_resolver=decision.Thresholds())


def test_a_duck_typed_threshold_object_is_rejected(tmp_path):
    """``for_agent``/``config_hash`` are load-bearing; a partial object is not a config."""

    class _Partial:
        def for_agent(self, agent):
            return decision.Thresholds()

    with pytest.raises(TypeError, match="ThresholdsConfig"):
        Sentinel(_store(tmp_path), signer=_no_signer(), budget=None,
                 dispatch_port=ShadowActuationPort(), thresholds=_Partial())


def test_the_shadow_tape_records_the_threshold_surface_that_ran(tmp_path):
    """End-to-end: the resolved surface is the one that reaches every record."""
    store = _store(tmp_path)
    cfg = decision.ThresholdsConfig.from_config(
        {"per_agent": {"generic": {"escalate_risk": 0.99}}}
    )
    s = Sentinel(store, signer=_no_signer(), budget=None,
                 dispatch_port=ShadowActuationPort(), thresholds=cfg)
    assert s.thresholds_source == "injected"
    # the plane runs, and the hash it stamps is the one from the injected surface
    assert s.thresholds.config_hash() == cfg.config_hash()
    assert s.thresholds.for_agent("generic").escalate_risk == 0.99
