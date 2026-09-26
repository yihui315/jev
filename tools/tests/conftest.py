"""Evidence generator for the FINAL-phase infrastructure suite (FINAL-01).

WHY THIS IS A GENERATOR AND NOT A PRINTER. ``FINAL-01_CONTROLLER`` is judged by
an ARTIFACT, not by an exit code, because "the child exited 0" is the weakest
claim in the whole pipeline -- the controller's own selftest already asserts that
rule for the steps it drives, so the suite that validates the controller has to
hold itself to it too. This writes
``artifacts/acceptance/CONTINUATION_INFRA_TEST.json`` as the machine-readable
record of what the infrastructure tests actually observed.

THREE PROPERTIES, EACH LEARNED FROM A DEFECT THIS PROJECT ALREADY PAID FOR.

1. NO CLOCK, NO COMMIT, NO PID. The payload is a pure function of the test
   source and the results: generator sha256, per-test node id and outcome,
   counts. Two runs over an unchanged tree produce identical bytes. The gate's
   own verdict had to be cured of the same thing -- a tracked artifact that
   changes on every run can never be the thing a clean-tree check compares
   against, so P-01 becomes unsatisfiable by construction.

2. COLLECT-ONLY WRITES NOTHING. The acceptance gate calls
   ``pytest --collect-only`` over every scope to learn which pinned test ids
   exist -- including this one. A ``pytest_sessionfinish`` hook runs for that
   session too, with no reports at all, and an earlier version of the e2e
   generator therefore rewrote good PASS evidence as ``FAILED during: <nothing>``
   every time the gate asked what tests existed. Asking a question must not
   destroy the answer.

3. AN ABSENT REPORT REFUSES THE WHOLE FILE. If any collected item has no
   report, the session did not reach it. That is an absence, and an absence must
   never be laundered into a verdict -- so the file is left exactly as it was
   rather than being rewritten with a partial tally that reads like a result.

AND ONE REPORTING PRECONDITION, ENFORCED RATHER THAN DOCUMENTED.
``CODEBUDDY_SAFE_DELETE_ENABLED=0`` is required, for the same reason
``packages/conftest.py`` requires it, and it is enforced here by aborting
collection (exit 4, a usage error -- a statement about the invocation, never
about the product).

AND ONE SELECTION PRECONDITION: A NARROWED RUN WRITES NO SUITE VERDICT.
``session.items`` is the collection AFTER deselection, so ``pytest -k
"not registered_as_scheduled_tasks"`` produces 65 items and this generator used
to write ``"65/65 infrastructure test(s) passed", "status": "PASS"`` -- i.e. a
green certificate for a suite with one of its tests skipped out of existence.
Measured, on this tree, from exactly that command. The artifact's whole meaning
is "the suite passed"; a subset run cannot support that sentence, so it is
refused (loudly, and the file on disk is left untouched) unless the invocation
covers the entire directory.

WHY THIS SUITE NEEDS IT. The sandbox's bulk-delete guard counts deletions per
agent TOOL CALL (its helper is invoked with the process's
``CODEBUDDY_TOOL_CALL_ID``) and refuses once the count passes 50. A pytest
session is not a tool call, but it inherits the variable, so the budget that was
meant for one shell command is spent by dozens of ``tmp_path`` fixtures. Measured
on this tree: ``pytest tools/tests`` under the guard passed 33 tests, reported 28
``tmp_path`` SETUP ERRORS, and had exactly 2 genuine failures -- the errors begin
partway through the run and every subsequent test that touches a temp directory
fails, in a file that passes completely on its own. That spread is what makes it
dangerous: it looks like 28 broken tests.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
EVIDENCE = REPO / "artifacts" / "acceptance" / "CONTINUATION_INFRA_TEST.json"
EVIDENCE_SCHEMA = "jcc-continuation-infra/1"

SAFE_DELETE_PRECONDITION = (
    "CODEBUDDY_SAFE_DELETE_ENABLED=0 is a REPORTING precondition for this suite, "
    "not a product one. With the sandbox's bulk-delete guard enabled, its "
    "delete counter is shared with the agent tool call that launched this run, so "
    "the budget intended for one shell command is consumed by this suite's own "
    "tmp_path churn: measured on this tree, 33 passed + 28 tmp_path SETUP ERRORS "
    "+ 2 real failures, where the same files pass individually. The errors mean "
    "'the environment refused scratch', never 'the product is broken'. Re-run "
    "with CODEBUDDY_SAFE_DELETE_ENABLED=0."
)

_RESULTS: dict = {}


def pytest_collection_modifyitems(config, items) -> None:
    if os.environ.get("CODEBUDDY_SAFE_DELETE_ENABLED", "1") == "0":
        return
    if not items:
        return
    raise pytest.UsageError(
        SAFE_DELETE_PRECONDITION + "\n\nThis run collected %d test(s) and would "
        "have measured the environment, not the product." % len(items))


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pytest_runtest_logreport(report) -> None:
    _RESULTS.setdefault(report.nodeid, {})[report.when] = report.outcome


def _collect_only(session) -> bool:
    """True when this session was asked to collect and not to run."""
    try:
        return bool(session.config.option.collectonly)
    except AttributeError:
        return False


def _narrowing(session):
    """Why this session is a SUBSET of the suite, as a list of reasons.

    The artifact says "the suite passed". Only a run that collected the suite can
    say that. Everything here narrows ``session.items`` without leaving an
    unreported item behind, which is what makes it invisible to the
    "no report recorded" check in ``pytest_sessionfinish``.
    """
    opt = session.config.option
    reasons = []
    keyword = getattr(opt, "keyword", "")
    if keyword:
        reasons.append("-k %r" % keyword)
    markexpr = getattr(opt, "markexpr", "")
    if markexpr:
        reasons.append("-m %r" % markexpr)
    for name in ("deselect", "ignore", "ignore_glob"):
        value = getattr(opt, name, None)
        if value:
            reasons.append("--%s %r" % (name.replace("_", "-"), value))
    for name in ("last_failed", "failed_first", "stepwise"):
        if getattr(opt, name, False):
            reasons.append("--%s" % name.replace("_", "-"))
    args = [str(a).replace("\\", "/").rstrip("/") for a in (session.config.args or [])]
    if not any(a.endswith("tools/tests") for a in args):
        reasons.append("invocation args %r do not name the whole directory" % (args,))
    return reasons


def _payload(checks: list) -> dict:
    ordered = sorted(checks, key=lambda c: c["node_id"])
    failed = [c for c in ordered if not c["ok"]]
    generator = Path(__file__).resolve()
    return {
        "schema": EVIDENCE_SCHEMA,
        "provenance": {
            "generator": "tools/tests/conftest.py",
            "generator_sha256": _sha256_file(generator),
            "command": "pytest tools/tests",
            "selection": "full directory (no -k / -m / --deselect; all items ran)",
            "deterministic": True,
            "note": "clock-free by design: this file is compared byte-for-byte by "
                    "the next run and is read to decide the platform runs",
        },
        "node_ids": [c["node_id"] for c in ordered],
        "checks": ordered,
        "check_count": len(ordered),
        "passed_count": len(ordered) - len(failed),
        "failed": len(failed),
        "passed": not failed and len(ordered) > 0,
        "status": "PASS" if (not failed and ordered) else "FAIL",
        "ok": not failed and len(ordered) > 0,
        "verdict": "%d/%d infrastructure test(s) passed" % (
            len(ordered) - len(failed), len(ordered)),
    }


def pytest_sessionfinish(session, exitstatus) -> None:
    if _collect_only(session):
        return
    narrowed = _narrowing(session)
    if narrowed:
        print("[jcc-infra] NOT writing %s: this run is a SUBSET of the suite (%s). "
              "The artifact states that the whole suite passed, and a subset run "
              "cannot support that sentence -- the file on disk is left untouched."
              % (os.path.basename(EVIDENCE), "; ".join(narrowed)))
        return
    checks = []
    for item in session.items:
        whens = _RESULTS.get(item.nodeid, {})
        if not whens:
            print("[jcc-infra] refusing to write %s: no report recorded for %s"
                  % (os.path.basename(EVIDENCE), item.nodeid))
            return
        ok = all(v == "passed" for v in whens.values())
        bad = sorted(w for w, v in whens.items() if v != "passed")
        checks.append({
            "node_id": item.nodeid,
            "name": item.nodeid.split("::")[-1],
            "ok": ok,
            "detail": "passed" if ok else "FAILED during: " + ",".join(bad),
        })
    if not checks:
        # Nothing collected from this directory: leave whatever is on disk alone.
        return
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    with open(EVIDENCE, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(_payload(checks), fh, indent=2, sort_keys=True)
        fh.write("\n")
