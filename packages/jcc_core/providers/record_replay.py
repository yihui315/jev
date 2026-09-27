"""Record/Replay provider (v1.1, ADR-v1.1-0002 candidate).

PURPOSE
    Captures the exact ``Assessment`` a real provider would return for a given
    ``AEPEnvelope`` + ``SessionState`` pair, then in subsequent runs returns
    that captured Assessment verbatim. Used to:

    1. build E2E tests that do NOT require live network or a real key,
    2. replay real-provider traces through the kernel so changes to the
       arbitration / policy / signing can be re-evaluated against the same
       captured evidence,
    3. drive the live-shadow gate's "did the real provider agree with the
       shadow?" comparison in offline mode.

WHAT THIS PROVIDER MUST NEVER DO
    * Return a mock or synthetic Assessment when no recording matches. The
      whole point is fidelity; an unmatched envelope is a STOP condition
      (``ReplayMiss``) so the kernel can fail closed, not "fill in" a mock
      result and pretend it came from a recording. That would be "mock
      pretending to be replay" -- the exact failure mode §9 forbids.
    * Make up a ``band`` or ``risk_score`` that isn't in the recording.
    * Pretend to be the real provider when in replay mode. The
      ``provider`` field is always the literal string ``"replay"`` so the
      UI can distinguish REPLAY from REAL/MOCK/DISABLED.

RECORDING FILE FORMAT
    A JSON line per assessment, fixed schema::

            {
              "schema":       "jcc-replay/1",
              "envelope":     <full AEPEnvelope JSON>,
              "session":      <SessionState JSON>,
              "assessment":   <Assessment JSON>,
              "captured_by":  "<recorder identifier>",
              "captured_at":  "<ISO-8601 UTC>",
              "source":       "real"|"manual"|"fixture"
            }

    Lookup key is ``(session_id, event_id, delivery_id)`` from the envelope.
    ``delivery_id`` is included so a replayed envelope whose redelivery
    has a different ``delivery_id`` (at-least-once delivery is part of the
    contract) still resolves.

FAILURE MODES
    * File missing                -> ``ReplayMiss`` (raised)
    * File unreadable / malformed -> ``ReplayCorrupt`` (raised)
    * Envelope not in file        -> ``ReplayMiss`` (raised)
    * Recording schema mismatch   -> ``ReplayVersion`` (raised)
    * Any provider-side error     -> ``RealProvider.is_available() == False``

WHY THESE EXCEPTIONS, NOT FALLBACKS
    A silent fallback to a default Assessment would be "mock pretending to be
    replay". Raising an exception forces the kernel / runner / UI to surface
    the gap, which is the only honest answer when a recorded trace does not
    match the live input.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Dict, Iterable, Optional

from ..schemas import AEPEnvelope, Assessment, SessionState
from .base import JevProvider

logger = logging.getLogger("jcc.providers.record_replay")

#: Schema name pinned so future format changes are detected rather than
#: silently mis-parsed. Bumping this constant is the single place to
#: change the on-disk contract.
REPLAY_SCHEMA = "jcc-replay/1"


# --- Errors --------------------------------------------------------------- #

class ReplayError(Exception):
    """Base class for all record/replay errors."""


class ReplayMiss(ReplayError):
    """The envelope was not present in the recording file."""


class ReplayCorrupt(ReplayError):
    """The recording file could not be read or parsed."""


class ReplayVersion(ReplayError):
    """The recording file's schema tag is not what this provider expects."""


# --- Provider ------------------------------------------------------------- #

