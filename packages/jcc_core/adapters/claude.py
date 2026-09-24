"""Claude Code agent adapter — REAL probe + AEP normalizer (P2).

Verified facts on this host (2026, Windows, ``C:\\Users\\Administrator``):
  * ``~/.claude/settings.json`` and ``~/.claude/settings.local.json`` EXIST.
  * ``claude --version`` -> ``2.1.196 (Claude Code)``; binary at
    ``~/.local/bin/claude.exe``.
  * Real observed Claude event shapes (VERIFIED):
      - transcript jsonl records under ``~/.claude/projects/<slug>/<sessionId>.jsonl``
        (``type=user|assistant|queue-operation|attachment|system``, carry
        ``sessionId`` + ``uuid`` + ``cwd`` + ``timestamp``);
      - internal telemetry ``~/.claude/telemetry/1p_failed_events.*.json``
        records ``{event_type:"ClaudeCodeInternalEvent", event_data:{event_name,
        session_id, event_id, ...}}``.
  * Native hook schema = "claude-hooks" (``hooks -> {EventName: [{matcher?,
    hooks:[{type:"command"|"prompt", command, timeout}]}]}``), hook input JSON on
    stdin with ``session_id`` (primary) + ``cwd`` (metadata) + ``hook_event_name``.

P2 SCOPE: telemetry only. Any EXISTING user hooks are PRESERVED; JCC entries are
tagged with a separate marker (``jcc_emit.py``) so they never clobber user config.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from ..redaction import redact
from ..schemas import AEPEnvelope, AgentName, EventType
from ._common import (
    STATUS_ADAPTER_DISABLED,
    STATUS_CONNECTED,
    STATUS_UNVERIFIED,
    UnidentifiableEvent,
    deep_get,
    delivery_id,
    evidence_bundle,
    now_ts,
    pick,
    stable_event_id,
)
from .base import AgentAdapter

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

_EVENT_TYPE_MAP: Dict[str, EventType] = {
    "SessionStart": EventType.SESSION_START,
    "SessionEnd": EventType.HEARTBEAT,
    "UserPromptSubmit": EventType.AGENT_MESSAGE,
    "PreToolUse": EventType.TOOL_CALL,
    "PostToolUse": EventType.TOOL_CALL,
    "Stop": EventType.HEARTBEAT,
    "SubagentStop": EventType.HEARTBEAT,
    "Notification": EventType.HEARTBEAT,
    "PreCompact": EventType.HEARTBEAT,
    "PostCompact": EventType.HEARTBEAT,
}

# transcript record type -> AEP event_type
_TRANSCRIPT_MAP: Dict[str, EventType] = {
    "user": EventType.AGENT_MESSAGE,
    "assistant": EventType.AGENT_MESSAGE,
    "queue-operation": EventType.HEARTBEAT,
    "attachment": EventType.EVIDENCE,
    "system": EventType.HEARTBEAT,
    "summary": EventType.HEARTBEAT,
}

_JCC_MARKER = "jcc_emit.py"
_CLAUDE_EXE = Path(os.path.expanduser("~")) / ".local" / "bin" / "claude.exe"


def _cli_version() -> Optional[str]:
    exe = _CLAUDE_EXE if _CLAUDE_EXE.exists() else "claude"
    try:
        r = subprocess.run(
            [str(exe), "--version"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip().split()[0]
    except Exception:  # noqa: BLE001
        return None
    return None


class ClaudeAdapter(AgentAdapter):
    agent = AgentName.CLAUDE
    hook_schema_kind = "claude-hooks"  # matcher + hooks[] (type: command|prompt)

    async def probe(self) -> Dict[str, Any]:
        home = Path(os.path.expanduser("~"))
        settings = home / ".claude" / "settings.json"
        settings_local = home / ".claude" / "settings.local.json"
        projects = home / ".claude" / "projects"
        telemetry = home / ".claude" / "telemetry"

        cfg = settings if settings.exists() else None
        installed = bool(cfg) or (home / ".claude").exists()

        version = os.environ.get("CLAUDE_CODE_VERSION") or _cli_version()

        hook_installed = False
        existing_hook_events: list[str] = []
        user_hook_count = 0
        for cand in (settings, settings_local):
            if not cand.exists():
                continue
            try:
                data = json.loads(cand.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            hooks = data.get("hooks") or {}
            existing_hook_events.extend(hooks.keys())
            for entries in hooks.values():
                for entry in (entries or []):
                    blob = json.dumps(entry)
                    if _JCC_MARKER in blob:
                        hook_installed = True
                    else:
                        user_hook_count += 1

        transcript_files = sum(1 for _ in projects.glob("*/*.jsonl")) if projects.exists() else 0
        telemetry_files = sum(1 for _ in telemetry.glob("*.json")) if telemetry.exists() else 0

        if installed and cfg:
            status, verified = STATUS_CONNECTED, True
        elif installed:
            status, verified = STATUS_ADAPTER_DISABLED, False
        else:
            status, verified = STATUS_UNVERIFIED, False

        self.verified = verified
        return {
            "agent": "claude",
            "installed": installed,
            "version": version,
            "config_path": str(cfg) if cfg else None,
            "candidate_paths": [
                {"path": str(settings), "exists": settings.exists()},
                {"path": str(settings_local), "exists": settings_local.exists()},
                {"path": str(home / ".claude.json"), "exists": (home / ".claude.json").exists()},
            ],
            "hook_schema_kind": self.hook_schema_kind,
            "hook_installed": hook_installed,
            "installed_hook_events": sorted(set(existing_hook_events)),
            "existing_user_hook_entries": user_hook_count,
            "telemetry_events_supported": TELEMETRY_EVENTS,
            "observed_sources": {
                "transcript_jsonl": transcript_files,
                "internal_telemetry_json": telemetry_files,
            },
            "session_source": "hook stdin JSON + transcript jsonl (session_id/sessionId present)",
            "verified": verified,
            "status": status,
            "note": (
                "settings.json found; user hooks preserved (separate JCC marker)"
                if verified
                else "no parseable ~/.claude/settings.json on host"
            ),
        }

    async def normalize(self, raw: Dict[str, Any]) -> AEPEnvelope:
        source = raw.get("_source") or "hook"
        if source == "transcript_jsonl":
            return self._normalize_transcript(raw)
        if source == "telemetry":
            return self._normalize_telemetry(raw)

        native_event = pick(raw, "hook_event_name", "event") or "unknown"
        session_id = pick(raw, "session_id", "sessionId")
        if not session_id:
            raise UnidentifiableEvent(
                "claude event has no session_id; refusing cwd-only identity"
            )
        event_type = _EVENT_TYPE_MAP.get(str(native_event), EventType.UNKNOWN)
        tool = pick(raw, "tool_name")
        ev_id = stable_event_id(
            "claude", pick(raw, "event_id", "uuid"), session_id, native_event, tool,
            pick(raw, "timestamp"),
        )
        payload = {
            "native_event": native_event,
            "permission_mode": pick(raw, "permission_mode"),
            "tool_name": tool,
            "reason": pick(raw, "reason"),
            "source": pick(raw, "source"),
            "trigger": pick(raw, "trigger"),
            "stop_hook_active": pick(raw, "stop_hook_active"),
        }
        if tool:
            payload["tool_input"] = redact(pick(raw, "tool_input") or {})
        if raw.get("tool_response") is not None:
            payload["tool_response"] = redact(raw["tool_response"])
        payload = {k: v for k, v in payload.items() if v is not None}
        return AEPEnvelope(
            event_id=ev_id,
            delivery_id=delivery_id(provided=raw.get("_delivery_id"), event_id=ev_id),
            session_id=str(session_id),
            agent=self.agent,
            project=str(pick(raw, "project") or "claude"),
            cwd=pick(raw, "cwd"),
            timestamp=now_ts(pick(raw, "timestamp", "client_timestamp")),
            event_type=event_type,
            payload=payload,
            evidence=evidence_bundle(
                "claude_hook", raw,
                ["hook_event_name", "session_id", "cwd", "tool_name", "source", "reason"],
            ),
            schema_ok=True,
        )

    def _normalize_transcript(self, raw: Dict[str, Any]) -> AEPEnvelope:
        """REAL observed ``~/.claude/projects/<slug>/<sessionId>.jsonl`` record."""
        session_id = pick(raw, "sessionId", "session_id")
        if not session_id:
            raise UnidentifiableEvent("claude transcript record has no sessionId")
        rec_type = str(pick(raw, "type") or "unknown")
        ev_id = stable_event_id("claude", pick(raw, "uuid"), session_id, rec_type, raw.get("timestamp"))
        payload = {
            "native_event": f"transcript:{rec_type}",
            "transcript_type": rec_type,
            "permission_mode": pick(raw, "permissionMode"),
            "entrypoint": pick(raw, "entrypoint"),
            "git_branch": pick(raw, "gitBranch"),
            "is_sidechain": pick(raw, "isSidechain"),
            "agent_version": pick(raw, "version"),
            "role": deep_get(raw, "message", "role"),
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        return AEPEnvelope(
            event_id=ev_id,
            delivery_id=delivery_id(provided=raw.get("_delivery_id"), event_id=ev_id),
            session_id=str(session_id),
            agent=self.agent,
            project=str(pick(raw, "project") or "claude"),
            cwd=pick(raw, "cwd"),
            timestamp=now_ts(pick(raw, "timestamp")),
            event_type=_TRANSCRIPT_MAP.get(rec_type, EventType.UNKNOWN),
            payload=payload,
            evidence=evidence_bundle(
                "claude_transcript", raw,
                ["type", "sessionId", "cwd", "version", "gitBranch"],
            ),
            schema_ok=True,
        )

    def _normalize_telemetry(self, raw: Dict[str, Any]) -> AEPEnvelope:
        """REAL observed ``ClaudeCodeInternalEvent`` telemetry record."""
        ed = raw.get("event_data") or {}
        session_id = pick(ed, "session_id", "sessionId")
        if not session_id:
            raise UnidentifiableEvent("claude telemetry record has no session_id")
        event_name = str(pick(ed, "event_name") or "unknown")
        ev_id = stable_event_id("claude", pick(ed, "event_id"), session_id, event_name, ed.get("client_timestamp"))
        env = ed.get("env") or {}
        payload = {
            "native_event": event_name,
            "telemetry_event_type": raw.get("event_type"),
            "model": pick(ed, "model"),
            "entrypoint": pick(ed, "entrypoint"),
            "client_type": pick(ed, "client_type"),
            "platform": env.get("platform"),
            "cli_version": env.get("version"),
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        return AEPEnvelope(
            event_id=ev_id,
            delivery_id=delivery_id(provided=raw.get("_delivery_id"), event_id=ev_id),
            session_id=str(session_id),
            agent=self.agent,
            project="claude",
            cwd=None,  # telemetry carries no cwd -> metadata absent, identity is session_id
            timestamp=now_ts(pick(ed, "client_timestamp")),
            event_type=EventType.HEARTBEAT,
            payload=payload,
            evidence=evidence_bundle(
                "claude_telemetry", ed,
                ["event_name", "session_id", "event_id", "model", "entrypoint"],
            ),
            schema_ok=True,
        )
