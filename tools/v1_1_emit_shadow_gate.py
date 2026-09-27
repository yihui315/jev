#!/usr/bin/env python
"""§19 v1.1 live-shadow gate — EMIT the actuation verdict, read-only.

WHAT THIS IS
    §19 requires that when the live-shadow observation window is unmet the system
    EMITS ``PENDING_LIVE_SHADOW_GATE`` with ``actuation_enabled=false`` rather
    than quietly staying silent about it. This script produces that emission as a
    release artifact, computed from the REAL database.

HOW IT READS THE DATABASE
    Read-only and ``immutable=1``. The runtime database is used by the protected
    v1.0 service, and an artifact emitter has no business taking a write lock on
    it — ``immutable=1`` tells SQLite the file cannot change, so no lock is taken
    and no ``-shm``/``-wal`` side file is created. If the file is missing
    entirely, that is not an error: the gate reports the tape as absent, which is
    the same finding as an uninitialised database.

WHAT IS DELIBERATELY NOT IN THE REPRODUCIBILITY DIGEST
    ``evaluated_at`` and the absolute database path. ``evaluated_at`` is a clock
    reading, so including it would make ``--check`` fail on every invocation; a
    check that always fails is worse than no check, because it teaches the
    operator to ignore it (the same lesson as §10's live record hashes). The
    digest is taken over the NORMALISED document, with those keys stripped
    recursively by name at any depth, and ``--check`` compares normalised forms
    end to end.

Usage
    F:/jev/.venv/Scripts/python.exe tools/v1_1_emit_shadow_gate.py
    F:/jev/.venv/Scripts/python.exe tools/v1_1_emit_shadow_gate.py --check
    F:/jev/.venv/Scripts/python.exe tools/v1_1_emit_shadow_gate.py --db <path>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parents[1]
# ``packages`` (not the individual package dirs) belongs on the path: the modules
# are imported as ``jcc_core.*`` / ``supervisor.*``, which is how the production
# wiring and the A8 gate import them.
_PKGS = str(REPO / "packages")
if _PKGS not in sys.path:
    sys.path.insert(0, _PKGS)

from jcc_core.config import load_config                     # noqa: E402
from supervisor import actuation_levels as AL                # noqa: E402
from supervisor import live_shadow_gate as LG                # noqa: E402

OUT_REL = "artifacts/release/V1_1_LIVE_SHADOW_GATE.json"
OUT = REPO / OUT_REL

#: Stripped (recursively, by key name at ANY depth) before digesting. The
#: ``reproducibility`` block is included because it CONTAINS the digest: leaving
#: it in would make the committed document and the freshly derived one differ by
#: construction on every ``--check``.
VOLATILE_KEYS = frozenset(
    {"evaluated_at", "generated_at", "db_path", "db_uri", "reproducibility"}
)


def _normalize(node: Any) -> Any:
    """Recursively drop volatile keys, so the digest is stable across runs."""
    if isinstance(node, dict):
        return {
            k: _normalize(v)
            for k, v in sorted(node.items())
            if k not in VOLATILE_KEYS
        }
    if isinstance(node, list):
        return [_normalize(v) for v in node]
    return node


def _digest(document: Dict[str, Any]) -> str:
    blob = json.dumps(_normalize(document), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _open(conn_target: Path) -> sqlite3.Connection:
    """A read-only, immutable connection, or an in-memory one when absent.

    An ABSENT FILE is deliberately routed through the SAME gate call as a present
    one, by handing the gate an in-memory database that has no shadow table. That
    is not a trick to make the answer come out right — it is the only way the
    verdict is produced by the gate's own code path instead of by a second
    special case here, where the two could drift apart.

    ``immutable=1`` on the real file matters: the runtime database is used by the
    protected v1.0 service, and an artifact emitter has no business taking a
    write lock on it or creating ``-shm``/``-wal`` side files.
    """
    if not conn_target.is_file():
        return sqlite3.connect(":memory:")
    uri = "file:%s?mode=ro&immutable=1" % conn_target.as_posix()
    return sqlite3.connect(uri, uri=True)


def derive(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Evaluate the gate against the real database and build the document."""
    cfg = load_config()
    target = Path(db_path) if db_path is not None else Path(cfg.state_store_db_path)
    file_present = target.is_file()
    configured_level = AL.resolve_configured_level(cfg, {})
    configured_enabled = bool(getattr(cfg.actuation, "enabled", False))

    conn = _open(target)
    try:
        result = LG.evaluate_live_shadow_gate(
            conn,
            criteria=LG.LiveShadowGateCriteria(),
            configured_level=configured_level,
            configured_enabled=configured_enabled,
        )
        verdict = result.as_dict()
    finally:
        conn.close()

    return {
        "schema": "jcc.v1_1.live_shadow_gate/v1",
        "headline": {
            "status": verdict["status"],
            "actuation_enabled": verdict["actuation_enabled"],
            "effective_level": verdict["effective_level"],
            "configured_level": verdict["configured_level"],
            "granted_ceiling": verdict["granted_ceiling"],
            "satisfied": verdict["satisfied"],
        },
        "db": {
            "present": file_present,
            "db_path": target.as_posix(),
            "db_uri": "mode=ro&immutable=1",
        },
        "verdict": verdict,
        "actuation_ladder": list(AL.describe()),
        "gate_vocabulary": LG.describe(),
        "note": (
            "A PENDING gate with actuation_enabled=false is a LAWFUL, SAFE "
            "v1.1 completion. Reaching the observation threshold does not by "
            "itself enable effects: the criteria's granted_ceiling and the "
            "operator's enabled flag are two further, separate conditions."
        ),
    }


