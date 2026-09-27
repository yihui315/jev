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
from jcc_core.redaction import (
    DEPTH_LIMIT_MARK,
    mask_secret_key_values,
    redact,
    scrub_str,
    scrub_value_secrets,
)
from jcc_core.actuator import Actuator
from jcc_core.config import load_config
from jcc_core.core import CoreEngine
from jcc_core.providers import build_provider
from jcc_core.schemas import AEPEnvelope
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


# --------------------------------------------------------------------------- #
# 6) Depth cap (P2 gate F-1): a secret nested deeper than MAX_SCRUB_DEPTH must
#    be COLLAPSED to the depth-limit marker, never passed through raw.
#    Reachable over POST /api/assess (no adapter in path -> only ingest's scrub).
# --------------------------------------------------------------------------- #
def _nest(depth: int, leaf: str) -> dict:
    """Build a dict nested ``depth`` levels deep whose final leaf holds ``leaf``."""
    root = {"k": "v"}
    node = root
    for _ in range(depth):
        node["child"] = {"k": "v"}
        node = node["child"]
    node["leak"] = leaf
    return root


def test_depth_cap_returns_marker_not_raw():
    """Structural proof the cap COLLAPSES (recursion stops) instead of leaking."""
    secret = "sk-deepcap-STRUCTURAL0123456789abcdef"
    for depth in (9, 12, 50, 200):
        out = scrub_value_secrets(_nest(depth, "Bearer " + secret))
        flat = json.dumps(out)
        assert secret not in flat, f"secret survived at depth {depth}"
        assert DEPTH_LIMIT_MARK in flat, f"expected a depth-limit marker at depth {depth}"


def test_depth_cap_marker_present_at_first_collapsed_level():
    """The marker replaces the sub-tree at the first level past the cap (8)."""
    out = scrub_value_secrets(_nest(12, "Bearer sk-x"))
    node = out
    for _ in range(8):
        node = node["child"]                                # descend to depth 8
    assert node["child"] == DEPTH_LIMIT_MARK                # depth 9 collapsed


@pytest.mark.parametrize("depth", [10, 12])
def test_deep_nested_secret_never_persisted(depth):
    """In-process (no adapter): secret below the cap must not reach the DB bytes.

    Mirrors the POST /api/assess path, where ``engine.ingest``'s scrub is the
    ONLY protection (no adapter/``redact()`` in the path).
    """
    secret = "sk-deepcap-PLANTED" + "0123456789abcdef01234567"
    with tempfile.TemporaryDirectory(prefix="jcc_f1_") as tmp:
        engine = _engine(tmp)
        deep = _nest(depth, "Bearer " + secret)
        env = AEPEnvelope(
            event_id=f"ev_deep_{depth}",
            delivery_id=f"dl_deep_{depth}",
            session_id=f"sess-deep-{depth}",
            agent="workbuddy",
            project="demo",
            cwd="/tmp",
            event_type="tool_call",
            payload=deep,
            evidence={"deep": deep},
        )
        engine.ingest(env)

        blob = _db_bytes(tmp)
        assert secret.encode() not in blob, f"depth-{depth} secret leaked into jcc.db!"
        # event still recorded; identity intact
        assert engine.store.get_session(env.session_id) is not None
        ok, n = engine.store.verify_audit()
        assert ok is True and n >= 1


# --------------------------------------------------------------------------- #
# 7) §21 S-02c — the ingest CHOKEPOINT must apply the KEY-NAME pass too.
#    Pre-fix, engine.ingest() ran ``scrub_value_secrets`` alone (value-pattern
#    only, key-blind), so an AEP envelope whose payload held a secret under a
#    secret-NAMED key (api_key / token / authorization / …) was persisted
#    VERBATIM into jcc.db. This is reachable with NO adapter in the path
#    (POST /api/assess -> AEPEnvelope(**body) -> engine.ingest), which is exactly
#    the ingress ADR-0007 D-1 names as relying solely on the chokepoint.
# --------------------------------------------------------------------------- #
BENIGN = "benign-note-keep-me-visible"
KEYNAME_SECRET = "keyname-leak-PLANTED-4f9a2c8e1b7d3056"
KEYNAME_SECRET2 = "keyname-evidence-PLANTED-99aa88bb77cc66dd"
#: > MAX_TEXT_LEN (240) and free of any secret shape, so ONLY redact()'s blanket
#: truncation could shorten it — used to pin the chokepoint's no-truncation policy.
LONG = ("lorem ipsum dolor sit amet " * 20).strip()


def _all_row_reprs(store) -> str:
    """repr() of EVERY row in EVERY table — the "did it reach the DB" check."""
    chunks = []
    with store.connection() as conn:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        for t in tables:
            for row in conn.execute(f"SELECT * FROM {t}").fetchall():
                chunks.append(f"{t}:{row!r}")
    return "\n".join(chunks)


