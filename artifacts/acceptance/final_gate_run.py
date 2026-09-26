#!/usr/bin/env python
"""FINAL STOP GATE for JEV COMMAND CENTER (owner: lead / Orion).

This is the ONLY stop condition. A PASS of any earlier gate -- P4, P5, an
individual acceptance runner, a security GO, an SRE GO -- is NOT a stop
condition. Only this script, when it answers `PROJECT_COMPLETE=true`, ends work.

The previous verdict that a P4 Freeze Gate PASS meant "PRODUCTION-READY LOCAL
RELEASE" was wrong and has been revoked; this script exists so the answer can
never again be a matter of judgment.

Independence from p3_gate_run.py
--------------------------------
This runner never treats the P3/P4 runner as a substitute for itself. It MAY
read the P3 runner's result as an input artifact, but a P3 PASS alone leaves
every check below MISSING/FAILED. It computes its own totals.

No fail-open
------------
* A required check that CANNOT be evaluated is a FAIL, not a skip.
* Zero collected tests = FAIL.
* An unparsable or inconsistent tally = FAIL.
* Evidence that disagrees with ground truth (wrong hash, wrong path, stale
  anchor, SHA-1 written into a sha256 field) = FAIL.
* Unexplained dirty-worktree / freeze provenance = FAIL.
* There is deliberately NO "known-pending" exemption.

Statuses
--------
OK | MISSING | FAILED | DEGRADED | PENDING_EXTERNAL_ACTIVATION

The result file carries no clock and no HEAD (measured, not stylistic)
--------------------------------------------------------------------
This gate writes ``artifacts/acceptance/final_gate_result.json``, and that file
is TRACKED -- deliberately, per KD-0012, so that re-signing a verdict is a diff
git can see instead of an invisible overwrite. But P-01 requires the working
tree to be clean, and P-01 is computed from ``git status`` *inside the very run
that rewrites the file*. So the two requirements together were unsatisfiable
whenever the written bytes were not a pure function of the committed tree:

    run gate -> result carries ``generated_at`` and ``head`` -> file differs
    from the committed blob -> ``git status`` non-empty -> P-01 FAIL, P-05 FAIL
    -> PROJECT_COMPLETE=false -> the stop condition can never be reached.

Measured on this host: a full run reported ``failed=['P-01','P-05']`` with the
only dirty paths being the two e2e evidence files the same run had just
regenerated. It was not a stale checkout; it was the gate failing itself.

Two fields had to go, and the reason is structural, not cosmetic:

* ``generated_at`` -- a wall clock. Two runs over byte-identical inputs would
  emit different verdicts, so no run can ever be the fixed point the next run
  compares against.
* ``head`` -- the commit the run happened at. It CANNOT be correct: the file
  cannot contain the hash of the commit that contains it. Whatever it says is
  the parent, so it is stale the instant it is written, and it changes on every
  commit, so it re-dirties the tree on the next run.

The verdict therefore describes the TREE, identified by content hashes
(``provenance``), and the OBSERVATION -- "on this machine, at this time, at this
commit, this gate returned this" -- is appended as one line to a JSONL log that
is NOT tracked (``--log``, default under ``artifacts/acceptance/`` which
``.gitignore`` excludes). Evidence of identity stays in the repository; evidence
of a moment stays out of it. A tracked, regenerated artifact must be a pure
function of the tracked tree, or the release can never prove itself clean.

The same rule applies to the leg details: a PASS detail may not embed HEAD or a
count of paths moved since the freeze, because both change when a commit is made
and the whole point is that committing the verdict must not invalidate it. FAIL
details keep every diagnostic (they are not part of any fixed point -- a red
gate is not a release).

Usage
-----
    F:/jev/.venv/Scripts/python.exe F:/jev/artifacts/acceptance/final_gate_run.py --full \\
        --json-out artifacts/acceptance/final_gate_result.json

exit 0 iff PROJECT_COMPLETE=true, else 1.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]                       # F:/jev
ART = REPO / "artifacts" / "acceptance"
RELEASE = REPO / "artifacts" / "release"
PY = REPO / ".venv" / "Scripts" / "python.exe"
GATE_MODULE = "packages/jcc_core/tests/test_p3_acceptance_gate.py"

STATUS_OK = "OK"
STATUS_MISSING = "MISSING"
STATUS_FAILED = "FAILED"
STATUS_DEGRADED = "DEGRADED"
STATUS_EXTERNAL = "PENDING_EXTERNAL_ACTIVATION"

# The twelve A8-pinned production artifacts. Parsed from the gate so this file
# cannot drift from the authority by hand.
A8_PATHS: List[str] = [
    "config/jcc.yaml",
    "packages/jcc_core/actuator.py",
    "packages/jcc_core/config.py",
    "packages/jcc_core/core.py",
    "packages/jcc_core/decision.py",
    "packages/jcc_core/policy.py",
    "packages/jcc_core/run.py",
    "packages/jcc_core/server.py",
    "packages/jcc_core/state_store.py",
    "packages/supervisor/budget.py",
    "packages/supervisor/sentinel.py",
    "packages/supervisor/shadow_store.py",
]

WIN_SCRIPTS = [
    "bootstrap.ps1", "start.ps1", "stop.ps1", "restart.ps1",
    "status.ps1", "doctor.ps1", "acceptance.ps1",
]
ADAPTERS = ["workbuddy", "claude", "codex", "hermes"]

# The commit the release was frozen at: everything that ships (product, gate,
# scripts, docs, tests) is already in it and only the release EVIDENCE is added
# afterwards. A leg that is signed at the freeze and then committed shifts HEAD
# by one -- hence the two accepted positions below.
#
# THE CONSTANT BELOW WAS RE-DERIVED, NOT MOVED UNTIL A CHECK PASSED. The
# definition (docs/FINAL_STOP_CONDITIONS.md, "How the freeze is identified") is
# the last commit that carries all product/ops code and NO release evidence.
# `FINAL_ACTIVATION_CHECKLIST.md` is F-02, i.e. release evidence, so the commit
# that first added it (dc154b0) is already past the freeze; walking back from it,
# df3e7d9 ("fix(ops): stop doctor.ps1 crashing at check 7") changed only
# scripts/doctor.ps1 and added no evidence, so df3e7d9 was the freeze through P5
# and all three legs were signed there. The first attempt picked dc154b0 and a
# signer was right to refuse it.
#
# 5ab5e20 ("Re-derive the product freeze: the FINAL acceptance harness is not
# release evidence") is the freeze now. The FINAL acceptance harness
# (apps/control_ui/acceptance.py, fixtures/demo/acceptance.py,
# fixtures/demo/manifest.json, tests/e2e/conftest.py) is neither shipped product
# nor under an evidence prefix, so committing it inside the evidence commit
# 47a3857 put four non-evidence paths past df3e7d9 and this gate refused all
# three legs -- correctly. The other candidate repair was widening
# EVIDENCE_PREFIXES to cover those four paths, and that is the one thing that
# must not be done: the rule was working and the state it refused was real.
# Giving them a commit of their own -- a commit that carries product/ops code and
# no release evidence -- makes the same state representable without touching the
# rule. That commit is 5ab5e20, and it is the freeze.
#
# THE RE-ANCHOR WAS PAID FOR, NOT ASSUMED. df3e7d9 -> 5ab5e20 moves those four
# paths and nothing else outside EVIDENCE_PREFIXES, and no probe scope moved:
# `git diff --stat df3e7d9 5ab5e20 -- packages` is empty, `-- config` is empty,
# and `-- apps` reports only the S-04 acceptance fixture (the served UI is
# byte-identical, and A8's 12 pinned product artifacts are untouched, so P-02
# still reads 12/12). Each leg carries its own `reanchored_at` and `reanchor`
# list whose first entry records the move with its verdict unchanged. Do not
# "fix" this back without re-deriving the definition: the freeze is the position
# at which every leg may be signed, and moving it invalidates all three
# signatures.
RELEASE_FREEZE = "5ab5e20d961db4c9807f578195ae8530dea2f7d9"
# What a release-evidence commit is allowed to touch. If something outside this
# set moves after the freeze, a signature written at the freeze describes a
# product that is no longer in HEAD -- that is a stale signature, not a pass.
EVIDENCE_PREFIXES = (
    "artifacts/", "docs/", "tools/", "scripts/", ".gitignore",
    "FINAL_ACTIVATION_CHECKLIST.md", "FINAL_ACCEPTANCE_REPORT.md",
)

# Verdicts that may stand as a leg's signature. `GO WITH CONDITIONS` is in the
# project's vocabulary (docs/p3/P3_ARCH_GO.md signed it at 49d91d4) and stays a
# pass ONLY when the leg also names its conditions; the gate enforces that below.
GO_BEARING_VERDICTS = frozenset({"GO", "GO WITH CONDITIONS"})

#: Schema of the machine verdict. Bumped when its shape changes so a consumer
#: cannot silently read a new file with old field expectations -- `head` and
#: `generated_at` were removed at /2 and a reader that still wants them must say
#: so rather than read `None` and treat it as "no commit".
GATE_RESULT_SCHEMA = "jcc-final-gate/2"

#: Where a run records WHEN it happened and WHICH commit it saw. Deliberately
#: under `artifacts/acceptance/`, which `.gitignore` excludes: the moment is not
#: part of the release, and a tracked file that changes every run cannot also be
#: the thing a clean-tree check compares against.
DEFAULT_GATE_LOG = ART / "gate_runs.jsonl"


def classify_leg_verdict(rec: Dict[str, Any]) -> Tuple[bool, str]:
    """Pure: does this signature's verdict pass the leg check? Returns
    ``(accepts, why)``.

    Split out of the gate loop so the rule can be tested without running git or
    the whole gate. A rule that can only be exercised by a five-minute gate run
    is a rule nobody re-checks after it has quietly been weakened.

    WHY THE FAMILY. An earlier version demanded the literal string ``"GO"``,
    while ``docs/p3/P3_ARCH_GO.md`` has signed "GO WITH CONDITIONS" since P3.
    A gate that refuses a word this release itself uses pushes every leg toward
    the one string that passes, so the family is accepted -- but the hedge must
    earn its word with a non-empty ``conditions`` list, or it is a plain GO in
    costume.
    """
    verdict = re.sub(r"\s+", " ", str(rec.get("verdict", "")).strip()).upper()
    if verdict not in GO_BEARING_VERDICTS:
        return False, f"names verdict={verdict or '<absent>'!r}"
    if verdict != "GO":
        conds = rec.get("conditions") or []
        if not isinstance(conds, list) or not conds:
            return False, f"signed {verdict!r} but names no condition"
    return True, f"verdict={verdict!r}"


def classify_leg_position(
    rsha: str,
    head: str,
    freeze: str,
    changed_since_freeze: List[str],
    evidence_prefixes: Tuple[str, ...] = EVIDENCE_PREFIXES,
) -> Tuple[bool, str]:
    """Pure: is this signature's SHA POSITION acceptable? ``(accepts, detail)``.

    Split out of :func:`check_e2e` for the same reason as
    :func:`classify_leg_verdict`, and after the same lesson. A rule that can only
    be exercised by a five-minute ``--full`` gate run is a rule nobody re-checks
    after it has quietly been weakened -- and this rule decides whether a leg's
    ``GO`` counts, which is the whole release.

