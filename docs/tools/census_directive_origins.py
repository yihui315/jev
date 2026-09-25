"""Census of the ``directives`` corpus (ADR-0011 evidence table; closes KD-004).

METHOD -- and this is the whole point of the file. The first draft of ADR-0011
measured "0 unrecognised rows" by matching files NAMED ``jcc.db``, which silently
excluded every other store the tree holds (``m_*.db``, ``live.db``, ``det.db``,
stores under ``artifacts/runtime``). That number was refuted and withdrawn.

So:

* the walk matches **every** ``*.db`` file, on the extension, not on the name;
* every file is opened **read-only** (``file:...?mode=ro&uri=1``) -- a census
  that mutates the corpus it is counting is not a census;
* a file that cannot be read is **reported as an error**, never skipped: an
  unreadable file is precisely the case that would make "0 unattributable rows"
  a false claim, and the correct answer for that file is "unknown", not "zero";
* a store in WAL mode can have **committed rows in its ``-wal`` sibling that the
  main file does not contain**. Sibling sizes are recorded, because a non-empty
  sibling is a caveat on any count taken of the main file alone;
* every run is stamped with wall-clock time and is a SNAPSHOT: the corpus is
  written concurrently by other people's test runs, so two runs one minute
  apart legitimately differ.

Usage::

    python docs/tools/census_directive_origins.py \
        --root F:/jev --out .workbuddy/scratch/kd004-census.json

Exit code is 0 even when unattributable rows are found: the census MEASURES,
it does not judge. ``--strict`` makes unattributable rows a non-zero exit so a
gate can use it as a gate.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from typing import Any, Dict, List

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir, os.pardir, "packages"))

#: Sibling files sqlite keeps next to a store. ``-wal`` is the one that matters:
#: committed-but-uncheckpointed rows live there and not in the main file.
_SIBLING_SUFFIXES = ("-wal", "-shm", "-journal")


def enumerate_stores(root: str) -> List[str]:
    """Every ``*.db`` under ``root``, by EXTENSION -- never by filename."""
    found: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            if name.lower().endswith(".db"):
                found.append(os.path.join(dirpath, name))
    return sorted(found)


def siblings_of(db_path: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for suffix in _SIBLING_SUFFIXES:
        side = db_path + suffix
        out[suffix] = os.path.getsize(side) if os.path.exists(side) else 0
    return {k: v for k, v in out.items() if v}


def inspect(db_path: str) -> Dict[str, Any]:
    """Read ONE store. Never writes. Unknowns are reported as unknowns."""
    rec: Dict[str, Any] = {
        "db_path": db_path,
        "bytes": os.path.getsize(db_path),
        "mtime": os.path.getmtime(db_path),
        "readable": False,
        "error": None,
        "tables": [],
        "has_directives": False,
        "origin_column": None,      # True/False, or None when no directives table
        "directive_rows": 0,
        "null_priors": 0,           # rows an F1-less writer would have produced
        "stored_origins": None,     # Counter of non-NULL origins
        "unattributable": [],       # ids directive_plane_of() cannot place
        "siblings": siblings_of(db_path),
    }
    try:
        from jcc_core.state_store import directive_plane_of
    except Exception as exc:  # pragma: no cover - environment failure
        rec["error"] = f"could not import directive_plane_of: {exc!r}"
        return rec
    try:
        uri = f"file:{db_path.replace(chr(92), '/')}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=30.0)
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "ORDER BY name"
            ).fetchall()
            rec["tables"] = [r[0] for r in rows]
            rec["readable"] = True
            if "directives" not in rec["tables"]:
                return rec
            rec["has_directives"] = True
            cols = [r[1] for r in conn.execute("PRAGMA table_info(directives)")]
            rec["origin_column"] = "origin" in cols
            rec["directive_rows"] = conn.execute(
                "SELECT COUNT(*) FROM directives").fetchone()[0]
            if not rec["origin_column"]:
                # PRE-F1: the column is absent, so every row is a NULL prior and
                # the clause-1 predicate is not even evaluable on this store.
                rec["null_priors"] = rec["directive_rows"]
            else:
                rec["null_priors"] = conn.execute(
                    "SELECT COUNT(*) FROM directives WHERE origin IS NULL"
                ).fetchone()[0]
                rec["stored_origins"] = dict(
                    conn.execute(
                        "SELECT origin, COUNT(*) FROM directives "
                        "WHERE origin IS NOT NULL GROUP BY origin"
                    ).fetchall()
                )
            for (did,) in conn.execute("SELECT directive_id FROM directives"):
                if directive_plane_of(did or "") is None:
                    rec["unattributable"].append(did)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        rec["error"] = f"{type(exc).__name__}: {exc}"
    except OSError as exc:
        rec["error"] = f"{type(exc).__name__}: {exc}"
    return rec


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=r"F:/jev")
    ap.add_argument("--out", default=None,
                    help="write the full per-file census here (JSON)")
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero if any unattributable row is found")
    args = ap.parse_args(argv)

    started = time.time()
    paths = enumerate_stores(args.root)
    records = [inspect(p) for p in paths]

    pre_f1 = [r for r in records if r["has_directives"] and r["origin_column"] is False]
    post_f1 = [r for r in records if r["has_directives"] and r["origin_column"] is True]
    no_directives = [r for r in records if not r["has_directives"]]
    unreadable = [r for r in records if not r["readable"]]
    unattached = [r for r in records if r["unattributable"]]

    summary = {
        "tool": "docs/tools/census_directive_origins.py",
        "method": "walk every *.db by extension (never by filename); open "
                  "read-only; report unreadable files as errors, not zeros",
        "root": args.root,
        "run_started_at": started,
        "run_finished_at": time.time(),
        "files_matched": len(records),
        "files_named_jcc_db": sum(1 for p in paths if os.path.basename(p) == "jcc.db"),
        "stores_with_directives": len(pre_f1) + len(post_f1),
        "pre_f1_stores_no_origin_column": len(pre_f1),
        "post_f1_stores_origin_column": len(post_f1),
        "stores_without_directives_table": len(no_directives),
        "unreadable_files": len(unreadable),
        "directive_rows_total": sum(r["directive_rows"] for r in records),
        "null_priors_total": sum(r["null_priors"] for r in records),
        "stored_origin_rows": sum(sum(v.values()) for v in
                                  (r["stored_origins"] or {} for r in records)),
        "stores_with_unattributable_rows": len(unattached),
        "unattributable_rows_total": sum(len(r["unattributable"]) for r in records),
        "distinct_unattributable_ids": sorted({d for r in unattached
                                               for d in r["unattributable"]}),
        "wal_siblings_nonempty": sum(1 for r in records if "-wal" in r["siblings"]),
        "stores_visited": paths,
    }
    payload = {"summary": summary, "stores": records}

    if args.out:
        parent = os.path.dirname(os.path.abspath(args.out))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=1, sort_keys=True)

    print(json.dumps({k: v for k, v in summary.items()
                      if k != "stores_visited"}, indent=2, sort_keys=True))
    print(f"-- sidecar: {args.out}")

    if unreadable:
        print(f"-- UNREADABLE ({len(unreadable)}):")
        for r in unreadable:
            print(f"   {r['db_path']}: {r['error']}")
    if unattached:
        print(f"-- UNATTRIBUTABLE id shapes of this run:")
        for d in summary["distinct_unattributable_ids"]:
            print(f"   {d!r}")

    if args.strict and (unattached or unreadable):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
