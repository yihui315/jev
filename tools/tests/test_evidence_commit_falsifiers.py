#!/usr/bin/env python3
"""Falsifiers for the release-evidence commit boundary (deliverable J).

A rule that cannot fail is not a rule. Each case below makes ONE thing true that
must force ``tools/finalize/commit_evidence.py`` to refuse (or, for case 1, to
succeed and leave a clean tree), and asserts on the TOOL'S EXIT CODE and on
``git status`` afterwards -- never on the tool's prose alone.

Every case runs against a THROWAWAY git repository created under the test's own
tmp directory, through the tool's ``--repo`` seam. Nothing here reads, writes or
commits anything in F:\\jev.

For each case the docstring names what would have to break for the case to pass
wrongly, so the test's own failure mode is on the record.

Run standalone (prints a table, exit 0 iff all pass):
    python tools/tests/test_evidence_commit_falsifiers.py
Run under pytest: it is also collected as ordinary tests.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "tools" / "finalize" / "commit_evidence.py"


def _git(root: Path, *args: str):
    return subprocess.run(["git", *args], cwd=str(root), capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def _porcelain(root: Path):
    return [l for l in (_git(root, "status", "--porcelain", "-uall").stdout or "").splitlines()
            if l.strip()]


def _make_repo(base: Path) -> Path:
    r = base / "repo"
    r.mkdir(parents=True, exist_ok=True)
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "harbor.falsifier@test.local")
    _git(r, "config", "user.name", "Harbor Falsifier")
    _git(r, "config", "commit.gpgsign", "false")
    (r / "artifacts").mkdir(exist_ok=True)
    (r / "packages").mkdir(exist_ok=True)
    (r / "artifacts" / "evidence.json").write_text('{"v": 1}\n', encoding="utf-8")
    (r / "packages" / "core.py").write_text("x = 1\n", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "init")
    return r


def _run_tool(repo: Path, json_out: Path, *extra: str):
    cmd = [sys.executable, str(TOOL), "--repo", str(repo),
           "--json-out", str(json_out)] + list(extra)
    env = dict(os.environ)
    env["CODEBUDDY_SAFE_DELETE_ENABLED"] = "0"
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env, timeout=180)


# --------------------------------------------------------------------------- #
# The five cases. Each returns (ok, detail).
# --------------------------------------------------------------------------- #

def case_1_only_allowed_evidence(tmp: Path):
    """Only allowed evidence dirty -> boundary PASSES and the tree is clean.

    WOULD PASS WRONGLY IF: the tool committed something other than the inspected
    set (then a product path could ride along), or reported success without the
    tree actually reaching the fixed point. Detected by re-reading
    `git status --porcelain` here, not by trusting the tool's message.
    """
    r = _make_repo(tmp)
    (r / "artifacts" / "evidence.json").write_text('{"v": 2}\n', encoding="utf-8")
    p = _run_tool(r, tmp / "out1.json", "--message", "case1 evidence")
    if p.returncode != 0:
        return False, "expected exit 0, got %d: %s" % (p.returncode, p.stdout[:200])
    left = _porcelain(r)
    if left:
        return False, "the boundary reported success but the tree is dirty: %s" % left
    return True, "exit 0 and git status clean"


def case_2_product_path_refused(tmp: Path):
    """`packages/core.py` dirty -> REFUSE, and nothing is committed.

    WOULD PASS WRONGLY IF: the prefix rule were widened to swallow product code.
    Detected by asserting the refuse exit AND that the file is still dirty (a
    refused boundary must not have committed anything).
    """
    r = _make_repo(tmp)
    (r / "packages" / "core.py").write_text("x = 2\n", encoding="utf-8")
    p = _run_tool(r, tmp / "out2.json", "--message", "case2")
    if p.returncode != 2:
        return False, "expected refuse (2), got %d" % p.returncode
    if "outside the release-evidence prefixes" not in p.stdout:
        return False, "refused for the wrong reason: %s" % p.stdout[:200]
    if not _porcelain(r):
        return False, "a refused boundary still modified the tree"
    return True, "exit 2, product path named, tree untouched"


def case_3_untracked_refused(tmp: Path):
    """An untracked (`??`) path -> REFUSE.

    WOULD PASS WRONGLY IF: the tool added untracked files (it would need `-f`,
    which is exactly the uncontrolled sweep the rule forbids). Detected by the
    `??` path still being untracked afterwards.
    """
    r = _make_repo(tmp)
    (r / "artifacts" / "brand_new.json").write_text("{}\n", encoding="utf-8")
    p = _run_tool(r, tmp / "out3.json", "--message", "case3")
    if p.returncode != 2:
        return False, "expected refuse (2), got %d" % p.returncode
    if "untracked path" not in p.stdout:
        return False, "refused for the wrong reason: %s" % p.stdout[:200]
    if not any(l.startswith("??") for l in _porcelain(r)):
        return False, "the untracked path is no longer untracked: something staged it"
    return True, "exit 2, untracked path refused"


def case_4_staged_ne_inspected(tmp: Path):
    """Staged set != inspected set -> REFUSE (exit 3).

    FORCED WITH A CONCURRENT GIT, NOT A TEST HOOK: `.git/index.lock` is what a
    second git process holds. `git add` then fails, so the staged set does not
    equal the set the tool inspected, and the tool must refuse rather than commit
    a partial boundary. This is a REAL condition (two git processes), not a seam.

    WOULD PASS WRONGLY IF: the tool trusted `git add`'s exit code, or committed
    whatever happened to be staged. Detected by the exit-3 code AND by the tree
    still being dirty.
    """
    r = _make_repo(tmp)
    (r / "artifacts" / "evidence.json").write_text('{"v": 2}\n', encoding="utf-8")
    lock = r / ".git" / "index.lock"
    lock.write_text("held by another git process\n", encoding="utf-8")
    try:
        p = _run_tool(r, tmp / "out4.json", "--message", "case4")
    finally:
        lock.unlink(missing_ok=True)
    if p.returncode != 3:
        return False, "expected refuse (3), got %d: %s" % (p.returncode, p.stdout[:200])
    if "staged set != inspected" not in p.stdout:
        return False, "refused for the wrong reason: %s" % p.stdout[:200]
    if not _porcelain(r):
        return False, "the tree is clean, so something was committed anyway"
    return True, "exit 3, staged!=inspected refused, tree untouched"


def case_5_not_a_fixed_point(tmp: Path):
    """Still dirty after the commit -> REFUSE (exit 5).

    FORCED WITH A pre-commit HOOK that writes a new allowed-prefix file during the
    commit, i.e. the tree moves underneath the boundary. A commit that does not
    reach a clean tree has not done its job -- P-01 would still fail -- so the
    tool must say so with a non-zero exit.

    WOULD PASS WRONGLY IF: the tool never re-read `git status` after committing.
    Detected by the exit-5 code AND by the leftover dirty path being present.
    """
    r = _make_repo(tmp)
    hook = r / ".git" / "hooks" / "pre-commit"
    hook.write_text('#!/bin/sh\necho "{}" > artifacts/after_commit.json\nexit 0\n',
                    encoding="utf-8", newline="\n")
    try:
        os.chmod(str(hook), 0o755)
    except OSError:
        pass
    (r / "artifacts" / "evidence.json").write_text('{"v": 2}\n', encoding="utf-8")
    p = _run_tool(r, tmp / "out5.json", "--message", "case5")
    if p.returncode != 5:
        return False, "expected refuse (5), got %d: %s" % (p.returncode, p.stdout[:200])
    if "still dirty" not in p.stdout.lower():
        return False, "refused for the wrong reason: %s" % p.stdout[:200]
    if not any("after_commit" in l for l in _porcelain(r)):
        return False, "the hook's file is gone, so the post-commit check did not see it"
    return True, "exit 5, non-fixed-point refused"


CASES = (
    ("case 1 only allowed evidence dirty -> PASS, tree clean", case_1_only_allowed_evidence),
    ("case 2 packages/core.py dirty -> REFUSE", case_2_product_path_refused),
    ("case 3 untracked ?? path -> REFUSE", case_3_untracked_refused),
    ("case 4 staged != inspected -> REFUSE", case_4_staged_ne_inspected),
    ("case 5 still dirty after commit -> REFUSE", case_5_not_a_fixed_point),
)


# --------------------------------------------------------------------------- #
# pytest wrappers
# --------------------------------------------------------------------------- #

def test_case_1_only_allowed_evidence(tmp_path):
    ok, detail = case_1_only_allowed_evidence(tmp_path)
    assert ok, detail


def test_case_2_product_path_refused(tmp_path):
    ok, detail = case_2_product_path_refused(tmp_path)
    assert ok, detail


def test_case_3_untracked_refused(tmp_path):
    ok, detail = case_3_untracked_refused(tmp_path)
    assert ok, detail


def test_case_4_staged_ne_inspected(tmp_path):
    ok, detail = case_4_staged_ne_inspected(tmp_path)
    assert ok, detail


def test_case_5_not_a_fixed_point(tmp_path):
    ok, detail = case_5_not_a_fixed_point(tmp_path)
    assert ok, detail


def test_the_tool_is_in_version_control():
    """The boundary must be a tracked tool, not a scratch script.

    The old tool lived under `.workbuddy/scratch/`; a boundary that verifies
    exactly once, on the machine that produced it, is the defect the whole
    provenance story exists to refuse.

    The tool must be COMMITTED AT HEAD, not merely present on disk and not merely
    `git add`-ed. The check reads the HEAD commit tree (`git cat-file -e
    HEAD:<path>`, asserting on the exit code only), because `git ls-files` reads
    the INDEX -- a staged-only file would pass there while a clone still would
    not have it. An untracked gate is an unreproducible verdict.
    """
    assert TOOL.is_file(), str(TOOL)
    assert (REPO / ".workbuddy" / "scratch" / "final" / "commit_evidence.py") != TOOL
    rel = TOOL.relative_to(REPO).as_posix()
    committed = _git(REPO, "cat-file", "-e", "HEAD:%s" % rel)
    assert committed.returncode == 0, (
        "%s is not committed at HEAD (git cat-file -e HEAD:%s exited %d); a clone "
        "would not have it." % (rel, rel, committed.returncode))


def main() -> int:
    import tempfile
    results = []
    for name, fn in CASES:
        with tempfile.TemporaryDirectory(prefix="jcc_commit_") as d:
            try:
                ok, detail = fn(Path(d))
            except Exception as exc:                              # noqa: BLE001
                ok, detail = False, "the case raised %s: %s" % (type(exc).__name__, exc)
        results.append((name, ok, detail))
    print("=" * 84)
    for name, ok, detail in results:
        print("  [%s] %-52s %s" % ("PASS" if ok else "FAIL", name, detail[:90]))
    bad = [r for r in results if not r[1]]
    print("=" * 84)
    print("evidence-commit falsifiers: %d/%d" % (len(results) - len(bad), len(results)))
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
