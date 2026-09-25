"""P3 (Jev Shadow) INDEPENDENT ACCEPTANCE GATE harness — task #46 (Harbor).

This module is the *independent* acceptance evidence for P3. It is designed to be
able to FAIL: a rubber-stamp verifier undermines the chain (the #54 defect shipped
green precisely because the falsifier passed vacuously). Every check below is
non-vacuous by construction — where a check could pass over an EMPTY set, it also
asserts a positive-count clause (see the D-40 block).

WHAT IT COVERS (mapped to the frozen docs)
-----------------------------------------
* ``P3_METRIC_EVAL_DESIGN.md`` §5 **A1–A11** probes (shadow != actuation), read
  against the frozen ``directives`` / ``shadow_decisions`` / ``audit_tape``
  surfaces.
* **D-40** non-actuation falsifiers (a–e), with the load-bearing non-vacuity
  clause: ``Sentinel.persistence_attempts > 0`` **AND**
  ``persistence_failures == 0`` **AND** ``persistence_durable_writes == attempts``
  **AND** ``SELECT COUNT(*) FROM shadow_decisions`` > 0 — and only THEN
  "every recorded row has acted == False". A row-count-only check passes over an
  empty shadow plane; that vacuous pass is exactly how #54 shipped.
* Engine inertness (``core.py`` ingest step 6 -> acted False, reason
  ``shadow_mode``).
* The two **declared** distinct reasons — engine ``ActuationPort`` ->
  ``shadow_mode``; Sentinel ``DispatchPort`` -> ``shadow_no_actuation``
  (Errata E-1 / D-41). They are asserted distinct; the difference is **not** a
  defect.
* ``Actuator.execute()`` -> ``"actuation_disabled"`` with no side effect.
* **D-26** server boundary: a malformed envelope -> structured 422 + an audit row
  written; never 500; never a synthesized ESCALATE.
* The four frozen budget tests (ADR-0008 D-34 / spec §2.4):
  ``..._exhaustion_matrix``, ``..._survives_restart``,
  ``..._suppression_is_deterministic``, ``..._defaults_match_config_yaml`` — plus
  a store-derivation liveness falsifier that catches an inert dimension.

HONEST STATUS (do NOT read this file as a PASS). As of the last pre-freeze run:
* ``scorer_tokens`` is **inert by design** (no persisted column) and is PINNED as
  such by ``test_budget_scorer_tokens_is_inert_by_design`` — a waiver, not a gap.
  The ``cost_units`` derivation has landed, so the exhaustion matrix is green.
* ``test_R6_1_single_resolver_under_per_agent_override`` is **EXPECTED RED** until
  F-p3-4 / #61 lands. That red is the gate DOING ITS JOB (lead ruled #61 MUST FIX);
  it is never absorbed or reclassified.
* F-p3-1 (engine-side budget) is a **WAIVER**, recorded by atlas-2 as an explicit
  spec row at ``docs/p3/P3_ARCHITECTURE_SPEC.md:500`` ("This is a waiver, not an
  implementation"). Do not re-open it as a gate gap.

Run: ``F:/jev/artifacts/acceptance/p3_gate_run.py [--full]`` — the runner sets a
safe ``--basetemp`` under ``tempfile.gettempdir()`` (a reused basetemp is
``_rm_rf``'d by pytest at startup and can trip the sandbox bulk-delete guard, so a
fresh unique child is used per run).
"""
from __future__ import annotations

import ast
import hashlib
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jcc_core import decision, policy
from jcc_core.actuator import FORBIDDEN_EFFECTS, Actuator
from jcc_core.config import load_config
from jcc_core.core import CoreEngine
from jcc_core.providers import build_provider
from jcc_core.schemas import AEPEnvelope, Directive, DirectiveAction
from jcc_core.server import create_app
from jcc_core.signing import DirectiveSigner
from jcc_core.state_store import StateStore
from supervisor.budget import (
    COST_UNITS,
    BudgetGovernor,
    BudgetLimits,
    CODE_ASSESS_CALLS,
    CODE_COST_UNITS,
    CODE_DIRECTIVES,
    CODE_ESCALATIONS,
    CODE_EVENTS,
    CODE_RATE,
    CODE_RETRIES,
    CODE_SCORER_TOKENS,
    CODE_WALL_CLOCK,
)
from supervisor import shadow_store
from supervisor.ports import ShadowActuationPort
from supervisor.sentinel import BlockerCode, Sentinel

REPO_ROOT = Path(__file__).resolve().parents[3]
SHIPPED_CONFIG = REPO_ROOT / "config" / "jcc.yaml"
TOKEN = "p3-gate-token"

FOUR_AGENTS = {"workbuddy", "claude", "codex", "hermes"}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _sha(path: Path) -> str:
    """EOL-normalised sha256 for the A8 freeze pin.

    The repo runs with ``core.autocrlf=true`` and NO ``.gitattributes``, so a FRESH
    checkout (``git worktree`` / clone) smudges committed LF to CRLF while the main
    working tree holds LF. A raw byte hash therefore does NOT survive a re-checkout
    of the SAME commit (measured at frozen SHA ``49d91d4``: a clean worktree hashed
    0/12 against a main-tree baseline, 12/12 once CRLF->LF was normalised). The
    normalisation keeps the pin checkout-independent while still failing on any
    real content change.
    """
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _key(path) -> str:
    """Canonical posix key so pin scope and pin baseline compare reliably on Windows."""
    return Path(path).as_posix()


def _rel(path) -> str:
    """Repo-relative posix key. The A8 scope==baseline consistency check must hold
    from ANY checkout root; absolute ``F:/jev/...`` keys made a relocated
    ``git worktree`` false-fail as 'scope/baseline mismatch' (measured at frozen
    SHA ``49d91d4``), i.e. the pin was not portable across checkouts."""
    return Path(path).resolve().relative_to(REPO_ROOT.resolve()).as_posix()


