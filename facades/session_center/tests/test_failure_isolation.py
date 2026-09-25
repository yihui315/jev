"""S-06 — failure isolation tests.

A crash in the visual layer MUST NOT affect the supervision core. The facade has
two faces of the same guarantee:

* the strict path (``render_event``) RAISES to its caller, so a caller that
  asked for a projection and got a crash is told;
* the live path (``on_event``) CONTAINS the crash so it never reaches the core,
  but still counts + logs it (with a traceback) — i.e. the failure is surfaced,
  not swallowed.

Both are asserted: the core still emits its real directive and writes its audit
row; the facade's death is observable via its counters and the ERROR log.
"""
from __future__ import annotations

import logging

import pytest
from jcc_core.schemas import AEPEnvelope, AgentName

from session_center import ProjectionOutcome, SessionCenterFacade


class DyingFacade(SessionCenterFacade):
    """A facade whose projection explodes on every frame."""

    def render_event(self, msg):
        raise RuntimeError("visual layer exploded")


def _env(event_id: str, delivery_id: str) -> AEPEnvelope:
    return AEPEnvelope(
        version="aep/v0.1",
        event_id=event_id,
        delivery_id=delivery_id,
        session_id="s-death",
        agent=AgentName.WORKBUDDY,
        project="jev",
        cwd="F:\\jev",
        event_type="tool_call",
        payload={"tool": "read"},
    )


def test_render_event_raises_to_caller(store):
    """Strict path: a crash propagates to the caller, not absorbed."""
    dying = DyingFacade(store)
    with pytest.raises(RuntimeError):
        dying.render_event({"session_id": "s-x", "event_id": "e-x"})


def test_on_event_contains_failure_and_surfaces_it(store):
    """Live path: crash is contained but recorded (counted + typed)."""
    dying = DyingFacade(store)
    oc = dying.on_event({"session_id": "s-x", "event_id": "e-x"})
    assert isinstance(oc, ProjectionOutcome)
    assert oc.ok is False
    assert oc.error_type == "RuntimeError"
    assert oc.error
    assert dying.failures == 1
    assert dying.last_failure_type == "RuntimeError"
    assert "visual layer exploded" in (dying.last_failure or "")


def test_core_survives_facade_death(engine, store, caplog):
    """Wired to a REAL engine: facade dies every frame, core still completes."""
    dying = DyingFacade(store)
    engine.subscribe(dying.on_event)

    with caplog.at_level(logging.ERROR, logger="jcc.facade.session_center"):
        out = engine.ingest(_env("ev-death-1", "dl-death-1"))

    # 1) The core emitted a REAL decision (not a stub).
    assert out["directive"], "core produced no directive"
    assert out["action"] in {
        "ESCALATE", "PAUSE", "VERIFY", "STEER", "RETRY", "FINISH", "CONTINUE",
    }
    # 2) The core wrote its immutable audit row despite the facade death.
    rows = store.get_audit()
    assert any(r.get("event_id") == "ev-death-1" for r in rows), \
        "core wrote NO audit row — facade death corrupted the core"

    # 3) The failure was surfaced, NOT swallowed.
    assert dying.failures == 1
    assert dying.last_failure and "visual layer exploded" in dying.last_failure
    errs = [r for r in caplog.records
            if r.levelno == logging.ERROR and r.name == "jcc.facade.session_center"]
    assert errs, "facade death produced no ERROR log — swallowed"
    assert errs[0].exc_info, "facade death logged WITHOUT a traceback — swallowed"
    assert "core output unchanged" in errs[0].message


def test_failure_logged_with_traceback(store, caplog):
    dying = DyingFacade(store)
    with caplog.at_level(logging.ERROR, logger="jcc.facade.session_center"):
        dying.on_event({"session_id": "s-y", "event_id": "e-y"})
    errs = [r for r in caplog.records
            if r.levelno == logging.ERROR and r.name == "jcc.facade.session_center"]
    assert errs
    assert errs[0].exc_info
    assert isinstance(errs[0].exc_info, tuple) and errs[0].exc_info[0] is RuntimeError
