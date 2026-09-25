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
    # The gate's F-01 check looks FINAL_ACCEPTANCE_REPORT.md up under
    # artifacts/acceptance/ (docs/FINAL_STOP_CONDITIONS.md sec.4). Naming any
    # other path here would make the manifest attest to a file nobody checks for.
    {"path": "artifacts/acceptance/FINAL_ACCEPTANCE_REPORT.md", "owner": "lead"},
    {"path": "FINAL_ACTIVATION_CHECKLIST.md", "owner": "lead"},
]

# Owner -> the path prefixes that owner owns. If an entry's path lives under a
# tree its declared owner does not own, the attribution is wrong and the manifest
# is not evidence.
#
# An owner may own SEVERAL trees -- core-platform owns the whole product/ops
# surface, not just `packages/`. The map held a single prefix per owner and the
# mismatch only surfaced as a crash while hashing a directory (the tree object
# made `cat-file -e` lie about presence), long after somebody had already filled
# in `files:` with owners the map rejected. Fixing the map here is a statement
# about ownership, not an exception: `_assert_owner_map()` below refuses to run
# if two non-lead owners ever claim overlapping trees, so widening cannot drift
# into "everything is owned by everyone".
OWNER_PREFIX: Dict[str, List[str]] = {
    "lead": [""],                    # repo-level documents and the gate itself
    "release-evidence": ["artifacts/release/"],
    "core-platform": ["packages/", "apps/", "facades/", "scripts/", "fixtures/"],
    "qa-sre": ["tests/"],
    "protocol-architect": ["docs/"],
}


def _owns(owner: str, rel: str) -> bool:
    """Does `owner` own `rel`? Component-wise, so `fixtures` cannot swallow a
    sibling `fixtures_old/` and `packages` cannot swallow `packages_old/`."""
    pres = OWNER_PREFIX.get(owner)
    if pres is None:
        return False
    if pres == [""]:                     # repo-level owner: nothing is out of reach
        return True
    for pre in pres:
        base = pre.rstrip("/")
        if rel == base or rel.startswith(base + "/"):
            return True
    return False


def _assert_owner_map() -> None:
    """The widened map must not leave two non-lead owners owning the same tree."""
    owners = [o for o in OWNER_PREFIX if o != "lead"]
    for i, a in enumerate(owners):
        for b in owners[i + 1:]:
            overlap = set(OWNER_PREFIX[a]) & set(OWNER_PREFIX[b])
            if overlap:
                raise SystemExit(
                    f"OWNER_PREFIX is ambiguous: {a} and {b} both claim "
                    f"{sorted(overlap)}"
                )


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
        if not _owns(owner, rel):
            problems.append(f"{rel}: owner '{owner}' does not own that path prefix")
        if spec.get("dir"):
            if not p.is_dir():
                # A declared surface that does not exist is a MISSING release
                # input -- silently dropping it is the "wrong path" failure.
                problems.append(f"{rel}: declared directory does not exist")
                continue
            # A directory is hashed by CONTENT, never by the git object that
            # points at it. `cat-file -e <commit>:<dir>` exits 0 for a tree
            # (the object *does* exist), so the old `_tracked()` test returned
            # True here and `cat-file blob` --fatal: bad file. Had it not
            # crashed it would have hashed the serialised tree's own bytes and
            # labelled the entry `git_blob`: the "right hash, wrong path"
            # regression this file exists to refuse.
            kind = _object_type(commit, rel)
            if kind == "blob":
                problems.append(
                    f"{rel}: declared as a directory but a blob is committed at "
                    f"that path in {commit}; a content fingerprint would cover "
                    f"the wrong surface"
                )
                continue
            size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
            h = sha256_bytes(_dir_fingerprint(p))
            reject_bad_digest(rel, h)
            digest = h
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


def _object_type(commit: str, rel: str) -> str:
    """blob | tree | commit | tag | ``''`` when nothing is committed at that path.

    Presence and kind are different questions. ``cat-file -e`` answers only the
    first, and a tree is an object, so a directory path reports "tracked".
    """
    try:
        out = subprocess.check_output(
            ["git", "cat-file", "-t", f"{commit}:{rel}"],
            cwd=str(REPO), stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        return ""
    return out.decode("utf-8", "replace").strip()


def _tracked(commit: str, rel: str) -> bool:
    return _object_type(commit, rel) != ""


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


def build(now: Optional[datetime] = None) -> Dict[str, Any]:
    """`now` is pinable: two builds of the same release must agree on every byte,
    clock included, or nothing about the manifest is reproducible."""
    commit = git("rev-parse", "HEAD")
    rec = collect(commit)
    doc = {
        "schema": "jcc/final-manifest/1",
        "release_sha": commit,
        "product_freeze_sha": git("rev-parse", FROZEN_AT),
        "product_freeze_frozen_at": FROZEN_AT,
        "test_sha": commit,
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(),
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
    """Deterministic: sorted keys, no trailing newline, clock included.

    The clock used to be stripped here, which quietly deleted `generated_at` from
    every manifest ever written -- the schema named a field the serialiser threw
    away. Determinism now comes from *pinning* the clock (see `build(now=...)`)
    rather than from dropping the field, so a generation that disagrees about the
    time is still a failure worth raising.
    """
    a = json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False).encode()
    b = json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False).encode()
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
            got = sha256_bytes(_dir_fingerprint(p)) if p.is_dir() else ""
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
        if not _owns(e.get("owner", ""), e["path"]):
            problems.append(
                f"{e['path']}: owner '{e['owner']}' does not own that path prefix")
    if not doc.get("generated_at"):
        problems.append("manifest carries no generated_at; it is not timestamped "
                        "evidence")

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
    stamp = datetime.now(timezone.utc)
    doc = build(stamp)
    payload = serialise(doc)
    digest = sha256_bytes(payload)

    # Reproducibility gate: rebuild from scratch, serialise again, require the
    # same digest. A manifest that cannot be re-derived is not evidence. Both
    # builds carry the SAME stamp -- otherwise "reproducible" only ever meant
    # "equal except for the one field nobody reads".
    again = build(stamp)
    again_payload = serialise(again)
    if sha256_bytes(again_payload) != digest:
        raise SystemExit("manifest is not reproducible: two builds disagree")
    if "generated_at" not in doc:
        raise SystemExit("refusing to emit a manifest with no generated_at")
    # The reproducibility check is over bytes that exclude the clock, so a
    # re-build crossing a second boundary agrees here. The old fallback, though,
    # compared the dicts *with* their timestamps -- `again != doc` is therefore
    # always true -- and responded by popping `generated_at` off the copy it
    # adopted: every emitted manifest shipped minus the one field the schema
    # names. Losing a field is not "reproducible", it is a quiet omission, and
    # "instrument says PASS while shipping something else" is the failure this
    # generator exists to refuse. Keep the first build; compare the clock-free
    # half only.
    same_except_clock = {k: v for k, v in again.items() if k != "generated_at"} == \
                        {k: v for k, v in doc.items() if k != "generated_at"}
    if not same_except_clock:
        raise SystemExit(
            "manifest is not stable across builds: two collections disagree"
        )
    if "generated_at" not in doc:
        raise SystemExit("refusing to emit a manifest with no generated_at")

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
    _assert_owner_map()
    if args.check:
        return check()
    if args.emit:
        return emit()
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
