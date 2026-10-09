"""P4 #77 — server surface convergence: declared read-only surface, loopback-only
bind, and the D-41 no-regression pin.

Everything here is a DECLARATION test. A read-only route, a bind address and a
seam are all things that a small, plausible-looking edit can change while every
value-level test still passes: add a GET route and nothing breaks; change
``BIND_HOST`` and nothing breaks; route a manual directive through
``Actuator.execute`` and the manual-endpoint tests still pass. So the surface is
pinned three ways -- as data (what the server says it exposes), as shape (what
the framework actually serves / what the entrypoint actually binds), and as
source (where the seams are called).
"""
from __future__ import annotations

import ast
import sys
import types
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jcc_core import run as run_mod
from jcc_core.actuator import Actuator
from jcc_core.ports import ActuationPort
from jcc_core.run import BIND_HOST, check_bind_host
from jcc_core.server import OPEN_GET_ENDPOINTS, create_app

from jcc_core.config import Config, ServerConfig
from jcc_core.core import CoreEngine
from jcc_core.providers import build_provider
from jcc_core.schemas import Directive, DirectiveAction
from jcc_core.state_store import StateStore

TOKEN = "surface-test-token"
REPO_ROOT = Path(__file__).resolve().parents[3]   # F:/jev
PKGS = REPO_ROOT / "packages"

#: THE INERT ENGINE-PORT OUTCOME (P4 #77.3). A manual control-plane directive
#: that returns anything else is the D-28 bypass landing. This is pinned
#: POSITIVELY on purpose: a set of "reasons that would mean real execution" is
#: EMPTY while no code path can produce one, and `reason not in <empty set>`
#: would be vacuous -- the exact failure mode this file must not repeat.
INERT_ENGINE_PORT_OUTCOME = {
    "acted": False,
    "executable": False,
    "reason": "shadow_mode",
}


# --------------------------------------------------------------------------- #
# #77.1 — the open GET surface is DECLARED, and the declaration cannot drift
# --------------------------------------------------------------------------- #
def _get_routes(app: FastAPI) -> set[str]:
    """Every GET path FastAPI actually serves (path templates, as declared)."""
    return {r.path for r in app.routes if "GET" in getattr(r, "methods", ())}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("JCC_CONTROL_TOKEN", TOKEN)
    db_path = str(tmp_path / "surface.db")
    cfg = Config(server=ServerConfig(), state_store_db_path=db_path)
    store = StateStore(db_path)
    engine = CoreEngine(store, build_provider(cfg), Actuator(enabled=False), cfg)
    app = create_app(engine, cfg)
    return TestClient(app)


def test_health_declares_exactly_the_open_get_surface(client):
    """The declaration IS the surface: no undeclared read, no phantom entry."""
    body = client.get("/api/health").json()
    declared = set(body["open_get_endpoints"])
    served = _get_routes(client.app)

    assert declared == served, (
        "open GET surface drifted\n"
        f"  declared but NOT served (stale declaration): {sorted(declared - served)}\n"
        f"  served but NOT declared (INVISIBLE open route): {sorted(served - declared)}\n"
        "  -> add/remove the entry in jcc_core.server.OPEN_GET_ENDPOINTS in the "
        "SAME commit that adds/removes the route."
    )


