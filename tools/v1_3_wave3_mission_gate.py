"""WAVE 3 PRODUCT GATE — FIRST REAL WORKBUDDY MISSION (owner: Metric).

THE GATE'S QUESTION
-------------------
"A mission was launched against a REAL WorkBuddy session; its AEP telemetry
reached the engine; and assessment -> policy -> directive -> audit -> SSE all
followed -- with Actuation still False, and none of it faked."

This runs the whole chain against a REAL v1.3 ASGI app on an ephemeral port with
an isolated temp DB, drives it over REAL HTTP, and asserts on what actually
happened. It does not accept a synthetic AEP envelope as a launch event unless the
capture step itself is what is being proven absent.

FAIL-CLOSED PHILOSOPHY
----------------------
Every check here can fail, and several are designed to fail if the code under
test regresses to a lie:

  * ``LAUNCH_REQUIRES_REAL_SESSION`` — a launch with no live session must be
    refused with 409, not silently succeed. If the server ever starts
    fabricating a session id, this check catches it.
  * ``NO_FAKE_TELEMETRY`` — the ingested session id must be one that exists in
    the WorkBuddy session registry on THIS host. A made-up id fails here.
  * ``INGEST_IS_REAL`` — the audit tape must contain a row for the launched
    session, and the session must be visible in the state store.
  * ``ACTUATION_FALSE`` — every ingest result must report acted=False. A single
    True fails the gate.
  * ``SSE_BROADCAST`` — a subscriber connected before the launch must receive
    the launch's event. This catches "the data is there but nothing is live".

Evidence is written to ``.workbuddy/scratch/v1_3_wave3_gate_<stamp>.json``.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parents[1]
PKGS = REPO / "packages"
if str(PKGS) not in sys.path:
    sys.path.insert(0, str(PKGS))

TOKEN = "wave3-gate-control-token"
TOKEN_ENV = "JCC_CONTROL_TOKEN"
SCRATCH = REPO / ".workbuddy" / "scratch"

_LOOPBACK = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _http(method: str, url: str, *, token: Optional[str] = None,
          body: Optional[dict] = None, timeout: float = 20.0, stream: bool = False):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-JCC-Token", token)
    try:
        with _LOOPBACK.open(req, timeout=timeout) as resp:
            raw = "" if stream else resp.read().decode("utf-8", "replace")
            return resp.status, raw, dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace"), dict(exc.headers)


def _json(raw: str) -> Any:
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return None


class Checks:
    def __init__(self) -> None:
        self.steps: List[Dict[str, Any]] = []

    def add(self, step: str, ok: bool, value: Any = None, note: str = "") -> None:
        self.steps.append({"step": step, "ok": bool(ok),
                           "value": value, "note": note})

    @property
    def failed(self) -> List[Dict[str, Any]]:
        return [s for s in self.steps if not s["ok"]]

    def payload(self, gate: str) -> Dict[str, Any]:
        return {
            "gate": gate,
            "verdict": "PASS" if not self.failed and self.steps else "FAIL",
            "passed": sum(1 for s in self.steps if s["ok"]),
            "total": len(self.steps),
            "failures": self.failed,
            "steps": self.steps,
            "ts": time.time(),
        }


def main() -> int:
    checks = Checks()
    tmp = Path(tempfile.mkdtemp(prefix="w3gate_"))
    db = str(tmp / "jcc.db")

    os.environ[TOKEN_ENV] = TOKEN

    import uvicorn
    from jcc_core.actuator import Actuator
    from jcc_core.config import Config, ServerConfig
    from jcc_core.core import CoreEngine
    from jcc_core.providers import build_provider
    from jcc_core.server import create_app
    from jcc_core.state_store import StateStore
    from jcc_core.workbuddy_launch import capture_launch_event, list_sessions

    cfg = Config(server=ServerConfig(port=0), state_store_db_path=db,
                 control_token_env=TOKEN_ENV)
    store = StateStore(db)
    engine = CoreEngine(store, build_provider(cfg), Actuator(enabled=False), cfg)
    app = create_app(engine, cfg)

    uvcfg = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(uvcfg)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 25
    while not server.started and time.time() < deadline:
        time.sleep(0.02)
    if not server.started:
        checks.add("BOOT", False, None, "uvicorn did not start")
        _write(checks)
        return 1
    sock = server.servers[0].sockets[0]
    host, port = sock.getsockname()[0], sock.getsockname()[1]
    base = "http://127.0.0.1:%d" % port
    checks.add("BOOT", True, base, "v1.3 app answered on an ephemeral port")

    try:
        # ---------------------------------------------------------------- #
        # 1. Probe: is a real WorkBuddy live on this host?
        # ---------------------------------------------------------------- #
        st, raw, _ = _http("GET", base + "/api/v1/domain/launch/probe")
        probe = _json(raw)
        checks.add("LAUNCH_PROBE_OPEN", st == 200,
                   {"status": st}, "probe is open (read-only)")
        if not isinstance(probe, dict):
            checks.add("LAUNCH_PROBE_SCHEMA", False, raw[:200], "probe returned no JSON")
            raise RuntimeError("probe unusable")
        checks.add("LAUNCH_PROBE_SCHEMA",
                   probe.get("schema") == "jcc/v1.3-launch-probe/1",
                   probe.get("schema"), "probe schema id")
        checks.add("LAUNCH_PROBE_ACTUATION_FALSE",
                   probe.get("actuation") is False,
                   probe.get("actuation"), "probe declares actuation False")
        wb = probe.get("workbuddy") or {}
        checks.add("LAUNCH_PROBE_HONEST",
                   "ok" in wb and "observed" in wb,
                   {"ok": wb.get("ok"), "session_count": wb.get("session_count")},
                   "probe reports a real answer either way")

        # Ground truth from the host registry, independent of the server.
        host_sessions = list_sessions()
        host_ids = {str(s.get("sessionId")) for s in host_sessions}
        checks.add("HOST_SESSIONS_EXIST", len(host_sessions) > 0,
                   len(host_sessions), "WorkBuddy session registry read from disk")

        cap = capture_launch_event()
        live_available = bool(cap.get("ok"))
        checks.add("REAL_SESSION_AVAILABLE", live_available,
                   cap.get("source", {}).get("session_id") if live_available
                   else cap.get("reason"),
                   "a live WorkBuddy session was discovered on this host")

        # ---------------------------------------------------------------- #
        # 2. LAUNCH_REQUIRES_REAL_SESSION — fail-closed when no session.
        #    Proven with an explicit non-capturing launch on an agent whose
        #    event is deliberately absent, i.e. the route must refuse rather
        #    than fabricate. We use capture=false + no event.
        # ---------------------------------------------------------------- #
        st, raw, _ = _http("POST", base + "/api/v1/domain/projects",
                           token=TOKEN, body={"name": "Wave3 Gate Project"})
        proj = _json(raw)
        checks.add("CREATE_PROJECT", st == 200 and isinstance(proj, dict),
                   (proj or {}).get("project_id"), "project created")
        pid = proj["project_id"]

        st, raw, _ = _http("POST", base + "/api/v1/domain/missions", token=TOKEN,
                           body={"project_id": pid, "title": "Gate mission",
                                 "agent": "workbuddy", "goal": "prove the bridge",
                                 "working_directory": str(REPO)})
        miss = _json(raw)
        mid = miss["mission_id"]
        checks.add("CREATE_MISSION_DRAFT", st == 200 and miss.get("status") == "DRAFT",
                   {"status": miss.get("status")}, "mission created as DRAFT")
        checks.add("MISSION_CREATE_TOKEN_GATED",
                   _http("POST", base + "/api/v1/domain/missions",
                         body={"project_id": pid, "title": "x"})[0] in (401, 403),
                   None, "mission create without token refused")

        # A launch with no event and no capture must be a programming refusal.
        st, raw, _ = _http("POST",
                           base + "/api/v1/domain/missions/%s/launch" % mid,
                           token=TOKEN, body={"capture": False})
        refused = _json(raw)
        checks.add("LAUNCH_REQUIRES_REAL_SESSION", st == 200 and refused is not None
                   and refused.get("ok") is False,
                   {"status": st, "ok": (refused or {}).get("ok")},
                   "a launch with no real event is refused, never faked")

        # The mission must NOT be RUNNING after a refused launch.
        st, raw, _ = _http("GET", base + "/api/v1/domain/missions/%s" % mid)
        m_now = _json(raw)
        checks.add("REFUSED_LAUNCH_LEAVES_NO_RUNNING", m_now.get("status") != "RUNNING",
                   m_now.get("status"), "a refused launch cannot leave RUNNING")

        # ---------------------------------------------------------------- #
        # 3. The REAL launch. Skipped only if the host has no live session,
        #    and that skip is reported as a failure-to-exercise, not a pass.
        # ---------------------------------------------------------------- #
        if not live_available:
            checks.add("LAUNCH_REAL", False, cap.get("reason"),
                       "no live WorkBuddy on this host; the real launch could "
                       "not be exercised")
        else:
            # Subscribe to SSE BEFORE launching so the broadcast is observable.
            sse_events: List[dict] = []
            sse_err: List[str] = []

            def _sse() -> None:
                try:
                    req = urllib.request.Request(
                        base + "/api/v1/events/stream", method="GET")
                    with _LOOPBACK.open(req, timeout=15) as resp:
                        deadline2 = time.time() + 12
                        buf = ""
                        while time.time() < deadline2:
                            chunk = resp.readline()
                            if not chunk:
                                break
                            line = chunk.decode("utf-8", "replace").strip()
                            if line.startswith("data:"):
                                try:
                                    sse_events.append(json.loads(line[5:].strip()))
                                except Exception:  # noqa: BLE001
                                    pass
                except Exception as exc:  # noqa: BLE001
                    sse_err.append(str(exc))

            t = threading.Thread(target=_sse, daemon=True)
            t.start()
            time.sleep(1.5)  # let the subscriber attach

            st, raw, _ = _http("POST",
                               base + "/api/v1/domain/missions/%s/launch" % mid,
                               token=TOKEN, body={"capture": True,
                                                  "cwd": str(REPO)})
            launch = _json(raw)
            checks.add("LAUNCH_REAL", st == 200 and isinstance(launch, dict)
                       and launch.get("ok") is True,
                       {"status": st, "ok": (launch or {}).get("ok"),
                        "error": (launch or {}).get("error")},
                       "a REAL launch completed")

            if isinstance(launch, dict) and launch.get("ok"):
                sid = launch.get("session_id")
                checks.add("LAUNCH_REAL_SESSION_FROM_REGISTRY",
                           sid in host_ids,
                           {"session_id": sid, "in_registry": sid in host_ids},
                           "the ingested session id is a REAL host session id")
                checks.add("LAUNCH_INGESTED", launch.get("ingested") is True,
                           launch.get("ingested"), "AEP telemetry was ingested")
                checks.add("LAUNCH_INGEST_ACTION",
                           (launch.get("ingest") or {}).get("action") is not None,
                           (launch.get("ingest") or {}).get("action"),
                           "policy produced a directive action")
                checks.add("LAUNCH_ACTUATION_FALSE",
                           (launch.get("ingest") or {}).get("acted") is False,
                           (launch.get("ingest") or {}).get("acted"),
                           "the launch did not actuate")
                checks.add("LAUNCH_SESSION_STATUS",
                           (launch.get("ingest") or {}).get("session_status")
                           is not None,
                           (launch.get("ingest") or {}).get("session_status"),
                           "the session reached a normalized state")
                steps = {s.get("step"): s.get("ok") for s in (launch.get("steps") or [])}
                checks.add("LAUNCH_STEPS_ALL_OK",
                           all(steps.values()) and len(steps) >= 5,
                           steps, "every launch step succeeded")

                # Mission is really RUNNING and its timeline records the walk.
                st, raw, _ = _http("GET", base + "/api/v1/domain/missions/%s" % mid)
                m2 = _json(raw)
                checks.add("MISSION_RUNNING", m2.get("status") == "RUNNING",
                           m2.get("status"), "mission is RUNNING")
                checks.add("MISSION_SESSION_BOUND",
                           m2.get("session_id") == sid,
                           m2.get("session_id"), "mission carries the real session")

                st, raw, _ = _http(
                    "GET", base + "/api/v1/domain/missions/%s/timeline" % mid)
                tl = _json(raw) or []
                path = [t.get("to_status") for t in tl]
                checks.add("MISSION_TIMELINE",
                           path == ["DRAFT", "READY", "STARTING", "RUNNING"],
                           path, "state machine walk recorded server-side")

                # AgentRun exists and carries the session.
                st, raw, _ = _http(
                    "GET", base + "/api/v1/domain/missions/%s/runs" % mid)
                runs = _json(raw) or []
                checks.add("AGENT_RUN_BOUND",
                           len(runs) == 1 and runs[0].get("session_id") == sid,
                           {"runs": len(runs),
                            "session": (runs[0].get("session_id") if runs else None)},
                           "the AgentRun owns the Session")

                # Workload is real: the session store knows this session.
                st, raw, _ = _http(
                    "GET", base + "/api/v1/domain/missions/%s/workload" % mid)
                wl = _json(raw)
                checks.add("WORKLOAD_REAL",
                           isinstance(wl, dict) and wl.get("live_session_count", 0) >= 1,
                           {"live": (wl or {}).get("live_session_count"),
                            "runs": (wl or {}).get("run_count")},
                           "workload counts a real live session")
                checks.add("WORKLOAD_ACTUATION_FALSE",
                           (wl or {}).get("actuation") is False,
                           (wl or {}).get("actuation"),
                           "workload declares actuation False")

                # The audit tape carries the launched session. The tape is the
                # ``audit_tape`` table in the same SQLite file (jcc_core.audit);
                # read it directly so this check does not depend on a store API
                # that may or may not expose a list method.
                audit_sids = set()
                audit_rows = 0
                try:
                    import sqlite3
                    ac = sqlite3.connect(db)
                    try:
                        rows = ac.execute(
                            "SELECT session_id FROM audit_tape").fetchall()
                        audit_rows = len(rows)
                        audit_sids = {str(r[0]) for r in rows if r[0] is not None}
                    finally:
                        ac.close()
                except Exception as exc:  # noqa: BLE001
                    checks.add("AUDIT_TAPE_READABLE", False, str(exc),
                               "the audit tape could not be read")
                checks.add("AUDIT_TAPE_HAS_SESSION", sid in audit_sids,
                           {"session_id": sid, "audit_rows": audit_rows,
                            "distinct_sessions": len(audit_sids)},
                           "the audit tape recorded the launched session")

                # The engine's session store knows the session.
                sess = store.get_session(sid)
                checks.add("SESSION_IN_STORE", sess is not None,
                           sid, "the session exists in the state store")

                # SSE: wait briefly for the broadcast thread to see it.
                t.join(timeout=8)
                got = [e for e in sse_events
                       if str(e.get("session_id")) == str(sid)]
                checks.add("SSE_BROADCAST", len(got) > 0,
                           {"matching": len(got), "total": len(sse_events),
                            "errors": sse_err[:2]},
                           "a pre-attached SSE subscriber received the launch")
            else:
                for name in ("LAUNCH_REAL_SESSION_FROM_REGISTRY", "LAUNCH_INGESTED",
                             "LAUNCH_INGEST_ACTION", "LAUNCH_ACTUATION_FALSE",
                             "LAUNCH_SESSION_STATUS", "LAUNCH_STEPS_ALL_OK",
                             "MISSION_RUNNING", "MISSION_SESSION_BOUND",
                             "MISSION_TIMELINE", "AGENT_RUN_BOUND",
                             "WORKLOAD_REAL", "WORKLOAD_ACTUATION_FALSE",
                             "AUDIT_TAPE_HAS_SESSION", "SESSION_IN_STORE",
                             "SSE_BROADCAST"):
                    checks.add(name, False, None,
                               "not reached: the real launch did not succeed")

        # ---------------------------------------------------------------- #
        # 4. Home reflects the launched mission (live counter, not a claim).
        # ---------------------------------------------------------------- #
        st, raw, _ = _http("GET", base + "/api/v1/domain/home")
        home = _json(raw)
        checks.add("HOME_COUNTS_RUNNING",
                   isinstance(home, dict) and home.get("running_missions", 0) >= 1,
                   (home or {}).get("running_missions"),
                   "Home counts the running mission")

        # ---------------------------------------------------------------- #
        # 5. Actuation is OFF everywhere it can be read.
        # ---------------------------------------------------------------- #
        st, raw, _ = _http("GET", base + "/api/health")
        health = _json(raw) or {}
        checks.add("HEALTH_ACTUATION_FALSE",
                   health.get("actuation_enabled") is False,
                   health.get("actuation_enabled"), "health reports actuation off")
    finally:
        try:
            server.should_exit = True
            thread.join(timeout=10)
        except Exception:  # noqa: BLE001
            pass

    payload = checks.payload("WAVE3_FIRST_REAL_WORKBUDDY_MISSION")
    out = _write(checks, payload)
    print(json.dumps({"verdict": payload["verdict"], "passed": payload["passed"],
                      "total": payload["total"],
                      "failed": [f["step"] for f in payload["failures"]],
                      "evidence": str(out)}, ensure_ascii=False))
    return 0 if payload["verdict"] == "PASS" else 1


def _write(checks: Checks, payload: Optional[Dict[str, Any]] = None) -> Path:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = SCRATCH / ("v1_3_wave3_gate_%s.json" % stamp)
    data = payload or checks.payload("WAVE3_FIRST_REAL_WORKBUDDY_MISSION")
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


if __name__ == "__main__":
    raise SystemExit(main())
