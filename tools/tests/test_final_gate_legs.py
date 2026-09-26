"""Falsifiers for the final gate's own decision rules (owner: lead / Orion).

WHY THIS MODULE EXISTS. The rules that decide whether this release ships used to
be reachable only by running `final_gate_run.py --full`, which takes minutes and
needs a live repository. A rule that can only be exercised that way is a rule
nobody re-checks after it has quietly been weakened -- and two of these rules had
already been weakened:

  * the leg rule demanded the literal string ``"GO"`` while this project's own
    ``docs/p3/P3_ARCH_GO.md`` had signed ``GO WITH CONDITIONS`` since P3, pushing
    every leg's author toward the one string that passed;
  * the same rule composed its result as ``g.ok(...) + cond_note``, which is a
    latent ``TypeError`` (``Gate.ok`` returns ``None``) that also dropped the
    conditions -- a defect invisible precisely because no leg was signed at the
    position that reached it.

Each test below therefore states a WAY THE RULE COULD BE WRONG and checks the
rule refuses it. Cases are named for the input, not for the assertion, so a
future reader can tell which inputs are covered at a glance.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GATE_PATH = REPO / "artifacts" / "acceptance" / "final_gate_run.py"

HOLD = "a" * 40
FREEZE = "b" * 40
TIP = "c" * 40

EVIDENCE_ONLY = ["artifacts/release/PROJECT_RUN_READINESS.md",
                 "docs/FINAL_STOP_CONDITIONS.md"]
WITH_PRODUCT = EVIDENCE_ONLY + ["packages/jcc_core/core.py"]


@pytest.fixture(scope="module")
def gate():
    """The gate module, loaded from its path (it is a script, not a package)."""
    spec = importlib.util.spec_from_file_location("_jcc_final_gate", GATE_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_jcc_final_gate"] = mod
    spec.loader.exec_module(mod)
    return mod


def _leg(verdict="GO", sha=HOLD, **extra):
    rec = {"verdict": verdict, "release_sha": sha}
    rec.update(extra)
    return rec


# --------------------------------------------------------------------------- #
# The six positions the release planner named, plus the two that actually bit.
# --------------------------------------------------------------------------- #

def test_head_go_is_accepted(gate):
    status, detail = gate.judge_leg(_leg("GO", HOLD), HOLD, FREEZE, EVIDENCE_ONLY)
    assert status == gate.STATUS_OK, detail
    assert "re-signed against" in detail and "current tip" in detail


def test_head_go_with_conditions_keeps_its_conditions(gate):
    """The regression that made this module necessary.

    ``g.ok(...) + cond_note`` raised TypeError and dropped the conditions. Both
    facts are asserted here: the call must NOT raise, and the rendered detail
    must actually carry the conditions.
    """
    rec = _leg("GO WITH CONDITIONS", HOLD,
               conditions=["BLOCKER-1: control token rotation is manual",
                           "BLOCKER-2: no external telemetry sink"])
    status, detail = gate.judge_leg(rec, HOLD, FREEZE, EVIDENCE_ONLY)
    assert status == gate.STATUS_OK, detail
    assert "2 condition(s)" in detail, detail
    assert "BLOCKER-1" in detail and "BLOCKER-2" in detail, detail


def test_freeze_go_is_accepted_when_only_evidence_moved(gate):
    status, detail = gate.judge_leg(_leg("GO", FREEZE), TIP, FREEZE, EVIDENCE_ONLY)
    assert status == gate.STATUS_OK, detail
    assert "release freeze" in detail and "release evidence" in detail


def test_freeze_go_with_conditions_is_accepted_and_rendered(gate):
    rec = _leg("GO WITH CONDITIONS", FREEZE, conditions=["one condition"])
    status, detail = gate.judge_leg(rec, TIP, FREEZE, EVIDENCE_ONLY)
    assert status == gate.STATUS_OK, detail
    assert "1 condition(s)" in detail and "one condition" in detail, detail


def test_nogo_is_refused_even_when_its_sha_is_perfect(gate):
    """A signature's SHA says WHERE it was written; its verdict says WHAT it said."""
    status, detail = gate.judge_leg(_leg("NO-GO", HOLD), HOLD, FREEZE, EVIDENCE_ONLY)
    assert status == gate.STATUS_FAILED, detail