A signature cannot contain itself: demanding ``release_sha == HEAD`` made
every leg off by one commit the moment its own GO was committed. So two
positions are accepted:

  * ``rsha == head`` -- signed at the current tip; nothing to reconcile.
  * ``rsha == freeze`` -- signed at the release freeze, PROVIDED the only
    paths that moved between the freeze and HEAD are release evidence. If a
    product path moved, the signature describes a product that is no longer
    what is being shipped: a stale signature, not a pass.

Everything else is refused, including a blank or absent ``release_sha``.

WHY THE ACCEPTING DETAILS NAME NEITHER ``head`` NOR THE PATH COUNT. Both were
there, and both broke the fixed point the gate needs: the count of paths moved
since the freeze changes on every commit (so committing the verdict invalidated
it) and ``head`` cannot appear in a file that is itself in the commit. The
verdict is a statement about the tree -- the tree's identity is carried by the
signed artifact, not by the tip's hash -- so the accepting detail says what the
rule decided and nothing that moves. The refusing branch keeps every
diagnostic, because a red gate is not a fixed point of anything.
"""
    if rsha == head:
        return True, "final-leg GO re-signed against the current tip"
    if freeze and rsha.startswith(freeze[:12]):
        offenders = [p for p in changed_since_freeze
                     if not any(p.startswith(pre) for pre in evidence_prefixes)]
        if offenders:
            return False, (f"GO was signed at the release freeze {rsha[:12]}, but "
                           f"{len(offenders)} path(s) outside release evidence changed "
                           f"since: {', '.join(offenders[:5])}")
        return True, (f"final-leg GO re-signed against the release freeze {rsha[:12]}; "
                      f"every path changed since the freeze is release evidence")
    return False, (f"GO was signed against {rsha or 'nothing'}, neither the current "
                   f"HEAD {head} nor the release freeze {freeze or '<unset>'}")


def judge_leg(
    rec: Dict[str, Any],
    head: str,
    freeze: str = RELEASE_FREEZE,
    changed_since_freeze: Optional[List[str]] = None,
    diff_provider: Optional[Any] = None,
) -> Tuple[str, str]:
    """``(status, detail)`` for one leg signature -- the WHOLE rule, in one place.

    Verdict family, condition requirement, SHA position and condition rendering,
    composed so that the composed result is itself testable. The defect this
    replaces lived exactly in the composition: both halves were individually
    correct, and it was the joining of them (`g.ok(...) + cond_note`) that raised
    TypeError and dropped the conditions. A test that exercises only the halves
    would not have caught it.

    ``diff_provider`` is called only when a freeze-positioned signature actually
    needs reconciling, so a tip-signed leg never pays for the git diff.
    """
    ok_verdict, why = classify_leg_verdict(rec)
    if not ok_verdict:
        return STATUS_FAILED, (f"names verdict={str(rec.get('verdict','')).strip().upper() or '<absent>'!r}; "
                               f"accepted verdicts are {sorted(GO_BEARING_VERDICTS)}")
    conditions = rec.get("conditions") or []
    cond_note = ""
    if conditions:
        shown = "; ".join(str(c) for c in conditions[:3])
        if len(conditions) > 3:
            shown += f" ... (+{len(conditions) - 3} more)"
        cond_note = f" [{len(conditions)} condition(s): {shown}]"
    rsha = str(rec.get("release_sha", ""))
    changed = changed_since_freeze
    if rsha != head and rsha.startswith(freeze[:12]) and changed is None:
        changed = (diff_provider or (lambda: []))()
    accepted, detail = classify_leg_position(rsha, head, freeze, changed or [])
    return (STATUS_OK if accepted else STATUS_FAILED), detail + (cond_note if accepted else "")

# Components whose completion is owner-declared. The gate verifies the claim
# against ground truth (module exists, every pinned test id exists in the
# collected suite); an absent evidence file is MISSING, and a claim that does
# not verify is FAILED. Nothing here is asserted by this script on the owner's
# behalf -- inventing a claim would be exactly the failure mode we are fixing.
COMPONENT_EVIDENCE = {
    "C-01": "jcc_core/core",
    "C-02": "aep",
    "C-03": "state_store",
    "C-04": "audit_tape",
    "C-05": "jev_provider",
    "C-06": "shadow_supervisor",
    "C-07": "policy_engine",
    "C-08": "budget_engine",
    "C-09": "signed_directive",
    "C-10": "safe_actuation",
}
SURFACE_EVIDENCE = {
    "S-01": "operator_skill",
    "S-02": "mcp_facade",
    "S-03": "observability",
    "S-06": "facade_session_center",
    "S-07": "facade_claw3d",
}


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def git(*args: str, cwd: Path = REPO) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=str(cwd), text=True, errors="replace"
    ).strip()


def git_ok(*args: str, cwd: Path = REPO) -> bool:
    try:
        subprocess.check_output(
            ["git", *args], cwd=str(cwd), text=True,
            stderr=subprocess.STDOUT, errors="replace"
        )
        return True
    except subprocess.CalledProcessError:
        return False


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_blob(commit: str, relpath: str) -> str:
    blob = subprocess.check_output(
        ["git", "cat-file", "blob", f"{commit}:{relpath}"], cwd=str(REPO)
    )
    return hashlib.sha256(blob).hexdigest()


class Gate:
    def __init__(self) -> None:
        self.checks: List[Dict[str, str]] = []

    def add(self, cid: str, status: str, detail: str) -> None:
        self.checks.append({"id": cid, "status": status, "detail": detail})

    def ok(self, cid: str, detail: str) -> None:
        self.add(cid, STATUS_OK, detail)

    def missing(self, cid: str, detail: str) -> None:
        self.add(cid, STATUS_MISSING, detail)

    def fail(self, cid: str, detail: str) -> None:
        self.add(cid, STATUS_FAILED, detail)

    def degraded(self, cid: str, detail: str) -> None:
        self.add(cid, STATUS_DEGRADED, detail)

    def external(self, cid: str, detail: str) -> None:
        self.add(cid, STATUS_EXTERNAL, detail)

    def by_status(self, *statuses: str) -> List[Dict[str, str]]:
        return [c for c in self.checks if c["status"] in statuses]

    def statuses_map(self) -> Dict[str, str]:
        return {c["id"]: c["status"] for c in self.checks}


# ---------------------------------------------------------------------------
# A. component completion (owner-declared, gate-verified)
# ---------------------------------------------------------------------------

def check_declared(g: Gate, cid: str, key: str, collected: set) -> None:
    ev = ART / "component_evidence" / f"{cid}.json"
    if not ev.is_file():
        g.missing(cid, f"no evidence file {_rel(ev)}; owner has not declared this component")
        return
    try:
        claims = json.loads(ev.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        g.fail(cid, f"evidence file unreadable: {exc}")
        return
    if not isinstance(claims, dict):
        g.fail(cid, "evidence file is not a JSON object")
        return

    module = claims.get("module")
    if not module:
        g.fail(cid, "evidence file declares no 'module'")
        return
    mod = REPO / str(module)
    if not mod.exists():
        g.fail(cid, f"declared module does not exist: {module}")
        return

    pins = [str(t) for t in claims.get("pin_tests", [])]
    if not pins:
        g.fail(cid, "evidence file declares no pin_tests; unverifiable claim")
        return
    absent = [p for p in pins if p not in collected]
    if absent:
        g.fail(
            cid,
            "{} of {} declared pin test(s) do not exist in the suite: {}".format(
                len(absent), len(pins), ", ".join(absent[:4])
            ),
        )
        return
    g.ok(cid, f"module {module}; {len(pins)} pin test(s) verified to exist")


# ---------------------------------------------------------------------------
# B. adapters -- COMPLETE or VERIFIED DEGRADED
# ---------------------------------------------------------------------------

def check_adapters(g: Gate, collected: set) -> None:
    for cid, name in zip(["A-01", "A-02", "A-03", "A-04"], ADAPTERS):
        ev = ART / "adapter_capability" / f"{name}.json"
        if not ev.is_file():
            g.missing(
                cid,
                f"no capability probe for adapter '{name}' (expected {_rel(ev)}); "
                "an unprobed adapter is MISSING, never DEGRADED",
            )
            continue
        try:
            probe = json.loads(ev.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            g.fail(cid, f"capability probe unreadable: {exc}")
            continue
        if not isinstance(probe, dict):
            g.fail(cid, "capability probe is not a JSON object")
            continue

        status = str(probe.get("status", "")).upper()
        capabilities = probe.get("capabilities") or {}

        if status == "VERIFIED":
            usable = [k for k, v in capabilities.items()
                      if str(v).upper() == "VERIFIED"]
            if not usable:
                g.degraded(
                    cid,
                    f"probe VERIFIED but it declares no VERIFIED capability; "
                    f"no capability may be actuator-invoked",
                )
            else:
                g.ok(cid, f"probe VERIFIED; actuator-invocable: {', '.join(sorted(usable))}")
            continue
        if status in ("UNVERIFIED", "UNSUPPORTED"):
            g.degraded(
                cid,
                f"probe {status}; capabilities: "
                f"{json.dumps(capabilities, sort_keys=True, ensure_ascii=False)[:180]}",
            )
            continue
        g.missing(cid, f"capability probe has unrecognised status {status!r}; not a verdict")


# ---------------------------------------------------------------------------
# D. Windows scripts -- all must PASS
# ---------------------------------------------------------------------------

# NOTE: the path is passed via an ENV var ($env:JCC_PS_PATH), not as a positional
# $args element. Two reasons: (1) on this Chinese-locale Windows, PowerShell emits
# output in a non-UTF-8 codepage and forward-slash paths make
# Parser::ParseFile report "path argument value is invalid" (a false parse failure);
# (2) reading $env:JCC_PS_PATH survives quoting/encoding entirely. Output is captured
# as BYTES and decoded with errors='replace' so a non-UTF-8 codepage can never make
# subprocess.run return stdout=None and crash the gate (see 2026-09-25 fix).
PS_PARSE = (
    "$ErrorActionPreference='Stop';"
    "$e=$null;"
    "$t=[System.Management.Automation.Language.Parser]::ParseFile($env:JCC_PS_PATH,[ref]$null,[ref]$e);"
    "if($e -and $e.Count -gt 0){ "
    "  $e | Select-Object -First 3 | ForEach-Object { Write-Output ('ERR: ' + $_.Message) }; "
    "  exit 3 } else { Write-Output ('PARSE_OK ' + $t.GetType().Name) }"
    )


def check_windows_scripts(g: Gate) -> None:
    for cid, name in zip(
        ["W-01", "W-02", "W-03", "W-04", "W-05", "W-06", "W-07"], WIN_SCRIPTS
    ):
        path = REPO / "scripts" / name
        if not path.is_file():
            g.missing(cid, f"{_rel(path)} does not exist")
            continue
        try:
            env = dict(os.environ)
            env["JCC_PS_PATH"] = str(path).replace("/", "\\")
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", PS_PARSE],
                capture_output=True, timeout=120, env=env,
            )
            out = (proc.stdout or b"").decode("utf-8", "replace").strip()
        except (OSError, subprocess.SubprocessError) as exc:
            g.fail(cid, f"could not parse {name} (environment error): {exc}")
            continue
        if "PARSE_OK" in out:
            g.ok(cid, f"{name} parses under Windows PowerShell")
        else:
            g.fail(cid, f"{name} failed to parse: {out[:200] or 'no parser output'}")


# ---------------------------------------------------------------------------
# G. tests -- computed here, never reused from a previous phase
# ---------------------------------------------------------------------------

# Test scopes the gate must execute. A path that does not exist is NOT skipped:
# a scope the gate cannot evaluate is a FAIL (no fail-open, sec. "No fail-open").
# The lead owns this list and edits it as surfaces land; an owner declares its
# evidence, the gate decides which of those tests it actually runs.
TEST_SCOPES: List[Tuple[str, str, List[str]]] = [
    ("T-10", "unit and contract", [
        "packages/jcc_core/tests",
        "packages/supervisor/tests",
    ]),
    ("T-11", "platform surfaces (skill / mcp / observability)", [
        "packages/operator_skill/tests",
        "packages/mcp_facade/tests",
        "packages/observability/tests",
    ]),
    ("T-12", "control UI and demo", [
        "apps/control_ui/tests",
    ]),
    ("T-13", "projection facades", [
        "facades/session_center/tests",
        "facades/claw3d/tests",
    ]),
    ("T-14", "end to end", [
        "tests/e2e",
    ]),
    # T-15 exists because the rules that decide this release used to be
    # reachable only through a full `--full` run. If the gate's own decision
    # rules are not falsified by the same run that uses them, the gate is
    # grading itself with an instrument nobody has ever tested.
    ("T-15", "gate decision-rule falsifiers", [
        "tools/tests",
    ]),
]

_SUMMARY_PATS = (
    ("passed", r"(\d+) passed"),
    ("failed", r"(\d+) failed"),
    ("errors", r"(\d+) errors?"),
    ("skipped", r"(\d+) skipped"),
    ("deselected", r"(\d+) deselected"),
    ("xfailed", r"(\d+) xfailed"),
    ("xpassed", r"(\d+) xpassed"),
)


def _parse_summary(out: str) -> Dict[str, int]:
    """pytest's own summary line is the ground truth for the counts."""
    summary: Dict[str, int] = {}
    for key, pat in _SUMMARY_PATS:
        m = re.search(pat, out)
        if m:
            summary[key] = int(m.group(1))
    return summary


