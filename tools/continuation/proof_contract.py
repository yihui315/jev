#!/usr/bin/env python3
"""The control-plane proof contract (FINAL-12 / stop-condition section L).

THE GAP THIS CLOSES. Four falsifiers --
``tools/tests/runtime_lease_falsifier.py``, ``watchdog_election_falsifier.py``,
``interruption_recovery.py`` and ``provenance_integration.py`` -- each measure a
claim the stop condition asserts (a beating controller lease, interruption
recovery, single-controller election, runtime exclusivity). None of them matched
``test_*.py`` and none of them was in the controller's STEPS table, so NOTHING IN
THE PIPELINE EVER RAN THEM. A claim whose only evidence is a test nobody runs is
an assertion, and this project has been burned five times by instruments that
reported a result they had not produced.

So each runner now emits a machine-readable proof, and FINAL-12
(``artifacts/acceptance/project_run_gate.py``) reads and JUDGES those proofs as
part of its verdict. The proof is not a copy of the runner's stdout: it names the
claim, the number of cases that ran and passed, and every case by name, and the
judge refuses a proof that ran no case, has fewer checks than its own case count,
or reports anything but ``ok is True`` and ``status == "PASS"``.

WHY ONE MODULE. The schema and the judge live here rather than in each runner, so
there is exactly ONE implementation of "is this proof green" -- the same reason
``project_run_gate.py`` imports the controller's judges instead of retyping them.
Two implementations of one claim is two answers.

A PROOF IS ABOUT A MOMENT, and that is why it is NOT a tracked tree verdict: it
carries ``produced_at``, so it cannot be a fixed point of the tree. It is tracked
(with an explicit negation in .gitignore, following the FINAL-phase evidence
precedent) so that it travels with the repository instead of verifying once on the
machine that produced it.
"""
from __future__ import annotations

import io
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

SCHEMA = "jcc-control-proof/1"
TZ = timezone(timedelta(hours=8))

#: name -> (the section-L claim it proves, the runner that produces it). The gate
#: judges EXACTLY these; a proof that is absent is a red verdict, not a footnote.
PROOFS: Dict[str, Tuple[str, str]] = {
    "CONTROL_HEARTBEAT_PROOF": (
        "the controller's lease heartbeat advances for the whole of a long step, so "
        "a replacement controller never reads a live controller as stale",
        "tools/tests/interruption_recovery.py",
    ),
    "INTERRUPTION_RECOVERY_PROOF": (
        "a controller killed mid-step is replaced, the replacement ATTACHES to the "
        "live run, and NO second long run is launched",
        "tools/tests/interruption_recovery.py",
    ),
    "WATCHDOG_ELECTION_PROOF": (
        "simultaneous watchdog ticks elect exactly ONE controller and the losers NOOP",
        "tools/tests/watchdog_election_falsifier.py",
    ),
    "RUNTIME_LEASE_PROOF": (
        "the runtime lease admits exactly one owner for port 4778, reclaims an "
        "abandoned one, and never opens the port",
        "tools/tests/runtime_lease_falsifier.py",
    ),
    # NOT a section-L claim: this one covers section F (three-layer provenance and
    # the self-reference fix). It is judged here anyway because the same hole
    # applied -- a falsifier nothing runs -- and a provenance claim nobody checks
    # is exactly the class of defect this gate exists to refuse.
    "PROVENANCE_INTEGRATION_PROOF": (
        "the gate's --record-moment is pure, the publisher's canonical summary is a "
        "pure function of staging carrying all three provenance layers with "
        "final_evidence_sha None by construction, and it fails closed on a missing "
        "or verdict-less staging result",
        "tools/tests/provenance_integration.py",
    ),
}


def proof_artifact(name: str) -> str:
    """The repo-relative path of a proof, e.g. ``artifacts/acceptance/X.json``."""
    return "artifacts/acceptance/%s.json" % name


def proof_path(root: str, name: str) -> str:
    return os.path.join(root, "artifacts", "acceptance", "%s.json" % name)


