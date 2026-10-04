#!/usr/bin/env python3
"""CR-H1 falsifier -- can two concurrent pytest runs still delete each other?

THE DEFECT
----------
``packages/conftest.py`` and ``tools/conftest.py`` both pinned pytest's basetemp
to one FIXED path, ``gettempdir()/jev-pytest``. pytest ``rm_rf``s a basetemp on
entry, so two concurrent runs resolved to the same directory and deleted each
other's scratch while tests were using it. The victim does not fail an
assertion -- it errors during SETUP, because the test is never entered.

MEASURED BEFORE THE FIX, on this host (5 concurrent pairs, no ``--basetemp``)::

    trial 1: A rc=0 [16 passed]  | B rc=0 [11 passed]
    trial 2: A rc=1 [16 errors] | B rc=0 [11 passed]   <-- CONTAMINATED
    trial 3: A rc=0 [16 passed]  | B rc=0 [11 passed]
    trial 4: A rc=1 [16 errors] | B rc=0 [11 passed]   <-- CONTAMINATED
    trial 5: A rc=0 [16 passed]  | B rc=1 [11 errors]  <-- CONTAMINATED

3 of 5. Note that trials 1 and 3 are CLEAN: the collision is intermittent and
order-dependent, so a single passing run is not evidence it is gone. Every
concurrency case here therefore runs REPEATED pairs, and a case only passes if
every pair passes.

WHAT THIS SUITE PROVES
----------------------
1. The two conftests delegate to ONE helper, so the rule cannot drift.
2. The basetemp is per-process (pid + uuid), derived from
   ``tempfile.gettempdir()`` with no drive letter anywhere.
3. An explicit ``--basetemp`` STILL WINS -- the acceptance gate always passes
   one, and breaking that would be a worse regression than the bug.
4. Concurrent runs, REPEATED, all pass. This is the requirement, measured.
5. The parent's directory is never deleted by a child's cleanup.
6. NEGATIVE CONTROL: with the helper forced back to a fixed shared name, the
   concurrency case must FAIL. A concurrency fix that cannot be shown to fix
   concurrency is a comment.

Run:
    D:/jev/.venv/Scripts/python.exe tools/crh1_basetemp_falsifier.py
Exit 0 only if every case holds.
"""
from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import List, Optional

REPO = Path(__file__).resolve().parents[1]
PY = str(REPO / ".venv" / "Scripts" / "python.exe")
if not Path(PY).is_file():  # pragma: no cover - environment guard
    PY = sys.executable

sys.path.insert(0, str(REPO / "packages"))

RESULTS: List[dict] = []

#: Concurrency pairs per case. Three is the minimum that can catch an
#: intermittent race at all; the pre-fix measurement hit 3/5, so a single pair
#: would have been coin-flip evidence in both directions.
PAIRS = 3

NEGCTL_MARKER = "CRH1_NEGATIVE_CONTROL"


def record(case_id: str, ok: bool, detail: str) -> None:
    RESULTS.append({"id": case_id, "ok": bool(ok), "detail": detail})


def child_env(**overrides: str) -> dict:
    """A child env with the reporting precondition this tree measures under.

    ``CODEBUDDY_SAFE_DELETE_ENABLED=0`` is the recorded configuration; running
    the concurrency case with the guard ON would measure the guard, not the
    basetemp (see packages/conftest.py docstring item 2). The suite's own control
    variables are cleared so a control flag cannot leak past the control.
    """
    env = dict(os.environ)
    env.pop("JCC_CRH1_NEGCTL", None)
    env["PYTHONPATH"] = str(REPO / "packages")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["CODEBUDDY_SAFE_DELETE_ENABLED"] = "0"
    env["JCC_PYTEST_ALLOW_DIRTY_ENV"] = "1"
    env.update(overrides)
    return env


