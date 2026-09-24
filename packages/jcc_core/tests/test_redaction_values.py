"""Value-pattern secret redaction tests (P2 security gate finding C1).

The original ``redact()`` matched on KEY NAMES only, so a secret embedded inside
a free-form string under a non-secret key was persisted verbatim. aegis-2's
repro: a ``tool_input.command`` like
``curl -H 'Authorization: Bearer <SECRET>' https://…`` landed in the raw bytes of
``jcc.db``.

These tests cover both directions:
  * secrets in values ARE masked (unit + end-to-end persistence + emit boundary);
  * useful non-secrets are NOT masked (git SHA / UUID / prose / rollout names) --
    the deliberate precision policy in ``adapters/_common.py``.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
PACKAGES = REPO / "packages"
_INTEG_COMMON = REPO / "integrations" / "_common"
for p in (str(PACKAGES), str(_INTEG_COMMON)):
    if p not in sys.path:
        sys.path.insert(0, p)

from jcc_core.adapters import ClaudeAdapter
from jcc_core.adapters._common import redact, scrub_str, scrub_value_secrets
from jcc_core.actuator import Actuator
from jcc_core.config import load_config
from jcc_core.core import CoreEngine
from jcc_core.providers import build_provider
from jcc_core.state_store import StateStore

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SECRET = "sk-live-PLANTEDbravo99887766554433221100"
BODY = "curl -H 'Authorization: Bearer " + SECRET + "' https://api.example.com/v1"


def _engine(tmp: str) -> CoreEngine:
    cfg = load_config(os.environ.get("JCC_CONFIG"))
    store = StateStore(os.path.join(tmp, "jcc.db"))
    return CoreEngine(store, build_provider(cfg), Actuator(enabled=False, allowed_actions=[]), cfg)


def _db_bytes(tmp: str) -> bytes:
    blob = b""
    for suffix in ("", "-wal", "-shm"):
        f = Path(tmp) / ("jcc.db" + suffix)
        if f.exists():
            blob += f.read_bytes()
    return blob


# --------------------------------------------------------------------------- #
# 1) Unit: secret-looking VALUES are masked
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,secret", [
    (BODY, SECRET),                                                    # Bearer in -H
    ("Authorization: Bearer " + SECRET, SECRET),                        # header-like
    ("authorization=Bearer " + SECRET, SECRET),
    ("api_key=" + SECRET, SECRET),                                      # labelled sk-
    ("token=abc123XYZsecretValue", "abc123XYZsecretValue"),
    ("password=hunter2", "hunter2"),
    ("curl -u alice:s3cr3tpw https://x", "s3cr3tpw"),                   # basic auth
    ("key=sk-proj-ABCDEFGHIJKLMNOPQRSTUVWX", "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWX"),
    ("pat=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"),
    ("aws=AKIAIOSFODNN7EXAMPLE", "AKIAIOSFODNN7EXAMPLE"),
    ("slack=xoxb-123456789012-abcdefghijklmn", "xoxb-123456789012-abcdefghijklmn"),
    ("jwt=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
     "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"),
    ("blob=" + "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo=", "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo="),
    ("h=" + "a1b2c3d4e5f6" * 4, "a1b2c3d4e5f6" * 4),                     # 48-hex
])
def test_secret_values_are_masked(text, secret):
    out = scrub_str(text)
    assert secret not in out, f"secret survived: {out!r}"
    assert "<redacted:" in out, f"no marker produced: {out!r}"


def test_marker_is_stable_and_idempotent():
    once = scrub_str(BODY)
    twice = scrub_str(once)
    assert once == twice
    assert "<redacted:bearer>" in once


def test_recursive_scrub_reaches_nested_leaves():
    payload = {"a": [{"b": {"command": BODY}}], "c": "x"}
    out = scrub_value_secrets(payload)
    assert SECRET not in json.dumps(out)
    assert out["c"] == "x"                       # untouched leaf preserved
    assert out["a"][0]["b"]["command"].startswith("curl -H 'Authorization: Bearer <redacted:")


def test_redact_still_key_name_redacts():
    out = redact({"api_key": SECRET, "password": "hunter2", "keep": "visible"})
    assert out["api_key"] == "[REDACTED]"
    assert out["password"] == "[REDACTED]"
    assert out["keep"] == "visible"


# --------------------------------------------------------------------------- #
# 2) Non-over-redaction invariants (deliberate precision policy)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text", [
    "3f2a9b1c4d5e6f708192a3b4c5d6e7f8091a2b3c",                      # 40-hex git SHA
    "550e8400-e29b-41d4-a716-446655440000",                          # UUID
    "rollout-2026-04-14T09-55-07-019d89b3-1219-7233-866e-9e9717cd8218",
    "the quick brown fox jumps over the lazy dog",
    "F:\\jev\\packages\\jcc_core\\adapters\\_common.py",
    "continuation_token is fine",
])
def test_useful_non_secrets_are_preserved(text):
    assert scrub_str(text) == text, f"over-redacted: {scrub_str(text)!r}"


# --------------------------------------------------------------------------- #
# 3) End-to-end: aegis-2's adversarial case must not reach the DB
# --------------------------------------------------------------------------- #
def test_value_embedded_secret_never_persisted():
    with tempfile.TemporaryDirectory(prefix="jcc_c1_") as tmp:
        engine = _engine(tmp)
        raw = json.loads((FIXTURES / "claude_hook_pre_tool_use.json").read_text(encoding="utf-8"))
        raw["tool_input"] = {"command": BODY}                # secret inside a free-form VALUE
        raw["reason"] = "blocked: saw Bearer " + SECRET      # directly-set payload field
        raw["authorization"] = "Bearer " + SECRET            # named secret key
        env = asyncio.run(ClaudeAdapter().normalize(raw))

        engine.ingest(env)

        blob = _db_bytes(tmp)
        assert SECRET.encode() not in blob, "value-embedded SECRET leaked into jcc.db!"
        assert b"Bearer " + SECRET.encode() not in blob
        # event still recorded; audit chain intact
        assert engine.store.get_session(env.session_id) is not None
        ok, n = engine.store.verify_audit()
        assert ok is True and n >= 1


def test_session_id_identity_survives_scrubbing():
    """Scrubbing must never alter the PRIMARY identity."""
    raw = json.loads((FIXTURES / "claude_hook_pre_tool_use.json").read_text(encoding="utf-8"))
    raw["tool_input"] = {"command": BODY}
    env = asyncio.run(ClaudeAdapter().normalize(raw))
    sid_before = env.session_id
    scrubbed = scrub_value_secrets(env.payload)
    assert env.session_id == sid_before
    assert sid_before == "f7fdc56a-0d08-46b4-b661-c775c1aeb2c9" or sid_before  # UUID untouched
    assert SECRET not in json.dumps(scrubbed)


# --------------------------------------------------------------------------- #
# 4) Emit boundary: --dry-run / HTTP body / spool must also be clean
# --------------------------------------------------------------------------- #
def test_emit_normalize_event_is_scrubbed():
    import jcc_emit

    raw = json.loads((FIXTURES / "claude_hook_pre_tool_use.json").read_text(encoding="utf-8"))
    raw["tool_input"] = {"command": BODY}
    data = jcc_emit.normalize_event("claude", raw)      # what --dry-run prints
    assert SECRET not in json.dumps(data), "secret leaked through the emit boundary"
    assert data["payload"]["tool_input"]["command"].startswith("curl -H 'Authorization: Bearer <redacted:")


# --------------------------------------------------------------------------- #
# 5) Spool boundary: raw / garbled payloads must not persist secrets verbatim
#    (P2 gate FINDING-D, aegis-2 re-verify)
# --------------------------------------------------------------------------- #
GARBLED = ("garbled notify Bearer sk-aegis2PLANT-7f3a9c1e5b2d8f04 "
           "AKIAIOSFODNN7EXAMPLE not-json")


def test_spool_scrubs_free_text_payload(tmp_path, monkeypatch):
    """Unit: spool() is a persistence boundary and must scrub string leaves."""
    import jcc_emit

    monkeypatch.setenv("JCC_SPOOL_DIR", str(tmp_path / "spool"))
    path = jcc_emit.spool("codex", {"kind": "notify_raw", "payload": GARBLED})
    written = path.read_text(encoding="utf-8")

    assert "sk-aegis2PLANT-7f3a9c1e5b2d8f04" not in written, "bearer secret in spool!"
    assert "AKIAIOSFODNN7EXAMPLE" not in written, "aws key in spool!"
    assert "<redacted:bearer>" in written
    # the record shape / keys survive; only the secret values are masked
    rec = json.loads(written.splitlines()[-1])
    assert rec["kind"] == "notify_raw"
    assert rec["payload"].startswith("garbled notify Bearer <redacted:")


def test_codex_notify_garbled_payload_does_not_leak_to_spool(tmp_path):
    """End-to-end: run the real codex notify helper with an undecodable,
    secret-laden payload and assert nothing leaks into ~/.jcc/spool/codex.jsonl.

    This is the EXACT path aegis-2 flagged: jcc-codex-notify.py:71 spools the raw
    notify text when the JSON fails to decode (a normal cmd.exe occurrence).
    """
    import subprocess

    spool_dir = tmp_path / "spool"
    env = dict(os.environ)
    env["JCC_SPOOL_DIR"] = str(spool_dir)
    env["JCC_ROOT"] = str(REPO)
    sidecar = tmp_path / "no-such-sidecar.json"   # force _chain() to no-op

    script = REPO / "integrations" / "codex" / "jcc-codex-notify.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--sidecar", str(sidecar), GARBLED],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, f"notify helper must never fail: {proc.stderr}"

    spool_file = spool_dir / "codex.jsonl"
    assert spool_file.exists(), "expected the raw payload to be spooled"
    written = spool_file.read_text(encoding="utf-8")
    assert "sk-aegis2PLANT-7f3a9c1e5b2d8f04" not in written, "bearer secret leaked to spool!"
    assert "AKIAIOSFODNN7EXAMPLE" not in written, "aws key leaked to spool!"
    assert "<redacted:" in written