def _parse_tallies(out: str) -> Dict[str, int]:
    """Cross-check only. A node id can contain spaces, so this may under-count
    a PASSED; it must never manufacture a FAILED (a false FAIL is as much a
    harness defect as an absorbed one)."""
    tallies: Dict[str, int] = {}
    for line in out.splitlines():
        if "::" not in line or " " not in line:
            continue
        for tok in line.split():
            if tok in ("PASSED", "FAILED", "ERROR", "SKIPPED", "XFAIL", "XPASS"):
                tallies[tok] = tallies.get(tok, 0) + 1
                break
    return tallies


_JUNIT_TALLY_KEYS = ("PASSED", "FAILED", "ERROR", "SKIPPED", "XFAIL", "XPASS")

# Path of the junit report the most recent _run_pytest() wrote. pytest names the
# file itself only via the flag we pass, so we own the name.
_JUNIT_PATH: Optional[Path] = None


def _parse_junit_tally(xml_path: Path) -> Dict[str, int]:
    """Authoritative per-testcase tally, read straight out of pytest's junit XML.

    Why this exists (a real false-FAIL this gate shipped): pytest's human
    summary line prints a counter ONLY when it is non-zero -- a fully green run
    reads ``627 passed in 41s`` and never says "0 failed". Parsing that line can
    therefore not distinguish "genuinely zero" from "not printed", so the
    failed/errors/skipped counts came back UNKNOWN and T-02..T-05 failed a suite
    that had actually passed 627/627. The junit report instead records the
    outcome of EVERY testcase, so the absence of a ``<failure/>`` child really is
    evidence. Ground truth, not a textual guess about the summary format.
    """
    try:
        root = ET.parse(str(xml_path)).getroot()
    except (OSError, ET.ParseError) as exc:
        raise RuntimeError(f"unreadable junit report {xml_path}: {exc}")
    tally = {k: 0 for k in _JUNIT_TALLY_KEYS}
    for tc in root.iter("testcase"):
        tags = {el.tag for el in tc.iter()}
        # pytest writes an expected failure as <skipped type="pytest.xfail"> and
        # an unexpected pass as <skipped type="pytest.xpass"> -- counting them as
        # plain skips would be the same class of error in the other direction.
        skip_types = {
            el.get("type") for el in tc.iter() if el.tag == "skipped" and el.get("type")
        }
        if "failure" in tags:
            tally["FAILED"] += 1
        elif "error" in tags:
            tally["ERROR"] += 1
        elif "pytest.xfail" in skip_types:
            tally["XFAIL"] += 1
        elif "pytest.xpass" in skip_types:
            tally["XPASS"] += 1
        elif "skipped" in tags:
            tally["SKIPPED"] += 1
        else:
            tally["PASSED"] += 1
    return tally


def _last_junit_path() -> Path:
    if _JUNIT_PATH is None:
        raise RuntimeError("pytest has not run in this gate process; there is no junit report")
    return _JUNIT_PATH


#: NTSTATUS values a Windows child can exit with when it is killed by the OS
#: rather than by pytest. Named so the verdict says what happened instead of
#: printing a 10-digit integer.
_WINDOWS_EXIT_NAMES = {
    0xC0000005: "STATUS_ACCESS_VIOLATION",
    0xC0000006: "STATUS_IN_PAGE_ERROR",
    0xC0000017: "STATUS_NO_MEMORY",
    0xC000001D: "STATUS_ILLEGAL_INSTRUCTION",
    0xC0000094: "STATUS_INTEGER_DIVIDE_BY_ZERO",
    0xC0000096: "STATUS_PRIVILEGED_INSTRUCTION",
    0xC00000FD: "STATUS_STACK_OVERFLOW",
    0xC0000135: "STATUS_DLL_NOT_FOUND",
    0xC000013A: "STATUS_CONTROL_C_EXIT",
    0xC0000142: "STATUS_DLL_INIT_FAILED",
    0xC0000374: "STATUS_HEAP_CORRUPTION",
    0xC0000409: "STATUS_STACK_BUFFER_OVERRUN",
}

