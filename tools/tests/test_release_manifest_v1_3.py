#!/usr/bin/env python3
"""Wave 0 regression tests -- non-self-referential release manifest (owner: Metric).

These tests prove the NEW provenance model has teeth. The v1.2 model could not be
tested for its central claim ("a tag checkout is CLEAN without re-emitting")
because that claim was FALSE by construction. Here we build real throwaway git
repositories in a temp dir and assert the directional invariants hold -- and,
just as importantly, that a deliberate product-code change after the freeze makes
the check FAIL.

The falsifiers (each a test that would pass on a broken implementation):
  T1  tag checkout, HEAD advanced by evidence-only commit -> CLEAN, no re-emit
  T2  product code changed after freeze -> FAIL (hard)
  T3  freeze not an ancestor of evidence_head -> FAIL
  T4  40-hex value in a sha256 field is refused
  T5  evidence-only commit between freeze and evidence head -> CLEAN
  T6  path classifier: product paths are product, evidence paths are not
  T7  the checker NEVER writes the manifest (mtime + digest unchanged)
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _load_engine():
    p = REPO / "tools" / "jcc_release_manifest.py"
    spec = importlib.util.spec_from_file_location("jcc_release_manifest", p)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


eng = _load_engine()


def _git(cwd: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=str(cwd), text=True, errors="replace",
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
             "GIT_CONFIG_GLOBAL": str(cwd / ".gitconfig-none"),
             "GIT_CONFIG_SYSTEM": str(cwd / ".gitconfig-none-none")}).strip()


def _init(cwd: Path) -> None:
    cwd.mkdir(parents=True, exist_ok=True)
    _git(cwd, "init", "-q", "-b", "main")
    _git(cwd, "config", "user.email", "t@t")
    _git(cwd, "config", "user.name", "t")
    _git(cwd, "config", "commit.gpgsign", "false")


def _commit(cwd: Path, msg: str) -> str:
    _git(cwd, "add", "-A")
    _git(cwd, "commit", "-q", "-m", msg)
    return _git(cwd, "rev-parse", "HEAD")


def _write(cwd: Path, rel: str, text: str) -> None:
    p = cwd / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


# The engine resolves `git`/`rev-parse` against eng.repo_root(). We monkeypatch
# both repo_root and the subprocess cwd by rebinding eng.repo_root for the test.
class _Sandbox:
    def __init__(self, root: Path):
        self.root = root

    def __enter__(self):
        self._orig = eng.repo_root
        eng.repo_root = lambda: self.root
        return self

    def __exit__(self, *exc):
        eng.repo_root = self._orig


PRODUCT_FILES = [
    {"path": "packages/app", "owner": "core", "dir": "true"},
    {"path": "config/jcc.yaml", "owner": "core"},
]
EVIDENCE_FILES = [
    {"path": "artifacts/release/proof.json", "owner": "ev"},
]


def _scaffold(root: Path) -> dict:
    _init(root)
    _write(root, "packages/app/__init__.py", "V = 1\n")
    _write(root, "config/jcc.yaml", "actuation: false\n")
    _write(root, "artifacts/release/proof.json", '{"ok": true}\n')
    freeze = _commit(root, "freeze")
    return {"freeze": freeze}


# --- T6: path classifier -----------------------------------------------------

@pytest.mark.parametrize("path,product", [
    ("packages/jcc_core/server.py", True),
    ("apps/command_center_3d/src/App.tsx", True),
    ("tools/emit_v1_3_release_manifest.py", True),
    ("config/jcc-v1.3.yaml", True),
    ("tests/e2e/test_x.py", True),
    ("artifacts/release/v1.3_product/V1_3_RELEASE_MANIFEST.json", False),
    ("artifacts/acceptance/final_gate_v1_3_result.json", False),
    ("docs/v1_3/WAVE_0.md", False),
    ("docs/release/NOTES.md", False),
    ("some/unknown/path.txt", True),  # fail-closed
])
def test_t6_path_classifier(path, product):
    assert eng.is_product_path(path) is product, path


def test_t6_glob_double_star():
    assert eng._glob_match("artifacts/release/**", "artifacts/release/a/b/c.json")
    assert eng._glob_match("artifacts/release/**", "artifacts/release/x.json")
    assert not eng._glob_match("artifacts/release/**", "artifacts/acceptance/x.json")
    assert eng._glob_match("docs/v1_3/**", "docs/v1_3/sub/x.md")
    assert not eng._glob_match("docs/v1_3/*", "docs/v1_3/sub/x.md")


# --- T4: digest hygiene ------------------------------------------------------

def test_t4_rejects_git_sha1_in_sha256_field():
    with pytest.raises(SystemExit):
        eng.reject_bad_digest("x.py", "a" * 40)
    with pytest.raises(SystemExit):
        eng.reject_bad_digest("x.py", "zz" * 32)


def test_t4_accepts_sha256():
    eng.reject_bad_digest("x.py", "a" * 64)


# --- T1: tag checkout CLEAN without re-emit ----------------------------------

def test_t1_tag_checkout_clean_without_reemit(tmp_path):
    root = tmp_path / "repo"
    with _Sandbox(root):
        st = _scaffold(root)
        freeze = st["freeze"]
        files = PRODUCT_FILES + EVIDENCE_FILES
        doc = eng.build_manifest(files, freeze, evidence_head=freeze,
                                 tag_head=None, release="t", generated_by="")
        # evidence-only commit AFTER the manifest was built
        _write(root, "artifacts/release/extra.json", '{"more": 1}\n')
        evid = _commit(root, "evidence only")
        # tag the evidence head
        _git(root, "tag", "release-tag")
        doc["evidence_head"] = evid
        doc["tag_head"] = evid
        # NOTE: no re-emit. The doc built at freeze must still check CLEAN.
        problems, notes = eng.check_manifest(doc)
        assert problems == [], problems


# --- T2: product change after freeze -> FAIL ---------------------------------

def test_t2_product_change_after_freeze_fails(tmp_path):
    root = tmp_path / "repo"
    with _Sandbox(root):
        st = _scaffold(root)
        freeze = st["freeze"]
        files = PRODUCT_FILES + EVIDENCE_FILES
        doc = eng.build_manifest(files, freeze, evidence_head=freeze,
                                 tag_head=None, release="t", generated_by="")
        # PRODUCT change after freeze
        _write(root, "packages/app/core.py", "def f(): return 2\n")
        evid = _commit(root, "product change (illegal)")
        doc["evidence_head"] = evid
        problems, _ = eng.check_manifest(doc)
        assert any("PRODUCT CODE CHANGED" in p for p in problems), problems


def test_t2_product_change_after_freeze_fails_with_tag(tmp_path):
    root = tmp_path / "repo"
    with _Sandbox(root):
        st = _scaffold(root)
        freeze = st["freeze"]
        files = PRODUCT_FILES + EVIDENCE_FILES
        doc = eng.build_manifest(files, freeze, evidence_head=freeze,
                                 tag_head=None, release="t", generated_by="")
        _write(root, "config/jcc.yaml", "actuation: TRUE\n")
        tagc = _commit(root, "config change (illegal)")
        _git(root, "tag", "bad-tag")
        doc["evidence_head"] = tagc
        doc["tag_head"] = tagc
        problems, _ = eng.check_manifest(doc)
        assert any("PRODUCT CODE CHANGED" in p for p in problems), problems


# --- T3: freeze not ancestor -> FAIL -----------------------------------------

def test_t3_freeze_not_ancestor_fails(tmp_path):
    root = tmp_path / "repo"
    with _Sandbox(root):
        st = _scaffold(root)
        files = PRODUCT_FILES + EVIDENCE_FILES
        # create a genuinely DIVERGENT line: an orphan root with no shared history
        _git(root, "checkout", "-q", "--orphan", "other")
        _git(root, "rm", "-q", "-rf", "--cached", ".")
        _write(root, "artifacts/release/proof.json", '{"ok": false}\n')
        other = _commit(root, "orphan line")
        # a fabricated doc whose freeze is on a different line
        doc = eng.build_manifest(files, st["freeze"], evidence_head=st["freeze"],
                                 tag_head=None, release="t", generated_by="")
        doc["evidence_head"] = other  # other is NOT a descendant of freeze
        problems, _ = eng.check_manifest(doc)
        assert any("not an ancestor" in p for p in problems), problems


# --- T5: evidence-only commits are fine --------------------------------------

def test_t5_evidence_only_commits_stay_clean(tmp_path):
    root = tmp_path / "repo"
    with _Sandbox(root):
        st = _scaffold(root)
        freeze = st["freeze"]
        files = PRODUCT_FILES + EVIDENCE_FILES
        doc = eng.build_manifest(files, freeze, evidence_head=freeze,
                                 tag_head=None, release="t", generated_by="")
        for i in range(5):
            _write(root, "artifacts/release/step_%d.json" % i, '{"i": %d}\n' % i)
            head = _commit(root, "evidence %d" % i)
        doc["evidence_head"] = head
        doc["tag_head"] = head
        problems, _ = eng.check_manifest(doc)
        assert problems == [], problems


# --- T7: the checker never writes --------------------------------------------

def test_t7_check_does_not_write_manifest(tmp_path):
    root = tmp_path / "repo"
    with _Sandbox(root):
        st = _scaffold(root)
        files = PRODUCT_FILES + EVIDENCE_FILES
        doc = eng.build_manifest(files, st["freeze"], evidence_head=st["freeze"],
                                 tag_head=None, release="t", generated_by="")
        out = root / "artifacts" / "release" / "V1_3_RELEASE_MANIFEST.json"
        payload = eng.serialise(doc)
        out.write_bytes(payload)
        digest_before = hashlib.sha256(out.read_bytes()).hexdigest()
        mtime_before = out.stat().st_mtime_ns
        eng.check_manifest(doc)
        assert hashlib.sha256(out.read_bytes()).hexdigest() == digest_before
        assert out.stat().st_mtime_ns == mtime_before
