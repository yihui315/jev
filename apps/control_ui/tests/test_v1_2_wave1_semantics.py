"""JCC v1.2 — Wave 1: the three UI semantic-defect fixes.

These tests pin the SEMANTIC clarity of the 2D Control UI that Wave 1 introduced,
so a future edit cannot silently re-collapse the distinctions:

* Defect A — Agent capability ambiguity: the dashboard agent table must show the
  three SEMANTIC dimensions (adapter/telemetry state, AEP normalization, capability
  set verified) as SEPARATE columns, and must surface the authoritative
  "Verified actuable agents: X/4" count from the frozen §10 record (which is NOT
  the same as the per-agent ``verified`` flag).
* Defect B — Shadow semantic contradiction: the shadow-stats panel must group
  Storage / Observations / Gate-defects into three labelled sub-sections, not one
  flat row that mixes them.
* Defect C — Actuation Ladder ambiguity: the actuation panel must state
  POLICY-PERMITTED vs CURRENTLY-EXECUTABLE separately, and give a three-state
  BLOCKED reason (never a silent "off").

No live server is required for the render-level assertions; the panel builders are
exercised against the real on-disk artifacts where they read from a source.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

from control_ui import v1_1
from control_ui.render import _render_actuation, _render_shadow_stats, render_dashboard

REPO = Path(__file__).resolve().parents[3]
for p in (str(REPO), str(REPO / "apps"), str(REPO / "packages"), str(REPO / "fixtures")):
    if p not in sys.path:
        sys.path.insert(0, p)


# --------------------------------------------------------------------------- #
# Defect A
# --------------------------------------------------------------------------- #
def _dashboard_model_with(dimensions: Dict[str, Dict[str, Any]],
                          verified_actuable: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "health": {"ok": True, "data": {
            "status": "ok", "jev_provider": "mock", "actuation_enabled": False,
            "policy_version": "p3.0.0", "localhost_only": True, "wal": True,
            "open_get_endpoints": [],
        }},
        "agents": dimensions,
        "risk_by_band": {},
        "problems": 0,
        "session_count": 0,
        "demo_count": 0,
        "budget_limits": {},
        "verified_actuable": verified_actuable,
    }


def test_defect_a_dashboard_shows_three_separate_dimensions():
    agents = {
        "workbuddy": {"status": "CONNECTED", "aep": True, "verified": True, "version": "5.5.6"},
        "claude": {"status": "CONNECTED", "aep": True, "verified": True, "version": "2.1.196"},
        "codex": {"status": "DEGRADED", "aep": True, "verified": True},
        "hermes": {"status": "CONNECTED", "aep": True, "verified": True, "version": "0.4.0"},
    }
    m = _dashboard_model_with(agents, {"available": True,
                                       "actuable_capability_count": 0,
                                       "agent_count": 4, "actuable": []})
    html = render_dashboard(m)
    # The three dimensions are labelled, not collapsed into a single "verified".
    assert "adapter state (telemetry)" in html
    assert "AEP normalization" in html
    assert "capability set verified" in html
    # All four agents still listed (Legacy 8/8 smoke depends on this).
    for name in ("workbuddy", "claude", "codex", "hermes"):
        assert name in html
    # The old conflating single column header must be gone.
    assert "<th>verified</th>" not in html
    assert "<th>status</th>" not in html


def test_defect_a_actuable_count_is_authoritative_not_the_verified_flag():
    """``verified=True`` on every agent must NOT read as 4/4 actuable."""
    agents = {n: {"status": "CONNECTED", "aep": True, "verified": True}
              for n in ("workbuddy", "claude", "codex", "hermes")}
    m = _dashboard_model_with(agents, {"available": True,
                                       "actuable_capability_count": 0,
                                       "agent_count": 4, "actuable": []})
    html = render_dashboard(m)
    assert "Verified actuable agents: 0/4" in html
    # The note must explain the distinction so a reader cannot conflate them.
    assert "capability set verified" in html
    assert "NOT &quot;actuation-capable&quot;" in html or "NOT \"actuation-capable\"" in html


def test_defect_a_actuable_summary_is_source_traced():
    from control_ui.model import _verified_actuable_summary
    got = _verified_actuable_summary()
    assert got["available"], got
    # The authoritative count comes from the frozen §10 artifact, not the config.
    cm = json.loads((REPO / v1_1.CAPABILITY_ARTIFACT).read_text(encoding="utf-8"))
    assert got["actuable_capability_count"] == (
        cm["actuation_summary"]["actuable_capability_count"])
    assert got["agent_count"] == len(cm["agents"])


# --------------------------------------------------------------------------- #
# Defect B
# --------------------------------------------------------------------------- #
def test_defect_b_shadow_stats_grouped_into_three_sections():
    p = v1_1.shadow_stats_panel()
    assert p["available"], p
    html = _render_shadow_stats(p)
    assert "<h3>Storage</h3>" in html
    assert "<h3>Observations (release-time)</h3>" in html
    assert "<h3>Gate defects</h3>" in html
    # Each distinct fact is present and labelled, so they cannot be confused.
    assert "shadow tape present (db_present)" in html
    assert "observed_decisions" in html
    assert "defects" in html


# --------------------------------------------------------------------------- #
# Defect C
# --------------------------------------------------------------------------- #
def test_defect_c_actuation_states_policy_vs_execution_separately():
    p = v1_1.actuation_panel({"ok": True, "data": {"actuation_enabled": False}})
    assert p["available"], p
    html = _render_actuation(p)
    assert "POLICY-PERMITTED (gate ladder)" in html
    assert "CURRENTLY-EXECUTABLE (policy + operator switch)" in html
    assert "BLOCKED reason (three-state)" in html
    # For the frozen closed gate (ceiling L0, gate not satisfied, config off) the
    # two states are both False and the reason is one of the three legal values.
    assert p["data"]["policy_permitted"] is False
    assert p["data"]["currently_executable"] is False
    assert p["data"]["blocked_reason"] in (
        "config_off", "gate_not_satisfied", "ceiling_L0", "unknown")


def test_defect_c_blocked_reason_truth_table():
    closed = {"satisfied": False, "granted_ceiling": "L0",
              "status": "PENDING_LIVE_SHADOW_GATE"}
    # operator switch off + ceiling L0 -> ceiling_L0 wins (most specific)
    assert v1_1._actuation_blocked_reason(closed, False) == "ceiling_L0"
    # operator switch off + ceiling above L0 + gate not satisfied -> gate_not_satisfied
    assert v1_1._actuation_blocked_reason(
        {"satisfied": False, "granted_ceiling": "L3"}, False) == "gate_not_satisfied"
    # operator switch on + gate satisfied + ceiling above L0 -> executable
    assert v1_1._actuation_blocked_reason(
        {"satisfied": True, "granted_ceiling": "L3"}, True) is None
    # operator switch off + gate satisfied + ceiling above L0 -> config_off
    assert v1_1._actuation_blocked_reason(
        {"satisfied": True, "granted_ceiling": "L3"}, False) == "config_off"
