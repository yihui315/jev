import os

from jcc_core import decision
from jcc_core import config as cfg_mod
from jcc_core.providers import build_provider
from jcc_core.providers.disabled import DisabledProvider
from jcc_core.providers.mock import DeterministicMockProvider
from jcc_core.schemas import AEPEnvelope, AgentName, DirectiveAction, SessionState, SessionStatus


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
    # D-32: the deterministic contract is now carried by the SIGNALS (the P2
    # directive-shaped booleans escalate/pause/steer are no longer emitted).
    assert a1.raw["signals"] == a2.raw["signals"]
    assert decision.decide_action(a1) is decision.decide_action(a2)


def test_mock_emits_the_full_provider_contract():
    """D-32 / Add B: 6 required booleans + definite band + score + verified."""
    p = DeterministicMockProvider()
    a = p.assess(_env(evidence={"ok": True}), None)
    signals = a.raw["signals"]
    for field in decision.REQUIRED_SIGNAL_FIELDS:
        assert field in signals, f"provider omitted required signal {field!r}"
        assert signals[field] is not None
    assert "work_off_track" in signals          # recorded-only, carried anyway
    assert a.band in ("low", "medium", "high", "critical"), a.band
    assert a.band != "unknown"
    assert a.provider_verified is True
    assert a.risk_score == a.probabilities["risk"]   # one numeric channel
    # and it no longer chooses the directive
    assert "escalate" not in a.raw.get("signals", {})


def test_mock_unknown_evidence_routes_to_verify():
    p = DeterministicMockProvider()
    a = p.assess(_env(), None)  # no evidence field
    assert a.needs_verify is True
    assert a.raw["signals"]["needs_verification"] is True
    # The band stays DEFINITE (never "unknown"): an unknown band would be read
    # as band is None and escalate at EVERY risk level (ADR-0008 Add B).
    assert a.band in ("low", "medium", "high", "critical")


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
    # D-32: the mock no longer sets escalate=True; the KERNEL derives it.
    assert found.band == "critical"
    assert decision.decide_action(found) is DirectiveAction.ESCALATE


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
