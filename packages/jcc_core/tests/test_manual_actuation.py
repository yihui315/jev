"""D-28: the control-plane manual path must never reach ``Actuator.execute``.

The manual endpoints (``/api/pause``, ``/api/steer``, ``/api/retry``) are the one
HTTP path a human can use to force an action. ADR-0008 D-28 forbids that path
from calling ``Actuator.execute`` — ``execute`` is the single function that can
produce a real side-effect once actuation is enabled, so a manual directive must
go through the ActuationPort seam (``dispatch``), which is permanently inert
(D-27 / D-41).

The compliance gap is only observable with a spy: with ``enabled=True`` and the
action allow-listed (the worst case), pre-fix ``execute`` and post-fix
``dispatch`` both return ``acted=False``, so the HTTP body alone cannot tell them
apart. What distinguishes them is WHICH method was called, so that is what this
test asserts.

RECORDED DEVIATION (lead ruling, pre-freeze): the SIGNING half of D-28 is a P4
item and is deliberately NOT done here — there is no Assessment to arbitrate a
manual directive against, and the signing key is resolved in ``run._wire_shadow``.
This test therefore pins the actuation half only, and says so.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from jcc_core.actuator import Actuator
from jcc_core.config import ActuationConfig, Config, PolicyConfig, ServerConfig
from jcc_core.core import CoreEngine
from jcc_core.providers import build_provider
from jcc_core.schemas import DirectiveAction
from jcc_core.server import create_app
from jcc_core.state_store import StateStore

TOKEN = "manual-actuation-token"


class _CountingActuator(Actuator):
    """Counts the two entry points so "never called" is observed, not assumed."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.execute_calls = 0
        self.dispatch_calls = 0

    def execute(self, directive):  # noqa: D102 - spy
        self.execute_calls += 1
        return super().execute(directive)

    def dispatch(self, directive):  # noqa: D102 - spy
        self.dispatch_calls += 1
        return super().dispatch(directive)


def _client(tmp_path, monkeypatch, actuator):
    monkeypatch.setenv("JCC_CONTROL_TOKEN", TOKEN)
    db_path = str(tmp_path / "jcc.db")
    cfg = Config(
        server=ServerConfig(),
        actuation=ActuationConfig(),
        policy=PolicyConfig(),
        state_store_db_path=db_path,
    )
    store = StateStore(db_path)
    engine = CoreEngine(store, build_provider(cfg), actuator, cfg)
    return TestClient(create_app(engine, cfg))


@pytest.mark.parametrize("endpoint,action", [
    ("/api/pause", DirectiveAction.PAUSE),
    ("/api/steer", DirectiveAction.STEER),
    ("/api/retry", DirectiveAction.RETRY),
], ids=["pause", "steer", "retry"])
def test_manual_path_dispatches_and_never_executes(tmp_path, monkeypatch, endpoint, action):
    # Worst case for the defect: actuation ENABLED and the action allow-listed, so
    # a stray `execute` would be the only thing standing between a human request
    # and a real side-effect.
    spy = _CountingActuator(enabled=True, allowed_actions=[action.value])
    client = _client(tmp_path, monkeypatch, spy)

    res = client.post(endpoint, json={"session_id": "s-manual"},
                      headers={"X-JCC-Token": TOKEN})
    assert res.status_code == 200, res.text
    body = res.json()

    assert spy.execute_calls == 0, (
        f"{endpoint} reached Actuator.execute (D-28 violation): the manual path "
        "must go through the inert dispatch seam only"
    )
    assert spy.dispatch_calls == 1, f"{endpoint} did not dispatch exactly once"

    assert body["action"] == action.value
    assert body["actuation"]["acted"] is False
    assert body["actuation"]["executable"] is False
    assert body["actuation"]["reason"] == "shadow_mode"


def test_manual_path_is_inert_even_when_actuation_is_enabled(tmp_path, monkeypatch):
    """Non-vacuity: the spy proves `execute` WAS reachable on this actuator.

    Without this, `execute_calls == 0` could pass merely because the actuator was
    disabled. Calling `execute` directly on the same instance shows the method is
    wired and counted, so its zero count in the test above is meaningful.
    """
    spy = _CountingActuator(enabled=True, allowed_actions=["PAUSE"])
    spy.execute(_manual_directive())
    assert spy.execute_calls == 1


def _manual_directive():
    from jcc_core.schemas import Directive

    return Directive(
        directive_id="dir_probe",
        session_id="s-probe",
        timestamp=0.0,
        action=DirectiveAction.PAUSE,
        executable=False,
        requires_human=True,
        params={"source": "control-plane", "requires_human": True},
    )
