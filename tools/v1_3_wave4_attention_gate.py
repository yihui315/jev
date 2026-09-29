"""WAVE 4 PRODUCT GATE — NEEDS ATTENTION + CONTROL (owner: Metric).

THE GATE'S QUESTION
-------------------
"The inbox is a control surface that tells the truth about what it can do: it
separates what POLICY PERMITS from what THIS HOST CAN DO RIGHT NOW, it never
renders an unknown prerequisite as fine, a preview never mutates, and Actuation
is still False."

It runs a REAL v1.3 ASGI app on an ephemeral port with an isolated temp DB and
drives it over REAL HTTP.

FAIL-CLOSED PHILOSOPHY
----------------------
Every check can fail, and several are designed to fail if the code regresses to a
lie:

  * ``PREVIEW_DOES_NOT_MUTATE`` — a dry_run resolve must leave the domain DB
    byte-identical (sha256 before/after). This is the falsifier for "the preview
    lied".
  * ``UNKNOWN_NEVER_EXECUTABLE`` — for every item whose action is UNKNOWN, it must
    not also be reported as executable. "Could not check" must not look fine.
  * ``ACTUATION_DISABLED_BLOCKS_RESUME`` — every high-risk action is
    BLOCKED/ACTUATION_DISABLED while actuation is off.
  * ``POLICY_VS_EXECUTABLE_ARE_SEPARATE_FIELDS`` — the payload carries both, so no
    consumer can see only one.
  * ``APPLY_REQUIRES_TOKEN`` — the write is gated.
  * ``APPLY_TRANSITIONS_VIA_STATE_MACHINE`` — a verify actually moves the mission
    and clears its attention, and a resume does NOT (it is blocked).

Evidence is written to ``.workbuddy/scratch/v1_3_wave4_gate_<stamp>.json``.
"""
from __future__ import annotations

import hashlib
import json
import os
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

TOKEN = "wave4-gate-control-token"
TOKEN_ENV = "JCC_CONTROL_TOKEN"
SCRATCH = REPO / ".workbuddy" / "scratch"

