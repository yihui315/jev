#!/usr/bin/env python3
"""FINAL-PHASE INTEGRATION: three-layer provenance and the self-reference fix (F).

THREE INDEPENDENT CLAIMS, each measured on real code:

  1. `--record-moment` IS PURE. The gate is run twice over the SAME tree, once
     with `--record-moment` and once without. Everything except the `moment`
     block must be byte-identical, and the exit code must be identical. If the
     moment block leaked into any verdict field the gate would no longer be a
     fixed point of the tree -- which is exactly the self-reference that made the
     stop condition unreachable.

  2. THE PUBLISH STEP IS END-TO-END. The gate's real `--record-moment` output (the
     IGNORED staging result) is fed to `tools/finalize/publish_gate_result.py` and
     the TRACKED canonical summary must:
       * carry all THREE provenance layers,
       * have `final_evidence_sha is None` BY CONSTRUCTION (a file cannot name the
         commit that contains it),
       * have DROPPED the `moment` block (a re-committed verdict is a pure function
         of the tree),
       * preserve the verdict fields unchanged,
       * be byte-identical when the publisher runs twice (pure function of staging).

  3. IT FAILS CLOSED. A missing or verdict-less staging result makes the publisher
     exit 2 and write nothing.

P-01 IS NOT TOUCHED. A static guard asserts P-01 still fails on ANY unexplained
dirty path and still passes only on an empty `git status --porcelain`, and that
`check_provenance` (which reads P-01) remains the LAST check block in `main()`.

NOTHING TRACKED IS WRITTEN. The gate's `--json-out` goes to a scratch dir and the
publisher's `--canonical` too; `--no-log` suppresses the observation log. The real
`artifacts/acceptance/final_gate_result.json` is never read or written.

Run:
    python tools/tests/provenance_integration.py
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GATE = REPO / "artifacts" / "acceptance" / "final_gate_run.py"
PUBLISH = REPO / "tools" / "finalize" / "publish_gate_result.py"
CTRL = REPO / "tools" / "jcc_continuation.py"
BASE_PY = Path(r"C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe")
TZ = timezone(timedelta(hours=8))


def log(msg):
    print("[PR] %s" % msg, flush=True)


def _contract():
    """The proof contract, by path -- one implementation of the proof schema."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "jcc_proof_contract", str(REPO / "tools" / "continuation" / "proof_contract.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _git(*args) -> str:
    p = subprocess.run(["git"] + list(args), cwd=str(REPO), capture_output=True, text=True,
                       errors="replace")
    return (p.stdout or "").strip()


