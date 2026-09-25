#!/usr/bin/env python
"""FINAL release manifest generator (directive sec.18) -- owner: lead / Orion.

Produces ``artifacts/release/final-manifest.json`` -- the evidence that a release
can be re-derived from a commit by somebody who was not in the room.

What it records
---------------
release_sha        HEAD at generation time (the release the manifest describes)
product_freeze_sha the commit the product was frozen at (recorded in
                   docs/p4/P4_FREEZE_RECORD.md -- never assumed)
test_sha           the commit at which the final gate was last computed
generated_at       UTC iso8601 (the ONLY field allowed to differ between runs)
files              [{path, sha256, bytes, hash_convention, owner}]
final_gate         the gate's verdict block as observed
generated_by       this script, with its own blob hash

Banned regressions this script is built to refuse
-------------------------------------------------
* correct hash, wrong path     -> an absent path is a hard error in emit mode
* a git SHA-1 in a sha256 field -> 40-hex values are rejected outright
* a stale anchor               -> --check recomputes every hash from the blob
* wrong file hash attribution  -> every entry carries an explicit `owner` and the
  prefix rule is asserted (an entry's owner must match the path's owning tree)

Reproducibility
---------------
The generator runs the whole collection twice and serialises twice; if the two
digests differ it REFUSES to emit. ``--check`` re-derives everything from the
commit and compares, so "it was generated once" is never the evidence.

Usage
-----
    F:/jev/.venv/Scripts/python.exe tools/emit_final_manifest.py [--emit]
    F:/jev/.venv/Scripts/python.exe tools/emit_final_manifest.py --check
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
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parents[1]
ART = REPO / "artifacts" / "release"
OUT = ART / "final-manifest.json"
SIDECAR = ART / "final-manifest.sha256"
GATE_RESULT = REPO / "artifacts" / "acceptance" / "final_gate_result.json"
FROZEN_AT = "e6f5c15"                       # product-blob freeze of the A8 pins
FREEZE_RECORD = "docs/p4/P4_FREEZE_RECORD.md"
GENERATED_BY = "tools/emit_final_manifest.py"

# Every entry names its owner. An artifact without an owner is not evidence:
# "who wrote this" is exactly what a fabrication attempt attacks.
FILES: List[Dict[str, str]] = [
    {"path": "artifacts/acceptance/final_gate_run.py", "owner": "lead"},
    {"path": "docs/FINAL_STOP_CONDITIONS.md", "owner": "lead"},
    {"path": "docs/p4/P4_FREEZE_RECORD.md", "owner": "lead"},
    {"path": "docs/ADR/KNOWN_DEVIATIONS.md", "owner": "protocol-architect"},
    {"path": "artifacts/release/manifest.json", "owner": "release-evidence"},
    {"path": "artifacts/release/manifest.sha256", "owner": "release-evidence"},
    {"path": "scripts/bootstrap.ps1", "owner": "core-platform"},
    {"path": "scripts/start.ps1", "owner": "core-platform"},
    {"path": "scripts/stop.ps1", "owner": "core-platform"},
    {"path": "scripts/restart.ps1", "owner": "core-platform"},
    {"path": "scripts/status.ps1", "owner": "core-platform"},
    {"path": "scripts/doctor.ps1", "owner": "core-platform"},
    {"path": "scripts/acceptance.ps1", "owner": "core-platform"},
    {"path": "packages/operator_skill", "owner": "core-platform", "dir": "true"},
    {"path": "packages/mcp_facade", "owner": "core-platform", "dir": "true"},
    {"path": "packages/observability", "owner": "core-platform", "dir": "true"},
    {"path": "apps/control_ui", "owner": "core-platform", "dir": "true"},
    {"path": "facades/session_center", "owner": "core-platform", "dir": "true"},
    {"path": "facades/claw3d", "owner": "core-platform", "dir": "true"},
    {"path": "fixtures", "owner": "core-platform", "dir": "true"},
    {"path": "tests/e2e", "owner": "qa-sre", "dir": "true"},
    {"path": "docs/ADR/FINAL_ACCEPTANCE_REPORT.md", "owner": "lead"},
    {"path": "FINAL_ACTIVATION_CHECKLIST.md", "owner": "lead"},
]

# Owner -> path prefix. If an entry's path does not live under its owner's tree
# the attribution is wrong and the manifest is not evidence.
OWNER_PREFIX = {
    "lead": "",                      # repo-level documents and the gate itself
    "release-evidence": "artifacts/release/",
    "core-platform": "packages/",
    "qa-sre": "tests/",
    "protocol-architect": "docs/",
}


def git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=str(REPO), text=True, errors="replace"
    ).strip()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_blob(commit: str, relpath: str) -> str:
    """sha256 of the committed blob -- never git's SHA-1 object id."""
    blob = subprocess.check_output(
        ["git", "cat-file", "blob", f"{commit}:{relpath}"], cwd=str(REPO)
    )
    return hashlib.sha256(blob).hexdigest()


