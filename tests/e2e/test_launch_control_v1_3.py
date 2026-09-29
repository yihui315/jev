"""Wave 9.2 API tests — the mission launch CONTROL PLANE over REAL HTTP.

Runs against the in-process ``api_server`` fixture (a real uvicorn app, isolated
temp DB, real control token), so these measure the deployed surface rather than
the module in isolation.

Scope, stated so it cannot drift
--------------------------------
These tests deliberately exercise the PREVIEW route and the token gate. They do
NOT commit a launch: the in-process app is constructed with the PRODUCTION
template registry, whose head resolves to the real WorkBuddy CLI on this host, so
a commit here would spawn a real agent process from inside the test suite. That
belongs to a deliberate, operator-initiated run — not to `pytest`. The commit
path is covered by ``packages/jcc_core/tests/test_launch_control_v1_3.py``, which
uses a test template and a real child process.

The token used is ``api_server.token``, taken from the fixture, rather than
importing the name out of ``conftest``: there is no ``__init__.py`` under
``tests/``, so every conftest competes for the bare module name ``conftest`` and
an import by name is correct only while the loader's directory order happens to
favour it.
"""
from __future__ import annotations

import http.client
import json


def _get(api_server, path):
    conn = http.client.HTTPConnection("127.0.0.1", api_server.port, timeout=15)
    conn.request("GET", path)
    r = conn.getresponse()
    body = r.read().decode("utf-8")
    conn.close()
    try:
        return r.status, json.loads(body)
    except ValueError:
        return r.status, body


def _post(api_server, path, body, token):
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["X-JCC-Token"] = token
    conn = http.client.HTTPConnection("127.0.0.1", api_server.port, timeout=20)
    conn.request("POST", path, json.dumps(body), headers)
    r = conn.getresponse()
    raw = r.read().decode("utf-8")
    conn.close()
    try:
        return r.status, json.loads(raw)
    except ValueError:
        return r.status, raw


def _project_and_mission(api_server, *, root_path="", agent="workbuddy"):
    body = {"name": "W9.2"}
    if root_path:
        body["root_path"] = root_path
    st, proj = _post(api_server, "/api/v1/domain/projects", body, api_server.token)
    assert st in (200, 201), (st, proj)
    st, miss = _post(api_server, "/api/v1/domain/missions",
                     {"project_id": proj["project_id"], "title": "M",
                      "goal": "say hi", "agent": agent}, api_server.token)
    assert st in (200, 201), (st, miss)
    return proj, miss


# --- the gate ---------------------------------------------------------------

def test_launch_request_and_commit_require_the_control_token(api_server):
    _proj, miss = _project_and_mission(api_server)
    mid = miss["mission_id"]
    for path in ("/api/v1/domain/missions/%s/launch-request" % mid,
                 "/api/v1/domain/missions/%s/launch-commit" % mid):
        st, _ = _post(api_server, path, {}, None)
        # The control guard's refusal code, not a product code.
        assert st in (401, 403), (path, st)
        st_bad, _doc = _post(api_server, path, {}, "wrong-token")
        assert st_bad in (401, 403), (path, st_bad)
        assert st_bad == st, (path, "the two refusal paths disagree")


# --- preview ---------------------------------------------------------------

def test_launch_request_previews_without_spawning(api_server, tmp_path):
    proj, miss = _project_and_mission(api_server, root_path=str(tmp_path))
    st, doc = _post(api_server,
                    "/api/v1/domain/missions/%s/launch-request" % miss["mission_id"],
                    {}, api_server.token)
    assert st == 200, doc
    assert doc["committed"] is False
    assert doc["actuation"] is False
    # NOTHING was started, and the payload says so by omission rather than by a
    # `spawned: false` that would imply it tried.
    assert "spawned" not in doc
    assert "pid" not in doc
    assert doc["preview"]["shell"] is False
    assert doc["preview"]["actuation"] is False
    # The directive is real, signed, and bound to THIS mission.
    d = doc["directive"]
    assert d["signed"] is True and d["signature"].startswith("sha256=")
    assert d["session_id"] == miss["mission_id"]
    assert d["params"]["capability"] == "USER_INITIATED_MISSION_LAUNCH"
    assert d["human_approval"]
    assert 0 < d["ttl_s"] <= 120
    # The goal is one argv slot, last, and never a command.
    argv = doc["preview"]["argv"]
    assert argv[-1] == "say hi"
    assert doc["preview"]["argv_goal_slot"] == len(argv) - 1


