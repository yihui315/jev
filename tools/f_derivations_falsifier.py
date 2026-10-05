#!/usr/bin/env python3
"""Falsifier for the P1 drive-literal repairs (F:/jev relocation class).

WHAT IS BEING CLAIMED
---------------------
No PRODUCT (gate/tool/script) file may resolve a path through a hardcoded drive
letter. The repo root must come from ``__file__`` (Python), ``$PSScriptRoot``
(PowerShell), or explicit configuration. This has broken the repo four times.

HOW THIS FALSIFIER IS BUILT, AND WHY
-------------------------------------
The failure mode this file exists to prevent is the one the brief names: "the
test did not run, and that was read as 'the guard is effective'". Four
falsifiers in this project already died that way. So every claim below is
backed by a SUBPROCESS whose exit code and output we capture, and the central
claim -- that the detector actually detects -- is proven by a NEGATIVE CONTROL:

  * We COPY a real repaired file to a scratch location.
  * We INJECT a genuine ``F:/jev`` live-code literal into the copy.
  * We assert the detector goes RED on the copy.
  * We REMOVE the injection and assert it goes GREEN again.
  * Only then do we run the detector over the real tree.

If the injection step silently failed to inject, the RED assertion would fail
loudly rather than pass -- so "the detector is red" can never be reported unless
the detector really saw the literal. We also print the injected line back, so
the mutation is visible in the transcript rather than asserted.

TOKEN-LEVEL, NOT GREP
---------------------
A drive literal in a docstring is documentation. The same literal in a live
string is the defect. ``grep`` cannot tell them apart, and that ambiguity has
already produced two "confident, clean, incorrect zeros" in this project. So
Python files are analysed with ``tokenize``: we mark the exact line spans
occupied by module/class/function docstrings and only count STRING/NAME tokens
outside them. PowerShell gets a small lexer, because there the distinction is
three-way -- ``<# #>`` block comment, ``#`` line comment, and string (which in
PowerShell IS live code, so masking strings would hide the very defect we hunt).

usage: python tools/f_derivations_falsifier.py
exit 0 = guard holds, 1 = a drive literal is live in product code
"""
from __future__ import annotations

import ast
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable

# ---------------------------------------------------------------------------
# SCOPE
# ---------------------------------------------------------------------------
# Directories that hold historical evidence or scratch state. A drive literal in
# a signed evidence snapshot is part of the record, not a defect: rewriting it
# would falsify the audit trail. Same for scratch/continuation-superseded.
EVIDENCE_PREFIXES = (
    "artifacts/",
    "docs/",
    "fixtures/",
    "runtime/",
    ".workbuddy/",
    ".git/",
    # Archived, replaced, and referenced by nothing live: verified by grepping
    # tools/ scripts/ packages/ for "superseded" and getting only unrelated
    # prose hits. These files are history; editing them would rewrite history.
    "tools/continuation/superseded/",
)
EVIDENCE_SUFFIXES = (".md", ".json")

