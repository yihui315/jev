"""Codex config.toml notify editor (opt-in, reversible, P2).

Codex push telemetry uses the top-level ``notify = [ <program>, <args...> ]``
key. On this host that key is ALREADY owned by the user
(``codex-computer-use.exe turn-ended``), so the installer defaults to NOT
touching it and refuses to overwrite unless ``--force`` is given. When forced,
it chains: it writes a wrapper ``.cmd`` that forwards the notify JSON to JCC and
then invokes the ORIGINAL program, and stores the original value in a sidecar so
uninstall restores it exactly.

This is text-based TOML surgery (no external toml writer) with strict guards so
it can never silently destroy an unrelated config.

CLI:
  python jcc_codex_config.py status  --config <config.toml>
  python jcc_codex_config.py install --config <config.toml> --wrapper <path> [--force]
  python jcc_codex_config.py uninstall --config <config.toml>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

NOTIFY_RE = re.compile(r"^\s*notify\s*=\s*\[(?P<body>[^\]]*)\]\s*$", re.MULTILINE)
JCC_MARKER = "jcc-codex-notify"
SIDECAR_SUFFIX = ".jcc-notify-original.json"


def _iter_strings(body: str) -> List[str]:
    return re.findall(r"""['"]([^'"]*)['"]""", body)


def _backup(path: Path) -> Optional[Path]:
    if not path.exists():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dst = path.with_name(f"{path.name}.jcc-backup-{stamp}")
    shutil.copy2(path, dst)
    return dst


def status(config: Path) -> Dict[str, Any]:
    if not config.exists():
        return {"config": str(config), "exists": False}
    text = config.read_text(encoding="utf-8")
    m = NOTIFY_RE.search(text)
    vals = _iter_strings(m.group("body")) if m else []
    return {
        "config": str(config),
        "exists": True,
        "notify_present": bool(m),
        "notify_value": vals,
        "jcc_notify_installed": any(JCC_MARKER in v for v in vals),
        "user_notify_present": bool(m) and not any(JCC_MARKER in v for v in vals),
    }


def install(config: Path, wrapper: str, force: bool = False) -> Dict[str, Any]:
    if not config.exists():
        raise FileNotFoundError(config)
    text = config.read_text(encoding="utf-8")
    m = NOTIFY_RE.search(text)
    sidecar = config.with_name(config.name + SIDECAR_SUFFIX)

    if m:
        existing = _iter_strings(m.group("body"))
        if any(JCC_MARKER in v for v in existing):
            return {"ok": True, "note": "already installed", "notify_value": existing}
        if not force:
            return {
                "ok": False,
                "reason": "user_notify_present",
                "detail": f"existing notify={existing!r}; refusing to overwrite (pass --force to chain)",
            }
        sidecar.write_text(json.dumps({"notify": existing}), encoding="utf-8")

    bak = _backup(config)
    new_line = f'notify = [ "cmd.exe", "/c", "{wrapper}" ]'
    if m:
        text = NOTIFY_RE.sub(new_line, text, count=1)
    else:
        # insert after the first top-level key line (before any [table])
        lines = text.splitlines()
        idx = 0
        for i, ln in enumerate(lines):
            if ln.strip().startswith("["):
                break
            idx = i + 1
        lines.insert(idx, new_line)
        text = "\n".join(lines) + "\n"
    config.write_text(text, encoding="utf-8")
    return {
        "ok": True,
        "config": str(config),
        "backup": str(bak) if bak else None,
        "notify_value": ["cmd.exe", "/c", wrapper],
        "chained": bool(m),
    }


def uninstall(config: Path) -> Dict[str, Any]:
    if not config.exists():
        return {"ok": True, "note": "config absent"}
    text = config.read_text(encoding="utf-8")
    m = NOTIFY_RE.search(text)
    if not m or not any(JCC_MARKER in v for v in _iter_strings(m.group("body"))):
        return {"ok": True, "note": "no JCC notify entry present"}
    bak = _backup(config)
    sidecar = config.with_name(config.name + SIDECAR_SUFFIX)
    original = None
    if sidecar.exists():
        try:
            original = json.loads(sidecar.read_text(encoding="utf-8")).get("notify")
        except Exception:  # noqa: BLE001
            original = None
    if original:
        repl = 'notify = [ ' + ", ".join(f'"{v}"' for v in original) + " ]"
        text = NOTIFY_RE.sub(repl, text, count=1)
    else:
        text = NOTIFY_RE.sub("", text, count=1)
    config.write_text(text, encoding="utf-8")
    return {"ok": True, "restored": original, "backup": str(bak) if bak else None}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["status", "install", "uninstall"])
    ap.add_argument("--config", required=True)
    ap.add_argument("--wrapper", default=None)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    cfg = Path(args.config)
    try:
        if args.action == "status":
            res = status(cfg)
        elif args.action == "install":
            if not args.wrapper:
                raise ValueError("--wrapper required for install")
            res = install(cfg, args.wrapper, force=args.force)
        else:
            res = uninstall(cfg)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    print(json.dumps(res, indent=2))
    return 0 if res.get("ok", True) else 2


if __name__ == "__main__":
    raise SystemExit(main())
