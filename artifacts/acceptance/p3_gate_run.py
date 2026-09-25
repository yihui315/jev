"""P3 (Jev Shadow) acceptance-gate RUNNER — task #46 (Harbor).

Runs the independent P3 acceptance gate and REFUSES to pass if anything failed or
skipped. It is deliberately able to fail: a verifier that cannot fail is not a
verifier (the #54 defect shipped green behind a vacuous falsifier).

What it runs (P3 surface by default):
  * ``jcc_core/tests/test_p3_acceptance_gate.py``  — the §5 A1-A11 matrix,
    D-40(a-e) falsifiers, engine inertness, the two declared port reasons,
    ``Actuator.execute`` inertness, D-26 server boundary, the four frozen budget
    tests, and the §2.6 ``StateStore.transaction()`` conformance check.
  * the frozen P3 suites: ``test_p3_core_integration.py``, ``test_decision.py``,
    ``test_signing.py``, ``test_policy.py``, ``supervisor/tests``.

Pass ``--full`` to additionally run the ENTIRE ``jcc_core/tests`` tree (that is
the #39 full-suite freeze; it is off by default so this stays the P3 gate).

Gate rules enforced here (not merely reported):
  * failed == 0 AND errors == 0 AND **skipped == 0** (a skip is a gate FAILURE).
  * every §5 A-row and every D-40 row must be PASSED.
  * the per-test parse must RECONCILE with pytest's own final tally. `counts` is
    derived from a regex over `pytest -v` lines; a regex gap under-counts silently
    and can hide a FAILED (a node id containing spaces did exactly that — see
    `_STATUS_RE`). Any parse/tally disagreement is fatal.

Environment (MEASURED on this host; two earlier advices superseded):
  * ``TEMP``/``TMP`` alone does NOT fix tmp_path setup errors — pytest's basetemp
    is what matters, and ``packages/conftest.py::pytest_configure`` used to force
    it to ``F:\\jev\\runtime\\pytest-tmp`` (outside the safe-delete exempt zone).
    forge fixed that in ``9c0820d`` (#60): an explicit ``--basetemp`` now wins, and
    the default derives from ``tempfile.gettempdir()``.
  * The lead's suggested ``--basetemp=C:\\Windows\\TEMP\\jevpytest`` FAILS here.
    ``tempfile.gettempdir()`` is ``C:\\Users\\<user>\\AppData\\Local\\Temp``, so
    ``C:\\Windows\\TEMP`` is NOT exempt; and pytest ``_rm_rf``s a given basetemp on
    startup, so a reused path gets mass-deleted — once it held 171 entries the
    sandbox bulk-delete guard aborted pytest (``count=171 > threshold=50`` =>
    ``SystemExit(1)``, every test ERROR at setup).
  * This runner derives the base from ``tempfile.gettempdir()`` (the ONLY exempt
    prefix) AND uses a FRESH UNIQUE child per run, so nothing pre-exists to be
    deleted. Override with ``JEV_PYTEST_BASETEMP`` if needed.

NO FAILURE IS ABSORBED. There is deliberately no "known-pending" exemption: the
lead ruled that anything marked MUST FIX must be green for a GO, so every
FAILED/ERROR/SKIPPED counts against the gate and is reported plainly.

Usage:
    F:/jev/.venv/Scripts/python.exe F:/jev/artifacts/acceptance/p3_gate_run.py [--full]
Exit code: 0 iff the gate passes; 1 otherwise (never 0 on a skip).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]                       # F:/jev
PACKAGES = REPO / "packages"
PY = REPO / ".venv" / "Scripts" / "python.exe"
ARTIFACTS = REPO / "artifacts" / "acceptance"

GATE_MODULE = "jcc_core/tests/test_p3_acceptance_gate.py"
P3_SUITES = [
    GATE_MODULE,
    "jcc_core/tests/test_p3_core_integration.py",
    "jcc_core/tests/test_decision.py",
    "jcc_core/tests/test_signing.py",
    "jcc_core/tests/test_policy.py",
    "supervisor/tests",
]

#: One `pytest -v` result line: ``<nodeid> <STATUS> [ NN%]``.
#:
#: The node id is ``\S.*?`` and NOT ``\S+``: 10 tests in this tree are parametrized
#: with ids that CONTAIN SPACES (e.g. ``test_codex_notify_restore_is_byte_exact[
#: model = "x"\nnotify = [ "a.exe" ]]``, ``test_secret_values_are_masked[curl -u
#: alice:s3cr3tpw …]``). With ``\S+`` those lines failed to match and were SILENTLY
#: DROPPED from the totals — an under-count, and worse, a FAILED line for such a
#: test would have been dropped too, letting ``failed == 0`` hold while a test
#: failed. That is a fail-open parse hole of the same class as F-2 (vacuity).
#:
#: Anchoring the line END on the ``[ NN%]`` progress marker (which only pytest's
#: own result lines carry) is what keeps this precise while allowing spaces: the
#: runner's own ``[gate] … PASSED`` prose lines cannot match.
_STATUS_RE = re.compile(
    r"^(?P<node>\S.*?)\s+(?P<status>PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)"
    r"\s+\[\s*\d+%\]\s*$"
)
_A_RE = re.compile(r"::test_A(\d+)_")
_D40_RE = re.compile(r"::test_D40([a-e]?)_?")

#: pytest's own tally line, e.g.
#: ``==== 349 passed, 2 warnings in 161.49s (0:02:41) ====``.
#: Parsed only to RECONCILE against the per-test parse above: if the two disagree,
#: the parse is incomplete and the gate must fail loudly instead of under-reporting.
_SUMMARY_RE = re.compile(
    r"(?P<n>\d+)\s+(?P<kind>passed|failed|errors?|skipped|xfailed|xpassed)\b"
)
_SUMMARY_KINDS = {
    "passed": "PASSED", "failed": "FAILED", "error": "ERROR", "errors": "ERROR",
    "skipped": "SKIPPED", "xfailed": "XFAIL", "xpassed": "XPASS",
}


def _pytest_summary_counts(out: str) -> dict[str, str | int] | None:
    """Counts from pytest's OWN final tally line, or ``None`` if it was not emitted.

    Used only to cross-check the per-test parse. Returns a dict of
    ``{STATUS: count}``; a non-tally line is never accepted.
    """
    def _from_line(stripped: str) -> dict[str, int] | None:
        found: dict[str, int] = {}
        for m in _SUMMARY_RE.finditer(stripped):
            found[_SUMMARY_KINDS[m.group("kind")]] = int(m.group("n"))
        return found or None

    lines = out.splitlines()
    for line in reversed(lines):
        stripped = line.strip()
        if stripped.startswith("="):
            got = _from_line(stripped)
            if got:
                return got
    # Fallback: a pytest-style tally anywhere (e.g. wrapped output).
    for line in reversed(lines):
        stripped = line.strip()
        if " in " in stripped and "passed" in stripped:
            got = _from_line(stripped)
            if got:
                return got
    return None

#: DELIBERATELY EMPTY. No failure is absorbed or reclassified.
#: (Earlier this map labelled two budget failures "known-pending"; the lead ruled
#: both out — `cost_units` is MUST FIX (must go green) and `scorer_tokens` is
#: waived as inert-by-design and pinned by its own test, not red.)
KNOWN_PENDING: dict[str, str] = {}

#: Free-form facts recorded into the JSON result so the machine artifact carries
#: its own provenance. The A8 entry is filled at freeze time ONLY when a
#: production artifact inside the pin legitimately changes.
NOTES: list[str] = [
    "gate rule: passed>0 AND failed==0 AND errors==0 AND skipped==0 (a skip FAILS)",
    "no failure is absorbed or reclassified at this SHA",
]

#: Set when the A8 production-hash pin is re-baselined (lead expects forge's D-37
#: commit to touch config/jcc.yaml:32, config.py:55, schemas.py:140).
#: Shape: {"old": {...}, "new": {...}, "reason": "...", "authorized_by": "..."}
A8_REBASELINE: dict | None = None


def _git(args: list[str]) -> str:
    try:
        out = subprocess.run(["git", *args], cwd=str(REPO), capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=30)
        return (out.stdout or "").strip()
    except Exception as exc:  # noqa: BLE001
        return f"<git error: {exc}>"


def main(argv: list[str]) -> int:
    full = "--full" in argv

    # Authoritative fix (see module docstring). Two independent hazards:
    #  (1) LOCATION. The safe-delete shim only short-circuits deletes that resolve
    #      under `tempfile.gettempdir()` of the RUNNING process. That value is
    #      shell-dependent (observed both C:\Users\...\AppData\Local\Temp and
    #      C:\Windows\TEMP on this host). So rather than guess, we PIN TEMP/TMP for
    #      the child to our chosen base -- then the child's gettempdir() IS the base
    #      and everything beneath it is exempt by construction.
    #  (2) ACCUMULATION. pytest `_rm_rf`s a given basetemp on STARTUP, so a reused
    #      path gets mass-deleted each run; once it holds >50 entries the guard
    #      aborts with SystemExit(1) (measured: count=171 > threshold=50 => every
    #      test ERRORs). => use a FRESH UNIQUE child so nothing pre-exists.
    default_base = Path(tempfile.gettempdir()) / "jev-pytest-runs"
    base = Path(os.environ.get("JEV_PYTEST_BASETEMP", str(default_base)))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    tmp_dir = base / f"g{stamp}"
    try:
        base.mkdir(parents=True, exist_ok=True)
        tmp_dir.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        print(f"[gate] FATAL: cannot create basetemp {tmp_dir}: {exc}")
        return 1

    env = dict(os.environ)
    # TEMP/TMP -> the BASE (parent of the per-run basetemp) so the child's
    # tempfile.gettempdir() equals the base and the whole scratch subtree is in the
    # safe-delete exempt zone by construction, independent of the caller's shell.
    env["TEMP"] = str(base)
    env["TMP"] = str(base)
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("JCC_CONTROL_TOKEN", "p3-gate-runner-token")

    targets = list(P3_SUITES)
    if full:
        targets = ["jcc_core/tests", "supervisor/tests"]

    cmd = [str(PY), "-m", "pytest", *targets, "-v", "-p", "no:cacheprovider",
           "--no-header", "-rN", f"--basetemp={tmp_dir}"]
    print(f"[gate] HEAD={_git(['rev-parse', 'HEAD'])}  dirty={bool(_git(['status', '--porcelain']))}")
    print(f"[gate] TEMP={env['TEMP']}")
    print(f"[gate] cmd={' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=str(PACKAGES), env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    out = (proc.stdout or "") + (proc.stderr or "")

    results: dict[str, str] = {}
    for line in out.splitlines():
        m = _STATUS_RE.match(line.strip())
        if m:
            results[m.group("node")] = m.group("status")

    counts = {k: 0 for k in ("PASSED", "FAILED", "ERROR", "SKIPPED", "XFAIL", "XPASS")}
    for status in results.values():
        counts[status] = counts.get(status, 0) + 1

    # ---- parse reconciliation (lead, freeze-time finding) -------------------
    # `counts` is derived from OUR regex over pytest's per-test lines. A regex gap
    # under-counts silently, and can hide a FAILED whose node id did not match --
    # i.e. `failed == 0` while a test actually failed. Cross-check the parse against
    # pytest's own tally and treat ANY disagreement as fatal.
    summary = _pytest_summary_counts(out)
    parse_mismatch: list[str] = []
    if summary is None:
        parse_mismatch.append(
            "pytest emitted no final tally line -> the per-test parse cannot be "
            "reconciled (output truncated, crash before the summary, or run aborted)"
        )
    else:
        parsed_total = sum(counts.values())
        summary_total = sum(int(v) for v in summary.values())
        if parsed_total != summary_total:
            parse_mismatch.append(
                f"PARSE INCOMPLETE: matched {parsed_total} result lines but pytest "
                f"reported {summary_total} ({summary}) -> _STATUS_RE missed some lines"
            )
        for kind, n in sorted(summary.items()):
            if counts.get(kind, 0) != int(n):
                parse_mismatch.append(
                    f"parse/pytest disagree on {kind}: parsed "
                    f"{counts.get(kind, 0)} vs pytest {n}"
                )

    a_rows = {}
    d40_rows = {}
    for node, status in sorted(results.items()):
        if ma := _A_RE.search(node):
            a_rows[f"A{ma.group(1)}"] = (node.split("::")[-1], status)
        if _D40_RE.search(node):
            d40_rows[node.split("::")[-1]] = status

    print("\n[gate] ===== §5 A1-A11 matrix =====")
    for key in sorted(a_rows, key=lambda k: int(k[1:])):
        name, status = a_rows[key]
        print(f"[gate]   {key:<4} {status:<8} {name}")
    print("[gate] ===== D-40 falsifiers =====")
    for name, status in sorted(d40_rows.items()):
        print(f"[gate]   {status:<8} {name}")

    skipped = counts["SKIPPED"]
    failed = counts["FAILED"] + counts["ERROR"] + counts["XFAIL"] + counts["XPASS"]
    a_bad = [k for k, (_, s) in a_rows.items() if s != "PASSED"]
    d_bad = [k for k, s in d40_rows.items() if s != "PASSED"]

    # No "known-pending" absorption: every non-PASSED test counts and is reported.
    failing_tests = sorted({n.split("::")[-1] for n, s in results.items() if s != "PASSED"})

    # ---- F-2 anti-vacuity guard (aegis-2, CR F-2, HIGH) --------------------
    # The parse only ever sees pytest's own output lines. If pytest emits nothing
    # parseable -- collection error, usage error, crash, bad nodeid, wrong cwd,
    # renamed test -- `results` comes back empty and every derived set is empty,
    # so ANY of these would silently yield ok=True and GATE=PASS on zero executed
    # tests. That is the same vacuous-pass class as #54. Each is now fatal.
    vacuity: list[str] = []
    vacuity.extend(parse_mismatch)

    if not results:
        vacuity.append("no test results were parsed at all (pytest emitted nothing "
                       "parseable -- collection/usage error, crash or wrong cwd?)")

    # Every declared target must actually contribute rows. Catches PARTIAL
    # execution (one typo'd path among several good ones), which a lone
    # `counts['PASSED'] > 0` check cannot see.
    for target in targets:
        key = target.rstrip("/")
        if not [n for n in results if key in n.replace("\\", "/")]:
            vacuity.append(f"declared target contributed NO results: {target}")

    # The gate is the A1-A11 matrix: if any row failed to run, the matrix silently
    # shrinks and still reads green. Rows must be COMPLETE, not merely all-green.
    expected_a = {f"A{i}" for i in range(1, 12)}
    absent_a = sorted(expected_a - set(a_rows), key=lambda k: int(k[1:]))
    if absent_a:
        vacuity.append(f"A-matrix rows did not run: {absent_a} "
                       "(renamed/removed test, or it failed to collect)")

    # Same idea for the D-40 falsifiers; the non-vacuous one is load-bearing.
    for must in ("test_D40_shadow_non_actuation_is_non_vacuous",):
        if must not in d40_rows:
            vacuity.append(f"load-bearing D-40 falsifier did not run: {must}")

    # pytest return codes: 0 ok, 1 tests failed, and anything else means the RUN
    # itself did not happen (2 interrupted, 3 internal, 4 usage error, 5 no tests
    # collected). A nonzero-pytest / zero-failure combination is contradictory and
    # must never be read as a pass.
    rc = proc.returncode
    if rc not in (0, 1):
        vacuity.append(f"pytest returncode {rc} is not 0/1 -> the run did not "
                       "complete normally (see exit codes above)")
    if rc != 0 and not failing_tests:
        vacuity.append(f"pytest returned {rc} but no failing test was parsed -> "
                       "result is untrustworthy")

    for reason in vacuity:
        print(f"[gate] VACUITY GUARD: {reason}")

    ok = (failed == 0 and skipped == 0 and not a_bad and not d_bad
          and counts["PASSED"] > 0 and not vacuity)

    print(f"\n[gate] totals: {counts}")
    print(f"[gate] A-rows not PASSED: {a_bad or 'none'}")
    print(f"[gate] D-40 rows not PASSED: {d_bad or 'none'}")
    print(f"[gate] failing tests: {failing_tests or 'none'}")
    print("[gate]   (nothing is absorbed -- no known-pending exemption applies)")
    print(f"[gate] GATE = {'PASS' if ok else 'FAIL'}")

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    report = {
        "task": "#46 P3 acceptance gate (harbor)",
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "head": _git(["rev-parse", "HEAD"]),
        "dirty": bool(_git(["status", "--porcelain"])),
        "basetemp": str(tmp_dir),
        "basetemp_note": (
            "derived from tempfile.gettempdir() at runtime (the ONLY prefix the "
            "safe-delete shim exempts) with a FRESH UNIQUE per-run child, so nothing "
            "pre-exists to be mass-deleted. packages/conftest.py (#60, 9c0820d) derives "
            "the same base, so no explicit override is needed."),
        "notes": NOTES,
        "cmd": cmd,
        "counts": counts,
        "gate": "PASS" if ok else "FAIL",
        "failing_tests": failing_tests,
        "absorbed_failures": [],
        "pytest_returncode": proc.returncode,
        "vacuity_guard": vacuity,
        "vacuity_guard_triggered": bool(vacuity),
        "summary_reconciliation": {
            "pytest_tally": summary,
            "parsed_total": sum(counts.values()),
            "mismatches": parse_mismatch,
            "reconciled": not parse_mismatch,
        },
        "a8_rebaseline": A8_REBASELINE,
        "a_matrix": {k: {"test": v[0], "status": v[1]} for k, v in a_rows.items()},
        "d40": d40_rows,
        "results": results,
    }
    (ARTIFACTS / "p3_gate_result.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8")
    print(f"[gate] wrote {ARTIFACTS / 'p3_gate_result.json'}")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
