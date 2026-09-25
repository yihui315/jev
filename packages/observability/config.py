"""Environment feature-flag switch for the observability layer (S-03).

Contract
--------
* **OFF by default.** ``JCC_OBS_ENABLED`` unset or ``0`` means the layer does
  not exist for the caller: no tracer, no worker thread, no file, no log
  records, no measurable difference in the supervision run. An operator who did
  not opt in gets exactly the behaviour they had yesterday.
* Opt-in is explicit: ``JCC_OBS_ENABLED=1``. ``1``/``true``/``yes``/``on`` are
  accepted; anything else is a FAIL-CLOSED ``0`` with a recorded
  ``config_error`` (never an exception at import time, and never "on").
* ``JCC_OBS_EXPORTER`` selects the sink: ``none`` (default when enabled),
  ``log``, ``file``, ``otel``, ``openinference``. Unknown value => disabled +
  ``config_error``.
* Timeouts and queue bounds are configuration, not magic numbers, but they
  have sane defaults that keep an exporter's stall off the supervision thread.

Every field carries the key it came from (``source``), so a health report can
say "off by default" rather than "off because someone set it".
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

from .exporters import PUBLIC_EXPORTER_KINDS

log = logging.getLogger("jcc.observability.config")

ENV_ENABLED = "JCC_OBS_ENABLED"
ENV_EXPORTER = "JCC_OBS_EXPORTER"
ENV_FILE_PATH = "JCC_OBS_FILE_PATH"
ENV_LOG_LEVEL = "JCC_OBS_LOG_LEVEL"
ENV_TIMEOUT_MS = "JCC_OBS_EXPORTER_TIMEOUT_MS"
ENV_QUEUE_MAX = "JCC_OBS_QUEUE_MAX"
ENV_LOG_NAME = "JCC_OBS_LOG_NAME"

#: Default export deadline. A supervision tick is seconds apart; an exporter
#: that cannot answer inside this window is a stalled exporter, and the whole
#: point is that the loop does not wait for it.
DEFAULT_EXPORTER_TIMEOUT_MS = 250.0
DEFAULT_QUEUE_MAX = 4096

_TRUTHY = {"1", "true", "yes", "on", "enabled"}
_FALSY = {"0", "false", "no", "off", "disabled", ""}


@dataclass(frozen=True)
class ObservabilityConfig:
    """The resolved observability configuration."""

    enabled: bool = False
    exporter: str = "none"
    file_path: Optional[str] = None
    log_level: str = "INFO"
    log_name: str = "jcc.observability.exporter.log"
    exporter_timeout_ms: float = DEFAULT_EXPORTER_TIMEOUT_MS
    queue_max: int = DEFAULT_QUEUE_MAX
    #: ``"default"`` or ``"env"`` -- where the enabled flag came from.
    source: str = "default"
    #: Exactly one failure mode recorded here: an unparsable value. Everything
    #: else fails at wiring time with a message an operator can act on.
    config_error: Optional[str] = None

    # -- reads --------------------------------------------------------------
    @property
    def timeout_s(self) -> float:
        return max(0.001, self.exporter_timeout_ms / 1000.0)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "exporter": self.exporter,
            "source": self.source,
            "config_error": self.config_error,
            "exporter_timeout_ms": self.exporter_timeout_ms,
            "queue_max": self.queue_max,
        }

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "ObservabilityConfig":
        """Resolve from ``os.environ`` (or an injected mapping, for tests)."""
        source = os.environ if env is None else env

        raw_enabled = source.get(ENV_ENABLED)
        if raw_enabled is None:
            enabled, source_of_truth = False, "default"
        else:
            token = str(raw_enabled).strip().lower()
            if token in _TRUTHY:
                enabled, source_of_truth = True, "env"
            elif token in _FALSY:
                enabled, source_of_truth = False, "env"
            else:
                # Fail CLOSED, loudly. "JCC_OBS_ENABLED=yes-ish" must not become
                # observability on with no sink configured.
                return cls(
                    enabled=False, source="env",
                    config_error=f"{ENV_ENABLED}={raw_enabled!r} is not 0/1; "
                                 "observability stays disabled (fail-closed)",
                )

        if not enabled:
            return cls(enabled=False, source=source_of_truth,
                       config_error=None if raw_enabled is not None else None)

        raw_exporter = source.get(ENV_EXPORTER)
        exporter = (raw_exporter or "none").strip().lower()
        if exporter not in PUBLIC_EXPORTER_KINDS:
            return cls(
                enabled=False, source="env", config_error=(
                    f"{ENV_EXPORTER}={raw_exporter!r} is unknown; the closed set is "
                    f"{list(PUBLIC_EXPORTER_KINDS)}. Observability stays disabled "
                    "(fail-closed) rather than running with a sink nobody named."
                ),
            )
        if raw_exporter is None:
            exporter = "none"  # enabled, but no sink chosen -- drop, and SAY it

        timeout_ms = DEFAULT_EXPORTER_TIMEOUT_MS
        raw_timeout = source.get(ENV_TIMEOUT_MS)
        if raw_timeout is not None:
            try:
                timeout_ms = float(str(raw_timeout).strip())
            except ValueError:
                return cls(enabled=False, source="env",
                           config_error=f"{ENV_TIMEOUT_MS}={raw_timeout!r} is not a number")
            if timeout_ms < 0:
                return cls(enabled=False, source="env",
                           config_error=f"{ENV_TIMEOUT_MS} must be >= 0")

        queue_max = DEFAULT_QUEUE_MAX
        raw_queue = source.get(ENV_QUEUE_MAX)
        if raw_queue is not None:
            try:
                queue_max = int(str(raw_queue).strip())
            except ValueError:
                return cls(enabled=False, source="env",
                           config_error=f"{ENV_QUEUE_MAX}={raw_queue!r} is not an integer")
            if queue_max < 1:
                return cls(enabled=False, source="env",
                           config_error=f"{ENV_QUEUE_MAX} must be >= 1")

        return cls(
            enabled=True,
            exporter=exporter,
            file_path=source.get(ENV_FILE_PATH),
            log_level=(source.get(ENV_LOG_LEVEL) or "INFO").strip().upper(),
            log_name=source.get(ENV_LOG_NAME) or "jcc.observability.exporter.log",
            exporter_timeout_ms=timeout_ms,
            queue_max=queue_max,
            source="env",
        )


def describe_config(config: ObservabilityConfig) -> Dict[str, Any]:
    """Machine-readable description, for a ``/api/health``-style endpoint."""
    return config.to_dict()
