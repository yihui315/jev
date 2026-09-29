"""Wave 5 tests — deliverables: the PRODUCED vs VERIFIED distinction.

Owner: Metric. The property that matters is that a machine emitting an artifact
(PRODUCED) and a human accepting it (VERIFIED) are different claims -- so the
verdict is explicit, final, recorded, and the aggregate counts them separately.

Each test names its falsifier in the docstring.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from jcc_core import domain as D  # noqa: E402


@pytest.fixture()
def store(tmp_path):
    return D.DomainStore(str(tmp_path / "jcc_w5.db"))


def _mission_with_artifacts(store, n=1, name_prefix="out"):
    p = store.create_project("P")
    m = store.create_mission(p.project_id, "M")
    arts = []
    for i in range(n):
        arts.append(store.add_artifact(m.mission_id, f"{name_prefix}-{i}",
                                       kind=D.ArtifactKind.FILE, uri=f"F:/x/{i}"))
    return m, arts


# --- get_artifact: identity ---------------------------------------------------

def test_get_artifact_by_identity(store):
    """Falsifier: a deliverable must be addressable by id."""
    _, arts = _mission_with_artifacts(store, 1)
    got = store.get_artifact(arts[0].artifact_id)
    assert got is not None
    assert got.artifact_id == arts[0].artifact_id
    assert got.name == arts[0].name


def test_get_unknown_artifact_is_none_not_fabricated(store):
    """Falsifier: an unknown id returns None, never a made-up row."""
    assert store.get_artifact("art_does_not_exist") is None


# --- verify_artifact: explicit, final, recorded -------------------------------

def test_verify_sets_status_and_records_who(store):
    """Falsifier: acceptance must name a human and be recorded on the artifact."""
    _, arts = _mission_with_artifacts(store, 1)
    a = store.verify_artifact(arts[0].artifact_id, D.ArtifactStatus.VERIFIED,
                              decided_by="alice", note="looks good")
    assert a.status == D.ArtifactStatus.VERIFIED
    assert a.provenance["verified_by"] == "alice"
    assert a.provenance["verify_note"] == "looks good"
    assert isinstance(a.provenance["verified_at"], float)


def test_verify_reject_is_recorded(store):
    """Falsifier: a rejection is a verdict too, distinct from 'never decided'."""
    _, arts = _mission_with_artifacts(store, 1)
    a = store.verify_artifact(arts[0].artifact_id, D.ArtifactStatus.REJECTED,
                              decided_by="bob")
    assert a.status == D.ArtifactStatus.REJECTED
    assert a.provenance["verified_by"] == "bob"


def test_verify_unknown_artifact_raises(store):
    """Falsifier: a verdict on a non-existent deliverable must fail, not no-op."""
    with pytest.raises(ValueError):
        store.verify_artifact("art_nope", D.ArtifactStatus.VERIFIED, decided_by="x")


def test_verify_cannot_be_produced(store):
    """Falsifier: PRODUCED is not a verdict; you cannot 'verify' back to raw."""
    _, arts = _mission_with_artifacts(store, 1)
    with pytest.raises(ValueError):
        store.verify_artifact(arts[0].artifact_id, D.ArtifactStatus.PRODUCED,
                              decided_by="x")


def test_verdict_is_final(store):
    """Falsifier: an already-decided artifact cannot be re-decided."""
    _, arts = _mission_with_artifacts(store, 1)
    store.verify_artifact(arts[0].artifact_id, D.ArtifactStatus.VERIFIED,
                          decided_by="a")
    with pytest.raises(ValueError):
        store.verify_artifact(arts[0].artifact_id, D.ArtifactStatus.REJECTED,
                              decided_by="b")


def test_superseded_cannot_be_decided_either(store):
    """Falsifier: SUPERSEDED is terminal too (it was replaced)."""
    _, arts = _mission_with_artifacts(store, 1)
    # drive it to SUPERSEDED via the same status field
    with store._lock:  # noqa: SLF001 -- test-only direct state set
        conn = store._conn()
        try:
            conn.execute("UPDATE artifacts SET status=? WHERE artifact_id=?",
                         (D.ArtifactStatus.SUPERSEDED.value, arts[0].artifact_id))
            conn.commit()
        finally:
            conn.close()
    with pytest.raises(ValueError):
        store.verify_artifact(arts[0].artifact_id, D.ArtifactStatus.VERIFIED,
                              decided_by="a")


# --- deliverable_counts: the honest aggregate ---------------------------------

def test_counts_separate_produced_from_verified(store):
    """Falsifier: the headline must be `verified`, and rejected is not accepted."""
    _, arts = _mission_with_artifacts(store, 4)
    store.verify_artifact(arts[0].artifact_id, D.ArtifactStatus.VERIFIED, decided_by="a")
    store.verify_artifact(arts[1].artifact_id, D.ArtifactStatus.REJECTED, decided_by="a")
    # arts[2], arts[3] stay PRODUCED
    c = store.deliverable_counts()
    assert c["verified"] == 1
    assert c["rejected"] == 1
    # produced = the 2 untouched + the 1 rejected (emitted but not accepted)
    assert c["produced"] == 3
    assert c["total"] == 4


def test_counts_empty_store_is_zero(store):
    """Falsifier: no data must yield zeros, never a sample."""
    c = store.deliverable_counts()
    assert c == {"produced": 0, "verified": 0, "rejected": 0, "total": 0}


def test_home_summary_carries_honest_deliverables(store):
    """Falsifier: Home must not report all artifacts as 'deliverables'."""
    _, arts = _mission_with_artifacts(store, 2)
    store.verify_artifact(arts[0].artifact_id, D.ArtifactStatus.VERIFIED, decided_by="a")
    h = store.home_summary()
    assert h["deliverables"]["verified"] == 1
    assert h["deliverables"]["produced"] == 1
    assert h["deliverables"]["total"] == 2
    # the legacy scalar still counts everything, but the honest object is present
    assert h["recent_deliverables"] == 2


# --- list_deliverables: server-side view + mission title ----------------------

def test_list_deliverables_joins_mission_title(store):
    """Falsifier: the server-side view must carry the mission title (no client fan-out)."""
    m, arts = _mission_with_artifacts(store, 2)
    rows = store.list_deliverables()
    assert len(rows) == 2
    assert all(r["mission_title"] == "M" for r in rows)
    assert all(r["mission_id"] == m.mission_id for r in rows)


def test_list_deliverables_filters_by_status(store):
    """Falsifier: status=VERIFIED must exclude unverified rows."""
    _, arts = _mission_with_artifacts(store, 3)
    store.verify_artifact(arts[0].artifact_id, D.ArtifactStatus.VERIFIED, decided_by="a")
    verified = store.list_deliverables(status=D.ArtifactStatus.VERIFIED)
    assert len(verified) == 1
    assert verified[0]["artifact_id"] == arts[0].artifact_id


def test_list_deliverables_filters_by_mission(store):
    """Falsifier: mission filter must actually filter."""
    m1, a1 = _mission_with_artifacts(store, 1)
    p2 = store.create_project("P2")
    m2 = store.create_mission(p2.project_id, "M2")
    store.add_artifact(m2.mission_id, "other")
    rows = store.list_deliverables(mission_id=m1.mission_id)
    assert len(rows) == 1
    assert rows[0]["artifact_id"] == a1[0].artifact_id


def test_list_deliverables_respects_limit(store):
    """Falsifier: limit must bound the result."""
    _mission_with_artifacts(store, 5)
    assert len(store.list_deliverables(limit=2)) == 2
