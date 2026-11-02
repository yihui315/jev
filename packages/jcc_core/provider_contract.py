"""Jev provider OUTPUT BOUNDARY (§9) — what a scorer is allowed to say.

THE RULE
    Jev may output exactly eight things and nothing else:

        meaningful_progress      worker_stuck
        work_off_track           requirements_satisfied
        needs_verification       ready_to_finish
        needs_human              risk_level

    It may not run a shell, write a file, call git, click, send, deploy, pay, or
    choose a directive. The policy engine decides the action; the provider only
    supplies evidence about the work.

WHY THIS MODULE EXISTS
    Before it, that boundary was DOCUMENTATION ONLY. ``REQUIRED_SIGNAL_FIELDS``
    lists six booleans and ``RECORDED_SIGNAL_FIELDS`` one more, but nothing
    anywhere rejected a provider that emitted a NINTH signal, or one shaped like
    a directive (``{"signals": {"steer": true}}``). Every existing test asserts
    what a KNOWN provider does; none asserted that an UNKNOWN key is refused. So
    the boundary would have held only as long as no one added to it by accident --
    which is not a boundary, it is a habit.

    The check is a positive ALLOWLIST with a pinned constant. Adding a ninth
    signal means editing ``JEVI_ALLOWED_SIGNALS`` in a reviewed commit, and the
    test that pins the constant goes red until that happens.

WHERE IT IS ENFORCED
    ``JevProvider.__init_subclass__`` (``providers/base.py``) wraps every
    concrete provider's ``assess`` so its return value is checked before the
    kernel can read it. That placement is deliberate: it covers direct
    construction AND ``build_provider``, and a provider added later cannot
    forget to opt in. See that hook for why the kernel's OWN fail-closed
    synthesis (``core.CoreEngine.ingest``, ADR-0008 D-26) is correctly exempt.

THE THREE PARTITIONS (kept explicit so the boundary test is not vacuous)
    1. ``JEVI_ALLOWED_SIGNALS`` -- the seven booleans, i.e. the eight-item list
       minus ``risk_level``, which is categorical and therefore carried as a band
       plus a numeric channel rather than as a boolean.
    2. ``JEVI_META_FIELDS`` -- fields ABOUT the assessment (its validity, its
       provenance, its numeric channel). They are not claims about the work, so
       they sit outside the eight; they are enumerated so "outside the boundary"
       is a reviewed list rather than "anything else".
    3. ``FORBIDDEN_OUTPUTS`` -- the directive-shaped names. A provider that sets
       any of these is choosing an action, which ADR-0008 D-32 removed.

WHAT "risk_level" MEANS IN CODE
    §9 calls it ``risk_level``.  The code carries it in two agreeing forms: a
    label (``Assessment.band``: ``low|medium|high|critical|unknown``, and the
    0..3 ordinal on the internal ``decision.Signals``) plus the numeric channel
    ``probabilities["risk"]`` / ``risk_score``. Agreement between label and
    number is enforced by the kernel, not here (``decision._numeric_channel_conflict``).
    ``RISK_LEVEL_FIELD`` records the §9 name and the code names together so the
    mapping is not folklore.

TWO ENTRY POINTS, ONE RULE ENGINE
    ``find_violations(assessment)`` takes anything assessment-like (the pydantic
    ``Assessment`` a provider returns). ``find_violations_in_mapping(recording)``
    takes a plain dict (an assessment as stored in a replay recording). Both
    normalise to the same mapping view and run the SAME rules, so a recording
    cannot be judged by a laxer standard than a live provider.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Tuple

from .decision import RECORDED_SIGNAL_FIELDS, REQUIRED_SIGNAL_FIELDS

#: The §9 name for the eighth output, plus the code names that carry it.
RISK_LEVEL_FIELD = "risk_level"
RISK_LEVEL_CODE_NAMES: Tuple[str, ...] = ("band", "probabilities", "risk_score")

#: The seven BOOLEAN signals: the six the arbitration actually reads, plus the
#: one it deliberately does not (``work_off_track`` is recorded-only, R6-3/R5-7).
#: Combined here so the allowlist has exactly one definition and cannot drift
#: away from ``decision``.
JEVI_ALLOWED_SIGNALS: Tuple[str, ...] = tuple(REQUIRED_SIGNAL_FIELDS) + tuple(
    RECORDED_SIGNAL_FIELDS
)

#: §9's eight-item list, spelled out as §9 spells it. Every member must be
#: accounted for by either ``JEVI_ALLOWED_SIGNALS`` or ``RISK_LEVEL_FIELD`` --
#: asserted in the tests, so a rename on either side is caught.
JEVI_EIGHT_OUTPUTS: Tuple[str, ...] = tuple(REQUIRED_SIGNAL_FIELDS) + tuple(
    RECORDED_SIGNAL_FIELDS
) + (RISK_LEVEL_FIELD,)

#: Fields ABOUT the assessment rather than claims about the work. Outside the
#: eight by design, and enumerated so that adding a new one is a visible edit.
JEVI_META_FIELDS: Tuple[str, ...] = (
    "agent",
    "session_id",
    "timestamp",
    "model",
    "provider",
    "provider_verified",
    "probabilities",
    "risk_score",
    "band",
    "score",
    "evidence_complete",
    "needs_verify",          # P1 compatibility field, read with a fallback
    "confidence",
    "confidence_kind",
    "blocker_kind",
    "blocker_reasons",
    "raw",
)

#: Names that mean "the provider chose an action". Forbidden: the policy engine
#: is the sole actuation authority (ADR-0002/0004/0008 D-32).
#:
#: NOTE for anyone tempted to prune this list: ``schemas.Assessment`` really does
#: carry ``escalate`` / ``pause`` / ``steer`` / ``retry`` / ``finished`` as
#: declared fields, left over from the pre-D-32 design. They are still in the
#: model, so a provider CAN still set them, so this check is live rather than
#: hypothetical.
FORBIDDEN_OUTPUTS: Tuple[str, ...] = (
    "escalate",
    "pause",
    "steer",
    "retry",
    "finished",
    "finish",
    "continue",
    "action",
    "directive",
    "directive_action",
)

#: The three violation kinds. Kept to three on purpose: one meaning each.
KIND_DIRECTIVE = "directive_selection"   # the scorer named an action
KIND_UNKNOWN_SIGNAL = "unknown_signal"   # a ninth signal appeared
KIND_NON_BOOLEAN = "non_boolean_signal"  # a signal channel carried a payload

#: Attributes copied off an assessment-like object into the mapping view. The
#: META partition plus the forbidden names; the signals live inside
#: ``raw["signals"]`` and are reached through the ``raw`` entry.
_VIEW_ATTRS: Tuple[str, ...] = JEVI_META_FIELDS + FORBIDDEN_OUTPUTS


class ProviderContractError(Exception):
    """A provider produced output outside the §9 boundary."""


def _view(assessment: Any) -> Dict[str, Any]:
    """A plain-dict view of anything assessment-like.

    A pydantic ``Assessment`` and a dict coming out of a replay recording are
    the same thing for the purpose of this check. Normalising here is what makes
    it impossible for the two paths to be judged by different rules.
    """
    if isinstance(assessment, Mapping):
        return dict(assessment)
    out: Dict[str, Any] = {}
    for attr in _VIEW_ATTRS:
        try:
            out[attr] = getattr(assessment, attr)
        except AttributeError:
            # A declared-but-absent field is not a claim, so it is not a
            # violation. pydantic raises here for names the model does not have.
            continue
    return out


def _violations_in_view(view: Mapping[str, Any]) -> List[str]:
    """The §9 rules, run against a normalised mapping view.

    PRESENCE IS NOT A CLAIM -- A TRUTHY VALUE IS. That distinction is load
    bearing, not pedantry. ``schemas.Assessment`` DECLARES ``escalate``,
    ``pause``, ``steer``, ``retry`` and ``finished`` as fields defaulting to
    ``False``, and a recording written with ``model_dump()`` therefore CONTAINS
    all five on every row. Flagging the name alone would reject every shipped
    provider and every well-formed recording -- a boundary that fails everything
    measures nothing. So a directive-shaped name counts only when the scorer
    actually set it, on the object channel and the dict channel alike.

    ``raw["signals"]`` is the exception, and deliberately so: that channel is
    ALLOWLIST-ONLY, so a name outside the seven is already a violation on
    presence alone. Naming a directive there is a violation either way; it is
    reported as ``directive_selection`` because that is the more informative of
    the two true things about it.
    """
    bad: List[str] = []

    # --- rule 1: a SET directive-shaped name is a chosen action --------------
    for key, value in view.items():
        if isinstance(key, str) and key in FORBIDDEN_OUTPUTS and bool(value):
            bad.append(f"{KIND_DIRECTIVE}:{key}")

    raw = view.get("raw")

    # --- rule 2: the SEMANTIC channel ``raw["signals"]`` is allowlist-only. --
    # Only here: ``raw`` itself is free-form ON PURPOSE (``disabled`` carries
    # ``provider_abstained``, ``real`` carries its interface digest and unmet
    # requirements, ``replay`` carries provenance), so forbidding unknown keys
    # there would break every provider without measuring anything. What ``raw``
    # may not contain is a directive-shaped name -- see rules 1 and 3.
    if isinstance(raw, Mapping):
        signals = raw.get("signals")
        if isinstance(signals, Mapping):
            for key, value in signals.items():
                if not isinstance(key, str):
                    continue
                if key in FORBIDDEN_OUTPUTS:
                    bad.append(f"{KIND_DIRECTIVE}:{key}")
                elif key not in JEVI_ALLOWED_SIGNALS:
                    bad.append(f"{KIND_UNKNOWN_SIGNAL}:{key}")
                elif not (isinstance(value, bool) or value is None):
                    # A signal is a BOOLEAN CHANNEL. A nested structure here is
                    # how a provider would smuggle a new semantic payload past an
                    # allowlist that only checks NAMES.
                    bad.append(f"{KIND_NON_BOOLEAN}:{key}")
        # --- rule 3: likewise at raw's top level, where it is a chosen action --
        for key, value in raw.items():
            if isinstance(key, str) and key in FORBIDDEN_OUTPUTS and bool(value):
                bad.append(f"{KIND_DIRECTIVE}:{key}")

    # --- rule 4: the numeric channel must not smuggle a directive either ------
    probs = view.get("probabilities")
    if isinstance(probs, Mapping):
        for key, value in probs.items():
            if isinstance(key, str) and key in FORBIDDEN_OUTPUTS and bool(value):
                bad.append(f"{KIND_DIRECTIVE}:{key}")

    # deterministic order, de-duplicated
    return sorted(set(bad))


def find_violations(assessment: Any) -> List[str]:
    """Every §9 boundary violation on a provider's return value, as ``kind:name``.

    Returns a LIST rather than raising so a failing test can show all of them at
    once -- a boundary that reports one problem per run costs a run per problem.
    """
    return _violations_in_view(_view(assessment))


def find_violations_in_mapping(assessment: Mapping[str, Any]) -> List[str]:
    """Same rules, applied to an assessment stored as a plain mapping.

    Used on the replay channel: a recording that carries a directive-shaped
    assessment is a CORRUPT RECORDING, and saying so at load time is more useful
    than discovering it when the row is finally replayed.
    """
    return _violations_in_view(dict(assessment))


def assert_within_boundary(assessment: Any) -> None:
    """Raise :class:`ProviderContractError` if the assessment crosses §9."""
    bad = find_violations(assessment)
    if bad:
        raise ProviderContractError(
            "provider output is outside the §9 boundary: %s. Jev may emit only "
            "%s (risk_level via band/probabilities), must not choose a directive, "
            "and must not invent a new signal without extending %s deliberately."
            % (", ".join(bad), ", ".join(JEVI_ALLOWED_SIGNALS), __name__)
        )


def describe_boundary() -> Dict[str, Any]:
    """The boundary as data, for reports and the UI."""
    return {
        "section": "§9",
        "eight_outputs": list(JEVI_EIGHT_OUTPUTS),
        "allowed_signal_names": list(JEVI_ALLOWED_SIGNALS),
        "risk_level_field": RISK_LEVEL_FIELD,
        "risk_level_code_names": list(RISK_LEVEL_CODE_NAMES),
        "meta_fields": list(JEVI_META_FIELDS),
        "forbidden_directive_outputs": list(FORBIDDEN_OUTPUTS),
        "violation_kinds": [KIND_DIRECTIVE, KIND_UNKNOWN_SIGNAL, KIND_NON_BOOLEAN],
        "enforced_at": "packages/jcc_core/providers/base.py::JevProvider.__init_subclass__",
        "rule": ("The provider supplies evidence about the work. The policy engine "
                 "is the sole actuation authority; a provider that names an action "
                 "has overstepped, whatever the action is."),
    }


__all__ = [
    "JEVI_ALLOWED_SIGNALS",
    "JEVI_EIGHT_OUTPUTS",
    "JEVI_META_FIELDS",
    "FORBIDDEN_OUTPUTS",
    "RISK_LEVEL_FIELD",
    "RISK_LEVEL_CODE_NAMES",
    "KIND_DIRECTIVE",
    "KIND_UNKNOWN_SIGNAL",
    "KIND_NON_BOOLEAN",
    "ProviderContractError",
    "find_violations",
    "find_violations_in_mapping",
    "assert_within_boundary",
    "describe_boundary",
]
