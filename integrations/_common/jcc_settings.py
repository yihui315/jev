"""Settings-file merge engine for agent hook installers (P2).

Doing the config surgery in Python (not PowerShell) avoids Windows PowerShell
5.1 ``ConvertTo-Json`` array-collapse pitfalls and gives us:

  * timestamped backup BEFORE any write (ADR-0006 / safe-ops);
  * JSON parse-validate BEFORE and AFTER the write;
  * PRESERVE all pre-existing user config + user hooks;
  * a single, greppable JCC marker set (see ``JCC_ARTIFACTS``) so uninstall
    removes ONLY JCC-owned entries (never user entries);
  * idempotency (re-install does not duplicate);
  * WorkBuddy/CodeBuddy: NEVER registers a ``PermissionRequest`` hook (P2 rule).

CLI (exit non-zero on failure):
  python jcc_settings.py install   --agent workbuddy --settings <path> [--jcc-root <repo>]
  python jcc_settings.py uninstall --agent workbuddy --settings <path>
  python jcc_settings.py status    --agent workbuddy --settings <path>
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

#: A hook entry is JCC-owned iff its JSON contains ANY of these tokens.
#:
#: WorkBuddy/CodeBuddy commands embed ``jcc_emit.py``; Claude Code (and Codex)
#: commands point at JCC wrapper scripts whose filenames are JCC-namespaced. A
#: single token is NOT enough: the Claude hook command is the wrapper path
#: ``...\jcc-claude-hook.cmd``, which does NOT contain ``jcc_emit.py`` -- matching
#: only that token made Claude installs non-idempotent AND non-reversible (found
#: by SRE gate Harbor). Coarse but safe: every token is JCC-namespaced, so a user
#: hook cannot collide, and anything that matches is definitely JCC's.
JCC_ARTIFACTS = (
    "jcc_emit.py",
    "jcc_receiver.py",
    "jcc-claude-hook.cmd",
    "jcc-codex-notify",
)
#: Backwards-compat alias (docs / other tools import this name).
JCC_MARKER = JCC_ARTIFACTS[0]

#: Repository root, derived from this file's location rather than hardcoded --
#: the same idiom as ``jcc_core/config.py``. ``jcc_settings.py`` lives at
#: ``<repo>/integrations/_common/jcc_settings.py``, so two ``parents`` steps up
#: is the repo root. This value becomes the ``--jcc-root`` default, i.e. it is
#: baked into the hook command written into an agent's settings.json, so a
#: literal naming a drive that no longer exists silently installs a hook that
#: can never run. Deriving it makes the written command follow the checkout.
REPO_ROOT = Path(__file__).resolve().parents[2]

#: ``JCC_ROOT`` is still honoured as the operator override; only the fallback is
#: derived. An explicit ``--jcc-root`` on the command line still wins over both
#: (argparse default), so the installer scripts keep full control.
DEFAULT_JCC_ROOT = os.environ.get("JCC_ROOT") or str(REPO_ROOT)

#: P2 telemetry events only. PermissionRequest is deliberately excluded.
TELEMETRY_EVENTS: Dict[str, List[str]] = {
    "workbuddy": [
        "SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse",
        "PostToolUse", "Stop", "Notification", "PreCompact",
    ],
    "claude": [
        "SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse",
        "PostToolUse", "Stop", "Notification", "PreCompact",
    ],
}

FORBIDDEN_EVENTS = {"PermissionRequest"}


def posix(path: str) -> str:
    p = str(path).replace("\\", "/")
    if len(p) > 1 and p[1] == ":":
        p = "/" + p[0].lower() + p[2:]
    return p


def default_hook_command(agent: str, jcc_root: str) -> str:
    """Shell command the agent runs for every hooked event.

    WorkBuddy/CodeBuddy enforce Git Bash on Windows -> POSIX python invocation.
    Claude Code on Windows is shell-ambiguous -> a .cmd wrapper that works from
    both cmd.exe and Git Bash.
    """
    root = Path(jcc_root)
    if agent == "workbuddy":
        py = posix(str(root / ".venv" / "Scripts" / "python.exe"))
        emit = posix(str(root / "integrations" / "_common" / "jcc_emit.py"))
        return f'"{py}" "{emit}" --agent workbuddy'
    if agent == "claude":
        return f'"{root / "integrations" / "claude" / "jcc-claude-hook.cmd"}"'
    raise ValueError(f"no hook command template for agent '{agent}'")


def _load(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    # newline="" -> never translate CRLF/LF; we must not silently rewrite a
    # user's line endings on install/uninstall.
    # utf-8-sig -> accept a UTF-8 BOM (some editors write one) instead of
    # failing with JSONDecodeError (P2 security gate finding C2).
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        text = fh.read().strip()
    if not text:
        return {}
    data = json.loads(text)  # validate
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return data


def _save(path: Path, data: Dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".jcc-tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    with open(tmp, "r", encoding="utf-8", newline="") as fh:
        json.loads(fh.read())  # re-validate before swap
    os.replace(tmp, path)


def backup(path: Path) -> Optional[Path]:
    if not path.exists():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dst = path.with_name(f"{path.name}.jcc-backup-{stamp}")
    shutil.copy2(path, dst)
    return dst


def _entry_is_jcc(entry: Dict[str, Any]) -> bool:
    """True iff the entry references a JCC-owned artifact (any JCC token)."""
    blob = json.dumps(entry)
    return any(token in blob for token in JCC_ARTIFACTS)


def _jcc_entry(agent: str, command: str, matcher: Optional[str]) -> Dict[str, Any]:
    hook: Dict[str, Any] = {"type": "command", "command": command}
    entry: Dict[str, Any] = {"hooks": [hook]}
    if matcher is not None:
        entry["matcher"] = matcher
    return entry


def install(agent: str, settings_path: Path, jcc_root: str, command: Optional[str] = None) -> Dict[str, Any]:
    if agent not in TELEMETRY_EVENTS:
        raise ValueError(f"agent '{agent}' has no settings-hook installer")
    cmd = command or default_hook_command(agent, jcc_root)
    events = TELEMETRY_EVENTS[agent]
    # P2 forbids PermissionRequest hooking. This is a RELEASE GATE, so it must
    # be a real raise and not an `assert`: `python -O` strips asserts, and this
    # module is reachable from installer/integrator entrypoints that may be run
    # optimised. Measured on this host, before the fix, with a real agent whose
    # event list had been made to include PermissionRequest:
    #
    #   python     -> AssertionError: P2 forbids PermissionRequest hooking
    #   python -O  -> NO EXCEPTION (gate did not hold)
    #
    # Under -O the install proceeded. Nothing was written in that particular
    # case ONLY because the loop below also skips forbidden events -- so the
    # bypass was silent rather than loud, which is worse: the guard that exists
    # to make P2 non-negotiable reported success while doing nothing. Any future
    # change to that loop would have turned it into a written PermissionRequest
    # hook, i.e. exactly the actuation-adjacent capability P2 removes.
    #
    # The error MESSAGE is preserved verbatim so anything matching on it (tests,
    # logs, an integrator's error handling) is unaffected.
    if FORBIDDEN_EVENTS & set(events):
        raise ValueError("P2 forbids PermissionRequest hooking")

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    data = _load(settings_path)
    bak = backup(settings_path)

    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
    user_hook_before = sum(
        1 for entries in hooks.values() for e in (entries or []) if not _entry_is_jcc(e)
    )

    added: List[str] = []
    kept_jcc: List[str] = []
    for ev in events:
        if ev in FORBIDDEN_EVENTS:
            continue
        entries = hooks.get(ev)
        if not isinstance(entries, list):
            entries = []
        # drop any prior JCC entry (idempotent), keep user entries
        cleaned = [e for e in entries if not (isinstance(e, dict) and _entry_is_jcc(e))]
        matcher = "*" if ev in ("PreToolUse", "PostToolUse") else None
        cleaned.append(_jcc_entry(agent, cmd, matcher))
        if len(cleaned) != len(entries):
            kept_jcc.append(ev)
        else:
            added.append(ev)
        hooks[ev] = cleaned

    data["hooks"] = hooks
    _save(settings_path, data)

    user_hook_after = sum(
        1 for entries in data.get("hooks", {}).values()
        for e in (entries or []) if not _entry_is_jcc(e)
    )
    return {
        "action": "install",
        "agent": agent,
        "settings_path": str(settings_path),
        "backup": str(bak) if bak else None,
        "events_added": sorted(added),
        "events_refreshed": sorted(kept_jcc),
        "permission_request_hooked": False,
        "user_hooks_preserved": user_hook_before == user_hook_after,
        "user_hook_count": user_hook_after,
        "command": cmd,
    }


def uninstall(agent: str, settings_path: Path, *, restore_backup: bool = False) -> Dict[str, Any]:
    if not settings_path.exists():
        return {"action": "uninstall", "agent": agent, "removed": 0, "note": "settings file absent"}
    if restore_backup:
        baks = sorted(settings_path.parent.glob(f"{settings_path.name}.jcc-backup-*"))
        if baks:
            shutil.copy2(baks[-1], settings_path)
            return {"action": "uninstall", "agent": agent, "restored": str(baks[-1])}

    data = _load(settings_path)
    bak = backup(settings_path)
    hooks = data.get("hooks") or {}
    removed = 0
    for ev in list(hooks.keys()):
        entries = hooks.get(ev) or []
        cleaned = []
        for e in entries:
            if isinstance(e, dict) and _entry_is_jcc(e):
                removed += 1
            else:
                cleaned.append(e)
        if cleaned:
            hooks[ev] = cleaned
        else:
            del hooks[ev]
    remaining = sum(len(v or []) for v in hooks.values())
    if remaining == 0:
        data.pop("hooks", None)
    else:
        data["hooks"] = hooks
    _save(settings_path, data)
    return {
        "action": "uninstall",
        "agent": agent,
        "removed_entries": removed,
        "remaining_user_hook_entries": remaining,
        "backup": str(bak) if bak else None,
    }


def status(agent: str, settings_path: Path) -> Dict[str, Any]:
    if not settings_path.exists():
        return {"agent": agent, "settings_path": str(settings_path), "exists": False, "jcc_installed": False}
    data = _load(settings_path)
    hooks = data.get("hooks") or {}
    jcc_events = [ev for ev, entries in hooks.items()
                  if any(isinstance(e, dict) and _entry_is_jcc(e) for e in (entries or []))]
    user_entries = [e for entries in hooks.values() for e in (entries or [])
                    if isinstance(e, dict) and not _entry_is_jcc(e)]
    return {
        "agent": agent,
        "settings_path": str(settings_path),
        "exists": True,
        "jcc_installed": bool(jcc_events),
        "jcc_events": sorted(jcc_events),
        "user_hook_entries": len(user_entries),
        "permission_request_hooked": "PermissionRequest" in jcc_events,
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["install", "uninstall", "status"])
    ap.add_argument("--agent", required=True, choices=["workbuddy", "claude"])
    ap.add_argument("--settings", required=True)
    ap.add_argument("--jcc-root", default=DEFAULT_JCC_ROOT)
    ap.add_argument("--command", default=None)
    ap.add_argument("--restore-backup", action="store_true")
    args = ap.parse_args(argv)

    path = Path(args.settings)
    try:
        if args.action == "install":
            result = install(args.agent, path, args.jcc_root, args.command)
        elif args.action == "uninstall":
            result = uninstall(args.agent, path, restore_backup=args.restore_backup)
        else:
            result = status(args.agent, path)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"action": args.action, "agent": args.agent, "ok": False, "error": str(exc)}))
        return 1
    print(json.dumps({"ok": True, **result}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
