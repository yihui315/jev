#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""emit_release_evidence.py -- machine-recomputable release evidence manifest.

WHY THIS EXISTS
---------------
`artifacts/` is gitignored (see `.gitignore`, the `artifacts/` line). Every machine artifact of
the P3 acceptance gate therefore lives OUTSIDE version control: the canonical
`p3_gate_result.json`, the immutable `verdict_3936c96.json`, and even the SRE leg's own report.
`docs/p3/P3_RELEASE_GATE.md` sec.1.2 compensated for that by hand-transcribing a table of sha256
anchors. A hand-maintained anchor table is exactly the thing that drifts (the record already
carried one mis-attributed hash before it was corrected at `9972288`).

This script turns that manual table into a recomputable one:

  * default mode : scan the release-evidence set, hash every file BYTE-LEVEL, write
                   `artifacts/release/manifest.json` (plus a `manifest.sha256` sidecar).
  * `--check`    : reconcile. Exit NON-ZERO on ANY mismatch. Never writes.

GATE SEMANTICS (deliberately strict; nothing here may be reclassified)
----------------------------------------------------------------------
  * hashing is byte-level. No encoding guess, no trailing-newline trimming, `open(..., 'rb')` +
    chunked `hashlib`. Windows is not special-cased because on Windows we read the bytes that are
    actually on disk.
  * there are TWO hashing conventions and they are recorded per entry (see HASH_RAW /
    HASH_EOL_LF below). Getting this wrong is not a rounding error: swallowing an EOL difference
    hides a real content question, while swallowing the autocrlf smudge makes the manifest
    irreproducible from a clean clone. Both failure modes have happened in this project.
  * `--check` fails on: missing manifest, schema_version mismatch, entry missing on disk,
    sha256 mismatch, byte-count mismatch, a file present on disk but absent from the manifest,
    an anchor mismatch against the P3 sec.1.2 table, and `--expect-sha` drift.
  * No failure is ever mapped onto "OK". A mismatch printed as a mismatch exits non-zero.

SAFETY
------
  * Default mode never silently overwrites: the previous manifest is preserved as
    `artifacts/release/manifest.previous.json` and the overwrite is announced on stdout.
  * `--check` never writes anything.
  * Nothing in this script reads, emits or stores secrets, logs or transcripts. The manifest
    records paths, digests and sizes only.

EV-11 REPRODUCIBILITY (ADR-0009)
-------------------------------
The manifest is regenerated TWICE within a single invocation, at the SAME commit. The two
serialised forms must hash identically or the emit is refused (a non-reproducible manifest is
worse than none). All run-time fields -- `ran_at`, `basetemp` -- are stripped from the main
record and recorded ONLY in the EV-11a sidecar (`manifest.ev11a.<commit>.json`, named by COMMIT,
never by wall-clock time). Pass `--basetemp <dir>` to give each proof run its own scratch
directory. This is the EV-11 guarantee: byte-identical evidence across re-runs.

THE EVIDENCE SET
----------------
The default set is the release-critical, sec.1.2-anchored evidence. It is an explicit list on
purpose: a recursive sweep of `artifacts/` would sweep pytest basetemp dirs, megabyte-scale
console logs and third-party scratch trees into a "release manifest", which would make the
manifest's meaning undefined. Use `--scan <dir>` to fold a whole directory in when you really
want to, with `--exclude` globs.

The generator is NOT a member of its own evidence set (it would make its own output depend on
its own bytes; see `--exclude-self`).

USAGE
-----
    python scripts/emit_release_evidence.py                 # write manifest (default mode)
    python scripts/emit_release_evidence.py --dry-run       # print what would be hashed
    python scripts/emit_release_evidence.py --check         # reconcile, exit 1 on drift
    python scripts/emit_release_evidence.py --check --expect-sha 3936c96
    python scripts/emit_release_evidence.py --check --no-anchors
    python scripts/emit_release_evidence.py --scan artifacts/acceptance --exclude '_*'
    python scripts/emit_release_evidence.py --json-report .workbuddy/p4_check.json

EXIT CODES
----------
    0  reconciled / written
    1  DRIFT: manifest does not match reality (or reality does not match the sec.1.2 anchors)
    2  ENVIRONMENT / USAGE error (bad root, git unavailable, target not found, bad argument)