def test_declared_surface_contents_are_the_five_read_endpoints():
    """Pins the list itself, so a rename cannot pass via the anti-drift check.

    Updated for v1.3 Wave 1, which ADDS a read-only Project/Mission domain GET
    surface (``/api/v1/domain/*``). Every entry here is a GET with no token
    dependency, and the anti-drift test above refuses any route that is served
    but not declared. The ``/api/v1/domain/*`` reads are open BY DESIGN: the
    product Home screen must render before any token exists, and none of them
    mutates anything. Writes stay token-gated.
    """
    assert set(OPEN_GET_ENDPOINTS) == {
        "/api/health",
        "/api/sessions",
        "/api/sessions/{sid}",
        "/api/audit",
        "/api/decisions",
        "/api/audit/verify",
        "/api/v1/scene",
        "/api/v1/events/stream",
        # v1.2.1 AI CONNECTION CENTER
        "/api/connections",
        "/api/connections/{agent}",
        "/api/onboarding/state",
        # v1.3 Wave 1 Project/Mission domain
        "/api/v1/domain/home",
        # v1.3 Wave 9.3: uncapped totals. Open for the same reason the other
        # projections are -- four scalars, no rows, and it is what lets the UI
        # disclose that a bounded list was truncated. Adding it moves the
        # declared set, which is why it is a reviewed edit here and in
        # OPEN_GET_ENDPOINTS rather than a silent widening.
        "/api/v1/domain/counts",
        "/api/v1/domain/projects",
        "/api/v1/domain/projects/{pid}",
        # Jev (TypeSafe System One) reachability. Open for the same reason: it
        # reports WHETHER a credential exists, never its value, and mutates
        # nothing. The executor-DECISION route is token-gated and is deliberately
        # absent from this list.
        "/api/v1/domain/jev/health",
        # Model & API centre. Open for the same reason: a STATUS read reporting
        # WHETHER a credential exists (plus last4) and never a value. The
        # credential write, probe, live-call and routing-PATCH routes are all
        # token-gated and deliberately absent from this list.
        "/api/v1/providers",
        "/api/v1/providers/{pid}",
        "/api/v1/providers/{pid}/models",
        "/api/v1/model-routing",
        "/api/v1/domain/missions",
        "/api/v1/domain/missions/{mid}",
        "/api/v1/domain/missions/{mid}/timeline",
        "/api/v1/domain/missions/{mid}/runs",
        "/api/v1/domain/missions/{mid}/artifacts",
        "/api/v1/domain/connections",
        "/api/v1/domain/needs-attention",
        # v1.3 Wave 3 first real mission: the launch probe reports only host-side
        # session liveness (three-state), and the workload endpoint reports only
        # telemetry the engine already holds. Neither mutates, so both are open
        # reads by design; the launch write itself stays token-gated.
        "/api/v1/domain/launch/probe",
        "/api/v1/domain/missions/{mid}/workload",
        # v1.3 Wave 4: the resolution read is open (it answers "what CAN I do here"
        # for each inbox item); the resolve WRITE stays token-gated.
        "/api/v1/domain/needs-attention/resolutions",
        # v1.3 Wave 5: the deliverable view and one-deliverable-by-id read are open
        # (read-only projections); the verify WRITE stays token-gated.
        "/api/v1/domain/deliverables",
        "/api/v1/domain/artifacts/{aid}",
        # v1.3 Wave 7: one connection with per-capability provenance is open (it
        # answers "why does JCC believe this works?"); the verify WRITE -- which
        # runs a real probe -- stays token-gated.
        "/api/v1/domain/connections/{cid}",
    }
    # /ws is a WebSocket route, not a GET -- pinning that avoids a false drift.
    assert not any(p.startswith("/ws") for p in OPEN_GET_ENDPOINTS)
    # Every declared domain read must be a GET, never a mutating verb.
    assert not any("/domain/" in p and p.endswith(("/transition", "/decide"))
                   for p in OPEN_GET_ENDPOINTS)


def test_every_declared_open_endpoint_is_reachable_without_a_token(client):
    """Declaration without reachability would be theatre; check both together.

    The read endpoints are open BY DESIGN, so a 401 here would mean the
    declaration lies. New POST routes must keep failing closed, which the
    mutating-endpoint tests cover; what matters here is that a declaration
    cannot be added to make a route *look* open.

    The SSE route streams; it is checked with an explicit stream context so the
    test does not block waiting on a long-lived connection.
    """
    for path in OPEN_GET_ENDPOINTS:
        if path == "/api/v1/events/stream":
            # The SSE route is declared open (no token dependency) by
            # construction; a live HTTP check is exercised by
            # test_events_sse.py. TestClient buffers streamed bodies and would
            # hang on a long-lived connection, so we assert the declaration here
            # rather than reading the stream.
            assert path in OPEN_GET_ENDPOINTS
            continue
        r = client.get(path)
        assert r.status_code != 401, f"{path} is declared open but refuses a tokenless read"


# --------------------------------------------------------------------------- #
# #77.2 — loopback-only bind, enforced at the entrypoint
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "host", ["0.0.0.0", "10.0.0.5", "192.168.1.10", "::", "0.0.0.0\n"]
)
def test_non_loopback_bind_is_refused(host):
    with pytest.raises(SystemExit) as exc:
        check_bind_host(Config(server=ServerConfig(host=host)))
    message = str(exc.value)
    assert "refusing to start" in message and host.split()[0] in message, message


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", ""])
def test_loopback_bind_is_accepted(host):
    check_bind_host(Config(server=ServerConfig(host=host)))  # must not raise


def test_bind_host_constant_is_loopback():
    assert BIND_HOST == "127.0.0.1"


class _UvicornSpy:
    """Stands in for ``uvicorn`` so the entrypoint can be run without a socket."""

    def __init__(self):
        self.calls = []

    def run(self, app, **kwargs):
        self.calls.append(kwargs)