_LOOPBACK = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _http(method: str, url: str, *, token: Optional[str] = None,
          body: Optional[dict] = None, timeout: float = 20.0):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-JCC-Token", token)
    try:
        with _LOOPBACK.open(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def _json(raw: str) -> Any:
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return None


def _sha(path: str) -> Optional[str]:
    if not os.path.exists(path):
        return None
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class Checks:
    def __init__(self) -> None:
        self.steps: List[Dict[str, Any]] = []

    def add(self, step: str, ok: bool, value: Any = None, note: str = "") -> None:
        self.steps.append({"step": step, "ok": bool(ok), "value": value, "note": note})

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


def _mk_attention(checks, base, kind, name="W4"):
    st, raw = _http("POST", base + "/api/v1/domain/projects", token=TOKEN,
                    body={"name": name})
    proj = _json(raw)
    checks.add("CREATE_PROJECT", st == 200 and isinstance(proj, dict), st, kind)
    st, raw = _http("POST", base + "/api/v1/domain/missions", token=TOKEN,
                    body={"project_id": proj["project_id"], "title": "M"})
    mid = _json(raw)["mission_id"]
    for to in ("READY", "STARTING", "RUNNING"):
        _http("POST", base + "/api/v1/domain/missions/%s/transition" % mid,
              token=TOKEN, body={"to": to})
    st, raw = _http("POST", base + "/api/v1/domain/missions/%s/transition" % mid,
                    token=TOKEN, body={"to": "NEEDS_ATTENTION",
                                       "attention_kind": kind,
                                       "attention_reason": "gate %s" % kind})
    return mid


def main() -> int:
    checks = Checks()
    tmp = Path(tempfile.mkdtemp(prefix="w4gate_"))
    db = str(tmp / "jcc.db")

    os.environ[TOKEN_ENV] = TOKEN

    import uvicorn
    from jcc_core.actuator import Actuator
    from jcc_core.config import Config, ServerConfig
    from jcc_core.core import CoreEngine
    from jcc_core.providers import build_provider
    from jcc_core.server import create_app
    from jcc_core.state_store import StateStore

    cfg = Config(server=ServerConfig(port=0), state_store_db_path=db,
                 control_token_env=TOKEN_ENV)
    store = StateStore(db)
    engine = CoreEngine(store, build_provider(cfg), Actuator(enabled=False), cfg)
    app = create_app(engine, cfg)

    uvcfg = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(uvcfg)
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.time() + 25
    while not server.started and time.time() < deadline:
        time.sleep(0.02)
    if not server.started:
        checks.add("BOOT", False, None, "uvicorn did not start")
        _write(checks)
        return 1
    host, port = server.servers[0].sockets[0].getsockname()[:2]
    base = "http://127.0.0.1:%d" % port
    checks.add("BOOT", True, base, "v1.3 app answered on an ephemeral port")

    try:
        # 1. resolutions read is open and shaped
        st, raw = _http("GET", base + "/api/v1/domain/needs-attention/resolutions")
        doc = _json(raw)
        checks.add("RESOLUTIONS_OPEN", st == 200 and isinstance(doc, dict), st,
                   "the resolution read is open (read-only)")
        checks.add("RESOLUTIONS_SCHEMA",
                   isinstance(doc, dict)
                   and doc.get("schema") == "jcc/v1.3-attention-resolutions/1",
                   doc.get("schema") if isinstance(doc, dict) else None)
        checks.add("RESOLUTIONS_ACTUATION_FALSE",
                   isinstance(doc, dict) and doc.get("actuation_enabled") is False,
                   doc.get("actuation_enabled") if isinstance(doc, dict) else None,
                   "actuation is reported as False")

        # 2. build one item of each interesting kind
        stuck = _mk_attention(checks, base, "AGENT_STUCK", name="W4A")
        verify = _mk_attention(checks, base, "VERIFICATION_REQUIRED", name="W4B")
        conn = _mk_attention(checks, base, "CONNECTION_LOST", name="W4C")

        st, raw = _http("GET", base + "/api/v1/domain/needs-attention/resolutions")
        doc = _json(raw)
        by_id = {i["mission_id"]: i for i in doc["items"]}

        # 3. POLICY vs EXECUTABLE are separate fields, both present
        stuck_row = by_id.get(stuck) or {"actions": []}
        a0 = (stuck_row["actions"] or [{}])[0]
        checks.add("POLICY_VS_EXECUTABLE_ARE_SEPARATE_FIELDS",
                   "policy_permitted" in a0 and "executability" in a0,
                   {"action": a0.get("action"),
                    "policy_permitted": a0.get("policy_permitted"),
                    "executability": a0.get("executability")},
                   "an action carries BOTH policy_permitted and executability")

        # 4. resume is policy-permitted (it exists) but BLOCKED by actuation
        checks.add("RESUME_POLICY_PERMITTED",
                   a0.get("action") == "resume" and a0.get("policy_permitted") is True,
                   a0.get("policy_permitted"), "resume is a policy action")
        checks.add("ACTUATION_DISABLED_BLOCKS_RESUME",
                   a0.get("executability") == "BLOCKED"
                   and a0.get("blocked_reason") == "ACTUATION_DISABLED",
                   {"executability": a0.get("executability"),
                    "blocked_reason": a0.get("blocked_reason")},
                   "high-risk action blocked while actuation is off")

        # 5. UNKNOWN never shows as EXECUTABLE
        unknown_ok = True
        for i in doc["items"]:
            for a in i.get("actions", []):
                if a.get("executability") == "UNKNOWN" and a.get("policy_permitted") \
                        and a.get("blocked_reason") is None:
                    unknown_ok = True  # UNKNOWN with no blocked_reason is honest
                if a.get("executability") == "EXECUTABLE" \
                        and a.get("blocked_reason") is not None:
                    unknown_ok = False
        conn_row = by_id.get(conn) or {"actions": []}
        conn_a = (conn_row["actions"] or [{}])[0]
        checks.add("UNKNOWN_NEVER_EXECUTABLE",
                   unknown_ok and conn_a.get("executability") in
                   ("UNKNOWN", "BLOCKED"),
                   {"conn_executability": conn_a.get("executability")},
                   "a never-checked connection is UNKNOWN or BLOCKED, never EXECUTABLE")

        # 6. PREVIEW DOES NOT MUTATE (the central falsifier)
        before = _sha(db)
        st, raw = _http("POST",
                        base + "/api/v1/domain/needs-attention/%s/resolve" % verify,
                        token=TOKEN, body={"action": "verify", "dry_run": True})
        prev = _json(raw)
        after = _sha(db)
        checks.add("PREVIEW_RETURNS_PLAN",
                   st == 200 and isinstance(prev, dict)
                   and prev.get("dry_run") is True and bool(prev.get("plan")),
                   {"dry_run": prev.get("dry_run") if isinstance(prev, dict) else None,
                    "plan_len": len(prev.get("plan", [])) if isinstance(prev, dict) else 0},
                   "preview returns exactly what would change")
        checks.add("PREVIEW_DOES_NOT_MUTATE", before is not None and before == after,
                   {"before": (before or "")[:12], "after": (after or "")[:12]},
                   "the domain DB is byte-identical across a dry_run")

        # 7. apply requires a token
        st, _ = _http("POST",
                      base + "/api/v1/domain/needs-attention/%s/resolve" % verify,
                      body={"action": "verify", "dry_run": False})
        checks.add("APPLY_REQUIRES_TOKEN", st in (401, 403), st,
                   "the resolve write is token-gated")

        # 8. apply verify transitions the mission and clears attention
        st, raw = _http("POST",
                        base + "/api/v1/domain/needs-attention/%s/resolve" % verify,
                        token=TOKEN, body={"action": "verify", "dry_run": False})
        applied = _json(raw)
        st2, raw2 = _http("GET", base + "/api/v1/domain/missions/%s" % verify)
        miss = _json(raw2)
        checks.add("APPLY_VERIFY_TRANSITIONS",
                   st == 200 and isinstance(applied, dict)
                   and applied.get("applied") is True
                   and (applied.get("result") or {}).get("mission_status") == "RUNNING",
                   applied.get("result") if isinstance(applied, dict) else None,
                   "verify moves the mission back to RUNNING")
        checks.add("APPLY_CLEARS_ATTENTION",
                   isinstance(miss, dict) and miss.get("status") == "RUNNING"
                   and miss.get("attention_kind") is None,
                   {"status": miss.get("status") if isinstance(miss, dict) else None},
                   "attention is cleared on resolution")

        # 9. a blocked apply does not move the mission
        st, raw = _http("POST",
                        base + "/api/v1/domain/needs-attention/%s/resolve" % stuck,
                        token=TOKEN, body={"action": "resume", "dry_run": False})
        blocked = _json(raw)
        st2, raw2 = _http("GET", base + "/api/v1/domain/missions/%s" % stuck)
        still = _json(raw2)
        checks.add("BLOCKED_APPLY_DOES_NOT_MOVE",
                   isinstance(blocked, dict) and blocked.get("ok") is False
                   and blocked.get("applied") is False
                   and isinstance(still, dict) and still.get("status") == "NEEDS_ATTENTION",
                   {"ok": blocked.get("ok") if isinstance(blocked, dict) else None,
                    "status": still.get("status") if isinstance(still, dict) else None},
                   "a blocked action reports honestly and changes nothing")

        # 10. resolve on a non-attention mission is refused, not 500
        st, raw = _http("POST", base + "/api/v1/domain/projects", token=TOKEN,
                        body={"name": "W4D"})
        pid = _json(raw)["project_id"]
        st, raw = _http("POST", base + "/api/v1/domain/missions", token=TOKEN,
                        body={"project_id": pid, "title": "M"})
        draft = _json(raw)["mission_id"]
        st, raw = _http("POST",
                        base + "/api/v1/domain/needs-attention/%s/resolve" % draft,
                        token=TOKEN, body={"action": "verify", "dry_run": False})
        nn = _json(raw)
        checks.add("RESOLVE_NOT_IN_ATTENTION_REFUSED",
                   st == 200 and isinstance(nn, dict) and nn.get("ok") is False
                   and nn.get("blocked_reason") == "NOT_IN_ATTENTION",
                   nn.get("blocked_reason") if isinstance(nn, dict) else None,
                   "a stale UI cannot act on an item that is not in attention")

        # 11. actuation is still False everywhere
        st, raw = _http("GET", base + "/api/health")
        health = _json(raw)
        checks.add("HEALTH_ACTUATION_FALSE",
                   isinstance(health, dict) and health.get("actuation_enabled") is False,
                   health.get("actuation_enabled") if isinstance(health, dict) else None,
                   "Actuation stays disabled")

    except Exception as exc:  # noqa: BLE001
        checks.add("GATE_EXCEPTION", False, repr(exc), "the gate raised")
    finally:
        server.should_exit = True

    _write(checks)
    p = checks.payload("WAVE4_NEEDS_ATTENTION_CONTROL")
    print(json.dumps({"verdict": p["verdict"], "passed": p["passed"],
                      "total": p["total"],
                      "failed": [f["step"] for f in p["failures"]],
                      "evidence": str(_last_path())}))
    return 0 if p["verdict"] == "PASS" else 1


_LAST: List[Path] = []


def _last_path() -> Path:
    return _LAST[-1] if _LAST else Path()


def _write(checks: Checks) -> None:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = SCRATCH / ("v1_3_wave4_gate_%s.json" % stamp)
    path.write_text(json.dumps(checks.payload("WAVE4_NEEDS_ATTENTION_CONTROL"),
                               indent=2), encoding="utf-8")
    _LAST.append(path)


if __name__ == "__main__":
    raise SystemExit(main())