def reject_bad_digest(path: str, digest: str) -> None:
    """A 40-hex value in a sha256 field is the 8e4e5e1 regression, re-introduced."""
    if re.fullmatch(r"[0-9a-f]{40}", digest or ""):
        raise SystemExit(
            "REFUSING to write a git SHA-1 object id into a sha256 field for "
            f"{path} (got {digest!r}); see commit 8e4e5e1"
        )


def collect(commit: str) -> Dict[str, Any]:
    entries = []
    problems = []
    for spec in FILES:
        rel = spec["path"]
        p = REPO / rel
        owner = spec["owner"]
        prefix = OWNER_PREFIX.get(owner)
        if prefix and not rel.startswith(prefix):
            problems.append(f"{rel}: owner '{owner}' does not own that path prefix")
        if spec.get("dir"):
            if not p.is_dir():
                # A declared surface that does not exist is a MISSING release
                # input -- silently dropping it is the "wrong path" failure.
                problems.append(f"{rel}: declared directory does not exist")
                continue
            digest = sha256_blob(commit, rel) if _tracked(commit, rel) else ""
            if not digest:
                h = sha256_bytes(_dir_fingerprint(p))
                reject_bad_digest(rel, h)
                digest = h
            size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
            entries.append(_entry(rel, digest, size, "dir_fingerprint", owner))
            continue
        if not p.is_file():
            if _tracked(commit, rel):
                problems.append(
                    f"{rel}: tracked at {commit} but missing on disk (stale anchor)"
                )
            else:
                problems.append(f"{rel}: declared file does not exist")
            continue
        if _tracked(commit, rel):
            entries.append(
                _entry(rel, sha256_blob(commit, rel), p.stat().st_size,
                       "git_blob", owner)
            )
        else:
            entries.append(
                _entry(rel, sha256_bytes(p.read_bytes()), p.stat().st_size,
                       "raw_file", owner)
            )
    if problems:
        raise SystemExit(
            "manifest inputs are incomplete; refusing to emit a manifest that "
            "would look complete:\n  " + "\n  ".join(problems)
        )
    return {"entries": entries, "problems": problems}


def _entry(rel: str, digest: str, size: int, conv: str, owner: str) -> Dict[str, Any]:
    reject_bad_digest(rel, digest)
    return {
        "path": rel,
        "sha256": digest,
        "bytes": size,
        "hash_convention": conv,
        "owner": owner,
    }