#: Production artifacts whose bytes are PINNED (A8 negative-control / freeze guard).
#: SCOPE EXTENDED per team-lead ruling: the MUST-FIX batch touches state_store.py,
#: budget.py, policy.py (#61/#63), run.py, core.py (#61), server.py (#62) and
#: config.py + jcc.yaml (#64/#65). A pin that excludes those cannot detect drift in
#: the very code the gate protects, which would make A8 decorative.
_PROD_ARTIFACTS = [
    SHIPPED_CONFIG,
    REPO_ROOT / "packages" / "jcc_core" / "core.py",
    REPO_ROOT / "packages" / "jcc_core" / "actuator.py",
    REPO_ROOT / "packages" / "jcc_core" / "decision.py",
    REPO_ROOT / "packages" / "jcc_core" / "state_store.py",
    REPO_ROOT / "packages" / "jcc_core" / "policy.py",
    REPO_ROOT / "packages" / "jcc_core" / "run.py",
    REPO_ROOT / "packages" / "jcc_core" / "server.py",
    REPO_ROOT / "packages" / "jcc_core" / "config.py",
    REPO_ROOT / "packages" / "supervisor" / "sentinel.py",
    REPO_ROOT / "packages" / "supervisor" / "shadow_store.py",
    REPO_ROOT / "packages" / "supervisor" / "budget.py",
]
# FROZEN A8 baseline. These hashes are captured explicitly (NOT recomputed at
# import time) so that A8 detects a production artifact moving, rather than
# silently following whatever HEAD happens to be. Any legitimate change requires
# a deliberate re-baseline recorded with old->new hashes + reason.
#
# RE-BASELINE @ HEAD 9ee4507 (freeze batch; 3 files moved, 9 byte-current).
# Independently recomputed by harbor (not taken from forge's report):
#   config/jcc.yaml  old 70b5bdf9..  -> new 4ae55770..   (#65, D-37 versioning)
#   run.py           old 910fefc6..  -> new d820cd84..   (#61 R6-1 + #64 fail-closed)
#   config.py        old fac5baa7..  -> new af0ddc18..   (#64 + #65)
A8_FROZEN_AT = "re-baselined @ HEAD 9ee4507 (3 files: jcc.yaml/run.py/config.py); was 02bdb3f"
# Keys are REPO-RELATIVE (not absolute ``F:/jev/...``) so the scope==baseline
# consistency check holds from any checkout root and the pin is portable.
_BASELINE_HASHES = {
    "config/jcc.yaml":
        "4ae55770ed70dbd0ec940514f51ebf8adf3e703a9a49105656d8b9e643acaee0",
    "packages/jcc_core/core.py":
        "6e44314a9578d2ced819bbf2c220b366a37323e82c51cf644392b915a4a6d117",
    "packages/jcc_core/actuator.py":
        "5f0e338c3618e434702e1f50f2c037c5b604e642aa0f364d93cd96383a6e1ae2",
    "packages/jcc_core/decision.py":
        "b61c88177e776aeacac4cda17bd14410a0f8ef4e88de91f3a6faa3262ad0e31c",
    "packages/jcc_core/state_store.py":
        "399b08994b231e624a286d90b8c946f76bb14912e45490806a227c4d1be41587",
    "packages/jcc_core/policy.py":
        "a4fe24cb1b2ad4b55ce91cb8360245a6df77c2033b3ace829cbf0614f2359258",
    "packages/jcc_core/run.py":
        "d820cd848befeac1ed99a66e710b5353a53a23409f0cfddb27e4b986606765f3",
    "packages/jcc_core/server.py":
        "92ac49cf294935c7c7d187dce5eea09b10cbd90a41ea150e5ae1a005c810beff",
    "packages/jcc_core/config.py":
        "af0ddc1868abf19edc8f7babd5edc61c7fd47ed7ee31187e2798edc844f1c08f",
    "packages/supervisor/sentinel.py":
        "65dbfea5050e9e6a0087354b219c88a741d2b4ff4cb9598c8beff7987b2655eb",
    "packages/supervisor/shadow_store.py":
        "b101aebef464bab5f01ebb7cf3ef3318262d5cd4f73c61343ded92e3dcae5a3e",
    "packages/supervisor/budget.py":
        "1efb9186fe2191b968508c81808124b5113235d1e86d593b780b213004a2a311",
}


def _build(tmp_path, monkeypatch, *, enabled=True):
    """Build the REAL engine through the REAL composition root (`run._wire_shadow`).

    Uses the SHIPPED ``config/jcc.yaml`` (so the live ``p3.shadow`` block is what
    is exercised) with only ``enabled`` toggled and the DB redirected.
    """
    from jcc_core.run import _wire_shadow

    monkeypatch.setenv("JCC_CONTROL_TOKEN", TOKEN)
    cfg = load_config(str(SHIPPED_CONFIG))
    cfg.state_store_db_path = str(tmp_path / "jcc.db")
    cfg.raw["p3"]["shadow"]["enabled"] = enabled
    store = StateStore(cfg.state_store_db_path)
    engine = CoreEngine(store, build_provider(cfg), Actuator(enabled=False), cfg)
    _wire_shadow(engine, store, cfg)
    app = create_app(engine, cfg)
    return TestClient(app), store, engine, _sentinel_of(engine), cfg


def _sentinel_of(engine) -> Sentinel:
    for cb in engine._subscribers:
        owner = getattr(cb, "__self__", None)
        if isinstance(owner, Sentinel):
            return owner
    raise AssertionError("no Sentinel subscribed (run._wire_shadow did not wire it)")


def _env(i: int, agent: str = "workbuddy", *, evidence=True) -> dict:
    body = {
        "event_id": f"ev{i}",
        "delivery_id": f"dl{i}",
        "session_id": f"s{i % 2}",
        "agent": agent,
        "project": "p",
        "event_type": "tool_call",
        "payload": {"i": i},
    }
    if evidence:
        body["evidence"] = {"ok": True, "i": i}
    return body


def _drive(client, n=8) -> int:
    """Ingest ``n`` distinct agent events across the four agents. Returns count."""
    agents = ["workbuddy", "claude", "codex", "hermes"]
    for i in range(n):
        r = client.post("/api/assess", json=_env(i, agents[i % 4]),
                        headers={"X-JCC-Token": TOKEN})
        assert r.status_code == 200, (i, r.status_code, r.text[:200])
    return n


def _db(client_or_store, sql, params=()):
    store = client_or_store
    with store.connection() as conn:
        return conn.execute(sql, params).fetchall()


def _shadow_rows(store, **kw):
    with store.connection() as conn:
        shadow_store.ensure_schema(conn)
        return shadow_store.read_decisions(conn, **kw)


@pytest.fixture()
def live(tmp_path, monkeypatch):
    client, store, engine, sentinel, cfg = _build(tmp_path, monkeypatch)
    _drive(client, 8)
    return client, store, engine, sentinel, cfg


# =========================================================================== #
# §5 A1–A11 — shadow != actuation
# =========================================================================== #
def test_A1_no_directive_has_acted(live):
    """A1: `SELECT COUNT(*) FROM directives WHERE acted=1` == 0 (read the DB)."""
    _client, store, _e, _s, _c = live
    (n,) = _db(store, "SELECT COUNT(*) FROM directives WHERE acted=1")[0]
    assert n == 0, f"{n} directive(s) claim to have acted"
    (total,) = _db(store, "SELECT COUNT(*) FROM directives")[0]
    assert total > 0, "vacuous: no directives were produced"


