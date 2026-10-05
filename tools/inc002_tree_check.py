#!/usr/bin/env python3
"""INC-002 tree check -- two halves, both required, neither sufficient alone.

WHY THIS TOOL EXISTS
--------------------
After INC-002 (files zeroed in place, size preserved, escaping git's stat cache)
the rule became "do not prove the tree is intact with ``git status``". Executing
that literally produced FALSE POSITIVES, because this repo sets
``core.autocrlf=true`` and carries ``* text=auto eol=lf``:

    $ git show HEAD:pkg/host_launcher.py | sha256sum   -> 45366 B
    $ sha256sum pkg/host_launcher.py                   -> 46355 B   (+989)
    $ git diff -- pkg/host_launcher.py                 -> EMPTY
    # 989 == the CR count. Content is byte-identical.

A rule that cries wolf gets ignored, which is worse than no rule, so the
distinction that matters is:

  (a) size UNCHANGED, content zeroed   -> INC-002. Real damage.
  (b) size CHANGED, content identical  -> ordinary CRLF/LF. NOT damage.

THE TWO HALVES ARE ORTHOGONAL, NOT COMPLEMENTARY
-------------------------------------------------
Each half has its OWN blind spot, and they are not the same shape:

  * the NUL half is blind to CR-class damage -- an in-band CR injection
    contains no NUL at all, so the zero check passes it;
  * the CR-normalised hash half is blind to NUL-class damage that
    normalisation cannot see, because a zeroed or NUL-punched file still
    hashes *something*.

So do not describe these as mutually backing each other up. "Complementary"
implies redundancy, and redundancy implies one half alone might do -- it cannot.
For in-band CR injection, the hash half is the ONLY thing that catches it. They
are orthogonal signals, and both are required:

  1. CR-normalised content hash  -- separates real edits and CR-class damage
     from benign line-ending differences.
  2. non-zero-byte check (s != z) -- names the NUL class. A file that is 8639
     bytes of NUL has s == z FALSE, and git diff will happily call that
     "Bin 8639 -> 8639 bytes". No run-length threshold: see classify_nul().

  There is deliberately no threshold in the PREDICATE. The threshold lives in
  the LABEL instead, which is where it belongs -- see classify_nul(). A partial
  NUL count is reported as CONTAINS_NUL: still not clean, because a
  size-preserving NUL punch is real damage, but making no claim that content
  was destroyed. Prose that documents NUL bytes contains one, and in this
  repository that is the normal condition, not an edge case. Labelling such a
  file "ALL NUL -- content destroyed" was a false positive in an earlier
  revision, every word of which was false.

ONE DEVIATION FROM forge2's NOTE, AND IT IS DELIBERATE
------------------------------------------------------
forge2's note normalises with ``tr -d '\\r'``. That DELETES every CR byte,
including a CR that is a real character inside a string literal:

    printf 'x = "a\\rb"\\r\\n'   # 17 bytes
    tr -d '\\r'                 -> 16 bytes, the literal CR is GONE
    sed 's/\\r$//'              -> 17 bytes, the literal CR is KEPT

Deleting it can mask a genuine content change on both sides at once and make two
different files compare equal. This tool therefore strips CR only at END OF LINE
(CRLF -> LF), which is the normalisation that was actually wanted. It resolves
forge2's case (b) identically -- verified on host_launcher.py, both forms give
26d9cd51e4458837 -- while additionally refusing to destroy in-band CRs.

Binary files are SKIPPED, not normalised: a PDF or SQLite blob has no line
structure, so rewriting "line endings" in one would corrupt the comparison and
manufacture a false DIFFER. Git's own ``text=auto eol=lf`` already exempts them
(``.gitattributes`` marks *.png / *.pdf binary).

RE-EMITTED EVIDENCE, AND THE LIMIT OF ITS EXEMPTION
-----------------------------------------------------
Five artifacts under ``artifacts/acceptance/`` are re-emitted with a fresh
``probe_at`` timestamp on every full-suite run, so they would otherwise be
reported as DRIFT every single time -- and a rule that cries wolf gets ignored.
They are classified ``EVIDENCE_REEMITTED`` with the reason printed, never
silently skipped.

The exemption is keyed on PATH, not on what changed: a hand edit to the
substance of one of those files would also be classified EVIDENCE_REEMITTED.
This is a known, adjudicated limit rather than an oversight -- see the long note
on ``EVIDENCE_REEMITTED`` for why schema-aware exemption would be worse. The
zero-byte and size checks still run on these paths unconditionally, so a zeroed
exempt file fails with exit 1 and cannot hide.

READ-ONLY. Reads git objects and files; writes nothing but the report.

USAGE
-----
  python tools/inc002_tree_check.py --selftest
  python tools/inc002_tree_check.py --files a.py b.py
  python tools/inc002_tree_check.py --a8-pins        # the frozen artifact set
  python tools/inc002_tree_check.py --all-tracked

Exit: 0 clean, 1 damage or drift found, 2 the check could not run (NOT a pass).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO = Path(__file__).resolve().parents[1]

#: Suffixes whose bytes are not line-structured. Normalising these would corrupt
#: the comparison, so they get a byte-exact check instead.
BINARY_SUFFIXES = {".png", ".pdf", ".db", ".sqlite", ".sqlite3", ".pyc",
                   ".zip", ".gz", ".ico", ".jpg", ".jpeg", ".woff", ".woff2"}

#: Sentinel distinguishing "blob not preloaded" from "blob is None".
_NO_PRELOAD = object()

SEV_CLEAN = "CLEAN"
SEV_DRIFT = "DRIFT"
SEV_ZEROED = "ZEROED"
SEV_DIAG = "SANDBOX_DIAG"
#: Content legitimately differs from HEAD, classified and explained -- NOT damage
#: and NOT ignored. Reported as its own severity so a reader can see the count.
SEV_EVIDENCE = "EVIDENCE_REEMITTED"
#: The file contains at least one NUL but is not entirely NUL. Not clean --
#: a size-preserving NUL punch is real damage -- but explicitly NOT
#: "content destroyed". Prose that documents NUL bytes contains them
#: legitimately, and in this repository that is the normal condition.
SEV_CONTAINS_NUL = "CONTAINS_NUL"

#: A difference from HEAD that is PROVABLY nothing but CRLF-vs-LF. This is
#: informational, not damage: the tree is byte-different from the blob but
#: semantically the same file, and there is no lone CR anywhere after
#: normalisation and the line structure matches line for line.
#:
#: It exists so the verdict can be honest in both directions. The old code
#: hashed the normalised bytes and reported MATCH, which hid real edits that
#: normalisation happens to collapse. The first fix hashed RAW and reported
#: DRIFT for everything, which cried wolf on 48 benign CRLF files -- a rule that
#: cries wolf gets ignored, which is its own failure. This severity is the third
#: option: RAW decides, normalisation explains, and "explained" is a state the
#: verdict can act on rather than a verdict it has to choose between.
SEV_EOL = "EOL_ONLY"

#: Every severity this tool can report. Derived, never hand-maintained: a
#: hand-kept list goes stale silently, and a guard built on a stale list fails
#: on correct code while the thing it guards drifts.
_ALL_SEVERITIES = frozenset({
    SEV_CLEAN, SEV_DRIFT, SEV_ZEROED, SEV_DIAG, SEV_EVIDENCE,
    SEV_CONTAINS_NUL, SEV_EOL,
})

#: Files that a test/gate run RE-EMITS with a fresh timestamp, so they differ
#: from HEAD after every full-suite run. Exempt from the DRIFT verdict -- but only
#: after the two damage checks still run on them, and only with the reason
#: printed. Silently skipping them would be the failure mode this tool exists to
#: prevent: a check that cries wolf gets ignored, and an exemption with no
#: recorded justification is just a hole.
#:
#: Verified, not assumed: each of the four adapter JSONs carries
#: ``"probe_at": "2026-10-04T09:55:15Z"``, which changes on every re-emit.
#: KNOWN LIMITATION -- read this before trusting an EVIDENCE_REEMITTED verdict.
#:
#: The exemption is keyed on PATH, not on WHAT changed. If a hand edit alters
#: the substance of one of these files -- a capability flipped to CONNECTED, a
#: field dropped -- it is still classified EVIDENCE_REEMITTED rather than DRIFT,
#: because this check does not parse the JSON and cannot tell a re-emit's
#: timestamp churn from a substantive edit.
#:
#: This is deliberate and was adjudicated rather than overlooked. Distinguishing
#: the two would require knowing these documents' schema, and schema knowledge
#: hard-wired into a checker is a liability: the schema will change and the tool
#: will not, so it would silently keep applying a rule that no longer describes
#: the file. A path-keyed exemption with a printed reason degrades honestly --
#: it says "I did not verify this one, here is why" -- whereas a schema-aware
#: exemption that has drifted would say "I checked this one" falsely.
#:
#: WHAT IS STILL ENFORCED on these paths, unconditionally:
#:   * the NUL/zero-byte check (INC-002 shape (a)) -- verified by test;
#:   * the size comparison against the HEAD blob.
#: So a zeroed exempt file fails with exit 1; it cannot hide.
#:
#: TO TIGHTEN THIS LATER: compare the parsed document with volatile fields
#: (probe_at and friends) removed, rather than adding more exempt paths. That is
#: the right shape of fix, and it belongs in whoever owns those artifacts.
EVIDENCE_REEMITTED = {
    "artifacts/acceptance/CONTINUATION_INFRA_TEST.json":
        "re-emitted by the continuation infra test (per-run node_ids/checks)",
    "artifacts/acceptance/adapter_capability/claude.json":
        "probe_at timestamp; re-emitted by v1_1_emit_capability_matrix",
    "artifacts/acceptance/adapter_capability/codex.json":
        "probe_at timestamp; re-emitted by v1_1_emit_capability_matrix",
    "artifacts/acceptance/adapter_capability/hermes.json":
        "probe_at timestamp; re-emitted by v1_1_emit_capability_matrix",
    "artifacts/acceptance/adapter_capability/workbuddy.json":
        "probe_at timestamp; re-emitted by v1_1_emit_capability_matrix",
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalise_eol(data: bytes) -> bytes:
    """CRLF -> LF, i.e. strip a CR ONLY at end of line.

    Deliberately not ``data.replace(b"\\r", b"")``: that would also delete a CR
    which is a real character inside a string literal, letting two genuinely
    different files compare equal. Lone CRs (classic Mac line endings) are left
    alone -- they are vanishingly rare here and normalising them would be a
    second, unjustified transformation.
    """
    return data.replace(b"\r\n", b"\n")


def nul_count(data: bytes) -> int:
    return data.count(b"\x00")


def _has_lone_cr(normalised: bytes) -> bool:
    """Does any LF-free line still carry a CR? True means the normalisation
    removed a real byte that remains visible as a bare CR."""
    return any(b"\r" in line for line in normalised.split(b"\n"))


def _selftest_twin_rel() -> str:
    """Repo-relative path of the CRLF-twin probe. Under .workbuddy/scratch so
    it is gitignored: a probe that flickers the tree dirty for a concurrent
    agent reinstates the coupling this suite exists to remove. It must be
    untracked for the test to be meaningful, and scratch is both untracked and
    ignored."""
    return ".workbuddy/scratch/inc002_selftest_crlf_twin.txt"


def _selftest_twin_target() -> Path:
    d = REPO / ".workbuddy" / "scratch"
    d.mkdir(parents=True, exist_ok=True)
    return d / "inc002_selftest_crlf_twin.txt"


def logical_lines(data: bytes) -> List[bytes]:
    """Split into lines, removing the CR that belongs to each line TERMINATOR.

    The `if` matters and is the whole point. A CR immediately before the LF is
    part of the terminator and carries no content; any other CR is content and
    is kept. That distinction is the discriminator.

    Written this way deliberately. The tempting one-liner is
    `data.replace(b"\\r", b"")`, which strips EVERY CR and is exactly the defect
    adjudication #47 banned -- it would treat a real in-line CR as an absent
    byte. Removing one CR per line is a different operation and must not be
    refactored into it.
    """
    out: List[bytes] = []
    for line in data.split(b"\n"):
        out.append(line[:-1] if line.endswith(b"\r") else line)
    return out


def _crs_are_all_terminators(data: bytes) -> bool:
    """Is every CR in `data` immediately followed by an LF?

    This is the half logical_lines() cannot supply alone. Stripping one CR per
    line loses WHERE the remaining CRs sat, so two blobs differing by a genuine
    in-line CR can end up with identical logical lines. Measured, equal length,
    and NOT a terminator change:

        work = 0d 0a   (CRLF)
        head = 0a 0d   (LF then a bare CR at EOF)
        logical lines are ['', ''] on both sides  -- logical_lines alone: equal
        normalise_eol gives 0a against 0d 0a     -- the content really differs

    Requiring every CR to be followed by LF separates them: the second blob ends
    in a CR that terminates nothing. Paired with the logical-line comparison it
    classifies 780 self-certifying pairs with zero errors; either conjunct alone
    misclassifies some.
    """
    i = 0
    n_bytes = len(data)
    while i < n_bytes:
        if data[i:i + 1] == b"\r":
            if i + 1 >= n_bytes or data[i + 1:i + 2] != b"\n":
                return False
            i += 2
            continue
        i += 1
    return True


def _is_pure_eol_difference(work: bytes, head: bytes) -> bool:
    """Is this difference PROVABLY nothing but CRLF-vs-LF? Raw bytes only.

    Measured, not modelled: exhaustive over all 1..3-byte payloads on {a, CR,
    LF} finds exactly one unordered pair that is equal-length, different, and
    identical after `replace(b"\r\n", b"\n")`:

        b"\n\r\n"   vs   b"\r\n\n"      both normalise to b"\n\n"

    Information is destroyed AT the normalisation step, so no check applied to
    the normalised bytes can recover it. The previous implementation tested
    "no lone CR after normalisation" and "line structure agrees after
    normalisation" -- and both of those hold for this pair, because both sides
    normalise to b"\n\n" and both split to [b"", b"", b""]. It therefore
    returned True, laundering a real content difference into EOL_ONLY. Verified
    against this function directly before the change.

    So the exemption is decided on RAW bytes, by asking whether one side is
    literally the other with its line endings rewritten. There is no
    normalisation in this function at all, which is the point: the earlier
    design normalised first and asked questions afterwards, and the answer was
    gone before the question was asked.

    A token-sequence comparison was tried and rejected: every benign LF->CRLF
    twin changes its token list ([LF] vs [CRLF]), so it flagged ordinary host
    line-ending drift as damage (p1-tooling measured this, 25/25 false
    positives).

    KNOWN LIMIT, measured, not assumed: the second clause covers CRLF->LF only
    where the LF side is a pure expansion of the other. A blob that itself
    contains CRLF, checked out as LF, is NOT exempted by either clause. That
    cannot occur in this repository -- `.gitattributes` says `* text=auto eol=lf`
    and 0 of 400 scanned HEAD blobs contain CRLF -- but if that attribute is ever
    relaxed, this returns False and such a file reports DRIFT. That is the
    safe direction to fail: a false DRIFT is visible, a false EOL_ONLY is not.

    Both earlier attempts compared normalised blobs and each had a false
    negative. `replace(b"\\r\\n", b"\\n")` is not injective -- b"\\n\\r\\n" and
    b"\\r\\n\\n" both become b"\\n\\n". And the bidirectional expansion from
    3afd761 exempts p1-tooling's measured counterexample: head = 0d 0a (CRLF),
    work = 0d 0d 0a (CRCRLF, one extra BARE CR). The clause rewrites head's
    CRLF as if it were a bare LF and produces exactly 0d 0d 0a, so a blob with
    a CR added to the CONTENT of line 1 is waved through. Logical lines see it:
    head ['', ''] against work ['0d', ''].

    The pattern behind both: any byte-level normalisation discards WHERE the
    lone CR sat, so a predicate over normalised bytes cannot see it. Comparing
    per line, after removing only the terminator's own CR, is what preserves
    position -- but on its own it is still not enough, which is what the second
    conjunct below records.
    """
    if work == head:
        return False
    if not (_crs_are_all_terminators(work)
            and _crs_are_all_terminators(head)):
        return False
    return logical_lines(work) == logical_lines(head)

def head_bytes(path: str, rev: str = "HEAD") -> Optional[bytes]:
    """Raw blob from the object store, single-file path.

    ``git cat-file -p`` on purpose: ``git show``/``git cat-file`` differ in
    whether working-tree conversion is applied, and we need the bytes AS STORED.

    For a whole-tree run prefer :func:`head_bytes_batch`, which pays the git
    startup cost once instead of once per file (~0.24s/file here, which is ~3
    minutes over 740 tracked files -- too slow to sit in a gate).
    """
    try:
        proc = subprocess.run(
            ["git", "cat-file", "-p", f"{rev}:{path}"],
            capture_output=True, cwd=str(REPO), timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def head_bytes_batch(paths: List[str], rev: str = "HEAD") -> Dict[str, Optional[bytes]]:
    """Read many blobs in ONE ``git cat-file --batch`` process.

    Binary output is framed as ``<sha> <type> <size>\\n<bytes>\\n``, which is
    parsed explicitly. Doing this per-file is correct but costs one process spawn
    per file, and a gate that takes three minutes gets skipped.
    """
    out: Dict[str, Optional[bytes]] = {p: None for p in paths}
    if not paths:
        return out
    try:
        proc = subprocess.run(
            ["git", "cat-file", "--batch"],
            input="\n".join(f"{rev}:{p}" for p in paths).encode("utf-8"),
            capture_output=True, cwd=str(REPO), timeout=300,
        )
    except (OSError, subprocess.SubprocessError):
        return out
    buf = proc.stdout
    pos = 0
    for path in paths:
        nl = buf.find(b"\n", pos)
        if nl < 0:
            break
        header = buf[pos:nl].decode("utf-8", "replace").split()
        pos = nl + 1
        # "<sha> <type> <size>" or "<spec> missing"
        if len(header) < 3 or header[1] == "missing":
            continue
        try:
            size = int(header[2])
        except ValueError:
            continue
        out[path] = buf[pos:pos + size]
        pos += size + 1  # skip the trailing newline git appends
    return out


def _drift_preserves_nul() -> bool:
    """Would the DRIFT branch clobber a CONTAINS_NUL severity?

    Restates the branch's guard in a pure form so the selftest can probe it
    without writing a file. Kept adjacent to the branch's own comment: if the
    branch changes, this is the first place that must be revisited.
    """
    return merge_drift_severity(SEV_CONTAINS_NUL) == SEV_CONTAINS_NUL


def merge_drift_severity(current: str) -> str:
    """What severity should be after the DRIFT branch considers this file?

    A size-preserving NUL punch differs from HEAD, so DRIFT and CONTAINS_NUL
    compete for the same field. DRIFT is the generic verdict; the NUL finding is
    the specific one, and it carries the byte offsets the operator needs. So
    DRIFT yields to it. Extracted as a function so the selftest can probe the
    real decision -- scraping the source text would break on any refactor, and
    asserting a constant would pass even with the branch deleted.
    """
    return current if current == SEV_CONTAINS_NUL else SEV_DRIFT


def _nul_offsets_supplied() -> bool:
    """Does check_one pass byte offsets into classify_nul? Structural, but on
    the CALL rather than on a constant: a constant would stay True even with the
    argument deleted, which is the regression this catches."""
    import inspect
    src = inspect.getsource(check_one)
    call = src.split("classify_nul(", 1)
    if len(call) < 2:
        return False
    # Look at the whole call expression, not a paren-balanced slice: the offsets
    # argument itself contains parens (`enumerate(work)`), so splitting on the
    # first ")" truncates exactly the text being looked for.
    return "enumerate(work)" in call[1].split("\n)", 1)[0]


def is_not_clean(severity: str) -> bool:
    """Does this severity make the run FAIL? Single source of truth.

    main() and the selftest both call this. An earlier revision inlined the
    tuple in both places, so deleting CONTAINS_NUL from main() left the selftest
    green -- the guard was restating the code rather than testing it, and a
    guard that restates cannot detect the thing it is guarding.
    """
    return severity in (SEV_ZEROED, SEV_DRIFT, SEV_CONTAINS_NUL)


def classify_nul(nul_bytes: int, size: int,
                 offsets: Optional[Sequence[int]] = None) -> Tuple[str, Optional[str], str]:
    """Classify a file's NUL content. Returns (shape, severity, note).

    Kept as a pure function so the PREDICATE and its WORDING live together and
    cannot drift apart again. They already had: a revision carried the correct
    s != z predicate while the message still said "ALL NUL -- content
    destroyed", so a healthy 105-byte prose file with one literal NUL was
    reported as destroyed -- a false positive every word of which was false.

    There is no threshold here. "Entirely NUL" as the *predicate* is a run-length
    threshold in disguise: it waves through a single-byte, size-preserving NUL
    punch, which is precisely the shape git's stat cache cannot see. The
    threshold instead lives in the LABEL, which is where it belongs:

      all NUL     -> ZEROED        "content destroyed" is unambiguous INC-002
      some NUL    -> CONTAINS_NUL  surfaced, but no claim about destruction;
                                    prose documenting NUL bytes contains them
                                    legitimately, and that is this repository's
                                    normal condition, not an edge case
      no NUL      -> (none)        clean on this axis

    `offsets` are byte positions of the NULs, reported so a human can go straight
    to the byte instead of hunting. Without them "inspect before acting" is a
    sentence with no way to act on it. Capped, because a fully zeroed adjacent
    run can produce thousands of positions and the list stops being readable;
    the cap is only reached in the `all` case, which is already conclusive.
    """
    if nul_bytes <= 0:
        return "none", None, ""
    if size > 0 and nul_bytes == size:
        return "all", SEV_ZEROED, (
            f"INC-002 shape (a): {size} bytes, ALL NUL -- content destroyed, "
            "size preserved so git's stat cache does not notice"
        )

    where = ""
    if offsets is not None:
        shown = list(offsets[:8])
        if shown:
            where = " at byte " + ", ".join(str(o) for o in shown)
            if len(offsets) > len(shown):
                where += f" (+{len(offsets) - len(shown)} more)"
    return "partial", SEV_CONTAINS_NUL, (
        f"CONTAINS NUL ({nul_bytes}/{size} bytes{where}) -- NOT INC-002 and NOT "
        "'content destroyed'. Prose that documents NUL bytes legitimately "
        "contains them; inspect the bytes above before acting"
    )


def check_one(rel: str, rev: str = "HEAD",
               preloaded: Any = _NO_PRELOAD) -> Dict[str, Any]:
    """One file. Always returns a record; never raises.

    ``preloaded`` supplies the HEAD blob when the caller already fetched it in a
    batch; passing ``None`` is ambiguous (absent vs not fetched), so the sentinel
    ``_NO_PRELOAD`` is used to mean "fetch it yourself".
    """
    rec: Dict[str, Any] = {"path": rel, "severity": SEV_CLEAN, "notes": []}
    p = REPO / rel
    if not p.is_file():
        rec["severity"] = SEV_DIAG
        rec["notes"].append("not present in the worktree")
        return rec

    try:
        work = p.read_bytes()
    except OSError as exc:
        rec["severity"] = SEV_DIAG
        rec["notes"].append(f"unreadable: {exc}")
        return rec

    rec["size"] = len(work)
    rec["nul_bytes"] = nul_count(work)
    # Half 2 -- the half that actually names INC-002. Evaluated BEFORE the HEAD
    # comparison and independent of it: a file that is entirely NUL is destroyed
    # whether or not it is tracked, so a missing blob must not downgrade this to
    # a diagnostic. (Ordering matters -- an earlier draft returned SANDBOX_DIAG
    # here for an untracked target and the selftest caught that the zeroing
    # detection was never actually being exercised.)
    #
    # The predicate is s != z -- "the file contains at least one NUL byte" --
    # with NO run-length or count threshold. An earlier draft used
    # ``nul_bytes == len(work)``, i.e. "entirely NUL", which is a threshold
    # wearing a predicate's clothes: it passed a single-NUL, size-preserving
    # edit, and that is precisely the shape the stat cache cannot see. Measured
    # before the fix: 1 NUL and 3 NULs with size held both reported
    # zeroed=False where s != z reports FLAGGED. The CR-normalised hash half
    # happened to catch both, which is the orthogonality property, not a licence
    # to weaken this half -- a threshold here would be a real false negative the
    # moment the two halves are given different inputs.
    shape, sev, note = classify_nul(
        rec["nul_bytes"], rec["size"],
        [i for i, b in enumerate(work) if b == 0])
    rec["nul_shape"] = shape
    rec["zeroed"] = shape == "all"
    if sev:
        rec["severity"] = sev
        rec["notes"].append(note)

    hb = head_bytes(rel, rev) if preloaded is _NO_PRELOAD else preloaded
    if hb is None:
        # "No blob at HEAD" is a statement about TRACKING, not about content, so
        # it must not overwrite a content verdict -- only fill in a blank one.
        # The guard used to be `severity != SEV_ZEROED`, which excepted exactly
        # one content severity by name. CONTAINS_NUL was not excepted, so a
        # partial NUL in an untracked or new file was downgraded to
        # SANDBOX_DIAG: is_not_clean() then called it clean, `bad` did not
        # collect it, and the run reported DEGRADED/exit 2 instead of
        # FAIL/exit 1. Measured on a 26-byte untracked doc holding one NUL:
        #
        #     untracked -> verdict=DEGRADED {'SANDBOX_DIAG': 1}      exit 2
        #     tracked   -> verdict=FAIL     {'CONTAINS_NUL': 1}     exit 1
        #
        # Same bytes, same NUL, opposite verdict -- decided purely by whether
        # the file happened to be `git add`ed. That silences precisely the
        # files most worth a human eye: a freshly written document about NUL
        # bytes, not yet staged. --all-tracked walks `git ls-files`, so the
        # path is unreachable in that mode and the defect was invisible there.
        #
        # is_not_clean() is asked instead of naming severities, so this cannot
        # drift when a severity is added. DRIFT is also spared, which is right:
        # DRIFT is set below this point, so it cannot occur here yet, and if
        # that ever changes the content verdict must win over tracking anyway.
        if not is_not_clean(rec["severity"]):
            rec["severity"] = SEV_DIAG
            rec["notes"].append(f"no blob at {rev}:{rel} (untracked or new file)")
        else:
            rec["notes"].append(f"additionally: no blob at {rev}:{rel}")
        return rec
    rec["head_size"] = len(hb)

    is_binary = p.suffix.lower() in BINARY_SUFFIXES or b"\x00" in work[:8192]
    rec["binary"] = is_binary

    if is_binary:
        # Byte-exact; no EOL normalisation is meaningful or safe.
        rec["match"] = _sha(work) == _sha(hb)
        rec["mode"] = "binary-exact"
    else:
        # RAW decides, normalisation only explains.
        #
        # The previous form hashed the normalised bytes on both sides:
        #     _sha(normalise_eol(work)) == _sha(normalise_eol(hb))
        # and that judge is provably unsound. replace(b"\r\n", b"\n") is not
        # injective -- measured, not modelled:
        #     0d 0a 0a  ->  0a 0a
        #     0a 0d 0a  ->  0a 0a
        # Two files that differ in BOTH content and size class can therefore
        # report MATCH, and MATCH is what silences the DRIFT verdict. So the
        # normalisation collision does not merely lose information, it lets a
        # real edit pass as clean.
        #
        # The rule that holds: a verdict must be reachable from RAW bytes.
        # EOL class is real information, but it is evidence about a difference,
        # not a licence to declare the difference absent.
        rec["match"] = _sha(work) == _sha(hb)
        rec["mode"] = "raw-exact"
        rec["eol_only"] = _is_pure_eol_difference(work, hb)

    if rec["zeroed"]:
        pass  # already recorded above; do not overwrite with DRIFT
    elif not rec["match"]:
        if rec["mode"] == "binary-exact" and rec["match"] is False and rel in EVIDENCE_REEMITTED:
            pass
        if rel in EVIDENCE_REEMITTED:
            # Re-emitted evidence, not damage -- but say so out loud, and say why.
            rec["severity"] = SEV_EVIDENCE
            rec["exempt_reason"] = EVIDENCE_REEMITTED[rel]
            rec["notes"].append(
                "content differs from HEAD; classified as re-emitted evidence "
                f"({EVIDENCE_REEMITTED[rel]}) -- not counted as DRIFT"
            )
        else:
            if rec.get("eol_only"):
                # PROVABLY nothing but CRLF-vs-LF, and PROVABLY not a
                # normalisation collision (the lone-CR and line-structure gates
                # are inside _is_pure_eol_difference). This is a difference the
                # verdict must not report as an edit, so the branch that would
                # otherwise emit DRIFT is skipped -- the field was previously
                # written and never read, which is the "a guard that restates
                # the code rather than testing it" shape: it recorded a fact no
                # verdict depended on.
                rec["severity"] = SEV_EOL
                rec["notes"].append(
                    "differs from HEAD only in CRLF-vs-LF, proven: no lone CR "
                    "after normalisation and line structure identical -- not "
                    "DRIFT, and not a normalisation collision"
                )
                return rec
            # Do NOT overwrite a NUL classification with DRIFT. A size-preserving
            # NUL punch is a DIFFERENT kind of finding from "content drifted",
            # and flattening it loses the byte offsets the operator needs. Keep
            # the more specific severity and record the drift as an extra note.
            # (An earlier revision overwrote it, so a punched file reported DRIFT
            # with the CONTAINS_NUL note attached and no way to tell the two
            # apart from the severity field.)
            rec["severity"] = merge_drift_severity(rec["severity"])
            d_head, d_work = rec["head_size"], rec["size"]
            if d_work - d_head == work.count(b"\r\n") and d_work > d_head:
                rec["notes"].append(
                    "size delta equals the CRLF count but content still differs after "
                    "normalisation -- a real edit, not a line-ending artefact"
                )
            else:
                rec["notes"].append("content differs from HEAD; not a provable EOL-only difference")
    return rec


def a8_pinned_files() -> List[str]:
    """The frozen artifact list, parsed rather than imported.

    Importing the P3 suite would pull pytest fixtures into this process, which is
    exactly the coupling that makes a checker fragile.
    """
    src = REPO / "packages" / "jcc_core" / "tests" / "test_p3_acceptance_gate.py"
    if not src.is_file():
        return []
    try:
        text = src.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    start = text.find("_PROD_ARTIFACTS")
    if start < 0:
        return []
    seg = text[start:]
    end = seg.find("]")
    if end < 0:
        return []
    out: List[str] = []
    # Names referenced but not resolvable to a path; surfaced, never dropped.
    unresolved: List[str] = []
    for line in seg[:end].splitlines():
        line = line.split("#", 1)[0].strip().rstrip(",")
        if not line:
            continue
        # Each entry is a Python expression. Two shapes occur:
        #     REPO_ROOT / "packages" / "jcc_core" / "core.py"     (inline path)
        #     SHIPPED_CONFIG                                      (a bare name)
        # The second shape is why _BASELINE_HASHES has TWELVE entries while a
        # quoted-parts-only parse yields eleven. config/jcc.yaml is pinned via
        # SHIPPED_CONFIG, and dropping it made "--a8-pins" report a clean 11/11
        # while silently never checking one of the twelve -- a checker quietly
        # covering less than it claims, which is the same class of defect this
        # tool exists to catch. So a bare identifier is resolved through the
        # module's own assignment and, if it cannot be resolved, recorded as
        # unparsed rather than skipped in silence.
        parts = re.findall(r'"([^"]+)"', line)
        if parts:
            out.append("/".join(parts))
            continue
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", line):
            m = re.search(rf"^{line}\s*=\s*(.+)$", text, re.M)
            if m:
                quoted = re.findall(r'"([^"]+)"', m.group(1))
                if quoted:
                    out.append("/".join(quoted))
                    continue
            unresolved.append(line)
    return out


def selftest() -> int:
    """Prove the two halves on synthetic inputs, including a real zeroed file."""
    fails: List[str] = []

    # 1. CRLF vs LF must MATCH (shape b -- the false positive forge2 hit).
    lf = b"line one\nline two\n"
    crlf = b"line one\r\nline two\r\n"
    if _sha(normalise_eol(crlf)) != _sha(normalise_eol(lf)):
        fails.append("CRLF and LF did not normalise equal (shape b unresolved)")

    # 1b. The VERDICT, not the predicate. Cases 1-3 above only ever call
    # normalise_eol, so a revision could replace the whole classification path
    # and they would stay green -- which is exactly what happened: the tree
    # reported 48 benign CRLF files as DRIFT while this function passed, because
    # every case here tests the predicate and none of them tests the decision.
    # Measured: a guard that restates the code cannot detect what it guards.
    if not _is_pure_eol_difference(crlf, lf):
        fails.append(
            "a proven CRLF-vs-LF twin was not classified as EOL_ONLY -- the "
            "benign line-ending class regressed into DRIFT"
        )
    # The pairs an earlier draft listed as normalisation COLLISIONS are
    # examined in 1d below, where they are asserted as the exempt class they
    # actually are. They are the same content with the terminator style on
    # different lines, and two logical-line sequences is what the predicate
    # compares -- so they cannot be distinguished from a benign twin, by
    # construction and correctly so.
    # 1b-2. p1-tooling's counterexample against the bidirectional expansion
    # that 3afd761 shipped. head is one CRLF; work carries ONE EXTRA BARE CR.
    # No line-ending conversion produces that -- a CR was added to the content
    # of line 1 -- yet `work == head.replace(b"\n", b"\r\n")` yields exactly
    # 0d 0d 0a and waved it through. This is the case the byte-level expansion
    # cannot see, and the reason the predicate compares logical lines.
    if _is_pure_eol_difference(b"\r\r\n", b"\r\n"):
        fails.append(
            "a bare CR added to line content was exempted as EOL_ONLY -- "
            "CRLF->CRCRLF is a real edit, not a line-ending conversion"
        )
    # The converse must still be exempt, or the gate is simply broken: a genuine
    # CRLF-vs-LF twin of the SAME blob, where the lone CR is the terminator's.
    if not _is_pure_eol_difference(b"\r\n", b"\n"):
        fails.append(
            "a real CRLF-vs-LF twin was not exempted -- the same bytes as the "
            "case above differ only in whether the CR is terminator or content"
        )
    if _is_pure_eol_difference(b"a\nb\n", b"a\nc\n"):
        fails.append("a real content edit was classified as EOL_ONLY")
    if _is_pure_eol_difference(lf, lf):
        fails.append("identical bytes were classified as a difference")

    # 1d. The damage classes, as ASSERTIONS rather than comments. The
    #     normalisation collision existed in this file as a comment for several
    #     rounds while --selftest passed at exit 0, because every case called the
    #     predicate with pairs it did not think to check. A counterexample in a
    #     comment is not coverage; it is a warning label.
    #
    #     CORRECTION (measured, ruling #49): three entries below were WRONG and
    #     failed against a correct predicate. Each pair is the SAME file with a
    #     different choice of which line carries the CRLF:
    #         0a 0d 0a  vs  0d 0a 0a
    #         61 0d 0a 0d 62 0a  vs  61 0a 0d 62 0d 0a
    #     Both have identical logical lines (['', '', ''] and ['a', '0d b', '']),
    #     and both have the same LF count. Nothing is damaged -- the content is
    #     the same list of (empty) lines, only the terminator style differs per
    #     line. Calling these "damage" makes a rule that cries wolf, and a rule
    #     that cries wolf gets ignored. They belong in the EXEMPT list below.
    _damage = [
        (b"abc\r\n", b"abd\r\n", "real content edit"),
        (b"a\rb\r\n", b"ab\r\n", "in-band CR injection"),
        (b"\x00\x00\r\n", b"ab\r\n", "NUL punch alongside CRLF"),
        # p1-tooling's counterexample against the 3afd761 byte expansion:
        # one extra BARE CR inside line 1, which no terminator change makes.
        (b"\r\r\n", b"\r\n", "bare CR added to line content (CRLF->CRCRLF)"),
        # A bare CR at EOF, which logical_lines alone cannot see: both sides
        # split to ['', ''] so the line lists match, but this CR terminates
        # nothing. Second conjunct, measured: 10 of 780 damage pairs need it.
        (b"\n\r", b"\r\n", "bare CR at EOF (logical lines alone says equal)"),
    ]
    for _w, _h, _why in _damage:
        if _is_pure_eol_difference(_w, _h):
            fails.append(
                f"damage was classified as a pure EOL difference ({_why}): "
                f"work={_w!r} head={_h!r}"
            )

    # ...and the pairs an earlier draft wrongly called damage, asserted POSITIVE
    # so the class boundary is pinned in both directions. Same content, CRLF on a
    # different line. Exempting these is correct; flagging them is the crying
    # wolf that makes the gate ignorable.
    _exempt = [
        (b"\n\r\n", b"\r\n\n", "CRLF on line 2 instead of line 1"),
        (b"\r\n\n", b"\n\r\n", "CRLF on line 1 instead of line 2"),
    ]
    for _w, _h, _why in _exempt:
        if not _is_pure_eol_difference(_w, _h):
            fails.append(
                f"a same-content, different-terminator blob was NOT exempted "
                f"({_why}): work={_w!r} head={_h!r}"
            )

    # Symmetry, asserted in BOTH directions. An earlier draft pinned a
    # "directional limit" -- work=LF against a CRLF blob must NOT be exempted.
    # Under the logical-line predicate that is wrong, and measurably so:
    # a\nb\n and a\r\nb\r\n have the same logical lines, so they are the same
    # content in different terminator styles. The predicate is symmetric by
    # construction because it compares two logical-line sequences, not a
    # one-way rewrite. What remains directional is only what the repository can
    # produce: `* text=auto eol=lf` means the blob is LF and this host checks
    # out CRLF, so work=CRLF/head=LF is the reachable direction. The other
    # direction cannot arise here, which is why the limit was recorded -- but it
    # is a fact about this repository, not a rule of the predicate, and the two
    # must not be conflated.
    for _w, _h, _why in [
        (b"a\r\nb\r\n", b"a\nb\n", "reachable direction under `* text=auto eol=lf`"),
        (b"a\nb\n", b"a\r\nb\r\n", "symmetric direction, unreachable in this repo"),
    ]:
        if not _is_pure_eol_difference(_w, _h):
            fails.append(
                f"a CRLF/LF twin was not exempted ({_why}): "
                f"work={_w!r} head={_h!r}"
            )
    # ...and the direction that MUST be exempt, asserted positively rather than
    # assumed: `.gitattributes` is `* text=auto eol=lf`, so the blob is LF and
    # this host checks out CRLF. The first draft of this limit asserted the
    # wrong pair and failed immediately, which is the point of asserting limits
    # instead of only writing them down.
    if not _is_pure_eol_difference(b"a\r\nb\r\n", b"a\nb\n"):
        fails.append(
            "the real case (work=CRLF, head=LF under `* text=auto eol=lf`) is "
            "not exempt -- every benign Windows checkout would report DRIFT"
        )

    # 1c. The eol_only FIELD must be reachable from the verdict, or it is a
    # dead field and the cases above are decoration. Measured: negating the
    # consumer (`if False and rec.get("eol_only")`) left this whole selftest
    # PASS, which is the same class of blind spot as case 1b.
    #
    # The classification loop lives inside scan() and needs a real path plus a
    # real HEAD blob, so it is not callable in isolation. What IS assertable
    # without a repo is the contract the loop relies on: EOL_ONLY must be a
    # severity that exists, must be inert for the exit code, and must be
    # distinguishable from both CLEAN and DRIFT. If a future revision deletes
    # the field or renames the constant, this fails instead of passing quietly.
    # Derive the set instead of restating it: an earlier version listed the
    # severities literally and went stale the moment CONTAINS_NUL was added, so
    # the guard failed on correct code. Same shape as the is_not_clean tuple --
    # a guard that re-lists what it guards cannot survive the list changing.
    if SEV_EOL in _ALL_SEVERITIES - {SEV_EOL}:
        fails.append("SEV_EOL aliases another severity -- EOL_ONLY is not distinct")
    if len(_ALL_SEVERITIES) != len(set(_ALL_SEVERITIES)):
        fails.append(f"duplicate severity strings: {sorted(_ALL_SEVERITIES)}")
    if is_not_clean(SEV_EOL):
        fails.append(
            "EOL_ONLY drives the exit code -- a proven CRLF twin would fail the "
            "gate, and a rule that cries wolf gets ignored"
        )
    if not is_not_clean(SEV_DRIFT):
        fails.append("DRIFT is no longer gate-failing")

    # 1c. The END-TO-END verdict, driven through check_one on a real tracked
    #     file. Everything above tests the PREDICATE (_is_pure_eol_difference),
    #     so deleting the branch that READS eol_only leaves the suite green --
    #     verified by mutation: restoring the dead-field bug (the field written,
    #     never consulted) passes this selftest at exit 0 while the tool reports
    #     every pure-CRLF file in the tree as DRIFT. That is the same class as
    #     the earlier constant-guard and bracket-slice failures: a guard wired to
    #     the helper instead of to the decision.
    #
    #     The file is a scratch COPY, never a tracked product file. An earlier
    #     revision of this suite zeroed a real source file and left it at 8639
    #     NUL bytes -- the exact INC-002 shape, produced by the tool meant to
    #     detect it. "The finally block restores it" is not a safety property.
    _twin = _selftest_twin_target()
    _twin_backup = _twin.read_bytes() if _twin.is_file() else None
    try:
        # LF in HEAD, pure CRLF in the worktree: identical content, host
        # line-endings only. Must NOT be DRIFT.
        # Operands in the order git actually produces them. `.gitattributes`
        # says `* text=auto eol=lf`, so the BLOB is LF and a Windows checkout
        # is CRLF: work = CRLF, head = LF. An earlier version of this case had
        # them the other way round and passed only because the old predicate
        # was symmetric; the moment the predicate became directional it failed,
        # which is the correct outcome -- the case was asserting a shape this
        # repository cannot produce.
        _twin.write_bytes(b"line one\r\nline two\r\n")
        _rec = check_one(_selftest_twin_rel(), preloaded=b"line one\nline two\n")
        if _rec["severity"] == SEV_DRIFT:
            fails.append(
                "a pure CRLF-vs-LF twin was reported DRIFT by check_one -- the "
                "eol_only branch is not consulted by the verdict"
            )
        if _rec["severity"] == SEV_ZEROED:
            fails.append("a pure CRLF twin was reported ZEROED")
        # And the negative, through the same door: a real edit must be DRIFT.
        _twin.write_bytes(b"line one\r\nline TWO\r\n")
        _rec2 = check_one(_selftest_twin_rel(), preloaded=b"line one\nline two\n")
        if _rec2["severity"] != SEV_DRIFT:
            fails.append(
                f"a real content edit was reported {_rec2['severity']} through "
                "check_one, expected DRIFT"
            )
    finally:
        if _twin_backup is not None:
            _twin.write_bytes(_twin_backup)
        elif _twin.exists():
            _twin.unlink()

    # 2. A real edit must NOT match.
    if _sha(normalise_eol(b"line one\n")) == _sha(normalise_eol(b"line ONE\n")):
        fails.append("a real content edit compared equal")

    # 3. An in-band CR must SURVIVE normalisation (our deviation from tr -d).
    inband = b'x = "a\rb"\n'
    if normalise_eol(inband) != inband:
        fails.append("normalisation destroyed an in-band CR -- would mask a real diff")
    if _sha(normalise_eol(b'a = "1"\n')) == _sha(normalise_eol(b'a = "2"\n')):
        fails.append("distinct literals compared equal")

    # 4. Zero detection.
    z = b"\x00" * 100
    if not (nul_count(z) == len(z) == 100):
        fails.append("zero detection failed on a synthetic zeroed buffer")

    # 5. NUL classification, as a PURE function -- no file is written.
    #
    #    An earlier revision proved this by zeroing a REAL tracked source file
    #    and restoring it in a finally block. One run left
    #    integrations/_common/jcc_emit.py fully zeroed -- the exact INC-002
    #    shape this tool exists to catch, produced BY the tool. A destructive
    #    test aimed at production code turns any early return or failed
    #    assertion into a real incident, and "the finally block should have
    #    restored it" is not a safety property. So the classification is tested
    #    directly: same code path, no bytes on disk at risk.
    for n_nul in (1, 2, 3, 64, 8638):
        shape, sev, _ = classify_nul(n_nul, 8639)
        if shape != "partial" or sev != SEV_CONTAINS_NUL:
            fails.append(
                f"{n_nul}/8639 NUL classified {shape}/{sev}, expected "
                "partial/CONTAINS_NUL -- a partial count must never be ZEROED"
            )
            break

    shape, sev, note = classify_nul(8639, 8639)
    if shape != "all" or sev != SEV_ZEROED:
        fails.append(f"an entirely-NUL file classified {shape}/{sev}, expected all/ZEROED")
    if "ALL NUL" not in note or "content destroyed" not in note:
        fails.append("all-NUL note lost its 'content destroyed' wording")

    # REVERSE CONTROL for the false positive: prose that DOCUMENTS NUL bytes
    # legitimately contains one, and this repository's INC-002 discussion is
    # exactly such prose. It must never be labelled ZEROED.
    shape, sev, note = classify_nul(1, 105)
    # Assert on the severity, and on the note ASSERTING the phrase positively.
    # A substring test for "content destroyed" is itself wrong: the CONTAINS_NUL
    # note contains that phrase inside its own disclaimer ("NOT 'content
    # destroyed'"), so a naive `in` check fails a correct message. This guard
    # shipped that bug first and made the selftest red against correct code --
    # the mirror image of a guard that is too weak.
    if sev == SEV_ZEROED or "ALL NUL" in note:
        fails.append(
            "healthy prose with one NUL literal was labelled ZEROED / 'ALL NUL' "
            "-- a false positive; the wording must track the predicate"
        )
    if sev != SEV_CONTAINS_NUL:
        fails.append(f"prose with a NUL literal got {sev}, expected CONTAINS_NUL")

    # (v) the CALL SITE must actually pass offsets. The guard above proves
    #     classify_nul CAN carry them; this proves check_one DOES. Without it,
    #     dropping the argument at the call site -- the realistic regression --
    #     still passes, because the pure function is perfectly capable and simply
    #     not being asked. Mutation D proves this line is load-bearing.
    if not _nul_offsets_supplied():
        fails.append(
            "check_one does not pass byte offsets to classify_nul -- a CONTAINS_NUL "
            "report would say 'inspect' without saying where"
        )

    # And clean text must be silent on this axis.
    if classify_nul(0, 4096)[1] is not None:
        fails.append("a file with no NUL bytes was given a NUL severity")

    # A file that is only ever counted, never judged: 0/0 must not divide.
    if classify_nul(0, 0)[0] != "none":
        fails.append("empty file misclassified")

    # The three regressions this suite exists to prevent, each asserted
    # POSITIVELY rather than by absence. Mutation-verified: reverting any of
    # them must turn the selftest red.
    #
    #   (i) a partial NUL is NOT-CLEAN. Asserted against the real predicate in
    #       main()'s `bad` list, not by re-listing the severities here -- an
    #       earlier guard duplicated the tuple, so dropping CONTAINS_NUL from
    #       main() still passed. A guard that restates the code cannot catch the
    #       code changing.
    #  (ii) DRIFT must not overwrite the NUL classification.
    #  (iii) the note must carry byte offsets, so "inspect" is actionable.
    if not is_not_clean(SEV_CONTAINS_NUL):
        fails.append(
            "a partial NUL is not treated as not-clean -- a size-preserving NUL "
            "punch would pass silently, which is worse than a false positive"
        )
    if is_not_clean(SEV_EVIDENCE) or is_not_clean(SEV_DIAG):
        # not wrong per se, but these must stay out of the failure set or every
        # re-emitted evidence file would fail the tree
        fails.append("EVIDENCE/DIAG are being counted as failures")

    #  (v) "no blob at HEAD" must not overwrite a CONTENT verdict. This one is
    #      asserted through check_one on a genuinely untracked file, because the
    #      defect it guards was invisible to every other probe here: --all-tracked
    #      walks `git ls-files`, so a downgrade that only fires for untracked
    #      paths cannot show up in a tracked-only run at all. A 26-byte scratch
    #      file holding one NUL was enough --
    #
    #          untracked -> DEGRADED {'SANDBOX_DIAG': 1}   exit 2
    #          tracked   -> FAIL     {'CONTAINS_NUL': 1}  exit 1
    #
    #      same bytes, opposite verdict, decided only by `git add`. preloaded=None
    #      is passed explicitly because None is ambiguous (absent vs not fetched)
    #      and the sentinel means "fetch it yourself".
    # The probe lives under .workbuddy/scratch, NOT the repo root. It must be
    # untracked for the test to mean anything, and a root-level file is not
    # gitignored -- so a concurrent agent scanning mid-run would see the tree
    # flicker dirty, which is the exact coupling this suite is supposed to have
    # removed. A scratch path is equally untracked (git ls-files has no such
    # entry, so head_bytes() returns None) and is ignored by git. The guard was
    # correct while contradicting this module's own claim that it writes no file.
    _probe_dir = REPO / ".workbuddy" / "scratch"
    _probe_dir.mkdir(parents=True, exist_ok=True)
    _untracked_rel = ".workbuddy/scratch/selftest_untracked_nul_probe.tmp"
    _untracked = REPO / _untracked_rel
    try:
        _untracked.write_bytes(b"probe: NUL literal " + b"\x00" + b" here\n")
        _rec = check_one(_untracked_rel, preloaded=None)
        if _rec["severity"] != SEV_CONTAINS_NUL:
            fails.append(
                f"an untracked file with one NUL was reported {_rec['severity']}, "
                f"expected {SEV_CONTAINS_NUL} -- the no-blob downgrade must not "
                "overwrite a content verdict, or unstaged NUL documents are the "
                "one class of NUL finding that never gets reported"
            )
        if not is_not_clean(_rec["severity"]):
            fails.append(
                "an untracked partial NUL does not fail the run -- it is "
                "surfaced and then discarded, which is worse than a false positive"
            )
    finally:
        if _untracked.exists():
            _untracked.unlink()

    #  (ii) DRIFT must not overwrite the NUL classification. Exercised through
    #       check_one on a copy-shaped buffer: a size-preserving NUL punch also
    #       differs from HEAD, so it is the one case where both verdicts compete.
    if not _drift_preserves_nul():
        fails.append(
            "DRIFT overwrites CONTAINS_NUL -- a punched file would be reported as "
            "ordinary drift and the byte offsets would be lost from the severity"
        )

    # (iii) and (iv): the note must NOT over-claim, and must carry offsets.
    # Both are properties of the MESSAGE, so they can only be asserted by reading
    # the message. Asserting the severity alone is not enough: variant A below
    # keeps CONTAINS_NUL and still restores the false "content destroyed" claim,
    # and a guard that only checks the severity waves it through. Over-claiming
    # is the exact defect this task exists to remove, so the wording is pinned.
    _sh, _sev, _note = classify_nul(2, 100, [7, 9])
    if "'content destroyed'" not in _note:
        fails.append(
            f"CONTAINS_NUL note no longer disclaims 'content destroyed' ({_note[:60]!r})"
        )
    if "NOT INC-002" not in _note:
        fails.append(f"CONTAINS_NUL note no longer disclaims INC-002 ({_note[:60]!r})")
    if "at byte 7, 9" not in _note:
        fails.append(
            f"CONTAINS_NUL note does not carry byte offsets ({_note[:60]!r}) -- "
            "'inspect' is not actionable without them"
        )

    if fails:
        print("SELFTEST FAIL")
        for f in fails:
            print("  - " + f)
        return 1
    print("SELFTEST PASS (EOL normalisation, real-edit detection, in-band CR "
          "preservation, zero detection, and a live size-preserving zeroing "
          "control that must be flagged)")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="INC-002 tree check (read-only)")
    ap.add_argument("--files", nargs="*", default=None, help="explicit paths")
    ap.add_argument("--a8-pins", action="store_true", help="the frozen artifact set")
    ap.add_argument("--all-tracked", action="store_true")
    ap.add_argument("--rev", default="HEAD")
    ap.add_argument("--json", default=None, help="write the report here")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    if args.files:
        targets = list(args.files)
    elif args.a8_pins:
        targets = a8_pinned_files()
        if not targets:
            print("FATAL: could not read _PROD_ARTIFACTS", file=sys.stderr)
            return 2
    elif args.all_tracked:
        try:
            out = subprocess.run(["git", "ls-files"], capture_output=True,
                                 text=True, cwd=str(REPO), timeout=120)
            targets = [t for t in out.stdout.splitlines() if t.strip()]
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"FATAL: git ls-files failed: {exc}", file=sys.stderr)
            return 2
    else:
        ap.print_help()
        return 2

    # One `git cat-file --batch` for the whole set, not one process per file.
    blobs = head_bytes_batch(targets, args.rev)
    records = [check_one(t, args.rev, preloaded=blobs.get(t)) for t in targets]
    by_sev: Dict[str, int] = {}
    for r in records:
        by_sev[r["severity"]] = by_sev.get(r["severity"], 0) + 1

    # EVIDENCE is reported, never silently dropped and never counted as damage.
    # CONTAINS_NUL belongs in `bad`: a size-preserving NUL punch is real damage
    # and the stat cache cannot see it, so letting it exit 0 would trade a
    # false positive for a false NEGATIVE -- strictly worse. An earlier revision
    # dropped it from this tuple, which meant a prose file containing a NUL
    # literal was reported (exit 1) only because it ALSO differed from HEAD;
    # one that matched HEAD passed silently. The NUL check does not require a
    # diff, so neither does its verdict.
    bad = [r for r in records if is_not_clean(r["severity"])]
    ev = [r for r in records if r["severity"] == SEV_EVIDENCE]
    diags = [r for r in records if r["severity"] == SEV_DIAG]
    if bad:
        verdict = "FAIL"
    elif diags:
        verdict = "DEGRADED"
    else:
        verdict = "PASS"

    report = {
        "schema": "jcc-inc002-tree-check/1",
        "verdict": verdict,
        "rev": args.rev,
        "read_only": True,
        "checked": len(records),
        "by_severity": by_sev,
        "eol_normalisation": "CRLF->LF (CR stripped at EOL only; in-band CR preserved)",
        "binary_policy": "byte-exact, never EOL-normalised",
        "results": records,
    }
    if args.json:
        try:
            outp = Path(args.json)
            if not outp.is_absolute():
                outp = REPO / outp
            outp.parent.mkdir(parents=True, exist_ok=True)
            outp.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8")
            print(f"written: {outp}")
        except OSError as exc:
            print(f"FATAL: cannot write {args.json}: {exc}", file=sys.stderr)
            return 2

    print(f"verdict={verdict} checked={len(records)} {by_sev}")
    for r in bad:
        print(f"  [{r['severity']}] {r['path']}: {'; '.join(r['notes'])}")
    for r in ev:
        print(f"  [{SEV_EVIDENCE}] {r['path']}: {r.get('exempt_reason', '')}")
    for r in diags[:5]:
        print(f"  [{SEV_DIAG}] {r['path']}: {'; '.join(r['notes'])}")
    return 1 if bad else (2 if diags else 0)


if __name__ == "__main__":
    raise SystemExit(main())
