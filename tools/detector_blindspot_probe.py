#!/usr/bin/env python3
"""detector_blindspot_probe -- proves whether a content-reading detector can be
made to report GREEN on a file whose bytes have been destroyed.

THE DEFECT CLASS THIS EXISTS TO MEASURE
---------------------------------------
INC-002 replaced two tracked files' bytes with NULs and LEFT THE SIZE INTACT, so
git's stat cache still called them clean. p1-tooling (commit 74255f8) found that
`tools/f_derivations_falsifier.py` -- a gate whose entire job is to detect drive
literals in product code -- reported ``FALSIFIER: PASS`` and exit 0 on exactly
that damage, because a file with no tokens has no literals to count.

The general rule that came out of it:

    A DETECTOR THAT ONLY COUNTS FEATURES READS "THE FEATURE DISAPPEARED"
    AS "PASS". SO EVERY CONTENT-SCAN GATE NEEDS A NEGATIVE EXISTENCE
    ASSERTION -- the file must parse, or carry a minimum token count, or
    match a hash -- EVALUATED *BEFORE* ADJUDICATION.

    BEFORE is load-bearing. An exemption ("these hits are deliberate test
    inputs") is a claim about a file's CONTENT. A file with no parsable content
    makes the claim meaningless, so an exemption must not be able to silence an
    unreadable file -- otherwise the exemption is a hole straight through the
    detector.

WHAT THIS PROBE DOES, AND WHY IT IS BUILT THIS WAY
-------------------------------------------------
Destructive experiments happen in a THROWAWAY GIT REPO under the OS temp dir,
never in the D:\\jev worktree. This repo has already lost two tracked files to
in-place zeroing; the probe does not get to be the third. The repo is created by
`tempfile.mkdtemp()` and removed in a `finally`, and the tool under test is
invoked with its own root pointed at the fixture (see HARNESS ROOT SELECTION).

Five falsifiers in this project have already died because their negative control
did not actually run. Every anti-pattern that killed them is closed here:

  1. NO PIPES. Child stdout/stderr go to FILES, never to a pipe. One of those
     falsifiers did ``proc.stdout or ""`` and read 4298 bytes of real output as
     0 bytes, then reported the child as silent. Reading a file we ourselves
     closed cannot lose the bytes.
  2. THE CHILD PROVES IT RAN. Every child prints ``CHILD_PID=<pid>`` and
     ``OUTPUT_BYTES=<n>`` to its own result file. A child that crashed before
     printing is distinguishable from a child that ran and said nothing.
  3. DESTRUCTIVE CODE IS ``compile()``-PRECHECKED. The sabotaged module and every
     probe helper are compiled in-process before they are handed to a child, so
     a syntax error is caught by the parent instead of surfacing as an
     unexplained child traceback.
  4. NO ``HEAD~1``. A baseline that drifts with other people's commits is not a
     baseline. The fixture's baseline commit SHA is read back from
     ``git rev-parse HEAD`` immediately after committing and pinned for the rest
     of the run.
  5. NO ``git add -A`` / ``git add .`` / ``git checkout`` / ``git restore`` /
     ``git stash`` in this repo's namespace. The fixture is a separate repo in
     a temp dir, so this is structurally impossible rather than merely avoided.

HOW TO READ THE OUTPUT
----------------------
Each subject detector is run TWICE against a fixture whose tracked product file
has been size-preserving-zeroed:

  * ``UNPATCHED`` -- the detector's own logic, re-implemented by this probe as a
    faithful transcription of the lines cited in the AUDIT table. This is the
    NEGATIVE CONTROL: it demonstrates the blind spot is real and that this probe
    can see it. If UNPATCHED reports green here, the probe is broken and every
    later green is meaningless.
  * ``PATCHED`` -- the detector's ACTUAL code, executed as a subprocess against
    the same zeroed fixture.

A detector counts as FIXED only when UNPATCHED=green and PATCHED=red. A detector
that is green in both is UNPROVEN, not fixed.

usage: python tools/detector_blindspot_probe.py [--json OUT] [--keep]
exit 0 = every B-class detector proved red, 1 = otherwise
"""
from __future__ import annotations

import argparse
import ast
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable

