"""Configuration loading for JCC Core.

Reads the v1.3 product config ``config/jcc-v1.3.yaml`` (path overridable via
``JCC_CONFIG``) and applies environment overrides. The control token is NEVER
stored in config — it is read from ``JCC_CONTROL_TOKEN`` at runtime
(fail-closed if unset).

The GREEN v1.0.1 baseline is a DIFFERENT instance with a different file,
``config/jcc.yaml``, and it must be named explicitly (``JCC_CONFIG``) to run —
see :data:`DEFAULT_CONFIG_PATH` for the fail-open this replaces, and
:func:`is_green_config` for how the two instances are told apart.

Provider mode (mock | disabled | real) may be overridden by ``JCC_PROVIDER``.
A DB path override ``JCC_DB_PATH`` is honoured so the test harness can use an
isolated database without touching runtime state.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import yaml

#: Repository root, derived from this file's location rather than hardcoded.
#: ``config.py`` lives at ``<repo>/packages/jcc_core/config.py``, so three
#: ``parents`` steps up is the repo root. Hardcoding an absolute drive path made
#: the whole control plane unloadable the moment the checkout moved, which is a
#: relocation bug, not a configuration choice.
REPO_ROOT = Path(__file__).resolve().parents[2]

#: The DEFAULT is the v1.3 PRODUCT EDITION, not the GREEN baseline.
#:
#: HISTORY (CR-002, fail-open): this used to be ``config/jcc.yaml`` -- the GREEN
#: baseline's own file, which carries ``server.port: 4778``. So a bare
#: ``python -m jcc_core.run`` with no ``JCC_CONFIG`` came up as a *second* v1.3
#: product instance ON TOP OF GREEN's protected port, which breaks the port
#: ownership contract in config/jcc-v1.3.yaml and invalidates every GREEN-protection
#: assertion the release makes. A product instance squatting the baseline's port is
#: indistinguishable, from a health probe, from the baseline itself.
#:
#: WHY THIS DOES NOT MOVE GREEN (measured, not assumed): GREEN is only ever
#: started by ``scripts/start.ps1``, which sets ``JCC_CONFIG`` EXPLICITLY to
#: ``config\jcc.yaml`` before spawning the child. ``restart.ps1`` and
#: ``ensure-stable-runtime.ps1`` both delegate to that same script, and the two
#: durable Scheduled Tasks run the latter -- so no GREEN launch path reads this
#: default. ``runtime/jcc/jcc.ownership.json`` corroborates it: ``config =
#: F:\\jev\\config\\jcc.yaml``, ``created_by = scripts/start.ps1``.
DEFAULT_CONFIG_PATH = str(REPO_ROOT / "config" / "jcc-v1.3.yaml")

#: The GREEN baseline's configuration, and therefore its DECLARED IDENTITY.
#:
#: An instance's config file is what names it: GREEN is the thing started with
#: this file, and a v1.2/v1.3/product instance is anything started with another
#: one. ``run.check_green_port_ownership`` uses this to decide whether a process
#: asking for :data:`GREEN_PROTECTED_PORT` is GREEN itself or a product instance
#: that must be refused -- so GREEN keeps starting with no change to its launcher,
#: and no new env var that a future launch path could forget to set.
GREEN_CONFIG_PATH = str(REPO_ROOT / "config" / "jcc.yaml")

#: Loopback port reserved for the frozen GREEN v1.0.1 baseline. A product
#: instance must never bind it (master directive 0.2: read-only, never signal,
#: stop, kill, rewrite, migrate or mutate).
GREEN_PROTECTED_PORT = 4778

DEFAULT_DB_PATH = str(REPO_ROOT / "runtime" / "jcc" / "jcc.db")


def is_green_config(path: str) -> bool:
    """True when ``path`` is GREEN's own config file.

    Compares resolved, case-folded absolute paths so ``JCC_CONFIG`` spelled with
    a different separator or drive-letter case still identifies correctly on
    Windows. An empty/unusable path is NOT GREEN -- fail-closed.
    """
    if not path:
        return False
    try:
        return (os.path.normcase(os.path.abspath(str(path)))
                == os.path.normcase(os.path.abspath(GREEN_CONFIG_PATH)))
    except (OSError, ValueError):
        return False


class ConfigError(RuntimeError):
    """An EXPLICITLY requested configuration cannot be used.

    ADR-0008 D-30 / F-p3-5: config load fails CLOSED. Falling back to the
    built-in defaults would run the control plane on a policy the operator never
    chose -- a silent downgrade, and exactly the class of defect D-30 forbids.
    """


def _resolve_db_path(raw: str) -> str:
    """Resolve a configured ``state_store.db_path`` against the repo root.

    A RELATIVE path is anchored to :data:`REPO_ROOT` so the database lands in the
    same place no matter what the process CWD happens to be -- the old absolute
    path was correct only while the checkout stayed on one drive.

    An ABSOLUTE path is honoured verbatim (with ``/`` normalised for Windows),
    because an operator who names an absolute location means it, and silently
    re-anchoring it would be a downgrade of their intent.
    """
    text = str(raw).strip()
    if not text:
        return DEFAULT_DB_PATH
    candidate = Path(text)
    if candidate.is_absolute():
        return str(candidate)
    return str((REPO_ROOT / candidate).resolve())


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"          # hardcoded to localhost-only at bind time too
    # CR-002: the built-in default used to be 4778 -- GREEN's protected port. A
    # Config built with NO server block (library callers, tests) therefore
    # described a process that would land on the baseline's port. The default is
    # now the canonical PRODUCT port, so "no port configured" and "GREEN" are no
    # longer the same statement. GREEN still gets 4778 the only way it should:
    # by naming config/jcc.yaml, which declares it.
    port: int = 4779
    cors_origins: List[str] = field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ]
    )


@dataclass
class JevConfig:
    provider: str = "mock"           # mock | disabled | real
    model: str = "jev-1.13.0"
    api_key_env: str = "JEV_API_KEY"
    base_url: str = "https://api.typesafe.ai"
    feature_flags: Dict[str, Any] = field(
        default_factory=lambda: {"real_provider": False}
    )


@dataclass
class ActuationConfig:
    enabled: bool = False            # P0-P2: actuation OFF (ADR-0004)
    allowed_actions: List[str] = field(default_factory=list)


@dataclass
class PolicyConfig:
    version: str = "p3.0.0"          # ADR-0008 D-37 (was p1.0.0 through P2)
    priority: List[str] = field(
        default_factory=lambda: [
            "ESCALATE", "PAUSE", "VERIFY", "STEER", "RETRY", "FINISH", "CONTINUE"
        ]
    )
    thresholds: Dict[str, Any] = field(
        default_factory=lambda: {
            "escalate_risk": 0.8,
            "pause_risk": 0.6,
            "verify_risk": 0.4,
        }
    )


@dataclass
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    control_token_env: str = "JCC_CONTROL_TOKEN"
    jev: JevConfig = field(default_factory=JevConfig)
    state_store_db_path: str = DEFAULT_DB_PATH
    actuation: ActuationConfig = field(default_factory=ActuationConfig)
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    agents: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)
    #: The config file this Config was actually read from. Empty means the
    #: built-in defaults (no file was found), which is NOT the GREEN instance.
    config_path: str = ""
    #: True when the operator named the file (``path`` argument or ``JCC_CONFIG``)
    #: rather than falling through to the default.
    config_is_explicit: bool = False

    @property
    def control_token(self) -> "str | None":
        return os.environ.get(self.control_token_env)

    @property
    def is_green_instance(self) -> bool:
        """True only for the GREEN baseline instance.

        Deliberately NOT "the port is 4778": that would make the check circular
        (a misconfigured product asking for 4778 would thereby qualify as GREEN).
        Identity comes from the config file that was loaded.
        """
        return is_green_config(self.config_path)


def load_config(path: "str | None" = None) -> Config:
    """Load configuration, FAIL-CLOSED whenever a config was explicitly named.

    Two situations that look similar and must NOT be conflated (D-30/F-p3-5):

    * **Explicit request** -- the ``path`` argument, or a set ``JCC_CONFIG``. If
      that file is missing, unreadable, or unusable YAML, this RAISES
      :class:`ConfigError`. It never degrades to the built-in defaults: an
      operator who named a file gets that file or an error, nothing else.
    * **Default path** -- a missing ``DEFAULT_CONFIG_PATH`` yields the built-in
      defaults, so library callers and tests can build a ``Config`` without a
      filesystem. Refusing to *start* in that case belongs to the SERVER
      ENTRYPOINT (``run.main``), which is the only place that can decide a
      process must not come up without a deliberately chosen config.

    A malformed or non-mapping default file still RAISES: a corrupt config is
    never ignorable, only a *missing* default is.

    The file actually read is recorded on ``Config.config_path`` (and whether it
    was named explicitly on ``Config.config_is_explicit``). ``run.py`` needs
    both: a process may only hold :data:`GREEN_PROTECTED_PORT` if it is the
    GREEN instance, and "is the GREEN instance" is decided by WHICH file was
    loaded, not by a flag a launcher might forget to set.
    """
    explicit = path or os.environ.get("JCC_CONFIG")
    target = explicit or DEFAULT_CONFIG_PATH
    try:
        with open(target, "r", encoding="utf-8") as fh:
            raw_text = fh.read()
    except FileNotFoundError:
        if explicit:
            raise ConfigError(
                f"config file {target!r} does not exist; refusing to fall back to "
                f"the built-in defaults (fail-closed, ADR-0008 D-30)"
            ) from None
        data: Dict[str, Any] = {}
    except OSError as exc:  # permissions, EISDIR, ...
        raise ConfigError(f"config file {target!r} is unreadable: {exc}") from exc
    else:
        try:
            data = yaml.safe_load(raw_text) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"config file {target!r} is not valid YAML: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError(
                f"config file {target!r} must hold a mapping at the top level, "
                f"got {type(data).__name__}"
            )

    server = ServerConfig(**(data.get("server") or {}))  # type: ignore[arg-type]

    jev_data = dict(data.get("jev") or {})
    if os.environ.get("JCC_PROVIDER"):          # env override of provider mode
        jev_data["provider"] = os.environ["JCC_PROVIDER"]
    jev = JevConfig(**jev_data)                 # type: ignore[arg-type]

    db_path = (
        os.environ.get("JCC_DB_PATH")
        or (data.get("state_store") or {}).get("db_path")
        or DEFAULT_DB_PATH
    )
    db_path = _resolve_db_path(db_path)

    act = data.get("actuation") or {}
    actuation = ActuationConfig(**act)          # type: ignore[arg-type]

    pol = data.get("policy") or {}
    policy = PolicyConfig(**pol)                # type: ignore[arg-type]

    agents = (data.get("agents") or {}).get("capabilities", {})

    return Config(
        server=server,
        control_token_env=(data.get("control") or {}).get("token_env", "JCC_CONTROL_TOKEN"),
        jev=jev,
        state_store_db_path=db_path,
        actuation=actuation,
        policy=policy,
        agents=agents,
        raw=data,
        config_path=str(target),
        config_is_explicit=bool(explicit),
    )
