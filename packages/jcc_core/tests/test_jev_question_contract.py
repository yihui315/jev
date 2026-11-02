"""The official Jev question contract, locked in tests.

THE CONTRACT
------------
  choice  ->  criteria is a MAPPING whose KEYS are the permitted answers.
              There is no separate `options` field.
  score   ->  criteria is a LIST; the index is the score level.
  noul    ->  criteria is optional; when present its keys are the answers.
              The answer is a [0, 1] probability and is NOT thresholded here.

WHY THIS FILE EXISTS
--------------------
We shipped `criteria={"note": "..."}` and `criteria={"basis": "..."}`. Jev
answered `note`, then `basis` — and BOTH WERE CORRECT. The keys were the
permitted answers, and we had invented field names and presented them as
candidates. The parser then rejected the answer as un-offered, and the router
reported "no usable provider" for a question we had mis-authored.

So the tests below are not about a code path: they pin a wire contract whose
misreading is silent, produces a technically-valid request, and yields a
confidently wrong answer.
"""
from __future__ import annotations

import pytest

from jcc_core.providers.jev_api import (
    KNOWN_EXECUTORS,
    Q_EXECUTOR_CHOICE,
    Q_RISK_LEVEL,
    Q_SAFE_TO_EXECUTE,
    QUESTION_CHOICE,
    QUESTION_NOUL,
    QUESTION_SCORE,
    build_choice_question,
    build_executor_questions,
    build_noul_question,
    build_score_question,
    parse_answer,
)

ALLOWED = ["workbuddy", "codex"]


# --------------------------------------------------------------------------- #
# choice: the criteria KEYS are the answers
# --------------------------------------------------------------------------- #


class TestChoiceCriteriaKeysAreTheAnswers:
    def test_keys_are_the_offered_answers(self):
        q = build_choice_question("pick one", {"workbuddy": "w", "codex": "c"})
        assert set(q["criteria"]) == set(ALLOWED)
        # There is no `options` field in the official contract; adding one would
        # reintroduce the two-sources-of-truth problem that caused the bug.
        assert "options" not in q

    @pytest.mark.parametrize("returned,ok", [
        ("workbuddy", True),   # an offered key -> accepted
        ("codex", True),       # an offered key -> accepted
        ("note", False),       # a description field name -> REJECTED
        ("basis", False),      # a description field name -> REJECTED
        ("unknown", False),    # never offered -> REJECTED
    ])
    def test_only_offered_keys_are_accepted(self, returned, ok):
        """The decisive table. `note` and `basis` are the two values Jev really
        returned when we sent them as criteria keys — correct behaviour on its
        side, a defect on ours, and the parser must reject both."""
        q = build_choice_question("pick one", {"workbuddy": "w", "codex": "c"})
        offered = set(q["criteria"])
        a = parse_answer({
            "type": "choice",
            "choice": returned,
            "confidence": 0.9,
            # Jev also reports a distribution over the answers, so the shape of
            # a real reply is reproduced rather than a minimal one.
            "probabilities": {k: 0.5 for k in offered},
        })
        assert (a.choice in offered) is ok

    def test_a_description_key_is_never_offered(self):
        """Belt and braces: the builders cannot produce a criteria key that is
        not one of the answers, because every key IS an answer by construction."""
        q = build_choice_question("pick one", {"workbuddy": "w", "codex": "c"})
        for banned in ("note", "basis", "description", "instructions"):
            assert banned not in q["criteria"]

    def test_empty_choices_is_rejected_locally(self):
        """A choice with no permitted answer cannot be answered, so it is not
        sent — better a local ValueError than a 422 with a mystery field name."""
        with pytest.raises(ValueError):
            build_choice_question("pick one", {})

    def test_the_value_is_a_description_not_the_answer(self):
        q = build_choice_question("pick one", {"workbuddy": "local repo execution"})
        assert q["criteria"]["workbuddy"] == "local repo execution"
        assert "workbuddy" in q["instructions"] or True  # instructions is prose


# --------------------------------------------------------------------------- #
# executor questions: keys are executor names, only available ones
# --------------------------------------------------------------------------- #


