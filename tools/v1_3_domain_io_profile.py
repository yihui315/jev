#!/usr/bin/env python3
"""v1.3 — DOMAIN I/O PROFILE: what a domain operation actually costs, and why.

A MEASUREMENT TOOL, NOT A RELEASE GATE LEG.

It exists because the JCC v1.3 release quoted a number — "a mission creation
costs ~0.9 s on this host" — and a quoted number whose instrument is not in the
repository is not evidence. This is that instrument, and it is the one that
CORRECTED the previous wave: `docs/v1_3/WAVE_9_3_NO_SILENT_TRUNCATION.md` §3
attributed the cost to `sqlite3.Connection.close()`; §3a records the amendment
that this profile produced, and `docs/v1_3/WAVE_10_REAL_BROWSER_PERFORMANCE.md`
§8 is where the finding is written up.

WHAT IT MEASURES, IN THREE SECTIONS
-----------------------------------
A. A control with NO product code in it. Raw `sqlite3`, one table, four file
   sizes (12 KB -> 1.68 MB), three journal modes. Rows are reached with a single
   bulk transaction so the cost of *filling* never contaminates the cost of
   *committing*. This section carries the tool's only falsifiable claim:
   `COMMIT_IS_FLAT_IN_DATA_SIZE`.

B. The pragma decomposition. `DomainStore._conn()` re-issues
   `PRAGMA journal_mode=WAL`, `PRAGMA synchronous=NORMAL` and
   `PRAGMA foreign_keys=ON` on EVERY connection. This section prices each one
   against a bare `connect + close` on the SAME database, in the SAME process,
   so the difference is the pragma and not the file.

C. Whole store operations, through `DomainStore` itself, on a database holding
   `--missions` rows.

THE DURABILITY WARNING THIS TOOL PRINTS
---------------------------------------
`PRAGMA synchronous=NORMAL` is a *durability* setting, not a cosmetic one. This
tool does not recommend removing it. It prices it. A fix that trades durability
for latency is a product decision and must be made explicitly, with the
crash-consistency consequence written down — which is exactly why the fix
recorded in the release notes is connection REUSE (pay the pragma once per
process) and not pragma removal.

NOISE, STATED UP FRONT
----------------------
Medians here are taken over `--reps` samples and they are dominated by this
host's fsync variance. Five repetitions rank the costs but do not pin them; the
release notes report the ranking and the caveat together. Do not quote a single
figure from this tool without its spread.

Run:  F:/jev/.venv/Scripts/python.exe tools/v1_3_domain_io_profile.py
Exit: 0 = the structural claim held, 1 = it did not (the tool can fail).
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))

SCRATCH = ROOT / ".workbuddy" / "scratch"
SCRATCH.mkdir(parents=True, exist_ok=True)

#: The raw-control grid. The point of 40000 is the file size (~1.7 MB), not the
#: row count: the claim under test is that cost does not track size.
CONTROL_SIZES = (0, 500, 2000, 10000, 40000)
CONTROL_MODES = ("delete", "wal", "memory")
#: A commit at 12 KB versus a commit at ~1.7 MB. SQLite's commit touches changed
#: pages, so the ratio must stay small; 20x is a deliberately generous ceiling
#: that a genuine O(size) implementation could never pass.
FLATNESS_MAX_RATIO = 20.0


def med(xs: List[float]) -> float:
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else float("nan")


def spread(xs: List[float]) -> Dict[str, float]:
    xs = sorted(xs)
    if not xs:
        return {}
    return {"median": round(med(xs), 2), "min": round(xs[0], 2),
            "p90": round(xs[min(len(xs) - 1, int(len(xs) * 0.9))], 2),
            "max": round(xs[-1], 2)}


# --------------------------------------------------------------------------- #
# A. the control: no product code
# --------------------------------------------------------------------------- #
def section_control(reps: int) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    print("\nA. RAW SQLITE CONTROL (no product code in the loop)")
    for mode in CONTROL_MODES:
        tmp = Path(tempfile.mkdtemp(prefix="jcc_io_profile_ctl_"))
        db = tmp / ("ctl_%s.db" % mode)
        conn = sqlite3.connect(str(db))
        effective = conn.execute("PRAGMA journal_mode=%s" % mode).fetchone()[0]
        conn.execute("CREATE TABLE t(k TEXT PRIMARY KEY, v REAL)")
        conn.commit()
        rows: List[Dict[str, Any]] = []
        have = 0
        for n in CONTROL_SIZES:
            if n > have:
                conn.execute("BEGIN")
                conn.executemany("INSERT INTO t(k,v) VALUES(?,?)",
                                 [("k%09d" % i, float(i)) for i in range(have, n)])
                conn.commit()
                have = n
            com, con = [], []
            for r in range(reps):
                c = sqlite3.connect(str(db))
                t0 = time.perf_counter()
                c.execute("INSERT INTO t(k,v) VALUES(?,?)", ("z%d_%d" % (n, r), 0.0))
                c.commit()
                com.append((time.perf_counter() - t0) * 1000)
                t1 = time.perf_counter()
                c.close()
                con.append((time.perf_counter() - t1) * 1000)
            size = os.path.getsize(db)
            row = {"n": n, "size_bytes": size, "commit": spread(com),
                   "close": spread(con)}
            rows.append(row)
            print("   %-7s n=%-6d size=%-9d commit=%8.2f ms  close=%7.2f ms"
                  % (effective, n, size, med(com), med(con)))
        conn.close()
        out[mode] = {"journal_mode": effective, "rows": rows}
    return out


def judge_flatness(control: Dict[str, Any]) -> Dict[str, Any]:
    """The tool's one falsifiable claim: commit cost does not track file size."""
    detail: Dict[str, Any] = {}
    ok = True
    for mode, data in control.items():
        rows = data["rows"]
        small, big = rows[0], rows[-1]
        ratio = big["commit"]["median"] / max(1e-9, small["commit"]["median"])
        detail[mode] = {"size_small": small["size_bytes"],
                        "size_large": big["size_bytes"],
                        "commit_small_ms": small["commit"]["median"],
                        "commit_large_ms": big["commit"]["median"],
                        "size_ratio": round(big["size_bytes"] / max(1.0, small["size_bytes"]), 1),
                        "commit_ratio": round(ratio, 2),
                        "held": ratio < FLATNESS_MAX_RATIO}
        ok = ok and ratio < FLATNESS_MAX_RATIO
    return {"claim": "COMMIT_IS_FLAT_IN_DATA_SIZE",
            "max_ratio_allowed": FLATNESS_MAX_RATIO, "held": ok, "detail": detail}


