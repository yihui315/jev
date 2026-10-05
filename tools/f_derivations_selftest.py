#!/usr/bin/env python3
"""Self-test for tools/f_derivations_falsifier.py -- the mutation-lands guard.

WHY THIS FILE EXISTS
--------------------
The negative control in the detector proves it can FIND a drive literal. This
file guards a different, quieter failure: a probe that mutates a sample and then
draws a conclusion from the result, where the mutation never happened.

That is not hypothetical. Writing the detector's own NUL check, I justified it
with:

    bytes.replace(b"# frozen", b"# \x00roze", 1)   # against the detector file

and then reported "a NUL in a comment parses fine, so the gate was blind to
it". The string "# frozen" does not occur in that file. The replace was a no-op,
the sample stayed clean, and I read the clean result as the finding. Both
`compile()` and `tokenize` in fact REJECT a NUL anywhere, so the claimed hole
did not exist.

The same shape bit me three more times in one round: an anchor that did not
match, a replacement that changed the byte count and so broke the
size-preserved invariant, and a CR-split literal misread as a detector miss.
Every one was caught by an assertion, never by reasoning -- which is the only
reason any of them got caught at all.

So the invariant enforced here is: EVERY mutation used as evidence must be
proven to have landed, and the detector must be shown to be what changed the
verdict. A probe that cannot demonstrate its own mutation is not evidence.

usage: python tools/f_derivations_selftest.py
exit 0 = pass, 1 = fail
"""
from __future__ import annotations

import io
import pathlib
import sys
import tempfile
import tokenize

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import f_derivations_falsifier as fals  # noqa: E402


def mutate(src: bytes, anchor: bytes, repl: bytes) -> bytes:
    """bytes.replace that REFUSES to be a no-op.

    The refusal is the entire point. A silent no-op produces a sample identical
    to the baseline, and a baseline-clean result then 'confirms' whatever the
    author expected to find.
    """
    if anchor not in src:
        raise AssertionError(
            "anchor %r not present -- the mutation would be a NO-OP and any "
            "conclusion drawn from it would be vacuous" % (anchor,))
    out = src.replace(anchor, repl, 1)
    if out == src:
        raise AssertionError("replace() produced identical bytes")
    return out


# NEVER mutate a tracked file to run a test. Measured in this repository, not
# theorised: `tools/inc002_tree_check.py --selftest` wrote its "healthy prose
# containing one NUL literal" sample straight into
# `integrations/_common/jcc_emit.py`, which left that tracked product file at
# 8639 NUL bytes in a 8639-byte body -- INC-002 shape (a), manufactured by the
# very tool that detects it. A second run truncated it to 0 bytes. A `finally:
# write_bytes(original)` is best-effort, not a guarantee: one restore here
# silently no-opped, and a read racing the window saw an empty file.
#
# So every mutation in THIS file happens on a copy under a tempdir, and the
# originals are only ever read. If a future case needs a file inside REPO, the
# pattern is: copy to tmpdir, point the scanner at the copy, and assert the
# original is untouched afterwards -- never write to the original at all.


def _tokenize_rejects(raw: bytes) -> bool:
    """Does the tokenizer refuse this source? (The honesty check below.)"""
    try:
        for _ in tokenize.generate_tokens(
                io.StringIO(raw.decode("utf-8", "replace")).readline):
            pass
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return True
    return False


