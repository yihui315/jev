#!/usr/bin/env python3
"""JCC v1.3 FINAL -- authoritative RELEASE TRUTH (owner: Forge, contract A2).

WHAT THIS IS
------------
The single place that answers "what is the product, and what is only evidence?"
The whole release rests on that question, and the defect it retires is that the
answer used to be read out of report TEXT. A report that says
``product_freeze_sha: 3a382de`` is a claim about the repository; it is not
evidence about it. This module never reads a SHA out of a report. Every commit
id here comes out of `git` itself, on this machine, right now:

    head                 = git rev-parse HEAD
    last_product_commit  = the newest commit that touched a PRODUCT path
    evidence_head        = HEAD (evidence travels on top of the frozen product)

If a SHA is not derivable from git, this tool reports the fact instead of
inventing a value.

THE CLASSIFIER (fail-closed, one direction)
-------------------------------------------
`classify()` returns exactly one of PRODUCT / EVIDENCE, and the rules are
ordered so that ambiguity resolves toward PRODUCT:

    1. any PRODUCT rule matches            -> PRODUCT
    2. else any EVIDENCE rule matches      -> EVIDENCE
    3. else                                -> PRODUCT  (UNKNOWN, fail-closed)

Rule (3) is the load-bearing one. A path nobody thought about is product, so a
stray new top-level directory cannot be waved through as "just evidence". This
is directive rule 6 and jcc-core-contract rule 6, and it is why `reports/**`
being evidence cannot accidentally become a hole: an unrecognised `reports2/`
is PRODUCT.

DELIBERATE SUPERSET OF THE DIRECTIVE (read this before "fixing" it)
------------------------------------------------------------------
Directive A2 lists `scripts/runtime-critical/**` under PRODUCT. This module
classifies ALL of `scripts/**` as product, and likewise `tools/**`, `tests/**`,
`facades/**`, `fixtures/**`, `integrations/**`.

Two reasons, both of which outrank the literal text:

  * A2 says the classification list is a MINIMUM ("路径分类至少包含"), so a
    superset satisfies the contract.
  * `tools/jcc_release_manifest.py` -- the engine that the v1.3 release manifest
    is actually built and `--check`ed with -- already classifies `scripts/**`
    and `tools/**` as product (PRODUCT_DENY). If this module used a narrower
    rule, the two release-truth tools would disagree about the same repository
    and the same commit range, and the narrower one would be the one that
    declares a release clean. Two classifiers that can disagree are worse than
    one strict classifier.

Usage
-----
  D:/jev/.venv/Scripts/python.exe tools/release_truth.py --emit
  D:/jev/.venv/Scripts/python.exe tools/release_truth.py --emit --freeze <sha>
  D:/jev/.venv/Scripts/python.exe tools/release_truth.py --check
  D:/jev/.venv/Scripts/python.exe tools/release_truth.py --selftest

`--emit` writes RELEASE_TRUTH.json + a .sha256 sidecar under
artifacts/release/v1.3_final/ (an EVIDENCE path, so emitting never dirties the
product). `--check` never writes: it re-derives the truth and reports
CLEAN/STALE, so it can be run on a tag or in CI.

Exit code is 0 only when the verdict is PASS. A product change after the freeze,
or a dirty PRODUCT file, is exit 1.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO = Path(__file__).resolve().parents[1]
ART = REPO / "artifacts" / "release" / "v1.3_final"
OUT = ART / "RELEASE_TRUTH.json"
SIDECAR = ART / "RELEASE_TRUTH.sha256"

GENERATED_BY = "tools/release_truth.py"
SCHEMA = "jcc/release-truth/1"

#: Bump when a rule below changes meaning. Consumers pin this: a verdict produced
#: under a different classifier is not comparable to one produced under this one.
CLASSIFICATION_VERSION = 1

SHA_RE = re.compile(r"^[0-9a-f]{40}$")


# --------------------------------------------------------------------------- #
# git primitives -- every SHA in this file comes from here
# --------------------------------------------------------------------------- #
def git(*args: str, cwd: Optional[Path] = None) -> str:
    """Run git and return stdout, raising on failure."""
    proc = subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=str(cwd or REPO), text=True, errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError("git %s failed (rc=%d): %s"
                           % (" ".join(args), proc.returncode,
                              (proc.stderr or "").strip()))
    return (proc.stdout or "").strip()


def git_try(*args: str, cwd: Optional[Path] = None,
            strip: bool = True) -> Tuple[int, str]:
    """Run git, returning (rc, combined output). Never raises.

    `strip=False` exists for `git status --porcelain`, whose first field is a
    fixed-width two-column XY code followed by a space. Stripping the output
    would eat that leading space and shift every path left by one character --
    'M packages/app.py' would be read as 'ackages/app.py'. Silent, and it would
    have mis-reported a dirty file as a path that does not exist.
    """
    proc = subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=str(cwd or REPO), text=True, errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = proc.stdout or ""
    return proc.returncode, (out.strip() if strip else out)


def resolve_commit(rev: str, cwd: Optional[Path] = None) -> Optional[str]:
    """Resolve rev to a full commit sha, or None if it does not resolve."""
    if not rev:
        return None
    rc, out = git_try("rev-parse", "--verify", "--quiet", "%s^{commit}" % rev,
                      cwd=cwd)
    return out if rc == 0 and SHA_RE.match(out) else None


def is_ancestor(older: str, newer: str, cwd: Optional[Path] = None) -> bool:
    return git_try("merge-base", "--is-ancestor", older, newer,
                   cwd=cwd)[0] == 0


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- #
# path classification
# --------------------------------------------------------------------------- #
def _norm(path: str) -> str:
    p = str(path).replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/")


def _glob_match(pattern: str, path: str) -> bool:
    """Segment-aware glob: '*' stays inside a segment, '**' crosses segments."""
    pat = pattern.split("/")
    seg = path.split("/")

    def match(pi: int, si: int) -> bool:
        while pi < len(pat):
            p = pat[pi]
            if p == "**":
                if pi == len(pat) - 1:
                    return True
                return any(match(pi + 1, k) for k in range(si, len(seg) + 1))
            if si >= len(seg):
                return False
            rx = p.replace(".", r"\.").replace("*", "[^/]*")
            if not re.fullmatch(rx, seg[si]):
                return False
            pi += 1
            si += 1
        return si == len(seg)

    return match(0, 0)


def _base(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _dirs(path: str) -> List[str]:
    parts = path.split("/")
    return parts[:-1]


#: Whole directories that are product, whatever is inside them.
PRODUCT_DIRS: Tuple[str, ...] = (
    "apps", "packages", "config",
    # A2 names scripts/runtime-critical/**. This is deliberately all of scripts/
    # -- see the module docstring. Same for the rest of these: they are the code
    # and the gates, and a release that certifies product while excluding the
    # tools that certified it certifies nothing.
    "scripts", "tools", "tests", "facades", "fixtures", "integrations",
    "adapters", "policies", "evals", "_bootstrap", "jev-cascade",
)

#: Directory names that mean "this is runtime/adapter/backend product source"
#: wherever they appear as a path segment.
PRODUCT_SEGMENTS: Tuple[str, ...] = (
    "runtime", "launcher", "mission", "missions", "adapter", "adapters",
    "backend", "frontend", "migrations", "schema",
)

#: Lockfiles / package manifests / Python manifests -- product wherever they are,
#: because a dependency that moves after the freeze invalidates every byte
#: certified at the freeze.
MANIFEST_NAMES: Tuple[str, ...] = (
    "package.json", "package-lock.json", "npm-shrinkwrap.json",
    "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "bun.lock",
    "pyproject.toml", "setup.py", "setup.cfg", "pipfile", "requirements.txt",
    "constraints.txt", "poetry.lock", "uv.lock", "pdm.lock",
)

#: Build / test / lint configuration -- product.
BUILD_CONFIG_NAMES: Tuple[str, ...] = (
    "vite.config.ts", "vite.config.js", "vitest.config.ts", "vitest.config.js",
    "rollup.config.js", "rollup.config.ts", "webpack.config.js",
    "tsconfig.json", "jsconfig.json", "tailwind.config.js",
    "tailwind.config.ts", "postcss.config.js", "babel.config.js",
    "eslint.config.js", "eslint.config.mjs", "jest.config.js",
    "playwright.config.ts", "next.config.js", "nuxt.config.ts",
    "dockerfile", "makefile", "alembic.ini", "tox.ini", "pytest.ini",
)

#: Database schema / migrations -- product.
SCHEMA_NAMES: Tuple[str, ...] = ("schemas.py", "schema.py", "schema.sql")

#: Extensions that are schema or migration content.
SCHEMA_SUFFIXES: Tuple[str, ...] = (".sql",)

#: Whole directories that are evidence and nothing else.
#:
#: The first four are directive A2 verbatim. `artifacts/acceptance/**` and
#: `docs/release/**` are added so this classifier CANNOT DISAGREE with
#: `jcc_release_manifest.RELEASE_METADATA_ALLOW`, which is the engine the v1.3
#: manifest is built and --checkED with. The failure this avoids is concrete:
#: without them, a commit that only refreshed `artifacts/acceptance/
#: project_run_gate_result.json` would be "no product drift" here and
#: "PRODUCT CODE CHANGED" in `manifest --check`, and the tool reporting the
#: release clean would be the one that is wrong. Both lists must move together.
EVIDENCE_DIRS: Tuple[str, ...] = (
    "docs/v1_3", "artifacts/release", "artifacts/evidence", "reports",
    "artifacts/acceptance", "docs/release",
)

#: META: not product BYTES, but not evidence either -- repository mechanics whose
#: bytes change what a checkout contains, so they must never be waved through as
#: "just documentation". Adjudicated as its own class by the release owner.
#:
#: THE CLASSIFICATION IS NOT THE SAME AS THE FREEZE BEHAVIOUR, and conflating the
#: two would be the bug here. `.gitignore` and `.gitattributes` carry no runtime
#: byte, so calling them "product code" would be false; but a change to
#: `.gitignore` can ADD OR REMOVE tracked product files, which moves the product
#: surface without touching a single product byte. So `classify()` labels them
#: META while :func:`is_product` still counts them as product for drift purposes
#: (see `META_COUNTS_AS_PRODUCT_FOR_FREEZE`). The label answers "what is this";
#: the freeze rule answers "does changing it move the product".
META_FILES: Tuple[str, ...] = (".gitattributes", ".gitignore")

#: META is a distinct LABEL, never a drift exemption. Deliberate, and the reason
#: is in the META_FILES comment: `.gitignore` edits change WHICH product files
#: exist. If META were exempt, editing .gitignore to un-ignore a product
#: directory would let product bytes change without moving the freeze -- the exact
#: "back door" the release owner named when adjudicating this rule.
META_COUNTS_AS_PRODUCT_FOR_FREEZE = True

#: PRODUCT despite living under ``artifacts/``. Adjudicated explicitly, and the
#: reason is the whole point: this is EXECUTABLE GATE CODE. A change to it changes
#: what the freeze gate itself accepts, so classifying it as evidence would mean
#: "you may rewrite the referee without moving the freeze". It must NEVER be
#: evidence, and it is listed here so that intent is explicit rather than
#: accidental (a future reader adding `artifacts/**` to EVIDENCE_DIRS would
#: otherwise silently un-pin the gate).
GATE_CODE_PATHS: Tuple[str, ...] = ("artifacts/security/freeze_check.py",)

#: ADRs are the architecture's own record and are adjudicated PRODUCT: they are
#: the contract the product is built against, not run evidence.
PRODUCT_DOC_DIRS: Tuple[str, ...] = ("docs/ADR",)

#: Named delivery checklists are evidence: they record what was handed over.
#: Matched on basename anywhere, since a checklist is a file, not a subtree.
EVIDENCE_FILES: Tuple[str, ...] = (
    "final_delivery.md", "runbook.md", "rollback.md",
    "final_activation_checklist.md",
)


def _is_manifest_name(base: str) -> bool:
    if base.lower() in MANIFEST_NAMES:
        return True
    # requirements*.txt / constraints*.txt / tsconfig*.json
    if re.fullmatch(r"(requirements|constraints)[^/]*\.txt", base, re.I):
        return True
    if re.fullmatch(r"tsconfig[^/]*\.json", base, re.I):
        return True
    return False


def _is_build_config(base: str) -> bool:
    low = base.lower()
    if low in BUILD_CONFIG_NAMES:
        return True
    if re.fullmatch(r"(vite|vitest|rollup|webpack|tailwind|postcss|babel|jest|"
                    r"playwright|next|nuxt|eslint)\.config\.[jt]s", low):
        return True
    if re.fullmatch(r"\.eslintrc(\.[a-z]+)?", low):
        return True
    if low.startswith("dockerfile"):
        return True
    if re.fullmatch(r"docker-compose[^/]*\.ya?ml", low):
        return True
    return False


def classify(path: str) -> Tuple[str, str]:
    """Return (PRODUCT|EVIDENCE, rule_name). PRODUCT rules are checked first."""
    p = _norm(path)
    if not p:
        # An empty path is not evidence of anything.
        return "PRODUCT", "EMPTY_PATH_FAIL_CLOSED"

    # ---- PRODUCT rules, in order. First match wins. ----
    dirs = _dirs(p)
    for top in PRODUCT_DIRS:
        if dirs and dirs[0] == top:
            return "PRODUCT", "PRODUCT_DIR:%s/" % top

    for seg in PRODUCT_SEGMENTS:
        if seg in dirs:
            return "PRODUCT", "PRODUCT_SEGMENT:%s/" % seg

    base = _base(p)
    if _is_manifest_name(base):
        return "PRODUCT", "PRODUCT_MANIFEST:%s" % base
    if _is_build_config(base):
        return "PRODUCT", "PRODUCT_BUILD_CONFIG:%s" % base
    if base.lower() in SCHEMA_NAMES:
        return "PRODUCT", "PRODUCT_SCHEMA:%s" % base
    if base.lower().endswith(SCHEMA_SUFFIXES):
        return "PRODUCT", "PRODUCT_SCHEMA_SUFFIX:%s" % base

    # Gate code that lives under artifacts/ is PRODUCT, and this check is
    # deliberately placed ABOVE the evidence rules so no future broad
    # `artifacts/**` evidence rule can silently un-pin the freeze gate.
    for gate in GATE_CODE_PATHS:
        if p == gate or _glob_match(gate, p):
            return "PRODUCT", "PRODUCT_GATE_CODE:%s" % gate

    for pd in PRODUCT_DOC_DIRS:
        if _glob_match(pd, p) or _glob_match(pd + "/**", p):
            return "PRODUCT", "PRODUCT_DOC_DIR:%s/**" % pd

    # ---- META: labelled, but still counted as product for the freeze. ----
    if base in META_FILES and not dirs:
        return "META", "META_REPO_MECHANICS:%s" % base

    # ---- EVIDENCE rules. Only reachable when no PRODUCT rule matched. ----
    for ev in EVIDENCE_DIRS:
        if _glob_match(ev, p) or _glob_match(ev + "/**", p):
            return "EVIDENCE", "EVIDENCE_DIR:%s/**" % ev
    low = _base(p).lower()
    if low in EVIDENCE_FILES:
        return "EVIDENCE", "EVIDENCE_FILE:%s" % _base(p)

    # ---- UNKNOWN => PRODUCT. Fail closed. ----
    return "PRODUCT", "UNKNOWN_FAIL_CLOSED"


def is_product(path: str) -> bool:
    """True when a change to ``path`` must move the product freeze.

    META counts here. See :data:`META_COUNTS_AS_PRODUCT_FOR_FREEZE` for why the
    label and the drift rule deliberately differ.
    """
    return classify(path)[0] in ("PRODUCT", "META")


# --------------------------------------------------------------------------- #
# commit walking
# --------------------------------------------------------------------------- #
def commits_with_paths(rev: str, cwd: Optional[Path] = None
                       ) -> List[Tuple[str, List[str]]]:
    """[(commit_sha, [changed paths])] newest first, in ONE git call.

    Uses a NUL-free \\x01 marker on the commit line so a file whose name happens
    to be 40 hex characters cannot be mistaken for a commit boundary.
    """
    rc, out = git_try("log", "--format=format:%x01%H", "--name-only",
                      "--no-renames", rev, cwd=cwd)
    if rc != 0:
        return []
    commits: List[Tuple[str, List[str]]] = []
    cur: Optional[str] = None
    paths: List[str] = []
    for line in out.splitlines():
        if line.startswith("\x01"):
            if cur is not None:
                commits.append((cur, paths))
            cur = line[1:].strip()
            paths = []
        elif line.strip():
            paths.append(_norm(line.strip()))
    if cur is not None:
        commits.append((cur, paths))
    return commits


def last_product_commit(rev: str, cwd: Optional[Path] = None
                        ) -> Tuple[Optional[str], List[str]]:
    """Newest commit at/under rev that touched a PRODUCT path, and those paths."""
    for sha, paths in commits_with_paths(rev, cwd=cwd):
        touched = sorted({p for p in paths if is_product(p)})
        if touched:
            return sha, touched
    return None, []


def changed_paths(base: str, head: str, cwd: Optional[Path] = None) -> List[str]:
    rc, out = git_try("diff", "--name-only", "%s..%s" % (base, head), cwd=cwd)
    if rc != 0:
        return []
    return [_norm(ln) for ln in out.splitlines() if ln.strip()]


def product_changes_between(base: str, head: str, cwd: Optional[Path] = None
                            ) -> List[Dict[str, Any]]:
    """Every path changed in base..head THAT MUST MOVE THE FREEZE.

    The filter is :func:`is_product`, NOT the ``classify()`` label. Those two
    differ for META on purpose, and using the label here was a real bug this
    suite caught: a META path (``.gitignore``) came back as "no drift", so a
    commit that changed WHICH product files are tracked would have left the
    freeze where it was -- the exact hole META is documented as guarding. One
    predicate decides drift everywhere; the label only describes what the path is.
    """
    out: List[Dict[str, Any]] = []
    for path in changed_paths(base, head, cwd=cwd):
        if not is_product(path):
            continue
        kind, rule = classify(path)
        rc, log = git_try("log", "--format=%H", "-1", "%s..%s" % (base, head),
                          "--", path, cwd=cwd)
        out.append({
            "path": path,
            "classification": kind,
            "rule": rule,
            "last_changed_commit": log.splitlines()[0] if rc == 0 and log else "",
        })
    return sorted(out, key=lambda e: e["path"])


def worktree_state(cwd: Optional[Path] = None
                   ) -> Tuple[str, List[str], List[str], List[str]]:
    """(CLEAN|DIRTY, all dirty paths, dirty PRODUCT paths, dirty EVIDENCE paths)."""
    rc, out = git_try("status", "--porcelain", "-uall", cwd=cwd, strip=False)
    if rc != 0:
        return "UNKNOWN", [], [], []
    dirty: List[str] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        # Porcelain v1: two status columns, then one space, then the path.
        entry = line[3:] if len(line) > 3 else line.strip()
        # rename/copy entries read "old -> new"; the new name is the live path.
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1]
        dirty.append(_norm(entry.strip().strip('"')))
    dirty = sorted(set(p for p in dirty if p))
    # Same predicate as the drift check, for the same reason: a dirty META path
    # is as freeze-relevant as a dirty product path.
    prod = [p for p in dirty if is_product(p)]
    evid = [p for p in dirty if classify(p)[0] == "EVIDENCE"]
    return ("CLEAN" if not dirty else "DIRTY"), dirty, prod, evid


def current_branch(cwd: Optional[Path] = None) -> str:
    rc, out = git_try("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd)
    return out if rc == 0 else "UNKNOWN"


def current_tag(cwd: Optional[Path] = None) -> Optional[str]:
    rc, out = git_try("describe", "--tags", "--exact-match", cwd=cwd)
    return out if rc == 0 else None


# --------------------------------------------------------------------------- #
# the truth document
# --------------------------------------------------------------------------- #
def _load_freeze_from_file(path: Path) -> Tuple[Optional[str], str]:
    """Read a freeze sha out of a freeze DOCUMENT.

    Note this is only ever a CROSS-CHECK on a git-verified value: the sha is
    resolved and then proven to exist in this repository before use. A report
    cannot invent a commit here, because a commit it invented would not resolve.
    """
    if not path.is_file():
        return None, "FREEZE_DOCUMENT_ABSENT:%s" % path.name
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, "FREEZE_DOCUMENT_UNREADABLE:%s" % exc
    if not isinstance(doc, dict):
        return None, "FREEZE_DOCUMENT_NOT_AN_OBJECT"
    for key in ("product_freeze_sha", "freeze_sha", "product_freeze_commit",
                "sha", "head"):
        val = doc.get(key)
        if isinstance(val, str) and val.strip():
            resolved = resolve_commit(val.strip())
            if resolved:
                return resolved, "FREEZE_DOCUMENT:%s" % key
            return None, ("FREEZE_DOCUMENT_SHA_DOES_NOT_RESOLVE_IN_GIT:%s=%s"
                          % (key, val.strip()[:16]))
    return None, "FREEZE_DOCUMENT_HAS_NO_FREEZE_FIELD"


def manifest_status() -> Dict[str, Any]:
    """Status of the v1.3 release manifest, via the engine that owns it.

    Delegated rather than reimplemented: MANIFEST verdicts must come from the
    same `check_manifest` the release is certified with, or this tool would be a
    second, weaker opinion about the same artifact.
    """
    man = REPO / "artifacts" / "release" / "v1.3_product" / "V1_3_RELEASE_MANIFEST.json"
    side = man.with_suffix(".sha256")
    if not man.is_file():
        return {"status": "ABSENT", "path": str(man),
                "detail": "no v1.3 release manifest on disk"}
    detail: List[str] = []
    try:
        doc = json.loads(man.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"status": "UNREADABLE", "path": str(man), "detail": str(exc)}

    if side.is_file():
        want = side.read_text(encoding="utf-8").split()[0] if side.read_text(
            encoding="utf-8").split() else ""
        got = sha256_bytes(man.read_bytes())
        if want != got:
            detail.append("sidecar digest mismatch (sidecar=%s actual=%s)"
                          % (want[:12], got[:12]))
    else:
        detail.append("no sidecar -- manifest cannot be integrity-checked")

    try:
        sys.path.insert(0, str(REPO / "tools"))
        import jcc_release_manifest as eng  # noqa: E402
        problems, notes = eng.check_manifest(doc, require_tag=False)
    except Exception as exc:  # noqa: BLE001 -- report, never crash the truth
        return {"status": "ENGINE_UNAVAILABLE", "path": str(man),
                "detail": "cannot run jcc_release_manifest.check_manifest: %s"
                          % exc}

    if problems:
        return {"status": "PROBLEMS", "path": str(man),
                "problems": problems, "detail": "; ".join(detail)}
    if detail:
        return {"status": "PROBLEMS", "path": str(man),
                "problems": detail, "detail": ""}
    return {"status": "CLEAN", "path": str(man), "notes": notes,
            "detail": "; ".join(detail)}


def build_truth(freeze_arg: Optional[str] = None,
                freeze_file: Optional[Path] = None) -> Dict[str, Any]:
    """Re-derive the whole release truth from git. Reads nothing else."""
    problems: List[str] = []
    notes: List[str] = []

    head = resolve_commit("HEAD")
    if head is None:
        return {
            "schema": SCHEMA, "generated_by": GENERATED_BY,
            "generated_at": now_utc(),
            "classification_version": CLASSIFICATION_VERSION,
            "sha_source": "GIT_MACHINE_COMPUTED",
            "verdict": "FAIL",
            "problems": ["HEAD does not resolve to a commit -- not a git "
                         "repository?"],
            "notes": [],
        }

    branch = current_branch()
    last_prod, last_prod_paths = last_product_commit(head)

    # --- which freeze is authoritative, and where the number came from ------
    freeze_sha: Optional[str] = None
    freeze_source = "DERIVED_LAST_PRODUCT_COMMIT"
    if freeze_arg:
        freeze_sha = resolve_commit(freeze_arg)
        if freeze_sha is None:
            problems.append("--freeze %r does not resolve to a commit"
                            % freeze_arg)
            freeze_source = "ARG_UNRESOLVED"
        else:
            freeze_source = "ARG"
    elif freeze_file is not None:
        freeze_sha, why = _load_freeze_from_file(freeze_file)
        freeze_source = why
        if freeze_sha is None:
            notes.append("freeze document not usable (%s); falling back to the "
                         "git-derived last product commit" % why)
            freeze_sha = None

    if freeze_sha is None:
        freeze_sha = last_prod
        freeze_source = "DERIVED_LAST_PRODUCT_COMMIT"
        if freeze_sha is None:
            problems.append("no PRODUCT commit found in history -- cannot "
                            "establish a product freeze")

    # --- product drift after the freeze -------------------------------------
    drift: List[Dict[str, Any]] = []
    if freeze_sha and freeze_sha != head:
        if not is_ancestor(freeze_sha, head):
            problems.append("product_freeze_sha %s is NOT an ancestor of HEAD %s"
                            % (freeze_sha[:12], head[:12]))
        drift = product_changes_between(freeze_sha, head)
        if drift:
            problems.append(
                "PRODUCT CODE CHANGED after the freeze: %d path(s) in %s..%s "
                "(e.g. %s) -- the freeze is INVALID, earlier evidence cannot be "
                "reused" % (len(drift), freeze_sha[:12], head[:12],
                            drift[0]["path"]))
    elif freeze_sha and freeze_sha == head:
        notes.append("product freeze == HEAD; nothing has been committed since")

    # --- worktree -----------------------------------------------------------
    wt, dirty, dirty_prod, dirty_evid = worktree_state()
    if dirty_prod:
        problems.append(
            "dirty PRODUCT file(s) in the worktree: %s -- uncommitted product "
            "bytes cannot be part of any freeze" % ", ".join(dirty_prod[:8]))

    # --- manifest -----------------------------------------------------------
    man = manifest_status()

    doc: Dict[str, Any] = {
        "schema": SCHEMA,
        "generated_at": now_utc(),
        "generated_by": GENERATED_BY,
        "repo_root": str(REPO),
        "sha_source": "GIT_MACHINE_COMPUTED",

        # --- the required fields ---
        "branch": branch,
        "head": head,
        "last_product_commit": last_prod,
        "product_freeze_sha": freeze_sha,
        "evidence_head": head,
        "product_changes_after_freeze": drift,
        "worktree": wt,
        "manifest_status": man.get("status", "UNKNOWN"),
        "classification_version": CLASSIFICATION_VERSION,

        # --- provenance of the freeze number ---
        "product_freeze_source": freeze_source,
        "last_product_commit_paths": last_prod_paths,
        "tag_head": current_tag(),

        # --- worktree detail ---
        "dirty_paths": dirty,
        "dirty_product_paths": dirty_prod,
        "dirty_evidence_paths": dirty_evid,

        # --- manifest detail ---
        "manifest_path": man.get("path"),
        "manifest_problems": man.get("problems", []),
        "manifest_notes": man.get("notes", []),

        # --- the rules, recorded so a verdict can be re-derived by hand ---
        "classification_rules": {
            "product_dirs": list(PRODUCT_DIRS),
            "product_segments": list(PRODUCT_SEGMENTS),
            "manifest_names": list(MANIFEST_NAMES),
            "build_config_names": list(BUILD_CONFIG_NAMES),
            "product_doc_dirs": list(PRODUCT_DOC_DIRS),
            "gate_code_paths": list(GATE_CODE_PATHS),
            "meta_files": list(META_FILES),
            "meta_counts_as_product_for_freeze": META_COUNTS_AS_PRODUCT_FOR_FREEZE,
            "evidence_dirs": list(EVIDENCE_DIRS),
            "evidence_files": list(EVIDENCE_FILES),
            "unknown": "PRODUCT (fail-closed)",
        },
    }
    doc["verdict"] = "PASS" if not problems else "FAIL"
    doc["problems"] = problems
    doc["notes"] = notes
    return doc


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# emit / check
# --------------------------------------------------------------------------- #
def serialise(doc: Dict[str, Any]) -> bytes:
    return (json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False)
            + "\n").encode("utf-8")


def emit(doc: Dict[str, Any], out_path: Path = OUT,
         sidecar: Path = SIDECAR) -> int:
    body = serialise(doc)
    if serialise(doc) != body:
        raise SystemExit("non-deterministic serialisation; refusing to emit")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(body)
    sidecar.write_bytes(("%s  %s\n" % (sha256_bytes(body), out_path.name))
                        .encode("ascii"))
    print("wrote %s (%d bytes, sha256 %s)"
          % (out_path.name, len(body), sha256_bytes(body)))
    _print_summary(doc)
    return 0 if doc.get("verdict") == "PASS" else 1


def _print_summary(doc: Dict[str, Any]) -> None:
    print("  branch=%s  head=%s  worktree=%s"
          % (doc.get("branch"), str(doc.get("head"))[:12], doc.get("worktree")))
    print("  last_product_commit=%s  product_freeze_sha=%s (%s)"
          % (str(doc.get("last_product_commit"))[:12],
             str(doc.get("product_freeze_sha"))[:12],
             doc.get("product_freeze_source")))
    print("  evidence_head=%s  manifest_status=%s  classification_version=%s"
          % (str(doc.get("evidence_head"))[:12], doc.get("manifest_status"),
             doc.get("classification_version")))
    drift = doc.get("product_changes_after_freeze") or []
    print("  product_changes_after_freeze=%d" % len(drift))
    for n in doc.get("notes") or []:
        print("  note: %s" % n)
    for p in doc.get("problems") or []:
        print("  PROBLEM: %s" % p)


def check(freeze_arg: Optional[str] = None,
          freeze_file: Optional[Path] = None) -> int:
    """Re-derive and compare. Writes nothing, ever."""
    if not OUT.is_file():
        print("CHECK: no release truth document at %s" % OUT)
        return 1
    try:
        stored = json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print("CHECK: stored truth is unreadable: %s" % exc)
        return 1

    fresh = build_truth(freeze_arg, freeze_file)

    problems: List[str] = list(fresh.get("problems") or [])
    # Fields whose value legitimately moves: evidence head and the freeze source.
    drift_fields = ("generated_at", "evidence_head", "product_freeze_source",
                    "notes")
    for key in sorted(set(stored) | set(fresh)):
        if key in drift_fields:
            continue
        if stored.get(key) != fresh.get(key):
            problems.append("field %r differs: stored=%s fresh=%s"
                            % (key, _short(stored.get(key)),
                               _short(fresh.get(key))))
    if SIDECAR.is_file():
        text = SIDECAR.read_text(encoding="utf-8").split()
        want = text[0] if text else ""
        got = sha256_bytes(OUT.read_bytes())
        if want != got:
            problems.append("sidecar digest mismatch (sidecar=%s actual=%s)"
                            % (want[:12], got[:12]))
    else:
        problems.append("no sidecar -- release truth cannot be "
                        "integrity-checked")

    _print_summary(fresh)
    if problems:
        print("CHECK STALE/FAILED (%d):" % len(problems))
        for p in problems:
            print("  - %s" % p)
        return 1
    print("CHECK CLEAN: head=%s freeze=%s worktree=%s verdict=PASS"
          % (str(fresh.get("head"))[:12], str(fresh.get("product_freeze_sha"))[:12],
             fresh.get("worktree")))
    return 0


def _short(v: Any, n: int = 60) -> str:
    s = repr(v)
    return s if len(s) <= n else s[:n] + "..."


# --------------------------------------------------------------------------- #
# selftest -- a fixture per case, each on its own real git repository
# --------------------------------------------------------------------------- #
class _Fixture:
    """A throwaway git repo.

    Real `git init` / `git commit`, not a mock: the four properties A2 requires
    are properties of git's behaviour, so a mock would be testing the mock.
    """

    def __init__(self, base: Path) -> None:
        self.dir = base
        self._seed = 0

    def git(self, *args: str) -> str:
        rc, out = git_try(*args, cwd=self.dir)
        if rc != 0:
            raise RuntimeError("fixture git %s -> %s" % (" ".join(args), out))
        return out

    def write(self, rel: str, content: str) -> None:
        p = self.dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8", newline="\n")

    def commit(self, msg: str) -> str:
        self._seed += 1
        # -c on the command line so the developer's global git identity is not
        # required and never leaks into the fixture's commits.
        self.git("-c", "user.name=JCC Fixture",
                 "-c", "user.email=fixture@jcc.invalid", "add", "-A")
        self.git("-c", "user.name=JCC Fixture",
                 "-c", "user.email=fixture@jcc.invalid",
                 "commit", "--allow-empty", "-m", msg, "--quiet")
        return self.git("rev-parse", "HEAD")

    def commit_paths(self, paths: Sequence[str], msg: str) -> str:
        for rel in paths:
            self.write(rel, "%s\n%s\n" % (rel, self._seed))
        return self.commit(msg)

    @property
    def head(self) -> str:
        return self.git("rev-parse", "HEAD")


def _eval_in_fixture(fx: _Fixture, freeze: Optional[str] = None
                     ) -> Tuple[List[str], Dict[str, Any]]:
    """Run the real classifier + real drift/wt logic inside a fixture repo.

    Deliberately calls the same functions the real path calls, with cwd=fx.dir,
    so the selftest cannot pass while the tool is broken.
    """
    head = resolve_commit("HEAD", cwd=fx.dir)
    last_prod, _paths = last_product_commit(head, cwd=fx.dir)
    freeze_sha = resolve_commit(freeze, cwd=fx.dir) if freeze else last_prod
    drift = product_changes_between(freeze_sha, head, cwd=fx.dir) \
        if freeze_sha else []
    wt, _all, prod, _ev = worktree_state(cwd=fx.dir)
    problems: List[str] = []
    if drift:
        problems.append("PRODUCT CODE CHANGED after the freeze: %d path(s)" % len(drift))
    if prod:
        problems.append("dirty PRODUCT file(s): %s" % ", ".join(prod))
    return problems, {
        "head": head, "last_product_commit": last_prod,
        "product_freeze_sha": freeze_sha, "drift": drift,
        "worktree": wt, "dirty_product_paths": prod,
    }


def selftest() -> int:
    base = Path(tempfile.mkdtemp(prefix="jcc_release_truth_selftest_"))
    results: List[Dict[str, Any]] = []
    try:
        # --- POSITIVE CONTROL ------------------------------------------------
        # A clean tree with only evidence on top of the freeze must PASS. Without
        # this, "reject everything" would also score 4/4 and prove nothing.
        fx = _Fixture(base / "positive")
        fx.dir.mkdir(parents=True)
        fx.git("init", "--quiet", "-b", "main", ".")
        fx.commit_paths(["packages/jcc_core/app.py"], "product: initial")
        fx.commit_paths(["artifacts/release/v1.3_final/RUN.json"],
                        "evidence: run record")
        problems, st = _eval_in_fixture(fx)
        results.append({
            "id": "POSITIVE_CONTROL_CLEAN_TREE_WITH_EVIDENCE_ON_TOP_PASSES",
            "ok": not problems and st["worktree"] == "CLEAN",
            "detail": "problems=%r worktree=%s drift=%d"
                      % (problems, st["worktree"], len(st["drift"])),
        })

        # --- A2.1 product change after freeze -> FAIL -------------------------
        fx = _Fixture(base / "drift")
        fx.dir.mkdir(parents=True)
        fx.git("init", "--quiet", "-b", "main", ".")
        fx.commit_paths(["packages/jcc_core/app.py"], "product: initial")
        freeze = fx.head
        fx.commit_paths(["packages/jcc_core/app.py"], "product: change after freeze")
        problems, st = _eval_in_fixture(fx, freeze)
        results.append({
            "id": "PRODUCT_CHANGE_AFTER_FREEZE_FAILS",
            "ok": (not problems) is False
                  and any("PRODUCT CODE CHANGED" in p for p in problems)
                  and len(st["drift"]) == 1
                  and st["drift"][0]["path"] == "packages/jcc_core/app.py",
            "detail": "problems=%r drift=%r"
                      % (problems, [d["path"] for d in st["drift"]]),
        })

        # --- A2.2 evidence-only commit -> freeze does not move ---------------
        fx = _Fixture(base / "evidence_only")
        fx.dir.mkdir(parents=True)
        fx.git("init", "--quiet", "-b", "main", ".")
        fx.commit_paths(["packages/jcc_core/app.py",
                         "apps/command_center_3d/package.json"], "product: initial")
        freeze = fx.head
        first_last_product = last_product_commit(fx.head, cwd=fx.dir)[0]
        fx.commit_paths(["artifacts/release/v1.3_final/SANDBOX.json",
                         "docs/v1_3/FINAL_DELIVERY.md",
                         "reports/summary.md"], "evidence: only")
        problems, st = _eval_in_fixture(fx, freeze)
        moved = last_product_commit(fx.head, cwd=fx.dir)[0]
        results.append({
            "id": "EVIDENCE_ONLY_COMMIT_DOES_NOT_MOVE_THE_FREEZE",
            "ok": (not problems) and moved == first_last_product == freeze
                  and st["last_product_commit"] == freeze and not st["drift"],
            "detail": "freeze=%s last_product_commit=%s drift=%d problems=%r"
                      % (freeze[:8], str(moved)[:8], len(st["drift"]), problems),
        })

        # --- A2.3 unknown path -> PRODUCT -------------------------------------
        # Each of these is a path no rule names. All must classify PRODUCT.
        unknowns = [
            "brandnewtopdir/thing.txt",
            "packages2/mod.py",
            "artifacts/evidence_v2/leak.json",
            "reports_v2/x.md",
            "totally/unexpected/blob.bin",
            "runtime/secrets/token.txt",
        ]
        wrong = [(p, classify(p)) for p in unknowns
                 if classify(p)[0] != "PRODUCT"]
        # A control pair, so the list above is known to be reachable.
        controls = {"docs/v1_3/A.md": "EVIDENCE",
                    "artifacts/release/x.json": "EVIDENCE",
                    "artifacts/evidence/x.json": "EVIDENCE",
                    "reports/x.md": "EVIDENCE",
                    "apps/x/src/main.ts": "PRODUCT",
                    "config/jcc-v1.3.yaml": "PRODUCT",
                    "scripts/jcc-v1.3.ps1": "PRODUCT",
                    "tools/release_truth.py": "PRODUCT",
                    "packages/jcc_core/requirements.txt": "PRODUCT",
                    "apps/command_center_3d/package-lock.json": "PRODUCT",
                    "apps/command_center_3d/vite.config.ts": "PRODUCT",
                    "packages/jcc_core/schemas.py": "PRODUCT"}
        ctl_wrong = [(p, classify(p)[0], want) for p, want in controls.items()
                     if classify(p)[0] != want]
        # An unknown path must also be refused as EVIDENCE by the drift check.
        fx = _Fixture(base / "unknown_is_product")
        fx.dir.mkdir(parents=True)
        fx.git("init", "--quiet", "-b", "main", ".")
        fx.commit_paths(["packages/jcc_core/app.py"], "product: initial")
        freeze = fx.head
        fx.commit_paths(["brandnewtopdir/thing.txt"], "unknown top-level dir")
        problems, st = _eval_in_fixture(fx, freeze)
        results.append({
            "id": "UNKNOWN_PATH_IS_PRODUCT",
            "ok": not wrong and not ctl_wrong and bool(problems)
                  and any(d["path"] == "brandnewtopdir/thing.txt"
                          for d in st["drift"]),
            "detail": "unknown misclassified=%r controls misclassified=%r "
                      "drift_detected=%r"
                      % (wrong, ctl_wrong,
                         [d["path"] for d in st["drift"]]),
        })

        # --- A2.4 dirty PRODUCT file -> FAIL ---------------------------------
        fx = _Fixture(base / "dirty_product")
        fx.dir.mkdir(parents=True)
        fx.git("init", "--quiet", "-b", "main", ".")
        fx.commit_paths(["packages/jcc_core/app.py"], "product: initial")
        freeze = fx.head
        fx.commit_paths(["artifacts/release/v1.3_final/RUN.json"],
                        "evidence: run record")
        fx.write("packages/jcc_core/app.py", "# edited, never committed\n")
        problems, st = _eval_in_fixture(fx, freeze)
        results.append({
            "id": "DIRTY_PRODUCT_FILE_FAILS",
            "ok": (not problems) is False
                  and any("dirty PRODUCT" in p for p in problems)
                  and st["dirty_product_paths"] == ["packages/jcc_core/app.py"]
                  and not st["drift"],
            "detail": "problems=%r dirty_product=%r committed_drift=%d "
                      "(uncommitted bytes must fail even with no commit drift)"
                      % (problems, st["dirty_product_paths"], len(st["drift"])),
        })

        # --- negative control: dirty EVIDENCE must NOT fail ------------------
        fx = _Fixture(base / "dirty_evidence")
        fx.dir.mkdir(parents=True)
        fx.git("init", "--quiet", "-b", "main", ".")
        fx.commit_paths(["packages/jcc_core/app.py"], "product: initial")
        fx.write("artifacts/release/v1.3_final/EXTRA.json", "{}\n")
        fx.write("docs/v1_3/SCRATCH.md", "scratch\n")
        problems, st = _eval_in_fixture(fx)
        results.append({
            "id": "DIRTY_EVIDENCE_FILE_DOES_NOT_FAIL_THE_FREEZE",
            "ok": not problems and st["worktree"] == "DIRTY"
                  and not st["dirty_product_paths"],
            "detail": "problems=%r worktree=%s dirty_product=%r "
                      "(worktree is honestly DIRTY; only PRODUCT dirtiness is a "
                      "freeze failure)"
                      % (problems, st["worktree"], st["dirty_product_paths"]),
        })

        # --- adjudicated classifications must be EXPLICIT, not accidental -----
        # The release owner adjudicated these four after the classifier was
        # shown hitting its fail-closed branch on them. Each gets a named rule
        # so the outcome is intentional. The two that are PRODUCT are the
        # important half: admitting them as evidence would have "fixed" the
        # selftest by weakening fail-closed, which is the trap.
        adjudicated = {
            # file/class: the class the release owner ruled
            "artifacts/security/freeze_check.py": "PRODUCT",
            "docs/ADR/ADR-0001-root-and-legacy-boundary.md": "PRODUCT",
            "docs/ADR/ADR-0002-four-plane-architecture.md": "PRODUCT",
            "FINAL_ACTIVATION_CHECKLIST.md": "EVIDENCE",
            ".gitattributes": "META",
            ".gitignore": "META",
        }
        wrong_class = [(p, classify(p)[0], want)
                       for p, want in adjudicated.items()
                       if classify(p)[0] != want]
        # Non-vacuity: gate code must NOT be evidence, and ADR must not be
        # evidence. If someone ever adds a broad `artifacts/**` evidence rule,
        # this is the assertion that catches the referee being rewritten.
        gate_not_evidence = (classify("artifacts/security/freeze_check.py")[0]
                             != "EVIDENCE")
        adr_not_evidence = (classify("docs/ADR/ADR-0002-four-plane-architecture.md")[0]
                            != "EVIDENCE")
        results.append({
            "id": "ADJUDICATED_PATHS_ARE_EXPLICITLY_CLASSIFIED",
            "ok": not wrong_class and gate_not_evidence and adr_not_evidence,
            "detail": "misclassified=%r gate_code_is_evidence=%s "
                      "adr_is_evidence=%s"
                      % (wrong_class, not gate_not_evidence, not adr_not_evidence),
        })

        # --- META is a LABEL, never a drift exemption ----------------------
        # .gitignore carries no runtime byte, so calling it "product code" would
        # be false -- but editing it can add or remove tracked product files,
        # which moves the product surface without touching a product byte. So it
        # is labelled META and STILL counts as product for the freeze. If META
        # were exempt, un-ignoring a product directory would change product
        # bytes without moving the freeze.
        meta_drift_ok = (classify(".gitignore")[0] == "META"
                         and is_product(".gitignore")
                         and is_product(".gitattributes"))
        fx = _Fixture(base / "meta_moves_freeze")
        fx.dir.mkdir(parents=True)
        fx.git("init", "--quiet", "-b", "main", ".")
        fx.commit_paths(["packages/jcc_core/app.py"], "product: initial")
        freeze = fx.head
        fx.write(".gitignore", "runtime/\n")
        fx.commit(".gitignore: ignore runtime (no product bytes change)")
        meta_problems, meta_state = _eval_in_fixture(fx, freeze)
        results.append({
            "id": "META_PATH_STILL_MOVES_THE_FREEZE",
            "ok": meta_drift_ok and bool(meta_problems)
                  and any(d["path"] == ".gitignore" for d in meta_state["drift"]),
            "detail": "is_product(.gitignore)=%s drift_detected=%r problems=%r "
                      "(labelled META, but a .gitignore edit can change WHICH "
                      "product files exist, so it must move the freeze)"
                      % (is_product(".gitignore"),
                         [d["path"] for d in meta_state["drift"]], meta_problems),
        })

        # --- the real repository must classify its own paths sanely ---------
        real_evidence = ["docs/v1_3/FINAL_DELIVERY.md",
                         "artifacts/release/v1.3_final/RELEASE_TRUTH.json",
                         "artifacts/release/v1.3_product/V1_3_RELEASE_MANIFEST.json"]
        real_product = ["packages/jcc_core/schemas.py",
                        "apps/command_center_3d/package.json",
                        "config/jcc-v1.3.yaml", "scripts/jcc-v1.3.ps1",
                        "tools/release_truth.py",
                        "apps/command_center_3d/vite.config.ts",
                        "packages/jcc_core/requirements.txt"]
        real_wrong = ([(p, classify(p)[0]) for p in real_evidence
                       if classify(p)[0] != "EVIDENCE"]
                      + [(p, classify(p)[0]) for p in real_product
                         if classify(p)[0] != "PRODUCT"])
        # Every tracked file must be classified BY A NAMED RULE. A path with no
        # rule is not wrong -- fail-closed makes it PRODUCT, which is the safe
        # answer -- but it is an UNREVIEWED one, and the point of this case is
        # that the set of such paths stays small, explicit, and visible instead
        # of growing silently as the tree grows.
        #
        # NOTE this does NOT assert "nothing hits the fail-closed branch". That
        # would be the wrong bar twice over: it would push toward adding broad
        # evidence rules (weakening fail-closed, the trap the release owner named
        # when adjudicating this), and it would be red for the ~20 reviewed
        # ``docs/{p3,p4,v1.1,v1.2}`` files that are correctly PRODUCT because
        # no rule names them. The adjudicated names above are the reviewed set.
        tracked = git("ls-files").splitlines()
        unreviewed = [p for p in tracked
                      if classify(p)[1] == "UNKNOWN_FAIL_CLOSED"]
        # Budget, not perfection: this fails only if the unreviewed set grows
        # well past the reviewed docs/ residue, which is what "a new unclassified
        # top-level directory appeared" looks like.
        budget = 30
        results.append({
            "id": "NO_TRACKED_PATH_IS_MISCLASSIFIED_AND_FALLBACK_SET_IS_BOUNDED",
            "ok": not real_wrong and len(unreviewed) <= budget,
            "detail": "misclassified=%r unreviewed_fail_closed=%d (budget %d) %r"
                      % (real_wrong, len(unreviewed), budget, unreviewed[:5]),
        })
    finally:
        shutil.rmtree(base, ignore_errors=True)

    passed = sum(1 for r in results if r["ok"])
    for r in results:
        print("  [%s] %s\n        %s"
              % ("ok  " if r["ok"] else "HOLE", r["id"], r["detail"]))
    print("\nSELFTEST %s %d/%d" % ("PASS" if passed == len(results) else "FAIL",
                                   passed, len(results)))
    return 0 if passed == len(results) else 1


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="JCC v1.3 final release truth (git-machine-computed)")
    ap.add_argument("--emit", action="store_true",
                    help="re-derive and write RELEASE_TRUTH.json + sidecar")
    ap.add_argument("--check", action="store_true",
                    help="re-derive and compare against the stored document; "
                         "never writes")
    ap.add_argument("--selftest", action="store_true",
                    help="prove the classifier and the freeze rules can fail")
    ap.add_argument("--freeze", default=None,
                    help="product freeze sha/ref to check against "
                         "(default: git-derived last product commit)")
    ap.add_argument("--freeze-file", default=None,
                    help="read the freeze sha from this JSON document "
                         "(resolved in git; a report cannot invent a commit)")
    ap.add_argument("--json", action="store_true",
                    help="also print the derived document to stdout")
    ns = ap.parse_args(argv)

    if ns.selftest:
        return selftest()

    freeze_file = Path(ns.freeze_file) if ns.freeze_file else None
    if freeze_file is not None and not freeze_file.is_absolute():
        freeze_file = REPO / freeze_file

    if ns.check and ns.emit:
        ap.error("--emit and --check are mutually exclusive")
    if not ns.emit and not ns.check:
        ap.print_help()
        return 2

    if ns.check:
        return check(ns.freeze, freeze_file)

    doc = build_truth(ns.freeze, freeze_file)
    rc = emit(doc)
    if ns.json:
        print(json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False))
    return rc


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        sys.exit(2)