def test_A2_two_port_reasons_exactly(live):
    """A2: the two persisted surfaces carry exactly the two port reasons.

    engine `directives.actuation_reason == 'shadow_mode'`;
    Sentinel `shadow_decisions.actuation_reason == 'shadow_no_actuation'`;
    the legacy `actuation_disabled` must appear on NEITHER persisted surface.
    """
    _client, store, _e, _s, _c = live
    eng = [r[0] for r in _db(store, "SELECT DISTINCT actuation_reason FROM directives")]
    assert eng, "vacuous: no directives"
    assert set(eng) == {"shadow_mode"}, f"engine reasons: {eng}"

    sh = [r[0] for r in _db(store, "SELECT DISTINCT actuation_reason FROM shadow_decisions")]
    assert sh, "vacuous: no shadow rows"
    assert set(sh) == {"shadow_no_actuation"}, f"sentinel reasons: {sh}"

    assert "actuation_disabled" not in set(eng) | set(sh), (
        "legacy reason leaked onto a persisted surface"
    )


def test_A3_actuation_disabled_in_config_and_health(live):
    """A3: loaded config actuation disabled + empty allowlist; /api/health agrees."""
    client, _store, _e, _s, cfg = live
    assert cfg.actuation.enabled is False
    assert list(cfg.actuation.allowed_actions) == []
    h = client.get("/api/health")
    assert h.status_code == 200
    assert h.json()["actuation_enabled"] is False


def test_A4_can_act_is_false_for_every_directive(live):
    """A4: `Actuator.can_act(d)` is False for every directive in the tape."""
    _client, store, engine, _s, _c = live
    rows = engine.store.get_directives()
    assert rows, "vacuous: no directives"
    for r in rows:
        d = Directive(
            directive_id=r["directive_id"], session_id=r["session_id"],
            timestamp=r["timestamp"], action=DirectiveAction(r["action"]),
            target_actuator=r["target_actuator"],
        )
        assert engine.actuator.can_act(d) is False, r["directive_id"]


def test_A5_both_ports_return_inert(tmp_path, monkeypatch):
    """A5: every dispatch on BOTH ports returns acted=False / executable=False.

    Spies are scoped to the two PORT surfaces only. A *direct* `Actuator.execute`
    class call legitimately still returns `actuation_disabled` (it is NOT a port
    surface) and is asserted separately.
    """
    client, store, engine, sentinel, _c = _build(tmp_path, monkeypatch)

    engine_calls = []
    real_dispatch = engine.actuator.dispatch

    def engine_spy(directive):
        res = real_dispatch(directive)
        engine_calls.append(res)
        return res

    monkeypatch.setattr(engine.actuator, "dispatch", engine_spy)

    sentinel_calls = []
    real_port = sentinel.dispatch_port

    class _RecordingPort:
        def dispatch(self, directive):
            res = real_port.dispatch(directive)
            sentinel_calls.append(res)
            return res

    monkeypatch.setattr(sentinel, "dispatch_port", _RecordingPort())

    ingested = _drive(client, 3)

    assert len(engine_calls) == ingested, (len(engine_calls), ingested)
    assert len(sentinel_calls) == ingested, (len(sentinel_calls), ingested)
    for res in engine_calls + sentinel_calls:
        assert res["acted"] is False, res
        assert res["executable"] is False, res


def test_A6_target_actuator_is_noop(live):
    """A6: every directive targets 'noop' — no real actuator name is ever used."""
    _client, store, _e, _s, _c = live
    names = {r[0] for r in _db(store, "SELECT DISTINCT target_actuator FROM directives")}
    assert names == {"noop"}, names


def test_A7_chains_stay_intact_across_restart(live, tmp_path):
    """A7: verify_audit() AND verify_shadow_chain() are intact, then again after a
    store restart (reopen the same DB)."""
    _client, store, _e, _s, _c = live
    ok_a, n_a = store.verify_audit()
    assert ok_a is True and n_a > 0, (ok_a, n_a)
    ok_s, cnt_s = _shadow_chain(store)
    assert ok_s is True and cnt_s > 0, (ok_s, cnt_s)

    restarted = StateStore(str(tmp_path / "jcc.db"))   # restart: same file, new object
    ok_a2, n_a2 = restarted.verify_audit()
    assert ok_a2 is True and n_a2 == n_a, (ok_a2, n_a2, n_a)
    ok_s2, cnt_s2 = _shadow_chain(restarted)
    assert ok_s2 is True and cnt_s2 == cnt_s, (ok_s2, cnt_s2, cnt_s)


def test_A8_negative_control_enabled_without_allowlist(tmp_path):
    """A8: forcing enabled=True with an EMPTY allowlist still refuses; production
    artifacts are byte-unchanged after the whole run."""
    a = Actuator(enabled=True, allowed_actions=[])
    d = Directive(directive_id="d", session_id="s", timestamp=0.0,
                  action=DirectiveAction.ESCALATE)
    assert a.can_act(d) is False
    assert a.execute(d)["reason"] == "action_not_allowed"
    # even allowlisting the action does not reach a side effect (P3+ is absent)
    a2 = Actuator(enabled=True, allowed_actions=["ESCALATE"])
    assert a2.execute(d)["reason"] == "real_actuation_not_implemented"
    # Pin scope MUST equal pin baseline, else an artifact is silently unchecked.
    pinned = {_rel(p) for p in _PROD_ARTIFACTS}
    unbaselined = sorted(pinned - set(_BASELINE_HASHES))
    assert not unbaselined, (
        f"PIN SCOPE/BASELINE MISMATCH: {unbaselined} are listed in _PROD_ARTIFACTS "
        "but have no entry in _BASELINE_HASHES, so they would be SILENTLY UNCHECKED. "
        "Add their hashes in the freeze commit (old->new recorded)."
    )
    extra = sorted(set(_BASELINE_HASHES) - pinned)
    assert not extra, (
        f"PIN BASELINE/SCOPE MISMATCH: {extra} carry a baseline hash but are NOT in "
        "_PROD_ARTIFACTS, so the digest is dead weight (stale/renamed entry)."
    )
    for rel, digest in _BASELINE_HASHES.items():
        assert _sha(REPO_ROOT / rel) == digest, (
            f"PINNED production artifact changed: {rel}\n"
            f"  frozen at: {A8_FROZEN_AT}\n"
            "  => either the frozen SHA moved without a re-baseline, or the tree is "
            "dirty. Re-baseline ONLY in the same commit that freezes the SHA, and "
            "record old->new hashes + reason in p3_gate_result.json and the report."
        )


