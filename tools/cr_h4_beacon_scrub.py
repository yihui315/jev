"""Line-accurate CR-H4 scrub: prove, then remove ONLY the 3 corrupt beacon lines.

CR-H4 WAS RAISED BY harbor and is owned by harbor. team-lead ruled: the file is
UNTRACKED and gitignored (``.gitignore:6`` -> ``runtime/``; verified with
``git ls-files --error-unmatch`` -> no match), so the three NUL-padded lines are
scrubabble -- but ONLY with a per-line, reversible, evidence-preserving record.

WHY THIS IS NOT JUST ``grep -v``\\:. Deleting lines from an append-only evidence
log is exactly the operation that can turn "the beacon is corrupt" into "the
beacon was quietly shortened". So this script:

  1. READS the whole file and identifies the bad lines BY BYTES, not by a guess;
  2. refuses to touch anything unless every bad line is provably all-NUL (a NUL
     line carries no information, so removing it cannot lose evidence) and every
     KEPT line parses as JSON;
  3. writes the survivors to a NEW file and only then replaces the original,
     atomically, keeping a full-fidelity copy plus a sha256 of BOTH before and
     after;
  4. refuses to run twice (a second run finds 0 bad lines and exits 0 having
     changed nothing).

The before/after copies live under artifacts/, so the operation is reversible and
auditable even though the original is untracked.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LOG = REPO / "runtime" / "logs" / "stable-runtime-boot.jsonl"
OUTDIR = REPO / "artifacts" / "release" / "v1.3_final" / "cr_h4_beacon_scrub"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def classify(raw: bytes) -> tuple[list[int], list[int]]:
    """Return (all_nul_line_indexes, unparseable_but_not_all_nul_indexes)."""
    all_nul, other_bad = [], []
    for i, line in enumerate(raw.split(b"\n")):
        if not line.strip():
            continue
        if set(line) == {0}:
            all_nul.append(i)
            continue
        try:
            json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            other_bad.append(i)
    return all_nul, other_bad


def main() -> int:
    if not LOG.is_file():
        print("REFUSING: %s does not exist" % LOG)
        return 1
    raw = LOG.read_bytes()
    before_sha = sha256_bytes(raw)
    all_nul, other_bad = classify(raw)

    print("file            : %s" % LOG)
    print("bytes           : %d" % len(raw))
    print("sha256 BEFORE   : %s" % before_sha)
    print("all-NUL lines   : %d -> indexes %s" % (len(all_nul), all_nul))
    print("other unparseable: %d -> indexes %s" % (len(other_bad), other_bad))

    if other_bad:
        print("REFUSING: %d line(s) are unparseable but NOT all-NUL. Those may "
              "carry evidence, so this scrub only removes provably-empty lines."
              % len(other_bad))
        return 1
    if not all_nul:
        print("NOTHING TO DO: 0 all-NUL lines. The file is already clean; "
              "exiting 0 without writing.")
        return 0

    lines = raw.split(b"\n")
    survivors = [ln for i, ln in enumerate(lines) if i not in set(all_nul)]
    new_raw = b"\n".join(survivors)

    # Prove the survivors are all valid JSON BEFORE we are allowed to call this a
    # repair rather than a truncation.
    kept = 0
    for ln in survivors:
        if not ln.strip():
            continue
        json.loads(ln.decode("utf-8"))
        kept += 1
    removed_bytes = len(raw) - len(new_raw)

    OUTDIR.mkdir(parents=True, exist_ok=True)
    (OUTDIR / "BEFORE_stable-runtime-boot.jsonl").write_bytes(raw)
    (OUTDIR / "scrub_manifest.json").write_text(
        json.dumps({
            "log_rel": str(LOG.relative_to(REPO)).replace("\\", "/"),
            "git_tracked": False,
            "gitignored_by": ".gitignore:6 -> runtime/",
            "sha256_before": before_sha,
            "sha256_after": sha256_bytes(new_raw),
            "bytes_before": len(raw),
            "bytes_after": len(new_raw),
            "bytes_removed": removed_bytes,
            "line_indexes_removed_zero_based": all_nul,
            "lines_removed": len(all_nul),
            "json_lines_kept": kept,
            "reason": "every removed line was 100% NUL bytes (no information); "
                      "every kept line parses as JSON",
        }, indent=2, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n")

    # Atomic replace, same directory, so a reader never sees a partial file.
    tmp = LOG.with_suffix(".jsonl.scrubtmp")
    with open(tmp, "wb") as fh:
        fh.write(new_raw)
        fh.flush()
        os.fsync(fh.fileno())
    shutil.copystat(LOG, tmp)
    os.replace(tmp, LOG)

    after = LOG.read_bytes()
    print("sha256 AFTER    : %s" % sha256_bytes(after))
    print("bytes removed   : %d (%d line(s))" % (removed_bytes, len(all_nul)))
    print("json lines kept : %d" % kept)
    print("manifest        : %s" % (OUTDIR / "scrub_manifest.json"))
    print("full copy kept  : %s" % (OUTDIR / "BEFORE_stable-runtime-boot.jsonl"))

    # Post-condition: the file the failing test reads must now be clean.
    _, still_bad = classify(after)
    print("POST-CONDITION  : unparseable-non-NUL lines = %d" % len(still_bad))
    return 1 if still_bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
