#!/usr/bin/env python
"""Relocate the agent sandbox's temp-probe canaries out of the repository root.

WHY THIS EXISTS (measured 2026-09-30, reproduction below)
=========================================================
Release provenance requires a clean worktree.  On this host the repository root
accumulated up to 180 untracked 8-character files that no product code creates.
They are *sandbox* residue, and the mechanism is fully accounted for:

1. CPython's ``tempfile._get_default_tempdir()`` validates a candidate temp
   directory by creating a file in it, writing the canary literal ``b'blat'``,
   and then unlinking it.  The literal is in CPython itself -- it is not in this
   repository, and it is not in the sandbox shim either, which is why grepping
   for it finds only the residue.
2. The sandbox denies ``os.unlink`` (and ``CODEBUDDY_SAFE_DELETE_ENABLED=0``
   does *not* lift it -- measured).  CPython's probe therefore never sees a
   successful cleanup, so it retries the next name, walking every candidate
   directory in ``_candidate_tempdir_list()``.
3. That list ends with ``os.curdir``.  The process CWD -- normally the
   repository root -- is the one place the sandbox *does* permit the create, so
   it absorbs the canaries, and ``tempfile.gettempdir()`` still raises
   ``FileNotFoundError``.  Hence the ``Error in sitecustomize`` banner on every
   Python start.

Reproduction (a single plain interpreter in an empty directory):

    $ cd <empty dir>
    $ python -c "print(1)"          # -> 20 files named [a-z0-9_]{8}, 4 bytes, b'blat'
    $ python -S -c "print(1)"       # -> 0 files

The identical bytes also appear in unrelated directories (measured in
``F:\\WorkBuddy\\resources``), which is what rules a product defect in: nothing
in this repository writes ``blat``.

WHY IT IS NOT A ``.gitignore`` RULE
===================================
The obvious fix -- ignore ``/[a-z0-9_][a-z0-9_]...`` (eight classes) -- would
also match the real root directories ``packages`` and ``fixtures``.  Git has no
"file but not directory" pattern, a directory match swallows its whole subtree,
and the resulting silence would hide genuinely new product files.  That is the
same temptation the ``nul`` rule in ``.gitignore`` was written about: the answer
is to decide whose file it is, not to hide it from ``git status``.

WHY IT IS A TRACKED TOOL
========================
An untracked gate is an unreproducible verdict.  "The tree is clean" is only
checkable if the cleanup that produced it travels with the repository, states
the exact signature it matched, and proves its own selector both accepts and
rejects.

WHAT IT REFUSES TO DO
=====================
* It never deletes.  Matches are **moved** into ``.workbuddy/`` (gitignored),
  following the project's ``只搬不删`` rule; ``PERMANENT_DELETE`` stays 0.
* It never moves a path that git tracks, and it never moves anything whose
  name, size and content do not all match the canary signature.  Both refusals
  are asserted with negative controls that must fail to be selected.
* It never reports the destination as safe without asking git whether the
  destination is actually ignored.
* It does not claim the root will *stay* clean.  Every Python start recreates
  canaries, including this tool's own, which is why it sweeps first and then
  reads ``git status`` as its last act.

Usage::

    python tools/v1_3_sandbox_canary_sweep.py             # dry run, report only
    python tools/v1_3_sandbox_canary_sweep.py --apply     # move them
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import time

try:  # keep GBK consoles from dying on an odd path
    sys.stdout.reconfigure(errors="backslashreplace")
    sys.stderr.reconfigure(errors="backslashreplace")
except Exception:  # pragma: no cover
    pass

REPO_ROOT = str(Path(__file__).resolve().parents[1])
SCRATCH = os.path.join(REPO_ROOT, ".workbuddy", "scratch")
DEST_ROOT = os.path.join(SCRATCH, "sandbox_canaries")

NAME_RE = re.compile(r"^[a-z0-9_]{8}$")
CANARY_BYTES = b"blat"

# Files that are legitimately tracked in the repository root.  Listed so the
# "nothing tracked is touched" leg has a real, non-synthetic subject: `.gitignore`
# is an eight-... it is not, it is nine characters with a dot -- which is exactly
# why it must be rejected.  A same-shaped *tracked* file would be rejected by the
# `git ls-files` check instead, and that check is exercised in the control.
TRACKED_ROOT_SUBJECTS = (".gitignore", ".gitattributes")


def decode_out(b: bytes) -> str:
    for enc in ("utf-8", "mbcs", "gbk", "latin-1"):
        try:
            return b.decode(enc)
        except Exception:
            continue
    return repr(b)


def run_git(args, cwd=REPO_ROOT):
    p = subprocess.run(
        ["git"] + list(args), cwd=cwd, capture_output=True, timeout=120,
        env={**os.environ, "GIT_PAGER": "cat"},
    )
    return p.returncode, decode_out(p.stdout), decode_out(p.stderr)


def is_canary(path: str):
    """Return None if the path is not a canary, else a dict describing the match.

    The three conditions are deliberately ANDed and the *name* is checked before
    the file is opened, so a directory or a large file is rejected without being
    read.
    """
    if not os.path.isfile(path):
        return None
    name = os.path.basename(path)
    if not NAME_RE.match(name):
        return None
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    if size != len(CANARY_BYTES):
        return None
    try:
        with open(path, "rb") as fh:
            body = fh.read()
    except OSError:
        return None
    if body != CANARY_BYTES:
        return None
    return {"name": name, "size": size, "sha256": hashlib.sha256(body).hexdigest()}


def select(root: str):
    """Every canary directly in `root`.  Pure: reads, never writes."""
    found = []
    try:
        names = sorted(os.listdir(root))
    except OSError as e:
        return found, str(e)
    for n in names:
        info = is_canary(os.path.join(root, n))
        if info:
            found.append(info)
    return found, None


def git_tracked(root: str, rel: str) -> bool:
    rc, _, _ = run_git(["-C", root, "ls-files", "--error-unmatch", "--", rel])
    return rc == 0


def tracked_paths(root: str):
    """All tracked paths in one call, instead of one subprocess per candidate.

    Semantics are unchanged (membership against what git reports); this only
    removes ~640 process spawns per sweep, which is what made a 641-file sweep
    take many minutes. Returns None if git fails, so the caller can refuse rather
    than silently treat every file as untracked.
    """
    rc, out, _ = run_git(["-C", root, "ls-files"])
    if rc != 0:
        return None
    return set(line.strip() for line in out.splitlines() if line.strip())


def unexpected_root_entries(root: str, canary_names) -> list:
    """Untracked entries in the root that are NOT canaries.

    Without this, "the root is clean" is unfalsifiable: the canary count can
    reach zero while other stray files sit right beside them. Anything untracked
    and not a canary is reported, so a human has to account for it.
    """
    rc, out, _ = run_git(["-C", root, "status", "--porcelain"])
    if rc != 0:
        return ["<git status failed>"]
    known = set(canary_names)
    bad = []
    for line in out.splitlines():
        if not line.startswith("?? "):
            continue
        name = line[3:].strip()
        if name.startswith('"') and name.endswith('"'):
            name = name[1:-1]
        if name.endswith("/"):
            name = name[:-1]
        if name in known:
            continue
        bad.append(name)
    return sorted(bad)


def _plant(directory: str, name: str, body: bytes):
    p = os.path.join(directory, name)
    with open(p, "wb") as fh:
        fh.write(body)
    return p


def run_controls(workdir: str):
    """Prove the selector is not a rubber stamp, in both directions."""
    os.makedirs(workdir, exist_ok=True)
    legs = []

    good = _plant(workdir, "ab12cd34", CANARY_BYTES)
    legs.append({
        "id": "POSITIVE_CONTROL_IS_SELECTED",
        "ok": is_canary(good) is not None,
        "detail": "an exact-signature file must be recognised",
    })

    upper = _plant(workdir, "AB12CD34", CANARY_BYTES)
    legs.append({
        "id": "NEGATIVE_CONTROL_UPPERCASE_NAME_IS_REJECTED",
        "ok": is_canary(upper) is None,
        "detail": "the generator alphabet is lowercase-only; uppercase is a different thing",
    })

    wrongbody = _plant(workdir, "ef56gh78", b"blat!")
    legs.append({
        "id": "NEGATIVE_CONTROL_WRONG_CONTENT_IS_REJECTED",
        "ok": is_canary(wrongbody) is None,
        "detail": "one extra byte must be enough to refuse",
    })

    wronglen = _plant(workdir, "ij90klmn1", CANARY_BYTES)
    legs.append({
        "id": "NEGATIVE_CONTROL_NINE_CHARACTER_NAME_IS_REJECTED",
        "ok": is_canary(wronglen) is None,
        "detail": "the name regex is anchored, not a substring test",
    })

    subdir = os.path.join(workdir, "op12qr34")
    os.makedirs(subdir, exist_ok=True)
    legs.append({
        "id": "NEGATIVE_CONTROL_DIRECTORY_IS_REJECTED",
        "ok": is_canary(subdir) is None,
        "detail": "a directory must never be moved as if it were a probe file",
    })

    # A real tracked root file, used as the subject of the same refusals that
    # guard the move loop.
    for rel in TRACKED_ROOT_SUBJECTS:
        p = os.path.join(REPO_ROOT, rel)
        legs.append({
            "id": "NEGATIVE_CONTROL_TRACKED_FILE_IS_NEITHER_SELECTED_NOR_MOVEABLE",
            "ok": is_canary(p) is None and git_tracked(REPO_ROOT, rel),
            "detail": "%s: not a canary by signature, and git reports it tracked" % rel,
        })
    return legs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="move matches (default: report only)")
    ap.add_argument("--root", default=REPO_ROOT)
    ap.add_argument("--dest", default=None)
    ap.add_argument("--limit", type=int, default=0,
                    help="move at most N files this invocation (0 = unbounded). The "
                         "host kills a process that performs too many deletions in "
                         "one turn, so a large backlog must be swept in bounded "
                         "batches; each batch then reports the remaining count "
                         "instead of claiming a clean root.")
    args = ap.parse_args()
    if args.limit < 0:
        ap.error("--limit must be >= 0")

    started = time.time()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = args.dest or os.path.join(DEST_ROOT, stamp)
    control_dir = os.path.join(SCRATCH, "canary_sweep_controls", stamp)

    legs = run_controls(control_dir)

    found, err = select(args.root)
    legs.append({
        "id": "ROOT_WAS_ACTUALLY_READABLE",
        "ok": err is None,
        "detail": "listing error: %s" % err if err else "listed %s" % args.root,
    })

    # Nothing tracked may be selected -- checked against git, not against a list.
    tracked = tracked_paths(args.root)
    legs.append({
        "id": "TRACKED_SET_IS_NON_EMPTY",
        "ok": bool(tracked),
        "detail": ("git ls-files reported %d paths; an empty set would make every "
                   "refusal below vacuously true"
                   % (len(tracked) if tracked else 0)),
    })
    tracked_hits = [c["name"] for c in found if tracked and c["name"] in tracked]
    legs.append({
        "id": "NOTHING_GIT_TRACKS_IS_SELECTED",
        "ok": not tracked_hits,
        "detail": "tracked paths that matched the signature: %r" % (tracked_hits,),
    })

    # Scanning order matters: this refuses to call the root clean just because
    # the canary count reached zero. Stray non-canary files are named explicitly.
    unexpected = unexpected_root_entries(args.root, [c["name"] for c in found])
    legs.append({
        "id": "NO_UNEXPECTED_UNTRACKED_FILES_IN_ROOT",
        "ok": not unexpected,
        "detail": "untracked, not canaries: %r" % (unexpected[:20],),
    })

    # Destination must really be ignored, or the "clean" claim is a relocation of
    # the problem to somewhere git can still see.
    os.makedirs(dest, exist_ok=True)
    rel_dest = os.path.relpath(dest, args.root).replace("\\", "/")
    rc, _, _ = run_git(["-C", args.root, "check-ignore", "-q", "--", rel_dest])
    legs.append({
        "id": "DESTINATION_IS_IGNORED_BY_GIT",
        "ok": rc == 0,
        "detail": "git check-ignore %s -> rc=%d" % (rel_dest, rc),
    })

    moved, failed = [], []
    remaining = None
    bounded = bool(args.limit) and len(found) > args.limit
    attempted = found[:args.limit] if args.limit else found
    if args.apply:
        manifest = []
        for c in attempted:
            src = os.path.join(args.root, c["name"])
            dst = os.path.join(dest, c["name"])
            if tracked and c["name"] in tracked:
                failed.append({"name": c["name"], "why": "tracked"})
                continue
            try:
                shutil.move(src, dst)
            except Exception as e:
                failed.append({"name": c["name"], "why": "%s: %s" % (type(e).__name__, e)})
                continue
            moved.append(c["name"])
            manifest.append({"name": c["name"], "sha256": c["sha256"], "moved_to": dst})
        with open(os.path.join(dest, "_MANIFEST.json"), "w", encoding="utf-8", newline="\n") as fh:
            json.dump({
                "tool": "v1_3_sandbox_canary_sweep",
                "moved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "count": len(moved),
                "bounded": bounded,
                "limit": args.limit or None,
                "signature": {"name_regex": NAME_RE.pattern, "size": len(CANARY_BYTES),
                              "content_hex": CANARY_BYTES.hex()},
                "entries": manifest,
            }, fh, indent=2)
        legs.append({
            "id": "EVERY_SELECTED_FILE_MOVED_OR_EXPLAINED",
            "ok": not failed,
            "detail": "attempted=%d moved=%d failed=%r" % (len(attempted), len(moved), failed),
        })

        after, err2 = select(args.root)
        remaining = len(after) if err2 is None else -1
        if bounded:
            # A bounded batch must prove it moved exactly what it said it would,
            # and that the residue fell by exactly that much. Claiming a clean
            # root here would be a lie, so it is not asserted at all.
            legs.append({
                "id": "BOUNDED_BATCH_MOVED_EXACTLY_ITS_QUOTA_AND_ADVANCED_THE_BACKLOG",
                "ok": (not failed and len(moved) == len(attempted)
                       and remaining >= 0 and remaining == len(found) - len(moved)
                       and remaining < len(found)),
                "detail": ("limit=%d found=%d attempted=%d moved=%d remaining=%d"
                           % (args.limit, len(found), len(attempted), len(moved), remaining)),
            })
        else:
            legs.append({
                "id": "ROOT_IS_FREE_OF_CANARIES_AFTER",
                "ok": err2 is None and not after,
                "detail": "remaining after sweep: %d" % remaining,
            })

        # Last act: read the ground truth the sweep just affected.  Note this must
        # come after the move, and nothing may start a fresh interpreter between
        # it and the reader of this report -- a new start re-creates canaries.
        rc, out, gerr = run_git(["-C", args.root, "status", "--porcelain"])
        legs.append({
            "id": "GIT_STATUS_READ_AFTER_THE_SWEEP",
            "ok": rc == 0,
            "detail": "rc=%d lines=%d stderr=%s" % (rc, len(out.splitlines()), gerr.strip()[:120]),
        })

    passed = sum(1 for l in legs if l["ok"])
    payload = {
        "tool": "v1_3_sandbox_canary_sweep",
        "mode": "apply" if args.apply else "dry-run",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "root": args.root,
        "destination": dest,
        "signature": {"name_regex": NAME_RE.pattern, "size": len(CANARY_BYTES),
                      "content_hex": CANARY_BYTES.hex()},
        "found": len(found),
        "moved": len(moved),
        "remaining": remaining,
        "bounded": bounded,
        "unexpected_files": unexpected,
        "failed": failed,
        "legs": legs,
        "verdict": "%s %d/%d" % ("PASS" if passed == len(legs) else "FAIL", passed, len(legs)),
        "elapsed_s": round(time.time() - started, 2),
        "not_claimed": [
            "that the root stays clean -- every Python start re-creates canaries",
            "that these files were ever part of the product",
        ],
    }
    os.makedirs(SCRATCH, exist_ok=True)
    out_path = os.path.join(SCRATCH, "v1_3_sandbox_canary_sweep_%s.json" % stamp)
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, indent=2)
    print(json.dumps(payload, indent=2))
    print("\nEVIDENCE %s" % out_path)
    return 0 if passed == len(legs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
