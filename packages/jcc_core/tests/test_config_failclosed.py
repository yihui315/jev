"""ADR-0008 D-30 / F-p3-5 -- configuration loading FAILS CLOSED.

Three behaviours that look alike and must not be conflated, because conflating
them IS the defect:

  (a) an EXPLICITLY named config that cannot be used -> ``load_config`` RAISES;
  (b) a MISSING DEFAULT config -> tolerated by ``load_config``, but the SERVER
      ENTRYPOINT refuses to start (a server on invisible defaults is a silent
      downgrade to a policy nobody chose);
  (c) default construction still works, with no filesystem involved.

Plus non-vacuity controls: the named file really is the one that is read, and a
real config still loads (so the raise is not blanket).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jcc_core import config as config_mod
from jcc_core.config import Config, ConfigError, load_config

REPO_ROOT = Path(__file__).resolve().parents[3]
SHIPPED_CONFIG = REPO_ROOT / "config" / "jcc.yaml"


# --------------------------------------------------------------------------- #
# (a) an explicitly requested config that cannot be used => RAISE
# --------------------------------------------------------------------------- #
def test_explicit_jcc_config_that_does_not_exist_raises(tmp_path, monkeypatch):
    """A typo'd ``JCC_CONFIG`` must NOT silently run the built-in defaults.

    This is the whole point of D-30: before the fix, a non-existent path was
    ``os.path.exists()``-guarded away and the server came up on defaults with no
    signal that the operator's file had been ignored.
    """
    missing = tmp_path / "does-not-exist.yaml"
    monkeypatch.setenv("JCC_CONFIG", str(missing))

    with pytest.raises(ConfigError) as ei:
        load_config()
    # the message must name the path, else an operator cannot act on it
    assert "does-not-exist.yaml" in str(ei.value)


def test_explicit_path_argument_that_does_not_exist_raises(tmp_path):
    """The ``path`` argument is an explicit request too, not just the env var."""
    with pytest.raises(ConfigError):
        load_config(str(tmp_path / "also-absent.yaml"))


def test_explicit_config_that_is_unreadable_raises(tmp_path, monkeypatch):
    """Unreadable (not merely absent) also fails closed.

    A DIRECTORY is the portable way to make ``open()`` fail with an ``OSError``
    on both Windows and POSIX without depending on file permissions.
    """
    directory = tmp_path / "a-directory"
    directory.mkdir()
    monkeypatch.setenv("JCC_CONFIG", str(directory))

    with pytest.raises(ConfigError):
        load_config()


def test_explicit_config_with_malformed_yaml_raises(tmp_path, monkeypatch):
    malformed = tmp_path / "malformed.yaml"
    malformed.write_text("policy: [unterminated\n", encoding="utf-8")
    monkeypatch.setenv("JCC_CONFIG", str(malformed))

    with pytest.raises(ConfigError):
        load_config()


def test_explicit_config_that_is_not_a_mapping_raises(tmp_path, monkeypatch):
    """A top-level list/str parses as YAML but is not a config."""
    not_a_mapping = tmp_path / "list.yaml"
    not_a_mapping.write_text("- just\n- a list\n", encoding="utf-8")
    monkeypatch.setenv("JCC_CONFIG", str(not_a_mapping))

    with pytest.raises(ConfigError):
        load_config()


def test_explicit_jcc_config_is_actually_the_file_that_is_read(tmp_path, monkeypatch):
    """Non-vacuity control for the whole (a) group.

    Without this, every assertion above would also hold for an implementation
    that simply ignored the configured path.
    """
    custom = tmp_path / "custom.yaml"
    custom.write_text("server:\n  port: 4999\n", encoding="utf-8")
    monkeypatch.setenv("JCC_CONFIG", str(custom))

    assert load_config().server.port == 4999


# --------------------------------------------------------------------------- #
# (b) the SERVER ENTRYPOINT refuses when there is no config at all
# --------------------------------------------------------------------------- #
def test_entrypoint_refuses_to_start_without_any_config(tmp_path, monkeypatch):
    """(b) The refusal belongs to ``run.main``, NOT to ``load_config``.

    ``load_config`` keeps working without a filesystem (see (c)); it is the
    *server* that must refuse, because only there is "which config am I running?"
    a question whose answer matters.
    """
    from jcc_core import run as run_mod  # local: importing run.py configures logging

    monkeypatch.delenv("JCC_CONFIG", raising=False)
    monkeypatch.setattr(run_mod, "DEFAULT_CONFIG_PATH", str(tmp_path / "no-default.yaml"))

    with pytest.raises(SystemExit) as ei:
        run_mod.main()
    # a refusal must be a FAILURE for whatever supervises the process
    assert ei.value.code not in (0, None), "entrypoint refused but exited 0"


# --------------------------------------------------------------------------- #
# (c) the library stays usable without a filesystem
# --------------------------------------------------------------------------- #
def test_load_config_tolerates_a_missing_DEFAULT_path(tmp_path, monkeypatch):
    """A missing DEFAULT config yields built-in defaults -- no raise, no server."""
    monkeypatch.delenv("JCC_CONFIG", raising=False)
    monkeypatch.setattr(config_mod, "DEFAULT_CONFIG_PATH", str(tmp_path / "absent.yaml"))

    cfg = load_config()

    assert isinstance(cfg, Config)
    assert cfg.server.host == "127.0.0.1", "defaults must still be the safe ones"
    assert cfg.policy.version


def test_the_shipped_config_still_loads():
    """Positive control: the raise above is not blanket."""
    cfg = load_config(str(SHIPPED_CONFIG))
    assert cfg.server.host == "127.0.0.1"
    assert (cfg.raw.get("p3") or {}).get("shadow") is not None
