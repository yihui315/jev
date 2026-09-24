"""P3 core integration test — the D-26 ingest-boundary matrix (ADR-0008 D-26,
extended to the server boundary in Round 5).

Why this file exists
--------------------
The ``POST /api/assess`` boundary previously had **no** test coverage for a
malformed body: ``AEPEnvelope(**body)`` raised an unhandled ``ValidationError``
-> HTTP 500 with **no audit row** (a hole in the authoritative tape). Round 5
extended D-26 to this boundary: a malformed/unauthorised envelope must NOT 500
silently; it returns a structured 4xx that still writes its audit row.

This is the regression test for that path. It enumerates the **full
token x body-validity matrix** and states the audit-row expectation for **every**
cell explicitly (not left implied):

    cell                                | status | audit row written
    ------------------------------------|--------|------------------
    valid   token  + valid     body     |  200   | YES (engine.ingest)
    valid   token  + malformed body     |  422   | YES (boundary records the rejection)
    no/inv. token  + valid     body     |  403   | NO  (rejected before the handler)
    no/inv. token  + malformed body     |  403   | NO  (rejected before the handler)

The chain must verify intact after every cell.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jcc_core import decision, policy
from jcc_core.actuator import Actuator
from jcc_core.config import ActuationConfig, Config, PolicyConfig, ServerConfig, load_config
from jcc_core.core import CoreEngine
from jcc_core.providers import build_provider
from jcc_core.providers.base import JevProvider
from jcc_core.providers.mock import DeterministicMockProvider
from jcc_core.schemas import AEPEnvelope, Assessment, DirectiveAction, SessionState
from jcc_core.server import create_app
from jcc_core.state_store import StateStore

TOKEN = "p3-core-integration-token"

VALID_BODY = {
    "event_id": "e1",
    "delivery_id": "d1",
    "session_id": "s1",
    "agent": "claude",
    "project": "p",
    "event_type": "tool_call",
    "payload": {},
}

# Missing the required AEP fields (event_id/delivery_id/agent) -> ValidationError.
MALFORMED_BODY = {"session_id": "s1"}

# (id, headers, body, expected_status, expect_audit_row)
CASES = [
    ("valid-token+valid-body", {"X-JCC-Token": TOKEN}, VALID_BODY, 200, True),
    ("valid-token+malformed-body", {"X-JCC-Token": TOKEN}, MALFORMED_BODY, 422, True),
    ("no-token+valid-body", {}, VALID_BODY, 403, False),
    ("no-token+malformed-body", {}, MALFORMED_BODY, 403, False),
    ("bad-token+valid-body", {"X-JCC-Token": "wrong"}, VALID_BODY, 403, False),
    ("bad-token+malformed-body", {"X-JCC-Token": "wrong"}, MALFORMED_BODY, 403, False),
]


@pytest.fixture()
def client_and_store(tmp_path, monkeypatch):
    monkeypatch.setenv("JCC_CONTROL_TOKEN", TOKEN)
    db_path = str(tmp_path / "jcc.db")
    cfg = Config(
        server=ServerConfig(),
        actuation=ActuationConfig(),
        policy=PolicyConfig(),
        state_store_db_path=db_path,
    )
    store = StateStore(db_path)
    engine = CoreEngine(store, build_provider(cfg), Actuator(enabled=False), cfg)
    return TestClient(create_app(engine, cfg)), store


@pytest.mark.parametrize(
    "case_id,headers,body,expected_status,expect_audit_row",
    CASES,
    ids=[case[0] for case in CASES],
)
def test_assess_boundary_matrix(
    client_and_store, case_id, headers, body, expected_status, expect_audit_row
):
    client, store = client_and_store
    before = len(store.get_audit())

    r = client.post("/api/assess", json=body, headers=headers)

    wrote = len(store.get_audit()) - before

    # 1) exact HTTP status for the cell
    assert r.status_code == expected_status, (case_id, r.status_code, r.text[:200])
    # 2) never a silent 500 (D-26 extended)
    assert r.status_code != 500, (case_id, "unhandled server error")

    # 3) the audit-row expectation for THIS cell
    if expect_audit_row:
        assert wrote == 1, (case_id, "record must never be dropped", wrote)
    else:
        assert wrote == 0, (case_id, "rejected before the handler; no record", wrote)

    # 4) the hash chain stays intact after every cell
    ok, _count = store.verify_audit()
    assert ok is True, (case_id, "audit chain broken")


def test_malformed_rejection_is_recorded_as_exact_blocker(client_and_store):
    """The rejection is auditable: it is labelled, not silently dropped."""
    client, store = client_and_store
    r = client.post("/api/assess", json=MALFORMED_BODY, headers={"X-JCC-Token": TOKEN})
    assert r.status_code == 422
    row = store.get_audit()[-1]
    assert row.get("assessment") == "rejected:EXACT_BLOCKER", row
    assert row.get("directive") == "NONE:rejected", row


def test_valid_ingest_reports_inert_engine_reason(client_and_store):
    """The engine ingest path is permanently inert (D-27): acted=False,
    reason=shadow_mode -- asserted here so the reason string cannot drift silently."""
    client, store = client_and_store
    r = client.post("/api/assess", json=VALID_BODY, headers={"X-JCC-Token": TOKEN})
    assert r.status_code == 200, r.text[:200]
    payload = r.json()
    assert payload.get("acted") is False
    assert payload.get("actuation_reason") == "shadow_mode", payload.get("actuation_reason")


# --------------------------------------------------------------------------- #
# D-32 / R6-6 — characterization: engine-path ``policy.decide`` vs P2
# --------------------------------------------------------------------------- #
#: FROZEN P2 reference mapping, transcribed from the pre-D-32 code
#: (``providers/mock.py:55-69`` setting the flags + ``policy.py:30-49`` walking
#: them in priority order). These literals are the POINT of a characterization
#: test: they pin the P1/P2 mapping the live engine must keep.
_P2_CUTS = (0.8, 0.6, 0.4)   # escalate / pause / steer (mock.py:62-67)
_P2_FINISH_CUT = 0.1         # mock.py:68-69


def _p2_reference(risk: float, evidence_present: bool) -> DirectiveAction:
    """What P2's (mock + flag-walking policy) produced for this input."""
    if not evidence_present:
        return DirectiveAction.VERIFY            # mock.py:55-60 -> policy.py:33
    if risk >= _P2_CUTS[0]:
        return DirectiveAction.ESCALATE
    if risk >= _P2_CUTS[1]:
        return DirectiveAction.PAUSE
    if risk >= _P2_CUTS[2]:
        return DirectiveAction.STEER
    if risk < _P2_FINISH_CUT:
        return DirectiveAction.FINISH
    return DirectiveAction.CONTINUE              # policy.py:49 floor


