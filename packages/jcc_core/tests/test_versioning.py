"""ADR-0008 D-37 -- the version surface must AGREE across its three literals.

One fact ("which policy produced this row") is written in three places, and they
were free to drift through P1/P2:

  * ``config/jcc.yaml`` -> ``policy.version``
  * ``jcc_core.config.PolicyConfig.version``  (the dataclass default)
  * ``jcc_core.schemas.Directive.policy_version``  (the persisted default)

A persisted ``Directive`` stamped ``p1.0.0`` while the running config said
something else is exactly the re-adjudication hazard D-37 closes: the tape must
let a reader tell which policy produced a row.

The WIRE version is deliberately NOT part of this: ``AEPEnvelope.version`` stays
``aep/v0.1`` (D-25 / D-38).
"""
from __future__ import annotations

from pathlib import Path

import yaml

from jcc_core import decision
from jcc_core.config import PolicyConfig, load_config
from jcc_core.schemas import SCHEMA_INTERNAL_VERSION, AEPEnvelope, Directive, DirectiveAction

REPO_ROOT = Path(__file__).resolve().parents[3]
SHIPPED_CONFIG = REPO_ROOT / "config" / "jcc.yaml"

P3 = "p3.0.0"


def test_shipped_config_declares_p3():
    doc = yaml.safe_load(SHIPPED_CONFIG.read_text(encoding="utf-8"))
    assert doc["policy"]["version"] == P3


def test_config_default_and_shipped_file_agree():
    """A dataclass default disagreeing with the shipped file is the drift D-37 forbids."""
    assert PolicyConfig().version == P3
    assert load_config(str(SHIPPED_CONFIG)).policy.version == P3


def test_directive_persisted_default_is_p3_and_agrees_with_config():
    d = Directive(directive_id="d", session_id="s", timestamp=0.0,
                  action=DirectiveAction.CONTINUE)
    assert d.policy_version == P3
    # the engine stamps config.policy.version; the bare default must not differ
    assert d.policy_version == PolicyConfig().version


def test_internal_schema_version_and_policy_table_version_are_both_p3():
    """Two different concepts (model shape vs mapping table) -- both move in P3."""
    assert SCHEMA_INTERNAL_VERSION == P3
    assert decision.POLICY_TABLE_VERSION == P3


def test_wire_version_is_untouched_by_d37():
    """D-37 is additive: the frozen wire contract must NOT have moved."""
    assert AEPEnvelope.model_fields["version"].default == "aep/v0.1"
    assert SCHEMA_INTERNAL_VERSION != AEPEnvelope.model_fields["version"].default


def test_no_stale_p1_version_literal_on_the_version_surface():
    """Regression guard with teeth.

    Scans ONLY the files the D-37 register names, so an unrelated historical
    literal elsewhere in the tree cannot make this pass by accident (and cannot
    make it fail spuriously either).

    Inline comments are stripped first: the provenance comments added by the D-37
    bump legitimately record ``p1.0.0 -> p3.0.0``, and a guard that cannot tell a
    comment from a value would fail on the very edit it exists to protect. The
    check is therefore about CODE, where a stale literal would actually take
    effect.
    """
    surface = [
        REPO_ROOT / "config" / "jcc.yaml",
        REPO_ROOT / "packages" / "jcc_core" / "config.py",
        REPO_ROOT / "packages" / "jcc_core" / "schemas.py",
    ]
    offenders = []
    for path in surface:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if "p1.0.0" in code:
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, f"stale p1.0.0 version literal(s): {offenders}"
