"""WorkBuddy launch driver — capture a REAL WorkBuddy session as an AEP event.

WHAT THIS IS
------------
The bridge between "the operator pressed Launch" and "a real WorkBuddy process
produced real telemetry". It does NOT invent an event: it discovers a LIVE
WorkBuddy session on this host from the host's own session registry
(``~/.workbuddy/sessions/<pid>.json`` -- the file the WorkBuddy runtime writes and
heartbeats itself) and hands that record to the adapter.

WHY A SESSION FILE AND NOT A HOOK PAYLOAD
-----------------------------------------
The two real telemetry channels on this host are:
  1. **native hooks** — the ``jcc_emit.py`` hook command (``integrations/``).
     Verified on this host: hooks are NOT installed in
     ``~/.workbuddy/settings.json`` (``hooks == {}``), so a hook-driven launch
     would have no producer. Claiming otherwise would be a fake integration.
  2. **session registry** — ``~/.workbuddy/sessions/<pid>.json``, written and
     heartbeat-refreshed by the WorkBuddy runtime for every live session.
     Verified present and live on this host.

This driver uses channel 2 because it is the channel that is ACTUALLY LIVE here,
and it reads a file the WorkBuddy process itself owns -- it is not a synthetic
payload. The adapter (``WorkBuddyAdapter._normalize_session_file``) is the piece
that turns that record into an AEP envelope, so the normalisation contract is the
production one, not a driver-local shortcut.

LIVENESS IS MEASURED, NOT ASSUMED
---------------------------------
``is_live()`` requires BOTH:
  * the file's ``lastHeartbeat`` to be recent (the WorkBuddy runtime beats it),
  * the recorded ``pid`` to be a live process (read-only Win32 probe).
A stale file for a dead process is NOT a launch. The driver reports which
condition failed so a caller can tell "no WorkBuddy running" from "WorkBuddy is
running but has not beaten recently" -- two different problems.

NO ACTUATION. This module reads files and returns a dict. It starts nothing,
kills nothing, and holds no actuator.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

#: A session file is considered live when the runtime beat it within this window.
#: WorkBuddy's own heartbeat cadence is sub-second (observed ~0.5-1 s); 120 s is
#: deliberately generous so a momentarily busy host is not reported as dead, while
#: still never accepting a file from a previous boot.
HEARTBEAT_FRESH_SECONDS = 120.0


def _pid_alive(pid: int) -> bool:
    """Read-only Windows liveness probe (never delivers a signal).

    Mirrors ``jcc_core.run._pid_alive`` and ``tests/e2e/conftest.pid_alive``: on
    Windows ``os.kill(pid, 0)`` would TerminateProcess, so the check uses
    OpenProcess + GetExitCodeProcess. Returns False for any unusable pid rather
    than raising, because "cannot determine" and "not alive" must not be
    conflated by a caller that only sees a bool -- callers that need the
    distinction read the ``reason`` the driver returns instead.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            STILL_ACTIVE = 259
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.GetExitCodeProcess.restype = wintypes.BOOL
            kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE,
                                                    ctypes.POINTER(wintypes.DWORD)]
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            try:
                code = wintypes.DWORD()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return code.value == STILL_ACTIVE
                return False
            finally:
                kernel32.CloseHandle(handle)
        except Exception:  # noqa: BLE001
            return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def sessions_dir() -> Path:
    cfg = os.environ.get("CODEBUDDY_CONFIG_DIR") or os.environ.get("WORKBUDDY_CONFIG_DIR")
    base = Path(cfg) if cfg else Path(os.path.expanduser("~")) / ".workbuddy"
    return base / "sessions"


def _now_ms() -> float:
    return time.time() * 1000.0


def list_sessions() -> List[Dict[str, Any]]:
    """Every parseable session record, newest heartbeat first. Read-only."""
    d = sessions_dir()
    out: List[Dict[str, Any]] = []
    if not d.is_dir():
        return out
    for f in d.glob("*.json"):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 -- a torn read while the writer flushes
            continue
        if not isinstance(rec, dict) or not rec.get("sessionId"):
            continue
        rec["_file"] = str(f)
        out.append(rec)
    out.sort(key=lambda r: float(r.get("lastHeartbeat") or 0), reverse=True)
    return out


