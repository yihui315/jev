"""P3 (CR-H5) and P4 (CR-H6) -- the control-token gate must report and refuse.

Spec: ``docs/P3_P4_SPEC.md``. This file is the executable form of §2 and §3.

P3 -- ``/api/health`` did not say whether a control token was CONFIGURED. Every
mutating route is fail-closed without one (401/403, measured: 27 routes) and that
is correct, but it left an operator unable to distinguish "deliberately locked"
from "I set the token and it never took". Nothing reported it, so a silent
misconfiguration was invisible.

P4 -- ``hmac.compare_digest`` raises ``TypeError`` on a ``str`` holding non-ASCII
code points, so a token like ``中文令牌`` escaped the 403 and became an unhandled
exception. NATURE: availability/robustness, NOT confidentiality or integrity --
the request is refused either way.

WHY THESE TESTS CALL THE ASGI APP DIRECTLY INSTEAD OF USING ``TestClient``
------------------------------------------------------------------------
Measured on this host, and it is the reason this file looks the way it does::

    httpx/_models.py: UnicodeEncodeError: 'ascii' codec can't encode characters
    in position 0-3: ordinal not in range(128)

``TestClient`` encodes every header as ASCII before the request leaves the test
process, so a non-ASCII token cannot be SENT through it at all. A P4 test written
with ``TestClient`` therefore fails at the transport layer, never reaches
``require_control_token``, and would "pass" for entirely the wrong reason. That
is the same shape as the two falsifiers this round rejected: a green that never
exercised the thing it claimed to measure. So the non-ASCII case drives a
hand-built ASGI scope with raw bytes, which is also how the security member
originally observed the ``TypeError``.

The ASCII cases still go through ``TestClient`` -- it handles them fine, and using
the ordinary client keeps the "normal" path on the ordinary client.

FALSIFIABILITY (both directions were executed, not assumed)
---------------------------------------------------------
With ``server.py`` at HEAD (pre-fix), measured through this same ASGI scope::

    non-ASCII     -> RAISED TypeError: comparing strings with non-ASCII ...
    correct ASCII -> HTTP [200]

With the fix applied::

    non-ASCII     -> HTTP [403]
    correct ASCII -> HTTP [200]
    wrong ASCII   -> HTTP [403]
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jcc_core.actuator import Actuator                      # noqa: E402
from jcc_core.config import Config, ServerConfig            # noqa: E402
from jcc_core.core import CoreEngine                         # noqa: E402
from jcc_core.providers import build_provider               # noqa: E402
from jcc_core.server import create_app                       # noqa: E402
from jcc_core.state_store import StateStore                 # noqa: E402

#: Deliberately non-ASCII, and deliberately a plausible token shape. "中文令牌"
#: is not a token anyone would configure; the point is only that the COMPARISON
#: must not raise when a caller sends one.
NON_ASCII_TOKEN = "中文令牌"
TOKEN = "p34-p45-acceptance-token"


def _build(tmp_path: Path, token: Optional[str]) -> Tuple[Any, Config]:
    """A real app over a real store, with the token set or deliberately absent."""
    if token is None:
        os.environ.pop("JCC_CONTROL_TOKEN", None)
    else:
        os.environ["JCC_CONTROL_TOKEN"] = token
    cfg = Config(server=ServerConfig(),
                 state_store_db_path=str(tmp_path / "health.db"))
    store = StateStore(cfg.state_store_db_path)
    engine = CoreEngine(store, build_provider(cfg), Actuator(enabled=False), cfg)
    return create_app(engine, cfg), cfg


@pytest.fixture()
def restore_env():
    """Put JCC_CONTROL_TOKEN back exactly as it was.

    These tests mutate the process environment because that IS the gate's input
    (``os.environ.get(config.control_token_env)``) and a test that cannot set the
    input cannot measure the gate. Restoring it keeps the mutation from leaking
    into whichever suite runs next in this process -- an ordering-dependent
    failure here would be indistinguishable from a real one.
    """
    previous = os.environ.get("JCC_CONTROL_TOKEN")
    yield
    if previous is None:
        os.environ.pop("JCC_CONTROL_TOKEN", None)
    else:
        os.environ["JCC_CONTROL_TOKEN"] = previous


# --------------------------------------------------------------------------- #
# P3 / CR-H5 -- health exposes control_token_configured, and only the boolean
# --------------------------------------------------------------------------- #
def test_health_reports_control_token_not_configured(tmp_path, restore_env):
    """No token in the environment -> the field exists and is False.

    This is the case that motivated P3: on a host like this one the field is
    False, every mutating route returns 401, and before this change nothing
    anywhere said so.
    """
    app, _cfg = _build(tmp_path, None)
    body = TestClient(app).get("/api/health").json()
    assert "control_token_configured" in body, (
        "health does not expose control_token_configured at all, so an operator "
        "cannot tell a locked control plane from a configured one. Fields: %r"
        % sorted(body))
    assert body["control_token_configured"] is False, (
        "no token is set, so the field must be False, got %r"
        % (body["control_token_configured"],))


def test_health_reports_control_token_configured_and_never_leaks_it(
        tmp_path, restore_env):
    """A configured token -> True, and the VALUE appears nowhere in the body.

    The second half is the load-bearing half. A boolean is a legitimate thing to
    publish on an open GET; the token itself would not be, and a future edit that
    helpfully returned the value "for debugging" would pass a truthiness-only
    test. So this asserts on the whole serialised body, not on the field.
    """
    app, _cfg = _build(tmp_path, TOKEN)
    raw = TestClient(app).get("/api/health")
    body = raw.json()
    assert body.get("control_token_configured") is True, (
        "a CONFIGURED token reported as not configured -- this false negative "
        "would make an operator delete a working token")
    assert TOKEN not in raw.text, (
        "the token VALUE appears in the health body. The field must stay a "
        "boolean; the value is not part of this contract.")


def test_control_token_configured_tracks_the_environment(tmp_path, restore_env):
    """The field must not be cached at import or at app-construction time.

    A boolean computed once when the module loaded would report the state of
    whatever the environment held at import, which is a different question from
    "is it configured now" -- and would go stale the moment an operator exports
    the variable in a shell.
    """
    app, _cfg = _build(tmp_path, None)
    client = TestClient(app)
    assert client.get("/api/health").json()["control_token_configured"] is False
    os.environ["JCC_CONTROL_TOKEN"] = TOKEN
    assert client.get("/api/health").json()["control_token_configured"] is True
    os.environ.pop("JCC_CONTROL_TOKEN", None)
    assert client.get("/api/health").json()["control_token_configured"] is False


# --------------------------------------------------------------------------- #
# P4 / CR-H6 -- a non-ASCII token is REFUSED, and refusing is unchanged
# --------------------------------------------------------------------------- #
async def _asgi_post(app, path: str, token: Optional[bytes],
                     authorization: Optional[bytes] = None) -> Optional[int]:
    """Drive the ASGI app directly with RAW header bytes; return the status.

    ``token`` goes in ``X-JCC-Token``; ``authorization`` in ``Authorization``.
    At least one must be given, since the point is to exercise a header the
    httpx client cannot transmit.

    Returns None if the app never sent a response start -- which is what an
    unhandled exception looks like from here, and is the shape this test exists
    to catch. It is raised rather than returned so the failure message names the
    real problem instead of reporting a bare None.
    """
    headers = [(b"host", b"127.0.0.1"),
               (b"content-type", b"application/json")]
    if token is not None:
        headers.append((b"x-jcc-token", token))
    if authorization is not None:
        headers.append((b"authorization", b"Bearer " + authorization))
    scope: Dict[str, Any] = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "path": path, "raw_path": path.encode("utf-8"),
        "query_string": b"", "root_path": "", "scheme": "http",
        "server": ("127.0.0.1", 4779), "client": ("127.0.0.1", 5555),
        "headers": headers,
    }
    sent: List[Dict[str, Any]] = []

    async def receive():
        return {"type": "http.request", "body": b'{"reason":"probe"}',
                "more_body": False}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    for message in sent:
        if message["type"] == "http.response.start":
            return int(message["status"])
    return None


def test_p4_non_ascii_token_is_refused_and_does_not_raise(tmp_path, restore_env):
    """THE P4 CASE. Non-ASCII token -> 403, never an exception and never a 500.

    Measured at HEAD (pre-fix), this same scope raised
    ``TypeError: comparing strings with non-ASCII characters is not supported``
    from inside ``require_control_token``, which surfaced as a 500. The request
    was refused either way, so confidentiality and integrity were never at risk;
    what was broken was that "refused" arrived as an unhandled error.
    """
    app, _cfg = _build(tmp_path, TOKEN)
    status = asyncio.run(
        _asgi_post(app, "/api/pause", NON_ASCII_TOKEN.encode("utf-8")))
    assert status == 403, (
        "a non-ASCII token must be REFUSED with 403. Got %r -- an uncaught "
        "TypeError here means the endpoint 500s instead of refusing, which is "
        "the CR-H6 defect." % (status,))


def test_p4_ascii_paths_are_unchanged_by_the_fix(tmp_path, restore_env):
    """POSITIVE CONTROL: a correct token still passes, a wrong one still 403s.

    Without this, "always return 403" would satisfy the non-ASCII case and turn a
    robustness fix into a lockout -- refusing everything is the failure mode that
    satisfies a negative-only test suite. This is the CR-H1 positive-control
    lesson applied here.
    """
    app, _cfg = _build(tmp_path, TOKEN)
    client = TestClient(app)
    ok = client.post("/api/pause", json={"reason": "x"},
                     headers={"X-JCC-Token": TOKEN})
    assert ok.status_code == 200, (
        "a CORRECT token was refused (%s): the fix must not change the accept "
        "path, only the comparison's input type" % ok.status_code)
    bad = client.post("/api/pause", json={"reason": "x"},
                      headers={"X-JCC-Token": "wrong-token-entirely"})
    assert bad.status_code == 403, (
        "a WRONG token was not refused (%s)" % bad.status_code)


def test_p4_bearer_authorization_path_is_also_covered(tmp_path, restore_env):
    """The `Authorization: Bearer` branch reaches the SAME comparison.

    Added after risk1-scope asked whether the ASCII path was covered by the
    hand-built ASGI scope. It is -- but that question exposed a real gap: the gate
    accepts the token from EITHER header, and the Bearer branch is the one a
    normal API client uses. The fix rewrote the line that consumes it
    (``presented = authorization.split(" ", 1)[1]`` feeds the encoded compare),
    so leaving that branch untested meant a mistake there would not be caught.

    Both the accept and the refuse direction are asserted, and a wrong Bearer
    token must still be 403 -- a rewrite of the comparison is exactly the kind
    of edit that can quietly turn "Bearer" into "accepted".
    """
    app, _cfg = _build(tmp_path, TOKEN)
    client = TestClient(app)
    ok = client.post("/api/pause", json={"reason": "x"},
                     headers={"Authorization": "Bearer " + TOKEN})
    assert ok.status_code == 200, (
        "a correct Bearer token was refused (%s) -- the Bearer branch feeds the "
        "same comparison and must still accept" % ok.status_code)
    bad = client.post("/api/pause", json={"reason": "x"},
                      headers={"Authorization": "Bearer wrong-token-entirely"})
    assert bad.status_code == 403, (
        "a wrong Bearer token was NOT refused (%s) -- the Bearer branch must be "
        "held to the same standard as the X-JCC-Token branch" % bad.status_code)
    # And a non-ASCII Bearer token must be refused, not raise, for the same
    # reason as the header branch: it arrives at the identical comparison.
    status = asyncio.run(
        _asgi_post(app, "/api/pause", None,
                   authorization=NON_ASCII_TOKEN.encode("utf-8")))
    assert status == 403, (
        "a non-ASCII Bearer token must be refused with 403, got %r" % (status,))



def test_p4_unconfigured_token_is_still_401_not_403(tmp_path, restore_env):
    """The 401-vs-403 distinction must survive.

    401 means "no token is configured on this host"; 403 means "you presented the
    wrong one". Collapsing them would destroy the exact signal P3 exists to make
    visible, and the short-circuit that produces 401 is the part of the gate a
    careless refactor drops.
    """
    app, _cfg = _build(tmp_path, None)
    status = asyncio.run(_asgi_post(app, "/api/pause", TOKEN.encode("utf-8")))
    assert status == 401, (
        "with no token configured the gate must answer 401 (fail-closed, "
        "unconfigured), got %r" % (status,))


def test_p4_ws_call_site_is_also_encoded(tmp_path, restore_env):
    """Pins the SECOND call site, structurally.

    A source assertion, not a behaviour assertion: driving a real WebSocket
    handshake with a non-ASCII subprotocol is not worth the machinery here, but
    leaving one of the two sites un-encoded is exactly the "inconsistent
    hardening" the spec forbids, and it is invisible to the three HTTP cases
    above. If someone reverts :1493 only, this fails and the HTTP tests still
    pass -- which is the whole reason it exists.
    """
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text(
        encoding="utf-8")
    raw_calls = [ln for ln in src.splitlines()
                 if "compare_digest(" in ln and ".encode(" not in ln
                 and "hmac.compare_digest(token, expected)" in ln]
    assert not raw_calls, (
        "server.py still compares a raw str in the WebSocket path: %r -- both "
        "call sites must be encoded (spec §3.3)" % raw_calls)
    assert "token.encode(" in src and "presented.encode(" in src, (
        "both call sites must encode before comparing (spec §3.4)")