def _cell(i: int, *, evidence: bool) -> AEPEnvelope:
    return AEPEnvelope(
        event_id=f"cell{i}",
        delivery_id=f"d{i}",
        session_id="s1",
        agent="workbuddy",
        project="p",
        event_type="tool_call",
        evidence=({"ok": True} if evidence else None),
    )


def test_engine_path_is_identical_to_p2_on_every_evidence_fixture():
    """ASSERTED EQUALITY (R6-6): zero divergent cells with evidence present.

    Also pins R6-1: ``policy.decide`` IS ``decision.decide_action`` (no second
    authority), so this characterization cannot be satisfied by a special case.
    """
    provider = DeterministicMockProvider()
    divergent = []
    n = 1500
    for i in range(n):
        env = _cell(i, evidence=True)
        assess = provider.assess(env, None)
        risk = assess.risk_score
        got = policy.decide(assess)
        ref = _p2_reference(risk, True)

        assert got is decision.decide_action(assess), "wrapper is not the kernel"
        if got is not ref:
            divergent.append((i, risk, ref.value, got.value))

    assert divergent == [], f"{len(divergent)}/{n} cells diverged: {divergent[:10]}"
    # sanity: the corpus really spans all four bands, else the test is vacuous
    bands = {provider.assess(_cell(i, evidence=True), None).band for i in range(n)}
    assert bands == {"low", "medium", "high", "critical"}, bands


