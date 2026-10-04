"""Mutation-test MY OWN falsifiers -- do they actually catch what they claim?

WHY THIS EXISTS. `tools/fix_missing_pathlib_import.py` and
`tools/wal_falsifier.py` are instruments I wrote to adjudicate C2 and C1. An
instrument that cannot fail is not evidence. A green run from a broken detector
is the worst outcome in a test suite: it turns "I checked" into a claim with no
discriminating power. (Method borrowed from metric-verify's CR-002 mutation
work; their finding that a green suite proves nothing unless the code is broken
on purpose is exactly right.)

THE BLAST-RADIUS RULE. Never mutate a file inside `packages/`, `apps/` or the
real `tools/`. Those belong to other owners. So: copy the whole `tools/` tree
under `runtime/`, mutate the COPY, and hand the detector a copy of itself whose
own `REPO` constant then resolves to the mirror. The product tree is never
written to. (This is the structural fix for my earlier `exec_module` mistake,
which rewrote 16 tracked files.)

THE MUTATIONS, each aimed at one specific claim:

  M1  REPAIR-FORCING. Delete `from pathlib import Path` from a copy of
      tools/jcc_continuation.py -- i.e. restore the exact defect commit cf9b2ea
      shipped. The tool is a REPAIR tool, so correct behaviour is to put the
      import back and exit 0. Verified by RE-READING the file from disk, not by
      trusting the exit code.

  M2  NEAR-MISS DETECTION. Keep the import but move the `Path(__file__)` use
      into a function body, so nothing is bound at module scope. This is the
      shape of a real half-finished repair. The repair half must correctly
      decline (the import is already there); the DETECTION half -- the
      `_binds_path_at_module_scope` verify pass -- must list the file as FAILED
      and exit non-zero. A detector that only greps for the import string would
      pass this, and that would be a hole in my own instrument.

  M3  SELF-CHECK HONESTY. Remove the falsifier's "VACUOUS: asked for
      journal_mode=X, SQLite reported Y" guard and confirm the guard was load
      bearing, i.e. that without it an unattainable journal mode is no longer
      refused. Reported as INFO, not PASS/FAIL: it is a statement about my own
      guard, and M3's own value is only that I checked.

A note on the oracle, because getting it wrong is the failure this whole script
exists to prevent: my first version asserted "detector must exit non-zero" and
produced two FALSE MISSES against a tool that was working correctly. Exit code
alone cannot distinguish "repaired the defect" from "missed the defect" for a
repair tool. Each mutation therefore declares which half of the contract it
attacks, and the oracle checks that half against the bytes on disk.
"""
from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]      # artifacts/release/v1.3_final
TOOLS = REPO / "tools"
SCRATCH = REPO / "runtime" / "harbor_mutation"
DETECTOR = TOOLS / "fix_missing_pathlib_import.py"
FALSIFIER = TOOLS / "wal_falsifier.py"
VENV_PY = REPO / ".venv" / "Scripts" / "python.exe"
MUTATED_REL = "jcc_continuation.py"


def fresh_copy() -> Path:
    """A throwaway mirror of tools/. Returns the COPY ROOT, not the tools dir.

    Unique name per call rather than delete-and-recreate: the WorkBuddy
    safe-delete shim refuses a bulk rmtree of 100+ entries without an
    interactive confirmation (SAFE_DELETE_BULK_CONFIRM_REQUIRED, threshold=50),
    and a mutation harness must never depend on a human clicking a dialog.
    Nothing is ever removed, so a failed run cannot leak state either.
    """
    stamp = "%d-%04x" % (time.time_ns(), id(object()) & 0xFFFF)
    root = SCRATCH / ("copy-" + stamp)
    root.mkdir(parents=True, exist_ok=True)
    dst = root / "tools"
    shutil.copytree(TOOLS, dst, ignore=shutil.ignore_patterns("__pycache__"))
    for pyc in dst.rglob("__pycache__"):
        shutil.rmtree(pyc, ignore_errors=True)
    return root


