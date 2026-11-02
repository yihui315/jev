"""JCC Hermes hook handler (official Hermes hook mechanism).

Installed to ``~/.hermes/hooks/jcc-telemetry/handler.py``. Hermes calls
``handle(event_type, context)`` for every subscribed lifecycle event.

Design (P2):
  * telemetry ONLY - this handler observes and forwards; it never returns a
    decision and never mutates agent behaviour (P0-P2: no actuation);
  * fully best-effort - any error is swallowed so a JCC outage can never break
    the Hermes agent (Hermes also isolates handler errors itself);
  * identity = ``session_id`` (falls back to ``session_key``); events without an
    identity are dropped by the adapter, never cwd-keyed;
  * transport = loopback token POST (default) or HMAC-signed webhook (opt-in).

Config via environment (read at handler import):
  JCC_ROOT                 repo root; default = the value install.ps1 substituted
  JCC_MODE                 "loopback" (default) | "signed"
  JCC_CONTROL_TOKEN        needed for loopback
  JCC_CORE_URL             default http://127.0.0.1:4778
  JCC_HOOKS_URL            needed for signed (e.g. http://127.0.0.1:4778/api/hooks)
  JCC_WEBHOOK_SECRET       needed for signed
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

# This handler is COPIED to ~/.hermes/hooks/jcc-telemetry/handler.py, i.e.
# OUTSIDE the checkout, so a repo root cannot be derived from __file__ here the
# way jcc_core/config.py and jcc_emit.py derive theirs. install.ps1 substitutes
# the __JCC_ROOT__ token with the -JccRoot it resolved; that substituted value is
# the fallback. An unsubstituted token means the handler was installed by some
# path that did not render it, and it degrades to "telemetry inert" rather than
# to a guess at a drive letter.
_JCC_ROOT = os.environ.get("JCC_ROOT") or "__JCC_ROOT__"
for _p in (
    os.path.join(_JCC_ROOT, "packages"),
    os.path.join(_JCC_ROOT, "integrations", "_common"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from jcc_emit import emit_event  # noqa: E402
    from jcc_receiver import SecureWebhookReceiver  # noqa: E402
except Exception:  # noqa: BLE001  (never break Hermes on import failure)
    emit_event = None
    SecureWebhookReceiver = None


def _signed_post(payload: dict) -> None:
    secret = os.environ.get("JCC_WEBHOOK_SECRET")
    url = os.environ.get("JCC_HOOKS_URL")
    if not secret or not url or SecureWebhookReceiver is None:
        return
    body = json.dumps(payload).encode("utf-8")
    rcv = SecureWebhookReceiver(secret)
    headers = rcv.sender_headers("hermes", body)
    req = urllib.request.Request(url, data=body, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=3) as resp:  # noqa: S310 (loopback)
        resp.read()


def handle(event_type: str, context: dict) -> None:
    """Entry point required by Hermes' HookRegistry."""
    try:
        payload = {
            "event": event_type,
            "context": dict(context or {}),
            "_delivery_id": f"hermes-{int(time.time() * 1000)}-{os.getpid()}",
        }
        mode = os.environ.get("JCC_MODE", "loopback")
        if mode == "signed":
            _signed_post(payload)
            return
        if emit_event is None:
            return
        emit_event("hermes", payload)
    except Exception:  # noqa: BLE001  (telemetry must never break the agent)
        return
