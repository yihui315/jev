"""JCC Core — P1 control-plane package.

Implements the P1 skeleton of the JEV COMMAND CENTER local control plane:
Agent Event -> Adapter -> AEP -> Normalized Session State -> Evidence ->
Jev Assessment -> Deterministic Policy -> (Unsigned in P1) Directive ->
Actuator (disabled) -> Visual Projection.

See ADR-0002..0006. Actuation is disabled; the real Jev/adapter integrations
are UNVERIFIED stubs (P2/P3+).
"""
__version__ = "v1.2.1"
