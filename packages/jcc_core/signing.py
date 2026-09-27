"""Directive signing kernel (ADR-0008 D-33, revising D-6).

What "signed" means — NORMATIVE
-------------------------------
``signed`` means **integrity + origin-as-holder-of-the-key**. It does **not**
mean the directive is *authorised*. Authorisation is a separate, later check.
Collapsing the two is how "signed" degrades into a rubber stamp, so the split is
enforced structurally here: this module can only ever decide the first four
checks (integrity, TTL/clock-skew, replay). Checks 5–9 — session match,
capability, policy/table version, budget, human approval — need context that a
pure kernel does not have, so they belong to the **effect boundary**
(``Actuator.verifyEnvelope`` / ``Sentinel._dispatch``), which re-runs them in the
mandated order. The reason vocabulary for all nine is defined here so the
boundary and this kernel cannot drift apart.

Canonical form (byte-stable, D-33)
----------------------------------
::

    policy_table_version . policy_version . session_id . directive_id .
    int(issued_at) . <ttl_s> . nonce . action . target_actuator . evidence_hash .
    json.dumps(params, sort_keys=True, separators=(",", ":"))

``ttl_s`` and ``nonce`` are **inside** the signed bytes on purpose: if they were
outside, an attacker could replay a valid signature with a fresh timestamp and a
new nonce. ``int(issued_at)`` (not the float) avoids platform float-repr drift;
``float(ttl_s)`` is used for the same reason (so ``120`` and ``120.0`` sign
identically).

Key handling
------------
Symmetric HMAC-SHA256. The key is read from the environment variable named by
``config.p3.shadow.signing.key_env`` (default ``JCC_DIRECTIVE_SIGNING_KEY``),
never from ``config/jcc.yaml``, the database, or the log — the same rule as the
control token. HMAC is symmetric, so it is **not** non-repudiation against the
control plane itself; asymmetric signing is an explicit P4 decision (D-39).

Two signing forms (v1.1 §17)
----------------------------
``v1`` is the D-33 form above, **byte-frozen**, and remains the default on every
function here so that every directive already recorded in the P3 shadow tape
still verifies. It covers ten tokens: policy table version, policy version,
session, directive id, issued_at, ttl_s, nonce, action, target actuator, params.

``v2`` is v1 plus three more tokens — ``human_approval``, ``scope`` and
``expires_at`` — and is **required by the effect boundary** (``directive_gate``).

WHY v2 EXISTS, AND WHY IT DOES NOT "FIX" v1 IN PLACE
----------------------------------------------------
v1 does not cover ``human_approval``. A directive carrying an approval and the
same directive with the approval stripped sign to the **same bytes**, so an
attacker holding one valid v1 signature could *add* an approval and the
signature would still verify. That is not a theoretical weakness: rule 9 of the
nine-rule authorisation gate (v1.1 §17) is exactly "does a human approval exist",
so under v1 that rule is decorative — "signed" and "approved" collapse into the
same claim, which the module docstring above already names as the failure mode
this whole split exists to prevent.

The choice is therefore between (a) editing v1's token list, invalidating every
signature already on tape, or (b) adding a form and making the *gate* refuse
anything not signed under the stronger one. (b) is taken, because the P3 tape is
evidence: re-writing the canonical form would turn recorded history into a set of
signatures that no longer verify, and "the audit tape does not verify" is
indistinguishable from tampering.

There is deliberately **no** downgrade surface. The form is a keyword-only
parameter with a constant default, and it is never read off the directive: the
effect boundary passes ``SIGNING_FORM_V2`` as a literal, so nothing an attacker
can write into a directive — not ``signing_alg``, not any new field — can make
the gate verify under the weaker form.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any, Optional, Tuple

from .nonce_cache import ReplayCache

#: The one algorithm we speak. Stored on the directive as ``signing_alg``.
SIGN_ALG = "hmac-sha256"

#: Signature wire prefix (mirrors ``integrations/_common/jcc_receiver.py:122``).
SIG_PREFIX = "sha256="

#: Env var consulted when the caller does not name one (D-33 is authoritative;
#: the design input's ``JCC_DIRECTIVE_KEY`` is superseded).
DEFAULT_KEY_ENV = "JCC_DIRECTIVE_SIGNING_KEY"

#: Tolerated clock skew for "not yet valid"; expired is not skewed (fail-closed).
DEFAULT_CLOCK_SKEW_S = 30.0

# -- signing forms (v1.1 §17) -------------------------------------------------
#: D-33 canonical form: ten tokens, no ``human_approval``. Byte-frozen.
SIGNING_FORM_V1 = "v1"
#: v1.1 form: v1 plus ``human_approval`` / ``scope`` / ``expires_at``, and the
#: literal marker ``v2.`` in front so the two forms are distinguishable by
#: inspection rather than by trying both.
SIGNING_FORM_V2 = "v2"
#: The closed set. A form outside this tuple is refused, never defaulted.
SIGNING_FORMS: Tuple[str, ...] = (SIGNING_FORM_V1, SIGNING_FORM_V2)
#: What a caller gets when it does not say. ``v1`` so that every existing
#: signature, record and test keeps its meaning; the effect boundary opts into
#: ``v2`` explicitly and unconditionally.
DEFAULT_SIGNING_FORM = SIGNING_FORM_V1


# -- verification reason vocabulary (checks 1..9; frozen strings) -------------
REASON_OK = "ok"
REASON_UNSIGNED = "UNSIGNED"                      # check 1
REASON_NO_KEY = "unavailable:no_key"              # check 1/2 (cannot verify)
REASON_BAD_SIGNATURE = "BAD_SIGNATURE"            # check 2
REASON_EXPIRED = "EXPIRED"                        # check 3
REASON_NOT_YET_VALID = "NOT_YET_VALID"            # check 3
REASON_REPLAY = "REPLAY"                          # check 4
# checks 5..9 are enforced at the effect boundary, but the codes live here:
REASON_SESSION_MISMATCH = "SESSION_MISMATCH"      # check 5
REASON_CAPABILITY_DENIED = "CAPABILITY_DENIED"    # check 6
REASON_POLICY_MISMATCH = "POLICY_MISMATCH"        # check 7
REASON_BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"      # check 8
REASON_APPROVAL_MISSING = "APPROVAL_MISSING"      # check 9

#: The ordered integrity checks this kernel owns (1..4). Kept as data so a test
#: can assert the order and that replay is strictly after signature.
INTEGRITY_CHECK_ORDER: Tuple[str, ...] = (
    "signed_present",
    "signature",
    "ttl",
    "replay",
)

#: The five checks the EFFECT BOUNDARY owns (5..9). Declared here, next to the
#: four above and next to the reason codes they return, so the full nine-rule
#: order and its vocabulary live in ONE file: the kernel's docstring already
#: says the boundary "re-runs them in the mandated order", and a mandated order
#: split across two modules is an order that drifts.
EFFECT_CHECK_ORDER: Tuple[str, ...] = (
    "session",
    "capability",
    "policy",
    "budget",
    "approval",
)

#: All nine, in the order they must be evaluated. First failure wins; the answer
#: is DENY either way, so the order's only job is to make the *reason* stable
#: and to keep the cheap integrity checks ahead of the context-heavy ones.
VERIFICATION_CHECK_ORDER: Tuple[str, ...] = INTEGRITY_CHECK_ORDER + EFFECT_CHECK_ORDER


# --------------------------------------------------------------------------- #
# Key loading
# --------------------------------------------------------------------------- #
def load_signing_key(env_name: str = DEFAULT_KEY_ENV) -> Optional[bytes]:
    """Return the HMAC key as ``bytes``, or ``None`` when unset/blank.

    ``None`` is a first-class, *honest* outcome: the caller records
    ``signed=False, signature_status="unavailable:no_key"`` rather than inventing
    a fake signature. A key present but empty/whitespace is treated as absent —
    an empty HMAC key is not a key.
    """
    if not isinstance(env_name, str) or not env_name:
        raise TypeError(f"env_name must be a non-empty str, got {env_name!r}")
    raw = os.environ.get(env_name)
    if raw is None or raw.strip() == "":
        return None
    return raw.encode("utf-8")


# --------------------------------------------------------------------------- #
# Canonicalisation
# --------------------------------------------------------------------------- #
def _t(value: Any) -> str:
    """Token for the canonical string; ``None`` → empty string (never "None")."""
    return "" if value is None else str(value)


def _action_token(action: Any) -> str:
    value = getattr(action, "value", action)
    return _t(value)


def scope_token(directive: Any) -> str:
    """The canonical SCOPE token of ``directive``.

    An explicit ``scope`` wins; otherwise the scope IS the actuator (the
    actuator says where an effect would land, and for a directive that never
    named a narrower authority that is the whole of what it may affect).

    This function is the single definition of that fallback. If the rule lived at
    the call sites, the signer and the verifier could each apply a slightly
    different one and every signature would still "verify" — against different
    bytes.
    """
    explicit = getattr(directive, "scope", None)
    if explicit is not None and str(explicit) != "":
        return _t(explicit)
    return _t(getattr(directive, "target_actuator", None))


def expires_at_token(directive: Any) -> str:
    """``issued_at + ttl_s`` as an integer token, or ``""`` when underivable.

    ``expires_at`` is a DERIVED view, not a stored field: ``expires_at`` and
    ``issued_at + ttl_s`` that could disagree would be two answers to one
    question, and the one the gate reads would be whichever was written last.
    ``int()`` for the same reason ``int(issued_at)`` is used above — no float
    repr drift — and ``""`` (never the string ``"None"``) when either half is
    missing, so an underivable expiry is *covered* as underivable rather than
    silently absent from the payload.
    """
    issued = getattr(directive, "issued_at", None)
    ttl = getattr(directive, "ttl_s", None)
    if issued is None or ttl is None:
        return ""
    return str(int(float(issued) + float(ttl)))


def canonical_payload(directive: Any, form: str = DEFAULT_SIGNING_FORM) -> bytes:
    """Byte-stable canonical bytes for ``directive`` under ``form``.

    ``form`` is not validated here — :func:`sign_directive` and
    :func:`verify_directive` both validate through :func:`_check_form` before
    reaching this function, and this one is also called directly by tests that
    deliberately probe the two forms' byte layouts.
    """
    issued = getattr(directive, "issued_at", None)
    ttl = getattr(directive, "ttl_s", None)
    params = getattr(directive, "params", None) or {}
    head = ".".join(
        [
            _t(getattr(directive, "policy_table_version", None)),
            _t(getattr(directive, "policy_version", None)),
            _t(getattr(directive, "session_id", None)),
            _t(getattr(directive, "directive_id", None)),
            "" if issued is None else str(int(issued)),
            "" if ttl is None else repr(float(ttl)),
            _t(getattr(directive, "nonce", None)),
            _action_token(getattr(directive, "action", None)),
            _t(getattr(directive, "target_actuator", None)),
            _t(getattr(directive, "evidence_hash", None)),
        ]
    )
    if form == SIGNING_FORM_V2:
        # The ``v2.`` marker is not load-bearing for security (the call site
        # fixes the form) — it exists so that a hex digest, a payload dump or a
        # bug report says which form produced it without anybody having to try
        # both. The three added tokens follow the same positional discipline as
        # v1's: they are inside the bytes, in a fixed order.
        head = ".".join(
            [
                SIGNING_FORM_V2,
                head,
                _t(getattr(directive, "human_approval", None)),
                scope_token(directive),
                expires_at_token(directive),
            ]
        )
    # Trailing '.' after evidence_hash, then the params JSON — per D-33.
    body = head + "." + json.dumps(params, sort_keys=True, separators=(",", ":"))
    return body.encode("utf-8")


def _check_form(form: object) -> str:
    """Return ``form`` unchanged, or RAISE.

    No fallback to ``DEFAULT_SIGNING_FORM``: a misspelled form that silently
    degrades to v1 is precisely the downgrade this module's ``v2`` section exists
    to prevent, and it would be reachable by a typo rather than by an attack.
    """
    if form not in SIGNING_FORMS:
        raise ValueError(f"unknown signing form {form!r}; the closed set is {list(SIGNING_FORMS)}")
    return str(form)


# --------------------------------------------------------------------------- #
# Sign
# --------------------------------------------------------------------------- #
def sign_directive(directive: Any, key: bytes, *, form: str = DEFAULT_SIGNING_FORM) -> str:
    """Return ``"sha256=<hex>"`` for ``directive`` under ``key`` and ``form``.

    Raises ``ValueError`` when the directive is not fully signable. A directive
    without ``issued_at``/``ttl_s``/``nonce`` cannot be integrity- or
    replay-protected, so signing one would produce a signature that lies about
    what it covers — refuse rather than emit it.
    """
    _check_form(form)
    if not isinstance(key, (bytes, bytearray)) or len(key) == 0:
        raise ValueError("signing key must be non-empty bytes")
    for field in ("issued_at", "ttl_s", "nonce"):
        if getattr(directive, field, None) is None:
            raise ValueError(f"cannot sign a directive without {field!r} (it is inside the payload)")
    mac = hmac.new(bytes(key), canonical_payload(directive, form), hashlib.sha256)
    return SIG_PREFIX + mac.hexdigest()



# --------------------------------------------------------------------------- #
# Verify (checks 1..4)
# --------------------------------------------------------------------------- #
def verify_directive(
    directive: Any,
    key: Optional[bytes],
    *,
    now: float,
    replay: Optional[ReplayCache] = None,
    clock_skew_s: float = DEFAULT_CLOCK_SKEW_S,
    form: str = DEFAULT_SIGNING_FORM,
) -> Tuple[bool, str]:
    """Run integrity checks 1–4 in order; first failure wins; no side effects.

    Returns ``(ok, reason)`` where ``reason`` is ``"ok"`` on success and a code
    from the module vocabulary otherwise. Order (normative, D-33):

    1. ``signed`` true and ``signature``/``nonce``/``issued_at``/``ttl_s`` present
    2. recomputed HMAC matches (``hmac.compare_digest``)
    3. ``0 <= now - issued_at <= ttl_s`` and ``issued_at - now <= clock_skew_s``
    4. ``nonce`` unseen in the replay cache

    **Check 4 runs only after check 2** so unauthenticated traffic cannot poison
    the replay cache (the same rule already recorded at
    ``integrations/_common/jcc_receiver.py:166``). Checks 5–9 are the effect
    boundary's responsibility (see module docstring) and are deliberately absent
    here — this function cannot authorise anything.

    ``form`` selects the canonical bytes (see the module's "Two signing forms"
    section). It is a CALLER-CHOSEN constant and is never derived from
    ``directive``: the whole point of the parameter is that the verifier decides
    which strength it will accept, so reading it off the object under
    verification would hand that decision to whoever wrote the object.
    """
    # -- check 1: presence --------------------------------------------------
    _check_form(form)
    if not bool(getattr(directive, "signed", False)):
        return False, REASON_UNSIGNED
    signature = getattr(directive, "signature", None)
    if not isinstance(signature, str) or not signature:
        return False, REASON_UNSIGNED
    if any(getattr(directive, f, None) is None for f in ("issued_at", "ttl_s", "nonce")):
        # A directive missing the fields that are *inside* the payload cannot be
        # verified at all; treat as unsigned (fail-closed) rather than guess.
        return False, REASON_UNSIGNED
    if key is None:
        return False, REASON_NO_KEY

    # -- check 2: signature -------------------------------------------------
    expected = sign_directive(directive, key, form=form)
    if not hmac.compare_digest(expected, signature):
        return False, REASON_BAD_SIGNATURE

    # -- check 3: TTL / clock skew -----------------------------------------
    issued_at = float(directive.issued_at)
    ttl_s = float(directive.ttl_s)
    if now - issued_at > ttl_s:
        return False, REASON_EXPIRED
    if issued_at - now > float(clock_skew_s):
        return False, REASON_NOT_YET_VALID

    # -- check 4: replay (AFTER signature) ----------------------------------
    if replay is not None:
        if not replay.check_and_add(str(directive.nonce), now):
            return False, REASON_REPLAY

    return True, REASON_OK


# --------------------------------------------------------------------------- #
# Object-style facade
# --------------------------------------------------------------------------- #
class DirectiveSigner:
    """Thin, stateful wrapper holding one key and one signing FORM.

    Provided because wiring code wants ``signer.sign(d)`` / ``signer.verify(d)``
    without threading the key through every call — and because the Sentinel
    self-verifies immediately after signing to prove the seam (D-33 line 63).
    ``available`` is the honest "is a key present" flag the recorder branches on.

    ``form`` is a property of the signer, not of the directive, for the same
    reason it is a keyword-only parameter on the two functions: the strength of
    verification is the verifier's decision. A signer built with ``v1`` (the
    default — what ``run.py`` builds today) produces signatures the v1.1 effect
    boundary will REFUSE, because that boundary requires ``v2``. That refusal is
    correct and it is the intended coupling: raising the actuation rung above
    ``L0`` must not be possible with a signing form that cannot cover an
    approval.
    """

    SIGN_ALG = SIGN_ALG

    def __init__(self, key: Optional[bytes], *, form: str = DEFAULT_SIGNING_FORM) -> None:
        if key is not None and (not isinstance(key, (bytes, bytearray)) or len(key) == 0):
            raise ValueError("key must be None or non-empty bytes")
        self._key: Optional[bytes] = bytes(key) if key is not None else None
        self._form = _check_form(form)

    @property
    def key(self) -> Optional[bytes]:
        return self._key

    @property
    def available(self) -> bool:
        return self._key is not None

    @property
    def form(self) -> str:
        return self._form

    @staticmethod
    def canonical(directive: Any, form: str = DEFAULT_SIGNING_FORM) -> bytes:
        return canonical_payload(directive, form)

    def sign(self, directive: Any) -> Optional[str]:
        """``"sha256=<hex>"`` when a key is present, else ``None`` (never fake)."""
        if self._key is None:
            return None
        return sign_directive(directive, self._key, form=self._form)

    def verify(self, directive: Any, signature: Optional[str] = None) -> bool:
        """Signature-only boolean (check 2), under this signer's OWN form.

        ``signature`` defaults to the directive's. Note that this is check 2
        alone: it is a proof that the bytes are untouched under ``self.form``,
        and nothing more. It is deliberately not a boolean for "this directive
        may act" — that is nine checks, and it is ``directive_gate``'s answer.
        """
        if self._key is None:
            return False
        candidate = signature if signature is not None else getattr(directive, "signature", None)
        if not isinstance(candidate, str) or not candidate:
            return False
        return hmac.compare_digest(
            sign_directive(directive, self._key, form=self._form), candidate
        )