def test_engine_path_missing_evidence_corner_is_measured_not_hidden():
    """The ONE known P2 divergence, measured explicitly (ADR-0008 D-35 vs D-32).

    P2 returned VERIFY for *every* missing-evidence event (it short-circuited
    before reading the risk). The kernel cannot: rows 1-4 are evaluated before
    the VERIFY row, so a missing-evidence event whose risk reaches the pause
    cut is absorbed by PAUSE/ESCALATE first.

    Below the pause cut the two agree exactly; at/above it they diverge and this
    test PINS the divergence instead of hiding it (the band must stay definite —
    a `band="unknown"` would escalate at every risk level, Add B).
    """
    provider = DeterministicMockProvider()
    agree, diverge = 0, []
    n = 1500
    for i in range(n):
        env = _cell(i, evidence=False)
        assess = provider.assess(env, None)
        risk = assess.risk_score

        # preconditions: the provider contract holds on the missing-evidence path
        assert assess.band in ("low", "medium", "high", "critical"), assess.band
        assert assess.raw["signals"]["needs_verification"] is True
        assert assess.raw["evidence_complete"] is False

        got = policy.decide(assess)
        ref = _p2_reference(risk, False)          # always VERIFY

        if got is ref:
            agree += 1
            assert got is DirectiveAction.VERIFY
        else:
            # only the documented corner: risk at/above the pause cut
            assert risk >= _P2_CUTS[1], (i, risk, got.value)
            assert got in (DirectiveAction.PAUSE, DirectiveAction.ESCALATE)
            diverge.append((i, risk, ref.value, got.value))

    # both sides must be exercised, or the measurement is vacuous
    assert agree > 0, "no missing-evidence cell reached VERIFY"
    assert diverge, "expected the documented high-risk missing-evidence corner"
    # every divergent cell is at/above the pause cut, and none is a resuming action
    assert all(r >= _P2_CUTS[1] for _, r, _, _ in diverge)
    assert {got for *_, got in diverge} <= {"PAUSE", "ESCALATE"}


# --------------------------------------------------------------------------- #
# ADR-0008 F1/F2 — the PRODUCTION composition root must actually turn P3 ON
# --------------------------------------------------------------------------- #
REPO_ROOT = Path(__file__).resolve().parents[3]


def _engine_with_shadow_wiring(tmp_path, *, enabled: bool):
    """Build the real engine through the real composition root.

    Uses the SHIPPED `config/jcc.yaml` (so the live `p3.shadow` block is what is
    exercised) with only `enabled` toggled and the DB redirected to `tmp_path`.
    """
    from jcc_core.run import _wire_shadow  # local: importing run.py configures logging

    cfg = load_config(str(REPO_ROOT / "config" / "jcc.yaml"))
    cfg.state_store_db_path = str(tmp_path / "jcc.db")
    cfg.raw["p3"]["shadow"]["enabled"] = enabled
    store = StateStore(cfg.state_store_db_path)
    engine = CoreEngine(store, build_provider(cfg), Actuator(enabled=False), cfg)
    _wire_shadow(engine, store, cfg)
    return store, engine