def test_A9_port_log_surface_has_no_side_effect_line(tmp_path, monkeypatch, caplog):
    """A9: the port surface logs only inert outcomes; `Actuator.execute` is never
    reached from ingest (non-vacuity: the engine dispatch IS called)."""
    client, _store, engine, _s, _c = _build(tmp_path, monkeypatch)

    calls = {"execute": 0}
    real_execute = Actuator.execute

    def poison(self, directive):
        calls["execute"] += 1
        return real_execute(self, directive)

    monkeypatch.setattr(Actuator, "execute", poison)
    dispatches = {"n": 0}
    real_dispatch = engine.actuator.dispatch

    def spy(directive):
        dispatches["n"] += 1
        return real_dispatch(directive)

    monkeypatch.setattr(engine.actuator, "dispatch", spy)

    with caplog.at_level("INFO"):
        _drive(client, 3)

    assert dispatches["n"] == 3, "non-vacuity: engine dispatch must be exercised"
    assert calls["execute"] == 0, "ingest reached Actuator.execute!"
    joined = "\n".join(r.getMessage() for r in caplog.records).lower()
    for bad in ("shell", "subprocess", "os.system", "side effect", "executed"):
        assert bad not in joined, f"side-effect-looking log line: {bad!r}"


def test_A10_audit_event_parity_excluding_control_plane(live):
    """A10: for INGESTED agent events, audit rows are 1:1 with accepted events and
    each `ACTION:directive_id` matches a directives row. The naive
    `COUNT(audit)==COUNT(events)` is WRONG — control-plane rows must be excluded.

    THREE row classes are excluded, not one (metric, latent false-FAIL):
      * control-plane rows (`/api/pause|steer|retry`) — the manual endpoint;
      * D-26 REJECTED-ingest rows. `server.py` appends the audit row BEFORE
        `store.add_event`, so a rejected row has NO `events` row, yet its `agent`
        comes from the request body — a malformed body carrying a valid agent name
        would otherwise land inside the four-agent set (one extra audit row, zero
        extra events => false FAIL). Excluded by BOTH markers, NULL-safe.
    """
    client, store, _e, _s, _c = live
    # non-vacuity: create a CONTROL-PLANE audit row (manual endpoint) so a naive
    # count would visibly fail.
    r = client.post("/api/pause", json={"session_id": "s0"}, headers={"X-JCC-Token": TOKEN})
    assert r.status_code == 200, r.text[:200]

    agent_audit = _db(
        store,
        "SELECT directive FROM audit_tape WHERE agent IN (?,?,?,?) "
        "AND directive NOT LIKE 'NONE:rejected%' "
        "AND (assessment IS NULL OR assessment NOT LIKE 'rejected:%')",
        ("workbuddy", "claude", "codex", "hermes"),
    )
    control_audit = _db(store, "SELECT COUNT(*) FROM audit_tape WHERE agent='control-plane'")[0][0]
    assert control_audit >= 1, "non-vacuity: the manual endpoint should append a row"

    accepted = _db(store, "SELECT COUNT(*) FROM events WHERE agent IN (?,?,?,?)",
                   ("workbuddy", "claude", "codex", "hermes"))[0][0]
    assert len(agent_audit) == accepted, (len(agent_audit), accepted)

    directive_ids = {row[0] for row in _db(store, "SELECT directive_id FROM directives")}
    for (lossy,) in agent_audit:
        assert lossy and ":" in lossy, lossy
        _, did = lossy.split(":", 1)
        assert did in directive_ids, f"audit row {lossy!r} has no directives row"


def test_A11_no_new_inbound_surface(live):
    """A11: no new inbound surface — mutating endpoints are exactly
    {assess,pause,steer,retry}; /api/hooks is 404."""
    client, _store, engine, _s, cfg = live
    app = client.app
    post_paths = {
        route.path for route in app.routes
        if getattr(route, "methods", None) and "POST" in route.methods
    }
    assert post_paths == {"/api/assess", "/api/pause", "/api/steer", "/api/retry"}, post_paths
    assert client.get("/api/hooks").status_code == 404
    assert client.post("/api/hooks", json={}).status_code == 404


# =========================================================================== #
# D-40 — non-actuation falsifiers (a–e)
# =========================================================================== #
def test_D40a_engine_dispatch_called_once_per_ingest_and_execute_never(tmp_path, monkeypatch):
    """D-40(a): poison-pill tripwire `execute_calls == 0`; the engine `dispatch`
    IS called once per ingest (so the probe always exercises a real port call and
    the inert invariant is not vacuous)."""
    client, store, engine, _sentinel, _c = _build(tmp_path, monkeypatch)

    poisoned = {"n": 0}
    real_execute = Actuator.execute

    def poison(self, directive):
        poisoned["n"] += 1
        return real_execute(self, directive)

    monkeypatch.setattr(Actuator, "execute", poison)
    engine_dispatches = {"n": 0}
    real_dispatch = engine.actuator.dispatch

    def spy(d):
        engine_dispatches["n"] += 1
        return real_dispatch(d)

    monkeypatch.setattr(engine.actuator, "dispatch", spy)

    ingested = _drive(client, 4)
    assert engine_dispatches["n"] == ingested, (
        "non-vacuity: the engine port must be exercised once per ingest"
    )
    assert poisoned["n"] == 0, "engine ingest reached Actuator.execute"
    # with the genuine ShadowActuationPort the tape stays fully inert
    rows = _shadow_rows(store, limit=100)
    assert rows and all(r["acted"] == 0 for r in rows)