@pytest.mark.parametrize("rec,why", [
    ({"release_sha": HOLD}, "verdict absent"),
    ({"verdict": "", "release_sha": HOLD}, "verdict empty"),
    ({"verdict": None, "release_sha": HOLD}, "verdict None"),
    ({"verdict": "CONDITIONAL GO", "release_sha": HOLD}, "near-miss verdict"),
    ({"verdict": "GO WITH CONDITIONS", "release_sha": HOLD}, "GO WITH CONDITIONS, no conditions"),
    ({"verdict": "GO WITH CONDITIONS", "release_sha": HOLD, "conditions": []},
     "GO WITH CONDITIONS, empty conditions"),
    ({"verdict": "GO WITH CONDITIONS", "release_sha": HOLD, "conditions": "oops"},
     "conditions not a list"),
    ({"verdict": "GO", "release_sha": ""}, "sha absent"),
    ({"verdict": "GO", "release_sha": "d" * 40}, "sha matches nothing"),
])
def test_malformed_signatures_are_refused(gate, rec, why):
    status, detail = gate.judge_leg(rec, HOLD, FREEZE, EVIDENCE_ONLY)
    assert status == gate.STATUS_FAILED, f"{why} was accepted: {detail}"


def test_freeze_signature_is_refused_once_a_product_path_moves(gate):
    """The one falsifier that matters most: a stale signature is not a pass."""
    status, detail = gate.judge_leg(_leg("GO", FREEZE), TIP, FREEZE, WITH_PRODUCT)
    assert status == gate.STATUS_FAILED, detail
    assert "outside release evidence" in detail and "core.py" in detail


def test_tip_signed_leg_does_not_pay_for_a_diff(gate):
    """A tip-signed leg must not trigger the freeze diff at all."""
    calls = []

    def boom():
        calls.append(1)
        raise AssertionError("diff_provider was called for a tip-signed leg")

    status, _ = gate.judge_leg(_leg("GO", HOLD), HOLD, FREEZE, None, diff_provider=boom)
    assert status == gate.STATUS_OK
    assert calls == []


def test_gate_ok_really_does_return_none(gate):
    """The premise of the fixed defect, asserted rather than assumed.

    If a future refactor makes ``Gate.ok`` return something truthy, this test
    stops being evidence -- but it stays true, so the regression test above keeps
    its meaning.
    """
    g = gate.Gate()
    assert g.ok("X-01", "detail") is None
    assert g.checks == [{"id": "X-01", "status": gate.STATUS_OK, "detail": "detail"}]


# --------------------------------------------------------------------------- #
# The fixed point: the gate must be able to prove the tree clean AFTER it has
# written its own verdict. These are the falsifiers for that property, and the
# whole reason this module is allowed to call itself a stop condition.
# --------------------------------------------------------------------------- #