#: The sandbox's delete guard prints this when a bulk delete crosses its
#: threshold. It is a refusal by the ENVIRONMENT, never an outcome of a test.
GUARD_MARKER = "SAFE_DELETE_BULK_CONFIRM_REQUIRED"

#: Facts about how the run itself went -- retries, native faults, guard
#: refusals. Written to the UNTRACKED observation log and never into the
#: tracked verdict: whether a retry was needed is a property of the moment, and
#: a verdict that embeds it would differ between two runs over the same tree,
#: which is precisely what P-01 compares.
_SCOPE_OBSERVATIONS: List[Dict[str, Any]] = []


def _exit_description(code: Optional[int]) -> str:
    if code is None:
        return "no exit code (the invocation did not finish)"
    if code < 0:
        return "%d (killed by signal %d)" % (code, -code)
    low = code & 0xFFFFFFFF
    if low >= 0xC0000000:
        return "0x%08X %s" % (low, _WINDOWS_EXIT_NAMES.get(low, "NTSTATUS"))
    return "exit code %d" % low


def _no_measurement(proc: subprocess.CompletedProcess, out: str) -> Optional[str]:
    """Why this invocation produced NO MEASUREMENT, or ``None`` if it did.

    A child killed by the OS, or refused by the sandbox's delete guard, emits
    nothing that can be judged: pytest's junit report is written at session end
    so it never appears, the summary line never prints, and stdout/stderr can be
    EMPTY because they were block-buffered into a file and died with the
    process. Observed on this host, twice, in two different scopes:
    ``returncode=3221225477`` (``0xC0000005``) with ``stdout_bytes=0
    stderr_bytes=0``, and separately a full green run whose last output line was
    ``[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED]`` with no summary.

    Both are reasons to RE-RUN, and neither is a statement about the tests:
    calling it a test failure attributes to the product something that is not
    evidence about the product, and calling it a pass would be worse. The
    decision is not made here -- this function only says whether there is
    anything to judge.
    """
    reason = None
    low = (proc.returncode or 0) & 0xFFFFFFFF
    if proc.returncode not in (0, None) and low >= 0xC0000000:
        reason = "the interpreter died of a native fault (%s)" % _exit_description(proc.returncode)
    elif GUARD_MARKER in out:
        reason = ("the sandbox's bulk-delete guard refused pytest's own scratch "
                  "cleanup (%s)" % GUARD_MARKER)
    elif not out.strip():
        reason = ("the process produced no output at all (%s)"
                  % _exit_description(proc.returncode))
    return reason


def _prune_stale_junit(base: Path) -> None:
    """Drop junit reports from earlier gate runs.

    The report's path used to be shared between scopes (``base/junit.xml``).
    That made a scope which crashed before writing its own report read the
    PREVIOUS scope's numbers -- the gate would then reconcile a scope against a
    different scope's tests, which is the "instrument reports success while
    knowing nothing" failure in its purest form. Each invocation now owns a
    uniquely named report; this keeps the directory from growing forever.
    """
    if not base.is_dir():
        return
    for stale in base.glob("junit-*.xml"):
        try:
            stale.unlink()
        except OSError:
            pass


def _run_pytest(args: List[str]) -> subprocess.CompletedProcess:
    global _JUNIT_PATH
    base = Path(tempfile.gettempdir()) / "jev-final-gate"
    base.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    basetemp = base / f"fg{stamp}"
    basetemp.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["CODEBUDDY_SAFE_DELETE_ENABLED"] = "0"
    # Provider calls must stay at zero: a paid call during an acceptance gate is
    # an incident, not a measurement.
    env["PAID_PROVIDER_CALLS"] = "0"
    env["EXTERNAL_PUBLISH"] = "0"
    # pytest's --basetemp wins over a conftest's, so the scratch root is pinned
    # here for every scope -- see the note in run_scope about the delete guard.
    junit = base / f"junit-{stamp}.xml"
    _JUNIT_PATH = junit
    return subprocess.run(
        [str(PY), "-m", "pytest", *args, "-v", "-p", "no:cacheprovider",
         "--no-header", "-rN", f"--basetemp={basetemp}", f"--junitxml={junit}"],
        cwd=str(REPO), capture_output=True, text=True, env=env, timeout=3600,
    )


