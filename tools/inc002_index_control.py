#!/usr/bin/env python3
"""INC-002 -- the index-level negative control nobody has signed off on.

THE GAP THIS CLOSES
-------------------
A full-tree integrity sweep compares each worktree file against its HEAD blob and
reports ZEROED / HIDDEN / DIFFER. weaver's sweep found ZEROED=0, HIDDEN=0,
DIFFER=3 (the three E1/E4/F1 legs, independently shown to be legitimate re-runs).

weaver stated the boundary explicitly and refused to sign the stronger claim:

    "HIDDEN=0 is an OBSERVATION, not a proof that it cannot happen. The
     index-level stat-cache forgery negative control is still missing. I will not
     sign that claim."

That is the correct refusal, and it is exactly the gap this tool fills. HIDDEN=0
means "a content comparison, when performed, found nothing hidden". It does NOT
mean "a content comparison was forced to happen". `git diff` decides WHETHER to
compare bytes using the index stat cache (mtime + size), so a file whose bytes
were rewritten and whose mtime+size were then restored is reported CLEAN without
git ever reading its content.

So the question HIDDEN=0 cannot answer is: **was any file's content actually read?**

WHAT THIS DOES
--------------
For every tracked file this tool reads the HEAD blob and the worktree bytes
DIRECTLY, via `git cat-file --batch` in ONE spawn. It never consults the stat
cache, so a file that `git diff` would skip is still read and compared. Then it
reports, separately:

  * ZEROED   -- the file CONTAINS NUL bytes. NOT "is entirely NUL": any single
                NUL byte makes `len(data) != len(data.replace(NUL, b""))`, so
                partial zeroing is caught here too. The one damage shape this
                cannot see is an INLINE-CR INJECTION, which contains no NUL at
                all -- that is the normalised hash's job, not this check's.
                (Corrected 2026-10-05: an earlier version of this docstring, and
                of the team-wide guidance, described this check as "all NUL
                only, misses (a')". That was wrong -- it detects "contains NUL"
                and does catch (a').)
  * HIDDEN   -- `git status` reports the file CLEAN, yet the bytes DIFFER
                (this is the forgery shape, and the reason stat cache matters)
  * INDEX    -- the file carries assume-unchanged / skip-worktree, the two index
                flags that make git skip content comparison for that path
  * EOL      -- bytes differ ONLY by CRLF/LF, i.e. not a real difference

A HEALTHY TREE IS THE POINT. This tool must be able to say "clean", or it is just
a louder way of saying nothing. The negative controls below prove the detectors
actually fire, by forging each shape in a throwaway fixture and asserting it is
caught -- and that a clean tree is NOT flagged.

Nothing here writes to the repository, starts or signals any process, or touches
a port. It reads.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: Prefixes git uses in `ls-files -v`. These mark paths git will not
#: content-compare: a LOWERCASE letter is assume-unchanged (`h` is the usual
#: one), and `S` is skip-worktree. Both make git trust the stat cache for that
#: path, which is exactly the "content never gets read" condition under test.
#: `h` was missing from the first version of this set, so the detector reported
#: INDEX_FLAGGED=0 on a tree that carried the flag -- the failure mode a
#: corruption check must never have.
SKIP_CONTENT_FLAGS = ("a", "c", "h", "m", "r", "s", "S")


def git(*args: str, repo: "Path | None" = None) -> str:
    """Run git in ``repo`` (default: this repository).

    ``repo`` is a parameter, not a constant, because the selftest forges each
    corruption shape in a THROWAWAY fixture. When the helpers ignored it they
    read the real tree instead, so every negative control below was vacuous --
    it "failed" against a repository that was never modified.
    """
    proc = subprocess.run(["git", *args], cwd=str(repo or REPO), text=True,
                          errors="replace", stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL)
    return proc.stdout or ""


def load_index(repo: "Path | None" = None) -> "dict[str, str]":
    """path -> blob sha, from the index. One spawn.

    `git ls-files -s` emits ``<mode> <sha> <stage>\\t<path>``, so the sha is
    field 1 and the path is field 3 -- reading the sha as field 0 silently keys
    the whole index by MODE ("100644"), which is a dict of 6 entries that looks
    like an empty tree rather than an error. Verified against a real line:
    ``100644 018cd8c2... 0<TAB>.gitattributes``.
    """
    out: dict[str, str] = {}
    for line in git("ls-files", "-s", repo=repo).splitlines():
        if not line.strip():
            continue
        meta, _, path = line.partition("\t")
        fields = meta.split()
        if len(fields) >= 2 and path:
            out[path.replace("\\", "/")] = fields[1]
    return out


def head_blobs(index: "dict[str, str]", repo: "Path | None" = None) -> "dict[str, bytes]":
    """path -> HEAD blob bytes, via ONE `cat-file --batch` spawn.

    Requests are keyed by PATH (``HEAD:<path>``), never by the index sha. The
    index records what is STAGED, which after any unstaged edit is not what is
    committed, so ``HEAD:<index-sha>`` answers ``missing`` for exactly the files
    an integrity sweep most needs to see -- and "missing" parses as nothing at
    all, so the sweep reports a clean tree. Asking for the committed PATH gets
    the committed BYTES, which is the thing being compared.

    One spawn for the whole tree: a per-file loop costs four spawns per file and
    timed out twice on 713 files (weaver's measurement). Batch mode is not an
    optimisation here, it is the difference between running and not running.
    """
    if not index:
        return {}
    payload = "".join("HEAD:%s\n" % p for p in index)
    # BYTES, not str: this call sets neither `text=True` nor `encoding=`, so
    # stdin must be bytes. Passing str raised TypeError from _stdin_write, and
    # the process then sat on a pipe nobody drained until the harness killed it
    # at 239s -- which made a one-line bug look like a hang.
    proc = subprocess.run(["git", "cat-file", "--batch"], cwd=str(repo or REPO),
                          input=payload.encode("utf-8"),
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    blobs: dict[str, bytes] = {}
    stream = proc.stdout or b""
    pos = 0
    # Replies come back in REQUEST ORDER, one per input line, and the name git
    # echoes is the RESOLVED SHA ("890a0d73... blob 20585"), not the path we
    # asked for. So the reply cannot be keyed by its own header: the Nth reply
    # belongs to the Nth path we sent. Keying by header name -- or trying to
    # strip a "HEAD:" prefix that is not there -- silently yields an empty dict,
    # i.e. a sweep that reports a clean tree having compared nothing.
    #
    # A path absent from HEAD answers "<path> missing" (2 fields, no payload),
    # so `resolved[pos]` still lines up: it is the sentinel `None`.
    requested = list(index)
    resolved: "list[bytes | None]" = []
    while pos < len(stream):
        nl = stream.find(b"\n", pos)
        if nl < 0:
            break
        header = stream[pos:nl].split()
        if len(header) < 3:
            resolved.append(None)          # missing / ambiguous: no payload
            pos = nl + 1
            continue
        size = int(header[2])
        resolved.append(stream[nl + 1:nl + 1 + size])
        pos = nl + 1 + size + 1            # payload + trailing newline
    for path, data in zip(requested, resolved):
        if data is not None:
            blobs[path] = data
    return blobs


def logical_lines(data: bytes) -> "list[bytes]":
    """Split into lines, dropping ONE trailing CR from each -- the line terminator.

    This is the only representation that answers "is this the same file, written
    with a different choice of line endings?".
    """
    return [ln[:-1] if ln.endswith(b"\r") else ln for ln in data.split(b"\n")]


def is_pure_eol_choice(work: bytes, head: bytes) -> bool:
    """Is this difference PURELY a CRLF/LF choice? `--all` decides; nothing else.

    THE HISTORY OF THIS FUNCTION, because both wrong versions were mine:

    v1 -- fold equality only (`normalise(w) == normalise(h)`). WRONG: folding maps
         two different files onto one value, irreversibly.
    v2 -- fold equality AND no lone CR survives. ALSO WRONG, in the opposite
         direction: a lone CR can be legitimate CONTENT, and then this refuses a
         pair that really is a pure smudge.

    MEASURED on a five-case table (correct / v2 / this):
        61 0d0a 0d62 0a   vs 61 0a0d 62 0d0a  eol-only  v2 FALSE(x)  this TRUE
        a\\rb\\r\\n         vs a\\rb\\n         eol-only  v2 FALSE(x)  this TRUE
        a\\rb\\r\\n         vs a\\nb\\r\\n       differ     v2 false     this false
        aXb\\r\\n           vs aYb\\n           differ     v2 false     this false
        plain CRLF smudge                         eol-only  v2 true      this true
        --> v2 scored 3/5; this scores 5/5.

    v2's error is the same shape as the `tr -d '\\r'` false positive, pointed the
    other way: it reported damage that was not there. The lone CR in `a\\rb` is
    content -- `core.autocrlf` will not invent it -- so its presence says nothing
    about whether the endings changed.

    WHY LOGICAL LINES. prism-3dui established that folding is lossy about
    line-ending PLACEMENT, not about content: two files can differ in where the
    CR sits between line boundaries and still hold identical text. Both of us
    got this wrong once, in opposite directions, by comparing the wrong
    representation (raw split, then folded bytes). Comparing logical lines is the
    representation the question actually asks for.

    NOTE this classifies ONLY. It is reached after raw bytes already differed, so
    it can never turn a difference into a verdict.
    """
    return logical_lines(work) == logical_lines(head)


def normalise(data: bytes) -> bytes:
    """Fold the CRLF PAIR to LF so line-ending smudging is not a difference.

    It replaces the two-byte PAIR, never a lone CR. That distinction is the whole
    point, and `tr -d '\\r'` gets it wrong: deleting every CR byte also deletes an
    IN-BAND CR, so a file whose content gained a stray CR normalises to the same
    bytes as the clean file and the corruption is reported as MATCH.

    Verified (prism-3dui raised this against a doc of mine that used `tr`):
        clean "ab\\r\\n"        -> 61 62 0a        (ours)  / 61 62 0a        (tr)  both
        inject "a\\rb\\r\\n"     -> 61 0d 62 0a     (ours)  / 61 62 0a        (tr)  <- tr MASKS
    So this function detects what `tr -d '\\r'` cannot.

    WHY THE SELFTEST IS NOT SELF-CONFIRMING (weaver's (a)/(b) rule, adopted).
    A claim that this is "stricter" only counts if the false negative is
    REPRODUCIBLE ON THE DATA. On this repository it is not: every tracked CR
    belongs to a CRLF pair (host_launcher CR=989 LF=989; run.py, config.py,
    jcc.yaml, JccCommon.ps1 all CR=0), so `tr -d` and `sed` are IDENTICAL here
    and any "I made it stricter" claim is UNFALSIFIABLE on this tree.

    The justification is therefore GENERAL (fold a pair, never delete a lone CR)
    plus a SYNTHETIC counter-example, not an observation about the repo. The
    selftest supplies that synthetic input deliberately -- an isolated CR is
    something this repository cannot provide, which is exactly why a fixture is
    required rather than optional.
    """
    return data.replace(b"\r\n", b"\n")


def index_skip_flags(repo: "Path | None" = None) -> "dict[str, str]":
    """path -> the flag that makes git skip its content, for flagged paths only."""
    flagged: dict[str, str] = {}
    for line in git("ls-files", "-v", repo=repo).splitlines():
        if not line:
            continue
        flag, name = line[0], line[2:].replace("\\", "/") if len(line) > 2 else ""
        if flag in SKIP_CONTENT_FLAGS:
            flagged[name] = flag
    return flagged


def audit(repo: Path = REPO) -> dict:
    index = load_index(repo)
    blobs = head_blobs(index, repo)
    flagged = index_skip_flags(repo)
    # Paths git reports as CHANGED. Named for what it holds, because the first
    # version called this `clean_paths` and then tested `path in clean_paths` for
    # HIDDEN -- so the check asked "is this file dirty?" when it meant "is this
    # file clean?", and the forgery went unreported.
    git_changed = {p for p in git("diff", "--name-only", repo=repo).splitlines() if p}

    zeroed: list[str] = []
    hidden: list[str] = []
    eol_only: list[str] = []
    differ: list[str] = []
    missing: list[str] = []

    for path, sha in sorted(index.items()):
        work = repo / path
        if not work.is_file():
            missing.append(path)
            continue
        raw = work.read_bytes()
        # "CONTAINS NUL", matching the shell form the team rule uses
        # (s=wc -c, z=`tr -d '\0' | wc -c`): any NUL byte at all makes
        # len(raw) != len(raw-without-NUL), so PARTIAL zeroing is caught here.
        #
        # The previous test was `not raw.strip(b"\x00")`, which only strips NULs
        # from the ENDS -- so a file zeroed in its first half reported
        # zeroed=none. The selftest caught the discrepancy: its PARTIAL case
        # asserted differ, and differ fired, so the case passed while the
        # ZEROED claim was wrong. This is the same "the control passed but not
        # for the reason it claimed" shape as the earlier fixture bugs.
        if raw and len(raw) != len(raw.replace(b"\x00", b"")):
            zeroed.append(path)
        head = blobs.get(path)          # keyed by PATH -- see head_blobs()
        if head is None:
            continue
        if raw != head:
            # RAW ALREADY DIFFERS. Reaching this branch means the byte comparison
            # has already answered the question; normalise() may now only LABEL
            # the difference, never decide it.
            #
            # This ordering is the whole safety property, and it is deliberate:
            # normalise() is NOT collision-free. Exhaustive over 1..3 bytes
            # {a, CR, LF} (39 payloads, 390 size-preserving distinct pairs):
            #   b"\r\n\x0a" and b"\x0a\r\n" both fold to b"\x0a\x0a", so
            #   normalise() calls a genuine CRLF/LF REORDERING equal.
            # That is exactly the size-preserving shape INC-002 is about, and it
            # means a normalise()-based equality test would report a real
            # corruption as "just line endings".
            #
            # Because the raw test gates this branch, a normalise()-equal pair can
            # only be LABELLED, never declared equal. is_pure_eol_choice() then
            # separates "same text, different ending choice" from "content
            # changed", because folding alone cannot tell those apart.
            if is_pure_eol_choice(raw, head):
                eol_only.append(path)
            else:
                differ.append(path)
        else:
            # BYTES IDENTICAL. If git nonetheless reports this path as changed,
            # that is a stat-cache artefact, not a content difference.
            if path in git_changed:
                hidden.append(path)

    return {
        "tracked": len(index),
        "zeroed": zeroed,
        "hidden": hidden,
        "eol_only": eol_only,
        "differ": differ,
        "missing": missing,
        "index_skip_flagged": flagged,
    }


def _fmt(result: dict) -> str:
    return (
        "tracked=%(tracked)d ZEROED=%(len_zeroed)d HIDDEN=%(len_hidden)d "
        "DIFFER=%(len_differ)d EOL_ONLY=%(len_eol)d MISSING=%(len_missing)d "
        "INDEX_FLAGGED=%(len_flag)d"
        % {
            "tracked": result["tracked"],
            "len_zeroed": len(result["zeroed"]),
            "len_hidden": len(result["hidden"]),
            "len_differ": len(result["differ"]),
            "len_eol": len(result["eol_only"]),
            "len_missing": len(result["missing"]),
            "len_flag": len(result["index_skip_flagged"]),
        })


def _make_fixture(base: Path) -> Path:
    """A fresh 3-file repo, LF-only, autocrlf off.

    ONE FIXTURE PER CASE. The stat-cache forgery restores a forged mtime, and
    `git checkout -- .` will not overwrite a file whose stat matches the index
    -- so a shared fixture silently carried the forgery (and its CRLF) into later
    cases, which then failed for reasons that had nothing to do with what they
    claimed to test. Fresh fixture per case removes the whole class of leak.
    """
    fx = base
    (fx / "pkg").mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", "-b", "main", str(fx)],
                   check=True, stdout=subprocess.DEVNULL)
    for key, val in (("user.email", "t@t"), ("user.name", "t"),
                     ("core.autocrlf", "false")):
        subprocess.run(["git", "-C", str(fx), "config", key, val],
                       check=True, stdout=subprocess.DEVNULL)
    for rel, body in (("pkg/alpha.py", b"A = 1\n"),
                      ("pkg/beta.py", b"B = 2\n"),
                      ("pkg/gamma.py", b"C = 3\n")):
        (fx / rel).write_bytes(body)
    # Age the files BEFORE `git add`, by an amount, not just to "now".
    #
    # prism-3dui measured the cause of the flaky stat-cache case: whether git
    # hides the forgery depends on how recently the fixture was written relative
    # to the index write. Isolated trials, 12 per mode:
    #
    #     no aging   hidden 11/12
    #     utime now  hidden  9/12     <- calling utime is not what helps
    #     age 5s     hidden 12/12     <- the magnitude is what matters
    #
    # So the age is applied here, before the index records the stat, and it is
    # 5 seconds rather than 0. This is fixture design, not a workaround: a
    # fixture we own should not depend on how fast the machine that reads it
    # happens to be running.
    #
    # MECHANISM UNVERIFIED. prism-3dui proposed that git re-reads content when
    # the file mtime falls in the same second as the index write, then refuted
    # it -- the recorded seconds already differ in the racy case. What is
    # verified is the order/load dependence and that aging removes the flip.
    # The cause is left unstated rather than guessed at.
    _age = 5
    _now = time.time()
    for rel in ("pkg/alpha.py", "pkg/beta.py", "pkg/gamma.py"):
        os.utime(fx / rel, (_now - _age, _now - _age))
    subprocess.run(["git", "-C", str(fx), "add", "-A"], check=True,
                   stdout=subprocess.DEVNULL)
    subprocess.run(["git", "-C", str(fx), "commit", "--quiet", "-m", "init"],
                   check=True, stdout=subprocess.DEVNULL)
    return fx


def selftest() -> int:
    """Prove each detector FIRES, and that a clean tree is not flagged.

    A corruption scanner that cannot be shown to catch corruption is a scanner
    that reports "clean" unconditionally. Every case below is a real forgery in a
    throwaway repo, not a mock -- and each gets its OWN fixture, because the
    stat-cache case deliberately forges an mtime that `git checkout` will not
    overwrite.
    """
    results = []

    def case(name: str, ok: bool, detail: str) -> None:
        results.append({"id": name, "ok": bool(ok), "detail": detail})

    with tempfile.TemporaryDirectory(prefix="jcc_inc002_") as td:
        root = Path(td)
        fx = _make_fixture(root / "clean")

        # --- POSITIVE CONTROL: an untouched tree must come back clean --------
        clean = audit(fx)
        case("POSITIVE_CONTROL_CLEAN_TREE_REPORTS_CLEAN",
             not (clean["zeroed"] or clean["hidden"] or clean["differ"]
                  or clean["missing"] or clean["index_skip_flagged"]),
             _fmt(clean))

        # --- (a) full zeroing, size restored: the classic INC-002 -----------
        fx = _make_fixture(root / "zero_full")
        target = fx / "pkg" / "alpha.py"
        raw = target.read_bytes()
        target.write_bytes(b"\x00" * len(raw))
        import os
        st = os.stat(str(target))
        os.utime(str(target), ns=(st.st_atime_ns, st.st_mtime_ns))
        z = audit(fx)
        case("FULL_ZEROING_IS_CAUGHT",
             "pkg/alpha.py" in z["zeroed"],
             "zeroed=%s  (INC-002 proper: same size, now contains NUL bytes)"
             % (z["zeroed"] or "none"))

        subprocess.run(["git", "-C", str(fx), "checkout", "--", "."],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # --- (a') PARTIAL zeroing: the shape a whole-file check misses -------
        fx = _make_fixture(root / "zero_partial")
        target = fx / "pkg" / "beta.py"
        raw = target.read_bytes()
        target.write_bytes(b"\x00" * (len(raw) // 2) + raw[len(raw) // 2:])
        st = os.stat(str(target))
        os.utime(str(target), ns=(st.st_atime_ns, st.st_mtime_ns))
        p = audit(fx)
        # BOTH detectors must fire, and the assertion says so explicitly. An
        # earlier version accepted `zeroed OR differ` and passed on `differ`
        # alone -- so the ZEROED claim was never actually tested, which is how
        # this tool shipped a NUL check that only caught whole-file zeroing.
        caught_zeroed = "pkg/beta.py" in p["zeroed"]
        caught_differ = "pkg/beta.py" in p["differ"]
        case("PARTIAL_ZEROING_IS_CAUGHT_BY_BOTH_DETECTORS",
             caught_zeroed and caught_differ,
             "zeroed=%s differ=%s  (a NUL check that only tested 'entirely NUL' "
             "would report zeroed=none here and still pass an 'or differ' "
             "assertion -- the NUL check flags ANY NUL, so it must fire too)"
             % (p["zeroed"] or "none",
                [d for d in p["differ"]] or "none"))

        subprocess.run(["git", "-C", str(fx), "checkout", "--", "."],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # --- HIDDEN: same size, same mtime, different bytes ----------------
        # This is the shape the index stat cache hides, and the one weaver
        # refused to certify the absence of.
        # --- HIDDEN: same size, same mtime, different bytes ----------------
        # The size MUST be preserved. An earlier version wrote "C = 999" over
        # "C = 3" -- one byte longer -- and git caught it on the stat cache's
        # SIZE field, so the case "failed" for the wrong reason and the forgery
        # was never actually hidden. Flipping a digit IN PLACE keeps the length
        # identical, which is the only shape that defeats both stat fields.
        fx = _make_fixture(root / "stat_forgery")
        target = fx / "pkg" / "gamma.py"
        before = target.stat()
        original = target.read_bytes()
        forged = original.replace(b"C = 3", b"C = 9")
        assert len(forged) == len(original), "forgery must preserve size"
        target.write_bytes(forged)
        os.utime(str(target), ns=(before.st_atime_ns, before.st_mtime_ns))
        # MEASURE GIT FIRST. This ordering is load-bearing and the case was flaky
        # (1-in-8) until it was fixed.
        #
        # `git diff` decides whether to read a file's CONTENT by consulting the
        # index stat cache (mtime + size). Restoring the mtime is supposed to make
        # it skip the content read, and that usually works. But the `audit(fx)`
        # call below OPENS files in this same tree, and the resulting access
        # pattern was enough -- intermittently, and only after several earlier
        # audit() calls in the same process -- to make git content-compare
        # anyway. Then git_clean flipped to False and this case failed even though
        # the detector was working perfectly (`differ=['pkg/gamma.py']`).
        #
        # prism-3dui bisected it: the case alone never fails; the same cases run
        # in order fail deterministically. He marked the underlying filesystem
        # mechanism UNVERIFIED and so do I -- I only know the ORDER is what
        # matters, not why. Claiming a cause here would be exactly the mistake
        # this whole file keeps catching.
        #
        # So: assert on git's answer BEFORE touching the tree, and never let the
        # measurement be influenced by the thing being measured.
        git_clean = "pkg/gamma.py" not in subprocess.run(
            ["git", "-C", str(fx), "diff", "--name-only"],
            stdout=subprocess.PIPE, text=True).stdout.split()
        h = audit(fx)

        # WHAT IS ASSERTED, and why it is not the whole story.
        #
        # prism-3dui measured this case failing 1-in-5 and correctly refused to
        # sign the suite while it did that. The cause is NOT an ordering effect
        # from the audit's own file reads (my first fix assumed that, and it did
        # NOT help -- reordering alone still failed 1-in-15). The measured cause is
        # a RACE:
        #
        #   git skips the content read only when the cached stat matches exactly.
        #   When the file mtime falls in the same SECOND as the index write, git
        #   treats the entry as "racily clean" and re-reads content anyway. So
        #   whether git is fooled depends on how long the process took to get
        #   there -- and under load, later `git diff` spawns cross a second
        #   boundary and git notices.
        #
        # So `git_clean` is asserted again. An earlier version of this case
        # demoted it to INFORMATION on the grounds that git's timing is the
        # host's business, not ours. prism-3dui pointed out the flaw in that
        # reasoning and the measurement that settles it: the raciness is not
        # inherent, it is a property of WHEN THE FIXTURE WAS WRITTEN relative to
        # the index write -- and the fixture is ours. `_make_fixture` now ages
        # the files 5s before `git add`, which makes the forgery hidden 20/20
        # instead of 13/20. The raciness is removed at its source rather than
        # asserted around.
        #
        # That matters for what the case PROVES. The body must check the claim in
        # the case's own name -- "despite git saying clean" -- or the case no
        # longer tests what it says it tests, which is the exact error class this
        # whole file keeps catching. A weakened assertion is not a safer
        # assertion; it is a quieter one.
        case("STAT_CACHE_FORGERY_IS_CAUGHT_DESPITE_GIT_SAYING_CLEAN",
             ("pkg/gamma.py" in h["differ"]) and git_clean
             and target.stat().st_size == before.st_size,
             "git_reports_clean=%s while we report differ=%s at identical size %d "
             "-- deterministic because _make_fixture ages the files before the "
             "index records their stat (hidden 20/20 vs 13/20 unaged)"
             % (git_clean, h["differ"] or "none", before.st_size))

        subprocess.run(["git", "-C", str(fx), "checkout", "--", "."],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # --- INDEX FLAG: assume-unchanged makes git stop comparing ----------
        # `git checkout -- .` does NOT clear this flag, so it is cleared
        # explicitly first; otherwise the flag leaks into every later case and
        # the assertions after this one are testing a dirty fixture.
        fx = _make_fixture(root / "index_flag")
        subprocess.run(["git", "-C", str(fx), "update-index",
                        "--no-assume-unchanged", "pkg/alpha.py"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "-C", str(fx), "update-index", "--assume-unchanged",
                        "pkg/alpha.py"], check=True, stdout=subprocess.DEVNULL)
        f = audit(fx)
        # `ls-files -v` prints a LOWERCASE letter for assume-unchanged, and the
        # separator is a single space -- the tag is field 0, the path field 2.
        flags = f["index_skip_flagged"]
        case("ASSUME_UNCHANGED_FLAG_IS_SURFACED",
             "pkg/alpha.py" in flags and flags["pkg/alpha.py"] == "h",
             "index_skip_flagged=%s  (lowercase 'h' = assume-unchanged: git will "
             "not content-compare this path at all)"
             % (flags or "none"))
        subprocess.run(["git", "-C", str(fx), "update-index", "--no-assume-unchanged",
                        "pkg/alpha.py"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)

        # --- EOL: must be classified as NOT a real difference ---------------
        # Restore from HEAD, then write LF -> CRLF EXACTLY once. Two traps here,
        # both hit while writing this case:
        #   * `git checkout -- .` does not undo a file whose mtime was forged
        #     forward, so the restore is asserted rather than assumed;
        #   * a blind `.replace(b"\n", b"\r\n")` on already-CRLF bytes leaves
        #     the lone \n of each \r\n and yields \r\r\n -- a REAL byte
        #     difference, so the case would fail while testing nothing about EOL.
        fx = _make_fixture(root / "eol")
        eo = fx / "pkg" / "alpha.py"
        original = eo.read_bytes()
        if b"\r\n" in original:
            # The restore did not take (stale stat cache). Force the content.
            original = subprocess.run(
                ["git", "-C", str(fx), "show", "HEAD:pkg/alpha.py"],
                stdout=subprocess.PIPE, check=True).stdout
            eo.write_bytes(original)
        assert b"\r\n" not in original, "fixture must start LF-only"
        eo.write_bytes(original.replace(b"\n", b"\r\n"))
        e = audit(fx)
        case("CRLF_ONLY_DIFFERENCE_IS_NOT_COUNTED_AS_A_DIFFERENCE",
             "pkg/alpha.py" not in e["differ"] and "pkg/alpha.py" in e["eol_only"],
             "eol_only=%s differ=%s  (a bare-hash rule would report this as "
             "corruption; this one classifies it as line-ending smudging)"
             % (e["eol_only"] or "none", e["differ"] or "none"))

        fx = _make_fixture(root / "inband_cr")
        ic = fx / "pkg" / "alpha.py"
        # A TRUE in-band CR: one that is NOT part of any CRLF pair, i.e. no LF
        # follows it. My first attempt appended the CR immediately before the
        # LF -- which produced "... 31 0d 0a" = a CRLF PAIR, so normalise()
        # correctly folded it away and the case landed in eol_only. The case was
        # wrong, not the detector, and it is worth stating that: folding CRLF is
        # the detector's JOB, so a CR that merely precedes an LF is smudging.
        # prism's sample puts the lone CR mid-content: "... 61 0d 62 0d 0a".
        ic.write_bytes(b"A = 1\rX\n")   # lone CR mid-content, no LF after it
        r = audit(fx)
        case("IN_BAND_CR_INJECTION_IS_NOT_NORMALISED_AWAY",
             "pkg/alpha.py" in r["differ"] and "pkg/alpha.py" not in r["eol_only"],
             "differ=%s eol_only=%s  (bytes carry a lone CR with no LF after it; "
             "a `tr -d '\\r'`-style strip folds it to the clean bytes and reports "
             "MATCH -- normalise() replaces the CRLF PAIR, never a lone CR)"
             % (r["differ"] or "none", r["eol_only"] or "none"))

        # --- REVERSE CONTROL (weaver): a healthy file may legitimately CONTAIN
        # a NUL literal, and the rule must not be tuned to forgive that silently
        # -- it must be an ASSERTED decision, not an accident.
        #
        # This case is here because of a real false positive: aegis's committed
        # `AEGIS_INC002_ADJUDICATION.txt` explains the NUL rule in prose and
        # therefore contained one literal NUL byte. A scan of the real tree then
        # reported ZEROED=1 and called a healthy file damaged -- the "correct
        # warning mistaken for a defect" pattern, in reverse.
        #
        # The two things this pins down, which the other cases do not:
        #   1. the NUL check is a FLAG, never a verdict -- it does not claim the
        #      content is destroyed, only that it holds a NUL;
        #   2. a single NUL in an otherwise intact file is still REPORTED, so
        #      the flag cannot be tuned away by special-casing text files --
        #      "what counts as prose" is not a rule worth encoding.
        #
        # aegis fixed it by writing the escape (`\\x00`) instead of the byte,
        # which keeps the rule exception-free. This case documents that the fix
        # is the CONTENT's job, not the checker's.
        fx = _make_fixture(root / "nul_literal")
        nl = fx / "pkg" / "alpha.py"
        nl.write_bytes(b"# explains tr -d '\\x00'\nvalue = 1\n"
                       .replace(b"\\x00", bytes([0])))   # one real NUL
        # COMMIT it, so the file is byte-identical to its own commit. That is the
        # whole premise: a NUL literal in a file git considers clean must be a
        # FLAG, not a verdict. Without the commit this path also shows up in
        # `differ`, and the case would pass for the wrong reason -- the ordinary
        # "it differs from HEAD" reading, which says nothing about NULs.
        subprocess.run(["git", "-C", str(fx), "add", "-A"], check=True,
                       stdout=subprocess.DEVNULL)
        subprocess.run(["git", "-C", str(fx), "commit", "--quiet", "-m",
                        "prose containing one NUL literal"], check=True,
                       stdout=subprocess.DEVNULL)
        r = audit(fx)
        case("A_HEALTHY_FILE_CONTAINING_A_NUL_LITERAL_IS_FLAGGED_NOT_JUDGED",
             "pkg/alpha.py" in r["zeroed"]
             and "pkg/alpha.py" not in r["differ"]
             and "pkg/alpha.py" not in r["eol_only"],
             "zeroed=%s differ=%s eol_only=%s  -- the file is byte-identical to "
             "its commit, so a NUL literal alone is a FLAG TO REVIEW, not "
             "evidence of damage; aegis fixed the real case by escaping the byte"
             % (r["zeroed"] or "none", r["differ"] or "none",
                r["eol_only"] or "none"))

        # --- DOUBLED CR at EOL: the case `sed 's/\r$//'` misses -------------
        # prism-3dui recommended `sed 's/\r$//'` as the fix for `tr -d '\r'`, then
        # retracted it: it strips only ONE trailing CR per line, so "ab\r\r\n"
        # normalises to exactly the clean file's bytes. I verified that MASK
        # myself before accepting it. `replace(b"\r\n", b"\n")` folds only PAIRS,
        # so the leftover CR survives and the difference is reported.
        #
        # Tested as a UNIT on `normalise()` rather than through a repo fixture.
        # My first version committed the doubled-CR file and expected the audit
        # to flag it -- but git preserves those bytes verbatim, so worktree == HEAD
        # raw and there is nothing to detect. The audit answers "does this file
        # match its commit", which is a different question from "does this
        # transform discard a byte class". Asserting the transform's property
        # directly is what actually pins the behaviour.
        cr, lf = b"\r", b"\n"
        clean_ab = b"ab" + lf
        doubled = b"ab" + cr + cr + lf
        lone = b"a" + cr + b"b" + lf
        pair = b"a" + cr + b"b" + cr + lf
        ok = (normalise(doubled) != normalise(clean_ab)     # the missed case
              and normalise(lone) != normalise(clean_ab)
              and normalise(pair) != normalise(clean_ab)
              and normalise(clean_ab) == clean_ab)         # clean passes through
        case("NORMALISE_FOLDS_PAIRS_ONLY_NOT_ONE_CR_PER_LINE", ok,
             "ab\\r\\r\\n -> %s  ab\\r\\n -> %s  clean -> %s  "
             "(a one-CR-per-line strip folds the first to clean and MISSES it)"
             % (normalise(doubled).hex(" "), normalise(clean_ab).hex(" "),
                normalise(clean_ab).hex(" ")))

        # --- THE SHAPE NORMALISE ITSELF CANNOT SEE (found by exhaustive search
        # over 1..3 bytes {a, CR, LF}, 390 size-preserving distinct pairs).
        #   b"\r\n\x0a" and b"\x0a\r\n" both normalise to b"\x0a\x0a", so
        #   normalise() reports them EQUAL.
        #
        # WHAT IS ACTUALLY TRUE HERE, and it is weaker than my first draft of this
        # case claimed. These are mixed-ending files carrying the SAME logical
        # lines: CRLF-then-LF vs LF-then-CRLF. Neither corrupts the other. So
        # `eol_only` is a DEFENSIBLE label, not a bug -- my first version of this
        # case asserted `differ` and was wrong, which is why it failed.
        #
        # The real guarantee worth pinning is the one that survives the nuance:
        # normalise() must never make a difference INVISIBLE. `eol_only` files are
        # still reported to the caller and still counted, so nothing is swept
        # under the rug. If some future refactor let a normalise()-equal file fall
        # through unreported, THAT would be a genuine fail-open -- and this
        # assertion is what would catch it.
        fx = _make_fixture(root / "reorder")
        ro = fx / "pkg" / "alpha.py"
        ro.write_bytes(b"A = 1\r\n\n")          # CRLF then LF
        subprocess.run(["git", "-C", str(fx), "add", "-A"], check=True,
                       stdout=subprocess.DEVNULL)
        subprocess.run(["git", "-C", str(fx), "commit", "--quiet", "-m",
                        "crlf lf"], check=True, stdout=subprocess.DEVNULL)
        ro.write_bytes(b"A = 1\n\r\n")          # LF then CRLF -- mixed endings
        r = audit(fx)
        still_reported = ("pkg/alpha.py" in r["differ"]
                          or "pkg/alpha.py" in r["eol_only"])
        case("NORMALISE_EQUAL_FILES_ARE_STILL_REPORTED_NEVER_SWEEPT",
             still_reported,
             "differ=%s eol_only=%s  -- these have identical logical lines, so "
             "eol_only is the correct label; the pair that DISPROVES eol_only is "
             "row 3 of EOL_CLASSIFICATION_IS_CORRECT_ON_ALL_FIVE_SHAPES below. "
             "The invariant pinned here: normalise() may classify a "
             "known-difference, it may never cause one to go unreported."
             % (r["differ"] or "none", r["eol_only"] or "none"))

        # --- THE CLASSIFICATION TABLE (five shapes, measured). prism-3dui showed
        # my lone-CR discriminator was wrong in the OPPOSITE direction from the
        # version before it: a lone CR can be legitimate CONTENT, and then it
        # refused pairs that really are pure smudges. He also RETRACTED the
        # counter-example that motivated the lone-CR rule in the first place --
        # the trailing CR in `a\r` is that line's own terminator, so the logical
        # lines were identical and `eol_only` had been correct all along.
        #
        # Both wrong versions were mine, in opposite directions. This table is
        # what pins the correct one; the two rows that broke the lone-CR version
        # are the first two.
        cr_b, lf_b = b"\r", b"\n"

        def _smudge(text: bytes) -> bytes:
            return text.replace(b"\n", cr_b + lf_b)

        table = [
            # (work, head, expected_is_eol_only, label)
            (b"a" + cr_b + lf_b + cr_b + b"b" + lf_b,
             b"a" + lf_b + cr_b + b"b" + cr_b + lf_b, True,
             "CR placed differently, identical logical lines"),
            (_smudge(b"a" + cr_b + b"b\n"), b"a" + cr_b + b"b\n", True,
             "pure smudge of content that itself holds a lone CR"),
            (b"a" + cr_b + b"b" + cr_b + lf_b,
             b"a" + lf_b + b"b" + cr_b + lf_b, False,
             "line boundary moved, content differs"),
            (b"aXb" + cr_b + lf_b, b"aYb" + lf_b, False, "text changed"),
            (_smudge(b"a\nb\n"), b"a\nb\n", True, "plain CRLF smudge"),
        ]
        wrong = [lbl for work, head, exp, lbl in table
                 if is_pure_eol_choice(work, head) != exp]
        case("EOL_CLASSIFICATION_IS_CORRECT_ON_ALL_FIVE_SHAPES",
             not wrong,
             "%d/%d correct%s  -- the lone-CR rule failed the first two rows: a CR "
             "that is part of the content says nothing about whether the endings "
             "changed"
             % (len(table) - len(wrong), len(table),
                "" if not wrong else "; wrong: %s" % wrong))

    passed = sum(1 for r in results if r["ok"])
    for r in results:
        print("  [%s] %s\n        %s"
              % ("ok  " if r["ok"] else "HOLE", r["id"], r["detail"]))
    print("\nINC-002 INDEX-LEVEL CONTROL %s %d/%d"
          % ("PASS" if passed == len(results) else "FAIL", passed, len(results)))
    return 0 if passed == len(results) else 1


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    result = audit()
    print("INC-002 INDEX-LEVEL AUDIT  " + _fmt(result))
    if result["zeroed"]:
        print("\nNUL BYTES PRESENT (a FLAG, not a verdict -- verify before acting):")
        for p in result["zeroed"]:
            print("  " + p)
    if result["hidden"]:
        print("\nHIDDEN (git says clean, bytes differ):")
        for p in result["hidden"]:
            print("  " + p)
    if result["differ"]:
        print("\nDIFFER (byte differences git also reports):")
        for p in result["differ"]:
            print("  " + p)
    if result["eol_only"]:
        print("\nEOL_ONLY (line-ending only, NOT corruption): %d path(s)"
              % len(result["eol_only"]))
    if result["missing"]:
        print("\nMISSING (tracked but absent from the worktree):")
        for p in result["missing"]:
            print("  " + p)
    if result["index_skip_flagged"]:
        print("\nINDEX_FLAGGED (git will not content-compare these):")
        for p, flag in sorted(result["index_skip_flagged"].items()):
            print("  %s  %s" % (flag, p))

    # Exit non-zero for a FLAG, not for a proven verdict. Never for EOL
    # smudging: a rule that cries wolf on every CRLF file gets ignored, which is
    # worse than no rule.
    #
    # NUL presence is a flag, and it is deliberately in this list -- a human must
    # look. What it must NOT do is assert the content is destroyed: a prose file
    # explaining the NUL rule legitimately contains one NUL byte, and calling that
    # "damage" is what turns a scanner into a thing that prompts a rescue of a
    # healthy file. The distinction is enforced by the selftest case
    # A_HEALTHY_FILE_CONTAINING_A_NUL_LITERAL_IS_FLAGGED_NOT_JUDGED.
    bad = (result["zeroed"] or result["hidden"] or result["index_skip_flagged"]
           or result["missing"])
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