def test_D40b_shadow_path_static_no_actuation_or_effects():
    """D-40(b): static AST scan of the shadow path — no actuation `execute`, no
    os.system/subprocess/socket/urllib. The only `execute` calls are SQLite on a
    connection (must be `conn`/`cursor`), never an actuator."""
    forbidden_modules = {"subprocess", "socket", "urllib", "requests", "shutil", "ctypes"}
    db_receivers = {"conn", "cursor", "cur", "connection", "c"}
    offenders = []
    for path in sorted((REPO_ROOT / "packages" / "supervisor").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = getattr(node, "module", None) or ""
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else []
                root = mod.split(".")[0] if mod else ""
                if root in forbidden_modules or any(n.split(".")[0] in forbidden_modules for n in names):
                    offenders.append(f"{path.name}: import {mod or names}")
            if isinstance(node, ast.Attribute) and node.attr == "execute":
                recv = node.value
                recv_name = recv.id if isinstance(recv, ast.Name) else (
                    recv.attr if isinstance(recv, ast.Attribute) else "?"
                )
                if recv_name not in db_receivers:
                    offenders.append(f"{path.name}: .execute on {recv_name!r}")
            if isinstance(node, ast.Attribute) and node.attr in ("system", "popen", "execv", "spawn"):
                offenders.append(f"{path.name}: .{node.attr}")
    assert offenders == [], offenders
    # the Sentinel's only dispatch call is the seam attribute (never `execute`)
    src = (REPO_ROOT / "packages" / "supervisor" / "sentinel.py").read_text(encoding="utf-8")
    assert ".execute(" not in src


def test_D40c_end_to_end_every_record_inert_and_chains_intact(live):
    """D-40(c): end-to-end over the real server — every acted=False, both chains
    intact."""
    _client, store, _e, _s, _c = live
    rows = _shadow_rows(store, limit=1000)
    assert rows, "vacuous: no shadow rows"
    assert all(r["acted"] == 0 for r in rows)
    assert all(r["actuation_reason"] == "shadow_no_actuation" for r in rows)
    ok_a, _ = store.verify_audit()
    assert ok_a is True
    ok_s, _ = _shadow_chain(store)
    assert ok_s is True


def test_D40d_persisted_acted_count_zero(live):
    """D-40(d): `SELECT COUNT(*) FROM directives WHERE acted=1` == 0 AND
    `COUNT(*) FROM shadow_decisions WHERE acted=1` == 0."""
    _client, store, _e, _s, _c = live
    assert _db(store, "SELECT COUNT(*) FROM directives WHERE acted=1")[0][0] == 0
    assert _db(store, "SELECT COUNT(*) FROM shadow_decisions WHERE acted=1")[0][0] == 0


def test_D40_shadow_non_actuation_is_non_vacuous(live):
    """The load-bearing falsifier: counts AND rows, never rows alone.

    ``attempts > 0`` is what makes the "every recorded row is inert" check
    non-vacuous; without it an EMPTY shadow plane passes (the #54 failure mode).
    """
    _client, store, _e, sentinel, _c = live
    assert sentinel.persistence_attempts > 0, "no persistence attempts -> check would be vacuous"
    assert sentinel.persistence_failures == 0
    assert sentinel.persistence_durable_writes == sentinel.persistence_attempts
    (n,) = _db(store, "SELECT COUNT(*) FROM shadow_decisions")[0]
    assert n == sentinel.persistence_attempts, (n, sentinel.persistence_attempts)
    rows = _shadow_rows(store, limit=1000)
    assert len(rows) == n and all(r["acted"] == 0 for r in rows)


def test_D40e_negative_control_swapped_sentinel_port_can_act_but_engine_cannot(tmp_path, monkeypatch):
    """D-40(e): the falsifier CAN fail — a swapped SENTINEL port actuates (so the
    invariant is not vacuous), while the ENGINE site can never actuate."""
    store = StateStore(str(tmp_path / "jcc.db"))

    class _ActuatingPort:
        def dispatch(self, directive):
            return {"acted": True, "executable": True,
                    "reason": "WOULD_ACTUATE", "directive_id": directive.directive_id}

    from jcc_core.decision import ThresholdsConfig
    from jcc_core.schemas import AgentName, Assessment

    sentinel = Sentinel(
        store, signer=DirectiveSigner(None), budget=BudgetGovernor(BudgetLimits(), store=None),
        dispatch_port=_ActuatingPort(), thresholds=ThresholdsConfig(),
        policy_version="p3.0.0", clock=lambda: 1.0, nonce_factory=lambda: "n",
    )
    assess = Assessment(agent=AgentName.CLAUDE, session_id="s", timestamp=1.0,
                        probabilities={"risk": 0.05}, risk_score=0.05, band="low",
                        provider_verified=True,
                        raw={"signals": {"meaningful_progress": True, "worker_stuck": False,
                                         "requirements_satisfied": True, "needs_verification": False,
                                         "ready_to_finish": True, "needs_human": False},
                             "evidence_complete": True})
    d = sentinel.arbitrate_and_record(assessment=assess, event_id="e1", agent="claude")
    assert d.acted is True, "negative control failed: the swapped port could not actuate"
    (stored,) = _shadow_rows(store, limit=5)
    assert stored["acted"] == 1, "the actuation did not reach the tape -> check is vacuous"

    # the ENGINE site can never actuate: core.py uses dispatch(), never execute().
    core_src = (REPO_ROOT / "packages" / "jcc_core" / "core.py").read_text(encoding="utf-8")
    assert "self.actuator.dispatch(" in core_src
    assert "self.actuator.execute(" not in core_src


# =========================================================================== #
# engine inertness / the two declared reasons / Actuator.execute
# =========================================================================== #
def test_engine_ingest_is_inert_shadow_mode(tmp_path, monkeypatch):
    """The engine ingest path is permanently inert (D-27): acted False + reason
    `shadow_mode` — pinned so the reason string cannot drift silently."""
    client, _store, _e, _s, _c = _build(tmp_path, monkeypatch)
    r = client.post("/api/assess", json=_env(0), headers={"X-JCC-Token": TOKEN})
    assert r.status_code == 200, r.text[:200]
    payload = r.json()
    assert payload["acted"] is False
    assert payload["actuation_reason"] == "shadow_mode", payload.get("actuation_reason")


def test_two_reasons_are_distinct_declared_values():
    """Errata E-1 / D-41: two distinct PORT TYPES carry two distinct reason
    strings. Asserted distinct on purpose — this is a DECLARED divergence, NOT a
    defect. Do not 'unify' them."""
    engine_reason = Actuator(enabled=False).dispatch(
        Directive(directive_id="d", session_id="s", timestamp=0.0, action=DirectiveAction.CONTINUE)
    )["reason"]
    sentinel_reason = ShadowActuationPort().dispatch(
        Directive(directive_id="d", session_id="s", timestamp=0.0, action=DirectiveAction.CONTINUE)
    )["reason"]
    assert engine_reason == "shadow_mode"
    assert sentinel_reason == "shadow_no_actuation"
    assert engine_reason != sentinel_reason
    assert engine_reason != "actuation_disabled" and sentinel_reason != "actuation_disabled"


def test_actuator_execute_is_inert_with_no_side_effect():
    """`Actuator.execute()` returns `actuation_disabled` and performs no side
    effect. `allowed_actions` cannot enable a real effect (P3+ is absent)."""
    a = Actuator(enabled=False, allowed_actions=[])
    d = Directive(directive_id="d", session_id="s", timestamp=0.0,
                  action=DirectiveAction.ESCALATE)
    res = a.execute(d)
    assert res["acted"] is False and res["reason"] == "actuation_disabled"
    # FORBIDDEN_EFFECTS are the exhaustive side-effect set: none is reachable
    assert set(FORBIDDEN_EFFECTS) == {
        "shell_exec", "browser_open", "file_delete", "external_send",
        "git_push", "prod_db_write", "network_bind",
    }


# =========================================================================== #
# D-26 — server boundary
# =========================================================================== #
def test_D26_malformed_envelope_is_422_plus_audit_never_500(tmp_path, monkeypatch):
    """D-26: a malformed body returns structured 422 + writes an audit row; never
    500; never a synthesized directive/ESCALATE."""
    client, store, _e, _s, _c = _build(tmp_path, monkeypatch)
    before_audit = len(store.get_audit())
    before_dirs = len(store.get_directives())

    r = client.post("/api/assess", json={"session_id": "s1"}, headers={"X-JCC-Token": TOKEN})
    assert r.status_code == 422, (r.status_code, r.text[:200])
    assert r.status_code != 500

    after_audit = store.get_audit()
    assert len(after_audit) - before_audit == 1, "the rejection must still be recorded"
    row = after_audit[0] if after_audit else {}
    assert row.get("assessment") == "rejected:EXACT_BLOCKER", row
    assert row.get("directive") == "NONE:rejected", row
    # NEVER a synthesized directive / ESCALATE from a malformed envelope
    assert len(store.get_directives()) == before_dirs, "malformed body synthesized a directive"

    ok, _n = store.verify_audit()
    assert ok is True


# =========================================================================== #
# spec §2.6 — the store surface the shadow plane needs (E-5)
# =========================================================================== #
def test_spec_2_6_store_exposes_lock_scoped_transaction(tmp_path):
    """Spec §2.6 item 1 / Errata E-5: `StateStore.transaction()` is a lock-scoped
    context manager that yields a connection and commits on clean exit (this is
    the surface that closes the non-atomic-append chain-fork hazard)."""
    store = StateStore(str(tmp_path / "jcc.db"))
    assert hasattr(store, "transaction"), "StateStore.transaction() missing (spec §2.6/E-5)"
    with store.transaction() as conn:
        assert hasattr(conn, "execute")
        conn.execute("CREATE TABLE IF NOT EXISTS _gate_probe (x INTEGER)")
        conn.execute("INSERT INTO _gate_probe (x) VALUES (1)")
        conn.execute("INSERT INTO _gate_probe (x) VALUES (2)")
    with store.connection() as conn:
        (n,) = conn.execute("SELECT COUNT(*) FROM _gate_probe").fetchone()
    assert n == 2, "transaction() did not commit on clean exit"


# =========================================================================== #
# budget — the four frozen D-34 tests + a liveness falsifier
# =========================================================================== #
def _persist(store, session_id, *, created_at=None, assessments=0, events=0,
             directives=0, retries=0, escalations=0, directive_ts=None):
    ts = directive_ts if directive_ts is not None else 1.0
    with store.connection() as conn:
        if created_at is not None:
            conn.execute(
                "INSERT OR REPLACE INTO sessions (session_id, agent, project, cwd, status, "
                "created_at, updated_at, last_event_id, event_count, evidence_count) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (session_id, "generic", "p", None, "active", created_at, created_at, None, 0, 0),
            )
        for i in range(assessments):
            conn.execute(
                "INSERT INTO assessments (session_id, timestamp, agent, model, provider, data) "
                "VALUES (?,?,?,?,?,?)", (session_id, 1.0, "generic", "m", "mock", "{}"))
        for i in range(events):
            conn.execute(
                "INSERT INTO events (event_id, session_id, delivery_id, agent, project, "
                "timestamp, event_type, payload, evidence) VALUES (?,?,?,?,?,?,?,?,?)",
                (f"e{i}", session_id, f"d{i}", "generic", "p", 1.0, "tool_call", "{}", None))
        def _dir(prefix, action):
            conn.execute(
                "INSERT INTO directives (directive_id, session_id, timestamp, action, "
                "target_actuator, params, policy_version, signed, signature, evidence_hash, "
                "acted, actuation_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"{prefix}", session_id, ts, action, "noop", "{}", "p1.0.0", 0, None, None, 0,
                 "shadow_mode"))
        for i in range(directives):
            _dir(f"dir{i}", "CONTINUE")
        for i in range(retries):
            _dir(f"rt{i}", "RETRY")
        for i in range(escalations):
            _dir(f"esc{i}", "ESCALATE")
        conn.commit()


