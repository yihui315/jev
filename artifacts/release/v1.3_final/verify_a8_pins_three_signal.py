"""Verify every A8 pin with the THREE-SIGNAL rule, and report which signal fired.

WHY THIS EXISTS. forge2's INC-002 sweep used a two-signal rule and I showed it has a
blind spot: `tr -d '\r'` discards CR POSITION, so a same-size change involving a CR
reads as MATCH, and the non-zero-byte check cannot see it either. The three-signal
version keeps the RAW hash so that case is classified CR-ONLY instead of silently
folded into MATCH.

This applies that to the actual A8 pin table
(`packages/jcc_core/tests/test_p3_acceptance_gate.py:288 _BASELINE_HASHES`) rather
than only reasoning about it, and it answers the question the pins exist to answer:
is every pinned file still byte-identical to the baseline the A8 self-check asserts?

DELIBERATELY NOT A TAMPER TEST. forge2 already established that the A8 self-check
catches a single-byte tamper; re-proving that would be the fifth falsifier this
round that tested something other than the thing it was for. This run answers one
question only: do the 13 pins match, and by which of the three signals.

Signal order, and why it is this order:
  1. NUL check   -- must be first. A zeroed file (INC-002 form (a)) must never
                    reach a hash comparison and be explained away as CRLF.
  2. CR-stripped -- the real content comparison, immune to this host's autocrlf.
  3. raw hash    -- catches what CR-stripping discards (CR position), and is the
                    only signal that can label a file CR-ONLY.

Reported per pin: which signal fired, plus the byte counts, so a reader can see WHY
rather than being told a verdict.
"""
from __future__ import annotations

import ast
import hashlib
import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
GATE = REPO / "packages" / "jcc_core" / "tests" / "test_p3_acceptance_gate.py"
OUT = REPO / "artifacts" / "release" / "v1.3_final" / "A8_PIN_THREE_SIGNAL.json"


def load_pins() -> dict:
    """Read _BASELINE_HASHES out of the gate module WITHOUT importing it.

    Importing the test module would execute its import chain; the pins are a plain
    dict literal, so ast.literal_eval on the assignment is both safer and sufficient.
    Reading a value by executing the file that uses it is the same category of
    mistake as `git show` versus `git cat-file`.
    """
    tree = ast.parse(GATE.read_text(encoding="utf-8", errors="replace"),
                     filename=str(GATE))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "_BASELINE_HASHES":
                    return ast.literal_eval(node.value)
    raise SystemExit("_BASELINE_HASHES not found as a module-level assignment")


def head_blob(rel: str) -> bytes | None:
    p = subprocess.run(["git", "-C", str(REPO), "cat-file", "-p", "HEAD:%s" % rel],
                       capture_output=True, timeout=120)
    return p.stdout if p.returncode == 0 else None


def check(rel: str, expected: str) -> dict:
    work = REPO / rel
    rec: dict = {"pin": rel, "expected": expected[:12] + "..."}
    if not work.is_file():
        rec["signal"] = "MISSING"
        rec["verdict"] = "FAIL"
        return rec
    raw = work.read_bytes()
    rec["worktree_bytes"] = len(raw)
    rec["worktree_crlf"] = raw.count(b"\r\n")

    # Signal 1 -- NUL. First, always.
    if len(raw) != len(raw.replace(b"\x00", b"")):
        rec["signal"] = "ZEROED"
        rec["verdict"] = "FAIL"
        return rec

    blob = head_blob(rel)
    if blob is None:
        rec["signal"] = "NO_HEAD_BLOB"
        rec["verdict"] = "UNVERIFIED"
        return rec
    rec["head_bytes"] = len(blob)

    raw_h_work = hashlib.sha256(raw).hexdigest()
    raw_h_head = hashlib.sha256(blob).hexdigest()
    # CRLF -> LF ONLY, per team-lead's final rule. This file previously used
    # replace(b"\r", b""), which is the delete-every-CR form I criticised in
    # someone else's rule and then shipped myself: it discards CR *position*, so a
    # mid-line CR or a CR immediately before LF read as MATCH. Measured blind on
    # both. Corrected here so no committed script of mine carries the flaw.
    lf_h_work = hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()
    lf_h_head = hashlib.sha256(blob.replace(b"\r\n", b"\n")).hexdigest()
    rec["raw_match"] = raw_h_work == raw_h_head
    rec["lf_match"] = lf_h_work == lf_h_head

    if lf_h_work == expected:
        # Signal 2 fired clean. If the raw hash also matches, the file is identical
        # including line endings. If not, the difference is CR-only: benign on this
        # host, but named rather than folded into MATCH.
        rec["signal"] = "CR-ONLY" if raw_h_work != raw_h_head else "EXACT"
        rec["verdict"] = "PASS" if raw_h_work == raw_h_head else "PASS_CRONLY"
        if rec["verdict"] == "PASS_CRONLY":
            rec["note"] = ("CR-stripped content matches the pin, but the raw bytes "
                           "differ. Host autocrlf artefact, not a content change.")
    elif raw_h_work == raw_h_head:
        # The pin disagrees with HEAD but the file equals HEAD. The PIN is stale,
        # which is a re-baseline question, not a corruption question.
        rec["signal"] = "PIN_STALE_VS_HEAD"
        rec["verdict"] = "FAIL"
        rec["head_hash"] = raw_h_head[:12] + "..."
    else:
        rec["signal"] = "CONTENT_DIFFERS"
        rec["verdict"] = "FAIL"
        rec["note"] = "CR-stripped content differs from the pin: a real content change."
    return rec


def main() -> int:
    pins = load_pins()
    recs = [check(rel, exp) for rel, exp in sorted(pins.items())]
    tally: dict = {}
    for r in recs:
        tally[r["verdict"]] = tally.get(r["verdict"], 0) + 1
    out = {
        "pin_source": str(GATE.relative_to(REPO)) + " (_BASELINE_HASHES)",
        "pin_count": len(recs),
        "verdict_tally": tally,
        "signals_used": tally,
        "results": recs,
    }
    out["verdict"] = (
        "ALL PINS PASS, byte-identical including line endings"
        if tally.get("PASS") == len(recs) and len(recs) else
        "MIXED -- see per-pin results; a CR-ONLY pass is benign, anything else is FAIL")
    OUT.write_text(json.dumps(out, indent=2, sort_keys=True, ensure_ascii=False),
                   encoding="utf-8", newline="\n")
    for r in recs:
        print("%-12s %-22s %s" % (r["verdict"], r["signal"], r["pin"]))
    print("\ntally: %s" % json.dumps(tally, sort_keys=True))
    print("VERDICT: %s" % out["verdict"])
    print("wrote %s" % OUT)
    return 0 if tally.get("FAIL", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
