"""Record/Replay provider (v1.1 §9A — hardened recording format).

PURPOSE
    Captures the exact ``Assessment`` a provider returned for a given
    ``AEPEnvelope`` + ``SessionState`` pair, then in later runs returns that
    captured Assessment verbatim. Used to:

    1. build E2E tests that do NOT require live network or a real key,
    2. replay real-provider traces through the kernel so changes to the
       arbitration / policy / signing can be re-evaluated against the same
       captured evidence,
    3. drive the live-shadow gate's "did the real provider agree with the
       shadow?" comparison in offline mode.

WHAT THIS PROVIDER MUST NEVER DO
    * Return a mock or synthetic Assessment when no recording matches. The whole
      point is fidelity; an unmatched envelope is a STOP condition
      (``ReplayMiss``) so the kernel can fail closed, not "fill in" a mock result
      and pretend it came from a recording. That would be "mock pretending to be
      replay".
    * Make up a ``band`` or ``risk_score`` that isn't in the recording.
    * Pretend to be the real provider when in replay mode. The ``provider`` field
      is rewritten to the literal string ``"replay"`` so the UI can distinguish
      REPLAY from REAL/MOCK/DISABLED.
    * **Match approximately.** §9A requires an EXACT ``input_digest`` match. There
      is no nearest-match, no tolerance, and no fallback: an index hit whose
      digest differs is still a miss.

RECORDING FILE FORMAT — ``jcc-replay/2``
    One JSON object per line::

        {
          "schema_version":  "jcc-replay/2",
          "recording_id":    "<stable id shared by every line of the file>",
          "created_at":      "<ISO-8601 UTC>",
          "provider_source": "real"|"mock"|"manual"|"fixture",
          "captured_by":     "<recorder identifier>",
          "envelope":        <full AEPEnvelope JSON>,
          "session":         <SessionState JSON>|null,
          "input_digest":    "<sha256 of canonical {envelope, session}>",
          "assessment":      <Assessment JSON>,
          "fixture_digest":  "<sha256 of canonical assessment>"
        }

    Lookup key is ``(session_id, event_id, delivery_id)`` from the envelope.
    ``delivery_id`` is included because at-least-once delivery is part of the AEP
    contract: a redelivery is a separate capture, not the same event.

    WHY THE TWO DIGESTS EXIST (§9A)
        ``input_digest`` binds the record to the EXACT input it was captured
        from. Without it the only binding is three id strings, so a recording
        made from a different payload with reused ids would be served as a hit --
        a fidelity violation that looks like a success.
        ``fixture_digest`` seals the assessment the recorder wrote. Recomputing
        it at load turns "someone edited the recording" into a loud
        ``ReplayCorrupt`` instead of a plausible-looking assessment.

    WHY ``jcc-replay/1`` IS REJECTED, NOT MIGRATED SILENTLY
        v1 carried ``schema``/``captured_at``/``source`` and NO digests, so a v1
        file cannot be verified at all -- its entries could have been edited.
        Accepting it would mean accepting unverifiable evidence. The loader
        raises ``ReplayVersion`` naming the migration instead.

FAILURE MODES — all fail closed, none degrade silently
    * File missing                     -> ``is_available()`` False (see below)
    * File empty (0 entries)           -> ``ReplayCorrupt``
    * File unreadable / bad JSON line  -> ``ReplayCorrupt``
    * Legacy ``jcc-replay/1``          -> ``ReplayVersion`` (migration required)
    * Other schema version             -> ``ReplayVersion``
    * Missing required metadata field  -> ``ReplayCorrupt`` (names the fields)
    * ``fixture_digest`` mismatch      -> ``ReplayCorrupt`` (recorded assessment edited)
    * §9 boundary violation            -> ``ReplayCorrupt`` (names the line; §9)
    * ``input_digest`` mismatch        -> ``ReplayCorrupt`` (entry self-inconsistent)
    * Duplicate lookup key             -> ``ReplayCorrupt`` (ambiguous recording)
    * Inconsistent ``recording_id``    -> ``ReplayCorrupt`` (two files concatenated?)
    * Secret-shaped content            -> ``ReplayCorrupt`` (names the JSON path)
    * Envelope not in file             -> ``ReplayMiss`` (raised)
    * Index hit, digest differs        -> ``ReplayMiss`` (raised, WITH a distinct message)

    ORDER MATTERS BETWEEN THE FIRST THREE: integrity is checked before meaning.
    A hand-edited recording is usually BOTH digest-mismatched and (if the edit
    was the interesting kind) boundary-violating, and "these bytes are not what
    was recorded" is the more specific, more actionable of the two true things.
    The §9 check exists for the recording that is properly signed and still
    names an action -- a third-party writer that computed its digests correctly.
    Both orders are defensible; this one is pinned by a test so that changing it
    is a decision rather than a tidy-up.

    A MISSING file is deliberately NOT an exception. "No recording configured" is
    a configuration state that ``build_provider`` turns into ``DisabledProvider``
    (fail-closed), whereas "a recording was named and it is broken" is an error
    the operator must see. An earlier version of this docstring claimed a missing
    file raised ``ReplayMiss``; the code did not, and the docstring was wrong. It
    is corrected here rather than left as a second, conflicting source of truth.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..provider_contract import find_violations_in_mapping
from ..redaction import scrub_str
from ..schemas import AEPEnvelope, Assessment, SessionState
from .base import JevProvider

logger = logging.getLogger("jcc.providers.record_replay")

#: Schema name pinned so future format changes are detected rather than silently
#: mis-parsed. Bumping this constant is the single place to change the on-disk
#: contract.
REPLAY_SCHEMA = "jcc-replay/2"

#: The one previous version, named so the loader can say something useful
#: instead of a generic "wrong version".
REPLAY_SCHEMA_V1 = "jcc-replay/1"

#: Where a recording came from. ``provider_source`` answers "what produced these
#: bytes", which is a different question from "what is replaying them".
PROVIDER_SOURCES = ("real", "mock", "manual", "fixture")

#: Every field a v2 entry must carry. Checked by name so a missing field produces
#: a message naming it, rather than a KeyError deep in the loader.
REQUIRED_ENTRY_FIELDS = (
    "schema_version",
    "recording_id",
    "created_at",
    "provider_source",
    "envelope",
    "input_digest",
    "assessment",
    "fixture_digest",
)

#: Keys whose mere presence in a recording is grounds for rejection.
#:
#: DELIBERATELY NARROWER THAN ``redaction._SECRET_KEY_RE``, and matched by FULL
#: key equality rather than substring. That kernel pattern is used to decide what
#: to SCRUB before persistence, where over-matching is harmless (a redacted
#: "author" field costs an audit nicety). Here over-matching REJECTS a recording,
#: which is destructive, and ``_SECRET_KEY_RE`` would flag any key containing
#: "auth" -- including "author" and "authority", both of which are plausible in
#: agent telemetry. Value-level detection below DOES reuse the kernel's
#: ``scrub_str``, which is where embedded secrets actually hide.
_UNAMBIGUOUS_SECRET_KEYS = frozenset({
    "api_key", "apikey", "api_token", "access_token", "refresh_token",
    "client_secret", "private_key", "secret", "password", "passwd",
    "passphrase", "token", "authorization", "cookie", "credential",
    "credentials", "session_key", "signature",
})

#: Keys whose VALUE is a checksum by contract. Not scanned (a sha256 is a long
#: hex run, and the scrubber rightly redacts those), because both digest fields
#: are RECOMPUTED AND COMPARED on load -- validation, not pattern-matching.
_DIGEST_LIKE_KEY_RE = re.compile(r"(digest|sha256|hash|checksum)", re.IGNORECASE)


# --- Errors --------------------------------------------------------------- #

class ReplayError(Exception):
    """Base class for all record/replay errors."""


class ReplayMiss(ReplayError):
    """The exact input was not present in the recording file."""


class ReplayCorrupt(ReplayError):
    """The recording file could not be read, parsed, or verified."""


class ReplayVersion(ReplayError):
    """The recording file's schema tag is not what this provider expects."""


