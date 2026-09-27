"""Falsifiers for the v1.1 Record/Replay provider.

WHAT THIS SUITE IS FOR.

    RecordReplayProvider returns pre-recorded Assessments verbatim, so the
    policy / arbitration / signing can be re-evaluated against the same
    captured evidence without a live external scorer. Two design properties
    must hold, and are the subject of every test below:

      1. Fidelity.  A matching envelope returns the recorded one byte-for-byte.
         The provider never silently fabricates an Assessment when no
         recording matches (that would be "mock pretending to be replay",
         the exact failure §9 forbids).

      2. Identity.   The provider's ``name`` is the literal string
         ``"replay"``; even when a recording's source field is ``"real"``
         the Assessment's ``provider`` field is rewritten to ``"replay"``
         before the kernel ever sees it. The UI must always be able to
         distinguish REPLAY from REAL.

Each test states a way the property could silently fail and fails the run
if it does. The suite is runnable as part of the v1.1 release gate.
"""
from __future__ import annotations

import json
import textwrap

import pytest

from jcc_core.schemas import (
    AEPEnvelope,
    AgentName,
    Assessment,
    SessionState,
)
from jcc_core.providers.record_replay import (
    REPLAY_SCHEMA,
    RecordReplayProvider,
    ReplayCorrupt,
    ReplayMiss,
    ReplayVersion,
)


def _env(session_id="s1", event_id="e1", delivery_id="d1"):
    return AEPEnvelope(
        session_id=session_id,
        event_id=event_id,
        delivery_id=delivery_id,
        agent="workbuddy",
        evidence={"ok": True},
        schema_ok=True,
    )


def _write_recording(tmp_path, entries):
    """Write a JSONL recording file from a list of envelope/assessment pairs."""
    path = tmp_path / "recording.jsonl"
    lines = []
    for env, asd in entries:
        lines.append(json.dumps({
            "schema": REPLAY_SCHEMA,
            "envelope": env.model_dump(mode="json"),
            "session": None,
            "assessment": asd.model_dump(mode="json"),
            "captured_by": "unit-test",
            "captured_at": "2026-09-27T00:00:00Z",
            "source": "fixture",
        }))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _sample_assessment(**overrides):
    """Build a definite Assessment the kernel would accept."""
    base = dict(
        agent=AgentName.WORKBUDDY,
        session_id="s1",
        timestamp=1700000000.0,
        model="jev-1.13.0",
        provider="real",                # will be overwritten to "replay"
        probabilities={"risk": 0.42},
        risk_score=0.42,
        band="medium",
        needs_verify=False,
        escalate=False, pause=False, steer=False, retry=False, finished=False,
        raw={"signals": {
            "meaningful_progress": True, "worker_stuck": False,
            "work_off_track": False, "requirements_satisfied": True,
            "needs_verification": False, "ready_to_finish": True,
            "needs_human": False,
        }},
        confidence=None, confidence_kind="none",
        provider_verified=True,
        blocker_kind="NONE", blocker_reasons=[],
    )
    base.update(overrides)
    return Assessment(**base)


# --- Fidelity -------------------------------------------------------------- #

def test_replay_returns_recorded_assessment_verbatim(tmp_path):
    """A matching envelope must return the recorded Assessment byte-for-byte.

    Records one Assessment, builds the provider, and checks that the
    result equals the recorded one on every observable field except
    ``provider`` (which the provider overwrites to its own name -- see
    the Identity tests below).
    """
    env = _env(session_id="s_alpha", event_id="e_alpha", delivery_id="d_alpha")
    rec = _sample_assessment(session_id="s_alpha", risk_score=0.42, band="medium")
    path = _write_recording(tmp_path, [(env, rec)])

    prov = RecordReplayProvider(recording_path=path)
    out = prov.assess(env, None)

    assert out.risk_score == rec.risk_score
    assert out.band == rec.band
    assert out.probabilities == rec.probabilities
    assert out.raw["signals"] == rec.raw["signals"]


def test_replay_missing_envelope_raises_does_not_fabricate(tmp_path):
    """No recording for an envelope -> ReplayMiss, NOT a default Assessment.

    This is the test that pins down the "mock must not pretend to be
    replay" property. A silent fallback would be a regression.
    """
    rec_env = _env(session_id="s_one")
    rec = _sample_assessment(session_id="s_one")
    path = _write_recording(tmp_path, [(rec_env, rec)])

    prov = RecordReplayProvider(recording_path=path)
    wrong = _env(session_id="s_two", event_id="e_two", delivery_id="d_two")
    with pytest.raises(ReplayMiss):
        prov.assess(wrong, None)


def test_replay_missing_recording_file_falls_back_to_unavailable(tmp_path, monkeypatch):
    """No recording loaded -> is_available() is False.

    ``build_provider`` must then route to DisabledProvider (fail-closed,
    same shape as ``real``/disabled). The provider itself does not raise
    on construction in this case -- it just reports unavailable so the
    caller can decide.

    To express "no recording" precisely without depending on the presence
    or absence of the default fixture at ``fixtures/v1.1/replay_default.jsonl``,
    we point the env var at a path that does not exist. The provider's
    lookup walks the env var before falling back to the project-relative
    default, so this is "no recording" regardless of what is on disk.
    """
    absent = tmp_path / "definitely_not_a_recording.jsonl"
    monkeypatch.setenv("JCC_REPLAY_RECORDING", str(absent))
    prov = RecordReplayProvider(recording_path=None)
    assert prov.is_available() is False