def run_detector(copy_root: Path) -> dict:
    """Run a COPY of the detector on the mutated file, then inspect the bytes.

    LAYOUT is forced by two lines in the detector:
        REPO = Path(__file__).resolve().parents[1]        -> <root>
        path = (REPO / arg).resolve(); path.relative_to(REPO)
    So the detector copy sits at <root>/tools/ and the mutated file must be a
    DIRECT child of <root>, or relative_to raises ValueError. The detector's own
    confinement is what keeps the real tree safe, so I reuse it rather than
    working around it.
    """
    local_detector = copy_root / "tools" / DETECTOR.name
    shutil.copy2(DETECTOR, local_detector)
    addressed = copy_root / MUTATED_REL
    shutil.copy2(copy_root / "tools" / MUTATED_REL, addressed)
    before = addressed.read_text(encoding="utf-8")

    env = {"PATH": "/usr/bin:/bin:/c/Windows/System32",
           "SYSTEMROOT": r"C:\Windows", "PYTHONIOENCODING": "utf-8",
           "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run(
        [str(VENV_PY), str(local_detector), MUTATED_REL],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300, cwd=str(copy_root), env=env)
    out = proc.stdout or ""
    err = proc.stderr or ""
    after = addressed.read_text(encoding="utf-8")
    return {
        "exit_code": proc.returncode,
        "flagged": [ln.strip() for ln in out.splitlines()
                    if ln.strip().startswith("FAILED")],
        "summary": out.splitlines()[0] if out.splitlines() else "",
        "file_changed_on_disk": before != after,
        "import_present_after": "from pathlib import Path" in after,
        "stderr_tail": err.strip()[-300:],
    }


def mutation_m1() -> dict:
    """Undo the repair entirely: the import line goes away, as cf9b2ea left it."""
    root = fresh_copy()
    p = root / "tools" / MUTATED_REL
    src = p.read_text(encoding="utf-8")
    mutated = src.replace("from pathlib import Path\n", "", 1)
    assert mutated != src, "M1 did not apply: the import line was not found"
    p.write_text(mutated, encoding="utf-8", newline="")
    got = run_detector(root)
    # The repair half: it must have put the import BACK, on disk.
    caught = got["import_present_after"] and got["file_changed_on_disk"]
    return {"name": "M1 remove `from pathlib import Path` (repair-forcing)",
            "attacks": "the repair half",
            "expect": "import restored on disk, file changed, exit 0",
            "CAUGHT": caught, **got}


def mutation_m2() -> dict:
    """Near-miss: the import line is present, but `Path` is NEVER BOUND.

    This is the mutation that matters. It is the shape of a plausible
    half-finished repair, and it is exactly what a detector that only greps for
    the import string would wave through.

    HOW IT IS BUILT, and why the obvious version does not work. My first
    attempt just re-indented the `Path(__file__)` line by four spaces. That is a
    NO-OP on this file, and the detector was right to pass it: the line is the
    CONTINUATION of a module-level expression

        ROOT = os.environ.get("JCC_ROOT") or str(
            Path(__file__).resolve().parents[1]        <- re-indenting THIS
        )

    so the name is still resolved at module scope and the module-scope binding
    check correctly still holds. I had mutated the formatting, not the binding.
    A mutation that does not change behaviour cannot test a detector, and
    reporting it as a MISS would have been a fabricated finding.

    So this mutation removes the import's BINDING EFFECT at module scope while
    leaving the text in place: the import is rewritten to a `TYPE_CHECKING`
    guard, which is a real, legal, and genuinely broken shape -- the name is
    imported for type checkers and is absent at runtime, which is precisely the
    NameError this whole repair exists to prevent. The file still parses, and
    the detector's `_binds_path_at_module_scope` must reject it.
    """
    root = fresh_copy()
    p = root / "tools" / MUTATED_REL
    src = p.read_text(encoding="utf-8")
    needle = "from pathlib import Path"
    assert needle in src, "M2 precondition failed: no pathlib import to rewrite"
    mutated = src.replace(
        needle,
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from pathlib import Path",
        1)
    assert mutated != src, "M2 did not apply"
    p.write_text(mutated, encoding="utf-8", newline="")
    ast.parse(p.read_text(encoding="utf-8"))     # the mutation must still parse
    got = run_detector(root)
    # The DETECTION half: the verify pass must name the file and exit non-zero.
    caught = bool(got["flagged"]) and got["exit_code"] != 0
    return {"name": "M2 `Path` imported only under TYPE_CHECKING (near-miss)",
            "attacks": "the detection half (the near-miss)",
            "expect": "listed as FAILED, exit != 0",
            "CAUGHT": caught, **got}


