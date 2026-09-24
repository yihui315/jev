"""Deterministic arbitration kernel — the ONE mapping authority (ADR-0008 D-32).

Background (why this module exists)
-----------------------------------
Before P3 the *provider* chose the directive: ``DeterministicMockProvider``
hard-coded 0.8/0.6/0.4 into ``escalate``/``pause``/``steer``/``finished``
(``providers/mock.py:62-69``) and ``policy.decide`` merely walked those flags
(``policy.py:30-49``), while ``config.policy.thresholds`` was dead config. That
made ADR-0002's "Jev outputs probabilities ONLY; Policy produces the directive"
untrue in practice. D-32 fixes it by making this module the single authority:
``policy.decide`` (and ``supervisor.arbitration``) become thin callers.

Contract
--------
* Pure and TOTAL: no I/O, no clock, no RNG, no model/provider call, no logging,
  no environment read. Everything is passed in. Every input — including
  ``None``, ``NaN``, wrong types, missing keys — resolves to exactly one action.
* The rule table's action column, read top to bottom, MUST equal
  ``schemas.POLICY_PRIORITY`` (ESCALATE > PAUSE > VERIFY > STEER > RETRY >
  FINISH > CONTINUE). Row order IS the tie-break; there is no second mechanism.
* ``EXACT_BLOCKER`` (D-35 reading R1) fires the top row: ESCALATE, never a
  resuming action, never executable.

Intentional non-input: ``work_off_track``
----------------------------------------
``Signals.work_off_track`` is carried for *recording* but is deliberately NOT
referenced by any row, per ADR-0008 D-16 / lead Ruling F (no goal/plan field
exists, so off-track is not a decidable input in P3). A test asserts that
changing it can never change the outcome.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .schemas import POLICY_PRIORITY, Assessment, DirectiveAction

#: Bumped whenever ROWS or the normalisation table changes. It is inside the
#: signed directive payload (D-33), so a table change invalidates old signatures.
POLICY_TABLE_VERSION = "p3.0.0"

#: A normalised probability p is treated as True when ``p >= 0.5`` (the boundary
#: is INCLUSIVE, and it is documented because a boundary is a tie).
FIELD_DECISION_THRESHOLD = 0.5

#: Scores are quantised before comparison so that float noise (0.7999999999 vs
#: 0.8) cannot flip a row. Documented epsilon, not an accident.
_SCORE_QUANTUM = 6

#: Band/risk_level ordinals. Anything else is UNKNOWN (``None``).
_BAND_ORDINALS: Dict[str, int] = {"low": 0, "medium": 1, "high": 2, "critical": 3}

#: The six fixed *boolean* Jev signal fields arbitration consumes. ``risk_level``
#: is carried separately as the ``band`` ordinal because it is categorical, and
#: ``work_off_track`` is **recorded only** (R6-3 / R5-7: it is NOT a decision
#: input) so an unknown value can never be a blocker for it.
REQUIRED_SIGNAL_FIELDS: Tuple[str, ...] = (
    "meaningful_progress",
    "worker_stuck",
    "requirements_satisfied",
    "needs_verification",
    "ready_to_finish",
    "needs_human",
)

#: Signals that must be carried on the assessment but are deliberately NOT read
#: by any row or by ``find_blockers`` (ADR-0008 D-16, R5 ruling #7, R6-3). Kept
#: as an explicit constant so the omission is a documented decision, not a gap.
RECORDED_SIGNAL_FIELDS: Tuple[str, ...] = ("work_off_track",)

#: Fixed blocker vocabulary (D-35). Sorted + de-duplicated so the recorded
#: reason list is byte-stable for a given input set.
BLOCKER_PROVIDER_ERROR = "provider_error"
BLOCKER_PROVIDER_UNVERIFIED = "provider_unverified"
BLOCKER_REQUIRED_SIGNAL_UNKNOWN = "required_signal_unknown"
BLOCKER_RISK_LEVEL_UNKNOWN = "risk_level_unknown"
BLOCKER_SCORE_UNKNOWN = "score_unknown"
BLOCKER_CONTRADICTORY_SIGNALS = "contradictory_signals"
BLOCKER_PROBABILITY_OUT_OF_RANGE = "probability_out_of_range"
BLOCKER_ASSESSMENT_INVALID = "assessment_invalid"

#: Two numeric risk channels from the SAME source must agree within this epsilon.
#: This is threshold-independent by design (see ``find_blockers``).
_SELF_CONSISTENCY_EPSILON = 1e-6

#: Sentinel distinguishing "assessment was not supplied" from "supplied as None".
#: ``arbitrate`` is legitimately callable with already-normalised ``Signals`` and
#: no assessment at all; that must NOT be reported as an invalid assessment. An
#: explicitly passed ``None`` (or a wrong-typed object) still is.
_UNSET: Any = object()


# --------------------------------------------------------------------------- #
# Normalisation primitives
# --------------------------------------------------------------------------- #
def coerce_bool(value: Any) -> Optional[bool]:
    """Total, deterministic coercion to ``True``/``False``/``None`` (None=UNKNOWN).

    Uses an explicit table rather than Python truthiness, because ``bool("false")
    is True`` is exactly the kind of silent inversion this kernel must not have.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):  # bool is an int subclass; handled above
        if value == 0:
            return False
        if value == 1:
            return True
        return None
    if isinstance(value, float):
        if math.isnan(value) or not (0.0 <= value <= 1.0):
            return None
        return value >= FIELD_DECISION_THRESHOLD
    if isinstance(value, str):
        token = value.strip().casefold()
        if token in ("true", "yes", "1"):
            return True
        if token in ("false", "no", "0"):
            return False
        return None
    return None


