"""P2 integration smoke test: four agents -> JCC Core, no cross-talk.

Proves (per the P2 task acceptance criteria):
  * real distinction ``agent=workbuddy|claude|codex|hermes`` in the state store;
  * per-agent sessions do NOT cross-talk;
  * no duplicate state advance on redelivery (dedup on event_id + delivery_id);
  * no cwd identity collision (same cwd, different sessions stay distinct);
  * NO secret leakage (planted secret searched in the whole SQLite DB bytes);
  * the Hermes inbound receiver enforces HMAC + timestamp + delivery_id replay
    + size limit + session isolation.

Part A runs in-process against a real CoreEngine (isolated temp SQLite).
Part B unit-tests the hardened receiver.
Part C runs the REAL JCC server over HTTP on an OS-assigned EPHEMERAL port and
emits one event per agent.

Windows teardown note: on Windows the child process's handle on ``server.log``
(and SQLite's handle on ``jcc.db``) can outlive ``proc.wait()`` by a few ms, so
``TemporaryDirectory`` cleanup would intermittently raise ``WinError 32``. Every
temp dir below therefore uses ``ignore_cleanup_errors=True`` and the HTTP leg
explicitly retries unlinking the log after the child is dead.

Run: F:/jev/.venv/Scripts/python -m pytest jcc_core/tests/test_p2_integration_smoke.py -q
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]  # F:/jev
PACKAGES = REPO / "packages"
_INTEG_COMMON = REPO / "integrations" / "_common"
for p in (str(PACKAGES), str(_INTEG_COMMON)):
    if p not in sys.path:
        sys.path.insert(0, p)

from jcc_core.actuator import Actuator
from jcc_core.config import Config, load_config
from jcc_core.core import CoreEngine
from jcc_core.providers import build_provider
from jcc_core.state_store import StateStore
from jcc_core.adapters import ClaudeAdapter, CodexAdapter, HermesAdapter, WorkBuddyAdapter

from jcc_emit import emit_event  # noqa: E402
from jcc_receiver import (  # noqa: E402
    HDR_AGENT,
    HDR_DELIVERY,
    HDR_SIGNATURE,
    HDR_TIMESTAMP,
    ReceiverRejection,
    SecureWebhookReceiver,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PLANTED_SECRET = "sk-live-PLANTEDSECRETdeadbeef0123456789"
#: a secret that is NOT under a secret KEY -- it sits inside a free-form value
#: (aegis-2's P2 security-gate case). Must be masked by the value-pattern pass.
VALUE_PLANTED_SECRET = "sk-live-VALUEEMBEDDED0011223344556677889900"


def _tmpdir(prefix: str) -> "tempfile.TemporaryDirectory[str]":
    """Temp dir whose cleanup never flakes on Windows handle-release races.

    The child's stdout handle (and SQLite's file handle) may still be held for a
    few ms after ``proc.wait()``, which makes ``rmtree`` raise ``WinError 32``.
    Cleanup errors are ignored: the assertion content has already passed and a
    leaked OS temp dir is harmless (and self-cleaned by the OS temp policy).
    """
    return tempfile.TemporaryDirectory(prefix=prefix, ignore_cleanup_errors=True)


def _free_port() -> int:
    """Reserve an OS-assigned free TCP port on loopback (avoids machine-wide
    contention from a hardcoded port). Small TOCTOU window is acceptable."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def _engine(tmp: str) -> CoreEngine:
    cfg = load_config(os.environ.get("JCC_CONFIG"))
    store = StateStore(os.path.join(tmp, "jcc.db"))
    provider = build_provider(cfg)
    actuator = Actuator(enabled=False, allowed_actions=[])
    return CoreEngine(store, provider, actuator, cfg)


