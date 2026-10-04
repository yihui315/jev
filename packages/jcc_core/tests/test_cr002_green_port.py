"""CR-002 -- the GREEN baseline port is fail-closed, for EVERY spelling of 4778.

THE DEFECT THIS SUITE EXISTS FOR
--------------------------------
`run.check_green_port_ownership` originally compared ``config.server.port`` to
``GREEN_PROTECTED_PORT`` with ``!=``. ``ServerConfig`` is a bare dataclass and
``load_config`` splats ``**data["server"]`` into it, so the YAML TYPE survives
all the way to the bind. A config with a quoted port therefore slipped through:

    server:
      port: '4778'          # str, not int

    '4778' != 4778  ->  guard returns early  ->  binds GREEN's port

Independently verified on 2026-10-04 (metric-verify), and reproduced here before
the fix: ``port='4778' -> REFUSED=False`` while ``port=4778 -> REFUSED=True``.
The failure is invisible from outside -- the impostor answers ``/api/health``
with 200, exactly like the baseline.

The other half of the suite protects the fix from being "resolved" the wrong
way. A guard that refuses EVERY port, or one that refuses GREEN itself, would
also stop the quoted form -- so both are asserted as ALLOWED. Without those the
suite would pass against a guard that simply refuses to start anything.

The launcher half (Get-JccPort resolving GREEN's port) is PowerShell and is
covered by tools/cr002_green_port_falsifier.py; this file is the Python unit
layer, fast enough to run in the ordinary suite.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jcc_core.config import (
    GREEN_CONFIG_PATH,
    GREEN_PROTECTED_PORT,
    Config,
    ConfigError,
    ServerConfig,
    load_config,
)
from jcc_core.run import check_green_port_ownership

REPO_ROOT = Path(__file__).resolve().parents[3]
NEWLINE = "\n"


def _write(tmp_path: Path, port_literal: str) -> str:
    """Write a minimal config whose server.port is the given YAML literal.

    The literal is written verbatim, because the whole point is to control the
    YAML type. ``port: '4778'`` MUST stay quoted or the test proves nothing.
    """
    cfg = tmp_path / "candidate.yaml"
    cfg.write_text(
        "server:" + NEWLINE
        + "  host: 127.0.0.1" + NEWLINE
        + "  port: " + port_literal + NEWLINE,
        encoding="utf-8",
    )
    return str(cfg)


# --------------------------------------------------------------------------- #
# every spelling of the protected port must be refused
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("literal", [
    "4778",          # int
    "'4778'",        # single-quoted -> str
    '"4778"',        # double-quoted -> str
    "4778.0",        # float
    "' 4778 '",      # padded string
])
def test_every_spelling_of_the_green_port_is_refused(tmp_path, literal):
    """THE REGRESSION. A quoted 4778 must not slip past the guard."""
    cfg = load_config(_write(tmp_path, literal))

    with pytest.raises(SystemExit) as ei:
        check_green_port_ownership(cfg)

    # A refusal must be a FAILURE for whatever supervises the process.
    assert ei.value.code not in (0, None), "guard refused but exited 0"
    # And it must be actionable: name the port and the way out.
    msg = str(ei.value)
    assert str(GREEN_PROTECTED_PORT) in msg
    assert "jcc-v1.3.yaml" in msg


def test_quoted_port_really_is_a_string_at_bind_time(tmp_path):
    """Non-vacuity: prove the fixture produces a str, not an int.

    Without this, a future edit that made the loader coerce the port would turn
    every case above into a tautology -- they would still pass, but no longer be
    testing the type-confusion path they exist to cover.
    """
    cfg = load_config(_write(tmp_path, "'4778'"))
    assert isinstance(cfg.server.port, str), (
        "expected the quoted port to stay a str all the way to the guard, got "
        "%s -- if the loader now coerces ports, this file's cases are no longer "
        "exercising type confusion and should be revisited"
        % type(cfg.server.port).__name__)


@pytest.mark.parametrize("literal,expected_type", [
    ("4778", int), ("'4778'", str), ('"4778"', str), ("4778.0", float),
])
def test_the_fixture_actually_produces_each_yaml_type(tmp_path, literal,
                                                      expected_type):
    """The parametrised cases are only meaningful if the TYPES really differ.

    Kept separate from the refusal cases because the refusal assertion holds for
    every spelling, including ones that are not type-confused at all. This test
    is what stops the refusal cases from silently degrading into "int works"
    over and over -- which is exactly how the original defect survived review.
    """
    cfg = load_config(_write(tmp_path, literal))
    assert isinstance(cfg.server.port, expected_type), (
        "literal %r produced %s, expected %s"
        % (literal, type(cfg.server.port).__name__, expected_type.__name__))


# --------------------------------------------------------------------------- #
# the guard must not become a blanket refusal
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("literal", ["4779", "'4779'", "4800"])
def test_other_ports_are_not_refused(tmp_path, literal):
    """A guard that refuses every port is not a fix; it is an outage."""
    cfg = load_config(_write(tmp_path, literal))
    check_green_port_ownership(cfg)  # must not raise


def test_green_instance_may_still_bind_the_green_port():
    """POSITIVE CONTROL. Refusing GREEN would be worse than the defect."""
    cfg = load_config(GREEN_CONFIG_PATH)
    assert cfg.server.port == GREEN_PROTECTED_PORT
    assert cfg.is_green_instance is True
    check_green_port_ownership(cfg)  # must not raise


def test_a_renamed_copy_of_green_config_is_still_refused(tmp_path):
    """Copying GREEN's config elsewhere must not buy GREEN's port.

    Identity is the file, so a byte-identical copy at another path is a PRODUCT
    instance wearing GREEN's config. This is the cheapest bypass available and it
    has to fail.
    """
    copy = tmp_path / "renamed-green.yaml"
    copy.write_text(Path(GREEN_CONFIG_PATH).read_text(encoding="utf-8"),
                    encoding="utf-8")
    cfg = load_config(str(copy))
    assert cfg.server.port == GREEN_PROTECTED_PORT
    assert cfg.is_green_instance is False
    with pytest.raises(SystemExit):
        check_green_port_ownership(cfg)


def test_path_spelling_of_green_config_still_identifies_as_green():
    """``config/../config/jcc.yaml`` is GREEN -- normalise, do not refuse it."""
    odd = str(Path(GREEN_CONFIG_PATH).parent.parent / "config" / "jcc.yaml")
    cfg = load_config(odd)
    assert cfg.is_green_instance is True
    check_green_port_ownership(cfg)  # must not raise


# --------------------------------------------------------------------------- #
# a non-numeric port is not this guard's business
# --------------------------------------------------------------------------- #
def test_a_non_numeric_port_is_not_treated_as_the_green_port(tmp_path):
    """Fail honestly: an unparseable port is not 'GREEN', it is broken config.

    Claiming it is GREEN would produce a misleading message and, worse, would
    mean the guard's answer depended on a coincidence rather than on the port.
    uvicorn rejects it later, where the operator sees the real problem.
    """
    cfg = load_config(_write(tmp_path, "not-a-port"))
    assert cfg.server.port == "not-a-port"
    check_green_port_ownership(cfg)  # must not raise


# --------------------------------------------------------------------------- #
# the refactor must not have broken config loading
# --------------------------------------------------------------------------- #
def test_shipped_configs_still_load_with_their_declared_ports():
    """Both shipped instances load, and each keeps its own port.

    This is the load-time half of the launcher regression: after CR-002 changed
    DEFAULT_CONFIG_PATH, anything that resolved a port WITHOUT naming a config
    started reading the v1.3 port while still operating on GREEN.
    """
    green = load_config(str(REPO_ROOT / "config" / "jcc.yaml"))
    v13 = load_config(str(REPO_ROOT / "config" / "jcc-v1.3.yaml"))
    assert green.server.port == GREEN_PROTECTED_PORT
    assert green.is_green_instance is True
    assert v13.server.port != GREEN_PROTECTED_PORT
    assert v13.is_green_instance is False


def test_default_config_is_not_green():
    """The default must be the PRODUCT instance, or a bare run squats on 4778."""
    from jcc_core import config as cfg_mod

    default = load_config()
    assert Path(default.config_path).name == "jcc-v1.3.yaml"
    assert default.server.port != GREEN_PROTECTED_PORT
    assert default.is_green_instance is False
    assert Path(cfg_mod.DEFAULT_CONFIG_PATH).name != "jcc.yaml"


def test_a_config_with_no_server_block_defaults_off_the_green_port():
    """``ServerConfig()`` must not describe a process that lands on 4778."""
    assert ServerConfig().port != GREEN_PROTECTED_PORT


def test_explicit_bad_config_still_raises_config_error(tmp_path):
    """Pre-existing fail-closed behaviour must survive the CR-002 edits."""
    missing = tmp_path / "nope.yaml"
    with pytest.raises(ConfigError):
        load_config(str(missing))
