#!/usr/bin/env python3
"""FINAL-12 -- every acceptance leg of the FINAL phase, judged together.

WHAT THIS ADDS, PRECISELY. FINAL-01..FINAL-11 each judge their own artifact in
the moment they run. This step answers a different question: do those verdicts
still describe the bytes on disk *now*? A leg's artifact can be replaced between
the step that wrote it and the moment the release is published -- by a stray
producer, a partial re-run, or the editor of a person who thought the file was
theirs. Every leg's recorded sha256 is therefore re-checked against the file, and
a mismatch is a FAIL, not a footnote.

AND IT JUDGES THE CONTROL-PLANE PROOFS. Four falsifiers measure claims the stop
condition asserts -- a controller lease heartbeat that keeps beating through a
long step, interruption recovery of a killed controller, single-controller
election between simultaneous watchdog ticks, and runtime exclusivity on port
4778 (plus the three-layer provenance claim). None of them is a pytest test and
none is a controller step, so until now NOTHING IN THE PIPELINE RAN THEM: the
claims were asserted, not checked. Each now writes a proof under
``artifacts/acceptance/`` and this gate reads and judges every one. A missing,
red, or zero-case proof is a FAIL, and there is deliberately no "not yet
produced, tolerate it" branch -- that branch is exactly how the hole opened.

WHY IT DOES NOT RE-IMPLEMENT THE LEGS. The judges are imported from
``tools/jcc_continuation.py`` -- the module whose STEPS table drives the whole
pipeline -- so a leg is judged by the SAME function that judged it as a step.
Two implementations of one claim is two answers, and this project has already
paid for that lesson once (regenerate_surfaces.py re-judges with the gate's own
functions for the same reason).

WHY THERE IS NO CLOCK AND NO HEAD IN THE RESULT. Identical reasoning to
final_gate_run.py, and it is not stylistic: this file is tracked so that
re-signing a verdict is a diff git can see. If the payload embedded a wall clock,
two runs over byte-identical inputs would differ, the file would never be the
fixed point the next run compares against, and the tree would be permanently
dirty -- which fails P-01 inside FINAL-11 and makes the release unable to prove
itself clean. So the payload is a pure function of the tracked tree, and the
identity of the moment lives in the *inputs*: each leg's artifact path and
sha256.

WHAT IT DELIBERATELY DOES NOT CLAIM.
  * It does not check the working tree. Cleanliness is P-01 inside FINAL-11, and
    it is already a hard criterion there; duplicating it here would only create a
    second place to disagree. The reading is recorded for the reader, labelled as
    a reading.
  * FINAL-13_PUBLISH has NOT run when this step runs -- it is the step that
    publishes the record this verdict is written into. It is listed explicitly as
    ``pending_after_this_step`` rather than omitted, and the project-run verdict
    that covers it is the one FINAL-13 publishes.
  * There is no "known-pending" exemption and no fail-open. A leg that cannot be
    evaluated is a FAIL.

Exit 0 iff ``ok`` is true.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

#: Repo root, DERIVED from this file's location. The literal this replaced was
#: ``r"F:\jev"``, a drive that does not exist on the current host, so
#: ``_load_proof_contract()`` raised FileNotFoundError at MODULE level (line 126,
#: before ``main()``) and the FINAL-12 step could not run at all. Same idiom as
#: ``jcc_core/config.py``. JCC_ROOT still wins, for pointing the gate at a
#: checkout other than this one.
ROOT = os.environ.get("JCC_ROOT") or str(Path(__file__).resolve().parents[2])
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
STATE = os.path.join(ROOT, ".workbuddy", "jcc_execution_state.json")
OUT = os.path.join(ROOT, "artifacts", "acceptance", "project_run_gate_result.json")
SCHEMA = "jcc-project-run-gate/1"

#: The legs this step judges, and the only legs it judges. Taken from the
#: controller's own STEPS table rather than retyped, so a step cannot be added to
#: the pipeline and silently escape the project-run verdict.
EXCLUDED = ("FINAL-12_PROJECT_RUN", "FINAL-13_PUBLISH")


def log(msg: str) -> None:
    sys.stdout.write("%s\n" % msg)
    sys.stdout.flush()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with io.open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: str) -> Any:
    try:
        with io.open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _load_controller():
    """The controller module, by path. Importing it runs no pipeline: everything
    that starts a step lives behind main()."""
    import importlib.util
    p = os.path.join(ROOT, "tools", "jcc_continuation.py")
    spec = importlib.util.spec_from_file_location("jcc_continuation_for_gate", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_proof_contract():
    """The proof contract, by path -- the SAME module the four runners write with.

    One implementation of "is this proof green": the gate judges a proof with the
    very function whose schema the producer filled in, so the two cannot drift.
    """
    import importlib.util
    p = os.path.join(ROOT, "tools", "continuation", "proof_contract.py")
    spec = importlib.util.spec_from_file_location("jcc_proof_contract_for_gate", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


PROOF_CONTRACT = _load_proof_contract()

#: The producers of the control-plane proofs, for a message a human can act on.
PROOF_PRODUCER = {name: producer for name, (_claim, producer)
                  in PROOF_CONTRACT.PROOFS.items()}


def read_control_proofs() -> List[Dict[str, Any]]:
    """Read every control-plane proof. I/O only; the JUDGEMENT is pure, below."""
    readings: List[Dict[str, Any]] = []
    for name in PROOF_CONTRACT.PROOFS:
        rel = PROOF_CONTRACT.proof_artifact(name)
        path = os.path.join(ROOT, *rel.split("/"))
        present = os.path.isfile(path)
        readings.append({
            "name": name,
            "claim": PROOF_CONTRACT.PROOFS[name][0],
            "artifact": rel,
            "produced_by": PROOF_CONTRACT.PROOFS[name][1],
            "present": present,
            "sha256": sha256_file(path) if present else None,
            "payload": load_json(path) if present else None,
        })
    return readings


def judge_control_proofs(readings: List[Dict[str, Any]]) -> Tuple[bool, List[str]]:
    """The §L (and §F) proof rule, extracted so ``--selftest`` can falsify it.

    A claim whose only evidence is a falsifier nothing runs is an ASSERTION. This
    rule turns each of them into a check: the proof must exist, be green, and have
    actually run the cases it reports. It refuses a missing proof, a red proof, a
    zero-case proof, and a proof with fewer recorded checks than cases -- and
    there is deliberately no "not yet produced, tolerate it" branch, because that
    branch is how the hole reopened the first time.
    """
    problems: List[str] = []
    if not readings:
        return False, ["no control-plane proof was examined at all, so nothing was judged"]

    for r in readings:
        name = r.get("name")
        if not r.get("present"):
            problems.append(
                "%s: the proof is MISSING (%s), so the claim it covers is unproven -- "
                "not merely unread. Produce it with: python %s"
                % (name, r.get("artifact"), PROOF_PRODUCER.get(name, "<unknown>")))
            continue
        ok, why = PROOF_CONTRACT.judge_proof(r.get("payload"))
        if not ok:
            problems.append("%s: %s (%s)" % (name, "; ".join(why), r.get("artifact")))
    return (not problems), problems


def _proof_digest(readings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """What the gate result records about each proof: everything but the payload."""
    out = []
    for r in readings:
        payload = r.get("payload") or {}
        out.append({
            "name": r.get("name"),
            "claim": r.get("claim"),
            "artifact": r.get("artifact"),
            "produced_by": r.get("produced_by"),
            "present": r.get("present"),
            "sha256": r.get("sha256"),
            "status": payload.get("status"),
            "ok": payload.get("ok"),
            "verdict": str(payload.get("verdict"))[:300],
            "cases_passed": payload.get("cases_passed"),
            "cases_total": payload.get("cases_total"),
            "produced_at": payload.get("produced_at"),
        })
    return out


def git_porcelain() -> List[str]:
    r = subprocess.run(["git", "status", "--porcelain", "-uall"], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=180)
    return [l for l in (r.stdout or "").splitlines() if l.strip()]


def judge(legs: List[Dict[str, Any]]) -> Tuple[bool, List[str]]:
    """The verdict rule, extracted so ``--selftest`` can falsify it.

    Every clause names one way this step could report a pass it did not earn.
    """
    problems: List[str] = []
    if not legs:
        # A rule that passes when it examined nothing is the defect class this
        # pipeline exists to catch. There is no "no legs" outcome.
        return False, ["no acceptance leg was examined at all, so nothing was judged"]

    for leg in legs:
        sid = leg.get("id")
        if not leg.get("artifact_present"):
            problems.append("%s: the artifact this PASS was judged on is not on disk any "
                            "more (%s)" % (sid, leg.get("artifact")))
            continue
        if leg.get("sha_matches_record") is False:
            problems.append("%s: the artifact changed after it was judged -- recorded %s, "
                            "on disk %s (%s)"
                            % (sid, str(leg.get("sha_recorded"))[:16],
                               str(leg.get("sha_on_disk"))[:16], leg.get("artifact")))
        if leg.get("judge_ok") is not True:
            problems.append("%s: the controller's own judge refuses it now: %s"
                            % (sid, str(leg.get("judge_detail"))[:240]))
        if not leg.get("recorded_status"):
            problems.append("%s: the state file has no step record for it, so nothing ever "
                            "judged this leg" % sid)
    return (not problems), problems


def collect() -> Dict[str, Any]:
    cont = _load_controller()
    state = load_json(STATE) or {}
    recorded = state.get("steps") or {}

    legs: List[Dict[str, Any]] = []
    for step in cont.STEPS:
        if step.id in EXCLUDED:
            continue
        artifact_rel = step.result
        artifact_abs = os.path.join(ROOT, *artifact_rel.split("/"))
        present = os.path.isfile(artifact_abs)
        rec = recorded.get(step.id) or {}
        rec_ev = rec.get("evidence") or {}
        sha_recorded = rec_ev.get("sha256")
        sha_disk = sha256_file(artifact_abs) if present else None

        judge_ok: Any = None
        judge_detail = "not judged: no artifact on disk"
        if present:
            doc = load_json(artifact_abs)
            if doc is None:
                judge_ok = False
                judge_detail = "the artifact is unparsable as JSON"
            else:
                try:
                    judge_ok, judge_detail = step.judge(doc)
                except Exception as exc:                          # noqa: BLE001
                    judge_ok = False
                    judge_detail = "the judge raised %s: %s" % (type(exc).__name__, exc)

        leg = {
            "id": step.id,
            "title": step.title,
            "artifact": artifact_rel,
            "artifact_present": present,
            "artifact_bytes": os.path.getsize(artifact_abs) if present else 0,
            "sha_on_disk": sha_disk,
            "sha_recorded": sha_recorded,
            "sha_matches_record": (bool(sha_disk) and sha_disk == sha_recorded),
            "recorded_status": rec.get("status"),
            "recorded_detail": str(rec.get("detail"))[:400],
            "judge_ok": judge_ok,
            "judge_detail": str(judge_detail)[:400],
        }
        legs.append(leg)
        log("  %-22s present=%-5s sha_match=%-5s judge=%s"
            % (step.id, present, leg["sha_matches_record"], judge_ok))

    ok, problems = judge(legs)

    # ---- section L (and F): the control-plane proofs -------------------------
    # The four falsifiers that measure the controller's lease heartbeat,
    # interruption recovery, single-controller election and runtime exclusivity
    # are NOT pytest tests and NOT controller steps, so nothing in the pipeline
    # ran them. Each writes a proof, and the claim is only CHECKED if this gate
    # reads and judges it. A missing or red proof is a FAIL.
    proof_readings = read_control_proofs()
    proofs_ok, proof_problems = judge_control_proofs(proof_readings)
    if not proofs_ok:
        ok = False
        problems = problems + proof_problems
    for r in proof_readings:
        log("  proof %-30s present=%-5s ok=%s"
            % (r["name"], r["present"], (r.get("payload") or {}).get("ok")))

    tree = git_porcelain()
    payload = {
        "schema": SCHEMA,
        "ok": ok,
        # `project_run` is what the controller and
        # scripts/continuation-watchdog-status.ps1 READ: the controller copies it
        # into the state file (tools/jcc_continuation.py:259-261) and the status
        # script exits 0 only for COMPLETE + PASS. Without it a finished release
        # would sit at project_run=UNKNOWN, which is the state in which the
        # watchdog starts a replacement controller on every tick, forever. It is a
        # derived mirror of `ok`, kept adjacent so the two cannot disagree.
        "project_run": "PASS" if ok else "FAIL",
        "status": "PASS" if ok else "FAIL",
        "verdict": ("PASS: every FINAL-phase acceptance leg is present, unchanged since it "
                    "was judged, and still accepted by the same judge that accepted it"
                    if ok else "FAIL: " + "; ".join(problems)),
        "question": ("do the leg verdicts of the FINAL phase still describe the bytes on "
                     "disk now?"),
        "why_these_legs": ("the ids come from tools/jcc_continuation.py's own STEPS table, "
                           "so a step cannot be added to the pipeline and escape this "
                           "verdict; the judges are that module's own judge functions, so "
                           "there is one implementation of each claim"),
        "does_not_prove": ("that jcc_core survives a reboot unattended -- no component in "
                           "this repository restarts the service; and nothing about "
                           "FINAL-13_PUBLISH, which has not run yet"),
        "legs": legs,
        "control_proofs": _proof_digest(proof_readings),
        "pending_after_this_step": {
            "id": "FINAL-13_PUBLISH",
            "why": ("it publishes the record this verdict is written into, so its artifact "
                    "cannot exist yet; the project-run verdict that covers it is the one "
                    "FINAL-13 writes into artifacts/release/final-manifest.json"),
        },
        "working_tree_at_evaluation": {
            "dirty_paths": tree,
            "count": len(tree),
            "note": ("READING ONLY, not a criterion. The load-bearing cleanliness check is "
                     "P-01 inside FINAL-11, which is part of the gate verdict re-checked "
                     "above; duplicating it here would be a second place to disagree."),
        },
        "problems": problems,
    }
    return payload


def selftest() -> int:
    """Prove the rule refuses the ways this step could fake a pass."""
    cases: List[Tuple[str, bool, str]] = []

    def case(name: str, ok: bool, detail: str = "") -> None:
        cases.append((name, ok, detail))

    def leg(**kw: Any) -> Dict[str, Any]:
        base = {"id": "FINAL-XX", "artifact": "artifacts/acceptance/x.json",
                "artifact_present": True, "sha_on_disk": "a" * 64,
                "sha_recorded": "a" * 64, "sha_matches_record": True,
                "recorded_status": "PASS", "judge_ok": True, "judge_detail": "ok"}
        base.update(kw)
        return base

    green = [leg(id="FINAL-01_CONTROLLER"), leg(id="FINAL-11_FINAL_GATE")]
    ok, problems = judge(green)
    case("a_green_set_is_green", ok and not problems, str(problems))

    ok, problems = judge([leg(id="FINAL-01_CONTROLLER"),
                          leg(id="FINAL-11_FINAL_GATE", judge_ok=False,
                              judge_detail="project_complete=False")])
    case("one_refused_leg_is_red", (not ok) and any("FINAL-11" in p for p in problems),
         str(problems))

    ok, problems = judge([])
    case("an_empty_run_is_not_green", (not ok) and len(problems) == 1, str(problems))

    ok, problems = judge([leg(id="FINAL-08_SOAK", artifact_present=False)])
    case("a_deleted_artifact_is_not_green",
         (not ok) and any("not on disk any more" in p for p in problems), str(problems))

    ok, problems = judge([leg(id="FINAL-08_SOAK", sha_matches_record=False,
                              sha_recorded="b" * 64)])
    case("an_artifact_swapped_after_judging_is_not_green",
         (not ok) and any("changed after it was judged" in p for p in problems),
         str(problems))

    ok, problems = judge([leg(id="FINAL-09_RECOVERY", recorded_status=None)])
    case("a_leg_that_was_never_run_is_not_green",
         (not ok) and any("no step record" in p for p in problems), str(problems))

    # The rule must be refusable in BOTH directions: a green set that is reported
    # red is as much a defect as a red set reported green, and this file has
    # already shipped once in a project where only one direction was tested.
    ok, _ = judge(green)
    case("a_green_set_is_not_reported_red", ok is True, "the rule is not merely strict")

    # ---- the section-L/F control-plane proofs ---------------------------------
    def proof(**kw: Any) -> Dict[str, Any]:
        base = {"name": "RUNTIME_LEASE_PROOF",
                "artifact": "artifacts/acceptance/RUNTIME_LEASE_PROOF.json",
                "produced_by": "tools/tests/runtime_lease_falsifier.py",
                "present": True, "sha256": "c" * 64,
                "payload": {"schema": "jcc-control-proof/1", "name": "RUNTIME_LEASE_PROOF",
                            "ok": True, "status": "PASS", "verdict": "PASS: proved",
                            "cases_total": 8, "cases_passed": 8,
                            "checks": [{"name": "c%d" % i, "ok": True, "detail": ""}
                                       for i in range(8)]}}
        base.update(kw)
        return base

    ok, problems = judge_control_proofs([proof()])
    case("a_green_control_proof_is_green", ok and not problems, str(problems))

    ok, problems = judge_control_proofs([proof(present=False, payload=None)])
    case("a_missing_control_proof_is_red",
         (not ok) and any("MISSING" in p for p in problems), str(problems))

    ok, problems = judge_control_proofs([proof(payload={"schema": "jcc-control-proof/1",
                                                        "ok": False, "status": "FAIL",
                                                        "verdict": "FAIL: heartbeat frozen",
                                                        "cases_total": 8, "cases_passed": 3,
                                                        "checks": [{"name": "h", "ok": False}]})])
    case("a_red_control_proof_is_red",
         (not ok) and any("ok=False" in p or "cases_passed" in p for p in problems),
         str(problems))

    ok, problems = judge_control_proofs([proof(payload={"schema": "jcc-control-proof/1",
                                                        "ok": True, "status": "PASS",
                                                        "cases_total": 0, "cases_passed": 0,
                                                        "checks": []})])
    case("a_zero_case_control_proof_is_red", not ok, str(problems))

    ok, problems = judge_control_proofs([])
    case("no_control_proof_examined_is_red", (not ok), str(problems))

    # both directions again, for the proof rule specifically
    ok, _ = judge_control_proofs([proof()])
    case("a_green_control_proof_is_not_reported_red", ok is True, "the rule is not merely strict")

    for name, passed, detail in cases:
        log("  [%s] %-46s %s" % ("PASS" if passed else "FAIL", name, detail[:110]))
    good = sum(1 for _n, p, _d in cases if p)
    log("project_run_gate selftest: %d/%d" % (good, len(cases)))
    return 0 if good == len(cases) else 1


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="FINAL-12 project-run gate")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()

    log("FINAL-12 project-run gate -- every FINAL-phase acceptance leg together")
    payload = collect()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(OUT), suffix=".tmp")
    try:
        with io.open(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, OUT)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    for p in payload["problems"]:
        log("  PROBLEM %s" % p)
    log("verdict=%s -> %s" % (payload["status"], OUT))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
