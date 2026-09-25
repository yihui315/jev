"""S-06 — versioned facade contract for the Session Center read-only projection.

The contract is the SINGLE SOURCE OF TRUTH and it lives on disk as
``contract.json`` (next to this file). This module is a thin, deterministic
loader over that file: the facade's public field spec (``FIELD_SPEC``) and its
secret barrier (``EXCLUDED_FIELDS``) are loaded from the JSON, never
hand-maintained, so the two cannot drift. A contract change is therefore a
deliberate edit to ``contract.json`` (asserted by ``tests/test_contract.py``),
not a formatting preference in two places.

Nothing here writes to the store or imports the platform writer API.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

#: ``<test>/session_center/contract.py`` -> parent holds ``contract.json``.
_THIS = Path(__file__).resolve()
CONTRACT_FILE = _THIS.parent / "contract.json"


class ContractError(ValueError):
    """Raised when the facade contract cannot be loaded or is structurally wrong."""


def contract_path() -> Path:
    """Absolute path of the on-disk contract (``contract.json``)."""
    return CONTRACT_FILE


def load_contract(path: Any = None) -> Dict[str, Any]:
    """Load the facade contract from disk.

    Parameters
    ----------
    path:
        Optional override path (str or :class:`pathlib.Path`). Defaults to the
        bundled ``contract.json``.
    """
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


#: Derived constants — loaded once, from the on-disk contract. Keeping them
#: here (rather than copied) is what stops the code and the JSON from diverging.
_CONTRACT = load_contract()
CONTRACT_NAME: str = _CONTRACT["name"]
CONTRACT_VERSION: str = _CONTRACT["version"]
#: The full field specification (each entry: ``path`` / ``source`` / ``type`` / ``note``).
FIELD_SPEC: List[Dict[str, Any]] = list(_CONTRACT["fields"])
#: Dotted paths that must NEVER reach a projected payload (the secret barrier).
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
    """Build the in-memory contract dict (a fresh copy of the on-disk source).

    The facade treats ``contract.json`` as authoritative; ``build_contract`` is
    the read path used by tooling that wants the live contract object without
    mutating what was loaded at import time.
    """
    return json.loads(json.dumps(load_contract()))