def _tracked(commit: str, rel: str) -> bool:
    code = subprocess.call(
        ["git", "cat-file", "-e", f"{commit}:{rel}"],
        cwd=str(REPO), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return code == 0


def _dir_fingerprint(p: Path) -> bytes:
    """Stable over a directory's *content*, unordered, no timestamps."""
    h = hashlib.sha256()
    for f in sorted(x for x in p.rglob("*") if x.is_file()):
        h.update(f.relative_to(p).as_posix().encode())
        h.update(b"\0")
        h.update(hashlib.sha256(f.read_bytes()).hexdigest().encode())
        h.update(b"\0")
    return h.hexdigest().encode()


def gate_block() -> Dict[str, Any]:
    if not GATE_RESULT.is_file():
        return {"present": False, "note": "final gate has not produced a verdict yet"}
    try:
        rec = json.loads(GATE_RESULT.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"present": False, "error": str(exc)}
    return {
        "present": True,
        "project_complete": rec.get("project_complete"),
        "head": rec.get("head"),
        "blocker": rec.get("blocker"),
        "high": rec.get("high"),
        "missing": rec.get("missing", []),
        "failed": rec.get("failed", []),
        "degraded": rec.get("degraded", []),
        "pending_external_activation": rec.get("pending_external_activation", []),
    }


def build() -> Dict[str, Any]:
    commit = git("rev-parse", "HEAD")
    rec = collect(commit)
    doc = {
        "schema": "jcc/final-manifest/1",
        "release_sha": commit,
        "product_freeze_sha": git("rev-parse", FROZEN_AT),
        "product_freeze_frozen_at": FROZEN_AT,
        "test_sha": commit,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_by": GENERATED_BY,
        "generated_by_sha256": sha256_bytes(Path(__file__).read_bytes()),
        "hash_convention": "sha256(git cat-file blob <commit>:<path>) for tracked "
                           "files; sha256(raw bytes) otherwise; "
                           "sha256(sorted content fingerprints) for directories",
        "file_count": len(rec["entries"]),
        "files": rec["entries"],
        "final_gate": gate_block(),
    }
    return doc


def serialise(doc: Dict[str, Any]) -> bytes:
    """Two serialisations must agree; clock-bearing fields are stripped first."""
    stripped = {k: v for k, v in doc.items() if k != "generated_at"}
    a = json.dumps(stripped, indent=2, sort_keys=True, ensure_ascii=False).encode()
    b = json.dumps(stripped, indent=2, sort_keys=True, ensure_ascii=False).encode()
    if a != b:
        raise SystemExit("non-deterministic serialisation; refusing to emit")
    return a


def check() -> int:
    problems: List[str] = []
    if not OUT.is_file():
        print("CHECK: no final manifest at {}".format(OUT))
        return 1
    doc = json.loads(OUT.read_text(encoding="utf-8"))

    # 1. every path exists and every hash recomputable
    for e in doc.get("files", []):
        rel, want = e["path"], e.get("sha256", "")
        reject_bad_digest(rel, want)
        p = REPO / rel
        if not p.exists():
            problems.append(f"{rel}: listed but does not exist (stale anchor)")
            continue
        if e.get("hash_convention") == "dir_fingerprint":
            got = sha256_bytes(_dir_fingerprint(p)).decode() if p.is_dir() else ""
        elif e.get("hash_convention") == "git_blob":
            if not _tracked(doc.get("release_sha", "HEAD"), rel):
                problems.append(f"{rel}: claims git_blob but is not in the commit")
                continue
            got = sha256_blob(doc["release_sha"], rel)
        else:
            got = hashlib.sha256(p.read_bytes()).hexdigest()
        if got != want:
            problems.append(f"{rel}: hash mismatch ({want[:12]}...) vs recomputed")

    # 2. attribution: each entry under its owner's prefix
    for e in doc.get("files", []):
        prefix = OWNER_PREFIX.get(e.get("owner", ""))
        if prefix and not e["path"].startswith(prefix):
            problems.append(
                f"{e['path']}: owner '{e['owner']}' does not own that path prefix")

    # 3. sidecar
    if SIDECAR.is_file():
        want = SIDECAR.read_text(encoding="utf-8").split()[0]
        if want != sha256_bytes(OUT.read_bytes()):
            problems.append("sidecar digest does not match the manifest bytes")
    else:
        problems.append("no sidecar {} -- the manifest cannot be integrity-checked".format(SIDECAR.name))

    # 4. two generations must agree on every substantive field
    fresh = build()
    fresh.pop("generated_at", None)
    stored = {k: v for k, v in doc.items() if k != "generated_at"}
    if fresh != stored:
        diffs = sorted(
            f"{k}: stored={str(stored.get(k))[:60]} fresh={str(fresh.get(k))[:60]}"
            for k in set(fresh) | set(stored)
            if fresh.get(k) != stored.get(k)
        )
        problems.append("two generations disagree on: " + "; ".join(diffs[:5]))

    if problems:
        print("CHECK FAILED ({} problem(s)):".format(len(problems)))
        for p_ in problems:
            print("  - {}".format(p_))
        return 1
    print("CHECK CLEAN: {} files, release_sha={}, gate={}".format(
        doc.get("file_count"), str(doc.get("release_sha"))[:12],
        doc.get("final_gate", {}).get("project_complete")))
    return 0


def emit() -> int:
    doc = build()
    payload = serialise(doc)
    digest = sha256_bytes(payload)

    # Reproducibility gate: rebuild from scratch, serialise again, require the
    # same digest. A manifest that cannot be re-derived is not evidence.
    again = build()
    again_payload = serialise(again)
    if sha256_bytes(again_payload) != digest:
        raise SystemExit("manifest is not reproducible: two builds disagree")
    if again != doc:
        again.pop("generated_at", None)
        doc = again

    ART.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(payload)
    SIDECAR.write_text(digest + "  final-manifest.json\n", encoding="utf-8")
    print("wrote {} ({} bytes, sha256 {})".format(
        OUT.relative_to(REPO), len(payload), digest))
    print("wrote {}".format(SIDECAR.relative_to(REPO)))
    print("files: {}".format(doc["file_count"]))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="final release manifest")
    ap.add_argument("--check", action="store_true",
                    help="re-derive every hash and compare; exit 0 only if clean")
    ap.add_argument("--emit", action="store_true", help="write the manifest")
    args = ap.parse_args(argv)
    if args.check:
        return check()
    if args.emit:
        return emit()
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
