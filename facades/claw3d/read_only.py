"""Read-only enforcement for the S-07 (Claw3D) facade.

A facade that can write can take down the platform. Read-only here is enforced
by STRUCTURE, not discipline: the facade is constructed from a :class:`StoreGuard`
that exposes only the reader API and raises :class:`ReadOnlyViolation` on any
writer; the facade surface exposes no mutating method at all (a call to any
mutating name raises); and ``MUTATING_API`` is empty and is asserted against the
facade's public surface by the read-only tests.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

#: State-store methods that change platform state. The guard refuses all of
#: them; the facade is constructed from the guard, so it has no writable handle.
WRITER_API = frozenset({
    "upsert_session",
    "add_event",
    "add_evidence",
    "add_assessment",
    "add_directive",
    "append_audit",
    "backfill_directive_origins",
})

#: State-store members the facade may use. Anything not listed here is refused.
READER_API = frozenset({
    "get_session",
    "list_sessions",
    "get_directives",
    "get_audit",
    "seen",
    "verify_audit",
    "budget_snapshot",
    "connection",
    "transaction",
    "is_wal",
    "db_path",
})

#: Names a caller might try on a facade pretending to be writable. All refuse.
MUTATING_API: tuple = ()


class ReadOnlyViolation(RuntimeError):
    """Raised when anything tries to mutate through the read-only facade."""


class StoreGuard:
    """Read-only view over a :class:`jcc_core.state_store.StateStore`."""

    __slots__ = ("_store", "_underlying")

    def __init__(self, store: Any):
        object.__setattr__(self, "_store", store)
        object.__setattr__(self, "_underlying", store)

    @property
    def underlying(self) -> Any:
        """The untouched store. Tests use this to WRITE fixtures; the facade
        itself never sees it."""
        return self._underlying

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise ReadOnlyViolation(
                f"read-only facade refused private store attribute {name!r}"
            )
        if name in WRITER_API:
            raise ReadOnlyViolation(
                f"read-only facade refuses store writer {name!r}: the Claw3D "
                "facade may display state and never change it"
            )
        if name not in READER_API:
            raise ReadOnlyViolation(
                f"read-only facade refuses undeclared store member {name!r}; "
                f"the declared reader API is {sorted(READER_API)}"
            )
        return getattr(self._store, name)

    def wrap(store: Any) -> "StoreGuard":
        """Always return a guard — never a raw store."""
        return StoreGuard(store)

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return f"StoreGuard(read_only=True, db={getattr(self._store, 'db_path', '?')!r})"


def refuse_mutation(*args: Any, **kwargs: Any) -> None:
    """The one mutating entry point the facade exposes, and what it does."""
    raise ReadOnlyViolation(
        "the Claw3D facade is read-only; there is no mutation path "
        "(mutation belongs to the control plane behind a control token)"
    )


def public_api(obj: Any) -> Iterable[str]:
    """Public, non-dunder names of ``obj`` — what the read-only contract covers."""
    return sorted(n for n in dir(obj) if not n.startswith("_"))