# --------------------------------------------------------------------------- #
# B + C. the product's store
# --------------------------------------------------------------------------- #
def build_store_db(missions: int) -> tuple:
    """A store database holding ``missions`` rows, filled in bulk.

    Seeding through `create_mission` would cost ~0.9 s per row on this host and
    would make the profile take twenty minutes; the rows are inserted directly
    with one transaction instead. Section C then measures the STORE's own
    operations against that database, which is the thing under test.
    """
    from jcc_core.domain import DomainStore

    tmp = Path(tempfile.mkdtemp(prefix="jcc_io_profile_store_"))
    db = tmp / "profile.db"
    store = DomainStore(str(db))
    project = store.create_project("Profile Project", root_path=str(ROOT))

    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("BEGIN")
    # `status` must be the ENUM NAME, not the lowercase wire form: pydantic
    # validates this column on the way out, and 'draft' raises
    #   ValidationError: status Input should be 'DRAFT', 'READY', ...
    # which is how this line was caught -- by running the tool, not by reading it.
    conn.executemany(
        "INSERT INTO missions (mission_id, project_id, title, goal, agent, "
        "connection_ref, working_directory, context, permissions, budget_units, "
        "status, attention_kind, attention_reason, created_at, updated_at, "
        "started_at, finished_at, session_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [("msn_profile_%06d" % i, project.project_id, "profile mission %d" % i,
          "", "workbuddy", None, "", "", "[]", 0.0, "DRAFT", None, None,
          float(i), float(i), None, None, None) for i in range(missions)])
    conn.commit()
    conn.close()
    return store, project.project_id, db


