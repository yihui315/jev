"""Jev provider abstraction (scorer-only, per ADR-0002 / ADR-0004).

A ``JevProvider`` turns an AEP event + current session state into an
``Assessment`` of PROBABILITIES ONLY. It MUST NOT emit directives or take
actions. Concrete providers: ``DeterministicMockProvider`` (default, zero
external keys), ``DisabledProvider`` (no-op), and ``RealProvider`` (UNVERIFIED
stub, disabled until key + feature flag present).
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..schemas import AEPEnvelope, Assessment, SessionState

#: §7D — the four legal provider modes. There are exactly four: anything that is
#: not one of these is ``UNKNOWN`` and must be visible as such.
PROVIDER_MODES = ("REAL", "MOCK", "REPLAY", "DISABLED")

#: §7D — the activation states a provider can be in.
#: ``PENDING_EXTERNAL_ACTIVATION`` is the honest state for a provider whose
#: interface is not yet VERIFIED (see ``providers/real.py`` §7B/§7C): the
#: software is complete and the provider is wired, but it cannot score because a
#: fact OUTSIDE this repo is missing. It is NOT a failure and it must never be
#: rounded up to ACTIVE.
#: ``UNAVAILABLE`` is the third state on purpose: "I could not load my recording"
#: must not read as "I am ready". The probe three-state discipline (exists /
#: does not exist / cannot be queried) applies here too.
ACTIVATION_ACTIVE = "ACTIVE"
ACTIVATION_PENDING = "PENDING_EXTERNAL_ACTIVATION"
ACTIVATION_DISABLED = "DISABLED"
ACTIVATION_UNAVAILABLE = "UNAVAILABLE"


class JevProvider(ABC):
    name = "base"
    verified: bool = False  # provider implementations set this

    #: §7D — the provider-mode contract. Declared EXPLICITLY by every concrete
    #: provider, never INFERRED from ``name``. Inference is what would let a
    #: stand-in be relabelled REAL by a rename; the whole point of this field is
    #: that "which scorer produced this number" is never guessed.
    #:
    #: ``UNKNOWN`` is the base default and is deliberately NOT one of the four
    #: legal modes, so a provider that forgets to declare itself shows up as
    #: UNKNOWN on /api/health (and fails ``test_provider_mode_contract``) instead
    #: of being silently mistaken for a mock.
    mode = "UNKNOWN"

    #: §7D — the anti-impersonation bit. True ONLY for the provider that actually
    #: reaches the external Jev scorer. Every stand-in (mock / replay / disabled)
    #: is False, so a UI badge can never render REAL over a stand-in's number.
    is_real = False

    @abstractmethod
    def assess(self, event: AEPEnvelope, session: SessionState) -> Assessment:
        """Return a probability-only Assessment for the given event."""
        raise NotImplementedError

    def is_available(self) -> bool:
        return True

    @property
    def activation_state(self) -> str:
        """One of :data:`PROVIDER_MODES`-derived activation states (§7D).

        Derived from ``mode`` + ``verified`` + ``is_available()`` rather than
        stored, so it cannot drift away from the object it describes.
        """
        if self.mode == "REAL":
            return ACTIVATION_ACTIVE if self.verified else ACTIVATION_PENDING
        if self.mode == "DISABLED":
            return ACTIVATION_DISABLED
        if self.mode == "UNKNOWN":
            # A provider that has not declared its mode cannot claim to be ready.
            return ACTIVATION_UNAVAILABLE
        return ACTIVATION_ACTIVE if self.is_available() else ACTIVATION_UNAVAILABLE


def activation_state_for_requested(requested_mode: str, provider: JevProvider) -> str:
    """The activation state of the **requested** scorer, not just the live one.

    ``provider.activation_state`` answers "is THIS object ready". That is the
    right answer for a provider, and the wrong answer for an operator, because
    of the fail-closed fallback: when ``jev.provider=real`` is requested without
    a credential, ``build_provider`` returns a ``DisabledProvider`` -- whose own
    state is correctly ``DISABLED``. An operator reading only that would conclude
    "real Jev is deliberately switched off", when the truth is "real Jev was
    requested and is blocked on something outside this repo".

    Those two situations need OPPOSITE responses, so they must not render the
    same. When the requested mode is ``REAL`` this function reports
    ``PENDING_EXTERNAL_ACTIVATION`` unless a real provider is both live AND
    verified; otherwise it defers to the live provider's own state.

    This is deliberately a FREE FUNCTION of (config, object) rather than a
    property on the object: the object cannot know what the config asked for,
    and giving it a cached copy of that answer is how the two drift apart.
    """
    req = (requested_mode or "").upper()
    if req == "REAL":
        if getattr(provider, "is_real", False) and getattr(provider, "verified", False):
            return ACTIVATION_ACTIVE
        return ACTIVATION_PENDING
    return provider.activation_state


