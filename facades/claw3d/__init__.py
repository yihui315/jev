"""S-07 — Claw3D facade (read-only 3D-scene projection of JCC Core session state).

Public surface:

* :mod:`claw3d.contract` — the versioned facade contract (``contract.json``).
* :mod:`claw3d.projection` — :class:`Claw3DFacade`.
* :mod:`claw3d.read_only` — :class:`StoreGuard`, :class:`ReadOnlyViolation`.
* :mod:`claw3d.external` — probe for the (optional) external product.

The facade is read-only by construction: its only handle on the store is a guard
that refuses every writer. A crash in this layer cannot affect the supervision
core, and a crash in the core's own broadcast is contained here — counted, logged
and returned as a :class:`ProjectionOutcome`, never silently swallowed.
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
from .projection import ProjectionOutcome, Claw3DFacade

__all__ = [
    "EXCLUDED_FIELDS",
    "FIELD_SPEC",
    "CONTRACT_NAME",
    "CONTRACT_VERSION",
    "ContractError",
    "ProjectionOutcome",
    "Claw3DFacade",
    "build_contract",
    "contract_path",
    "load_contract",
]
