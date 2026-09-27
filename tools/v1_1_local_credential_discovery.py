#!/usr/bin/env python
"""§7A local credential / document discovery — PRESENCE ONLY, never values.

Why this is in ``tools/`` and not in ``.workbuddy/scratch/``
-----------------------------------------------------------
Its output feeds a COMMITTED release artifact
(``artifacts/release/V1_1_REAL_JEV_VERIFICATION.json``). A script that produces
a tracked artifact but lives in an ignored directory makes that artifact
non-reproducible -- the exact defect this repo already fixed once for the
continuation harness. So the probe is tracked.

What it does NOT do
-------------------
It never reads a credential's value. Every result is one of:

* ``PRESENT``  -- the name is set; only a COARSE LENGTH BUCKET is reported
                  (``<16`` / ``16-63`` / ``>=64``), never a substring.
* ``ABSENT``   -- the name is not set in this process.
* ``SECURITY_BLOCKER`` -- a secret-shaped path is TRACKED in git. The content is
                  not read and not printed; the file listing is the finding.

For ``.env``-style files it lists KEY NAMES only: the values are dropped before
anything is returned, so a value cannot leak through this module even by
accident. There is a self-check in ``assert_no_secret_escaped()`` that scans the
finished report for the live values of every credential variable it knows about
-- a leak would have to survive that too.

Usage
-----
    F:/jev/.venv/Scripts/python.exe tools/v1_1_local_credential_discovery.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "packages") not in sys.path:
    sys.path.insert(0, str(REPO / "packages"))

#: Where the raw dump goes. This is SCRATCH, deliberately: raw probe output is
#: not release evidence (only the curated summary is), and anything under
#: ``artifacts/release/**`` is tracked -- writing there would dirty the worktree
#: and void an authoritative verdict.
OUT = REPO / ".workbuddy" / "scratch" / "v11" / "local_credential_discovery.json"

# --- credential-shaped env var names we care about ------------------------- #

_CRED_ENV_NAMES = [
    # Jev / TypeSafe (the v1.1 target)
    "JEV_API_KEY",
    "TYPESAFE_API_KEY",
    "TYPESAFE_TOKEN",
    "TYPESAFE_API_TOKEN",
    "JEV_TOKEN",
    # JCC control-plane
    "JCC_CONTROL_TOKEN",
    "JCC_DIRECTIVE_SIGNING_KEY",
    # Agent-side (never printed; presence only)
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
]

# --- local config files that might carry a credential ---------------------- #

_CRED_FILE_CANDIDATES = [
    ".env",
    "config/.env.example",
    "config/.env",
    ".workbuddy/settings.json",
    "C:/Users/Administrator/.workbuddy/settings.json",
    "C:/Users/Administrator/.claude/settings.json",
    "C:/Users/Administrator/.codex/config.toml",
    "C:/Users/Administrator/.hermes/config.yaml",
    # TypeSafe / Jev SDK conventions
    "C:/Users/Administrator/.typesafe/config",
    "C:/Users/Administrator/.typesafe/credentials",
    "C:/Users/Administrator/.config/typesafe/config.json",
    "C:/Users/Administrator/AppData/Roaming/typesafe/config.json",
]

# --- secret-shaped path globs (must not be tracked) ----------------------- #

_SECRET_PATH_PATTERNS = [
    re.compile(r"(^|/)\.env$"),
    re.compile(r"(^|/)\.env\.(?!example)"),
    re.compile(r"\.api_key$"),
    re.compile(r"\.tok$"),
    re.compile(r"credentials", re.IGNORECASE),
    re.compile(r"auth\.json$", re.IGNORECASE),
    re.compile(r"\.(key|pem|pfx|p12|keystore|jks)$", re.IGNORECASE),
    re.compile(r"id_(rsa|ed25519)$"),
    re.compile(r"\.sandbox-secrets$"),
    re.compile(r"^fixtures/v1\.1/.*real_session_"),
]


def _length_bucket(value: str) -> str:
    n = len(value)
    if n < 16:
        return "<16"
    if n < 64:
        return "16-63"
    return ">=64"


def env_presence() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for name in _CRED_ENV_NAMES:
        raw = os.environ.get(name)
        if raw is None:
            out[name] = {"state": "ABSENT"}
        elif raw == "":
            out[name] = {"state": "PRESENT", "note": "set but EMPTY"}
        else:
            out[name] = {"state": "PRESENT", "length_bucket": _length_bucket(raw)}
    return out


def file_presence() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for cand in _CRED_FILE_CANDIDATES:
        p = Path(cand) if Path(cand).is_absolute() else (REPO / cand)
        if p.exists():
            out[cand] = {"state": "PRESENT", "is_file": p.is_file(),
                         "bytes": (p.stat().st_size if p.is_file() else None)}
        else:
            out[cand] = {"state": "ABSENT"}
    return out


def _key_names_only(path: Path) -> List[str]:
    """KEY NAMES only from a KEY=VALUE file. Values are dropped here, once."""
    names = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            names.append(line.split("=", 1)[0].strip())
        elif ":" in line:
            names.append(line.split(":", 1)[0].strip())
    return sorted(set(names))


def dotenv_key_scan() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for cand in [".env", "config/.env", "config/.env.example"]:
        p = REPO / cand
        if p.is_file():
            out[cand] = {"state": "PRESENT", "key_names": _key_names_only(p)}
        else:
            out[cand] = {"state": "ABSENT"}
    return out


def git_tracked_secret_scan() -> Dict[str, Any]:
    try:
        files = subprocess.check_output(
            ["git", "-C", str(REPO), "ls-files"], text=True,
            stderr=subprocess.DEVNULL).splitlines()
    except (OSError, subprocess.SubprocessError) as exc:
        return {"state": "UNKNOWN", "note": f"git ls-files failed: {exc}"}

    hits = []
    for f in files:
        norm = f.replace("\\", "/")
        for pat in _SECRET_PATH_PATTERNS:
            if pat.search(norm):
                hits.append(f)
                break
    if hits:
        return {
            "state": "SECURITY_BLOCKER",
            "note": ("secret-shaped path is TRACKED in git; content NOT read, NOT "
                     "printed. Migrate the secret out of the repo and rotate it "
                     "before proceeding."),
            "tracked_secret_shaped_paths": hits,
        }
    return {"state": "CLEAN", "tracked_file_count": len(files)}


def assert_no_secret_escaped(report: Dict[str, Any]) -> None:
    """Falsifier for this module's own promise.

    "I only printed buckets" is a claim; this measures it. Every credential
    variable that is actually set in this process has its live value searched
    for in the serialised report -- so a future edit that starts echoing a value
    fails loudly here rather than leaking quietly into a committed artifact.
    """
    blob = json.dumps(report, sort_keys=True, ensure_ascii=False)
    for name in _CRED_ENV_NAMES:
        val = os.environ.get(name)
        if not val:
            continue
        if val in blob:
            raise SystemExit(
                f"SECRET LEAK: the live value of {name} appears verbatim in the "
                f"report. Refusing to emit. This is a defect in the probe, not a "
                f"reason to redact by hand."
            )


def environment_report() -> Dict[str, Any]:
    report = {
        "schema": "jcc-v1.1-local-credential-discovery/1",
        "repo": str(REPO),
        "env_presence": env_presence(),
        "file_presence": file_presence(),
        "dotenv_key_names": dotenv_key_scan(),
        "git_tracked_secret_scan": git_tracked_secret_scan(),
        "note": ("PRESENT / ABSENT / SECURITY_BLOCKER only. Values are NEVER "
                 "printed. Length buckets are coarse; no value substring is "
                 "emitted anywhere in this report."),
    }
    assert_no_secret_escaped(report)
    return report


def main() -> None:
    report = environment_report()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))


if __name__ == "__main__":
    main()