Only the Python standard library is used. Python >= 3.8.
"""

from __future__ import annotations

import argparse
import datetime
import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------------------------
# Contract constants (shared contract, owned by the release process -- do not invent variants)
# --------------------------------------------------------------------------------------------

SCHEMA_VERSION = "1.0"
GENERATOR = "scripts/emit_release_evidence.py"

RELEASE_DIR = "artifacts/release"
MANIFEST_NAME = "manifest.json"
MANIFEST_SIDECAR = "manifest.sha256"
MANIFEST_PREVIOUS = "manifest.previous.json"
# EV-11a reproducibility sidecar: manifest.ev11a.<commit>.json -- named by COMMIT, never by wall
# clock time, so re-emission at the same commit yields the same sidecar path.
MANIFEST_EV11_SIDECAR_PREFIX = "manifest.ev11a"

CHUNK = 1 << 20  # 1 MiB; a single file is never read into memory whole

# sha256 is printed to 16 hex chars in the P3 record, matching the convention already used by
# docs/p3/P3_RELEASE_GATE.md sec.1.2.
HASH_PREFIX_LEN = 16

# --- hashing conventions ---------------------------------------------------------------------
# There are TWO, and applying one globally is the bug this section exists to prevent.
#
# HASH_RAW -- sha256 over the bytes exactly as stored. Correct, and the only meaningful choice,
# for files under `artifacts/` that are GITIGNORED: git never checks them out, so autocrlf never
# smudges them, so their bytes are identical in the main tree and in any worktree. Measured:
# `p3_gate_result.json` carries 458 CRLF pairs and hashes to 9f96bb69... in BOTH trees, raw.
#
# HASH_EOL_LF -- sha256 over the bytes with CRLF replaced by LF. This is the A8 `_sha()` convention
# and it is MANDATORY for any TRACKED file, because `core.autocrlf=true` smudges those on
# checkout: a fresh worktree holds CRLF where the main tree holds LF for the SAME commit, so a raw
# digest is not reproducible from a clean checkout. Measured on the frozen tree 3936c96:
#   packages/jcc_core/policy.py          main 4081B LF -> a4fe24cb | worktree 4171B CRLF -> 39639998
#   packages/supervisor/budget.py        main (dirty)          LF -> ? | worktree 13981B CRLF -> 96a11b69
#   artifacts/acceptance/p3_gate_run.py  main 17513B LF -> 83ce300b | worktree 17887B CRLF -> 6fa1a98e
# In every one of those rows the CRLF->LF digest is the one P3 sec.1.2 publishes. That is not
# coincidence: the A8 pin was built this way precisely because "a raw byte hash does NOT survive a
# re-checkout of the SAME commit" (verbatim from the pin's own docstring).
#
# Two rules, therefore:
#   * gitignored evidence -> raw.      "EOL normalisation does not apply", as instructed.
#   * tracked source      -> CRLF->LF. "Windows-stable" means it survives a re-checkout, which is
#                            the opposite of what raw gives you here.
#
# SUPERSEDED for tracked files by ADR-0010 / commit 9b82393 (`.gitattributes`: `* text=auto
# eol=lf`). The reason tracked files needed CRLF->LF was that `core.autocrlf=true` with no
# `.gitattributes` smudges them on checkout. With the attribute file in place a checkout produces
# LF for every tracked file, so the raw bytes ARE the bytes in the object store and raw is both
# correct and reproducible from a clean clone -- which is what ADR-0009 EV-4 ("byte-level hashing,
# no EOL normalisation") always asked for and what ADR-0010 condition 2 requires. The measurement
# above is preserved, not deleted: it is the evidence that the old rule was necessary, and it
# remains true of any tree checked out before 9b82393.
#
# `hash_convention` is recorded per entry so a reader can never mistake one for the other -- the
# failure mode that produced the mis-attributed hash in the P3 record before it was fixed at 9972288.
HASH_RAW = "raw"
HASH_EOL_LF = "crlf_to_lf"
# HASH_GIT_BLOB -- the sha256 of the canonical git blob, obtained via
# `git cat-file blob <frozen_at>:<path>` then `hashlib.sha256` (64 hex). This is EXACTLY the A8
# `_sha()` digest the release gate reconciles against (test_p3_acceptance_gate.py `_sha` /
# `_BASELINE_HASHES`): because `.gitattributes` (`* text=auto eol=lf`) stores every tracked file's
# blob as LF, the blob bytes already equal the CRLF->LF-normalised bytes, so sha256(blob) ==
# sha256(_sha()). It is read at the FROZEN commit (default HEAD), not from the worktree, so it
# survives a clean re-checkout and a re-clone. Used for the 12 P4 A8-pinned production artifacts.
# The manifest records it under the `sha256` field, which is now correctly a real sha256: 40-hex
# git SHA-1 object ids were NEVER sha256 and must never again be stored under that label (see
# harbor's defect fix).
HASH_GIT_BLOB = "git_blob"

# --------------------------------------------------------------------------------------------
# The release evidence set  (P4 freeze: the 12 A8-pinned production artifacts)
# --------------------------------------------------------------------------------------------
# (repo-relative path, generated_by, provenance_note, hash_convention)
#
# For the P4 freeze the release evidence IS the set of 12 A8-pinned production artifacts. The
# manifest's job is to certify, byte-exactly, the blobs frozen at `frozen_at`. Every entry here is
# a TRACKED file; with `.gitattributes` (`* text=auto eol=lf`, ADR-0010) the stored blob is LF, so
# the A8 `_sha()` baseline (CRLF->LF-normalised sha256) equals sha256(git cat-file blob <frozen>:path).
# The convention is therefore HASH_GIT_BLOB -- sha256 of the canonical blob at the frozen commit --
# which is both reproducible from a clean re-checkout/re-clone AND numerically equal to the A8
# `_BASELINE_HASHES` the release gate enforces. This is what makes the manifest's `sha256` field
# reconcile with the gate instead of carrying git's 40-hex SHA-1 object id (the prior defect).
#
# `generated_by` / `provenance_note` are free-form CLAIMS, advisory only; --check validates
# digests and sizes, never those fields.
#
# The fourth element is the hashing convention (HASH_RAW / HASH_EOL_LF / HASH_GIT_BLOB). No silent default.

# The 12 A8-pinned production artifacts for the P4 freeze. `hash_convention` is RAW for all (see
# the note above). Order is alphabetical by path for a stable, diff-friendly manifest.
_RELEASE_A8_PINS: List[Tuple[str, str, str, str]] = [
    ("config/jcc.yaml", "version control (git; A8 pin, P4)", "A8 pinned production artifact", HASH_GIT_BLOB),
    ("packages/jcc_core/actuator.py", "version control (git; A8 pin, P4)", "A8 pinned production artifact", HASH_GIT_BLOB),
    ("packages/jcc_core/config.py", "version control (git; A8 pin, P4)", "A8 pinned production artifact", HASH_GIT_BLOB),
    ("packages/jcc_core/core.py", "version control (git; A8 pin, P4)", "A8 pinned production artifact", HASH_GIT_BLOB),
    ("packages/jcc_core/decision.py", "version control (git; A8 pin, P4)", "A8 pinned production artifact", HASH_GIT_BLOB),
    ("packages/jcc_core/policy.py", "version control (git; A8 pin, P4)", "A8 pinned production artifact", HASH_GIT_BLOB),
    ("packages/jcc_core/run.py", "version control (git; A8 pin, P4)", "A8 pinned production artifact", HASH_GIT_BLOB),
    ("packages/jcc_core/server.py", "version control (git; A8 pin, P4)", "A8 pinned production artifact", HASH_GIT_BLOB),
    ("packages/jcc_core/state_store.py", "version control (git; A8 pin, P4)", "A8 pinned production artifact", HASH_GIT_BLOB),
    ("packages/supervisor/budget.py", "version control (git; A8 pin, P4)", "A8 pinned production artifact", HASH_GIT_BLOB),
    ("packages/supervisor/sentinel.py", "version control (git; A8 pin, P4)", "A8 pinned production artifact", HASH_GIT_BLOB),
    ("packages/supervisor/shadow_store.py", "version control (git; A8 pin, P4)", "A8 pinned production artifact", HASH_GIT_BLOB),
]

# The legacy P3 gate machine artifacts (gitignored; never checked out) are intentionally NOT part
# of the P4 A8 evidence set. They remain on disk under artifacts/acceptance/ for audit but are no
# longer enumerated by this manifest, which certifies only the frozen production code.

RELEASE_EVIDENCE: List[Tuple[str, str, str, str]] = list(_RELEASE_A8_PINS)

GENERATED_BY_BY_PATH: Dict[str, str] = {row[0]: row[1] for row in RELEASE_EVIDENCE}
PROVENANCE_NOTE_BY_PATH: Dict[str, str] = {row[0]: row[2] for row in RELEASE_EVIDENCE}
CONVENTION_BY_PATH: Dict[str, str] = {row[0]: row[3] for row in RELEASE_EVIDENCE}

UNSPECIFIED_GENERATED_BY = "unspecified (path is not in the release-evidence spec)"

# Default recursive-scan excludes. `__pycache__` and pytest basetemp trees are the two things that
# must never be swept into a release manifest.
DEFAULT_EXCLUDES: Tuple[str, ...] = (
    "__pycache__",
    "*.pyc",
    ".pytest_cache",
    "*.tmp",
    "*.log",
    "*.backup-*",
)

# --------------------------------------------------------------------------------------------
# The P3 sec.1.2 anchor table, transcribed for machine reconciliation.
#
# SOURCE: docs/p3/P3_RELEASE_GATE.md sec.1.2 "The evidence is NOT in version control", revision at
#         commit 9972288 (the revision that corrected the mis-attributed report hash).
#
# This is the machine-readable form of the manual table. It exists so the manual table can be
# checked rather than trusted. If a P4 change legitimately alters one of these files, the anchor
# MUST be re-baselined the same way the A8 pin was re-baselined for #70 -- not worked around:
#   --check is designed to fail loudly, and re-baselining means updating sec.1.2 in the record.
#
# The P3 table elides the `artifacts/acceptance/` prefix on rows 2-5; the full repo-relative
# paths are restored here.
#
# Only the first 16 hex characters are published in sec.1.2, so that is all that is stored: the
# stored value is a literal transcription of the document, nothing is padded or invented, and the
# comparison is prefix comparison.
#
# MEASUREMENT NOTE (harbor, 2026-09-25; byte-exact: main tree vs a pristine worktree of 3936c96
# vs `git cat-file blob`):
#
#   path                                  main tree          fresh worktree         sec.1.2 says
#   p3_gate_result.json                   raw 9f96bb69 ...    raw 9f96bb69 ...      9f96bb69
#   p3_gate_result.verdict_3936c96.json   raw 86222c22 ...    raw 86222c22 ...      86222c22
#   p3_gate_run.py                        raw 83ce300b ...    raw 6fa1a98e ...      83ce300b
#   P3_SRE_GATE_HARBOR.md                 raw be7f5cb2 ...    raw be7f5cb2 ...      be7f5cb2
#   packages/supervisor/budget.py         (dirty)            13981B CRLF -> 96a11b69
#   packages/jcc_core/policy.py          4081B LF -> a4fe24cb / 4171B CRLF -> 39639998
#   .../test_budget_cost_failclosed.py   5801B LF -> 52b6b5db / 5916B CRLF -> 891034da
#
# Reading: every row agrees with the record in BOTH trees EXCEPT the tracked ones, where only the
# CRLF->LF form agrees. sec.1.2's `packages/**` rows and the runner row are therefore A8 `_sha()`
# digests, not raw ones. That is not a defect in the record -- it is the only convention that
# survives a re-checkout, and the A8 pin's own docstring says so -- but it means one global rule
# cannot reconcile sec.1.2, so the convention is carried per row below.
# --------------------------------------------------------------------------------------------
P3_ANCHORS: Dict[str, Dict[str, object]] = {
    "artifacts/acceptance/p3_gate_result.json": {
        "sha256": "9f96bb6907a97edb",
        "bytes": 41496,
        "hash_convention": HASH_RAW,
    },
    "artifacts/acceptance/p3_gate_result.verdict_3936c96.json": {
        "sha256": "86222c22afaf2604",
        "bytes": 41546,
        "hash_convention": HASH_RAW,
    },
    "artifacts/acceptance/p3_gate_result.clean_pre_c1_4cc657f.json": {
        "sha256": "2daf168dcbb683db",
        "bytes": 40828,
        "hash_convention": HASH_RAW,
    },
    "artifacts/acceptance/p3_gate_result.c1_red_4c9c3f0.json": {
        "sha256": "52b8559aa564cce9",
        "bytes": 21991,
        "hash_convention": HASH_RAW,
    },
    "artifacts/acceptance/p3_gate_result.superseded_dirty_1082fff.json": {
        "sha256": "425bb0c85d7467f5",
        "bytes": 40808,
        "hash_convention": HASH_RAW,
    },
    "artifacts/acceptance/p3_gate_run.py": {
        "sha256": "83ce300b1b98dc6a",
        "bytes": 17513,
        "hash_convention": HASH_EOL_LF,
    },
    "artifacts/acceptance/P3_SRE_GATE_HARBOR.md": {
        "sha256": "be7f5cb299479007",
        "bytes": 19510,
        "hash_convention": HASH_RAW,
    },
    "packages/supervisor/budget.py": {
        "sha256": "96a11b69dd041482",
        "bytes": None,  # sec.1.2 publishes no size for this row; a size is not invented.
        "hash_convention": HASH_EOL_LF,
    },
    "packages/supervisor/tests/test_budget_cost_failclosed.py": {
        "sha256": "52b6b5db645fddcc",
        "bytes": None,
        "hash_convention": HASH_EOL_LF,
    },
    "packages/jcc_core/policy.py": {
        "sha256": "a4fe24cb1b2ad4b5",
        "bytes": None,
        "hash_convention": HASH_EOL_LF,
    },
}


# --------------------------------------------------------------------------------------------
# Hashing
# --------------------------------------------------------------------------------------------


def sha256_file(path: str) -> Tuple[str, int]:
    """sha256 + byte count of a file, read as raw bytes.

    No encoding assumption and no size cap. Used for HASH_RAW entries.
    """
    digest = hashlib.sha256()
    total = 0
    with open(path, "rb") as fh:
        while True:
            block = fh.read(CHUNK)
            if not block:
                break
            digest.update(block)
            total += len(block)
    return digest.hexdigest(), total


def git_blob_hash(root: str, path: str, frozen_at: Optional[str] = None) -> Tuple[str, int]:
    """sha256 of the canonical git blob, via `git cat-file blob <rev>:<path>`.

    This is the A8 pin's required digest. The release gate's `_sha()` is
    `sha256(bytes.replace(b"\\r\\n", b"\\n"))` (see
    packages/jcc_core/tests/test_p3_acceptance_gate.py), and because `.gitattributes`
    (`* text=auto eol=lf`, ADR-0010) stores every tracked file's blob as LF, the
    stored blob bytes ARE the CRLF->LF-normalised bytes. So `sha256(cat-file blob ...)`
    is byte-identical to the A8 `_sha()` baseline (`_BASELINE_HASHES`), and the
    manifest's `sha256` field is now genuinely a sha256 (64 hex) -- NOT git's 40-hex
    SHA-1 object id.

    The blob is read at the FROZEN commit (`frozen_at`, default HEAD) rather than from
    the worktree, so the digest is checkout-independent and reproduces from a clean
    clone regardless of `core.autocrlf`. The previously-shipped implementation used
    `git hash-object` and therefore recorded git's SHA-1 object id under a `sha256`
    label -- a defect corrected here (harbor).
    """
    if frozen_at is None:
        frozen_at = git_head(root)
    # Resolve to a full SHA-1 so an abbreviated `frozen_at` cannot be ambiguous in
    # the `<rev>:<path>` syntax.
    resolved = subprocess.run(
        ["git", "-C", root, "rev-parse", "--verify", frozen_at],
        capture_output=True,
        text=True,
        shell=False,
    )
    if resolved.returncode != 0:
        raise EnvironmentError(
            "git rev-parse --verify failed for frozen_at={!r}: {}".format(
                frozen_at, resolved.stderr.strip()
            )
        )
    rev = resolved.stdout.strip()
    rel = relpath(root, path)
    blob_spec = "{}:{}".format(rev, rel)
    proc = subprocess.run(
        ["git", "-C", root, "cat-file", "blob", blob_spec],
        capture_output=True,
        text=False,
        shell=False,
    )
    if proc.returncode != 0:
        raise EnvironmentError(
            "git cat-file blob failed for {!r}: {}".format(
                blob_spec, proc.stderr.decode("utf-8", "replace").strip()
            )
        )
    blob = proc.stdout  # bytes -- the canonical blob, already LF-normalised by .gitattributes
    digest = hashlib.sha256(blob).hexdigest()
    return digest, len(blob)


def hash_artifact(root: str, path: str, convention: str, frozen_at: Optional[str] = None) -> Tuple[str, int]:
    """Digest + size of `path` under the named convention.

    HASH_RAW      -- the bytes exactly as stored.
    HASH_EOL_LF   -- the bytes with CRLF replaced by LF (the A8 `_sha` convention). This is NOT
                     "smoothing an EOL difference away": it is the only digest of a tracked file
                     that still matches after `core.autocrlf=true` smudges the file on a fresh
                     checkout. It is what makes the manifest reproducible from a clean clone.
    HASH_GIT_BLOB -- sha256 of the canonical git blob (`git cat-file blob <frozen_at>:<path>`),
                     NOT git's SHA-1 object id. This equals the A8 `_sha()` baseline (the gate's
                     `_BASELINE_HASHES`) because the stored blob is already LF-normalised by
                     `.gitattributes`. It is the convention used for the 12 P4 A8-pinned
                     production artifacts, and it is recorded under the `sha256` field, which is
                     genuinely a sha256.

    An unrecognised convention is a hard error rather than a silent fallback to raw, because a
    silently-wrong convention is exactly how a hash gets mis-attributed without anyone noticing.
    """
    if convention == HASH_RAW:
        return sha256_file(path)
    if convention == HASH_EOL_LF:
        digest = hashlib.sha256()
        total = 0
        carry = b""  # a CR at a chunk boundary may be the start of a CRLF that spans the boundary
        with open(path, "rb") as fh:
            while True:
                block = fh.read(CHUNK)
                if not block:
                    break
                buf = carry + block
                carry = b""
                if buf.endswith(b"\r"):
                    carry = b"\r"  # possibly half of a CRLF; hold it until the next block decides
                    buf = buf[:-1]
                buf = buf.replace(b"\r\n", b"\n")
                digest.update(buf)
                total += len(buf)
        if carry:
            # A trailing lone CR has no LF to pair with, so it is a real byte; keep it as-is.
            digest.update(carry)
            total += 1
        return digest.hexdigest(), total
    if convention == HASH_GIT_BLOB:
        return git_blob_hash(root, path, frozen_at)
    raise EnvironmentError(
        "unknown hash convention {!r} for {!r} (expected {!r}, {!r} or {!r})".format(
            convention, path, HASH_RAW, HASH_EOL_LF, HASH_GIT_BLOB
        )
    )


def short(hexdigest: str) -> str:
    return hexdigest[:HASH_PREFIX_LEN]


def conv_label(convention: str) -> str:
    """Two-char label for the hashing convention, used only in human-readable output."""
    if convention == HASH_EOL_LF:
        return "lf"
    if convention == HASH_GIT_BLOB:
        return "git"
    return "raw"


# --------------------------------------------------------------------------------------------
# Repository helpers
# --------------------------------------------------------------------------------------------


def is_git_root(candidate: str) -> bool:
    """True if `candidate` is a git working tree root.

    Both shapes have to be accepted, and this is not hypothetical:
      * a MAIN checkout has a `.git` DIRECTORY;
      * a LINKED worktree (`git worktree add`) has a `.git` FILE containing
        `gitdir: <...>/.git/worktrees/<name>`.
    The canonical P3 verdict was itself taken inside a detached worktree, so rejecting the
    worktree shape would make this tool unusable on exactly the trees it is meant to certify.
    """
    dot_git = os.path.join(candidate, ".git")
    if os.path.isdir(dot_git):
        return True
    if os.path.isfile(dot_git):
        try:
            with open(dot_git, "r", encoding="utf-8", errors="replace") as fh:
                # `.git` files written by `git worktree` contain `gitdir: <path>`; comparing the
                # prefix (not the whole first read) keeps this correct for any git version.
                return fh.read(64).strip().startswith("gitdir:")
        except OSError:
            return False
    return False


def repo_root(explicit: Optional[str] = None) -> str:
    """Locate the repository root.

    `scripts/emit_release_evidence.py` -> parents[1] of the script dir, with an explicit
    `--root` override. The override exists because the same file is run from inside throwaway
    git worktrees, where the script's own location already points at the right root anyway -- the
    override is for tooling that copies the script elsewhere.
    """
    if explicit:
        root = os.path.abspath(explicit)
    else:
        root = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
    if not is_git_root(root):
        raise EnvironmentError(
            "no .git at {!r}. Pass --root <repo root> or run from inside the repository.".format(root)
        )
    return root


def git_head(root: str) -> str:
    """Current HEAD, abbreviated the way the P3 record writes it (7 hex chars).

    Raises rather than fabricating a value: a manifest stamped with an invented SHA is worse than
    a manifest that cannot be produced.
    """
    proc = subprocess.run(
        ["git", "-C", root, "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        shell=False,
    )
    if proc.returncode != 0:
        raise EnvironmentError("git rev-parse HEAD failed (rc={}): {}".format(proc.returncode, proc.stderr.strip()))
    sha = proc.stdout.strip()
    return sha[:7] if len(sha) > 7 else sha


def relpath(root: str, path: str) -> str:
    """Repo-relative, forward-slash separated -- the manifest's `path` unit."""
    return os.path.relpath(os.path.abspath(path), os.path.abspath(root)).replace(os.sep, "/")


