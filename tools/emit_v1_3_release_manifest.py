#!/usr/bin/env python3
"""JCC v1.3 Product Edition -- release manifest generator (owner: Orion).

Emits artifacts/release/v1.3_product/V1_3_RELEASE_MANIFEST.json using the
non-self-referential engine in tools/jcc_release_manifest.py. See that module's
docstring for why the v1.2 model was retired.

The manifest certifies PRODUCT_FREEZE_SHA. `--check` is directional: it verifies
the freeze is an ancestor of the evidence head, that every product artifact's
bytes AT THE FREEZE COMMIT match, and that nothing product-shaped changed in the
freeze..evidence / freeze..tag windows. It never writes anything, so

    git checkout <final-tag> && manifest --check

is CLEAN by construction. The retired re-emit -> check -> discard ritual is not
used anywhere in this tool.

Usage:
  F:/jev/.venv/Scripts/python.exe tools/emit_v1_3_release_manifest.py --emit \
      [--freeze <sha>] [--tag <sha-or-ref>]
  F:/jev/.venv/Scripts/python.exe tools/emit_v1_3_release_manifest.py --check
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jcc_release_manifest as eng  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
ART = REPO / "artifacts" / "release" / "v1.3_product"
OUT = ART / "V1_3_RELEASE_MANIFEST.json"
SIDECAR = ART / "V1_3_RELEASE_MANIFEST.sha256"
GATE_RESULT = REPO / "artifacts" / "acceptance" / "final_gate_v1_3_result.json"
GENERATED_BY = "tools/emit_v1_3_release_manifest.py"
RELEASE = "JCC v1.3 Product Edition (local)"

# Product surface + evidence the release is re-derivable from. Paths are read at
# PRODUCT_FREEZE_SHA, so evidence listed here must exist at the freeze commit.
FILES: List[Dict[str, str]] = [
    # --- v1.3 product surface ---
    {"path": "packages/jcc_core", "owner": "core-platform", "dir": "true"},
    {"path": "apps/command_center_3d", "owner": "core-platform", "dir": "true"},
    {"path": "config", "owner": "core-platform", "dir": "true"},
    {"path": "tools/jcc_release_manifest.py", "owner": "lead"},
    {"path": "tools/emit_v1_3_release_manifest.py", "owner": "lead"},
    # --- v1.2 baseline carried forward ---
    {"path": "docs/v1_2/FINAL_PRODUCT_FREEZE.md", "owner": "protocol-architect"},
]


def gate_block() -> Dict[str, Any]:
    if not GATE_RESULT.is_file():
        return {"present": False, "note": "v1.3 final gate has not run yet"}
    try:
        rec = json.loads(GATE_RESULT.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"present": False, "error": str(exc)}
    return {"present": True,
            "project_complete": rec.get("project_complete"),
            "final_release": rec.get("final_release"),
            "release_readiness": rec.get("release_readiness"),
            "blocker": rec.get("blocker"), "blockers": rec.get("blockers", []),
            "pending_host_activation": rec.get("pending_host_activation", [])}


def build(freeze: Optional[str], tag: Optional[str]) -> Dict[str, Any]:
    if not freeze:
        raise SystemExit("--freeze <sha> is required to emit a v1.3 manifest")
    doc = eng.build_manifest(
        FILES,
        product_freeze_sha=freeze,
        evidence_head="HEAD",
        tag_head=tag,
        release=RELEASE,
        generated_by=GENERATED_BY,
        gate_block=gate_block(),
    )
    return doc


def check() -> int:
    if not OUT.is_file():
        print("CHECK: no v1.3 manifest at %s" % OUT)
        return 1
    doc = json.loads(OUT.read_text(encoding="utf-8"))
    problems, notes = eng.check_manifest(doc, require_tag=False)
    if SIDECAR.is_file():
        want = SIDECAR.read_text(encoding="utf-8").split()[0]
        if want != eng.sha256_bytes(OUT.read_bytes()):
            problems.append("sidecar digest mismatch")
    else:
        problems.append("no sidecar -- manifest cannot be integrity-checked")
    for n in notes:
        print("note: %s" % n)
    if problems:
        print("CHECK FAILED (%d):" % len(problems))
        for p in problems:
            print("  - %s" % p)
        return 1
    print("CHECK CLEAN: %d files, product_freeze_sha=%s, evidence_head=%s, head=%s"
          % (doc.get("file_count"), str(doc.get("product_freeze_sha"))[:12],
             str(doc.get("evidence_head"))[:12],
             subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(REPO),
                                     text=True).strip()[:12]))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="JCC v1.3 release manifest")
    ap.add_argument("--emit", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--freeze", default=None,
                    help="PRODUCT_FREEZE_SHA (required for --emit)")
    ap.add_argument("--tag", default=None, help="TAG_HEAD ref/sha (optional)")
    args = ap.parse_args(argv)
    if args.check:
        return check()
    if args.emit:
        doc = build(args.freeze, args.tag)
        return eng.emit(doc, OUT, SIDECAR)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
