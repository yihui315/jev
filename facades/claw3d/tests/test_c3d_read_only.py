"""S-07 — read-only enforcement tests for the Claw3D facade.

Read-only is enforced by STRUCTURE (a guard that refuses every store writer);
these tests prove the facade cannot write, even by accident.
"""
from __future__ import annotations

import hashlib
import os

import pytest

from claw3d import Claw3DFacade
from claw3d.read_only import (
    ReadOnlyViolation,
    StoreGuard,
    public_api,
)


def _db_fingerprint(db_path: str) -> str:
    blob = b""
    for suffix in ("", "-wal", "-shm"):
        f = db_path + suffix
        if os.path.exists(f):
            with open(f, "rb") as fh:
                blob += fh.read()
    return hashlib.sha256(blob).hexdigest()


def test_facade_advertises_no_mutating_api(facade):
    assert facade.mutating_api == ()
    writers = {
        "upsert_session", "add_event", "add_evidence", "add_assessment",
        "add_directive", "append_audit", "mutate", "steer", "pause",
        "retry", "finish", "escalate", "quarantine", "delete_session", "write",
        "dispatch", "apply_change",
    }
    public = set(public_api(facade))
    for name in writers:
        if name in public:
            with pytest.raises(ReadOnlyViolation):
                getattr(facade, name)()


def test_store_guard_refuses_writers(store):
    guard = StoreGuard.wrap(store)
    for writer in ("upsert_session", "add_event", "add_evidence",
                   "add_assessment", "add_directive", "append_audit"):
        with pytest.raises(ReadOnlyViolation):
            getattr(guard, writer)(None)
    with pytest.raises(ReadOnlyViolation):
        _ = guard._hidden
    with pytest.raises(ReadOnlyViolation):
        _ = guard.does_not_exist
    assert guard.list_sessions() == []


def test_facade_never_writes_through_project_scene(facade, store, rich_state):
    store.upsert_session(rich_state)
    fp_before = _db_fingerprint(store.db_path)
    counters = {n: 0 for n in ("upsert_session", "add_event", "add_evidence",
                               "add_assessment", "add_directive", "append_audit")}

    def _stub(name):
        def _fn(*_a, **_kw):
            counters[name] += 1
        return _fn

    for n in counters:
        setattr(store, n, _stub(n))

    facade.project_scene()
    facade.get_node(rich_state.session_id)

    fp_after = _db_fingerprint(store.db_path)
    assert fp_before == fp_after, "facade changed the SQLite store on disk"
    assert all(v == 0 for v in counters.values()), \
        f"facade reached a store writer: {counters}"


def test_mutating_entry_point_refuses(facade):
    with pytest.raises(ReadOnlyViolation):
        facade.apply_change(session_id="x")
    for alias in ("mutate", "steer", "pause", "retry", "finish",
                  "escalate", "quarantine", "delete_session", "write", "dispatch"):
        with pytest.raises(ReadOnlyViolation):
            getattr(facade, alias)({})


def test_read_only_violation_is_runtime_error():
    assert issubclass(ReadOnlyViolation, RuntimeError)