def coerce_score(value: Any) -> Optional[float]:
    """Total coercion to a quantised score in [0, 1], else ``None`` (UNKNOWN)."""
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None
    score = float(value)
    if math.isnan(score) or math.isinf(score) or not (0.0 <= score <= 1.0):
        return None
    return round(score, _SCORE_QUANTUM)


def coerce_band(value: Any) -> Optional[int]:
    """Total coercion of a band/risk_level label to its ordinal, else ``None``."""
    if not isinstance(value, str):
        return None
    return _BAND_ORDINALS.get(value.strip().casefold())


# NOTE (design correction, 2026-09-24): an earlier iteration derived a "band
# implied by score" and raised a blocker when it disagreed with the assessment's
# declared band. That was WRONG, and was removed after it failed empirically:
#   * Round 5 makes thresholds per-agent (``{default, per_agent}``). Deriving a
#     band from the *policy's* cuts and comparing it to the band the *provider*
#     computed with its own cuts makes a disagreement inevitable as soon as any
#     per-agent threshold is set — i.e. it would turn R5's headline feature into
#     a permanent EXACT_BLOCKER for that agent.
#   * It also made rows unsatisfiable: with complementary cuts, medium risk
#     could never reach CONTINUE.
# Threshold-dependent "contradictions" therefore cannot be a blocker. The
# threshold-INDEPENDENT self-consistency check in ``find_blockers`` replaces it.
# '''


# --------------------------------------------------------------------------- #
# Thresholds
# --------------------------------------------------------------------------- #
#: Keys the kernel understands. An unknown key is REJECTED (not ignored): a
#: silently-ignored threshold is how a misconfiguration becomes a no-op.
#: Round 6 (ADR-0008 D-24, R6-2): the cut set is 4 keys — the 0.4 cut is
#: ``steer_risk`` (not a VERIFY band), and VERIFY is evidence-driven.
KNOWN_THRESHOLD_KEYS = frozenset(
    {"escalate_risk", "pause_risk", "steer_risk", "finish_risk"}
)

#: Keys that were deliberately RETIRED. Their presence means the config predates
#: the freeze, so we fail LOUDLY instead of silently honouring a toggle/cut that
#: no longer exists. ``verify_risk`` is retired by Round 6 (R6-2): VERIFY is no
#: longer a risk band, so a config that still carries a verify cut is mis-shaped.
RETIRED_THRESHOLD_KEYS = frozenset({"enabled", "verify_risk"})