# ---------------------------------------------------------------------------
# ADJUDICATED EXCEPTIONS -- each one names its reason and its owner
# ---------------------------------------------------------------------------
# These files legitimately contain a repo-root drive literal, and the reason is
# the same in every case: the literal IS the test input. A relocation detector
# that is handed a bad value must be able to see it; "cleaning" these would
# delete the very thing being asserted. They are listed here -- not silently
# skipped -- so the exemption is auditable and so that a NEW file cannot quietly
# inherit the exemption.
#
# (file-substring, reason, ruling owner)
ADJUDICATED_EXCEPTIONS: tuple[tuple[str, str, str], ...] = (
    ("packages/jcc_core/tests/test_relocation_derivations.py",
     "asserts _looks_like_absolute_repo_root() returns True for bad values "
     "('F:/jev', 'D:\\\\jev'); and monkeypatches JCC_ROOT to 'D:/jev' to prove "
     "the override still wins. The literals are the assertions.",
     "team-lead (explicitly ruled 'do not change')"),
    ("tools/cr003_project_root_falsifier.py",
     "CR-003 containment falsifier: 'D:/jev/../etc' and 'Z:/somewhere/else' are "
     "the traversal and cross-drive probes. _is_inside() must refuse them.",
     "relay (author of CR-003)"),
    ("packages/jcc_core/tests/test_project_scope_v1_3.py",
     "RISK-1 write-entry containment test: the same _is_inside() probes "
     "('D:/jev/../etc', 'Z:/somewhere/else'). Its own docstring says a rewrite "
     "would make 'the cases above measure the rewrite rather than the wiring'.",
     "forge2 (RISK-1 author)"),
    ("tools/port_watch.py",
     "selftest() -- 'exercised directly on synthetic inputs', cases are "
     "'D:/jev/config/jcc.yaml'->GREEN and 'D:/jev/runtime/jcc-v1.3/jcc.db'->"
     "PRODUCT. classify_by_paths() must key on the 'jev' directory name, so the "
     "inputs MUST name it; a rewritten input would test nothing. Also includes a "
     "deliberate 'C:\\\\somewhere\\\\else'->UNKNOWN case.",
     "relay (CR-002 GREEN-port owner)"),
    ("tools/crh1_basetemp_falsifier.py",
     "the literal appears inside the string that DESCRIBES the measured set; the "
     "measurement it performs is 'this helper hardcodes no drive letter'.",
     "metric-verify"),
    ("tools/continuation/write_final_acceptance_report.py",
     "report PROSE: a fenced code block showing an operator the stop command. It "
     "is emitted text, never executed, and ROOT is already derived at L16.",
     "team-lead (report wording, not runtime)"),
    # This file necessarily NAMES the literals it hunts: the exception table
    # quotes them, and the negative control injects one. A detector that flagged
    # its own rulebook would be unusable, and worse, it would train a reader to
    # ignore it. The literals here are the SPECIFICATION, not the defect.
    ("tools/f_derivations_falsifier.py",
     "this file: the ADJUDICATED_EXCEPTIONS table quotes the bad values it "
     "explains, and CONTROL_MARKER is the string the negative control injects. "
     "The literals are the specification of what must be detected.",
     "p1-tooling (self)"),
    ("tools/f_derivations_selftest.py",
     "the self-test's own fixtures. Case 7 writes b'X = \"F:/jev\"' because a "
     "plain drive literal is the POSITIVE control -- if the detector stopped "
     "finding it, the suite would pass while measuring nothing. Case 8 writes "
     "a CR-split variant to prove that shape is correctly NOT reported. These "
     "literals are the inputs, not defects; rewriting them would delete the "
     "assertion.",
     "p1-tooling (self)"),
)

# Non-Python, non-PowerShell files carry no live code we execute, and their
# drive mentions are descriptive text (checked separately as INERT).
SCANNED_SUFFIXES = (".py", ".ps1")

DRIVE_RE = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z]):[\\/]")

# ---------------------------------------------------------------------------
# SCOPE OF THE CLAIM -- read this before trusting a zero
# ---------------------------------------------------------------------------
# P1's rule is that the REPOSITORY ROOT must not be baked in as a drive
# literal. It is NOT a rule that no string anywhere may contain a colon and a
# backslash. The repo legitimately contains:
#
#   * `C:\Program Files\...`  -- where a host-installed exe lives
#   * `C:\Users\Administrator` -- an opaque cwd in a probe payload
#   * `os.environ["SystemDrive"]`, `%SystemRoot%` -- derived, not typed
#
# Those are host-install facts, not relocation hazards: move the checkout and
# they are still correct. Flagging them would bury the signal this file exists
# to raise, and "129 hits" would have been a number nobody could act on.
#
# So a hit counts only when it names the REPO ROOT -- i.e. it ends in `jev`
# (this project's directory name) or is a bare `<drive>:/jev`. Everything else
# is reported as CONTEXT so the exclusion is auditable rather than invisible.
REPO_DIR_NAME = "jev"


def is_repo_root_literal(text: str, source_line: str = "") -> bool:
    """True when the literal names THIS repository's root directory."""
    blob = (text + " " + source_line)
    # normalise separators so `D:\jev` and `D:/jev` are the same claim
    flat = blob.replace("\\", "/")
    return bool(re.search(r"(?i)(?<![A-Za-z0-9_])[A-Za-z]:/" + REPO_DIR_NAME + r"(/|\b|$)", flat))


