"""WorkBuddy agent adapter — REAL probe + AEP normalizer (P2).

Verified facts on this host (2026, Windows, ``C:\\Users\\Administrator``):
  * ``~/.workbuddy/settings.json`` EXISTS and is the EFFECTIVE config: the host
    exports ``CODEBUDDY_CONFIG_DIR=%USERPROFILE%\\.workbuddy``, which relocates
    the CLI's user-settings dir from the default ``~/.codebuddy`` to
    ``~/.workbuddy``. ``~/.codebuddy/settings.json`` and
    ``~/.workbuddy-ai/settings.json`` do NOT exist.
  * Native hook schema = "CodeBuddy hooks" (shipped in ``hooks.md`` of the
    installed CLI): ``settings.json -> hooks -> {EventName: [{matcher?, hooks:[{type,command,...}]}]}``.
  * Hook input arrives as JSON on stdin and always carries ``session_id``
    (primary identity) + ``cwd`` (metadata only) + ``hook_event_name``.

P2 SCOPE RULE: telemetry only for
``SessionStart, SessionEnd, UserPromptSubmit, PreToolUse, PostToolUse, Stop,
Notification, PreCompact``. **PermissionRequest is deliberately NOT hooked** so
WorkBuddy's native GUI permission flow is untouched.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from ..redaction import redact
from ..schemas import AEPEnvelope, AgentName, EventType
from ._common import (
    STATUS_ADAPTER_DISABLED,
    STATUS_CONNECTED,
    STATUS_UNVERIFIED,
    UnidentifiableEvent,
    delivery_id,
    evidence_bundle,
    now_ts,
    pick,
    stable_event_id,
)
from .base import AgentAdapter

#: Telemetry events we are allowed to hook in P2 (PermissionRequest excluded).
TELEMETRY_EVENTS = [
    "SessionStart",
    "SessionEnd",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "Stop",
    "Notification",
    "PreCompact",
]

#: CodeBuddy hook event -> AEP coarse event_type.
_EVENT_TYPE_MAP: Dict[str, EventType] = {
    "SessionStart": EventType.SESSION_START,
    "SessionEnd": EventType.HEARTBEAT,
    "UserPromptSubmit": EventType.AGENT_MESSAGE,
    "PreToolUse": EventType.TOOL_CALL,
    "PostToolUse": EventType.TOOL_CALL,
    "Stop": EventType.HEARTBEAT,
    "Notification": EventType.HEARTBEAT,
    "PreCompact": EventType.HEARTBEAT,
}

_JCC_MARKER = "jcc_emit.py"


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def candidate_config_paths() -> list[Path]:
    """Ordered candidates, most-authoritative first (read-only probe)."""
    home = _home()
    out: list[Path] = []
    cfg_dir = os.environ.get("CODEBUDDY_CONFIG_DIR") or os.environ.get("WORKBUDDY_CONFIG_DIR")
    if cfg_dir:
        out.append(Path(cfg_dir) / "settings.json")
    out.append(home / ".workbuddy-ai" / "settings.json")   # legacy / spec candidate
    out.append(home / ".codebuddy" / "settings.json")      # CLI default when no override
    out.append(home / ".workbuddy" / "settings.json")      # observed effective here
    # de-dup preserving order
    seen, res = set(), []
    for p in out:
        key = str(p).lower()
        if key not in seen:
            seen.add(key)
            res.append(p)
    return res


def effective_config_path() -> Optional[Path]:
    """The single config the installer is allowed to touch, or None."""
    for p in candidate_config_paths():
        if p.exists():
            return p
    return None


class WorkBuddyAdapter(AgentAdapter):
    agent = AgentName.WORKBUDDY
    hook_schema_kind = "codebuddy-hooks"  # matcher + hooks[] (type: command|prompt)

    async def probe(self) -> Dict[str, Any]:
        cfg = effective_config_path()
        app_dir = _home() / ".workbuddy" / "app"
        sessions_dir = _home() / ".workbuddy" / "sessions"

        installed = bool(cfg) or app_dir.exists()
        version = os.environ.get("WORKBUDDY_APP_VERSION")
        if not version and app_dir.exists():
            rv = app_dir / "renderer-version.json"
            if rv.exists():
                try:
                    version = json.loads(rv.read_text(encoding="utf-8")).get("version")
                except Exception:  # noqa: BLE001
                    version = None
        if not version and sessions_dir.exists():
            for f in sorted(sessions_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:5]:
                try:
                    version = json.loads(f.read_text(encoding="utf-8")).get("version")
                    if version:
                        break
                except Exception:  # noqa: BLE001
                    continue

        hook_installed = False
        js_hooks: Dict[str, Any] = {}
        if cfg and cfg.exists():
            try:
                data = json.loads(cfg.read_text(encoding="utf-8"))
                js_hooks = data.get("hooks") or {}
                hook_installed = any(
                    _JCC_MARKER in json.dumps(entry)
                    for entries in js_hooks.values()
                    for entry in (entries or [])
                )
            except Exception:  # noqa: BLE001
                js_hooks = {}

        if installed and cfg and cfg.exists():
            status, verified = STATUS_CONNECTED, True
        elif installed:
            status, verified = STATUS_ADAPTER_DISABLED, False
        else:
            status, verified = STATUS_UNVERIFIED, False

        self.verified = verified
        return {
            "agent": "workbuddy",
            "installed": installed,
            "version": version,
            "config_path": str(cfg) if cfg else None,
            "config_dir_env": os.environ.get("CODEBUDDY_CONFIG_DIR"),
            "candidate_paths": [{"path": str(p), "exists": p.exists()} for p in candidate_config_paths()],
            "hook_schema_kind": self.hook_schema_kind,
            "hook_installed": hook_installed,
            "installed_hook_events": sorted(js_hooks.keys()),
            "telemetry_events_supported": TELEMETRY_EVENTS,
            "permission_request_hooked": False,   # P2: must stay False
            "session_source": "hook stdin JSON (session_id present)",
            "verified": verified,
            "status": status,
            "note": (
                "effective settings.json located; CodeBuddy-compatible hooks schema"
                if verified
                else "no parseable settings.json on host"
            ),
        }

    async def normalize(self, raw: Dict[str, Any]) -> AEPEnvelope:
        source = raw.get("_source") or "hook"

        if source == "session_file":
            return self._normalize_session_file(raw)

        native_event = pick(raw, "hook_event_name", "event", "event_name") or "unknown"
        session_id = pick(raw, "session_id", "sessionId")
        if not session_id:
            # P2 rule: no session_id -> do not enter the state store.
            raise UnidentifiableEvent(
                "workbuddy event has no session_id; refusing cwd-only identity"
            )

        event_type = _EVENT_TYPE_MAP.get(str(native_event), EventType.UNKNOWN)
        tool = pick(raw, "tool_name")
        native_id = pick(raw, "event_id", "uuid")
        ev_id = stable_event_id(
            "workbuddy", native_id, session_id, native_event, tool,
            pick(raw, "timestamp", "client_timestamp"),
        )
        payload = {
            "native_event": native_event,
            "permission_mode": pick(raw, "permission_mode"),
            "tool_name": tool,
            "notification_type": pick(raw, "notification_type"),
            "trigger": pick(raw, "trigger"),
            "source": pick(raw, "source"),
            "reason": pick(raw, "reason"),
            "stop_hook_active": pick(raw, "stop_hook_active"),
        }
        if tool:
            payload["tool_input"] = redact(pick(raw, "tool_input") or {})
        if raw.get("tool_response") is not None:
            payload["tool_response"] = redact(raw["tool_response"])
        payload = {k: v for k, v in payload.items() if v is not None}

        evidence = evidence_bundle(
            "workbuddy_hook",
            raw,
            ["hook_event_name", "session_id", "cwd", "tool_name", "notification_type", "trigger"],
        )

        return AEPEnvelope(
            event_id=ev_id,
            delivery_id=delivery_id(provided=raw.get("_delivery_id"), event_id=ev_id),
            session_id=str(session_id),
            agent=self.agent,
            project=str(pick(raw, "project") or "workbuddy"),
            cwd=pick(raw, "cwd"),
            timestamp=now_ts(pick(raw, "timestamp", "client_timestamp")),
            event_type=event_type,
            payload=payload,
            evidence=evidence,
            schema_ok=True,
        )

    def _normalize_session_file(self, raw: Dict[str, Any]) -> AEPEnvelope:
        """`~/.workbuddy/sessions/<pid>.json` process record (REAL observed shape)."""
        session_id = pick(raw, "sessionId", "session_id")
        if not session_id:
            raise UnidentifiableEvent("workbuddy session file has no sessionId")
        ev_id = stable_event_id("workbuddy", None, session_id, "session_file", raw.get("startedAt"))
        payload = {
            "native_event": "SessionFileHeartbeat",
            "pid": raw.get("pid"),
            "kind": raw.get("kind"),
            "mode": raw.get("mode"),
            "url": raw.get("url"),
            "agent_version": raw.get("version"),
            "os": raw.get("os"),
            "arch": raw.get("arch"),
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        return AEPEnvelope(
            event_id=ev_id,
            delivery_id=delivery_id(provided=raw.get("_delivery_id"), event_id=ev_id),
            session_id=str(session_id),
            agent=self.agent,
            project="workbuddy",
            cwd=pick(raw, "cwd"),
            timestamp=now_ts(pick(raw, "lastHeartbeat", "updatedAt", "startedAt")),
            event_type=EventType.HEARTBEAT,
            payload=payload,
            evidence=evidence_bundle("workbuddy_session_file", raw, ["sessionId", "cwd", "version", "kind"]),
            schema_ok=True,
        )