@dataclass(frozen=True)
class Thresholds:
    """One threshold set (ADR-0008 D-24 / R6-2). Lower cuts are inclusive.

    ``FINISH`` is the only *upper* cut: it fires when ``score < finish_risk``,
    reproducing P2 (``mock.py:62-69``). The 0.4 band is ``steer_risk``; there is
    deliberately no ``verify_risk`` — VERIFY is evidence-driven, never a band.
    """

    escalate_risk: float = 0.8
    pause_risk: float = 0.6
    steer_risk: float = 0.4
    finish_risk: float = 0.1

    def __post_init__(self) -> None:
        for name in sorted(KNOWN_THRESHOLD_KEYS):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"threshold {name!r} must be a number, got {type(value).__name__}")
            if math.isnan(float(value)) or not (0.0 <= float(value) <= 1.0):
                raise ValueError(f"threshold {name!r} must be within [0, 1], got {value!r}")
        # Ordering invariant: a mis-ordered set silently inverts behaviour. The
        # three risk cuts descend; FINISH's upper cut sits at or below STEER's.
        if not (self.escalate_risk >= self.pause_risk >= self.steer_risk):
            raise ValueError(
                "thresholds must satisfy escalate_risk >= pause_risk >= steer_risk, got "
                f"{self.escalate_risk!r}/{self.pause_risk!r}/{self.steer_risk!r}"
            )
        if not (self.finish_risk <= self.steer_risk):
            raise ValueError(
                "thresholds must satisfy finish_risk <= steer_risk, got "
                f"{self.finish_risk!r}/{self.steer_risk!r}"
            )

    def as_dict(self) -> Dict[str, float]:
        return {name: float(getattr(self, name)) for name in sorted(KNOWN_THRESHOLD_KEYS)}


def _parse_thresholds(node: Any, where: str) -> Thresholds:
    if not isinstance(node, Mapping):
        raise TypeError(f"{where} must be a mapping, got {type(node).__name__}")
    keys = set(node.keys())
    retired = keys & RETIRED_THRESHOLD_KEYS
    if retired:
        raise ValueError(
            f"{where} contains retired key(s) {sorted(retired)}; ADR-0008 D-24 (Round 5) "
            "removed the threshold 'enabled' toggle and mandates no such key. Fix the config."
        )
    unknown = keys - KNOWN_THRESHOLD_KEYS
    if unknown:
        raise ValueError(
            f"{where} contains unknown threshold key(s) {sorted(unknown)}; "
            f"known keys are {sorted(KNOWN_THRESHOLD_KEYS)}"
        )
    return Thresholds(**{k: node[k] for k in keys})