# ---------------------------------------------------------------------------
# PYTHON: token-level, docstring-aware
# ---------------------------------------------------------------------------
def _docstring_lines(src: str) -> set[int]:
    """1-based lines covered by module/class/function docstrings."""
    covered: set[int] = set()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return covered
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef,
                                 ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            end = first.end_lineno or first.lineno
            covered.update(range(first.lineno, end + 1))
    return covered


def scan_python(path: Path) -> list[dict]:
    """Live drive literals in a Python file (docstrings and comments excluded).

    NOTE ON LINE ENDINGS, because this is a known trap in this repo.
    `read_text()` opens in universal-newline mode, which translates a lone "\\r"
    into "\\n" just as it translates "\\r\\n". So an in-band CR -- a CR in the
    MIDDLE of a line, which is real content damage and not a line ending -- is
    silently re-flowed before tokenize ever sees it.

    That was measured here rather than assumed, because the obvious mitigation is
    itself the bug weaver retracted from the INC-002 rule: `tr -d '\\r'` (or
    Python's `b.replace(b"\\r", b"")`) deletes EVERY CR, so it makes an in-band
    CR injection hash-identical to a benign CRLF file -- a false NEGATIVE, not a
    labelling nit. Only line-ENDING CR may be stripped: `sed 's/\\r$//'` in
    shell, `b.replace(b"\\r\\n", b"\\n")` in Python. This scanner strips nothing.

    Universal-newline folding is safe HERE, and that is a measured result with a
    specific shape. In-band CR was tested at five placements: before the drive
    letter, between drive and colon, between colon and slash, after the closing
    quote, and in an adjacent comment. On every one, the RAW BYTES already
    contain no drive path under DRIVE_RE, because the CR splits `F:` away from
    `/jev`. So there is nothing for this gate to report, and reporting CLEAN is
    correct rather than a miss.

    That is a statement about THIS gate's subject, not about corruption. A CR
    that splits a literal is real damage, but detecting damage-as-such is the
    job of the NUL check below and of the tree-level CR normalisation rule
    elsewhere -- not of a drive-literal counter. The two concerns are
    orthogonal, and conflating them is how a gate ends up claiming coverage it
    does not have.

    The dataset above is discriminating, not vacuous: in each of those five
    rows the raw bytes and the folded text genuinely differ, so a method that
    stripped in-band CR and one that did not would disagree. Agreement here
    would have proved nothing; divergence is what makes the result meaningful.
    """
    # NUL check FIRST, and explicitly, because relying on the parser to catch
    # NUL is relying on an accident.
    #
    # WHY, HONESTLY. An earlier version of this comment claimed that "a NUL
    # inside a COMMENT leaves the file perfectly valid Python -- it compiles,
    # tokenize is happy". That was FALSE, and how it went false is worth
    # recording so nobody repeats it. The probe backing it ran
    # `bytes.replace(b"# frozen", b"# \x00roze", 1)` against this very file --
    # and the string "# frozen" does not occur here (find returns -1), so the
    # replace was a NO-OP. The sample stayed clean, the probe correctly
    # reported no NUL, and I read that clean result as proof of a hole instead
    # of as the no-op it was. Measured properly, BOTH `compile()` and
    # `tokenize` REJECT a NUL wherever it appears: comment, string, or EOL.
    #
    # So the parse-failure path would in fact have caught most NUL placements
    # on its own. The explicit check stays anyway, for two real reasons rather
    # than the invented one:
    #   1. It states the invariant instead of inheriting it. A future CPython
    #      that tolerated a NUL somewhere would silently un-fix this gate, and
    #      the failure would be invisible -- no crash, just a green result.
    #   2. It removes the dependence on WHICH stage notices, which is the same
    #      "right by luck, not by correctness" shape aegis flagged when
    #      retracting his own CR conclusion.
    #
    # INC-002 is documented as size-preserving, and partial zeroing preserves
    # size as well as total zeroing does, so the signal to test is "this file
    # contains NUL AT ALL" rather than "this file failed to parse".
    #
    # The test is deliberately `b"\x00" in raw`, i.e. exactly `s != z` (total
    # byte count vs non-NUL byte count), with NO run-length or count threshold.
    # A "longest NUL run >= 64" rule was proposed and is wrong here: it PASSES a
    # file with 1, 2 or 3 NULs at preserved size, and a single zeroed byte at
    # unchanged size is exactly the stat-cache attack shape. Measured on this
    # file: 1/2/3/10/25 NULs at size 29 all flag, where a run>=64 rule reports
    # clean. Do not "optimise" this into a threshold.
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    if b"\x00" in raw:
        nul = sum(1 for b in raw if b == 0)
        return [{"file": path.name, "line": 0, "kind": "UNREADABLE",
                 "text": "contains %d NUL byte(s) at unchanged size "
                         "(zeroed/corrupt; the file may still parse, so a parse "
                         "check alone would miss it)" % nul}]
    try:
        src = raw.decode("utf-8", errors="replace")
    except OSError:
        return []
    docs = _docstring_lines(src)
    hits: list[dict] = []
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError) as exc:
        # An unparseable file is NOT a clean file. INC-002 replaced a tracked
        # file's bytes with NULs while leaving its size (and therefore git's
        # stat cache) intact; such a file yields no tokens, so a detector that
        # only counts literals sees "zero hits" and reports GREEN on exactly the
        # damage it exists to catch. Measured end-to-end: with one zeroed
        # product file in the tree, this tool used to exit 0 with
        # "FALSIFIER: PASS". Emit an UNREADABLE marker so main() can refuse.
        return [{"file": path.name, "line": 0, "kind": "UNREADABLE",
                 "text": "%s: file does not parse as source (zeroed or corrupt?)"
                         % type(exc).__name__}]
    for tok in toks:
        if tok.type != tokenize.STRING:
            continue
        if tok.start[0] in docs:
            continue  # docstring: documentation, inert
        for m in DRIVE_RE.finditer(tok.string):
            hits.append({"file": str(path), "line": tok.start[0],
                         "kind": "live-string", "text": m.group(0)})
    return hits