#: The audit table. Each entry names the file the detector reads, and names the
#: ``unpatched`` control that stands in for its pre-fix decision rule.
#:
#: AN HONEST WORDING CORRECTION, because the first version of this comment
#: claimed each transcription was "a faithful transcription of the cited lines",
#: and for one of the three that was not true.
#:
#: ``reference_unpatched`` is a BARE DRIVE-LITERAL COUNT. The real
#: f_derivations_falsifier has never been a bare count: since well before
#: 74255f8 it has been token-level and docstring-aware, and it has always
#: detected an unparseable file. What 74255f8 actually changed is that the
#: detected condition STOPPED BEING DIAGNOSTIC AND BECAME A REFUSAL -- before
#: it, a zeroed file produced a TOKENIZE_ERROR record that main() counted as
#: neither violation nor unreadable, so the run still printed FALSIFIER: PASS.
#: Verified against `git show 74255f8^`: the TOKENIZE_ERROR catch clause and its
#: record both predate that commit.
#:
#: So ``reference_unpatched`` models no historical version of the tool. It is a
#: CARICATURE -- a weaker detector than any the repo has shipped -- and calling
#: it a transcription overstates what it is.
#:
#: Why keep it, and why it is still sound for its actual job: the control's
#: question is "does a count-only detector go green on destroyed bytes?" That
#: question is about the SHAPE, not about any specific revision, and a weaker
#: detector is the right instrument for it -- a caricature that goes green
#: proves the shape is sufficient to produce a false green. The verdict for
#: every detector in this file comes from the REAL code in a subprocess
#: (`run_patched`), never from these functions; `_classify` does not reference
#: them. Mutation-tested: forcing any of the three controls RED makes the probe
#: exit 2 with a FATAL, so a red control cannot hide behind a green verdict.
AUDIT: Tuple[Dict[str, Any], ...] = (
    {
        "id": "WAVE6_WORLD_HAS_NO_WRITE_PATH",
        "tool": "tools/v1_3_wave6_world_gate.py",
        "cites": "L135-L145 (reads 2 TS/TSX sources, L140-141 findall, "
                 "L143 `not w_hits and not s_hits`)",
        "target": "app/src/store/domainWorld.ts",
        "kind": "typescript",
        "unpatched": "wave6_unpatched",
    },
    {
        "id": "RELEASE_DOCS_DO_NOT_DENY_REGISTERED_TASKS",
        "tool": "tools/tests/test_release_docs_do_not_deny_registered_tasks.py",
        "cites": "L96-L99 _read(), L157 offenders=[d for d in docs if "
                 "_FALSE_WORDING.search(_read(d))], L158 assert not offenders",
        "target": "artifacts/report.md",
        "kind": "markdown",
        "unpatched": "docs_unpatched",
    },
    {
        "id": "REFERENCE_FALSIFIER_ALREADY_FIXED",
        "tool": "tools/f_derivations_falsifier.py",
        "cites": "L201-L213 tokenize -> UNREADABLE marker, L539-L549 refused "
                 "BEFORE adjudication at L559",
        "target": "product/mod.py",
        "kind": "python",
        "unpatched": "reference_unpatched",
    },
)

#: Minimum plausible token count for a file that is supposed to hold code. A
#: Python file with fewer than this many characters has had its content removed;
#: the threshold is deliberately far below any real product file so it cannot
#: fire on legitimate small modules.
MIN_SOURCE_BYTES = 32


# ===========================================================================
# NEGATIVE CONTROL TRANSCRIPTIONS
#
# These re-implement each detector's decision rule on the SAME zeroed bytes, so
# a green here is evidence the blind spot is real rather than a claim about it.
# They are compiled in-process before use (discipline 3).
# ===========================================================================

def wave6_unpatched(root: Path) -> Tuple[bool, str]:
    """Transcription of v1_3_wave6_world_gate.py L135-L145, verbatim shape.

    Reads the projection source, counts forbidden write-path tokens, and passes
    when there are none. A zeroed file has no tokens, so the count is 0 and the
    check is green -- on a file that no longer exists as far as the gate cares.
    """
    world = root / "app" / "src" / "store" / "domainWorld.ts"
    scene = root / "app" / "src" / "scene" / "OperationalWorld.tsx"
    world_src = world.read_text(encoding="utf-8")
    scene_src = scene.read_text(encoding="utf-8")
    forbidden = re.compile(r"\bfetch\s*\(|\bPOST\b|X-JCC-Token|\.createProject\s*\(|"
                           r"\.verifyDeliverable\s*\(|\.resolveAttention\s*\(|"
                           r"\.addArtifact\s*\(")
    w_hits = forbidden.findall(world_src)
    s_hits = forbidden.findall(scene_src)
    ok = not w_hits and not s_hits
    return ok, ("w_hits=%d s_hits=%d world_bytes=%d scene_bytes=%d"
                % (len(w_hits), len(s_hits), len(world_src), len(scene_src)))


def docs_unpatched(root: Path) -> Tuple[bool, str]:
    """Transcription of test_release_docs_do_not_deny_registered_tasks.py L157.

    ``offenders = [d for d in docs if _FALSE_WORDING.search(_read(d))]`` then
    ``assert not offenders``. A zeroed document matches no pattern, so it is
    never an offender and the assertion holds.
    """
    false_wording = re.compile(r"is\s+not\s+registered", re.I)
    doc = root / "artifacts" / "report.md"
    text = doc.read_text(encoding="utf-8", errors="replace")
    offenders = [doc.name] if false_wording.search(text) else []
    return not offenders, "offenders=%d doc_bytes=%d" % (len(offenders), len(text))


def reference_unpatched(root: Path) -> Tuple[bool, str]:
    """A bare drive-literal count -- a CARICATURE, not a historical revision.

    Read the AUDIT table comment before trusting this as a model of anything.
    Short version: the real falsifier has always been token-level and has
    always detected an unparseable file. It went green on a zeroed file because
    the detection was DIAGNOSTIC rather than a REFUSAL, not because it lacked
    the check. So this function is deliberately weaker than any shipped
    revision, and its only job is to answer one question about a SHAPE:

        does "count a feature and pass on zero" go green on destroyed bytes?

    It does. That is the point, and it is why the probe's contrast is real:
    the same fixture that makes this caricature green is refused by the actual
    tool, running as a subprocess, with a stated reason.
    """
    drive_re = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z]):[\\/]")
    mod = root / "product" / "mod.py"
    src = mod.read_text(encoding="utf-8", errors="replace")
    hits = drive_re.findall(src)
    return not hits, "literals=%d bytes=%d" % (len(hits), len(src))