@dataclass(frozen=True)
class ThresholdsConfig:
    """The live threshold surface: ``{default, per_agent}`` (ADR-0008 D-24 R5).

    There is deliberately **no ``enabled`` key**: thresholding is always active,
    which is what makes the provider-vs-policy authority single-valued.
    """

    default: Thresholds = field(default_factory=Thresholds)
    per_agent: Mapping[str, Thresholds] = field(default_factory=dict)

    @classmethod
    def from_config(cls, cfg: Any = None) -> "ThresholdsConfig":
        """Parse ``p3.shadow.thresholds``.

        Round 5 shape: ``{default: {...}, per_agent: {<agent>: {...}}}``.
        The pre-Round-5 shape (an ``enabled`` toggle plus *flat* per-agent keys
        at the top level) is REJECTED rather than mis-read — see
        ``RETIRED_THRESHOLD_KEYS`` and the unknown-key check.
        """
        if cfg is None or cfg == {}:
            return cls()
        if not isinstance(cfg, Mapping):
            raise TypeError(
                f"p3.shadow.thresholds must be a mapping, got {type(cfg).__name__}"
            )
        retired_top = set(cfg.keys()) & RETIRED_THRESHOLD_KEYS
        if retired_top:
            raise ValueError(
                f"p3.shadow.thresholds contains retired key(s) {sorted(retired_top)}; "
                "ADR-0008 D-24 (Round 5) removed the threshold 'enabled' toggle and "
                "mandates the exact shape {default, per_agent}. Fix the config."
            )
        unknown_top = set(cfg.keys()) - {"default", "per_agent"}
        if unknown_top:
            raise ValueError(
                "p3.shadow.thresholds has unknown top-level key(s) "
                f"{sorted(unknown_top)}; Round 5 shape is exactly "
                "{default, per_agent}. (Flat per-agent keys and an 'enabled' "
                "toggle are the pre-Round-5 shape and are rejected.)"
            )
        default = _parse_thresholds(cfg.get("default") or {}, "p3.shadow.thresholds.default")
        per_agent_raw = cfg.get("per_agent") or {}
        if not isinstance(per_agent_raw, Mapping):
            raise TypeError("p3.shadow.thresholds.per_agent must be a mapping")
        per_agent: Dict[str, Thresholds] = {}
        for agent, node in per_agent_raw.items():
            if not isinstance(agent, str):
                raise TypeError(f"per_agent keys must be strings, got {type(agent).__name__}")
            # An empty mapping inherits the default wholesale.
            if not node:
                per_agent[agent] = default
                continue
            merged = default.as_dict()
            for key, value in dict(node).items():
                if key in RETIRED_THRESHOLD_KEYS or key not in KNOWN_THRESHOLD_KEYS:
                    raise ValueError(
                        f"p3.shadow.thresholds.per_agent.{agent} has unknown/retired key {key!r}"
                    )
                merged[key] = value
            # Re-validate the merged set (ordering + range) under this agent.
            per_agent[agent] = _parse_thresholds(merged, f"p3.shadow.thresholds.per_agent.{agent}")
        return cls(default=default, per_agent=dict(per_agent))

    def for_agent(self, agent: Any) -> Thresholds:
        """Thresholds for ``agent``; falls back to ``default``."""
        if isinstance(agent, str):
            found = self.per_agent.get(agent)
            if found is not None:
                return found
        return self.default

    def snapshot(self) -> Dict[str, Any]:
        """Canonical, JSON-safe view recorded on every shadow decision (D-21)."""
        return {
            "default": self.default.as_dict(),
            "per_agent": {a: t.as_dict() for a, t in sorted(self.per_agent.items())},
        }

    def config_hash(self) -> str:
        """sha256 over the canonical snapshot — the ``policy_config_hash`` (D-21)."""
        canon = json.dumps(self.snapshot(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Signals
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Signals:
    """Normalised, total view of a Jev assessment.

    ``None`` means UNKNOWN (distinct from ``False``). Every ``None`` on a
    required field is absorbed by the EXACT_BLOCKER rule, which is why the row
    predicates below never need an UNKNOWN branch.
    """

    meaningful_progress: Optional[bool] = None
    worker_stuck: Optional[bool] = None
    work_off_track: Optional[bool] = None      # recorded only; NOT a decision input
    requirements_satisfied: Optional[bool] = None
    needs_verification: Optional[bool] = None
    ready_to_finish: Optional[bool] = None
    needs_human: Optional[bool] = None
    band: Optional[int] = None                 # 0..3 ordinal; None = UNKNOWN
    score: Optional[float] = None
    provider_verified: bool = False            # fail-closed default (D-31)
    evidence_complete: Optional[bool] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "meaningful_progress": self.meaningful_progress,
            "worker_stuck": self.worker_stuck,
            "work_off_track": self.work_off_track,
            "requirements_satisfied": self.requirements_satisfied,
            "needs_verification": self.needs_verification,
            "ready_to_finish": self.ready_to_finish,
            "needs_human": self.needs_human,
            "band": self.band,
            "score": self.score,
            "provider_verified": self.provider_verified,
            "evidence_complete": self.evidence_complete,
        }


def _raw_signals(assessment: Any) -> Mapping[str, Any]:
    raw = getattr(assessment, "raw", None)
    if isinstance(raw, Mapping):
        signals = raw.get("signals")
        if isinstance(signals, Mapping):
            return signals
    return {}


def _read_field(assessment: Any, name: str) -> Tuple[Any, bool]:
    """Read a signal: typed attribute first, then ``raw['signals'][name]``.

    Returns ``(value, provided)``. ``provided`` is True whenever the field is
    present at all — **including an explicit ``None``** — so that an explicit
    "unknown" is never silently replaced by a compatibility fallback. (Conflating
    absent with null was a real bug: it let ``needs_verify=False`` masquerade as
    a present ``needs_verification``.)
    """
    if hasattr(assessment, name):
        value = getattr(assessment, name)
        if value is not None:
            return value, True
    signals = _raw_signals(assessment)
    if name in signals:
        return signals[name], True
    return None, False


def normalize(assessment: Any) -> Signals:
    """Total normalisation. Never raises; every surprise becomes UNKNOWN."""
    if assessment is None or not isinstance(assessment, Assessment):
        # UNKNOWN signals; ``find_blockers`` reports assessment_invalid when an
        # assessment was actually supplied and was the wrong type.
        return Signals()

    needs_verification, nv_provided = _read_field(assessment, "needs_verification")
    if not nv_provided:
        # P1 field-name compatibility (schemas.py:118). Applied ONLY when the P3
        # signal was not supplied at all — an explicit null stays UNKNOWN.
        needs_verification = getattr(assessment, "needs_verify", None)

    evidence_complete = None
    raw = getattr(assessment, "raw", None)
    if isinstance(raw, Mapping) and "evidence_complete" in raw:
        evidence_complete = coerce_bool(raw.get("evidence_complete"))

    score = coerce_score(getattr(assessment, "risk_score", None))
    if score is None:
        probabilities = getattr(assessment, "probabilities", None)
        if isinstance(probabilities, Mapping):
            score = coerce_score(probabilities.get("risk"))

    return Signals(
        meaningful_progress=coerce_bool(_read_field(assessment, "meaningful_progress")[0]),
        worker_stuck=coerce_bool(_read_field(assessment, "worker_stuck")[0]),
        work_off_track=coerce_bool(_read_field(assessment, "work_off_track")[0]),
        requirements_satisfied=coerce_bool(_read_field(assessment, "requirements_satisfied")[0]),
        needs_verification=coerce_bool(needs_verification),
        ready_to_finish=coerce_bool(_read_field(assessment, "ready_to_finish")[0]),
        needs_human=coerce_bool(_read_field(assessment, "needs_human")[0]),
        band=coerce_band(getattr(assessment, "band", None)),
        score=score,
        provider_verified=bool(getattr(assessment, "provider_verified", False)),
        evidence_complete=evidence_complete,
    )


def _numeric_channel_conflict(assessment: Any) -> bool:
    """True when the provider's two numeric risk channels disagree.

    ``risk_score`` and ``probabilities['risk']`` are two representations of the
    SAME quantity from the SAME source. When both are present and differ by more
    than ``_SELF_CONSISTENCY_EPSILON``, the provider contradicts itself and no
    deterministic rule can attribute the disagreement — an EXACT_BLOCKER (D-35).

    This is deliberately threshold-INDEPENDENT: it compares two *numbers*, never
    a number against a band label. (The discarded alternative compared a score to
    a *policy*-derived band; Round 5 makes thresholds per-agent, so the provider's
    band and the policy's cuts are not required to agree — see the design note
    above.)
    """
    if not isinstance(assessment, Assessment):
        return False
    probabilities = getattr(assessment, "probabilities", None)
    if not isinstance(probabilities, Mapping) or "risk" not in probabilities:
        return False
    left = coerce_score(getattr(assessment, "risk_score", None))
    right = coerce_score(probabilities.get("risk"))
    if left is None or right is None:
        return False
    return abs(left - right) > _SELF_CONSISTENCY_EPSILON


def find_blockers(
    signals: Signals,
    assessment: Any = _UNSET,
    thresholds: Optional[Thresholds] = None,
) -> Tuple[str, ...]:
    """Return the sorted, de-duplicated EXACT_BLOCKER reason codes (D-35).

    An *exact blocker* means the signals cannot be trusted or attributed, which
    is strictly worse than incomplete evidence (that case is VERIFY, unchanged
    from P1).

    ``thresholds`` is accepted only for call-site symmetry and is deliberately
    **never read**: nothing here may depend on a cut point (see the design note
    above). ``assessment`` defaults to the ``_UNSET`` sentinel so that a caller
    passing pre-normalised ``Signals`` is not mislabelled as an invalid
    assessment; an explicitly supplied non-``Assessment`` still is.
    """
    reasons: List[str] = []

    if assessment is not _UNSET:
        if not isinstance(assessment, Assessment):
            reasons.append(BLOCKER_ASSESSMENT_INVALID)
        else:
            declared = getattr(assessment, "blocker_kind", "NONE")
            if isinstance(declared, str) and declared.strip().upper() == "EXACT":
                # The provider already declared an exact blocker (D-26 provider_error
                # path). Carry its reasons through.
                declared_reasons = getattr(assessment, "blocker_reasons", None) or []
                if isinstance(declared_reasons, (list, tuple, set)) and declared_reasons:
                    reasons.extend(str(r) for r in declared_reasons)
                else:
                    reasons.append(BLOCKER_PROVIDER_ERROR)
            if not bool(getattr(assessment, "provider_verified", False)):
                reasons.append(BLOCKER_PROVIDER_UNVERIFIED)
            probabilities = getattr(assessment, "probabilities", None)
            if isinstance(probabilities, Mapping):
                for value in probabilities.values():
                    bad = isinstance(value, bool) or not isinstance(value, (int, float))
                    if not bad:
                        bad = math.isnan(float(value)) or not (0.0 <= float(value) <= 1.0)
                    if bad:
                        reasons.append(BLOCKER_PROBABILITY_OUT_OF_RANGE)
                        break
            if _numeric_channel_conflict(assessment):
                reasons.append(BLOCKER_CONTRADICTORY_SIGNALS)

    for name in REQUIRED_SIGNAL_FIELDS:
        if getattr(signals, name) is None:
            reasons.append(BLOCKER_REQUIRED_SIGNAL_UNKNOWN)

    if signals.band is None:
        reasons.append(BLOCKER_RISK_LEVEL_UNKNOWN)
    if signals.score is None:
        reasons.append(BLOCKER_SCORE_UNKNOWN)

    _ = thresholds  # accepted for symmetry; must never influence the result
    return tuple(sorted(set(reasons)))


# --------------------------------------------------------------------------- #
# The rule table (row order == tie-break == POLICY_PRIORITY)
# --------------------------------------------------------------------------- #
_Predicate = Callable[[Signals, Thresholds, Tuple[str, ...]], bool]


@dataclass(frozen=True)
class Row:
    action: DirectiveAction
    predicate: _Predicate
    reason_code: str


def _r1(s: Signals, t: Thresholds, blockers: Tuple[str, ...]) -> bool:
    return bool(blockers)


def _r2(s: Signals, t: Thresholds, blockers: Tuple[str, ...]) -> bool:
    return s.needs_human is True


def _r3(s: Signals, t: Thresholds, blockers: Tuple[str, ...]) -> bool:
    return s.band == 3 or (s.score is not None and s.score >= t.escalate_risk)


def _r4(s: Signals, t: Thresholds, blockers: Tuple[str, ...]) -> bool:
    return s.worker_stuck is True or s.band == 2 or (s.score is not None and s.score >= t.pause_risk)


def _r5(s: Signals, t: Thresholds, blockers: Tuple[str, ...]) -> bool:
    # VERIFY is evidence/uncertainty only (R6-2/R6-3): no risk band. The
    # ``band is None`` (risk_level unknown) branch is unreachable because row 1
    # already absorbed it; it is kept for verbatim fidelity with §1.4.
    return s.needs_verification is True or s.evidence_complete is False or s.band is None


def _r6(s: Signals, t: Thresholds, blockers: Tuple[str, ...]) -> bool:
    # The 0.4 band maps to STEER (R6-3), not VERIFY.
    return s.meaningful_progress is False or (s.score is not None and s.score >= t.steer_risk)


def _r7(s: Signals, t: Thresholds, blockers: Tuple[str, ...]) -> bool:
    return s.requirements_satisfied is False and s.ready_to_finish is False


def _r8(s: Signals, t: Thresholds, blockers: Tuple[str, ...]) -> bool:
    # FINISH is bounded by the upper cut score < finish_risk (R6-3, reproduces P2).
    return (
        s.ready_to_finish is True
        and s.requirements_satisfied is True
        and s.needs_verification is False
        and s.band == 0
        and s.score is not None
        and s.score < t.finish_risk
    )


ROWS: Tuple[Row, ...] = (
    Row(DirectiveAction.ESCALATE, _r1, "exact_blocker"),
    Row(DirectiveAction.ESCALATE, _r2, "needs_human"),
    Row(DirectiveAction.ESCALATE, _r3, "risk_critical"),
    Row(DirectiveAction.PAUSE, _r4, "stuck_or_high_risk"),
    Row(DirectiveAction.VERIFY, _r5, "needs_verification"),
    Row(DirectiveAction.STEER, _r6, "no_progress_or_steer_band"),
    Row(DirectiveAction.RETRY, _r7, "requirements_open"),
    Row(DirectiveAction.FINISH, _r8, "ready_to_finish"),
    Row(DirectiveAction.CONTINUE, lambda s, t, b: True, "floor"),
)


def table_actions() -> List[DirectiveAction]:
    """De-duplicated row actions in row order — MUST equal POLICY_PRIORITY."""
    seen: List[DirectiveAction] = []
    for row in ROWS:
        if row.action not in seen:
            seen.append(row.action)
    return seen


@dataclass(frozen=True)
class ArbitrationResult:
    action: DirectiveAction
    reason_code: str
    matched_rows: Tuple[int, ...]
    blocked: bool
    blocker_reasons: Tuple[str, ...]
    table_version: str = POLICY_TABLE_VERSION

    def as_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action.value,
            "reason_code": self.reason_code,
            "matched_rows": list(self.matched_rows),
            "blocked": self.blocked,
            "blocker_reasons": list(self.blocker_reasons),
            "table_version": self.table_version,
        }


