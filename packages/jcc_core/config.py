"""Configuration loading for JCC Core.

Reads ``config/jcc.yaml`` (path overridable via ``JCC_CONFIG``) and applies
environment overrides. The control token is NEVER stored in config — it is
read from ``JCC_CONTROL_TOKEN`` at runtime (fail-closed if unset).

Provider mode (mock | disabled | real) may be overridden by ``JCC_PROVIDER``.
A DB path override ``JCC_DB_PATH`` is honoured so the test harness can use an
isolated database without touching runtime state.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List

import yaml

DEFAULT_CONFIG_PATH = "F:/jev/config/jcc.yaml"


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"          # hardcoded to localhost-only at bind time too
    port: int = 4778
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
    version: str = "p1.0.0"
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
    state_store_db_path: str = "F:/jev/runtime/jcc/jcc.db"
    actuation: ActuationConfig = field(default_factory=ActuationConfig)
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    agents: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def control_token(self) -> "str | None":
        return os.environ.get(self.control_token_env)


def load_config(path: "str | None" = None) -> Config:
    path = path or os.environ.get("JCC_CONFIG") or DEFAULT_CONFIG_PATH
    data: Dict[str, Any] = {}
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}

    server = ServerConfig(**(data.get("server") or {}))  # type: ignore[arg-type]

    jev_data = dict(data.get("jev") or {})
    if os.environ.get("JCC_PROVIDER"):          # env override of provider mode
        jev_data["provider"] = os.environ["JCC_PROVIDER"]
    jev = JevConfig(**jev_data)                 # type: ignore[arg-type]

    db_path = (
        os.environ.get("JCC_DB_PATH")
        or (data.get("state_store") or {}).get("db_path")
        or "F:/jev/runtime/jcc/jcc.db"
    )

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
    )