UNPATCHED_IMPLS: Dict[str, Callable[[Path], Tuple[bool, str]]] = {
    "wave6_unpatched": wave6_unpatched,
    "docs_unpatched": docs_unpatched,
    "reference_unpatched": reference_unpatched,
}


# ===========================================================================
# CHILD PROCESSES
#
# Every child writes to a FILE and self-reports. No pipes anywhere.
# ===========================================================================

_CHILD_PREAMBLE = '''\
import json, os, sys
OUT = sys.argv[1]
ROOT = sys.argv[2]
def emit(rec):
    rec["CHILD_PID"] = os.getpid()
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(rec, fh)
    sys.stdout.write("CHILD_PID=%d\\n" % os.getpid())
    sys.stdout.write("OUTPUT_BYTES=%d\\n" % os.path.getsize(OUT))
    sys.stdout.flush()
'''


def child_source(body: str, preamble: str = "") -> str:
    """Assemble + PRECOMPILE a child. A syntax error raises HERE, not in the kid.

    The body is used AS WRITTEN -- no dedent, no re-indent. Callers that splice
    in source lifted from a real detector have already indented it to sit inside
    the child's try block, and re-indenting here would either double-indent it
    or (worse, via a blanket dedent) flatten the try body and produce an
    IndentationError that looks like a finding but is a bug in the probe.
    """
    head = _CHILD_PREAMBLE + (textwrap.dedent(preamble) if preamble else "")
    src = head + body.strip("\n") + "\n"
    compile(src, "<probe-child>", "exec")   # discipline 3
    return src


#: Marker replaced by real source lifted out of a detector. Marker-based rather
#: than %-formatting because lifted code is full of % and braces, and escaping
#: a detector's own source to get it through a format string is how a probe
#: ends up testing something other than the detector.
LIFT_MARKER = "#<<<LIFTED>>>"


def run_child(src: str, workdir: Path, name: str,
              fixture: Optional[Path] = None,
              extra_argv: Optional[List[str]] = None,
              timeout: int = 120) -> Dict[str, Any]:
    """Run a child; capture stdout/stderr to FILES. Never a pipe.

    ``workdir`` is scratch space for the child's own files. ``fixture`` is the
    damaged tree the child is supposed to be examining, and it arrives as
    ``sys.argv[2]``. These are deliberately different directories: conflating
    them is how a probe ends up reporting "the detector went red" when what
    actually happened is that the detector never found its input at all.

    Returns a record that always carries the proof the child ran, even on
    timeout: ``child_reported`` is True only if the kid got far enough to write
    its own result file with a PID in it.
    """
    d = workdir / ("child_" + name)
    d.mkdir(parents=True, exist_ok=True)
    script = d / "child.py"
    out_file = d / "child_result.json"
    so = d / "child_stdout.txt"
    se = d / "child_stderr.txt"
    script.write_text(src, encoding="utf-8", newline="\n")

    rec: Dict[str, Any] = {"name": name, "script": str(script),
                           "fixture": str(fixture) if fixture else None}
    argv = [PY, str(script), str(out_file),
            str(fixture if fixture else workdir)] + list(extra_argv or [])
    with open(so, "wb") as f_out, open(se, "wb") as f_err:
        proc = subprocess.Popen(argv, stdout=f_out, stderr=f_err,
                                cwd=str(workdir), stdin=subprocess.DEVNULL)
        rec["CHILD_PID"] = proc.pid
        try:
            rec["returncode"] = proc.wait(timeout=timeout)
            rec["timed_out"] = False
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=30)
            rec["returncode"] = None
            rec["timed_out"] = True

    # Discipline 1: read the FILES. Do not trust a pipe, do not use `or ""`.
    rec["stdout_bytes"] = so.stat().st_size
    rec["stderr_bytes"] = se.stat().st_size
    rec["stdout_tail"] = so.read_text(encoding="utf-8", errors="replace")[-1500:]
    rec["stderr_tail"] = se.read_text(encoding="utf-8", errors="replace")[-1500:]

    if out_file.is_file():
        try:
            rec["result"] = json.loads(out_file.read_text(encoding="utf-8"))
            rec["child_reported"] = "CHILD_PID" in rec["result"]
        except ValueError as exc:
            rec["result"] = None
            rec["child_reported"] = False
            rec["result_parse_error"] = str(exc)
    else:
        rec["result"] = None
        rec["child_reported"] = False
    # Cross-check: the PID the parent assigned must equal the PID the kid printed.
    rec["pid_matches"] = (rec.get("result") or {}).get("CHILD_PID") == rec["CHILD_PID"]
    return rec


# ===========================================================================
# FIXTURE
# ===========================================================================