def test_wire_shadow_persists_against_the_real_state_store(tmp_path):
    """F1: `run._wire_shadow` had NEVER run in any test — every config ships
    `p3.shadow.enabled: false` — so its contract with `packages/supervisor` was
    never exercised. A shadow row MUST land.

    Non-vacuity is the whole point: without this, D-40's "every recorded
    `acted == false`" passes over an EMPTY table, so a silently broken shadow
    plane looks healthy.
    """
    store, engine = _engine_with_shadow_wiring(tmp_path, enabled=True)
    assert len(engine._subscribers) == 1, "Sentinel was not subscribed"

    out = engine.ingest(
        AEPEnvelope(event_id="w1", delivery_id="wd1", session_id="s-wire",
                    agent="workbuddy", project="p", event_type="tool_call",
                    evidence={"ok": True})
    )
    assert out["acted"] is False
    assert out["actuation_reason"] == "shadow_mode"

    # second ingest -> a SECOND row, so the hash CHAIN has a real link to check
    engine.ingest(
        AEPEnvelope(event_id="w2", delivery_id="wd2", session_id="s-wire",
                    agent="workbuddy", project="p", event_type="tool_call",
                    evidence={"ok": True})
    )

    # read with the PUBLIC accessor (the F1 contract), not a private reach-in
    with store.connection() as conn:
        rows = conn.execute(
            "SELECT seq, action, acted, prev_hash, entry_hash "
            "FROM shadow_decisions ORDER BY seq"
        ).fetchall()
    assert len(rows) == 2, (
        "shadow plane recorded NOTHING (or the wrong count) against the real "
        f"StateStore — got {len(rows)} rows"
    )
    (_s0, _a0, acted0, prev0, entry0), (_s1, _a1, acted1, prev1, entry1) = rows
    # every recorded row is inert in P3 (D-27/D-41 shadow_no_actuation)
    assert acted0 == 0 and acted1 == 0
    # a GENUINE linked hash chain: row0 starts from GENESIS, row1 links to row0
    assert prev0 == "GENESIS", f"chain must start at GENESIS, got {prev0!r}"
    assert prev1 == entry0, "row1.prev_hash must link to row0.entry_hash"
    assert entry0 and entry1 and entry0 != entry1, "entry hashes must be non-null & distinct"
    # persistence-failure count == 0, measured (2 ingests -> 2 persisted rows)
    assert len(rows) == 2, "any persisted/ingest mismatch is a persistence FAILURE"
    assert out["directive"]["action"]  # a directive was produced

    ok, _count = store.verify_audit()
    assert ok is True, "the engine audit chain must stay intact with the shadow plane on"

    # F2: the governor must read DERIVED counters from the real store
    sentinel = engine._subscribers[-1].__self__
    assert sentinel.budget.store is store, "budget must be store-backed (D-34)"
    # White-box ON PURPOSE: the clock DOMAIN is the defect class — a monotonic
    # clock against wall-clock `first_seen` makes the wall-clock cut never fire.
    assert sentinel.budget._clock is time.time
    # counters are DERIVED from persisted P1/P2 rows: 2 ingests -> 2 assessments,
    # 2 directives; the governor reads THIS, not an in-memory ledger (D-34).
    snap = store.budget_snapshot("s-wire")
    assert snap["assessments_used"] == 2
    assert snap["directives_used"] == 2
    assert snap["model_units_used"] == 0.0  # not faked; no persisted column


def test_wire_shadow_disabled_subscribes_nothing(tmp_path):
    """Negative control: the flag must actually gate, else the test above proves
    nothing about the wiring."""
    store, engine = _engine_with_shadow_wiring(tmp_path, enabled=False)
    assert engine._subscribers == []

    engine.ingest(
        AEPEnvelope(event_id="n1", delivery_id="nd1", session_id="s-off",
                    agent="workbuddy", project="p", event_type="tool_call")
    )
    with store.connection() as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "shadow_decisions" not in tables, "disabled shadow plane must not persist"


# --------------------------------------------------------------------------- #
# R6-1 / F-p3-4 — ONE threshold resolver across BOTH planes
# --------------------------------------------------------------------------- #
def _fixed_assessment(risk: float, agent: str = "workbuddy",
                      session_id: str = "s-r6") -> Assessment:
    """An assessment pinned at one risk score, isolating the threshold surface."""
    return Assessment(
        agent=agent, session_id=session_id, timestamp=time.time(),
        model="fixed", provider="fixed-risk",
        probabilities={"risk": risk}, risk_score=risk, band="medium",
        provider_verified=True,
        raw={"signals": {"meaningful_progress": True, "worker_stuck": False,
                         "requirements_satisfied": True, "needs_verification": False,
                         "ready_to_finish": True, "needs_human": False},
             "evidence_complete": True},
    )


