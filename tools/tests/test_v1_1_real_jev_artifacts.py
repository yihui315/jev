"""§7F — the Real-Jev verification artifacts must not be decorative.

THE DEFECT THIS EXISTS TO PREVENT. A verification record is a CLAIM about what
is known. Two specific ways it goes wrong here, both already seen in this repo's
history in other forms:

1. **The record drifts from the code it describes.** The artifact is generated
   from an interface card; if someone edits the card and does not regenerate, the
   committed record silently describes a different system. Nothing else in the
   suite would notice. ``--check`` is the fix, and this test RUNS it.
2. **The record cannot be reproduced.** ``artifacts/`` is gitignored except
   ``artifacts/release/**``; a generator living in the ignored scratch tree makes
   the released record unreproducible even though the record itself is tracked.
   So both the artifacts AND the two scripts are locked to HEAD, by exit code.

The tests are deliberately split between "is it consistent" (regeneration,
card agreement) and "does it say the right thing" (verdict, smoke gates,
credential states) -- a check that only tests one of the two directions passes
on an artifact that is wrong in the other.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
for _p in (REPO, REPO / "tools", REPO / "packages"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

JSON_REL = "artifacts/release/V1_1_REAL_JEV_VERIFICATION.json"
MD_REL = "docs/V1_1_REAL_JEV_INTERFACE.md"
GEN_REL = "tools/v1_1_emit_real_jev_verification.py"
PROBE_REL = "tools/v1_1_local_credential_discovery.py"

#: The shipped replay recording is a §9A artifact in the same sense: it carries
#: digests the provider RECOMPUTES and COMPARES, so a hand-edited fixture is
#: refused at load. Its bytes must therefore come from its generator.
REPLAY_FIXTURE_REL = "fixtures/v1.1/replay_default.jsonl"
REPLAY_GEN_REL = "tools/v1_1_make_replay_fixture.py"

JSON_PATH = REPO / JSON_REL
MD_PATH = REPO / MD_REL


def _git(*args: str):
    return subprocess.run(["git", "-C", str(REPO), *args],
                          capture_output=True, text=True)


@pytest.fixture(scope="module")
def report() -> dict:
    return json.loads(JSON_PATH.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Reproducibility
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("rel", [JSON_REL, MD_REL, GEN_REL, PROBE_REL,
                                 REPLAY_FIXTURE_REL, REPLAY_GEN_REL])
def test_the_artifact_and_its_generator_are_in_head(rel):
    """HEAD, not the index -- an index-only file is absent from a fresh clone.

    Asserted on the EXIT CODE, never on stderr prose (the two look similar and
    only one is a fact).
    """
    proc = _git("cat-file", "-e", f"HEAD:{rel}")
    assert proc.returncode == 0, (
        f"{rel} is not in HEAD, so a fresh clone cannot reproduce this record. "
        f"git said: {proc.stderr.strip()}"
    )


def test_check_mode_reports_the_artifacts_are_current():
    """Runs the generator's own staleness check as a subprocess."""
    proc = subprocess.run(
        [sys.executable, str(REPO / GEN_REL), "--check"],
        capture_output=True, text=True, cwd=str(REPO),
    )
    assert proc.returncode == 0, (
        "the committed verification record is stale relative to the interface "
        f"card.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


def test_the_replay_fixture_is_current_and_loadable():
    """Both halves, because either alone can pass on a broken fixture.

    Byte-currency proves nobody hand-edited it. LOADABILITY proves the bytes are
    acceptable to the verifier that will actually read them -- a fixture could be
    perfectly reproducible and still be rejected (a wrong digest rule, an edited
    assessment), and only the loader notices.
    """
    proc = subprocess.run(
        [sys.executable, str(REPO / REPLAY_GEN_REL), "--check"],
        capture_output=True, text=True, cwd=str(REPO),
    )
    assert proc.returncode == 0, (
        f"the shipped replay fixture is stale.\nstdout: {proc.stdout}"
        f"\nstderr: {proc.stderr}"
    )

    from jcc_core.providers.record_replay import REPLAY_SCHEMA, RecordReplayProvider

    prov = RecordReplayProvider(recording_path=REPO / REPLAY_FIXTURE_REL)
    assert prov.is_available() is True, "the shipped fixture did not load"
    assert prov.entry_count >= 1
    assert prov.recording_id, "the shipped fixture must declare a recording_id"
    assert REPLAY_SCHEMA.endswith("/2"), (
        "§9A requires the digest-bearing recording schema; a version regression "
        "would silently drop the integrity checks"
    )


def test_the_json_cannot_disagree_with_the_interface_card(report):
    """Anti-drift, measured against the code rather than trusted.

    The card IS the source of truth; the artifact is a serialisation of it. If
    these ever differ, one of them is lying and the reader cannot tell which.
    """
    from jcc_core.providers.real import unverified_default_interface

    card = unverified_default_interface()
    published = {f["field"]: f for f in report["provider"]["interface"]["fields"]}

    assert set(published) == {f.name for f in card.fields}, "field set drifted"
    for f in card.fields:
        pub = published[f.name]
        assert pub["status"] == f.status, f"{f.name}: status drifted"
        assert pub["authority"] == f.authority, f"{f.name}: authority drifted"
        assert pub["value"] == f.value, f"{f.name}: value drifted"
        assert pub["last_verified_at"] == f.last_verified_at, (
            f"{f.name}: verification date drifted"
        )
    assert report["provider"]["interface"]["card_digest_sha256"] == card.digest()
    assert report["provider"]["interface"]["missing_critical"] == list(
        card.missing_critical()
    )


def test_the_document_and_the_json_quote_the_same_card_digest(report):
    """Two outputs, one card: a reader of either must land on the same digest."""
    md = MD_PATH.read_text(encoding="utf-8")
    digest = report["provider"]["interface"]["card_digest_sha256"]
    assert digest in md, "the Markdown does not quote the card digest the JSON reports"


# --------------------------------------------------------------------------- #
# Secrets
# --------------------------------------------------------------------------- #
def test_no_credential_value_appears_in_either_artifact(report):
    """The single most damaging way this file could be wrong."""
    from v1_1_local_credential_discovery import _CRED_ENV_NAMES

    blob = JSON_PATH.read_text(encoding="utf-8") + MD_PATH.read_text(encoding="utf-8")
    leaked = [n for n in _CRED_ENV_NAMES
              if os.environ.get(n) and os.environ[n] in blob]
    assert not leaked, f"credential value(s) present verbatim in the artifacts: {leaked}"
    # ...and the presence-only contract is what is actually published
    assert "note" in report["credential_evidence"]


# --------------------------------------------------------------------------- #
# The verdict itself
# --------------------------------------------------------------------------- #
def test_the_real_jev_verdict_is_a_lawful_state(report):
    """Either ACTIVE (verified) or PENDING_EXTERNAL_ACTIVATION. Never a fudge."""
    verdict = report["decision"]["real_jev"]
    assert verdict in ("ACTIVE", "PENDING_EXTERNAL_ACTIVATION"), verdict
    assert report["decision"]["provider_mode"] == "REAL"
    # actuation must be OFF in every state this record can describe
    assert report["decision"]["actuation_enabled"] is False


def test_the_smoke_section_is_consistent_with_the_credential_state(report):
    """Derived, not asserted-by-hand.

    Hardcoding "requests issued == 0" here would keep passing after someone
    provided a credential and made a call -- and then the record would understate
    what happened. The check is: the gates and the attempt flag must agree.
    """
    gates = report["smoke"]["gates"]
    smoke = report["smoke"]
    all_gates = all(gates.values())
    if smoke["attempted"]:
        assert all_gates, (
            "a real smoke was attempted while a §7E gate was closed: "
            f"{gates}"
        )
        assert smoke["requests_issued"] >= 1
    else:
        assert smoke["requests_issued"] == 0
        assert not all_gates or not gates["transport_implemented"], (
            "smoke was skipped even though every §7E gate was open -- that is a "
            "silently skipped test, not a fail-closed decision"
        )
    # credential arithmetic must match the provider's own env var, not a guess
    env_name = report["provider"]["credential_env_name"]
    state = report["credential_evidence"]["env_presence"].get(env_name, {})
    assert gates["credential_present"] == (state.get("state") == "PRESENT")


def test_the_smoke_reason_does_not_contradict_the_gates(report):
    """Regression guard for a defect this file's generator actually shipped.

    The first version of the reason string was hand-written and said "At least
    one gate is OPEN" while every gate was closed -- the exact opposite of the
    truth, inside a committed evidence record. The reason is now derived from the
    gates; this asserts the derived text stays consistent with them, so the
    sentence cannot drift back.
    """
    smoke = report["smoke"]
    closed = smoke["closed_gates"]
    assert closed == [k for k, v in smoke["gates"].items() if not v], (
        "closed_gates must be exactly the false gates"
    )
    if smoke["allowed_by_section_7E"]:
        assert not closed, "smoke permitted while a gate is closed"
        assert "every gate is open" in smoke["reason"]
        assert "no request was issued" not in smoke["reason"].lower()
    else:
        assert closed, "smoke refused while every gate is open"
        assert "Closed gate(s):" in smoke["reason"], smoke["reason"]
        for name in closed:
            assert name in smoke["reason"], f"{name} missing from the reason text"
        assert "no request was issued" in smoke["reason"].lower()
        # the specific wrong sentence, asserted absent rather than merely hoped for
        assert "at least one gate is open" not in smoke["reason"].lower()


def test_every_verified_field_carries_evidence_and_a_date(report):
    """An assertion without evidence is the thing this whole file forbids."""
    for f in report["provider"]["interface"]["fields"]:
        if f["status"] != "VERIFIED":
            continue
        assert f["last_verified_at"], f"{f['field']} verified with no date"
        assert f["evidence"] and len(f["evidence"]) > 5, (
            f"{f['field']} verified with no usable evidence string"
        )


def test_the_unverified_critical_fields_are_named_individually(report):
    """The §7 rule in one assertion: no whole-provider UNVERIFIED blob."""
    missing = report["provider"]["interface"]["missing_critical"]
    assert len(missing) >= 10, missing
    for required in ("endpoint_path", "http_method", "request_schema"):
        assert required in missing, (
            f"{required} must be reported as an OPEN critical field, not folded "
            "into a provider-level 'unverified'"
        )
    assert report["do_not_guess"]["fields"], (
        "the explicit do-not-guess list must not be empty"
    )