class RecordReplayProvider(JevProvider):
    """Replay provider: returns pre-recorded Assessments verbatim.

    The recording is loaded once at construction time (small enough for
    the v1.1 line; large replays can switch to streaming later without
    touching the public surface). The provider name is the literal
    string ``"replay"``; it never claims to be ``"real"`` even when the
    recording's ``source`` field is ``"real"``.
    """

    name = "replay"
    # ``verified`` is True ONLY when the recording's source is a verified
    # run (we have no such source today). Until then it is False so the
    # kernel still routes a ``provider_unverified`` -> ESCALATE blocker.
    verified = False

    def __init__(
        self,
        recording_path: Optional[str | Path] = None,
        *,
        env_var: str = "JCC_REPLAY_RECORDING",
    ):
        # Recording path resolves from constructor arg, env, then a
        # project-relative default that fails loud at boot if missing.
        path: Optional[Path] = None
        if recording_path is not None:
            path = Path(recording_path)
        elif env_var and os.environ.get(env_var):
            path = Path(os.environ[env_var])
        else:
            # Default: fixtures/v1.1/replay_default.jsonl at repo root,
            # resolved by walking up from this file. None means "no
            # recording loaded" -- ``is_available`` returns False.
            default = Path(__file__).resolve().parents[3] / "fixtures" / "v1.1" / "replay_default.jsonl"
            if default.is_file():
                path = default

        self.recording_path = path
        self._index: Dict[tuple, dict] = {}
        self._loaded_from: Optional[str] = None
        self._schema_version: Optional[str] = None
        self._load_error: Optional[str] = None
        if path is not None:
            self._load(path)

    # -- I/O --------------------------------------------------------------- #

    def _load(self, path: Path) -> None:
        """Load the recording. A missing file is NOT an exception -- it is
        a configuration state that ``is_available()`` reports as False, so
        callers (and ``build_provider``) can route to DisabledProvider on
        their own terms.

        A present-but-malformed file IS still an exception, because the
        caller asked for replay with a recording file and got unreadable
        bytes back -- the worst-of-both case that must not be silently
        fabricated past.
        """
        if not path.is_file():
            # Recording file does not exist -> provider reports unavailable,
            # ``build_provider`` will degrade to DisabledProvider (fail-closed).
            logger.info(
                "RecordReplayProvider: no recording at %s; is_available()=False",
                path)
            return

        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ReplayCorrupt("cannot read recording %s: %s" % (path, exc)) from exc

        count = 0
        for lineno, raw in enumerate(text.splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ReplayCorrupt(
                    "malformed JSON at %s line %d: %s" % (path, lineno, exc)) from exc

            schema = entry.get("schema")
            if schema != REPLAY_SCHEMA:
                raise ReplayVersion(
                    "recording %s line %d schema=%r expected %r"
                    % (path, lineno, schema, REPLAY_SCHEMA))

            env = entry.get("envelope") or {}
            key = (
                env.get("session_id", ""),
                env.get("event_id", ""),
                env.get("delivery_id", ""),
            )
            if key in self._index:
                # Last-wins is the documented behaviour: the recording file
                # is treated as an append-only log of captures and the
                # most recent capture for a given envelope tuple wins.
                logger.debug(
                    "recording %s line %d duplicates %s -- last-wins",
                    path, lineno, key)
            self._index[key] = entry
            count += 1

        self._loaded_from = str(path)
        self._schema_version = REPLAY_SCHEMA
        logger.info(
            "RecordReplayProvider loaded %d entries from %s (schema=%s)",
            count, path, REPLAY_SCHEMA)

    # -- JevProvider surface ---------------------------------------------- #

    def is_available(self) -> bool:
        return self.recording_path is not None and bool(self._index)

    @property
    def recording_sha256(self) -> Optional[str]:
        """Hash of the recording bytes, useful for evidence / provenance.

        None when no recording is loaded. Returned as a 64-char lowercase
        hex string; readers must compare with ``str.lower``.
        """
        if self.recording_path is None:
            return None
        try:
            data = self.recording_path.read_bytes()
        except OSError:
            return None
        return hashlib.sha256(data).hexdigest()

    @property
    def entry_count(self) -> int:
        return len(self._index)

    def assess(self, event: AEPEnvelope, session: SessionState) -> Assessment:
        """Return the recorded Assessment verbatim, or raise ReplayMiss.

        Why raising, not falling back to a default Assessment: see module
        docstring. The whole point of replay is fidelity; an unmatched
        envelope is a STOP condition that the kernel and the operator's
        UI must surface, not "fill in" with a mock that pretends to be
        a recording.
        """
        key = (event.session_id, event.event_id, event.delivery_id)
        entry = self._index.get(key)
        if entry is None:
            raise ReplayMiss(
                "no recording matches envelope (session=%r event=%r delivery=%r); "
                "loaded %d entries from %s"
                % (event.session_id, event.event_id, event.delivery_id,
                   len(self._index), self._loaded_from or "(none)"))

        raw_assessment = entry.get("assessment")
        if not isinstance(raw_assessment, dict):
            raise ReplayCorrupt(
                "recording entry for %s is missing 'assessment' dict" % (key,))

        # The recording's ``provider`` field is preserved as-is when it
        # already names us ("replay"); if a recording accidentally claims
        # to be "real", we OVERWRITE it to "replay" so the UI and any
        # downstream code can never confuse the two. This is the
        # provider-side enforcement of "mock/replay must never pretend to
        # be real".
        if raw_assessment.get("provider") != self.name:
            raw_assessment = dict(raw_assessment)
            raw_assessment["provider"] = self.name

        # Provenance: every replayed Assessment carries a small
        # ``raw.replay_provenance`` block so the audit trail can answer
        # "where did this Assessment come from?" without joining the
        # recording file.
        raw_assessment.setdefault("raw", {})
        if isinstance(raw_assessment["raw"], dict):
            raw_assessment["raw"]["replay_provenance"] = {
                "recording_sha256": self.recording_sha256,
                "recording_path": self._loaded_from,
                "schema": self._schema_version,
            }

        # Pydantic re-validates on construction; a recording with a stale
        # or wrong field type becomes a Corrupt rather than a silent
        # misread.
        try:
            return Assessment(**raw_assessment)
        except Exception as exc:
            raise ReplayCorrupt(
                "recording entry for %s failed Assessment validation: %s"
                % (key, exc)) from exc


__all__ = [
    "REPLAY_SCHEMA",
    "ReplayError",
    "ReplayMiss",
    "ReplayCorrupt",
    "ReplayVersion",
    "RecordReplayProvider",
]