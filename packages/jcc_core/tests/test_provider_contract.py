"""§9 provider OUTPUT BOUNDARY — falsifiers for ``jcc_core.provider_contract``.

Why this file exists
--------------------
Before §9 the eight-output boundary was DOCUMENTATION ONLY. ``decision`` listed
seven signal names and every existing test asserted what a KNOWN provider emits;
NONE asserted that an UNKNOWN key is refused. That distinction matters more than
it looks: a rule nobody can fail is not a rule, it is a comment.

So this file is written as a set of FALSIFIERS. For every claim the boundary
makes there is a case that would make it red:

    claim                                   | falsifying case in this file
    ----------------------------------------|------------------------------------
    a ninth signal cannot be emitted         | 'levitate'
    a signal cannot carry a payload          | worker_stuck -> {'nested': 1}
    a provider cannot choose an action       | steer=True / raw['retry'] / probs
    a DECLARED field left False is not a     | model_dump() shape must PASS
      claim (or every shipped provider and   |
      every well-formed recording fails)     |
    the check is automatic, not opt-in       | a subclass defined here that never
                                             | mentions the contract is caught
    the engine's D-26 synthesis is exempt    | its escalate=True IS a violation by
      for the right reason, not by blindness | these rules, and is still recorded

Every test below is expected to be able to FAIL. Where a passing case exists
(the "must PASS" rows) it is paired with a failing case for the same rule, so a
checker that simply returned [] would go red on the pair rather than green on
both.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from jcc_core import decision
from jcc_core.actuator import Actuator
from jcc_core.config import Config
from jcc_core.core import CoreEngine
from jcc_core.provider_contract import (
    FORBIDDEN_OUTPUTS,
    JEVI_ALLOWED_SIGNALS,
    JEVI_EIGHT_OUTPUTS,
    JEVI_META_FIELDS,
    KIND_DIRECTIVE,
    KIND_NON_BOOLEAN,
    KIND_UNKNOWN_SIGNAL,
    RISK_LEVEL_CODE_NAMES,
    RISK_LEVEL_FIELD,
    ProviderContractError,
    assert_within_boundary,
    describe_boundary,
    find_violations,
    find_violations_in_mapping,
)
from jcc_core.providers.base import JevProvider
from jcc_core.providers.disabled import DisabledProvider
from jcc_core.providers.mock import DeterministicMockProvider
from jcc_core.providers.real import RealProvider
from jcc_core.providers.record_replay import (
    REPLAY_SCHEMA,
    RecordReplayProvider,
    ReplayCorrupt,
    build_entry,
    fixture_digest_for,
    input_digest_for,
    write_recording,
)
from jcc_core.schemas import AEPEnvelope, Assessment, DirectiveAction
from jcc_core.state_store import StateStore

REPO = Path(__file__).resolve().parents[3]
SHIPPED_FIXTURE = REPO / "fixtures" / "v1.1" / "replay_default.jsonl"
PROVIDERS_BASE_REL = Path("packages") / "jcc_core" / "providers" / "base.py"


def _env(**kw: object) -> AEPEnvelope:
    base = dict(event_id="e1", delivery_id="d1", session_id="s1", agent="workbuddy")
    base.update(kw)
    return AEPEnvelope(**base)


def _assessment(**kw: object) -> Assessment:
    base: dict = dict(
        agent="workbuddy",
        session_id="s1",
        timestamp=1.0,
        provider="probe",
    )
    base.update(kw)
    return Assessment(**base)


class _Provider(JevProvider):
    """A provider that returns exactly the assessment a test hands it.

    Declared at module scope so ``__init_subclass__`` wraps it once. Instances
    vary only in what they return -- which is the point: the wrapping happens to
    the CLASS, so every case below is exercising the same automatic path a new
    provider would get.
    """

    name = "probe"
    mode = "MOCK"

    def __init__(self, out: Assessment) -> None:
        self._out = out

    def assess(self, event: AEPEnvelope, session: object = None) -> Assessment:
        return self._out


# --- the boundary as data -------------------------------------------------- #


def test_the_allowlist_is_pinned_literally_so_a_ninth_signal_is_a_reviewed_edit():
    """Pinned by VALUE, not derived.

    A test that recomputed ``REQUIRED_SIGNAL_FIELDS`` on both sides would follow
    the code wherever it went and could never go red -- it would be testing that
    a thing equals itself. The literal below is the reviewed decision.
    """
    assert JEVI_ALLOWED_SIGNALS == (
        "meaningful_progress",
        "worker_stuck",
        "requirements_satisfied",
        "needs_verification",
        "ready_to_finish",
        "needs_human",
        "work_off_track",
    )


def test_the_allowlist_is_still_derived_from_decision():
    """...and it is also pinned to ``decision``, so the two cannot drift apart.

    Both halves are needed: the literal catches "someone edited one side", this
    catches "someone edited both sides independently".
    """
    assert JEVI_ALLOWED_SIGNALS == tuple(decision.REQUIRED_SIGNAL_FIELDS) + tuple(
        decision.RECORDED_SIGNAL_FIELDS
    )


def test_the_eight_outputs_are_the_seven_signals_plus_risk_level():
    assert JEVI_EIGHT_OUTPUTS == JEVI_ALLOWED_SIGNALS + (RISK_LEVEL_FIELD,)
    assert len(JEVI_EIGHT_OUTPUTS) == 8
    assert len(set(JEVI_EIGHT_OUTPUTS)) == 8, "§9's list has a duplicate in it"


def test_the_meta_fields_are_disjoint_from_the_eight_outputs():
    """§9 says EIGHT. A meta field that was also a signal would make it nine."""
    overlap = sorted(set(JEVI_META_FIELDS) & set(JEVI_EIGHT_OUTPUTS))
    assert overlap == [], f"these are both a §9 output and a meta field: {overlap}"


def test_the_risk_level_code_names_are_declared_on_the_assessment_model():
    """The §9 name ``risk_level`` is not a field on the wire.

    Its code names are, so the mapping in ``RISK_LEVEL_CODE_NAMES`` is checked
    against the model that actually carries it rather than trusted.
    """
    declared = set(Assessment.model_fields)
    missing = sorted(set(RISK_LEVEL_CODE_NAMES) - declared)
    assert missing == [], f"risk_level code names not on Assessment: {missing}"
    for name in RISK_LEVEL_CODE_NAMES:
        assert name in JEVI_META_FIELDS, f"{name} carries risk_level; must be a meta field"


def test_every_forbidden_output_is_a_name_a_provider_could_actually_use():
    """A forbidden list padded with names nobody could set is decorative.

    ``schemas.Assessment`` really does declare ``escalate``/``pause``/``steer``/
    ``retry``/``finished``, so those five are genuinely reachable and must be in
    the list. The others are reachable through ``raw`` / ``probabilities``.
    """
    declared = set(Assessment.model_fields)
    reachable_typed = sorted(declared & set(FORBIDDEN_OUTPUTS))
    assert reachable_typed == ["escalate", "finished", "pause", "retry", "steer"], (
        "the directive-shaped flags declared on Assessment have changed; the "
        "forbidden list is no longer describing a real surface"
    )
    assert set(FORBIDDEN_OUTPUTS) <= set(FORBIDDEN_OUTPUTS)


def test_describe_boundary_reports_the_same_thing_the_constants_do():
    d = describe_boundary()
    assert d["eight_outputs"] == list(JEVI_EIGHT_OUTPUTS)
    assert d["allowed_signal_names"] == list(JEVI_ALLOWED_SIGNALS)
    assert d["forbidden_directive_outputs"] == list(FORBIDDEN_OUTPUTS)
    assert d["violation_kinds"] == [KIND_DIRECTIVE, KIND_UNKNOWN_SIGNAL, KIND_NON_BOOLEAN]


def test_describe_boundary_names_a_place_that_actually_enforces_it():
    """``enforced_at`` is a claim about the code, so it is MEASURED, not asserted.

    The failure this guards against is the one this whole session keeps meeting:
    a report saying "enforced" while nothing calls the check. Reading the file
    and looking for the call is the difference between a claim and a fact.
    """
    enforced_at = describe_boundary()["enforced_at"]
    rel, _, symbol = enforced_at.partition("::")
    src = (REPO / rel).read_text(encoding="utf-8")
    assert symbol.split(".")[-1] in src, f"{enforced_at} does not exist in {rel}"
    assert "assert_within_boundary" in src, (
        f"{rel} names the enforcement site but never calls the boundary check"
    )


# --- the rule engine: falsifiers -------------------------------------------- #


def test_a_fresh_provider_is_checked_without_opting_in():
    """THE property that makes this a boundary rather than a convention.

    ``_LateProvider`` is defined HERE, mentions no contract, is constructed
    directly (not via ``build_provider``), and is still blocked. If enforcement
    were a per-provider call, this test would be the only thing that noticed.
    """

    class _LateProvider(JevProvider):
        name = "late"
        mode = "MOCK"

        def assess(self, event: AEPEnvelope, session: object = None) -> Assessment:
            return _assessment(steer=True)

    with pytest.raises(ProviderContractError) as ei:
        _LateProvider().assess(_env(), None)
    assert f"{KIND_DIRECTIVE}:steer" in str(ei.value)


@pytest.mark.parametrize("flag", ["escalate", "pause", "steer", "retry", "finished"])
def test_a_set_directive_flag_is_blocked(flag):
    with pytest.raises(ProviderContractError) as ei:
        _Provider(_assessment(**{flag: True})).assess(_env(), None)
    assert f"{KIND_DIRECTIVE}:{flag}" in str(ei.value)


@pytest.mark.parametrize("flag", ["escalate", "pause", "steer", "retry", "finished"])
def test_a_declared_directive_field_left_false_is_not_a_violation(flag):
    """The pair to the test above, and the reason the rule tests the VALUE.

    ``Assessment`` DECLARES these five, defaulting to False, and a recording
    written with ``model_dump()`` therefore carries all five on every row. Had
    the rule fired on the NAME, every shipped provider and every well-formed
    recording would have been rejected -- a boundary that fails everything
    measures nothing, and the failure would have been discovered by whoever
    tried to record, not by this file.
    """
    assert find_violations(_assessment(**{flag: False})) == []
    # and through the object channel with no explicit value at all
    assert find_violations(_assessment()) == []


def test_a_ninth_signal_is_blocked_and_named():
    with pytest.raises(ProviderContractError) as ei:
        _Provider(_assessment(raw={"signals": {"levitate": True}})).assess(_env(), None)
    assert f"{KIND_UNKNOWN_SIGNAL}:levitate" in str(ei.value)


def test_a_ninth_signal_false_is_still_blocked():
    """Presence is the violation HERE, because this channel is allowlist-only.

    Unlike the typed fields above, ``raw['signals']`` has no declared shape to
    default to -- a key in it is a statement that the scorer has a ninth thing
    to say, and False is not less of a ninth thing.
    """
    assert find_violations(_assessment(raw={"signals": {"levitate": False}})) == [
        f"{KIND_UNKNOWN_SIGNAL}:levitate"
    ]


def test_a_directive_named_inside_signals_is_reported_as_a_directive_selection():
    """It is both an unknown signal and a chosen action; report the worse one."""
    bad = find_violations(_assessment(raw={"signals": {"steer": True}}))
    assert bad == [f"{KIND_DIRECTIVE}:steer"]


def test_a_payload_where_a_signal_belongs_is_blocked():
    """An allowlist that only checks NAMES would let a payload through."""
    with pytest.raises(ProviderContractError) as ei:
        _Provider(
            _assessment(raw={"signals": {"worker_stuck": {"nested": 1}}})
        ).assess(_env(), None)
    assert f"{KIND_NON_BOOLEAN}:worker_stuck" in str(ei.value)


def test_a_signal_may_be_null_meaning_unknown():
    """``None`` is the documented "unknown" for a signal and is NOT a payload."""
    assert find_violations(_assessment(raw={"signals": {"worker_stuck": None}})) == []


def test_a_directive_at_raw_top_level_is_blocked():
    with pytest.raises(ProviderContractError) as ei:
        _Provider(_assessment(raw={"retry": True})).assess(_env(), None)
    assert f"{KIND_DIRECTIVE}:retry" in str(ei.value)


def test_a_directive_in_the_numeric_channel_is_blocked():
    with pytest.raises(ProviderContractError) as ei:
        _Provider(
            _assessment(probabilities={"steer": 0.9}, risk_score=0.9)
        ).assess(_env(), None)
    assert f"{KIND_DIRECTIVE}:steer" in str(ei.value)


def test_free_form_provenance_keys_are_not_mistaken_for_signals():
    """``raw`` is free-form ON PURPOSE; the allowlist applies to ``signals`` only.

    Without this, ``disabled``'s ``provider_abstained``, ``real``'s
    ``interface_digest`` / ``unmet_requirements`` and ``replay``'s
    ``replay_provenance`` would all be violations, and the boundary would have
    been "fixed" by deleting the provenance that makes those providers auditable.
    """
    a = _assessment(
        raw={
            "provider_abstained": True,
            "interface_digest": "de" * 32,
            "unmet_requirements": ["transport_not_implemented"],
            "replay_provenance": {"recording_id": "r1"},
            "signals": {"worker_stuck": True, "needs_human": False},
        }
    )
    assert find_violations(a) == []


def test_the_violation_list_is_sorted_and_deduplicated():
    """A boundary that reports one problem per run costs a run per problem."""
    a = _assessment(steer=True, escalate=True, raw={"signals": {"steer": True}})
    assert find_violations(a) == [
        f"{KIND_DIRECTIVE}:escalate",
        f"{KIND_DIRECTIVE}:steer",
    ]


def test_assert_within_boundary_is_a_no_op_for_a_compliant_assessment():
    assert_within_boundary(_assessment(raw={"signals": {"worker_stuck": True}}))


def test_a_non_assessment_object_is_not_assumed_compliant_or_rejected():
    """Objects the contract cannot read produce no violations, never an exception.

    The boundary is a check on a shape; a caller handing it something else must
    not turn a contract question into a crash on the ingest path.
    """
    assert find_violations(object()) == []
    assert find_violations(None) == []
    assert find_violations({}) == []


# --- the mapping channel gets the SAME rules --------------------------------- #


def test_the_mapping_channel_agrees_with_the_object_channel():
    """A recording must not be judged by a laxer copy of the rules."""
    a = _assessment(steer=True, raw={"signals": {"levitate": True}})
    assert find_violations(a) == find_violations_in_mapping(a.model_dump(mode="json"))


def test_a_model_dump_of_a_clean_assessment_has_no_violations():
    """The exact bytes a recorder writes for a compliant assessment."""
    assert find_violations_in_mapping(_assessment(raw={"signals": {}}).model_dump()) == []


def test_a_static_assess_is_refused_rather_than_silently_unchecked():
    """A declaration the hook cannot wrap must fail, not be skipped.

    The dangerous version of this is the quiet one: a ``@staticmethod assess``
    that the hook passes over, leaving that provider outside §9 while looking
    exactly like the three that are inside it.
    """
    with pytest.raises(TypeError) as ei:

        class _Static(JevProvider):
            name = "static"
            mode = "MOCK"

            @staticmethod
            def assess(event, session=None):  # type: ignore[override]
                return _assessment()

    assert "staticmethod" in str(ei.value)
    assert "§9" in str(ei.value)


def test_a_subclass_that_inherits_assess_is_not_double_wrapped():
    """Wrapping is idempotent, so a deep provider hierarchy cannot stack checks."""
    class _Base(JevProvider):
        name = "base2"
        mode = "MOCK"

        def assess(self, event, session=None):
            return _assessment(raw={"signals": {"worker_stuck": True}})

    class _Child(_Base):
        pass

    assert _Base.assess._jcc_boundary_checked is True
    assert find_violations(_Child().assess(_env(), None)) == []
    assert _Child.assess is _Base.assess


# --- the four live channels -------------------------------------------------- #


def _fixture_entries() -> list:
    """The shipped fixture's records, with its ``#`` provenance header skipped.

    Parsed here rather than through the provider so that the test reads the FILE
    (the ground truth) instead of asking the loader to confirm itself.
    """
    out = []
    for line in SHIPPED_FIXTURE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(json.loads(line))
    return out


def _shipped_provider_classes() -> list:
    """Every provider class the package actually ships, discovered not listed."""
    return sorted(
        (c for c in JevProvider.__subclasses__()
         if c.__module__.startswith("jcc_core.providers")),
        key=lambda c: c.__name__,
    )


def _provider_for(mode: str) -> JevProvider:
    """Build the shipped provider for a §7D mode. Raises for an unknown mode."""
    if mode == "MOCK":
        return DeterministicMockProvider()
    if mode == "DISABLED":
        return DisabledProvider()
    if mode == "REAL":
        return RealProvider()
    if mode == "REPLAY":
        return RecordReplayProvider(recording_path=str(SHIPPED_FIXTURE))
    raise AssertionError(f"no shipped provider declares mode {mode!r}")


SHIPPED_MODES = ("MOCK", "DISABLED", "REAL", "REPLAY")


def test_the_live_channel_matrix_covers_every_shipped_provider():
    """Completeness pin: a fifth provider must not arrive untested.

    ``SHIPPED_MODES`` is written by hand (it is the assertion), so this checks it
    against the classes that actually exist. Without this, adding a provider and
    forgetting the matrix would leave it outside §9 coverage with every test
    still green.
    """
    declared = {c.mode for c in _shipped_provider_classes()}
    assert declared == set(SHIPPED_MODES), (
        f"shipped providers declare {sorted(declared)} but the §9 matrix tests "
        f"{sorted(SHIPPED_MODES)}"
    )


@pytest.mark.parametrize("mode", SHIPPED_MODES)
def test_every_shipped_provider_passes_the_boundary(mode):
    """The check must be satisfiable by all four modes, not merely strict."""
    provider = _provider_for(mode)
    if mode == "REPLAY":
        env = AEPEnvelope(**_fixture_entries()[0]["envelope"])
    else:
        env = _env(evidence={"ok": True})
    a = provider.assess(env, None)
    assert find_violations(a) == [], f"{mode} emits outside the §9 boundary"


def test_the_mock_provider_emits_exactly_the_seven_signals():
    a = DeterministicMockProvider().assess(_env(evidence={"ok": True}), None)
    assert set(a.raw["signals"]) == set(JEVI_ALLOWED_SIGNALS)


def test_all_four_modes_share_one_path_rather_than_four():
    """§9: "all four modes go through the same JevProvider -> Assessment -> Policy".

    A claim about SHAPE, so it is pinned against the code's shape. If a mode
    later grew its own shortcut -- a second construction site, a second
    ``assess`` call -- the boundary would only cover the modes that happen to use
    the checked path, and this is the test that says so.

    Counted with ``ast`` rather than by grepping text, because a grep also counts
    the string in a docstring, a comment and a test name. Being precise about
    what is being counted is the difference between a pin and a coincidence.
    """
    root = REPO / "packages" / "jcc_core"
    call_sites: dict = {"build_provider": set(), "assess": set()}
    for path in sorted(root.rglob("*.py")):
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id in call_sites:
                call_sites[func.id].add(path.relative_to(root).as_posix())
            elif isinstance(func, ast.Attribute) and func.attr == "assess":
                call_sites["assess"].add(path.relative_to(root).as_posix())

    assert call_sites["build_provider"] == {"run.py"}, (
        "a provider must be constructed in exactly one place, or a mode can be "
        f"built without going through the shared path: {sorted(call_sites['build_provider'])}"
    )
    assert call_sites["assess"] == {"core.py"}, (
        "the provider must be consulted in exactly one place, or a mode can be "
        f"scored without a policy reading it: {sorted(call_sites['assess'])}"
    )


# --- the engine path: exemption for the RIGHT reason ------------------------- #


class _RaisingProvider(JevProvider):
    name = "raising"
    mode = "MOCK"

    def assess(self, event: AEPEnvelope, session: object = None) -> Assessment:
        raise RuntimeError("provider exploded")


def _engine(tmp_path: Path, provider: JevProvider) -> CoreEngine:
    cfg = Config(state_store_db_path=str(tmp_path / "jcc.db"))
    store = StateStore(cfg.state_store_db_path)
    return CoreEngine(store, provider, Actuator(enabled=False), cfg)


def test_the_engine_synthesis_on_provider_error_is_a_violation_by_these_rules(tmp_path):
    """First half of the exemption pair: the object the engine builds IS outside §9.

    Written as an assertion rather than a comment so that if someone later
    "fixes" the boundary by adding an allowlist entry for ``escalate``, this
    goes red and the reason has to be restated.
    """
    engine = _engine(tmp_path, _RaisingProvider())
    out = engine.ingest(_env())
    synthesized = out["assessment"]
    bad = find_violations(synthesized)
    assert f"{KIND_DIRECTIVE}:escalate" in bad, (
        "the D-26 synthesis no longer sets escalate; if the engine changed, the "
        "documented exemption below is describing something that no longer exists"
    )


def test_the_engine_still_records_its_own_fail_closed_synthesis(tmp_path):
    """Second half: and the engine is still allowed to do it.

    §9 constrains the SCORER. The kernel's emergency statement when a provider
    raises is not a scorer's opinion, so the hook on the provider class exempts
    it by construction -- not by an entry in a list that someone could widen.
    """
    engine = _engine(tmp_path, _RaisingProvider())
    out = engine.ingest(_env())
    assert out["action"] == DirectiveAction.ESCALATE.value
    assert out["assessment"]["blocker_kind"] == "EXACT"
    assert "provider_error" in out["assessment"]["blocker_reasons"]


def test_a_provider_that_names_an_action_cannot_reach_the_store_as_that_action(tmp_path):
    """End-to-end: the boundary changes the OUTCOME, not just a helper's answer.

    A stand-in that says ``steer`` is now a provider ERROR, so the kernel
    fail-closes it to ESCALATE. The forbidden word never becomes a directive.
    """
    engine = _engine(tmp_path, _Provider(_assessment(steer=True)))
    out = engine.ingest(_env())

    assert out["action"] == DirectiveAction.ESCALATE.value
    assert out["action"] != DirectiveAction.STEER.value
    stored = engine.store.get_directives("s1")
    # the store returns plain dicts with the action as its string value
    assert [d["action"] for d in stored] == [DirectiveAction.ESCALATE.value]

    # The DETAIL is retained, and the test says where, because "the reason
    # survived" is only worth asserting together with the location.
    #
    # ``blocker_reasons`` carries the kernel's one coarse label for "the provider
    # did not answer" (``provider_error``) -- that label is the stable contract and
    # the kernel does not append exception text to it. The specific reason is
    # carried in ``raw["error"]``, which is exactly the blob ``add_assessment``
    # serialises into ``assessments.data``, so it is on the record and an operator
    # can see that this was the §9 boundary and not, say, a network timeout.
    assessment = out["assessment"]
    assert assessment["blocker_reasons"] == ["provider_error"]
    detail = assessment["raw"]["error"]
    assert "§9" in detail, f"the boundary violation lost its identity: {detail!r}"
    assert "steer" in detail, f"the offending output is not named: {detail!r}"


# --- the replay channel ------------------------------------------------------ #


def _write_raw_recording(path: Path, assessment: dict) -> None:
    """Write a recording with CORRECT digests, bypassing ``build_entry``.

    The digests must be right or the integrity check fires first and the test
    proves nothing about the boundary. Using the module's own digest helpers
    keeps this an honest "a third-party writer produced these bytes" case rather
    than an accidental tamper case -- see
    ``test_integrity_is_checked_before_meaning`` for the tamper case, which is
    deliberately reported as a different error.
    """
    envelope = {"event_id": "e1", "delivery_id": "d1", "session_id": "s1",
                "agent": "workbuddy"}
    entry = {
        "schema_version": REPLAY_SCHEMA,
        "recording_id": "raw",
        "created_at": "2026-01-01T00:00:00Z",
        "provider_source": "fixture",
        "captured_by": "test",
        "envelope": envelope,
        "session": None,
        "input_digest": input_digest_for(envelope, None),
        "assessment": assessment,
        "fixture_digest": fixture_digest_for(assessment),
    }
    path.write_text(json.dumps(entry) + "\n", encoding="utf-8", newline="\n")


def test_a_recording_that_names_an_action_is_corrupt_at_load_not_at_replay(tmp_path):
    """The boundary check on the replay channel must be REACHABLE, not decorative.

    Reachability is the whole question: the digests here verify, so nothing else
    can be doing the work. If the boundary check were dead code, this recording
    would load cleanly and the violation would surface as a live violation later,
    blaming the code instead of the file.
    """
    path = tmp_path / "bad.jsonl"
    _write_raw_recording(path, _assessment(steer=True).model_dump(mode="json"))

    with pytest.raises(ReplayCorrupt) as ei:
        RecordReplayProvider(recording_path=str(path))
    msg = str(ei.value)
    assert f"{KIND_DIRECTIVE}:steer" in msg
    assert "line 1" in msg, "the loader must name the line, or the file is unrepairable"


def test_integrity_is_checked_before_meaning(tmp_path):
    """A hand-edited violating recording reports the EDIT, which is the truer fact.

    Two true statements compete here: "these bytes are not what was recorded" and
    "the assessment names an action". The first is more specific and more
    actionable, so integrity runs first on purpose. Pinned so that reordering the
    checks has to be a decision rather than a tidy-up.
    """
    path = tmp_path / "tampered.jsonl"
    _write_raw_recording(path, _assessment(steer=True).model_dump(mode="json"))
    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["fixture_digest"] = "0" * 64
    path.write_text(json.dumps(tampered) + "\n", encoding="utf-8", newline="\n")

    with pytest.raises(ReplayCorrupt) as ei:
        RecordReplayProvider(recording_path=str(path))
    assert "fixture_digest" in str(ei.value)


def test_the_recorder_refuses_to_write_an_entry_its_own_loader_would_reject(tmp_path):
    """A writer that can emit an unloadable file turns a rule into a mystery."""
    with pytest.raises(ValueError) as ei:
        build_entry(
            envelope=_env(),
            assessment=_assessment(raw={"signals": {"levitate": True}}),
            recording_id="r1",
            created_at="2026-01-01T00:00:00Z",
        )
    assert "§9" in str(ei.value)
    assert KIND_UNKNOWN_SIGNAL in str(ei.value)


def test_a_recording_of_a_compliant_assessment_round_trips(tmp_path):
    """Non-vacuity for the two tests above: a good recording must still load."""
    path = tmp_path / "good.jsonl"
    env = _env(evidence={"ok": True})
    assessment = _assessment(raw={"signals": {"worker_stuck": True}})
    entry = build_entry(
        envelope=env,
        assessment=assessment,
        recording_id="r1",
        created_at="2026-01-01T00:00:00Z",
    )
    write_recording(path, [entry])

    prov = RecordReplayProvider(recording_path=str(path))
    assert prov.is_available()
    assert find_violations(prov.assess(env, None)) == []


def test_the_shipped_fixture_loads_and_is_within_the_boundary():
    prov = RecordReplayProvider(recording_path=str(SHIPPED_FIXTURE))
    assert prov.is_available()
    entries = _fixture_entries()
    assert entries, "the shipped fixture has no records in it"
    for entry in entries:
        assert find_violations_in_mapping(entry["assessment"]) == []
        # and the whole entry, not just the assessment field
        assert find_violations_in_mapping(entry) == []