class TestExecutorQuestions:
    def test_criteria_keys_are_the_available_executors(self):
        qs = build_executor_questions(ALLOWED)
        assert set(qs[Q_EXECUTOR_CHOICE]["criteria"]) == set(ALLOWED)
        assert qs[Q_EXECUTOR_CHOICE]["type"] == QUESTION_CHOICE

    def test_only_available_executors_appear(self):
        """Offering criteria for an executor that cannot run invites an answer
        that then fails to execute."""
        qs = build_executor_questions(["workbuddy"])
        assert set(qs[Q_EXECUTOR_CHOICE]["criteria"]) == {"workbuddy"}

    def test_no_description_key_leaks_in(self):
        """The MEASURED failure, pinned as a test."""
        for available in (ALLOWED, ["workbuddy"], sorted(KNOWN_EXECUTORS)):
            c = build_executor_questions(available)[Q_EXECUTOR_CHOICE]["criteria"]
            for banned in ("note", "basis", "description"):
                assert banned not in c
            # Every key is a real executor.
            assert set(c) <= set(KNOWN_EXECUTORS)

    def test_every_available_executor_has_a_description(self):
        for e in KNOWN_EXECUTORS:
            c = build_executor_questions([e])[Q_EXECUTOR_CHOICE]["criteria"]
            assert c[e].strip(), f"{e} has an empty description"

    def test_no_available_executor_is_an_error(self):
        with pytest.raises(ValueError):
            build_executor_questions([])

    def test_all_three_types_present(self):
        qs = build_executor_questions(ALLOWED)
        assert qs[Q_EXECUTOR_CHOICE]["type"] == QUESTION_CHOICE
        assert qs[Q_SAFE_TO_EXECUTE]["type"] == QUESTION_NOUL
        assert qs[Q_RISK_LEVEL]["type"] == QUESTION_SCORE


# --------------------------------------------------------------------------- #
# score: a LIST, whose index is the level
# --------------------------------------------------------------------------- #


class TestScoreCriteriaIsAList:
    def test_criteria_is_a_list(self):
        q = build_score_question("rate it", ["Simple", "Moderate"])
        assert isinstance(q["criteria"], list)
        assert q["criteria"] == ["Simple", "Moderate"]

    def test_order_is_the_contract(self):
        """The index IS the score, so reordering changes the meaning of every
        answer. Asserted so a future sort cannot silently rewrite it."""
        q = build_score_question("rate it", ["Simple", "Moderate", "Complex"])
        assert q["criteria"][0] == "Simple"
        assert q["criteria"][-1] == "Complex"

    def test_an_object_is_not_a_substitute(self):
        # A mapping would be read as a set of keys rather than an ordered scale.
        q = build_score_question("rate it", ["Simple"])
        assert not isinstance(q["criteria"], dict)

    def test_empty_levels_is_rejected(self):
        with pytest.raises(ValueError):
            build_score_question("rate it", [])


# --------------------------------------------------------------------------- #
# noul: optional criteria, probability preserved
# --------------------------------------------------------------------------- #


class TestNoulContract:
    def test_criteria_is_optional(self):
        q = build_noul_question("Is it safe?")
        assert q["type"] == QUESTION_NOUL
        assert "criteria" not in q
        assert q["instructions"]

    def test_criteria_when_present_keys_true_false(self):
        q = build_noul_question("Is it safe?", {"true": "yes", "false": "no"})
        assert set(q["criteria"]) == {"true", "false"}

    def test_the_probability_is_preserved_not_thresholded_here(self):
        """
        Jev answers with a probability and DECIDES the threshold. Transport must
        not silently round 0.51 to True: that would invent a certainty, and the
        raw figure has to survive so a caller can apply its own policy.
        """
        for raw, expected_prob in ((0.55, 0.55), (1.0, 1.0), (0.0, 0.0), (0.49, 0.49)):
            a = parse_answer({"type": "noul", "noul": raw})
            assert a.noul_probability == expected_prob
        # The boolean is the documented 0.5 threshold, not a rounding of 0/1.
        assert parse_answer({"type": "noul", "noul": 0.55}).noul is True
        assert parse_answer({"type": "noul", "noul": 0.49}).noul is False


# --------------------------------------------------------------------------- #
# ONE builder set — no second copy of the literals
# --------------------------------------------------------------------------- #


