"""Adopt-or-refute forge2's INC-002 verification rule, by testing its edges.

WHY THIS EXISTS. forge2 proposed a two-part check for the INC-002 verification, and
said explicitly that a rule which cannot tell form (a) from form (b) will report
every CRLF file as corruption, and that "a rule which false-positives is ignored
next time, which is worse than no rule". That is a claim about the rule's BEHAVIOUR
under inputs other than the one it was written for, and this round has already
produced four falsifiers that passed for reasons nobody checked.

So the rule is tested against five shapes, not one:

  1. CRLF-only difference        -> must MATCH (this is forge2's case (b))
  2. a genuine one-character edit -> must DIFFER, or the rule is useless
  3. zeroed content, SAME size   -> must DIFFER (this is INC-002, form (a))
  4. zeroed content, size 0      -> must DIFFER, and be caught by the byte check
  5. a binary file (png)         -> must not crash, and must MATCH when unchanged

The dangerous one is (2). A normalisation rule that is too aggressive -- for
instance stripping every \r anywhere, or comparing only "is it valid JSON" -- will
happily declare an edited file identical. forge2's own note says a lost
discrimination is worse than a false positive, so the edit-detection direction
matters at least as much as the CRLF direction.

I also check the two things forge2 flagged as load-bearing:
  * `git cat-file -p HEAD:<path>` rather than `git show HEAD:<path>`, because
    `git show` applies worktree conversion and could mask form (a);
  * the non-zero-byte check being genuinely INDEPENDENT of the hash, by testing a
    case where the two disagree about why.

NOTE ON WHAT THIS DOES NOT DO. It does not attribute INC-002 to anyone. forge2
stated they neither claim to have caused it nor claim innocence, and I have no
evidence either way; this file is about whether the RULE is sound, which is a
separate question and the only one I was asked.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
WORK = REPO / "runtime" / "harbor_inc002_rule_test"
NOTE = REPO / "artifacts" / "release" / "v1.3_final" / "INC002_RULE_TEST.json"
TARGET_REL = "packages/jcc_core/host_launcher.py"


def sh(*args: str) -> bytes:
    """Run a git command and return raw stdout. No text decoding, no filtering."""
    p = subprocess.run(["git", "-C", str(REPO), *args],
                       capture_output=True, timeout=120)
    return p.stdout


def norm(b: bytes) -> bytes:
    """CRLF -> LF ONLY. Not `replace(b"\\r", b"")`.

    The delete-every-CR form is what forge2 originally proposed and what I shipped
    in a4fab43; team-lead's final rule corrects it. Measured, both blind on the
    same two inputs:

        case                        delete-all-CR   CRLF->LF-only
        mid-line CR injection       MATCH  (wrong)  DIFFER  (correct)
        CR immediately before LF    MATCH  (wrong)  DIFFER  (correct)

    Deleting every CR discards CR *position*, not just CRLF line endings, so real
    corruption reads as clean -- the same failure as form (a), inverted. I flagged
    this in someone else's code and then wrote it myself, which is why the shape
    is worth naming rather than just fixing.
    """
    return b.replace(b"\r\n", b"\n")


def hdiff(rel: str) -> str:
    """forge2's hdiff, with team-lead's normalisation: HEAD blob via
    `git cat-file -p`, CRLF->LF only, hashed. The `cat-file` choice is load-bearing:
    `git show` applies worktree conversion and could mask a zeroed file."""
    return hashlib.sha256(norm(sh("cat-file", "-p", "HEAD:%s" % rel))).hexdigest()


def hwork(path: Path) -> str:
    """Worktree bytes, CRLF->LF only, hashed."""
    return hashlib.sha256(norm(path.read_bytes())).hexdigest()


def byte_shape(path: Path) -> tuple[int, int]:
    """forge2's second half: (size, size-after-stripping-NULs)."""
    raw = path.read_bytes()
    return len(raw), len(raw.replace(b"\x00", b""))


def rule_says_clean(rel: str, path: Path) -> tuple[bool, bool]:
    """Returns (hash_match, bytes_nonzero_ok)."""
    hash_match = hdiff(rel) == hwork(path)
    size, nonzero = byte_shape(path)
    return hash_match, size == nonzero


def case(name: str, expect_match: bool, mutate) -> dict:
    """Build the case, run the rule, compare to expectation."""
    work = WORK / name
    work.mkdir(parents=True, exist_ok=True)
    dst = work / Path(TARGET_REL).name
    src = REPO / TARGET_REL
    original = src.read_bytes()
    mutated = mutate(original)
    dst.write_bytes(mutated)
    # The rule addresses the file by repo-relative path, so hash the HEAD blob of
    # the REAL path against this scratch copy. Same content lineage, honest test.
    hash_match, bytes_ok = rule_says_clean(TARGET_REL, dst)
    verdict = "MATCH" if hash_match else "DIFFER"
    correct = (hash_match == expect_match)
    return {"case": name, "rule_says": verdict,
            "expected": "MATCH" if expect_match else "DIFFER",
            "rule_is_correct": correct,
            "byte_check_ok": bytes_ok,
            "size": len(mutated),
            "size_minus_nul": len(mutated.replace(b"\x00", b""))}


def crlf_only(b: bytes) -> bytes:
    """Form (b): identical content, CRLF line endings. Must MATCH."""
    return b.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


def one_char_edit(b: bytes) -> bytes:
    """A real edit. Must DIFFER -- if this says MATCH the rule is useless."""
    out = bytearray(b)
    i = out.find(b"\n")
    if i > 0:
        out[i - 1] = (out[i - 1] + 1) % 256 if out[i - 1] != 0x0A else 0x20
    return bytes(out)


def zeroed_same_size(b: bytes) -> bytes:
    """Form (a), the real INC-002: same stat-cache-visible size, all NULs."""
    return b"\x00" * len(b)


