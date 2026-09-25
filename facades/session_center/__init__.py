"""S-06 — Session Center facade (read-only projection of JCC Core session state).

Public surface:

* :mod:`session_center.contract` — the versioned facade contract
  (``contract.json`` on disk, :data:`FIELD_SPEC` as its source of truth).
* :mod:`session_center.projection` — :class:`SessionCenterFacade`.
* :mod:`session_center.read_only` — :class:`StoreGuard`, :class:`ReadOnlyViolation`.
* :mod:`session_center.external` — probe for the (optional) external product.

The facade is read-only by construction: it never writes to the state store, and
its only handle on it is a guard that refuses every writer. A crash in this
layer cannot affect the supervision core, and a crash in the core's own
broadcast is contained here — counted, logged and returned as a
:class:`ProjectionOutcome`, never silently swallowed.
"""
from __future__ import annotations

from .contract import (
    EXCLUDED_FIELDS,
    FIELD_SPEC,
    CONTRACT_NAME,
    CONTRACT_VERSION,
    ContractError,
    build_contract,
    contract_path,
    load_contract,
)
from .projection import ProjectionOutcome, SessionCenterFacade

__all__ = [
    "EXCLUDED_FIELDS",
    "FIELD_SPEC",
    "CONTRACT_NAME",
    "CONTRACT_VERSION",
    "ContractError",
    "ProjectionOutcome",
    "SessionCenterFacade",
    "build_contract",
    "contract_path",
    "load_contract",
]
