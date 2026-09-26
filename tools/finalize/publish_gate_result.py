#!/usr/bin/env python3
"""Publish the tracked canonical gate summary from the gate's IGNORED staging result.

WHY THIS STEP EXISTS (deliverable F -- three-layer provenance, no self-reference)
-------------------------------------------------------------------------------
`final_gate_run.py --json-out artifacts/acceptance/final_gate_result.json` writes
a TRACKED file. That is the self-reference the FINAL pipeline kept tripping over:

  * P-01 requires a clean tree, and P-01 is computed from `git status` inside the
    very run that rewrites the file, so the two requirements are unsatisfiable
    whenever the written bytes are not a pure function of the committed tree;
  * and a commit must never be required to already contain a gate result that does
    not exist yet.

So the gate's AUTHORITATIVE result is now written to an IGNORED staging path
(`runtime/final-gate/final_gate_result.json`, under the gitignored `runtime/`),
where it is free to carry the momentary layers of provenance. This tool is the
FINAL-11B step: it reads the staging result and materialises the TRACKED canonical
summary that every downstream consumer (project_run_gate, the publisher, the
readiness reports) already reads.

THREE-LAYER PROVENANCE
----------------------
  * PRODUCT SHA          -- the frozen product/ops commit (stable, tree identity).
  * EVIDENCE PRE-GATE SHA-- the commit the gate was evaluated on (from `moment`).
  * FINAL EVIDENCE SHA   -- the commit that will carry the final evidence; it
                            cannot be named by the file it is inside, so it is
                            recorded as null here and named by the release manifest
                            once the commit exists.

P-01 IS NOT TOUCHED. This tool neither reads nor weakens the cleanliness rule; it
only decides WHERE the gate writes, and copies the result to where the readers
look. If the staging result is missing or does not carry a verdict, it FAILS.

Exit codes: 0 published; 2 refused (staging missing / unreadable / no verdict).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys

ROOT = os.environ.get("JCC_ROOT") or r"F:\jev"
STAGING = os.path.join(ROOT, "runtime", "final-gate", "final_gate_result.json")
CANONICAL = os.path.join(ROOT, "artifacts", "acceptance", "final_gate_result.json")
PRODUCT_FREEZE = "5ab5e20d961db4c9807f578195ae8530dea2f7d9"


def _sha256(path: str):
    if not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with io.open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: str):
    if not os.path.isfile(path):
        return None
    try:
        with io.open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def canonicalise(staging: dict) -> dict:
    """Pure: the staging verdict -> the tracked canonical verdict.

    The `moment` block (commit seen, clock) is REMOVED -- a re-committed verdict
    must stay a pure function of the tree, so no moment may survive into it. The
    three provenance layers are surfaced in the canonical file: the product layer
    as identity, the pre-gate layer as a named record, and the final-evidence layer
    as null (it does not exist yet -- that is the whole reason this order is used).
    """
    out = dict(staging)
    moment = out.pop("moment", None) or {}
    prov = dict(out.get("provenance") or {})
    prov["product_sha"] = prov.get("product_sha") or PRODUCT_FREEZE
    prov["evidence_pre_gate_sha"] = moment.get("evidence_pre_gate_sha")
    prov["final_evidence_sha"] = None
    prov["provenance_layers"] = ["product_sha", "evidence_pre_gate_sha",
                                 "final_evidence_sha"]
    prov["published_from"] = "runtime/final-gate/final_gate_result.json"
    prov["note"] = ("final_evidence_sha is null by construction: a file cannot name "
                    "the commit that contains it. It is recorded by the release "
                    "manifest once that commit exists.")
    out["provenance"] = prov
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="publish the tracked canonical gate summary")
    ap.add_argument("--staging", default=STAGING)
    ap.add_argument("--canonical", default=CANONICAL)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    staging = load_json(args.staging)
    if not isinstance(staging, dict):
        print("REFUSING: the gate's staging result is missing or unparsable: %s" % args.staging)
        return 2
    if "project_complete" not in staging:
        print("REFUSING: the staging result carries no verdict (no project_complete)")
        return 2

    out = canonicalise(staging)
    d = os.path.dirname(args.canonical)
    if d:
        os.makedirs(d, exist_ok=True)
    with io.open(args.canonical, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False, sort_keys=True)
        fh.write("\n")
    print("published canonical gate summary -> %s (project_complete=%r blocker=%r)"
          % (args.canonical, out.get("project_complete"), out.get("blocker")))
    return 0


def selftest() -> int:
    cases = []

    def case(name, ok, detail=""):
        cases.append((name, ok, detail))

    staging = {
        "project_complete": True, "blocker": 0, "missing": [], "failed": [],
        "provenance": {"release_freeze": PRODUCT_FREEZE, "product_sha": PRODUCT_FREEZE},
        "moment": {"evidence_pre_gate_sha": "deadbeef" * 5, "final_evidence_sha": None},
    }
    out = canonicalise(staging)
    case("the_moment_block_is_removed", "moment" not in out, str(list(out.keys()))[:80])
    case("the_product_layer_survives", out["provenance"]["product_sha"] == PRODUCT_FREEZE)
    case("the_pre_gate_layer_is_named",
         out["provenance"]["evidence_pre_gate_sha"] == "deadbeef" * 5)
    case("the_final_evidence_layer_is_null_by_construction",
         out["provenance"]["final_evidence_sha"] is None)
    case("the_verdict_fields_survive",
         out["project_complete"] is True and out["blocker"] == 0)

    print("=" * 74)
    for name, ok, detail in cases:
        print("  [%s] %-52s %s" % ("PASS" if ok else "FAIL", name, detail))
    bad = [c for c in cases if not c[1]]
    print("=" * 74)
    print("publish_gate_result selftest: %d/%d" % (len(cases) - len(bad), len(cases)))
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