# ---------------------------------------------------------------------------
# POWERSHELL: three-way lexer (block comment / line comment / string+code)
# ---------------------------------------------------------------------------
def scan_powershell(text: str, label: str) -> tuple[list[dict], list[dict]]:
    """Return (live_hits, inert_hits) for PowerShell source.

    In PowerShell a quoted string is a VALUE, so a drive literal inside quotes is
    live code. Masking strings before searching -- the obvious implementation --
    hides exactly the defect we are looking for.
    """
    live: list[dict] = []
    inert: list[dict] = []
    i, n = 0, len(text)
    line = 1
    state = "code"
    quote = ""
    buf: list[str] = []

    def flush(kind: str) -> None:
        if not buf:
            return
        blob = "".join(buf)
        for m in DRIVE_RE.finditer(blob):
            rec = {"file": label, "line": line, "kind": kind, "text": m.group(0)}
            (live if kind in ("string", "code") else inert).append(rec)
        buf.clear()

    while i < n:
        c = text[i]
        if state == "code":
            if text.startswith("<#", i):
                state = "block"; i += 2; continue
            if c == "#":
                state = "line"; i += 1; continue
            if c in "'\"":
                state = "str"; quote = c; buf.append(c); i += 1; continue
            if c == "`":
                buf.append(c)
                if i + 1 < n:
                    buf.append(text[i + 1])
                i += 2; continue
            if c == "\n":
                buf.append(c); flush("code"); line += 1
            else:
                buf.append(c)
            i += 1; continue
        if state == "block":
            if text.startswith("#>", i):
                flush("block-comment"); state = "code"; i += 2; continue
            if c == "\n":
                buf.append(c); flush("block-comment"); line += 1
            else:
                buf.append(c)
            i += 1; continue
        if state == "line":
            if c == "\n":
                flush("line-comment"); state = "code"; line += 1
            else:
                buf.append(c)
            i += 1; continue
        if state == "str":
            if c == "`":
                buf.append(c)
                if i + 1 < n:
                    buf.append(text[i + 1])
                i += 2; continue
            # a doubled single quote is an escaped quote, not a terminator
            if c == "'" and i + 1 < n and text[i + 1] == "'":
                buf.append(c); buf.append("'"); i += 2; continue
            if c == quote:
                buf.append(c); flush("string"); state = "code"; i += 1; continue
            if c == "\n":
                line += 1
            buf.append(c)
            i += 1; continue
        i += 1
    return live, inert