def _raw(agent: str, name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _norm(adapter, raw: dict):
    return asyncio.run(adapter.normalize(raw))


# --------------------------------------------------------------------------- #
# Part A — in-process engine ingest, four agents
# --------------------------------------------------------------------------- #
# one identified fixture per agent, with a shared cwd to force a collision test
PER_AGENT_FIXTURE = {
    "workbuddy": ("workbuddy_hook_session_start.json", WorkBuddyAdapter),
    "claude": ("claude_transcript_record.json", ClaudeAdapter),
    "codex": ("codex_rollout_session_meta.json", CodexAdapter),
    "hermes": ("hermes_hook_session_start.json", HermesAdapter),
}
SHARED_CWD = r"C:\some\checkout"  # deliberately identical across agents


def _force_cwd(agent: str, raw: dict) -> dict:
    """Inject the shared cwd at the location each native schema stores it."""
    if agent == "hermes":
        raw.setdefault("context", {})["cwd"] = SHARED_CWD
    elif agent == "codex":
        raw.setdefault("payload", {})["cwd"] = SHARED_CWD
    else:
        raw["cwd"] = SHARED_CWD
    return raw


def test_four_agents_distinct_and_no_crosstalk_and_no_duplicate_advance():
    with _tmpdir("jcc_p2a_") as tmp:
        engine = _engine(tmp)
        seen_sessions = {}
        for agent, (fname, cls) in PER_AGENT_FIXTURE.items():
            raw = _force_cwd(agent, _raw(agent, fname))  # force same-cwd collision pressure
            env = _norm(cls(), raw)
            out = engine.ingest(env)
            assert out.get("deduped") is not True, f"{agent} first ingest should not dedup"
            seen_sessions[agent] = env.session_id

            # redelivery of the exact same event must NOT advance state again
            s_before = engine.store.get_session(env.session_id)
            out2 = engine.ingest(env)
            assert out2.get("deduped") is True, f"{agent} redelivery must dedup"
            s_after = engine.store.get_session(env.session_id)
            assert s_after.event_count == s_before.event_count, f"{agent} duplicate state advance!"

        # distinctness: 4 agents -> 4 distinct session ids, all present
        assert len(set(seen_sessions.values())) == 4, seen_sessions
        rows = {r["session_id"]: r for r in engine.store.list_sessions()}
        for agent, sid in seen_sessions.items():
            assert sid in rows, f"missing session for {agent}"
            assert rows[sid]["agent"] == agent, f"cross-talk: {sid} agent={rows[sid]['agent']} != {agent}"

        # no cwd identity collision: 4 rows share cwd but are 4 sessions
        shared = [r for r in rows.values() if r.get("cwd") == SHARED_CWD]
        assert len(shared) == 4, f"expected 4 same-cwd sessions, got {len(shared)}"
        assert len({r["session_id"] for r in shared}) == 4

        # audit tape intact
        ok, n = engine.store.verify_audit()
        assert ok is True and n >= 4


def test_no_secret_leakage_in_state_store_bytes():
    """Plant a secret in a PreToolUse event; assert it never reaches disk."""
    with _tmpdir("jcc_p2secret_") as tmp:
        engine = _engine(tmp)
        raw = _raw("claude", "claude_hook_pre_tool_use.json")
        raw["tool_input"] = {"command": "curl", "api_key": PLANTED_SECRET, "password": "hunter2-" + PLANTED_SECRET}
        raw["authorization"] = "Bearer " + PLANTED_SECRET
        env = _norm(ClaudeAdapter(), raw)
        engine.ingest(env)

        blob = b""
        for suffix in ("", "-wal", "-shm"):
            f = Path(tmp) / ("jcc.db" + suffix)
            if f.exists():
                blob += f.read_bytes()
        assert PLANTED_SECRET.encode() not in blob, "PLANTED SECRET LEAKED to the state store!"
        assert b"hunter2" not in blob, "planted password leaked"
        # sanity: the store did receive the event
        assert engine.store.get_session(env.session_id) is not None


# --------------------------------------------------------------------------- #
# Part B — hardened receiver controls
# --------------------------------------------------------------------------- #
def _signed_headers(rcv: SecureWebhookReceiver, agent: str, body: bytes, **kw):
    return rcv.sender_headers(agent, body, **kw)


def test_receiver_accepts_valid_and_ingests():
    with _tmpdir("jcc_p2b_") as tmp:
        engine = _engine(tmp)
        rcv = SecureWebhookReceiver("unit-secret")
        raw = _raw("hermes", "hermes_hook_agent_step.json")
        body = json.dumps(raw).encode()
        headers = _signed_headers(rcv, "hermes", body)
        res = rcv.handle(headers=headers, body=body, engine=engine)
        assert res["accepted"] is True, res
        assert engine.store.get_session(raw["context"]["session_id"]) is not None


def test_receiver_rejects_bad_signature():
    rcv = SecureWebhookReceiver("unit-secret")
    body = b'{"event":"agent:step","context":{"session_id":"s1"}}'
    headers = _signed_headers(rcv, "hermes", body)
    headers[HDR_SIGNATURE] = "sha256=" + "0" * 64
    res = rcv.handle(headers=headers, body=body)
    assert res == {"accepted": False, "reason": "bad_signature"}, res


def test_receiver_rejects_stale_timestamp():
    rcv = SecureWebhookReceiver("unit-secret", max_skew=60)
    body = b'{"event":"agent:step","context":{"session_id":"s1"}}'
    headers = rcv.sender_headers("hermes", body, timestamp=time.time() - 1000)
    res = rcv.handle(headers=headers, body=body)
    assert res["reason"] == "timestamp_out_of_window", res


def test_receiver_rejects_replay():
    with _tmpdir("jcc_p2replay_") as tmp:
        engine = _engine(tmp)
        rcv = SecureWebhookReceiver("unit-secret")
        body = b'{"event":"agent:step","context":{"session_id":"s-replay","iteration":1}}'
        headers = rcv.sender_headers("hermes", body, delivery_id="fixed-1")
        assert rcv.handle(headers=headers, body=body, engine=engine)["accepted"] is True
        second = rcv.handle(headers=headers, body=body, engine=engine)
        assert second == {"accepted": False, "reason": "replay"}, second


def test_receiver_rejects_oversized_body():
    rcv = SecureWebhookReceiver("unit-secret", max_body=64)
    body = b'{"event":"agent:step","context":{"session_id":"' + b"x" * 200 + b'"}}'
    headers = _signed_headers(rcv, "hermes", body)
    res = rcv.handle(headers=headers, body=body)
    assert res["reason"] == "payload_too_large", res


def test_receiver_rejects_missing_headers():
    rcv = SecureWebhookReceiver("unit-secret")
    body = b"{}"
    res = rcv.handle(headers={}, body=body)
    assert res["reason"] == "missing_header", res


def test_receiver_enforces_session_isolation():
    with _tmpdir("jcc_p2iso_") as tmp:
        engine = _engine(tmp)
        rcv = SecureWebhookReceiver("unit-secret")
        # bind session to workbuddy
        b1 = json.dumps({"hook_event_name": "SessionStart", "session_id": "SHARED-S", "cwd": SHARED_CWD}).encode()
        assert rcv.handle(headers=_signed_headers(rcv, "workbuddy", b1), body=b1, engine=engine)["accepted"] is True
        # a claude source must NOT be able to reuse the same session id
        b2 = json.dumps({"event": "session:start", "context": {"session_id": "SHARED-S"}}).encode()
        res = rcv.handle(headers=_signed_headers(rcv, "claude", b2), body=b2, engine=engine)
        assert res == {"accepted": False, "reason": "session_isolation"}, res


def test_receiver_rejects_unidentifiable_event():
    rcv = SecureWebhookReceiver("unit-secret")
    body = json.dumps({"event": "unknown", "context": {"platform": "x"}}).encode()
    res = rcv.handle(headers=_signed_headers(rcv, "hermes", body), body=body)
    assert res["accepted"] is False and res["reason"] == "unidentifiable", res


# --------------------------------------------------------------------------- #
# Part C — real HTTP server end-to-end (ephemeral port)
# --------------------------------------------------------------------------- #
TOKEN = "p2-token-" + uuid.uuid4().hex[:8]


def _write_cfg(tmp: str, db: str, port: int) -> str:
    cfg = f"""
server:
  host: 127.0.0.1
  port: {port}
  cors_origins: [http://127.0.0.1:5173]
control:
  token_env: JCC_CONTROL_TOKEN
state_store:
  db_path: {db}
jev:
  provider: mock
  model: jev-1.13.0
  feature_flags: {{real_provider: false}}
actuation:
  enabled: false
  allowed_actions: []
policy:
  version: p1.0.0
  priority: [ESCALATE, PAUSE, VERIFY, STEER, RETRY, FINISH, CONTINUE]
agents:
  capabilities:
    workbuddy: {{aep: true, verified: true}}
"""
    p = os.path.join(tmp, "jcc.yaml")
    Path(p).write_text(cfg, encoding="utf-8")
    return p


def _wait_health(base: str, timeout: float = 30.0) -> bool:
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/api/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    return False


def _stop_server(proc: subprocess.Popen, logf, tmp: str) -> None:
    """Kill the child and release its log handle BEFORE temp-dir cleanup.

    On Windows ``TerminateProcess`` returns before the OS reclaims the child's
    inherited stdout handle, so an immediate rmtree can hit ``WinError 32``.
    We wait, close our own handle, then best-effort unlink ``server.log`` with a
    short retry/backoff (the retry is what actually removes the intermittent
    failure).
    """
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass
    try:
        logf.close()
    except Exception:  # noqa: BLE001
        pass
    log_path = Path(tmp) / "server.log"
    for _ in range(20):  # ~1s total; releases within a few ms in practice
        try:
            log_path.unlink(missing_ok=True)
            break
        except PermissionError:
            time.sleep(0.05)
        except Exception:  # noqa: BLE001
            break


@pytest.mark.skipif(os.environ.get("JCC_SKIP_HTTP") == "1", reason="HTTP leg disabled")
def test_http_end_to_end_four_agents_distinct_no_secret_leak():
    import urllib.request

    venv_py = str(REPO / ".venv" / "Scripts" / "python.exe")
    if not Path(venv_py).exists():
        pytest.skip("venv python unavailable")

    port = _free_port()
    with _tmpdir("jcc_p2c_") as tmp:
        db = os.path.join(tmp, "jcc.db")
        cfg = _write_cfg(tmp, db, port)
        env = dict(os.environ)
        env.update(JCC_CONFIG=cfg, JCC_CONTROL_TOKEN=TOKEN, JCC_PROVIDER="mock")
        logf = open(os.path.join(tmp, "server.log"), "w", encoding="utf-8")
        proc = subprocess.Popen(
            [venv_py, "-m", "jcc_core.run"], cwd=str(PACKAGES), env=env,
            stdout=logf, stderr=subprocess.STDOUT,
        )
        base = f"http://127.0.0.1:{port}"
        try:
            assert _wait_health(base), "server did not become healthy"
            # emit one identified event per agent over HTTP
            for agent, (fname, cls) in PER_AGENT_FIXTURE.items():
                raw = _force_cwd(agent, _raw(agent, fname))
                res = emit_event(agent, raw, url=base + "/api/assess", token=TOKEN)
                assert res["status"] == "sent", f"{agent}: {res}"

            # plant secrets through a 5th (claude) event and confirm they are absent.
            # Two shapes: a named secret KEY, and a secret embedded in a free-form
            # VALUE (aegis-2's P2 security-gate case).
            sraw = _raw("claude", "claude_hook_pre_tool_use.json")
            sraw["tool_input"] = {
                "api_key": PLANTED_SECRET,
                "command": "curl -H 'Authorization: Bearer " + VALUE_PLANTED_SECRET
                           + "' https://api.example.com/v1",
            }
            sraw["session_id"] = "http-secret-session"
            emit_event("claude", sraw, url=base + "/api/assess", token=TOKEN)

            with urllib.request.urlopen(base + "/api/sessions", timeout=5) as r:
                sessions = json.loads(r.read())
            agents = {s["agent"] for s in sessions}
            assert {"workbuddy", "claude", "codex", "hermes"} <= agents, agents

            with urllib.request.urlopen(base + "/api/audit?limit=500", timeout=5) as r:
                # Explicit codec. The audit endpoint serves JSON, and JSON is
                # UTF-8 by definition (RFC 8259 sec 8.1), so "utf-8" is not a
                # guess about this server -- it is the only correct reading.
                # See test_audit_response_decode_is_explicit_and_utf8 for why
                # the bare form is not left to chance.
                audit = r.read().decode("utf-8")
            assert PLANTED_SECRET not in audit, "secret leaked into the audit endpoint"
            assert VALUE_PLANTED_SECRET not in audit, "value-embedded secret leaked into the audit endpoint"
            assert "Bearer " + VALUE_PLANTED_SECRET not in audit

            # no cwd collision among the four agent sessions
            four_sids = set()
            for agent, (fname, cls) in PER_AGENT_FIXTURE.items():
                env2 = _norm(cls(), _force_cwd(agent, _raw(agent, fname)))
                four_sids.add(env2.session_id)
            four = [s for s in sessions if s["session_id"] in four_sids]
            assert len({s["session_id"] for s in four}) == 4, four

            with urllib.request.urlopen(base + "/api/audit/verify", timeout=5) as r:
                assert json.loads(r.read())["intact"] is True

            blob = b""
            for suffix in ("", "-wal", "-shm"):
                f = Path(db + suffix)
                if f.exists():
                    blob += f.read_bytes()
            assert PLANTED_SECRET.encode() not in blob, "secret leaked into the HTTP state store"
            assert VALUE_PLANTED_SECRET.encode() not in blob, \
                "value-embedded secret leaked into the HTTP state store"
        finally:
            _stop_server(proc, logf, tmp)


# --------------------------------------------------------------------------- #
# CR-H2: the /api/audit read must not decode on the default codec.
#
# WHAT THIS PINS. The HTTP leg above used to call `r.read().decode()` with no
# codec. Two independent facts make that a defect in a SECURITY test, which is
# what this file is:
#
#  1. The value being decoded is the audit endpoint -- the exact surface whose
#     whole job is to prove that planted secrets did NOT survive. A decode that
#     silently mis-decodes does not fail loudly; it produces mojibake, and
#     `PLANTED_SECRET not in audit` then passes on a string that is not what
#     the server actually sent. A redaction gate that can pass on corrupted
#     input is worse than no gate.
#  2. JSON is UTF-8 by definition (RFC 8259 sec 8.1), so `decode("utf-8")` is
#     not a guess about the server's configuration -- it is the only correct
#     reading of the bytes.
#
# HONEST SCOPE OF THE EVIDENCE (do not upgrade this claim). On THIS host the
# change is not observable: `bytes.decode()` with no argument defaults to
# UTF-8 (`bytes.decode.__text_signature__` is
# `($self, /, encoding='utf-8', errors='strict')`), NOT to the locale. That was
# measured, not assumed -- see the assertions below, which re-derive it in a
# child process. So this is a CORRECTNESS fix, not a demonstrated-behaviour
# fix, and the old line was not producing wrong answers here. It was one
# default away from doing so on any host where that default differs.
# --------------------------------------------------------------------------- #

_BARE_DECODE_RE = re.compile(r"\.read\(\)\s*\.\s*decode\s*\(([^)]*)\)")


def _code_lines_with_line_numbers(src: str):
    """Yield (lineno, text) for lines that contain CODE, not prose.

    The bare-call pattern this file guards against appears verbatim in this
    file's own CR-H2 note and in its failure message -- unavoidable, since the
    defect being described IS the bare call. Scanning raw source therefore
    matches the documentation and the test reports a false positive on itself.

    `tokenize` distinguishes the two without heuristics: a physical line is
    "code" if it carries at least one token that is not a string, comment or
    pure-layout marker.
    """
    import io
    import tokenize

    code_lines = set()
    with io.StringIO(src) as fh:
        for tok in tokenize.generate_tokens(fh.readline):
            if tok.type in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
                            tokenize.DEDENT, tokenize.COMMENT, tokenize.ENCODING,
                            tokenize.STRING, tokenize.ENDMARKER):
                continue
            for ln in range(tok.start[0], tok.end[0] + 1):
                code_lines.add(ln)

    lines = src.splitlines()
    for ln in sorted(code_lines):
        if 1 <= ln <= len(lines):
            yield ln, lines[ln - 1]


