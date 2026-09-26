#!/usr/bin/env python3
"""Generate `final_sre_go.json` from evidence. Never hand-write the verdict.

The verdict is derived, not decided: every leg and every E-07 step must be PASS,
otherwise the document is NO-GO. There is no flag, environment variable or
command line switch that can turn a NO-GO into a GO, because (a) the file is
derived from files that are themselves machine-produced, and (b) the file is now
tracked in git, so an in-place edit to make it look better shows up in
`git status` and breaks the clean-tree gate.

Usage:
    python tools/generate_final_sre_go.py [--evidence <path>] [--out <path>]
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
from datetime import datetime, timezone, timedelta

ROOT = r"F:\jev"
DEFAULT_EVIDENCE = os.path.join(ROOT, r"artifacts\acceptance\win_scripts\E07_FINAL_EVIDENCE.json")
DEFAULT_OUT = os.path.join(ROOT, r"artifacts\acceptance\final_sre_go.json")
ARCH_LEG = os.path.join(ROOT, r"artifacts\acceptance\final_architecture_go.json")
SEC_LEG = os.path.join(ROOT, r"artifacts\acceptance\final_security_go.json")

TZ = timezone(timedelta(hours=8))

# The product freeze, and what a release-evidence commit is allowed to touch.
# Copied from artifacts/acceptance/final_gate_run.py so the signer and the gate
# cannot disagree about where a leg may be signed -- they disagreed once, and the
# disagreement made a legitimate state unrepresentable (see leg_position_ok).
RELEASE_FREEZE = "df3e7d91ac274d971ef110199d5b65acf131aacd"
EVIDENCE_PREFIXES = (
    "artifacts/", "docs/", "tools/", "scripts/", ".gitignore",
    "FINAL_ACTIVATION_CHECKLIST.md", "FINAL_ACCEPTANCE_REPORT.md",
)
GO_BEARING_VERDICTS = ("GO", "GO WITH CONDITIONS")


def git(*args: str) -> str:
    import subprocess
    return subprocess.check_output(
        ["git", *args], cwd=ROOT, text=True, errors="replace"
    ).strip()


def leg_position_ok(leg_sha: str, ev_head: str) -> tuple[bool, str]:
    """Can a leg signed at ``leg_sha`` still be read as describing ``ev_head``?

    Mirrors the two positions ``final_gate_run.py`` accepts, and applies the same
    proof for the second one -- a signature cannot contain itself and claim to
    have been written at the very commit that contains it, so a leg is signed at
    the release freeze and the gate proves nothing outside release evidence has
    moved since.

    WHY THIS REPLACES ``release_sha.startswith(ev_head[:12])``. That predicate
    demanded the legs be signed at the exact commit E-07 happened to run on, while
    the gate accepts the freeze position -- and a signer stricter than its own gate
    rejects every configuration the gate calls valid. On 2026-09-26 that made the
    SRE leg un-signable: E-07 ran at a47bf7b (release evidence only, ahead of the
    freeze) and both other legs sit at the freeze, so the predicate fired on a
    state the gate passes. A legitimate state must be representable, or the signer
    is the defect.
    """
    if not leg_sha:
        return False, "names no release_sha"
    if leg_sha == ev_head:
        return True, "signed at the commit E-07 ran on (%s)" % ev_head[:12]
    if leg_sha.startswith(RELEASE_FREEZE[:12]):
        changed = [p for p in git("diff", "--name-only", RELEASE_FREEZE, ev_head).split() if p]
        offenders = [p for p in changed
                     if not any(p.startswith(pre) for pre in EVIDENCE_PREFIXES)]
        if offenders:
            return False, ("signed at the release freeze but %d path(s) outside release "
                           "evidence changed since: %s"
                           % (len(offenders), ", ".join(offenders[:5])))
        return True, ("signed at the release freeze %s; the %d path(s) added since are "
                      "release evidence only" % (RELEASE_FREEZE[:12], len(changed)))
    return False, ("signed at %s, neither the commit E-07 ran on (%s) nor the release "
                   "freeze (%s)" % (leg_sha[:12], ev_head[:12], RELEASE_FREEZE[:12]))


def load_json(path: str) -> dict | None:
    if not os.path.isfile(path):
        return None
    try:
        with io.open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (ValueError, OSError):
        return None


def sha256_file(path: str) -> str | None:
    if not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with io.open(path, "rb") as fh:
        for c in iter(lambda: fh.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def supersedes_block(out_path: str, new_verdict: str) -> dict | None:
    """Keep the verdict this signature replaces on the record.

    Two signers were once assigned this same leg file and the second write erased
    the first's evidence; the team-lead ruled that neither may be erased by the
    other. A superseded NO-GO is evidence about what was true then, so it is
    carried into the new document instead of being dropped.
    """
    prev = load_json(out_path)
    if not prev:
        return None
    old = str(prev.get("verdict", "")).strip().upper()
    if not old or old == new_verdict.strip().upper():
        return None
    return {
        "verdict": prev.get("verdict"),
        "release_sha": prev.get("release_sha"),
        "signed_at": prev.get("signed_at"),
        "signer": prev.get("signer"),
        "sha256": sha256_file(out_path),
        "conditions_count": len(prev.get("conditions") or []),
        "note": ("Superseded by this signature, preserved rather than overwritten. "
                 "The earlier verdict named the operator-lifecycle defects that were "
                 "repaired afterwards in scripts/ (an evidence prefix, so the repair "
                 "did not invalidate the freeze position it was signed at)."),
    }


def freeze_proof(ev_head: str) -> dict:
    """The same proof final_gate_run.py applies to a freeze-signed leg."""
    changed = [p for p in git("diff", "--name-only", RELEASE_FREEZE, ev_head).split() if p]
    evidence = [p for p in changed if any(p.startswith(x) for x in EVIDENCE_PREFIXES)]
    others = [p for p in changed if p not in evidence]
    return {
        "release_freeze": RELEASE_FREEZE,
        "e07_head_sha": ev_head,
        "changed_paths_count": len(changed),
        "changed_paths_all_release_evidence": not others,
        "non_evidence_paths": others,
        "scripts_changed_since_freeze": [p for p in changed if p.startswith("scripts/")],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence", default=DEFAULT_EVIDENCE)
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args(argv)

    problems: list[str] = []

    ev = load_json(args.evidence)
    if not ev:
        print("NO-GO: evidence missing or unreadable: %s" % args.evidence)
        return 2

    steps = ev.get("steps") or []
    non_pass = [s.get("name") for s in steps if s.get("result") != "PASS"]
    if non_pass:
        problems.append("E-07 steps not PASS: %r" % non_pass)

    # A truncated evidence file whose remaining steps are all PASS would otherwise
    # sign as GO -- the "instrument silently did less and still reported success"
    # class. The count and the step set are checked against what the evidence
    # itself declares, not against a literal, so the check cannot drift.
    expected = ev.get("expected_steps")
    if not expected:
        problems.append("evidence declares no expected_steps; the run's completeness is unverifiable")
    else:
        if len(steps) != expected:
            problems.append("evidence holds %d step(s) but declares expected_steps=%d"
                            % (len(steps), expected))
        seen = sorted(s.get("step") for s in steps if isinstance(s.get("step"), int))
        if seen != list(range(1, int(expected) + 1)):
            problems.append("step set %r != 1..%d" % (seen, int(expected)))

    doc = {
        "evidence_sha256": sha256_file(args.evidence),
        "steps_total": len(steps),
        "steps_expected": expected,
        "steps_pass": len(steps) - len(non_pass),
        "steps_not_pass": non_pass,
    }

    # the other two legs, and the release sha they were signed on
    legs = {}
    for name, path in (("architecture", ARCH_LEG), ("security", SEC_LEG)):
        leg = load_json(path) or {}
        verdict = leg.get("verdict")
        legs[name] = {
            "verdict": verdict,
            "release_sha": leg.get("release_sha"),
            "signed_at": leg.get("signed_at"),
            "sha256": sha256_file(path),
        }
        if verdict not in ("GO", "GO WITH CONDITIONS"):
            problems.append("leg %s verdict is %r" % (name, verdict))

    ev_head = ev.get("head_sha")
    for name, info in legs.items():
        ok_pos, why = leg_position_ok(str(info.get("release_sha") or ""), str(ev_head or ""))
        info["position"] = ("accepted: " + why) if ok_pos else ("refused: " + why)
        if not ok_pos:
            problems.append("leg %s %s" % (name, why))

    aborted = ev.get("abort")
    if aborted:
        problems.append("E-07 aborted: %s" % aborted.get("reason"))

    if problems:
        doc.update({
            "verdict": "NO-GO",
            "release_sha": RELEASE_FREEZE,
            "e07_head_sha": ev_head,
            "signed_at": datetime.now(TZ).isoformat(),
            "signer": "generate_final_sre_go.py",
            "generator": "tools/generate_final_sre_go.py (derived; do not hand-edit)",
            "freeze_proof": freeze_proof(str(ev_head or "")),
            "supersedes": supersedes_block(args.out, "NO-GO"),
            "problems": problems,
            "legs": legs,
            "evidence_refs": [
                {"name": s.get("name"), "result": s.get("result"),
                 "timestamp": s.get("timestamp"), "duration_s": s.get("duration_s"),
                 "measured_pid": s.get("measured_pid"),
                 "measured_port": s.get("measured_port")}
                for s in steps
            ],
        })
        tmp = args.out + ".tmp.%d" % os.getpid()
        with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(doc, indent=2, ensure_ascii=False))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, args.out)
        print("NO-GO (%d problem(s)) -> %s" % (len(problems), args.out))
        for p in problems:
            print("   -", p)
        return 1

    doc.update({
        "verdict": "GO",
        "release_sha": RELEASE_FREEZE,
        "e07_head_sha": ev_head,
        "signed_at": datetime.now(TZ).isoformat(),
        "signer": "generate_final_sre_go.py",
        "generator": "tools/generate_final_sre_go.py (derived; do not hand-edit)",
        "freeze_proof": freeze_proof(str(ev_head or "")),
        "supersedes": supersedes_block(args.out, "GO"),
        "product": "JEV COMMAND CENTER",
        "phase": "P6 / E-07 final acceptance",
        "legs": legs,
        "conditions": [],
        "evidence_refs": [
            {"name": s.get("name"), "result": s.get("result"),
             "timestamp": s.get("timestamp"), "duration_s": s.get("duration_s"),
             "measured_pid": s.get("measured_pid"),
             "measured_port": s.get("measured_port"),
             "command": s.get("command"),
             "exit_code": s.get("exit_code"),
             "stdout_sha256": s.get("stdout_sha256"),
             "stderr_sha256": s.get("stderr_sha256")}
            for s in steps
        ],
        "throughput": next(
            ({"rate_per_s": s["details"].get("rate_per_s"),
              "ok": s["details"].get("ok"),
              "failed": s["details"].get("failed"),
              "window_s": s["details"].get("window_s")}
             for s in steps if s.get("name") == "THROUGHPUT"), None),
    })

    tmp = args.out + ".tmp.%d" % os.getpid()
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(doc, indent=2, ensure_ascii=False))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, args.out)
    print("GO -> %s (%d evidence refs)"
          % (args.out, len(doc["evidence_refs"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