def selftest() -> int:
    failures: list[str] = []
    work = pathlib.Path(tempfile.mkdtemp(prefix="fals-selftest-"))

    print("selftest: mutation must land, and the detector must be the difference")
    print("=" * 74)

    # 1. the no-op guard fires ------------------------------------------
    try:
        mutate(b"hello", b"NOT-PRESENT", b"x")
        print("  FAIL  no-op guard did not fire")
        failures.append("no-op guard did not fire on an absent anchor")
    except AssertionError as exc:
        print("  PASS  absent anchor refused: %s" % str(exc)[:50])

    clean = b"# header comment\nX = 1\nY = 2\n"

    # 2. NUL is flagged, and the mutation is proven to have landed -------
    #    "X = 1" -> "X = \x001": same length, NUL replaces the space. (An
    #    earlier attempt used "X = \x001" against "X = 1" and grew the file by
    #    one byte -- the size assertion below caught exactly that.)
    one_nul = mutate(clean, b"X = 1", b"X =\x001")
    if len(one_nul) != len(clean):
        failures.append("NUL sample changed size, so it is not form (a')")
    p = work / "nul.py"
    p.write_bytes(one_nul)
    nul_flagged = any(h["kind"] == "UNREADABLE" for h in fals.scan_python(p))
    if not nul_flagged:
        failures.append("NUL-bearing file not flagged UNREADABLE")
    print("  %s  NUL flagged: %s (size %d -> %d)"
          % ("PASS" if nul_flagged else "FAIL", nul_flagged,
             len(clean), len(one_nul)))

    # 3. HONESTY CHECK: the parser rejects it too, so the detector's
    #    explicit NUL check is defence-in-depth, not the only line.
    #    This is the claim I got wrong before; assert it rather than assert
    #    a story.
    parser_rejects = _tokenize_rejects(one_nul)
    print("  INFO  tokenize also rejects the NUL: %s" % parser_rejects)
    if not parser_rejects:
        print("  NOTE  tokenizer tolerates it -- the explicit check is then the")
        print("        ONLY line of defence and must not be removed.")

    # 4. a clean file is not flagged (no over-trigger) -------------------
    p2 = work / "clean.py"
    p2.write_bytes(clean)
    over = [h for h in fals.scan_python(p2) if h["kind"] == "UNREADABLE"]
    if over:
        failures.append("clean file flagged UNREADABLE (false positive)")
    print("  %s  clean file not flagged: %s"
          % ("PASS" if not over else "FAIL", not over))

    # 5. threshold-free: one NUL flags where a run>=64 rule would not ----
    single = b"\x00" + clean[1:]
    assert len(single) == len(clean)
    p3 = work / "single.py"
    p3.write_bytes(single)
    longest = run = 0
    for b in single:
        run = run + 1 if b == 0 else 0
        longest = max(longest, run)
    mine = any(h["kind"] == "UNREADABLE" for h in fals.scan_python(p3))
    thresh = longest >= 64
    if not mine:
        failures.append("single-NUL file not flagged")
    if mine == thresh:
        failures.append("criterion indistinguishable from a run>=64 rule")
    print("  %s  single NUL: mine=%s run>=64=%s -> methods differ"
          % ("PASS" if (mine and not thresh) else "FAIL", mine, thresh))

    # 6. the PowerShell path checks NUL too ------------------------------
    ps = work / "x.ps1"
    ps.write_bytes(b"# note\x00\r\nWrite-Host hi\r\n")
    lo, _ = fals.scan_ps_file(ps)
    ps_flagged = any(h["kind"] == "UNREADABLE" for h in lo)
    if not ps_flagged:
        failures.append("PowerShell path does not flag NUL")
    print("  %s  PowerShell NUL flagged: %s"
          % ("PASS" if ps_flagged else "FAIL", ps_flagged))

    # 7. the detector still finds a plain literal ------------------------
    p4 = work / "lit.py"
    p4.write_bytes(b'X = "F:/jev"\n')
    lits = [h for h in fals.scan_python(p4) if h["kind"] != "UNREADABLE"]
    if not lits:
        failures.append("detector no longer finds a plain drive literal")
    print("  %s  plain drive literal found: %d"
          % ("PASS" if lits else "FAIL", len(lits)))

    # 8. a CR-split literal is correctly NOT a drive literal ------------
    #    (in-band CR splits "F:" from "/jev", so there is nothing to report)
    p5 = work / "cr.py"
    p5.write_bytes(b'X = "F\r:/jev"\n')
    cr_hits = [h for h in fals.scan_python(p5) if h["kind"] != "UNREADABLE"]
    if cr_hits:
        failures.append("CR-split literal wrongly reported as a drive path")
    print("  %s  CR-split literal not reported (no drive path exists)"
          % ("PASS" if not cr_hits else "FAIL"))

    print("=" * 74)
    if failures:
        print("SELFTEST FAIL")
        for f in failures:
            print("  - %s" % f)
        return 1
    print("SELFTEST PASS (mutation-lands guard; NUL in both paths; no "
          "over-trigger;")
    print("                 threshold-free; detector still detects; CR "
          "boundary honest)")
    return 0


if __name__ == "__main__":
    sys.exit(selftest())
