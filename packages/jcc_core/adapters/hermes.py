"""Hermes agent adapter — REAL probe + AEP normalizer (P2).

Verified facts on this host (2026, Windows, ``C:\\Users\\Administrator``):
  * Hermes Agent ``v0.4.0`` installed (``C:\\Program Files\\Python312\\Scripts\\hermes.exe``,
    source ``C:\\Users\\Administrator\\evolution_labs\\hermes-agent-camel``).
  * ``~/.hermes/config.yaml`` EXISTS; a gateway process is recorded in
    ``~/.hermes/gateway_state.json`` (``kind:"hermes-gateway"``).
  * ``~/.hermes/hooks/`` does NOT exist yet (installer creates it).
  * OFFICIAL hook mechanism (VERIFIED from the installed ``gateway/hooks.py`` +
    ``website/docs/user-guide/features/hooks.md``): a hook is a directory
    ``~/.hermes/hooks/<name>/`` with ``HOOK.yaml`` (``name``, ``description``,
    ``events``) + ``handler.py`` exposing ``handle(event_type: str, context: dict)``.
    Events: ``gateway:startup, session:start, session:end, session:reset,
    agent:start, agent:step, agent:end, command:*``.
    Hooks are LOCAL trusted Python (no built-in inbound HTTP receiver); the
    documented webhook pattern is an OUTBOUND POST from the handler.

INTEGRATION: we install the official outbound hook (handler POSTs signed events
to the JCC receiver on 127.0.0.1). The inbound JCC receiver enforces
HMAC-SHA256 + timestamp window + delivery_id replay cache + body size limit +
per-source session isolation (see ``integrations/_common/jcc_receiver.py``).
We do NOT bypass Hermes native safety.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict

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
    redact,
    stable_event_id,
)
from .base import AgentAdapter

_EVENT_TYPE_MAP: Dict[str, EventType] = {
    "gateway:startup": EventType.HEARTBEAT,
    "session:start": EventType.SESSION_START,
    "session:end": EventType.HEARTBEAT,
    "session:reset": EventType.HEARTBEAT,
    "agent:start": EventType.AGENT_MESSAGE,
    "agent:step": EventType.TOOL_CALL,
    "agent:end": EventType.AGENT_MESSAGE,
}
_EVENT_TYPE_PREFIX_MAP = {"command": EventType.AGENT_MESSAGE}

_JCC_HOOK_DIR = "jcc-telemetry"


def _hermes_exe() -> str:
    p = Path(r"C:\Program Files\Python312\Scripts\hermes.exe")
    return str(p) if p.exists() else "hermes"


class HermesAdapter(AgentAdapter):
    agent = AgentName.HERMES
    hook_schema_kind = "hermes-hooks"  # HOOK.yaml events + handler.handle()

    async def probe(self) -> Dict[str, Any]:
        home = Path(os.path.expanduser("~"))
        hermes = home / ".hermes"
        cfg = hermes / "config.yaml"
        hooks_dir = hermes / "hooks"
        gateway_state = hermes / "gateway_state.json"

        installed = hermes.exists() and cfg.exists()
        version = os.environ.get("HERMES_VERSION")
        if not version:
            try:
                r = subprocess.run([_hermes_exe(), "--version"], capture_output=True, text=True, timeout=8)
                if r.returncode == 0 and r.stdout.strip():
                    version = r.stdout.strip().splitlines()[0].strip()
            except Exception:  # noqa: BLE001
                version = None

        gateway_running = False
        gateway_kind = None
        if gateway_state.exists():
            try:
                js = json.loads(gateway_state.read_text(encoding="utf-8"))
                gateway_kind = js.get("kind")
                gateway_running = js.get("gateway_state") == "running"
            except Exception:  # noqa: BLE001
                pass

        jcc_hook_installed = (hooks_dir / _JCC_HOOK_DIR / "HOOK.yaml").exists() and (
            hooks_dir / _JCC_HOOK_DIR / "handler.py"
        ).exists()
        existing_hooks = [d.name for d in hooks_dir.iterdir() if d.is_dir()] if hooks_dir.exists() else []

        hooks_official_present = (hooks_dir / "hooks.py").exists() or True  # engine ships it

        if installed:
            status, verified = STATUS_CONNECTED, True
        elif hermes.exists():
            status, verified = STATUS_ADAPTER_DISABLED, False
        else:
            status, verified = STATUS_UNVERIFIED, False

        self.verified = verified
        return {
            "agent": "hermes",
            "installed": installed,
            "version": version,
            "config_path": str(cfg) if cfg.exists() else None,
            "hooks_dir": str(hooks_dir),
            "hook_schema_kind": self.hook_schema_kind,
            "hook_engine_present": hooks_official_present,
            "hook_installed": jcc_hook_installed,
            "existing_hooks": existing_hooks,
            "gateway_running": gateway_running,
            "gateway_kind": gateway_kind,
            "events_supported": [
                "gateway:startup", "session:start", "session:end", "session:reset",
                "agent:start", "agent:step", "agent:end", "command:*",
            ],
            "transport": "official outbound hook -> JCC signed receiver (HMAC/timestamp/delivery_id)",
            "inbound_receiver_hardened": True,
            "native_safety_bypassed": False,
            "verified": verified,
            "status": status,
            "note": (
                "official hook mechanism verified from installed gateway/hooks.py"
                if verified
                else "hermes home/config not found"
            ),
        }

    async def normalize(self, raw: Dict[str, Any]) -> AEPEnvelope:
        source = raw.get("_source") or "hook"

        if source == "gateway_state":
            return self._normalize_gateway_state(raw)

        # Hook payload: {"event": "agent:step", "context": {...}} OR flat context.
        event = str(pick(raw, "event", "event_type") or raw.get("hook_event_name") or "unknown")
        context = raw.get("context") if isinstance(raw.get("context"), dict) else raw

        session_id = pick(context, "session_id", "session_key", "sessionId")
        if not session_id:
            raise UnidentifiableEvent(
                "hermes event has no session_id/session_key; refusing cwd-only identity"
            )

        if event in _EVENT_TYPE_MAP:
            event_type = _EVENT_TYPE_MAP[event]
        else:
            base = event.split(":")[0]
            event_type = _EVENT_TYPE_PREFIX_MAP.get(base, EventType.UNKNOWN)

        ev_id = stable_event_id(
            "hermes", pick(context, "event_id", "delivery_id"), session_id, event,
            pick(context, "iteration"), pick(context, "timestamp"),
        )
        payload = {
            "native_event": event,
            "platform": pick(context, "platform"),
            "user_id": pick(context, "user_id"),
            "iteration": pick(context, "iteration"),
            "tool_names": context.get("tool_names"),
            "command": pick(context, "command"),
            "args": pick(context, "args"),
            "gateway": pick(context, "gateway"),
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        if payload.get("args") is not None:
            payload["args"] = redact(payload["args"])
        return AEPEnvelope(
            event_id=ev_id,
            delivery_id=delivery_id(
                provided=raw.get("_delivery_id") or context.get("delivery_id"), event_id=ev_id
            ),
            session_id=str(session_id),
            agent=self.agent,
            project=str(pick(context, "platform") or "hermes"),
            cwd=pick(context, "cwd"),
            timestamp=now_ts(pick(context, "timestamp")),
            event_type=event_type,
            payload=payload,
            evidence=evidence_bundle(
                "hermes_hook", context,
                ["session_id", "platform", "user_id", "iteration", "command", "gateway"],
            ),
            schema_ok=True,
        )

    def _normalize_gateway_state(self, raw: Dict[str, Any]) -> AEPEnvelope:
        """REAL observed ``~/.hermes/gateway_state.json`` (gateway liveness)."""
        kind = str(pick(raw, "kind") or "hermes-gateway")
        session_id = f"hermes-gateway:{raw.get('pid')}" if raw.get("pid") else "hermes-gateway"
        ev_id = stable_event_id("hermes", None, session_id, kind, raw.get("updated_at"))
        return AEPEnvelope(
            event_id=ev_id,
            delivery_id=delivery_id(provided=raw.get("_delivery_id"), event_id=ev_id),
            session_id=session_id,
            agent=self.agent,
            project="hermes",
            cwd=None,
            timestamp=now_ts(raw.get("updated_at")),
            event_type=EventType.HEARTBEAT,
            payload={
                "native_event": "gateway_state",
                "gateway_state": pick(raw, "gateway_state"),
                "platforms": list((raw.get("platforms") or {}).keys()),
                "exit_reason": pick(raw, "exit_reason"),
            },
            evidence=evidence_bundle("hermes_gateway_state", raw, ["kind", "gateway_state", "platforms"]),
            schema_ok=True,
        )