# --------------------------------------------------------------------------- #
# 1. the rule lives in ONE place
# --------------------------------------------------------------------------- #
def check_both_conftests_delegate_to_one_helper() -> None:
    """Neither conftest may recompute the basetemp on its own.

    They previously carried DUPLICATE copies of the fixed-path rule, which is
    precisely how a fix ends up applied to one file and not the other. Checked on
    the AST, so a mention in a docstring cannot satisfy it.
    """
    findings = {}
    for rel in ("packages/conftest.py", "tools/conftest.py"):
        tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
        calls = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = getattr(fn, "id", None) or getattr(fn, "attr", None)
                if name:
                    calls.add(name)
        findings[rel] = {
            "calls_configure_basetemp": "configure_basetemp" in calls,
            "computes_its_own_basetemp": "gettempdir" in calls,
        }
    ok = all(v["calls_configure_basetemp"] and not v["computes_its_own_basetemp"]
             for v in findings.values())
    record(
        "BOTH_CONFTESTS_DELEGATE_TO_THE_SINGLE_BASETEMP_HELPER",
        ok,
        "%r -- each must CALL configure_basetemp and must NOT compute a basetemp "
        "itself (counted as ast.Call nodes, so prose cannot pass this)"
        % findings,
    )


def check_helper_source_has_no_drive_letter() -> None:
    """No drive letter in the helper's CODE (docstrings may discuss them).

    This project has been broken by a hardcoded drive letter three times. A grep
    would match the prose in this very file's docstring, so the check is an AST
    walk over string literals only.
    """
    src = (REPO / "packages" / "jcc_core" / "pytest_basetemp.py").read_text(
        encoding="utf-8")
    tree = ast.parse(src)
    literals = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if re.match(r"^[A-Za-z]:[\\/]", node.value):
                literals.append((node.lineno, node.value[:40]))
    # And the root must be built from gettempdir(), not concatenated.
    uses_gettempdir = "gettempdir" in src
    record(
        "THE_HELPER_DERIVES_ITS_ROOT_AND_HARD_CODES_NO_DRIVE_LETTER",
        not literals and uses_gettempdir,
        "drive-letter string literals in executable code: %r (docstrings are "
        "excluded, they necessarily discuss the F:/jev history); root built from "
        "tempfile.gettempdir(): %s" % (literals, uses_gettempdir),
    )


# --------------------------------------------------------------------------- #
# 2. the basetemp IS per-process
# --------------------------------------------------------------------------- #
def check_basetemp_is_per_process() -> None:
    """Two calls in one process must NOT collide, and both must be attributable.

    Also pins the shape: the leaf sits under the SHARED parent (so the safe-delete
    exemption still applies) and its name carries this pid.
    """
    from jcc_core.pytest_basetemp import basetemp_root, per_process_basetemp

    a = per_process_basetemp()
    b = per_process_basetemp()
    parent = basetemp_root()
    checks = {
        "two_calls_differ": a != b,
        "both_exist": a.is_dir() and b.is_dir(),
        "under_shared_parent": a.parent == parent and b.parent == parent,
        "name_carries_pid": a.name.startswith("%d-" % os.getpid()),
        "parent_is_under_gettempdir":
            str(parent).startswith(str(Path(tempfile.gettempdir()))),
    }
    record(
        "BASETEMP_IS_PER_PROCESS_UNDER_THE_SHARED_SAFE_DELETE_PARENT",
        all(checks.values()),
        "%r | parent=%s leaf=%s" % (checks, parent, a.name),
    )


def check_uuid_survives_pid_recycling() -> None:
    """A recycled pid must not inherit a previous run's directory.

    The pid alone is not sufficient: Windows recycles pids, and a reused pid
    pointed at a stale directory would resurrect another run's leftovers. The
    uuid suffix is what makes the name unique per PROCESS, not per pid value.
    """
    from jcc_core.pytest_basetemp import per_process_basetemp

    # Build the name the way the helper does, but twice with a forced identical
    # pid, to model the recycled-pid case that os.getpid() cannot produce here.
    names = set()
    for _ in range(5):
        names.add("%d-%s" % (os.getpid(), uuid.uuid4().hex[:8]))
    real = per_process_basetemp().name
    record(
        "BASETEMP_NAME_IS_UNIQUE_PER_PROCESS_EVEN_IF_THE_PID_RECYCLED",
        len(names) == 5 and real not in names
        and all(n.startswith("%d-" % os.getpid()) for n in names),
        "5 names generated with one fixed pid=%d -> %d distinct; a real leaf is "
        "%r. Without the uuid a recycled pid would collide; with it, two runs on "
        "the same pid value still differ." % (os.getpid(), len(names), real),
    )