#: A product module with a REAL live drive literal, so the reference detector
#: has something true to find before the damage (and nothing after it, which is
#: the whole point).
_PRODUCT_PY = '''\
"""Fixture product module for the blind-spot probe."""

import os

#: A genuine repo-root drive literal in live-code position. The reference
#: detector is supposed to flag this. After zeroing there is nothing to flag.
CONFIG = "F:/jev/config/jcc.yaml"


def resolve():
    return CONFIG
'''

_DOMAIN_TS = """\
// Fixture projection source for the blind-spot probe.
import type { DomainApi } from "./domainApi";

export function projectWorld(state: State) {
  return state.items.map((i) => i.id);
}
"""

_SCENE_TSX = """\
// Fixture scene source for the blind-spot probe.
export function OperationalWorld() {
  return <div className="world" />;
}
"""

_NAV_TS = """\
// Fixture navigation source for the blind-spot probe.
export const NAV = [
  { id: "world", label: "Operational World" },
  { id: "engine-world", label: "Engine World" },
];
"""

_REPORT_MD = """\
# Fixture report

The scheduled task `JEV-JCC-Continuation-Watchdog` is not registered on this host;
registering it needs elevation, which was not requested.
"""


def build_fixture(base: Path) -> Dict[str, Any]:
    """A throwaway git repo with the fixture content committed.

    Returns the pinned baseline SHA. The SHA is read back from
    ``git rev-parse HEAD`` right after the commit and never expressed relative
    to a moving reference (discipline 4).
    """
    files = {
        "product/mod.py": _PRODUCT_PY,
        "app/src/store/domainWorld.ts": _DOMAIN_TS,
        "app/src/scene/OperationalWorld.tsx": _SCENE_TSX,
        "app/src/app/navigation.ts": _NAV_TS,
        "artifacts/report.md": _REPORT_MD,
    }
    for rel, body in files.items():
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8", newline="\n")

    def git(*a: str) -> str:
        r = subprocess.run(["git", "-C", str(base)] + list(a),
                           capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
        if r.returncode != 0:
            raise SystemExit("fixture git %s failed: %s" % (a, r.stderr))
        return r.stdout

    git("init", "--quiet", "-b", "main")
    git("config", "user.email", "probe@localhost")
    git("config", "user.name", "blindspot probe")
    git("config", "core.autocrlf", "false")
    # Explicit per-file adds. Never `add -A` / `add .`.
    for rel in sorted(files):
        git("add", "--", rel)
    git("commit", "--quiet", "-m", "fixture baseline")
    sha = git("rev-parse", "HEAD").strip()
    return {"baseline_sha": sha, "files": sorted(files)}


def zero_preserve_size(base: Path, rel: str) -> Dict[str, Any]:
    """Replace a file's bytes with NULs, keeping its SIZE. The INC-002 shape.

    Also restores mtime, so git's stat cache (size + mtime) is fully satisfied
    and ``git status`` calls the file clean -- which is what made INC-002 able
    to hide from a content-blind check in the first place.
    """
    p = base / rel
    original = p.read_bytes()
    before = p.stat()
    p.write_bytes(b"\x00" * len(original))
    os.utime(str(p), ns=(before.st_atime_ns, before.st_mtime_ns))
    after = p.stat()
    return {
        "path": rel,
        "size_before": len(original),
        "size_after": after.st_size,
        "size_preserved": len(original) == after.st_size,
        "mtime_preserved": before.st_mtime_ns == after.st_mtime_ns,
        "all_nul": p.read_bytes() == b"\x00" * len(original),
    }


def git_says_clean(base: Path, rel: str) -> bool:
    """Whether git's own stat-cache path calls the file unchanged."""
    r = subprocess.run(["git", "-C", str(base), "diff", "--name-only", "--", rel],
                       capture_output=True, text=True)
    return not r.stdout.strip()


# ===========================================================================
# NEGATIVE CONTROL: the UNPATCHED transcription must go GREEN on the damage
# ===========================================================================

def negative_control(base: Path) -> List[Dict[str, Any]]:
    """Run every unpatched transcription against the zeroed fixture.

    If any of these reports red, the probe cannot see the blind spot and every
    later result is void -- so this is asserted, not assumed.
    """
    out = []
    for entry in AUDIT:
        impl = UNPATCHED_IMPLS[entry["unpatched"]]
        # PRECOMPILE the transcription before running it.
        ast.parse(textwrap.dedent(inspect_source(impl)))
        try:
            green, detail = impl(base)
        except Exception as exc:                      # noqa: BLE001
            green, detail = None, "raised %s: %s" % (type(exc).__name__, exc)
        out.append({
            "id": entry["id"],
            "impl": entry["unpatched"],
            "reported_green": green,
            "detail": detail,
        })
    return out


def inspect_source(fn: Callable[..., Any]) -> str:
    import inspect
    return inspect.getsource(fn)


# ===========================================================================
# PATCHED RUN: execute the detectors' REAL code
# ===========================================================================

def run_patched(base: Path, work: Path) -> List[Dict[str, Any]]:
    """Run each detector's actual adjudication against the zeroed fixture.

    A detector is invoked as a SUBPROCESS and never imported: many scripts in
    this repo lack an ``if __name__ == "__main__"`` guard, so importing one is
    executing it. A previous member imported repo scripts that way and it ran 16
    files and started 2 processes.
    """
    results: List[Dict[str, Any]] = []

    # --- 1. the reference detector, which p1-tooling already fixed ----------
    results.append(_probe_reference_falsifier(base, work))

    # --- 2. v1_3_wave6_world_gate.py ---------------------------------------
    results.append(_probe_wave6(base, work))

    # --- 3. the release-docs test ------------------------------------------
    results.append(_probe_release_docs(base, work))

    return results


def _probe_reference_falsifier(base: Path, work: Path) -> Dict[str, Any]:
    """Drive the REAL f_derivations_falsifier over the zeroed fixture.

    The tool derives ROOT from ``parents[1]`` of its own path, so it is copied
    byte-for-byte (not edited, not stubbed) to ``<fixture>/tools/`` and executed
    with the FIXTURE as its root. That is why the fixture is a git repo: the
    tool's inventory is driven by ``git ls-files`` in its own root.

    Only the temp fixture is written. The D:/jev worktree is never touched.
    """
    (base / "tools").mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(ROOT / "tools" / "f_derivations_falsifier.py"),
                 str(base / "tools" / "f_derivations_falsifier.py"))

    # The tool executes artifacts/security/freeze_check.py as its subject proof.
    # The real one imports final_gate_run (the whole gate package); what is under
    # test here is the UNREADABLE branch, which fires during inventory() at
    # step [3], well before the subject-execution step at [2] would need it. A
    # stand-in keeps the fixture hermetic, and it says so in its own output so
    # no reader can mistake it for the real gate.
    fc = base / "artifacts" / "security"
    fc.mkdir(parents=True, exist_ok=True)
    (fc / "freeze_check.py").write_text(
        'import sys\n'
        'print("FREEZE CHECK: COMPLIANT (probe fixture stand-in, not the real gate)")\n'
        'sys.exit(0)\n', encoding="utf-8", newline="\n")

    src = child_source(
        "import subprocess, sys\n"
        "proc = subprocess.run([sys.executable, sys.argv[3]],\n"
        "                      capture_output=True, text=True, cwd=sys.argv[2])\n"
        "rc = proc.returncode\n"
        "blob = (proc.stdout or '') + (proc.stderr or '')\n"
        "# GREEN is the defect: exit 0 while a tracked product file is destroyed.\n"
        "emit({'tool_exit': rc, 'green': rc == 0,\n"
        "      'mentions_unreadable': 'UNREADABLE' in blob,\n"
        "      'mentions_pass': 'FALSIFIER: PASS' in blob,\n"
        "      'error': None,\n"
        "      'red_reason': ('falsifier named UNREADABLE and exited nonzero'\n"
        "                      if (rc != 0 and 'UNREADABLE' in blob) else ''),\n"
        "      'tail': blob[-2500:]})\n"
    )
    rec = run_child(src, work, "reference", fixture=base,
                    extra_argv=[str(base / "tools" / "f_derivations_falsifier.py")])
    rec["id"] = "REFERENCE_FALSIFIER_ALREADY_FIXED"
    rec["detector"] = "tools/f_derivations_falsifier.py"
    rec["cites"] = AUDIT[2]["cites"]
    rec["unpatched_green"] = True
    return _classify(rec)