def build_proof(name: str, cases: Sequence[Tuple[str, bool, str]], *,
                claim: Optional[str] = None,
                extra: Optional[Dict[str, Any]] = None,
                produced_by: Optional[str] = None) -> Dict[str, Any]:
    """Assemble the proof payload from the runner's own ``(name, ok, detail)`` cases.

    ``cases_total``/``cases_passed`` are derived from the SAME list the checks come
    from, so a runner cannot report more passes than it recorded cases.
    """
    if name not in PROOFS:
        raise ValueError("unknown control proof %r; expected one of %s"
                         % (name, sorted(PROOFS)))
    if not cases:
        # A runner that recorded no case must still leave a RED proof on disk: the
        # judge refuses `cases_total <= 0`, so the claim stays unproven instead of
        # disappearing. (The alternative -- crashing -- would leave whatever green
        # proof was there before, which is the defect class this file is about.)
        payload: Dict[str, Any] = {
            "schema": SCHEMA,
            "name": name,
            "claim": claim or PROOFS[name][0],
            "ok": False,
            "status": "FAIL",
            "verdict": "FAIL: the runner recorded no case at all, so nothing was proved",
            "cases_total": 0,
            "cases_passed": 0,
            "checks": [],
            "produced_at": datetime.now(TZ).isoformat(timespec="seconds"),
            "produced_by": produced_by or PROOFS[name][1],
        }
        if extra:
            payload.update(extra)
        return payload
    bad = [c for c in cases if not c[1]]
    payload: Dict[str, Any] = {
        "schema": SCHEMA,
        "name": name,
        "claim": claim or PROOFS[name][0],
        "ok": not bad,
        "status": "PASS" if not bad else "FAIL",
        "verdict": ("PASS: %s" % (claim or PROOFS[name][0])) if not bad
                   else "FAIL: " + "; ".join(str(c[0]) for c in bad),
        "cases_total": len(cases),
        "cases_passed": len(cases) - len(bad),
        "checks": [{"name": str(n), "ok": bool(ok), "detail": str(d)[:400]}
                   for n, ok, d in cases],
        "produced_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "produced_by": produced_by or PROOFS[name][1],
    }
    if extra:
        payload.update(extra)
    return payload


def write_proof(root: str, name: str, payload: Dict[str, Any]) -> str:
    """Write the proof atomically (temp -> fsync -> replace); return its path."""
    path = proof_path(root, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return path


def judge_proof(payload: Any) -> Tuple[bool, List[str]]:
    """Pure. Every clause names one way a proof could claim more than it measured."""
    problems: List[str] = []
    if not isinstance(payload, dict):
        return False, ["the proof artifact is not a JSON object (unparsable or empty)"]
    if payload.get("schema") != SCHEMA:
        problems.append("schema=%r, not %r" % (payload.get("schema"), SCHEMA))
    if payload.get("ok") is not True:
        problems.append("ok=%r, so the runner reported it RED: %s"
                        % (payload.get("ok"), str(payload.get("verdict"))[:240]))
    if payload.get("status") != "PASS":
        problems.append("status=%r" % (payload.get("status"),))

    total = payload.get("cases_total")
    passed = payload.get("cases_passed")
    if not isinstance(total, int) or isinstance(total, bool) or total <= 0:
        problems.append("cases_total=%r: a proof that ran no case proves nothing" % (total,))
    elif passed != total:
        problems.append("cases_passed=%r of %r -- a proof that did not pass every case "
                        "does not prove the claim" % (passed, total))

    checks = payload.get("checks")
    if not isinstance(checks, list) or not checks:
        problems.append("the proof records no checks at all")
    else:
        red = [c.get("name") for c in checks
               if not (isinstance(c, dict) and c.get("ok") is True)]
        if red:
            problems.append("red check(s): %s" % (red,))
        if isinstance(total, int) and not isinstance(total, bool) and len(checks) != total:
            problems.append("%d recorded check(s) but cases_total=%r"
                            % (len(checks), total))
    return (not problems), problems


def load_proof(root: str, name: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Read one proof. Returns ``(payload, error)``; exactly one is None."""
    path = proof_path(root, name)
    if not os.path.isfile(path):
        return None, "no proof artifact at %s -- run %s" % (path, PROOFS[name][1])
    try:
        with io.open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh), None
    except (OSError, ValueError) as exc:
        return None, "%s could not be read as JSON: %s: %s" % (path, type(exc).__name__, exc)


def import_contract(root: str):
    """Load this module by path, for scripts that cannot import it as a package."""
    import importlib.util
    path = os.path.join(root, "tools", "continuation", "proof_contract.py")
    spec = importlib.util.spec_from_file_location("jcc_proof_contract", path)
    mod = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod
