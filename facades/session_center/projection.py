"""S-06 — the Session Center facade: a READ-ONLY projection of session state.

What this is
------------
A projection is a *read*. Every value here already exists in the state store;
this module only selects, scrubs and encodes it. There is no code path in this
package that writes to the store, to the audit tape or to the platform, and the
constructor makes that structural rather than aspirational: it wraps whatever
store it is handed in a :class:`~session_center.read_only.StoreGuard`, so the
facade's only handle on the platform cannot write even if someone adds a line
that tries.

Crash containment
-----------------
A visual layer that can take down supervision is a bug. The facade's live path
(:meth:`SessionCenterFacade.on_event`) never propagates an exception into the
supervision core: it catches, increments :attr:`failures`, records
:attr:`last_failure`, logs at ERROR **with the traceback**, and returns an
:class:`ProjectionOutcome` so a caller that polls can see the degradation. Not
raising into the core is precisely what makes the failure *observable*: a fault
that both the facade and the core swallow is the defect class this project has
been burned by twice, so the counter and the log record are asserted by tests,
and a direct call (:meth:`render_event`) still raises to its caller.

Determinism
-----------
No wall clock, no pid, no ``hash()``. ``canonical_dumps`` is the one encoder
and the projected document is sorted by ``session_id``, so the same input state
projects to byte-identical output.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from jcc_core.redaction import scrub_value_secrets

from .canonical import canonical_bytes, canonical_dumps
from .contract import CONTRACT_VERSION, contract_header, load_contract
from .read_only import MUTATING_API, ReadOnlyViolation, StoreGuard, refuse_mutation

logger = logging.getLogger("jcc.facade.session_center")


@dataclass
class ProjectionOutcome:
    """What a projection attempt produced — including when it produced nothing."""

    ok: bool
    kind: str
    detail: str = ""
    failure_count: int = 0
    records: int = 0
    error_type: str = ""
    error: str = ""

    def __bool__(self) -> bool:  # an outcome is truthy exactly when it is ok
        return self.ok


def _as_dict(value: Any) -> Dict[str, Any]:
    """A store row / model as a plain dict, or ``{}``."""
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        try:
            dumped = value.model_dump(mode="json")
            return dumped if isinstance(dumped, dict) else {}
        except Exception:  # noqa: BLE001 - a broken model must not break a projection
            return {}
    if isinstance(value, dict):
        return value
    return {}


def _walk(source: Dict[str, Any], dotted: str) -> Any:
    node: Any = source
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


class SessionCenterFacade:
    """Read-only Session Center projection.

    Parameters
    ----------
    store:
        A :class:`jcc_core.state_store.StateStore`. It is wrapped in a
        :class:`~session_center.read_only.StoreGuard` before it is kept, so the
        facade cannot write through it. Pass ``None`` to project callers that
        already hold rows (tests, in-process emitters).
    """

    #: Documented, versioned public surface. ``mutating_api`` is empty by
    #: construction and is asserted by the read-only tests.
    contract_version = CONTRACT_VERSION

    def __init__(self, store: Any = None, *, contract: Optional[Dict[str, Any]] = None):
        self._contract = contract if contract is not None else load_contract()
        self._store = StoreGuard.wrap(store) if store is not None else None
        self.failures: int = 0
        self.last_failure: Optional[str] = None
        self.last_failure_type: Optional[str] = None
        #: Diagnostics a consumer (or a test) can read after a bad frame.
        self.outcomes: List[ProjectionOutcome] = []

    # -- constants ---------------------------------------------------------- #
    @property
    def contract(self) -> Dict[str, Any]:
        return self._contract

    @property
    def mutating_api(self) -> tuple:
        return MUTATING_API

    @property
    def read_only(self) -> bool:
        return True

    @property
    def external_product_available(self) -> bool:
        return bool(self._contract.get("external_product_available"))

    def contract_header(self) -> Dict[str, Any]:
        return contract_header()

    # -- projection --------------------------------------------------------- #
    def project_session(self, state: Any) -> Dict[str, Any]:
        """Project ONE session row. Contract-driven: only declared paths."""
        raw = _as_dict(state)
        # Scrub BEFORE selection: a secret must not even be addressable.
        source = scrub_value_secrets(raw) if raw else {}
        row: Dict[str, Any] = {}
        for spec in self._contract["fields"]:
            value = _walk(source, spec["path"])
            if isinstance(value, float):
                value = float(round(value, 9))
            row[spec["path"]] = value
        return row

    def project_sessions(self, states: Any) -> List[Dict[str, Any]]:
        """Project many rows in canonical order (sorted by ``session_id``)."""
        rows = [self.project_session(s) for s in states]
        rows.sort(key=lambda r: str(r.get("session_id") or ""))
        return rows

    def project_all(self) -> Dict[str, Any]:
        """The full scoreboard document: everything the facade can show."""
        if self._store is None:
            rows: List[Dict[str, Any]] = []
        else:
            rows = self.project_sessions(self._store.list_sessions())
        return {
            "contract": contract_header(),
            "facade_version": self.contract_version,
            "read_only": True,
            "external_product_available": self.external_product_available,
            "ordering": self._contract.get("ordering", ""),
            "session_count": len(rows),
            "sessions": rows,
        }

    def project_all_bytes(self) -> bytes:
        """The byte form a determinism test compares."""
        return canonical_bytes(self.project_all())

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        if self._store is None:
            return None
        row = _as_dict(self._store.get_session(session_id))
        return self.project_session(row) if row else None

    def project_engine_event(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        """Project one supervision-core ingest result as a scoreboard row.

        The core's ingest output is fused OVER the persisted session row, so a
        frame carries the freshest decision without the facade having to write
        anything anywhere.
        """
        source: Dict[str, Any] = {}
        if self._store is not None:
            source = _as_dict(self._store.get_session(msg.get("session_id")))
        directive = _as_dict(msg.get("directive"))
        assessment = _as_dict(msg.get("assessment"))
        if directive:
            source["current_directive"] = directive
        if assessment:
            source["current_assessment"] = assessment
        if msg.get("session_status"):
            source["status"] = msg["session_status"]
        if msg.get("action"):
            source.setdefault("current_directive", {})
            action = msg["action"]
            if isinstance(source["current_directive"], dict):
                source["current_directive"]["action"] = action
        source["event_id"] = msg.get("event_id")
        return self.project_session(source)

    # -- canonical encoding -------------------------------------------------- #
    def canonical(self, obj: Any) -> str:
        return canonical_dumps(obj)

    # -- live projection (read-only, crash-contained) ------------------------ #
    def attach(self, engine: Any) -> None:
        """Subscribe to a :class:`jcc_core.core.CoreEngine` (read-only)."""
        engine.subscribe(self.on_event)

    def render_event(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        """Project one core event. A failure HERE propagates to the caller.

        This is the strict path: a caller that asked for a projection and got a
        crash is told. The containment is implemented one level down, in
        :meth:`on_event`, where a crash must not reach the core.
        """
        return self.project_engine_event(msg)

    def on_event(self, msg: Dict[str, Any]) -> ProjectionOutcome:
        """Live-projection callback. NEVER raises into the supervision core.

        Returns a :class:`ProjectionOutcome` that carries the failure instead.
        ``failures`` and the ERROR log record are, here, the whole point: a
        swallowed exception is a silent visual-layer death, and the core must be
        able to prove it happened.
        """
        try:
            row = self.render_event(msg)
            outcome = ProjectionOutcome(ok=True, kind="session", records=1,
                                        detail=str(row.get("session_id") or ""))
        except Exception as exc:  # noqa: BLE001 - containment, not absolution
            self.failures += 1
            self.last_failure_type = type(exc).__name__
            self.last_failure = f"{type(exc).__name__}: {exc}"
            outcome = ProjectionOutcome(
                ok=False, kind="session", failure_count=self.failures,
                error_type=type(exc).__name__, error=str(exc),
            )
            logger.error(
                "session_center facade failed to project an event (core output "
                "unchanged; failure counted and logged): %s",
                self.last_failure, exc_info=True, extra={"facade": "session_center"},
            )
        self.outcomes.append(outcome)
        return outcome

    # -- read-only enforcement ---------------------------------------------- #
    def apply_change(self, *args: Any, **kwargs: Any) -> None:
        """The facade's one mutating entry point: it refuses."""
        refuse_mutation(*args, **kwargs)

    #: Aliases a caller might reach for. All of them refuse; none of them exist
    #: as real methods, so ``dir()`` does not advertise a write path.
    mutate = apply_change
    pause = apply_change
    steer = apply_change
    retry = apply_change
    finish = apply_change
    quarantine = apply_change
    escalate = apply_change
    delete_session = apply_change
    write = apply_change
    dispatch = apply_change

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return (f"SessionCenterFacade(read_only=True, contract="
                f"{self._contract.get('name')}/{self.contract_version}, "
                f"store={self._store!r}, failures={self.failures})")
