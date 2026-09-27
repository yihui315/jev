"""§21 — the v1.1 security-gate artifact must be DERIVED, not asserted.

WHAT THIS EXISTS TO PREVENT
    §21's contract is ``NO-GO iff high > 0, or a check failed to RUN, or a check
    reported its own evidence as vacuous``. Four ways that contract can rot into a
    green light, every one of which this file can fail on:

1. **The artifact says GO while its own numbers say NO-GO.** The gate's most
   characteristic failure mode is not a missed finding, it is a *document* whose
   summary disagrees with its detail. So the verdict is recomputed HERE, from the
   artifact's own ``counts`` / ``checks_not_run`` / ``non_vacuity_gaps``, by an
   implementation written in this test rather than imported from the generator.
   If the two ever disagree the artifact is a claim, not evidence.
2. **A check silently stops running.** A gate that catches its checks' exceptions
   turns "the probe was never executed" into a row that merely looks quiet. So
   ``checks_ran`` must equal the number of checks, the declared check table must
   equal the artifact's rows in order, and every row must carry ``ran: true``.
3. **A check stops declaring its own non-vacuity.** ``non_vacuous`` is
   three-state on purpose (``True``/``False``/``None``); a check that never
   answers is unauditable, and ``False`` must coincide with the gap list.
4. **The instrument damages what it measures.** S-08 records the protected
   database before and after. The artifact must therefore contain the proof that
   the run did not move it, rather than a promise that it does not.

The reproducibility test RUNS ``--check`` and also tampers with a copy, so the
checker is proven able to FAIL rather than merely able to pass.
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

ARTIFACT_REL = "artifacts/release/V1_1_SECURITY_GATE.json"
GEN_REL = "tools/v1_1_security_gate.py"

ARTIFACT_PATH = REPO / ARTIFACT_REL

#: The frozen severity vocabulary. A finding outside it is a typo, and a typo in
#: a severity is a silently-unblocked finding.
SEVERITIES = ("HIGH", "MEDIUM", "LOW", "INFO")

#: Modules the gate reads, so the artifact is reproducible from a fresh clone:
#: the five v1.1 modules S-04/S-06 scan, the redaction kernel S-02 exercises, and
#: the ingest chokepoint whose key-name behaviour S-02c measures.
SOURCES_READ = (
    "packages/supervisor/actuation_levels.py",
    "packages/supervisor/directive_gate.py",
    "packages/supervisor/budget_report.py",
    "packages/supervisor/live_shadow_gate.py",
    "apps/control_ui/v1_1.py",
    "packages/jcc_core/redaction.py",
    "packages/jcc_core/core.py",
)


def _git(*args: str):
    return subprocess.run(["git", "-C", str(REPO), *args],
                          capture_output=True, text=True)


def _load_gen():
    """Import the gate by path, REGISTERING it in ``sys.modules`` first.

    The registration is load-bearing, not tidiness. The gate has
    ``from __future__ import annotations``, so every annotation is a *string*, and
    ``dataclasses._is_type`` resolves those strings through
    ``sys.modules.get(cls.__module__).__dict__``. A module executed via
    ``spec.loader.exec_module`` without being registered has no entry in
    ``sys.modules``, so that lookup returns ``None`` and the ``@dataclass``
    declaration dies with ``AttributeError: 'NoneType' object has no attribute
    '__dict__'`` -- from inside the standard library, naming nothing about the
    real cause. Measured: 6 errors at fixture setup, all pointing at
    ``dataclasses.py:757``.

    Every other ``test_v1_1_*_artifact.py`` in this tree uses the unregistered
    form and works only because its generator happens to define no dataclass. That
    is a trap for the next person who adds one.
    """
    spec = importlib.util.spec_from_file_location("v1_1_security_gate", REPO / GEN_REL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return mod


@pytest.fixture(scope="module")
def artifact() -> dict:
    return json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def gen():
    return _load_gen()


def _verdict_recomputed(record: dict) -> str:
    """§21's rule, reimplemented independently of the generator.

    Deliberately NOT ``gen._verdict_of``: importing the rule under test would let a
    mutated rule certify itself. This is the same rule expressed twice, and the
    test's whole value is that the two must agree.
    """
    counts = record["counts"]
    if any(counts.get(s, 0) > 0 for s in ("HIGH",)):
        return "NO-GO"
    if record["checks_not_run"]:
        return "NO-GO"
    if record["non_vacuity_gaps"]:
        return "NO-GO"
    return "GO"


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("rel", [ARTIFACT_REL, GEN_REL, *SOURCES_READ])
def test_the_artifact_and_everything_it_reads_are_in_head(rel):
    """Asserted on the EXIT CODE, never on stderr prose.

    The sources are included because the artifact embeds their measurements: a
    module that is not committed makes the artifact unreproducible from a fresh
    clone even though the artifact itself is tracked, and an untracked gate is an
    unreproducible verdict.
    """
    proc = _git("cat-file", "-e", "HEAD:%s" % rel)
    assert proc.returncode == 0, (
        "%s is not in HEAD, so a fresh clone cannot reproduce this record. "
        "git said: %s" % (rel, proc.stderr.strip())
    )


def test_the_artifact_declares_its_schema(artifact):
    assert artifact["schema"] == "jcc.v1_1.security_gate/v1"


# --------------------------------------------------------------------------- #
# The verdict is derived, and the derivation can fail
# --------------------------------------------------------------------------- #
def test_the_recorded_verdict_equals_the_rule_applied_to_the_record(artifact):
    assert artifact["verdict"] == _verdict_recomputed(artifact), (
        "the artifact's verdict (%s) disagrees with §21's rule applied to the "
        "artifact's own numbers (%s) -- the summary contradicts the detail"
        % (artifact["verdict"], _verdict_recomputed(artifact))
    )


def test_the_rule_can_actually_go_no_go(artifact):
    """Positive control for the derivation above.

    A rule that returned "GO" unconditionally would satisfy the previous test on
    a clean run and prove nothing. Each of §21's three NO-GO clauses is exercised
    on a copy of the real record.
    """
    base = json.loads(json.dumps(artifact))
    assert _verdict_recomputed(base) == "GO", "the shipped record is not a GO; see the artifact"
    for mutate in (
        lambda r: r["counts"].__setitem__("HIGH", r["counts"].get("HIGH", 0) + 1),
        lambda r: r.__setitem__("checks_not_run", ["S-XX"]),
        lambda r: r.__setitem__("non_vacuity_gaps", ["S-XX"]),
    ):
        broken = json.loads(json.dumps(base))
        mutate(broken)
        assert _verdict_recomputed(broken) == "NO-GO", (
            "a NO-GO clause did not fire; §21's rule would never block"
        )


# --------------------------------------------------------------------------- #
# Every check ran, is declared, and is auditable
# --------------------------------------------------------------------------- #
def test_the_declared_check_table_equals_the_artifact_rows(artifact, gen):
    declared = [(cid, title) for cid, title, _fn in gen.CHECK_TABLE]
    recorded = [(c["check"], c["title"]) for c in artifact["checks"]]
    assert recorded == declared, (
        "the artifact's checks are not the gate's declared check table, in order; "
        "a check that stopped being emitted would look like a shorter list"
    )
    assert artifact["check_count"] == len(gen.CHECK_TABLE)


def test_no_check_failed_to_run(artifact):
    assert artifact["checks_not_run"] == [], (
        "a check did not run; an unrunnable check is a HIGH finding, not a skip"
    )
    assert artifact["checks_ran"] == artifact["check_count"]
    for chk in artifact["checks"]:
        assert chk["ran"] is True and chk["error"] is None, chk["check"]


def test_every_check_states_whether_it_was_vacuous(artifact):
    """``None`` is "the check never answered", which is not the same as ``True``."""
    for chk in artifact["checks"]:
        assert chk["non_vacuous"] is not None, (
            "%s did not report its own non-vacuity; an unauditable check is an "
            "unchecked check" % chk["check"]
        )
        assert chk["non_vacuity_note"], "%s gave no reason for its non-vacuity" % chk["check"]


def test_the_vacuity_gap_list_matches_the_per_check_flags(artifact):
    declared_gaps = sorted(c["check"] for c in artifact["checks"]
                           if c["non_vacuous"] is False)
    assert sorted(artifact["non_vacuity_gaps"]) == declared_gaps, (
        "the top-level vacuity gap list disagrees with the per-check flags"
    )


def test_the_per_check_counts_match_its_findings(artifact):
    """Arithmetic is checkable, so it is checked. Counts that disagree with the
    findings they summarise are how a HIGH gets edited out of a total."""
    for chk in artifact["checks"]:
        for sev in SEVERITIES:
            actual = sum(1 for f in chk["findings"] if f["severity"] == sev)
            assert chk["counts"][sev] == actual, (chk["check"], sev, chk["counts"][sev], actual)
        assert chk["ok"] == (chk["error"] is None and chk["counts"]["HIGH"] == 0)


def test_the_totals_are_the_sums_of_the_parts(artifact):
    for sev in SEVERITIES:
        assert artifact["counts"][sev] == sum(c["counts"][sev] for c in artifact["checks"]), sev


def test_every_finding_carries_a_known_severity_and_is_filed_under_its_check(artifact):
    """A finding must belong to the check that produced it.

    Sub-probe ids (``S-02a``/``S-02b``/``S-02c``, ``S-07c``) are deliberate: one
    check can run several independent probes and a reviewer needs to know which
    one spoke. The rule is therefore "the check row owns this id", expressed as an
    exact match or a single-letter sub-probe tag -- not a bare ``startswith``,
    which ``S-0`` would also satisfy.
    """
    for chk in artifact["checks"]:
        owner = re.compile(re.escape(chk["check"]) + r"[a-z]?$")
        for finding in chk["findings"]:
            assert finding["severity"] in SEVERITIES, finding["severity"]
            assert finding["detail"], "a finding with no detail is not reviewable"
            assert owner.match(finding["check"]), (
                "%s is filed under %s but does not belong to it"
                % (finding["check"], chk["check"])
            )


# --------------------------------------------------------------------------- #
# The headline safety claims the artifact carries
# --------------------------------------------------------------------------- #
def test_the_actuator_is_inert_at_every_measured_setting(artifact):
    """The P0-P3 invariant, read out of the artifact rather than re-measured.

    Every row must report ``acted`` false for BOTH the execute() and the
    permanent dispatch() seam; a single true row would have produced a HIGH and
    the verdict test above would already have failed -- this asserts the
    evidence is actually present, so it cannot be satisfied by its own absence.
    """
    s04 = next(c for c in artifact["checks"] if c["check"] == "S-04")
    rows = s04["evidence"]["actuator_execute"]
    assert len(rows) >= 2, "the actuator was measured at fewer than two settings"
    for row in rows:
        assert row["acted"] is False, row
        assert row["dispatch_acted"] is False, row
    assert s04["evidence"]["l0_permits"] == [], "L0 must permit nothing"


def test_no_single_condition_opens_actuation(artifact):
    """The positive control reached SATISFIED+enabled, and every removal closed it.

    Both halves are required: without the first, the removals would be consistent
    with a gate that is simply broken shut.
    """
    s07 = next(c for c in artifact["checks"] if c["check"] == "S-07")
    assert s07["evidence"]["all_three"]["actuation_enabled"] is True, (
        "the positive control did not open actuation, so the negative controls "
        "below prove nothing"
    )
    for name, row in s07["evidence"]["variants"].items():
        assert row["actuation_enabled"] is False, name
        assert row["effective_level"] == row["expected_level"], name


def test_the_secret_scan_was_non_vacuous(artifact):
    """§21's S-02 refuses to claim "no secret at rest" from a run in which nothing
    was signed and the whole payload was erased. The artifact must show both
    preconditions, not just the conclusion."""
    s02 = next(c for c in artifact["checks"] if c["check"] == "S-02")
    assert s02["evidence"]["signed_rows"] >= 1, (
        "no signed directive row existed during the scan: 'the signing key did "
        "not leak' would be a statement about a run in which signing never happened"
    )
    assert s02["evidence"]["signature_self_verified"] is True
    assert s02["evidence"]["benign_survived"] is True, (
        "the benign control did not survive, so 'nothing leaked' is vacuous"
    )
    assert s02["non_vacuous"] is True


def test_a_secret_under_a_secret_named_key_is_absent_from_every_sink(artifact):
    """The regression guard, pinned at the artifact level.

    This is the finding the gate was built to be able to make: ``api_key`` is a
    key the redaction policy itself declares secret-named, so a value under it
    must never reach a persisted sink. The check is written to HOLD, not to
    remember -- the assertion is that the hit list is empty, and it fails the day
    the chokepoint stops masking.
    """
    s02 = next(c for c in artifact["checks"] if c["check"] == "S-02")
    for field, label in (
        ("env_secret_hits", "an environment secret is in a persisted sink"),
        ("labelled_credential_hits", "a credential-labelled value is in a persisted sink"),
        ("key_named_secret_hits", "a value under a secret-NAMED key is in a persisted sink"),
    ):
        evidence = s02["evidence"][field]
        if isinstance(evidence, dict):
            leaked = {k: v for k, v in evidence.items() if v}
        else:
            leaked = {"": evidence} if evidence else {}
        assert not leaked, "%s: %s" % (label, leaked)


# --------------------------------------------------------------------------- #
# The instrument did not damage what it measured
# --------------------------------------------------------------------------- #
def test_the_protected_service_and_runtime_db_are_untouched_in_the_record(artifact):
    """S-08's before/after, asserted as an invariant of the record.

    The four database fields must be equal: the gate never opens
    ``runtime/jcc/jcc.db``, and the point of recording before and after is that
    the claim is falsifiable. The port is asserted one-directionally because a
    service that came UP during the run is not damage (S-08's own rule), while a
    service that went DOWN would have been a HIGH.
    """
    s08 = next(c for c in artifact["checks"] if c["check"] == "S-08")
    before, after = s08["evidence"]["before"], s08["evidence"]["after"]
    for field in ("runtime_db_sha256", "runtime_db_size", "wal_present", "shm_present"):
        assert before[field] == after[field], (field, before[field], after[field])
    assert not (before["protected_port_open"] and not after["protected_port_open"]), (
        "the protected v1.0 service went down during the gate run"
    )
    assert s08["evidence"]["protected_port"] == 4778
    assert s08["non_vacuous"] is True, (
        "the protected database did not exist, so 'it did not change' is a "
        "statement about nothing"
    )


def test_the_gate_writes_nothing_into_the_protected_runtime_directory(gen):
    """The gate's whole non-destructiveness rests on using a throwaway tmpdir.
    Asserted on the source, because the absence of a write path is the property.

    ``PROTECTED_DB`` is allowed to appear, but only in read-side statements: a
    ``connect()``, an ``open(...,'w')``, a rename or a mkdir against it would mean
    the instrument had acquired the ability to damage the thing it certifies.
    """
    source = (REPO / GEN_REL).read_text(encoding="utf-8")
    assert "mkdtemp" in source, "the gate no longer allocates a throwaway directory"
    writable = [
        line.strip() for line in source.splitlines()
        if "PROTECTED_DB" in line
        and any(token in line for token in
                ("connect(", "write_", "write(", "open(", "unlink", "rename",
                 "mkdir", "replace(", "rmtree"))
    ]
    assert not writable, (
        "the gate acquired a write path against the protected database: %s" % writable
    )


# --------------------------------------------------------------------------- #
# Reproducibility — and the check can FAIL
# --------------------------------------------------------------------------- #
#: How many times the checker may be RELAUNCHED when it reaches no verdict at
#: all. A relaunch is only ever spent on the INSTRUMENT (a launcher that died in
#: native code); it is never spent on a verdict, because a condemnation is
#: authoritative on the first reading.
GATE_LAUNCH_ATTEMPTS = 4

#: The two markers ``main()`` prints under ``--check``. The OK marker alone is
#: not enough to pass: ``main`` returns 0 only when the artifact matches AND the
#: recomputed verdict is GO, so a matching-but-NO-GO run also prints OK.
GATE_DRIFT_MARK = "[check] FAIL:"
GATE_OK_MARK = "[check] OK:"
GATE_GO_MARK = "verdict=GO"


def _gate_check_answer(out: str) -> str:
    """Classify what the checker SAID. Verdict text, not exit status.

    Returns one of:
      ``"condemned"`` -- the checker rejected the artifact, or matched it while
                         recomputing a non-GO verdict. Authoritative: the caller
                         must fail on this and must NOT relaunch.
      ``"pass"``      -- artifact matches AND the verdict is GO.
      ``"no-verdict"``-- the checker never reached a verdict line (it crashed
                         before answering, or could not even be imported). The
                         caller may relaunch the instrument.

    This ordering is the point: a condemnation is recognised BEFORE the OK
    marker, so no sequence of relaunches can turn a rejected artifact green.
    """
    if GATE_DRIFT_MARK in out:
        return "condemned"
    if GATE_OK_MARK in out:
        return "condemned" if GATE_GO_MARK not in out else "pass"
    return "no-verdict"


def _run_gate_check():
    """Launch the checker. Split out so the relaunch policy can be falsified."""
    return subprocess.run(
        [sys.executable, str(REPO / GEN_REL), "--check"],
        cwd=str(REPO), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=900,
    )


def _assert_artifact_is_current():
    """The real check, extracted so ``_run_gate_check`` can be monkeypatched."""
    attempts = []
    out = ""
    for attempt in range(1, GATE_LAUNCH_ATTEMPTS + 1):
        proc = _run_gate_check()
        out = proc.stdout + proc.stderr
        kind = _gate_check_answer(out)
        attempts.append({"attempt": attempt, "rc": proc.returncode, "kind": kind})
        if kind == "condemned":
            pytest.fail(
                "the committed artifact was CONDEMNED by the checker "
                "(attempt %d, rc=%s, classified %r). This is a product verdict, "
                "not a launcher problem, and it is never relaunched:\n%s"
                % (attempt, proc.returncode, kind, out[-1500:]))
        if kind == "pass" and proc.returncode == 0:
            return
        # Either there was no verdict at all, or the checker answered and then
        # died in native code. Both are instrument problems: the verifier on
        # this host has already been seen to print the complete correct verdict
        # and then exit 0xC0000005 (STATUS_ACCESS_VIOLATION). Relaunch the
        # instrument; the verdict, when one appears, is still read strictly.
        time.sleep(0.4 * attempt)
    pytest.fail(
        "could not obtain a clean authoritative answer from --check within %d "
        "launch attempt(s): every attempt either died in native code or never "
        "reached a verdict line. This is an ENVIRONMENT failure (the checker "
        "produced no verdict), NOT evidence that the artifact is stale.\n"
        "attempts=%r\nlast output:\n%s"
        % (GATE_LAUNCH_ATTEMPTS, attempts, out[-1500:]))


def test_check_mode_reports_the_artifact_is_current():
    """RUNS the checker. Reading its source would prove nothing about the file.

    Asserted on the checker's VERDICT, with the exit status as a *second*
    requirement that the instrument must eventually satisfy. This host can kill
    the process after it has answered -- one loaded full-suite run printed the
    complete, correct ``[check] OK ... sha 862a088692c9c15b830b9be1`` and then
    exited ``0xC0000005``. Folding that into "the artifact is stale" would be
    precisely the instrument-failure-read-as-a-product-failure defect this
    project keeps hunting, so the instrument is relaunched while the verdict is
    read strictly (see ``_assert_artifact_is_current``).
    """
    _assert_artifact_is_current()


def test_the_gate_check_classifier_never_reads_a_condemnation_as_a_pass():
    """Positive control for the classifier: all three states, and the ordering.

    A classifier that simply looked for the OK marker would call a
    matching-but-NO-GO run a pass -- which is the concrete way this project's
    "condemned" state could be silently absorbed.
    """
    assert _gate_check_answer("[check] OK: the committed artifact matches (sha abc)"
                              "\nverdict=GO  high=0\n") == "pass"
    assert _gate_check_answer("[check] FAIL: the committed artifact does not "
                              "match a fresh run") == "condemned"
    assert _gate_check_answer("[check] FAIL: <out> does not exist") == "condemned"
    # matching artifact, non-GO verdict: prints OK but must NOT count as a pass
    assert _gate_check_answer("[check] OK: the committed artifact matches (sha abc)"
                              "\nverdict=NO-GO  high=1\n") == "condemned"
    # a launcher that died before printing anything
    assert _gate_check_answer("") == "no-verdict"
    assert _gate_check_answer("Traceback (most recent call last):\n ImportError") == "no-verdict"
    # ordering: a stream that somehow carried BOTH must be read as condemned
    assert _gate_check_answer("[check] OK: ...\n[check] FAIL: something") == "condemned"


def test_a_launcher_crash_is_relaunched_and_can_still_pass(monkeypatch):
    """A crash before any verdict is an instrument fault -- and must not fail the check."""
    calls = []

    class _Proc:
        def __init__(self, rc, out):
            self.returncode, self.stdout, self.stderr = rc, out, ""

    def fake_run():
        calls.append(1)
        if len(calls) == 1:
            return _Proc(-1073741819, "")          # die before answering
        return _Proc(0, "[check] OK: the committed artifact matches (sha abc)\nverdict=GO\n")

    monkeypatch.setattr(sys.modules[__name__], "_run_gate_check", fake_run)
    _assert_artifact_is_current()
    assert len(calls) == 2, "the relaunch must happen exactly once here, got %d" % len(calls)


def test_a_condemnation_is_not_relaunched_into_a_pass(monkeypatch):
    """The falsifier that matters: a rejected artifact is never retried green."""
    calls = []

    class _Proc:
        def __init__(self, rc, out):
            self.returncode, self.stdout, self.stderr = rc, out, ""

    def fake_run():
        calls.append(1)
        # Even if a LATER launch would say OK, the first condemnation ends it.
        if len(calls) == 1:
            return _Proc(1, "[check] FAIL: the committed artifact does not match a fresh run\n")
        return _Proc(0, "[check] OK: the committed artifact matches (sha abc)\nverdict=GO\n")

    monkeypatch.setattr(sys.modules[__name__], "_run_gate_check", fake_run)
    with pytest.raises(pytest.fail.Exception):
        _assert_artifact_is_current()
    assert calls == [1], "a condemnation must be authoritative on the first reading"


def test_two_consecutive_runs_agree_on_the_normalised_digest(gen):
    """Determinism of the normalised document — the property ``--check`` relies on."""
    first = gen._digest(gen.run_checks(verbose=False))
    second = gen._digest(gen.run_checks(verbose=False))
    assert first == second


def test_the_normalised_digest_ignores_the_volatile_fields(gen):
    """Non-vacuity for the digest: the fields that change every run must not
    change it, or ``--check`` would fail every time and be ignored.

    The run-varying keys are stamped onto BOTH records before varying them,
    because ``run_checks`` does not emit them (``main`` adds the repo stamp). An
    earlier version of this test set them on one record only -- which *adds* keys
    rather than varying values, and a digest that ignored added keys would be the
    wrong digest, so the test was measuring the opposite of what it claimed.
    """
    base = gen.run_checks(verbose=False)
    for key, value in (("generated_at", "t0"), ("repo_head", "h0"),
                       ("repo_branch", "b0"), ("worktree_dirty", False)):
        base[key] = value
    mutated = json.loads(json.dumps(base))
    mutated["generated_at"] = "t1"
    mutated["repo_head"] = "h1"
    mutated["repo_branch"] = "b1"
    mutated["worktree_dirty"] = True
    mutated["checks"][0]["seconds"] = base["checks"][0]["seconds"] + 999.0
    assert set(mutated) == set(base), "the two records must differ only in values"
    assert gen._digest(base) == gen._digest(mutated)


def test_the_digest_changes_when_a_real_verdict_field_changes(gen):
    """The other direction, so the digest is not simply constant."""
    base = gen.run_checks(verbose=False)
    mutated = json.loads(json.dumps(base))
    mutated["counts"]["HIGH"] = base["counts"]["HIGH"] + 1
    assert gen._digest(base) != gen._digest(mutated)


def test_check_mode_can_actually_fail(tmp_path, monkeypatch, gen):
    """Tampers with a copy of the artifact and asserts ``--check`` rejects it.

    Without this, a ``--check`` that always returned 0 would pass the test above.
    The tampered field is ``counts.HIGH`` -- a substantive total, and not one of
    the keys the normaliser blanks, so the digest must move.
    """
    tampered = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    tampered["counts"]["HIGH"] = tampered["counts"]["HIGH"] + 1
    assert tampered["counts"] != json.loads(
        ARTIFACT_PATH.read_text(encoding="utf-8"))["counts"]
    fake = tmp_path / ARTIFACT_REL
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_text(json.dumps(tampered), encoding="utf-8")

    monkeypatch.setattr(gen, "OUT", fake)
    assert gen.main(["--check"]) == 1, "the checker accepted a tampered artifact"


def test_the_selftest_passes():
    """The generator's own selftest falsifies the verdict rule and the scanner."""
    proc = subprocess.run(
        [sys.executable, str(REPO / GEN_REL), "--selftest"],
        cwd=str(REPO), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "[selftest] PASS" in proc.stdout