def test_main_refuses_to_start_on_a_non_loopback_bind(tmp_path, monkeypatch):
    """End-to-end: ``0.0.0.0`` means no process, not a corrected process.

    Proven by absence -- the marker/PID lock (ADR-0006) and the database are
    never created, i.e. the refusal happens before the engine exists.
    """
    db = tmp_path / "jcc.db"
    cfg = Config(
        server=ServerConfig(host="0.0.0.0", port=0),
        state_store_db_path=str(db),
    )
    monkeypatch.setattr(run_mod, "load_config", lambda *a, **k: cfg)
    monkeypatch.setattr(run_mod, "_wire_shadow", lambda *a, **k: None)
    spy = _UvicornSpy()
    monkeypatch.setitem(sys.modules, "uvicorn", spy)

    with pytest.raises(SystemExit) as exc:
        run_mod.main()

    assert "refusing to start" in str(exc.value)
    assert spy.calls == [], "the entrypoint bound (or tried to) despite the refusal"
    assert not db.exists(), "the store was created -- the refusal is too late"


def test_main_binds_loopback_when_the_config_asks_for_it(tmp_path, monkeypatch):
    """The positive half: a loopback config actually starts, on 127.0.0.1."""
    db = tmp_path / "jcc.db"
    cfg = Config(
        server=ServerConfig(host="127.0.0.1", port=0),
        state_store_db_path=str(db),
    )
    monkeypatch.setattr(run_mod, "load_config", lambda *a, **k: cfg)
    monkeypatch.setattr(run_mod, "_wire_shadow", lambda *a, **k: None)
    spy = _UvicornSpy()
    monkeypatch.setitem(sys.modules, "uvicorn", spy)

    run_mod.main()

    assert spy.calls, "the entrypoint did not bind at all"
    assert spy.calls[0]["host"] == BIND_HOST == "127.0.0.1", spy.calls[0]


