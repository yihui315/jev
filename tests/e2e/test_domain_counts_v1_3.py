"""Wave 9.3 API tests — the domain totals read, over REAL HTTP (owner: Orion).

THE PROPERTY THAT MATTERS, WITH ITS FALSIFIER
---------------------------------------------
"With 5 missions in the store, asking for 2 returns 2 rows -- and the product can
tell that this is a partial view."

Before this wave it could not. The bounded list reads were presented as the
domain, so a truncated list and a complete one were indistinguishable on screen.
The falsifier is therefore not "the endpoint exists" but "the shortfall is
DETECTABLE": the totals read must report the real size while the bounded read
reports the bound, and the two must disagree exactly when they should.

Also pinned here: the read is OPEN (no token -- it joins the other read-only
projections) and it is a read (no state changes, so a second call is identical).
"""
from __future__ import annotations

import http.client
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from conftest import E2E_CONTROL_TOKEN  # noqa: E402

TOK = E2E_CONTROL_TOKEN
COUNTS_PATH = "/api/v1/domain/counts"


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


def _seed(api_server, projects=1, missions_each=5):
    """Create real state through the API. Returns (project_ids, mission_count)."""
    pids, total = [], 0
    for i in range(projects):
        st, proj = _post(api_server, "/api/v1/domain/projects",
                         {"name": "Counts P%d" % i, "root_path": r"F:\\jev"}, TOK)
        assert st == 200 and proj.get("project_id"), (st, proj)
        pids.append(proj["project_id"])
        for j in range(missions_each):
            st, m = _post(api_server, "/api/v1/domain/missions",
                          {"project_id": proj["project_id"],
                           "title": "Counts M%d-%d" % (i, j),
                           "goal": "prove the bound is visible",
                           "agent": "workbuddy", "permissions": ["fs.read"]}, TOK)
            assert st == 200, (st, m)
            total += 1
    return pids, total


def test_counts_is_open_and_declares_its_schema(api_server):
    """A read-only projection joins the open surface; it must not need a token."""
    st, body = _get(api_server, COUNTS_PATH)
    assert st == 200, st
    assert body.get("schema") == "jcc/v1.3-domain-counts/1"
    for key in ("projects", "archived_projects", "missions", "deliverables"):
        assert isinstance(body.get(key), int), (key, body.get(key))


def test_counts_matches_the_real_domain(api_server):
    """The totals must be the domain's, not the bounded read's."""
    _pids, created = _seed(api_server, projects=2, missions_each=4)

    st, counts = _get(api_server, COUNTS_PATH)
    assert st == 200
    assert counts["projects"] == 2, counts
    assert counts["missions"] == created == 8, counts

    # ...and the same numbers are visible through the list read when it is large
    # enough, so the two reads are measuring the same domain.
    st, rows = _get(api_server, "/api/v1/domain/missions?limit=1000")
    assert st == 200 and len(rows) == created


def test_a_truncated_list_is_DETECTABLE(api_server):
    """THE FALSIFIER. Bounded read says 2; totals say 5; the gap is the point.

    This is what a silent truncation destroys: before Wave 9.3 the client saw
    only `len(rows) == 2` and had no second number to compare it against.
    """
    _pids, created = _seed(api_server, projects=1, missions_each=5)
    assert created == 5

    st, rows = _get(api_server, "/api/v1/domain/missions?limit=2")
    assert st == 200 and len(rows) == 2, ("the bound must still bind", st, rows)

    st, counts = _get(api_server, COUNTS_PATH)
    assert st == 200
    assert counts["missions"] == 5, counts
    # The two reads disagree exactly when they should -- so "showing 2 of 5" is
    # something the product can now actually say.
    assert len(rows) < counts["missions"]


def test_counts_reports_archived_projects_separately(api_server):
    """Active and archived are different numbers; collapsing them would overstate
    the active workspace, which is the counter Home leads with."""
    _pids, _n = _seed(api_server, projects=1, missions_each=1)
    st, counts = _get(api_server, COUNTS_PATH)
    assert st == 200
    assert counts["projects"] == 1
    assert counts["archived_projects"] == 0
    assert counts["projects"] + counts["archived_projects"] >= counts["projects"]


def test_counts_reads_nothing_into_existence(api_server):
    """It is a READ. A second call must be byte-identical -- no lazy creation, no
    schema side effect that changes the numbers."""
    st, first = _get(api_server, COUNTS_PATH)
    assert st == 200
    st, second = _get(api_server, COUNTS_PATH)
    assert st == 200
    assert first == second


def test_counts_is_declared_on_the_open_surface(api_server):
    """/api/health declares the open GET surface; an undeclared open route is a
    gate failure (test_server_surface), so the declaration is checked here too."""
    st, health = _get(api_server, "/api/health")
    assert st == 200
    assert COUNTS_PATH in health.get("open_get_endpoints", []), \
        "the counts read must be DECLARED open, not merely open"