#: Dimensions that MUST be exhaustible through the REAL store. Each case is
#: (expected ``budget_kind``, kwargs for ``_persist``, ``BudgetLimits`` overrides).
#: NOTE: ``scorer_tokens`` is deliberately NOT here — with no persisted column it
#: is INERT BY DESIGN (lead ruling: WAIVED). The assertion is NOT deleted; it is
#: asserted explicitly in ``test_budget_scorer_tokens_is_inert_by_design``, which
#: additionally FAILS if a token column is later added without wiring the governor.
def _matrix_cases():
    return [
        (CODE_ASSESS_CALLS, dict(assessments=1), dict(assess_calls=0)),
        (CODE_EVENTS, dict(events=1), dict(events=0)),
        (CODE_DIRECTIVES, dict(directives=1), dict(directives=0)),
        (CODE_RETRIES, dict(retries=1), dict(retries=0)),
        (CODE_ESCALATIONS, dict(escalations=1), dict(escalations=0)),
        (CODE_WALL_CLOCK, dict(created_at=time.time() - 100.0), dict(wall_clock_s=1.0)),
        (CODE_COST_UNITS, dict(escalations=1), dict(cost_units=0.0)),
        (CODE_RATE, dict(directives=1, directive_ts=time.time()), dict(directives_per_minute=0)),
    ]


def test_budget_exhaustion_matrix(tmp_path):
    """Frozen §2.4 budget test: wire EACH of the 8 EXHAUSTIBLE dimensions
    individually to the REAL store and assert the correct ``budget_kind``.

    This is the test that catches an inert dimension (it caught
    ``cost_units_used = 0.0``). Rulings applied here:
      * ``cost_units`` STAYS IN THE MATRIX and MUST GO GREEN — it is derivable
        from the persisted ``directives.action`` column via COST_UNITS
        (spec §2.4 cost table, limit 500.0). A hard-coded 0.0 makes the dimension
        permanently fail-open, contradicting D-34/D-5. Owner: forge.
      * ``scorer_tokens`` is REMOVED from this matrix and WAIVED as inert by
        design (no persisted column). It is not dropped — see
        ``test_budget_scorer_tokens_is_inert_by_design``.
    No expected-RED entries remain: everything below must pass.
    """
    results = {}
    for code, persist_kw, limit_kw, in _matrix_cases():
        store = StateStore(str(tmp_path / f"m_{code}.db"))
        _persist(store, "s", **persist_kw)
        gov = BudgetGovernor(BudgetLimits(**limit_kw), store=store, clock=time.time)
        got = gov.check("s", kind="assessment" if code in (CODE_ASSESS_CALLS,) else "directive")
        results[code] = (got.allowed, got.code)

    red = {k: v for k, v in results.items() if v[1] != k}
    assert not red, f"dimensions not exhaustible through the real store: {red}\nall: {results}"