def test_launch_request_reports_the_capability_it_honours(api_server, tmp_path):
    proj, miss = _project_and_mission(api_server, root_path=str(tmp_path))
    _, doc = _post(api_server,
                   "/api/v1/domain/missions/%s/launch-request" % miss["mission_id"],
                   {}, api_server.token)
    assert doc["capability"] == "USER_INITIATED_MISSION_LAUNCH"
    assert doc["policy_version"] == "p3.0.0"
    assert doc["capability_version"].startswith("sha256:")
    assert doc["checks_ok"] is True
    assert doc["command_buildable"] is True
    assert doc["would_accept"] is True
    assert doc["would_reason"] == "ok"


def test_a_project_with_no_root_at_all_cannot_even_be_previewed(api_server):
    """No root anywhere => the request cannot be BUILT, so it is a 409.

    Deliberately a different answer from the next test, which has a root that is
    merely absent from disk: that one CAN be built and previewed, and says so in
    the preview. Collapsing the two into one "cannot launch" would lose which
    half failed -- and the operator's fix is different in each case.
    """
    proj, miss = _project_and_mission(api_server, root_path="")
    st, doc = _post(api_server,
                    "/api/v1/domain/missions/%s/launch-request" % miss["mission_id"],
                    {}, api_server.token)
    assert st == 409, (st, doc)
    assert doc["detail"]["code"] == "PROJECT_ROOT_MISSING"


def test_preview_is_honest_when_the_command_cannot_be_built(api_server, tmp_path):
    """A root that is named but GONE must not preview as launchable."""
    gone = str(tmp_path / "does-not-exist")
    proj, miss = _project_and_mission(api_server, root_path=gone)
    st, doc = _post(api_server,
                    "/api/v1/domain/missions/%s/launch-request" % miss["mission_id"],
                    {}, api_server.token)
    assert st == 200, doc
    assert doc["checks_ok"] is True          # the nine checks themselves pass
    assert doc["command_buildable"] is False  # but the command cannot be built
    assert "PROJECT_ROOT_MISSING" in doc["preview_problems"]
    # The single answer the UI reads is consistent with the reason it shows: the
    # two are composed, never two independent truth claims.
    assert doc["would_accept"] is False
    assert doc["would_reason"].startswith("COMMAND_NOT_BUILDABLE:")


def test_a_model_token_that_is_not_a_token_is_refused(api_server, tmp_path):
    proj, miss = _project_and_mission(api_server, root_path=str(tmp_path))
    st, doc = _post(api_server,
                    "/api/v1/domain/missions/%s/launch-request" % miss["mission_id"],
                    {"model": "--permission-mode=bypassPermissions"}, api_server.token)
    assert st == 409, (st, doc)
    assert doc["detail"]["code"] == "INVALID_MODEL"


def test_unknown_mission_is_404_on_both_routes(api_server):
    for path in ("/api/v1/domain/missions/mis-nope/launch-request",
                 "/api/v1/domain/missions/mis-nope/launch-commit"):
        st, doc = _post(api_server, path, {}, api_server.token)
        assert st == 404, (path, st, doc)


def test_an_agent_with_no_template_is_refused(api_server, tmp_path):
    proj, miss = _project_and_mission(api_server, root_path=str(tmp_path), agent="hermes")
    st, doc = _post(api_server,
                    "/api/v1/domain/missions/%s/launch-request" % miss["mission_id"],
                    {}, api_server.token)
    assert st == 409, (st, doc)
    assert doc["detail"]["code"] == "NO_TEMPLATE_FOR_AGENT"


# --- the probe now answers BOTH questions, separately ----------------------

def test_probe_exposes_native_launch_capability(api_server):
    st, doc = _get(api_server, "/api/v1/domain/launch/probe")
    assert st == 200, doc
    # The attach/observe answer is still there and unchanged...
    assert "workbuddy" in doc
    assert doc["actuation"] is False
    # ...and the START answer is a SEPARATE, MEASURED reading.
    nl = doc["native_launch"]
    assert nl["capability"] == "USER_INITIATED_MISSION_LAUNCH"
    assert nl["autonomous_actuation"] is False
    assert nl["executability"] in (
        "LOCAL_SPAWN_READY", "HOST_ACTIVATION_PENDING", "UNKNOWN")
    assert nl["capability_version"].startswith("sha256:")
    # A capability that has not been measured end-to-end is never reported as
    # verified.
    if nl["last_measured"] is None:
        assert nl["end_to_end_verified"] is False


def test_no_new_open_get_route_was_added(api_server):
    """The launch plane added POST routes only; the GET surface is unchanged.

    This is the anti-drift check for ``open_get_endpoints``: if a GET had been
    added without declaring it, the declaration would be a lie.
    """
    st, health = _get(api_server, "/api/health")
    assert st == 200
    declared = set(health["open_get_endpoints"])
    assert "/api/v1/domain/launch/probe" in declared
    for post_only in ("/api/v1/domain/missions/{mid}/launch-request",
                      "/api/v1/domain/missions/{mid}/launch-commit"):
        assert post_only not in declared