def test_accepted_leg_details_do_not_move_with_the_tip(gate):
    """An ACCEPTING detail lands in a TRACKED file; it may not name HEAD.

    A detail that embeds the tip makes the verdict differ from its own committed
    blob the moment it is committed -- and since the verdict IS the file P-01
    compares, the gate would fail itself on every run. Measured, before this
    rule existed: a full run reported ``failed=['P-01','P-05']`` whose only
    dirty paths were the evidence files that same run had regenerated.
    """
    tip_signed = gate.classify_leg_position(TIP, TIP, FREEZE, [])
    tip_signed_other_tip = gate.classify_leg_position(HOLD, HOLD, FREEZE, [])
    assert tip_signed[0] and tip_signed_other_tip[0]
    assert tip_signed[1] == tip_signed_other_tip[1], (tip_signed, tip_signed_other_tip)
    for detail in (tip_signed[1], tip_signed_other_tip[1]):
        assert TIP[:12] not in detail and HOLD[:12] not in detail, detail

    freeze_signed = gate.classify_leg_position(FREEZE, TIP, FREEZE, EVIDENCE_ONLY)
    freeze_signed_more = gate.classify_leg_position(FREEZE, HOLD, FREEZE,
                                                    EVIDENCE_ONLY * 4)
    assert freeze_signed[0] and freeze_signed_more[0]
    assert freeze_signed[1] == freeze_signed_more[1], (freeze_signed, freeze_signed_more)
    for detail in (freeze_signed[1], freeze_signed_more[1]):
        assert TIP[:12] not in detail and HOLD[:12] not in detail, detail
        assert "8 path(s)" not in detail and "2 path(s)" not in detail, detail


def test_written_verdict_carries_no_clock_and_no_head(gate, tmp_path):
    """The verdict must identify the TREE, not the moment or the commit."""
    out = tmp_path / "verdict.json"
    rc = gate.main(["--json-out", str(out), "--no-log", "--quiet"])
    rec = json.loads(out.read_text(encoding="utf-8"))
    assert rc == 1, "without --full the gate must never claim completeness"
    assert rec["schema"] == gate.GATE_RESULT_SCHEMA
    assert "generated_at" not in rec, "a wall clock makes two runs incomparable"
    assert "head" not in rec, ("a file cannot contain the hash of the commit that "
                               "contains it, so this field is stale by construction")
    prov = rec["provenance"]
    assert prov["clock_free"] is True
    assert prov["generator_sha256"] and len(prov["generator_sha256"]) == 64
    assert prov["release_freeze"] == gate.RELEASE_FREEZE


def test_two_runs_over_the_same_tree_produce_byte_identical_verdicts(gate, tmp_path):
    """THE falsifier for the fixed point. Byte equality, because that is what
    `git status` compares.

    This is the property whose absence made the stop condition unreachable: the
    gate regenerated a tracked file with a different timestamp on every run, so
    the file was always dirty relative to its own commit and P-01 always failed.
    Two runs over an unchanged tree must produce the same bytes.
    """
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    gate.main(["--json-out", str(first), "--no-log", "--quiet"])
    gate.main(["--json-out", str(second), "--no-log", "--quiet"])
    assert first.read_bytes() == second.read_bytes(), (
        "the gate's own verdict is not reproducible from the tree it judges; "
        "P-01 can never be satisfied")


# --------------------------------------------------------------------------- #
# S-04 / S-05: the surface rules, falsified with synthetic records.
# --------------------------------------------------------------------------- #

def _good_smoke(gate):
    screens = []
    for i, name in enumerate(gate.CONTROL_UI_SCREENS):
        screens.append({"name": name, "status": 200, "non_trivial": True,
                        "unreachable": False, "excerpt": f"<title>{name}</title>"})
    return {"schema": "jcc-control-ui-smoke/3", "screens": screens,
            "screens_ok": len(screens), "screens_expected": len(screens),
            "passed": True, "status": "PASS",
            "seeded_sessions": ["demo-workbuddy-0001"],
            "provenance": {"deterministic": True, "generator_sha256": "f" * 64}}


def test_s04_accepts_a_complete_record(gate):
    assert gate.judge_control_ui_smoke(_good_smoke(gate)) == []


@pytest.mark.parametrize("mutate,needle", [
    (lambda r: r["screens"].pop(), "screens not fetched"),
    (lambda r: r.update(screens_ok=7), "screens_ok"),
    (lambda r: r.update(passed=False), "passed"),
    (lambda r: r.update(status="FAIL"), "status"),
    (lambda r: r["screens"][0].update(status=500), "HTTP 500"),
    (lambda r: r["screens"][1].update(unreachable=True), "API UNREACHABLE"),
    (lambda r: r["screens"][2].update(non_trivial=False), "empty or near-empty"),
    (lambda r: [s.update(excerpt="same") for s in r["screens"]], "stub"),
    (lambda r: r.update(provenance={}), "deterministic provenance"),
])
def test_s04_refuses_a_record_that_does_not_prove_the_claim(gate, mutate, needle):
    rec = _good_smoke(gate)
    mutate(rec)
    problems = gate.judge_control_ui_smoke(rec)
    assert problems, f"a record broken by {needle!r} was accepted"
    assert any(needle in p for p in problems), problems