# --- Canonical digests ----------------------------------------------------- #
#: ONE implementation, used by the recorder (``build_entry``) AND by the
#: verifier. A second copy of the digest rule is how "the recorder and the
#: checker disagree" happens, and it fails silently in the direction of a miss.

def _as_json(value: Any) -> Any:
    """Canonical JSON-able form of a model, a dict, or ``None``."""
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _digest(obj: Any) -> str:
    canon = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def input_digest_for(envelope: Any, session: Any = None) -> str:
    """Digest of the EXACT input pair. The replay match key (§9A)."""
    return _digest({"envelope": _as_json(envelope), "session": _as_json(session)})


def fixture_digest_for(assessment: Any) -> str:
    """Digest of the recorded assessment payload. The tamper seal (§9A)."""
    return _digest(_as_json(assessment))


# --- Secret admission ------------------------------------------------------ #

def _secret_path(value: Any, path: str = "$", *,
                 parent_key: Optional[str] = None) -> Optional[str]:
    """First JSON path holding secret-shaped content, or ``None``.

    Returns a PATH, not a value: the error must tell an operator where to look
    without ever echoing what was found.

    PRECISION POLICY (measured, not guessed). The first version of this function
    scanned every string leaf with the kernel's ``scrub_str``, which redacts
    ≥48-char hex runs. Every recording entry therefore failed its own write with
    "secret-shaped content at $.input_digest": a sha256 IS a long hex run, so the
    digests this module exists to add looked exactly like the thing it forbids.
    A false positive here is not a harmless nuisance -- it makes recording
    impossible -- so digests are excluded BY KEY NAME.

    That exclusion is safe because those fields are VALIDATED, not merely
    scanned: ``_check_input_digest`` and ``_check_fixture_digest`` recompute both
    values and reject any entry where they do not match, which is strictly
    stronger than looking for a hex pattern. ``redaction.py`` documents the same
    kind of precision policy for the same reason (its §"Precision policy"
    comment keeps git SHAs and UUIDs readable on purpose).
    """
    if isinstance(value, dict):
        for k, v in value.items():
            child = f"{path}.{k}"
            if isinstance(k, str):
                norm = k.strip().lower().replace("-", "_")
                if norm in _UNAMBIGUOUS_SECRET_KEYS and v not in (None, "", False):
                    return child
                found = _secret_path(v, child, parent_key=norm)
            else:
                found = _secret_path(v, child, parent_key=None)
            if found:
                return found
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            found = _secret_path(v, f"{path}[{i}]", parent_key=parent_key)
            if found:
                return found
    elif isinstance(value, str):
        if parent_key and _DIGEST_LIKE_KEY_RE.search(parent_key):
            # A digest/hash field holds hex by contract and is validated
            # elsewhere. Scanning it can only produce false positives.
            return None
        # Reuses the kernel's value-pattern scrubber: the SAME detector the
        # persistence path uses, so this cannot drift from it.
        if scrub_str(value) != value:
            return path
    return None