def _dirty_set(root: str) -> List[str]:
    """Paths that git reports as modified or untracked, or [] when the tree is clean.

    Untracked paths count: a manifest generated alongside an untracked `scripts/` or an
    untracked test file is describing a tree no one can reproduce from a clone.
    """
    proc = subprocess.run(
        ["git", "-C", root, "status", "--porcelain"],
        capture_output=True,
        text=True,
        shell=False,
    )
    if proc.returncode != 0:
        raise EnvironmentError("git status --porcelain failed: {}".format(proc.stderr.strip()))
    return sorted(
        line[3:].strip() for line in proc.stdout.splitlines() if len(line) > 3 and line[3:].strip()
    )


# --------------------------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------------------------


def _excluded(rel: str, excludes: Sequence[str]) -> bool:
    for pattern in excludes:
        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(rel, pattern.rstrip("/") + "/*"):
            return True
        for part in rel.split("/"):
            if fnmatch.fnmatch(part, pattern):
                return True
            if pattern.endswith("/*") and fnmatch.fnmatch(part, pattern[:-2]):
                return True
    return False


def discover(
    root: str,
    scan_dirs: Sequence[str],
    excludes: Sequence[str],
    exclude_self: bool,
) -> Tuple[List[str], List[str]]:
    """Return (targets, missing). Targets are repo-relative, sorted, de-duplicated."""
    targets: List[str] = [row[0] for row in RELEASE_EVIDENCE]

    for spec in scan_dirs:
        abs_dir = spec if os.path.isabs(spec) else os.path.join(root, spec)
        if not os.path.isdir(abs_dir):
            raise EnvironmentError("scan directory not found: {!r}".format(abs_dir))
        for dirpath, dirnames, filenames in os.walk(abs_dir):
            if exclude_self and os.path.abspath(dirpath) == os.path.abspath(os.path.join(root, RELEASE_DIR)):
                continue
            dirnames[:] = [d for d in dirnames if not _excluded(os.path.join(relpath(root, dirpath), d), excludes)]
            for name in filenames:
                rel = relpath(root, os.path.join(dirpath, name))
                if exclude_self and rel.startswith(RELEASE_DIR + "/"):
                    continue
                if _excluded(rel, excludes):
                    continue
                if rel not in targets:
                    targets.append(rel)

    targets = sorted(set(targets))
    missing = [t for t in targets if not os.path.isfile(os.path.join(root, t))]
    return targets, missing


