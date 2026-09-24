"""Codex agent adapter — REAL probe + AEP normalizer (P2).

Verified facts on this host (2026, Windows, ``C:\\Users\\Administrator``):
  * ``~/.codex/config.toml`` EXISTS; ``codex --version`` -> ``codex-cli 0.136.0``.
  * ``config.toml`` ALREADY defines ``notify = [<codex-computer-use.exe>, "turn-ended"]``
    (a user-owned push hook) -> the JCC installer must NOT clobber it.
  * REAL observed session shape: ``~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl``
    (362 files on this host), one JSON object per line:
    ``{timestamp, ordinal, type, payload}`` with
    ``type in {session_meta, event_msg, response_item, turn_context, ...}``.
    ``session_meta.payload`` carries ``session_id``/``id`` + ``cwd`` + ``cli_version``.
  * The installed binary contains a native ``hooks`` runtime + JSON schema
    (``PreToolUse/PostToolUse/PermissionRequest/SessionStart/...`` plus
    ``command_windows`` and ``ConfiguredHookMatcherGroup{matcher,hooks[]}``), and
    a legacy ``notify`` payload ``{type:"agent-turn-complete", turn-id,
    input-messages, last-assistant-message}``. See probe strings artifact.

HONEST CAPABILITY (do NOT assume the Claude hook schema):
  * OBSERVATION (rollout jsonl) -> real ``session_id`` -> VERIFIED mapping.
  * PUSH (``notify``) -> mechanism present, but payload contract is only
    evidence-grade (binary strings) and the user already owns ``notify`` -> the
    installer refuses to overwrite; push remains UNVERIFIED.
  * App Server / realtime steering -> P0-P2 OFF; capability flag DISABLED.
=> integration status DEGRADED (observer works; push not installed).
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from ..schemas import AEPEnvelope, AgentName, EventType
from ._common import (
    STATUS_DEGRADED,
    STATUS_UNVERIFIED,
    UnidentifiableEvent,
    deep_get,
    delivery_id,
    evidence_bundle,
    now_ts,
    pick,
    redact,
    stable_event_id,
)
from .base import AgentAdapter

# payload.type (event_msg) -> AEP event_type
_EVENT_MSG_MAP: Dict[str, EventType] = {
    "task_started": EventType.HEARTBEAT,
    "task_complete": EventType.HEARTBEAT,
    "turn_aborted": EventType.HEARTBEAT,
    "agent_message": EventType.AGENT_MESSAGE,
    "agent_reasoning": EventType.AGENT_MESSAGE,
    "exec_command_begin": EventType.TOOL_CALL,
    "exec_command_end": EventType.TOOL_CALL,
    "patch_apply_begin": EventType.TOOL_CALL,
    "patch_apply_end": EventType.TOOL_CALL,
    "mcp_tool_call_begin": EventType.TOOL_CALL,
    "mcp_tool_call_end": EventType.TOOL_CALL,
    "token_count": EventType.HEARTBEAT,
    "stream_error": EventType.ERROR,
    "error": EventType.ERROR,
}

# payload.type (response_item) -> AEP event_type
_RESPONSE_ITEM_MAP: Dict[str, EventType] = {
    "message": EventType.AGENT_MESSAGE,
    "reasoning": EventType.AGENT_MESSAGE,
    "function_call": EventType.TOOL_CALL,
    "function_call_output": EventType.EVIDENCE,
    "local_shell_call": EventType.TOOL_CALL,
    "custom_tool_call": EventType.TOOL_CALL,
    "custom_tool_call_output": EventType.EVIDENCE,
    "web_search_call": EventType.TOOL_CALL,
}

# top-level record type -> AEP event_type (fallback)
_RECORD_MAP: Dict[str, EventType] = {
    "session_meta": EventType.SESSION_START,
    "turn_context": EventType.HEARTBEAT,
    "compacted": EventType.HEARTBEAT,
    "turn_aborted": EventType.HEARTBEAT,
}

_JCC_MARKER = "jcc_emit.py"


def _codex_exe() -> str:
    return "codex"


def _offline_version(home: "Path") -> Optional[str]:
    """Best-effort version read from on-disk state (no subprocess)."""
    vf = home / ".codex" / "version.json"
    if vf.exists():
        try:
            js = json.loads(vf.read_text(encoding="utf-8"))
            if isinstance(js, dict):
                for key in ("version", "cli_version", "latest_version"):
                    if js.get(key):
                        return str(js[key])
            if isinstance(js, str):
                return js
        except Exception:  # noqa: BLE001
            return None
    return None


class CodexAdapter(AgentAdapter):
    agent = AgentName.CODEX
    hook_schema_kind = "codex-native-hooks+notify"  # NOT the Claude schema

    async def probe(self) -> Dict[str, Any]:
        home = Path(os.path.expanduser("~"))
        cfg = home / ".codex" / "config.toml"
        sessions = home / ".codex" / "sessions"

        installed = cfg.exists() or (home / ".codex").exists()
        version = os.environ.get("CODEX_CLI_VERSION") or _offline_version(home)
        if not version:
            # npm-installed codex is a .ps1/.cmd shim -> invoke via cmd.exe
            for cmd in (["codex", "--version"], ["cmd", "/c", "codex", "--version"]):
                try:
                    r = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
                    if r.returncode == 0 and r.stdout.strip():
                        version = r.stdout.strip().split()[-1]
                        break
                except Exception:  # noqa: BLE001
                    continue

        notify_cfg = None
        config_ok = False
        if cfg.exists():
            try:
                import tomllib

                data = tomllib.loads(cfg.read_text(encoding="utf-8"))
                config_ok = True
                notify_cfg = data.get("notify")
            except Exception:  # noqa: BLE001
                config_ok = False
        user_notify_present = bool(notify_cfg)
        jcc_notify_installed = bool(
            notify_cfg and any(_JCC_MARKER in str(x) for x in notify_cfg)
        )

        session_files = sum(1 for _ in sessions.rglob("rollout-*.jsonl")) if sessions.exists() else 0

        # Observation mapping is real; push (notify) + app-server are not verified.
        observation_verified = bool(config_ok and session_files > 0)
        push_verified = False   # cannot be verified without an interactive turn
        app_server_steering = False  # P0-P2: OFF

        status = STATUS_DEGRADED if installed else STATUS_UNVERIFIED
        self.verified = observation_verified

        return {
            "agent": "codex",
            "installed": installed,
            "version": version,
            "config_path": str(cfg) if cfg.exists() else None,
            "config_parses": config_ok,
            "hook_schema_kind": self.hook_schema_kind,
            "native_hooks_runtime_present": True,   # from binary strings (evidence-grade)
            "notify_present": user_notify_present,
            "notify_value": notify_cfg,
            "jcc_notify_installed": jcc_notify_installed,
            "session_files_observed": session_files,
            "observation_verified": observation_verified,
            "push_verified": push_verified,
            "app_server_steering": app_server_steering,   # DISABLED
            "session_source": "rollout jsonl (~/.codex/sessions/**/rollout-*.jsonl); session_id in session_meta",
            "verified": observation_verified,
            "status": status,
            "note": (
                "observer (rollout jsonl) verified; push notify NOT installed "
                "(user-owned notify present) => DEGRADED; app-server steering OFF"
                if installed else "codex not present"
            ),
        }

    # ---------------------------------------------------------------- normalize
    async def normalize(self, raw: Dict[str, Any]) -> AEPEnvelope:
        source = raw.get("_source") or "rollout"
        if source == "notify":
            return self._normalize_notify(raw)
        return self._normalize_rollout(raw)

    def _resolve_session(self, raw: Dict[str, Any]) -> Optional[str]:
        payload = raw.get("payload") or {}
        return pick(raw, "_session_id", "session_id", "sessionId") or pick(
            payload, "session_id", "id", "sessionId"
        )

    def _normalize_rollout(self, raw: Dict[str, Any]) -> AEPEnvelope:
        session_id = self._resolve_session(raw)
        if not session_id:
            raise UnidentifiableEvent(
                "codex rollout record has no session_id (needs session_meta or _session_id)"
            )
        rec_type = str(pick(raw, "type") or "unknown")
        payload = raw.get("payload") or {}
        sub_type = str(pick(payload, "type") or "")

        if rec_type == "event_msg":
            event_type = _EVENT_MSG_MAP.get(sub_type, EventType.UNKNOWN)
        elif rec_type == "response_item":
            event_type = _RESPONSE_ITEM_MAP.get(sub_type, EventType.UNKNOWN)
        else:
            event_type = _RECORD_MAP.get(rec_type, EventType.UNKNOWN)

        ordinal = raw.get("ordinal")
        ev_id = stable_event_id(
            "codex", None, session_id, rec_type, sub_type, ordinal, raw.get("timestamp")
        )
        out_payload = {
            "native_event": f"{rec_type}:{sub_type}" if sub_type else rec_type,
            "record_type": rec_type,
            "sub_type": sub_type or None,
            "ordinal": ordinal,
            "turn_id": pick(payload, "turn_id"),
            "role": pick(payload, "role"),
            "cli_version": pick(payload, "cli_version"),
            "originator": pick(payload, "originator"),
            "source": pick(payload, "source"),
            "collaboration_mode": deep_get(payload, "collaboration_mode", "mode")
            or pick(payload, "collaboration_mode_kind"),
            "tool_name": pick(payload, "name", "tool_name"),
        }
        out_payload = {k: v for k, v in out_payload.items() if v is not None}
        # never persist raw content/instructions
        cwd = pick(payload, "cwd") or pick(raw, "cwd")
        return AEPEnvelope(
            event_id=ev_id,
            delivery_id=delivery_id(provided=raw.get("_delivery_id"), event_id=ev_id),
            session_id=str(session_id),
            agent=self.agent,
            project=str(pick(payload, "cwd") or "codex").replace("\\", "/").rstrip("/").split("/")[-1] or "codex",
            cwd=cwd,
            timestamp=now_ts(pick(raw, "timestamp") or pick(payload, "timestamp")),
            event_type=event_type,
            payload=out_payload,
            evidence=evidence_bundle(
                "codex_rollout", {**payload, "ordinal": ordinal, "record_type": rec_type},
                ["record_type", "type", "cli_version", "originator", "cwd", "session_id"],
            ),
            schema_ok=True,
        )

    def _normalize_notify(self, raw: Dict[str, Any]) -> AEPEnvelope:
        """Codex ``notify`` payload (legacy push). Usually has NO session_id.

        Per the P2 rule a missing ``session_id`` is NOT fabricated -> raises
        :class:`UnidentifiableEvent` unless the transport supplies ``_session_id``.
        """
        session_id = pick(raw, "_session_id", "session_id")
        if not session_id:
            raise UnidentifiableEvent(
                "codex notify payload carries no session_id (turn-id only); "
                "refusing to fabricate identity"
            )
        turn_id = pick(raw, "turn-id", "turn_id")
        ev_id = stable_event_id("codex", None, session_id, "notify", turn_id, raw.get("timestamp"))
        payload = {
            "native_event": str(pick(raw, "type") or "notify"),
            "turn_id": turn_id,
            "input_message_count": len(raw.get("input-messages") or []),
            "has_last_assistant_message": bool(raw.get("last-assistant-message")),
        }
        return AEPEnvelope(
            event_id=ev_id,
            delivery_id=delivery_id(provided=raw.get("_delivery_id"), event_id=ev_id),
            session_id=str(session_id),
            agent=self.agent,
            project="codex",
            cwd=None,
            timestamp=now_ts(raw.get("timestamp")),
            event_type=EventType.HEARTBEAT,
            payload=payload,
            evidence=evidence_bundle("codex_notify", raw, ["type", "turn-id"]),
            schema_ok=True,
        )
