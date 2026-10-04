"""§15 AEP end-to-end — the legs that were NOT already covered.

WHAT §15 ASKS FOR, AND WHERE EACH LEG LIVES
    §15 names six properties. Five of them already had real coverage before this
    file existed, and duplicating them here would create two sources of truth for
    the same claim. The map is written down so the §15 claim is auditable from one
    place:

      leg                          | already covered by
      -----------------------------|-----------------------------------------------
      dedupe (session_id,event_id) | test_state_store.py::test_dedup_event_and_delivery
      delivery dedupe              | same test (the delivery_id branch)
      session isolation            | test_p2_integration_smoke.py::
                                   |   test_receiver_enforces_session_isolation and
                                   |   test_four_agents_distinct_and_no_crosstalk...
      redaction                    | test_adapters_contract.py::
                                   |   test_secret_redaction_in_payload_and_evidence,
                                   |   test_p2_integration_smoke.py::
                                   |   test_no_secret_leakage_in_state_store_bytes
      cwd is never the identity    | test_adapters_contract.py::
                                   |   test_identity_is_session_id_not_cwd (positive)
                                   |   test_missing_session_id_raises_unidentifiable
                                   |   (cwd-only payload raises)
      restart recovery             | ** MISSING -- this file **
      unknown semantics            | ** MISSING -- this file **

WHY THE TWO MISSING LEGS MATTER MORE THAN THEY LOOK
    * Dedupe that only holds inside one process is not dedupe. JCC's whole reason
      for a state store rather than an in-memory dict is that the platform is
      restarted constantly (watchdogs, maintenance restarts, reboots), and an
      agent redelivering an event after a restart must not advance a session
      twice. Every existing dedupe test builds ONE store and never reopens it, so
      a regression that kept dedupe in memory would pass all of them.
    * "Unknown" is where a system quietly lies. The two bad endings are inventing
      a type (the event claims to be something it is not) and dropping the event
      (the pipeline goes silent). The tests below pin that the native name is
      RECORDED and the pipeline still completes.

Note on `cwd`: the rule is declared on the model itself
(``AEPEnvelope.cwd`` -- "metadata ONLY — never primary identity"), which is why
the negative test lives with the adapters. It is deliberately NOT re-asserted
here.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from jcc_core.actuator import Actuator
from jcc_core.config import Config
from jcc_core.core import CoreEngine
from jcc_core.providers.mock import DeterministicMockProvider
from jcc_core.adapters.workbuddy import WorkBuddyAdapter
from jcc_core.schemas import AEPEnvelope, EventType
from jcc_core.state_store import StateStore


def _cli(db_path: Path):
    """A (store, engine) pair. Calling this twice on one path IS a restart:
    ``StateStore`` opens a fresh connection per operation, so a new instance on
    the same file is a new process's view of the same state."""
    cfg = Config(state_store_db_path=str(db_path))
    store = StateStore(cfg.state_store_db_path)
    return store, CoreEngine(store, DeterministicMockProvider(), Actuator(enabled=False), cfg)


def _env(**kw) -> AEPEnvelope:
    base = dict(event_id="e1", delivery_id="d1", session_id="s1", agent="workbuddy")
    base.update(kw)
    return AEPEnvelope(**base)


# --------------------------------------------------------------------------- #
# Restart recovery
# --------------------------------------------------------------------------- #
def test_the_control_a_duplicate_within_one_process_is_deduped(tmp_path):
    """The baseline. Without this, a restart test that passes proves nothing
    about dedupe -- it could be passing because dedupe never worked at all."""
    _, engine = _cli(tmp_path / "jcc.db")
    first = engine.ingest(_env())
    second = engine.ingest(_env())

    assert "deduped" not in first
    assert second.get("deduped") is True


def test_event_dedupe_survives_a_restart(tmp_path):
    db = tmp_path / "jcc.db"
    _, engine1 = _cli(db)
    assert "deduped" not in engine1.ingest(_env())

    _, engine2 = _cli(db)
    out = engine2.ingest(_env())
    assert out.get("deduped") is True, (
        "the same (session_id, event_id) was accepted again after a restart; "
        "dedupe is living in memory, not in the store")


def test_delivery_dedupe_survives_a_restart(tmp_path):
    """A different event_id with an already-seen delivery_id is a redelivery."""
    db = tmp_path / "jcc.db"
    _, engine1 = _cli(db)
    assert "deduped" not in engine1.ingest(_env(event_id="e1", delivery_id="d1"))

    _, engine2 = _cli(db)
    out = engine2.ingest(_env(event_id="e2", delivery_id="d1"))
    assert out.get("deduped") is True, (
        "delivery_id dedupe did not survive the restart")