class _FixedRiskProvider(JevProvider):
    """Deterministic provider at one risk score (no SHA-seeded guessing)."""

    name = "fixed-risk"

    def __init__(self, risk: float):
        self.risk = risk

    def assess(self, event: AEPEnvelope, session: SessionState) -> Assessment:
        return _fixed_assessment(self.risk, agent=str(event.agent.value
                                                      if hasattr(event.agent, "value")
                                                      else event.agent),
                                 session_id=event.session_id)


def test_R6_1_engine_and_shadow_resolve_the_SAME_threshold_surface(tmp_path):
    """F-p3-4: a non-empty ``per_agent`` override must move BOTH planes together.

    Under the SHIPPED config every ``per_agent`` entry is empty, so both planes
    resolve identical values *by accident* — the defect is invisible. Pre-fix,
    ``run._wire_shadow`` injected the configured surface into the Sentinel ONLY,
    while ``core.py``'s ``policy.decide`` fell back to the kernel's module-private
    defaults; with a real override the engine executed one action and the shadow
    plane recorded another, so every label described a policy that never ran.

    This test drives the PRODUCTION composition root and reads what each plane
    actually PERSISTED (not a re-derivation), which is the only way to observe
    divergence:

      * engine -> ``directives.action``        (the policy that executed)
      * shadow -> ``shadow_decisions.action``  (the label's description of it)
    """
    from jcc_core.decision import ThresholdsConfig
    from jcc_core.run import _wire_shadow
    from supervisor.sentinel import Sentinel

    cfg = load_config(str(REPO_ROOT / "config" / "jcc.yaml"))
    cfg.state_store_db_path = str(tmp_path / "jcc.db")
    sh = cfg.raw["p3"]["shadow"]
    sh["enabled"] = True
    # At risk 0.5 this override MOVES the kernel's answer: default steer_risk 0.4
    # yields STEER, the override's 0.6 yields CONTINUE.
    sh["thresholds"] = {"default": {}, "per_agent": {"workbuddy": {"steer_risk": 0.6}}}
    configured = ThresholdsConfig.from_config(sh["thresholds"])

    store = StateStore(cfg.state_store_db_path)
    engine = CoreEngine(store, _FixedRiskProvider(0.5), Actuator(enabled=False), cfg)
    _wire_shadow(engine, store, cfg)

    sentinel = next(cb.__self__ for cb in engine._subscribers
                    if isinstance(getattr(cb, "__self__", None), Sentinel))

    # ONE resolver: identical object, not merely equal values
    assert engine.thresholds is sentinel.thresholds, (
        "the engine and the Sentinel hold DIFFERENT ThresholdsConfig objects — "
        "there is more than one threshold resolver (R6-1 / F-p3-4)"
    )
    assert engine.thresholds.config_hash() == configured.config_hash()

    # non-vacuity of the override, asserted on the SAME assessment the engine sees
    probe = _fixed_assessment(0.5)
    assert decision.decide_action(probe, ThresholdsConfig().for_agent("workbuddy")) \
        is DirectiveAction.STEER
    assert decision.decide_action(probe, configured.for_agent("workbuddy")) \
        is DirectiveAction.CONTINUE

    engine.ingest(AEPEnvelope(event_id="r1", delivery_id="rd1", session_id="s-r6",
                              agent="workbuddy", project="p", event_type="tool_call",
                              evidence={"ok": True}))

    with store.connection() as conn:
        engine_action = conn.execute(
            "SELECT action FROM directives WHERE session_id='s-r6' ORDER BY rowid DESC LIMIT 1"
        ).fetchone()[0]
        shadow_action, shadow_hash = conn.execute(
            "SELECT action, policy_config_hash FROM shadow_decisions "
            "WHERE session_id='s-r6' ORDER BY seq DESC LIMIT 1"
        ).fetchone()

    assert engine_action == shadow_action == "CONTINUE", (
        f"planes diverged: engine={engine_action!r} shadow={shadow_action!r} "
        "(a divergent pair means the two planes used different threshold surfaces)"
    )
    # THE falsifier: pre-fix the engine ignored the override and stayed on the
    # module defaults, i.e. STEER.
    assert engine_action != "STEER", "engine ignored the per-agent override"
    # and the shadow tape records the SAME surface hash the engine resolved
    assert shadow_hash == configured.config_hash()