def section_pragmas(db: Path, reps: int) -> Dict[str, Any]:
    print("\nB. WHAT THE PER-CONNECTION PRAGMAS COST (%d rows, %d reps)"
          % (sqlite3.connect(str(db)).execute(
              "SELECT COUNT(*) FROM missions").fetchone()[0], reps))
    series = {
        "connect+close": lambda: _conn_close(db),
        "connect+synchronous=NORMAL+close": lambda: _conn_close(db, ["synchronous=NORMAL"]),
        "connect+foreign_keys=ON+close": lambda: _conn_close(db, ["foreign_keys=ON"]),
        "connect+journal_mode=WAL+close": lambda: _conn_close(db, ["journal_mode=WAL"]),
        "connect+all three (the store's _conn)+close": lambda: _conn_close(
            db, ["journal_mode=WAL", "synchronous=NORMAL", "foreign_keys=ON"]),
        "the same, plus one SELECT": lambda: _conn_close(
            db, ["journal_mode=WAL", "synchronous=NORMAL", "foreign_keys=ON"], read=True),
    }
    out: Dict[str, Any] = {}
    for label, fn in series.items():
        xs = []
        for _ in range(reps):
            t0 = time.perf_counter()
            fn()
            xs.append((time.perf_counter() - t0) * 1000)
        out[label] = spread(xs)
        print("   %-44s %9.2f ms  (min %7.2f  max %8.2f)"
              % (label, med(xs), min(xs), max(xs)))
    return out


def _conn_close(db: Path, pragmas: List[str] = (), read: bool = False) -> None:
    c = sqlite3.connect(str(db))
    try:
        for p in pragmas:
            c.execute("PRAGMA %s" % p)
        if read:
            c.execute("SELECT 1 FROM projects LIMIT 1").fetchone()
    finally:
        c.close()


def section_ops(store, project_id: str, reps: int) -> Dict[str, Any]:
    print("\nC. WHOLE STORE OPERATIONS")
    out: Dict[str, Any] = {}
    ops = (("get_project", lambda: store.get_project(project_id)),
           ("list_missions(1000)", lambda: store.list_missions(limit=1000)),
           ("list_projects", lambda: store.list_projects()),
           ("domain_counts", lambda: store.domain_counts()),
           ("needs_attention", lambda: store.needs_attention()),
           ("create_mission", lambda: store.create_mission(
               project_id, "profile created mission")))
    for label, fn in ops:
        xs = []
        for _ in range(reps):
            t0 = time.perf_counter()
            fn()
            xs.append((time.perf_counter() - t0) * 1000)
        out[label] = spread(xs)
        print("   %-24s %9.2f ms  (min %7.2f  max %8.2f)"
              % (label, med(xs), min(xs), max(xs)))
    out["_note"] = ("create_mission opens and closes TWO connections "
                    "(get_project's existence check, then the insert), so it "
                    "pays the per-connection cost twice")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=11,
                    help="samples per series (5 ranks; 11 is the default here)")
    ap.add_argument("--missions", type=int, default=1000,
                    help="rows in the store database used by sections B and C")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_path = Path(args.out) if args.out else (
        SCRATCH / ("v1_3_domain_io_profile_%s.json" % stamp))

    payload: Dict[str, Any] = {
        "tool": "v1_3_domain_io_profile",
        "kind": "measurement, not a release gate leg",
        "reps": args.reps, "store_missions": args.missions,
        "measured_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "host_note": ("medians are dominated by this host's fsync variance; the "
                      "RANKING is the durable result, the individual figures are "
                      "not. See docs/v1_3/WAVE_10_REAL_BROWSER_PERFORMANCE.md "
                      "section 8."),
        "durability_warning": ("PRAGMA synchronous=NORMAL is a durability "
                               "setting. This tool prices it; it does not "
                               "recommend removing it. The recorded fix is "
                               "connection REUSE, not pragma removal."),
    }

    payload["control"] = section_control(args.reps)
    payload["claim_flatness"] = judge_flatness(payload["control"])

    store, project_id, db = build_store_db(args.missions)
    payload["store_db_bytes"] = os.path.getsize(db)
    payload["pragmas"] = section_pragmas(db, args.reps)
    payload["operations"] = section_ops(store, project_id, args.reps)

    payload["verdict"] = "HOLD" if payload["claim_flatness"]["held"] else "REFUTED"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8", newline="\n")

    print("\nclaim %s: %s"
          % (payload["claim_flatness"]["claim"], payload["verdict"]))
    print("wrote %s" % out_path)
    print(json.dumps({"verdict": payload["verdict"],
                      "claim": payload["claim_flatness"]["claim"],
                      "evidence": str(out_path)}))
    return 0 if payload["claim_flatness"]["held"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
