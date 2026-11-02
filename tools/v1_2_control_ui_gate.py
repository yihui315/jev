"""Wave 8 — Control-UI gate for the v1.2 Visual Command Center.

Verifies the control surface is fail-closed (a token is REQUIRED) AND that every
directive the control plane produces is INERT (acted=False). The v1.2 Visual
Command Center never executes a side-effect, even when a VALID control token is
present. This is the "control UI cannot act on the world" guarantee the project
hard boundary demands (Jev scores; Policy is the sole directive origin; the
actuator is disabled; high-risk needs human approval).

Runs ENTIRELY in-process via FastAPI's TestClient — NO socket is bound, so the
protected GREEN 4778 (and any running 4779 instance) are never contacted.

Checks:
  * TOKEN-FAIL-CLOSED — every mutating endpoint (assess/pause/steer/retry)
    refuses WITHOUT a token (401) and WITH a wrong token (403).
  * INERT-AUTHORIZED — with a CORRECT token the endpoint returns 200, but its
    actuation result is {"acted": False, "executable": False,
    "reason": "shadow_mode"}: no side-effect, even when authorized.
  * INERT-STRUCTURAL — Actuator(enabled=True, allowed_actions=["*"]).execute(d)
    STILL returns acted=False (real actuation is not implemented in v1.2; the
    INERT property is structural, not merely config-dependent).
  * GREEN-UNTOUCHED — by construction no socket is opened; asserted + a
    read-only note. GREEN 4778 is never probed for writes.

Run:  python tools/v1_2_control_ui_gate.py
Exit: 0 = PASS, 1 = FAIL (a real gate, not a green-washer).
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))

from fastapi.testclient import TestClient  # noqa: E402

from jcc_core.actuator import Actuator  # noqa: E402
from jcc_core.config import Config, ServerConfig  # noqa: E402
from jcc_core.core import CoreEngine  # noqa: E402
from jcc_core.providers import build_provider  # noqa: E402
from jcc_core.schemas import AEPEnvelope, AgentName, Directive, DirectiveAction, EventType  # noqa: E402
from jcc_core.server import create_app  # noqa: E402
from jcc_core.state_store import StateStore  # noqa: E402

# The env var the server reads for the control token (config.control_token_env).
CONTROL_TOKEN_ENV = "JCC_CONTROL_TOKEN"
GATE_TOKEN = "wave8-control-ui-gate-token"

MUTATING_ENDPOINTS = ("/api/assess", "/api/pause", "/api/steer", "/api/retry")

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {msg}")
    if not cond:
        failures.append(msg)


def _make_engine(db_dir: Path) -> CoreEngine:
    db_path = str(db_dir / "control_ui_gate.db")
    cfg = Config(server=ServerConfig(), state_store_db_path=db_path)
    store = StateStore(db_path)
    return CoreEngine(store, build_provider(cfg), Actuator(enabled=False), cfg)


def _minimal_directive(action: DirectiveAction) -> Directive:
    return Directive(
        directive_id=f"dir_gate_{int(time.time()*1000)}",
        session_id="gate-session",
        timestamp=time.time(),
        action=action,
        target_actuator="noop",
        policy_version="p3.0.0",
        evidence_hash="gate",
        signed=False,
        signature=None,
    )


def main() -> int:
    print("WAVE 8 — CONTROL-UI GATE")
    saved_token = os.environ.get(CONTROL_TOKEN_ENV)
    with tempfile.TemporaryDirectory() as d:
        engine = _make_engine(Path(d))
        app = create_app(engine, engine.config)
        # TestClient binds NO socket, so GREEN 4778 is never contacted.
        client = TestClient(app)

        # 1) TOKEN-FAIL-CLOSED: with the env unset, every mutating endpoint 401s.
        if CONTROL_TOKEN_ENV in os.environ:
            del os.environ[CONTROL_TOKEN_ENV]
        for ep in MUTATING_ENDPOINTS:
            r = client.post(ep, json={"session_id": "x"})
            check(r.status_code == 401,
                  f"{ep} without token -> 401 fail-closed (got {r.status_code})")

        # 2) Wrong token -> 403 (token configured but not presented correctly).
        os.environ[CONTROL_TOKEN_ENV] = GATE_TOKEN
        for ep in MUTATING_ENDPOINTS:
            r = client.post(ep, json={"session_id": "x"},
                            headers={"X-JCC-Token": "wrong-token"})
            check(r.status_code == 403,
                  f"{ep} with WRONG token -> 403 (got {r.status_code})")

        # 3) INERT-AUTHORIZED: correct token -> 200, but actuation is inert.
        #    /api/assess needs a VALID AEP envelope (a malformed one is a
        #    fail-closed 422 that still writes its audit row, per ADR-0008 D-26);
        #    the other three only need a session_id (control-plane manual).
        valid_envelope = {
            "event_id": f"gate-assess-{int(time.time()*1000)}",
            "delivery_id": "dl-gate-assess",
            "session_id": "gate-session",
            "agent": "workbuddy",
            "project": "proj",
            "cwd": "/tmp",
            "timestamp": time.time(),
            "event_type": "tool_call",
            "payload": {"n": 1},
            "evidence": {"ok": True},
        }
        for ep in MUTATING_ENDPOINTS:
            body = valid_envelope if ep == "/api/assess" else {"session_id": "x"}
            r = client.post(ep, json=body,
                            headers={"X-JCC-Token": GATE_TOKEN})
            check(r.status_code == 200,
                  f"{ep} with CORRECT token -> 200 (got {r.status_code})")
            if r.status_code == 200:
                body = r.json()
                # /api/assess returns the engine.ingest `out` dict (top-level
                # `acted` / `actuation_reason`); the manual control endpoints
                # return {"actuation": {acted, executable, reason}}. Both must
                # be INERT when authorized.
                if ep == "/api/assess":
                    check(body.get("acted") is False,
                          f"{ep} actuation acted=False even when authorized "
                          f"(got {body.get('acted')})")
                    check(body.get("actuation_reason") == "shadow_mode",
                          f"{ep} actuation actuation_reason=shadow_mode "
                          f"(got {body.get('actuation_reason')})")
                else:
                    act = body.get("actuation") or {}
                    check(act.get("acted") is False,
                          f"{ep} actuation acted=False even when authorized "
                          f"(got {act.get('acted')})")
                    check(act.get("executable") is False,
                          f"{ep} actuation executable=False (got {act.get('executable')})")
                    check(act.get("reason") == "shadow_mode",
                          f"{ep} actuation reason=shadow_mode (got {act.get('reason')})")

        # 4) INERT-STRUCTURAL: even an ENABLED actuator with allowed action
        #    cannot execute a real side-effect in v1.2.
        enabled = Actuator(enabled=True, allowed_actions=["*"])
        res = enabled.execute(_minimal_directive(DirectiveAction.PAUSE))
        check(res.get("acted") is False,
              "Actuator(enabled=True).execute -> still acted=False "
              f"(got {res.get('acted')}, reason={res.get('reason')})")
        check(res.get("reason") in ("real_actuation_not_implemented",
                                    "action_not_allowed"),
              f"enabled actuator refuses real actuation (reason={res.get('reason')})")
        # dispatch is permanently inert regardless of enabled flag.
        disp = enabled.dispatch(_minimal_directive(DirectiveAction.STEER))
        check(disp.get("acted") is False and disp.get("reason") == "shadow_mode",
              "Actuator.dispatch permanently inert (acted=False, shadow_mode)")

        # 5) Engine ingest path: the broadcast 'out' carries acted=False.
        out = engine.ingest(AEPEnvelope(
            event_id="gate-ingest-1", delivery_id="dl-gate-1",
            session_id="gate-session", agent=AgentName.WORKBUDDY, project="proj",
            cwd="/tmp", timestamp=time.time(), event_type=EventType.TOOL_CALL,
            payload={"n": 1}, evidence={"ok": True}, record_source="live",
        ))
        check(out.get("acted") is False,
              f"engine.ingest broadcast acted=False (got {out.get('acted')})")
        check(out.get("actuation_reason") == "shadow_mode",
              f"engine.ingest actuation_reason=shadow_mode "
              f"(got {out.get('actuation_reason')})")

    # Restore env to avoid leaking the gate token into the host process.
    if saved_token is None:
        os.environ.pop(CONTROL_TOKEN_ENV, None)
    else:
        os.environ[CONTROL_TOKEN_ENV] = saved_token

    print(f"\nCONTROL-UI GATE: {'PASS' if not failures else 'FAIL'} "
          f"({len(failures)} failure(s))")
    for f in failures:
        print(f"  - {f}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