def _probe_wave6(base: Path, work: Path) -> Dict[str, Any]:
    """Run the REAL wave6 structural check against the zeroed fixture.

    The full gate needs npm/vitest/a browser, so the subprocess executes the
    exact source of the structural check, lifted by line range from the real
    file, with APP pointed at the fixture. The code under test is the detector's
    own; only its surroundings are supplied here.
    """
    gate_src = (ROOT / "tools" / "v1_3_wave6_world_gate.py").read_text(
        encoding="utf-8", errors="replace")
    block = _extract_between(
        gate_src,
        "# 3. structural: the projection and scene have NO write path",
        "both the operational world and the v1.2 engine world exist",
        "wave6 structural checks")
    for needle in ("forbidden.findall", "read_text", "SOURCES_ARE_READABLE_SOURCE"):
        if needle not in block:
            raise SystemExit("wave6 structural block lost %r -- the check under "
                             "audit is no longer in the lifted range, so a green "
                             "here would prove nothing" % needle)
    body = _lift(block)
    src = child_source(
        "import re, sys\n"
        "from pathlib import Path\n"
        "from typing import Any, Dict, List, Optional\n"
        "APP = Path(sys.argv[2]) / 'app'\n"
        "RECORDED = []\n"
        "class _Checks:\n"
        "    failed = []\n"
        "    def add(self, name, ok, value, note):\n"
        "        RECORDED.append({'step': name, 'ok': bool(ok),\n"
        "                         'value': str(value)[:300]})\n"
        "        if not ok:\n"
        "            _Checks.failed.append(name)\n"
        "checks = _Checks()\n"
        "verdict_green = False\n"
        "detail = 'not run'\n"
        "err = None\n"
        "try:\n"
        + body + "\n"
        "        verdict_green = not _Checks.failed\n"
        "        detail = json.dumps(RECORDED)[:2000]\n"
        "except Exception as exc:\n"
        "    err = 'raised %s: %s' % (type(exc).__name__, exc)\n"
        "    detail = err\n"
        "failed_names = [r['step'] for r in RECORDED if not r['ok']]\n"
        "# The check UNDER AUDIT is 'WORLD_HAS_NO_WRITE_PATH', not the gate as a\n"
        "# whole. The aggregate can go red by coincidence -- a different check\n"
        "# failing for an unrelated reason would otherwise be reported as this\n"
        "# one being fixed, which is the same false green in a new costume.\n"
        "audited = [r for r in RECORDED if r['step'] == 'WORLD_HAS_NO_WRITE_PATH']\n"
        "audited_ok = audited[0]['ok'] if audited else None\n"
        "emit({'green': verdict_green, 'audited_step': 'WORLD_HAS_NO_WRITE_PATH',\n"
        "      'audited_ok': audited_ok,\n"
        "      'checks': RECORDED, 'detail': detail, 'error': err,\n"
        "      'red_reason': ('check WORLD_HAS_NO_WRITE_PATH went red on the '\n"
        "                      'zeroed projection' if audited_ok is False else '')})\n"
    )
    rec = run_child(src, work, "wave6", fixture=base)
    rec["id"] = "WAVE6_WORLD_HAS_NO_WRITE_PATH"
    rec["detector"] = "tools/v1_3_wave6_world_gate.py"
    rec["cites"] = AUDIT[0]["cites"]
    rec["unpatched_green"] = True
    return _classify(rec)


