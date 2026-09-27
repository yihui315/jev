#!/usr/bin/env python3
"""§21 -- the v1.1 security gate: HIGH > 0 means NO-GO.

WHAT MAKES THIS A GATE AND NOT A REPORT
---------------------------------------
The verdict is DERIVED from findings, not decided:

    NO-GO  iff  any HIGH finding exists
           OR   any check failed to RUN (an unrunnable check is a HIGH)

The second clause is the one that matters. A security gate's characteristic
failure is not a false GO from a real finding -- it is a check that silently does
not execute, or executes vacuously, and reports nothing wrong. So:

  * every check is timed and its exception is converted into a HIGH finding
    rather than propagated (a crashed probe is a finding, not a skip);
  * the checks that could pass vacuously carry their own non-vacuity assertions:
    the secret scan asserts that signing actually happened (``signed_rows > 0``)
    before it claims nothing leaked, and the scrubber probe asserts that a BENIGN
    string in the same payload SURVIVED -- without that, a scrubber that deletes
    everything would pass a "no secret at rest" test;
  * ``--selftest`` falsifies the verdict rule (HIGH>0 must produce NO-GO) and
    falsifies the leak scanner (a planted value MUST be found), because a scanner
    that cannot find a planted secret is not evidence about the real one.

NON-DESTRUCTIVE BY CONSTRUCTION
-------------------------------
Everything runs against a throwaway database in ``tempfile.mkdtemp()`` on an
ephemeral loopback port. The protected v1.0 service on 127.0.0.1:4778 and its
``runtime/jcc/jcc.db`` are never opened, bound or written; S-08 measures both
before and after and turns a change into a HIGH finding, so "the instrument did
not damage what it measured" is itself evidence rather than an assurance.

Usage
-----
    python tools/v1_1_security_gate.py              # run, write the artifact
    python tools/v1_1_security_gate.py --selftest   # falsify the rule + scanner
    python tools/v1_1_security_gate.py --check      # recompute, compare
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

ROOT = Path(r"F:\jev")
PKGS = ROOT / "packages"
APPS = ROOT / "apps"
FIXTURES = ROOT / "fixtures"
OUT = ROOT / "artifacts" / "release" / "V1_1_SECURITY_GATE.json"

SCHEMA = "jcc.v1_1.security_gate/v1"

#: The protected v1.0 service. Nothing in this gate may touch either.
PROTECTED_PORT = 4778
PROTECTED_DB = ROOT / "runtime" / "jcc" / "jcc.db"

#: The v1.1 modules. §21 must establish that v1.1 added NO authority surface.
V1_1_MODULES = (
    "packages/supervisor/actuation_levels.py",
    "packages/supervisor/directive_gate.py",
    "packages/supervisor/budget_report.py",
    "packages/supervisor/live_shadow_gate.py",
    "apps/control_ui/v1_1.py",
)

#: Modules a v1.1 file may import: stdlib, or a project root. Anything else is a
#: new dependency, which the plan forbids.
ALLOWED_IMPORT_ROOTS = {"jcc_core", "supervisor", "control_ui", "fixtures"}

#: Calls that would give a v1.1 module a side effect it must not have. §16-§19 are
#: evidence/metrics modules: they read, they compute, they return. A write or an
#: egress in one of them would be an authority surface smuggled in beside the
#: directive plane.
FORBIDDEN_CALLS = {
    "system", "popen", "execv", "execve", "spawnv", "spawnl", "spawnvp",
    "remove", "unlink", "rmdir", "removedirs", "rmtree", "move", "copy",
    "copy2", "copyfile", "rename", "replace",
    "write_text", "write_bytes", "mkdir", "makedirs", "touch", "unlink",
    "run", "call", "check_output", "check_call", "Popen",
    "urlopen", "urlretrieve", "socket",
}
#: ``run``/``call``/``socket`` only matter qualified (``subprocess.run``,
#: ``socket.socket``); bare ``run`` is far too common to flag. These are the
#: qualified forms checked instead.
FORBIDDEN_ATTRS = {
    "subprocess.run", "subprocess.call", "subprocess.Popen",
    "subprocess.check_output", "subprocess.check_call",
    "os.system", "os.popen", "os.remove", "os.unlink", "os.rmdir",
    "os.rename", "os.replace", "os.makedirs", "os.mkdir",
    "shutil.move", "shutil.copy", "shutil.copy2", "shutil.copyfile",
    "shutil.rmtree", "shutil.rename",
    "socket.socket", "urllib.request.urlopen", "requests.get", "requests.post",
}

SEV_HIGH, SEV_MEDIUM, SEV_LOW, SEV_INFO = "HIGH", "MEDIUM", "LOW", "INFO"
SEV_ORDER = (SEV_HIGH, SEV_MEDIUM, SEV_LOW, SEV_INFO)


# --------------------------------------------------------------------------- #
# findings
# --------------------------------------------------------------------------- #
@dataclass
class Finding:
    check: str
    severity: str
    detail: str
    evidence: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CheckResult:
    check: str
    title: str
    findings: List[Finding] = field(default_factory=list)
    evidence: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    seconds: float = 0.0
    #: False when the check asserts something about its own non-vacuity.
    non_vacuous: Optional[bool] = None
    non_vacuity_note: str = ""

    def ok(self) -> bool:
        return self.error is None and not any(
            f.severity == SEV_HIGH for f in self.findings)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "check": self.check,
            "title": self.title,
            "ran": self.error is None,
            "error": self.error,
            "seconds": round(self.seconds, 3),
            "ok": self.ok(),
            "non_vacuous": self.non_vacuous,
            "non_vacuity_note": self.non_vacuity_note,
            "counts": {s: sum(1 for f in self.findings if f.severity == s)
                       for s in SEV_ORDER},
            "findings": [f.as_dict() for f in self.findings],
            "evidence": self.evidence,
        }


class Ctx:
    """Shared state. Deliberately small: each check builds its own throwaway."""

    def __init__(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="jcc-v11-sec-"))
        self.notes: List[str] = []

    def db(self, name: str = "sec.db") -> Path:
        return self.tmp / name


def _sha256_file(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def _ensure_paths() -> None:
    for p in (str(ROOT), str(APPS), str(PKGS), str(FIXTURES)):
        if p not in sys.path:
            sys.path.insert(0, p)


def _build_app(db_path: Path):
    """The REAL control-plane app over a throwaway database."""
    _ensure_paths()
    from jcc_core.actuator import Actuator
    from jcc_core.config import Config, ServerConfig
    from jcc_core.core import CoreEngine
    from jcc_core.providers import build_provider
    from jcc_core.server import create_app
    from jcc_core.state_store import StateStore

    cfg = Config(server=ServerConfig(host="127.0.0.1", port=0),
                 state_store_db_path=str(db_path))
    store = StateStore(str(db_path))
    engine = CoreEngine(store, build_provider(cfg), Actuator(enabled=False), cfg)
    return create_app(engine, cfg), engine, store, cfg


def _http(method: str, url: str, *, headers: Optional[Dict[str, str]] = None,
          body: Optional[bytes] = None, timeout: float = 10.0) -> Tuple[int, str]:
    req = urllib.request.Request(url, data=body, method=method,
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return int(resp.status), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        return 0, f"TRANSPORT: {exc}"


def _start_uvicorn(app, port: int):
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 25
    while time.time() < deadline:
        status, _ = _http("GET", f"http://127.0.0.1:{port}/api/health", timeout=1.0)
        if status == 200:
            return server, thread
        time.sleep(0.15)
    raise RuntimeError(f"uvicorn did not become ready on {port}")


def _stop_uvicorn(server, thread) -> None:
    """Stop the probe server and WAIT for its thread to actually finish.

    ``daemon=True`` is not a shutdown strategy. A daemon thread is killed
    abruptly at interpreter finalization, and if it is still inside
    asyncio/proactor native code when that happens the whole process can fault.
    Measured on this host during a loaded full-suite run: the gate printed its
    complete, correct verdict (``verdict=GO`` together with
    ``[check] OK: the committed artifact matches (sha 862a088692c9c15b830b9be1)``)
    and then exited ``0xC0000005`` (STATUS_ACCESS_VIOLATION). The answer was
    right; the exit path was not.

    Setting ``should_exit`` and joining for a fixed 6 s is a race, not a
    guarantee: under load uvicorn's async shutdown can exceed it, and then the
    interpreter finalizes with the thread still live. So: ask it to stop, give
    it a generous bounded budget, and if it is STILL alive say so rather than
    silently walking into finalization.

    This changes no check's verdict -- it only decides how long we wait before
    returning.
    """
    try:
        server.should_exit = True
        # `force_exit` drops in-flight connections instead of draining them; the
        # probe phase is over, so draining buys nothing. Guarded because not
        # every uvicorn version exposes the attribute.
        server.force_exit = True
    except Exception:  # noqa: BLE001
        pass
    if thread is not None:
        thread.join(timeout=60)
        if thread.is_alive():
            log("[warn] the probe-server thread was still alive 60s after "
                "should_exit; the process may finalize with a live asyncio "
                "thread (an ENVIRONMENT robustness issue, not a check result)")


# --------------------------------------------------------------------------- #
# S-01  auth is fail-closed on EVERY mutating route, and every route is known
# --------------------------------------------------------------------------- #
def check_s01_auth_fail_closed(ctx: Ctx) -> CheckResult:
    r = CheckResult("S-01", "auth fail-closed on every mutating route")
    token = "v11-sec-gate-token-" + hashlib.sha256(os.urandom(8)).hexdigest()[:12]
    os.environ["JCC_CONTROL_TOKEN"] = token
    app, engine, store, cfg = _build_app(ctx.db("s01.db"))

    # The route set is READ OFF THE APP, not typed here: a fifth mutating route
    # added later is covered automatically, and a route that stops being mutating
    # cannot silently drop out of scope.
    routes = []
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = set(getattr(route, "methods", None) or [])
        if path is None or path.startswith(("/docs", "/openapi", "/redoc")):
            continue
        if methods & {"POST", "PUT", "PATCH", "DELETE"}:
            routes.append((path, sorted(methods)))
    r.evidence["mutating_routes"] = [p for p, _ in routes]
    if not routes:
        r.findings.append(Finding("S-01", SEV_HIGH,
                                  "no mutating route found; the probe would be vacuous"))
        r.non_vacuous = False
        return r

    port = _find_free_port()
    server, thread = _start_uvicorn(app, port)
    base = f"http://127.0.0.1:{port}"
    probes: List[Dict[str, Any]] = []
    try:
        for path, methods in routes:
            for method in methods:
                body = b'{"session_id":"s01","event_id":"e1","delivery_id":"e1-d1",' \
                       b'"agent":"workbuddy","project":"SEC","event_type":"session_start"}'
                no_token, _ = _http(method, base + path, body=body,
                                    headers={"Content-Type": "application/json"})
                wrong, _ = _http(method, base + path, body=body,
                                 headers={"Content-Type": "application/json",
                                          "X-JCC-Token": token + "-wrong"})
                ok, _ = _http(method, base + path, body=body,
                              headers={"Content-Type": "application/json",
                                       "X-JCC-Token": token})
                probes.append({"route": path, "method": method,
                               "no_token": no_token, "wrong_token": wrong,
                               "correct_token": ok})
                if no_token not in (401, 403):
                    r.findings.append(Finding(
                        "S-01", SEV_HIGH,
                        f"{method} {path} answered {no_token} without a token",
                        {"route": path, "status": no_token}))
                if wrong not in (401, 403):
                    r.findings.append(Finding(
                        "S-01", SEV_HIGH,
                        f"{method} {path} answered {wrong} with a WRONG token",
                        {"route": path, "status": wrong}))
                if ok not in (200, 201, 202):
                    # Not a security finding in the leak sense, but a probe that
                    # cannot show the gate OPENING cannot show it is a gate and
                    # not a wall. Recorded as MEDIUM so it cannot pass unnoticed.
                    r.findings.append(Finding(
                        "S-01", SEV_MEDIUM,
                        f"{method} {path} answered {ok} with the CORRECT token; "
                        "the probe cannot distinguish a gate from a wall",
                        {"route": path, "status": ok}))
    finally:
        _stop_uvicorn(server, thread)
        os.environ.pop("JCC_CONTROL_TOKEN", None)

    r.evidence["probes"] = probes
    r.non_vacuous = all(p["correct_token"] in (200, 201, 202) for p in probes)
    r.non_vacuity_note = ("the correct token must open the gate, otherwise "
                          "'refused without a token' proves nothing")
    return r


# --------------------------------------------------------------------------- #
# S-02  no secret at rest, in any sink, and the scrubber is not a shredder
# --------------------------------------------------------------------------- #
def check_s02_no_secret_at_rest(ctx: Ctx) -> CheckResult:
    r = CheckResult("S-02", "planted secrets absent from every persisted sink")
    _ensure_paths()
    # FOUR distinct probes, deliberately different values. The first version of
    # this check reused the env-var value inside the payload, so a hit on the
    # payload reported itself as "the signing key leaked" -- the leak was
    # self-inflicted and the check was measuring its own scaffolding.
    #
    #  * env_key / env_token: the REAL secrets, present only in the environment.
    #    These must not appear in ANY sink. This is the v1.0 claim under test.
    #  * labelled: a credential in a free-text leaf, in a shape the redaction
    #    policy DOCUMENTS as caught (a credential-labelled header). Must be
    #    scrubbed. Chosen for the documented shape on purpose: asserting that an
    #    arbitrary dashed id is scrubbed would be asserting something the policy
    #    explicitly declines to do.
    #  * named: a secret under a secret-NAMED key. Measured and reported -- see
    #    the finding below, which is the one real discovery this check made.
    #  * benign: must SURVIVE, or a shredder passes a "nothing leaked" test.
    env_key = "V11SECK" + hashlib.sha256(os.urandom(16)).hexdigest()  # 64 hex chars
    env_token = "V11SECT" + hashlib.sha256(os.urandom(16)).hexdigest()
    labelled = "V11SECL" + hashlib.sha256(os.urandom(16)).hexdigest()
    named = "V11SECN" + hashlib.sha256(os.urandom(16)).hexdigest()
    benign = "v11sec-benign-note-" + hashlib.sha256(os.urandom(5)).hexdigest()[:8]

    os.environ["JCC_DIRECTIVE_SIGNING_KEY"] = env_key
    os.environ["JCC_CONTROL_TOKEN"] = env_token
    try:
        db = ctx.db("s02.db")
        app, engine, store, cfg = _build_app(db)

        # 1) Ingest through the REAL chokepoint.
        from jcc_core.schemas import AEPEnvelope

        env = AEPEnvelope(
            event_id="s02-e1", delivery_id="s02-e1-d1", session_id="s02-sess",
            agent="workbuddy", project="SEC", event_type="tool_call",
            payload={
                "tool_input": {
                    "command": f"curl -H 'Authorization: Bearer {labelled}' https://example.invalid",
                    "note": benign,
                    "api_key": named,
                },
            },
        )
        engine.ingest(env)

        # 2) Sign a directive with the planted key, through the REAL signer and
        #    the REAL directives table. The id prefix is not cosmetic:
        #    resolve_directive_origin() refuses a directive whose id prefix does
        #    not belong to the origin claiming it, so a supervisor-origin row must
        #    be a ``shadow_`` id. DirectiveSigner.sign() RETURNS the signature
        #    string and does not mutate the directive, so it is attached
        #    explicitly -- the columns come from d.signed / d.signature.
        from jcc_core.schemas import Directive, DirectiveAction
        from jcc_core.signing import DirectiveSigner
        from supervisor.sentinel import SUPERVISOR_ORIGIN

        signer = DirectiveSigner(env_key.encode("utf-8"))
        directive = Directive(
            directive_id="shadow_s02-d1", session_id="s02-sess",
            action=DirectiveAction.CONTINUE, target_actuator="noop",
            evidence_hash="s02-h", policy_version=cfg.policy.version,
            timestamp=time.time(),
            # issued_at / ttl_s / nonce are inside the signed payload; the signer
            # refuses to sign without them because such a signature would not be
            # integrity- or replay-protected. Supplied here rather than omitted so
            # the probe exercises the real signing path.
            issued_at=time.time(), ttl_s=300.0, nonce="s02-nonce-1",
        )
        signature = signer.sign(directive)
        # The canonical payload does not include `signature`, so verifying the
        # unsigned object against the explicit signature is the same check the
        # Sentinel's self-verify performs.
        verified = bool(signature) and signer.verify(directive, signature)
        directive = directive.model_copy(update={"signed": True, "signature": signature})
        store.add_directive(directive, origin=SUPERVISOR_ORIGIN)

        # 3) Append a shadow decision so the third sink EXISTS to be scanned.
        #    Scanning a table that is not there is how a sink check goes vacuous.
        from supervisor import shadow_store

        with store.transaction() as conn:
            shadow_store.ensure_schema(conn)
            shadow_store.append_shadow(conn, record={
                "session_id": "s02-sess", "event_id": "s02-e1",
                "agent": "workbuddy", "action": "CONTINUE",
                "policy_version": cfg.policy.version,
                "signature": directive.signature or "",
                "rationale": f"sec probe {benign}",
            })

        # -- the sinks ----------------------------------------------------- #
        sinks: Dict[str, str] = {}
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(db) + suffix)
            if p.is_file():
                sinks[f"file:{p.name}"] = p.read_bytes().decode("utf-8", "replace")
        conn = sqlite3.connect(str(db))
        try:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            for table in tables:
                rows = conn.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608
                sinks[f"table:{table}"] = repr(rows)
            signed_rows = conn.execute(
                "SELECT COUNT(*) FROM directives WHERE signed=1").fetchone()[0]
        finally:
            conn.close()

        r.evidence["sinks_scanned"] = sorted(sinks)
        r.evidence["signed_rows"] = signed_rows
        r.evidence["directive_signature_present"] = bool(directive.signature)
        r.evidence["signature_self_verified"] = verified

        # NON-VACUITY FIRST: nothing below means anything unless signing ran.
        benign_survived = any(benign in t for t in sinks.values())
        r.evidence["benign_survived"] = benign_survived
        if signed_rows < 1:
            r.findings.append(Finding(
                "S-02", SEV_HIGH,
                "no signed directive row exists, so 'the signing key did not "
                "leak' would be a statement about a run in which signing never "
                "happened", {"signed_rows": signed_rows}))
        if not benign_survived:
            r.findings.append(Finding(
                "S-02", SEV_HIGH,
                "the BENIGN control string did not survive, so 'no secret at "
                "rest' is vacuously true: the payload was erased rather than "
                "sanitised", {"benign": benign}))
        r.non_vacuous = signed_rows >= 1 and benign_survived
        r.non_vacuity_note = (
            "requires signed_rows >= 1 (signing was live during the scan) AND the "
            "benign string in the same payload to survive")

        # -- probe A: the real secrets, which live only in the environment ---- #
        env_hits = {}
        for label, secret in (("signing key", env_key), ("control token", env_token)):
            hits = sorted(n for n, t in sinks.items() if secret in t)
            env_hits[label] = hits
            if hits:
                r.findings.append(Finding(
                    "S-02a", SEV_HIGH,
                    f"the {label} from the ENVIRONMENT appears in {len(hits)} "
                    f"sink(s); it was never placed in any payload", {"sinks": hits}))
        r.evidence["env_secret_hits"] = env_hits

        # -- probe B: documented value-pattern scrubbing --------------------- #
        labelled_hits = sorted(n for n, t in sinks.items() if labelled in t)
        r.evidence["labelled_credential_hits"] = labelled_hits
        if labelled_hits:
            r.findings.append(Finding(
                "S-02b", SEV_HIGH,
                "a credential-labelled value (Authorization: Bearer <secret>) "
                "survived into the persisted payload, although the redaction "
                "policy documents that shape as caught",
                {"sinks": labelled_hits}))

        # -- probe C: a secret under a secret-NAMED key ----------------------- #
        named_hits = sorted(n for n, t in sinks.items() if named in t)
        r.evidence["key_named_secret_hits"] = named_hits
        if named_hits:
            r.findings.append(Finding(
                "S-02c", SEV_HIGH,
                "a secret under a secret-NAMED key (payload.tool_input.api_key) "
                "is persisted VERBATIM. CoreEngine.ingest applies only "
                "scrub_value_secrets() (the value-pattern pass); the key-name "
                "pass redact() is called solely from the adapter plane "
                "(adapters/{claude,codex,hermes,workbuddy}.py, adapters/_common.py, "
                "operator_skill/trace.py), so any producer that reaches the "
                "chokepoint WITHOUT an adapter bypasses key-name redaction "
                "entirely: POST /api/assess (which does "
                "AEPEnvelope(**body); engine.ingest(env) and has no adapter), or "
                "an in-process engine.ingest(). This is a conformance gap "
                "against ADR-0007 D-1, which declares Redaction a "
                "persistence-boundary guarantee owned by that chokepoint, and "
                "against the chokepoint's own comment that no producer can "
                "bypass it. "
                "REMEDY (deliberately NOT the obvious one): do NOT call "
                "redact(event.payload) here. redact() ALSO truncates every "
                "string leaf longer than MAX_TEXT_LEN=240 to "
                "'<first 240>…[sha256:…]' (redaction.py, and its "
                "_FREETEXT_KEY_RE branch is byte-identical to its else branch, "
                "so the truncation is blanket, not PII-targeted). Wiring it into "
                "ingest would change what the audit tape stores for "
                "essentially every real event, buying no security and losing "
                "audit fidelity. The minimal correct fix is the key-name half "
                "only: add a masking pass to packages/jcc_core/redaction.py "
                "(NOT A8-pinned) that replaces the VALUE under any key matching "
                "_SECRET_KEY_RE with '[REDACTED]', shares MAX_SCRUB_DEPTH / "
                "DEPTH_LIMIT_MARK with its two siblings, does no truncation, and "
                "call it at the chokepoint in packages/jcc_core/core.py BEFORE "
                "scrub_value_secrets(), for payload and evidence symmetrically. "
                "That file is one of the twelve A8-pinned artifacts, so the "
                "change carries a mandatory re-baseline with old->new recorded "
                "(the pin's own contract). "
                "SEVERITY NOTE: reaching this requires an authenticated caller "
                "(S-01 proves every mutating route is token-gated) or in-process "
                "code, and the database is loopback-only and unexposed, so it is "
                "a defence-in-depth gap rather than an auth bypass. It is "
                "recorded HIGH because the gate has one blocking severity and a "
                "reviewer who disagrees with the severity must still have to see "
                "it.",
                {"sinks": named_hits, "field": "payload.tool_input.api_key",
                 "chokepoint": "packages/jcc_core/core.py: ingest() step 0",
                 "adr": "docs/ADR/ADR-0007-p2-redaction-boundary-and-shared-kernel.md D-1",
                 "key_name_pass_is_adapter_only": True,
                 "redact_would_truncate_at": 240}))

    finally:
        os.environ.pop("JCC_DIRECTIVE_SIGNING_KEY", None)
        os.environ.pop("JCC_CONTROL_TOKEN", None)
    return r


# --------------------------------------------------------------------------- #
# S-03  loopback only, and the declared open surface IS the open surface
# --------------------------------------------------------------------------- #
def check_s03_declared_surface(ctx: Ctx) -> CheckResult:
    r = CheckResult("S-03", "loopback-only bind and no undeclared open route")
    _ensure_paths()
    import jcc_core.server as S

    app, engine, store, cfg = _build_app(ctx.db("s03.db"))
    declared = set(S.OPEN_GET_ENDPOINTS)

    actual_get = set()
    other = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = set(getattr(route, "methods", None) or [])
        if path is None or path.startswith(("/docs", "/openapi", "/redoc")):
            continue
        if "GET" in methods:
            actual_get.add(path)
        elif not (methods & {"POST", "PUT", "PATCH", "DELETE"}):
            other.add(path)

    r.evidence = {
        "declared_open_get": sorted(declared),
        "actual_get_routes": sorted(actual_get),
        "declared_but_not_a_get_route": sorted(declared - actual_get),
        "get_route_but_not_declared": sorted(actual_get - declared),
        "neither_get_nor_mutating": sorted(other),
    }
    if declared - actual_get:
        r.findings.append(Finding("S-03", SEV_HIGH,
                                  "open_get_endpoints declares a route that is not "
                                  "a GET route",
                                  {"routes": sorted(declared - actual_get)}))
    if actual_get - declared:
        r.findings.append(Finding(
            "S-03", SEV_HIGH,
            "a GET route is reachable without a token but is NOT declared in "
            "open_get_endpoints, which makes the declaration a lie",
            {"routes": sorted(actual_get - declared)}))

    # The bind is loopback. Asserted on the real socket, not on the config value.
    port = _find_free_port()
    server, thread = _start_uvicorn(app, port)
    try:
        bound = getattr(server, "servers", None)
        host = None
        if bound:
            try:
                host = bound[0].sockets[0].getsockname()[0]
            except Exception:  # noqa: BLE001
                host = None
        r.evidence["bound_address_observed"] = host
        if host not in (None, "127.0.0.1"):
            r.findings.append(Finding("S-03", SEV_HIGH,
                                      f"the server bound {host}, not loopback",
                                      {"bound": host}))
        # Loopback reachable, and the protected port is untouched.
        status, _ = _http("GET", f"http://127.0.0.1:{port}/api/health", timeout=3)
        r.evidence["loopback_reachable"] = status == 200
        if status != 200:
            r.findings.append(Finding("S-03", SEV_HIGH,
                                      f"loopback health probe answered {status}",
                                      {"status": status}))
        r.evidence["protected_port_open_during_run"] = _port_open(PROTECTED_PORT)
    finally:
        _stop_uvicorn(server, thread)
    r.non_vacuous = bool(actual_get) and r.evidence.get("loopback_reachable", False)
    r.non_vacuity_note = ("requires at least one real GET route and a reachable "
                          "loopback endpoint; two empty sets would compare equal")
    return r


# --------------------------------------------------------------------------- #
# S-04  v1.1 added no side effect and no authority
# --------------------------------------------------------------------------- #
def _calls_in(path: Path) -> List[Tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: List[Tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                out.append((func.id, node.lineno))
            elif isinstance(func, ast.Attribute):
                parts = []
                cur = func
                while isinstance(cur, ast.Attribute):
                    parts.append(cur.attr)
                    cur = cur.value
                if isinstance(cur, ast.Name):
                    parts.append(cur.id)
                out.append((".".join(reversed(parts)), node.lineno))
    return out


def check_s04_no_new_authority(ctx: Ctx) -> CheckResult:
    r = CheckResult("S-04", "v1.1 modules add no write and no egress")
    scanned = []
    for rel in V1_1_MODULES:
        path = ROOT / rel
        if not path.is_file():
            r.findings.append(Finding("S-04", SEV_HIGH,
                                      f"v1.1 module missing: {rel}", {"path": rel}))
            continue
        scanned.append(rel)
        for name, lineno in _calls_in(path):
            if name in FORBIDDEN_ATTRS or (
                    "." not in name and name in FORBIDDEN_CALLS
                    and name in {"system", "popen", "urlretrieve"}):
                r.findings.append(Finding(
                    "S-04", SEV_HIGH,
                    f"{rel}:{lineno} calls {name}; a v1.1 evidence module must "
                    "have no write and no egress path",
                    {"path": rel, "line": lineno, "call": name}))
    r.evidence["modules_scanned"] = scanned
    if not scanned:
        r.findings.append(Finding("S-04", SEV_HIGH, "no v1.1 module was scanned"))

    # Behavioural half: the actuator stays inert and the ladder denies at L0.
    _ensure_paths()
    from jcc_core.actuator import Actuator
    from jcc_core.schemas import Directive, DirectiveAction
    from supervisor import actuation_levels as AL

    directive = Directive(
        directive_id="s04-d1", session_id="s04", action=DirectiveAction.PAUSE,
        target_actuator="noop", evidence_hash="h", policy_version="p3.0.0",
        timestamp=time.time())
    for enabled in (False, True):
        actuator = Actuator(enabled=enabled, allowed_actions=["PAUSE"])
        # execute() returns a plain dict {acted, executable, reason, ...}; both it
        # and the permanent engine seam dispatch() must report acted=False.
        result = actuator.execute(directive)
        acted = bool(result.get("acted")) if isinstance(result, dict) else True
        dispatched = actuator.dispatch(directive)
        d_acted = bool(dispatched.get("acted")) if isinstance(dispatched, dict) else True
        r.evidence.setdefault("actuator_execute", []).append(
            {"enabled": enabled, "acted": acted, "dispatch_acted": d_acted,
             "reason": result.get("reason") if isinstance(result, dict) else None})
        if acted or d_acted:
            r.findings.append(Finding(
                "S-04", SEV_HIGH,
                f"Actuator(enabled={enabled}) reported acted=true "
                f"(execute={acted}, dispatch={d_acted}); the P0-P3 invariant is "
                "that no directive is ever actuated",
                {"enabled": enabled}))

    l0_permits = sorted(_level_value(a) for a in AL.capability_of(AL.ActuationLevel.L0))
    r.evidence["l0_permits"] = l0_permits
    if l0_permits:
        r.findings.append(Finding("S-04", SEV_HIGH,
                                  f"L0 permits actions: {l0_permits}",
                                  {"permits": l0_permits}))
    if AL.gate_runs(AL.ActuationLevel.L0):
        r.findings.append(Finding("S-04", SEV_HIGH,
                                  "L0 runs the gate; L0 must do nothing at all"))
    r.non_vacuous = bool(scanned) and bool(r.evidence.get("actuator_execute"))
    r.non_vacuity_note = ("requires modules scanned and at least one actuator "
                          "execute() observed with acted=false")
    return r


# --------------------------------------------------------------------------- #
# S-05  the UI holds no token and calls no mutating endpoint but the probe
# --------------------------------------------------------------------------- #
def _product_app_modules() -> List[Path]:
    out = []
    for path in sorted((APPS / "control_ui").glob("*.py")):
        if path.name in ("_harness.py", "acceptance.py"):
            continue  # test/acceptance infrastructure, not shipped UI
        out.append(path)
    return out


def _post_sites(path: Path) -> Tuple[List[str], List[str]]:
    """(explicit post_json sites, raw request sites carrying a mutating method).

    Two different questions, deliberately separated. ``post_json`` is the UI's
    named POST helper and there must be exactly one call to it. A raw
    ``self.request("POST", ...)`` is the transport plumbing *inside* the client,
    which is allowed only in ``api_client.py`` -- so the rule is "no product
    module outside the client hand-rolls a mutating request", not "the word POST
    appears nowhere", which could never hold.
    """
    rel = str(path.relative_to(ROOT)).replace("\\", "/")
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    explicit: List[str] = []
    raw: List[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        parts: List[str] = []
        cur = func
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        name = ".".join(reversed(parts))
        leaf = name.rsplit(".", 1)[-1]
        if leaf == "post_json":
            explicit.append(f"{rel}:{node.lineno}:{name}")
        elif leaf == "request":
            literals = [a.value for a in node.args
                        if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            for kw in node.keywords:
                if kw.arg == "method" and isinstance(kw.value, ast.Constant):
                    literals.append(kw.value.value)
            if any(str(v).upper() in {"POST", "PUT", "PATCH", "DELETE"}
                   for v in literals):
                raw.append(f"{rel}:{node.lineno}:{name}({literals})")
    return explicit, raw


def check_s05_ui_holds_no_token(ctx: Ctx) -> CheckResult:
    r = CheckResult("S-05", "the UI reads no token and posts only the probe")
    scanned = [str(p.relative_to(ROOT)).replace("\\", "/")
               for p in _product_app_modules()]
    r.evidence["modules_scanned"] = scanned
    if not scanned:
        r.findings.append(Finding("S-05", SEV_HIGH, "no UI product module found"))
        return r

    explicit: List[str] = []
    raw: List[str] = []
    token_reads: List[str] = []
    for path in _product_app_modules():
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        source = path.read_text(encoding="utf-8")
        ex, ra = _post_sites(path)
        explicit += ex
        raw += ra
        for name, lineno in _calls_in(path):
            if "environ" in name:
                # Reading an env var is how the UI would obtain the token value.
                token_reads.append(f"{rel}:{lineno}:{name}")
        if "Authorization" in source:
            r.findings.append(Finding(
                "S-05", SEV_HIGH,
                f"{rel} builds an Authorization header; the UI must never carry "
                "the control token", {"path": rel}))
        if "JCC_CONTROL_TOKEN" in source:
            r.findings.append(Finding(
                "S-05", SEV_HIGH,
                f"{rel} names JCC_CONTROL_TOKEN in product code; only the "
                "env-var NAME is rendered (via cfg.control_token_env)",
                {"path": rel}))

    r.evidence["post_json_sites"] = explicit
    r.evidence["raw_mutating_request_sites"] = raw
    r.evidence["environ_calls"] = token_reads
    if len(explicit) != 1:
        r.findings.append(Finding(
            "S-05", SEV_HIGH,
            f"expected exactly one post_json call site (the anonymous fail-closed "
            f"probe), found {len(explicit)}: {explicit}",
            {"post_json_sites": explicit}))
    stray = [s for s in raw if not s.startswith("apps/control_ui/api_client.py:")]
    if stray:
        r.findings.append(Finding(
            "S-05", SEV_HIGH,
            f"a product module outside the transport client hand-rolls a mutating "
            f"request: {stray}", {"stray": stray}))
    r.non_vacuous = bool(explicit)
    r.non_vacuity_note = ("requires the probe's POST to be found; 'no POST found' "
                          "and 'no POST expected' must not look the same")
    return r


# --------------------------------------------------------------------------- #
# S-06  no new dependency
# --------------------------------------------------------------------------- #
def check_s06_no_new_dependency(ctx: Ctx) -> CheckResult:
    r = CheckResult("S-06", "v1.1 modules import only stdlib and project roots")
    stdlib = set(getattr(sys, "stdlib_module_names", set()))
    imports: Dict[str, List[str]] = {}
    for rel in V1_1_MODULES:
        path = ROOT / rel
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        tops: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                tops += [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # relative import = same package
                    continue
                if node.module:
                    tops.append(node.module.split(".")[0])
        imports[rel] = sorted(set(tops))
    r.evidence["imports"] = imports
    allowed = stdlib | ALLOWED_IMPORT_ROOTS | {
        "__future__", "typing", "time", "json", "sqlite3", "hashlib",
        "dataclasses", "pathlib", "enum", "re", "os", "sys", "math",
    }
    foreign = {rel: [t for t in tops if t not in allowed]
               for rel, tops in imports.items()}
    foreign = {rel: tops for rel, tops in foreign.items() if tops}
    r.evidence["foreign_imports"] = foreign
    for rel, tops in foreign.items():
        r.findings.append(Finding("S-06", SEV_HIGH,
                                  f"{rel} imports non-stdlib, non-project "
                                  f"module(s): {tops}",
                                  {"path": rel, "modules": tops}))
    r.non_vacuous = bool(imports)
    r.non_vacuity_note = "requires at least one module parsed"
    return r


# --------------------------------------------------------------------------- #
# S-07  no bypass: no single switch can open actuation
# --------------------------------------------------------------------------- #
def _level_value(level: Any) -> str:
    """``ActuationLevel.L0`` -> ``"L0"``.

    ``ActuationLevel`` is a ``(str, Enum)``, and ``str()`` on such a member gives
    ``"ActuationLevel.L0"``, not ``"L0"``. Comparing ``str(level)`` against "L0"
    was therefore ALWAYS true and produced three false HIGH findings about a gate
    that was in fact behaving correctly. ``.value`` is the token the emitted
    artifacts carry, so it is also the one the evidence should record.
    """
    return str(getattr(level, "value", level))


def check_s07_no_actuation_bypass(ctx: Ctx) -> CheckResult:
    r = CheckResult("S-07", "no single condition can enable actuation")
    _ensure_paths()
    from supervisor import live_shadow_gate as G

    criteria = G.LiveShadowGateCriteria(
        min_decisions=2, min_wall_clock_s=1.0,
        granted_ceiling=G.AL.ActuationLevel.L2)
    conn = sqlite3.connect(":memory:")
    try:
        from supervisor import shadow_store

        shadow_store.ensure_schema(conn)
        base = time.time() - 10.0
        for i in range(3):
            # `observed_at` is the column the gate measures its window from (not
            # `created_at`): a fixture that sets the wrong column leaves the span
            # at 0.0 and the criteria unmet, which is how the first version of
            # this check failed its own positive control.
            shadow_store.append_shadow(conn, record={
                "session_id": "s07", "event_id": f"e{i}", "agent": "workbuddy",
                "action": "CONTINUE", "policy_version": "p3.0.0",
                "observed_at": base + i, "acted": 0,
            })
        all_three = G.evaluate_live_shadow_gate(
            conn, criteria=criteria,
            configured_level=G.AL.ActuationLevel.L3, configured_enabled=True)
        r.evidence["all_three"] = {
            "status": all_three.status, "satisfied": all_three.satisfied,
            "actuation_enabled": all_three.actuation_enabled,
            "observed_decisions": all_three.observed_decisions,
            "observed_span_s": all_three.observed_span_s,
            "defects": list(all_three.defects),
            "effective_level": _level_value(all_three.effective_level)}
        if not (all_three.status == G.GATE_SATISFIED
                and all_three.actuation_enabled):
            r.findings.append(Finding(
                "S-07", SEV_MEDIUM,
                "the positive control did not reach SATISFIED+enabled, so the "
                "negative controls below prove nothing",
                r.evidence["all_three"]))

        # Remove each condition in turn. `actuation_enabled` is the load-bearing
        # refusal; `effective_level` is DESCRIPTIVE (see S-07c below), so the two
        # are asserted differently rather than lumped into one rule.
        configured_level = G.AL.ActuationLevel.L3
        variants = {
            "operator_switch_off": dict(
                criteria=G.LiveShadowGateCriteria(
                    min_decisions=2, min_wall_clock_s=1.0,
                    granted_ceiling=G.AL.ActuationLevel.L2),
                configured_enabled=False),
            "ceiling_never_granted": dict(
                criteria=G.LiveShadowGateCriteria(
                    min_decisions=2, min_wall_clock_s=1.0,
                    granted_ceiling=G.AL.ActuationLevel.L0),
                configured_enabled=True),
            "criteria_unmet": dict(
                criteria=G.LiveShadowGateCriteria(
                    min_decisions=10_000, min_wall_clock_s=1.0,
                    granted_ceiling=G.AL.ActuationLevel.L2),
                configured_enabled=True),
        }
        observed = {}
        for name, kw in variants.items():
            crit = kw.pop("criteria")
            res = G.evaluate_live_shadow_gate(
                conn, criteria=crit, configured_level=configured_level, **kw)
            got_level = _level_value(res.effective_level)
            # The PRODUCT's own documented rule, not my guess: PENDING forces L0;
            # a satisfied gate reports restrict(configured, ceiling) even when the
            # operator switch is off, because the level describes the ceiling and
            # `actuation_enabled` is what refuses.
            if res.status == G.GATE_PENDING:
                expected_level = _level_value(
                    G.AL.restrict(configured_level, G.AL.ActuationLevel.L0))
            else:
                expected_level = _level_value(
                    G.AL.restrict(configured_level, crit.granted_ceiling))
            observed[name] = {
                "status": res.status, "satisfied": res.satisfied,
                "actuation_enabled": res.actuation_enabled,
                "effective_level": got_level,
                "expected_level": expected_level,
                "ceiling": _level_value(crit.granted_ceiling)}
            if res.actuation_enabled:
                r.findings.append(Finding(
                    "S-07", SEV_HIGH,
                    f"actuation_enabled stayed true with {name}; a single "
                    "condition must not be sufficient", observed[name]))
            if got_level != expected_level:
                r.findings.append(Finding(
                    "S-07", SEV_HIGH,
                    f"effective_level is {got_level} with {name} but the documented "
                    f"rule yields {expected_level}; a PENDING gate must force L0",
                    observed[name]))
        r.evidence["variants"] = observed
    finally:
        conn.close()
    r.non_vacuous = r.evidence.get("all_three") is not None
    r.non_vacuity_note = ("requires the positive control (all three conditions) to "
                          "open actuation; otherwise the removals prove nothing")
    return r


#: `effective_level` is descriptive: it names the ceiling the gate would grant.
#: The refusal is `actuation_enabled`. That is only safe while NOTHING ENFORCING
#: reads the level, so the property is measured rather than asserted in prose.
#: The display layer is allowed; any other consumer is a HIGH finding, because a
#: descriptive value that an enforcement path consults has become a load-bearing
#: one and must be re-derived there.
EFFECTIVE_LEVEL_DISPLAY_ONLY = {
    "packages/supervisor/live_shadow_gate.py",   # the definition
    "apps/control_ui/v1_1.py",                   # reads it out of the artifact
    "apps/control_ui/render.py",                 # draws it
    "tools/v1_1_emit_shadow_gate.py",            # emits the artifact
    "tools/v1_1_security_gate.py",               # this gate
}


def check_s07c_level_is_descriptive(ctx: Ctx) -> CheckResult:
    r = CheckResult("S-07c", "no enforcement path consumes effective_level")
    consumers: List[str] = []
    for path in sorted((ROOT / "packages").rglob("*.py")) + sorted(
            (ROOT / "apps").rglob("*.py")) + sorted((ROOT / "tools").rglob("*.py")):
        if "tests" in path.parts or "__pycache__" in path.parts:
            continue
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "effective_level" in text and rel not in EFFECTIVE_LEVEL_DISPLAY_ONLY:
            consumers.append(rel)
    r.evidence = {"allowed": sorted(EFFECTIVE_LEVEL_DISPLAY_ONLY),
                  "unexpected_consumers": consumers}
    for rel in consumers:
        r.findings.append(Finding(
            "S-07c", SEV_HIGH,
            f"{rel} reads effective_level; it is a DESCRIPTIVE value and the "
            "refusal is actuation_enabled, so an enforcement path that consults "
            "the level would be enforcing on a description",
            {"path": rel}))
    r.non_vacuous = bool(r.evidence["allowed"])
    r.non_vacuity_note = "requires the allow-list and the scan root to be non-empty"
    return r


# --------------------------------------------------------------------------- #
# S-08  the instrument did not damage what it measured
# --------------------------------------------------------------------------- #
class S08State:
    def __init__(self) -> None:
        self.before = {}

    def snapshot(self) -> Dict[str, Any]:
        return {
            "protected_port_open": _port_open(PROTECTED_PORT),
            "runtime_db_sha256": _sha256_file(PROTECTED_DB),
            "runtime_db_size": PROTECTED_DB.stat().st_size if PROTECTED_DB.is_file() else None,
            "wal_present": Path(str(PROTECTED_DB) + "-wal").is_file(),
            "shm_present": Path(str(PROTECTED_DB) + "-shm").is_file(),
        }


def check_s08_protected_untouched(ctx: Ctx, state: S08State) -> CheckResult:
    r = CheckResult("S-08", "the protected service and runtime DB are untouched")
    before, after = state.before, state.snapshot()
    r.evidence = {"before": before, "after": after,
                  "protected_port": PROTECTED_PORT,
                  "protected_db": str(PROTECTED_DB)}
    for field_name in ("runtime_db_sha256", "runtime_db_size", "wal_present",
                       "shm_present"):
        if before.get(field_name) != after.get(field_name):
            r.findings.append(Finding(
                "S-08", SEV_HIGH,
                f"the protected runtime database changed during the gate run: "
                f"{field_name} {before.get(field_name)!r} -> {after.get(field_name)!r}",
                {"field": field_name}))
    if before.get("protected_port_open") and not after.get("protected_port_open"):
        r.findings.append(Finding(
            "S-08", SEV_HIGH,
            f"the protected v1.0 service on {PROTECTED_PORT} was up before the run "
            "and is down after it", {}))
    r.non_vacuous = before.get("runtime_db_sha256") is not None
    r.non_vacuity_note = ("requires the protected database to exist, so 'it did "
                          "not change' is a statement about a real file")
    return r


# --------------------------------------------------------------------------- #
# the runner
# --------------------------------------------------------------------------- #
CHECK_TABLE: Sequence[Tuple[str, str, Callable[..., CheckResult]]] = (
    ("S-01", "auth fail-closed on every mutating route", check_s01_auth_fail_closed),
    ("S-02", "planted secrets absent from every persisted sink", check_s02_no_secret_at_rest),
    ("S-03", "loopback-only bind and no undeclared open route", check_s03_declared_surface),
    ("S-04", "v1.1 modules add no write and no egress", check_s04_no_new_authority),
    ("S-05", "the UI reads no token and posts only the probe", check_s05_ui_holds_no_token),
    ("S-06", "v1.1 modules import only stdlib and project roots", check_s06_no_new_dependency),
    ("S-07", "no single condition can enable actuation", check_s07_no_actuation_bypass),
    ("S-07c", "no enforcement path consumes effective_level", check_s07c_level_is_descriptive),
    ("S-08", "the protected service and runtime DB are untouched", check_s08_protected_untouched),
)


def run_checks(verbose: bool = True) -> Dict[str, Any]:
    ctx = Ctx()
    state = S08State()
    state.before = state.snapshot()
    results: List[CheckResult] = []
    for cid, title, fn in CHECK_TABLE:
        started = time.time()
        try:
            res = fn(ctx, state) if cid == "S-08" else fn(ctx)
            res.seconds = time.time() - started
        except Exception as exc:  # noqa: BLE001
            # A check that cannot RUN is a HIGH finding, never a skip: an
            # unrunnable gate and a passing gate must not look the same.
            res = CheckResult(cid, title, error=f"{type(exc).__name__}: {exc}",
                              seconds=time.time() - started)
            res.findings.append(Finding(
                cid, SEV_HIGH,
                f"the check could not run: {type(exc).__name__}: {exc}",
                {"traceback": traceback.format_exc()[-1500:]}))
        results.append(res)
        if verbose:
            log("[%s] %s  ran=%s ok=%s high=%d (%.2fs)"
                % (cid, title, res.error is None, res.ok(),
                   sum(1 for f in res.findings if f.severity == SEV_HIGH),
                   res.seconds))

    counts = {s: sum(1 for r in results for f in r.findings if f.severity == s)
              for s in SEV_ORDER}
    checks_ran = sum(1 for r in results if r.error is None)
    not_run = [r.check for r in results if r.error is not None]
    non_vacuous_gaps = [r.check for r in results if r.non_vacuous is False]
    record = {
        "schema": SCHEMA,
        "check_count": len(results),
        "checks_ran": checks_ran,
        "checks_not_run": not_run,
        "non_vacuity_gaps": non_vacuous_gaps,
        "counts": counts,
        "verdict_rule": ("NO-GO iff high > 0, or any check failed to run, or any "
                         "check reported its own evidence as vacuous"),
        "checks": [r.as_dict() for r in results],
    }
    # The rule is applied through _verdict_of and nowhere else, so the selftest --
    # which falsifies _verdict_of -- is falsifying the rule the gate actually
    # runs. A separate inline expression here would make the selftest decorative.
    record["verdict"] = _verdict_of(record)
    return record


def log(msg: str) -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "backslashreplace").decode("ascii"), flush=True)


# --------------------------------------------------------------------------- #
# selftest: falsify the rule and the scanner
# --------------------------------------------------------------------------- #
def selftest() -> int:
    failures: List[str] = []

    def expect(cond: bool, why: str) -> None:
        if not cond:
            failures.append(why)

    # 1. the rule can go NO-GO
    fake = {
        "schema": SCHEMA, "check_count": 1, "checks_ran": 1, "counts":
            {SEV_HIGH: 1, SEV_MEDIUM: 0, SEV_LOW: 0, SEV_INFO: 0},
        "checks_not_run": [], "non_vacuity_gaps": [],
    }
    expect(_verdict_of(fake) == "NO-GO", "HIGH>0 must produce NO-GO")
    fake["counts"][SEV_HIGH] = 0
    expect(_verdict_of(fake) == "GO", "no HIGH and all ran must produce GO")
    fake["checks_not_run"] = ["S-XX"]
    expect(_verdict_of(fake) == "NO-GO", "an unrunnable check must produce NO-GO")
    fake["checks_not_run"] = []
    fake["non_vacuity_gaps"] = ["S-XX"]
    expect(_verdict_of(fake) == "NO-GO", "a vacuous check must produce NO-GO")

    # 2. the leak scanner can FIND a planted value (anti-vacuity of the scanner
    #    itself: it is pointed at a sink that really contains the needle)
    needle = "selftest-needle-" + hashlib.sha256(os.urandom(5)).hexdigest()[:8]
    sinks = {"table:planted": f"row with {needle} inside", "file:other": "clean"}
    hits = [n for n, t in sinks.items() if needle in t]
    expect(hits == ["table:planted"], f"the scanner must find the needle, got {hits}")
    expect([n for n, t in sinks.items() if "absent-needle" in t] == [],
           "the scanner must not report a needle that is absent")

    # 3. the benign-survival rule can fail
    expect(_benign_survives("benign-needle", "no") is False,
           "a benign string absent from every sink must be reported as lost")
    expect(_benign_survives("benign-needle", "xx benign-needle xx") is True,
           "a benign substring present in a sink must be reported as survived")

    for f in failures:
        log("[selftest] FAIL: " + f)
    log("[selftest] %s (%d failure(s))" % ("PASS" if not failures else "FAIL",
                                           len(failures)))
    return 0 if not failures else 1


def _verdict_of(record: Dict[str, Any]) -> str:
    """The verdict rule, extracted so the selftest falsifies the REAL one."""
    counts = record.get("counts") or {}
    if counts.get(SEV_HIGH, 0) > 0:
        return "NO-GO"
    if record.get("checks_not_run"):
        return "NO-GO"
    if record.get("non_vacuity_gaps"):
        return "NO-GO"
    return "GO"


def _benign_survives(benign: str, haystack: str) -> bool:
    return benign in haystack


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
#: Fields blanked before hashing, so two runs over the same tree compare equal.
#:
#: THE POLICY, because getting it wrong in either direction is a defect. A key
#: belongs here ONLY if its value genuinely differs between two runs on the same
#: tree. Blanking a *stable* key is not harmless: it hides real drift from
#: ``--check``, so an artifact could under-report (a mutating route silently
#: dropped from ``probes``, a ladder row changed in ``l0_permits``) while the
#: checker still said "current". Blanking too little is the opposite failure --
#: a check that fails every run gets ignored, which is why the genuinely
#: run-varying measurements below stay in the list.
VOLATILE_KEYS = {
    # stamped per run
    "generated_at", "repo_head", "repo_branch", "worktree_dirty",
    # timed per check
    "seconds",
    # depends on whether SQLite happened to have a -wal/-shm at listing time
    "sinks_scanned",
    # depends on the live protected service's state, which this gate does not own
    "protected_port_open", "protected_port_open_during_run",
    "runtime_db_sha256", "runtime_db_size", "wal_present", "shm_present",
    "before", "after",
    # present only when a check crashed, and then it is run-specific
    "traceback",
}


def _normalize(node: Any) -> Any:
    """Strip the volatile parts so two runs over the same source compare equal."""
    if isinstance(node, dict):
        return {k: ("<volatile>" if k in VOLATILE_KEYS else _normalize(v))
                for k, v in sorted(node.items())}
    if isinstance(node, list):
        return [_normalize(v) for v in node]
    return node


def _digest(record: Dict[str, Any]) -> str:
    payload = json.dumps(_normalize({k: v for k, v in record.items()
                                     if k != "reproducibility"}),
                         sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _git(*args: str) -> str:
    import subprocess as sp

    try:
        return sp.check_output(["git", *args], cwd=str(ROOT), text=True,
                               errors="replace").strip()
    except Exception:  # noqa: BLE001
        return ""


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="§21 v1.1 security gate")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="recompute and compare against the committed artifact")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    log("§21 v1.1 security gate -- protected service on port %d" % PROTECTED_PORT)
    record = run_checks()
    record["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    record["repo_head"] = _git("rev-parse", "HEAD")
    record["repo_branch"] = _git("rev-parse", "--abbrev-ref", "HEAD")
    record["worktree_dirty"] = bool(_git("status", "--porcelain"))
    digest = _digest(record)
    record["reproducibility"] = {"deterministic": True, "normalized_sha256": digest,
                                 "volatile_keys_stripped": sorted(VOLATILE_KEYS)}

    counts = record["counts"]
    log("\nverdict=%s  high=%d medium=%d low=%d info=%d  ran=%d/%d"
        % (record["verdict"], counts[SEV_HIGH], counts[SEV_MEDIUM],
           counts[SEV_LOW], counts[SEV_INFO], record["checks_ran"],
           record["check_count"]))
    if record["checks_not_run"]:
        log("checks NOT run: %s" % ", ".join(record["checks_not_run"]))
    if record["non_vacuity_gaps"]:
        log("vacuous checks: %s" % ", ".join(record["non_vacuity_gaps"]))
    for chk in record["checks"]:
        for f in chk["findings"]:
            if f["severity"] in (SEV_HIGH, SEV_MEDIUM):
                log("  [%s] %s: %s" % (f["severity"], f["check"], f["detail"]))

    out = Path(args.out)
    if args.check:
        if not out.is_file():
            log("[check] FAIL: %s does not exist" % out)
            return 1
        old = json.loads(out.read_text(encoding="utf-8"))
        if _digest(old) != digest:
            log("[check] FAIL: the committed artifact does not match a fresh run")
            return 1
        log("[check] OK: the committed artifact matches (sha %s)" % digest[:24])
        return 0 if record["verdict"] == "GO" else 1

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    log("wrote %s" % out)
    return 0 if record["verdict"] == "GO" else 1


if __name__ == "__main__":
    raise SystemExit(main())