# --------------------------------------------------------------------------------------------
# Manifest build / load
# --------------------------------------------------------------------------------------------


def build_manifest(root: str, targets: Iterable[str], frozen_at: Optional[str] = None) -> Dict[str, object]:
    """Hash every target and return the manifest object. Raises if a target is absent."""
    frozen = frozen_at or git_head(root)
    entries: List[Dict[str, object]] = []
    for rel in sorted(targets):
        abs_path = os.path.join(root, rel)
        if not os.path.isfile(abs_path):
            raise EnvironmentError("evidence target missing: {}".format(rel))
        convention = CONVENTION_BY_PATH.get(rel, HASH_RAW)
        digest, size = hash_artifact(root, abs_path, convention, frozen)
        entry: Dict[str, object] = {
            "path": rel,
            "sha256": digest,
            "bytes": size,
            "generated_by": GENERATED_BY_BY_PATH.get(rel, UNSPECIFIED_GENERATED_BY),
            "hash_convention": convention,
        }
        note = PROVENANCE_NOTE_BY_PATH.get(rel)
        if note:
            entry["provenance_note"] = note
        entries.append(entry)

    # NOTE: no `generated_at` / run-time field here. EV-11 (ADR-0009) requires the main record to
    # be byte-stable across re-runs; all run-time fields (ran_at, basetemp) live ONLY in the
    # EV-11a sidecar, never in this record.
    return {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR,
        "frozen_at": frozen,
        "entries": entries,
    }


