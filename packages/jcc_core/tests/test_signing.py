"""Falsifiers for the directive signing kernel (ADR-0008 D-33) and the
hoisted replay cache. Named for the property each test falsifies.

Checks 1–4 (integrity / TTL / replay) live in ``jcc_core.signing``; checks 5–9
are the effect boundary's job, so the "session mismatch / capability / policy /
budget / approval" cases are asserted *absent* from this kernel's return space
rather than exercised here (they need context this kernel cannot hold).
"""
from __future__ import annotations

import os

import pytest

from jcc_core import signing as S
from jcc_core.nonce_cache import ReplayCache
from jcc_core.schemas import Directive, DirectiveAction

KEY = b"unit-test-key-not-a-real-secret"


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
        nonce="nonce-1",
        signed=True,
    )
    kw.update(over)
    return Directive(**kw)


def _signed(**over) -> Directive:
    d = _directive(**over)
    d.signature = S.sign_directive(d, KEY)
    return d


# --------------------------------------------------------------------------- #
# Canonicalisation
# --------------------------------------------------------------------------- #
def test_canonical_is_stable_and_param_order_independent():
    a = S.canonical_payload(_directive(params={"a": 1, "b": 2}))
    b = S.canonical_payload(_directive(params={"b": 2, "a": 1}))
    assert a == b
    assert S.canonical_payload(_directive()) == S.canonical_payload(_directive())


def test_canonical_covers_ttl_and_nonce():
    """ttl_s and nonce are INSIDE the signed bytes (D-33)."""
    base = S.canonical_payload(_directive())
    assert S.canonical_payload(_directive(ttl_s=121.0)) != base
    assert S.canonical_payload(_directive(nonce="nonce-2")) != base


def test_canonical_ttl_int_and_float_are_identical():
    assert S.canonical_payload(_directive(ttl_s=120)) == S.canonical_payload(_directive(ttl_s=120.0))


def test_canonical_int_issued_at_ignores_subsecond_jitter():
    # int(issued_at) means 1000.0 and 1000.9 sign identically — no float-repr drift.
    assert S.canonical_payload(_directive(issued_at=1000.0)) == S.canonical_payload(
        _directive(issued_at=1000.9)
    )


# --------------------------------------------------------------------------- #
# Round-trip
# --------------------------------------------------------------------------- #
def test_sign_verify_roundtrip():
    d = _signed()
    assert d.signature.startswith("sha256=")
    ok, reason = S.verify_directive(d, KEY, now=1000.0, replay=ReplayCache())
    assert ok is True
    assert reason == S.REASON_OK


def test_signature_starts_with_sha256_prefix():
    assert _signed().signature == S.SIG_PREFIX + S.sign_directive(_directive(), KEY)[len(S.SIG_PREFIX):]


# --------------------------------------------------------------------------- #
# Check 2 — tampering
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "mutation",
    [
        dict(params={"b": 2, "a": 999}),
        dict(action=DirectiveAction.ESCALATE),
        dict(nonce="nonce-2"),
        dict(ttl_s=999.0),
        dict(evidence_hash="cafebabe"),
        dict(target_actuator="other"),
        dict(policy_table_version="p9.9.9"),
    ],
)
def test_signature_rejects_tampered_field(mutation):
    d = _signed()
    for field, value in mutation.items():
        setattr(d, field, value)
    ok, reason = S.verify_directive(d, KEY, now=1000.0, replay=ReplayCache())
    assert ok is False
    assert reason == S.REASON_BAD_SIGNATURE


def test_signature_rejects_wrong_key():
    d = _signed()
    ok, reason = S.verify_directive(d, b"a-different-key", now=1000.0, replay=ReplayCache())
    assert (ok, reason) == (False, S.REASON_BAD_SIGNATURE)