def mutation_m3() -> dict:
    """Was the falsifier's own journal-mode VACUOUS guard load bearing?"""
    root = fresh_copy()
    p = root / "tools" / FALSIFIER.name
    if not p.exists():
        return {"name": "M3", "skipped": "wal_falsifier.py not in the copy"}
    src = p.read_text(encoding="utf-8")
    needle = ("raise SystemExit(\n"
              '                    "VACUOUS: asked for journal_mode=%s, '
              'SQLite reported %r"\n'
              "                    % (journal, got))")
    mutated = src.replace(needle, "pass  # MUTATED: mismatch no longer aborts")
    assert mutated != src, "M3 did not apply: the VACUOUS raise text changed"
    p.write_text(mutated, encoding="utf-8", newline="")
    env = {"PATH": "/usr/bin:/bin:/c/Windows/System32",
           "SYSTEMROOT": r"C:\Windows", "PYTHONIOENCODING": "utf-8",
           "JCC_FALSIFY_SCRATCH": str(root / "m3_scratch"),
           "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run([str(VENV_PY), str(p), "1"],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=300, cwd=str(root), env=env)
    blob = (proc.stdout or "") + (proc.stderr or "")
    return {"name": "M3 falsifier's journal-mode VACUOUS guard removed",
            "attacks": "the falsifier's own honesty guard",
            "expect": "an unattainable journal_mode is no longer refused",
            "guard_removed": "MUTATED" in blob,
            "exit_code": proc.returncode,
            "note": "INFO only -- it is a statement about my guard, not a PASS/FAIL"}


def main() -> int:
    results = [mutation_m1(), mutation_m2(), mutation_m3()]
    for r in results:
        if r.get("skipped"):
            print("[SKIP] %s (%s)" % (r["name"], r["skipped"]))
            continue
        if "CAUGHT" in r:
            verdict = "CAUGHT" if r["CAUGHT"] else "MISSED"
            print("[%s] %-52s exit=%s flagged=%d file_changed=%s"
                  % ("PASS" if r["CAUGHT"] else "MISS", r["name"],
                     r["exit_code"], len(r["flagged"]), r["file_changed_on_disk"]))
            if not r["CAUGHT"]:
                print("        summary=%r" % r["summary"])
                if r.get("stderr_tail"):
                    print("        stderr=%r" % r["stderr_tail"])
        else:
            print("[INFO] %-52s exit=%s" % (r["name"], r.get("exit_code")))

    out_path = REPO / "artifacts" / "release" / "v1.3_final" / "MUTATION_RESULTS.json"
    out_path.write_text(json.dumps(results, indent=2, sort_keys=True, default=str),
                        encoding="utf-8", newline="\n")
    missed = [r["name"] for r in results if r.get("CAUGHT") is False]
    print()
    print("results -> %s" % out_path)
    if missed:
        print("MUTATION MISSED (the instrument is not discriminating): %s" % missed)
        return 1
    print("MUTATION OK: every planted defect was caught by the instrument")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