def load_manifest(root: str, path: str) -> Dict[str, object]:
    abs_path = path if os.path.isabs(path) else os.path.join(root, path)
    if not os.path.isfile(abs_path):
        raise EnvironmentError("manifest not found: {}".format(relpath(root, abs_path)))
    with open(abs_path, "r", encoding="utf-8") as fh:
        try:
            data = json.load(fh)
        except ValueError as exc:
            raise EnvironmentError("manifest is not valid JSON: {}".format(exc))
    if not isinstance(data, dict):
        raise EnvironmentError("manifest root is not an object")
    return data


def manifest_index(manifest: Dict[str, object]) -> Dict[str, Dict[str, object]]:
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise EnvironmentError("manifest.entries is not a list")
    out: Dict[str, Dict[str, object]] = {}
    for raw in entries:
        if not isinstance(raw, dict) or not isinstance(raw.get("path"), str):
            raise EnvironmentError("manifest entry without a string 'path'")
        out[raw["path"]] = raw
    return out


# --------------------------------------------------------------------------------------------
# Anchors
# --------------------------------------------------------------------------------------------


def load_anchors(root: str, anchor_file: Optional[str]) -> Optional[Dict[str, Dict[str, object]]]:
    """Return the active anchor table (embedded P3 sec.1.2, overlaid/extended by --anchor-file).

    The embedded table stores exactly the 16-hex prefixes that sec.1.2 publishes -- no padding, no
    guessed tail. Bytes are None for the tracked-source rows, because sec.1.2 publishes no size for
    those, and a size is not invented.
    """
    anchors: Dict[str, Dict[str, object]] = {}
    for path, rec in P3_ANCHORS.items():
        prefix = str(rec["sha256"])[:HASH_PREFIX_LEN]
        anchors[path] = {
            "sha256": prefix,
            "bytes": rec.get("bytes"),
            "hash_convention": rec.get("hash_convention", HASH_RAW),
        }

    if anchor_file:
        abs_file = anchor_file if os.path.isabs(anchor_file) else os.path.join(root, anchor_file)
        if not os.path.isfile(abs_file):
            raise EnvironmentError("anchor file not found: {}".format(abs_file))
        with open(abs_file, "r", encoding="utf-8") as fh:
            raw = fh.read()
        rows = 0
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("\t")]
            if len(parts) == 1:
                parts = [p.strip() for p in line.split(None, 2)]
            if len(parts) < 2:
                raise EnvironmentError("malformed anchor line: {!r}".format(line))
            path = parts[0]
            digest = parts[1]
            size = int(parts[2]) if len(parts) > 2 and parts[2] else None
            # An --anchor-file row may declare its convention as a 4th field; without one the row
            # inherits the embedded table's convention for that same path if it has one.
            convention = HASH_RAW
            if len(parts) > 3 and parts[3]:
                convention = parts[3] if parts[3] in (HASH_RAW, HASH_EOL_LF) else HASH_RAW
            elif path in P3_ANCHORS:
                convention = str(P3_ANCHORS[path].get("hash_convention", HASH_RAW))
            anchors[path] = {
                "sha256": digest.strip()[:HASH_PREFIX_LEN],
                "bytes": size,
                "hash_convention": convention,
            }
            rows += 1
        print("[info] anchor file loaded: {} row(s) from {}".format(rows, relpath(root, abs_file)))
    return anchors


