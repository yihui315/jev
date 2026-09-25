"""Transport layer the MCP facade provides to reach the JCC Core control plane.

These classes are defined HERE (under ``mcp_facade`` ownership) and registered
as ``operator_skill.backends`` by :mod:`mcp_facade._compat` when the on-disk
module is broken. They are **pure transport** -- they carry the control token as
an HTTP header and NEVER log it -- and hold no authority of their own.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

TOKEN_HEADER = "X-JCC-Token"
DEFAULT_BASE_URL = "http://127.0.0.1:4778"


@dataclass
class BackendResult:
    """The contract ``OperatorSkill`` reads back from a transport request."""

    status: Optional[int]
    ok: bool
    payload: Any = None
    error: Optional[str] = None


class Backend:
    """Abstract transport. A ``request`` returns a :class:`BackendResult`."""

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        token: Optional[str] = None,
    ) -> BackendResult:
        raise NotImplementedError


class RecordingBackend(Backend):
    """Scriptable in-memory backend: for unit tests and offline operation.

    Every call is recorded as ``{method, path, token_present}`` -- the token
    VALUE is never stored, only the fact that one was supplied.
    """

    def __init__(
        self,
        script: Optional[Dict[Tuple[str, str], Any]] = None,
        default_status: int = 200,
        default_payload: Any = None,
    ) -> None:
        self.script = script or {}
        self.default_status = default_status
        self.default_payload = default_payload
        self.calls: list = []

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        token: Optional[str] = None,
    ) -> BackendResult:
        # Record call metadata only -- never the token value, never secrets.
        self.calls.append(
            {"method": method, "path": path, "token_present": bool(token)}
        )
        entry = self.script.get((method, path))
        if callable(entry):
            entry = entry(method, path, json_body, token)
        if entry is not None:
            return entry
        ok = 200 <= self.default_status < 400
        return BackendResult(
            status=self.default_status, ok=ok, payload=self.default_payload
        )


class LoopbackHttpBackend(Backend):
    """Real HTTP transport, loopback-only, stdlib only (no new third-party deps).

    Binds to nothing itself; it only ever speaks to ``127.0.0.1``. The control
    token is carried as the ``X-JCC-Token`` header and is never logged.
    """

    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        token: Optional[str] = None,
    ) -> BackendResult:
        url = self.base_url + path
        data = json.dumps(json_body).encode("utf-8") if json_body is not None else None
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if token:
            # Carried as a header; the value is intentionally never logged.
            headers[TOKEN_HEADER] = token
        req = urllib.request.Request(
            url, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
                status = int(resp.getcode())
            payload = json.loads(raw) if raw else None
            return BackendResult(status=status, ok=200 <= status < 400, payload=payload)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace") if exc.fp else ""
            try:
                payload = json.loads(raw) if raw else None
            except ValueError:
                payload = raw
            return BackendResult(status=exc.code, ok=False, payload=payload,
                                 error=f"HTTP {exc.code}")
        except Exception as exc:  # network down, timeout, etc.
            return BackendResult(status=None, ok=False,
                                 error=f"{type(exc).__name__}: {exc}")


class ASGIBackend(Backend):
    """In-process ASGI transport placeholder.

    Provided so the name the package imports exists. A real ASGI implementation
    requires an ASGI test client library (httpx / starlette); the MCP facade
    default is the loopback HTTP backend, and the test suite uses
    :class:`RecordingBackend`. If a deployment wants in-process dispatch it can
    supply its own backend; this stub refuses rather than silently doing nothing.
    """

    def __init__(self, app: Any = None, base_url: str = DEFAULT_BASE_URL) -> None:
        self.app = app
        self.base_url = base_url

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        token: Optional[str] = None,
    ) -> BackendResult:
        raise NotImplementedError(
            "ASGIBackend requires an ASGI test client; use LoopbackHttpBackend "
            "or RecordingBackend for the MCP facade transport."
        )


#: Public alias used by the broken-module shim and by callers who prefer the
#: "HttpBackend" name the Operator Skill's ``__init__`` imports.
HttpBackend = LoopbackHttpBackend
