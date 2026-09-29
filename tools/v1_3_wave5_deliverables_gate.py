"""WAVE 5 PRODUCT GATE — DELIVERABLES (owner: Metric).

THE GATE'S QUESTION
-------------------
"A deliverable is an object with an honest lifecycle: PRODUCED (the agent emitted
it) and VERIFIED (a human accepted it) are different claims, acceptance is an
explicit token-gated recorded act and is final, the aggregate counts accepted
artifacts separately from raw ones, and a deliverable has an addressable identity."

It runs a REAL v1.3 ASGI app on an ephemeral port with an isolated temp DB and
drives it over REAL HTTP.

FAIL-CLOSED PHILOSOPHY
----------------------
Every check can fail, and several are designed to fail if the code regresses to a
lie:

  * ``PRODUCED_IS_NOT_VERIFIED`` — creating an artifact must NOT increase the
    `verified` count. This is the falsifier for "raw output counted as a
    deliverable".
  * ``VERIFY_REQUIRES_TOKEN`` — a verdict without the control token must not land.
  * ``VERIFY_NAMES_A_HUMAN`` — a verdict with no `decided_by` must be refused.
  * ``VERDICT_IS_FINAL`` — an already-decided artifact cannot be re-decided.
  * ``FILTER_EXCLUDES_UNVERIFIED`` — `status=VERIFIED` must not return a PRODUCED
    row.
  * ``UNKNOWN_ID_IS_404`` — a made-up deliverable id must 404, never fabricate.
  * ``UNKNOWN_STATUS_IS_422`` — a filter that cannot be honoured must be refused,
    not silently dropped.

Evidence is written to ``.workbuddy/scratch/v1_3_wave5_gate_<stamp>.json``.
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

TOKEN = "wave5-gate-control-token"
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


def _write(checks: Checks) -> str:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = SCRATCH / ("v1_3_wave5_gate_%s.json" % stamp)
    out.write_text(json.dumps(checks.payload("WAVE5_DELIVERABLES"), indent=2),
                   encoding="utf-8")
    return str(out)


def _mk_artifact(base, name="out.txt"):
    _, raw = _http("POST", base + "/api/v1/domain/projects", token=TOKEN,
                   body={"name": "W5"})
    proj = _json(raw)
    _, raw = _http("POST", base + "/api/v1/domain/missions", token=TOKEN,
                   body={"project_id": proj["project_id"], "title": "Deliverable Mission"})
    mid = _json(raw)["mission_id"]
    st, raw = _http("POST", base + "/api/v1/domain/missions/%s/artifacts" % mid,
                    token=TOKEN, body={"name": name, "kind": "file",
                                       "uri": "F:/out/%s" % name,
                                       "sha256": "b" * 64, "bytes": 12})
    return mid, st, _json(raw)


def main() -> int:
    checks = Checks()
    tmp = Path(tempfile.mkdtemp(prefix="w5gate_"))
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
        # 1. the deliverable view is open and shaped
        st, raw = _http("GET", base + "/api/v1/domain/deliverables")
        rows = _json(raw)
        checks.add("DELIVERABLES_OPEN", st == 200 and isinstance(rows, list), st,
                   "the deliverable view is open (read-only)")

        # 2. build one artifact
        mid, st, art = _mk_artifact(base, name="one.txt")
        checks.add("CREATE_ARTIFACT", st == 200 and isinstance(art, dict)
                   and art.get("status") == "PRODUCED", st,
                   "an artifact is created PRODUCED, not pre-accepted")
        aid = art["artifact_id"]

        # 3. identity: by-id read returns the exact artifact; unknown -> 404
        st, raw = _http("GET", base + "/api/v1/domain/artifacts/%s" % aid)
        got = _json(raw)
        checks.add("DELIVERABLE_HAS_IDENTITY",
                   st == 200 and isinstance(got, dict)
                   and got.get("artifact_id") == aid and got.get("mission_id") == mid,
                   st, "a deliverable is addressable by id")
        st, _ = _http("GET", base + "/api/v1/domain/artifacts/art_made_up")
        checks.add("UNKNOWN_ID_IS_404", st == 404, st,
                   "an unknown deliverable id 404s, never fabricates")

        # 4. the honest aggregate: PRODUCED is not VERIFIED
        _, raw = _http("GET", base + "/api/v1/domain/home")
        home = _json(raw)
        dv = (home or {}).get("deliverables") or {}
        checks.add("HOME_CARRIES_HONEST_DELIVERABLES",
                   isinstance(home, dict) and "deliverables" in home
                   and set(dv) >= {"produced", "verified", "rejected", "total"},
                   dv, "Home splits produced from verified")
        v0 = dv.get("verified")
        checks.add("PRODUCED_IS_NOT_VERIFIED",
                   v0 == 0 and dv.get("produced", 0) >= 1 and dv.get("total", 0) >= 1,
                   dv, "a PRODUCED artifact is not counted as an accepted deliverable")

        # 5. verify requires a token
        st, _ = _http("POST", base + "/api/v1/domain/artifacts/%s/verify" % aid,
                      body={"status": "VERIFIED", "decided_by": "alice"})
        checks.add("VERIFY_REQUIRES_TOKEN", st in (401, 403), st,
                   "a verdict without the control token does not land")
        _, raw = _http("GET", base + "/api/v1/domain/artifacts/%s" % aid)
        checks.add("TOKENLESS_VERIFY_DID_NOT_LAND",
                   (_json(raw) or {}).get("status") == "PRODUCED",
                   (_json(raw) or {}).get("status"),
                   "the artifact is still PRODUCED after a tokenless verify")

        # 6. verify requires a named human
        st, raw = _http("POST", base + "/api/v1/domain/artifacts/%s/verify" % aid,
                        token=TOKEN, body={"status": "VERIFIED"})
        checks.add("VERIFY_NAMES_A_HUMAN", st == 422, st,
                   "a verdict must name who decided")

        # 7. verify sets the verdict and records who
        st, raw = _http("POST", base + "/api/v1/domain/artifacts/%s/verify" % aid,
                        token=TOKEN, body={"status": "VERIFIED", "decided_by": "alice",
                                           "note": "looks good"})
        res = _json(raw)
        checks.add("VERIFY_RECORDS_WHO",
                   st == 200 and isinstance(res, dict) and res.get("status") == "VERIFIED"
                   and (res.get("provenance") or {}).get("verified_by") == "alice",
                   {"status": res.get("status") if isinstance(res, dict) else None,
                    "verified_by": (res or {}).get("provenance", {}).get("verified_by")
                    if isinstance(res, dict) else None},
                   "acceptance is recorded with the human's name")

        # 8. a verdict is final
        st, _ = _http("POST", base + "/api/v1/domain/artifacts/%s/verify" % aid,
                      token=TOKEN, body={"status": "REJECTED", "decided_by": "bob"})
        checks.add("VERDICT_IS_FINAL", st == 422, st,
                   "an already-decided deliverable cannot be re-decided")

        # 9. the aggregate now counts it as verified
        _, raw = _http("GET", base + "/api/v1/domain/home")
        dv2 = (_json(raw) or {}).get("deliverables") or {}
        checks.add("VERIFIED_INCREASES_AFTER_ACCEPTANCE",
                   dv2.get("verified") == (v0 or 0) + 1, dv2,
                   "accepting a deliverable moves the honest headline")

        # 10. a filter excludes unverified rows
        mid2, st2, art2 = _mk_artifact(base, name="two.txt")
        aid2 = art2["artifact_id"]
        st, raw = _http("GET", base + "/api/v1/domain/deliverables?status=VERIFIED")
        listed = _json(raw) or []
        ids = {r.get("artifact_id") for r in listed}
        checks.add("FILTER_EXCLUDES_UNVERIFIED",
                   st == 200 and aid2 not in ids,
                   {"listed": len(listed), "unverified_absent": aid2 not in ids},
                   "status=VERIFIED does not return a PRODUCED row")

        # 11. an unhonourable filter is refused, not silently dropped
        st, _ = _http("GET", base + "/api/v1/domain/deliverables?status=NONSENSE")
        checks.add("UNKNOWN_STATUS_IS_422", st == 422, st,
                   "a filter that cannot be honoured is refused")

        # 12. the deliverable view carries the mission title (no client fan-out)
        st, raw = _http("GET", base + "/api/v1/domain/deliverables?mission_id=%s" % mid2)
        rows2 = _json(raw) or []
        checks.add("VIEW_CARRIES_MISSION_TITLE",
                   st == 200 and len(rows2) == 1
                   and rows2[0].get("mission_title") == "Deliverable Mission",
                   {"n": len(rows2),
                    "title": rows2[0].get("mission_title") if rows2 else None},
                   "the server-side view includes the mission title")

        # 13. actuation stays off on this build
        _, raw = _http("GET", base + "/api/v1/domain/home")
        checks.add("HOME_ACTUATION_FALSE",
                   (_json(raw) or {}).get("actuation_enabled") is False,
                   (_json(raw) or {}).get("actuation_enabled"),
                   "actuation is still False")
    finally:
        try:
            server.should_exit = True
        except Exception:  # noqa: BLE001
            pass

    out = _write(checks)
    print("gate WAVE5_DELIVERABLES verdict=%s passed=%d/%d"
          % (checks.payload("x")["verdict"], checks.payload("x")["passed"],
             len(checks.steps)))
    for s in checks.failed:
        print("  FAIL %s: %s" % (s["step"], s["value"]))
    print("wrote", out)
    return 0 if not checks.failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
