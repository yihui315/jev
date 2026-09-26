#!/usr/bin/env python3
"""FINAL-07: regenerate the S-04 / S-05 surface evidence from source, then re-judge it.

WHY "REGENERATE" AND NOT "CHECK THAT THE FILE IS THERE". S-04's only accepted
evidence is ``artifacts/acceptance/control_ui_smoke.json`` and S-05's is
``artifacts/acceptance/demo_e2e.json``. Both are written by producers that live
in the product (``apps/control_ui/acceptance.py``, ``fixtures/demo/acceptance.py``)
and both have already been wrong in this project in the exact way that matters:
S-04 shipped for weeks as DEGRADED with the note "full 8-screen coverage not
evidenced" while the covering artifact existed and was simply never read, and the
artifact's own generator had to lose its timestamp before a tracked copy could
ever be reproduced. A file present on disk is not evidence that the claim it
carries is true *now* -- the producer has to be run, and the claim re-judged.

So this step, in order:

  1. runs both producers, FROM SOURCE, with their streams on files;
  2. reads back what they wrote;
  3. re-judges it with THE GATE'S OWN CHECK FUNCTIONS, imported from
     ``artifacts/acceptance/final_gate_run.py`` rather than re-implemented --
     two implementations of "is the 8-screen claim true" is two answers, and the
     one that matters is the gate's;
  4. requires S-04 and S-05 to come back **OK**. DEGRADED is explicitly refused:
     it is what let "not evidenced" ride into a PASS.

The verdict is ``ok=false`` unless every one of those holds. A producer that
exits 0 while writing a FAIL artifact is a FAIL; an artifact that says PASS while
its producer crashed is a FAIL.

Usage
-----
    python tools/continuation/regenerate_surfaces.py
    python tools/continuation/regenerate_surfaces.py --selftest
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

ROOT = r"F:\jev"
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
OUT = os.path.join(ROOT, "artifacts", "acceptance", "SURFACE_EVIDENCE.json")
GATE_PY = os.path.join(ROOT, "artifacts", "acceptance", "final_gate_run.py")
WORK = os.path.join(ROOT, ".workbuddy", "scratch", "surfaces")

#: (surface, module, artifact) -- the producers, by the module path their own
#: docstrings publish. Kept here as data so the artifact names the command it ran.
PRODUCERS = (
    ("S-04", "control_ui.acceptance", "artifacts/acceptance/control_ui_smoke.json"),
    ("S-05", "fixtures.demo.acceptance", "artifacts/acceptance/demo_e2e.json"),
)


def log(msg: str) -> None:
    line = "[surfaces] %s" % msg
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", "backslashreplace").decode("ascii"), flush=True)


def sha256_file(path: str) -> Optional[str]:
    if not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with io.open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _load_gate():
    """The gate's own module, loaded from its path (it is a script)."""
    spec = importlib.util.spec_from_file_location("_jcc_final_gate_for_surfaces", GATE_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _env() -> Dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [ROOT, os.path.join(ROOT, "apps"), os.path.join(ROOT, "packages")])
    env["PYTHONIOENCODING"] = "utf-8"
    env["CODEBUDDY_SAFE_DELETE_ENABLED"] = "0"
    env["PAID_PROVIDER_CALLS"] = "0"
    env["EXTERNAL_PUBLISH"] = "0"
    return env


def _run_producer(module: str, out_log: str) -> Tuple[Optional[int], float]:
    """Run one producer with both streams on a file (never a pipe).

    A pipe's write end is inheritable, so a server the producer starts would hold
    it open and the reader would block until that server exits. The producers do
    stop their servers, but the rule is cheap and the failure mode is silent.
    """
    import time
    started = time.time()
    with io.open(out_log, "wb") as out:
        proc = subprocess.run([PY, "-m", module], cwd=ROOT, stdout=out,
                              stderr=subprocess.STDOUT, env=_env(), timeout=900)
    return proc.returncode, time.time() - started


def judge(records: List[Dict[str, Any]]) -> Tuple[bool, List[str]]:
    """The verdict rule, extracted so ``--selftest`` can falsify it.

    Every clause names one way the step could report a pass it did not earn.
    """
    problems: List[str] = []
    if not records:
        # A rule that passes when it examined nothing is the defect class this
        # whole pipeline exists to catch. There is no "no surfaces" outcome.
        return False, ["no surface was examined at all, so nothing was judged"]
    for r in records:
        name = r.get("surface")
        if r.get("producer_exit") != 0:
            problems.append("%s: the producer exited %r" % (name, r.get("producer_exit")))
        if not r.get("artifact_present"):
            problems.append("%s: the producer wrote no artifact (%s)"
                            % (name, r.get("artifact")))
            continue
        status = r.get("gate_status")
        if status != "OK":
            problems.append("%s: the gate judged it %r (only OK is accepted; DEGRADED is "
                            "what let 'not evidenced' ride into a PASS): %s"
                            % (name, status, str(r.get("gate_detail"))[:200]))
    return (not problems), problems


