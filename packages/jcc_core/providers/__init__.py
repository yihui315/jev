"""Provider selection.

Default is the deterministic mock (zero external keys -> demo mode works).
``real`` is only honored when explicitly enabled by feature flag AND a key is
present; otherwise we fail closed to ``DisabledProvider`` (never silently call
an external service).
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

    # default / "mock"
    return DeterministicMockProvider(model=config.jev.model)


__all__ = [
    "JevProvider",
    "DeterministicMockProvider",
    "DisabledProvider",
    "RealProvider",
    "build_provider",
]