# --------------------------------------------------------------------------------------------
# Reconciliation
# --------------------------------------------------------------------------------------------


def reconcile(
    root: str,
    manifest: Dict[str, object],
    targets: Sequence[str],
    anchors: Optional[Dict[str, Dict[str, object]]],
    expect_sha: Optional[str],
) -> Tuple[List[str], List[str]]:
    """Compare manifest vs disk vs anchors. Returns (ok_rows, drift_rows).

    ok_rows   are lines that can be shown as-is.
    drift_rows are lines that MUST be shown and MUST lead to a non-zero exit.
    """
    ok: List[str] = []
    drift: List[str] = []

    # The frozen commit the A8 pins were read from; passed to HASH_GIT_BLOB so reconcile reads
    # the same blob the manifest was emitted against (checkout-independent).
    frozen_at = manifest.get("frozen_at")

    # --- schema -------------------------------------------------------------------------
    if manifest.get("schema_version") != SCHEMA_VERSION:
        drift.append(
            "SCHEMA  manifest.schema_version={!r} expected {!r}".format(
                manifest.get("schema_version"), SCHEMA_VERSION
            )
        )
    else:
        ok.append("SCHEMA  schema_version == {}".format(SCHEMA_VERSION))

    # --- frozen_at ----------------------------------------------------------------------
    recorded = str(manifest.get("frozen_at") or "")
    head = git_head(root)
    if expect_sha:
        if recorded == expect_sha and head == expect_sha:
            ok.append("FROZEN  frozen_at={} matches --expect-sha and HEAD".format(recorded))
        else:
            if recorded != expect_sha:
                drift.append("FROZEN  manifest.frozen_at={} but --expect-sha={}".format(recorded, expect_sha))
            if head != expect_sha:
                drift.append("FROZEN  git HEAD={} but --expect-sha={}".format(head, expect_sha))
    else:
        if recorded == head:
            ok.append("FROZEN  frozen_at={} matches HEAD".format(recorded))
        elif recorded == "":
            drift.append("FROZEN  manifest.frozen_at is empty")
        else:
            ok.append(
                "FROZEN  manifest.frozen_at={} differs from HEAD={} (advice: re-run the generator "
                "on the tree you are certifying, or pass --expect-sha)".format(recorded, head)
            )

    # --- the working tree must be clean for the manifest to mean anything -----------------
    dirty = _dirty_set(root)
    if dirty:
        # NOT an informational line. A dirty tree means the manifest hashes bytes that no commit
        # pins, i.e. bytes that are not the product. --check must therefore FAIL on it; counting
        # it under "ok" would be absorbing a failure into a PASS.
        drift.append(
            "TREE-DIRTY  working tree has uncommitted change(s) ({} path(s)): {}".format(
                len(dirty), ", ".join(sorted(dirty))
            )
        )
    else:
        ok.append("TREE       working tree is clean")

    # --- entries ------------------------------------------------------------------------
    index = manifest_index(manifest)
    seen: List[str] = []
    for rel in targets:
        abs_path = os.path.join(root, rel)
        if not os.path.isfile(abs_path):
            drift.append(
                "MISSING  {} -- listed in the manifest / evidence set but absent on disk".format(rel)
            )
            continue
        m = index.get(rel)
        if m is None:
            # With no entry there is no declared convention, so measure raw and label it as such.
            fallback_digest, fallback_bytes = sha256_file(abs_path)
            drift.append(
                "UNRECORDED  {} -- present on disk but absent from the manifest "
                "(sha256={} bytes={} convention=raw)".format(rel, short(fallback_digest), fallback_bytes)
            )
            continue
        seen.append(rel)
        convention = str(m.get("hash_convention") or HASH_RAW)
        try:
            actual_digest, actual_bytes = hash_artifact(root, abs_path, convention, frozen_at)
        except EnvironmentError as exc:
            drift.append("CONVENTION  {}".format(exc))
            continue
        m_sha = str(m.get("sha256") or "")
        m_bytes = m.get("bytes")
        if m_sha != actual_digest:
            drift.append(
                "DIGEST[{}]  {}  manifest {}  disk {}".format(
                    convention, rel, short(m_sha) or "<none>", short(actual_digest)
                )
            )
        elif m_bytes != actual_bytes:
            drift.append(
                "SIZE[{}]   {}  manifest {}  disk {}".format(convention, rel, m_bytes, actual_bytes)
            )
        else:
            ok.append(
                "OK[{:2}]  {:56s} sha256 {}  bytes {}".format(
                    conv_label(convention), rel, short(actual_digest), actual_bytes
                )
            )

    for rel in sorted(set(index) - set(seen)):
        drift.append("STALE    {} -- recorded in the manifest but not in the evidence set".format(rel))

    # --- anchors ------------------------------------------------------------------------
    if anchors:
        for rel in sorted(anchors):
            abs_path = os.path.join(root, rel)
            anchor = anchors[rel]
            if not os.path.isfile(abs_path):
                drift.append("ANCHOR-MISSING  {}".format(rel))
                continue
            convention = str(anchor.get("hash_convention") or HASH_RAW)
            try:
                actual_digest, actual_bytes = hash_artifact(root, abs_path, convention)
            except EnvironmentError as exc:
                drift.append("ANCHOR-CONVENTION  {}".format(exc))
                continue
            got_prefix = str(anchor.get("sha256") or "")[:HASH_PREFIX_LEN]
            if got_prefix == actual_digest[:HASH_PREFIX_LEN]:
                if anchor.get("bytes") is None or anchor["bytes"] == actual_bytes:
                    ok.append(
                        "ANCHOR  [{:2}]  {:52s} sha256 {}  bytes {}".format(
                            "lf" if convention == HASH_EOL_LF else "raw",
                            rel, short(actual_digest), actual_bytes,
                        )
                    )
                else:
                    drift.append(
                        "ANCHOR-SIZE  {}  record says {} bytes, file is {}".format(
                            rel, anchor["bytes"], actual_bytes
                        )
                    )
            else:
                drift.append(
                    "ANCHOR-DIGEST[{}]  {}  P3 sec.1.2 records {}, file is {}".format(
                        convention, rel, got_prefix or "<none>", short(actual_digest)
                    )
                )

    return ok, drift