def scan_ps_file(path: Path) -> tuple[list[dict], list[dict]]:
    raw = path.read_bytes()
    # Same NUL rule as scan_python, and for the same reason: PowerShell has no
    # parser step here, so a NUL would flow straight through the lexer and the
    # file would simply look like it had fewer literals. A NUL is the strong
    # signal, so test for it directly rather than hoping a read fails.
    if b"\x00" in raw:
        nul = sum(1 for b in raw if b == 0)
        return ([{"file": str(path), "line": 0, "kind": "UNREADABLE",
                  "text": "contains %d NUL byte(s) at unchanged size "
                          "(zeroed/corrupt)" % nul}], [])
    text = raw.decode("utf-8-sig", errors="replace")
    return scan_powershell(text, str(path))


# ---------------------------------------------------------------------------
# INVENTORY
# ---------------------------------------------------------------------------
def tracked_files() -> list[str]:
    proc = subprocess.run(["git", "-C", str(ROOT), "ls-files"],
                          capture_output=True)
    if proc.returncode != 0:
        return []
    return [l for l in proc.stdout.decode("utf-8", "replace").splitlines() if l.strip()]


def in_evidence_scope(rel: str) -> bool:
    norm = rel.replace("\\", "/")
    if norm.startswith(EVIDENCE_PREFIXES):
        return True
    return norm.endswith(EVIDENCE_SUFFIXES)


def inventory() -> tuple[list[dict], list[dict], list[dict]]:
    """Scan every tracked product file.

    Returns (violations, context, inert):
      violations -- live literals that name the REPO ROOT. These are the defect.
      context    -- live literals that do NOT (host-install paths). Listed so the
                    exclusion is auditable instead of a silent narrowing.
      inert      -- docstrings and comments, preserved on purpose.
    """
    violations: list[dict] = []
    context: list[dict] = []
    inert: list[dict] = []
    for rel in tracked_files():
        if in_evidence_scope(rel):
            continue
        p = ROOT / rel
        if not p.is_file():
            continue
        if p.suffix.lower() == ".py":
            raw_hits = scan_python(p)
            try:
                lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                lines = []
            for h in raw_hits:
                h["file"] = rel
                src_line = lines[h["line"] - 1] if 0 < h["line"] <= len(lines) else ""
                if is_repo_root_literal(h["text"], src_line):
                    violations.append(h)
                else:
                    context.append(h)
        elif p.suffix.lower() == ".ps1":
            lo, inert_hits = scan_ps_file(p)
            try:
                lines = p.read_text(encoding="utf-8-sig",
                                    errors="replace").splitlines()
            except OSError:
                lines = []
            for h in lo:
                h["file"] = rel
                src_line = lines[h["line"] - 1] if 0 < h["line"] <= len(lines) else ""
                if is_repo_root_literal(h["text"], src_line):
                    violations.append(h)
                else:
                    context.append(h)
            for h in inert_hits:
                h["file"] = rel
                inert.append(h)
    return violations, context, inert


# ---------------------------------------------------------------------------
# NEGATIVE CONTROL -- prove the detector can actually go red
# ---------------------------------------------------------------------------
CONTROL_TARGET = "artifacts/security/freeze_check.py"
CONTROL_MARKER = "F:/jev"


