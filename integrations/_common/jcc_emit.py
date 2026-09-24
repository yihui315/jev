"""JCC agent-event emitter (shared by all four installers).

Reads a NATIVE agent event (JSON) from stdin or argv, normalizes it through the
matching adapter into an AEP v0.1 envelope, and delivers it to the JCC Core
control plane on 127.0.0.1. It is deliberately *best-effort*: a broken agent
hook must never block or crash the agent, so any delivery failure is spooled to
disk and the process still exits 0.

Delivery paths (in order):
  1. in-process (``emit_event(..., engine=...)``)  -- used by the Hermes hook.
  2. HTTP  POST http://127.0.0.1:4778/api/assess  with ``X-JCC-Token``
     (loopback only; token-gated; read-only-safe).
  3. spool  ``~/.jcc/spool/<agent>.jsonl``  (audit/redelivery; NEVER ingested
     when identity is unresolvable).

CLI:
  python jcc_emit.py --agent workbuddy [--stdin-json FILE] [--dry-run]
Env:
  JCC_CONTROL_TOKEN   control token for /api/assess (required for HTTP)
  JCC_CORE_URL        default http://127.0.0.1:4778
  JCC_SPOOL_DIR        default ~/.jcc/spool
  JCC_ROOT             repo root (default F:/jev)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_ROOT = os.environ.get("JCC_ROOT", "F:/jev")
_PACKAGES = str(Path(DEFAULT_ROOT) / "packages")
if _PACKAGES not in sys.path:
    sys.path.insert(0, _PACKAGES)

from jcc_core.adapters.discovery import adapter_for  # noqa: E402
from jcc_core.adapters._common import UnidentifiableEvent  # noqa: E402
from jcc_core.redaction import scrub_value_secrets  # noqa: E402


def _spool_dir() -> Path:
    d = Path(os.environ.get("JCC_SPOOL_DIR", str(Path(os.path.expanduser("~")) / ".jcc" / "spool")))
    d.mkdir(parents=True, exist_ok=True)
    return d


def spool(agent: str, record: Dict[str, Any]) -> Path:
    # The spool is a PERSISTENCE BOUNDARY too (P2 gate FINDING-D): a caller may
    # pass a raw / garbled agent payload as free text (e.g. codex
    # jcc-codex-notify.py writes the undecodable notify JSON verbatim). Scrub
    # every string leaf here, at the single chokepoint, so no caller can persist
    # a secret embedded in the text. scrub_value_secrets is deterministic,
    # idempotent and keys-preserving.
    safe = scrub_value_secrets(record)
    path = _spool_dir() / f"{agent}.jsonl"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"spooled_at": time.time(), **safe}, ensure_ascii=False) + "\n")
    return path


def normalize_event(agent: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize one native event to an AEP envelope dict (JSON mode)."""
    adapter = adapter_for(agent)
    if adapter is None:
        raise ValueError(f"unknown agent '{agent}'")
    import asyncio

    env = asyncio.run(adapter.normalize(raw))
    data = env.model_dump_api()
    data.pop("_provenance", None)
    # Value-pattern scrub (P2 gate C1) so the dry-run print, the HTTP body and
    # the on-failure spool are all clean even for secrets embedded in free-form
    # strings (core.ingest scrubs again before persistence — both boundaries).
    data["payload"] = scrub_value_secrets(data.get("payload") or {})
    if data.get("evidence") is not None:
        data["evidence"] = scrub_value_secrets(data["evidence"])
    return data


def _post(url: str, token: Optional[str], body: Dict[str, Any], timeout: float = 3.0) -> Dict[str, Any]:
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-JCC-Token", token)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (loopback)
        return json.loads(resp.read().decode("utf-8"))


def emit_event(
    agent: str,
    raw: Dict[str, Any],
    *,
    engine: Any = None,
    url: Optional[str] = None,
    token: Optional[str] = None,
    spool_on_failure: bool = True,
) -> Dict[str, Any]:
    """Normalize + deliver one native event. NEVER raises on delivery failure."""
    received_at = time.time()
    raw = dict(raw)
    raw.setdefault("_received_at", received_at)
    raw.pop("_provenance", None)

    try:
        envelope = normalize_event(agent, raw)
    except UnidentifiableEvent as exc:
        rec = {"kind": "unidentifiable", "agent": agent,
               "reason": str(exc), "raw_keys": sorted(raw.keys()),
               "received_at": received_at}
        if spool_on_failure:
            spool(agent, rec)
        return {"status": "dropped", "reason": "unidentifiable", "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001
        rec = {"kind": "normalize_error", "agent": agent, "reason": str(exc),
               "raw_keys": sorted(raw.keys()), "received_at": received_at}
        if spool_on_failure:
            spool(agent, rec)
        return {"status": "dropped", "reason": "normalize_error", "detail": str(exc)}

    # 1) in-process
    if engine is not None:
        try:
            from jcc_core.schemas import AEPEnvelope

            out = engine.ingest(AEPEnvelope(**envelope))
            return {"status": "ingested", "via": "engine", "session_id": envelope["session_id"], "result": out}
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "reason": "engine_ingest", "detail": str(exc)}

    # 2) HTTP loopback
    target = url or os.environ.get("JCC_CORE_URL", "http://127.0.0.1:4778") + "/api/assess"
    tok = token if token is not None else os.environ.get("JCC_CONTROL_TOKEN")
    try:
        out = _post(target, tok, envelope)
        return {"status": "sent", "via": "http", "session_id": envelope["session_id"], "result": out}
    except Exception as exc:  # noqa: BLE001
        # 3) spool (event IS identified -> redeliverable)
        if spool_on_failure:
            spool(agent, {"kind": "identified", "agent": agent, "envelope": envelope,
                          "reason": str(exc), "received_at": received_at})
        return {"status": "spooled", "reason": str(exc), "session_id": envelope["session_id"]}


def _read_input(path: Optional[str]) -> Dict[str, Any]:
    if path:
        text = Path(path).read_text(encoding="utf-8")
    else:
        text = sys.stdin.read()
    text = text.strip()
    if not text:
        return {}
    return json.loads(text)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="JCC agent event emitter")
    ap.add_argument("--agent", required=True, choices=["workbuddy", "claude", "codex", "hermes"])
    ap.add_argument("--stdin-json", default=None, help="read the native event from this file instead of stdin")
    ap.add_argument("--dry-run", action="store_true", help="print the AEP envelope, do not deliver")
    ap.add_argument("--payload", default=None, help="inline JSON event (used by codex notify)")
    args = ap.parse_args(argv)

    try:
        if args.payload is not None:
            raw = json.loads(args.payload)
        else:
            raw = _read_input(args.stdin_json)
    except Exception as exc:  # noqa: BLE001
        spool(args.agent, {"kind": "input_error", "reason": str(exc)})
        return 0  # never fail the agent

    try:
        if args.dry_run:
            print(json.dumps(normalize_event(args.agent, dict(raw)), ensure_ascii=False, indent=2))
            return 0
        result = emit_event(args.agent, raw)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001
        spool(args.agent, {"kind": "emit_error", "reason": str(exc)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
