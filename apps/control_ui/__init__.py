"""JEV COMMAND CENTER 2D Control UI (stop condition S-04).

A real web UI: a stdlib ``http.server`` bound to loopback that renders eight
screens from LIVE data read out of the running JCC control-plane API. There are
no fixture pages and no placeholder values -- if the API has nothing to say about
a field, the screen says so instead of inventing a number.

    Agent Event -> Adapter -> AEP -> StateStore -> Evidence -> Jev ->
    deterministic Policy -> signed/inert Directive -> Audit Tape -> THIS UI

Every value on every screen is either read from an API response, or read from the
real configuration file (the control plane exposes no config endpoint, so
``Settings`` and the parts of ``Dashboard`` that are configuration (agent
capabilities, budget limits, provider mode) go through ``jcc_core.config``).
Either way there is exactly one source and it is the real one.

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
]