def test_audit_response_decode_is_explicit_and_utf8():
    """The /api/audit read must name its codec, and name it utf-8.

    Asserted on the SOURCE rather than only at runtime because the failure
    this guards is a source-level habit, and the runtime behaviour on the
    default-codec path is indistinguishable on this host (see the CR-H2 note
    above). A source assertion is what actually keeps the bare `.decode()`
    from creeping back into a test whose job is to detect secret leakage.

    Only executable lines are scanned. This file necessarily CONTAINS the bare
    call as text -- in the CR-H2 note and in the failure message below -- so a
    raw-text scan would flag its own documentation and always fail.
    """
    src = Path(__file__).resolve().read_text(encoding="utf-8")
    found = []
    for lineno, text in _code_lines_with_line_numbers(src):
        for m in _BARE_DECODE_RE.finditer(text):
            found.append((lineno, m.group(1).strip()))

    assert found, (
        "could not find any .read().decode(...) in this file's code -- if the "
        "/api/audit read was moved or refactored, re-pin this test against its "
        "new location")

    bare = [(ln, a) for ln, a in found if not a]
    assert not bare, (
        "bare .read().decode() with no codec at line(s) %s. Every decode of a "
        "server response in this file must name its codec explicitly: a "
        "default-codec decode of the audit endpoint can mis-decode instead of "
        "failing, and the redaction assertions would then pass on text the "
        "server never sent." % ([ln for ln, _ in bare],))
    for lineno, arg in found:
        assert "utf-8" in arg.lower(), (
            "line %d decodes with codec %r; JSON is UTF-8 by definition "
            "(RFC 8259 sec 8.1)" % (lineno, arg))