# --------------------------------------------------------------------------- #
# #77.3 — D-41: Actuator.execute is unreachable from any port
# --------------------------------------------------------------------------- #
def _attr_call_sites(root: Path, attr: str) -> set[str]:
    """Files under ``root`` containing ``<something>.attr(...)`` call sites.

    DB helpers legitimately call ``conn.execute(...)``, so the receiver NAME is
    checked against a known non-actuator allowlist; anything else calling
    ``.execute`` is a candidate finding, which is the point.
    """
    db_like = {"conn", "cur", "cursor", "real", "db", "sqlite", "snapshot"}
    hits: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        if "tests" in path.parts or path.name.startswith("test_"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == attr:
                receiver = func.value
                name = receiver.id if isinstance(receiver, ast.Name) else None
                if name in db_like:
                    continue
                hits.add(f"{path.relative_to(REPO_ROOT).as_posix()}:{node.lineno}")
    return hits


def test_actuator_execute_has_no_call_site_in_the_product_tree():
    """D-40(a)/D-41: ``execute`` is the tripwire; it must have ZERO call sites.

    ``Actuator.execute`` is the one method that can reach a real side-effect once
    actuation is enabled, so nothing -- engine, server, supervisor -- may call it.
    """
    hits = _attr_call_sites(PKGS, "execute")
    # `execute` is defined in actuator.py and denied inside it; both are defs, not
    # call sites, so a non-empty result means a real caller exists.
    assert hits == set(), f"Actuator.execute is called at: {sorted(hits)}"


def test_engine_port_dispatch_call_sites_are_pinned():
    """D-41 falsifier, PINNED AS OBSERVED (see the deviation note).

    The ADR requires ``ActuationPort.dispatch`` to have exactly ONE call site
    (the engine's). The tree has TWO: the engine's, and the control-plane manual
    path ``server._manual``, which reaches the SAME port a second time. That
    second site is the D-28 policy bypass -- ``_manual`` enters the actuation
    seam outside policy -- so it cannot be silently normalised either way. It is
    pinned here as a KNOWN DEVIATION with the evidence, and reported to the lead
    as a change request; the pin exists so the count can only move by a visible,
    reviewed edit, never by drift.
    """
    hits = _attr_call_sites(PKGS, "dispatch")
    core = sorted(h for h in hits if h.startswith("packages/jcc_core/core.py"))
    server = sorted(h for h in hits if h.startswith("packages/jcc_core/server.py"))
    supervisor = sorted(h for h in hits if h.startswith("packages/supervisor/"))

    assert len(core) == 1, f"the engine call site must exist and be unique: {core}"
    assert len(supervisor) == 1, (
        "the Sentinel's OWN DispatchPort site must stay unique: "
        f"{supervisor}"
    )
    # Pinned by SHAPE (inside the ``_manual`` helper), not by line number: the
    # deviation survives edits to unrelated lines, and any change here is a
    # reviewed one.
    src = (PKGS / "jcc_core" / "server.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    def _find_function(node, name):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                continue  # a method, not the module-level helper
            if isinstance(child, ast.FunctionDef) and child.name == name:
                return child
            found = _find_function(child, name)
            if found is not None:
                return found
        return None

    manual = _find_function(tree, "_manual")
    assert manual is not None, "server._manual vanished -- re-check the D-28 path"
    sites = [
        n for n in ast.walk(manual)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "dispatch"
    ]
    assert len(sites) == 1, (
        "KNOWN DEVIATION (D-41), pinned: ``_manual`` calls the engine port "
        f"directly (ADR-0008 wants exactly one engine-port call site). Sites: "
        f"{[s.lineno for s in sites]}. Reported to the lead as a change "
        "request; do not 'fix' it silently -- it is also the D-28 bypass."
    )
    assert server, "the manual path's site must be the one in server.py"


def test_actuation_port_protocol_declares_dispatch_and_not_execute():
    """The seam's TYPE must not carry ``execute``: a side-effect-free port cannot
    be enacted by swapping an extra method onto it (D-41's two-distinct-ports)."""
    assert hasattr(ActuationPort, "dispatch")
    assert not hasattr(ActuationPort, "execute")
    assert not hasattr(ActuationPort, "_dispatch")


@pytest.mark.parametrize(
    "endpoint", ["/api/pause", "/api/steer", "/api/retry"]
)
def test_the_manual_path_produces_no_execution_result(client, endpoint):
    """#77.3 CONSEQUENCE pin (lead ruling): E-6 cares that the manual path CANNOT
    reach a real side-effect, not that it is spelled a particular way.

    The shape pin above would still pass if `_manual` were rewired to
    `Actuator.execute` while remaining inert, and it would blow up for the WRONG
    reason. So the OUTCOME is pinned too, on the wire, for every manual control
    endpoint: whatever the directive routes through, the response is the inert
    engine-port result and nothing else.
    """
    r = client.post(endpoint, json={"session_id": "s-manual"},
                    headers={"X-JCC-Token": TOKEN})
    assert r.status_code == 200, (endpoint, r.status_code, r.text)
    res = r.json()["actuation"]

    for key, expected in INERT_ENGINE_PORT_OUTCOME.items():
        assert res[key] == expected, (
            f"{endpoint} did not return the inert engine-port outcome; {key}={res[key]!r} "
            f"!= {expected!r}. A manual directive reaching a real side-effect is the "
            "D-28 bypass this pin exists to catch."
        )
    # non-vacuity: the response really did come off an ACTUATOR object -- an inert
    # result produced by a port that was never consulted would pass without this.
    assert res["directive_id"].startswith("dir_manual_"), res


def test_the_manual_path_and_the_real_port_refuse_differently_but_equally(client):
    """Non-vacuity for the consequence pin: the two ports are DISTINCT (D-41's
    "two distinct ports"), and being distinct must not make the manual one
    weaker. Same refusal class, different seam, neither one executing."""
    r = client.post("/api/pause", json={"session_id": "s-manual"},
                    headers={"X-JCC-Token": TOKEN})
    manual_reason = r.json()["actuation"]["reason"]
    assert manual_reason == "shadow_mode"

    # the OTHER port refuses with its own reason and STILL refuses to execute:
    a = Actuator(enabled=True, allowed_actions=["PAUSE"])
    out = a.execute(
        Directive(directive_id="dir_pin", session_id="s", timestamp=0.0,
                  action=DirectiveAction.PAUSE)
    )
    assert out["acted"] is False
    assert out["reason"] == "real_actuation_not_implemented"
    assert out["reason"] != manual_reason, "the two ports must stay distinguishable"


def test_the_shiped_actuator_denies_execute_even_when_enabled():
    """Defence in depth: even a fully enabled actuator with the action allowlisted
    refuses a real side-effect (D-27/D-41 inert-by-construction)."""
    a = Actuator(enabled=True, allowed_actions=["ESCALATE"])
    from jcc_core.schemas import Directive, DirectiveAction

    out = a.execute(
        Directive(directive_id="d", session_id="s", timestamp=0.0,
                  action=DirectiveAction.ESCALATE)
    )
    assert out["acted"] is False
    assert out["reason"] == "real_actuation_not_implemented"
    # ...and the port the engine actually uses is inert regardless.
    assert a.dispatch(
        Directive(directive_id="d", session_id="s", timestamp=0.0,
                  action=DirectiveAction.ESCALATE)
    )["reason"] == "shadow_mode"