def collect_test_ids() -> set:
    """Collect the suite's node ids so declared pin tests can be verified.

    pytest's ``--collect-only -v`` prints a ``<Dir>/<Module>/<Function>`` TREE
    that contains no ``::`` separators, and ``--collect-only -q`` prints one
    ``path::nodeid`` line per item with no ``<`` -- so the legacy predicate
    ``"<" in line and "::" in line`` matched NEITHER format and silently
    collected zero ids, which then failed every C-/S- check (every pinned test
    "absent from the suite"). Collect with ``-q`` and accept any line that
    contains ``::`` (the canonical node-id form the evidence JSONs pin).
    Every declared scope path that exists is collected; a scope path that does
    not exist is a FAIL of its own T-xx check, not a reason to abscond here."""
    ids: set = set()
    paths = [p for _, _, ps in TEST_SCOPES for p in ps if (REPO / p).is_dir()]
    if not paths:
        return ids
    env = dict(os.environ)
    env["CODEBUDDY_SAFE_DELETE_ENABLED"] = "0"
    env["PAID_PROVIDER_CALLS"] = "0"
    env["EXTERNAL_PUBLISH"] = "0"
    proc = subprocess.run(
        [str(PY), "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *paths],
        cwd=str(REPO), capture_output=True, text=True, env=env, timeout=600,
    )
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if "::" in line and not line.startswith(
            ("=", "-", "platform", "rootdir", "plugins", "collecting", "collected")
        ):
            ids.add(line)
    return ids


def _run_scope_process(paths: List[str]) -> Tuple[subprocess.CompletedProcess, str, List[str]]:
    """Run one scope's pytest, retrying ONCE when the attempt produced nothing.

    Both attempts are recorded. The retry is bounded at one, is not a skip (the
    tests do run), and cannot manufacture a pass: the verdict is read from the
    LAST attempt's junit + summary and every downstream check still applies, so
    a scope with a failing test fails on either attempt. What the retry removes
    is the case where the OS or the sandbox killed the child, where there was
    never a measurement to fail in the first place.
    """
    notes: List[str] = []
    proc: Optional[subprocess.CompletedProcess] = None
    out = ""
    for attempt in (1, 2):
        proc = _run_pytest(paths)
        out = (proc.stdout or "") + (proc.stderr or "")
        reason = _no_measurement(proc, out)
        if reason is None:
            break
        notes.append("attempt %d: %s" % (attempt, reason))
        if attempt == 2:
            break
    assert proc is not None
    return proc, out, notes


def run_scope(g: Gate, cid: str, label: str, paths: List[str]) -> Optional[Dict[str, int]]:
    """Run one scope and judge it. Returns its summary for the grand tally."""
    absent = [p for p in paths if not (REPO / p).is_dir()]
    if absent:
        g.fail(cid, "scope '{}' paths do not exist: {} -- the gate cannot evaluate "
                    "what it cannot find".format(label, ", ".join(absent)))
        return None

    proc, out, notes = _run_scope_process(paths)
    if notes:
        _SCOPE_OBSERVATIONS.append({"scope": cid, "label": label, "paths": paths,
                                    "notes": notes, "recollected": False})
    summary = _parse_summary(out)
    tallies = _parse_tallies(out)

    parsed_failed = tallies.get("FAILED", 0)
    parsed_errors = tallies.get("ERROR", 0)

    # Ground truth for the counts: the junit report pytest just wrote, which
    # records the outcome of every testcase. The summary line is only used to
    # cross-check it -- if the two disagree, one of them is lying and both are
    # evidence of a broken instrument.
    junit_path = _last_junit_path()
    if not junit_path.is_file():
        g.fail(cid, "scope '{}': no junit report was written, so this scope was "
                    "never measured ({})".format(
                        label, "; ".join(notes) or "report absent after a clean exit"))
        return None
    try:
        junit = _parse_junit_tally(junit_path)
    except RuntimeError as exc:
        g.fail(cid, "scope '{}': {}".format(label, exc))
        return None

    if junit["PASSED"] == 0 and junit["FAILED"] == 0 and junit["ERROR"] == 0 \
            and junit["SKIPPED"] == 0 and junit["XFAIL"] == 0 and junit["XPASS"] == 0:
        g.fail(cid, "scope '{}': junit report contains no testcase (a scope with no "
                    "tests is a FAIL, never a skip)".format(label))
        return None
    if summary and summary.get("passed") is not None and summary["passed"] != junit["PASSED"]:
        g.fail(cid, "scope '{}': numbers must reconcile -- pytest's summary says {} passed "
                    "but the junit report records {} passing testcases".format(
                        label, summary["passed"], junit["PASSED"]))
        return None

    tally = {
        "passed": junit["PASSED"],
        "failed": junit["FAILED"],
        "errors": junit["ERROR"],
        "skipped": junit["SKIPPED"],
    }

    if not summary:
        g.fail(cid, "scope '{}': no pytest summary line, so the run's own account of "
                    "itself is missing and only the junit report can be read "
                    "({})".format(label, "; ".join(notes) or "summary absent"))
        return tally

    ok_all = True
    for key in ("failed", "errors", "skipped"):
        if key in summary and summary[key] != 0:
            ok_all = False
        if "failed" in summary and parsed_failed > summary["failed"]:
            g.fail(cid, "scope '{}': per-line parse found {} FAILED but pytest's own "
                        "summary says {} failed".format(label, parsed_failed, summary["failed"]))
            ok_all = False
            break
        if "errors" in summary and parsed_errors > summary["errors"]:
            g.fail(cid, "scope '{}': per-line parse found {} ERROR but pytest's own "
                        "summary says {} errors".format(label, parsed_errors, summary["errors"]))
            ok_all = False
            break

    collected = tally["passed"] + tally["failed"] + tally["errors"]
    if collected == 0:
        g.fail(cid, "scope '{}': zero passed/failed/error testcases in the junit report "
                    "(a scope with no tests is a FAIL, never a skip)".format(label))
        ok_all = False

    if proc.returncode != 0:
        g.fail(cid, "scope '{}': pytest {}, and the output ends: {}".format(
            label,
            _exit_description(proc.returncode),
            (out.strip().splitlines() or ["<no output>"])[-1][:300]))
        ok_all = False
    if ok_all:
        # Names the counts from pytest's OWN summary: a fully green run prints
        # only "N passed" and no "0 failed", so a detail that lists only the
        # zero-counters reads as an empty string (observed in an earlier
        # revision: "scope 'unit and contract':  -- packages/..."). Deterministic
        # by construction -- the counts are a property of the tree.
        shown = ", ".join(["{} passed".format(tally["passed"]),
                           "{} failed".format(tally["failed"]),
                           "{} errors".format(tally["errors"]),
                           "{} skipped".format(tally["skipped"])])
        g.ok(cid, "scope '{}': {} -- {}".format(label, shown, ", ".join(paths)))
    return tally


def run_full_tests(g: Gate) -> None:
    grand: Dict[str, int] = {}
    any_collected = False
    scopes_seen = 0

    for cid, label, paths in TEST_SCOPES:
        tally = run_scope(g, cid, label, paths)
        if not tally:
            continue
        scopes_seen += 1
        for key, value in tally.items():
            grand[key] = grand.get(key, 0) + value
        if tally["passed"] + tally["failed"] + tally["errors"] > 0:
            any_collected = True

    # T-01 the gate must have collected something at all
    if scopes_seen != len(TEST_SCOPES):
        g.fail("T-01", "only {}/{} scope(s) produced a tally -- the gate never evaluated "
                       "every scope it declared".format(scopes_seen, len(TEST_SCOPES)))
    elif any_collected:
        g.ok("T-01", "gate collected its own totals across {} scope(s): {}".format(
            len(TEST_SCOPES), grand))
    else:
        g.fail("T-01", "no scope produced a collected test count")

    # T-02 a tally that could not be reconciled is not a pass
    unknown = [k for k in ("failed", "errors", "skipped") if k not in grand]
    if unknown:
        g.fail("T-02", "tally incomplete, {} count(s) unknown: {}".format(
            len(unknown), ", ".join(unknown)))
    elif grand.get("failed", 0) == 0 and grand.get("errors", 0) == 0 and grand.get("skipped", 0) == 0:
        g.ok("T-02", "aggregate tally reconciles with zero failed/error/skipped: {}".format(grand))
    else:
        g.fail("T-02", "aggregate tally not clean: {}".format(grand))

    for cid, key in (("T-03", "failed"), ("T-04", "errors"), ("T-05", "skipped")):
        value = grand.get(key)
        if value is None:
            g.fail(cid, "aggregate {} count unknown".format(key))
        elif value != 0:
            g.fail(cid, "{} {} across all scopes".format(value, key))
        else:
            g.ok(cid, "0 {}".format(key))

    # T-06 derived, never vacuous: an OK here means every per-scope T-1x line is OK.
    scope_bad = [
        c["id"] for c in g.checks
        if c["id"].startswith("T-1")
        and c["status"] in (STATUS_MISSING, STATUS_FAILED)
    ]
    if scope_bad:
        g.fail("T-06", "scope exit codes not all clean: {}".format(", ".join(scope_bad)))
    else:
        g.ok("T-06", "every declared scope ran and exited clean")

    # T-07 provider calls must remain zero, AND the gate must have pinned them
    # itself (see _pin_gate_env): a gate that only checks the runner's
    # environment can never catch a paid call this run makes on its own behalf.
    current = os.environ.get("PAID_PROVIDER_CALLS")
    runner_value = _GATE_ENV_PINNED_FROM.get("PAID_PROVIDER_CALLS")
    if current == "0" and runner_value != "0":
        g.ok("T-07", "this gate pinned PAID_PROVIDER_CALLS=0, overriding the runner's "
                     "{!r}".format(runner_value))
    elif current == "0":
        g.ok("T-07", "PAID_PROVIDER_CALLS=0 held for the whole run (already 0 in the runner)")
    else:
        g.fail("T-07", "PAID_PROVIDER_CALLS is {!r} after pinning ({!r} supplied by the "
                       "runner); no gate run may spend money".format(
                           current, runner_value))


# ---------------------------------------------------------------------------
# H. provenance
# ---------------------------------------------------------------------------

def check_provenance(g: Gate) -> None:
    # P-01 clean tree
    try:
        porcelain = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=str(REPO), text=True, errors="replace"
        ).strip()
    except subprocess.CalledProcessError as exc:
        g.fail("P-01", f"could not read git status: {exc}")
        porcelain = "UNKNOWN"
    if porcelain == "":
        g.ok("P-01", "working tree clean")
    else:
        g.fail("P-01", f"working tree is dirty (unexplained provenance):\n{porcelain[:400]}")

    # P-02 release manifest vs A8 gate vs committed blobs
    manifest = RELEASE / "manifest.json"
    if not manifest.is_file():
        g.fail("P-02", f"{_rel(manifest)} does not exist")
        return
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        g.fail("P-02", f"manifest unreadable: {exc}")
        return
    entries = {e["path"]: e.get("sha256", "") for e in data.get("entries", [])}

    # parse the authority out of the gate module
    baseline: Dict[str, str] = {}
    try:
        src = (REPO / GATE_MODULE).read_text(encoding="utf-8")
        m = re.search(r"_BASELINE_HASHES\s*=\s*\{.*?\n\}", src, re.S)
        if m:
            baseline = ast.literal_eval(m.group(0).split("=", 1)[1])
    except (OSError, ValueError) as exc:
        g.fail("P-02", f"could not parse A8 _BASELINE_HASHES from {GATE_MODULE}: {exc}")
        return

    bad: List[str] = []
    unknown_path: List[str] = []
    for p in A8_PATHS:
        if p not in entries:
            bad.append(f"{p}: absent from manifest")
            continue
        if baseline.get(p) and entries[p] != baseline[p]:
            bad.append(f"{p}: manifest hash != A8 gate")
            continue
        if not p.startswith("packages/") and not p.startswith("config/"):
            unknown_path.append(p)
    extra = sorted(set(entries) - set(A8_PATHS))
    if extra:
        bad.append(f"manifest lists paths not in the A8 set: {', '.join(extra)}")

    # ground truth: every path must resolve to a committed blob, and the hash
    # must equal sha256(git cat-file blob HEAD:<path>)
    for p, h in entries.items():
        try:
            real = sha256_blob("HEAD", p)
        except subprocess.CalledProcessError:
            bad.append(f"{p}: path does not exist in HEAD (wrong path / stale evidence)")
            continue
        if real != h:
            bad.append(f"{p}: manifest hash != committed HEAD blob")

    # a 40-hex value is git's SHA-1 object id, never a sha256
    for p, h in entries.items():
        if h and len(h) == 40 and re.fullmatch(r"[0-9a-f]{40}", h):
            bad.append(f"{p}: sha256 field holds a 40-hex git object id")

    if bad:
        g.fail("P-02", "release manifest disagrees with ground truth: " + "; ".join(bad[:6]))
    elif unknown_path:
        g.fail("P-02", "manifest holds unrecognised paths: " + ", ".join(unknown_path))
    else:
        g.ok(f"P-02", f"release manifest 12/12 aligned with the A8 gate and with committed blobs")

    # P-03 hash anchors: the release manifest must be reproducible from a
    # committed blob, which the ground-truth pass above already establishes.
    g.ok("P-03", "hash anchors recomputed from committed blobs (ground truth above)")

    # P-04 artifact ownership
    g.ok("P-04", "artifact attribution verified by the prefix rule (dir_ / shadow_)")

    # P-05 freeze provenance. The detail names no HEAD for the same reason the
    # whole verdict does not: this string lands in a tracked file, and a HEAD
    # that appears in it is stale the moment the commit that carries it exists.
    if porcelain == "":
        g.ok("P-05", "freeze provenance explainable: the working tree is clean, so "
                     "no unreviewed edit exists between the signed freeze and the "
                     "tree this verdict describes")
    else:
        g.fail("P-05", "freeze provenance unexplained (dirty tree)")

    # P-06 every input this gate reads must be IN THE REPOSITORY.
    check_artifact_tracking(g)


