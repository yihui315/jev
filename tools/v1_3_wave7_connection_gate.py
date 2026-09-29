"""WAVE 7 PRODUCT GATE — AGENT CONNECTION VERIFICATION (owner: Metric).

THE GATE'S QUESTION
-------------------
"Is a connection capability MEASURED rather than asserted: does verification run a
real probe and record only what it OBSERVED, does the caller never get to state
the outcome, is the effectful-action gate (CONTROL_VERIFIED) refused when it cites
no evidence, and does a v1.3 probe honestly refrain from claiming a control path it
never exercised?"

It runs a REAL v1.3 ASGI app on an ephemeral port with an isolated temp DB and
drives it over REAL HTTP.

FAIL-CLOSED PHILOSOPHY
----------------------
Every check can fail, and several are designed to fail if the code regresses to a
lie:

  * ``PROBE_NEVER_CONTROL_VERIFIED`` — the in-process probe must never return
    CONTROL_VERIFIED for any known agent. Actuation is off, so claiming a control
    path would be a lie. This is the anti-over-claim falsifier.
  * ``VERIFY_IGNORES_CALLER_KIND`` — POSTing {kind: CONTROL_VERIFIED} to /verify
    must record what the PROBE observed, not the body.
  * ``VERIFY_RECORDS_MEASURED_EVIDENCE`` — a verified connection must carry an
    evidence_ref (a re-checkable digest), else "measured" is a bare word.
  * ``BARE_CONTROL_VERIFIED_REFUSED`` — the effectful gate cannot be satisfied by a
    typed-in assertion.
  * ``CITED_CONTROL_VERIFIED_ACCEPTED`` — non-vacuity: the hardening refuses a
    BARE assertion, NOT the kind. Without this the fix is indistinguishable from a
    regression that disabled the route.
  * ``PROVENANCE_DISTINGUISHES_ASSERTED`` — a human-typed capability must report
    ASSERTED, not MEASURED.
  * ``VERIFY_REQUIRES_TOKEN`` / ``TOKENLESS_VERIFY_DID_NOT_LAND`` — verification is
    a write and must be token-gated, and a tokenless attempt must leave no trace.
  * ``UNKNOWN_AGENT_IS_UNKNOWN_HEALTH`` — an unobservable agent must not be drawn
    as CONNECTED.

Evidence is written to ``.workbuddy/scratch/v1_3_wave7_gate_<stamp>.json``.
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

TOKEN = "wave7-gate-control-token"
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
    out = SCRATCH / ("v1_3_wave7_gate_%s.json" % stamp)
    out.write_text(json.dumps(checks.payload("WAVE7_CONNECTION_VERIFICATION"), indent=2),
                   encoding="utf-8")
    return str(out)


def _mk_connection(base, agent):
    st, raw = _http("POST", base + "/api/v1/domain/connections", token=TOKEN,
                    body={"agent": agent})
    body = _json(raw)
    return (body or {}).get("connection_id"), st


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    checks = Checks()
    tmp = Path(tempfile.mkdtemp(prefix="w7gate_"))
    db = str(tmp / "jcc.db")

    os.environ[TOKEN_ENV] = TOKEN

    import uvicorn
    from jcc_core import capability_probe
    from jcc_core import domain as D
    from jcc_core.actuator import Actuator
    from jcc_core.config import Config, ServerConfig
    from jcc_core.core import CoreEngine
    from jcc_core.providers import build_provider
    from jcc_core.server import create_app
    from jcc_core.state_store import StateStore

    # 0. the in-process probe NEVER claims a control path (Rule 2, anti-over-claim)
    claimed = []
    for agent in list(capability_probe.KNOWN_AGENTS) + ["mystery-agent", "", "nope"]:
        try:
            res = capability_probe.probe_connection(agent)
        except Exception as exc:  # noqa: BLE001
            claimed.append({"agent": agent, "raised": type(exc).__name__})
            continue
        if res.kind == D.CapabilityKind.CONTROL_VERIFIED:
            claimed.append({"agent": agent, "kind": res.kind.value})
    checks.add("PROBE_NEVER_CONTROL_VERIFIED", not claimed, claimed,
               "a v1.3 probe can never observe a control path (actuation is off)")

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
        # 1. one connection, agent we cannot observe -> deterministic UNKNOWN
        cid, st = _mk_connection(base, "mystery-agent")
        checks.add("CREATE_CONNECTION", st == 200 and bool(cid), st,
                   "a connection is created with no capability pre-attached")

        # 2. the read is open and shaped
        st, raw = _http("GET", base + "/api/v1/domain/connections/%s" % cid)
        got = _json(raw)
        checks.add("CONNECTION_VIEW_OPEN",
                   st == 200 and isinstance(got, dict)
                   and got.get("connection_id") == cid
                   and got.get("control_verified") is False,
                   st, "one connection with per-capability provenance is open")
        st, _ = _http("GET", base + "/api/v1/domain/connections/con_made_up")
        checks.add("UNKNOWN_CONNECTION_404", st == 404, st,
                   "an unknown connection id 404s, never fabricates")

        # 3. verification is token-gated and a tokenless attempt leaves no trace
        st, _ = _http("POST", base + "/api/v1/domain/connections/%s/verify" % cid,
                      body={})
        checks.add("VERIFY_REQUIRES_TOKEN", st in (401, 403), st,
                   "verification without the control token does not land")
        _, raw = _http("GET", base + "/api/v1/domain/connections/%s" % cid)
        checks.add("TOKENLESS_VERIFY_DID_NOT_LAND",
                   (_json(raw) or {}).get("capabilities") == [],
                   len((_json(raw) or {}).get("capabilities") or []),
                   "no capability was recorded by the tokenless attempt")

        # 4. verify runs a REAL probe; the body cannot state the outcome
        st, raw = _http("POST", base + "/api/v1/domain/connections/%s/verify" % cid,
                        token=TOKEN, body={"kind": "CONTROL_VERIFIED", "probe": "auto"})
        body = _json(raw) or {}
        observed = body.get("observed") or {}
        checks.add("VERIFY_IGNORES_CALLER_KIND",
                   st == 200 and observed.get("kind") == "UNKNOWN"
                   and observed.get("reason") == "unknown_agent",
                   {"status": st, "observed_kind": observed.get("kind"),
                    "reason": observed.get("reason")},
                   "the recorded capability is what the PROBE observed, not the body")
        checks.add("VERIFY_NEVER_YIELDS_CONTROL_VERIFIED",
                   observed.get("kind") != "CONTROL_VERIFIED"
                   and body.get("control_verified") is False,
                   {"observed_kind": observed.get("kind"),
                    "control_verified": body.get("control_verified")},
                   "seeing nothing is UNKNOWN; it is never a control claim")
        checks.add("UNKNOWN_AGENT_IS_UNKNOWN_HEALTH",
                   body.get("health") == "UNKNOWN",
                   body.get("health"), "UNKNOWN never renders as CONNECTED")

        # 5. the recorded capability is MEASURED (carries a re-checkable evidence_ref)
        caps = body.get("capabilities") or []
        measured = [c for c in caps if c.get("kind") == "UNKNOWN"
                    and c.get("provenance") == "MEASURED"
                    and str(c.get("evidence_ref") or "").startswith("sha256:")]
        checks.add("VERIFY_RECORDS_MEASURED_EVIDENCE", bool(measured),
                   [{"kind": c.get("kind"), "prov": c.get("provenance"),
                     "ref": (c.get("evidence_ref") or "")[:16]} for c in caps],
                   "a measured capability carries a digest of the observation")

        # 6. an asserted capability reports ASSERTED, not MEASURED
        cid2, _ = _mk_connection(base, "codex")
        st, raw = _http("POST", base + "/api/v1/domain/connections/%s/capability" % cid2,
                        token=TOKEN, body={"kind": "TELEMETRY_ONLY"})
        a = _json(raw) or {}
        checks.add("PROVENANCE_DISTINGUISHES_ASSERTED",
                   st == 200 and a.get("provenance") == "ASSERTED",
                   {"status": st, "provenance": a.get("provenance")},
                   "a typed-in capability is ASSERTED, never dressed as MEASURED")

        # 7. the effectful gate cannot be satisfied by a bare assertion
        st, raw = _http("POST", base + "/api/v1/domain/connections/%s/capability" % cid2,
                        token=TOKEN, body={"kind": "CONTROL_VERIFIED"})
        checks.add("BARE_CONTROL_VERIFIED_REFUSED",
                   st == 422 and "evidence_ref" in raw, st,
                   "CONTROL_VERIFIED without an evidence_ref is refused (422)")
        _, raw = _http("GET", base + "/api/v1/domain/connections/%s" % cid2)
        checks.add("REFUSED_ASSERTION_DID_NOT_OPEN_THE_GATE",
                   (_json(raw) or {}).get("control_verified") is False,
                   (_json(raw) or {}).get("control_verified"),
                   "the effectful-action gate stayed shut after the refusal")

        # 8. NON-VACUITY: a CITED control-verified assertion is still accepted
        st, raw = _http("POST", base + "/api/v1/domain/connections/%s/capability" % cid2,
                        token=TOKEN, body={"kind": "CONTROL_VERIFIED",
                                           "evidence_ref": "sha256:" + "f" * 64,
                                           "detail": "external control-path evidence"})
        c = _json(raw) or {}
        checks.add("CITED_CONTROL_VERIFIED_ACCEPTED",
                   st == 200 and c.get("provenance") == "MEASURED", st,
                   "the hardening refuses a BARE assertion, not the kind itself")
        _, raw = _http("GET", base + "/api/v1/domain/connections/%s" % cid2)
        checks.add("CITED_ASSERTION_DID_OPEN_THE_GATE",
                   (_json(raw) or {}).get("control_verified") is True,
                   (_json(raw) or {}).get("control_verified"),
                   "a cited CONTROL_VERIFIED still gates in -- the route is alive")

        # 9. ordinary capability recording is unbroken by the hardening
        cid3, _ = _mk_connection(base, "hermes")
        st, _ = _http("POST", base + "/api/v1/domain/connections/%s/capability" % cid3,
                      token=TOKEN, body={"kind": "TELEMETRY_ONLY"})
        checks.add("OTHER_KINDS_STILL_ASSERTABLE", st == 200, st,
                   "the hardening did not break the ordinary recording path")

        # 10. an unknown capability kind is still rejected
        st, _ = _http("POST", base + "/api/v1/domain/connections/%s/capability" % cid3,
                      token=TOKEN, body={"kind": "NOT_A_KIND"})
        checks.add("CAPABILITY_KIND_STILL_VALIDATED", st == 422, st,
                   "kind validation was not masked by the new harden-check")

        # 11. the module under test is the one on disk (evidence provenance)
        probe_path = PKGS / "jcc_core" / "capability_probe.py"
        checks.add("PROBE_MODULE_PRESENT",
                   probe_path.is_file() and bool(_sha256(probe_path)),
                   _sha256(probe_path)[:16] if probe_path.is_file() else None,
                   "the probe module exists and is hashed into this evidence")

        # 12. actuation stays off on this build
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
    print("gate WAVE7_CONNECTION_VERIFICATION verdict=%s passed=%d/%d"
          % (checks.payload("x")["verdict"], checks.payload("x")["passed"],
             len(checks.steps)))
    for s in checks.failed:
        print("  FAIL %s: %s" % (s["step"], s["value"]))
    print("wrote", out)
    return 0 if not checks.failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
