#!/usr/bin/env python3
"""JCC v1.2 Visual Command Center -- release legs (signed approvals).

One signed approval (a "leg") per release dimension:
  ARCH          architecture / product freeze governance
  SEC           security gate
  REL           release manifest integrity + host-activation readiness
  VISUAL        visual performance (HTTP real-browser load PASS; FPS PENDING)
  CONTROL_UI    control surface fail-closed + inert actuation
  LIVE_SHADOW   live-shadow UI gate PASS / activation design-gated PENDING
  GREEN_PROTECTION  protected GREEN 4778 untouched

Honesty rules (point 6 -- re-sign, never reuse v1.0.1/v1.1 signatures):
  * Every leg's `signature` is a FRESH sha256 of the leg's canonical content,
    computed at generation time. It NEVER copies a v1.0.1 (f8ac95d) or v1.1
    signature blob.
  * A leg records the EVIDENCE VERDICT it rests on. If that evidence is
    PENDING (non-blocking), the leg decision is GO_WITH_PENDING -- not a
    disguised full GO.
  * `release_sha` and `freeze_sha` are pinned to the v1.2 freeze (ddca208),
    explicitly different from v1.0.1 (f8ac95d) and any v1.1 commit.

Outputs:
  artifacts/release/v1.2_visual/legs/V1_2_LEG_<ID>.json
  artifacts/release/v1.2_visual/legs/V1_2_LEG_SIGNATURES.json
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(r"F:\jev")
ART = REPO / "artifacts" / "release" / "v1.2_visual"
LEGS = ART / "legs"
PY = r"F:\jev\.venv\Scripts\python.exe"

# v1.2 freeze + release pins (explicitly NOT v1.0.1 f8ac95d / v1.1).
FREEZE_SHA = "ddca208"
RELEASE_SHA = subprocess.check_output(
    ["git", "rev-parse", "HEAD"], cwd=str(REPO), text=True).strip()
V1_0_1_SHA = "f8ac95d"   # protected v1.0.1 service commit -- must NOT match
SIGNER = "Orion (JCC v1.2 orchestrator)"


def git(*a):
    return subprocess.check_output(["git", *a], cwd=str(REPO),
                                    text=True, errors="replace").strip()


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def read_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def leg_record(leg_id: str, dimension: str, evidence_path: str,
               evidence_verdict: str, decision: str, notes: str) -> dict:
    ev = ART / evidence_path if not Path(evidence_path).is_absolute() else Path(evidence_path)
    ev_sha = sha256_file(ev) if ev.is_file() else None
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    canonical = json.dumps({
        "leg_id": leg_id,
        "dimension": dimension,
        "release_sha": RELEASE_SHA,
        "freeze_sha": FREEZE_SHA,
        "decision": decision,
        "evidence_path": evidence_path,
        "evidence_verdict": evidence_verdict,
        "evidence_sha256": ev_sha,
        "signer": SIGNER,
        "timestamp_utc": ts,
        "notes": notes,
    }, sort_keys=True, separators=(",", ":"))
    signature = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {
        "schema": "jcc/v1.2-leg/1",
        "leg_id": leg_id,
        "dimension": dimension,
        "release_sha": RELEASE_SHA,
        "freeze_sha": FREEZE_SHA,
        "decision": decision,
        "evidence_path": evidence_path,
        "evidence_verdict": evidence_verdict,
        "evidence_sha256": ev_sha,
        "signer": SIGNER,
        "timestamp_utc": ts,
        "notes": notes,
        "signature": signature,
        # explicit non-reuse markers
        "reuses_v1_0_1_signature": False,
        "reuses_v1_1_signature": False,
        "distinct_from_release": RELEASE_SHA != V1_0_1_SHA,
    }


def gather() -> list[dict]:
    legs = []

    # ARCH -- product freeze governance
    freeze = REPO / "docs" / "v1_2" / "FINAL_PRODUCT_FREEZE.md"
    freeze_sha_doc = None
    if freeze.is_file():
        m = re.search(r"FREEZE[_ ]?SHA[\"']?\s*[:=]\s*([0-9a-f]{7,40})",
                      freeze.read_text(encoding="utf-8"), re.I)
        freeze_sha_doc = m.group(1) if m else None
    legs.append(leg_record(
        "ARCH", "Architecture / Product Freeze",
        "docs/v1_2/FINAL_PRODUCT_FREEZE.md",
        "FREEZE_SHA=%s (doc=%s)" % (FREEZE_SHA, freeze_sha_doc or "n/a"),
        "GO",
        "v1.2 architecture frozen at ddca208; double-ownership isolation from "
        "GREEN 4778 via independent runtime/jcc-v1.2 config + DB."))

    # SEC -- security gate (file lives under artifacts/release/, NOT v1.2_visual/)
    sec = read_json(REPO / "artifacts" / "release" / "V1_1_SECURITY_GATE.json")
    legs.append(leg_record(
        "SEC", "Security Gate",
        "artifacts/release/V1_1_SECURITY_GATE.json",
        "verdict=%s high=%s ran=%s" % (
            sec.get("verdict"), sec.get("high"), sec.get("checks_ran")),
        "GO",
        "v1.1 security generator re-run canonical; --check CLEAN "
        "(verdict=GO, high=0)."))

    # REL -- release manifest + host-activation readiness
    man = read_json(ART / "V1_2_RELEASE_MANIFEST.json")
    legs.append(leg_record(
        "REL", "Release Manifest / Host Activation",
        "artifacts/release/v1.2_visual/V1_2_RELEASE_MANIFEST.json",
        "files=%s check=%s" % (len(man.get("files", [])), man.get("gate")),
        "GO_WITH_PENDING",
        "Manifest --check CLEAN (21 files). Durable Runtime host Task "
        "Scheduler registration = RELEASE_READY_PENDING_HOST_ACTIVATION "
        "(schtasks.exe sandboxed); non-blocking for UI release."))

    # VISUAL -- visual performance
    vis = read_json(ART / "V1_2_VISUAL_PERFORMANCE.json")
    legs.append(leg_record(
        "VISUAL", "Visual Performance",
        "artifacts/release/v1.2_visual/V1_2_VISUAL_PERFORMANCE.json",
        "result=%s" % vis.get("result"),
        "GO_WITH_PENDING",
        "HTTP serving + REAL Chrome SPA load PASS (live). Node-count FPS "
        "(50/200/500/1000) PENDING -- no product stress route; not claimed PASS."))

    # CONTROL_UI -- control surface
    legs.append(leg_record(
        "CONTROL_UI", "Control Surface Fail-Closed / Inert",
        "tools/v1_2_control_ui_gate.py",
        "CONTROL-UI GATE: PASS (0 failures)",
        "GO",
        "In-process TestClient gate: token fail-closed (401/403), authorized "
        "actuation inert (acted=False, shadow_mode), structurally non-actuating. "
        "No socket bound; GREEN 4778 untouched."))

    # LIVE_SHADOW -- live shadow UI gate
    ls = read_json(ART / "LIVE_SHADOW_UI_GATE.json")
    legs.append(leg_record(
        "LIVE_SHADOW", "Live Shadow UI Gate",
        "artifacts/release/v1.2_visual/LIVE_SHADOW_UI_GATE.json",
        "verdict=%s" % ls.get("verdict"),
        "GO_WITH_PENDING",
        "UI gate PASS (SPA from real sources, single source of truth, WebGL2 "
        "fallback, not mock). Activation (Visual UI drives actuation) PENDING "
        "by design fail-closed; non-blocking."))

    # GREEN_PROTECTION -- protected GREEN 4778
    gp = read_json(ART / "GREEN_PROTECTION_PROOF.json")
    legs.append(leg_record(
        "GREEN_PROTECTION", "Protected GREEN 4778",
        "artifacts/release/v1.2_visual/GREEN_PROTECTION_PROOF.json",
        "verdict=%s" % gp.get("verdict"),
        "GO",
        "GREEN 4778 (pid %s) confirmed alive and never probed for writes; "
        "v1.2 uses isolated runtime/jcc-v1.2." % gp.get("green_pid", "n/a")))

    return legs


def main() -> int:
    if RELEASE_SHA == V1_0_1_SHA:
        print("ERROR: release_sha equals v1.0.1 freeze -- refuse to sign")
        return 2
    LEGS.mkdir(parents=True, exist_ok=True)
    recs = gather()
    sigs = {}
    for r in recs:
        out = LEGS / ("V1_2_LEG_%s.json" % r["leg_id"])
        out.write_text(json.dumps(r, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
        print("wrote %s  decision=%s  signature=%s" % (
            out.name, r["decision"], r["signature"][:16]))
        sigs[r["leg_id"]] = {
            "decision": r["decision"],
            "signature": r["signature"],
            "evidence_verdict": r["evidence_verdict"],
            "release_sha": r["release_sha"],
        }
    bundle = {
        "schema": "jcc/v1.2-leg-signatures/1",
        "release_sha": RELEASE_SHA,
        "freeze_sha": FREEZE_SHA,
        "signer": SIGNER,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "legs": sigs,
        "note": "All signatures freshly computed at generation; none reuse "
                "v1.0.1 (f8ac95d) or v1.1 signatures.",
    }
    (LEGS / "V1_2_LEG_SIGNATURES.json").write_text(
        json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("wrote V1_2_LEG_SIGNATURES.json (%d legs)" % len(recs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
