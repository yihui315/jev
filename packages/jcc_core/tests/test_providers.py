import os

from jcc_core import config as cfg_mod
from jcc_core.providers import build_provider
from jcc_core.providers.disabled import DisabledProvider
from jcc_core.providers.mock import DeterministicMockProvider
from jcc_core.schemas import AEPEnvelope, AgentName, SessionState, SessionStatus


def _env(**kw):
    base = dict(event_id="e1", delivery_id="d1", session_id="s1", agent="workbuddy")
    base.update(kw)
    return AEPEnvelope(**base)


def test_mock_deterministic():
    p = DeterministicMockProvider()
    e = _env(evidence={"x": 1})
    a1 = p.assess(e, None)
    a2 = p.assess(e, None)
    assert a1.risk_score == a2.risk_score
    assert a1.band == a2.band
    assert (a1.escalate, a1.pause, a1.steer) == (a2.escalate, a2.pause, a2.steer)


def test_mock_unknown_evidence_routes_to_verify():
    p = DeterministicMockProvider()
    a = p.assess(_env(), None)  # no evidence field
    assert a.needs_verify is True
    assert a.band == "unknown"


def test_mock_high_risk_escalates():
    p = DeterministicMockProvider()
    found = None
    for i in range(5000):
        cand = _env(event_id=f"risk{i}", evidence={"ok": True})
        a = p.assess(cand, None)
        if a.risk_score >= 0.8:
            found = a
            break
    assert found is not None, "expected a deterministic high-risk seed"
    assert found.escalate is True


def test_disabled_is_verify_leaning():
    p = DisabledProvider()
    a = p.assess(_env(evidence={"x": 1}), None)
    assert a.needs_verify is True
    assert a.risk_score == 0.0
    assert p.is_available() is False


def test_build_provider_default_is_mock():
    c = cfg_mod.Config(server=cfg_mod.ServerConfig(), jev=cfg_mod.JevConfig(provider="mock"))
    assert build_provider(c).name == "mock"


def test_build_provider_real_without_key_falls_back_disabled():
    c = cfg_mod.Config(
        server=cfg_mod.ServerConfig(),
        jev=cfg_mod.JevConfig(provider="real", feature_flags={"real_provider": True}),
    )
    os.environ.pop("JEV_API_KEY", None)
    assert build_provider(c).name == "disabled"


def test_build_provider_real_with_flag_and_key():
    c = cfg_mod.Config(
        server=cfg_mod.ServerConfig(),
        jev=cfg_mod.JevConfig(provider="real", feature_flags={"real_provider": True}),
    )
    os.environ["JEV_API_KEY"] = "dummy"
    try:
        # UNVERIFIED stub; still must not crash and must report real name.
        prov = build_provider(c)
        assert prov.name == "real"
    finally:
        os.environ.pop("JEV_API_KEY", None)
