"""Shared fixtures for the S-06 Session Center facade tests.

The facade reads the REAL platform models (``packages/jcc_core``) — it does not
re-implement the data model — so every fixture here is built from
:class:`jcc_core.schemas.SessionState` / ``Assessment`` / ``Directive``, never
from a hand-written dict that could drift from the schemas.
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List

import pytest

#: ``<test>/session_center/tests/conftest.py`` -> parents[3] == F:/jev
REPO = Path(__file__).resolve().parents[3]
PACKAGES = REPO / "packages"
FACADES = REPO / "facades"
for _p in (str(PACKAGES), str(FACADES)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from jcc_core.actuator import Actuator  # noqa: E402
from jcc_core.config import load_config  # noqa: E402
from jcc_core.core import CoreEngine  # noqa: E402
from jcc_core.providers import build_provider  # noqa: E402
from jcc_core.schemas import (  # noqa: E402
    AEPEnvelope,
    AgentName,
    Assessment,
    Directive,
    DirectiveAction,
    SessionState,
    SessionStatus,
)
from jcc_core.state_store import StateStore  # noqa: E402

from session_center.canonical import canonical_bytes  # noqa: E402
from session_center.contract import (  # noqa: E402
    EXCLUDED_FIELDS,
    FIELD_SPEC,
    load_contract,
)
from session_center.external import probe_external_product  # noqa: E402
from session_center.projection import SessionCenterFacade  # noqa: E402

#: A sentinel that must never reach a facade payload. Planted into the source
#: state the way an adversarial producer would plant it (a secret under a
#: secret-looking key AND one embedded in free-form text).
PLANTED_SECRET = "sk-live-S06SENTINELdeadbeef0123456789abcdef"

SESSION_ID = "s06-session-0001"
EVENT_ID = "ev-s06-0001"


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def tmpdb(tmp_path) -> str:
    return str(tmp_path / "jcc.db")


@pytest.fixture
def store(tmpdb) -> StateStore:
    """A real StateStore on an isolated temp database."""
    return StateStore(tmpdb)


@pytest.fixture
def facade(store) -> SessionCenterFacade:
    """A facade over a real store. The store stays writable for TESTS only —
    the facade's handle on it is a read-only guard."""
    return SessionCenterFacade(store)


@pytest.fixture
def engine(store) -> CoreEngine:
    """A REAL CoreEngine: mock provider, actuation disabled, temp database."""
    cfg = load_config()               # defaults: provider=mock, no network
    return CoreEngine(store, build_provider(cfg), Actuator(enabled=False, allowed_actions=[]), cfg)


@pytest.fixture
def rich_state() -> SessionState:
    """A session state where every declared field carries a real value."""
    return SessionState(
        session_id=SESSION_ID,
        agent=AgentName.WORKBUDDY,
        project="jev",
        cwd=str(REPO),
        status=SessionStatus.AWAIT,
        created_at=1700000000.0,
        updated_at=1700000005.0,
        last_event_id=EVENT_ID,
        event_count=3,
        evidence_count=1,
        current_assessment=Assessment(
            agent=AgentName.WORKBUDDY,
            session_id=SESSION_ID,
            timestamp=1700000005.0,
            model="jev-1.13.0",
            provider="mock",
            probabilities={"risk": 0.72},
            risk_score=0.72,
            band="high",
            needs_verify=True,
            escalate=False,
            pause=False,
            steer=False,
            retry=True,
            finished=False,
            raw={"signals": {"meaningful_progress": True, "worker_stuck": False},
                 "note": "retry budget not exhausted"},
            confidence=0.81,
            confidence_kind="model",
            provider_verified=True,
            blocker_kind="EXACT",
            blocker_reasons=["evidence_incomplete"],
        ).model_dump(mode="json"),
        current_directive=Directive(
            directive_id=f"dir_{SESSION_ID}_{EVENT_ID}",
            session_id=SESSION_ID,
            timestamp=1700000005.0,
            action=DirectiveAction.RETRY,
            target_actuator="noop",
            params={"source": "control-plane"},
            policy_version="p3.0.0",
            signed=False,
            signature=None,
            evidence_hash="a" * 64,
            acted=False,
            actuation_reason="shadow_mode",
            executable=False,
            requires_human=False,
            shadow=True,
            signature_status="unsigned",
            signing_alg=None,
            signature_verified=False,
            issued_at=1700000005.0,
            ttl_s=60.0,
            nonce=None,
            policy_table_version="t3",
        ).model_dump(mode="json"),
    )


@pytest.fixture
def rich_doc(rich_state) -> Dict[str, Any]:
    """A persisted-shaped row: ``SessionState`` dump + the directive ``origin``
    the ``directives`` table (not the sessions row) carries."""
    doc = rich_state.model_dump(mode="json")
    doc["current_directive"]["origin"] = "engine"
    return doc


# --------------------------------------------------------------------------- #
# helpers (importable by sibling test modules)
# --------------------------------------------------------------------------- #
def envelope(session_id: str = SESSION_ID, event_id: str = EVENT_ID, **kw) -> AEPEnvelope:
    """A real AEP envelope, deterministic ids, no network."""
    opts = dict(
        version="aep/v0.1",
        event_id=event_id,
        delivery_id=f"dl-{event_id}",
        session_id=session_id,
        agent=AgentName.WORKBUDDY,
        project="jev",
        cwd=str(REPO),
        event_type="tool_call",
        payload={"tool": "read", "target": str(REPO / "config" / "jcc.yaml")},
        evidence={"kind": "tool_result", "complete": True},
    )
    opts.update(kw)
    return AEPEnvelope(**opts)


def db_bytes(db_path: str) -> bytes:
    """Every byte the SQLite files hold (main db + WAL + shared memory)."""
    blob = b""
    for suffix in ("", "-wal", "-shm"):
        f = db_path + suffix
        if os.path.exists(f):
            with open(f, "rb") as fh:
                blob += fh.read()
    return blob


def db_fingerprint(db_path: str) -> str:
    """sha256 over every SQLite file — what "the facade wrote nothing" measures."""
    return hashlib.sha256(db_bytes(db_path)).hexdigest()


def tripwire(store: Any, on_call: Callable[[str], None] = None) -> Dict[str, int]:
    """Replace every store writer with a counter. Returns the counter dict."""
    counters: Dict[str, int] = {name: 0 for name in
                                ("upsert_session", "add_event", "add_evidence",
                                 "add_assessment", "add_directive", "append_audit")}

    def _make(name: str):
        def _stub(*_a, **_kw):
            counters[name] += 1
            if on_call is not None:
                on_call(name)
        return _stub

    for name in counters:
        setattr(store, name, _make(name))
    return counters


def declared_paths() -> List[str]:
    return [f["path"] for f in load_contract()["fields"]]


def excluded() -> List[str]:
    return list(EXCLUDED_FIELDS)


def contract_of() -> Dict[str, Any]:
    return load_contract()


def external_probe() -> Dict[str, Any]:
    return probe_external_product()


def facade_bytes(f: SessionCenterFacade) -> bytes:
    return canonical_bytes(f.project_all())