def negative_control() -> tuple[bool, str]:
    """Inject a real literal into a COPY of a repaired file; detector must fire.

    Returns (ok, transcript). `ok` is True only if BOTH the RED and the GREEN
    assertion held, which is only possible if the injection really happened.
    """
    src = ROOT / CONTROL_TARGET
    if not src.is_file():
        return False, f"control target missing: {CONTROL_TARGET}"

    original = src.read_text(encoding="utf-8")
    work = Path(tempfile.mkdtemp(prefix="p1-falsifier-ctrl-"))
    copy = work / "freeze_check_injected.py"
    lines: list[str] = []

    # (1) BASELINE: the pristine copy must already be GREEN.
    copy.write_text(original, encoding="utf-8")
    base_hits = scan_python(copy)
    transcript = ["[control] baseline (unmodified copy): %d live hit(s)" % len(base_hits)]

    # (2) INJECT: a real module-level literal in live-code position.
    #     Appended as a statement (not a docstring), so tokenize sees it as a
    #     live STRING token -- exactly the shape of the original defect.
    injected_line = '_CONTROL_CANARY = "%s"\n' % CONTROL_MARKER
    copy.write_text(original + injected_line, encoding="utf-8")
    after_text = copy.read_text(encoding="utf-8")
    injected_ok = injected_line.strip() in after_text
    inj_hits = scan_python(copy)
    transcript.append("[control] injection written: %s" % injected_ok)
    transcript.append("[control] injected line   : %s" % injected_line.strip())
    transcript.append("[control] after injection : %d live hit(s)" % len(inj_hits))
    for h in inj_hits:
        transcript.append("[control]   -> %s:%s %s %r" % (h["file"], h["line"], h["kind"], h["text"]))

    red = bool(inj_hits) and any(
        # DRIVE_RE captures the drive + separator (e.g. "F:/"), not the whole
        # path, so match on that. Asserting the full literal here made the
        # control fail on a detector that was working correctly.
        h["text"].rstrip("\\/").upper() == CONTROL_MARKER[0].upper() + ":"
        for h in inj_hits
    )
    transcript.append("[control] RED assertion   : %s" % ("PASS" if red else "FAIL"))

    # (3) RESTORE: remove the injection; detector must be GREEN again.
    copy.write_text(original, encoding="utf-8")
    restored_hits = scan_python(copy)
    green = not restored_hits
    transcript.append("[control] after restore  : %d live hit(s)" % len(restored_hits))
    transcript.append("[control] GREEN assertion : %s" % ("PASS" if green else "FAIL"))

    shutil.rmtree(work, ignore_errors=True)
    ok = injected_ok and red and green
    transcript.append("[control] VERDICT         : %s" % ("PASS" if ok else "FAIL"))
    return ok, "\n".join(transcript)