# --------------------------------------------------------------------------------------------
# Output / write
# --------------------------------------------------------------------------------------------


def render_table(rows: Sequence[str]) -> str:
    width = 0
    for row in rows:
        head = row.split(" ", 2)[0]
        width = max(width, len(head))
    lines = []
    for row in rows:
        head, _, rest = row.partition(" ")
        pad = " " * (width - len(head) + 2)
        lines.append("{}{}{}".format(head, pad, rest))
    return "\n".join(lines)


def serialize_manifest(manifest: Dict[str, object]) -> bytes:
    """Deterministic UTF-8 bytes for the manifest (no run-time fields).

    `json.dumps(..., sort_keys=False)` is fine because both the top-level key order and the
    entries list order are fixed by construction (alphabetical by path). Two identical manifests
    therefore produce byte-identical output -- which is exactly what the EV-11 two-run gate checks.
    """
    return (json.dumps(manifest, indent=2, sort_keys=False, ensure_ascii=False) + "\n").encode("utf-8")


def _atomic_write_bytes(out_abs: str, data: bytes) -> None:
    """Write `data` to `out_abs` atomically (temp file + fsync + os.replace)."""
    out_dir = os.path.dirname(out_abs)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=out_dir, prefix=".manifest.", suffix=".json.tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, out_abs)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def write_manifest(
    root: str,
    manifest: Dict[str, object],
    out: str,
    sidecar: bool,
    basetemp: Optional[str] = None,
) -> Tuple[str, Optional[str], Optional[str], bool]:
    """Write the manifest with an EV-11 two-run reproducibility gate.

    The manifest is built TWICE at the same commit (run 1 from the passed object, run 2 from a
    fresh rebuild off disk). The two serialised forms MUST hash identically or the write is
    refused -- a non-reproducible manifest is worse than no manifest. All run-time fields
    (ran_at, basetemp) live ONLY in the EV-11a sidecar, never in the main record.

    Returns (out_rel, manifest_sha256, ev11_sidecar_rel_or_None, reproducible).
    """
    # ---- EV-11 run 1 ----------------------------------------------------------------
    t1 = datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")
    payload1 = serialize_manifest(manifest)

    # ---- EV-11 run 2 (independent rebuild from disk) --------------------------------
    t2 = datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")
    entry_paths = [e["path"] for e in manifest.get("entries", []) if isinstance(e, dict)]
    manifest2 = build_manifest(root, entry_paths, manifest.get("frozen_at"))
    payload2 = serialize_manifest(manifest2)

    d1 = hashlib.sha256(payload1).hexdigest()
    d2 = hashlib.sha256(payload2).hexdigest()
    reproducible = d1 == d2
    if not reproducible:
        # Refuse to emit non-reproducible evidence. The caller maps this to a non-zero exit.
        return relpath(root, out), None, None, False

    # ---- basetemp: a unique, commit-stable scratch dir per EV-11 proof run ----------
    commit = str(manifest.get("frozen_at") or git_head(root))
    run1_dir = run2_dir = None
    if basetemp:
        run1_dir = os.path.join(basetemp, "ev11a_{}_run1".format(commit))
        run2_dir = os.path.join(basetemp, "ev11a_{}_run2".format(commit))
        os.makedirs(run1_dir, exist_ok=True)
        os.makedirs(run2_dir, exist_ok=True)

    # ---- atomic write, preserving the previous generation ---------------------------
    out_abs = out if os.path.isabs(out) else os.path.join(root, out)
    out_dir = os.path.dirname(out_abs)
    if os.path.isfile(out_abs):
        prev_rel = os.path.join(out_dir, MANIFEST_PREVIOUS)
        if os.path.isfile(prev_rel):
            # keep exactly one previous generation; the one before that is already history
            try:
                os.remove(prev_rel)
            except OSError:
                pass
        shutil.move(out_abs, prev_rel)
    _atomic_write_bytes(out_abs, payload1)

    sidecar_digest = None
    if sidecar:
        sidecar_abs = os.path.join(out_dir, MANIFEST_SIDECAR)
        sidecar_digest = d1
        with open(sidecar_abs, "w", encoding="utf-8") as fh:
            fh.write("{0}  {1}\n".format(d1, relpath(root, out_abs)))

    # ---- EV-11a reproducibility sidecar (commit-named, never time-named) ------------
    ev11_rel = None
    if sidecar:
        ev11_name = "{}.{}.json".format(MANIFEST_EV11_SIDECAR_PREFIX, commit)
        ev11_abs = os.path.join(out_dir, ev11_name)
        ev11_doc = {
            "schema_version": SCHEMA_VERSION,
            "sidecar": "EV-11a",
            "generator": GENERATOR,
            "commit": commit,
            "frozen_at": commit,
            "tool": "emit_release_evidence.py (manifest generation, two-run)",
            "entries_count": len(entry_paths),
            "reproducible": reproducible,
            "run1": {"ran_at": t1, "basetemp": run1_dir, "manifest_sha256": d1},
            "run2": {"ran_at": t2, "basetemp": run2_dir, "manifest_sha256": d2},
            "note": (
                "ran_at/basetemp are run-time fields stripped from the main manifest record "
                "(ADR-0009 EV-11). Both runs at the same commit produced an identical sha256."
            ),
        }
        with open(ev11_abs, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(ev11_doc, indent=2, ensure_ascii=False) + "\n")
        ev11_rel = relpath(root, ev11_abs)

    return relpath(root, out_abs), sidecar_digest, ev11_rel, reproducible