def arbitrate(
    signals: Signals,
    thresholds: Optional[Thresholds] = None,
    *,
    assessment: Any = _UNSET,
) -> ArbitrationResult:
    """Map normalised signals to exactly one directive action.

    First match wins; the row index is the tie-break. See module docstring.

    ``assessment`` defaults to ``_UNSET`` (not supplied). Pass the originating
    ``Assessment`` when one exists so its fail-closed fields (``provider_verified``,
    ``blocker_kind``, numeric channel agreement) can be checked; passing ``None``
    explicitly means "an assessment was expected but is missing" and is blocked.
    """
    t = thresholds if thresholds is not None else Thresholds()
    blockers = find_blockers(signals, assessment, t)

    matched: List[int] = []
    for index, row in enumerate(ROWS):
        try:
            if row.predicate(signals, t, blockers):
                matched.append(index)
        except Exception:  # noqa: BLE001 — totality: never let a predicate escape
            continue

    if not matched:  # unreachable: row 9 is unconditional. Defensive only.
        return ArbitrationResult(
            action=DirectiveAction.ESCALATE,
            reason_code="exact_blocker",
            matched_rows=(),
            blocked=True,
            blocker_reasons=blockers or (BLOCKER_ASSESSMENT_INVALID,),
        )

    winner = ROWS[matched[0]]
    return ArbitrationResult(
        action=winner.action,
        reason_code=winner.reason_code,
        matched_rows=tuple(matched),
        blocked=bool(blockers),
        blocker_reasons=blockers,
    )


def decide_action(
    assessment: Any,
    thresholds: Optional[Thresholds] = None,
) -> DirectiveAction:
    """Convenience: normalise an ``Assessment`` and arbitrate it."""
    signals = normalize(assessment)
    return arbitrate(signals, thresholds, assessment=assessment).action


def trace_hash(signals: Signals, thresholds: Thresholds, result: ArbitrationResult) -> str:
    """Stable hash of (inputs, thresholds, outcome) for the shadow record (D-7)."""
    canon = json.dumps(
        {
            "signals": signals.as_dict(),
            "thresholds": thresholds.as_dict(),
            "result": result.as_dict(),
            "table_version": POLICY_TABLE_VERSION,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def priority_index(action: DirectiveAction) -> int:
    """Lower index == higher precedence."""
    return POLICY_PRIORITY.index(action)