def test_bare_bytes_decode_defaults_to_utf8_not_locale():
    """Establish WHY the explicit codec above is not observable on this host.

    This documents the real default rather than repeating the assumption in a
    comment. `bytes.decode()` is documented and implemented as UTF-8-default;
    it does NOT consult the locale, so `PYTHONUTF8=1` on this host is not what
    makes the bare and explicit forms agree -- the language default is.

    The child runs with `PYTHONUTF8=0` so that the locale genuinely disagrees
    (this host's ANSI codepage is cp936), which is the configuration where a
    locale-derived default WOULD have changed the answer. It does not, and
    that is the finding: the bare form was never reading cp936 here.

    If a future interpreter DID make the bare form locale-derived, this test
    fails loudly and the comment above is corrected by the same run -- rather
    than a reviewer trusting a stale justification.
    """
    probe = (
        "import sys;"
        "b=''.join(chr(c) for c in (0x4E2D, 0x6587)).encode('utf-8');"
        "print(sys.flags.utf8_mode);"
        "print(b.decode()==b.decode('utf-8'))"
    )
    env = {k: v for k, v in os.environ.items() if k != "PYTHONUTF8"}
    env["PYTHONUTF8"] = "0"
    proc = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env, timeout=120,
    )
    assert proc.returncode == 0, (
        "probe child failed under PYTHONUTF8=0 (rc=%d): %s"
        % (proc.returncode, (proc.stderr or "")[:500]))
    lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
    assert len(lines) >= 2, "probe child printed nothing usable: %r" % (proc.stdout,)
    assert lines[0] == "0", (
        "the PYTHONUTF8=0 child did not actually disable UTF-8 mode "
        "(sys.flags.utf8_mode=%r) -- this test would be proving nothing about "
        "the non-UTF-8 host" % (lines[0],))
    assert lines[1] == "True", (
        "under PYTHONUTF8=0 the bare .decode() no longer matches utf-8. The "
        "CR-H2 note in this file claims bytes.decode defaults to utf-8 "
        "independently of the locale; that claim is now FALSE and must be "
        "corrected before the explicit codec is justified by locale drift.")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
