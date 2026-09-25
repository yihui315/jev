"""Stdlib-only HTTP client for the real JCC control-plane API.

``urllib`` and nothing else: the UI must not add a dependency to a project whose
transport is deliberately boring, and it must not be able to reach anything the
loopback-only rule does not already bound.

Security properties this client exists to preserve:

* **It never holds, sends or renders the control token.** The UI is a read-only
  surface; every mutating endpoint stays uncalled. The ``Security`` screen
  demonstrates the gate by probing a mutating endpoint the way an anonymous
  caller would, which is the only probe that needs no secret.
* A failed transport is an exception, never an empty page: a screen that cannot
  reach the API says ``API UNREACHABLE`` and names the URL.

HTTP status codes are returned, not raised, because the ``Security`` screen and
the smoke test both need the actual status of a refusal.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

#: Default location of the control plane. Overridable so an operator can point
#: the UI at a UI-pinned port; it is loopback in both cases.
DEFAULT_API_BASE = os.environ.get("JCC_API_BASE", "http://127.0.0.1:4778")

DEFAULT_TIMEOUT = 10.0


class ApiUnreachable(RuntimeError):
    """The control plane could not be contacted at all."""

    def __init__(self, base_url: str, reason: str):
        super().__init__(f"JCC control plane unreachable at {base_url}: {reason}")
        self.base_url = base_url
        self.reason = reason


@dataclass(frozen=True)
class HttpResponse:
    """One response, whatever it turned out to be."""

    method: str
    url: str
    status: int
    text: str
    json: Any = field(default=None, compare=False)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class ApiClient:
    """Minimal read-mostly client for the JCC control-plane API."""

    def __init__(self, base_url: str = DEFAULT_API_BASE, *, timeout: float = DEFAULT_TIMEOUT):
        self.base_url = (base_url or DEFAULT_API_BASE).rstrip("/")
        self.timeout = timeout

    # -- transport ----------------------------------------------------------
    def request(
        self,
        method: str,
        path: str,
        *,
        body: "dict | None" = None,
        raw_body: "bytes | None" = None,
        content_type: str = "application/json",
    ) -> HttpResponse:
        url = self.base_url + path
        headers = {"Accept": "application/json", "Connection": "close"}
        data: "bytes | None" = None
        if raw_body is not None:
            data = raw_body
            headers["Content-Type"] = content_type
        elif body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = Request(url, data=data, method=method, headers=headers)  # noqa: S310
        try:
            with urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                text = resp.read().decode("utf-8", "replace")
                return HttpResponse(method, url, int(resp.status), text)
        except HTTPError as exc:
            try:
                text = exc.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001 - the body is optional evidence
                text = ""
            return HttpResponse(method, url, int(exc.code), text)
        except URLError as exc:
            raise ApiUnreachable(self.base_url, str(exc.reason or exc)) from None
        except OSError as exc:  # e.g. ECONNREFUSED surfaces as OSError/URLError
            raise ApiUnreachable(self.base_url, str(exc)) from None

    # -- JSON helpers -------------------------------------------------------
    def get_json(self, path: str) -> Any:
        """GET a URL that must return JSON. Raises ``ApiError`` on non-2xx."""
        resp = self.request("GET", path)
        if not resp.ok:
            raise ApiError.from_response(resp)
        return self._parse(resp)

    def get_json_or_none(self, path: str) -> Any:
        """GET a URL whose absence is meaningful (e.g. a missing session)."""
        resp = self.request("GET", path)
        if resp.status == 404:
            return None
        if not resp.ok:
            raise ApiError.from_response(resp)
        return self._parse(resp)

    @staticmethod
    def _parse(resp: HttpResponse) -> Any:
        if not resp.text:
            return None
        try:
            return json.loads(resp.text)
        except ValueError as exc:
            raise ApiError(
                f"GET {resp.url} returned a body that is not JSON"
            ) from exc

    def post_json(
        self,
        path: str,
        body: "dict | None" = None,
        *,
        raw_body: "bytes | None" = None,
    ) -> HttpResponse:
        """POST a body and return the raw response (status included)."""
        return self.request("POST", path, body=body, raw_body=raw_body)

    def session_detail_path(self, session_id: str) -> str:
        return f"/api/sessions/{quote(session_id)}"

    def decisions_path(self, session_id: "str | None" = None) -> str:
        if not session_id:
            return "/api/decisions"
        return f"/api/decisions?{urlencode({'sid': session_id})}"


class ApiError(RuntimeError):
    """A JSON endpoint answered with a non-2xx status."""

    def __init__(self, message: str, status: "int | None" = None, url: str = ""):
        super().__init__(message)
        self.status = status
        self.url = url

    @classmethod
    def from_response(cls, resp: HttpResponse) -> "ApiError":
        return cls(f"GET {resp.url} -> HTTP {resp.status}", resp.status, resp.url)


def quote(text: str) -> str:
    """``urllib.parse.quote``, imported here so the module stays import-only."""
    from urllib.parse import quote as _quote

    return _quote(str(text), safe="")
