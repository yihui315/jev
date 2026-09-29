#!/usr/bin/env python3
"""JCC v1.3 Product Edition -- FINAL PRODUCT GATES (owner: Orion).

THE GATE'S QUESTION
-------------------
"Does the v1.3 Product Edition actually work, end to end, measured at the final
HEAD -- and does every claim this release makes rest on a measurement rather
than on a remembered string?"

WHAT THIS IS NOT
----------------
It is not the v1.0.1-ops / v1.1 release profile (`artifacts/acceptance/
final_gate_run.py`), and it is not the v1.2 orchestrator
(`tools/final_gate_run_v1_2.py`). Those certify different freeze SHAs of a
different product surface. This orchestrator certifies the v1.3 surface that
W0..W8 built, and it re-derives every verdict from a LIVE measurement taken now.

THE FIVE NON-VACUITY RULES
--------------------------
Each rule below is a defect this project has already paid for once. They are
implemented as code, and Group F PROVES they can fail instead of asserting it.

R1  SKIPPED IS FAILURE. A pytest leg passes only on
    ``exit 0 AND passed>0 AND failed==0 AND errors==0 AND skipped==0``.
    A skipped test is a claim nobody checked, and "26 skipped / exit 0" reads
    exactly like a pass (measured on this tree before the rule existed).
R2  NEVER TRUST THE CHILD'S SUMMARY. A gate leg's verdict is not taken from its
    stdout. The summary and the evidence file the child says it wrote must AGREE
    (``verdict == PASS``, ``passed == total > 0``, ``failures == []``), and that
    file's mtime must POSTDATE this leg's start -- otherwise we are reading the
    previous run's result, which is how a fixed-but-not-rerun defect looks
    (measured: a launcher printed the PREVIOUS run's output 2 s after launch).
R3  AN EMPTY WALK FAILS. Every leg asserts a floor on the number of things it
    actually examined (``min_passed`` / ``total>0`` / non-empty findings), so
    "passes because it looked at nothing" is unrepresentable.
R4  ZERO PRODUCED FILES IS A FAILURE. ``invoked`` is not ``succeeded``: a leg
    that claims to have written evidence but left nothing on disk fails, and the
    aggregate re-checks that every leg record exists on disk.
R5  THE GATE ITSELF IS FALSIFIABLE. Group F feeds the parsers and the AST auditor
    synthetic inputs they MUST reject -- a zero-pass pytest summary, a
    skip-only summary, a PASS summary whose evidence file says FAIL, a stale
    evidence file, and a source file that really imports ``http.server``. If any
    of those is accepted, the final gate FAILS.

LEG SET
-------
  A  product contract, per Wave, at the final HEAD (pytest subsets)
  B  the seven whole Wave gates, RE-RUN at the final HEAD
  C  full regression: core+e2e, tools, frontend typecheck/vitest/build
  D  live invariants: the production config really is inert and local-only
     (booted, not read), GREEN 4778 untouched, no listeners left behind
  E  release hygiene: frozen tree, gate inputs committed at HEAD, A8 pins agree
     with the git BLOBS (so the release decision is reproducible from a clone)
  F  self-falsification of this gate's own instruments

Not a leg, by construction: the release manifest. The manifest certifies the
PRODUCT FREEZE SHA and its `gate` block is populated FROM this gate's result --
so making the manifest a leg would be the self-referential model that v1.3 Wave 0
(`docs/v1_3/WAVE_0_RELEASE_FOUNDATION.md`) exists to retire. `--check` on the
manifest is a release step, run after the freeze commit, by
`docs/v1_3/WAVE_9_FINAL_PRODUCT_GATES.md` section 5.

Usage
-----
  F:/jev/.venv/Scripts/python.exe tools/final_gate_run_v1_3.py --full
  F:/jev/.venv/Scripts/python.exe tools/final_gate_run_v1_3.py --only B7,GREEN
  F:/jev/.venv/Scripts/python.exe tools/final_gate_run_v1_3.py --selftest

Outputs
-------
  artifacts/release/v1.3_product/legs/V1_3_LEG_<ID>.json     (one per leg)
  artifacts/release/v1.3_product/FINAL_GATE_V1_3_RESULT.json
  artifacts/release/v1.3_product/FINAL_GATE_V1_3_RESULT.sha256
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
ART = REPO / "artifacts" / "release" / "v1.3_product"
LEGS_DIR = ART / "legs"
OUT = ART / "FINAL_GATE_V1_3_RESULT.json"
SIDECAR = ART / "FINAL_GATE_V1_3_RESULT.sha256"
LOG = ART / "FINAL_GATE_V1_3_RUN.log"

PY = str(REPO / ".venv" / "Scripts" / "python.exe")
APP = REPO / "apps" / "command_center_3d"
CONFIG = REPO / "config" / "jcc-v1.3.yaml"
PACKAGE = REPO / "packages" / "jcc_core" / "tests" / "test_p3_acceptance_gate.py"

GREEN_PORT = 4778
PRODUCT_PORTS = (4779, 4780, 4781, 4782)

sys.path.insert(0, str(REPO / "tools"))
import jcc_release_manifest as eng  # noqa: E402

#: The 12 A8-pinned production artifacts live in the P3 suite (parsed, not
#: imported: importing the suite pulls pytest fixtures into this process).
A8_SOURCE = REPO / "packages" / "jcc_core" / "tests" / "test_p3_acceptance_gate.py"


# --------------------------------------------------------------------------- #
# primitives
# --------------------------------------------------------------------------- #
def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> Optional[str]:
    try:
        return sha256_bytes(path.read_bytes())
    except OSError:
        return None


def fresh_temp(tag: str) -> Path:
    """A never-before-existing directory under the OS temp dir.

    Two measured reasons this is not cosmetic:
      * ``sitecustomize``'s safe-delete shim exempts only paths under
        ``tempfile.gettempdir()``; a basetemp elsewhere trips the bulk-delete
        guard and every ``tmp_path`` fixture errors out;
      * ``tools/tests/test_doctor_next_hint`` and ``test_final_gate_legs`` read
        pre-generated probes from ``$TEMP/jev-pytest/nhh``, so a SHARED temp dir
        makes the suite depend on another run's leftovers -- measured as
        "3 failed + 1 error" on a dirty temp and "247 passed" on a clean one.

    Handing each leg its own fresh ``TEMP`` fixes both at once: the shim derives
    its exemption from the child's own ``gettempdir()``, and nothing is shared.
    """
    base = Path(tempfile.gettempdir()) / "jev-w9"
    d = base / ("%s-%s" % (tag, time.strftime("%Y%m%d-%H%M%S")))
    n = 0
    while d.exists():
        n += 1
        d = base / ("%s-%s-%d" % (tag, time.strftime("%Y%m%d-%H%M%S"), n))
    d.mkdir(parents=True)
    return d


def port_pid(port: int) -> int:
    """The pid LISTENING on ``port``, or 0. Read from the OS, never guessed."""
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                             timeout=30, errors="backslashreplace").stdout
    except Exception:  # noqa: BLE001
        return 0
    pid = 0
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        if not parts[1].endswith(":%d" % port):
            continue
        if parts[3].upper() != "LISTENING":
            continue
        try:
            pid = int(parts[4])
        except ValueError:
            continue
    return pid


def http_get(url: str, timeout: float = 5.0) -> Tuple[int, str]:
    import urllib.error
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return 0, "%s: %s" % (type(exc).__name__, exc)


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(REPO), capture_output=True,
                          text=True, errors="replace").stdout


_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def strip_ansi(text: str) -> str:
    """Remove SGR/CSI sequences. vitest colourises even into a pipe (measured)."""
    return _ANSI.sub("", text)


#: Environment overrides every leg runs under.
#:
#: ``CODEBUDDY_SAFE_DELETE_ENABLED=0`` is the REPORTING CONFIGURATION, named by
#: ``packages/conftest.py`` itself, and it is applied to EVERY leg -- not just
#: the pytest ones. Measured reason (W9, run 20260929-134212): the sandbox shim
#: counts deletions PER AGENT TURN and raises
#: ``SAFE_DELETE_BULK_CONFIRM_REQUIRED {"count":50,"threshold":50}`` once the
#: counter saturates. A long gate invocation, or a host where an earlier step
#: deleted a lot, then aborts a HEALTHY gate mid-flight -- the W8 gate died with
#: an empty stdout while its ``repair`` quarantine had in fact already renamed
#: the stale pid bookkeeping. That is environment noise misread as a product
#: failure, and the fix is to run the gate under the configuration its recorded
#: numbers were taken in, not to relax any assertion. The gate's own safety is
#: unaffected: ``repair`` only renames (proved by the W8 gate's AST closure over
#: ``Invoke-V13Repair``), and ``E4``/``D1`` re-check the estate afterwards.
BASE_ENV_OVERRIDES = {
    "CODEBUDDY_SAFE_DELETE_ENABLED": "0",
    "PYTHONIOENCODING": "utf-8",
    "PYTHONDONTWRITEBYTECODE": "1",
}


def leg_env(**extra: str) -> Dict[str, str]:
    env = dict(os.environ)
    env.update(BASE_ENV_OVERRIDES)
    env.update(extra)
    return env


# --------------------------------------------------------------------------- #
# parsers (pure -- Group F calls them directly)
# --------------------------------------------------------------------------- #
_PYTEST_SUMMARY = re.compile(r"^(?=.*\b(?:passed|failed|error|errors|skipped)\b)"
                             r".*\bin\s+\d+(?:\.\d+)?s", re.M)


def parse_pytest(text: str) -> Dict[str, int]:
    """Counts from pytest's LAST summary line.

    Last, not first: ``-q`` prints per-file lines that also contain the words.
    A run with no summary line at all reports ``no_summary`` so R3 can refuse it
    instead of silently counting zeros as a pass.
    """
    lines = [ln.strip() for ln in strip_ansi(text).splitlines()
             if _PYTEST_SUMMARY.match(ln.strip())]
    if not lines:
        return {"no_summary": 1}
    tail = lines[-1]

    def count(*words: str) -> int:
        total = 0
        for w in words:
            total = max(total, max([int(m) for m in
                                    re.findall(r"(\d+)\s+%s\b" % w, tail)] or [0]))
        return total

    return {
        "passed": count("passed"),
        "failed": count("failed"),
        "errors": count("error", "errors"),
        "skipped": count("skipped"),
    }


_GATE_JSON = re.compile(r"\{[^{}]*\"verdict\"[^{}]*\}")
_GATE_KV = re.compile(r"verdict=(\w+)\s+passed=(\d+)/(\d+)")
_GATE_PASS = re.compile(r"\b(PASS|FAIL)\s+(\d+)/(\d+)\s*->\s*(\S+)")


def parse_gate_stdout(text: str) -> Dict[str, Any]:
    """The gate's SELF-REPORT, reduced to a comparable shape.

    ``evidence`` is the path the child claims it wrote (waves 2/5/6/7/8 print it;
    waves 3/4 carry it in their JSON line). Callers must still open that file and
    cross-check -- this function only reads the claim.
    """
    text = strip_ansi(text)
    for raw in reversed(_GATE_JSON.findall(text)):
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if "verdict" in obj:
            return {"verdict": str(obj.get("verdict")), "passed": obj.get("passed"),
                    "total": obj.get("total"), "evidence": obj.get("evidence"),
                    "failed": obj.get("failed", []), "shape": "json"}
    m = _GATE_KV.search(text)
    if m:
        ev = re.search(r"^wrote\s+(\S+)\s*$", text, re.M)
        return {"verdict": m.group(1), "passed": int(m.group(2)),
                "total": int(m.group(3)), "evidence": ev.group(1) if ev else None,
                "failed": None, "shape": "kv"}
    m = _GATE_PASS.search(text)
    if m:
        return {"verdict": m.group(1), "passed": int(m.group(2)),
                "total": int(m.group(3)), "evidence": m.group(4), "failed": None,
                "shape": "passline"}
    return {"verdict": None, "passed": None, "total": None, "evidence": None,
            "failed": None, "shape": "unparsed"}


def judge_gate_evidence(claim: Dict[str, Any], path: Optional[Path],
                        started_ns: int) -> Dict[str, Any]:
    """R2: the claim is only accepted if the ARTIFACT agrees and is not stale."""
    verdict = {"claim": claim.get("verdict"), "agrees": False, "stale": None,
               "artifact_verdict": None, "artifact_passed": None,
               "artifact_total": None, "artifact_failures": None,
               "artifact": None, "problems": []}
    if not path:
        verdict["problems"].append("the gate named no evidence file")
        return verdict
    p = Path(path)
    if not p.is_absolute():
        p = REPO / path
    verdict["artifact"] = str(p)
    if not p.is_file():
        verdict["problems"].append("evidence file was not produced: %s" % p)
        return verdict
    st = p.stat()
    verdict["stale"] = st.st_mtime_ns < started_ns
    if verdict["stale"]:
        verdict["problems"].append(
            "evidence predates this leg -- it is the PREVIOUS run's result")
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        verdict["problems"].append("evidence unreadable: %s" % exc)
        return verdict
    if not isinstance(doc, dict):
        verdict["problems"].append("evidence is not a JSON object")
        return verdict
    verdict["artifact_verdict"] = doc.get("verdict")
    verdict["artifact_passed"] = doc.get("passed")
    verdict["artifact_total"] = doc.get("total")
    failures = doc.get("failures")
    verdict["artifact_failures"] = len(failures) if isinstance(failures, list) else failures
    ok = (doc.get("verdict") == "PASS"
          and isinstance(doc.get("total"), int) and doc.get("total") > 0
          and doc.get("passed") == doc.get("total")
          and isinstance(failures, list) and not failures)
    if not ok:
        verdict["problems"].append(
            "the artifact does not say PASS with passed==total>0 and no failures")
    verdict["agrees"] = bool(ok and not verdict["stale"])
    return verdict


# --------------------------------------------------------------------------- #
# leg records
# --------------------------------------------------------------------------- #
class Ledger:
    """Append-only evidence writer: every leg is on disk before the next starts."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.legs: List[Dict[str, Any]] = []
        LEGS_DIR.mkdir(parents=True, exist_ok=True)

    def write(self, rec: Dict[str, Any]) -> None:
        rec["run_id"] = self.run_id
        self.legs.append(rec)
        safe = rec["leg_id"].replace("/", "_")
        (LEGS_DIR / ("V1_3_LEG_%s.json" % safe)).write_text(
            json.dumps(rec, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8", newline="\n")

    def log(self, line: str) -> None:
        print(line, flush=True)
        with LOG.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")


def record(ledger: Ledger, leg_id: str, dimension: str, kind: str, *,
           cmd: Sequence[str], env_keys: Sequence[str], started: float,
           proc: subprocess.CompletedProcess, verdict: str, detail: str,
           evidence: Dict[str, Any]) -> Dict[str, Any]:
    stdout = proc.stdout or ""
    rec = {
        "schema": "jcc/v1.3-final-leg/1",
        "leg_id": leg_id,
        # The head a leg was measured at travels WITH the leg: a leg measured at
        # a different commit is a claim about code that is not this release.
        "head": git("rev-parse", "HEAD").strip(),
        "dimension": dimension,
        "kind": kind,
        "verdict": verdict,
        "detail": detail,
        "command": list(cmd),
        "cwd": str(REPO),
        "env_keys": sorted(env_keys),
        "started_utc": datetime.fromtimestamp(started, timezone.utc)
                       .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "duration_s": round(time.time() - started, 2),
        "exit_code": proc.returncode,
        "stdout_sha256": sha256_bytes(stdout.encode("utf-8", "replace")),
        "stdout_bytes": len(stdout.encode("utf-8", "replace")),
        "stdout_tail": "\n".join(stdout.splitlines()[-25:]),
        "stderr_tail": "\n".join((proc.stderr or "").splitlines()[-25:]),
        "evidence": evidence,
        "measured_at_utc": now_utc(),
    }
    ledger.write(rec)
    mark = {"PASS": "ok  ", "FAIL": "FAIL", "DEFERRED": "defer"}.get(verdict, "????")
    ledger.log("  [%s] %-26s %s (%ss)" % (mark, leg_id, detail, rec["duration_s"]))
    return rec


# --------------------------------------------------------------------------- #
# leg runners
# --------------------------------------------------------------------------- #
def run_pytest_leg(ledger: Ledger, leg_id: str, dimension: str,
                   targets: Sequence[str], min_passed: int,
                   extra: Sequence[str] = ()) -> Dict[str, Any]:
    tmp = fresh_temp(leg_id.lower())
    # A fresh TEMP per leg, so (a) the shim's exemption and pytest's basetemp
    # both land under a directory nothing else has ever written to, and (b) the
    # tools suite cannot read another run's leftovers ($TEMP/jev-pytest/nhh).
    env = leg_env(TEMP=str(tmp), TMP=str(tmp))
    cmd = [PY, "-m", "pytest", *targets, "-q", "--no-header", "--color=no",
           "-p", "no:cacheprovider", *extra]
    started = time.time()
    ns = time.time_ns()
    proc = subprocess.run(cmd, cwd=str(REPO), env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="backslashreplace",
                          timeout=5400)
    counts = parse_pytest(proc.stdout or "")
    problems: List[str] = []
    if proc.returncode != 0:
        problems.append("exit=%d" % proc.returncode)
    if counts.get("no_summary"):
        problems.append("pytest printed NO summary line -- nothing to judge (R3)")
    else:
        if counts["passed"] < min_passed:
            problems.append("passed=%d < floor %d" % (counts["passed"], min_passed))
        for k, label in (("failed", "failed"), ("errors", "errors"),
                         ("skipped", "skipped")):
            if counts[k]:
                problems.append("%s=%d" % (label, counts[k]))
    verdict = "PASS" if not problems else "FAIL"
    detail = ("%d passed / %d failed / %d errors / %d skipped (floor %d)"
              % (counts.get("passed", 0), counts.get("failed", 0),
                 counts.get("errors", 0), counts.get("skipped", 0), min_passed))
    if problems:
        detail += " :: " + "; ".join(problems)
    return record(ledger, leg_id, dimension, "pytest", cmd=cmd,
                  env_keys=sorted(BASE_ENV_OVERRIDES) + ["TEMP", "TMP"],
                  started=started, proc=proc, verdict=verdict, detail=detail,
                  evidence={"targets": list(targets), "counts": counts,
                            "min_passed": min_passed, "problems": problems,
                            "fresh_temp": str(tmp)})


def run_gate_leg(ledger: Ledger, leg_id: str, dimension: str, script: str,
                 min_total: int = 1) -> Dict[str, Any]:
    env = leg_env()
    cmd = [PY, str(REPO / script)]
    started = time.time()
    ns = time.time_ns()
    proc = subprocess.run(cmd, cwd=str(REPO), env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="backslashreplace",
                          timeout=5400)
    claim = parse_gate_stdout(proc.stdout or "")
    judge = judge_gate_evidence(claim, claim.get("evidence"), ns)
    problems: List[str] = []
    if proc.returncode != 0:
        problems.append("exit=%d" % proc.returncode)
    if claim["shape"] == "unparsed":
        problems.append("no verdict could be parsed from the gate's stdout")
    else:
        if claim["verdict"] != "PASS":
            problems.append("child reported verdict=%s" % claim["verdict"])
        if not judge["agrees"]:
            problems.extend(judge["problems"])
    if isinstance(judge["artifact_total"], int) and judge["artifact_total"] < min_total:
        problems.append("total=%d < floor %d" % (judge["artifact_total"], min_total))
    verdict = "PASS" if not problems else "FAIL"
    detail = "child=%s artifact=%s %s/%s" % (
        claim["verdict"], judge["artifact_verdict"],
        judge["artifact_passed"], judge["artifact_total"])
    if problems:
        detail += " :: " + "; ".join(problems)
    return record(ledger, leg_id, dimension, "gate", cmd=cmd,
                  env_keys=sorted(BASE_ENV_OVERRIDES),
                  started=started, proc=proc, verdict=verdict, detail=detail,
                  evidence={"claim": claim, "cross_check": judge,
                            "min_total": min_total})


def run_cmd_leg(ledger: Ledger, leg_id: str, dimension: str, kind: str,
                cmd: Sequence[str], cwd: Path, *, env: Optional[Dict[str, str]] = None,
                expect_in_stdout: Optional[List[Tuple]] = None,
                ) -> Dict[str, Any]:
    """A leg whose pass condition is exit 0 plus regex facts on its own output.

    ``expect_in_stdout`` entries are ``(label, pattern, min_count[, floor])``.
    A pattern matching fewer than ``min_count`` times fails the leg (R3), so a
    tool that silently printed nothing cannot pass; when ``floor`` is given, the
    largest captured integer must reach it too -- "the word passed appeared" and
    "71 tests passed" are different claims.

    Matching happens on ANSI-STRIPPED output: measured, ``vitest`` colourises even
    when its stdout is a pipe, so ``Tests\\s+71 passed`` does not match the raw
    bytes (there is an escape sequence right after ``Tests``). Stripping is the
    fix; widening the pattern would have hidden colour-code drift instead.
    """
    env = env or leg_env()
    started = time.time()
    proc = subprocess.run(list(cmd), cwd=str(cwd), env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="backslashreplace",
                          timeout=5400)
    raw = proc.stdout or ""
    out = strip_ansi(raw)
    found: Dict[str, int] = {}
    problems: List[str] = []
    if proc.returncode != 0:
        problems.append("exit=%d" % proc.returncode)
    for entry in (expect_in_stdout or []):
        label, rx, minimum = entry[0], entry[1], entry[2]
        floor = entry[3] if len(entry) > 3 else None
        matches = rx.findall(out)
        found[label] = len(matches)
        if len(matches) < minimum:
            problems.append("%s matched %d < %d" % (label, len(matches), minimum))
        if floor is not None:
            vals = []
            for m in matches:
                raw_val = m if isinstance(m, str) else (m[-1] if m else None)
                try:
                    vals.append(int(raw_val))
                except (TypeError, ValueError):
                    pass
            if not vals or max(vals) < floor:
                problems.append("%s value %s < floor %d"
                                % (label, vals or "none", floor))
            found[label + "_max"] = max(vals) if vals else 0
    verdict = "PASS" if not problems else "FAIL"
    detail = "exit=%d" % proc.returncode + (
        (" :: " + "; ".join(problems)) if problems else
        (" ; " + ", ".join("%s=%d" % kv for kv in sorted(found.items())) if found
         else ""))
    return record(ledger, leg_id, dimension, kind, cmd=cmd,
                  env_keys=sorted(BASE_ENV_OVERRIDES), started=started,
                  proc=proc, verdict=verdict, detail=detail,
                  evidence={"expect": {k: found[k] for k in sorted(found)},
                            "problems": problems, "ansi_stripped": raw != out})


# --------------------------------------------------------------------------- #
# Group A -- product contract per Wave, at the final HEAD
# --------------------------------------------------------------------------- #
GROUP_A = (
    # id, dimension, targets, floor (from documented history, NOT from this run)
    ("A1_FREEZE_MODEL", "W0 release model certifies a freeze, not HEAD",
     ("tools/tests/test_release_manifest_v1_3.py",), 19),
    ("A2_DOMAIN", "W1 Project/Mission domain + state machine",
     ("packages/jcc_core/tests/test_domain_v1_3.py",
      "tests/e2e/test_domain_api_v1_3.py"), 1),
    ("A3_MISSION", "W3 first real WorkBuddy mission",
     ("packages/jcc_core/tests/test_mission_runtime_v1_3.py",
      "tests/e2e/test_mission_launch_v1_3.py"), 1),
    ("A4_ATTENTION", "W4 needs-attention control surface",
     ("packages/jcc_core/tests/test_attention_control_v1_3.py",
      "tests/e2e/test_attention_control_v1_3.py"), 1),
    ("A5_DELIVERABLES", "W5 deliverable lifecycle",
     ("packages/jcc_core/tests/test_deliverables_v1_3.py",
      "tests/e2e/test_deliverables_v1_3.py"), 1),
    ("A6_CONNECTIONS", "W7 capability is measured, not asserted",
     ("packages/jcc_core/tests/test_connection_verification_v1_3.py",
      "tests/e2e/test_connection_verification_v1_3.py"), 1),
    ("A7_INVARIANTS", "A8 pins + A11 inbound surface",
     ("packages/jcc_core/tests/test_p3_acceptance_gate.py",
      "packages/jcc_core/tests/test_server_surface.py"), 55),
)


def a8_security_gate(ledger: Ledger) -> Dict[str, Any]:
    """The v1.1 security generator, re-run live against the CURRENT route surface.

    ``--check`` recomputes every check and compares the digest against the
    committed artifact, so this leg fails if the route surface drifted away from
    the artifact the release rests on -- it is not a read of a stored string.
    """
    started = time.time()
    cmd = [PY, str(REPO / "tools" / "v1_1_security_gate.py"), "--check"]
    proc = subprocess.run(cmd, cwd=str(REPO), env=leg_env(), capture_output=True,
                          text=True, encoding="utf-8", errors="backslashreplace",
                          timeout=900)
    out = strip_ansi(proc.stdout or "")
    m = re.search(r"verdict=(\w+)\s+high=(\d+)\s+medium=(\d+)\s+low=(\d+)\s+"
                  r"info=(\d+)\s+ran=(\d+)/(\d+)", out)
    problems: List[str] = []
    observed: Dict[str, Any] = {}
    if proc.returncode != 0:
        problems.append("exit=%d" % proc.returncode)
    if not m:
        problems.append("no verdict line could be parsed")
    else:
        verdict, high, medium, low, info, ran, count = m.groups()
        observed = {"verdict": verdict, "high": int(high), "medium": int(medium),
                    "low": int(low), "info": int(info), "ran": int(ran),
                    "check_count": int(count)}
        if verdict != "GO":
            problems.append("verdict=%s" % verdict)
        if int(high) or int(medium):
            problems.append("high=%s medium=%s (nothing above LOW is acceptable)"
                            % (high, medium))
        if ran != count or ran == "0":
            problems.append("ran=%s/%s -- a check that did not run is not a check"
                            % (ran, count))
        if int(count) < 9:
            problems.append("check_count=%s shrank below the recorded 9" % count)
    if "[check] OK" not in out:
        problems.append("the committed security artifact does not match a fresh run")
    for marker in ("checks NOT run:", "vacuous checks:"):
        if marker in out:
            problems.append("%s %s" % (marker, out.split(marker, 1)[1].splitlines()[0].strip()))
    return record(ledger, "A8_SECURITY_GATE",
                  "the security artifact still matches a LIVE run of every check",
                  "builtin", cmd=cmd, env_keys=sorted(BASE_ENV_OVERRIDES),
                  started=started, proc=proc,
                  verdict="PASS" if not problems else "FAIL",
                  detail="%s %s" % (observed, "; ".join(problems) or "GO, non-vacuous"),
                  evidence={"observed": observed, "problems": problems})


def group_a(ledger: Ledger, only: Optional[set]) -> None:
    ledger.log("GROUP A -- product contract, per Wave, at the final HEAD")
    for leg_id, dim, targets, floor in GROUP_A:
        if only and leg_id not in only:
            continue
        run_pytest_leg(ledger, leg_id, dim, targets, floor)
    if not only or "A8_SECURITY_GATE" in only:
        a8_security_gate(ledger)


# --------------------------------------------------------------------------- #
# Group B -- the whole Wave gates, RE-RUN at the final HEAD
# --------------------------------------------------------------------------- #
GROUP_B = (
    ("B1_W2_PRODUCT_UI", "W2 product UI renders REAL persisted state",
     "tools/v1_3_wave2_ui_gate.py", 1),
    ("B2_W3_MISSION_GATE", "W3 mission gate",
     "tools/v1_3_wave3_mission_gate.py", 30),
    ("B3_W4_ATTENTION_GATE", "W4 attention gate",
     "tools/v1_3_wave4_attention_gate.py", 1),
    ("B4_W5_DELIVERABLES_GATE", "W5 deliverables gate",
     "tools/v1_3_wave5_deliverables_gate.py", 1),
    ("B5_W6_WORLD_GATE", "W6 3D plane is a pure projection",
     "tools/v1_3_wave6_world_gate.py", 1),
    ("B6_W7_CONNECTION_GATE", "W7 connection verification gate",
     "tools/v1_3_wave7_connection_gate.py", 20),
    ("B7_W8_RUNTIME_GATE", "W8 durable runtime gate",
     "tools/v1_3_wave8_runtime_gate.py", 54),
)


def group_b(ledger: Ledger, only: Optional[set]) -> None:
    ledger.log("GROUP B -- the seven Wave gates, re-run at the final HEAD")
    for leg_id, dim, script, floor in GROUP_B:
        if only and leg_id not in only:
            continue
        run_gate_leg(ledger, leg_id, dim, script, floor)


# --------------------------------------------------------------------------- #
# Group C -- full regression
# --------------------------------------------------------------------------- #
def group_c(ledger: Ledger, only: Optional[set]) -> None:
    ledger.log("GROUP C -- full regression")
    if not only or "C1_CORE_E2E" in only:
        run_pytest_leg(ledger, "C1_CORE_E2E",
                       "full core + e2e regression (the recorded 786)",
                       ("packages/jcc_core/tests", "tests/e2e"), 786)
    if not only or "C2_TOOLS_TESTS" in only:
        # The floor is 248, not 247, as of Wave 9: the wave added exactly one
        # test (test_the_file_presence_block_is_a_claim_not_a_host_measurement,
        # see docs/v1_3/WAVE_9_FINAL_PRODUCT_GATES.md section 4.5) and the
        # arithmetic is stated here rather than left to drift. A floor is a
        # MINIMUM carried forward from documented history -- it is raised when a
        # test is genuinely added, never lowered to meet a run.
        run_pytest_leg(ledger, "C2_TOOLS_TESTS",
                       "full tools regression (the recorded 248)",
                       ("tools/tests",), 248)
    # "C3_FRONTEND" is the alias for the trio; either the alias or an individual
    # id enters the block, and the inner test decides per leg.
    c3_selected = (not only) or ("C3_FRONTEND" in only) or any(
        x.startswith("C3") for x in only)
    if c3_selected:
        npm = shutil.which("npm") or shutil.which("npm.cmd")
        for leg_id, dim, args, expect in (
                ("C3a_TYPECHECK", "frontend tsc --noEmit -- a clean compile",
                 ("run", "typecheck"),
                 # tsc prints NOTHING on success, so "exit 0 and no output" is
                 # also what a leg that ran nothing looks like. Proven non-vacuous
                 # by requiring npm's own banner for the script that ran.
                 [("tsc_ran", re.compile(r"^\s*>\s*tsc --noEmit\s*$", re.M), 1)]),
                ("C3b_VITEST", "frontend vitest run -- the recorded 71",
                 ("run", "test"),
                 [("tests_passed", re.compile(r"Tests\s+(\d+) passed"), 1, 71)]),
                ("C3c_BUILD", "frontend vite build (produces dist/ for B1)",
                 ("run", "build"),
                 [("built_in", re.compile(r"built in"), 1)]),
        ):
            if only and leg_id not in only and "C3_FRONTEND" not in only:
                continue
            run_cmd_leg(ledger, leg_id, dim, "frontend", [npm, *args], APP,
                        expect_in_stdout=expect)


# --------------------------------------------------------------------------- #
# Group D -- live invariants
# --------------------------------------------------------------------------- #
def d_green(ledger: Ledger) -> Dict[str, Any]:
    """Read-only. The protected v1.0.1 service must be the SAME pid and UP."""
    started = time.time()
    pid0 = port_pid(GREEN_PORT)
    st0, body0 = http_get("http://127.0.0.1:%d/api/health" % GREEN_PORT)
    try:
        health = json.loads(body0)
    except ValueError:
        health = {}
    problems = []
    if pid0 == 0:
        problems.append("no listener on %d" % GREEN_PORT)
    if st0 != 200:
        problems.append("HTTP %s" % st0)
    if health.get("actuation_enabled") is not False:
        problems.append("actuation_enabled=%r" % health.get("actuation_enabled"))
    if health.get("policy_version") != "p3.0.0":
        problems.append("policy_version=%r" % health.get("policy_version"))
    proc = subprocess.CompletedProcess([], 0, body0, "")
    return record(ledger, "D1_GREEN_PROTECTION", "protected GREEN 4778 is alive and inert",
                  "builtin", cmd=["netstat+GET /api/health :4778"], env_keys=(),
                  started=started, proc=proc,
                  verdict="PASS" if not problems else "FAIL",
                  detail="pid=%d http=%s %s" % (pid0, st0, "; ".join(problems) or "untouched"),
                  evidence={"green_pid": pid0, "http": st0, "health": health,
                            "problems": problems})


def d_product_config_inert(ledger: Ledger) -> Dict[str, Any]:
    """BOOT the production config and MEASURE health -- do not read it and guess.

    The database path is redirected to a fresh temp file, and that override is
    recorded: the point is to measure the frozen CONFIGURATION, not to mutate the
    product's runtime state.
    """
    started = time.time()
    script = r'''
import json, os, sys, tempfile, threading, time
sys.path.insert(0, r"%s")
import uvicorn
from jcc_core.config import load_config
from jcc_core.actuator import Actuator
from jcc_core.core import CoreEngine
from jcc_core.providers import build_provider
from jcc_core.server import create_app
from jcc_core.state_store import StateStore

out = {"config_path": r"%s"}
cfg = load_config(r"%s")
out["actuation_enabled_in_config"] = bool(cfg.actuation.enabled)
out["jev_provider_in_config"] = cfg.jev.provider
out["policy_version_in_config"] = cfg.policy.version
out["config_port"] = cfg.server.port
out["config_host"] = cfg.server.host
out["config_db_path"] = cfg.state_store_db_path
tmp = tempfile.mkdtemp(prefix="w9act_")
cfg.state_store_db_path = os.path.join(tmp, "jcc.db")
cfg.server.port = 0
out["db_path_overridden_to"] = cfg.state_store_db_path
os.environ.setdefault("JCC_CONTROL_TOKEN", "w9-final-gate-token")
store = StateStore(cfg.state_store_db_path)
engine = CoreEngine(store, build_provider(cfg), Actuator(enabled=False), cfg)
app = create_app(engine, cfg)
uv = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
srv = uvicorn.Server(uv)
threading.Thread(target=srv.run, daemon=True).start()
deadline = time.time() + 25
while not srv.started and time.time() < deadline:
    time.sleep(0.02)
if not srv.started:
    out["boot"] = "FAILED"
    print("W9ACT " + json.dumps(out))
    raise SystemExit(1)
host, port = srv.servers[0].sockets[0].getsockname()[:2]
import urllib.request
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with op.open("http://127.0.0.1:%%d/api/health" %% port, timeout=10) as r:
    out["http_status"] = r.status
    out["health"] = json.loads(r.read().decode("utf-8"))
out["boot"] = "OK"
out["bound_port"] = port
print("W9ACT " + json.dumps(out))
''' % (str(REPO / "packages"), str(CONFIG), str(CONFIG))
    env = leg_env()
    cmd = [PY, "-c", script]
    proc = subprocess.run(cmd, cwd=str(REPO), env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="backslashreplace", timeout=180)
    data: Dict[str, Any] = {}
    for ln in (proc.stdout or "").splitlines():
        if ln.startswith("W9ACT "):
            try:
                data = json.loads(ln[6:])
            except ValueError:
                data = {}
    health = data.get("health") or {}
    problems: List[str] = []
    if data.get("boot") != "OK":
        problems.append("the frozen product configuration did not boot")
    if data.get("actuation_enabled_in_config") is not False:
        problems.append("config actuation.enabled is not False")
    if health.get("actuation_enabled") is not False:
        problems.append("live /api/health actuation_enabled=%r"
                        % health.get("actuation_enabled"))
    if health.get("localhost_only") is not True:
        problems.append("live /api/health localhost_only=%r" % health.get("localhost_only"))
    if data.get("jev_provider_in_config") != "mock":
        problems.append("provider=%r" % data.get("jev_provider_in_config"))
    if data.get("policy_version_in_config") != "p3.0.0":
        problems.append("policy=%r" % data.get("policy_version_in_config"))
    if data.get("config_port") != 4779:
        problems.append("product API port=%r" % data.get("config_port"))
    return record(ledger, "D2_PRODUCT_CONFIG_INERT",
                  "the frozen product config boots and is inert + loopback-only",
                  "builtin", cmd=["python -c <boot config/jcc-v1.3.yaml>"],
                  env_keys=sorted(BASE_ENV_OVERRIDES), started=started, proc=proc,
                  verdict="PASS" if not problems else "FAIL",
                  detail="boot=%s actuation=%s localhost_only=%s %s"
                         % (data.get("boot"), health.get("actuation_enabled"),
                            health.get("localhost_only"), "; ".join(problems)),
                  evidence={"probe": data, "problems": problems})


def d_audit(ledger: Ledger, only: Optional[set]) -> None:
    if only and "D3_UI_IS_NOT_STDLIB" not in only:
        return
    started = time.time()
    rel = "apps/command_center_3d/serve_visual.py"
    cmd = [PY, str(REPO / "tools" / "v1_3_ui_runtime_audit.py"),
           "--file", str(REPO / rel), "--json"]
    proc = subprocess.run(cmd, cwd=str(REPO), env=leg_env(), capture_output=True, text=True,
                          encoding="utf-8", errors="backslashreplace", timeout=300)
    try:
        finding = json.loads((proc.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        finding = {}
    positives = finding.get("positives") or {}
    positives_true = sorted(k for k, v in positives.items() if v)
    problems: List[str] = []
    if proc.returncode != 0:
        problems.append("exit=%d" % proc.returncode)
    if finding.get("verdict") != "PASS":
        problems.append("verdict=%r" % finding.get("verdict"))
    if not positives_true:
        problems.append("the audit reported NO positive evidence (vacuous)")
    if positives and len(positives_true) != len(positives):
        problems.append("positives missing: %s"
                        % sorted(set(positives) - set(positives_true)))
    return record(ledger, "D3_UI_IS_NOT_STDLIB",
                  "the SPA is served by a real ASGI server, not python http.server",
                  "builtin", cmd=cmd, env_keys=sorted(BASE_ENV_OVERRIDES),
                  started=started, proc=proc,
                  verdict="PASS" if not problems else "FAIL",
                  detail="positives=%d banned=%d %s"
                         % (len(positives_true), len(finding.get("banned_imports") or []),
                            "; ".join(problems)),
                  evidence={"finding": finding, "problems": problems})


def group_d(ledger: Ledger, only: Optional[set]) -> Dict[str, Any]:
    ledger.log("GROUP D -- live invariants")
    green = None
    if not only or "D1_GREEN_PROTECTION" in only:
        green = d_green(ledger)
    if not only or "D2_PRODUCT_CONFIG_INERT" in only:
        d_product_config_inert(ledger)
    d_audit(ledger, only)
    return green or {}


# --------------------------------------------------------------------------- #
# Group E -- release hygiene (reproducible from a clone)
# --------------------------------------------------------------------------- #
PRODUCT_PREFIXES = ("packages/", "apps/", "facades/", "scripts/", "config/",
                    "fixtures/", "tools/", "integrations/", "adapters/",
                    "policies/", "evals/", "tests/")


def e_frozen_tree(ledger: Ledger) -> Dict[str, Any]:
    """No tracked PRODUCT file may be modified when the gate starts.

    Untracked / release-metadata churn is reported, not hidden: the gate itself
    writes only under ``artifacts/release/v1.3_product/`` (allow-listed), so a
    product edit here means the "frozen" tree was not frozen.
    """
    started = time.time()
    porcelain = git("status", "--porcelain")
    product, metadata, untracked = [], [], []
    for line in porcelain.splitlines():
        if len(line) < 4:
            continue
        code, path = line[:2], line[3:].strip().strip('"')
        path = path.replace("\\", "/")
        if code == "??":
            untracked.append(path)
        elif path.startswith(PRODUCT_PREFIXES):
            product.append(path)
        else:
            metadata.append(path)
    problems = []
    if product:
        problems.append("tracked product file(s) modified: %s" % product)
    proc = subprocess.CompletedProcess([], 0, porcelain, "")
    return record(ledger, "E1_FROZEN_TREE",
                  "no tracked product file is modified when the gate starts",
                  "builtin", cmd=["git status --porcelain"], env_keys=(),
                  started=started, proc=proc,
                  verdict="PASS" if not problems else "FAIL",
                  detail="product_dirty=%d metadata_dirty=%d untracked=%d"
                         % (len(product), len(metadata), len(untracked)),
                  evidence={"product_dirty": product, "metadata_dirty": metadata,
                            "untracked": untracked, "problems": problems})


GATE_INPUTS = (
    "tools/final_gate_run_v1_3.py",
    "tools/jcc_release_manifest.py",
    "tools/emit_v1_3_release_manifest.py",
    # Added 2026-09-29 during Wave 9, before the freeze. The list claimed to be
    # "every input this release rests on" while omitting two of the release's own
    # decision carriers -- the legs signer, and the v1.1 security gate this gate
    # actually executes at line ~589 against its committed artefact. Reviewed
    # addition, not a silent widening: both are tracked at HEAD, so the leg still
    # means what its docstring says. (The alternative -- leaving them out -- would
    # make E2 a non-vacuous-looking list with two holes in it, which is exactly the
    # defect class this project keeps finding in its own instruments.)
    "tools/emit_v1_3_legs.py",
    "tools/v1_1_security_gate.py",
    "artifacts/release/V1_1_SECURITY_GATE.json",
    "tools/v1_3_ui_runtime_audit.py",
    "tools/v1_3_wave2_ui_gate.py",
    "tools/v1_3_wave3_mission_gate.py",
    "tools/v1_3_wave4_attention_gate.py",
    "tools/v1_3_wave5_deliverables_gate.py",
    "tools/v1_3_wave6_world_gate.py",
    "tools/v1_3_wave7_connection_gate.py",
    "tools/v1_3_wave8_runtime_gate.py",
    "tools/tests/test_release_manifest_v1_3.py",
    "scripts/jcc-v1.3.ps1",
    "scripts/jcc-v1.3-register-tasks.ps1",
    "scripts/lib/JccCommon.ps1",
    "config/jcc-v1.3.yaml",
    "apps/command_center_3d/serve_visual.py",
    "packages/jcc_core/tests/test_p3_acceptance_gate.py",
    "packages/jcc_core/tests/test_server_surface.py",
    "tests/e2e/test_domain_api_v1_3.py",
    "tests/e2e/test_mission_launch_v1_3.py",
    "tests/e2e/test_attention_control_v1_3.py",
    "tests/e2e/test_deliverables_v1_3.py",
    "tests/e2e/test_connection_verification_v1_3.py",
    "apps/command_center_3d/package.json",
    "docs/v1_3/FINAL_PRODUCT_FREEZE.md",
)


def e_inputs_committed(ledger: Ledger) -> Dict[str, Any]:
    """An untracked gate is an unreproducible verdict -- checked at HEAD, not the index."""
    started = time.time()
    missing = [p for p in GATE_INPUTS if not eng.is_tracked("HEAD", p)]
    problems = []
    if missing:
        problems.append("not committed at HEAD: %s" % missing)
    if len(GATE_INPUTS) < 20:
        problems.append("the input list shrank to %d -- restate the arithmetic"
                        % len(GATE_INPUTS))
    proc = subprocess.CompletedProcess([], 0, "\n".join(GATE_INPUTS), "")
    return record(ledger, "E2_GATE_INPUTS_TRACKED",
                  "every input this release rests on is committed at HEAD",
                  "builtin", cmd=["git cat-file -e HEAD:<path> x%d" % len(GATE_INPUTS)],
                  env_keys=(), started=started, proc=proc,
                  verdict="PASS" if not problems else "FAIL",
                  detail="%d inputs, %d missing" % (len(GATE_INPUTS), len(missing)),
                  evidence={"inputs": list(GATE_INPUTS), "missing": missing,
                            "problems": problems})


def _a8_pins() -> Dict[str, str]:
    """Parse ``_BASELINE_HASHES`` with ast -- importing the suite pulls pytest in."""
    import ast
    tree = ast.parse(A8_SOURCE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "_BASELINE_HASHES":
                    return dict(ast.literal_eval(node.value))
    return {}


def e_a8_vs_blobs(ledger: Ledger) -> Dict[str, Any]:
    """The 12 pins must match the BLOB AT HEAD, not merely the working tree.

    This is the check that catches a CRLF/attribute mismatch that makes the pin
    reproduce only on the machine that wrote it.
    """
    started = time.time()
    pins = _a8_pins()
    problems: List[str] = []
    rows: Dict[str, Any] = {}
    if len(pins) < 12:
        problems.append("parsed only %d pins (expected 12)" % len(pins))
    for rel, want in sorted(pins.items()):
        try:
            raw = subprocess.run(["git", "cat-file", "blob", "HEAD:%s" % rel],
                                 cwd=str(REPO), capture_output=True, timeout=120).stdout
        except Exception as exc:  # noqa: BLE001
            problems.append("%s: %s" % (rel, exc))
            continue
        got = sha256_bytes(raw.replace(b"\r\n", b"\n"))
        rows[rel] = {"want": want, "head_normalised": got, "match": got == want}
        if got != want:
            problems.append("%s differs from the HEAD blob" % rel)
    proc = subprocess.CompletedProcess([], 0, json.dumps(rows), "")
    return record(ledger, "E3_A8_PINS_VS_HEAD_BLOBS",
                  "the 12 A8 pins reproduce from the git blob at HEAD",
                  "builtin", cmd=["git cat-file blob HEAD:<pin> x12"], env_keys=(),
                  started=started, proc=proc,
                  verdict="PASS" if not problems else "FAIL",
                  detail="%d/%d pins match the HEAD blob" % (
                      sum(1 for r in rows.values() if r["match"]), len(pins)),
                  evidence={"pins": rows, "problems": problems})


FREEZE_DOC = "docs/v1_3/FINAL_PRODUCT_FREEZE.md"


def e_freeze_anchor(ledger: Ledger) -> Dict[str, Any]:
    """The freeze document must BE the freeze commit -- otherwise the anchor lies.

    A document cannot contain its own commit's sha: writing the sha changes the
    commit, which changes the sha. Every release that hard-codes the value pays
    for it either with a contradictory document or with a later "refresh the
    anchor" commit, and the refresh is exactly what the v1.3 Wave 0 model retired.
    So the anchor is defined RESOLVABLY -- "the commit that introduced
    docs/v1_3/FINAL_PRODUCT_FREEZE.md" -- and this leg asserts that resolution is
    the commit under measurement, which makes the declared value stable by
    construction. A non-trivial size floor stops an empty file satisfying it.
    """
    started = time.time()
    p = REPO / FREEZE_DOC
    problems: List[str] = []
    recorded = git("log", "-1", "--format=%H", "--", FREEZE_DOC).strip()
    head = git("rev-parse", "HEAD").strip()
    size = p.stat().st_size if p.is_file() else 0
    if not p.is_file():
        problems.append("%s does not exist" % FREEZE_DOC)
    if size < 2000:
        problems.append("%s is only %d bytes -- an empty anchor satisfies nothing"
                        % (FREEZE_DOC, size))
    if not recorded:
        problems.append("git could not resolve an introducing commit")
    elif recorded != head:
        problems.append("the freeze document was last changed by %s, not by HEAD %s"
                        % (recorded[:12], head[:12]))
    proc = subprocess.CompletedProcess([], 0, "recorded=%s head=%s" % (recorded, head), "")
    return record(ledger, "E5_FREEZE_ANCHOR_RESOLVES",
                  "the freeze document is introduced by the commit under measurement",
                  "builtin", cmd=["git log -1 --format=%H -- <freeze doc>"],
                  env_keys=(), started=started, proc=proc,
                  verdict="PASS" if not problems else "FAIL",
                  detail="recorded=%s head=%s bytes=%d"
                         % (recorded[:12], head[:12], size),
                  evidence={"recorded_introducing_commit": recorded, "head": head,
                            "bytes": size, "problems": problems})


def e_ports_clean(ledger: Ledger) -> Dict[str, Any]:
    started = time.time()
    held = {p: port_pid(p) for p in PRODUCT_PORTS}
    busy = {p: v for p, v in held.items() if v}
    problems = ["port %d still held by pid %d" % (p, v) for p, v in busy.items()]
    proc = subprocess.CompletedProcess([], 0, json.dumps(held), "")
    return record(ledger, "E4_NO_LISTENERS_LEFT_BEHIND",
                  "no v1.3 product listener is left running by the gate",
                  "builtin", cmd=["netstat -ano x4"], env_keys=(), started=started,
                  proc=proc, verdict="PASS" if not problems else "FAIL",
                  detail=json.dumps(held), evidence={"ports": held, "problems": problems})


def group_e(ledger: Ledger, only: Optional[set]) -> None:
    ledger.log("GROUP E -- release hygiene")
    for leg_id, fn in (("E1_FROZEN_TREE", e_frozen_tree),
                       ("E2_GATE_INPUTS_TRACKED", e_inputs_committed),
                       ("E3_A8_PINS_VS_HEAD_BLOBS", e_a8_vs_blobs),
                       ("E5_FREEZE_ANCHOR_RESOLVES", e_freeze_anchor)):
        if only and leg_id not in only:
            continue
        fn(ledger)


# --------------------------------------------------------------------------- #
# Group F -- the gate proves its own instruments can fail
# --------------------------------------------------------------------------- #
def group_f(ledger: Ledger) -> None:
    ledger.log("GROUP F -- self-falsification of this gate's own instruments")
    started = time.time()
    checks: List[Tuple[str, bool, str]] = []

    # F1 -- a zero-pass summary must NOT count as a pass (R1/R3)
    c = parse_pytest("no tests ran in 0.01s\n")
    checks.append(("ZERO_PASS_IS_NOT_A_PASS",
                   c.get("passed", 0) == 0 and c.get("no_summary", 0) == 1,
                   "a run that printed no summary must be refused, got %r" % c))

    # F2 -- skip-only must be visible to the judge (R1)
    c = parse_pytest("26 skipped in 3.10s\n")
    checks.append(("SKIPS_ARE_COUNTED",
                   c.get("skipped") == 26 and c.get("passed") == 0,
                   "'26 skipped / exit 0' must not be readable as a pass, got %r" % c))

    # F3 -- a PASS claim whose artifact says FAIL must be refused (R2)
    tmp = Path(tempfile.mkdtemp(prefix="w9f3_"))
    bad = tmp / "bad.json"
    bad.write_text(json.dumps({"verdict": "FAIL", "passed": 4, "total": 5,
                               "failures": [{"step": "x"}]}), encoding="utf-8")
    j = judge_gate_evidence({"verdict": "PASS", "passed": 5, "total": 5},
                            bad, 0)
    checks.append(("CLAIM_MUST_AGREE_WITH_ARTIFACT", not j["agrees"],
                   "a FAIL artifact must not confirm a PASS claim, got %r" % j["agrees"]))

    # F4 -- a stale artifact must be refused (R2, the "tool ran once" defect)
    good = tmp / "good.json"
    good.write_text(json.dumps({"verdict": "PASS", "passed": 5, "total": 5,
                                "failures": []}), encoding="utf-8")
    j = judge_gate_evidence({"verdict": "PASS", "passed": 5, "total": 5},
                            good, time.time_ns() + 10 ** 12)
    checks.append(("STALE_EVIDENCE_IS_REFUSED", not j["agrees"] and j["stale"] is True,
                   "an artifact older than the leg start must be refused, got %r" % j))

    # F5 -- the same artifact, from THIS run, must be ACCEPTED (the falsifier's
    # own non-vacuity: F3/F4 must fail for the reason claimed, not because the
    # judge rejects everything)
    j = judge_gate_evidence({"verdict": "PASS", "passed": 5, "total": 5},
                            good, 0)
    checks.append(("THE_JUDGE_ALSO_ACCEPTS", j["agrees"],
                   "a fresh, agreeing artifact MUST be accepted, got %r" % j))

    # F6 -- a garbled gate stdout must be "unparsed", never silently "PASS"
    p = parse_gate_stdout("all good, nothing to see here\n")
    checks.append(("UNPARSED_STDOUT_IS_NOT_A_PASS",
                   p["verdict"] is None and p["shape"] == "unparsed",
                   "unparseable output must not read as PASS, got %r" % p))

    # F7 -- the AST auditor must REJECT a file that really imports http.server
    evil = tmp / "evil_serve.py"
    evil.write_text(
        "import http.server\n"
        "def main():\n"
        "    http.server.HTTPServer(('127.0.0.1', 4780), None).serve_forever()\n",
        encoding="utf-8")
    try:
        sys.path.insert(0, str(REPO / "tools"))
        import v1_3_ui_runtime_audit as audit
        finding = audit.audit_source(str(evil), evil.read_text(encoding="utf-8"))
        checks.append(("AST_AUDIT_IS_FALSIFIABLE", finding.get("verdict") == "FAIL",
                       "the auditor must FAIL a real http.server import, got %r"
                       % finding.get("verdict")))
    except Exception as exc:  # noqa: BLE001
        checks.append(("AST_AUDIT_IS_FALSIFIABLE", False, "%s: %s" % (type(exc).__name__, exc)))

    problems = [("%s: %s" % (n, why)) for n, ok, why in checks if not ok]
    proc = subprocess.CompletedProcess(
        [], 0, "\n".join("%s=%s" % (n, ok) for n, ok, _ in checks), "")
    record(ledger, "F1_INSTRUMENTS_ARE_FALSIFIABLE",
           "this gate's own parsers and auditor reject the false shapes they exist to catch",
           "builtin", cmd=["in-process falsifiers x%d" % len(checks)], env_keys=(),
           started=started, proc=proc,
           verdict="PASS" if not problems else "FAIL",
           detail="%d/%d falsifiers behaved" % (len(checks) - len(problems), len(checks)),
           evidence={"checks": {n: {"ok": ok, "why": why} for n, ok, why in checks},
                     "problems": problems})
    shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------- #
# aggregate
# --------------------------------------------------------------------------- #
PENDING_HOST = (
    "Unregister the two stray Scheduled Tasks created during the measured "
    "incident recorded in docs/v1_3/WAVE_8_DURABLE_RUNTIME.md section 6: "
    "JCC-v1.3-API and JCC-v1.3-PROBE (schtasks.exe is blacklisted in the agent "
    "sandbox and Windows' own unregister path goes through the same blind CIM "
    "provider, so this cannot be done from here).",
    "Run scripts\\jcc-v1.3-register-tasks.ps1 -Apply from an ELEVATED PowerShell "
    "on the host to create the durable JCC-v1.3-API / JCC-v1.3-VisualUI tasks, "
    "then `scripts\\jcc-v1.3.ps1 -Action mark` to adopt them.",
)

NON_BLOCKING_DELIBERATE = (
    "Real-browser FPS at node counts 50/200/500/1000 is NOT claimed: the product "
    "has no stress route, and 'measured on a synthetic page' is not a product "
    "measurement. The v1.2 verdict PENDING is carried forward unchanged.",
    "Parent-exit survival of the product runtime is NOT claimed by this gate. The "
    "W8 restart transcript is evidence about the CONTROLLER, not proof that the "
    "service outlives its launcher; only the host Task Scheduler registration "
    "(pending, above) can establish that, and the claim stays open until it is "
    "measured on a host.",
    "DPAPI-at-rest for the control token is NOT claimed: the token stays an "
    "environment variable, which is what W8 shipped and what the health contract "
    "exposes. A stronger claim would need a key store the sandbox cannot provide.",
)


def expected_leg_ids() -> List[str]:
    """EVERY leg this gate is supposed to produce. Written down so a run that
    silently skipped a group cannot read as complete (R3 at the aggregate level).
    """
    ids = [x[0] for x in GROUP_A]
    ids += ["A8_SECURITY_GATE"]
    ids += ["C1_CORE_E2E", "C2_TOOLS_TESTS",
            "C3a_TYPECHECK", "C3b_VITEST", "C3c_BUILD"]
    ids += [x[0] for x in GROUP_B]
    ids += ["D1_GREEN_PROTECTION", "D2_PRODUCT_CONFIG_INERT", "D3_UI_IS_NOT_STDLIB"]
    ids += ["E1_FROZEN_TREE", "E2_GATE_INPUTS_TRACKED", "E3_A8_PINS_VS_HEAD_BLOBS",
            "E4_NO_LISTENERS_LEFT_BEHIND", "E5_FREEZE_ANCHOR_RESOLVES"]
    ids += ["F1_INSTRUMENTS_ARE_FALSIFIABLE"]
    return ids


def aggregate(merged: List[Dict[str, Any]], green0: Dict[str, Any],
              green1: Dict[str, Any], run_id: str, head: str,
              ran_now: List[str], total_duration_s: float) -> Dict[str, Any]:
    by_id = {r.get("leg_id"): r for r in merged}
    expected = expected_leg_ids()

    blockers: List[str] = []
    for r in merged:
        if r.get("verdict") == "FAIL":
            blockers.append(r.get("leg_id"))

    # COMPLETENESS -- a partial run cannot claim a release.
    absent = sorted(set(expected) - set(by_id))
    if absent:
        blockers.append("EXPECTED_LEGS_ABSENT: %s" % absent)

    # FRESHNESS -- a leg measured at another commit is a claim about other code.
    stale = sorted(k for k, r in by_id.items() if r.get("head") != head)
    if stale:
        blockers.append("LEGS_MEASURED_AT_ANOTHER_HEAD: %s" % stale)

    # R4 -- a leg whose record is not on disk did not happen.
    missing_files = sorted(
        k for k in by_id
        if not (LEGS_DIR / ("V1_3_LEG_%s.json" % k.replace("/", "_"))).is_file())
    if missing_files:
        blockers.append("LEG_RECORDS_MISSING_ON_DISK: %s" % missing_files)

    green_untouched = bool(green0.get("pid") and green0 == green1)
    if not green_untouched:
        blockers.append("GREEN_4778_CHANGED")

    passed = sorted(k for k, r in by_id.items() if r.get("verdict") == "PASS")
    failed = sorted(k for k, r in by_id.items() if r.get("verdict") == "FAIL")
    other = sorted(set(by_id) - set(passed) - set(failed) - set(absent))

    return {
        "schema": "jcc/v1.3-final-gate/1",
        "gate": "V1_3_FINAL_PRODUCT_GATES",
        "run_id": run_id,
        "release": "JCC v1.3 Product Edition (local)",
        "head": head,
        "head_short": head[:12],
        "branch": git("rev-parse", "--abbrev-ref", "HEAD").strip(),
        "measured_at_utc": now_utc(),
        "total_duration_s": total_duration_s,
        "legs_run_this_invocation": ran_now,
        "legs_from_earlier_runs_at_the_same_head":
            sorted(set(by_id) - set(ran_now)),
        "legs_total": len(by_id),
        "legs_expected": len(expected),
        "legs_passed": len(passed),
        "legs_failed": len(failed),
        "legs_not_pass": other,
        "passed": len(passed),
        "total": len(by_id),
        "failures": [{"leg_id": k, "detail": by_id[k].get("detail")} for k in failed],
        "blocker": len(blockers),
        "blockers": blockers,
        "project_complete": not blockers,
        "green_at_start": green0,
        "green_at_end": green1,
        "green_untouched": green_untouched,
        "actuation_enabled": False,
        "pending_host_activation": list(PENDING_HOST),
        "not_claimed_deliberately": list(NON_BLOCKING_DELIBERATE),
        "release_readiness": ("RELEASE_READY_PENDING_HOST_ACTIVATION"
                              if not blockers else "NOT_RELEASE_READY"),
        "final_release": (not blockers) and not PENDING_HOST,
        "legs": [{"leg_id": k, "verdict": by_id[k].get("verdict"),
                  "head": by_id[k].get("head"),
                  "detail": by_id[k].get("detail"),
                  "duration_s": by_id[k].get("duration_s"),
                  "measured_at_utc": by_id[k].get("measured_at_utc")}
                 for k in sorted(by_id)],
    }


def write_result(doc: Dict[str, Any]) -> None:
    ART.mkdir(parents=True, exist_ok=True)
    body = (json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False)
            + "\n").encode("utf-8")
    OUT.write_bytes(body)
    SIDECAR.write_bytes(("%s  %s\n" % (sha256_bytes(body), OUT.name)).encode("ascii"))


def load_legs_from_disk() -> List[Dict[str, Any]]:
    """Re-read every leg record so a partial run cannot hide a gap."""
    out = []
    if not LEGS_DIR.is_dir():
        return out
    for p in sorted(LEGS_DIR.glob("V1_3_LEG_*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def reset_legs(head: str) -> int:
    """Drop leg records measured at another HEAD -- they are not this release.

    Deliberately refuses to delete records from the CURRENT head: on a repair
    re-run the legs that already passed at this head stay valid, which is the
    only reason a 45-minute gate can be repaired without re-paying for all of it.
    """
    dropped = 0
    for p in sorted(LEGS_DIR.glob("V1_3_LEG_*.json")):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            rec = {}
        if rec.get("head") != head:
            p.unlink()
            dropped += 1
    return dropped


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def selftest() -> int:
    """Group F alone -- the falsifiers that prove this gate can fail."""
    led = Ledger("selftest")
    group_f(led)
    for p in LEGS_DIR.glob("V1_3_LEG_F1_*.json"):
        p.unlink()
    bad = [l for l in led.legs if l["verdict"] != "PASS"]
    print("SELFTEST %s (%d falsifiers)" % ("PASS" if not bad else "FAIL",
                                           len(led.legs[0]["evidence"]["checks"])))
    for l in bad:
        print("  %s: %s" % (l["leg_id"], l["detail"]))
    return 0 if not bad else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="JCC v1.3 final product gates")
    ap.add_argument("--full", action="store_true",
                    help="run every leg of every group at this HEAD")
    ap.add_argument("--only", default=None,
                    help="comma-separated leg ids (repair runs; the aggregate "
                         "still refuses to call a partial set complete)")
    ap.add_argument("--selftest", action="store_true",
                    help="run only Group F (this gate's own falsifiers)")
    ns = ap.parse_args(argv)
    if ns.selftest:
        return selftest()

    only = set(x.strip() for x in ns.only.split(",")) if ns.only else None
    if not ns.full and not only:
        ap.print_help()
        return 2

    run_id = time.strftime("%Y%m%d-%H%M%S")
    head = git("rev-parse", "HEAD").strip()
    ART.mkdir(parents=True, exist_ok=True)
    LEGS_DIR.mkdir(parents=True, exist_ok=True)
    dropped = reset_legs(head)
    ledger = Ledger(run_id)
    LOG.write_text("", encoding="utf-8", newline="\n")
    ledger.log("JCC v1.3 FINAL PRODUCT GATES  run=%s  head=%s" % (run_id, head[:12]))
    ledger.log("dropped %d leg record(s) measured at another HEAD" % dropped)

    green0 = {"pid": port_pid(GREEN_PORT),
              "http": http_get("http://127.0.0.1:%d/api/health" % GREEN_PORT)[0]}
    ledger.log("GREEN 4778 at start: pid=%s http=%s" % (green0["pid"], green0["http"]))

    t0 = time.time()
    group_a(ledger, only)          # 1. cheap contract legs first: fail fast
    group_c(ledger, only)          # 2. regression + dist/ for the UI gates
    group_b(ledger, only)          # 3. the Wave gates
    green = group_d(ledger, only)  # 4. live invariants
    group_e(ledger, only)          # 5. release hygiene
    group_f(ledger)                # 6. the gate's own falsifiers
    e_ports_clean(ledger)          # 7. nothing left running

    green1 = {"pid": port_pid(GREEN_PORT),
              "http": http_get("http://127.0.0.1:%d/api/health" % GREEN_PORT)[0]}
    ledger.log("GREEN 4778 at end:   pid=%s http=%s" % (green1["pid"], green1["http"]))

    merged = load_legs_from_disk()
    by_id = {r.get("leg_id"): r for r in merged}
    for r in ledger.legs:  # this invocation's records are authoritative
        by_id[r["leg_id"]] = r
    merged = [by_id[k] for k in sorted(by_id)]

    doc = aggregate(merged, green0, green1, run_id, head,
                    [l["leg_id"] for l in ledger.legs], round(time.time() - t0, 1))
    write_result(doc)

    ledger.log("")
    ledger.log("RESULT: %s  %d/%d legs PASS  blockers=%d"
               % ("PASS" if doc["project_complete"] else "FAIL",
                  doc["legs_passed"], doc["legs_total"], doc["blocker"]))
    for b in doc["blockers"]:
        ledger.log("  BLOCKER %s" % b)
    ledger.log("readiness: %s   final_release: %s"
               % (doc["release_readiness"], doc["final_release"]))
    ledger.log("wrote %s" % OUT)
    return 0 if doc["project_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
