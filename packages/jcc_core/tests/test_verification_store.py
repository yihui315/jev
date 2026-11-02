"""Verification persistence: a remembered pass must not masquerade as a live one.

The defect: `generation_verified` lived in process memory, so a provider that had
genuinely generated came back as 「已配置 · 待验证」 on every refresh and every
restart — an operator reasonably reads that as "this never worked".

The risk in fixing it is the opposite one: a remembered pass presented as current
health. So these tests pin BOTH directions — the evidence must survive, and it
must never be promoted to a live claim.
"""
from __future__ import annotations

import os
import tempfile
import time

import pytest

from jcc_core.provider_registry import (
    DISPLAY_CONFIGURED_PENDING,
    DISPLAY_CONNECTED,
    DISPLAY_STALE_VERIFIED,
    _display_state,
)
from jcc_core.providers.generation import (
    DISCOVERY_UNSUPPORTED,
    STATE_DISCOVERY_UNSUPPORTED,
    STATE_UNAVAILABLE,
    MiniMaxProvider,
)
from jcc_core.verification_store import (
    FRESH_WINDOW_S,
    VerificationStore,
    is_fresh,
    restore,
)

SECRET = "test-key-abcdefghijklmnopqrstuvwxyz0123456789"


@pytest.fixture()
def store():
    with tempfile.TemporaryDirectory() as d:
        yield VerificationStore(os.path.join(d, "verify.db"))


class _H:
    """A health snapshot: credential present, nothing proven in this process."""

    def __init__(self, state=STATE_DISCOVERY_UNSUPPORTED, gen=False, latency=None):
        self.state = state
        self.discovery = DISCOVERY_UNSUPPORTED
        self.generation_verified = gen
        self.decision_verified = False
        self.models_are_preset = True
        self.latency_ms = latency
        self.detail = "该 Provider 不提供模型列表接口，使用已声明的默认模型"


def _pass(store, **over):
    fields = dict(
        provider_id="minimax",
        verification_status="PASS",
        verification_kind="generation",
        verified_model="MiniMax-M3",
        latency_ms=1448,
        generation_verified=True,
        discovery=DISCOVERY_UNSUPPORTED,
        model_source="PRESET",
        base_url="https://api.minimaxi.com/v1",
    )
    fields.update(over)
    return store.record(**fields)


# --------------------------------------------------------------------------- #
# Evidence survives
# --------------------------------------------------------------------------- #


class TestEvidencePersists:
    def test_a_pass_survives_a_new_store_instance(self, store):
        """The whole point: a REOPENED database must still know."""
        _pass(store)
        reopened = VerificationStore(store.db_path)
        rec = reopened.get("minimax")
        assert rec is not None
        assert rec["verified_model"] == "MiniMax-M3"
        assert rec["generation_verified"] is True
        assert rec["latency_ms"] == 1448

    def test_a_decision_pass_is_recorded_too(self, store):
        """Jev proves itself through /v1/systemone, not a completion, so its
        record must not require `generation_verified`."""
        _pass(
            store,
            provider_id="typesafe_jev",
            verification_kind="decision",
            decision_verified=True,
            generation_verified=False,
            verified_model="jev-latest",
        )
        rec = store.get("typesafe_jev")
        assert rec["decision_verified"] is True
        assert rec["generation_verified"] is False

    def test_a_failure_is_not_recorded(self, store):
        """Failing now does not make a provider unproven. The store answers 'was
        it ever proven', so a failure must not overwrite a pass."""
        _pass(store)
        store.record(provider_id="minimax", verification_status="FAIL", error_class="auth")
        assert store.get("minimax") is not None

    def test_a_second_pass_replaces_the_first(self, store):
        _pass(store, verified_model="old", latency_ms=100)
        _pass(store, verified_model="new", latency_ms=200)
        rec = store.get("minimax")
        assert rec["verified_model"] == "new"
        assert rec["latency_ms"] == 200
        assert len(store.all()) == 1

    def test_forget_removes_the_evidence(self, store):
        _pass(store)
        assert store.forget("minimax") is True
        assert store.get("minimax") is None
        assert store.forget("minimax") is False

    def test_a_provider_id_is_required(self, store):
        assert store.record(verification_status="PASS", generation_verified=True) == {}


# --------------------------------------------------------------------------- #
# No secret reaches the database
# --------------------------------------------------------------------------- #


