"""§10 — the v1.1 capability matrix must be a measurement, not a table.

WHAT THIS EXISTS TO PREVENT
    §10's matrix is the artifact a reader uses to decide what JCC can actually do
    to a worker. Three ways it goes wrong, all of which this file can fail on:

1. **A claim nobody re-checks.** The probe reports a per-capability status, and
   the matrix is built from it. If the matrix simply copied the word "VERIFIED",
   a fixture-backed or stale record would be published as a live capability.
   So every VERIFIED is re-derived here from the evidence file AND the
   filesystem, and the states a lie would produce (fixture-sourced, missing
   record) are exercised directly.
2. **A fourth label with no meaning.** DEGRADED is only useful if something can
   reach it. Two tests below drive the code into DEGRADED from both directions,
   so it cannot quietly become decoration.
3. **An artifact that cannot be reproduced.** ``artifacts/acceptance/`` is
   gitignored, so the probe evidence is untracked while the matrix is tracked.
   That is why the matrix carries a digest of the evidence -- and why it must
   NOT carry a digest of a live record file, which is append-only and would make
   the matrix change on every run.

The two directions are tested separately on purpose: "is it consistent with the
evidence" and "does it say the right thing" each pass on an artifact that is
wrong in the other direction.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

MATRIX_REL = "artifacts/release/V1_1_CAPABILITY_MATRIX.json"
GEN_REL = "tools/v1_1_emit_capability_matrix.py"
PROBE_REL = "tools/probe_adapter_capabilities.py"

MATRIX_PATH = REPO / MATRIX_REL

#: §10's vocabulary, written out. If the artifact grows a fifth word, this is
#: what says so.
FOUR_STATES = ("VERIFIED", "DEGRADED", "UNSUPPORTED", "UNVERIFIED")
AGENTS = ("workbuddy", "codex", "claude", "hermes")


def _git(*args: str):
    return subprocess.run(["git", "-C", str(REPO), *args],
                          capture_output=True, text=True)


def _load_gen():
    spec = importlib.util.spec_from_file_location(
        "v1_1_capability_matrix", REPO / GEN_REL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def matrix() -> dict:
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def gen():
    return _load_gen()


# --------------------------------------------------------------------------- #
# Reproducibility
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("rel", [MATRIX_REL, GEN_REL, PROBE_REL])
def test_the_artifact_and_its_generator_are_in_head(rel):
    """HEAD, not the index -- an index-only file is absent from a fresh clone.

    Asserted on the EXIT CODE, never on stderr prose: the two look similar and
    only one of them is a fact.
    """
    proc = _git("cat-file", "-e", "HEAD:%s" % rel)
    assert proc.returncode == 0, (
        "%s is not in HEAD, so a fresh clone cannot reproduce this record. "
        "git said: %s" % (rel, proc.stderr.strip())
    )


def test_check_mode_reports_the_matrix_is_current():
    """RUNS the checker. Reading its source would prove nothing about the file."""
    proc = subprocess.run(
        [sys.executable, str(REPO / GEN_REL), "--check"],
        cwd=str(REPO), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300,
    )
    assert proc.returncode == 0, (
        "the committed matrix is stale relative to the probe evidence:\n%s"
        % (proc.stdout + proc.stderr)[:1500]
    )


@pytest.fixture(scope="module")
def fresh_probe(tmp_path_factory):
    """Run the probe ONCE into a scratch directory, for both tests below.

    Redirected on purpose. The evidence it writes carries ``probe_at`` and records
    whichever live session file this host happens to have, so it is not
    byte-reproducible -- writing it to the tracked location would leave the
    working tree dirty after every suite run and break the release gate's
    clean-tree check. The probe is exercised; the tracked evidence is left alone.
    """
    out = tmp_path_factory.mktemp("capability_probe")
    proc = subprocess.run(
        [sys.executable, str(REPO / PROBE_REL), "--out-dir", str(out)],
        cwd=str(REPO), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=600,
        env=dict(os.environ),
    )
    return out, proc


def test_the_adapter_probe_runs_green_on_this_host(fresh_probe, gen):
    """Closes the gap that nothing in this suite ran the adapter probe at all.

    Before this, the probe could break, or start emitting a status outside §10's
    vocabulary, and every check would stay green while the matrix described a
    probe that no longer worked.
    """
    out, proc = fresh_probe
    assert proc.returncode == 0, (
        "the adapter capability probe did not exit 0:\n%s"
        % (proc.stdout + proc.stderr)[-2000:]
    )
    for agent in AGENTS:
        p = out / ("%s.json" % agent)
        assert p.is_file(), "the probe exited 0 without writing %s" % p.name
        doc = json.loads(p.read_text(encoding="utf-8"))
        assert doc["adapter"] == agent
        assert doc["probe_exit_code"] == 0, (agent, doc["probe_exit_code"])
        assert doc["status"] in FOUR_STATES, (agent, doc["status"])
        for cap in gen.CAPABILITIES:
            assert doc["capabilities"][cap]["status"] in FOUR_STATES, (agent, cap)


def test_the_probe_writes_lf_into_tracked_evidence(fresh_probe):
    """Regression guard: ``.gitattributes`` says ``eol=lf`` for everything.

    The probe wrote CRLF, so every run produced a spurious diff in a TRACKED
    evidence file. Churn like that is not cosmetic -- it buries a real change in
    line-ending noise, and it was invisible for as long as nothing ran the probe
    from the suite.
    """
    out, _ = fresh_probe
    for agent in AGENTS:
        raw = (out / ("%s.json" % agent)).read_bytes()
        assert b"\r\n" not in raw, "%s.json contains CRLF" % agent


# --------------------------------------------------------------------------- #
# The vocabulary
# --------------------------------------------------------------------------- #
def test_the_matrix_uses_only_the_four_permitted_states(matrix):
    """§10 permits four states. A fifth word would make the table unreadable."""
    seen = set()
    for agent in AGENTS:
        blk = matrix["agents"][agent]
        assert blk["adapter_state"] in FOUR_STATES, (agent, blk["adapter_state"])
        for cap, entry in blk["capabilities"].items():
            seen.add(entry["status"])
    assert seen <= set(FOUR_STATES), "states outside §10's vocabulary: %s" % sorted(seen - set(FOUR_STATES))
    assert matrix["vocabulary"] == list(FOUR_STATES)


def test_all_four_agents_are_present_in_the_declared_order(matrix):
    assert matrix["probe_order"] == list(AGENTS)
    assert sorted(matrix["agents"]) == sorted(AGENTS)


def test_every_agent_covers_every_capability(matrix):
    """A shorter table would silently drop a column."""
    declared = set(matrix["capabilities"])
    for agent in AGENTS:
        assert set(matrix["agents"][agent]["capabilities"]) == declared, agent


# --------------------------------------------------------------------------- #
# "Does it say the right thing" -- the VERIFIED claim
# --------------------------------------------------------------------------- #
def test_every_verified_capability_carries_live_evidence(matrix):
    """A VERIFIED with no evidence is an assertion, not a measurement."""
    for agent in AGENTS:
        for cap, entry in matrix["agents"][agent]["capabilities"].items():
            if entry["status"] != "VERIFIED":
                continue
            assert entry.get("evidence"), "%s/%s is VERIFIED with no evidence" % (agent, cap)
            ev = entry["evidence"]
            assert ev["record_source"] == "live", (
                "%s/%s: a repository fixture is not evidence (record_source=%r)"
                % (agent, cap, ev["record_source"])
            )
            assert ev["record_exists"] is True, "%s/%s: record does not exist" % (agent, cap)
            assert ev["probe_mode"] == "live"


def test_telemetry_verification_includes_resolved_identity(matrix):
    """ADR-0003: identity is session_id, never cwd. A VERIFIED telemetry that
    cannot show a resolved identity would be verifying the wrong thing."""
    for agent in AGENTS:
        entry = matrix["agents"][agent]["capabilities"]["telemetry"]
        assert entry["status"] == "VERIFIED", (agent, entry)
        ev = entry["evidence"]
        assert ev["envelope_session_id_present"] is True, agent
        assert ev["envelope_schema_ok"] is True, agent


# --------------------------------------------------------------------------- #
# "Is it consistent with the evidence" -- and the DEGRADED path
# --------------------------------------------------------------------------- #
def test_the_matrix_agrees_with_the_probe_evidence_on_disk(matrix):
    """Both directions: the file on disk and the tracked record must match.

    Compared on the NORMALISED form, because the evidence carries ``probe_at``
    and the matrix carries ``generated_at``.
    """
    gen = _load_gen()
    assert gen._normalize(gen.derive()) == gen._normalize(matrix)


def test_a_fixture_sourced_verified_becomes_degraded_and_says_why(gen, tmp_path, monkeypatch):
    """The anti-mock guard, driven end to end.

    The probe's own R1 says a repository fixture is NOT sufficient. A stand-in
    wearing the real badge is exactly what §10 forbids publishing, so it must
    come out DEGRADED -- and must say which check caught it.
    """
    ev_dir = tmp_path / "adapter_capability"
    ev_dir.mkdir()
    src = REPO / "artifacts" / "acceptance" / "adapter_capability"
    for agent in AGENTS:
        raw = json.loads((src / ("%s.json" % agent)).read_text(encoding="utf-8"))
        # the lie: still says VERIFIED, but the record came from the repo
        raw["capabilities"]["telemetry"]["status"] = "VERIFIED"
        raw["capabilities_detail"]["telemetry"]["record_source"] = "fixture"
        (ev_dir / ("%s.json" % agent)).write_text(json.dumps(raw), encoding="utf-8")

    monkeypatch.setattr(gen, "EVIDENCE_DIR", ev_dir)
    out = gen.derive()
    for agent in AGENTS:
        entry = out["agents"][agent]["capabilities"]["telemetry"]
        assert entry["status"] == "DEGRADED", (agent, entry)
        assert "fixture" in entry["degraded_reason"]
        assert entry["reported_status"] == "VERIFIED", (
            "the matrix must keep showing what the probe claimed, or the "
            "discrepancy is hidden rather than reported")


def test_a_missing_record_becomes_degraded_not_verified(gen, tmp_path, monkeypatch):
    """The other direction into DEGRADED: the claim outlives its evidence."""
    ev_dir = tmp_path / "adapter_capability"
    ev_dir.mkdir()
    src = REPO / "artifacts" / "acceptance" / "adapter_capability"
    for agent in AGENTS:
        raw = json.loads((src / ("%s.json" % agent)).read_text(encoding="utf-8"))
        raw["capabilities_detail"]["telemetry"]["record_path"] = str(
            tmp_path / "definitely-not-here" / ("%s.json" % agent))
        (ev_dir / ("%s.json" % agent)).write_text(json.dumps(raw), encoding="utf-8")

    monkeypatch.setattr(gen, "EVIDENCE_DIR", ev_dir)
    out = gen.derive()
    for agent in AGENTS:
        entry = out["agents"][agent]["capabilities"]["telemetry"]
        assert entry["status"] == "DEGRADED", (agent, entry)
        assert "does not exist" in entry["degraded_reason"]
        assert entry["evidence"]["record_exists"] is False


def test_an_unmappable_reported_status_does_not_become_verified(gen, tmp_path, monkeypatch):
    """A status outside the vocabulary is UNVERIFIED and NAMED, never rounded up."""
    ev_dir = tmp_path / "adapter_capability"
    ev_dir.mkdir()
    src = REPO / "artifacts" / "acceptance" / "adapter_capability"
    for agent in AGENTS:
        raw = json.loads((src / ("%s.json" % agent)).read_text(encoding="utf-8"))
        raw["capabilities"]["pause"] = {"status": "PROBABLY_FINE", "reason": "vibes"}
        (ev_dir / ("%s.json" % agent)).write_text(json.dumps(raw), encoding="utf-8")

    monkeypatch.setattr(gen, "EVIDENCE_DIR", ev_dir)
    out = gen.derive()
    entry = out["agents"]["workbuddy"]["capabilities"]["pause"]
    assert entry["status"] == "UNVERIFIED"
    assert entry["unmapped_reported_status"] == "PROBABLY_FINE"


# --------------------------------------------------------------------------- #
# Reproducibility of the artifact itself
# --------------------------------------------------------------------------- #
def test_the_matrix_does_not_hash_a_live_record_file(matrix):
    """A digest of an append-only file changes every run.

    ``~/.hermes/gateway_state.json`` is rewritten while this suite runs. Hashing
    it would make the matrix irreproducible and ``--check`` would go red on every
    invocation -- a check that always fails is worse than none, because it
    teaches the operator to ignore it. The reproducibility anchor is the
    normalised probe evidence instead, and this test keeps it that way.
    """
    blob = json.dumps(matrix)
    for forbidden in ("record_sha256", "record_digest", "record_hash"):
        assert forbidden not in blob, (
            "%s appears in the matrix; a live record is append-only and must not "
            "be hashed" % forbidden)


def test_the_evidence_digest_is_stable_across_derivations(gen, matrix):
    """Two derivations from the same bytes must agree, or --check is noise."""
    a = gen.derive()
    b = gen.derive()
    for agent in AGENTS:
        assert a["agents"][agent]["probe_evidence_digest"] == \
            b["agents"][agent]["probe_evidence_digest"], agent
        assert a["agents"][agent]["probe_evidence_digest"] == \
            matrix["agents"][agent]["probe_evidence_digest"], agent
        assert str(matrix["agents"][agent]["probe_evidence_digest"]).startswith("sha256:")


def test_volatile_keys_are_stripped_at_any_depth(gen):
    """Matched by KEY NAME recursively -- the §7F emitter lost a day to a
    top-level-only strip, so the nested case is pinned here."""
    nested = {"a": {"probe_at": "changed"}, "b": [{"generated_at": "changed"}]}
    other = {"a": {"probe_at": "different"}, "b": [{"generated_at": "also-different"}]}
    assert gen._normalize(nested) == gen._normalize(other)


# --------------------------------------------------------------------------- #
# The §10 posture: nothing is actuable
# --------------------------------------------------------------------------- #
def test_the_actuation_summary_counts_match_the_per_agent_table(matrix):
    expected = [
        {"agent": a, "capability": c}
        for a in AGENTS
        for c in matrix["actuation_capabilities"]
        if matrix["agents"][a]["capabilities"][c]["status"] == "VERIFIED"
    ]
    assert matrix["actuation_summary"]["actuable"] == expected
    assert matrix["actuation_summary"]["actuable_capability_count"] == len(expected)


def test_no_actuation_capability_is_verified_on_this_host(matrix):
    """§10's posture, asserted rather than described.

    If this ever goes red it means JCC can move a worker, which must be a
    deliberate, separately-gated change -- not something a matrix edit occurs
    into.
    """
    assert matrix["actuation_summary"]["actuable_capability_count"] == 0, (
        "an actuation capability is VERIFIED: %s"
        % matrix["actuation_summary"]["actuable"])
    for agent in AGENTS:
        assert matrix["agents"][agent]["discovery_actuation_enabled"] is False, agent


def test_check_mode_is_not_vacuous(tmp_path):
    """A checker that cannot fail is a decoration. Tamper, then demand red."""
    doctored = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    doctored["agents"]["workbuddy"]["capabilities"]["telemetry"]["status"] = "DEGRADED"
    target = tmp_path / "V1_1_CAPABILITY_MATRIX.json"
    target.write_text(json.dumps(doctored), encoding="utf-8")

    gen = _load_gen()
    gen.OUT = target
    rc = gen.main(["--check"])
    assert rc == 1, "the checker passed a tampered matrix"
