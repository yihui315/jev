import os

from jcc_core import decision
from jcc_core import config as cfg_mod
from jcc_core.providers import build_provider
from jcc_core.providers.disabled import DisabledProvider
from jcc_core.providers.mock import DeterministicMockProvider
from jcc_core.providers.real import RealProvider
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


def test_disabled_declares_abstention_and_emits_no_signals():
    """Errata E-3 / spec §6.16: `disabled` is a DECLARED non-scoring abstention.

    It supplies NO numeric channels and NONE of the six required booleans (D-32:
    a provider must not choose a directive); the kernel resolves it to VERIFY
    (incomplete evidence -> row 5), never ESCALATE.
    """
    p = DisabledProvider()
    a = p.assess(_env(evidence={"x": 1}), None)
    assert p.is_available() is False
    # the declaration, and only the declaration
    assert a.raw.get("provider_abstained") is True
    # no numeric channel, no signal booleans
    assert a.probabilities == {}
    assert not a.risk_score
    assert "signals" not in a.raw
    assert a.needs_verify is False          # not one of the six, and not emitted
    # still the safe default -> VERIFY, and provably NOT a blocker
    assert decision.decide_action(a) is DirectiveAction.VERIFY
    assert decision.find_blockers(decision.normalize(a), a) == ()


def test_real_provider_escalates_not_verify():
    """Frozen F2 / Errata E-3: `real` is UNVERIFIED and **not** declared-abstaining,
    so its absent signals ARE an EXACT_BLOCKER -> ESCALATE (contrast `disabled`)."""
    p = RealProvider(api_key="dummy")
    a = p.assess(_env(evidence={"x": 1}), None)
    assert a.provider_verified is False
    assert "provider_abstained" not in a.raw
    assert decision.BLOCKER_PROVIDER_UNVERIFIED in decision.find_blockers(
        decision.normalize(a), a
    )
    assert decision.decide_action(a) is DirectiveAction.ESCALATE


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


def test_build_provider_real_fallback_routes_the_abstention_contract():
    """Errata E-3 (atlas-2's Q): the real-requested-but-absent fallback must reach
    the SAME declared-abstention contract as ``jev.provider=disabled`` — not a
    weaker path. The declaration lives in ``DisabledProvider.assess()``, so both
    paths (``build_provider`` line 26 and line 43) are the same class -> identical.
    """
    c = cfg_mod.Config(
        server=cfg_mod.ServerConfig(),
        jev=cfg_mod.JevConfig(provider="real", feature_flags={"real_provider": True}),
    )
    os.environ.pop("JEV_API_KEY", None)
    prov = build_provider(c)
    assert isinstance(prov, DisabledProvider)
    a = prov.assess(_env(evidence={"x": 1}), None)
    assert a.raw.get("provider_abstained") is True
    assert a.provider_verified is True
    assert decision.decide_action(a) is DirectiveAction.VERIFY


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