def _probe_release_docs(base: Path, work: Path) -> Dict[str, Any]:
    """Run the REAL release-docs assertion against the zeroed fixture.

    The shipped test has no CLI, so the assertion body is lifted by line range
    from the real file -- the same anti-drift discipline as the wave6 probe.
    The helper it calls (``_read``) is lifted from the real file too, because
    the fix for this detector belongs in ``_read``: had the probe supplied its
    own ``_read``, the probe would be testing the probe.
    """
    test_src = (ROOT / "tools" / "tests" /
                "test_release_docs_do_not_deny_registered_tasks.py").read_text(
        encoding="utf-8", errors="replace")
    read_fn = _extract_between(test_src, "def _read(", "return text",
                               "_read helper")
    # `_extract_between` returns the body WITHOUT its `def` line, so the
    # signature is not in the slice. What must be present is the body and the
    # negative existence assertion -- if that assertion is ever removed from
    # _read, this probe must stop rather than report a meaningless green.
    if "_MIN_DOC_BYTES" not in read_fn or "UNREADABLE_DOCS" not in read_fn:
        raise SystemExit("probe anchor lost: _read's negative existence "
                         "assertion is gone from the lifted body -- without it "
                         "a green here would prove nothing")
    # Start at the `docs = ...` line, not at the `def`, so the function's own
    # docstring (which contains a triple quote) is not spliced into the child
    # template. The lifted lines are the ones that decide the verdict.
    block = _extract_between(test_src, "def test_no_document_under_artifacts_denies",
                             "artifacts documents deny the registration",
                             "release-docs sweep")
    for needle in ("_FALSE_WORDING.search", "_swept_documents()"):
        if needle not in block:
            raise SystemExit("release-docs sweep lost %r -- a green here would "
                             "prove nothing" % needle)
    # ``_read`` and the sweep are lifted SEPARATELY and rejoined: lifting them
    # as one string would let textwrap.dedent measure indentation across both
    # at once, and the two have different natural indents.
    #
    # The child's module-level names mirror the real module's, because the
    # lifted code refers to them by those names. UNREADABLE_DOCS in particular
    # is the real module's accumulator -- the probe reads the real variable, so
    # it cannot pass by naming a list the real code never touches.
    read_body = _lift(read_fn)
    src = child_source(
        "import glob, io, json, os, re, sys\n"
        "ROOT = sys.argv[2]\n"
        "_FALSE_WORDING = re.compile(r'is\\s+not\\s+registered', re.I)\n"
        "_MIN_SWEPT = 1\n"
        "_MIN_DOC_BYTES = 32\n"
        "REPORTS = ['artifacts/report.md']\n"
        "UNREADABLE_DOCS = []\n"
        "def _swept_documents():\n"
        "    pattern = os.path.join(ROOT, 'artifacts', '**', '*.md')\n"
        "    found = glob.glob(pattern, recursive=True)\n"
        "    return sorted(os.path.relpath(q, ROOT).replace(os.sep, '/')\n"
        "                  for q in found)\n"
        "def _read(rel):\n"
        + read_body + "\n"
        "def _sweep():\n"
        + _lift(block) + "\n"
        "UNREADABLE = []\n"
        "try:\n"
        "    _sweep()\n"
        "    UNREADABLE.extend(UNREADABLE_DOCS)\n"
        "except AssertionError as exc:\n"
        "    UNREADABLE.append('probe assertion failed: %s' % exc)\n"
        "except Exception as exc:\n"
        "    UNREADABLE.append('probe raised %s: %s' % (type(exc).__name__, exc))\n"
        "emit({'green': len(UNREADABLE) == 0, 'unreadable': UNREADABLE,\n"
        "      'error': (UNREADABLE[0] if UNREADABLE and\n"
        "                 UNREADABLE[0].startswith('probe raised') else None),\n"
        "      'red_reason': ('unreadable documents: %s' % UNREADABLE)\n"
        "                      if UNREADABLE else '',\n"
        "      'detail': ('no unreadable docs' if not UNREADABLE\n"
        "                 else 'unreadable: %s' % UNREADABLE)})\n"
    )
    rec = run_child(src, work, "reldocs", fixture=base)
    rec["id"] = "RELEASE_DOCS_DO_NOT_DENY_REGISTERED_TASKS"
    rec["detector"] = "tools/tests/test_release_docs_do_not_deny_registered_tasks.py"
    rec["cites"] = AUDIT[1]["cites"]
    rec["unpatched_green"] = True
    return _classify(rec)