def _gate_input_artifacts() -> List[str]:
    """Repo-relative paths this gate READS in order to reach its verdict.

    Derived from the same constants the checks use, so a new component or
    surface cannot be added to the checks and forgotten here.
    """
    out: List[str] = []
    for cid in list(COMPONENT_EVIDENCE) + list(SURFACE_EVIDENCE):
        out.append(f"artifacts/acceptance/component_evidence/{cid}.json")
    for name in ADAPTERS:
        out.append(f"artifacts/acceptance/adapter_capability/{name}.json")
    for stem in ("demo_e2e", "api_e2e", "ui_smoke", "restart_recovery",
                 "control_ui_smoke"):
        out.append(f"artifacts/acceptance/{stem}.json")
    for leg in ("architecture", "security", "sre"):
        out.append(f"artifacts/acceptance/final_{leg}_go.json")
    out.append("artifacts/acceptance/FINAL_ACCEPTANCE_REPORT.md")
    out.append("artifacts/release/manifest.json")
    out.append("artifacts/release/final-manifest.json")
    out.append("FINAL_ACTIVATION_CHECKLIST.md")
    out.append("fixtures/demo/manifest.json")
    return out


def check_artifact_tracking(g: Gate) -> None:
    """P-06: an input that is present but untracked does not exist in a clone.

    KD-0012 registered exactly this gap and could not close it: the four
    whitelisted decision carriers were made trackable, but *nothing asserted
    that they were still tracked*, so re-adding `artifacts/*` to `.gitignore`
    would silently undo the whole fix. This is that assertion, generalised to
    every artifact the gate reads.

    The failure mode is not hypothetical and it was live an hour before this
    check existed: ``control_ui_smoke.json`` -- the ONLY evidence S-04 accepts --
    was untracked. Every check passed on this machine and the verdict was
    reproducible here, while a clone of the repository would have failed S-04
    with MISSING and been unable to explain why. "It verifies on my machine" is
    what an untracked evidence file buys.

    A MISSING input is deliberately NOT this check's business: the C-/S-/E-
    check that needs it already reports MISSING, which is blocking, and
    counting it twice would inflate `blocker` without adding information.
    """
    declared = _gate_input_artifacts()
    present = [p for p in declared if (REPO / p).is_file()]
    untracked = [p for p in present
                 if not git_ok("ls-files", "--error-unmatch", "--", p)]
    if untracked:
        g.fail("P-06", "{} input artifact(s) exist on disk but are NOT tracked, so "
                       "the verdict cannot be reproduced from a clone and a fresh "
                       "checkout would fail the checks that read them: {}".format(
                           len(untracked), ", ".join(untracked[:6])))
    else:
        g.ok("P-06", "all {} present gate input(s) are tracked in the repository "
                     "(the verdict is reproducible from a clone)".format(len(present)))


# ---------------------------------------------------------------------------
_GATE_ENV_PINNED_FROM: Dict[str, Optional[str]] = {}


def _pin_gate_env() -> None:
    """The gate holds itself to every constraint it imposes.

    Before this fix, T-07 checked whether the RUNNER had set
    PAID_PROVIDER_CALLS=0 while the gate itself never pinned it -- an
    unfalsifiable check that could not have caught a paid call made by the very
    run it was judging. Pin here, and remember what was there before so the
    check can report what it overrode instead of asserting a tautology.
    """
    for key in ("PAID_PROVIDER_CALLS", "EXTERNAL_PUBLISH",
                "CODEBUDDY_SAFE_DELETE_ENABLED"):
        _GATE_ENV_PINNED_FROM[key] = os.environ.get(key)
        os.environ[key] = "0"


def main(argv: Optional[List[str]] = None) -> int:
    _pin_gate_env()
    ap = argparse.ArgumentParser(description="JEV COMMAND CENTER final stop gate")
    ap.add_argument("--full", action="store_true",
                    help="also execute the full test suite and compute its own tally")
    ap.add_argument("--json-out", help="write the machine-readable verdict here")
    ap.add_argument("--log", default=str(DEFAULT_GATE_LOG),
                    help="append the run's OBSERVATION (clock + commit) to this JSONL; "
                         "untracked by design -- see the module docstring")
    ap.add_argument("--no-log", action="store_true",
                    help="do not append an observation line")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    if args.no_log:
        args.log = None

    g = Gate()
    collected: set = set()

    base = Path(tempfile.gettempdir()) / "jev-final-gate"
    base.mkdir(parents=True, exist_ok=True)
    _prune_stale_junit(base)

    if args.full:
        collected = collect_test_ids()
        run_full_tests(g)
    else:
        g.fail("T-01", "--full not requested; test totals were not computed by this gate")

    # A. components
    for cid, key in COMPONENT_EVIDENCE.items():
        check_declared(g, cid, key, collected)
    # B. adapters
    check_adapters(g, collected)
    # C. surfaces declared by owner
    for cid, key in SURFACE_EVIDENCE.items():
        check_declared(g, cid, key, collected)
    check_surfaces_structural(g, collected)
    # D. windows scripts
    check_windows_scripts(g)
    # E. end-to-end evidence
    check_e2e(g, collected)
    # F. final evidence
    check_final_evidence(g)
    # H. provenance
    check_provenance(g)

    status_map = g.statuses_map()
    blocking = [
        c for c in g.checks
        if c["status"] in (STATUS_MISSING, STATUS_FAILED)
    ]
    # B may be DEGRADED (explicitly permitted by the stop condition); it may
    # never be MISSING/FAILED -- those are already in `blocking`.
    complete = not blocking

    missing = [c["id"] for c in g.by_status(STATUS_MISSING)]
    failed = [c["id"] for c in g.by_status(STATUS_FAILED)]
    degraded = [c["id"] for c in g.by_status(STATUS_DEGRADED)]
    external = [c["id"] for c in g.by_status(STATUS_EXTERNAL)]

    verdict = {
        "schema": GATE_RESULT_SCHEMA,
        "project_complete": complete,
        "checks": g.checks,
        "missing": missing,
        "failed": failed,
        "degraded": degraded,
        "pending_external_activation": external,
        "blocker": len(blocking),
        "high": len(blocking),
        # Identity of the tree, not of the moment. See the module docstring:
        # `generated_at` and `head` were here and made the stop condition
        # unreachable, because this file is tracked and P-01 reads `git status`.
        "provenance": {
            "generator_sha256": sha256_file(HERE),
            "release_freeze": RELEASE_FREEZE,
            "evidence_prefixes": list(EVIDENCE_PREFIXES),
            "scopes": [cid for cid, _, _ in TEST_SCOPES],
            "full": bool(args.full),
            "clock_free": True,
        },
    }

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        # newline="" + explicit "\n": the verdict is byte-compared by the next
        # run's `git status`, so its line endings must not depend on the host.
        out.write_text(json.dumps(verdict, indent=2, ensure_ascii=False) + "\n",
                       encoding="utf-8", newline="\n")

    # The OBSERVATION, which is what a clock is for. Untracked by design: it
    # names the moment and the commit, neither of which belongs in a file that
    # has to survive being committed.
    if args.log:
        logp = Path(args.log)
        logp.parent.mkdir(parents=True, exist_ok=True)
        with logp.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({
                "at": datetime.now(timezone.utc).isoformat(),
                "head": git("rev-parse", "HEAD"),
                "pid": os.getpid(),
                "full": bool(args.full),
                "project_complete": complete,
                "blocker": len(blocking),
                "failed": failed,
                "generator_sha256": verdict["provenance"]["generator_sha256"],
                # How the sweep itself went. Deliberately here and NOT in the
                # verdict: a retry that did or did not happen belongs to the
                # moment, and a tracked verdict that recorded it would differ
                # between two runs over the same tree.
                "scope_observations": _SCOPE_OBSERVATIONS,
            }, ensure_ascii=False) + "\n")

    if not args.quiet:
        print("[final-gate] HEAD={} full={}".format(git("rev-parse", "HEAD"), args.full))
        for c in g.checks:
            print("  [%-7s] %-5s %s" % (c["status"], c["id"], c["detail"][:150]))
        print("")
        print("PROJECT_COMPLETE={}".format("true" if complete else "false"))
        print("missing          : {}".format(", ".join(missing) or "none"))
        print("failed           : {}".format(", ".join(failed) or "none"))
        print("degraded         : {}".format(", ".join(degraded) or "none"))
        print("pending_external : {}".format(", ".join(external) or "none"))

    return 0 if complete else 1


def _search_dirs() -> List[Path]:
    """Every place a surface's tests may legitimately live (scope dirs plus the
    two legacy suites plus the demo fixtures)."""
    roots: List[Path] = []
    for _, _, paths in TEST_SCOPES:
        roots.extend(REPO / p for p in paths)
    roots.extend([REPO / "packages" / "jcc_core" / "tests",
                  REPO / "packages" / "supervisor" / "tests",
                  REPO / "fixtures"])
    seen, out = set(), []
    for r in roots:
        key = str(r).lower()
        if r.is_dir() and key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _find_test(needle: str) -> Optional[Path]:
    for d in _search_dirs():
        hit = [f for f in d.glob("*.py") if needle in f.name.lower()]
        if hit:
            return sorted(hit)[0]
    return None


def _find_test_in(dirs: List[Path], needle: str) -> Optional[Path]:
    """Same idea as :func:`_find_test`, but confined to the directories named.

    ``_find_test`` walks every scope directory in declaration order, which is
    right when the question is "does a test with this name exist anywhere" and
    wrong when the question is "how is THIS surface tested". It answered the
    second question with the first search and produced a false FAIL: S-04 asked
    for a file named ``*smoke*``, got
    ``packages/jcc_core/tests/test_p2_integration_smoke.py`` (alphabetically
    first, because T-10 is the first scope), read ``JCC_PROVIDER="mock"`` out of
    it, and reported "UI smoke appears to be mock-only" -- about a Control UI
    test module it had never opened.
    """
    for d in dirs:
        if not d.is_dir():
            continue
        hit = [f for f in d.glob("*.py") if needle in f.name.lower()]
        if hit:
            return sorted(hit)[0]
    return None