# --------------------------------------------------------------------------- #
# Check 1 — presence / unsigned
# --------------------------------------------------------------------------- #
def test_unsigned_directive_is_rejected_as_unsigned():
    d = _directive(signed=False, signature=None)
    ok, reason = S.verify_directive(d, KEY, now=1000.0, replay=ReplayCache())
    assert (ok, reason) == (False, S.REASON_UNSIGNED)


def test_signed_true_but_missing_signature_is_unsigned():
    d = _directive(signed=True, signature=None)
    ok, reason = S.verify_directive(d, KEY, now=1000.0, replay=ReplayCache())
    assert (ok, reason) == (False, S.REASON_UNSIGNED)


def test_missing_payload_field_is_unsigned_not_a_crash():
    for field in ("issued_at", "ttl_s", "nonce"):
        d = _signed()
        setattr(d, field, None)
        ok, reason = S.verify_directive(d, KEY, now=1000.0, replay=ReplayCache())
        assert (ok, reason) == (False, S.REASON_UNSIGNED)


def test_no_key_yields_no_key_reason():
    d = _signed()
    ok, reason = S.verify_directive(d, None, now=1000.0, replay=ReplayCache())
    assert (ok, reason) == (False, S.REASON_NO_KEY)


# --------------------------------------------------------------------------- #
# Check 3 — TTL / clock skew
# --------------------------------------------------------------------------- #
def test_expired_ttl_rejected():
    d = _signed()
    ok, reason = S.verify_directive(d, KEY, now=1000.0 + 120.0 + 0.001, replay=ReplayCache())
    assert (ok, reason) == (False, S.REASON_EXPIRED)


def test_boundary_ttl_is_inclusive():
    d = _signed()
    ok, reason = S.verify_directive(d, KEY, now=1000.0 + 120.0, replay=ReplayCache())
    assert (ok, reason) == (True, S.REASON_OK)


def test_not_yet_valid_rejected():
    d = _signed(issued_at=1000.0)
    ok, reason = S.verify_directive(
        d, KEY, now=1000.0 - 60.0, replay=ReplayCache(), clock_skew_s=30.0
    )
    assert (ok, reason) == (False, S.REASON_NOT_YET_VALID)


def test_within_clock_skew_is_accepted():
    d = _signed(issued_at=1000.0)
    ok, reason = S.verify_directive(
        d, KEY, now=1000.0 - 10.0, replay=ReplayCache(), clock_skew_s=30.0
    )
    assert (ok, reason) == (True, S.REASON_OK)


# --------------------------------------------------------------------------- #
# Check 4 — replay, and its ORDER relative to signature
# --------------------------------------------------------------------------- #
def test_nonce_replay_rejected():
    d = _signed()
    replay = ReplayCache()
    assert S.verify_directive(d, KEY, now=1000.0, replay=replay) == (True, S.REASON_OK)
    ok, reason = S.verify_directive(d, KEY, now=1000.0, replay=replay)
    assert (ok, reason) == (False, S.REASON_REPLAY)


def test_replay_check_is_after_signature():
    """A forged directive must NOT be able to poison the replay cache (D-33)."""
    replay = ReplayCache()
    forged = _directive(nonce="fresh-nonce", signature="sha256=" + "0" * 64)
    ok, reason = S.verify_directive(forged, KEY, now=1000.0, replay=replay)
    assert (ok, reason) == (False, S.REASON_BAD_SIGNATURE)
    assert len(replay) == 0, "unauthenticated traffic reached the replay cache"
    assert not replay.seen("fresh-nonce")


def test_integrity_check_order_is_pinned():
    assert S.INTEGRITY_CHECK_ORDER == ("signed_present", "signature", "ttl", "replay")
    assert S.INTEGRITY_CHECK_ORDER.index("signature") < S.INTEGRITY_CHECK_ORDER.index("replay")