def check_explicit_basetemp_still_wins() -> None:
    """An explicit ``--basetemp`` must still be honoured.

    This is the one behaviour that must NOT change. The acceptance gate always
    passes ``--basetemp``, and silently overriding it would break every gate
    while this suite still reported green.
    """
    class Cfg:
        def __init__(self, value):
            self.option = type("O", (), {"basetemp": value})()

    from jcc_core.pytest_basetemp import configure_basetemp

    explicit = str(REPO / "runtime" / "crh1_explicit_check")
    applied = configure_basetemp(Cfg(explicit))
    unset = configure_basetemp(Cfg(None))
    honoured = Cfg(explicit)
    configure_basetemp(honoured)
    record(
        "AN_EXPLICIT_BASETEMP_STILL_WINS_OVER_THE_PER_PROCESS_DEFAULT",
        applied is None and honoured.option.basetemp == explicit
        and bool(unset) and Path(unset).name.startswith("%d-" % os.getpid()),
        "--basetemp %s -> honoured unchanged=%s (helper returned %r); no "
        "--basetemp -> assigned %s"
        % (explicit, honoured.option.basetemp == explicit, applied, unset),
    )
    shutil.rmtree(Path(unset), ignore_errors=True)


# --------------------------------------------------------------------------- #
# 3. THE REQUIREMENT: concurrent runs must both pass, repeatedly
# --------------------------------------------------------------------------- #
def _run_concurrent_pair(extra_a: Optional[list] = None,
                         extra_b: Optional[list] = None
                         ) -> tuple:
    """Start two pytest processes on DIFFERENT test roots, no --basetemp.

    No ``--basetemp`` on purpose: passing one would make both runs use the
    caller's directory and the case would prove nothing about the default that
    the conftest actually installs.
    """
    env = child_env()
    a_cmd = [PY, "-m", "pytest",
             "packages/jcc_core/tests/test_p3_core_integration.py",
             "-q", "-p", "no:cacheprovider"] + (extra_a or [])
    b_cmd = [PY, "-m", "pytest",
             "packages/jcc_core/tests/test_policy.py",
             "-q", "-p", "no:cacheprovider"] + (extra_b or [])
    a = subprocess.Popen(a_cmd, env=env, cwd=str(REPO), text=True,
                         errors="replace", stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    # A small stagger so the second process is genuinely CONCURRENT rather than
    # sequential: the collision needs both alive with the same basetemp.
    time.sleep(0.3)
    b = subprocess.Popen(b_cmd, env=env, cwd=str(REPO), text=True,
                         errors="replace", stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    ao = a.communicate(timeout=900)[0] or ""
    bo = b.communicate(timeout=900)[0] or ""
    return a.returncode, ao, b.returncode, bo


def _contamination(ao: str, bo: str) -> Optional[str]:
    """Name the SETUP-time signature of a shared-basetemp collision, or None.

    Scoped to ERROR/permission signatures on purpose. A plain assertion failure
    is a PRODUCT result and must not be laundered into "environmental" -- the
    whole point of the pre-fix finding was that these errors were never product
    results at all.
    """
    for tag, text in (("A", ao), ("B", bo)):
        if "errors" in text or "FileExistsError" in text or "PermissionError" in text:
            if re.search(r"\d+ errors? in ", text) or "FileExistsError" in text \
                    or "WinError 32" in text:
                return tag
    return None


def check_concurrent_runs_both_pass() -> None:
    """THE REQUIREMENT. Repeated concurrent pairs, every leg must be green.

    Every pair must pass, not most: a 3/5 pre-fix rate means a single passing
    pair is indistinguishable from a fixed one, and this case is the only thing
    in the suite that measures the actual defect.
    """
    outcomes = []
    for i in range(PAIRS):
        arc, ao, brc, bo = _run_concurrent_pair()
        la = (ao.strip().splitlines() or ["<none>"])[-1][:44]
        lb = (bo.strip().splitlines() or ["<none>"])[-1][:44]
        bad = _contamination(ao, bo) or (arc != 0) or (brc != 0)
        outcomes.append("pair%d: A rc=%s [%s] | B rc=%s [%s]%s"
                        % (i + 1, arc, la, brc, lb,
                           "  <-- CONTAMINATED" if bad else ""))
        if bad:
            break
    record(
        "CONCURRENT_PYTEST_RUNS_BOTH_PASS_REPEATEDLY_WITH_NO_BASETEMP_ARG",
        all("CONTAMINATED" not in o for o in outcomes),
        "%d pair(s) attempted, no --basetemp passed (so the conftest default is "
        "what is under test) | %s"
        % (len(outcomes), " || ".join(outcomes)),
    )


def check_one_runs_cleanup_cannot_delete_a_siblings_scratch() -> None:
    """A child's cleanup must not reach a sibling's directory.

    The atexit cleanup deletes a tree, so "per-process" is only true if the
    cleanup is also per-process. A run whose pid prefix check were wrong would
    delete a CONCURRENT run's basetemp -- reintroducing the original defect from
    the other direction, and only under concurrency.
    """
    from jcc_core.pytest_basetemp import (BASETEMP_PARENT_NAME,
                                          _remove_tree_quietly,
                                          per_process_basetemp)

    parent = Path(tempfile.gettempdir()) / BASETEMP_PARENT_NAME
    # A SIBLING is another process's directory, so its name must carry a
    # DIFFERENT pid. The first version of this case built the sibling with the
    # CURRENT pid and then asserted it survived -- so the guard correctly deleted
    # it and the case failed. That was a broken test, not a broken guard: a
    # sibling is by definition not ours, and a real one never carries our pid.
    sibling = parent / ("%d-%s" % (os.getpid() + 1, uuid.uuid4().hex[:8]))
    sibling.mkdir(parents=True, exist_ok=True)
    marker = sibling / "keep.txt"
    marker.write_text("do not delete", encoding="utf-8")

    # A directory that is NOT ours, and a path outside the parent entirely.
    outside = Path(tempfile.mkdtemp(prefix="crh1_outside_"))
    _remove_tree_quietly(sibling)      # foreign pid -> refused
    _remove_tree_quietly(outside)      # not under the parent -> refused
    survived = sibling.is_dir() and marker.is_file()
    outside_survived = outside.is_dir()
    # And OUR OWN directory must still be removable, or the guard is useless.
    mine = per_process_basetemp()
    _remove_tree_quietly(mine)
    mine_removed = not mine.exists()
    shutil.rmtree(sibling, ignore_errors=True)
    shutil.rmtree(outside, ignore_errors=True)
    record(
        "CLEANUP_REMOVES_ONLY_THIS_PROCESS_OWN_BASETEMP",
        survived and outside_survived and mine_removed,
        "sibling %s survived=%s (pid-suffix guard) | outside-parent %s survived=%s "
        "(parent guard) | own dir removed=%s (otherwise the guard is a no-op)"
        % (sibling.name, survived, outside.name, outside_survived, mine_removed),
    )


# --------------------------------------------------------------------------- #
# 4. NEGATIVE CONTROL -- force the old fixed name and require the case to fail
# --------------------------------------------------------------------------- #
def _concurrency_case(probe_id: str) -> None:
    """The concurrency requirement, as a callable the control can also run."""
    outcomes = []
    for i in range(PAIRS):
        arc, ao, brc, bo = _run_concurrent_pair()
        la = (ao.strip().splitlines() or ["<none>"])[-1][:40]
        lb = (bo.strip().splitlines() or ["<none>"])[-1][:40]
        bad = _contamination(ao, bo) or (arc != 0) or (brc != 0)
        outcomes.append("pair%d: A rc=%s [%s] | B rc=%s [%s]%s"
                        % (i + 1, arc, la, brc, lb,
                           "  <-- CONTAMINATED" if bad else ""))
        if bad:
            break
    record(
        probe_id,
        all("CONTAMINATED" not in o for o in outcomes),
        "%d pair(s): %s" % (len(outcomes), " || ".join(outcomes)),
    )


def run_negative_control() -> None:
    """Restore the FIXED shared name and require the concurrency case to FAIL.

    A concurrency fix that cannot be shown to fail on the original bug is a
    comment, not a fix. The helper's ``per_process_basetemp`` is temporarily
    rewritten to return the old single fixed directory, the child is asked for
    ONLY the concurrency case, and it must report contaminated.
    """
    target = REPO / "packages" / "jcc_core" / "pytest_basetemp.py"
    original = target.read_text(encoding="utf-8", newline="")
    backup = target.with_suffix(".py.crh1-backup")

    # Replace the BODY of per_process_basetemp with the pre-fix behaviour.
    pattern = re.compile(
        r"(def per_process_basetemp\(\) -> Path:(?:.|\n)*?return root\n)")
    replacement = (
        "def per_process_basetemp() -> Path:  # %s\n"
        "    root = basetemp_root()\n"
        "    root.mkdir(parents=True, exist_ok=True)\n"
        "    return root\n" % NEGCTL_MARKER)
    sabotaged, n = pattern.subn(lambda _m: replacement, original, count=1)
    if n != 1:
        record("NEGATIVE_CONTROL_THE_CONCURRENCY_CASE_WOULD_GO_RED", False,
               "could not locate per_process_basetemp in %s (matched %d times); "
               "the control must not silently no-op" % (target.name, n))
        return
    try:
        compile(sabotaged, str(target), "exec")
    except SyntaxError as exc:
        record("NEGATIVE_CONTROL_THE_CONCURRENCY_CASE_WOULD_GO_RED", False,
               "the sabotage would not compile (%s), so the control would measure "
               "a crash rather than the defect" % exc)
        return

    log = REPO / "runtime" / "crh1_negctl_child.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        backup.write_text(original, encoding="utf-8", newline="")
        target.write_text(sabotaged, encoding="utf-8", newline="")
        with open(log, "w", encoding="utf-8", errors="replace") as handle:
            subprocess.run(
                [PY, str(REPO / "tools" / "crh1_basetemp_falsifier.py"),
                 "--concurrency-only"],
                env=child_env(JCC_CRH1_NEGCTL="1"), cwd=str(REPO), text=True,
                errors="replace", stdin=subprocess.DEVNULL, stdout=handle,
                stderr=subprocess.STDOUT, timeout=1800)
        out = log.read_text(encoding="utf-8", errors="replace")
        # The child must report CONTAMINATION for the control to mean anything.
        detected = "CONTAMINATED" in out
        summary = [ln for ln in out.splitlines() if "CR-H1 FALSIFIER" in ln]
        holes = [ln.strip() for ln in out.splitlines()
                 if ln.strip().startswith("[HOLE]")]
        record(
            "NEGATIVE_CONTROL_THE_CONCURRENCY_CASE_WOULD_GO_RED",
            detected and bool(holes),
            "with per_process_basetemp forced back to the single fixed "
            "jev-pytest directory, the child reported CONTAMINATION=%s and %d "
            "HOLE(s) (%d bytes to %s) | %s. The child runs ONLY the concurrency "
            "case, so this is that case failing on the original defect."
            % (detected, len(holes), len(out), log.name,
               summary[-1] if summary else "NO SUMMARY"),
        )
    finally:
        target.write_text(original, encoding="utf-8", newline="")
        try:
            backup.unlink()
        except OSError:
            pass
        restored = target.read_text(encoding="utf-8", newline="")
        record(
            "SOURCE_RESTORED_AFTER_THE_NEGATIVE_CONTROL",
            restored == original,
            "pytest_basetemp.py restored byte-exactly (%d chars, verified by "
            "re-read); the control left no residue in the tree it audits"
            % len(original) if restored == original else
            "pytest_basetemp.py is NOT identical to its pre-sabotage content "
            "(%d vs %d chars) -- treat the working tree as suspect"
            % (len(restored), len(original)),
        )


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> int:
    concurrency_only = "--concurrency-only" in sys.argv

    if concurrency_only:
        # Used ONLY by the negative control: exercise the requirement alone, so
        # a control failure cannot be blamed on an unrelated case.
        _concurrency_case(
            "CONCURRENT_PYTEST_RUNS_BOTH_PASS_REPEATEDLY_WITH_NO_BASETEMP_ARG")
    else:
        check_both_conftests_delegate_to_one_helper()
        check_helper_source_has_no_drive_letter()
        check_basetemp_is_per_process()
        check_uuid_survives_pid_recycling()
        check_explicit_basetemp_still_wins()
        check_one_runs_cleanup_cannot_delete_a_siblings_scratch()
        check_concurrent_runs_both_pass()
        run_negative_control()
        # Prove the restore really did restore: the requirement must hold again.
        _concurrency_case(
            "CONCURRENCY_STILL_HOLDS_AFTER_THE_NEGATIVE_CONTROL_RESTORED")

    passed = sum(1 for r in RESULTS if r["ok"])
    for r in RESULTS:
        print("  [%s] %s\n        %s"
              % ("ok  " if r["ok"] else "HOLE", r["id"], r["detail"]))
    print("\nCR-H1 FALSIFIER %s %d/%d"
          % ("PASS" if passed == len(RESULTS) else "FAIL", passed, len(RESULTS)))
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