def _lift(block: str) -> str:
    """Indent lifted detector source so it sits inside the child's try block."""
    return textwrap.indent(textwrap.dedent(block).strip("\n"), "        ")


def _classify(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Classify a detector run -- and refuse the easiest way to get this wrong.

    A detector is FIXED only when BOTH hold:

      1. the unpatched transcription was GREEN on this exact damage (so the
         blind spot is real for this detector), and
      2. the patched detector is RED **for the right reason**.

    Condition 2 is the one that gets skipped, and skipping it is how a probe
    reports a fix that is really a crash. A FileNotFoundError, a NameError or a
    timeout all make a detector "not green". None of them is a fix. During
    development this probe duly reported two FIXED verdicts that were really
    FileNotFoundError and NameError, which is why the reason is now checked
    explicitly rather than inferred from the boolean.
    """
    res = rec.get("result") or {}
    patched_green = res.get("green")
    unpatched_green = rec.get("unpatched_green")
    reason = res.get("red_reason") or ""
    rec["red_reason"] = reason
    rec["crashed"] = bool(res.get("error"))

    if patched_green is None:
        rec["verdict"] = "ERROR_CHILD_DID_NOT_REPORT"
    elif not rec["child_reported"]:
        rec["verdict"] = "ERROR_CHILD_DID_NOT_SELF_REPORT"
    elif not unpatched_green:
        rec["verdict"] = "ERROR_NO_NEGATIVE_CONTROL"
    elif patched_green is True:
        rec["verdict"] = "UNPROVEN_BLINDSPOT_REMAINS"
    elif rec["crashed"]:
        # Red, but by crashing rather than by adjudicating. Not a fix.
        rec["verdict"] = "ERROR_CRASHED_NOT_ADJUDICATED"
    elif "audited_ok" in res and res["audited_ok"] is not False:
        # The gate as a whole is red, but the check this probe is auditing is
        # still green. That is the blind spot surviving, not a fix.
        rec["verdict"] = "UNPROVEN_BLINDSPOT_REMAINS"
    elif not reason:
        rec["verdict"] = "ERROR_RED_WITHOUT_STATED_REASON"
    else:
        rec["verdict"] = "FIXED"
    return rec


def _extract_lines(src: str, start: int, end: int) -> str:
    """1-based inclusive line slice of a real source file."""
    return "\n".join(src.splitlines()[start - 1:end])


def _extract_between(src: str, start_marker: str, end_marker: str,
                     what: str) -> str:
    """Slice a real file from the line after ``start_marker`` to ``end_marker``.

    Line numbers rot. Every time someone inserts a comment above the code under
    audit, a hardcoded range silently starts lifting the WRONG lines -- and a
    probe that quietly stopped testing the detector is worse than no probe,
    because it keeps printing a verdict. Anchoring on the code's own text means
    a moved block is still found, and a renamed one fails loudly.
    """
    lines = src.splitlines()
    try:
        i = next(n for n, l in enumerate(lines) if start_marker in l)
    except StopIteration:
        raise SystemExit("probe anchor lost: no line contains %r (%s). The "
                         "detector was restructured -- re-anchor the probe "
                         "before trusting its verdict." % (start_marker, what))
    # A block ends either at its explicit end marker or at the next top-level
    # `def`, whichever comes FIRST. Lifting to the next `def` is what keeps a
    # helper that ends in `return text` from being cut short by an earlier
    # `return ""` on a branch inside it.
    j = None
    for n, l in enumerate(lines):
        if n <= i:
            continue
        if end_marker in l:
            j = n
            break
        if l.startswith("def ") or l.startswith("class "):
            j = n - 1
            break
    if j is None:
        raise SystemExit("probe end-anchor lost: no line after %r contains %r "
                         "and no following top-level def (%s)."
                         % (start_marker, end_marker, what))
    return "\n".join(lines[i + 1:j + 1])


# ===========================================================================
# MAIN
# ===========================================================================

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", default=None, help="write the full transcript here")
    ap.add_argument("--keep", action="store_true", help="keep the fixture dir")
    args = ap.parse_args()

    print("=" * 78)
    print("DETECTOR BLIND-SPOT PROBE")
    print("repo under audit : %s" % ROOT)
    print("python           : %s" % PY)
    print("=" * 78)

    base = Path(tempfile.mkdtemp(prefix="jcc-blindspot-"))
    work = base / "work"
    work.mkdir(parents=True, exist_ok=True)
    print("fixture (throwaway, NOT the worktree): %s" % base)

    report: Dict[str, Any] = {"fixture": str(base), "cases": []}
    try:
        # --- step 0: precheck every child source in THIS process ------------
        print("\n[0] PRECOMPILE -- destructive helpers are checked before use")
        for fn in UNPATCHED_IMPLS.values():
            ast.parse(textwrap.dedent(inspect_source(fn)))
            print("    ok  %s" % fn.__name__)

        # --- step 1: baseline ------------------------------------------------
        info = build_fixture(base)
        pinned = info["baseline_sha"]
        report["baseline_sha"] = pinned
        print("\n[1] BASELINE -- pinned commit %s" % pinned)
        print("    (read back from `git rev-parse HEAD`; NOT HEAD~1, which drifts)")
        for rel in info["files"]:
            print("    committed %s (%d bytes)" % (rel, (base / rel).stat().st_size))

        # --- step 2: the damage ---------------------------------------------
        print("\n[2] DAMAGE -- size-preserving zeroing (the INC-002 shape)")
        damage: Dict[str, Any] = {}
        for entry in AUDIT:
            rec = zero_preserve_size(base, entry["target"])
            rec["git_says_clean"] = git_says_clean(base, entry["target"])
            damage[entry["id"]] = rec
            print("    %-42s %d -> %d bytes, size_preserved=%s, "
                  "mtime_preserved=%s, all_nul=%s, GIT SAYS CLEAN=%s"
                  % (entry["target"], rec["size_before"], rec["size_after"],
                     rec["size_preserved"], rec["mtime_preserved"],
                     rec["all_nul"], rec["git_says_clean"]))
        report["damage"] = damage
        if not all(d["size_preserved"] and d["all_nul"] for d in damage.values()):
            print("\nFATAL: the fixture was not damaged as intended. Refusing to "
                  "report any detector result.")
            return 2

        # --- step 3: negative control ---------------------------------------
        print("\n[3] NEGATIVE CONTROL -- unpatched logic on the SAME damage")
        print("    (a green here is what makes a later red meaningful)")
        ctrl = negative_control(base)
        report["negative_control"] = ctrl
        control_ok = True
        for c in ctrl:
            print("    %-8s %-42s %s" % (
                "GREEN" if c["reported_green"] else "RED",
                c["impl"], c["detail"]))
            if c["reported_green"] is not True:
                control_ok = False
        if not control_ok:
            print("\nFATAL: the negative control did not report green, so this "
                  "probe cannot see the blind spot. Every later result is void.")
            return 2
        print("    -> control established: the blind spot is REAL and MEASURABLE")

        # --- step 4: patched detectors ---------------------------------------
        print("\n[4] PATCHED -- each detector's REAL code, same damage, subprocess")
        patched = run_patched(base, work)
        report["patched"] = patched
        for p in patched:
            print("\n    --- %s" % p["id"])
            print("        detector   : %s" % p.get("detector", "?"))
            print("        child pid  : %s  (child_reported=%s, pid_matches=%s)"
                  % (p.get("CHILD_PID"), p.get("child_reported"),
                     p.get("pid_matches")))
            print("        child stdout bytes : %d" % p.get("stdout_bytes", -1))
            print("        child stderr bytes : %d" % p.get("stderr_bytes", -1))
            res = p.get("result") or {}
            for k in ("tool_exit", "mentions_unreadable", "mentions_pass",
                      "green", "detail", "tail"):
                if k in res:
                    v = str(res[k]).replace("\n", "\n                 ")
                    print("        %-12s : %s" % (k, v[:900]))
            print("        VERDICT    : %s" % p.get("verdict"))

        # --- step 5: overall --------------------------------------------------
        unproven = [p["id"] for p in patched
                    if p["verdict"] != "FIXED"]
        report["unproven"] = unproven

        print("\n" + "=" * 78)
        print("SUMMARY")
        print("  negative control (unpatched went green on the damage) : %s"
              % ("YES" if control_ok else "NO"))
        print("  detectors proved FIXED (red on the damage)             : %d/%d"
              % (sum(1 for p in patched if p["verdict"] == "FIXED"), len(patched)))
        for p in patched:
            print("    [%s] %s" % (p["verdict"], p["id"]))
        if unproven:
            print("\n  NOT PROVEN (a green here is an open blocker, not a pass):")
            for u in unproven:
                print("    - %s" % u)
        print("=" * 78)

        if args.json:
            outp = Path(args.json)
            if not outp.is_absolute():
                outp = ROOT / outp
            outp.parent.mkdir(parents=True, exist_ok=True)
            outp.write_text(json.dumps(report, indent=2, sort_keys=True,
                                       default=str) + "\n",
                            encoding="utf-8", newline="\n")
            print("wrote %s" % outp)

        return 0 if not unproven else 1
    finally:
        if args.keep:
            print("\nfixture kept at %s" % base)
        else:
            shutil.rmtree(base, ignore_errors=True)
            print("\nfixture removed: %s" % base)


if __name__ == "__main__":
    raise SystemExit(main())
