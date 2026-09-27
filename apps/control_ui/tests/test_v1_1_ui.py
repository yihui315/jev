"""§20 -- the v1.1 activation screen, over real HTTP and against real artifacts.

WHAT THIS SUITE IS FOR
----------------------
§20's constraint is "no front-end invention": every value on the screen has to
trace to one declared source. That is easy to say and easy to violate silently --
the first draft of the data layer asked the capability-matrix artifact for
``states`` and ``probe_source`` (the real keys are ``vocabulary`` and ``probe``)
and the directive panel rendered ``signature_verified`` and ``executable`` (not
columns of ``get_directives``). Both rendered as empty cells that looked exactly
like data.

So the central test here is :func:`test_every_panel_key_is_traceable_to_a_declared_source`:
it pins the panel key sets against the sources, and asserts each passthrough key
actually resolves in the source document. A key that cannot be resolved is a
failure, not an empty cell. The two historical defects are pinned by name in
:func:`test_the_known_invented_keys_stay_gone` so they cannot come back.

The rest:
* the screen is fetched over real HTTP from the real control plane (no stubs);
* the nav is walked, so a link that 404s is a failure;
* every panel is exactly one of the two legal shapes, and unavailable ones render
  their reason rather than a blank;
* the provider badge is table-driven over synthetic §7D payloads, and the
  reconciliation rule is tested in BOTH directions -- including the false
  positive that the first draft produced (live MOCK vs recorded REAL is two
  different configurations, not a divergence).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

from control_ui import v1_1
from control_ui.api_client import ApiClient
from control_ui.render import NAV, PANEL_HEADINGS, _PANEL_RENDERERS, render_v1_1
from control_ui._harness import (
    build_app,
    find_free_port,
    get,
    start_api_server,
    start_ui_server,
    stop_api_server,
    stop_ui_server,
)

REPO = Path(__file__).resolve().parents[3]

sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "apps"))
sys.path.insert(0, str(REPO / "packages"))
sys.path.insert(0, str(REPO / "fixtures"))

from fixtures.demo.load import load_demo_envelopes  # noqa: E402
from jcc_core.schemas import AEPEnvelope  # noqa: E402


# --------------------------------------------------------------------------- #
# live harness: one real control plane for the whole module
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def live(tmp_path_factory):
    """Real FastAPI control plane (uvicorn) + real stdlib UI, both on loopback.

    Module-scoped: the provider is asserted to be reached over a real socket, and
    paying for two servers per test would buy nothing -- every test below either
    needs the pair or needs neither.
    """
    import os

    os.environ.setdefault("JCC_CONTROL_TOKEN", "v11-ui-token")
    tmp = tmp_path_factory.mktemp("v11-ui")
    app, engine, _store, _cfg = build_app(tmp)
    for env in load_demo_envelopes():
        engine.ingest(AEPEnvelope(**env))

    api_port = find_free_port()
    api_server, api_thread = start_api_server(app, api_port)
    api_base = f"http://127.0.0.1:{api_port}"
    ui_port = find_free_port()
    ui_httpd, _ = start_ui_server(api_base, ui_port)
    ui_base = f"http://127.0.0.1:{ui_port}"
    ctx = {"api_base": api_base, "ui_base": ui_base, "engine": engine}
    yield ctx
    stop_ui_server(ui_httpd)
    stop_api_server(api_server, api_thread)


# --------------------------------------------------------------------------- #
# 1. the screen exists, over real HTTP, from the real API
# --------------------------------------------------------------------------- #
def test_the_v11_screen_is_served_over_real_http(live):
    status, body = get(f"{live['ui_base']}/v1.1")
    assert status == 200, status
    assert len(body.encode("utf-8")) > 300, "page suspiciously empty"

    # The live API was reachable, so the two API-backed panels must be sourced.
    assert "PANEL NOT SOURCED" not in body, "a panel failed against a live API"
    for heading in PANEL_HEADINGS.values():
        assert heading in body, f"panel heading missing: {heading}"


def test_the_nav_link_is_walked_and_every_href_answers(live):
    """Every NAV entry with an href must return 200. A dead link is a defect.

    Also pins that /v1.1 is reachable FROM the nav rather than only by typing it,
    which is the difference between a screen and a hidden route.
    """
    hrefs = [href for _label, href, _key in NAV if href]
    assert "/v1.1" in hrefs, "the v1.1 screen is not offered in the nav"
    for href in hrefs:
        status, body = get(f"{live['ui_base']}{href}")
        assert status == 200, (href, status)
        assert 'href="/v1.1"' in body, f"{href} does not offer the v1.1 screen"


def test_the_live_api_is_actually_reached_and_its_values_rendered(live):
    """Non-vacuity: the live panel numbers must equal the live API's numbers.

    Without this the screen could render a hardcoded provider mode and pass every
    other test here.
    """
    health = json.loads(get(f"{live['api_base']}/api/health")[1])
    _status, body = get(f"{live['ui_base']}/v1.1")
    assert str(health["provider_mode"]) in body
    assert str(health["provider_activation_state"]) in body
    decisions = json.loads(get(f"{live['api_base']}/api/decisions")[1])
    if decisions:
        assert str(decisions[-1]["directive_id"]) in body, "latest directive not shown"


# --------------------------------------------------------------------------- #
# 2. the artifact-backed panels must agree with the artifacts on disk
# --------------------------------------------------------------------------- #
def test_the_frozen_artifact_values_are_rendered_verbatim():
    """Read the artifacts here and assert the render shows those exact numbers.

    This is what separates "the panel is wired to the artifact" from "the panel
    prints something plausible".
    """
    cm = json.loads((REPO / v1_1.CAPABILITY_ARTIFACT).read_text(encoding="utf-8"))
    gate = json.loads((REPO / v1_1.GATE_ARTIFACT).read_text(encoding="utf-8"))
    html = render_v1_1(v1_1.v1_1_model(ApiClient("http://127.0.0.1:9", timeout=0.5), None))

    # capability matrix: every agent and its adapter_state must appear
    for agent, block in sorted((cm.get("agents") or {}).items()):
        assert agent in html, agent
        assert str(block.get("adapter_state")) in html
    # the actuation ladder, level by level, from the gate artifact
    for rung in gate["actuation_ladder"]:
        assert f">{rung['level']}<" in html or rung["level"] in html
    # the gate verdict's own vocabulary must be rendered, not paraphrased
    assert str(gate["headline"]["status"]) in html
    for defect in gate["verdict"]["defects"]:
        assert defect in html, defect


def test_the_shadow_stats_panel_says_it_is_release_time():
    """A release-time reading presented as live would be the specific dishonesty.

    Asserted on the SOURCE STRING, because the source string is what the reader
    sees next to the numbers.
    """
    p = v1_1.shadow_stats_panel()
    assert p["available"], p
    assert "release-time" in p["source"]
    assert "not live" in p["source"]


def test_an_absent_artifact_is_reported_as_absent_not_as_unreadable(monkeypatch):
    """Three-state rule: missing / unreadable / present-and-empty stay distinct.

    Collapsing "I could not find it" into "I could not read it" costs exactly the
    information an operator needs ("did someone delete the evidence, or is it
    corrupt?").
    """
    monkeypatch.setattr(v1_1, "CAPABILITY_ARTIFACT", "artifacts/release/does-not-exist.json")
    p = v1_1.capability_matrix_panel()
    assert p["available"] is False
    assert p["reason"] == v1_1.REASON_ARTIFACT_MISSING

    bad = Path("artifacts/release/_v11_ui_corrupt.json")
    target = REPO / bad
    target.write_text("{not json", encoding="utf-8")
    try:
        monkeypatch.setattr(v1_1, "CAPABILITY_ARTIFACT", str(bad).replace("\\", "/"))
        p2 = v1_1.capability_matrix_panel()
        assert p2["available"] is False
        assert p2["reason"] == v1_1.REASON_ARTIFACT_UNREADABLE
        assert p2["reason"] != p["reason"]
    finally:
        target.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# 3. the central rule: every panel key traces to a declared source
# --------------------------------------------------------------------------- #
#: Sentinel for a value the panel COMPUTES (an aggregate over the source, not a
#: field copied out of it). A derived key is allowed; an unresolvable key is not.
DERIVED = "<derived>"
CONST = "<constant>"

#: panel -> {panel_key: (source_name, path_in_that_source)}
#: source_name is one of: "health", "capability", "gate", "verification",
#: "config", "decisions". ``DERIVED``/``CONST`` need no source.
TRACE = {
    "provider": {
        "badge": DERIVED,
        "provider_mode": ("health", "provider_mode"),
        "provider_requested_mode": ("health", "provider_requested_mode"),
        "provider_is_real": ("health", "provider_is_real"),
        "provider_verified": ("health", "provider_verified"),
        "provider_activation_state": ("health", "provider_activation_state"),
        "jev_provider": ("health", "jev_provider"),
        "actuation_enabled_configured": ("health", "actuation_enabled"),
        "policy_version": ("health", "policy_version"),
        "verified_artifact": CONST,
        "recorded_real_jev": ("verification", "decision.real_jev"),
        "recorded_provider_mode": ("verification", "decision.provider_mode"),
        "recorded_provider_verified": ("verification", "decision.provider_verified"),
        "recorded_reason": DERIVED,
        "recorded_detail": DERIVED,
        "recorded_note": CONST,
        "safety_divergence": DERIVED,
    },
    "capability_matrix": {
        "rows": DERIVED,
        "agent_count": DERIVED,
        "actuable_capability_count": ("capability", "actuation_summary.actuable_capability_count"),
        "actuable": ("capability", "actuation_summary.actuable"),
        "vocabulary": ("capability", "vocabulary"),
        "section": ("capability", "section"),
        "schema": ("capability", "schema"),
        "verified_total": DERIVED,
        "telemetry_verified": DERIVED,
        "probe_script": ("capability", "probe.script"),
        "probe_measurement": ("capability", "probe.measurement"),
        "evidence_dir": ("capability", "probe.evidence_dir"),
        "repo_head_recorded": ("capability", "repo_head"),
    },
    "shadow_stats": {
        "status": ("gate", "verdict.status"),
        "satisfied": ("gate", "verdict.satisfied"),
        "observed_decisions": ("gate", "verdict.observed_decisions"),
        "observed_span_s": ("gate", "verdict.observed_span_s"),
        "actuated_observations": ("gate", "verdict.actuated_observations"),
        "chain_intact": ("gate", "verdict.chain_intact"),
        "defects": ("gate", "verdict.defects"),
        "thresholds": ("gate", "verdict.thresholds"),
    },
    "actuation": {
        "effective_level": ("gate", "headline.effective_level"),
        "configured_level": ("gate", "headline.configured_level"),
        "granted_ceiling": ("gate", "headline.granted_ceiling"),
        "gate_status": ("gate", "headline.status"),
        "gate_satisfied": ("gate", "headline.satisfied"),
        "actuation_enabled_gate": ("gate", "headline.actuation_enabled"),
        "actuation_enabled_configured": ("health", "actuation_enabled"),
        "mismatch": DERIVED,
        "ladder": ("gate", "actuation_ladder"),
        "defects": ("gate", "verdict.defects"),
        "db_present": ("gate", "db.present"),
        "note": ("gate", "note"),
    },
    "pending_activation": {
        "items": DERIVED,
        "count": DERIVED,
        "headline_status": ("gate", "headline.status"),
        "provider_artifact": CONST,
        "provider_artifact_reason": DERIVED,
    },
    "budget": {
        "limits": ("config", "p3.shadow.budget"),
        "limit_count": DERIVED,
        "provenance_values": DERIVED,
        "usage_available": CONST,
        "usage_reason": CONST,
        "usage_detail": CONST,
        "scorer_tokens_waived": DERIVED,
    },
    "directive": {
        "total": DERIVED,
        "signed": DERIVED,
        "unsigned": DERIVED,
        "acted": DERIVED,
        "signed_flag_but_no_signature_or_reverse": DERIVED,
        "origins": DERIVED,
        "session_count": DERIVED,
        "latest": ("decisions", "*"),
        "scope": DERIVED,
        "fields_returned": DERIVED,
    },
}


def _resolve(doc: Any, path: str) -> Any:
    if path == "*":
        return doc
    node = doc
    for part in path.split("."):
        assert isinstance(node, dict), f"{path}: {part} reached a non-dict"
        assert part in node, f"{path}: key {part!r} is not in the source"
        node = node[part]
    return node


def _source_docs() -> Dict[str, Any]:
    """The source documents, as PAYLOADS (not as API envelopes).

    The ``{"ok": ..., "data": ...}`` wrapper is transport plumbing produced by
    ``model._safe``; the source of a field is the payload inside it. Keeping the
    payload bare here means each TRACE path reads as "this field of that
    document", which is the claim being checked.
    """
    health = {
        "provider_mode": "MOCK", "provider_requested_mode": "MOCK",
        "provider_is_real": False, "provider_verified": True,
        "provider_activation_state": "ACTIVE", "jev_provider": "mock",
        "actuation_enabled": False, "policy_version": "p3.0.0",
    }
    cfg = pytest.importorskip("jcc_core.config").load_config()
    decisions = [{
        "directive_id": "d1", "session_id": "s1", "timestamp": 1.0,
        "action": "CONTINUE", "target_actuator": "noop", "params": {},
        "policy_version": "p3.0.0", "signed": True, "signature": "sha256=x",
        "evidence_hash": "h", "acted": False, "actuation_reason": "shadow_mode",
        "origin": "SUPERVISOR",
    }]
    return {
        "health": health,
        "capability": json.loads((REPO / v1_1.CAPABILITY_ARTIFACT).read_text(encoding="utf-8")),
        "gate": json.loads((REPO / v1_1.GATE_ARTIFACT).read_text(encoding="utf-8")),
        "verification": json.loads(
            (REPO / v1_1.VERIFICATION_ARTIFACT).read_text(encoding="utf-8")),
        "config": {"p3": {"shadow": {"budget": dict(
            (cfg.raw.get("p3") or {}).get("shadow", {}).get("budget") or {})}}},
        "decisions": decisions,
    }


def _panels_for_trace(docs: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Build the panels with the sources above substituted in, where possible.

    The two live panels are fed the synthetic health / decisions documents
    directly rather than through a socket, because this test is about the SHAPE of
    the mapping, not about transport (which the HTTP tests above cover).
    """
    import control_ui.model as model

    real_get = model._get_json

    def fake_get(_api, path, *a, **kw):
        if path.startswith("/api/health"):
            return {"ok": True, "data": docs["health"]}
        if path.startswith("/api/decisions"):
            return {"ok": True, "data": docs["decisions"]}
        return real_get(_api, path, *a, **kw)

    monkey = pytest.MonkeyPatch()
    monkey.setattr(model, "_get_json", fake_get)

    class Cfg:
        raw = docs["config"]

    health_envelope = {"ok": True, "data": docs["health"]}
    try:
        panels = {
            "provider": v1_1.provider_panel(health_envelope),
            "capability_matrix": v1_1.capability_matrix_panel(),
            "shadow_stats": v1_1.shadow_stats_panel(),
            "actuation": v1_1.actuation_panel(health_envelope),
            "pending_activation": v1_1.pending_activation_panel(),
            "budget": v1_1.budget_panel(Cfg),
            "directive": v1_1.directive_panel(ApiClient("http://127.0.0.1:9", timeout=0.5), None),
        }
    finally:
        monkey.undo()
    return panels


def test_every_panel_key_is_traceable_to_a_declared_source():
    """THE §20 rule, executed: no panel key may exist without a declared origin.

    Three assertions per panel:
      1. the panel's key set equals the declared mapping exactly (so a new key
         forces an explicit declaration of where it comes from);
      2. every passthrough key resolves in the named source document (so a typo
         or an invented field is a FAILURE, not an empty cell);
      3. the panel is available and carries data.
    """
    docs = _source_docs()
    panels = _panels_for_trace(docs)

    for name, mapping in TRACE.items():
        panel = panels[name]
        assert panel["available"], (name, panel)

        undeclared = set(panel["data"]) - set(mapping)
        assert not undeclared, f"{name}: key(s) with no declared source: {sorted(undeclared)}"
        missing = set(mapping) - set(panel["data"])
        assert not missing, f"{name}: declared key(s) not produced: {sorted(missing)}"

        for key, origin in mapping.items():
            if origin in (DERIVED, CONST):
                continue
            source_name, path = origin
            source = docs[source_name]
            value = _resolve(source, path)  # raises AssertionError if unresolvable
            got = panel["data"][key]
            if path == "*":
                # "*" claims "this value is one of the source's rows" (used for
                # the directive panel's ``latest``). Keyed on the PATH, not on the
                # resolved type: a path that resolves to a list is a list the panel
                # copies, and an empty list equals an empty list.
                assert got in value, f"{name}.{key} is not a row of {source_name}"
            else:
                assert got == value, (
                    f"{name}.{key}={got!r} does not match {source_name}:{path}={value!r}")


def test_the_known_invented_keys_stay_gone():
    """Both historical invented-key defects, pinned by name.

    These are not hypothetical: the capability panel asked for ``states`` and
    ``probe_source``, and the directive panel rendered ``signature_verified`` and
    ``executable``. All four rendered as empty cells. Named here so a future edit
    that reintroduces one fails loudly instead of quietly producing blanks.
    """
    cm = v1_1.capability_matrix_panel()
    assert cm["available"], cm
    for gone in ("states", "probe_source"):
        assert gone not in cm["data"], f"{gone} is not a key of the artifact"

    import control_ui.model as model
    monkey = pytest.MonkeyPatch()

    def fake_get(_api, path, *a, **kw):
        if path.startswith("/api/decisions"):
            return {"ok": True, "data": [{
                "directive_id": "d1", "session_id": "s1", "timestamp": 1.0,
                "action": "CONTINUE", "target_actuator": "noop", "params": {},
                "policy_version": "p3.0.0", "signed": True, "signature": "sha256=x",
                "evidence_hash": "h", "acted": False,
                "actuation_reason": "shadow_mode", "origin": "SUPERVISOR",
            }]}
        return {"ok": False, "error": "API UNREACHABLE", "detail": "n/a"}

    monkey.setattr(model, "_get_json", fake_get)
    try:
        dp = v1_1.directive_panel(ApiClient("http://127.0.0.1:9", timeout=0.5), None)
    finally:
        monkey.undo()
    assert dp["available"], dp
    latest = dp["data"]["latest"]
    for gone in ("signature_verified", "executable"):
        assert gone not in latest, f"{gone} is not a column returned by get_directives"
    # and the fields it DOES report are the real column names
    assert dp["data"]["fields_returned"] == sorted(latest.keys())


def test_the_directive_panels_field_list_matches_the_endpoint(live):
    """The one source that cannot be read from disk: ask the live endpoint."""
    decisions = json.loads(get(f"{live['api_base']}/api/decisions")[1])
    assert decisions, "the smoke run seeded no directives; the panel would be vacuous"
    real_fields = sorted(decisions[-1].keys())
    dp = v1_1.directive_panel(ApiClient(live["api_base"], timeout=5.0), None)
    assert dp["available"], dp
    assert dp["data"]["fields_returned"] == real_fields
    assert dp["data"]["total"] == len(decisions)
    assert dp["data"]["acted"] == sum(1 for d in decisions if d["acted"])


# --------------------------------------------------------------------------- #
# 4. the two legal panel shapes, and the renderer that must cover all of them
# --------------------------------------------------------------------------- #
def test_a_panel_is_never_available_and_unavailable_at_once():
    with pytest.raises(ValueError):
        v1_1._panel("x", "src")                                  # neither
    with pytest.raises(ValueError):
        v1_1._panel("x", "src", data={}, reason="why")           # both


def test_every_panel_has_a_heading_and_a_renderer():
    """A panel added to PANEL_NAMES without a renderer must not be silent.

    This is the "instrument does nothing and reports success" failure mode: a
    panel that is fetched, counted as sourced, and never drawn.
    """
    assert set(v1_1.PANEL_NAMES) == set(PANEL_HEADINGS), (
        set(v1_1.PANEL_NAMES) ^ set(PANEL_HEADINGS))
    assert set(v1_1.PANEL_NAMES) == set(_PANEL_RENDERERS), (
        set(v1_1.PANEL_NAMES) ^ set(_PANEL_RENDERERS))
    assert len(v1_1.PANEL_NAMES) == 7


def test_an_unsourced_panel_renders_its_reason_not_a_blank():
    """With the API dead the two live panels must say WHY, per panel.

    The page-level "API UNREACHABLE" marker must NOT appear: five of the seven
    panels are artifact-backed and remain true, so a page-wide unreachable verdict
    would be false. This is asserted explicitly because "the page says nothing
    useful" and "the page says the wrong thing" are different defects.
    """
    dead = ApiClient("http://127.0.0.1:9", timeout=0.5)
    m = v1_1.v1_1_model(dead, None)
    html = render_v1_1(m)

    assert m["available_count"] == 5, m["available_count"]
    assert m["unavailable_count"] == 2
    for name in ("provider", "directive"):
        p = m["panels"][name]
        assert p["available"] is False
        assert p["reason"] == v1_1.REASON_API_UNREACHABLE
        assert p["reason"] in html, f"{name}: reason not rendered"
        assert "PANEL NOT SOURCED" in html
    assert "API UNREACHABLE" not in html, (
        "the v1.1 page must not claim the whole page is unreachable: "
        "five panels are artifact-backed and still valid")


def test_an_unknown_value_is_spelled_out_not_left_empty():
    """``None`` means UNKNOWN on this page and must render as such.

    chain_intact is None when the shadow tape is absent -- it cannot be judged
    either way. Rendering it as an empty cell would read as "no chain problem",
    which is the opposite of what it means.
    """
    m = v1_1.v1_1_model(ApiClient("http://127.0.0.1:9", timeout=0.5), None)
    gate_reasons = m["panels"]["shadow_stats"]["data"]["defects"]
    if "shadow_tape_absent" in gate_reasons:
        assert m["panels"]["shadow_stats"]["data"]["chain_intact"] is None
        assert "(unknown)" in render_v1_1(m)


def test_the_mismatch_reconciliation_is_computed_from_two_real_surfaces():
    """config switch vs gate verdict, both directions, plus 'cannot tell'."""
    gate = json.loads((REPO / v1_1.GATE_ARTIFACT).read_text(encoding="utf-8"))
    gate_enabled = gate["headline"]["actuation_enabled"]
    assert gate_enabled is False, "this test assumes the emitted verdict is closed"

    on = v1_1.actuation_panel({"ok": True, "data": {"actuation_enabled": True}})
    assert on["data"]["mismatch"], "config on + gate closed must be raised"
    assert "gate" in on["data"]["mismatch"]

    off = v1_1.actuation_panel({"ok": True, "data": {"actuation_enabled": False}})
    assert off["data"]["mismatch"] is None

    unknown = v1_1.actuation_panel({"ok": False, "error": "API UNREACHABLE"})
    assert unknown["data"]["actuation_enabled_configured"] is None
    assert unknown["data"]["mismatch"] is None, (
        "an unknown config switch must not be reported as agreement")


# --------------------------------------------------------------------------- #
# 5. the provider badge, table-driven, and the reconciliation in both directions
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("health,expected", [
    ({"provider_is_real": False, "provider_activation_state": "PENDING_EXTERNAL_ACTIVATION"},
     "PENDING_ACTIVATION"),
    ({"provider_is_real": False, "provider_activation_state": "PENDING"},
     "PENDING_ACTIVATION"),
    ({"provider_is_real": False, "provider_mode": "DISABLED"}, "DISABLED"),
    ({"provider_is_real": False, "provider_mode": "MOCK"}, "MOCK"),
    ({"provider_is_real": False, "provider_mode": "REPLAY"}, "NOT_REAL"),
    ({"provider_is_real": True, "provider_verified": True}, "REAL"),
    ({"provider_is_real": True, "provider_verified": False}, "REAL_UNVERIFIED"),
])
def test_the_provider_badge_is_a_function_of_the_live_fields_only(health, expected):
    assert v1_1.provider_badge(health) == expected


def test_a_real_provider_with_an_unverified_card_is_the_one_alarm(monkeypatch):
    """The safety-relevant direction, asserted."""
    monkeypatch.setattr(v1_1, "verification_decision", lambda: {
        "ok": True, "data": {"real_jev": "PENDING_EXTERNAL_ACTIVATION",
                             "provider_mode": "REAL", "provider_verified": False},
        "smoke": {}})
    p = v1_1.provider_panel({"ok": True, "data": {
        "provider_mode": "REAL", "provider_is_real": True, "provider_verified": False}})
    assert p["data"]["badge"] == "REAL_UNVERIFIED"
    assert p["data"]["safety_divergence"], (
        "a live real provider against an unverified interface card must be raised")
    assert "DIVERGENCE" in render_v1_1({
        "panels": {"provider": p}, "order": ["provider"],
        "available_count": 1, "unavailable_count": 0, "session_id": None})


def test_a_different_configuration_is_not_reported_as_a_divergence(monkeypatch):
    """The false positive the first draft produced, pinned so it cannot return.

    A UI/test harness legitimately runs MOCK while the repository config requests
    REAL. Comparing the two records field-by-field flagged that as a divergence on
    every render, which is exactly the kind of always-on warning readers learn to
    ignore.
    """
    monkeypatch.setattr(v1_1, "verification_decision", lambda: {
        "ok": True, "data": {"real_jev": "PENDING_EXTERNAL_ACTIVATION",
                             "provider_mode": "REAL", "provider_verified": False},
        "smoke": {}})
    p = v1_1.provider_panel({"ok": True, "data": {
        "provider_mode": "MOCK", "provider_requested_mode": "MOCK",
        "provider_is_real": False, "provider_verified": True,
        "provider_activation_state": "ACTIVE"}})
    assert p["data"]["badge"] == "MOCK"
    assert p["data"]["safety_divergence"] is None
    assert p["data"]["recorded_provider_mode"] == "REAL", "the record is still shown"
    assert "DIVERGENCE" not in render_v1_1({
        "panels": {"provider": p}, "order": ["provider"],
        "available_count": 1, "unavailable_count": 0, "session_id": None})


def test_an_unreadable_verification_artifact_does_not_read_as_agreement(monkeypatch):
    """'I could not read it' must be stated, never treated as 'nothing to report'."""
    monkeypatch.setattr(v1_1, "verification_decision", lambda: {
        "ok": False, "reason": v1_1.REASON_ARTIFACT_MISSING, "detail": "gone"})
    p = v1_1.provider_panel({"ok": True, "data": {
        "provider_mode": "MOCK", "provider_is_real": False, "provider_verified": True}})
    assert p["data"]["safety_divergence"] is None
    assert p["data"]["recorded_reason"] == v1_1.REASON_ARTIFACT_MISSING
    html = render_v1_1({"panels": {"provider": p}, "order": ["provider"],
                        "available_count": 1, "unavailable_count": 0,
                        "session_id": None})
    assert "artifact unreadable" in html
    assert v1_1.REASON_ARTIFACT_MISSING in html


# --------------------------------------------------------------------------- #
# 6. pending activation must be complete, not just the shadow gate
# --------------------------------------------------------------------------- #
def test_pending_activation_lists_every_inert_thing():
    p = v1_1.pending_activation_panel()
    assert p["available"], p
    items = {i["item"]: i for i in p["data"]["items"]}
    assert "live_shadow_observation_window" in items
    assert items["live_shadow_observation_window"]["state"] == "PENDING_LIVE_SHADOW_GATE"
    assert "actuation_ceiling" in items
    assert "actuation_enabled" in items
    assert "real_jev_provider" in items, (
        "the provider is the other thing this release leaves inert and it lives "
        "in a different artifact; omitting it would make this panel mean "
        "'pending the shadow gate'")
    assert items["real_jev_provider"]["state"] == "PENDING_EXTERNAL_ACTIVATION"
    assert items["real_jev_provider"]["defects"], "the closed §7 gates must be listed"


def test_pending_activation_is_derived_from_the_artifacts_not_hand_written():
    """Every item's state must appear in the artifact it claims to come from."""
    gate = json.loads((REPO / v1_1.GATE_ARTIFACT).read_text(encoding="utf-8"))
    ver = json.loads((REPO / v1_1.VERIFICATION_ARTIFACT).read_text(encoding="utf-8"))
    p = v1_1.pending_activation_panel()
    states = {i["item"]: i["state"] for i in p["data"]["items"]}
    assert states["live_shadow_observation_window"] == gate["headline"]["status"]
    assert states["actuation_ceiling"] == gate["headline"]["granted_ceiling"]
    assert str(states["actuation_enabled"]).lower() == str(
        gate["headline"]["actuation_enabled"]).lower()
    assert states["real_jev_provider"] == ver["decision"]["real_jev"]


# --------------------------------------------------------------------------- #
# 7. budget: limits are real, usage is unavailable-with-a-reason
# --------------------------------------------------------------------------- #
def test_budget_reports_limits_and_refuses_to_invent_usage():
    from jcc_core.config import load_config

    cfg = load_config()
    p = v1_1.budget_panel(cfg)
    assert p["available"], p
    declared = (cfg.raw["p3"]["shadow"]["budget"])
    assert p["data"]["limits"] == declared, "limits must be the config's own block"
    assert p["data"]["usage_available"] is False
    assert p["data"]["usage_reason"] == v1_1.REASON_NO_ENDPOINT
    assert p["data"]["usage_detail"], "an unavailable value must carry a reason"
    html = render_v1_1({"panels": {"budget": p}, "order": ["budget"],
                        "available_count": 1, "unavailable_count": 0,
                        "session_id": None})
    for key in declared:
        assert key in html, key


def test_a_config_without_a_budget_block_is_unavailable_not_zero():
    class Cfg:
        raw: Dict[str, Any] = {}

    p = v1_1.budget_panel(Cfg)
    assert p["available"] is False
    assert p["reason"] == v1_1.REASON_NO_ENDPOINT


# --------------------------------------------------------------------------- #
# 8. S-04 coverage: the gap is deliberate and must be visible
# --------------------------------------------------------------------------- #
def test_the_acceptance_screen_set_still_matches_the_v10_claim():
    """Why this test exists.

    ``acceptance.py``'s SCREENS list is mirrored by ``CONTROL_UI_SCREENS`` in the
    TRACKED release gate, and the gate fails on an UNEXPECTED screen. So adding
    ``/v1.1`` to the acceptance set would make S-04 fail without touching the gate
    -- and touching the gate is an operator decision, not a side effect of adding
    a screen. The v1.1 screen is therefore covered by this module over real HTTP
    instead, and the exclusion is asserted here so it stays a decision rather than
    an oversight.
    """
    from control_ui import acceptance

    names = [n for n, _p in acceptance.SCREENS]
    assert "v1_1" not in names and "v1.1" not in names, (
        "the v1.1 screen was added to S-04's claimed set; that requires updating "
        "CONTROL_UI_SCREENS in artifacts/acceptance/final_gate_run.py in the same "
        "commit, deliberately")
    assert len(names) == 7, (
        "S-04's declared set excludes session_detail, which is appended at run "
        "time when a session exists (7 + 1 = the eight the gate expects)")


def test_the_tracked_smoke_artifact_agrees_with_the_declared_screen_set():
    """The artifact S-04 reads and the SCREENS list that produces it must agree.

    Checked here because the gate checks it too, and finding the drift in a unit
    test is cheaper than finding it in a 40-minute gate run -- and it can be found
    on a machine where the artifact has not been regenerated yet.
    """
    from control_ui import acceptance

    artifact = REPO / "artifacts" / "acceptance" / "control_ui_smoke.json"
    if not artifact.is_file():
        pytest.skip("the smoke artifact has not been generated in this checkout")
    rec = json.loads(artifact.read_text(encoding="utf-8"))
    declared = {n for n, _p in acceptance.SCREENS}
    actual = {s["name"] for s in rec.get("screens") or []}
    assert declared <= actual, declared - actual
    assert actual - declared <= {"session_detail"}, actual - declared
    assert rec.get("screens_expected") == len(actual)