# --- Recording writer ------------------------------------------------------ #

def build_entry(
    *,
    envelope: Any,
    assessment: Any,
    session: Any = None,
    recording_id: str,
    created_at: str,
    provider_source: str = "fixture",
    captured_by: str = "jcc",
) -> Dict[str, Any]:
    """Build one v2 recording entry, computing both digests from the inputs.

    Exported so that the RECORDER does not re-derive the digest rules. Every
    caller that writes a recording should go through this function; a recording
    built by hand is a recording whose digests may not match what the verifier
    computes, and that failure shows up as a miss rather than as an error.
    """
    if provider_source not in PROVIDER_SOURCES:
        raise ValueError(
            f"provider_source {provider_source!r} is not one of {PROVIDER_SOURCES}"
        )
    env_json = _as_json(envelope)
    sess_json = _as_json(session)
    asd_json = _as_json(assessment)
    entry = {
        "schema_version": REPLAY_SCHEMA,
        "recording_id": recording_id,
        "created_at": created_at,
        "provider_source": provider_source,
        "captured_by": captured_by,
        "envelope": env_json,
        "session": sess_json,
        "input_digest": _digest({"envelope": env_json, "session": sess_json}),
        "assessment": asd_json,
        "fixture_digest": _digest(asd_json),
    }
    bad = _secret_path(entry)
    if bad:
        raise ValueError(
            f"refusing to write a recording entry containing secret-shaped "
            f"content at {bad}; recordings are committed to version control"
        )
    # §9 — the writer must not emit a recording its own loader will reject. A
    # recorder that can write an unloadable file turns a boundary violation into
    # a mystery at load time; refusing here names the offending line at capture.
    bad = find_violations_in_mapping(asd_json)
    if bad:
        raise ValueError(
            f"refusing to write a recording entry whose assessment is outside "
            f"the §9 provider boundary ({', '.join(bad)}); a scorer may emit only "
            f"its eight outputs and must not choose a directive"
        )
    return entry