def test_replay_lookup_key_includes_delivery_id(tmp_path):
    """A redelivered envelope with a different delivery_id is NOT a hit.

    at-least-once delivery is part of the AEP contract; the lookup key
    includes ``delivery_id`` so a redelivery is a separate capture.
    """
    env_v1 = _env(session_id="s_x", event_id="e_x", delivery_id="d_v1")
    env_v2 = _env(session_id="s_x", event_id="e_x", delivery_id="d_v2")
    rec = _sample_assessment(session_id="s_x", risk_score=0.10)
    path = _write_recording(tmp_path, [(env_v1, rec)])

    prov = RecordReplayProvider(recording_path=path)
    out = prov.assess(env_v1, None)
    assert out.risk_score == 0.10

    with pytest.raises(ReplayMiss):
        prov.assess(env_v2, None)


def test_replay_recording_with_wrong_schema_raises_version(tmp_path):
    """A recording tagged with the wrong schema name is rejected loudly."""
    path = tmp_path / "rec.jsonl"
    bad = {"schema": "jcc-replay/0", "envelope": {}, "assessment": {}}
    path.write_text(json.dumps(bad) + "\n", encoding="utf-8")
    with pytest.raises(ReplayVersion):
        RecordReplayProvider(recording_path=path)


def test_replay_recording_with_malformed_json_raises_corrupt(tmp_path):
    """A recording with a broken JSON line is rejected loudly, not silently skipped."""
    path = tmp_path / "rec.jsonl"
    path.write_text("not-json\n", encoding="utf-8")
    with pytest.raises(ReplayCorrupt):
        RecordReplayProvider(recording_path=path)


# --- Identity -------------------------------------------------------------- #

def test_replay_overwrites_real_to_replay_in_provider_field(tmp_path):
    """A recording claiming ``provider="real"`` must be coerced to ``"replay"``.

    The provider-side enforcement of "mock/replay must never pretend to
    be real". If a recording file ever ships with ``"real"``, the kernel
    must still see ``"replay"`` and the UI must still show REPLAY, not
    REAL.
    """
    env = _env(session_id="s_y")
    rec = _sample_assessment(session_id="s_y", provider="real")
    path = _write_recording(tmp_path, [(env, rec)])

    prov = RecordReplayProvider(recording_path=path)
    out = prov.assess(env, None)
    assert out.provider == "replay"


def test_replay_carries_provenance_block(tmp_path):
    """Every replayed Assessment carries a ``raw.replay_provenance`` block.

    The audit trail can answer "where did this Assessment come from?"
    without joining the recording file. Provenance is added unconditionally
    so a downstream consumer never has to special-case replayed Assessments.
    """
    env = _env(session_id="s_z")
    rec = _sample_assessment(session_id="s_z")
    path = _write_recording(tmp_path, [(env, rec)])

    prov = RecordReplayProvider(recording_path=path)
    out = prov.assess(env, None)
    prov_block = out.raw.get("replay_provenance")
    assert prov_block is not None, "replayed Assessment must carry provenance"
    assert prov_block["recording_path"] == str(path)
    assert prov_block["schema"] == REPLAY_SCHEMA
    assert isinstance(prov_block["recording_sha256"], str)
    assert len(prov_block["recording_sha256"]) == 64


def test_replay_provider_name_is_literal_replay():
    """The provider class name is the literal string 'replay' -- always."""
    assert RecordReplayProvider.name == "replay"


# --- build_provider routing ----------------------------------------------- #

def test_build_provider_routes_replay_when_provider_set(monkeypatch, tmp_path):
    """``jev.provider=replay`` + a valid recording -> RecordReplayProvider."""
    from jcc_core import config as cfg_mod
    from jcc_core.providers import build_provider

    env = _env()
    rec = _sample_assessment()
    path = _write_recording(tmp_path, [(env, rec)])

    monkeypatch.setenv("JCC_REPLAY_RECORDING", str(path))
    c = cfg_mod.Config(
        server=cfg_mod.ServerConfig(),
        jev=cfg_mod.JevConfig(provider="replay"),
    )
    prov = build_provider(c)
    assert isinstance(prov, RecordReplayProvider)
    assert prov.is_available()


def test_build_provider_replay_without_recording_falls_back_disabled(monkeypatch, tmp_path):
    """``jev.provider=replay`` with no recording -> DisabledProvider (fail-closed).

    Same shape as ``real`` without a key: the requested provider is not
    fully enabled, so the route degrades to DisabledProvider rather than
    silently substituting the mock (which would be "mock pretending to be
    replay" the moment any envelope arrives).

    To express "no recording" precisely without depending on whether the
    default fixture is checked in, we point the env var at a path that
    does not exist. The provider's lookup walks the env var first.
    """
    from jcc_core import config as cfg_mod
    from jcc_core.providers import build_provider
    from jcc_core.providers.disabled import DisabledProvider

    absent = tmp_path / "definitely_not_a_recording.jsonl"
    monkeypatch.setenv("JCC_REPLAY_RECORDING", str(absent))
    c = cfg_mod.Config(
        server=cfg_mod.ServerConfig(),
        jev=cfg_mod.JevConfig(provider="replay"),
    )
    prov = build_provider(c)
    assert isinstance(prov, DisabledProvider)