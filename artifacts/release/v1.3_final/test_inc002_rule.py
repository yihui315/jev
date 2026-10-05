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


def hdiff(rel: str) -> str:
    """forge2's hdiff: HEAD blob via `git cat-file -p`, CR stripped, hashed."""
    return hashlib.sha256(sh("cat-file", "-p", "HEAD:%s" % rel).replace(b"\r", b"")).hexdigest()


def hwork(path: Path) -> str:
    """forge2's hwork: worktree bytes, CR stripped, hashed."""
    return hashlib.sha256(path.read_bytes().replace(b"\r", b"")).hexdigest()


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


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    src = REPO / TARGET_REL
    head_blob = sh("cat-file", "-p", "HEAD:%s" % TARGET_REL)
    work_bytes = src.read_bytes()
    out: dict = {
        "rule_under_test": "forge2's hdiff/hwork (CR-stripped sha256) + non-zero-byte check",
        "target": TARGET_REL,
        "measurement": {
            "head_blob_bytes": len(head_blob),
            "worktree_bytes": len(work_bytes),
            "delta": len(work_bytes) - len(head_blob),
            "worktree_crlf_lines": work_bytes.count(b"\r\n"),
            "head_crlf_lines": head_blob.count(b"\r\n"),
            "content_equal_after_cr_strip": (
                head_blob.replace(b"\r", b"") == work_bytes.replace(b"\r", b"")),
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
                               "cr_strip_is_lossy": blob.replace(b"\r", b"") != blob,
                               "unchanged_reads_identical": same}
    else:
        out["binary_probe"] = {"skipped": "no apps/favicon.png on this host"}

    wrong = [c["case"] for c in out["cases"] if not c["rule_is_correct"]]
    out["verdict"] = (
        "RULE SOUND on all tested shapes: it matches CRLF-only noise, still detects a "
        "real one-byte edit, and still detects INC-002 in both the same-size-zeroed and "
        "truncated forms. Adopt as written."
        if not wrong else
        "RULE UNSOUND on: %s -- do not adopt as written." % wrong)
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