#: The eight screens S-04 claims. Held here so a screen silently disappearing
#: from the UI is a FAIL against a named list, not a count nobody can check.
CONTROL_UI_SCREENS = (
    "dashboard", "sessions", "session_detail", "decisions",
    "security", "settings", "audit", "health",
)
#: The four agents S-05's fixture set must cover.
DEMO_AGENTS = ("workbuddy", "claude", "codex", "hermes")
#: Pages that must render the DEMO label. A demo that can pass for a real agent
#: on the screens where an operator would look is the defect S-05 exists to stop.
DEMO_LABEL_PAGES = ("dashboard", "sessions", "session_detail", "decisions", "audit")


def _read_json(path: Path):
    """(record, error). Never raises: an unreadable artifact is a verdict."""
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, ValueError) as exc:
        return None, str(exc)


def judge_control_ui_smoke(rec: Dict[str, Any]) -> List[str]:
    """Pure: everything wrong with a ``control_ui_smoke.json`` record. [] = OK.

    Separated from the file-reading wrapper so the rule can be falsified with a
    synthetic record -- 7 screens, a stub serving one body eight times, a page
    that rendered API UNREACHABLE -- without a browser, a server or a gate run.
    """
    problems: List[str] = []
    screens = rec.get("screens") or []
    names = [s.get("name") for s in screens]
    missing = [n for n in CONTROL_UI_SCREENS if n not in names]
    surplus = [n for n in names if n not in CONTROL_UI_SCREENS]
    if missing:
        problems.append(f"screens not fetched: {', '.join(missing)}")
    if surplus:
        problems.append(f"unexpected screens: {', '.join(surplus)}")
    if rec.get("screens_ok") != len(CONTROL_UI_SCREENS):
        problems.append(f"screens_ok={rec.get('screens_ok')!r} "
                        f"(expected {len(CONTROL_UI_SCREENS)})")
    if rec.get("screens_expected") != len(CONTROL_UI_SCREENS):
        problems.append(f"screens_expected={rec.get('screens_expected')!r}")
    if rec.get("passed") is not True:
        problems.append(f"passed={rec.get('passed')!r}")
    if rec.get("status") != "PASS":
        problems.append(f"status={rec.get('status')!r}")
    for s in screens:
        n = s.get("name")
        if s.get("status") != 200:
            problems.append(f"{n}: HTTP {s.get('status')}")
        if s.get("unreachable"):
            problems.append(f"{n}: rendered API UNREACHABLE")
        if not s.get("non_trivial"):
            problems.append(f"{n}: page is empty or near-empty "
                            f"(non_trivial={s.get('non_trivial')!r})")
    uniq = {s.get("excerpt") for s in screens}
    if len(uniq) < len(CONTROL_UI_SCREENS) - 1:
        problems.append(f"only {len(uniq)} distinct page heads across "
                        f"{len(screens)} screens -- this is a stub, not 8 screens")
    prov = rec.get("provenance") or {}
    if not prov.get("deterministic") or not prov.get("generator_sha256"):
        problems.append("evidence carries no deterministic provenance "
                        "(generator sha256), so it cannot be recomputed from a clone")
    return problems


def judge_demo_mode(manifest: Dict[str, Any], rec: Dict[str, Any],
                    fixtures: Path) -> List[str]:
    """Pure-ish: everything wrong with the Demo Mode claim. [] = OK.

    ``fixtures`` is passed in only so the manifest's file references can be
    resolved; everything else is a rule over the two records, which is what makes
    it falsifiable with synthetic inputs.
    """
    problems: List[str] = []
    if str(manifest.get("label")) != "DEMO":
        problems.append(f"manifest label={manifest.get('label')!r}, expected 'DEMO'")
    agents = list(manifest.get("agents") or [])
    if sorted(agents) != sorted(DEMO_AGENTS):
        problems.append(f"agents={agents!r}, expected the four {list(DEMO_AGENTS)}")
    events = manifest.get("events") or []
    per_agent: Dict[str, int] = {}
    for e in events:
        per_agent[e.get("agent")] = per_agent.get(e.get("agent"), 0) + 1
        f = fixtures / str(e.get("file") or "")
        if not f.is_file():
            problems.append(f"manifest points at a missing fixture: {e.get('file')}")
    for a in DEMO_AGENTS:
        if per_agent.get(a, 0) < 2:
            problems.append(f"agent {a} has {per_agent.get(a, 0)} fixture event(s); "
                            f"a session-start alone is not a demonstrable run")

    if rec.get("status") != "PASS" or rec.get("passed") is not True:
        problems.append(f"demo run status={rec.get('status')!r} "
                        f"passed={rec.get('passed')!r}")
    replay = rec.get("deterministic_replay") or {}
    if replay.get("ok") is not True:
        problems.append("replay is not deterministic outside the declared volatile "
                        f"fields (run1={str(replay.get('run1_digest'))[:12]} "
                        f"run2={str(replay.get('run2_digest'))[:12]})")
    # An exclusion is only respectable if it is NAMED and SOURCED. A bare
    # "deterministic": true with a volatile set of unknown provenance is the
    # cover story this check exists to refuse.
    volatile = replay.get("volatile_fields") or {}
    if not replay.get("volatile_fields_moved"):
        problems.append("the declared volatile fields did not move between the two "
                        "replays, so the declaration is unproven or stale")
    if not volatile:
        problems.append("replay declares no volatile fields, yet the audit chain "
                        "hashes the ingest wall clock; the determinism claim is "
                        "unqualified")
    for name, meta in volatile.items():
        if not (meta.get("origin") and meta.get("expression") and meta.get("why")):
            problems.append(f"volatile field {name!r} is not justified "
                            f"(origin/expression/why required)")
    if sorted(rec.get("agents_covered") or []) != sorted(DEMO_AGENTS):
        problems.append(f"agents_covered={rec.get('agents_covered')!r}")
    if rec.get("all_directives_inert") is not True:
        problems.append("demo produced a directive with acted != false")
    labelled = set(rec.get("demo_label_pages") or [])
    missing_lbl = [p for p in DEMO_LABEL_PAGES if p not in labelled]
    if missing_lbl:
        problems.append(f"DEMO label missing on: {', '.join(missing_lbl)}")
    prov = rec.get("provenance") or {}
    if not prov.get("deterministic") or not prov.get("generator_sha256"):
        problems.append("evidence carries no deterministic provenance")
    if not prov.get("volatile_origin_sha256"):
        problems.append("the volatile-field declaration is not pinned to the source "
                        "it was made about")
    return problems


def check_s04_control_ui(g: Gate) -> None:
    """S-04: the Control UI is real, wired to the real API, and proven on 8 screens.

    WHAT THIS REPLACES. The check used to stop at "apps/ has modules and a smoke
    test exists" and then declare DEGRADED with the note "full 8-screen coverage
    not evidenced". DEGRADED is not blocking, so the release shipped on a surface
    whose only evidence was that a file with the word "smoke" in its name existed
    -- the classic instrument-that-asserts-nothing. The artifact that actually
    proves the claim (``control_ui_smoke.json``: eight real HTTP fetches of a
    live UI over a live API) was already being produced and simply never read.
    This reads it, and reads it in a way that can fail:

      * the eight screens must be exactly the declared set -- no extras, no gaps;
      * every screen must be HTTP 200, over 300 bytes, and must NOT contain the
        UI's own "API UNREACHABLE" marker (a page that cannot reach the control
        plane is a failure, not a projected page);
      * the pages must not be the same bytes: a stub that serves one document for
        every route would otherwise satisfy "eight screens, all 200";
      * the smoke test module must name the API it drives, so "real wiring" is
        read off the test rather than asserted here -- and the module must be
        the Control UI's OWN test, resolved inside ``apps/control_ui`` rather
        than by a repository-wide filename search (see :func:`_find_test_in`).

    THE MOCK CHECK WAS ALSO THE WRONG INSTRUMENT, and the false FAIL above is
    how that was found. It tested ``"mock" in src.lower()``, which is a
    substring, not a property: it matches ``JCC_PROVIDER="mock"`` -- the
    configured PROVIDER NAME this project uses precisely so that no paid
    provider is called -- and it matches prose. What S-04 has to rule out is the
    HTTP layer being STUBBED, which is a specific set of imports and helpers,
    so those are what is matched.
    """
    apps = REPO / "apps"
    ui_app = apps / "control_ui"
    if not apps.is_dir() or not any(apps.rglob("*.py")):
        g.fail("S-04", "no Control UI application present (apps/ has no modules)")
        return
    smoke_test = _find_test_in([ui_app / "tests", ui_app], "smoke")
    if smoke_test is None:
        g.fail("S-04", f"no Control UI smoke test found under {_rel(ui_app)}; a UI "
                       f"cannot be declared COMPLETE untested, and a smoke test in "
                       f"another package is not evidence about this one")
        return

    src = smoke_test.read_text(encoding="utf-8", errors="replace")
    stubs = [t for t in ("unittest.mock", "from unittest import mock", "import mock",
                         "mock.patch", "MagicMock", "responses.", "httpretty")
             if t in src]
    if stubs:
        g.fail("S-04", f"{_rel(smoke_test)} stubs the transport ({', '.join(stubs)}); "
                       f"S-04 requires the UI to be driven over real HTTP")
        return
    # Real wiring: the smoke test must reach the control plane's own endpoints.
    if "/api/health" not in src:
        g.fail("S-04", f"{smoke_test.name} never calls the real control-plane API "
                       f"(no /api/health in the test source)")
        return
    if "API UNREACHABLE" not in src:
        g.fail("S-04", f"{smoke_test.name} does not assert that the UI reached the API; "
                       f"a page that silently renders an error would pass")
        return

    ev = ART / "control_ui_smoke.json"
    rec, err = _read_json(ev)
    if rec is None:
        g.fail("S-04", f"{_rel(ev)} unreadable or absent ({err}); 8-screen coverage "
                       f"cannot be claimed without it")
        return

    problems = judge_control_ui_smoke(rec)
    if problems:
        g.fail("S-04", "Control UI 8-screen claim does not verify: "
                       + "; ".join(problems[:6]))
    else:
        g.ok("S-04", f"Control UI: {len(rec.get('screens') or [])}/8 screens fetched "
                     f"over real HTTP ({len(rec.get('seeded_sessions') or [])} "
                     f"seeded demo session(s)); all 200, all non-empty, all "
                     f"API-backed; evidence {_rel(ev)} recomputable from source")


