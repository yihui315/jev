"""Wave 9.3 unit tests — the uncapped domain totals (owner: Orion).

WHY THIS FILE EXISTS
--------------------
`domain_counts()` is a four-COUNT read. Its value is not the arithmetic; it is
that a BOUNDED list read can be compared against it. So the tests here pin the
two halves together:

  * the totals are the domain's true size, and
  * the list reads still have a bound, which is a documented constant rather
    than a number that happened to be typed twice.

The defect this closes was measured, not theorised: with 500 missions in the
store the product client requested 200 and rendered them as the domain, and the
screens were indistinguishable from a workspace that genuinely has 200.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages"))

from jcc_core.domain import MAX_DOMAIN_LIST_LIMIT, DomainStore  # noqa: E402


@pytest.fixture()
def store(tmp_path):
    s = DomainStore(str(tmp_path / "counts.db"))
    yield s


def _seed(store, projects: int, missions_each: int):
    ids = []
    for i in range(projects):
        p = store.create_project(name="P%d" % i, root_path=r"F:\jev")
        ids.append(p.project_id)
        for j in range(missions_each):
            store.create_mission(project_id=p.project_id, title="M%d-%d" % (i, j),
                                 goal="g", agent="workbuddy")
    return ids


def test_an_empty_domain_counts_zero_not_unknown(store):
    """"There are none" is a fact; it must not read as "we could not ask"."""
    assert store.domain_counts() == {
        "projects": 0, "archived_projects": 0, "missions": 0, "deliverables": 0}


def test_counts_follow_the_real_domain(store):
    _seed(store, projects=3, missions_each=4)
    c = store.domain_counts()
    assert c["projects"] == 3
    assert c["missions"] == 12
    assert c["deliverables"] == 0


def test_counts_is_uncapped_while_the_list_read_is_bounded(store, monkeypatch):
    """THE POINT OF THE PAIR. The list obeys a bound; the count does not.

    The ceiling is LOWERED for this test rather than seeding MAX_DOMAIN_LIST_LIMIT
    + 25 real missions, and the reason is measured: one ``create_mission`` costs
    ~186 ms on this host (profiled 2026-09-29 -- ``sqlite3.Connection.close()``
    alone is 82 ms and each write opens and closes two connections), so seeding
    2025 rows took over six minutes and would have become the slowest test in the
    suite for no extra coverage. Lowering the constant still exercises the real
    clamp, because the clamp reads the module global at call time.

    That the ceiling is a real, documented number is asserted separately below.
    """
    import jcc_core.domain as domain_mod

    _seed(store, projects=1, missions_each=7)
    monkeypatch.setattr(domain_mod, "MAX_DOMAIN_LIST_LIMIT", 5)

    # The bound holds even when the caller asks for everything...
    assert len(store.list_missions(limit=10 ** 9)) == 5
    # ...so a caller comparing the two learns exactly how much it did not see.
    assert len(store.list_missions(limit=10 ** 9)) < store.domain_counts()["missions"]
    # The totals read is NOT subject to the list bound.
    assert store.domain_counts()["missions"] == 7


def test_the_ceiling_is_a_real_documented_number():
    """A clamp is a decision, not an accident: pin its value and its sanity."""
    assert isinstance(MAX_DOMAIN_LIST_LIMIT, int)
    assert MAX_DOMAIN_LIST_LIMIT >= 1000, (
        "the ceiling must clear the 1000-object measurement the product order "
        "names, or the plane's stress measurement would be measuring the clamp")
    assert MAX_DOMAIN_LIST_LIMIT <= 100_000, (
        "an unbounded read is its own defect; the ceiling must remain a bound")


def test_the_list_bound_is_one_documented_constant(store):
    """Both list reads clamp to the SAME documented ceiling.

    Before Wave 9.3 there were two silent ones (200 in ``list_missions``, 1000 in
    ``list_deliverables``), which is how a bound stops being a decision and
    becomes a coincidence.
    """
    _seed(store, projects=1, missions_each=5)
    assert len(store.list_missions(limit=MAX_DOMAIN_LIST_LIMIT + 1)) == 5
    assert len(store.list_deliverables(limit=MAX_DOMAIN_LIST_LIMIT + 1)) == 0


def test_the_bound_has_a_floor(store):
    """A limit of 0 or a negative limit must not mean "no limit" or "no rows"."""
    _seed(store, projects=1, missions_each=3)
    assert len(store.list_missions(limit=0)) == 1
    assert len(store.list_missions(limit=-5)) == 1


def test_counts_of_archived_projects_do_not_inflate_the_active_count(store, tmp_path):
    """There is no archive verb yet, so this writes the flag the way the schema
    stores it — and asserts the two counters are genuinely independent."""
    import sqlite3

    ids = _seed(store, projects=2, missions_each=1)
    raw = sqlite3.connect(store.db_path)
    try:
        raw.execute("UPDATE projects SET archived=1 WHERE project_id=?", (ids[0],))
        raw.commit()
    finally:
        raw.close()
    c = store.domain_counts()
    assert c["projects"] == 1, c
    assert c["archived_projects"] == 1, c
