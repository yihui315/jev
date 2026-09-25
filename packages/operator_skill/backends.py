"""Transport backends for the Operator Skill (S-01).

The skill never opens a socket on its own; it asks a :class:`Backend` to carry a
request to the REAL control plane. Three implementations:

    * ``RecordingBackend`` -- a deterministic, network-free double for the test
      suite. It makes NO network calls; it returns queued responses and records
      every call, so a test can assert exactly what the skill asked for (method,
      path, body, token) and that nothing unexpected was sent. This is the only
      backend the pinned test suite uses; it is the test seam, not a fake of the
      API -- the skill builds the *real* request shape and hands it to the
      backend, so the request-building path is exercised exactly.
    * ``ASGIBackend`` -- drives the actual ``create_app(engine, config)`` ASGI app
      IN PROCESS through its ASGI interface (no TCP, no bind). It is how the
      skill talks to the real engine without standing up a socket, and is the
      production path next to the engine.
    * ``HttpBackend`` -- hits a real loopback server over HTTP using the stdlib
      ``urllib`` only (zero new dependencies). It is hard-pinned to 127.0.0.1.

All three speak the SAME request shape the skill uses::

    backend.request(method, path, json_body=None, token=None)

and return a :class:`BackendResult` carrying ``status`` / ``payload`` / ``error``.
The skill maps those onto structured refusals; the backend never refuses, it only
reports what the wire said.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

DEFAULT_BASE_URL = "http://127.0.0.1:4778"
CONTROL_TOKEN_HEADER = "X-JCC-Token"


@dataclass
class BackendResult:
    """What a backend observed on the wire. Never a refusal by itself."""

    status: Optional[int]
    payload: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    elapsed: Optional[float] = None

    @property
    def ok(self) -> bool:
        return self.error is None and isinstance(self.status, int) and self.status < 400

    def as_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "payload": self.payload,
            "error": self.error,
            "ok": self.ok,
            "elapsed": self.elapsed,
        }


class Backend:
    """Abstract transport. Subclasses implement :meth:`request`."""

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        token: Optional[str] = None,
    ) -> BackendResult:
        raise NotImplementedError


def _headers(token: Optional[str]) -> Dict[str, str]:
    hdrs = {"Accept": "application/json"}
    if token:
        hdrs[CONTROL_TOKEN_HEADER] = token
    return hdrs


class RecordingBackend(Backend):
    """A deterministic, network-free double for the test suite.

    Queued responses are returned in order; when the queue is empty the
    ``default`` response is returned (200/empty by default) unless the backend
    was told to raise. Every call is appended to ``calls`` so tests can assert
    the skill issued exactly the request it should have -- and nothing more.
    """

    def __init__(
        self,
        responses: Optional[List[BackendResult]] = None,
        *,
        default: Optional[BackendResult] = None,
        raise_on_empty: bool = False,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._queue: List[BackendResult] = list(responses or [])
        self.default = default or BackendResult(status=200, payload={})
        self.raise_on_empty = raise_on_empty
        self.calls: List[Dict[str, Any]] = []
        self.clock = clock

    def enqueue(self, result: BackendResult) -> "RecordingBackend":
        self._queue.append(result)
        return self

    def set_default(self, result: BackendResult) -> "RecordingBackend":
        self.default = result
        return self

    def clear(self) -> None:
        self._queue.clear()
        self.calls.clear()

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        token: Optional[str] = None,
    ) -> BackendResult:
        call = {
            "method": method,
            "path": path,
            "json_body": json_body,
            "token": token,
            "headers": _headers(token),
            "ts": self.clock(),
        }
        self.calls.append(call)
        if self._queue:
            return self._queue.pop(0)
        if self.raise_on_empty:
            raise AssertionError(
                f"RecordingBackend received an unexpected request: {method} {path}"
            )
        return self.default

    def last_call(self) -> Optional[Dict[str, Any]]:
        return self.calls[-1] if self.calls else None

    def call_count(self) -> int:
        return len(self.calls)

    def calls_for(
        self,
        method: Optional[str] = None,
        path: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        out = self.calls
        if method is not None:
            out = [c for c in out if c["method"] == method]
        if path is not None:
            out = [c for c in out if c["path"] == path]
        return out


class ASGIBackend(Backend):
    """Drives the real ASGI app in process via its ASGI interface.

    No TCP socket is opened and nothing is bound: the app is exercised through
    ``httpx``'s ASGI transport against the real ``create_app`` instance, so the
    responses are the control plane's own. Used by the integration smoke test and
    by any deployment that wants the skill next to the engine. The ``httpx``
    import is lazy so this module imports even where ``httpx`` is absent.
    """

    def __init__(self, app: Any, base_url: str = DEFAULT_BASE_URL) -> None:
        self.app = app
        self.base_url = base_url
        self._client = self._make_client(app, base_url)

    @staticmethod
    def _make_client(app: Any, base_url: str):
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - env dependent
            raise RuntimeError(
                "ASGIBackend needs httpx (a transitive dependency of starlette); "
                "is the engine's test client installed?"
            ) from exc
        return httpx.Client(
            transport=httpx.ASGITransport(app=app), base_url=base_url, timeout=10.0
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        token: Optional[str] = None,
    ) -> BackendResult:
        headers = _headers(token)
        body = json.dumps(json_body) if json_body is not None else None
        if body is not None:
            headers["Content-Type"] = "application/json"
        started = time.time()
        try:
            resp = self._client.request(method, path, content=body, headers=headers)
            elapsed = time.time() - started
            try:
                payload: Optional[Dict[str, Any]] = resp.json()
            except ValueError:
                payload = None
            return BackendResult(
                status=resp.status_code, payload=payload, elapsed=elapsed
            )
        except Exception as exc:  # noqa: BLE001 - transport failure, not a crash
            return BackendResult(
                status=None,
                error=f"{type(exc).__name__}: {exc}",
                elapsed=time.time() - started,
            )


class HttpBackend(Backend):
    """Talks to a real loopback server with the stdlib ``urllib`` only.

    ``base_url`` is HARD-PINNED to 127.0.0.1 / localhost: it never reaches the
    wider network (ADR-0002/0004/0005). No third-party HTTP library required.
    """

    def __init__(self, base_url: str = DEFAULT_BASE_URL) -> None:
        self.base_url = self._require_loopback(base_url)

    @staticmethod
    def _require_loopback(base_url: str) -> str:
        from urllib.parse import urlparse

        host = urlparse(base_url).hostname or ""
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError(
                f"HttpBackend is loopback-only; refused to target {host!r}. "
                "Use 127.0.0.1 or localhost."
            )
        return base_url.rstrip("/")

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        token: Optional[str] = None,
    ) -> BackendResult:
        url = f"{self.base_url}{path}"
        data = json.dumps(json_body).encode("utf-8") if json_body is not None else None
        headers = _headers(token)
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        started = time.time()
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310 - loopback only
                raw = resp.read().decode("utf-8")
                status = resp.status
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace") if exc.fp else ""
            status = exc.code
        except Exception as exc:  # noqa: BLE001 - transport failure
            return BackendResult(
                status=None,
                error=f"{type(exc).__name__}: {exc}",
                elapsed=time.time() - started,
            )
        elapsed = time.time() - started
        try:
            payload = json.loads(raw) if raw else None
        except ValueError:
            payload = None
        return BackendResult(status=status, payload=payload, elapsed=elapsed)
