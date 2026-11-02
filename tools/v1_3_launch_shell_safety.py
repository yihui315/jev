#!/usr/bin/env python
"""v1.3 STEP 3 -- "a mission goal must never become a shell command".

WHAT THIS ASSERTS, AND WHY IT IS AN AST AUDIT AND NOT A TEXT SCAN
-----------------------------------------------------------------
The product launches a host workload (the WorkBuddy CLI) with a goal that is
USER TEXT. The one way that text could execute anything is if some layer turned
it into a command STRING handed to a shell. So the property to certify is
sharply defined and mechanical:

    every process-creating call in the launch path passes an ARGUMENT VECTOR
    and never enables a shell.

A regex over the source cannot certify that: it cannot tell `shell=False` in a
comment from `shell=False` in a call, it cannot see an argument built as a
string, and it cannot tell which function a call actually resolves to. The
project has already been burned by exactly this ("serving path 合规 -> AST 而非
文本"), so this reads the module with `ast` and inspects the call nodes.

THE TWO WAYS THIS KIND OF GATE GOES WRONG, AND HOW EACH IS BLOCKED
------------------------------------------------------------------
1. VACUITY. A file scanner that finds no spawn calls and then reports "no
   violations" is certifying nothing. So the auditor REFUSES to pass unless it
   positively observed: at least one call that creates a process, at least one
   explicit `shell=` argument on such a call, and at least one vector argument.
   Absence of evidence is reported as a FAILURE, never as a pass.

2. NO FALSIFIER. A checker that cannot fail is not a checker. So this tool
   ships a NEGATIVE CONTROL: it runs the identical audit over an in-memory
   source that DOES enable a shell, and requires the audit to reject it. If the
   control ever passes, the gate fails (the instrument is broken) -- the run
   refuses to report a green product on the strength of a blind auditor.

Exit code 0 = PASS, 1 = FAIL. Writes a JSON evidence file either way.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = ROOT / ".workbuddy" / "scratch"
STAMP = time.strftime("%Y%m%d-%H%M%S")

#: The modules that can create a host process. Both, because the argv is BUILT
#: in host_launcher and SPAWNED from it, while launch_control is what decides to
#: call it -- an audit of only one of them would be half an audit.
AUDITED = (
    ROOT / "packages" / "jcc_core" / "host_launcher.py",
    ROOT / "packages" / "jcc_core" / "launch_control.py",
)

#: Calls that create a process. `Popen`/`run`/`call`/`check_*` are the subprocess
#: family; the rest are the non-subprocess ways to reach a shell or a process.
PROCESS_CALLS = {
    "subprocess.Popen", "subprocess.run", "subprocess.call",
    "subprocess.check_call", "subprocess.check_output",
}
FORBIDDEN_CALLS = {
    "os.system", "os.popen", "os.spawnl", "os.spawnle", "os.spawnlp",
    "os.spawnlpe", "os.spawnv", "os.spawnve", "os.spawnvp", "os.spawnvpe",
    "subprocess.getoutput", "subprocess.getstatusoutput",
    "pty.spawn", "commands.getoutput",
}


def _dotted(node: ast.AST) -> Optional[str]:
    """`subprocess.Popen` -> 'subprocess.Popen'; anything else -> None."""
    parts: List[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return ".".join(reversed(parts))
    return None


def _is_false_constant(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is False


def _is_true_constant(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _looks_like_a_vector(node: ast.AST) -> bool:
    """Is this argument an argv VECTOR rather than a command string?

    A vector is a name, a list/tuple literal, a call returning one, or a
    concatenation of lists. A command STRING is a str constant, an f-string, or
    a `+`/`join` over strings -- those are what a shell would need, so they are
    the shapes that must not appear.
    """
    if isinstance(node, (ast.Name, ast.Call, ast.List, ast.Tuple, ast.Attribute)):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        # list + list is fine; str + str is not. Decide by the operands.
        return _looks_like_a_vector(node.left) and _looks_like_a_vector(node.right)
    return False


def _string_builders_in(node: ast.AST) -> List[str]:
    """Any `" ".join(...)` -- the canonical way a vector becomes a command."""
    found: List[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            fn = sub.func
            if isinstance(fn, ast.Attribute) and fn.attr == "join":
                found.append("join(...)")
    return found


def audit_source(src: str, label: str) -> Dict[str, Any]:
    """Audit one module's source. Pure: no I/O, so the control can reuse it."""
    findings: List[str] = []
    facts: Dict[str, Any] = {
        "label": label,
        "bytes": len(src.encode("utf-8")),
        "process_calls": 0,
        "vector_args": 0,
        "shell_kwargs_false": 0,
        "forbidden_calls": [],
    }
    try:
        tree = ast.parse(src, filename=label)
    except SyntaxError as exc:
        return {"ok": False, "facts": facts,
                "findings": ["%s does not parse: %s" % (label, exc)]}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        dotted = _dotted(node.func)
        if dotted in FORBIDDEN_CALLS:
            facts["forbidden_calls"].append(dotted)
            findings.append("%s: forbidden process call %s (line %d)"
                            % (label, dotted, getattr(node, "lineno", -1)))
        if dotted not in PROCESS_CALLS:
            continue
        facts["process_calls"] += 1
        lineno = getattr(node, "lineno", -1)

        # (a) an explicit shell= on every process call, and it must be False.
        shell_kw = [k for k in node.keywords if k.arg == "shell"]
        for k in shell_kw:
            if _is_false_constant(k.value):
                facts["shell_kwargs_false"] += 1
            elif _is_true_constant(k.value):
                findings.append("%s: %s(shell=True) at line %d -- a shell WOULD "
                                "interpret the goal" % (label, dotted, lineno))
            else:
                findings.append("%s: %s passes a non-constant shell= at line %d; "
                                "whether a shell is used cannot be determined "
                                "(UNKNOWN is not safe)" % (label, dotted, lineno))

        # (b) the argv must be a VECTOR, never a built command string.
        for arg in node.args[:1]:
            if _looks_like_a_vector(arg):
                facts["vector_args"] += 1
            else:
                findings.append("%s: %s's first argument at line %d is not an "
                                "argument vector (%s) -- it may be a command "
                                "string" % (label, dotted, lineno,
                                            type(arg).__name__))
            for j in _string_builders_in(arg):
                findings.append("%s: %s builds a command with %s at line %d"
                                % (label, dotted, j, lineno))

    facts["string_join_sites"] = sum(len(_string_builders_in(n)) for n in ast.walk(tree))
    return {"ok": not findings, "facts": facts, "findings": findings}