# --------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="emit_release_evidence.py",
        description="Recompute (or verify) the machine-recomputable release evidence manifest.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--root", help="repository root (default: the parent of scripts/)")
    p.add_argument(
        "--out",
        default="{}/{}".format(RELEASE_DIR, MANIFEST_NAME),
        help="manifest path relative to the root (default: %(default)s)",
    )
    p.add_argument(
        "--check",
        action="store_true",
        help="reconcile only; never write; exit 1 on any drift",
    )
    p.add_argument("--dry-run", action="store_true", help="print the entries that would be hashed; write nothing")
    p.add_argument("--frozen-at", help="override the manifest's frozen_at (default: current HEAD)")
    p.add_argument("--expect-sha", help="require manifest.frozen_at AND git HEAD to equal this SHA")
    p.add_argument("--scan", action="append", default=[], metavar="DIR", help="fold DIR in recursively (repeatable)")
    p.add_argument("--exclude", action="append", default=[], metavar="GLOB", help="extra exclude glob (repeatable)")
    p.add_argument("--no-anchors", action="store_true", help="skip the P3 sec.1.2 anchor reconciliation")
    p.add_argument("--anchor-file", help="TSV/WS-separated 'path<TAB>sha256[<TAB>bytes' table overriding the embedded anchors")
    p.add_argument("--exclude-self", action="store_true", help="exclude artifacts/release/ itself (on by default when scanning it)")
    p.add_argument("--no-sidecar", action="store_true", help="do not write artifacts/release/manifest.sha256")
    p.add_argument(
        "--basetemp",
        help="unique base dir for EV-11 proof-run scratch (one subdir per run); recorded in the sidecar",
    )
    p.add_argument("--json-report", help="write the machine-readable reconciliation report here")
    p.add_argument("--quiet", action="store_true", help="suppress the OK / info line (drift still printed)")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        root = repo_root(args.root)
    except EnvironmentError as exc:
        print("[env-error] {}".format(exc), file=sys.stderr)
        return 2

    try:
        if args.check:
            manifest = load_manifest(root, args.out)
        else:
            targets, missing = discover(
                root, args.scan, tuple(DEFAULT_EXCLUDES) + tuple(args.exclude), exclude_self=True
            )
            if missing:
                print("[env-error] evidence target(s) missing: {}".format(", ".join(missing)), file=sys.stderr)
                return 2
            manifest = build_manifest(root, targets, args.frozen_at)
    except EnvironmentError as exc:
        print("[env-error] {}".format(exc), file=sys.stderr)
        return 2

    anchors = None
    if not args.no_anchors:
        try:
            anchors = load_anchors(root, args.anchor_file)
        except EnvironmentError as exc:
            print("[env-error] {}".format(exc), file=sys.stderr)
            return 2

    if args.check:
        head = None
        try:
            head = git_head(root)  # fails fast if this root is not a git work tree
        except EnvironmentError as exc:
            print("[env-error] {}".format(exc), file=sys.stderr)
            return 2
        targets, _missing = discover(
            root, args.scan, tuple(DEFAULT_EXCLUDES) + tuple(args.exclude), exclude_self=True
        )
        ok, drift = reconcile(root, manifest, targets, anchors, args.expect_sha)
        if args.json_report:
            report_abs = args.json_report if os.path.isabs(args.json_report) else os.path.join(root, args.json_report)
            parent = os.path.dirname(os.path.abspath(report_abs))
            if parent:
                os.makedirs(parent, exist_ok=True)
            try:
                with open(report_abs, "w", encoding="utf-8") as fh:
                    json.dump(
                        {
                            "mode": "check",
                            "root": root,
                            "manifest": args.out,
                            "head": head,
                            "frozen_at": manifest.get("frozen_at"),
                            "anchor_reconciled": bool(anchors),
                            "ok": [{"line": r} for r in ok],
                            "drift": [{"line": r} for r in drift],
                            "ok_count": len(ok),
                            "drift_count": len(drift),
                            "reconciled": not drift,
                        },
                        fh,
                        indent=2,
                        ensure_ascii=False,
                    )
                    fh.write("\n")
            except OSError as exc:
                # A report the operator asked for that cannot be written is an ENVIRONMENT error,
                # not drift: it must not be reported as exit 1, which means "evidence mismatched".
                print("[env-error] could not write --json-report {}: {}".format(report_abs, exc), file=sys.stderr)
                return 2
        print("[check] reconciling {}".format(args.out))
        if not args.quiet:
            print(render_table(ok))
        if drift:
            print("")
            print("DRIFT -- {} item(s). Not reclassified, not absorbed:".format(len(drift)))
            print(render_table(drift))
            print("")
            print("[check] FAILED -- reconciliation is not clean.")
            return 1
        print("[check] CLEAN -- {} item(s) reconciled.".format(len(ok)))
        return 0

    if args.dry_run:
        index = manifest_index(manifest)
        print("[dry-run] would write {} ({} entries)".format(args.out, len(index)))
        for rel in sorted(index):
            entry = index[rel]
            print(
                "  [{:2}]  {:56s} sha256 {}  bytes {}".format(
                    conv_label(str(entry.get("hash_convention"))),
                    rel, short(str(entry.get("sha256"))), entry.get("bytes"),
                )
            )
        return 0

    out_rel, sidecar_digest, ev11_rel, reproducible = write_manifest(
        root, manifest, args.out, sidecar=not args.no_sidecar, basetemp=args.basetemp
    )
    if not reproducible:
        print(
            "[ev11-fail] manifest generation is NOT reproducible across two runs; refusing to emit.",
            file=sys.stderr,
        )
        return 1
    print("[write] manifest: {}".format(out_rel))
    if sidecar_digest:
        print("[write] sidecar : {}/{}  ({})".format(RELEASE_DIR, MANIFEST_SIDECAR, sidecar_digest))
    if ev11_rel:
        print("[write] ev11a   : {}  (two-run reproducible, commit-named)".format(ev11_rel))
    print("[write] entries : {}".format(len(manifest_index(manifest))))
    try:
        print("[write] frozen_at {} (git HEAD at this root {})".format(manifest.get("frozen_at"), git_head(root)))
    except EnvironmentError as exc:
        print("[write] frozen_at {} (could not read HEAD: {})".format(manifest.get("frozen_at"), exc))
    if anchors:
        print("[write] note    : P3 sec.1.2 anchors were NOT verified in write mode; run --check after.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
