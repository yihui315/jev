"""Loader for the deterministic four-agent demo fixtures (S-05).

The fixtures are DATA, not code: eight AEP v0.1 envelopes plus a manifest that
names them in replay order. This module only reads them, validates that the
manifest agrees with the files it points at, and returns the envelopes.

What makes a run of these reproducible:

  * every envelope carries a FIXED ``timestamp``, so no wall-clock input reaches
    the chain;
  * ``session_id`` / ``event_id`` / ``delivery_id`` are fixed, which also fixes
    the provider's risk score (``DeterministicMockProvider._risk`` hashes
    ``session_id:event_id``) and therefore the assessment, the action and the
    directive id for every step.

So one replay of :func:`load_demo_envelopes` into an empty database produces the
same assessments, the same actions and the same audit rows as any other replay.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Sequence

#: The mandatory demo label. A demo run must never be able to impersonate a real
#: agent, so the label is carried by the DATA (``project``, ``session_id``) and
#: rendered from it -- never hardcoded into a page.
DEMO_LABEL = "DEMO"

MANIFEST_NAME = "manifest.json"

#: Keys the server rejects or ignores silently, checked so a fixture that no
#: longer parses as AEP v0.1 fails loudly instead of being dropped.
_REQUIRED_KEYS = ("version", "event_id", "delivery_id", "session_id", "agent")


def demo_dir() -> Path:
    """Directory holding this fixture set."""
    return Path(__file__).resolve().parent


def load_manifest(directory: "Path | str | None" = None) -> Dict[str, Any]:
    """Read and return ``manifest.json``."""
    path = (Path(directory) if directory is not None else demo_dir()) / MANIFEST_NAME
    return json.loads(path.read_text(encoding="utf-8"))


def _validate(entry: Dict[str, Any]) -> None:
    missing = [k for k in _REQUIRED_KEYS if not entry.get(k)]
    if missing:
        raise ValueError(
            f"demo fixture {entry.get('file', '<unnamed>')} is missing required "
            f"key(s) {missing}; it is not a valid AEP v0.1 envelope"
        )
    if entry.get("version") != "aep/v0.1":
        raise ValueError(
            f"demo fixture {entry.get('file')!r} has version "
            f"{entry.get('version')!r}, expected 'aep/v0.1' (frozen wire version, "
            "ADR-0003 D-25/D-38)"
        )
    if entry.get("event_type") is None:
        raise ValueError(f"demo fixture {entry.get('file')!r} declares no event_type")


def load_demo_envelopes(
    directory: "Path | str | None" = None,
) -> List[Dict[str, Any]]:
    """Return every demo envelope, in manifest order, as plain dicts.

    The dicts are passed straight to ``POST /api/assess`` as JSON bodies, so a
    key added here lands on the wire unchanged.
    """
    base = Path(directory) if directory is not None else demo_dir()
    manifest = load_manifest(base)
    out: List[Dict[str, Any]] = []
    for entry in sorted(manifest.get("events") or [], key=lambda e: int(e.get("order", 0))):
        path = base / str(entry["file"])
        if not path.is_file():
            raise FileNotFoundError(f"demo manifest points at a missing file: {path}")
        envelope = json.loads(path.read_text(encoding="utf-8"))
        _validate(envelope)
        # Cross-check the manifest against the file: two sources of truth that
        # can disagree about an id is how a fixture set stops being reproducible.
        for key in ("event_id", "delivery_id", "session_id", "agent", "event_type"):
            declared = entry.get(key)
            if declared is not None and envelope.get(key) != declared:
                raise ValueError(
                    f"demo fixture {path.name} declares {key}={declared!r} in the "
                    f"manifest but the envelope carries {envelope.get(key)!r}"
                )
        out.append(envelope)
    return out


def demo_session_ids(directory: "Path | str | None" = None) -> List[str]:
    """distinct demo ``session_id`` values, in first-appearance order."""
    seen: List[str] = []
    for env in load_demo_envelopes(directory):
        if env["session_id"] not in seen:
            seen.append(env["session_id"])
    return seen


def is_demo_run(session: Dict[str, Any], label: str = DEMO_LABEL) -> bool:
    """Whether a session row came from the demo fixture set.

    Derived from the DATA the API returned (``project`` / ``session_id``), never
    from a flag the renderer carries. A real agent's session never satisfies it,
    so a real run can never be labelled demo and a demo run can never be
    presented as a real one.
    """
    project = str(session.get("project") or "").strip()
    session_id = str(session.get("session_id") or "").strip()
    if project.upper() == str(label).upper():
        return True
    return session_id.lower().startswith("demo-")
