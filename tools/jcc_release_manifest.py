#!/usr/bin/env python3
"""JCC release manifest engine -- non-self-referential provenance model (v1.3 Wave 0).

WHY THIS EXISTS (the defect it retires)
----------------------------------------
The v1.2 manifest (tools/emit_v1_2_release_manifest.py) anchored itself to
`release_sha = git rev-parse HEAD` and its --check compared a *fresh* build
(re-derived from the CURRENT HEAD) against the stored document. That is a
SELF-REFERENTIAL invariant: the moment a single commit lands after the manifest
is written -- even a commit that only adds evidence -- HEAD moves, `release_sha`
changes, and `--check` can never be CLEAN again. The only way to obtain a CLEAN
verdict on a tag was the ritual

    checkout tag -> --emit (do NOT commit) -> --check CLEAN -> discard -> checkout -

which proves nothing about the tagged tree: it proves that a manifest the
checker just wrote matches the state the checker just read. The v1.3 order
(retire the re-emit -> check -> discard hand-wave) is exactly this fix.

THE MODEL (three anchors, one direction of travel)
--------------------------------------------------
  PRODUCT_FREEZE_SHA  the commit at which PRODUCT code was last allowed to
                      change. Immutable. The manifest certifies THIS commit's
                      product bytes. Product changes AFTER it are a hard FAIL.
  EVIDENCE_HEAD       the commit that carries the latest evidence / release
                      metadata. It is >= PRODUCT_FREEZE_SHA along history and
                      may advance freely -- adding evidence never invalidates a
                      frozen product.
  TAG_HEAD            the commit the release tag points at. Recorded for audit;
                      must be a descendant of PRODUCT_FREEZE_SHA and must not
                      introduce product changes past the freeze.

The check is therefore DIRECTIONAL, not equality-based:

  1. PRODUCT_FREEZE_SHA must be an ancestor of (or equal to) EVIDENCE_HEAD.
  2. For every product artifact in the manifest, sha256(product file @
     PRODUCT_FREEZE_SHA) must equal the recorded manifest hash. The bytes are
     read AT THE FREEZE COMMIT, so advancing HEAD does not change the verdict.
  3. Every path that changed between PRODUCT_FREEZE_SHA and EVIDENCE_HEAD must
     be inside the release-metadata whitelist. A single product-file diff in
     that range is a hard FAIL.
  4. TAG_HEAD, when present, must be a descendant of PRODUCT_FREEZE_SHA with no
     product-file change in freeze..tag beyond the freeze.

`checkout final tag -> --check` is CLEAN by construction: the tag sits on the
evidence trajectory, its product bytes are the frozen bytes, and no re-emit is
required or accepted.

HONESTY RULES (inherited, unchanged)
------------------------------------
  * Every digest is sha256 of committed bytes, never git's SHA-1. A 40-hex value
    in a sha256 field is refused outright.
  * A directory artifact is fingerprinted over its tracked file content, in a
    stable order, at the freeze commit.
  * The checker may never be the emitter: it never writes the manifest.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Release-metadata whitelist: the ONLY paths allowed to differ in the window
# PRODUCT_FREEZE_SHA..EVIDENCE_HEAD / ..TAG_HEAD. Everything else is product.
# Patterns are prefix globs relative to the repo root; "*" (no slash) matches
# within a path segment, "**" matches across segments.
# ---------------------------------------------------------------------------
RELEASE_METADATA_ALLOW: Tuple[str, ...] = (
    "artifacts/release/**",
    "artifacts/acceptance/**",
    "docs/v1_3/**",
    "docs/release/**",
    # The manifest + its sidecar live beside the other evidence.
    "artifacts/release/v1.3_product/**",
)

# Patterns that are EXPLICITLY product even if a broad rule might seem to cover
# them. Checked first; a product match wins. Fail-closed default: anything not
# in RELEASE_METADATA_ALLOW is product.
PRODUCT_DENY: Tuple[str, ...] = (
    "packages/**",
    "apps/**",
    "facades/**",
    "scripts/**",
    "config/**",
    "fixtures/**",
    "tools/**",
    "integrations/**",
    "adapters/**",
    "policies/**",
    "evals/**",
    "tests/**",
)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def git(*args: str, cwd: Optional[Path] = None) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=str(cwd or repo_root()),
        text=True, errors="replace").strip()


def git_ok(*args: str, cwd: Optional[Path] = None) -> Tuple[int, str]:
    p = subprocess.run(["git", *args], cwd=str(cwd or repo_root()),
                       text=True, errors="replace",
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return p.returncode, (p.stdout or "").strip()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def resolve_rev(rev: str) -> str:
    rc, out = git_ok("rev-parse", "--verify", rev)
    if rc != 0:
        raise SystemExit("cannot resolve revision %r: %s" % (rev, out))
    return out


def is_ancestor(older: str, newer: str) -> bool:
    rc, _ = git_ok("merge-base", "--is-ancestor", older, newer)
    return rc == 0


def reject_bad_digest(path: str, digest: str) -> None:
    if re.fullmatch(r"[0-9a-f]{40}", digest or ""):
        raise SystemExit("REFUSING git SHA-1 in sha256 field for %s" % path)
    if not re.fullmatch(r"[0-9a-f]{64}", digest or ""):
        raise SystemExit("sha256 field for %s is not 64 hex chars" % path)


# --- path classification -----------------------------------------------------

def _glob_match(pattern: str, path: str) -> bool:
    """Segment-aware glob: '*' does not cross '/', '**' does."""
    pat_segs = pattern.split("/")
    path_segs = path.split("/")

    def match(pi: int, si: int) -> bool:
        while pi < len(pat_segs):
            p = pat_segs[pi]
            if p == "**":
                if pi == len(pat_segs) - 1:
                    return True
                return any(match(pi + 1, k) for k in range(si, len(path_segs) + 1))
            if si >= len(path_segs):
                return False
            if not re.fullmatch(p.replace(".", r"\.").replace("*", "[^/]*"), path_segs[si]):
                return False
            pi += 1
            si += 1
        return si == len(path_segs)

    return match(0, 0)


def is_product_path(path: str) -> bool:
    for pat in PRODUCT_DENY:
        if _glob_match(pat, path):
            return True
    for pat in RELEASE_METADATA_ALLOW:
        if _glob_match(pat, path):
            return False
    # fail-closed: unknown paths are product
    return True


# --- content hashing at a commit --------------------------------------------

def object_type(commit: str, rel: str) -> str:
    rc, out = git_ok("cat-file", "-t", f"{commit}:{rel}")
    return out if rc == 0 else ""


def is_tracked(commit: str, rel: str) -> bool:
    return object_type(commit, rel) != ""


def ls_tree_files(commit: str, rel_dir: str) -> List[str]:
    rc, out = git_ok("ls-tree", "-r", "--name-only", commit, "--", rel_dir)
    if rc != 0:
        return []
    return [ln for ln in out.splitlines() if ln.strip()]


def blob_sha256(commit: str, rel: str) -> str:
    blob = subprocess.check_output(["git", "cat-file", "blob", f"{commit}:{rel}"],
                                   cwd=str(repo_root()))
    return sha256_bytes(blob)


def dir_fingerprint(commit: str, rel_dir: str) -> Tuple[str, int]:
    """sha256 over (relpath NUL blobsha NUL) for every tracked file under dir."""
    h = hashlib.sha256()
    total = 0
    files = ls_tree_files(commit, rel_dir)
    for rel in sorted(files):
        blob = subprocess.check_output(["git", "cat-file", "blob", f"{commit}:{rel}"],
                                       cwd=str(repo_root()))
        total += len(blob)
        h.update(rel.encode())
        h.update(b"\0")
        h.update(sha256_bytes(blob).encode())
        h.update(b"\0")
    return h.hexdigest(), total


def entry_digest(commit: str, spec: Dict[str, str]) -> Tuple[Optional[str], int, str]:
    """Return (sha256, bytes, convention) for one artifact at a commit."""
    rel = spec["path"]
    if spec.get("dir"):
        d, size = dir_fingerprint(commit, rel)
        if not ls_tree_files(commit, rel):
            return None, 0, "dir_fingerprint"
        return d, size, "dir_fingerprint"
    if is_tracked(commit, rel):
        return blob_sha256(commit, rel), _blob_size(commit, rel), "git_blob"
    return None, 0, "missing"


def _blob_size(commit: str, rel: str) -> int:
    rc, out = git_ok("cat-file", "-s", f"{commit}:{rel}")
    return int(out) if rc == 0 and out.isdigit() else 0


# --- manifest document -------------------------------------------------------

SCHEMA = "jcc/release-manifest/2"


def build_manifest(files: Sequence[Dict[str, str]],
                   product_freeze_sha: str,
                   evidence_head: str,
                   tag_head: Optional[str],
                   release: str,
                   generated_by: str,
                   gate_block: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    freeze = resolve_rev(product_freeze_sha)
    evid = resolve_rev(evidence_head)
    tag = resolve_rev(tag_head) if tag_head else None

    problems: List[str] = []
    if not is_ancestor(freeze, evid):
        problems.append("PRODUCT_FREEZE_SHA %s is NOT an ancestor of EVIDENCE_HEAD %s"
                        % (freeze[:12], evid[:12]))
    if tag is not None and not is_ancestor(freeze, tag):
        problems.append("PRODUCT_FREEZE_SHA %s is NOT an ancestor of TAG_HEAD %s"
                        % (freeze[:12], tag[:12]))
    if tag is not None and not is_ancestor(tag, evid) and tag != evid:
        # the tag may lead or trail evidence, but must be on the same line
        if not is_ancestor(evid, tag):
            problems.append("TAG_HEAD %s and EVIDENCE_HEAD %s are on divergent lines"
                            % (tag[:12], evid[:12]))

    entries: List[Dict[str, Any]] = []
    for spec in files:
        rel = spec["path"]
        digest, size, conv = entry_digest(freeze, spec)
        if digest is None:
            problems.append("%s: not present at freeze commit %s" % (rel, freeze[:12]))
            continue
        reject_bad_digest(rel, digest)
        entries.append({
            "path": rel,
            "sha256": digest,
            "bytes": size,
            "hash_convention": conv,
            "owner": spec.get("owner", ""),
        })
    if problems:
        raise SystemExit("manifest inputs incomplete; refusing to emit a manifest "
                         "that looks complete:\n  " + "\n  ".join(problems))

    doc: Dict[str, Any] = {
        "schema": SCHEMA,
        "release": release,
        "product_freeze_sha": freeze,
        "evidence_head": evid,
        "tag_head": tag,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_by": generated_by,
        "generated_by_sha256": sha256_bytes(Path(generated_by).read_bytes())
        if (repo_root() / generated_by).is_file() else None,
        "hash_convention": ("sha256(git cat-file blob <PRODUCT_FREEZE_SHA>:<path>) "
                            "for tracked files; sha256(content fingerprint over "
                            "tracked files) for directories"),
        "release_metadata_allow": list(RELEASE_METADATA_ALLOW),
        "product_deny": list(PRODUCT_DENY),
        "file_count": len(entries),
        "files": entries,
    }
    if gate_block is not None:
        doc["final_gate"] = gate_block
    return doc


def serialise(doc: Dict[str, Any]) -> bytes:
    a = json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False).encode()
    return a


def changed_paths(base: str, head: str) -> List[str]:
    rc, out = git_ok("diff", "--name-only", f"{base}..{head}")
    if rc != 0:
        return []
    return [ln for ln in out.splitlines() if ln.strip()]


def check_manifest(doc: Dict[str, Any], *, require_tag: bool = False,
                   allow_evidence_head_ahead: bool = True) -> Tuple[List[str], List[str]]:
    """Return (problems, notes). Never writes anything."""
    problems: List[str] = []
    notes: List[str] = []

    if doc.get("schema") != SCHEMA:
        problems.append("schema is %r, expected %r" % (doc.get("schema"), SCHEMA))

    freeze_raw = doc.get("product_freeze_sha")
    if not freeze_raw:
        problems.append("manifest carries no product_freeze_sha")
        return problems, notes
    try:
        freeze = resolve_rev(freeze_raw)
    except SystemExit:
        problems.append("product_freeze_sha %r does not resolve" % freeze_raw)
        return problems, notes

    evid_raw = doc.get("evidence_head")
    try:
        evid = resolve_rev(evid_raw) if evid_raw else None
    except SystemExit:
        evid = None
        problems.append("evidence_head %r does not resolve" % evid_raw)

    tag_raw = doc.get("tag_head")
    tag: Optional[str] = None
    if tag_raw:
        try:
            tag = resolve_rev(tag_raw)
        except SystemExit:
            problems.append("tag_head %r does not resolve" % tag_raw)

    head = resolve_rev("HEAD")

    # (1) direction of travel
    if evid is not None and not is_ancestor(freeze, evid):
        problems.append("PRODUCT_FREEZE_SHA is not an ancestor of EVIDENCE_HEAD")

    # (2) product bytes at the freeze commit
    for e in doc.get("files", []):
        rel = e["path"]
        want = e.get("sha256", "")
        try:
            reject_bad_digest(rel, want)
        except SystemExit as exc:
            problems.append(str(exc))
            continue
        spec = {"path": rel, "owner": e.get("owner", "")}
        if e.get("hash_convention") == "dir_fingerprint" or _looks_like_dir(freeze, rel):
            spec["dir"] = "true"
        digest, _size, conv = entry_digest(freeze, spec)
        if digest is None:
            problems.append("%s: not present at product_freeze_sha" % rel)
            continue
        if digest != want:
            problems.append("%s: product bytes at freeze differ from manifest "
                            "(hash mismatch)" % rel)

    # (3) no product change in freeze..evidence window
    if evid is not None and allow_evidence_head_ahead:
        for path in changed_paths(freeze, evid):
            if is_product_path(path):
                problems.append("PRODUCT CODE CHANGED after freeze: %s "
                                "(freeze..evidence window)" % path)

    # (4) tag trajectory
    if tag is not None:
        if not is_ancestor(freeze, tag):
            problems.append("TAG_HEAD is not a descendant of PRODUCT_FREEZE_SHA")
        for path in changed_paths(freeze, tag):
            if is_product_path(path):
                problems.append("PRODUCT CODE CHANGED after freeze: %s "
                                "(freeze..tag window)" % path)
    elif require_tag:
        problems.append("TAG_HEAD required but absent")

    # informational: whether the current HEAD introduces product drift past freeze
    if not is_ancestor(freeze, head):
        notes.append("HEAD is not a descendant of product_freeze_sha "
                     "(detached/other branch) -- product-byte check still valid")
    else:
        drift = [p for p in changed_paths(freeze, head) if is_product_path(p)]
        if drift:
            notes.append("HEAD carries %d product change(s) past the freeze; the "
                         "released tag is judged at TAG_HEAD, not HEAD" % len(drift))

    if not doc.get("generated_at"):
        problems.append("manifest carries no generated_at")

    return problems, notes


def _looks_like_dir(commit: str, rel: str) -> bool:
    return object_type(commit, rel) == "tree"


# --- CLI ---------------------------------------------------------------------

def emit(doc: Dict[str, Any], out_path: Path, sidecar_path: Path) -> int:
    payload = serialise(doc)
    digest = sha256_bytes(payload)
    dup = serialise(doc)
    if dup != payload:
        raise SystemExit("non-deterministic serialisation; refusing to emit")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(payload)
    sidecar_path.write_text(digest + "  " + out_path.name + "\n", encoding="utf-8")
    print("wrote %s (%d bytes, sha256 %s)" % (out_path.name, len(payload), digest))
    print("files: %d  freeze=%s  evidence=%s  tag=%s" % (
        doc["file_count"], doc["product_freeze_sha"][:12],
        (doc.get("evidence_head") or "")[:12], (doc.get("tag_head") or "")[:12]))
    return 0