def write_recording(path: Path, entries: Iterable[Dict[str, Any]]) -> int:
    """Write JSONL with LF endings. Returns the entry count."""
    entries = list(entries)
    text = "".join(json.dumps(e, sort_keys=True) + "\n" for e in entries)
    Path(path).write_text(text, encoding="utf-8", newline="\n")
    return len(entries)


# --- Provider ------------------------------------------------------------- #

class RecordReplayProvider(JevProvider):
    """Replay provider: returns pre-recorded Assessments verbatim."""

    name = "replay"
    mode = "REPLAY"   # §7D — declared, never inferred
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
        self._recording_id: Optional[str] = None
        self._created_at: Optional[str] = None
        self._load_error: Optional[str] = None
        if path is not None:
            self._load(path)

    # -- I/O --------------------------------------------------------------- #

    def _load(self, path: Path) -> None:
        """Load and VERIFY the recording. Fail closed on anything unverifiable.

        A missing file is NOT an exception -- it is a configuration state that
        ``is_available()`` reports as False so ``build_provider`` can route to
        ``DisabledProvider`` on its own terms.

        Everything else IS an exception, including the cases an earlier version
        accepted: an empty file, a duplicate lookup key, and a file whose own
        digests do not recompute. Each of those means the recording cannot be
        trusted as evidence, and serving it anyway would be a silent fidelity
        loss -- the failure mode this provider exists to prevent.
        """
        if not path.is_file():
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
            if not isinstance(entry, dict):
                raise ReplayCorrupt(
                    "recording %s line %d is not a JSON object" % (path, lineno))

            self._check_version(entry, path, lineno)
            self._check_required_fields(entry, path, lineno)
            self._check_provider_source(entry, path, lineno)
            self._check_fixture_digest(entry, path, lineno)
            self._check_assessment_within_boundary(entry, path, lineno)
            self._check_input_digest(entry, path, lineno)
            self._check_recording_id(entry, path, lineno)
            self._check_no_secrets(entry, path, lineno)

            key = self._lookup_key(entry.get("envelope") or {})
            if key in self._index:
                # NO LONGER last-wins. Two captures for one
                # (session, event, delivery) triple means a replayed envelope
                # has more than one possible answer, and picking one silently --
                # whichever happens to be later in the file -- is a fidelity
                # choice the operator never made. An ambiguous recording is
                # corrupt; the fix is to record a new file, not to guess.
                raise ReplayCorrupt(
                    "recording %s line %d repeats lookup key %r (first seen "
                    "earlier in this file). An ambiguous recording cannot be "
                    "replayed faithfully -- write a separate recording instead."
                    % (path, lineno, key))
            self._index[key] = entry
            count += 1

        if count == 0:
            # A named recording with no entries is a broken recording, not "no
            # recording": the operator asked for replay and got a file that
            # cannot replay anything. Failing loudly here means the fallback to
            # DisabledProvider cannot be mistaken for a working replay.
            raise ReplayCorrupt(
                "recording %s exists but contains no entries; a named recording "
                "with nothing in it cannot replay anything" % path)

        self._loaded_from = str(path)
        self._schema_version = REPLAY_SCHEMA
        logger.info(
            "RecordReplayProvider loaded %d entries from %s (schema=%s, recording_id=%s)",
            count, path, REPLAY_SCHEMA, self._recording_id)

    # -- verification helpers ---------------------------------------------- #

    @staticmethod
    def _lookup_key(env: Dict[str, Any]) -> tuple:
        return (
            env.get("session_id", ""),
            env.get("event_id", ""),
            env.get("delivery_id", ""),
        )

    @staticmethod
    def _migrate_hint(tag: Optional[str]) -> str:
        if tag == REPLAY_SCHEMA_V1:
            return ("this is a jcc-replay/1 recording; v1 carried no digests, so "
                    "its entries cannot be verified and are not accepted. Re-record "
                    "it under jcc-replay/2.")
        return ("re-record it under %s." % REPLAY_SCHEMA)

    def _check_version(self, entry: Dict[str, Any], path: Path, lineno: int) -> None:
        tag = entry.get("schema_version")
        if tag is None and "schema" in entry:
            # Legacy key. Read the version out of it so a v1 file gets the
            # migration message instead of a generic "wrong version".
            tag = entry.get("schema")
        if tag != REPLAY_SCHEMA:
            raise ReplayVersion(
                "recording %s line %d schema_version=%r expected %r; %s"
                % (path, lineno, tag, REPLAY_SCHEMA, self._migrate_hint(tag)))

    @staticmethod
    def _check_required_fields(entry: Dict[str, Any], path: Path, lineno: int) -> None:
        missing = [f for f in REQUIRED_ENTRY_FIELDS if f not in entry]
        if missing:
            raise ReplayCorrupt(
                "recording %s line %d is missing required field(s): %s"
                % (path, lineno, ", ".join(missing)))

    @staticmethod
    def _check_provider_source(entry: Dict[str, Any], path: Path, lineno: int) -> None:
        src = entry.get("provider_source")
        if src not in PROVIDER_SOURCES:
            raise ReplayCorrupt(
                "recording %s line %d provider_source=%r is not one of %s"
                % (path, lineno, src, PROVIDER_SOURCES))

    @staticmethod
    def _check_fixture_digest(entry: Dict[str, Any], path: Path, lineno: int) -> None:
        expected = entry.get("fixture_digest")
        actual = fixture_digest_for(entry.get("assessment"))
        if expected != actual:
            raise ReplayCorrupt(
                "recording %s line %d fixture_digest MISMATCH: recorded %r but the "
                "assessment hashes to %r. The recorded assessment was edited after "
                "capture, so it is no longer evidence."
                % (path, lineno, expected, actual))

    @staticmethod
    def _check_input_digest(entry: Dict[str, Any], path: Path, lineno: int) -> None:
        expected = entry.get("input_digest")
        actual = _digest({
            "envelope": entry.get("envelope"),
            "session": entry.get("session"),
        })
        if expected != actual:
            raise ReplayCorrupt(
                "recording %s line %d input_digest MISMATCH: recorded %r but the "
                "stored envelope/session hash to %r. The entry is self-inconsistent."
                % (path, lineno, expected, actual))

    def _check_recording_id(self, entry: Dict[str, Any], path: Path, lineno: int) -> None:
        rid = entry.get("recording_id")
        if self._recording_id is None:
            self._recording_id = rid
        elif rid != self._recording_id:
            raise ReplayCorrupt(
                "recording %s line %d recording_id=%r but earlier lines use %r. "
                "Two recordings were concatenated; each file must be one recording."
                % (path, lineno, rid, self._recording_id))

    @staticmethod
    def _check_no_secrets(entry: Dict[str, Any], path: Path, lineno: int) -> None:
        bad = _secret_path(entry)
        if bad:
            raise ReplayCorrupt(
                "recording %s line %d contains secret-shaped content at %s. "
                "Recordings are committed to version control; the value is NOT "
                "echoed here and must not be committed." % (path, lineno, bad))

    @staticmethod
    def _check_assessment_within_boundary(
        entry: Dict[str, Any], path: Path, lineno: int
    ) -> None:
        """§9 — a recording whose assessment names an action is a bad recording.

        Checked at LOAD time rather than left to the §9 hook at ``assess`` time,
        for two reasons. The error kind matters to whoever has to fix it: "the
        recording is corrupt" points at the recording, while a violation surfacing
        on replay points at the code. And this is the only moment the LINE NUMBER
        is known, which is what makes the recording repairable.

        The rules are the same ones the live channel gets, via the same engine --
        a recorded assessment is held to the boundary, not to a laxer copy of it.
        """
        bad = find_violations_in_mapping(entry.get("assessment") or {})
        if bad:
            raise ReplayCorrupt(
                "recording %s line %d records an assessment outside the §9 "
                "provider boundary: %s. A scorer may emit only its eight outputs "
                "and must not choose a directive; re-record this entry from a "
                "compliant provider rather than editing the assessment by hand."
                % (path, lineno, ", ".join(bad)))

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

    @property
    def recording_id(self) -> Optional[str]:
        return self._recording_id

    def assess(self, event: AEPEnvelope, session: SessionState) -> Assessment:
        """Return the recorded Assessment verbatim, or raise ReplayMiss.

        EXACT match only (§9A). The index key narrows the search; the digest then
        decides. An index hit whose ``input_digest`` differs is reported as a MISS
        with a message that says so, because the recording genuinely does not
        contain this input -- reusing ids across different payloads is precisely
        the case the digest exists to catch.
        """
        want = input_digest_for(event, session)
        key = (event.session_id, event.event_id, event.delivery_id)
        entry = self._index.get(key)
        if entry is None:
            raise ReplayMiss(
                "no recording matches envelope (session=%r event=%r delivery=%r); "
                "loaded %d entries from %s"
                % (event.session_id, event.event_id, event.delivery_id,
                   len(self._index), self._loaded_from or "(none)"))

        if entry.get("input_digest") != want:
            raise ReplayMiss(
                "recording index key (session=%r event=%r delivery=%r) HIT but the "
                "input_digest differs: recorded %r, this input hashes to %r. §9A "
                "requires an exact match -- there is no nearest match and no "
                "fallback. The payload behind these ids is not the recorded one."
                % (event.session_id, event.event_id, event.delivery_id,
                   entry.get("input_digest"), want))

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
        # recording file. §9A fields are surfaced here too, so a consumer can
        # see which recording and which exact input produced the number.
        raw_assessment.setdefault("raw", {})
        if isinstance(raw_assessment["raw"], dict):
            raw_assessment["raw"]["replay_provenance"] = {
                "schema": self._schema_version,
                "schema_version": self._schema_version,
                "recording_id": entry.get("recording_id"),
                "created_at": entry.get("created_at"),
                "provider_source": entry.get("provider_source"),
                "input_digest": entry.get("input_digest"),
                "fixture_digest": entry.get("fixture_digest"),
                "recording_sha256": self.recording_sha256,
                "recording_path": self._loaded_from,
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
    "REPLAY_SCHEMA_V1",
    "PROVIDER_SOURCES",
    "REQUIRED_ENTRY_FIELDS",
    "ReplayError",
    "ReplayMiss",
    "ReplayCorrupt",
    "ReplayVersion",
    "RecordReplayProvider",
    "build_entry",
    "write_recording",
    "input_digest_for",
    "fixture_digest_for",
]