# ---------------------------------------------------------------------------
# SUBJECT-EXECUTION PROOF -- the tools really run
# ---------------------------------------------------------------------------
def run_freeze_check() -> dict:
    """Actually EXECUTE freeze_check.py and capture everything.

    We never import it: many scripts in this repo lack `__main__` guards, so
    import == execution. We spawn it and read its real verdict.
    """
    script = ROOT / "artifacts" / "security" / "freeze_check.py"
    if not script.is_file():
        return {"started": False, "reason": "script missing"}
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                          capture_output=True)
    head_sha = head.stdout.decode().strip()
    argv = [PY, str(script), head_sha, head_sha]
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=120, cwd=str(ROOT))
    except subprocess.TimeoutExpired:
        return {"started": True, "timed_out": True, "argv": argv}
    out = proc.stdout.decode("utf-8", "replace")
    err = proc.stderr.decode("utf-8", "replace")
    return {
        "started": True,
        "argv": argv,
        "returncode": proc.returncode,
        "stdout": out,
        "stderr": err,
        # A run that merely "did not crash" is not a check. Require that the
        # gate actually reached a verdict.
        "reached_verdict": ("FREEZE CHECK: COMPLIANT" in out
                            or "FREEZE CHECK: NOT COMPLIANT" in out),
        "no_traceback": "Traceback (most recent call last)" not in err,
    }


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main() -> int:
    print("=" * 74)
    print("P1 drive-literal falsifier")
    print("repo root (derived from __file__): %s" % ROOT)
    print("=" * 74)

    failures: list[str] = []

    # --- 1. negative control -------------------------------------------
    print("\n[1] NEGATIVE CONTROL -- can this detector detect at all?")
    ctrl_ok, transcript = negative_control()
    print(transcript)
    if not ctrl_ok:
        failures.append("negative control did not go red->green; detector unproven")
        print("    RESULT: FAIL -- the guard is UNPROVEN, so a later GREEN means nothing")
        print("\n" + "=" * 74)
        print("FALSIFIER: FAIL (guard unproven)")
        return 1
    print("    RESULT: PASS -- detector provably fires on a real injected literal")

    # --- 2. subject execution proof -------------------------------------
    print("\n[2] SUBJECT EXECUTION PROOF -- does freeze_check.py actually run?")
    fc = run_freeze_check()
    if not fc.get("started"):
        print("    FAIL  not started: %s" % fc.get("reason"))
        failures.append("freeze_check.py did not start")
    elif fc.get("timed_out"):
        print("    FAIL  started but timed out")
        failures.append("freeze_check.py timed out")
    else:
        print("    argv      : %s" % " ".join(fc["argv"][1:]))
        print("    exit code : %d" % fc["returncode"])
        print("    reached verdict : %s" % fc["reached_verdict"])
        print("    no traceback   : %s" % fc["no_traceback"])
        for line in fc["stdout"].strip().splitlines()[-6:]:
            print("    stdout| %s" % line)
        if not fc["no_traceback"]:
            failures.append("freeze_check.py raised: %s"
                            % fc["stderr"].strip().splitlines()[-1])
        if not fc["reached_verdict"]:
            failures.append("freeze_check.py exited without reaching a verdict")
        if fc["returncode"] not in (0, 1):
            failures.append("freeze_check.py exit code %d is neither 0 nor 1"
                            % fc["returncode"])
    print("    RESULT: %s" % ("PASS" if not failures else "see failures"))

    # --- 3. the actual inventory ---------------------------------------
    print("\n[3] INVENTORY -- tracked product files")
    violations, context, inert = inventory()
    print("    repo-root drive literals (raw)     : %d" % len(violations))
    print("    other live drive literals (context): %d  (host-install paths)" % len(context))
    print("    inert docstring/comment (preserved): %d" % len(inert))

    # --- 3b. UNREADABLE FILES: refuse before anything else ---------------
    # A file that does not parse is not clean, it is unknown. This check comes
    # BEFORE adjudication on purpose: an exemption names a file whose literals
    # are deliberate, which is a claim about CONTENT. A file with no parsable
    # content makes that claim meaningless, so no exemption may silence it.
    unreadable = [h for h in violations + context if h["kind"] == "UNREADABLE"]
    if unreadable:
        print("\n    UNREADABLE product files (INC-002 signature):")
        for h in unreadable:
            print("      %s  %s" % (h["file"], h["text"]))
        print("      -> INC-002 replaces bytes with NULs and leaves SIZE intact,")
        print("         so git's stat cache still calls the file clean. A")
        print("         relocation gate that only counts literals reports this")
        print("         as green. Refusing: an unparsable file is UNKNOWN, not OK.")
        failures.append("%d product file(s) do not parse (zeroed or corrupt)"
                        % len(unreadable))
    else:
        print("    unreadable product files              : 0")

    # --- 4. per-violation adjudication ---------------------------------
    # Every remaining hit must carry a stated reason from ADJUDICATED_EXCEPTIONS.
    # A file not on that list is a real defect, even if it looks harmless.
    print("\n[4] ADJUDICATION -- each keeper states why it stays")
    keepers: list[dict] = []
    unadjudicated: list[dict] = []
    for h in violations:
        if h["kind"] == "UNREADABLE":
            continue  # already refused in 3b; not a literal, cannot be exempted
        reason = next((r for frag, r, _ in ADJUDICATED_EXCEPTIONS
                       if frag in h["file"]), None)
        h["reason"] = reason
        (keepers if reason else unadjudicated).append(h)

    for h in keepers:
        print("      KEEP %s:%s  %s" % (h["file"], h["line"], h["text"]))
    seen = set()
    for frag, reason, owner in ADJUDICATED_EXCEPTIONS:
        if frag in seen:
            continue
        seen.add(frag)
        print("        reason : %s" % reason)
        print("        owner  : %s" % owner)
    print("        (%d adjudicated hit(s) across %d file(s))"
          % (len(keepers), len(seen)))

    if unadjudicated:
        print("\n    UNADJUDICATED -- these ARE defects:")
        for h in unadjudicated:
            print("      %s:%s  %s  %s" % (h["file"], h["line"], h["kind"], h["text"]))
        failures.append("%d repo-root drive literal(s) with no ruling"
                        % len(unadjudicated))
    else:
        print("\n    UNADJUDICATED: 0  (every remaining hit has a stated owner)")

    # --- 5. verdict ----------------------------------------------------
    print("\n" + "=" * 74)
    if failures:
        print("FALSIFIER: FAIL")
        for f in failures:
            print("  - %s" % f)
        print("=" * 74)
        return 1
    print("FALSIFIER: PASS")
    print("  negative control red->green   : PROVEN (injection visible above)")
    print("  subject executed               : YES (exit code + stdout captured)")
    print("  live drive literals, adjudicated keepers : %d" % len(keepers))
    print("  live drive literals, UNADJUDICATED       : 0")
    print("  host-install literals (out of scope)    : %d" % len(context))
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
