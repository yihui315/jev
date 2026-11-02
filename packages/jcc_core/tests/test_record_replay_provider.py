"""Falsifiers for the v1.1 Record/Replay provider (§8 + §9A hardened).

WHAT THIS SUITE IS FOR.

    RecordReplayProvider returns pre-recorded Assessments verbatim, so the
    policy / arbitration / signing can be re-evaluated against the same captured
    evidence without a live external scorer. Four design properties must hold,
    and are the subject of every test below:

      1. Fidelity.  A matching envelope returns the recorded one; the provider
         never silently fabricates an Assessment when no recording matches
         (that would be "mock pretending to be replay").

      2. Identity.  The provider's ``name`` is the literal string ``"replay"``;
         even when a recording's source field is ``"real"`` the Assessment's
         ``provider`` field is rewritten to ``"replay"``. The UI must always be
         able to distinguish REPLAY from REAL.

      3. EXACT match (§9A).  The lookup key narrows, the ``input_digest``
         decides. There is no nearest match and no fallback, so reusing ids
         across different payloads is a MISS, not a hit.

      4. Evidence integrity (§9A).  A recording is committed to version control
         and later read as evidence, so every way it can be wrong -- edited,
         truncated, ambiguous, secret-bearing, legacy -- must fail loudly rather
         than produce a plausible-looking number.

Each test states a way a property could silently fail and fails the run if it
does.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jcc_core.schemas import (
    AEPEnvelope,
    AgentName,
    Assessment,
)
from jcc_core.providers.record_replay import (
    PROVIDER_SOURCES,
    REPLAY_SCHEMA,
    REPLAY_SCHEMA_V1,
    REQUIRED_ENTRY_FIELDS,
    RecordReplayProvider,
    ReplayCorrupt,
    ReplayMiss,
    ReplayVersion,
    build_entry,
    fixture_digest_for,
    input_digest_for,
    write_recording,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SHIPPED_FIXTURE = REPO_ROOT / "fixtures" / "v1.1" / "replay_default.jsonl"

RECORDING_ID = "rec_unit_test"
CREATED_AT = "2026-09-27T00:00:00Z"


def _env(session_id="s1", event_id="e1", delivery_id="d1", **kw):
    base = dict(
        session_id=session_id,
        event_id=event_id,
        delivery_id=delivery_id,
        agent="workbuddy",
        evidence={"ok": True},
        schema_ok=True,
    )
    base.update(kw)
    return AEPEnvelope(**base)


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


def _entry(env, assessment, **overrides):
    kw = dict(
        envelope=env,
        assessment=assessment,
        session=None,
        recording_id=RECORDING_ID,
        created_at=CREATED_AT,
        provider_source="fixture",
        captured_by="unit-test",
    )
    kw.update(overrides)
    return build_entry(**kw)


def _write_recording(tmp_path, entries, name="recording.jsonl"):
    """Write a v2 JSONL recording from a list of (envelope, assessment) pairs."""
    return _write_raw(tmp_path, [_entry(e, a) for e, a in entries], name=name)


def _write_raw(tmp_path, dicts, name="recording.jsonl"):
    path = tmp_path / name
    path.write_text(
        "".join(json.dumps(d) + "\n" for d in dicts), encoding="utf-8", newline="\n")
    return path


# --- Fidelity -------------------------------------------------------------- #

def test_replay_returns_recorded_assessment_verbatim(tmp_path):
    """A matching envelope must return the recorded Assessment verbatim."""
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
    """No recording for an envelope -> ReplayMiss, NOT a default Assessment."""
    rec_env = _env(session_id="s_one")
    rec = _sample_assessment(session_id="s_one")
    path = _write_recording(tmp_path, [(rec_env, rec)])

    prov = RecordReplayProvider(recording_path=path)
    wrong = _env(session_id="s_two", event_id="e_two", delivery_id="d_two")
    with pytest.raises(ReplayMiss):
        prov.assess(wrong, None)


def test_replay_missing_recording_file_falls_back_to_unavailable(tmp_path, monkeypatch):
    """No recording loaded -> is_available() is False.

    ``build_provider`` then routes to DisabledProvider (fail-closed). A MISSING
    file is a configuration state, deliberately NOT an exception -- contrast the
    empty/named-recording cases below, which ARE exceptions.
    """
    absent = tmp_path / "definitely_not_a_recording.jsonl"
    monkeypatch.setenv("JCC_REPLAY_RECORDING", str(absent))
    prov = RecordReplayProvider(recording_path=None)
    assert prov.is_available() is False


def test_replay_lookup_key_includes_delivery_id(tmp_path):
    """A redelivered envelope with a different delivery_id is NOT a hit."""
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
    """A recording tagged with the wrong schema version is rejected loudly."""
    path = tmp_path / "rec.jsonl"
    bad = {"schema_version": "jcc-replay/0", "envelope": {}, "assessment": {}}
    path.write_text(json.dumps(bad) + "\n", encoding="utf-8")
    with pytest.raises(ReplayVersion):
        RecordReplayProvider(recording_path=path)


def test_replay_recording_with_malformed_json_raises_corrupt(tmp_path):
    """A broken JSON line is rejected loudly, not silently skipped."""
    path = tmp_path / "rec.jsonl"
    path.write_text("not-json\n", encoding="utf-8")
    with pytest.raises(ReplayCorrupt):
        RecordReplayProvider(recording_path=path)


# --- Identity -------------------------------------------------------------- #

def test_replay_overwrites_real_to_replay_in_provider_field(tmp_path):
    """A recording claiming ``provider="real"`` must be coerced to ``"replay"``."""
    env = _env(session_id="s_y")
    rec = _sample_assessment(session_id="s_y", provider="real")
    path = _write_recording(tmp_path, [(env, rec)])

    prov = RecordReplayProvider(recording_path=path)
    out = prov.assess(env, None)
    assert out.provider == "replay"


def test_replay_carries_provenance_block(tmp_path):
    """Every replayed Assessment carries a ``raw.replay_provenance`` block."""
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


# --- §9A: exact match ------------------------------------------------------ #

def test_index_hit_with_a_different_input_digest_is_a_miss_not_a_hit(tmp_path):
    """THE §9A rule. Reusing ids across different payloads must not match.

    Without the digest, the only binding between a record and its input is three
    id strings, so a recording captured from a different payload with the same
    ids would be served as a hit -- a fidelity violation that looks exactly like
    a success.
    """
    recorded_env = _env(session_id="s_d", event_id="e_d", delivery_id="dd",
                        evidence={"ok": True, "step": 1})
    rec = _sample_assessment(session_id="s_d")
    path = _write_recording(tmp_path, [(recorded_env, rec)])
    prov = RecordReplayProvider(recording_path=path)

    # same ids, different payload
    other = _env(session_id="s_d", event_id="e_d", delivery_id="dd",
                 evidence={"ok": True, "step": 2})
    with pytest.raises(ReplayMiss) as exc:
        prov.assess(other, None)
    assert "input_digest" in str(exc.value), (
        "the miss must say the digest is why -- otherwise an operator sees an "
        f"ordinary 'not found' and re-records blindly: {exc.value}"
    )


def test_provenance_names_the_recording_and_the_exact_input(tmp_path):
    """§9A fields must reach the consumer, not just the file."""
    env = _env(session_id="s_prov")
    rec = _sample_assessment(session_id="s_prov")
    path = _write_recording(tmp_path, [(env, rec)])
    prov = RecordReplayProvider(recording_path=path)
    block = prov.assess(env, None).raw["replay_provenance"]

    assert block["recording_id"] == RECORDING_ID
    assert block["created_at"] == CREATED_AT
    assert block["provider_source"] == "fixture"
    assert block["schema_version"] == REPLAY_SCHEMA
    assert block["input_digest"] == input_digest_for(env, None)
    assert block["fixture_digest"] == fixture_digest_for(
        rec)
    assert prov.recording_id == RECORDING_ID


def test_input_digest_is_stable_and_input_sensitive():
    """A digest that did not change with the input would pass every other test."""
    a = _env(session_id="s", evidence={"n": 1})
    b = _env(session_id="s", evidence={"n": 2})
    assert input_digest_for(a, None) == input_digest_for(_env(session_id="s", evidence={"n": 1}), None)
    assert input_digest_for(a, None) != input_digest_for(b, None)
    assert len(input_digest_for(a, None)) == 64


# --- §9A: evidence integrity ----------------------------------------------- #

def test_legacy_v1_recording_is_rejected_with_a_migration_hint(tmp_path):
    """v1 carried no digests, so its entries cannot be verified at all.

    Accepting it would mean accepting unverifiable evidence. The error must name
    the migration, not just say "wrong version".
    """
    path = tmp_path / "v1.jsonl"
    path.write_text(json.dumps({
        "schema": REPLAY_SCHEMA_V1,
        "envelope": {}, "session": None, "assessment": {},
        "captured_by": "old", "captured_at": CREATED_AT, "source": "fixture",
    }) + "\n", encoding="utf-8")
    with pytest.raises(ReplayVersion) as exc:
        RecordReplayProvider(recording_path=path)
    message = str(exc.value)
    assert REPLAY_SCHEMA in message, (
        "the error must name the schema a v1 recording has to be migrated TO"
    )
    # case-insensitive: the phrase is sentence-initial in the message, and a
    # capitalisation difference is not what this test is about.
    assert "re-record" in message.lower(), message


def test_empty_recording_file_is_corrupt_not_unavailable(tmp_path):
    """A NAMED recording with nothing in it cannot replay anything.

    Treating it as "unavailable" would let the fail-closed fallback to
    DisabledProvider look like a working replay configuration.
    """
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ReplayCorrupt) as exc:
        RecordReplayProvider(recording_path=path)
    assert "no entries" in str(exc.value)


def test_duplicate_lookup_key_is_rejected_not_last_wins(tmp_path):
    """Two captures for one triple = an ambiguous recording, not a preference.

    The previous behaviour was silently "last wins". That picks an answer the
    operator never chose, and the choice is invisible in the resulting number.
    """
    env = _env(session_id="s_dup")
    first = _entry(env, _sample_assessment(session_id="s_dup", risk_score=0.11))
    second = _entry(env, _sample_assessment(session_id="s_dup", risk_score=0.99))
    path = _write_raw(tmp_path, [first, second])
    with pytest.raises(ReplayCorrupt) as exc:
        RecordReplayProvider(recording_path=path)
    assert "repeats lookup key" in str(exc.value)


def test_tampered_assessment_is_caught_by_fixture_digest(tmp_path):
    """Editing the recorded assessment must be loud, not plausible."""
    env = _env(session_id="s_t")
    entry = _entry(env, _sample_assessment(session_id="s_t", risk_score=0.10))
    entry["assessment"]["risk_score"] = 0.99          # the edit
    path = _write_raw(tmp_path, [entry])
    with pytest.raises(ReplayCorrupt) as exc:
        RecordReplayProvider(recording_path=path)
    assert "fixture_digest MISMATCH" in str(exc.value)
    assert "edited after capture" in str(exc.value)


def test_tampered_envelope_is_caught_by_input_digest(tmp_path):
    """Editing the recorded input must be loud: the entry no longer agrees with itself."""
    env = _env(session_id="s_u")
    entry = _entry(env, _sample_assessment(session_id="s_u"))
    entry["envelope"]["evidence"] = {"ok": False}     # the edit
    path = _write_raw(tmp_path, [entry])
    with pytest.raises(ReplayCorrupt) as exc:
        RecordReplayProvider(recording_path=path)
    assert "input_digest MISMATCH" in str(exc.value)
    assert "self-inconsistent" in str(exc.value)


@pytest.mark.parametrize("field", REQUIRED_ENTRY_FIELDS)
def test_every_required_metadata_field_is_enforced_and_named(tmp_path, field):
    """Drop one field at a time; each must be caught BY NAME."""
    env = _env(session_id="s_m")
    entry = _entry(env, _sample_assessment(session_id="s_m"))
    entry.pop(field)
    path = _write_raw(tmp_path, [entry], name=f"missing_{field}.jsonl")
    with pytest.raises((ReplayCorrupt, ReplayVersion)) as exc:
        RecordReplayProvider(recording_path=path)
    assert field in str(exc.value), f"the error did not name the missing {field!r}"


def test_inconsistent_recording_id_is_rejected(tmp_path):
    """Two recordings concatenated into one file is a mixture, not a recording."""
    e1 = _env(session_id="s_a")
    e2 = _env(session_id="s_b")
    a = _entry(e1, _sample_assessment(session_id="s_a"), recording_id="rec_a")
    b = _entry(e2, _sample_assessment(session_id="s_b"), recording_id="rec_b")
    path = _write_raw(tmp_path, [a, b])
    with pytest.raises(ReplayCorrupt) as exc:
        RecordReplayProvider(recording_path=path)
    assert "recording_id" in str(exc.value) and "concatenated" in str(exc.value)


def test_secret_shaped_key_names_are_rejected(tmp_path):
    """A recording is committed to version control, so a credential in it is a leak."""
    env = _env(session_id="s_k")
    entry = _entry(env, _sample_assessment(session_id="s_k"))
    entry["envelope"]["evidence"] = {"api_key": "abcd1234abcd1234"}
    entry["input_digest"] = input_digest_for(entry["envelope"], entry["session"])
    path = _write_raw(tmp_path, [entry])
    with pytest.raises(ReplayCorrupt) as exc:
        RecordReplayProvider(recording_path=path)
    assert "$.envelope.evidence.api_key" in str(exc.value)


def test_secret_shaped_values_are_rejected_and_never_echoed(tmp_path):
    """A secret hidden inside free text must be caught WITHOUT being printed.

    The error message is itself committed evidence, so echoing the value would
    move the leak rather than stop it.
    """
    secret = "Bearer sk-abcdefghijklmnopqrstuvwx"
    env = _env(session_id="s_v")
    entry = _entry(env, _sample_assessment(session_id="s_v"))
    entry["envelope"]["evidence"] = {"command": f"curl -H '{secret}' https://x"}
    entry["input_digest"] = input_digest_for(entry["envelope"], entry["session"])
    path = _write_raw(tmp_path, [entry])
    with pytest.raises(ReplayCorrupt) as exc:
        RecordReplayProvider(recording_path=path)
    message = str(exc.value)
    assert "$.envelope.evidence.command" in message
    assert "sk-abcdefghijklmnopqrstuvwx" not in message, "the secret was echoed"


def test_the_writer_refuses_to_emit_a_secret_at_all():
    """Caught at WRITE time too: the recorder must not create the problem."""
    env = _env(session_id="s_w", evidence={"api_key": "abcd1234abcd1234"})
    with pytest.raises(ValueError) as exc:
        _entry(env, _sample_assessment(session_id="s_w"))
    assert "secret-shaped" in str(exc.value)


def test_the_writer_rejects_an_unknown_provider_source():
    with pytest.raises(ValueError) as exc:
        _entry(_env(), _sample_assessment(), provider_source="guesswork")
    assert "provider_source" in str(exc.value)


def test_build_entry_uses_the_same_digest_rules_as_the_loader(tmp_path):
    """Recorder and verifier must share ONE implementation, measured not assumed."""
    env = _env(session_id="s_shared")
    rec = _sample_assessment(session_id="s_shared")
    entry = _entry(env, rec)
    # the entry the writer produced must survive the loader's own checks
    path = _write_raw(tmp_path, [entry])
    prov = RecordReplayProvider(recording_path=path)
    assert prov.entry_count == 1
    assert prov.assess(env, None).risk_score == rec.risk_score


def test_finite_out_of_range_probability_is_passed_through_for_the_kernel_to_block(
    tmp_path,
):
    """§9A "invalid probability": fail closed at the layer that OWNS the rule.

    The kernel has a dedicated ``probability_out_of_range`` blocker, so a finite
    out-of-range probability is exactly the case the kernel exists to catch.
    Rejecting it in the loader would make that blocker unreachable from replay,
    removing the evidence that it works. What must NOT happen is silent
    acceptance -- so that is what this asserts.
    """
    from jcc_core import decision

    env = _env(session_id="s_p")
    rec = _sample_assessment(session_id="s_p", probabilities={"risk": 1.5},
                             risk_score=1.5)
    path = _write_recording(tmp_path, [(env, rec)])
    prov = RecordReplayProvider(recording_path=path)
    out = prov.assess(env, None)

    blockers = decision.find_blockers(decision.normalize(out), out)
    assert decision.BLOCKER_PROBABILITY_OUT_OF_RANGE in blockers, (
        "an out-of-range probability was accepted WITHOUT the kernel blocking it: "
        f"blockers={blockers}"
    )


# --- The shipped fixture --------------------------------------------------- #

def test_the_shipped_fixture_exists_and_loads():
    """The default recording path must work out of the box, or demo mode lies."""
    assert SHIPPED_FIXTURE.is_file(), f"missing {SHIPPED_FIXTURE}"
    prov = RecordReplayProvider(recording_path=SHIPPED_FIXTURE)
    assert prov.is_available() is True
    assert prov.entry_count >= 1
    assert prov.recording_id, "the shipped fixture must declare a recording_id"


def test_every_shipped_fixture_entry_survives_every_9a_check(tmp_path):
    """Re-verify the fixture line by line: loading it already proves this, so
    the point is to fail with a NAME when a future edit breaks one entry."""
    raw = SHIPPED_FIXTURE.read_text(encoding="utf-8")
    lines = [ln for ln in raw.splitlines() if ln.strip() and not ln.startswith("#")]
    assert lines, "the fixture has no entries"
    for i, line in enumerate(lines, 1):
        entry = json.loads(line)
        assert entry["schema_version"] == REPLAY_SCHEMA, f"fixture line {i}"
        assert entry["provider_source"] in PROVIDER_SOURCES, f"fixture line {i}"
        assert entry["fixture_digest"] == fixture_digest_for(entry["assessment"]), (
            f"fixture line {i} fixture_digest does not recompute"
        )


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
    """``jev.provider=replay`` with no recording -> DisabledProvider (fail-closed)."""
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
