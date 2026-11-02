"""Does the missing `synchronous` pragma in connections.py explain the SECOND 30 s?

THE PREDICTION. `wal_stall_shape.py` settled the shape of the stall: only the FIRST
WAL close in any process costs ~30 s (30.13 s, then 0.13 / 0.16 / 0.12 / 0.12),
and synchronous=FULL never pays it at all (5 closes, 0.52 s total). So one process
should cost ~30 s, not ~63 s.

But the server needs ~63 s, and it opens two databases. If the second 30 s is real
it has to come from somewhere, and the most likely candidate is visible in the
product:

    state_store.py:375-376   PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL
    connections.py:179       PRAGMA journal_mode=WAL        <-- no synchronous

`wal_stall_shape.py` confirmed the second one directly: reading
`connections.py`'s source for a synchronous pragma returns "NONE FOUND", while
`StateStore` reports `synchronous: 1` (NORMAL) after construction. So the two JCC
databases are NOT configured alike, and `connections.py` inherits whatever SQLite
defaults to on this build.

WHETHER THAT MATTERS IS THE QUESTION, and it is not answerable by reading. SQLite
3.53.1 may well default to FULL, in which case `connections.db` is already fast and
the second 30 s is somewhere else entirely. So this script does not assume -- it
measures the two configurations the product actually creates, in one process, in
the order the product creates them:

    arm 1: both NORMAL   (state_store as written + a NORMAL sibling)
    arm 2: NORMAL, then DEFAULT (state_store as written + connections.py as written)

If arm 2's second close is fast, the missing pragma is harmless and the 63 s needs
a different explanation. If arm 2's second close is also ~30 s, then the two
databases genuinely differ in cost and the pragma is the lever.

A third arm sets FULL on both, because that is the configuration the measurement
already showed to be free -- it is the candidate fix, and it should be measured in
the same shape as the arms it is meant to beat rather than asserted.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
WORK = REPO / "runtime" / "harbor_second_stall"
OUT = REPO / "artifacts" / "release" / "v1.3_final" / "wal_second_stall.json"

# What SQLite uses when the caller does not say -- measured, not looked up.
DEFAULT_SYNC = sqlite3.connect(":memory:").execute("PRAGMA synchronous").fetchone()[0]


def open_close(tag: str, idx: int, sync: str | None) -> float:
    d = WORK / tag
    d.mkdir(parents=True, exist_ok=True)
    p = d / ("%s_%d.db" % (tag, idx))
    for suf in ("", "-wal", "-shm"):
        stale = os.fspath(p) + suf
        if os.path.exists(stale):
            os.remove(stale)
    conn = sqlite3.connect(str(p), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    if sync is not None:
        conn.execute("PRAGMA synchronous=%s" % sync)
    effective = conn.execute("PRAGMA synchronous").fetchone()[0]
    conn.execute("CREATE TABLE t(x)")
    conn.commit()
    t0 = time.time()
    conn.close()
    dt = round(time.time() - t0, 2)
    print("  %-28s close %6.2fs  (synchronous=%s)" % ("%s[%d]" % (tag, idx), dt, effective))
    return dt


def arm(tag: str, syncs: list[str | None]) -> dict:
    print("%s:" % tag)
    times = [open_close(tag, i, s) for i, s in enumerate(syncs)]
    return {"syncs_requested": syncs, "close_times_s": times,
            "total_s": round(sum(times), 2)}


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    out: dict = {
        "sqlite_version": sqlite3.sqlite_version,
        "sqlite_default_synchronous": DEFAULT_SYNC,
        "note": ("synchronous: 1=NORMAL 2=FULL (3=EXTRA). The product sets NORMAL "
                 "in state_store.py:376 and sets NO synchronous pragma in "
                 "connections.py:179, so that connection inherits this default."),
    }
    # Each arm runs in its OWN process-worth of state, but they share this one
    # process -- so arm order matters and is reported. The first WAL close in a
    # process is the expensive one, so the arm that goes first pays it.
    out["arm1_both_NORMAL"] = arm("both_normal", ["NORMAL", "NORMAL"])
    out["arm2_NORMAL_then_default"] = arm("normal_default", ["NORMAL", None])
    out["arm3_both_FULL"] = arm("both_full", ["FULL", "FULL"])

    a2 = out["arm2_NORMAL_then_default"]["close_times_s"]
    out["findings"] = {
        "default_synchronous_is_FULL": DEFAULT_SYNC == 2,
        "second_close_slow_with_default": len(a2) > 1 and a2[1] > 5,
        "arm3_both_FULL_total_s": out["arm3_both_FULL"]["total_s"],
    }
    if out["findings"]["second_close_slow_with_default"]:
        out["interpretation"] = (
            "The missing synchronous pragma in connections.py IS a candidate: the "
            "second close is slow only when the sibling is DEFAULT. Setting both to "
            "FULL is the candidate fix.")
    else:
        out["interpretation"] = (
            "The missing pragma is NOT the cause: the second close is fast when the "
            "sibling uses SQLite's default. One process still pays ~30 s total, so "
            "the server's 63 s is NOT two first-WAL stalls and needs another "
            "explanation -- this script did not find it.")
    OUT.write_text(json.dumps(out, indent=2, sort_keys=True, ensure_ascii=False),
                   encoding="utf-8", newline="\n")
    print(json.dumps({k: v for k, v in out.items() if k != "note"},
                     indent=2, sort_keys=True, ensure_ascii=False)[:2500])
    print("\nINTERPRETATION: %s" % out["interpretation"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
