"""v1.1 §17 falsifiers for the two signing forms.

The load-bearing test is ``test_v2_covers_every_one_of_the_eleven_fields``: it is
generated from ``directive_gate.SIGNED_DIRECTIVE_FIELDS`` itself, so it cannot
fall out of step with the contract it enforces. Every field of §17 is flipped
one at a time and the signature must break. A field that can be rewritten under a
valid signature is a field the nine-rule gate cannot rely on, which is exactly how
v1 fails on ``human_approval`` (see ``test_v1_has_the_gap_that_forced_v2``).

The second load-bearing test is ``test_v1_canonical_bytes_are_frozen``: it pins
the exact v1 string. The P3 shadow tape is evidence, and evidence that stops
verifying is indistinguishable from evidence that was tampered with, so v1 must
stay byte-identical forever — v2 was added rather than v1 widened.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from jcc_core import signing as S
from jcc_core.nonce_cache import ReplayCache
from jcc_core.schemas import Directive, DirectiveAction
from supervisor import directive_gate as DG

KEY = b"unit-test-key-not-a-real-secret"

#: A known directive and the exact v1 bytes it must ALWAYS produce.
_FROZEN_V1 = (
    b"p3.0.0.p3.0.0.s-1.d-1.1000.120.0.n1.CONTINUE.noop.deadbeef."
    b'{"a":1,"b":2}'
)


def _directive(**over) -> Directive:
    kw = dict(
        directive_id="d-1",
        session_id="s-1",
        timestamp=1000.0,
        action=DirectiveAction.CONTINUE,
        target_actuator="noop",
        params={"b": 2, "a": 1},
        policy_version="p3.0.0",
        policy_table_version="p3.0.0",
        evidence_hash="deadbeef",
        issued_at=1000.0,
        ttl_s=120.0,
        nonce="n1",
        signed=True,
    )
    kw.update(over)
    return Directive(**kw)


def _signed(form: str, **over) -> Directive:
    d = _directive(**over)
    d.signature = S.sign_directive(d, KEY, form=form)
    return d


# --------------------------------------------------------------------------- #
# v1 is frozen
# --------------------------------------------------------------------------- #
def test_v1_canonical_bytes_are_frozen():
    assert S.canonical_payload(_directive()) == _FROZEN_V1


def test_v1_payload_is_unchanged_by_the_existence_of_v2():
    """Adding the v2 work must not move v1 by one byte — the default IS v1."""
    assert S.DEFAULT_SIGNING_FORM == S.SIGNING_FORM_V1
    assert S.canonical_payload(_directive(), S.SIGNING_FORM_V1) == _FROZEN_V1
    assert _signed(S.SIGNING_FORM_V1).signature == S.sign_directive(_directive(), KEY)


def test_an_existing_v1_signature_still_verifies_under_the_default_form():
    d = _signed(S.SIGNING_FORM_V1)
    ok, reason = S.verify_directive(d, KEY, now=1000.0)
    assert (ok, reason) == (True, S.REASON_OK)


# --------------------------------------------------------------------------- #
# v2 is a strict superset
# --------------------------------------------------------------------------- #
def test_v2_head_contains_the_v1_head_verbatim():
    """v2's token list is v1's, in order, with three tokens appended and a marker.

    Asserted structurally (split on the params JSON boundary, which both forms
    place last) rather than on a pinned literal, so the test states the SHAPE of
    the relationship rather than restating one example of it.
    """
    v1 = S.canonical_payload(_directive(), S.SIGNING_FORM_V1)
    v2 = S.canonical_payload(_directive(), S.SIGNING_FORM_V2)
    v1_head, v1_tail = v1.split(b".{", 1)
    v2_head, v2_tail = v2.split(b".{", 1)

    assert v1_tail == v2_tail, "both forms must place the params JSON last, identically"
    # v2 head = "v2." + v1 head + "." + human_approval + "." + scope + "." + expires_at
    assert v2_head == b"v2." + v1_head + b"..noop.1120"
    appended = v2_head[len(b"v2.") + len(v1_head) + 1:].split(b".")
    assert appended == [b"", b"noop", b"1120"], "the three added tokens changed order or count"


def test_v2_roundtrips_under_its_own_form():
    d = _signed(S.SIGNING_FORM_V2)
    assert S.verify_directive(d, KEY, now=1000.0, replay=ReplayCache(),
                              form=S.SIGNING_FORM_V2) == (True, S.REASON_OK)


def test_a_v1_signature_does_not_verify_under_v2_and_vice_versa():
    """The two forms are not interchangeable; a form that accepted both would be
    the downgrade surface this whole design avoids."""
    v1 = _signed(S.SIGNING_FORM_V1)
    v2 = _signed(S.SIGNING_FORM_V2)
    assert S.verify_directive(v1, KEY, now=1000.0, form=S.SIGNING_FORM_V2)[0] is False
    assert S.verify_directive(v2, KEY, now=1000.0, form=S.SIGNING_FORM_V1)[0] is False


# --------------------------------------------------------------------------- #
# The gap that forced v2
# --------------------------------------------------------------------------- #
def test_v1_has_the_gap_that_forced_v2():
    """``human_approval`` is OUTSIDE v1's signed bytes.

    Stated as a fact about v1's token list rather than as "v1 is broken": the
    assertion is that v1's bytes are IDENTICAL when the approval changes, which is
    why the effect boundary cannot accept v1 (see the paired v2 test below).
    """
    without = _directive()
    with_approval = _directive(human_approval="APPROVED-BY-HUMAN")
    assert S.canonical_payload(without, S.SIGNING_FORM_V1) == S.canonical_payload(
        with_approval, S.SIGNING_FORM_V1
    ), "v1 unexpectedly covers human_approval now; if so, delete this test and the v1/v2 split"


def test_v2_closes_that_gap():
    without = _directive()
    with_approval = _directive(human_approval="APPROVED-BY-HUMAN")
    assert S.canonical_payload(without, S.SIGNING_FORM_V2) != S.canonical_payload(
        with_approval, S.SIGNING_FORM_V2
    )
    signed = _signed(S.SIGNING_FORM_V2)
    signed.human_approval = "APPROVED-BY-HUMAN"          # tamper: add an approval
    assert S.verify_directive(signed, KEY, now=1000.0, form=S.SIGNING_FORM_V2) == (
        False, S.REASON_BAD_SIGNATURE,
    )


# --------------------------------------------------------------------------- #
# Every one of the eleven fields
# --------------------------------------------------------------------------- #
#: One mutation per payload-bearing field of §17. ``signature`` is excluded here
#: and covered by the wrong-signature test below, because the mutation for it IS
#: the signature.
_FIELD_MUTATION = {
    "directive_id": {"directive_id": "d-2"},
    "session_id": {"session_id": "s-2"},
    "action": {"action": DirectiveAction.ESCALATE},
    "scope": {"scope": "scope:something-else"},
    "evidence_hash": {"evidence_hash": "cafebabe"},
    "policy_version": {"policy_version": "p9.9.9"},
    "policy_table_version": {"policy_table_version": "p9.9.9"},
    "issued_at": {"issued_at": 1001.0},
    "expires_at": {"ttl_s": 121.0},
    "nonce": {"nonce": "n2"},
    "human_approval": {"human_approval": "APPROVED-BY-HUMAN"},
    "target_actuator": {"target_actuator": "other-actuator"},
    "params": {"params": {"a": 999, "b": 2}},
}

#: §17's eleven fields, plus the four further things a v2 signature must cover
#: because the enforcement reads them. ``signature`` is excluded because the
#: mutation for it IS the signature (covered by the wrong-signature test).
_EXPECTED_COVERED = (set(DG.SIGNED_DIRECTIVE_FIELDS) - {"signature"}) | {
    "policy_table_version", "target_actuator", "params",
}


def test_the_mutation_map_covers_everything_the_contract_requires():
    assert set(DG.SIGNED_DIRECTIVE_FIELDS) == {
        "directive_id", "session_id", "action", "scope", "evidence_hash",
        "policy_version", "issued_at", "expires_at", "nonce", "human_approval",
        "signature",
    }
    assert len(DG.SIGNED_DIRECTIVE_FIELDS) == 11
    assert _EXPECTED_COVERED <= set(_FIELD_MUTATION), (
        "a field the contract requires has no mutation, so nothing proves the "
        f"signature covers it: {sorted(_EXPECTED_COVERED - set(_FIELD_MUTATION))}"
    )


@pytest.mark.parametrize("field", sorted(_FIELD_MUTATION))
def test_v2_covers_every_one_of_the_eleven_fields(field):
    """Flip ONE field of a v2-signed directive; the signature must break."""
    signed = _signed(S.SIGNING_FORM_V2)
    for name, value in _FIELD_MUTATION[field].items():
        setattr(signed, name, value)
    ok, reason = S.verify_directive(signed, KEY, now=1000.0, form=S.SIGNING_FORM_V2)
    assert ok is False, f"mutating {field!r} left a valid v2 signature"
    assert reason == S.REASON_BAD_SIGNATURE


def test_v2_breaks_on_a_wrong_signature():
    signed = _signed(S.SIGNING_FORM_V2)
    signed.signature = S.SIG_PREFIX + "0" * 64
    assert S.verify_directive(signed, KEY, now=1000.0, form=S.SIGNING_FORM_V2) == (
        False, S.REASON_BAD_SIGNATURE,
    )


# --------------------------------------------------------------------------- #
# Derived tokens
# --------------------------------------------------------------------------- #
def test_scope_token_falls_back_to_the_actuator():
    assert S.scope_token(_directive(target_actuator="noop")) == "noop"
    assert S.scope_token(_directive(target_actuator="noop", scope="session:s-1")) == "session:s-1"
    assert S.scope_token(_directive(target_actuator="noop", scope="")) == "noop"


def test_scope_token_is_the_single_definition_shared_with_the_gate():
    """The view the gate publishes and the bytes the signature covers must agree."""
    d = _directive(scope="session:s-1")
    assert DG.signed_directive_view(d)["scope"] == S.scope_token(d)


def test_expires_at_token_is_integer_and_empty_when_underivable():
    assert S.expires_at_token(_directive(issued_at=1000.0, ttl_s=120.0)) == "1120"
    assert S.expires_at_token(_directive(issued_at=1000.9, ttl_s=120.0)) == "1120"
    assert S.expires_at_token(_directive(ttl_s=None)) == ""
    assert S.expires_at_token(_directive(issued_at=None)) == ""
    # never the string "None"
    assert "None" not in S.expires_at_token(_directive(ttl_s=None))


def test_expires_at_view_agrees_with_the_signed_token():
    d = _directive(issued_at=1000.0, ttl_s=120.0)
    view = DG.signed_directive_view(d)
    assert view["expires_at"] == 1120.0
    assert S.expires_at_token(d) == str(int(view["expires_at"]))


# --------------------------------------------------------------------------- #
# Form selection: no downgrade surface
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad", ["v3", "V1", "", None, 1, 0, True, ["v1"]])
def test_an_unknown_form_is_refused_not_defaulted(bad):
    with pytest.raises(ValueError, match="unknown signing form"):
        S.sign_directive(_directive(), KEY, form=bad)
    with pytest.raises(ValueError, match="unknown signing form"):
        S.verify_directive(_directive(), KEY, now=1000.0, form=bad)
    with pytest.raises(ValueError, match="unknown signing form"):
        S.DirectiveSigner(KEY, form=bad)


def test_the_form_is_never_read_off_the_object_being_verified():
    """No attribute on the object under verification may select the form.

    Driven through a duck-typed object rather than a ``Directive`` for two
    reasons: a pydantic model refuses assignment to an undeclared field (so the
    test could not write the hostile attribute at all), and doing it this way also
    proves the two functions read only the attributes they document. The hostile
    object claims ``signing_form="v1"`` and every other form-shaped attribute it
    can think of; the caller's ``form`` still decides, alone.
    """
    real = _directive()
    fields = dict(vars(real))
    # the hostile attributes: every form-shaped name a directive could grow
    fields.update(
        signing_form=S.SIGNING_FORM_V1,
        signing_alg="hmac-sha256",
        signature_status="signed_verified",
        signature_verified=True,
    )
    hostile = SimpleNamespace(**fields)
    hostile.signature = S.sign_directive(real, KEY, form=S.SIGNING_FORM_V2)

    assert S.verify_directive(hostile, KEY, now=1000.0, form=S.SIGNING_FORM_V2)[0] is True
    assert S.verify_directive(hostile, KEY, now=1000.0, form=S.SIGNING_FORM_V1)[0] is False


def test_the_gate_requires_v2():
    assert DG.REQUIRED_SIGNING_FORM == S.SIGNING_FORM_V2
    assert DG.describe()["required_signing_form"] == S.SIGNING_FORM_V2
    assert S.SIGNING_FORM_V2 in S.SIGNING_FORMS and S.SIGNING_FORM_V1 in S.SIGNING_FORMS
    assert len(S.SIGNING_FORMS) == 2


@pytest.mark.parametrize("fn", ["verify_effect"])
def test_the_effect_boundary_has_no_form_parameter(fn):
    """Structural anti-downgrade pin: if ``verify_effect`` grew a ``form``
    parameter, a caller could pass ``v1`` and rule 9 would become decorative."""
    params = inspect.signature(getattr(DG, fn)).parameters
    assert "form" not in params, "the effect boundary must not let a caller pick a weaker form"
    assert "signing_form" not in params


# --------------------------------------------------------------------------- #
# The signer carries the form, so Sentinel needs no change
# --------------------------------------------------------------------------- #
def test_a_signer_verifies_only_under_its_own_form():
    v1_signer = S.DirectiveSigner(KEY)
    v2_signer = S.DirectiveSigner(KEY, form=S.SIGNING_FORM_V2)
    assert v1_signer.form == S.SIGNING_FORM_V1 and v2_signer.form == S.SIGNING_FORM_V2

    d = _directive()
    d.signature = v2_signer.sign(d)
    assert v2_signer.verify(d) is True
    assert v1_signer.verify(d) is False
    assert v1_signer.sign(d) != v2_signer.sign(d)


def test_the_shipped_signer_default_is_still_v1():
    """``run.py`` builds ``DirectiveSigner(key)`` with no form. That is an
    A8-pinned file, so v1 must remain the default: switching the shipped signer to
    v2 must be a deliberate, re-baselined act, not a side effect of this work."""
    assert S.DirectiveSigner(KEY).form == S.SIGNING_FORM_V1


def test_signer_available_flag_is_unchanged_by_the_form_feature():
    assert S.DirectiveSigner(None, form=S.SIGNING_FORM_V2).available is False
    assert S.DirectiveSigner(None, form=S.SIGNING_FORM_V2).sign(_directive()) is None
    assert S.DirectiveSigner(KEY, form=S.SIGNING_FORM_V2).available is True


# --------------------------------------------------------------------------- #
# The nine-check order lives in one file
# --------------------------------------------------------------------------- #
def test_the_nine_check_order_is_assembled_from_the_two_documented_halves():
    assert S.INTEGRITY_CHECK_ORDER == ("signed_present", "signature", "ttl", "replay")
    assert S.EFFECT_CHECK_ORDER == ("session", "capability", "policy", "budget", "approval")
    assert S.VERIFICATION_CHECK_ORDER == S.INTEGRITY_CHECK_ORDER + S.EFFECT_CHECK_ORDER
    assert len(S.VERIFICATION_CHECK_ORDER) == 9
    assert len(set(S.VERIFICATION_CHECK_ORDER)) == 9