def test_budget_store_derived_dimensions_are_live(tmp_path):
    """Targeted falsifier: every STORE-derived counter the governor reads must be
    non-inert. ``cost_units_used`` is the named one (must rise after a persisted
    ESCALATE directive).

    ``scorer_tokens`` / ``model_units_used`` is deliberately NOT covered here — it
    has no persisted source and is pinned as inert by design in
    ``test_budget_scorer_tokens_is_inert_by_design``.
    """
    store = StateStore(str(tmp_path / "live.db"))
    # _persist SEMANTICS (read before asserting): directives=1 writes one CONTINUE
    # row AND escalations=1 writes one ESCALATE row — and an ESCALATE row IS ALSO
    # a row in the same persisted `directives` table. So the real directive count
    # here is 2, not 1. Asserted exactly (== 2); never weakened to >= 1.
    _persist(store, "s", assessments=1, events=1, directives=1, escalations=1)
    snap = store.budget_snapshot("s")

    assert snap["assessments_used"] == 1
    assert snap["events_used"] == 1
    assert snap["directives_used"] == 2, snap       # 1 CONTINUE + 1 ESCALATE (above)
    assert snap["escalations_used"] == 1
    # cost must be derivable: ESCALATE costs 10 units per the frozen COST table
    assert COST_UNITS[DirectiveAction.ESCALATE] == 10.0
    assert snap["cost_units_used"] >= 10.0, (
        "cost_units_used is INERT (0.0) though it is derivable from COST_UNITS x "
        f"persisted actions -- got {snap['cost_units_used']}"
    )


def _persisted_token_columns(store):
    """Any persisted column that could carry scorer/model units, or [].

    This is the TRIPWIRE helper: it is how we detect the day someone ADDS a token
    column without wiring the governor to it. Name-matched on purpose — a column
    added later almost certainly carries one of these tokens in its name.
    """
    needles = ("token", "tokens", "model_unit", "scorer", "eval_count")
    found = []
    with store.connection() as conn:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        for table in tables:
            cols = [r[1].lower() for r in conn.execute(
                f"PRAGMA table_info({table})").fetchall()]
            found.extend(f"{table}.{c}" for c in cols if any(n in c for n in needles))
    return found


def test_budget_scorer_tokens_is_inert_by_design(tmp_path):
    """WAIVED-BY-DESIGN dimension, PINNED rather than dropped (lead ruling).

    ``scorer_tokens`` observes only ``BudgetSnapshot.model_units_used`` (see
    ``_observed``: it takes no ``units`` term). With no persisted column to derive
    from, a store-wired governor can only ever observe 0.0, so the dimension can
    never trip. The waiver is asserted explicitly so it stays reviewed evidence:
      (i)   the snapshot really reports 0.0 even though the rows carry tokens;
      (ii)  driving 10x the limit through the REAL store still yields NO
            exhaustion for this dimension;
      (iii) no persisted token column exists — the documented reason for the
            waiver.

    TRIPWIRE: the moment a persisted token column appears, branch (iii) stops
    applying and this test then DEMANDS that the governor actually derives from
    it (``> 0.0``). So "add a column without wiring the governor" => FAIL, which
    is exactly the regression the waiver must not hide.
    """
    import json as _json

    store = StateStore(str(tmp_path / "inert_tokens.db"))
    limit = BudgetLimits().scorer_tokens          # frozen default: 200_000
    # 10 rows, each carrying a full limit => 10x the limit, through the REAL store.
    with store.connection() as conn:
        for i in range(10):
            conn.execute(
                "INSERT INTO assessments (session_id, timestamp, agent, model, provider, data) "
                "VALUES (?,?,?,?,?,?)",
                ("s", 1.0, "generic", "m", "mock", _json.dumps({"tokens": limit})),
            )
        conn.commit()

    cols = _persisted_token_columns(store)
    snap = store.budget_snapshot("s")

    if cols:
        # A persisted token column now exists => the waiver is void; the governor
        # MUST be wired to it. Adding the column unwired lands HERE and FAILS.
        assert snap["model_units_used"] > 0.0, (
            f"persisted token column(s) exist ({cols}) but the governor still derives "
            "model_units_used=0.0 -> the column is present and UNWIRED. The "
            "scorer_tokens waiver no longer applies; wire the governor to it."
        )
        return

    # (iii) the documented reason for the waiver: nothing persisted to derive from.
    assert cols == [], cols
    # (i) inert — and provably so, even though every row carries `tokens`.
    assert snap["model_units_used"] == 0.0, snap
    # (ii) 10x the limit in the real store does not trip this dimension.
    gov = BudgetGovernor(BudgetLimits(scorer_tokens=limit), store=store, clock=time.time)
    got = gov.check("s", kind="assessment")
    assert got.snapshot.exhausted_code != CODE_SCORER_TOKENS
    assert got.code is None, (
        f"scorer_tokens tripped with no persisted source — got {got.code!r}; expected "
        "no exhaustion for this dimension while it remains inert by design"
    )


def test_budget_survives_restart(tmp_path):
    """D-34 restart-safety: the counters are DERIVED, so a fresh governor on the
    same DB still reports the exhaustion (no in-memory reset)."""
    path = str(tmp_path / "restart.db")
    store = StateStore(path)
    _persist(store, "s", assessments=1)
    gov1 = BudgetGovernor(BudgetLimits(assess_calls=0), store=store, clock=time.time)
    first = gov1.check("s", kind="assessment")
    assert first.allowed is False and first.code == CODE_ASSESS_CALLS

    restarted_store = StateStore(path)                     # process restart analogue
    gov2 = BudgetGovernor(BudgetLimits(assess_calls=0), store=restarted_store, clock=time.time)
    again = gov2.check("s", kind="assessment")
    assert again.allowed is False and again.code == CODE_ASSESS_CALLS, (
        "budget reset across restart -> counters are NOT derived (D-34 violated)"
    )
    assert again.snapshot.assessments_used == 1


def test_budget_suppression_is_deterministic(tmp_path):
    """Exhaustion REPLACES with ESCALATE (never suppresses, never CONTINUE) and is
    deterministic; the shadow record is still written."""
    store = StateStore(str(tmp_path / "det.db"))
    gov = BudgetGovernor(BudgetLimits(directives=0), store=store, clock=time.time)

    codes = {gov.check("s", kind="directive").code for _ in range(10)}
    assert codes == {CODE_DIRECTIVES}, codes           # deterministic
    denied = gov.check("s", kind="directive")
    assert denied.allowed is False

    from jcc_core.decision import ThresholdsConfig
    from jcc_core.schemas import AgentName, Assessment

    sentinel = Sentinel(
        store, signer=DirectiveSigner(None), budget=BudgetGovernor(
            BudgetLimits(directives=0), store=store, clock=time.time),
        dispatch_port=ShadowActuationPort(), thresholds=ThresholdsConfig(),
        policy_version="p3.0.0", clock=lambda: 1.0, nonce_factory=lambda: "n",
    )
    assess = Assessment(agent=AgentName.WORKBUDDY, session_id="s", timestamp=1.0,
                        probabilities={"risk": 0.05}, risk_score=0.05, band="low",
                        provider_verified=True,
                        raw={"signals": {"meaningful_progress": True, "worker_stuck": False,
                                         "requirements_satisfied": True, "needs_verification": False,
                                         "ready_to_finish": True, "needs_human": False},
                             "evidence_complete": True})
    d = sentinel.arbitrate_and_record(assessment=assess, event_id="e1", agent="workbuddy")
    assert d.action is DirectiveAction.ESCALATE, "exhaustion must REPLACE with ESCALATE"
    assert d.action is not DirectiveAction.CONTINUE
    assert d.blocked is True and d.blocker_code == BlockerCode.BUDGET_EXHAUSTED.value
    assert _shadow_rows(store, limit=5), "the record must STILL be written (never suppressed)"


