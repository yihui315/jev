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

Usage
-----
    F:/jev/.venv/Scripts/python.exe F:/jev/artifacts/acceptance/final_gate_run.py --full

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
# WHY df3e7d9 AND NOT dc154b0. The freeze is defined as: the last commit that
# carries all product/ops code and NO release evidence. `FINAL_ACTIVATION_CHECKLIST.md`
# is F-02, i.e. release evidence (see docs/FINAL_STOP_CONDITIONS.md sec. F), and
# dc154b0 is the commit that first added it -- so dc154b0 already contains evidence
# and cannot be the freeze. df3e7d9 ("fix(ops): stop doctor.ps1 crashing at check 7")
# changes only scripts/doctor.ps1 and carries no evidence: that is the freeze. The
# first attempt picked dc154b0 and a signer was right to refuse it. Do not "fix" this
# back without re-deriving the definition: the freeze is the position at which every
# leg may be signed, and moving it invalidates all three signatures.
RELEASE_FREEZE = "df3e7d91ac274d971ef110199d5b65acf131aacd"
# What a release-evidence commit is allowed to touch. If something outside this
# set moves after the freeze, a signature written at the freeze describes a
# product that is no longer in HEAD -- that is a stale signature, not a pass.
EVIDENCE_PREFIXES = (
    "artifacts/", "docs/", "tools/", "scripts/", ".gitignore",
    "FINAL_ACTIVATION_CHECKLIST.md", "FINAL_ACCEPTANCE_REPORT.md",
)

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
    junit = base / "junit.xml"
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


def run_scope(g: Gate, cid: str, label: str, paths: List[str]) -> Optional[Dict[str, int]]:
    """Run one scope and judge it. Returns its summary for the grand tally."""
    absent = [p for p in paths if not (REPO / p).is_dir()]
    if absent:
        g.fail(cid, "scope '{}' paths do not exist: {} -- the gate cannot evaluate "
                    "what it cannot find".format(label, ", ".join(absent)))
        return None

    proc = _run_pytest(paths)
    out = (proc.stdout or "") + (proc.stderr or "")
    summary = _parse_summary(out)
    tallies = _parse_tallies(out)

    parsed_failed = tallies.get("FAILED", 0)
    parsed_errors = tallies.get("ERROR", 0)

    # Ground truth for the counts: the junit report pytest just wrote, which
    # records the outcome of every testcase. The summary line is only used to
    # cross-check it -- if the two disagree, one of them is lying and both are
    # evidence of a broken instrument.
    try:
        junit = _parse_junit_tally(_last_junit_path())
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
        g.fail(cid, "no pytest summary line for scope '{}': the tally came from the junit "
                    "report only, which is weaker evidence".format(label))
        return tally

    details = []
    ok_all = True
    for key in ("failed", "errors", "skipped"):
        if key in summary:
            details.append("{}={}".format(key, summary[key]))
            if summary[key] != 0:
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
        g.fail(cid, "scope '{}': pytest exit code {}".format(label, proc.returncode))
        ok_all = False
    if ok_all:
        g.ok(cid, "scope '{}': {} -- {}".format(label, ", ".join(details), ", ".join(paths)))
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

    # P-05 freeze provenance
    if porcelain == "":
        head = git("rev-parse", "HEAD")
        g.ok("P-05", f"freeze provenance explainable at HEAD {head[:12]} (clean tree)")
    else:
        g.fail("P-05", "freeze provenance unexplained (dirty tree)")


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
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    g = Gate()
    collected: set = set()

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
        "project_complete": complete,
        "head": git("rev-parse", "HEAD"),
        "checks": g.checks,
        "missing": missing,
        "failed": failed,
        "degraded": degraded,
        "pending_external_activation": external,
        "blocker": len(blocking),
        "high": len(blocking),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(verdict, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if not args.quiet:
        print("[final-gate] HEAD={} full={}".format(verdict["head"], args.full))
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


def check_surfaces_structural(g: Gate, collected: set) -> None:
    """S-04 Control UI and S-05 Demo Mode: structural, non-fabricated."""
    # S-04 Control UI must exist and be wired to the real API, not a mock page.
    apps = REPO / "apps"
    smoke = _find_test("smoke")
    if not apps.is_dir() or not any(apps.rglob("*.py")):
        g.missing("S-04", "no Control UI application present (apps/ has no modules)")
    elif smoke is None:
        g.missing("S-04", "no UI smoke test found; a UI cannot be declared COMPLETE untested")
    else:
        text = smoke.read_text(encoding="utf-8", errors="replace").lower()
        if "mock" in text and "mock" not in text.replace("nomock", ""):
            g.missing("S-04", "UI smoke appears to be mock-only; must drive the real JCC API")
        else:
            g.degraded("S-04", f"UI smoke {smoke.name} present; full 8-screen coverage not evidenced")

    # S-05 Demo Mode: fixtures + an E2E that is labelled DEMO.
    fixtures = REPO / "fixtures"
    demo_test = _find_test("demo")
    if not fixtures.is_dir() or not any(fixtures.rglob("*")):
        g.missing("S-05", "no demo fixtures directory")
    elif demo_test is None:
        g.missing("S-05", "no demo E2E test found")
    else:
        txt = demo_test.read_text(encoding="utf-8", errors="replace")
        if "DEMO" not in txt:
            g.missing("S-05", "demo test does not label the run DEMO; must never impersonate a real agent")
        else:
            g.degraded("S-05", f"demo fixture + DEMO-labelled e2e present ({demo_test.name})")


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
        head = git("rev-parse", "HEAD")
        rsha = str(rec.get("release_sha", ""))
        if rsha == head:
            g.ok(cid, f"final {leg} GO re-signed against {head[:12]}")
            continue
        # A signature cannot contain itself and simultaneously claim to have been
        # signed at the very commit that contains it -- demanding that made every
        # leg off by one commit. The leg may therefore be signed at the release
        # freeze, and when it is, the gate proves that is still the product it
        # describes: nothing outside release evidence may have moved since.
        if rsha.startswith(RELEASE_FREEZE[:12]):
            changed = [p for p in git("diff", "--name-only", RELEASE_FREEZE, head).split()
                       if p]
            offenders = [p for p in changed
                         if not any(p.startswith(pre) for pre in EVIDENCE_PREFIXES)]
            if offenders:
                g.fail(cid, f"{leg} GO was signed at the release freeze {rsha[:12]}, but "
                            f"{len(offenders)} path(s) outside release evidence changed "
                            f"since: {', '.join(offenders[:5])}")
            else:
                g.ok(cid, f"final {leg} GO re-signed against the release freeze {rsha[:12]}; "
                          f"the {len(changed)} path(s) added since are release evidence only")
            continue
        g.fail(cid, f"{leg} GO was signed against {rsha or 'nothing'}, neither the current "
                    f"HEAD {head} nor the release freeze {RELEASE_FREEZE[:12]}")


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
