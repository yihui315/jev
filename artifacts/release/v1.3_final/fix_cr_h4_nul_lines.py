"""CR-H4 -- surgically drop the NUL-padded boot-beacon lines, keep everything else.

SCOPE, exactly as team-lead authorised: strip the NUL lines, leave the 2 torn
lines alone. Both halves of that instruction are honoured literally, and the
classification below is measured rather than assumed -- it does not match the
"3 NUL / 2 torn" summary in the request, and the difference matters, so it is
recorded instead of being quietly reconciled.

MEASURED CLASSIFICATION of runtime/logs/stable-runtime-boot.jsonl (4277 lines,
943 062 bytes, sha256 f6380edf...):

  * 4245 lines parse as JSON.
  * 3 lines are NUL-PADDED: 3887, 3925, 4204. Each is mostly NUL with a short
    valid JSON record embedded (155-157 non-NUL bytes inside 598-1481). These
    are recoverable: the record is intact, a NUL pad was written in front of it.
  * 2 lines are TORN, and these are NOT what "3 NUL / 2 torn" assumed:
        4215 (316 B)  "Expecting ':' delimiter: line 1 column 95"
        4235 (278 B)  "Expecting ':' delimiter: line 1 column 57"
    Both are genuine mid-record truncations -- a duplicated `"watchdog"` key and
    a `port=4778;python=[F:\jev\...` detail -- i.e. a writer was interrupted
    mid-append. There is nothing to recover, and per instruction they are LEFT
    IN PLACE. A test that asserts "every line is valid JSON" will still fail on
    these two; that is the correct outcome, and hiding it would be the defect.
  * 27 further lines are EMPTY (zero-length). `splitlines()` yields them and they
    are not JSON either, but an empty line carries no record and no recovery, so
    it is reported separately rather than conflated with the padded records.

WHY ONLY THE 3 PADDED ONES ARE TOUCHED. The writer
`scripts/ensure-stable-runtime.ps1:104-118` appends with an explicit handle and
`Write-JccBoot` round-trips each record through ConvertTo-Json before writing, so
today's writer does not produce this. These are historical artefacts of the
F:-era host. The value of stripping the padded three is that a recoverable record
is no longer invisible to a reader; the value of NOT touching the torn two is that
a real truncation stays visible as evidence.

SAFETY. Writes to a NEW file beside the original and only then replaces it, so a
crash mid-write cannot lose the log; the original is also byte-identical in the
timestamped backup. Every dropped line is written to a quarantine file, so
nothing is deleted -- it is moved somewhere reviewable. Refuses to run twice.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
LOG = REPO / "runtime" / "logs" / "stable-runtime-boot.jsonl"
OUTDIR = REPO / "artifacts" / "release" / "v1.3_final"
MARKER = OUTDIR / "CR_H4_DONE.json"

# Measured on this host; asserted at runtime rather than hard-coded blindly.
PADDED = (3887, 3925, 4204)
TORN_LEFT_IN_PLACE = (4215, 4235)


def is_padded(line: str) -> bool:
    """True when the line is NUL-padded but still carries a recoverable record.

    The test is deliberately narrow: it must hold a real JSON record after the
    NULs are stripped, and it must actually contain NULs. A line that is merely
    unparseable is NOT a padded record and must be left alone.
    """
    if "\x00" not in line:
        return False
    stripped = line.lstrip("\x00")
    if not stripped:
        return False
    try:
        json.loads(stripped)
    except ValueError:
        return False
    return True


def main() -> int:
    if MARKER.exists():
        print("CR-H4 already applied; marker present at %s" % MARKER)
        print("Refusing to run twice -- a second pass would re-classify new data.")
        return 2
    if not LOG.is_file():
        print("FATAL: %s does not exist" % LOG)
        return 1

    original_bytes = LOG.read_bytes()
    before_sha = hashlib.sha256(original_bytes).hexdigest()
    text = original_bytes.decode("utf-8", errors="replace")
    lines = text.splitlines()

    kept: list[str] = []
    recovered: list[dict] = []
    untouched_torn: list[dict] = []
    empties = 0
    for n, line in enumerate(lines, 1):
        if is_padded(line):
            rec = line.lstrip("\x00")
            recovered.append({"line": n, "bytes": len(line),
                              "nul_bytes": len(line) - len(rec),
                              "record": rec})
            kept.append(rec)          # the record survives, the pad does not
            continue
        if not line.strip():
            empties += 1
            kept.append(line)          # left as-is; reported, not silently dropped
            continue
        try:
            json.loads(line)
        except ValueError as exc:
            untouched_torn.append({"line": n, "bytes": len(line),
                                   "error": str(exc),
                                   "preview": line[:120]})
        kept.append(line)

    # Sanity: the recovered set must be exactly what was measured.
    got = tuple(r["line"] for r in recovered)
    if got != PADDED:
        print("FATAL: expected padded lines %s but classified %s" % (PADDED, got))
        print("Refusing to write. Re-measure before touching the log.")
        return 1
    torn_seen = tuple(r["line"] for r in untouched_torn)
    if torn_seen != TORN_LEFT_IN_PLACE:
        print("FATAL: expected torn lines %s but classified %s"
              % (TORN_LEFT_IN_PLACE, torn_seen))
        return 1

    body = "".join(l + "\n" for l in kept)
    tmp = LOG.with_suffix(".jsonl.crh4.tmp")
    tmp.write_text(body, encoding="utf-8", newline="\n")

    # Verify the rewrite before it replaces anything.
    after = tmp.read_text(encoding="utf-8").splitlines()
    ok = sum(1 for l in after if l.strip() and _parses(l))
    still_bad = [n for n, l in enumerate(after, 1)
                 if l.strip() and not _parses(l)]
    if still_bad and still_bad != list(TORN_LEFT_IN_PLACE):
        # Line numbers shift by the number of dropped lines; compare COUNTS of
        # unparseable non-empty lines instead, which must be exactly 2.
        if len(still_bad) != len(TORN_LEFT_IN_PLACE):
            print("FATAL: after rewrite there are %d unparseable lines, expected %d"
                  % (len(still_bad), len(TORN_LEFT_IN_PLACE)))
            tmp.unlink()
            return 1

    quarantine = OUTDIR / ("CR_H4_recovered_records_%s.jsonl"
                           % LOG.stat().st_mtime_ns)
    quarantine.write_text(
        "".join(r["record"] + "\n" for r in recovered), encoding="utf-8", newline="\n")

    shutil.copy2(LOG, LOG.with_suffix(".jsonl.crh4.bak"))
    tmp.replace(LOG)

    result = {
        "log": str(LOG),
        "before_sha256": before_sha,
        "before_bytes": len(original_bytes),
        "before_lines": len(lines),
        "after_bytes": len(LOG.read_bytes()),
        "after_lines": len(after),
        "recovered_from_nul_pad": recovered,
        "torn_left_in_place": untouched_torn,
        "empty_lines_left_in_place": empties,
        "valid_json_after": ok,
        "quarantine": str(quarantine),
        "sidecar_backup": str(LOG.with_suffix(".jsonl.crh4.bak")),
        "note": ("The 2 torn lines are deliberately retained. They are real "
                 "mid-append truncations from the F:-era host, they are not "
                 "recoverable, and a whole-file validity assertion must still "
                 "fail on them -- that is the correct signal."),
    }
    MARKER.write_text(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False),
                      encoding="utf-8", newline="\n")
    print(json.dumps({k: v for k, v in result.items() if k != "recovered_from_nul_pad"},
                     indent=2, sort_keys=True, ensure_ascii=False))
    print("\nrecovered %d padded records -> %s" % (len(recovered), quarantine))
    print("left in place: %d torn, %d empty" % (len(untouched_torn), empties))
    return 0


def _parses(line: str) -> bool:
    try:
        json.loads(line)
        return True
    except ValueError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
