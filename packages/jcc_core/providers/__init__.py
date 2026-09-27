"""Provider selection.

Default is the deterministic mock (zero external keys -> demo mode works).
``real`` is only honored when explicitly enabled by feature flag AND a key is
present; otherwise we fail closed to ``DisabledProvider`` (never silently call
an external service).

``replay`` (v1.1) returns pre-recorded Assessments verbatim from a JSONL
recording file; never falls back to the mock, and a missing recording for the
live envelope raises (rather than silently fabricating a default Assessment).
"""
from __future__ import annotations

import logging
import os

from ..config import Config
from .base import JevProvider
from .disabled import DisabledProvider
from .mock import DeterministicMockProvider
from .real import RealProvider

logger = logging.getLogger("jcc.providers")


def build_provider(config: Config) -> JevProvider:
    mode = (config.jev.provider or "mock").lower()

    if mode == "disabled":
        return DisabledProvider()

    if mode == "real":
        key = os.environ.get(config.jev.api_key_env)
        flag_on = bool(config.jev.feature_flags.get("real_provider"))
        if flag_on and key:
            logger.warning("RealProvider selected (UNVERIFIED). External scorer active.")
            return RealProvider(
                model=config.jev.model,
                api_key=key,
                base_url=config.jev.base_url,
            )
        # Fail closed: requested real but not fully enabled -> disabled.
        logger.warning(
            "jev.provider=real requested but feature flag/key not satisfied; "
            "falling back to DisabledProvider (fail-closed)."
        )
        return DisabledProvider()

    if mode == "replay":
        # Imported lazily so the v1.0.1 line that does not use replay does
        # not pay the import cost (and so a syntax error in the replay
        # provider cannot break the v1.0.1 build).
        from .record_replay import RecordReplayProvider

        recording_path = os.environ.get("JCC_REPLAY_RECORDING")
        prov = RecordReplayProvider(recording_path=recording_path)
        if not prov.is_available():
            # Fail closed: a replay request with no recording is treated
            # as a configuration error and disables the provider rather
            # than silently substituting the mock. This is the same shape
            # as the ``real``/disabled fallback above, on purpose.
            logger.warning(
                "jev.provider=replay requested but no recording was loaded "
                "(set JCC_REPLAY_RECORDING); falling back to "
                "DisabledProvider (fail-closed)."
            )
            return DisabledProvider()
        return prov

    # default / "mock"
    return DeterministicMockProvider(model=config.jev.model)


__all__ = [
    "JevProvider",
    "DeterministicMockProvider",
    "DisabledProvider",
    "RealProvider",
    "RecordReplayProvider",
    "build_provider",
]