def _run(argv, timeout=600):
    p = subprocess.run([str(BASE_PY)] + list(argv), cwd=str(REPO), capture_output=True,
                       text=True, timeout=timeout, errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _read_json(path: Path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except ValueError:
        return None


def main() -> int:
    checks = []

    def check(name, ok, detail=""):
        checks.append((name, bool(ok), str(detail)[:180]))
        log("  [%s] %-54s %s" % ("PASS" if ok else "FAIL", name, str(detail)[:120]))

    stamp = datetime.now(TZ).strftime("%Y%m%d_%H%M%S")
    work = REPO / ".workbuddy" / "scratch" / "provenance" / stamp
    work.mkdir(parents=True, exist_ok=True)

    head = _git("rev-parse", "HEAD")

    # ------------------------------------------------------------------ claim 1
    with_moment = work / "gate_with_moment.json"
    plain = work / "gate_plain.json"
    rc_a, out_a = _run([str(GATE), "--json-out", str(with_moment), "--record-moment",
                        "--no-log", "--quiet"])
    rc_b, out_b = _run([str(GATE), "--json-out", str(plain), "--no-log", "--quiet"])
    va = _read_json(with_moment)
    vb = _read_json(plain)
    check("the gate produced a verdict in both runs", isinstance(va, dict) and isinstance(vb, dict),
          "rc_a=%s rc_b=%s" % (rc_a, rc_b))

    if isinstance(va, dict) and isinstance(vb, dict):
        check("the run WITHOUT --record-moment carries no moment block", "moment" not in vb,
              str(sorted(vb.keys()))[:120])
        check("the run WITH --record-moment carries a moment block", "moment" in va,
              str(sorted(va.keys()))[:120])
        stripped = {k: v for k, v in va.items() if k != "moment"}
        check("--record-moment changes NOTHING but the moment block", stripped == vb,
              "differing keys: %s" % (sorted(set(stripped) ^ set(vb)) or "none"))
        check("the two runs exited identically (pure outcome)", rc_a == rc_b,
              "rc_a=%s rc_b=%s" % (rc_a, rc_b))
        m = va.get("moment") or {}
        check("moment.evidence_pre_gate_sha is the commit the gate saw",
              m.get("evidence_pre_gate_sha") == head,
              "moment=%s head=%s" % (m.get("evidence_pre_gate_sha"), head))
        check("moment.final_evidence_sha is null (named later, by the manifest)",
              m.get("final_evidence_sha") is None, str(m.get("final_evidence_sha")))
        check("the two runs carry the same non-null product SHA",
              (va.get("provenance") or {}).get("product_sha") is not None
              and (va.get("provenance") or {}).get("product_sha")
              == (vb.get("provenance") or {}).get("product_sha"),
              "product_sha=%s" % (va.get("provenance") or {}).get("product_sha"))

    # ------------------------------------------------------------------ claim 2
    canonical = work / "published.json"
    rc_p, out_p = _run([str(PUBLISH), "--staging", str(with_moment), "--canonical",
                        str(canonical)])
    cv = _read_json(canonical)
    check("the publisher accepted the gate's staging result", rc_p == 0 and isinstance(cv, dict),
          "rc=%s %s" % (rc_p, out_p.strip()[:80]))
    if isinstance(cv, dict) and isinstance(va, dict):
        prov = cv.get("provenance") or {}
        check("the canonical summary DROPPED the moment block", "moment" not in cv,
              str(sorted(cv.keys()))[:120])
        check("layer 1 (product) is surfaced",
              prov.get("product_sha") == (va.get("provenance") or {}).get("product_sha"),
              str(prov.get("product_sha")))
        check("layer 2 (evidence pre-gate) is surfaced from the moment",
              prov.get("evidence_pre_gate_sha") == head,
              str(prov.get("evidence_pre_gate_sha")))
        check("layer 3 (final evidence) is null by construction",
              "final_evidence_sha" in prov and prov.get("final_evidence_sha") is None,
              str(prov.get("final_evidence_sha")))
        check("the verdict fields survive the publish",
              cv.get("project_complete") == va.get("project_complete")
              and cv.get("blocker") == va.get("blocker"),
              "complete=%s blocker=%s" % (cv.get("project_complete"), cv.get("blocker")))
    b1 = canonical.read_bytes() if canonical.is_file() else b""
    rc_p2, _ = _run([str(PUBLISH), "--staging", str(with_moment), "--canonical", str(canonical)])
    b2 = canonical.read_bytes() if canonical.is_file() else b""
    check("the publish is idempotent (pure function of staging)", b1 == b2 and b1 != b"",
          "bytes=%d rc=%s" % (len(b1), rc_p2))
    # The staging input must be read-only: publishing must not mutate it.
    staging_before = with_moment.read_bytes()
    _run([str(PUBLISH), "--staging", str(with_moment), "--canonical", str(work / "again.json")])
    check("the publisher never mutates its staging input",
          with_moment.read_bytes() == staging_before, "staging bytes unchanged")

    # ------------------------------------------------------------------ claim 3
    missing = work / "nope.json"
    target = work / "should_not_exist.json"
    rc_m, _ = _run([str(PUBLISH), "--staging", str(missing), "--canonical", str(target)])
    check("a missing staging result fails closed (exit 2, nothing written)",
          rc_m == 2 and not target.is_file(), "rc=%s wrote=%s" % (rc_m, target.is_file()))
    nov = work / "no_verdict.json"
    nov.write_text(json.dumps({"hello": "world"}), encoding="utf-8", newline="\n")
    t2 = work / "should_not_exist_2.json"
    rc_n, _ = _run([str(PUBLISH), "--staging", str(nov), "--canonical", str(t2)])
    check("a verdict-less staging result fails closed (exit 2, nothing written)",
          rc_n == 2 and not t2.is_file(), "rc=%s wrote=%s" % (rc_n, t2.is_file()))

    # ------------------------------------------------------- static P-01 guard
    src = GATE.read_text(encoding="utf-8")
    check("P-01 still fails on ANY non-empty git status (not weakened)",
          'g.fail("P-01"' in src and 'working tree is dirty' in src
          and 'if porcelain == "":' in src,
          "P-01 fail-closed block present")
    check("P-01 still passes ONLY on an empty porcelain",
          'g.ok("P-01", "working tree clean")' in src, "ok block present")
    i_def_main = src.find("def main(")
    i_call_e2e = src.find("check_e2e(g")
    i_call_final = src.find("check_final_evidence(g)")
    i_call_prov = src.find("check_provenance(g)")
    check("check_provenance (which reads P-01) is the LAST check block in main()",
          i_def_main < i_call_e2e < i_call_final < i_call_prov,
          "main=%d e2e=%d final=%d prov=%d" % (i_def_main, i_call_e2e, i_call_final, i_call_prov))
    check("the gate's --json-out is NOT defaulted to the TRACKED canonical path",
          'default=str(DEFAULT' not in src.split("--json-out")[1][:80],
          "--json-out has no tracked default")
    ctrl_src = CTRL.read_text(encoding="utf-8")
    check("the controller routes the gate result to the IGNORED staging path",
          ctrl_src.count("runtime/final-gate/final_gate_result.json") >= 2,
          "controller STEPS pass runtime/final-gate (result + json-out)")

    bad = [c for c in checks if not c[1]]
    print("=" * 90)
    print("provenance integration: %d/%d" % (len(checks) - len(bad), len(checks)))
    print("scratch: %s" % work)

    # The claim is only CHECKED if FINAL-12 can read a machine-readable verdict;
    # otherwise its evidence is a scratch file plus a sentence on a console.
    contract = _contract()
    proof = contract.build_proof("PROVENANCE_INTEGRATION_PROOF", checks,
                                 extra={"scratch_root": str(work), "head": head})
    path = contract.write_proof(str(REPO), "PROVENANCE_INTEGRATION_PROOF", proof)
    print("proof: %s (%s)" % (path, proof["status"]))
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
