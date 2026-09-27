"""JEV COMMAND CENTER 2D Control UI (stop condition S-04).

A real web UI: a stdlib ``http.server`` bound to loopback that renders nine
routes from LIVE data read out of the running JCC control-plane API. There are
no fixture pages and no placeholder values -- if the API has nothing to say about
a field, the screen says so instead of inventing a number.

    Agent Event -> Adapter -> AEP -> StateStore -> Evidence -> Jev ->
    deterministic Policy -> signed/inert Directive -> Audit Tape -> THIS UI

Every value on every screen is either read from an API response, read from the
real configuration file (the control plane exposes no config endpoint, so
``Settings`` and the parts of ``Dashboard`` that are configuration (agent
capabilities, budget limits, provider mode) go through ``jcc_core.config``), or
-- for the ``/v1.1`` activation screen -- read from a frozen release artifact
that is itself produced from source (see :mod:`control_ui.v1_1`). Either way
there is exactly one source and it is the real one.

The eighth v1.0 screen set (``dashboard`` through ``health``) is the set S-04's
acceptance evidence covers. ``/v1.1`` is additional: it is exercised over real
HTTP by ``tests/test_v1_1_ui.py`` but it is deliberately NOT in
``acceptance.py``'s ``SCREENS``, because that list is mirrored by
``CONTROL_UI_SCREENS`` in the tracked release gate and extending a gate's claimed
evidence set is an operator decision, not a side effect of adding a screen. The
gap is recorded rather than closed silently.

Launch (loopback only, OS-assigned port so two instances can never collide):

    F:/jev/.venv/Scripts/python -m control_ui

or point it at an already-running control plane:

    JCC_API_BASE=http://127.0.0.1:4778 F:/jev/.venv/Scripts/python -m control_ui
"""
from __future__ import annotations

__all__ = [
    "api_client",
    "bootstrap",
    "model",
    "paths",
    "render",
    "server",
    "v1_1",
]