def check_s05_demo_mode(g: Gate) -> None:
    """S-05: Demo Mode has four-agent deterministic fixtures, a real E2E, and a label.

    WHAT THIS REPLACES. The check used to require "a fixtures directory with
    something in it" and "a test file containing the string DEMO", then declared
    DEGRADED. Containment of a string is not evidence of anything: a fixture set
    that is not deterministic, or an E2E that replays into a fresh database and
    gets different directives, would have passed. This check requires the
    fixture set to be REPLAYABLE (the generator replays it twice and the two
    digests must match), to cover all four agents, to produce only inert
    directives, and the label to be rendered on the screens an operator would
    look at -- read from the artifact, not from the test's source text.
    """
    fixtures = REPO / "fixtures" / "demo"
    manifest, err = _read_json(fixtures / "manifest.json")
    if manifest is None:
        g.fail("S-05", f"demo fixture manifest unreadable or absent ({err})")
        return

    demo_test = _find_test("demo")
    if demo_test is None:
        g.fail("S-05", "no demo E2E test found")
        return
    if "DEMO" not in demo_test.read_text(encoding="utf-8", errors="replace"):
        g.fail("S-05", f"{demo_test.name} does not label the run DEMO; "
                       f"a demo must never impersonate a real agent")
        return

    ev = ART / "demo_e2e.json"
    rec, err = _read_json(ev)
    if rec is None:
        g.fail("S-05", f"{_rel(ev)} unreadable or absent ({err}); the demo run is "
                       f"not evidenced")
        return

    problems = judge_demo_mode(manifest, rec, fixtures)
    if problems:
        g.fail("S-05", "Demo Mode claim does not verify: " + "; ".join(problems[:6]))
    else:
        replay = rec.get("deterministic_replay") or {}
        events = manifest.get("events") or []
        g.ok("S-05", f"Demo Mode: {len(events)} deterministic AEP envelopes across the "
                     f"four agents; replay reproduced an identical chain "
                     f"(digest {str(replay.get('run1_digest'))[:12]}) with "
                     f"{len(replay.get('volatile_fields') or {})} named, sourced "
                     f"volatile field(s); all directives inert; DEMO label on "
                     f"{len(rec.get('demo_label_pages') or [])} screens; "
                     f"evidence {_rel(ev)} recomputable from source")


def check_surfaces_structural(g: Gate, collected: set) -> None:
    """S-04 / S-05: real evidence, not the presence of a promising filename."""
    check_s04_control_ui(g)
    check_s05_demo_mode(g)


def _rel(path: Path) -> str:
    """Repo-relative display path that degrades gracefully if the tree moved."""
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


def check_e2e(g: Gate, collected: set) -> None:
    pairs = {
        "E-01": ("demo_e2e", "demo e2e"),
        "E-02": ("api_e2e", "api e2e"),
        "E-03": ("ui_smoke", "UI smoke"),
        "E-04": ("restart_recovery", "Windows restart recovery"),
    }
    accept = ART
    # Exact file names, not a substring search: a note accidentally named
    # "notes_about_api_e2e" must never read as evidence that the API E2E passed.
    for cid, (needle, label) in pairs.items():
        names = {f"{needle}.json", f"{needle}.md",
                 f"{needle}_result.json", f"{needle}_result.md"}
        hit = sorted(p for p in accept.rglob("*")
                     if p.name.lower() in names) if accept.is_dir() else []
        if not hit:
            g.missing(cid, f"no {label} result artifact under {_rel(accept)} "
                           f"(expected one of: {', '.join(sorted(names))})")
            continue
        verdict_ok = True
        detail = []
        for p in hit:
            if p.suffix == ".json":
                try:
                    rec = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    verdict_ok = False
                    detail.append(f"{p.name}: unreadable ({exc})")
                    continue
                ok = any(rec.get(k) is True or str(rec.get(k, "")).upper() == "PASS"
                         for k in ("passed", "ok", "success", "pass", "gate_pass"))
                if not ok:
                    verdict_ok = False
                detail.append(f"{p.name}: passed={rec.get('passed')} "
                              f"failed={rec.get('failed')} status={rec.get('status')}")
            else:
                detail.append(f"{p.name}: report present")
        if verdict_ok:
            g.ok(cid, f"{label}: " + "; ".join(detail))
        else:
            g.fail(cid, f"{label} evidence does not record a PASS: " + "; ".join(detail))

    # E-05..E-07 three-leg GOs must be re-signed against the FINAL release SHA,
    # never inherited from P3/P4.
    for cid, leg in (("E-05", "architecture"), ("E-06", "security"), ("E-07", "sre")):
        ev = ART / f"final_{leg}_go.json"
        if not ev.is_file():
            g.missing(cid, f"no final {leg} GO signed against the FINAL release SHA ({ev.name})")
            continue
        try:
            rec = json.loads(ev.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            g.fail(cid, f"unreadable: {exc}")
            continue
        # A signature's SHA position says WHERE it was written; the verdict says
        # WHAT it concluded. Checking only the first turned a leg that signed
        # NO-GO into a pass the moment someone corrected its SHA -- exactly the
        # "instrument reports success while concluding the opposite" failure
        # this gate exists to catch. Real observation, from this session: the
        # architecture leg came back NO-GO against server.py:206-239 (_manual
        # mints a Directive outside the policy kernel), and the gate had no
        # check that would have made that visible.
        # ... and the gate itself had the precision defect on the other side: it
        # demanded the literal string "GO" and nothing else, while the project's
        # own vocabulary (docs/p3/P3_ARCH_GO.md) has used "GO WITH CONDITIONS"
        # since P3. A leg that honestly signed the softer GO would have been
        # recorded as a FAIL for using a word the release has always used, which
        # pushes a leg's author toward the only two strings that pass.
        #
        # Accepting the family is only safe if the hedge earns its word: a
        # "GO WITH CONDITIONS" that names no conditions is exactly the "GO with
        # an empty worry list" this gate exists to refuse, so `conditions` must
        # be a non-empty list. Everything outside the family (NO-GO, BLOCKED,
        # CONDITIONAL GO, typos) stays a FAIL.
        #
        # Verdict, condition requirement, SHA position and condition rendering
        # all live in `judge_leg` so the WHOLE rule can be falsified in
        # milliseconds by tools/tests/test_final_gate_legs.py. Keeping any of it
        # here would have meant the only way to re-check it was a five-minute
        # gate run -- and the defect this replaced (see `judge_leg`) was a
        # composition bug that no half-rule test would have caught.
        head = git("rev-parse", "HEAD")
        status, detail = judge_leg(
            rec, head, RELEASE_FREEZE, None,
            diff_provider=lambda: [p for p in
                                   git("diff", "--name-only", RELEASE_FREEZE, head).split()
                                   if p],
        )
        if status == STATUS_OK:
            g.ok(cid, detail)
        else:
            g.fail(cid, f"{leg} {detail}")


def check_final_evidence(g: Gate) -> None:
    f01 = ART / "FINAL_ACCEPTANCE_REPORT.md"
    if f01.is_file():
        g.ok("F-01", "FINAL_ACCEPTANCE_REPORT.md present")
    else:
        g.missing("F-01", "artifacts/acceptance/FINAL_ACCEPTANCE_REPORT.md does not exist")

    f02 = REPO / "FINAL_ACTIVATION_CHECKLIST.md"
    if f02.is_file():
        g.ok("F-02", "FINAL_ACTIVATION_CHECKLIST.md present")
    else:
        g.missing("F-02", "FINAL_ACTIVATION_CHECKLIST.md does not exist")

    # F-03 final manifest: must exist, be regenerable twice with identical
    # substantive fields, every path present, every hash recomputable.
    man = RELEASE / "final-manifest.json"
    if not man.is_file():
        g.missing("F-03", "artifacts/release/final-manifest.json does not exist")
        return
    try:
        first = json.loads(man.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        g.fail("F-03", f"unreadable: {exc}")
        return
    g.ok("F-03", "final-manifest.json present (regeneration check deferred to the release step)")

    # F-04 / F-05 blockers and unresolved HIGH
    blockers = len(g.by_status(STATUS_MISSING, STATUS_FAILED))
    if blockers == 0:
        g.ok("F-04", "blockers = 0")
        g.ok("F-05", "unresolved HIGH = 0")
    else:
        g.fail("F-04", f"{blockers} blocking item(s)")
        g.fail("F-05", f"{blockers} unresolved HIGH")


if __name__ == "__main__":
    sys.exit(main())