def _good_manifest(gate, fixtures: Path):
    events = []
    order = 0
    for agent in gate.DEMO_AGENTS:
        for n in (1, 2):
            order += 1
            events.append({"file": f"{agent}/e{n}.json", "order": order,
                           "agent": agent})
    return {"label": "DEMO", "agents": list(gate.DEMO_AGENTS), "events": events}


def _good_demo(gate):
    volatile = {"audit.timestamp": {"origin": "packages/jcc_core/core.py",
                                    "expression": "now = time.time()",
                                    "why": "ingest wall clock"}}
    return {
        "status": "PASS", "passed": True,
        "deterministic_replay": {"ok": True, "run1_digest": "1" * 64,
                                 "run2_digest": "1" * 64,
                                 "volatile_fields_moved": True,
                                 "volatile_fields": volatile},
        "agents_covered": list(gate.DEMO_AGENTS),
        "all_directives_inert": True,
        "demo_label_pages": list(gate.DEMO_LABEL_PAGES),
        "provenance": {"deterministic": True, "generator_sha256": "f" * 64,
                       "volatile_origin_sha256": {"packages/jcc_core/core.py": "e" * 64}},
    }


@pytest.fixture()
def demo_fixtures(tmp_path):
    """A fixture directory whose manifest references actually resolve."""
    for agent in ("workbuddy", "claude", "codex", "hermes"):
        (tmp_path / agent).mkdir()
        for n in (1, 2):
            (tmp_path / agent / f"e{n}.json").write_text("{}", encoding="utf-8")
    return tmp_path


def test_s05_accepts_a_complete_record(gate, demo_fixtures):
    manifest = _good_manifest(gate, demo_fixtures)
    assert gate.judge_demo_mode(manifest, _good_demo(gate), demo_fixtures) == []


@pytest.mark.parametrize("mutate,needle", [
    (lambda m, r: m.update(label="REAL"), "manifest label"),
    (lambda m, r: m["agents"].pop(), "agents="),
    (lambda m, r: m["events"].pop(0), "fixture event(s)"),
    (lambda m, r: m["events"][0].update(file="missing.json"), "missing fixture"),
    (lambda m, r: r.update(status="FAIL"), "status"),
    (lambda m, r: r.update(agents_covered=["workbuddy"]), "agents_covered"),
    (lambda m, r: r.update(all_directives_inert=False), "acted != false"),
    (lambda m, r: r.update(demo_label_pages=["dashboard"]), "DEMO label missing"),
    (lambda m, r: r["deterministic_replay"].update(ok=False), "not deterministic"),
    (lambda m, r: r["deterministic_replay"].update(volatile_fields_moved=False),
     "did not move"),
    (lambda m, r: r["deterministic_replay"].update(volatile_fields={}),
     "declares no volatile fields"),
    (lambda m, r: r.update(provenance={}), "deterministic provenance"),
    (lambda m, r: r["provenance"].update(volatile_origin_sha256={}), "not pinned"),
])
def test_s05_refuses_a_record_that_does_not_prove_the_claim(gate, demo_fixtures,
                                                            mutate, needle):
    manifest = _good_manifest(gate, demo_fixtures)
    rec = _good_demo(gate)
    mutate(manifest, rec)
    problems = gate.judge_demo_mode(manifest, rec, demo_fixtures)
    assert problems, f"a record broken by {needle!r} was accepted"
    assert any(needle in p for p in problems), problems