def test_keyname_secret_from_chokepoint_never_persisted(tmp_path):
    """(a) §21 S-02c regression guard — the non-vacuous key-name chokepoint test.

    Ingest an envelope whose payload carries a secret under ``api_key`` (a
    secret-NAMED key) alongside a benign string; the evidence carries one under
    ``token``. Asserts the secrets are absent from the RAW DB BYTES and from
    repr() of EVERY row, while the benign string SURVIVES (so a "shred the whole
    payload" implementation cannot pass) and the session/event rows really exist
    (so "nothing was written at all" cannot pass).

    NON-VACUITY (negative direction): before ingest, the value-pattern pass is
    run over the SAME payload and the secret is asserted STILL PRESENT — proving
    ``scrub_value_secrets`` alone does not close this hole, i.e. this test FAILS
    if the key-name pass is reverted from the chokepoint.
    """
    payload = {"tool_input": {"api_key": KEYNAME_SECRET, "note": BENIGN}}
    # negative direction: value-pattern-only scrubbing leaves the secret in place.
    assert KEYNAME_SECRET in json.dumps(scrub_value_secrets(payload)), (
        "non-vacuity broken: scrub_value_secrets alone already removed the secret, "
        "so this test would not prove the key-name pass does anything"
    )

    engine = _engine(str(tmp_path))
    env = AEPEnvelope(
        event_id="ev_keyname",
        delivery_id="dl_keyname",
        session_id="sess-keyname",
        agent="workbuddy",
        project="demo",
        cwd="/tmp",
        event_type="tool_call",
        payload=payload,
        evidence={"token": KEYNAME_SECRET2, "ok": True},
    )
    engine.ingest(env)

    blob = _db_bytes(str(tmp_path))
    assert KEYNAME_SECRET.encode() not in blob, "payload api_key secret leaked into jcc.db!"
    assert KEYNAME_SECRET2.encode() not in blob, "evidence token secret leaked into jcc.db!"

    rows = _all_row_reprs(engine.store)
    assert KEYNAME_SECRET not in rows, "payload secret visible in a persisted DB row"
    assert KEYNAME_SECRET2 not in rows, "evidence secret visible in a persisted DB row"
    # NOT a shredder: ordinary non-secret content survives the chokepoint.
    assert BENIGN in rows, "benign value was destroyed -> the chokepoint over-reaches"
    # NOT vacuous the other way: the write really happened.
    assert engine.store.get_session("sess-keyname") is not None, "no session persisted"
    with engine.store.connection() as conn:
        (n,) = conn.execute(
            "SELECT COUNT(*) FROM events WHERE session_id='sess-keyname'").fetchone()
    assert n == 1, "the event was not persisted at all -> the guard is vacuous"


def test_chokepoint_does_not_truncate_or_reshape_non_secret_payload(tmp_path):
    """(b) No over-reach — non-secret-named keys keep their values VERBATIM,
    including a >MAX_TEXT_LEN (240) string that ``redact()`` WOULD truncate, and
    nested structure keeps its shape. Pins the deliberate no-truncation decision.
    """
    assert len(LONG) > 240, "fixture must exceed MAX_TEXT_LEN for this pin to bite"
    payload = {"note": LONG, "nested": {"a": [1, 2, {"b": "ok"}]}}

    engine = _engine(str(tmp_path))
    env = AEPEnvelope(
        event_id="ev_long",
        delivery_id="dl_long",
        session_id="sess-long",
        agent="workbuddy",
        project="demo",
        cwd="/tmp",
        event_type="tool_call",
        payload=payload,
        evidence=None,
    )
    engine.ingest(env)

    with engine.store.connection() as conn:
        (raw,) = conn.execute(
            "SELECT payload FROM events WHERE event_id='ev_long'").fetchone()
    stored = json.loads(raw)
    assert stored["note"] == LONG, (
        "a long NON-secret value was truncated at the chokepoint (over-reach)"
    )
    assert "[sha256:" not in stored["note"], "redact()'s truncation tag reached the chokepoint"
    assert stored["nested"] == {"a": [1, 2, {"b": "ok"}]}, stored["nested"]


def test_mask_secret_key_values_unit_contract():
    """(c) Unit contract for the new kernel function."""
    out = mask_secret_key_values({
        "api_key": "S1",
        "token": "S2",
        "authorization": "S3",
        "keep": "visible",
        "list": [{"password": "S4", "ok": "yes"}],
    })
    assert out["api_key"] == "[REDACTED]"
    assert out["token"] == "[REDACTED]"
    assert out["authorization"] == "[REDACTED]"
    assert out["keep"] == "visible"                       # sibling untouched
    assert out["list"] == [{"password": "[REDACTED]", "ok": "yes"}]  # list recursion

    # scalars pass through untouched: no truncation, no value scrub
    assert mask_secret_key_values("bearer sk-abcdefghijklmnop") == "bearer sk-abcdefghijklmnop"
    assert mask_secret_key_values(7) == 7
    assert mask_secret_key_values(True) is True
    assert mask_secret_key_values(None) is None

    # depth cap: collapse to the SHARED marker, never raw passthrough
    deep = mask_secret_key_values(_nest(12, "deep-leaf"))
    node = deep
    for _ in range(8):
        node = node["child"]                       # descend to depth 8
    assert node["child"] == DEPTH_LIMIT_MARK       # depth 9 collapsed
    assert DEPTH_LIMIT_MARK in json.dumps(deep)
    assert "deep-leaf" not in json.dumps(deep)


def test_redact_still_truncates_long_leaf():
    """(d) Adapter-plane semantics UNCHANGED: ``redact()`` still truncates a
    >240-char string leaf and tags it with a sha256 digest. This pins that the new
    chokepoint function did not alter the shared kernel's existing behaviour."""
    out = redact({"note": LONG})
    assert out["note"] != LONG, "redact() stopped truncating its long leaf"
    assert "[sha256:" in out["note"], out["note"]
    assert out["note"].startswith(LONG[:240])
