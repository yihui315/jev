#!/usr/bin/env python3
"""Run the FINAL-01 infrastructure suite and reconcile its evidence.

THE DEFECT THIS EXISTS FOR
--------------------------
``FINAL-01_CONTROLLER`` used to be declared as::

    Step("FINAL-01_CONTROLLER", ..., "tools/tests/test_continuation_infra.py",
         (), None, None, 900)

``run_step`` executes that as ``python tools/tests/test_continuation_infra.py``,
and running a pytest module as a plain script does not run pytest. It imports
``pytest``, defines the fixtures and the test functions, reaches the end of the
file and exits 0. So the step PASSED BY RUNNING NOTHING, and because it declared
no result artifact, nothing could contradict it. This is the project's central
defect class -- the instrument reporting success while doing nothing -- and it
was sitting on the step that produces the platform's own executor.

So the suite is run here instead, through pytest, and then the evidence that
``tools/tests/conftest.py`` wrote is checked against pytest's OWN tally rather
than trusted:

  1. the pre-existing artifact is moved out of the repository (never deleted --
     `PERMANENT_DELETE=0`; it is parked under the system temp directory so the
     previous verdict can still be inspected), so "the artifact exists" really
     does mean "this run produced it";
  2. pytest runs the whole directory, writing both its terminal summary and a
     junit report to uniquely-named files;
  3. the artifact's totals and node ids are reconciled with the junit report --
     an artifact claiming 65/65 passes while pytest reports 66 items is a FAIL,
     in either direction. A verifier whose arithmetic cannot be checked has no
     way to discover that it is verifying the wrong thing.

NO CLOCK IN THE EVIDENCE. Freshness is established by the move-aside, not by
timestamps, so the artifact stays a pure function of the test sources and their
outcomes and can be compared byte-for-byte between runs.

Usage
-----
    python tools/continuation/run_infra_suite.py
    python tools/continuation/run_infra_suite.py --selftest
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

ROOT = str(Path(__file__).resolve().parents[2])
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
SUITE_DIR = "tools/tests"
EVIDENCE_REL = "artifacts/acceptance/CONTINUATION_INFRA_TEST.json"
EVIDENCE = os.path.join(ROOT, *EVIDENCE_REL.split("/"))
SCHEMA = "jcc-continuation-infra/1"

SUMMARY_RE = re.compile(r"(\d+)\s+(passed|failed|error|errors|skipped|deselected|xfailed|xpassed)")

#: NTSTATUS values that mean the process died inside the runtime. 0xC0000005 is
#: the one measured on this host, twice, against pytest itself as well as against
#: powershell.exe.
_NATIVE_DEATH = {
    0xC0000005: "STATUS_ACCESS_VIOLATION",
    0xC0000006: "STATUS_IN_PAGE_ERROR",
    0xC0000017: "STATUS_NO_MEMORY",
    0xC0000374: "STATUS_HEAP_CORRUPTION",
    0xC0000409: "STATUS_STACK_BUFFER_OVERRUN",
}
_PYTEST_ATTEMPTS = 3


def native_death(rc):
    """The NTSTATUS name when the process died inside the runtime, else None.

    WHY THE RUNNER RETRIES AND WHY THAT IS NOT AN EXCUSE. Measured on this host
    on 2026-09-26: a full run of this suite died at exit 3221225477 (0xC0000005
    STATUS_ACCESS_VIOLATION) after 86 s, pytest printing its own faulthandler
    traceback (``_pytest/config/__init__.py``) and leaving no junit report at
    all. Earlier the same code appeared inside powershell.exe, and earlier still
    it killed ``apps/control_ui/tests``. It is the host's documented memory
    fault, and the rate is high enough that a two-minute run is a coin flip.

    A retry buys a real measurement instead of an environment reading. It is
    BOUNDED, and when every attempt dies the run FAILS naming the NTSTATUS: a run
    that measured nothing must never be reported as a pass.
    """
    if rc is None:
        return None
    unsigned = rc & 0xFFFFFFFF
    if unsigned in _NATIVE_DEATH:
        return "%s (0x%08X)" % (_NATIVE_DEATH[unsigned], unsigned)
    return None


def log(msg: str) -> None:
    line = "[infra-suite] %s" % msg
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", "backslashreplace").decode("ascii"), flush=True)


def sha256_file(path: str):
    if not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with io.open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def parse_summary(text: str):
    """Parse pytest's terminal summary into counts, or {} if there is none.

    Only the LAST line carrying counts is used, and only the aggregate tallies:
    parameterised ids containing spaces are a known trap for per-id scraping, but
    they cannot corrupt ``"66 passed"``.
    """
    counts = {}
    for line in text.splitlines():
        found = SUMMARY_RE.findall(line)
        if not found:
            continue
        merged = {}
        for value, word in found:
            key = "error" if word == "errors" else word
            merged[key] = merged.get(key, 0) + int(value)
        if merged:
            counts = merged
    return counts


def parse_junit(path: str):
    """(total, failures, errors, [names]) from a junit report, or None."""
    if not os.path.isfile(path):
        return None
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return None
    cases = root.iter("testcase")
    total = 0
    failures = 0
    errors = 0
    names = []
    for case in cases:
        total += 1
        names.append(case.get("name") or "")
        for child in case:
            tag = child.tag.lower()
            if tag == "failure":
                failures += 1
            elif tag == "error":
                errors += 1
    return total, failures, errors, names


def reconcile(artifact, junit_total, junit_failures, junit_errors, pytest_counts):
    """Compare the evidence artifact with pytest's own tallies.

    Returns (ok, [problems]). Every disagreement is a problem: an artifact that
    undercounts looks better than the run, and one that overcounts means the
    artifact was not produced by this run.
    """
    problems = []
    if not isinstance(artifact, dict):
        return False, ["evidence artifact is not a JSON object"]
    if artifact.get("schema") != SCHEMA:
        problems.append("schema=%r, expected %r" % (artifact.get("schema"), SCHEMA))
    checks = artifact.get("checks")
    check_count = artifact.get("check_count")
    if not isinstance(checks, list) or not checks:
        problems.append("artifact lists no checks (%r)" % (check_count,))
        return False, problems
    if check_count != len(checks):
        problems.append("check_count=%r but checks lists %d" % (check_count, len(checks)))
    failed_in_artifact = sum(1 for c in checks if not c.get("ok"))
    if failed_in_artifact != artifact.get("failed"):
        problems.append("failed=%r but %d check(s) are not ok"
                        % (artifact.get("failed"), failed_in_artifact))

    if junit_total is not None:
        if check_count != junit_total:
            problems.append("the artifact records %r check(s) and pytest's junit "
                            "records %d: the evidence does not describe this run"
                            % (check_count, junit_total))
        junit_bad = junit_failures + junit_errors
        if artifact.get("failed") != junit_bad:
            problems.append("the artifact records failed=%r and pytest's junit "
                            "records %d failure(s)/error(s)"
                            % (artifact.get("failed"), junit_bad))
    if pytest_counts:
        if "passed" not in pytest_counts and not any(
                k in pytest_counts for k in ("failed", "error")):
            problems.append("pytest's terminal summary has no pass/fail tally: %r"
                            % (pytest_counts,))
        reported_passed = pytest_counts.get("passed", 0)
        if check_count is not None and reported_passed and \
                reported_passed != (check_count - failed_in_artifact):
            problems.append("pytest reported %d passed; the artifact implies %d"
                            % (reported_passed, check_count - failed_in_artifact))
    return (not problems), problems


def _selftest() -> int:
    """Prove the reconciliation can FAIL. Returns 0 iff every case holds."""
    cases = []

    def case(name, ok, detail=""):
        cases.append((name, ok, detail))

    good = {"schema": SCHEMA, "checks": [{"node_id": "a", "ok": True},
                                         {"node_id": "b", "ok": True}],
            "check_count": 2, "failed": 0}
    ok, problems = reconcile(good, 2, 0, 0, {"passed": 2})
    case("accepts_a_faithful_artifact", ok, "; ".join(problems))

    ok, problems = reconcile(dict(good, check_count=1), 2, 0, 0, {"passed": 2})
    case("refuses_an_undercount", not ok, "; ".join(problems))

    ok, problems = reconcile(dict(good, check_count=3, checks=[{"ok": True}] * 3),
                             2, 0, 0, {"passed": 3})
    case("refuses_an_overcount", not ok, "; ".join(problems))

    ok, problems = reconcile({"schema": SCHEMA, "checks": [{"ok": False}],
                              "check_count": 1, "failed": 0}, 1, 1, 0, {"failed": 1})
    case("refuses_a_laundered_failure", not ok, "; ".join(problems))

    ok, problems = reconcile({"schema": "other", "checks": [{"ok": True}],
                              "check_count": 1, "failed": 0}, 1, 0, 0, {"passed": 1})
    case("refuses_a_foreign_schema", not ok, "; ".join(problems))

    ok, problems = reconcile({}, None, 0, 0, {})
    case("refuses_a_non_object", not ok, "; ".join(problems))

    empty = parse_summary("65 passed, 1 deselected in 81.57s")
    case("parses_a_summary_with_a_deselection",
         empty.get("passed") == 65 and empty.get("deselected") == 1, str(empty))
    case("no_summary_is_empty", parse_summary("no tests ran") == {}, "{}")

    case("a_native_crash_is_not_a_verdict",
         native_death(3221225477) == "STATUS_ACCESS_VIOLATION (0xC0000005)",
         str(native_death(3221225477)))
    case("a_native_crash_is_recognised_as_signed_too",
         native_death(3221225477 - 0x100000000) == "STATUS_ACCESS_VIOLATION (0xC0000005)", "")
    case("an_ordinary_non_zero_exit_is_a_verdict",
         native_death(0) is None and native_death(1) is None and native_death(4) is None, "")

    print("=" * 74)
    for name, ok, detail in cases:
        print("  [%s] %-38s %s" % ("PASS" if ok else "FAIL", name, detail))
    bad = [c for c in cases if not c[1]]
    print("=" * 74)
    print("run_infra_suite selftest: %d/%d" % (len(cases) - len(bad), len(cases)))
    return 0 if not bad else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="run the FINAL-01 infrastructure suite")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest()

    if os.environ.get("CODEBUDDY_SAFE_DELETE_ENABLED", "1") != "0":
        # The move-aside below is a filesystem operation on a repository path and
        # the sandbox's bulk-delete guard intercepts it. The guard is not an
        # obstacle to route around -- see tools/tests/conftest.py -- so the run
        # states the precondition it needs instead of failing halfway.
        log("REFUSING to run: CODEBUDDY_SAFE_DELETE_ENABLED must be 0. The "
            "evidence file is moved aside before pytest runs (so that a stale "
            "green artifact cannot be mistaken for this run's), and the guard "
            "intercepts repository moves. Re-run with the variable set.")
        return 4

    if not os.path.isfile(PY):
        log("ERROR project venv interpreter missing at %s" % PY)
        return 1

    started = time.time()
    parked = None
    before = sha256_file(EVIDENCE)
    if os.path.isfile(EVIDENCE):
        parked = tempfile.mkdtemp(prefix="jev-infra-prev-")
        shutil.move(EVIDENCE, os.path.join(parked, os.path.basename(EVIDENCE)))
        log("parked the previous evidence at %s (moved, never deleted)" % parked)
        if os.path.isfile(EVIDENCE):
            log("ERROR could not move the previous evidence aside; a stale "
                "artifact would be indistinguishable from this run's")
            return 1

    stamp = "%d-%d" % (os.getpid(), int(started))
    junit = os.path.join(tempfile.gettempdir(), "jev-infra-junit-%s.xml" % stamp)
    if os.path.isfile(junit):
        os.remove(junit)
    summary_file = os.path.join(tempfile.gettempdir(), "jev-infra-summary-%s.txt" % stamp)
    if os.path.isfile(summary_file):
        os.remove(summary_file)

    env = dict(os.environ)
    env["CODEBUDDY_SAFE_DELETE_ENABLED"] = "0"
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [PY, "-m", "pytest", SUITE_DIR, "-q", "--no-header",
           "-p", "no:cacheprovider", "--junitxml", junit]
    text = ""
    counts = {}
    rc = None
    death = None
    for attempt in range(1, _PYTEST_ATTEMPTS + 1):
        if os.path.isfile(junit):
            os.remove(junit)          # never read a previous attempt's report
        with io.open(summary_file, "wb") as out:
            proc = subprocess.run(cmd, cwd=ROOT, stdout=out, stderr=subprocess.STDOUT,
                                  env=env)
        rc = proc.returncode
        death = native_death(rc)
        with io.open(summary_file, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        tail = [l for l in text.strip().splitlines() if l.strip()][-12:]
        for line in tail:
            log("  pytest| %s" % line)
        log("pytest attempt %d/%d exit=%s in %.1fs"
            % (attempt, _PYTEST_ATTEMPTS, rc, time.time() - started))
        if death is None:
            break
        log("  the process died inside the runtime (%s) and produced %s measure; "
            "retrying" % (death, "no" if not os.path.isfile(junit) else "a partial"))
        time.sleep(5)
    counts = parse_summary(text)

    artifact = None
    if os.path.isfile(EVIDENCE):
        try:
            with io.open(EVIDENCE, "r", encoding="utf-8-sig") as fh:
                artifact = json.load(fh)
        except (OSError, ValueError) as exc:
            log("ERROR the artifact this run wrote is unreadable: %s" % exc)
    junit_parsed = parse_junit(junit)
    if junit_parsed is None:
        if death:
            log("FAIL: pytest died inside the runtime on all %d attempt(s) (%s) and "
                "left no junit report. The suite was NOT measured; this is this "
                "host's memory fault, not a product verdict -- and it is not a "
                "pass." % (_PYTEST_ATTEMPTS, death))
        else:
            log("FAIL: pytest produced no readable junit report at %s (exit=%s)"
                % (junit, rc))
        return 1
    total, failures, errors, _names = junit_parsed
    log("junit: %d testcase(s), %d failure(s), %d error(s)" % (total, failures, errors))
    log("pytest summary: %r" % (counts,))

    if artifact is None:
        log("FAIL: pytest ran and this run wrote NO evidence artifact "
            "(a suite that ran nothing must not look like a suite that passed)")
        return 1

    ok, problems = reconcile(artifact, total, failures, errors, counts)
    after = sha256_file(EVIDENCE)
    log("artifact: %s -> %s" % (before or "<absent>", after))
    if not ok:
        for p in problems:
            log("FAIL: %s" % p)
        return 1
    if rc != 0:
        log("FAIL: the artifact is internally consistent but pytest exited %s" % rc)
        return 1
    log("OK: %s; reconciled with pytest's own tally (%d testcase(s), 0 failure(s))"
        % (artifact.get("verdict"), total))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