def test_budget_defaults_match_config_yaml():
    """The shipped `p3.shadow.budget` block must match `BudgetLimits` defaults, and
    the COST table must match the frozen spec §2.4 values."""
    import yaml

    raw = yaml.safe_load(SHIPPED_CONFIG.read_text(encoding="utf-8"))
    budget = raw["p3"]["shadow"]["budget"]
    assert set(budget) == set(BudgetLimits.__dataclass_fields__), (
        set(budget) ^ set(BudgetLimits.__dataclass_fields__)
    )
    assert BudgetLimits(**budget) == BudgetLimits(), (
        "config/jcc.yaml budget differs from BudgetLimits defaults"
    )
    assert {a.value: c for a, c in COST_UNITS.items()} == {
        "CONTINUE": 0.0, "VERIFY": 1.0, "FINISH": 1.0, "STEER": 2.0,
        "RETRY": 3.0, "PAUSE": 5.0, "ESCALATE": 10.0,
    }


# =========================================================================== #
# R6-1 guard — engine == shadow on the SHIPPED config (evidence-present corpus)
# =========================================================================== #
def test_R6_1_engine_equals_shadow_on_shipped_config():
    """R6-1: `policy.decide` and `decision.decide_action` agree over a
    evidence-present corpus (engine == shadow by construction)."""
    from jcc_core.providers.mock import DeterministicMockProvider

    provider = DeterministicMockProvider()
    for i in range(400):
        env = AEPEnvelope(event_id=f"g{i}", delivery_id=f"gd{i}", session_id="s",
                          agent="workbuddy", project="p", event_type="tool_call",
                          evidence={"ok": True})
        a = provider.assess(env, None)
        assert policy.decide(a) is decision.decide_action(a), i


def test_R6_1_single_resolver_under_per_agent_override(tmp_path, monkeypatch):
    """R6-1 / F-p3-4 — ONE threshold resolver: the engine and the shadow plane must
    resolve the SAME configured surface, observed on what each PERSISTED.

    Drives the REAL composition root (``run._wire_shadow``) with
    ``per_agent.workbuddy.steer_risk = 0.6`` and ingests a workbuddy event at risk
    0.5: under the defaults that is STEER, under the override it is CONTINUE.
    Pre-fix the engine resolved the kernel's module-private defaults while only the
    Sentinel received the configured surface, so ``directives.action`` (the policy
    that executed) diverged from ``shadow_decisions.action`` (the label's
    description) — every shadow label described a policy that never ran.

    NOTE (harness fix): this replaces an earlier form that called the bare
    module-level ``policy.decide(a)``. That call can never observe an ENGINE-injected
    surface (``core.py`` now passes ``thresholds=self.thresholds``), so it was RED
    for a reason unrelated to F-p3-4 and no implementation could have satisfied it.
    The assertion is kept, not deleted — it now exercises the real path.
    """
    from jcc_core.decision import ThresholdsConfig
    from jcc_core.run import _wire_shadow
    from jcc_core.schemas import AgentName, Assessment

    override = {"default": {}, "per_agent": {"workbuddy": {"steer_risk": 0.6}}}
    configured = ThresholdsConfig.from_config(override)

    def _assess(risk: float) -> Assessment:
        return Assessment(
            agent=AgentName.WORKBUDDY, session_id="s-r6", timestamp=1.0,
            probabilities={"risk": risk}, risk_score=risk, band="medium",
            provider_verified=True,
            raw={"signals": {"meaningful_progress": True, "worker_stuck": False,
                             "requirements_satisfied": True, "needs_verification": False,
                             "ready_to_finish": True, "needs_human": False},
                 "evidence_complete": True},
        )

    class _FixedRisk:
        name = "fixed-risk"

        def __init__(self, risk: float):
            self.risk = risk

        def assess(self, event, session):
            return _assess(self.risk)

    # Non-vacuity of the override, on the very assessment the engine will see:
    # risk 0.5 is STEER under the defaults and CONTINUE under the override.
    probe = _assess(0.5)
    assert decision.decide_action(probe, ThresholdsConfig().for_agent("workbuddy")) \
        is DirectiveAction.STEER
    assert decision.decide_action(probe, configured.for_agent("workbuddy")) \
        is DirectiveAction.CONTINUE

    monkeypatch.setenv("JCC_CONTROL_TOKEN", TOKEN)
    cfg = load_config(str(SHIPPED_CONFIG))
    cfg.state_store_db_path = str(tmp_path / "jcc.db")
    sh = cfg.raw["p3"]["shadow"]
    sh["enabled"] = True
    sh["thresholds"] = override

    store = StateStore(cfg.state_store_db_path)
    engine = CoreEngine(store, _FixedRisk(0.5), Actuator(enabled=False), cfg)
    _wire_shadow(engine, store, cfg)

    # ONE resolver: the SAME object on both planes, not merely equal values.
    assert engine.thresholds is _sentinel_of(engine).thresholds, (
        "engine and Sentinel hold DIFFERENT ThresholdsConfig objects -> more than "
        "one threshold resolver (R6-1 / F-p3-4)"
    )
    assert engine.thresholds.config_hash() == configured.config_hash()

    engine.ingest(AEPEnvelope(event_id="r6", delivery_id="rd6", session_id="s-r6",
                              agent="workbuddy", project="p", event_type="tool_call",
                              payload={"i": 0}, evidence={"ok": True}))

    with store.connection() as conn:
        engine_action = conn.execute(
            "SELECT action FROM directives WHERE session_id='s-r6' "
            "ORDER BY rowid DESC LIMIT 1").fetchone()[0]
        row = conn.execute(
            "SELECT action, policy_config_hash FROM shadow_decisions "
            "WHERE session_id='s-r6' ORDER BY seq DESC LIMIT 1").fetchone()
    assert row is not None, "shadow plane recorded nothing (non-vacuity)"
    shadow_action, shadow_hash = row

    assert engine_action == shadow_action == DirectiveAction.CONTINUE.value, (
        f"planes diverged: engine={engine_action!r} shadow={shadow_action!r} -> the "
        "two planes used DIFFERENT threshold surfaces (R6-1 / F-p3-4)"
    )
    assert engine_action != DirectiveAction.STEER.value, "engine ignored the override"
    assert shadow_hash == configured.config_hash()


def _shadow_chain(store):
    with store.connection() as conn:
        shadow_store.ensure_schema(conn)
        return shadow_store.verify_shadow_chain(conn)


if __name__ == "__main__":  # convenience matrix runner
    raise SystemExit(pytest.main([__file__, "-v", "-p", "no:cacheprovider"]))
