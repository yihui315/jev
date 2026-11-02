"""§19 — the live-shadow gate artifact must be an EMISSION, not a snapshot.

WHAT THIS EXISTS TO PREVENT
    §19 requires that an unmet observation window produces
    ``PENDING_LIVE_SHADOW_GATE`` with ``actuation_enabled=false``. Three ways that
    requirement can rot into a green light, all of which this file can fail on:

1. **The artifact becomes a snapshot.** If the only assertion were "the status is
   PENDING", the test would fail the day the shadow plane legitimately
   accumulates observations — teaching whoever hits it to relax the assertion.
   So the load-bearing assertion is the one that holds for EVERY possible tape:
   ``actuation_enabled is False`` and ``effective_level == "L0"``, because the
   emitter's criteria pin ``granted_ceiling`` at L0. Reaching the threshold
   cannot flip it.
2. **The artifact drifts from the enforcement.** The ladder and the gate
   vocabulary are asserted against ``actuation_levels.describe()`` /
   ``live_shadow_gate.describe()`` — the same tables the enforcement reads — so
   a UI cannot read a document that describes a system nobody runs.
3. **The generator touches the protected runtime database.** The emitter must
   open it read-only and immutable. Asserted on the SOURCE (the URI is the
   mechanism; a behavioural test would need a write to detect a write), because
   the constraint is "byte-identical", not "usually fine".

The reproducibility test runs the real ``--check`` and also tampers with a copy,
so the check is proven to be able to FAIL rather than merely to pass.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

ARTIFACT_REL = "artifacts/release/V1_1_LIVE_SHADOW_GATE.json"
GEN_REL = "tools/v1_1_emit_shadow_gate.py"

ARTIFACT_PATH = REPO / ARTIFACT_REL

#: Present, and PENDING is the expected state on the shipped configuration. Both
#: are lawful; the third word in §19's vocabulary does not exist.
ALLOWED_STATUSES = ("PENDING_LIVE_SHADOW_GATE", "SATISFIED")


def _git(*args: str):
    return subprocess.run(["git", "-C", str(REPO), *args],
                          capture_output=True, text=True)


def _load_gen():
    spec = importlib.util.spec_from_file_location("v1_1_shadow_gate", REPO / GEN_REL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def artifact() -> dict:
    return json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def gen():
    return _load_gen()


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "rel",
    [
        ARTIFACT_REL,
        GEN_REL,
        "packages/supervisor/live_shadow_gate.py",
        "packages/supervisor/actuation_levels.py",
    ],
)
def test_the_artifact_and_everything_it_reads_are_in_head(rel):
    """Asserted on the EXIT CODE, never on stderr prose.

    The two enforcement modules are included because the artifact EMBEDS their
    ``describe()`` output: an uncommitted module would make the artifact
    unreproducible from a fresh clone even though the artifact itself is tracked.
    """
    proc = _git("cat-file", "-e", "HEAD:%s" % rel)
    assert proc.returncode == 0, (
        "%s is not in HEAD, so a fresh clone cannot reproduce this record. "
        "git said: %s" % (rel, proc.stderr.strip())
    )


# --------------------------------------------------------------------------- #
# The invariant that holds for every tape
# --------------------------------------------------------------------------- #
def test_actuation_is_off_and_the_effective_level_is_l0(artifact):
    """The headline invariant — true for an EMPTY tape, a FULL tape, and a
    satisfied tape alike, because the emitter pins ``granted_ceiling`` at L0."""
    head = artifact["headline"]
    assert head["actuation_enabled"] is False
    assert head["effective_level"] == "L0"
    assert head["granted_ceiling"] == "L0"
    assert head["status"] in ALLOWED_STATUSES


def test_the_verdict_agrees_with_the_headline(artifact):
    """The document must not have two opinions about the same fact."""
    verdict = artifact["verdict"]
    head = artifact["headline"]
    assert verdict["status"] == head["status"]
    assert verdict["actuation_enabled"] == head["actuation_enabled"]
    assert verdict["effective_level"] == head["effective_level"]
    assert verdict["satisfied"] == head["satisfied"]


def test_a_non_satisfied_verdict_names_its_defects(artifact):
    """A refusal with no reason is not reviewable."""
    verdict = artifact["verdict"]
    if not verdict["satisfied"]:
        assert verdict["defects"], "the gate refused without saying why"
        known = set(artifact["gate_vocabulary"]["defect_codes"])
        assert set(verdict["defects"]) <= known, "a defect code outside the pinned vocabulary"


def test_the_defect_codes_are_from_the_pinned_vocabulary(artifact):
    described = set(artifact["gate_vocabulary"]["defect_codes"])
    assert described == {
        "no_observations", "insufficient_observations", "insufficient_observation_window",
        "actuation_observed", "shadow_chain_broken", "shadow_tape_absent",
        "storage_unavailable",
    }
    assert artifact["gate_vocabulary"]["pending_status"] == "PENDING_LIVE_SHADOW_GATE"
    assert artifact["gate_vocabulary"]["pending_forces_actuation_enabled_false"] is True


# --------------------------------------------------------------------------- #
# The artifact describes the system that is actually enforced
# --------------------------------------------------------------------------- #
def test_the_embedded_ladder_equals_the_enforcement_table(artifact, gen):
    from supervisor import actuation_levels as AL

    assert artifact["actuation_ladder"] == list(AL.describe())
    assert [row["level"] for row in artifact["actuation_ladder"]] == ["L0", "L1", "L2", "L3"]
    assert [row["capability_count"] for row in artifact["actuation_ladder"]] == [0, 0, 5, 7]
    assert [row["gate_runs"] for row in artifact["actuation_ladder"]] == [False, True, True, True]


def test_the_embedded_gate_vocabulary_equals_the_enforcement_table(artifact):
    from supervisor import live_shadow_gate as LG

    assert artifact["gate_vocabulary"] == LG.describe()


def test_the_artifact_states_the_safe_completion_is_lawful(artifact):
    assert "LAWFUL" in artifact["note"]
    assert artifact["schema"] == "jcc.v1_1.live_shadow_gate/v1"


# --------------------------------------------------------------------------- #
# The protected database is read, never written
# --------------------------------------------------------------------------- #
def test_the_emitter_opens_the_database_read_only_and_immutable(artifact):
    """``immutable=1`` is the load-bearing half: it tells SQLite the file cannot
    change, so no lock is taken and no -shm/-wal side file is created next to a
    database the protected v1.0 service is using."""
    source = (REPO / GEN_REL).read_text(encoding="utf-8")
    assert "mode=ro" in source
    assert "immutable=1" in source
    assert artifact["db"]["db_uri"] == "mode=ro&immutable=1"


def test_the_emitter_has_no_write_mode_escape_hatch():
    """No parameter may switch the connection to writable. Checked on the source
    because the absence of a branch is what is being asserted."""
    source = (REPO / GEN_REL).read_text(encoding="utf-8")
    for banned in ("mode=rw", "mode=rwc", "IMMEDIATE", "CREATE TABLE", "execute(\"UPDATE"):
        assert banned not in source, "the emitter grew a writable path: %s" % banned


# --------------------------------------------------------------------------- #
# Reproducibility — and the check can FAIL
# --------------------------------------------------------------------------- #
def test_check_mode_reports_the_artifact_is_current():
    """RUNS the checker. Reading its source would prove nothing about the file."""
    proc = subprocess.run(
        [sys.executable, str(REPO / GEN_REL), "--check"],
        cwd=str(REPO), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300,
    )
    assert proc.returncode == 0, (
        "the committed artifact is stale relative to the database:\n%s"
        % (proc.stdout + proc.stderr)[:1500]
    )


def test_two_consecutive_derivations_are_identical(gen):
    """Determinism of the normalised document — the property ``--check`` relies on."""
    first = gen.derive()
    second = gen.derive()
    assert gen._normalize(first) == gen._normalize(second)
    assert gen._digest(first) == gen._digest(second)


def test_the_normalised_digest_ignores_the_clock_and_the_path(gen):
    """Non-vacuity for the digest: the two fields that change every run must not
    change it, or ``--check`` would fail every time and be ignored."""
    base = gen.derive()
    mutated = json.loads(json.dumps(base))
    mutated["verdict"]["evaluated_at"] = base["verdict"]["evaluated_at"] + 12345.0
    mutated["db"]["db_path"] = "C:/somewhere/else.db"
    mutated["generated_at"] = "whenever"
    assert gen._digest(base) == gen._digest(mutated)


def test_the_digest_changes_when_a_real_verdict_field_changes(gen):
    """The other direction, so the digest is not simply constant."""
    base = gen.derive()
    mutated = json.loads(json.dumps(base))
    mutated["headline"]["status"] = "SATISFIED"
    assert gen._digest(base) != gen._digest(mutated)


def test_check_mode_can_actually_fail(tmp_path, monkeypatch, gen):
    """Tampers with a copy of the artifact and asserts ``--check`` rejects it.

    Without this, a ``--check`` that always returned 0 would pass the test above.
    """
    tampered = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    tampered["headline"]["actuation_enabled"] = True
    fake = tmp_path / ARTIFACT_REL
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_text(json.dumps(tampered), encoding="utf-8")

    monkeypatch.setattr(gen, "OUT", fake)
    assert gen.main(["--check"]) == 1, "the checker accepted a tampered artifact"
