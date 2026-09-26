#!/usr/bin/env python3
"""The commit boundary the FINAL pipeline does not have.

MIGRATED from ``.workbuddy/scratch/final/commit_evidence.py`` to a tracked home
under ``tools/finalize/``. The scratch copy worked; this one is the same tool with
the refusals kept, the parser hardened, and a ``--repo`` seam so it can be
falsified against a throwaway git repository instead of the release tree.

WHY THIS EXISTS
---------------
``final_gate_run.py`` decides P-01 with one unconditional rule::

    porcelain = git status --porcelain
    if porcelain == "":  ok
    else:                fail("working tree is dirty (unexplained provenance)")

The controller's own steps write TRACKED evidence into this repository
(JCC_SOAK_RESULT.json, WATCHDOG_RECOVERY_PROOF.json, REBOOT_RECOVERY.json, ...).
So before this tool existed, when FINAL-11 ran, ``git status`` was non-empty by
construction, P-01 failed, ``project_complete`` was false, ``_gate_judge`` refused
the step, and the pipeline could never reach COMPLETE.

This tool is the boundary that fixes that: it commits EXACTLY the evidence the run
just produced, so the gate is evaluated on a clean tree. P-01 is NOT weakened and
there is NO "evidence paths are tolerated" branch anywhere.

WHAT IT REFUSES (all fail-closed)
---------------------------------
* A dirty path that is not under an EVIDENCE_PREFIX. Product code must never be
  swept into a release-evidence commit -- that is how a freeze is broken.
* An untracked path (``??``). Committing one needs ``-f`` because ``artifacts/``
  is gitignored, and an untracked file appearing under the pipeline is exactly
  the "unreproducible judgement" this project refuses.
* An empty dirty set (nothing to do is not a success).
* A staged set that does not equal the dirty set it inspected, or a tree that is
  still dirty after the commit. Both are reconciled against ``git status``, not
  against an exit code.

Exit codes: 0 boundary reached; 2 refused at inspection; 3 staged != inspected;
4 commit failed; 5 still dirty after the commit.

Usage
-----
    python tools/finalize/commit_evidence.py --check
    python tools/finalize/commit_evidence.py --message "chore: evidence"
    python tools/finalize/commit_evidence.py --repo <scratch-repo> --message m
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys

DEFAULT_ROOT = os.environ.get("JCC_ROOT") or r"F:\jev"

#: The same set the gate uses to decide what "release evidence" means. Kept as a
#: literal here on purpose: if this tool imported it from the gate, a loosening of
#: the gate's list would silently widen what this tool commits.
EVIDENCE_PREFIXES = (
    "artifacts/", "docs/", "tools/", "scripts/", ".gitignore",
    "FINAL_ACTIVATION_CHECKLIST.md", "FINAL_ACCEPTANCE_REPORT.md",
)

#: A dirty path starting with a double quote means git quoted it (special bytes);
#: the byte-exact path is not what `line[3:]` shows, so refuse rather than guess.
def _parse_status(line: str):
    """``(status, path)`` for one porcelain line, or ``(status, None)`` if refused."""
    status = line[:2]
    rest = line[3:].rstrip()
    if rest.startswith('"'):
        return status, None                       # quoted path: refuse, do not guess
    if " -> " in rest:                            # a rename: use the NEW path
        rest = rest.split(" -> ", 1)[1].strip()
    return status, rest


class Boundary:
    def __init__(self, root: str):
        self.root = root

    def git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=self.root, capture_output=True,
                              text=True, encoding="utf-8", errors="replace")

    def porcelain(self) -> list:
        p = self.git("status", "--porcelain", "-uall")
        return [l for l in (p.stdout or "").splitlines() if l.strip()]

    def plan(self):
        """Returns ``(ok, paths, refused)``. Pure with respect to git."""
        lines = self.porcelain()
        if not lines:
            return False, [], ["the working tree is already clean; there is no "
                              "boundary to commit (an empty commit is not a success)"]
        paths, bad = [], []
        for line in lines:
            status, path = _parse_status(line)
            if path is None:
                bad.append("%s: a quoted path this tool will not interpret byte-exactly"
                           % line.strip())
                continue
            if status.strip() == "??":
                bad.append("%s: untracked path; the pipeline must not create one and "
                           "this tool will not add it (-f would be needed)" % path)
                continue
            if not any(path == p or path.startswith(p) for p in EVIDENCE_PREFIXES):
                bad.append("%s: outside the release-evidence prefixes %s -- product "
                           "code is never swept into this commit"
                           % (path, list(EVIDENCE_PREFIXES)))
                continue
            paths.append(path)
        return (not bad and bool(paths)), paths, bad


def _write_result(path, payload):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="JCC release-evidence commit boundary")
    ap.add_argument("--check", action="store_true", help="report only, never commit")
    ap.add_argument("--message", help="commit message (inline)")
    ap.add_argument("--message-file", help="commit message, read from this file")
    ap.add_argument("--json-out", help="write the machine result here")
    ap.add_argument("--repo", default=DEFAULT_ROOT,
                    help="the git repository to operate on (default: JCC_ROOT)")
    args = ap.parse_args(argv)

    b = Boundary(args.repo)
    ok, paths, bad = b.plan()
    inspected = len(b.porcelain())

    def finish(code, verdict, extra=None):
        payload = {"ok": code == 0, "code": code, "verdict": verdict,
                   "inspected_count": inspected, "paths": paths, "refused": bad}
        if extra:
            payload.update(extra)
        print(verdict)
        for p in paths:
            print("  EVIDENCE %s" % p)
        for x in bad:
            print("  REFUSED  %s" % x)
        if args.json_out:
            _write_result(args.json_out, payload)
        return code

    if args.check:
        return finish(0 if ok else 2,
                      "check only: %s" % ("would commit %d path(s)" % len(paths)
                                          if ok else "would refuse"))
    if not ok:
        return finish(2, "refusing to commit")

    message = args.message
    if args.message_file:
        with io.open(args.message_file, "r", encoding="utf-8") as fh:
            message = fh.read()
    if not message or not message.strip():
        return finish(2, "refusing: a commit message is required, so the boundary is "
                         "a recorded decision and not an anonymous sweep")

    b.git("add", "--", *paths)
    staged = [l for l in (b.git("diff", "--cached", "--name-only").stdout or "").splitlines()
              if l.strip()]
    if sorted(staged) != sorted(paths):
        print("REFUSING: staged set != inspected dirty set")
        print("  inspected: %s" % sorted(paths))
        print("  staged:    %s" % sorted(staged))
        return finish(3, "REFUSING: staged set != inspected dirty set",
                      {"staged": sorted(staged)})

    c = b.git("commit", "-m", message, "--", *paths)
    print((c.stdout or "") + (c.stderr or ""))
    if c.returncode != 0:
        return finish(4, "REFUSING: commit failed (exit %d)" % c.returncode)

    remaining = b.porcelain()
    if remaining:
        for l in remaining:
            print("  STILL DIRTY %s" % l)
        return finish(5, "REFUSING: the commit did not reach the fixed point; the tree "
                         "is still dirty, so P-01 would still fail",
                      {"remaining": remaining})
    return finish(0, "boundary commit complete; git status is empty, so the gate's P-01 "
                     "can now be evaluated on a clean tree")


if __name__ == "__main__":
    raise SystemExit(main())