def test_a_redelivery_after_a_restart_does_not_advance_the_session(tmp_path):
    """The behavioural consequence, not just the flag.

    A deduped event that still bumped ``event_count`` or appended a second
    directive would be dedupe in name only.
    """
    db = tmp_path / "jcc.db"
    store1, engine1 = _cli(db)
    engine1.ingest(_env())
    before = store1.get_session("s1")
    assert before is not None and before.event_count == 1

    store2, engine2 = _cli(db)
    out = engine2.ingest(_env())
    assert out.get("deduped") is True

    after = store2.get_session("s1")
    assert after.event_count == 1, (
        "a redelivered event advanced the session: event_count=%d" % after.event_count)
    assert len(store2.get_directives("s1")) == 1, (
        "a redelivered event produced a second directive")


def test_the_audit_chain_still_verifies_across_a_restart(tmp_path):
    """The tape is the authoritative record; a restart must not break its chain."""
    db = tmp_path / "jcc.db"
    store1, engine1 = _cli(db)
    engine1.ingest(_env(event_id="e1", delivery_id="d1"))
    engine1.ingest(_env(event_id="e2", delivery_id="d2"))

    store2, engine2 = _cli(db)
    engine2.ingest(_env(event_id="e3", delivery_id="d3"))

    ok, count = store2.verify_audit()
    assert ok is True, "the audit hash chain did not verify after a restart"
    assert count == 3, count


def test_a_new_session_after_a_restart_is_not_confused_with_the_old_one(tmp_path):
    """Isolation across a restart: a different session_id is a different session."""
    db = tmp_path / "jcc.db"
    store1, engine1 = _cli(db)
    engine1.ingest(_env(session_id="s1"))

    store2, engine2 = _cli(db)
    out = engine2.ingest(_env(event_id="e9", delivery_id="d9", session_id="s2"))
    assert "deduped" not in out
    # list_sessions() returns plain dicts, like get_directives()
    assert sorted(s["session_id"] for s in store2.list_sessions()) == ["s1", "s2"]


# --------------------------------------------------------------------------- #
# Unknown semantics
# --------------------------------------------------------------------------- #
def test_an_unrecognised_native_event_type_is_recorded_as_unknown():
    """Neither invented nor dropped: UNKNOWN plus the native name, kept.

    Inventing a type would make the event claim to be something it is not;
    dropping it would make the pipeline go silent about real activity. The
    native string survives in ``payload['native_event']`` so a human can still
    see what arrived.
    """
    env = asyncio.run(WorkBuddyAdapter().normalize({
        "_source": "hook",
        "hook_event_name": "TotallyUnknownHookName",
        "session_id": "S-unknown",
        "cwd": "C:/some/checkout",
    }))
    assert env.event_type is EventType.UNKNOWN
    assert env.payload.get("native_event") == "TotallyUnknownHookName"
    assert env.session_id == "S-unknown"


def test_a_recognised_native_event_type_is_not_degraded_to_unknown():
    """The other direction, so the test above cannot pass on a mapper that
    simply returns UNKNOWN for everything."""
    env = asyncio.run(WorkBuddyAdapter().normalize({
        "_source": "hook",
        "hook_event_name": "PreToolUse",
        "session_id": "S-2",
        "cwd": "C:/some/checkout",
        "tool_name": "Read",
    }))
    assert env.event_type is EventType.TOOL_CALL


def test_an_unknown_event_type_still_completes_the_pipeline(tmp_path):
    """UNKNOWN must be a normal input, not a hole.

    If an unrecognised type short-circuited ingest, the audit tape would have a
    gap exactly where something unexpected happened -- the worst place for one.
    """
    store, engine = _cli(tmp_path / "jcc.db")
    out = engine.ingest(_env(event_type=EventType.UNKNOWN))

    assert "deduped" not in out
    assert out.get("directive"), "no directive was produced for an UNKNOWN event"
    assert out.get("action"), out
    assert len(store.get_audit()) == 1, "an UNKNOWN event left no audit row"
    ok, count = store.verify_audit()
    assert ok is True and count == 1


def test_an_envelope_missing_its_event_type_defaults_to_unknown_not_to_a_guess():
    """The schema default is UNKNOWN, never a plausible-looking real type."""
    env = _env()
    assert env.event_type is EventType.UNKNOWN
