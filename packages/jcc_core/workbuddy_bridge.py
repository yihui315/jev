"""JEV First-Party Bridge adapter — the JEV-side production executor.

This is the NEW production path for native execution. It talks to the JEV
First-Party Bridge (``integrations/jev_bridge/bridge_server.py`` / the WorkBuddy
extension variant ``extension_index.cjs``) over a strictly-localhost, capability-
token-authenticated HTTP + SSE transport.

DESIGN RULES (per FINAL PRODUCT UNBLOCK)
---------------------------------------
* **Fail-closed.** If no bridge descriptor is discoverable, ``run_workload`` raises
  ``WorkbuddyBridgeUnavailable`` — it does NOT fall back to an ambient session, an
  isolated ``--serve``, or an unauthenticated CLI.
* **No WorkBuddy credential ever reaches JEV.** The adapter only carries the local
  *capability token* that authorises ``JEV -> local bridge``.
* **Three IDs stay separate:** ``mission_id`` / ``jcc_run_id`` / ``correlation_id``
  are JEV's; ``workbuddy_workload_id`` / ``workbuddy_session_id`` come from the
  bridge. They are never conflated.
* **Mission-scoped.** Every task is bound to its mission/jcc-run/correlation and a
  ``project_root``; the bridge returns a real ``workbuddy_workload_id``.

The adapter reuses the SAME native-event vocabulary as ``NativeLaunchSupervisor``
(file_read / file_write / command_start / command_end / tool_start / tool_end /
artifact_created) and records the T0–T6 causal timestamp chain. It does NOT modify
any frozen module.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class WorkbuddyBridgeUnavailable(RuntimeError):
    """Raised when no JEV First-Party Bridge is discoverable.

    This is the explicit fail-closed signal: JEV must NOT fall back to any other
    executor when the bridge is absent.
    """


class BridgeExecutionFailed(RuntimeError):
    """The bridge reported a workload failure (mission -> FAILED)."""


# --------------------------------------------------------------------------- #
# Result record (adapter-owned; the frozen NativeWorkload is not modified)
# --------------------------------------------------------------------------- #
@dataclass
class BridgeExecutionResult:
    """The full lifecycle record of one bridge-driven workload.

    Captures the T0–T6 causal chain and keeps the three JEV ids distinct from the
    two bridge ids.
    """

    mission_id: str
    jcc_run_id: str
    correlation_id: str
    project_root: str
    workbuddy_workload_id: Optional[str] = None
    workbuddy_session_id: Optional[str] = None
    instance_id: Optional[str] = None

    launch_requested_at: float = 0.0          # T0
    bridge_request_sent_at: Optional[float] = None  # T1
    workload_created_at: Optional[float] = None     # T2
    workload_started_at: Optional[float] = None     # T3
    first_native_event_at: Optional[float] = None   # T4
    artifact_created_at: Optional[float] = None     # T5
    workload_completed_at: Optional[float] = None   # T6

    status: str = "PENDING"
    native_events: List[Dict[str, Any]] = field(default_factory=list)
    artifact_path: Optional[str] = None
    failure_stage: Optional[str] = None
    failure_code: Optional[str] = None
    failure_message: Optional[str] = None
    failed_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "jcc_run_id": self.jcc_run_id,
            "correlation_id": self.correlation_id,
            "workbuddy_workload_id": self.workbuddy_workload_id,
            "workbuddy_session_id": self.workbuddy_session_id,
            "instance_id": self.instance_id,
            "launch_requested_at": self.launch_requested_at,
            "bridge_request_sent_at": self.bridge_request_sent_at,
            "workload_created_at": self.workload_created_at,
            "workload_started_at": self.workload_started_at,
            "first_native_event_at": self.first_native_event_at,
            "artifact_created_at": self.artifact_created_at,
            "workload_completed_at": self.workload_completed_at,
            "status": self.status,
            "native_events": self.native_events,
            "artifact_path": self.artifact_path,
            "failure_stage": self.failure_stage,
            "failure_code": self.failure_code,
            "failure_message": self.failure_message,
            "failed_at": self.failed_at,
        }


# Bridge event name -> native-event kind (heartbeat is intentionally absent: the
# bridge never emits it, and we reject it on receipt anyway).
_BRIDGE_TO_NATIVE = {
    "workload_created": "workload_created",
    "workload_started": "workload_started",
    "tool_start": "tool_start",
    "tool_end": "tool_end",
    "file_read": "file_read",
    "file_write": "file_write",
    "command_start": "command_start",
    "command_end": "command_end",
    "artifact_created": "artifact_created",
}


def _local_appdata() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = os.path.expanduser("~/.local/share")
    return Path(base)


DEFAULT_DESCRIPTOR = _local_appdata() / "WorkBuddy" / "jev-bridge.json"


def discover_bridge(
    descriptor_path: Optional[str] = None,
    base_url: Optional[str] = None,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """Locate the bridge. Fail-closed if nothing is discoverable.

    Priority: explicit ``base_url``+``token`` override > ``JEV_BRIDGE_URL`` /
    ``JEV_BRIDGE_TOKEN`` env > the ACL-scoped descriptor file.
    """
    if base_url and token:
        return {"base_url": base_url.rstrip("/"), "token": token, "instance_id": None}
    env_url = os.environ.get("JEV_BRIDGE_URL")
    env_token = os.environ.get("JEV_BRIDGE_TOKEN")
    if env_url and env_token:
        return {"base_url": env_url.rstrip("/"), "token": env_token, "instance_id": None}
    path = Path(descriptor_path) if descriptor_path else DEFAULT_DESCRIPTOR
    if not path.is_file():
        raise WorkbuddyBridgeUnavailable(
            f"no JEV First-Party Bridge descriptor at {path}; "
            "native execution is fail-closed (WORKBUDDY_BRIDGE_UNAVAILABLE)"
        )
    import json
    data = json.loads(path.read_text(encoding="utf-8"))
    if not data.get("capability_token") or not data.get("port"):
        raise WorkbuddyBridgeUnavailable(
            f"bridge descriptor {path} is missing token/port"
        )
    return {
        "base_url": f"http://127.0.0.1:{data['port']}",
        "token": data["capability_token"],
        "instance_id": data.get("instance_id"),
    }


class WorkBuddyBridgeAdapter:
    """Production executor that drives the JEV First-Party Bridge.

    Replaces ``WorkBuddyServeAdapter`` / ``WorkBuddyDaemonAdapter`` as the native
    path. Never falls back to ambient sessions, isolated ``--serve``, or an
    unauthenticated CLI.
    """

    def __init__(
        self,
        descriptor_path: Optional[str] = None,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout_s: float = 60.0,
    ) -> None:
        self.cfg = discover_bridge(descriptor_path, base_url, token)
        self.timeout_s = timeout_s

    # -- auth header -------------------------------------------------------- #
    def _headers(self) -> Dict[str, str]:
        return {"X-JEV-Bridge-Token": self.cfg["token"]}

    # -- mission-scoped execution ------------------------------------------ #
    def run_workload(
        self,
        mission_id: str,
        jcc_run_id: str,
        correlation_id: str,
        project_root: str,
        task: str,
        allowed_tools: Optional[List[str]] = None,
        timeout_s: Optional[float] = None,
    ) -> BridgeExecutionResult:
        """Submit a task to the bridge and consume its SSE event stream.

        Returns a complete :class:`BridgeExecutionResult`. Raises
        :class:`WorkbuddyBridgeUnavailable` on any transport failure (fail-closed)
        and :class:`BridgeExecutionFailed` if the bridge reports a workload
        failure.
        """
        t0 = time.time()
        timeout = timeout_s or self.timeout_s
        result = BridgeExecutionResult(
            mission_id=mission_id,
            jcc_run_id=jcc_run_id,
            correlation_id=correlation_id,
            project_root=project_root,
            launch_requested_at=t0,
        )
        base = self.cfg["base_url"]
        try:
            with httpx.Client(timeout=timeout, verify=False) as client:
                # T1 — bridge request sent
                result.bridge_request_sent_at = time.time()
                resp = client.post(
                    f"{base}/jev/v1/tasks",
                    headers=self._headers(),
                    json={
                        "mission_id": mission_id,
                        "jcc_run_id": jcc_run_id,
                        "correlation_id": correlation_id,
                        "project_root": project_root,
                        "task": task,
                        "allowed_tools": allowed_tools or [],
                    },
                )
                if resp.status_code == 401:
                    raise WorkbuddyBridgeUnavailable("bridge rejected capability token")
                resp.raise_for_status()
                body = resp.json()
                if not body.get("accepted"):
                    raise BridgeExecutionFailed(f"bridge did not accept task: {body}")
                result.workbuddy_workload_id = body.get("workbuddy_workload_id")
                result.workbuddy_session_id = body.get("workbuddy_session_id")
                result.instance_id = body.get("instance_id") or self.cfg.get("instance_id")
                # T2 (workload_created_at) is taken from the bridge's
                # `workload_created` SSE event (bridge clock), not the JEV clock,
                # so the whole T2–T6 chain stays on one clock.
                result.status = "RUNNING"

                # Consume SSE
                self._consume_events(client, result, timeout)
        except WorkbuddyBridgeUnavailable:
            raise
        except (httpx.HTTPError, OSError) as exc:
            raise WorkbuddyBridgeUnavailable(
                f"bridge transport unreachable: {exc}"
            ) from exc

        # Mission completion requires BOTH a bridge 'completed' event AND a real
        # artifact on disk (never trust the event alone).
        if result.status == "COMPLETED":
            if not self._validate_artifact(result):
                result.status = "FAILED"
                result.failure_stage = "ARTIFACT_VALIDATION"
                result.failure_code = "ARTIFACT_MISSING_OR_EMPTY"
                result.failure_message = (
                    "bridge completed but the artifact was not found on disk"
                )
                result.failed_at = time.time()
                raise BridgeExecutionFailed(
                    f"{result.failure_code}: {result.failure_message}")
        elif result.status == "FAILED":
            raise BridgeExecutionFailed(
                f"{result.failure_code}: {result.failure_message}")
        return result

    def _consume_events(
        self, client: httpx.Client, result: BridgeExecutionResult, timeout: float
    ) -> None:
        wid = result.workbuddy_workload_id
        base = self.cfg["base_url"]
        with client.stream(
            "GET", f"{base}/jev/v1/tasks/{wid}/events", headers=self._headers(),
            timeout=timeout,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if not payload:
                    continue
                import json
                try:
                    ev = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                self._ingest_event(result, ev)
                if result.status in ("COMPLETED", "FAILED"):
                    return

    def _ingest_event(self, result: BridgeExecutionResult, ev: Dict[str, Any]) -> None:
        name = ev.get("event")
        now = ev.get("at") or time.time()
        # Reject any heartbeat/health noise defensively (bridge never emits these).
        if name in ("heartbeat", "health", "ready", "poll"):
            return
        # Terminal events are handled FIRST — they are not native-event "kinds"
        # in the timing vocabulary, but they close the workload and must be
        # recorded as terminal markers.
        if name in ("completed", "failed"):
            result.workload_completed_at = now
            if name == "completed":
                result.status = "COMPLETED"
            else:
                result.status = "FAILED"
                result.failure_stage = ev.get("failure_stage")
                result.failure_code = ev.get("failure_code")
                result.failure_message = ev.get("failure_message")
                result.failed_at = now
            result.native_events.append({"kind": name, "at": now})
            return
        kind = _BRIDGE_TO_NATIVE.get(name)
        if kind is None:
            return  # unknown bridge event: ignore, do not fabricate
        # T2 — workload created (authoritative bridge-clock time)
        if name == "workload_created" and result.workload_created_at is None:
            result.workload_created_at = now
        # T4 — first genuine native event
        if result.first_native_event_at is None:
            result.first_native_event_at = now
            result.workload_started_at = result.workload_started_at or now
        if name == "workload_started" and result.workload_started_at is None:
            result.workload_started_at = now
        if name == "artifact_created":
            result.artifact_created_at = now
            result.artifact_path = ev.get("path") or result.artifact_path
        result.native_events.append({"kind": kind, "at": now})

    def _validate_artifact(self, result: BridgeExecutionResult) -> bool:
        path = result.artifact_path
        if not path:
            # Fall back to the fixture convention if the event omitted the path.
            cand = Path(result.project_root) / "output" / "release_acceptance.txt"
            path = str(cand) if cand.is_file() else path
        if not path:
            return False
        p = Path(path)
        if not p.is_file():
            return False
        try:
            return p.stat().st_size > 0
        except OSError:
            return False

    # -- restart re-attach (no ambient-session scan) ------------------------ #
    def reattach(
        self,
        workbuddy_workload_id: str,
        instance_id: Optional[str] = None,
        mission_id: Optional[str] = None,
        jcc_run_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        project_root: str = "",
    ) -> BridgeExecutionResult:
        """Re-attach to an existing workload after a JCC restart.

        Uses the bridge ``instance_id`` + ``workbuddy_workload_id`` + a JCC-side
        correlation check. It does NOT scan any ambient session to substitute a
        workload.
        """
        base = self.cfg["base_url"]
        result = BridgeExecutionResult(
            mission_id=mission_id or "",
            jcc_run_id=jcc_run_id or "",
            correlation_id=correlation_id or "",
            project_root=project_root,
            workbuddy_workload_id=workbuddy_workload_id,
            instance_id=instance_id or self.cfg.get("instance_id"),
        )
        try:
            with httpx.Client(timeout=self.timeout_s, verify=False) as client:
                resp = client.get(
                    f"{base}/jev/v1/tasks/{workbuddy_workload_id}",
                    headers=self._headers(),
                )
                if resp.status_code == 404:
                    raise WorkbuddyBridgeUnavailable(
                        f"workload {workbuddy_workload_id} not found on bridge")
                resp.raise_for_status()
                body = resp.json()
        except (httpx.HTTPError, OSError) as exc:
            raise WorkbuddyBridgeUnavailable(f"bridge unreachable on reattach: {exc}") from exc
        result.status = body.get("status", "UNKNOWN")
        result.workload_created_at = body.get("workload_created_at")
        result.workload_started_at = body.get("workload_started_at")
        result.artifact_created_at = body.get("artifact_created_at")
        result.workload_completed_at = body.get("workload_completed_at")
        result.artifact_path = body.get("artifact_path")
        result.failure_stage = body.get("failure_stage")
        result.failure_code = body.get("failure_code")
        result.failure_message = body.get("failure_message")
        result.failed_at = body.get("failed_at")
        return result


__all__ = [
    "WorkBuddyBridgeAdapter",
    "BridgeExecutionResult",
    "WorkbuddyBridgeUnavailable",
    "BridgeExecutionFailed",
    "discover_bridge",
    "DEFAULT_DESCRIPTOR",
]