def _write(document: Dict[str, Any]) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    document = dict(document)
    document["reproducibility"] = {
        "normalized_sha256": _digest(document),
        "volatile_keys_stripped": sorted(VOLATILE_KEYS),
        "deterministic": True,
    }
    OUT.write_text(
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8", newline="\n",
    )


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="§19 v1.1 live-shadow gate artifact")
    ap.add_argument("--check", action="store_true",
                    help="re-derive and compare with the committed artifact; does not write")
    ap.add_argument("--db", default=None, help="database path override")
    args = ap.parse_args(argv)
    db_path = Path(args.db) if args.db else None

    if args.check:
        if not OUT.is_file():
            print("[gate] STALE: %s does not exist" % OUT_REL)
            return 1
        committed = json.loads(OUT.read_text(encoding="utf-8"))
        current = derive(db_path)
        if _normalize(committed) != _normalize(current):
            print("[gate] STALE: %s no longer matches the database" % OUT_REL)
            for key in ("headline", "db", "verdict"):
                if _normalize(committed.get(key)) != _normalize(current.get(key)):
                    print("  %s differs" % key)
            ac = (committed.get("headline") or {}).get("status")
            bc = (current.get("headline") or {}).get("status")
            if ac != bc:
                print("  status: committed=%r now=%r" % (ac, bc))
            return 1
        print("[gate] OK: %s is current (status=%s, actuation_enabled=%s)"
              % (OUT_REL, current["headline"]["status"],
                 current["headline"]["actuation_enabled"]))
        return 0

    document = derive(db_path)
    _write(document)
    head = document["headline"]
    print("[gate] wrote %s" % OUT_REL)
    print("  status              : %s" % head["status"])
    print("  actuation_enabled   : %s" % head["actuation_enabled"])
    print("  effective_level     : %s  (configured=%s, ceiling=%s)"
          % (head["effective_level"], head["configured_level"], head["granted_ceiling"]))
    print("  defects             : %s" % (document["verdict"]["defects"] or ["none"]))
    print("  observed_decisions  : %s" % document["verdict"]["observed_decisions"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