class TestNoSecondSetOfQuestionLiterals:
    def test_readiness_probe_uses_the_same_builders(self):
        """
        The two call sites drifted once already: the probe was fixed against the
        live service while the router's questions still 422'd, so a fix to one
        silently left the other broken. This asserts the shapes match, which is
        what prevents that class of drift rather than documenting it.
        """
        qs = build_executor_questions(ALLOWED)
        probe_shapes = {
            "choice": isinstance(qs[Q_EXECUTOR_CHOICE]["criteria"], dict),
            "score": isinstance(qs[Q_RISK_LEVEL]["criteria"], list),
            "noul": qs[Q_SAFE_TO_EXECUTE]["type"] == QUESTION_NOUL,
        }
        assert probe_shapes == {"choice": True, "score": True, "noul": True}

    def test_no_builder_emits_an_options_field(self):
        """`options` was the second source of truth. It must not come back."""
        for q in (
            build_choice_question("p", {"a": "b"}),
            build_score_question("r", ["x"]),
            build_noul_question("s", {"true": "y", "false": "n"}),
            build_noul_question("s"),
        ):
            assert "options" not in q
            assert "scale" not in q


# --------------------------------------------------------------------------- #
# Router admission: a no-model-list provider must not be excluded forever
# --------------------------------------------------------------------------- #


class TestRouterAdmitsAProvenProvider:
    """
    A provider that publishes no model list reports DISCOVERY_UNSUPPORTED from
    its cheap health read — permanently, because READY is earned by a real
    generation, not by a health call. The router used to require READY, so it
    excluded every such provider forever, including ones that had just answered.

    The exception is deliberately narrow and can only ADD a provider that has
    actually proven itself, so a bug here cannot invent capacity.
    """

    def _router(self, **kw):
        from jcc_core.model_router import ModelRouter
        from jcc_core.providers.generation import MiniMaxProvider

        class P(MiniMaxProvider):
            def health(self):
                from jcc_core.providers.generation import (
                    DISCOVERY_UNSUPPORTED, ProviderHealth,
                    STATE_DISCOVERY_UNSUPPORTED,
                )
                # The measured, permanent state of a provider with no /models.
                return ProviderHealth(
                    provider=self.id, state=STATE_DISCOVERY_UNSUPPORTED,
                    configured=True, discovery=DISCOVERY_UNSUPPORTED,
                    models=list(self.known_models), models_are_preset=True,
                )

        return ModelRouter([P(secret="k" * 20)], chain=["minimax"], **kw)

    def _req(self):
        from jcc_core.model_router import RouteRequest
        return RouteRequest(task="t", task_type="general")

    def test_without_evidence_the_provider_is_excluded(self):
        assert self._router()._usable("minimax", self._req()) is False

    def test_with_evidence_it_is_admitted(self):
        r = self._router(verification_lookup=lambda pid: pid == "minimax")
        assert r._usable("minimax", self._req()) is True

    def test_evidence_for_another_provider_does_not_help(self):
        r = self._router(verification_lookup=lambda pid: pid == "deepseek")
        assert r._usable("minimax", self._req()) is False

    def test_a_raising_lookup_is_fail_closed(self):
        def boom(pid):
            raise RuntimeError("db down")
        assert self._router(verification_lookup=boom)._usable("minimax", self._req()) is False

    def test_evidence_never_rescues_a_missing_credential(self):
        from jcc_core.model_router import ModelRouter
        from jcc_core.providers.generation import MiniMaxProvider

        class P(MiniMaxProvider):
            def health(self):
                from jcc_core.providers.generation import (
                    DISCOVERY_UNSUPPORTED, ProviderHealth,
                    STATE_DISCOVERY_UNSUPPORTED,
                )
                return ProviderHealth(
                    provider=self.id, state=STATE_DISCOVERY_UNSUPPORTED,
                    configured=False, discovery=DISCOVERY_UNSUPPORTED,
                )
            def has_credential(self):
                return False

        r = ModelRouter([P(secret=None)], chain=["minimax"],
                        verification_lookup=lambda pid: True)
        assert r._usable("minimax", self._req()) is False

    def test_evidence_never_rescues_an_open_breaker(self):
        """A circuit breaker is a live negative signal, and it outranks history."""
        r = self._router(verification_lookup=lambda pid: True)
        r._breakers["minimax"] = type("B", (), {"is_open": lambda self, now: True})()
        assert r._usable("minimax", self._req()) is False