def run() -> int:
    os.makedirs(WORK, exist_ok=True)
    gate = _load_gate()
    records: List[Dict[str, Any]] = []

    for surface, module, artifact_rel in PRODUCERS:
        artifact = os.path.join(ROOT, *artifact_rel.split("/"))
        before = sha256_file(artifact)
        log("producing %s via `python -m %s`" % (surface, module))
        rc, took = _run_producer(module, os.path.join(WORK, "%s.log" % surface))
        after = sha256_file(artifact)
        log("  exit=%r in %.1fs; artifact %s -> %s"
            % (rc, took, (before or "<absent>")[:12], (after or "<absent>")[:12]))
        rec: Dict[str, Any] = {
            "surface": surface,
            "producer": "python -m %s" % module,
            "producer_exit": rc,
            "producer_seconds": round(took, 1),
            "log": os.path.relpath(os.path.join(WORK, "%s.log" % surface), ROOT)
                   .replace(os.sep, "/"),
            "artifact": artifact_rel,
            "artifact_present": os.path.isfile(artifact),
            "artifact_sha256": after,
            "artifact_sha256_before": before,
            "artifact_bytes": os.path.getsize(artifact) if os.path.isfile(artifact) else 0,
        }
        if rec["artifact_present"]:
            try:
                rec["artifact_says"] = {
                    k: json.load(io.open(artifact, encoding="utf-8-sig")).get(k)
                    for k in ("status", "passed", "screens_ok", "screens_expected")
                }
            except (OSError, ValueError) as exc:
                rec["artifact_says"] = {"unreadable": str(exc)[:160]}
        records.append(rec)

    # Re-judge with the gate's own functions. A fresh Gate per surface so a
    # problem in one cannot be hidden by the other's detail string.
    for rec, (surface, _module, _artifact) in zip(records, PRODUCERS):
        g = gate.Gate()
        try:
            if surface == "S-04":
                gate.check_s04_control_ui(g)
            else:
                gate.check_s05_demo_mode(g)
        except Exception as exc:  # noqa: BLE001 - a raising judge is a FAIL, named
            rec["gate_status"] = "JUDGE_RAISED"
            rec["gate_detail"] = "%s: %s" % (type(exc).__name__, exc)
            continue
        got = g.statuses_map().get(surface)
        detail = next((c["detail"] for c in g.checks if c["id"] == surface), "")
        rec["gate_status"] = got or "NOT_JUDGED"
        rec["gate_detail"] = detail
        log("  %s judged %s: %s" % (surface, rec["gate_status"], str(detail)[:160]))

    ok, problems = judge(records)
    payload = {
        "schema": "jcc-surface-evidence/1",
        "ok": ok,
        "status": "PASS" if ok else "FAIL",
        "verdict": ("PASS: both surfaces were regenerated from source and the gate's own "
                    "checks judged them OK" if ok
                    else "FAIL: " + "; ".join(problems)),
        "checked_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "question": ("are the S-04 / S-05 claims true of the tree as it stands NOW, with "
                     "the evidence regenerated rather than inspected?"),
        "why_the_gate_judges": ("check_s04_control_ui / check_s05_demo_mode are imported "
                                "from artifacts/acceptance/final_gate_run.py, so there is "
                                "one implementation of the claim, not two"),
        "surfaces": records,
        "problems": problems,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with io.open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False, sort_keys=True)
        fh.write("\n")
    log("verdict=%s -> %s" % (payload["status"], OUT))
    return 0 if ok else 1


def selftest() -> int:
    """Prove the surface rule refuses the ways this step could fake a pass."""
    cases: List[Tuple[str, bool, str]] = []

    def case(name, ok, detail=""):
        cases.append((name, ok, detail))

    good = {"surface": "S-04", "producer_exit": 0, "artifact_present": True,
            "gate_status": "OK", "gate_detail": "8 screens"}

    ok, problems = judge([good])
    case("accepts_a_regenerated_ok_surface", ok, "; ".join(problems))

    ok, problems = judge([dict(good, producer_exit=1)])
    case("refuses_a_producer_that_failed", not ok, "; ".join(problems))

    ok, problems = judge([dict(good, gate_status="DEGRADED")])
    case("refuses_degraded_because_degraded_is_how_not_evidenced_shipped",
         not ok, "; ".join(problems))

    ok, problems = judge([dict(good, gate_status="FAILED")])
    case("refuses_a_failed_surface", not ok, "; ".join(problems))

    ok, problems = judge([dict(good, artifact_present=False)])
    case("refuses_a_missing_artifact", not ok, "; ".join(problems))

    ok, problems = judge([good, dict(good, surface="S-05", gate_status="MISSING")])
    case("one_bad_surface_fails_the_step", not ok, "; ".join(problems))

    ok, problems = judge([])
    case("refuses_a_run_that_examined_nothing", not ok, "; ".join(problems))

    print("=" * 78)
    for name, passed, detail in cases:
        print("  [%s] %-62s %s" % ("PASS" if passed else "FAIL", name, detail[:90]))
    bad = [c for c in cases if not c[1]]
    print("=" * 78)
    print("regenerate_surfaces selftest: %d/%d" % (len(cases) - len(bad), len(cases)))
    return 0 if not bad else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="regenerate the S-04 / S-05 evidence")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not os.path.isfile(GATE_PY):
        log("ERROR the gate module is missing at %s; refusing to invent a second "
            "implementation of its checks" % GATE_PY)
        return 1
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
