"""Falsify or confirm the WAL hypothesis for the audit-chain threaded test.

THE MEASURED FAILURE. ``test_threaded_appends_keep_one_contiguous_chain`` fails
intermittently with::

    AssertionError: a writer thread failed: ['OperationalError: database is
    locked', 'OperationalError: database is locked']

Reproduced 2 failures in 6 runs on this host. The chain itself never forks --
``BEGIN IMMEDIATE`` (audit.py:156) is doing its job. What fails is a writer
WAITING for the write lock, despite the connection setting
``PRAGMA busy_timeout=30000``.

THE HYPOTHESIS. The test opens its own bare ``sqlite3.connect(path)``, which
defaults to ``journal_mode=DELETE``. Production ``StateStore`` opens with
``PRAGMA journal_mode=WAL`` (state_store.py:375). Under DELETE, a writer takes
an EXCLUSIVE lock for the whole commit and readers/writers serialise hard; under
WAL, writers still serialise but a busy waiter is woken on commit far more
promptly. So the test exercises a journal mode the product never uses, and the
30 s budget it does grant is spent against the wrong contention profile.

This script runs the SAME eight-thread / 25-append workload twice -- once in the
test's default journal mode, once in the production WAL mode -- and reports the
failure counts, so the claim is measured rather than argued. Non-vacuity: it
reports the journal mode each run actually got, and it fails loudly if either
run produced no rows.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))
from jcc_core import audit  # noqa: E402

THREADS = 8
PER_THREAD = 25

#: Scratch root, overridable. MEASURED HAZARD, and the reason this default is
#: NOT `tempfile.gettempdir()`: the WorkBuddy shim's sitecustomize.py overrides
#: `tempfile.gettempdir()` to D:\jev\.workbuddy\scratch\host_temp, and that tree
#: carries stale sqlite files whose handles are still open. A first attempt at
#: this script failed 14/16 appends with "attempt to write a readonly database"
#: -- an artefact of that shared, contended scratch, not of journal mode. Under
#: the gate the same mechanism is what produces A1_FREEZE_MODEL's 6 setup
#: ERRORs. So the falsifier is given its own directory and reports which one.
SCRATCH = os.environ.get("JCC_FALSIFY_SCRATCH") or os.path.join(
    str(Path(__file__).resolve().parents[1]), "runtime", "harbor_wal_falsify")


def one_round(path: str, journal: str | None) -> tuple[str, list[str], int]:
    """Run the workload once. Returns (journal_mode_used, errors, rows)."""
    for suffix in ("", "-wal", "-shm", "-journal"):
        stale = Path(path + suffix)
        if stale.exists():
            stale.unlink()
    seed = sqlite3.connect(path, isolation_level=None)
    try:
        seed.executescript(audit.SCHEMA)
        seed.commit()
        if journal:
            # Set the journal mode AFTER the schema exists and the seed
            # connection has committed, and read it back so a silently ignored
            # PRAGMA cannot pass as a clean result.
            got = seed.execute("PRAGMA journal_mode=%s" % journal).fetchone()[0]
            seed.commit()
            if got.lower() != journal.lower():
                raise SystemExit(
                    "VACUOUS: asked for journal_mode=%s, SQLite reported %r"
                    % (journal, got))
    finally:
        seed.close()

    errors: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(THREADS)
    modes: list[str] = []
    got = "unknown"

    def run(n: int) -> None:
        nonlocal got
        local: list[str] = []
        try:
            conn = sqlite3.connect(path, isolation_level=None)
            try:
                # The test's own setting, kept identical so the ONLY variable
                # under test is the journal mode.
                conn.execute("PRAGMA busy_timeout=30000")
                got = conn.execute("PRAGMA journal_mode").fetchone()[0]
                barrier.wait(timeout=120)
                for i in range(n):
                    audit.append_audit(
                        conn, event_id="t%d-%d" % (threading.get_ident(), i),
                        timestamp=float(i), session_id="s", agent="a", project="p")
            finally:
                conn.close()
        except BaseException as exc:  # noqa: BLE001
            local.append("%s: %s" % (type(exc).__name__, exc))
        with lock:
            errors.extend(local)
            modes.append(got)

    threads = [threading.Thread(target=run, args=(PER_THREAD,)) for _ in range(THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=300)

    reader = sqlite3.connect(path, isolation_level=None)
    try:
        rows = reader.execute("SELECT COUNT(*) FROM audit_tape").fetchone()[0]
        mode_used = reader.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        reader.close()
    return mode_used, errors, rows


def main() -> int:
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    out: dict = {"threads": THREADS, "per_thread": PER_THREAD, "rounds": rounds,
                 "scratch": SCRATCH, "gettempdir": tempfile.gettempdir()}
    root = Path(SCRATCH)
    root.mkdir(parents=True, exist_ok=True)
    for label, journal in (("default_DELETE", None), ("production_WAL", "WAL")):
        errs: Counter = Counter()
        modes, rowcounts = Counter(), Counter()
        for r in range(rounds):
            d = root / ("%s_%d.db" % (label, r))
            mode_used, errors, rows = one_round(str(d), journal)
            modes[mode_used] += 1
            rowcounts[rows] += 1
            for e in errors:
                errs["%s: %s" % (e.split(":")[0], e.split(": ", 1)[-1][:70])] += 1
        out[label] = {
            "rounds": rounds,
            "journal_modes_observed": dict(modes),
            "row_counts": dict(rowcounts),
            "errors": dict(errs),
            "rounds_with_all_rows": sum(
                1 for k, v in rowcounts.items() if k == THREADS * PER_THREAD),
        }
        print("[%s] modes=%s rows=%s errors=%s"
              % (label, dict(modes), dict(rowcounts), dict(errs) or "none"))
    Path(__file__).with_name("wal_falsify_result.json").write_text(
        json.dumps(out, indent=2, sort_keys=True), encoding="utf-8", newline="\n")
    print(json.dumps(out, indent=2, sort_keys=True))
    # Non-vacuity: a round that wrote fewer rows than threads*per_thread proves
    # the workload aborted, so the run measures contention, not correctness.
    for label in ("default_DELETE", "production_WAL"):
        if out[label]["rounds_with_all_rows"] == 0:
            print("VACUOUS: %s never completed a full round" % label)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
