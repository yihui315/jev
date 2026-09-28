#!/usr/bin/env python3
"""JCC v1.2 Visual Command Center -- release manifest generator (owner: Orion).

Emits artifacts/release/v1.2_visual/V1_2_RELEASE_MANIFEST.json: the evidence a
v1.2 release can be re-derived from a commit by someone who was not in the room.

It is the v1.2 sibling of tools/emit_final_manifest.py (the FINAL/v1.0.1-ops
manifest, which is documented as LEGACY_RELEASE_PROFILE and is out of scope for
v1.2). Same guarantees: every hash is sha256 of the committed blob (never git's
SHA-1), --check re-derives everything and compares, and a 40-hex value in a
sha256 field is refused outright.

Usage:
  F:/jev/.venv/Scripts/python.exe tools/emit_v1_2_release_manifest.py --emit
  F:/jev/.venv/Scripts/python.exe tools/emit_v1_2_release_manifest.py --check
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
ART = REPO / "artifacts" / "release" / "v1.2_visual"
OUT = ART / "V1_2_RELEASE_MANIFEST.json"
SIDECAR = ART / "V1_2_RELEASE_MANIFEST.sha256"
GATE_RESULT = REPO / "artifacts" / "acceptance" / "final_gate_v1_2_result.json"
# The v1.2 product freeze (rc provenance). Re-anchored to the true Evidence HEAD
# at the end of the release run (point 14) by re-emitting.
V1_2_FREEZE = "ddca208"
GENERATED_BY = "tools/emit_v1_2_release_manifest.py"

FILES: List[Dict[str, str]] = [
    # --- v1.2 product surface (core-platform) ---
    {"path": "config/jcc-v1.2.yaml", "owner": "core-platform"},
    {"path": "apps/command_center_3d", "owner": "core-platform", "dir": "true"},
    {"path": "scripts/jcc-v1.2-register-tasks.ps1", "owner": "core-platform"},
    {"path": "scripts/start-jcc-v1.2.ps1", "owner": "core-platform"},
    {"path": "scripts/stop-jcc-v1.2.ps1", "owner": "core-platform"},
    {"path": "scripts/status-jcc-v1.2.ps1", "owner": "core-platform"},
    {"path": "scripts/start-jcc-v1.2-visual-ui.ps1", "owner": "core-platform"},
    {"path": "scripts/stop-jcc-v1.2-visual-ui.ps1", "owner": "core-platform"},
    {"path": "scripts/restart-jcc-v1.2.ps1", "owner": "core-platform"},
    {"path": "scripts/test-visual-ui.ps1", "owner": "core-platform"},
    {"path": "packages/jcc_core", "owner": "core-platform", "dir": "true"},
    # --- v1.2 release docs (protocol-architect) ---
    {"path": "docs/v1_2/FINAL_PRODUCT_FREEZE.md", "owner": "protocol-architect"},
    # --- v1.2 evidence (release-evidence) ---
    {"path": "artifacts/release/V1_1_SECURITY_GATE.json", "owner": "release-evidence"},
    {"path": "artifacts/release/v1.2_visual/GREEN_PROTECTION_PROOF.json", "owner": "release-evidence"},
    {"path": "artifacts/release/v1.2_visual/DURABLE_RUNTIME_PROOF.json", "owner": "release-evidence"},
    {"path": "artifacts/release/v1.2_visual/FOUR_AGENT_EVIDENCE.json", "owner": "release-evidence"},
    {"path": "artifacts/release/v1.2_visual/FIRST_NATIVE_LIVE_3D_SESSION.json", "owner": "release-evidence"},
    {"path": "artifacts/release/v1.2_visual/V1_2_VISUAL_PERFORMANCE.json", "owner": "release-evidence"},
    # --- v1.2 release governance (lead) ---
    {"path": "tools/v1_2_collect_evidence.py", "owner": "lead"},
    {"path": "tools/emit_v1_2_release_manifest.py", "owner": "lead"},
    {"path": "tools/final_gate_run_v1_2.py", "owner": "lead"},
]

OWNER_PREFIX: Dict[str, List[str]] = {
    "lead": [""],
    "release-evidence": ["artifacts/release/"],
    "core-platform": ["packages/", "apps/", "facades/", "scripts/", "fixtures/", "config/"],
    "protocol-architect": ["docs/"],
}


def _owns(owner: str, rel: str) -> bool:
    pres = OWNER_PREFIX.get(owner)
    if pres is None:
        return False
    if pres == [""]:
        return True
    for pre in pres:
        base = pre.rstrip("/")
        if rel == base or rel.startswith(base + "/"):
            return True
    return False


def _assert_owner_map() -> None:
    owners = [o for o in OWNER_PREFIX if o != "lead"]
    for i, a in enumerate(owners):
        for b in owners[i + 1:]:
            if set(OWNER_PREFIX[a]) & set(OWNER_PREFIX[b]):
                raise SystemExit("OWNER_PREFIX ambiguous: %s/%s" % (a, b))


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=str(REPO),
                                    text=True, errors="replace").strip()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_blob(commit: str, relpath: str) -> str:
    blob = subprocess.check_output(["git", "cat-file", "blob", f"{commit}:{relpath}"],
                                    cwd=str(REPO))
    return hashlib.sha256(blob).hexdigest()


def reject_bad_digest(path: str, digest: str) -> None:
    if re.fullmatch(r"[0-9a-f]{40}", digest or ""):
        raise SystemExit("REFUSING git SHA-1 in sha256 field for %s" % path)


def _object_type(commit: str, rel: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "cat-file", "-t", f"{commit}:{rel}"], cwd=str(REPO),
            stderr=subprocess.DEVNULL).decode("utf-8", "replace").strip()
    except subprocess.CalledProcessError:
        return ""


def _tracked(commit: str, rel: str) -> bool:
    return _object_type(commit, rel) != ""


def _dir_fingerprint(p: Path) -> bytes:
    h = hashlib.sha256()
    for f in sorted(x for x in p.rglob("*") if x.is_file()):
        h.update(f.relative_to(p).as_posix().encode())
        h.update(b"\0")
        h.update(hashlib.sha256(f.read_bytes()).hexdigest().encode())
        h.update(b"\0")
    return h.hexdigest().encode()


def _entry(rel: str, digest: str, size: int, conv: str, owner: str) -> Dict[str, Any]:
    reject_bad_digest(rel, digest)
    return {"path": rel, "sha256": digest, "bytes": size,
            "hash_convention": conv, "owner": owner}


def collect(commit: str) -> Dict[str, Any]:
    entries, problems = [], []
    for spec in FILES:
        rel = spec["path"]
        p = REPO / rel
        owner = spec["owner"]
        if not _owns(owner, rel):
            problems.append("%s: owner %s does not own that path" % (rel, owner))
        if spec.get("dir"):
            if not p.is_dir():
                problems.append("%s: declared directory does not exist" % rel)
                continue
            kind = _object_type(commit, rel)
            if kind == "blob":
                problems.append("%s: dir path is a blob in %s" % (rel, commit))
                continue
            size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
            entries.append(_entry(rel, sha256_bytes(_dir_fingerprint(p)), size,
                                   "dir_fingerprint", owner))
            continue
        if not p.is_file():
            problems.append("%s: declared file does not exist" % rel)
            continue
        if _tracked(commit, rel):
            entries.append(_entry(rel, sha256_blob(commit, rel), p.stat().st_size,
                                  "git_blob", owner))
        else:
            entries.append(_entry(rel, sha256_bytes(p.read_bytes()), p.stat().st_size,
                                  "raw_file", owner))
    if problems:
        raise SystemExit("manifest inputs incomplete; refusing to emit a manifest "
                         "that looks complete:\n  " + "\n  ".join(problems))
    return {"entries": entries, "problems": problems}


def gate_block() -> Dict[str, Any]:
    if not GATE_RESULT.is_file():
        return {"present": False, "note": "v1.2 final gate has not run yet"}
    try:
        rec = json.loads(GATE_RESULT.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"present": False, "error": str(exc)}
    return {"present": True, "project_complete": rec.get("project_complete"),
            "final_release": rec.get("final_release"),
            "release_readiness": rec.get("release_readiness"),
            "blocker": rec.get("blocker"), "blockers": rec.get("blockers", []),
            "pending_host_activation": rec.get("pending_host_activation", [])}


def build(now: Optional[datetime] = None) -> Dict[str, Any]:
    commit = git("rev-parse", "HEAD")
    rec = collect(commit)
    doc = {
        "schema": "jcc/v1.2-release-manifest/1",
        "release_sha": commit,
        "product_freeze_sha": git("rev-parse", V1_2_FREEZE),
        "product_freeze_frozen_at": V1_2_FREEZE,
        "test_sha": commit,
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(),
        "generated_by": GENERATED_BY,
        "generated_by_sha256": sha256_bytes(Path(__file__).read_bytes()),
        "hash_convention": "sha256(git cat-file blob <commit>:<path>) for tracked "
                           "files; sha256(raw bytes) otherwise; "
                           "sha256(content fingerprint) for directories",
        "file_count": len(rec["entries"]),
        "files": rec["entries"],
        "final_gate": gate_block(),
    }
    return doc


def serialise(doc: Dict[str, Any]) -> bytes:
    a = json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False).encode()
    b = json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False).encode()
    if a != b:
        raise SystemExit("non-deterministic serialisation; refusing to emit")
    return a


def check() -> int:
    problems: List[str] = []
    if not OUT.is_file():
        print("CHECK: no v1.2 manifest at %s" % OUT)
        return 1
    doc = json.loads(OUT.read_text(encoding="utf-8"))
    for e in doc.get("files", []):
        rel, want = e["path"], e.get("sha256", "")
        reject_bad_digest(rel, want)
        p = REPO / rel
        if not p.exists():
            problems.append("%s: listed but does not exist" % rel)
            continue
        if e.get("hash_convention") == "dir_fingerprint":
            got = sha256_bytes(_dir_fingerprint(p)) if p.is_dir() else ""
        elif e.get("hash_convention") == "git_blob":
            if not _tracked(doc.get("release_sha", "HEAD"), rel):
                problems.append("%s: claims git_blob but not in commit" % rel)
                continue
            got = sha256_blob(doc["release_sha"], rel)
        else:
            got = hashlib.sha256(p.read_bytes()).hexdigest()
        if got != want:
            problems.append("%s: hash mismatch" % rel)
    for e in doc.get("files", []):
        if not _owns(e.get("owner", ""), e["path"]):
            problems.append("%s: owner %s mismatch" % (e["path"], e["owner"]))
    if not doc.get("generated_at"):
        problems.append("manifest carries no generated_at")
    if SIDECAR.is_file():
        want = SIDECAR.read_text(encoding="utf-8").split()[0]
        if want != sha256_bytes(OUT.read_bytes()):
            problems.append("sidecar digest mismatch")
    else:
        problems.append("no sidecar -- manifest cannot be integrity-checked")
    fresh = build()
    fresh.pop("generated_at", None)
    stored = {k: v for k, v in doc.items() if k != "generated_at"}
    if fresh != stored:
        diffs = sorted(
            "%s" % k for k in set(fresh) | set(stored)
            if fresh.get(k) != stored.get(k))[:5]
        problems.append("two generations disagree on: " + "; ".join(diffs))
    if problems:
        print("CHECK FAILED (%d):" % len(problems))
        for p_ in problems:
            print("  - %s" % p_)
        return 1
    print("CHECK CLEAN: %d files, release_sha=%s, gate=%s" % (
        doc.get("file_count"), str(doc.get("release_sha"))[:12],
        doc.get("final_gate", {}).get("release_readiness")))
    return 0


def emit() -> int:
    stamp = datetime.now(timezone.utc)
    doc = build(stamp)
    payload = serialise(doc)
    digest = sha256_bytes(payload)
    again = build(stamp)
    if sha256_bytes(serialise(again)) != digest:
        raise SystemExit("manifest not reproducible")
    ART.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(payload)
    SIDECAR.write_text(digest + "  V1_2_RELEASE_MANIFEST.json\n", encoding="utf-8")
    print("wrote %s (%d bytes, sha256 %s)" % (OUT.name, len(payload), digest))
    print("files: %d" % doc["file_count"])
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="v1.2 release manifest")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--emit", action="store_true")
    args = ap.parse_args(argv)
    _assert_owner_map()
    if args.check:
        return check()
    if args.emit:
        return emit()
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