def test_kernel_never_returns_authorisation_reasons():
    """5–9 are the boundary's job; this kernel must not claim to decide them."""
    boundary_only = {
        S.REASON_SESSION_MISMATCH, S.REASON_CAPABILITY_DENIED,
        S.REASON_POLICY_MISMATCH, S.REASON_BUDGET_EXHAUSTED, S.REASON_APPROVAL_MISSING,
    }
    d = _signed()
    _, reason = S.verify_directive(d, KEY, now=1000.0, replay=ReplayCache())
    assert reason == S.REASON_OK
    assert reason not in boundary_only


# --------------------------------------------------------------------------- #
# Sign refusal / honest "no key"
# --------------------------------------------------------------------------- #
def test_sign_refuses_incomplete_directive():
    for field in ("issued_at", "ttl_s", "nonce"):
        d = _directive()
        setattr(d, field, None)
        with pytest.raises(ValueError, match=field):
            S.sign_directive(d, KEY)


def test_sign_refuses_empty_key():
    with pytest.raises(ValueError):
        S.sign_directive(_directive(), b"")


def test_load_signing_key_absent_is_none(monkeypatch):
    monkeypatch.delenv("JCC_DIRECTIVE_SIGNING_KEY", raising=False)
    assert S.load_signing_key() is None
    monkeypatch.setenv("JCC_DIRECTIVE_SIGNING_KEY", "   ")
    assert S.load_signing_key() is None


def test_load_signing_key_present_is_bytes(monkeypatch):
    monkeypatch.setenv("JCC_DIRECTIVE_SIGNING_KEY", "abc123")
    assert S.load_signing_key() == b"abc123"
    assert S.load_signing_key("JCC_DIRECTIVE_SIGNING_KEY") == b"abc123"


def test_no_signing_key_keeps_signed_false():
    """The signer must never invent a signature when no key is present (D-33)."""
    signer = S.DirectiveSigner(None)
    assert signer.available is False
    assert signer.sign(_directive()) is None
    assert signer.verify(_directive()) is False


def test_directive_signer_roundtrip():
    signer = S.DirectiveSigner(KEY)
    assert signer.available is True
    d = _directive()
    sig = signer.sign(d)
    assert sig is not None and sig.startswith("sha256=")
    d.signature = sig
    assert signer.verify(d) is True
    assert signer.verify(d, signature="sha256=" + "0" * 64) is False


def test_signer_rejects_blank_key():
    with pytest.raises(ValueError):
        S.DirectiveSigner(b"")


# --------------------------------------------------------------------------- #
# ReplayCache (hoisted neutral kernel)
# --------------------------------------------------------------------------- #
def test_replay_cache_new_then_replay():
    c = ReplayCache()
    assert c.check_and_add("k", now=1.0) is True
    assert c.check_and_add("k", now=1.0) is False
    assert c.seen("k") is True
    assert "k" in c


def test_replay_cache_ttl_expiry_frees_the_key():
    c = ReplayCache(ttl=10.0)
    assert c.check_and_add("k", now=0.0) is True
    assert c.check_and_add("k", now=5.0) is False
    assert c.check_and_add("k", now=11.0) is True   # pruned -> accepted again


def test_replay_cache_maxsize_evicts_oldest():
    c = ReplayCache(ttl=10_000.0, maxsize=3)
    for i in range(5):
        c.check_and_add(f"k{i}", now=0.0)
    assert len(c) <= 3
    assert c.check_and_add("k0", now=0.0) is True   # evicted -> accepted again


def test_replay_cache_rejects_bad_config():
    with pytest.raises(ValueError):
        ReplayCache(ttl=0)
    with pytest.raises(ValueError):
        ReplayCache(maxsize=0)


def test_replay_cache_is_thread_safe_for_distinct_keys():
    import threading

    c = ReplayCache()
    results = []
    lock = threading.Lock()

    def worker(n):
        got = c.check_and_add(f"k{n}", now=0.0)
        with lock:
            results.append(got)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(results) and len(results) == 50