class TestNoSecretIsStored:
    def test_key_and_header_fields_are_dropped(self, store):
        """An allowlist plus a name filter. A future caller passing a whole probe
        report must not be able to spill a credential into storage."""
        _pass(
            store,
            api_key=SECRET,
            authorization="Bearer " + SECRET,
            x_jcc_token=SECRET,
            credential=SECRET,
            secret=SECRET,
            password=SECRET,
        )
        raw = store.get("minimax")
        blob = repr(raw)
        assert SECRET not in blob
        for banned in ("api_key", "authorization", "x_jcc_token", "password"):
            assert banned not in raw

    def test_unknown_fields_are_dropped(self, store):
        _pass(store, something_new="value")
        assert "something_new" not in store.get("minimax")

    def test_whole_report_cannot_be_stored_wholesale(self, store):
        """The realistic accident: passing a probe report dict straight in."""
        _pass(store, **{
            "state": "READY", "models": ["MiniMax-M3"], "detail": "ok",
            "probe": {"auth": "PASS", "headers": {"Authorization": "Bearer " + SECRET}},
        })
        assert SECRET not in repr(store.get("minimax"))


# --------------------------------------------------------------------------- #
# LIVE vs LAST-VERIFIED — the distinction that matters
# --------------------------------------------------------------------------- #


class TestLiveIsNotInferredFromHistory:
    def test_a_remembered_pass_is_never_promoted_to_connected(self, store):
        """THE invariant. A stored success raises the card to STALE_VERIFIED and
        no further; only a check in this process may say CONNECTED."""
        _pass(store)
        r = restore("minimax", store)
        assert r["restored"] is True
        assert r["verification_source"] == "LAST_VERIFIED_READY"
        p = MiniMaxProvider(secret=SECRET)
        assert _display_state(_H(), p, r) == DISPLAY_STALE_VERIFIED
        assert _display_state(_H(), p, r) != DISPLAY_CONNECTED

    def test_connected_requires_a_live_check(self, store):
        _pass(store)
        r = restore("minimax", store)
        p = MiniMaxProvider(secret=SECRET)
        assert _display_state(_H(gen=True), p, r) == DISPLAY_CONNECTED

    def test_nothing_on_record_stays_pending(self, store):
        r = restore("minimax", store)
        assert r["restored"] is False
        assert r["verification_source"] == "NOT_VERIFIED"
        p = MiniMaxProvider(secret=SECRET)
        assert _display_state(_H(), p, r) == DISPLAY_CONFIGURED_PENDING

    def test_restore_reports_when_and_which_model(self, store):
        _pass(store, latency_ms=1448)
        r = restore("minimax", store)
        assert r["last_verified_at"] > 0
        assert r["last_verified_model"] == "MiniMax-M3"
        assert r["last_latency_ms"] == 1448
        assert r["verification_source"] == "LAST_VERIFIED_READY"

    def test_freshness_is_bounded(self, store):
        """A pass has to be ageable, or it cannot be called current."""
        _pass(store)
        now = time.time()
        assert is_fresh(store.get("minimax"), now=now) is True
        assert is_fresh(store.get("minimax"), now=now + FRESH_WINDOW_S + 60) is False

    def test_a_record_without_a_timestamp_is_never_fresh(self, store):
        """An un-ageable pass is not evidence about now."""
        assert is_fresh({"generation_verified": True}, now=time.time()) is False
        assert is_fresh(None) is False

    def test_an_unavailable_provider_is_not_masked_by_history(self, store):
        """A remembered pass must not hide a CURRENT hard failure — that is the
        direction that would matter operationally."""
        _pass(store)
        r = restore("minimax", store)
        p = MiniMaxProvider(secret=SECRET)
        live = _H(state=STATE_UNAVAILABLE)
        live.detail = "真实生成失败：HTTP 401 invalid api key (2049)"
        assert _display_state(live, p, r) not in (DISPLAY_CONNECTED, DISPLAY_STALE_VERIFIED)

    def test_forgetting_drops_the_card_back_to_pending(self, store):
        _pass(store)
        p = MiniMaxProvider(secret=SECRET)
        assert _display_state(_H(), p, restore("minimax", store)) == DISPLAY_STALE_VERIFIED
        store.forget("minimax")
        assert _display_state(_H(), p, restore("minimax", store)) == DISPLAY_CONFIGURED_PENDING
