"""Codex rollout observer (P2) - the VERIFIED Codex telemetry path.

Codex writes one JSON object per line to
``~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<session_uuid>.jsonl``. Only the
first line (``type=session_meta``) carries ``session_id``; every other record
belongs to that same session. This observer tails new lines, attaches the
resolved ``_session_id``, and forwards each record through the Codex adapter.

Identity rule: a record with no resolvable session id is NEVER ingested (the
adapter raises UnidentifiableEvent and the emitter spools it).

Usage:
  python codex_rollout_observer.py --once            # drain new lines, exit
  python codex_rollout_observer.py                    # run continuously
  python codex_rollout_observer.py --once --dry-run   # print envelopes only
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, Iterator, Optional, Tuple

#: Repository root, derived from this file's location rather than hardcoded --
#: the same idiom as ``jcc_core/config.py``. ``codex_rollout_observer.py`` lives
#: at ``<repo>/integrations/codex/``, so two ``parents`` steps up is the repo
#: root. The literal this replaced pointed at a drive that no longer exists,
#: which made the ``jcc_emit`` import below fail at LOAD time and took the whole
#: (VERIFIED) Codex rollout telemetry path down with it.
REPO_ROOT = Path(__file__).resolve().parents[2]

#: ``JCC_ROOT`` remains the operator override; only the fallback is derived.
JCC_ROOT = os.environ.get("JCC_ROOT") or str(REPO_ROOT)
_PACKAGES = str(Path(JCC_ROOT) / "packages")
_INTEG = str(Path(JCC_ROOT) / "integrations" / "_common")
for _p in (_PACKAGES, _INTEG):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from jcc_emit import emit_event  # noqa: E402

_UUID_RE = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$")


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", str(Path(os.path.expanduser("~")) / ".codex")))


def state_path() -> Path:
    d = Path(os.environ.get("JCC_STATE_DIR", str(Path(os.path.expanduser("~")) / ".jcc")))
    d.mkdir(parents=True, exist_ok=True)
    return d / "codex_observer_state.json"


def session_id_from_name(path: Path) -> Optional[str]:
    m = _UUID_RE.search(path.stem)
    return m.group(1) if m else None


def iter_new_records() -> Iterator[Dict[str, object]]:
    """Yield raw rollout records that have not been seen yet (offset tracked)."""
    try:
        state: Dict[str, int] = json.loads(state_path().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        state = {}

    sessions = codex_home() / "sessions"
    if not sessions.exists():
        return
    for f in sorted(sessions.rglob("rollout-*.jsonl")):
        key = str(f)
        offset = int(state.get(key, 0))
        try:
            size = f.stat().st_size
        except OSError:
            continue
        if size < offset:      # file truncated/rotated -> restart
            offset = 0
        if size == offset:
            continue
        with open(f, "r", encoding="utf-8", errors="replace") as fh:
            fh.seek(offset)
            first = True
            file_session = session_id_from_name(f)
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                # resolve session id (session_meta wins)
                sid = None
                if isinstance(rec, dict):
                    if rec.get("type") == "session_meta":
                        sid = (rec.get("payload") or {}).get("session_id") or (rec.get("payload") or {}).get("id")
                    if sid:
                        file_session = sid
                rec["_session_id"] = file_session
                rec["_source"] = "rollout"
                yield rec
                first = False
            state[key] = fh.tell()
    state_path().write_text(json.dumps(state), encoding="utf-8")


def run_once(dry_run: bool = False) -> Dict[str, int]:
    counts = {"emitted": 0, "dropped": 0, "spooled": 0, "sent": 0}
    for rec in iter_new_records():
        if dry_run:
            from jcc_emit import normalize_event

            try:
                print(json.dumps(normalize_event("codex", dict(rec)), ensure_ascii=False))
                counts["emitted"] += 1
            except Exception as exc:  # noqa: BLE001
                counts["dropped"] += 1
            continue
        res = emit_event("codex", rec)
        if res["status"] == "dropped":
            counts["dropped"] += 1
        elif res["status"] == "spooled":
            counts["spooled"] += 1
        else:
            counts["sent"] += 1
        counts["emitted"] += 1
    return counts


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--interval", type=float, default=5.0)
    args = ap.parse_args(argv)
    try:
        if args.once:
            print(json.dumps(run_once(args.dry_run)))
            return 0
        while True:
            print(json.dumps(run_once(args.dry_run)), flush=True)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
