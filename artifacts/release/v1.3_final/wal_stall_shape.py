"""Is the 63 s stall ONE first-WAL cost, or TWO? And is it really fsync?

WHY THIS EXISTS. team-lead ruled the 63 s startup a BLOCKER for forge2 and wrote
the likely cause as "the first WAL establishment fsync", on the strength of my own
table. But that table does not actually support the fsync reading, and I am not
going to hand forge2 a hypothesis I can already contradict:

    WAL, synchronous=FULL   ->  0.09 s
    WAL, synchronous=NORMAL -> 30.1  s

synchronous=FULL fsyncs on EVERY commit; NORMAL does not. If the stall were fsync
cost, FULL would be the SLOW one. It is 300x faster. So "slow fsync" is backwards,
and "first WAL establishment" is contradicted by the other row of the same table:
the first WAL close in a process costs 30 s and the next four cost 0.11 s each --
that is ONE cost amortised, not two.

But "amortised once per process" predicts ~30 s total, and the server needs ~63 s
= 2 x 30. Those two facts cannot both be true. One of my two measurements is
wrong, and the difference decides what forge2 should look at:

  * if only the FIRST WAL close is slow, the 63 s needs another explanation;
  * if EVERY WAL close under synchronous=NORMAL is slow, then the fix is in the
    pragma, not in any warm-up -- and "pre-warm both DBs" (team-lead's chosen fix)
    would NOT help at all.

THIS SCRIPT SETTLES IT. It opens several WAL databases in one process under
NORMAL and under FULL, times every close, and reports the per-database series
rather than a single number. It also reads what each product connection actually
sets, because `connections.py:179` sets journal_mode=WAL and I did not see a
synchronous pragma beside it -- meaning it inherits SQLite's default, which is
FULL. If that is right, the two JCC databases are NOT configured alike, and a
fix aimed at "the WAL pragma" has to know which one it is changing.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
WORK = REPO / "runtime" / "harbor_stall_shape"
OUT = REPO / "artifacts" / "release" / "v1.3_final" / "wal_stall_shape.json"

N_DB = 5


def series(sync: str, tag: str) -> dict:
    """Open N_DB fresh WAL databases in THIS process; time every close."""
    d = WORK / tag
    d.mkdir(parents=True, exist_ok=True)
    times, mids = [], []
    for i in range(N_DB):
        p = d / ("%s_%d.db" % (tag, i))
        for suf in ("", "-wal", "-shm"):
            # os.fspath, not `p + suf`: p is a Path and Path + str is a TypeError.
            # (My own scripting slip, caught by running the script rather than
            # re-reading it -- which is the only reason this is a 1s fix.)
            stale = os.fspath(p) + suf
            if os.path.exists(stale):
                os.remove(stale)
        conn = sqlite3.connect(str(p), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=%s" % sync)
        mids.append(conn.execute("PRAGMA synchronous").fetchone()[0])
        conn.execute("CREATE TABLE t(x)")
        conn.commit()
        t0 = time.time()
        conn.close()
        times.append(round(time.time() - t0, 2))
    return {"requested_sync": sync, "effective_sync_observed": sorted(set(mids)),
            "close_times_s": times, "total_s": round(sum(times), 2)}


def what_the_product_sets() -> dict:
    """Read the two JCC connections' actual pragma state, post-construction."""
    out: dict = {}
    try:
        sys.path.insert(0, str(REPO / "packages"))
        from jcc_core.state_store import StateStore
        d = WORK / "product"
        d.mkdir(parents=True, exist_ok=True)
        store = StateStore(str(d / "jcc.db"))
        with store.connection() as c:
            out["state_store"] = {
                "journal_mode": c.execute("PRAGMA journal_mode").fetchone()[0],
                "synchronous": c.execute("PRAGMA synchronous").fetchone()[0],
            }
    except Exception as exc:                                   # noqa: BLE001
        out["state_store_error"] = "%s: %s" % (type(exc).__name__, exc)
    # connections.py sets journal_mode only; read what that leaves behind.
    try:
        from jcc_core import connections as C
        import inspect
        src = inspect.getsource(C)
        pragmas = [ln.strip() for ln in src.splitlines()
                   if "PRAGMA" in ln and "synchronous" in ln]
        out["connections_explicit_synchronous_pragma"] = pragmas or "NONE FOUND"
    except Exception as exc:                                   # noqa: BLE001
        out["connections_read_error"] = "%s: %s" % (type(exc).__name__, exc)
    return out


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    out: dict = {
        "question": ("is the 63s stall ONE amortised first-WAL cost, or one per "
                     "database? and is it fsync at all?"),
        "sqlite_version": sqlite3.sqlite_version,
        "series_NORMAL": series("NORMAL", "normal"),
        "series_FULL": series("FULL", "full"),
        "product_settings": what_the_product_sets(),
    }
    n = out["series_NORMAL"]["close_times_s"]
    f = out["series_FULL"]["close_times_s"]
    out["findings"] = {
        "every_close_slow_under_NORMAL": all(t > 5 for t in n),
        "only_first_slow_under_NORMAL": n[0] > 5 and all(t < 2 for t in n[1:]),
        "FULL_is_not_slower_than_NORMAL": sum(f) < sum(n),
        "NORMAL_total_vs_server_63s": out["series_NORMAL"]["total_s"],
    }
    out["interpretation"] = (
        "EVERY WAL close under synchronous=NORMAL is slow, not just the first: "
        "a parent-process warm-up cannot help and team-lead's chosen fix (pre-warm "
        "both DBs) would not reduce the 63s. The lever is the synchronous pragma."
        if out["findings"]["every_close_slow_under_NORMAL"] else
        "Only the FIRST WAL close is slow: the 30s is a one-time per-process cost, "
        "so the 63s needs a second explanation that this script did not find."
    )
    OUT.write_text(json.dumps(out, indent=2, sort_keys=True, ensure_ascii=False),
                   encoding="utf-8", newline="\n")
    print(json.dumps(out, indent=2, sort_keys=True, ensure_ascii=False)[:3500])
    print("\nINTERPRETATION: %s" % out["interpretation"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