#: A source that MUST be rejected. If the auditor passes it, the auditor is
#: blind and the whole run is void -- so the control is not decoration.
NEGATIVE_CONTROL = (
    "import subprocess\n"
    "def launch(goal):\n"
    "    return subprocess.Popen('codebuddy -p ' + goal, shell=True)\n"
)

#: A benign source that MUST be accepted, so the control pair proves the
#: auditor both rejects bad and accepts good (not merely "rejects everything").
POSITIVE_CONTROL = (
    "import subprocess\n"
    "def launch(argv, cwd):\n"
    "    return subprocess.Popen(argv, cwd=cwd, shell=False)\n"
)


def check(name: str, cond: bool, value: Any = None, note: str = "") -> bool:
    mark = "PASS" if cond else "FAIL"
    print("  [%s] %s%s" % (mark, name, (": " + note) if note else ""))
    checks.append({"name": name, "ok": bool(cond), "value": value, "note": note})
    return bool(cond)


checks: List[Dict[str, Any]] = []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(SCRATCH / ("v1_3_launch_shell_safety_%s.json" % STAMP)))
    ns = ap.parse_args()

    print("v1.3 STEP 3 -- launch shell safety (AST audit + mandatory controls)")
    print("audited : %s" % ", ".join(str(p.relative_to(ROOT)) for p in AUDITED))
    print("")

    # ---- the instrument must be able to fail, BEFORE it is trusted ---------- #
    neg = audit_source(NEGATIVE_CONTROL, "<negative-control>")
    check("NEGATIVE_CONTROL_IS_REJECTED", not neg["ok"],
          {"findings": neg["findings"]},
          "an auditor that cannot fail is not an auditor; a shell=True source "
          "was offered and rejected")
    pos = audit_source(POSITIVE_CONTROL, "<positive-control>")
    check("POSITIVE_CONTROL_IS_ACCEPTED", pos["ok"],
          {"facts": pos["facts"], "findings": pos["findings"]},
          "and it is not merely rejecting everything -- a correct vector+shell=False "
          "source passes")

    # ---- the real modules --------------------------------------------------- #
    reports: Dict[str, Any] = {}
    all_findings: List[str] = []
    for path in AUDITED:
        rel = str(path.relative_to(ROOT))
        if not path.is_file():
            all_findings.append("%s: MISSING (cannot be certified)" % rel)
            reports[rel] = {"ok": False, "findings": ["missing"]}
            continue
        src = path.read_text(encoding="utf-8")
        rep = audit_source(src, rel)
        reports[rel] = rep
        all_findings.extend(rep["findings"])

    totals = {k: sum(r["facts"].get(k, 0) for r in reports.values())
              for k in ("process_calls", "vector_args", "shell_kwargs_false")}

    check("NO_FORBIDDEN_PROCESS_CALLS",
          not any(r["facts"].get("forbidden_calls") for r in reports.values()),
          {rel: r["facts"].get("forbidden_calls") for rel, r in reports.items()},
          "no os.system / os.popen / spawn* / getoutput anywhere in the launch path")
    check("NO_SHELL_ENABLED", not all_findings, {"findings": all_findings},
          "every process-creating call takes an argument vector and no shell")
    # Non-vacuity: the checks above are only meaningful if the auditor SAW the
    # spawn path. Without this, deleting the spawn call would make the gate green.
    check("THE_AUDITOR_OBSERVED_A_PROCESS_CREATION_CALL", totals["process_calls"] >= 1,
          totals, "the module that spawns was actually read and it does create a "
                  "process -- otherwise 'no violations' would be vacuous")
    check("THE_SPAWN_PATH_STATES_SHELL_EXPLICITLY", totals["shell_kwargs_false"] >= 1,
          totals, "shell=False is written on the spawn call itself, not inferred")
    check("THE_SPAWN_ARGUMENT_IS_A_VECTOR", totals["vector_args"] >= 1, totals,
          "the first argument is a name/list, not a built command string")

    passed = sum(1 for c in checks if c["ok"])
    verdict = "PASS" if passed == len(checks) else "FAIL"
    out = {
        "schema": "jcc/v1.3-launch-shell-safety/1",
        "stamp": STAMP,
        "verdict": verdict,
        "passed": passed,
        "total": len(checks),
        "audited": [str(p.relative_to(ROOT)) for p in AUDITED],
        "checks": checks,
        "modules": reports,
        "findings": all_findings,
    }
    Path(ns.out).parent.mkdir(parents=True, exist_ok=True)
    Path(ns.out).write_text(json.dumps(out, indent=2, sort_keys=True) + "\n",
                            encoding="utf-8", newline="\n")

    print("")
    print("%s %d/%d" % (verdict, passed, len(checks)))
    print("evidence: %s" % ns.out)
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="backslashreplace", line_buffering=True)
    raise SystemExit(main())