# --------------------------------------------------------------------------- #
# D-28 / D-41 — the control-plane manual path is INERT (never Actuator.execute)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "endpoint,expected_action",
    [("/api/pause", "PAUSE"), ("/api/steer", "STEER"), ("/api/retry", "RETRY")],
)
def test_manual_endpoints_never_reach_actuator_execute(tmp_path, monkeypatch,
                                                       endpoint, expected_action):
    """D-28: ``/api/{pause,steer,retry}`` must route through the ActuationPort seam.

    ``Actuator.execute`` is the ONE path that can reach a real side-effect once
    actuation is later enabled, so a control-plane endpoint must never call it
    (D-28 — explicit "no manual Actuator.execute"). The manual directive is INERT
    (D-41) and human-gated, i.e. no machine may act on it unaided.

    Asserted three independent ways, because a single assertion here is easy to
    satisfy by accident: ``execute`` is NEVER called, ``dispatch`` is called
    EXACTLY once, and the HTTP response itself reports ``acted == False``.
    """
    monkeypatch.setenv("JCC_CONTROL_TOKEN", TOKEN)
    db_path = str(tmp_path / "jcc.db")
    cfg = Config(server=ServerConfig(), actuation=ActuationConfig(),
                 policy=PolicyConfig(), state_store_db_path=db_path)
    store = StateStore(db_path)
    # enabled=True on purpose: with actuation OFF, `execute` would deny anyway and
    # the assertion could pass without the route being fixed. Enabling it removes
    # the excuse.
    engine = CoreEngine(store, build_provider(cfg), Actuator(enabled=True), cfg)
    client = TestClient(create_app(engine, cfg))

    calls = {"execute": 0, "dispatch": 0}
    real_execute, real_dispatch = Actuator.execute, Actuator.dispatch
    seen = {}

    def _spy_execute(self, directive):
        calls["execute"] += 1
        return real_execute(self, directive)

    def _spy_dispatch(self, directive):
        calls["dispatch"] += 1
        seen["directive"] = directive
        return real_dispatch(self, directive)

    monkeypatch.setattr(Actuator, "execute", _spy_execute)
    monkeypatch.setattr(Actuator, "dispatch", _spy_dispatch)

    r = client.post(endpoint, json={"session_id": "s-manual"},
                    headers={"X-JCC-Token": TOKEN})
    assert r.status_code == 200, r.text[:200]
    payload = r.json()

    assert calls["execute"] == 0, (
        f"{endpoint} called Actuator.execute -- D-28 forbids the manual path from "
        "reaching the one function that can produce a side-effect"
    )
    assert calls["dispatch"] == 1, calls
    assert payload["action"] == expected_action
    assert payload["actuation"]["acted"] is False, payload["actuation"]

    directive = seen["directive"]
    assert directive.executable is False
    assert directive.requires_human is True
    assert directive.params.get("source") == "control-plane"
    assert directive.params.get("requires_human") is True
    # and the manual path never writes a `directives` row (legacy contract: the
    # only record is the audit row), so it cannot be mistaken for an execution
    with store.connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM directives WHERE session_id='s-manual'"
        ).fetchone()[0] == 0