def liveness(record: Dict[str, Any]) -> Dict[str, Any]:
    """Three-state liveness: live / stale / dead, with the reason.

    Deliberately NOT a bool. A caller must be able to tell
      * ``dead``   — the pid is gone (WorkBuddy exited)
      * ``stale``  — the pid lives but has not beaten (hung / wedged / paused)
      * ``live``   — pid alive AND heartbeat fresh
    Collapsing ``stale`` into ``dead`` would hide a hung WorkBuddy behind "not
    running", which is exactly the kind of silent-unknown this project bans.
    """
    hb = float(record.get("lastHeartbeat") or 0)
    age_ms = (_now_ms() - hb) if hb else None
    fresh = age_ms is not None and age_ms <= HEARTBEAT_FRESH_SECONDS * 1000.0
    alive = _pid_alive(record.get("pid") or 0)
    if not alive:
        state, reason = "dead", "pid %s is not alive" % record.get("pid")
    elif not fresh:
        state, reason = "stale", (
            "pid %s is alive but last heartbeat was %.1fs ago (> %.0fs)"
            % (record.get("pid"), (age_ms or 0) / 1000.0, HEARTBEAT_FRESH_SECONDS))
    else:
        state, reason = "live", "pid %s alive, heartbeat %.1fs ago" % (
            record.get("pid"), (age_ms or 0) / 1000.0)
    return {"state": state, "reason": reason, "pid_alive": alive,
            "heartbeat_age_ms": age_ms, "fresh": bool(fresh)}


def find_live_session(*, cwd: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The best live WorkBuddy session, optionally preferring one in ``cwd``.

    "Best" = live, then freshest heartbeat, then matching cwd. Returns None when
    nothing is live -- the caller must treat that as "cannot launch", never as
    "launch with a placeholder".
    """
    best = None
    best_key = None
    for rec in list_sessions():
        lv = liveness(rec)
        if lv["state"] != "live":
            continue
        cwd_match = bool(cwd) and str(rec.get("cwd") or "").lower() == str(cwd).lower()
        key = (1 if cwd_match else 0, float(rec.get("lastHeartbeat") or 0))
        if best_key is None or key > best_key:
            best, best_key = rec, key
    return best


def capture_launch_event(*, cwd: Optional[str] = None) -> Dict[str, Any]:
    """Build the raw event the adapter will normalise, or explain why not.

    Returns ``{"ok": True, "event": {...}, "source": {...}}`` on success, or
    ``{"ok": False, "reason": ..., "detail": ...}`` when no live session exists.
    The caller (the mission runtime) must not proceed on ``ok == False``.

    SCOPE STRICTNESS (B4). When a ``cwd`` is requested, the captured session MUST
    actually belong to that cwd. ``find_live_session`` treats ``cwd`` as a
    *preference* and would otherwise fall back to the freshest live session in a
    DIFFERENT tree -- which is exactly the scope escape the launch template's
    ``--add-dir`` / ``cwd`` binding is meant to prevent: a launch would be bound
    to a directory the WorkBuddy process never ran in, and the AEP event would
    claim work happened where it did not. Refuse with
    ``WORKBUDDY_SESSION_SCOPE_MISMATCH`` rather than mis-scope. Callers that do
    not pass ``cwd`` keep the preference behaviour (no scope is requested, so no
    scope can be violated).
    """
    rec = find_live_session(cwd=cwd)
    if rec is None:
        sessions = list_sessions()
        if not sessions:
            return {"ok": False, "reason": "no_workbuddy_session",
                    "detail": "no WorkBuddy session files under %s" % sessions_dir(),
                    "sessions_dir": str(sessions_dir()), "session_count": 0}
        states = [{"sessionId": s.get("sessionId"), **liveness(s)} for s in sessions[:5]]
        return {"ok": False, "reason": "no_live_workbuddy_session",
                "detail": "found %d session(s), none live" % len(sessions),
                "sessions_dir": str(sessions_dir()),
                "session_count": len(sessions), "observed": states}

    # B4 -- strict cwd scope. Requested cwd has no live session of its own, so
    # the only live session we could capture is scoped to a DIFFERENT directory.
    if cwd is not None:
        rec_cwd = str(rec.get("cwd") or "")
        if rec_cwd.lower() != str(cwd).lower():
            return {
                "ok": False,
                "reason": "WORKBUDDY_SESSION_SCOPE_MISMATCH",
                "detail": (
                    "requested cwd=%r has no live WorkBuddy session; the only "
                    "live session is scoped to cwd=%r, so capturing it would "
                    "mis-bind a launch to a directory it never ran in"
                    % (cwd, rec.get("cwd"))),
                "sessions_dir": str(sessions_dir()),
                "requested_cwd": cwd,
                "captured_cwd": rec.get("cwd"),
                "session_id": rec.get("sessionId"),
                "liveness": liveness(rec),
            }

    # The event handed to the adapter is the REAL record, tagged so the adapter
    # takes the session-file normalisation branch instead of the hook branch.
    event = dict(rec)
    event["_source"] = "session_file"
    event.pop("_file", None)
    return {
        "ok": True,
        "event": event,
        "source": {
            "kind": "workbuddy_session_file",
            "file": rec.get("_file"),
            "session_id": rec.get("sessionId"),
            "pid": rec.get("pid"),
            "version": rec.get("version"),
            "liveness": liveness(rec),
        },
    }
