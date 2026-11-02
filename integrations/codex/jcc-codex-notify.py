"""Codex `notify` chaining helper (P2, opt-in, DEGRADED/UNVERIFIED path).

Codex invokes the configured `notify` program with the event JSON appended as the
FINAL argument (payload shape: {"type":"agent-turn-complete",...}). This helper:

  1. forwards that JSON to the JCC core, best-effort (never raises, never blocks);
  2. re-invokes the user's ORIGINAL notify program (recorded in the sidecar written
     by jcc_codex_config.py install --force) with the SAME JSON appended, so the
     user's pre-existing behaviour is preserved exactly;
  3. ALWAYS exits 0.

Invocation (as written by the installer):
  <python> jcc-codex-notify.py "<json>"

This path is OPT-IN and UNVERIFIED: the default Codex integration never touches
config.toml and uses the rollout-jsonl observer instead. Known caveat: when the
wrapper is reached through `cmd.exe /c` (the installer's notify value), cmd.exe
argument parsing can mangle the embedded double quotes of the JSON; the helper
therefore tolerates a missing/garbled payload by spooling the raw text.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

#: Repository root, derived from this file's location rather than hardcoded --
#: the same idiom as ``jcc_core/config.py``. This file lives at
#: ``<repo>/integrations/codex/``, so two ``parents`` steps up is the repo root.
#: The literal this replaced pointed at a drive that no longer exists, so both
#: injected paths were dead and ``from jcc_emit import emit_event`` failed --
#: silently, because this wrapper is required to swallow every error.
REPO_ROOT = Path(__file__).resolve().parents[2]

#: ``JCC_ROOT`` remains the operator override; only the fallback is derived.
DEFAULT_ROOT = os.environ.get("JCC_ROOT") or str(REPO_ROOT)
_PACKAGES = str(Path(DEFAULT_ROOT) / "packages")
_COMMON = str(Path(DEFAULT_ROOT) / "integrations" / "_common")
for _p in (_COMMON, _PACKAGES):
    if _p not in sys.path:
        sys.path.insert(0, _p)

JCC_TOKENS = ("jcc-codex-notify", "jcc_emit.py")
DEFAULT_SIDECAR = Path(os.path.expanduser("~")) / ".codex" / "config.toml.jcc-notify-original.json"


def _split_args(argv: List[str]) -> tuple[Optional[str], str]:
    """Return (sidecar_path, payload). Payload = the final non-flag argument."""
    sidecar: Optional[str] = None
    rest: List[str] = []
    i = 0
    while i < len(argv):
        if argv[i] == "--sidecar" and i + 1 < len(argv):
            sidecar = argv[i + 1]
            i += 2
            continue
        rest.append(argv[i])
        i += 1
    payload = rest[-1] if rest else ""
    return sidecar, payload


def _forward(payload: str) -> None:
    """Best-effort forward to JCC; swallow every error."""
    try:
        text = payload.strip()
        if not text:
            return
        raw = json.loads(text)
        from jcc_emit import emit_event  # noqa: WPS433 (path-injected)

        emit_event("codex", dict(raw))
    except Exception:  # noqa: BLE001
        try:
            from jcc_emit import spool  # noqa: WPS433

            spool("codex", {"kind": "notify_raw", "payload": payload[:2000]})
        except Exception:  # noqa: BLE001
            pass


def _chain(sidecar: Optional[str], payload: str) -> None:
    """Re-invoke the user's original notify program (never blocks)."""
    path = Path(sidecar) if sidecar else DEFAULT_SIDECAR
    try:
        if not path.exists():
            return
        original = json.loads(path.read_text(encoding="utf-8")).get("notify") or []
        if not isinstance(original, list) or not original:
            return
        if any(any(tok in str(a) for tok in JCC_TOKENS) for a in original):
            return  # never recurse into ourselves
        subprocess.run([str(a) for a in original] + [payload], check=False, timeout=15)
    except Exception:  # noqa: BLE001
        pass


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        sidecar, payload = _split_args(args)
        if not payload:
            payload = sys.stdin.read() if not sys.stdin.isatty() else ""
        _forward(payload)
        _chain(sidecar, payload)
    except Exception:  # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
