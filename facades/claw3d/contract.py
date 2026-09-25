"""S-07 — versioned facade contract for the Claw3D read-only 3D-scene projection.

The contract is the SINGLE SOURCE OF TRUTH and it lives on disk as
``contract.json`` (next to this file). This module is a thin, deterministic
loader over that file: ``FIELD_SPEC`` and the secret barrier ``EXCLUDED_FIELDS``
are loaded from the JSON, never hand-maintained, so the two cannot drift. The
Claw3D facade projects a deterministic 3D scene-graph node per session; geometry
is derived from the session id (never random), so the projection is byte-stable.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

_THIS = Path(__file__).resolve()
CONTRACT_FILE = _THIS.parent / "contract.json"


class ContractError(ValueError):
    """Raised when the facade contract cannot be loaded or is structurally wrong."""


def contract_path() -> Path:
    """Absolute path of the on-disk contract (``contract.json``)."""
    return CONTRACT_FILE


def load_contract(path: Any = None) -> Dict[str, Any]:
    """Load the facade contract from disk (defaults to the bundled ``contract.json``)."""
    p = Path(path) if path is not None else CONTRACT_FILE
    if not p.exists():
        raise ContractError(f"facade contract missing at {p}")
    try:
        with p.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"facade contract at {p} is unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise ContractError(f"facade contract at {p} is not a JSON object")
    required = ("name", "version", "fields", "excluded_fields", "read_only")
    missing = [k for k in required if k not in data]
    if missing:
        raise ContractError(f"facade contract at {p} missing keys: {missing}")
    return data


_CONTRACT = load_contract()
CONTRACT_NAME: str = _CONTRACT["name"]
CONTRACT_VERSION: str = _CONTRACT["version"]
FIELD_SPEC: List[Dict[str, Any]] = list(_CONTRACT["fields"])
EXCLUDED_FIELDS: tuple = tuple(_CONTRACT["excluded_fields"])


def contract_header() -> Dict[str, Any]:
    """The compact header embedded in every projected document."""
    return {
        "name": _CONTRACT.get("name"),
        "version": _CONTRACT.get("version"),
        "read_only": _CONTRACT.get("read_only"),
        "external_product_available": _CONTRACT.get("external_product_available"),
    }


def build_contract() -> Dict[str, Any]:
    """Build the in-memory contract dict (a fresh copy of the on-disk source)."""
    return json.loads(json.dumps(load_contract()))
