#!/usr/bin/env python3
"""JCC v1.3 Product Edition -- SIGNED RELEASE LEGS (owner: Orion).

One signed approval ("leg") per release dimension, DERIVED FROM THE FINAL GATE'S
OWN LEG RECORDS rather than from remembered strings. That direction matters: a
hand-written leg records what someone believed at signing time, and this project
has twice shipped a leg whose evidence path no longer contained the verdict it
was quoted for. Here every leg's decision is a pure function of the gate result
and the per-leg evidence files the gate itself wrote, and the leg carries the
sha256 of the file it rests on.

Dimensions
----------
  ARCH             product freeze governance (W0 non-self-referential model)
  CONTRACT         the per-Wave product contract legs, re-measured at the freeze
  REGRESSION       full core+e2e / tools / frontend regression
  WAVE_GATES       the seven whole Wave gates, re-run at the freeze
  RUNTIME          the durable product runtime + the frozen config's inertness
  SECURITY         the v1.1 security generator re-run live against the surface
  SERVING_PATH     the SPA is served by ASGI, not python's stdlib demo server
  GREEN_PROTECTION protected GREEN 4778 alive and never written to
  RELEASE_HYGIENE  frozen tree, committed inputs, A8 pins vs git blobs
  SELF_FALSIFICATION the gate's own instruments reject the false shapes

Honesty rules
-------------
  * Every signature is a FRESH sha256 of the leg's canonical content, computed
    now. None of them reuse a v1.0.1 (``f8ac95d``), v1.1, or v1.2 (``ddca208``)
    signature, and the leg records that as data, not as a promise.
  * A leg exists only if EVERY gate leg it maps to is present AND PASS. A leg
    whose gate legs are missing FAILS: an absent measurement is not an approval.
  * ``GO_WITH_PENDING`` is used where the evidence is complete but a HOST action
    remains (the durable task registration). It is never spelled ``GO``.

Outputs
-------
  artifacts/release/v1.3_product/legs/V1_3_SIGNED_LEG_<ID>.json
  artifacts/release/v1.3_product/legs/V1_3_LEG_SIGNATURES.json

Usage
-----
  F:/jev/.venv/Scripts/python.exe tools/emit_v1_3_legs.py
  F:/jev/.venv/Scripts/python.exe tools/emit_v1_3_legs.py --check
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

REPO = Path(__file__).resolve().parents[1]
ART = REPO / "artifacts" / "release" / "v1.3_product"
LEGS = ART / "legs"
GATE_RESULT = ART / "FINAL_GATE_V1_3_RESULT.json"
SIGS = LEGS / "V1_3_LEG_SIGNATURES.json"

SIGNER = "Orion (JCC v1.3 final product gate)"

#: Superseded freezes whose signatures must NEVER be reused here.
FORBIDDEN_SIGNATURE_SOURCES = {
    "v1.0.1-ops": "f8ac95d",
    "v1.1": "5ab5e20",
    "v1.2-visual-local": "ddca208",
}

#: leg_id -> (dimension, [gate leg ids it rests on], pending host note or None)
DIMENSIONS: Dict[str, tuple] = {
    "ARCH": ("Architecture / Product Freeze Governance",
             ["E1_FROZEN_TREE", "E2_GATE_INPUTS_TRACKED", "E3_A8_PINS_VS_HEAD_BLOBS"],
             None),
    "CONTRACT": ("Product Contract per Wave",
                 ["A1_FREEZE_MODEL", "A2_DOMAIN", "A3_MISSION", "A4_ATTENTION",
                  "A5_DELIVERABLES", "A6_CONNECTIONS", "A7_INVARIANTS"],
                 None),
    "REGRESSION": ("Full Regression (core+e2e / tools / frontend)",
                   ["C1_CORE_E2E", "C2_TOOLS_TESTS", "C3a_TYPECHECK",
                    "C3b_VITEST", "C3c_BUILD"],
                   None),
    "WAVE_GATES": ("Wave Gates re-run at the freeze",
                   ["B1_W2_PRODUCT_UI", "B2_W3_MISSION_GATE",
                    "B3_W4_ATTENTION_GATE", "B4_W5_DELIVERABLES_GATE",
                    "B5_W6_WORLD_GATE", "B6_W7_CONNECTION_GATE",
                    "B7_W8_RUNTIME_GATE"],
                   None),
    "RUNTIME": ("Durable Product Runtime / frozen config inertness",
                ["B7_W8_RUNTIME_GATE", "D2_PRODUCT_CONFIG_INERT"],
                "Durable Task Scheduler registration is a HOST action: run "
                "scripts\\jcc-v1.3-register-tasks.ps1 -Apply elevated, then "
                "`scripts\\jcc-v1.3.ps1 -Action mark`."),
    "SECURITY": ("Security Gate re-run live",
                 ["A8_SECURITY_GATE"],
                 None),
    "SERVING_PATH": ("ASGI serving path, not python http.server",
                     ["D3_UI_IS_NOT_STDLIB"],
                     None),
    "GREEN_PROTECTION": ("Protected GREEN 4778",
                         ["D1_GREEN_PROTECTION"],
                         None),
    "RELEASE_HYGIENE": ("Reproducible-from-a-clone hygiene",
                        ["E1_FROZEN_TREE", "E2_GATE_INPUTS_TRACKED",
                         "E3_A8_PINS_VS_HEAD_BLOBS",
                         "E4_NO_LISTENERS_LEFT_BEHIND"],
                        None),
    "SELF_FALSIFICATION": ("The gate's own instruments can fail",
                           ["F1_INSTRUMENTS_ARE_FALSIFIABLE"],
                           None),
}


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def git(*a: str) -> str:
    return subprocess.run(["git", *a], cwd=str(REPO), capture_output=True,
                          text=True, errors="replace").stdout.strip()


def leg_path(leg_id: str) -> Path:
    return LEGS / ("V1_3_LEG_%s.json" % leg_id.replace("/", "_"))


def load_gate_result() -> Dict[str, Any]:
    if not GATE_RESULT.is_file():
        raise SystemExit("no final gate result at %s -- run "
                         "tools/final_gate_run_v1_3.py --full first" % GATE_RESULT)
    return json.loads(GATE_RESULT.read_text(encoding="utf-8"))


FREEZE_DOC = "docs/v1_3/FINAL_PRODUCT_FREEZE.md"


def freeze_anchor() -> Optional[str]:
    """The freeze is DEFINED as the commit that introduced the freeze document.

    Not a sha written inside the document: a document cannot contain its own
    commit's sha, so a hard-coded value is either contradictory or requires a
    later "refresh the anchor" commit -- the ritual the v1.3 Wave 0 model
    retired. Resolution is stable by construction, and the final gate's
    ``E5_FREEZE_ANCHOR_RESOLVES`` leg asserts the same thing independently.
    """
    out = git("log", "-1", "--format=%H", "--", FREEZE_DOC)
    return out or None


def make_leg(leg_id: str, gate_doc: Dict[str, Any], head: str) -> Dict[str, Any]:
    dimension, needs, pending = DIMENSIONS[leg_id]
    rows: List[Dict[str, Any]] = []
    missing: List[str] = []
    failed: List[str] = []
    for nid in needs:
        p = leg_path(nid)
        if not p.is_file():
            missing.append(nid)
            continue
        rec = json.loads(p.read_text(encoding="utf-8"))
        rows.append({
            "leg_id": nid,
            "verdict": rec.get("verdict"),
            "head": rec.get("head"),
            "detail": rec.get("detail"),
            "evidence_path": str(p.relative_to(REPO)).replace("\\", "/"),
            "evidence_sha256": sha256_bytes(p.read_bytes()),
        })
        if rec.get("verdict") != "PASS":
            failed.append(nid)
        elif rec.get("head") != head:
            failed.append("%s(measured at %s, not %s)"
                          % (nid, str(rec.get("head"))[:12], head[:12]))

    if missing:
        decision = "NO_GO"
        notes = ("REFUSED: the gate legs this leg rests on were never produced: "
                 "%s. An absent measurement is not an approval." % missing)
    elif failed:
        decision = "NO_GO"
        notes = "REFUSED: %s did not pass at this head." % failed
    elif pending:
        decision = "GO_WITH_PENDING"
        notes = ("All %d gate legs PASS at this head. A HOST action remains: %s"
                 % (len(needs), pending))
    else:
        decision = "GO"
        notes = ("All %d gate legs PASS at this head (%s)." % (len(needs), head[:12]))

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    canonical = json.dumps({
        "leg_id": leg_id,
        "dimension": dimension,
        "head": head,
        "decision": decision,
        "gate_legs": [{"leg_id": r["leg_id"], "evidence_sha256": r["evidence_sha256"]}
                      for r in rows],
        "signer": SIGNER,
        "timestamp_utc": ts,
    }, sort_keys=True, separators=(",", ":"))
    return {
        "schema": "jcc/v1.3-leg/1",
        "leg_id": leg_id,
        "dimension": dimension,
        "product_freeze_sha": head,
        "decision": decision,
        "gate_legs": rows,
        "gate_legs_required": list(needs),
        "gate_legs_missing": missing,
        "gate_legs_failed": failed,
        "notes": notes,
        "signer": SIGNER,
        "timestamp_utc": ts,
        "signature": sha256_bytes(canonical.encode("utf-8")),
        # stated as data so a reader can check it, not as a promise
        "reuses_earlier_signature": False,
        "earlier_signatures_available": FORBIDDEN_SIGNATURE_SOURCES,
        "project_complete_at_signing": bool(gate_doc.get("project_complete")),
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="JCC v1.3 signed release legs")
    ap.add_argument("--check", action="store_true",
                    help="verify the signed legs on disk still match the gate result")
    ns = ap.parse_args(argv)

    gate = load_gate_result()
    head = gate.get("head") or git("rev-parse", "HEAD")
    LEGS.mkdir(parents=True, exist_ok=True)

    if ns.check:
        problems = []
        for leg_id in DIMENSIONS:
            p = LEGS / ("V1_3_SIGNED_LEG_%s.json" % leg_id)
            if not p.is_file():
                problems.append("missing %s" % p.name)
                continue
            rec = json.loads(p.read_text(encoding="utf-8"))
            if rec.get("product_freeze_sha") != head:
                problems.append("%s signed at %s, gate result is at %s"
                                % (leg_id, rec.get("product_freeze_sha"),
                                   head))
            for row in rec.get("gate_legs", []):
                q = REPO / row["evidence_path"]
                if not q.is_file():
                    problems.append("%s: evidence vanished %s" % (leg_id, q))
                elif sha256_bytes(q.read_bytes()) != row["evidence_sha256"]:
                    problems.append("%s: evidence changed since signing %s"
                                    % (leg_id, q.name))
        if problems:
            print("LEGS CHECK FAILED (%d):" % len(problems))
            for p in problems:
                print("  - %s" % p)
            return 1
        print("LEGS CHECK CLEAN: %d signed legs, all evidence digests match, "
              "head=%s" % (len(DIMENSIONS), head[:12]))
        return 0

    if not gate.get("project_complete"):
        print("REFUSING TO SIGN: the final gate is NOT complete "
              "(blockers=%s)" % gate.get("blockers"))
        return 2

    anchor = freeze_anchor()
    if anchor is None:
        print("REFUSING TO SIGN: could not resolve an introducing commit for %s"
              % FREEZE_DOC)
        return 2
    if anchor != head:
        print("REFUSING TO SIGN: %s was introduced by %s but the gate measured %s "
              "-- the product freeze and the measured commit are not the same "
              "thing" % (FREEZE_DOC, anchor[:12], head[:12]))
        return 2

    sigs: Dict[str, Any] = {}
    worst = 0
    for leg_id in DIMENSIONS:
        rec = make_leg(leg_id, gate, head)
        out = LEGS / ("V1_3_SIGNED_LEG_%s.json" % leg_id)
        body = (json.dumps(rec, indent=2, sort_keys=True, ensure_ascii=False)
                + "\n").encode("utf-8")
        out.write_bytes(body)
        print("wrote %-28s decision=%-16s signature=%s"
              % (out.name, rec["decision"], rec["signature"][:16]))
        sigs[leg_id] = {"decision": rec["decision"],
                        "signature": rec["signature"],
                        "gate_legs": [r["leg_id"] for r in rec["gate_legs"]],
                        "evidence_sha256": sha256_bytes(body)}
        if rec["decision"] == "NO_GO":
            worst = 1

    bundle = {
        "schema": "jcc/v1.3-leg-signatures/1",
        "release": "JCC v1.3 Product Edition (local)",
        "product_freeze_sha": head,
        "signer": SIGNER,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "gate_run_id": gate.get("run_id"),
        "gate_project_complete": gate.get("project_complete"),
        "legs": sigs,
        "note": ("Every signature is freshly computed from this gate run's own "
                 "evidence; none reuse the v1.0.1 (%s), v1.1 (%s) or v1.2 (%s) "
                 "signatures."
                 % (FORBIDDEN_SIGNATURE_SOURCES["v1.0.1-ops"],
                    FORBIDDEN_SIGNATURE_SOURCES["v1.1"],
                    FORBIDDEN_SIGNATURE_SOURCES["v1.2-visual-local"])),
    }
    SIGS.write_bytes((json.dumps(bundle, indent=2, sort_keys=True,
                                 ensure_ascii=False) + "\n").encode("utf-8"))
    print("wrote %s (%d legs)" % (SIGS.name, len(sigs)))
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