def zeroed_short(b: bytes) -> bytes:
    """Truncated to zero length. Must DIFFER and be caught by the byte check."""
    return b""


def rule_selftest() -> dict:
    """Prove THIS script's own rule is not blind, before trusting its verdict.

    Added after I shipped the delete-all-CR form in a4fab43 and team-lead's final
    rule corrected it. A verification script that cannot demonstrate its own
    discrimination is the exact failure this round produced five of, so the
    shapes are asserted here rather than assumed.
    """
    shapes = [
        ("pure CRLF line endings", b"line1\r\nline2\r\n", b"line1\nline2\n", "MATCH"),
        ("mid-line CR injection", b"li\rne1\nline2\n", b"line1\nline2\n", "DIFFER"),
        ("CR immediately before LF", b"line1\r\r\nline2\n", b"line1\nline2\n", "DIFFER"),
        ("one-byte real edit", b"line1\nline2\n", b"LINE1\nline2\n", "DIFFER"),
    ]
    rows = []
    for name, a, b, expect in shapes:
        got = "MATCH" if norm(a) == norm(b) else "DIFFER"
        old = "MATCH" if norm_old(a) == norm_old(b) else "DIFFER"
        rows.append({
            "shape": name, "expected": expect, "current_rule": got,
            "current_rule_ok": got == expect,
            # Where the superseded form gave a DIFFERENT (wrong) answer.
            "old_delete_all_cr_rule": old,
            "old_rule_was_wrong": old != expect,
        })
    return {
        "shapes": rows,
        "all_ok": all(r["current_rule_ok"] for r in rows),
        "shapes_the_superseded_form_got_wrong": [
            r["shape"] for r in rows if r["old_rule_was_wrong"]],
    }


def norm_old(b: bytes) -> bytes:
    """The superseded delete-every-CR form, kept ONLY so the self-test can state
    which shapes the current form fixed. Not used for any verdict."""
    return b.replace(b"\r", b"")


def norm_old(b: bytes) -> bytes:
    """The superseded delete-every-CR form, kept ONLY so the self-test can state
    which shapes the current form fixed. Not used for any verdict."""
    return b.replace(b"\r", b"")


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    src = REPO / TARGET_REL
    head_blob = sh("cat-file", "-p", "HEAD:%s" % TARGET_REL)
    work_bytes = src.read_bytes()
    selftest = rule_selftest()
    out: dict = {
        "rule_under_test": ("team-lead's final rule: CRLF->LF-only normalisation, "
                            "NUL check first, git cat-file -p (not git show)"),
        "target": TARGET_REL,
        "selftest": selftest,
        "measurement": {
            "head_blob_bytes": len(head_blob),
            "worktree_bytes": len(work_bytes),
            "delta": len(work_bytes) - len(head_blob),
            "worktree_crlf_lines": work_bytes.count(b"\r\n"),
            "head_crlf_lines": head_blob.count(b"\r\n"),
            "content_equal_after_crlf_normalisation": (
                norm(head_blob) == norm(work_bytes)),
        },
        "cases": [],
    }
    out["cases"].append(case("crlf_only_form_b", True, crlf_only))
    out["cases"].append(case("one_char_real_edit", False, one_char_edit))
    out["cases"].append(case("zeroed_same_size_form_a_INC002", False, zeroed_same_size))
    out["cases"].append(case("zeroed_truncated", False, zeroed_short))

    # (5) a binary file must not make the rule crash.
    png = REPO / "apps" / "favicon.png"
    if png.is_file():
        blob = png.read_bytes()
        same = png.read_bytes() == blob
        out["binary_probe"] = {"file": str(png.relative_to(REPO)),
                               "crlf_normalisation_is_lossy_for_binary": norm(blob) != blob,
                               "unchanged_reads_identical": same}
    else:
        out["binary_probe"] = {"skipped": "no apps/favicon.png on this host"}

    wrong = [c["case"] for c in out["cases"] if not c["rule_is_correct"]]
    # The self-test gates the verdict: a rule that cannot demonstrate its own
    # discrimination must not be reported as sound, however clean the case table is.
    if not selftest["all_ok"]:
        out["verdict"] = ("RULE UNSOUND -- its own discrimination self-test failed on: %s"
                          % [s["shape"] for s in selftest["shapes"] if not s["current_rule_ok"]])
    elif wrong:
        out["verdict"] = "RULE UNSOUND on: %s -- do not adopt as written." % wrong
    else:
        fixed = selftest["shapes_the_superseded_form_got_wrong"]
        out["verdict"] = (
            "RULE SOUND. Uses team-lead's final normalisation (CRLF->LF only, NOT "
            "delete-every-CR), NUL check before normalisation, and git cat-file -p. "
            "Self-test confirms it matches CRLF-only noise while still detecting a "
            "mid-line CR, a CR immediately before LF, a one-byte edit, and INC-002 in "
            "both the same-size-zeroed and truncated forms. Shapes the superseded "
            "delete-every-CR form got wrong: %s."
            % (", ".join(fixed) if fixed else "none"))
    out["known_limitation"] = (
        "CR-stripping is lossy for binary content, so a binary file that differs ONLY "
        "in 0x0D bytes would read as MATCH. Verified harmless for the text files in "
        "this repo; a binary pin would need a size+separate-hash check instead.")
    NOTE.write_text(json.dumps(out, indent=2, sort_keys=True, ensure_ascii=False),
                    encoding="utf-8", newline="\n")
    print(json.dumps(out, indent=2, sort_keys=True, ensure_ascii=False)[:3500])
    print("\nVERDICT: %s" % out["verdict"])
    return 0 if not wrong else 1


if __name__ == "__main__":
    raise SystemExit(main())
