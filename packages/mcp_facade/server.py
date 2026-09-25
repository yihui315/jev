"""Minimal MCP (Model Context Protocol) server surface over stdio.

Implements the subset of JSON-RPC 2.0 + MCP method surface the S-02 gate needs:

* ``initialize``
* ``tools/list``
* ``tools/call``
* ``ping``
* ``notifications/initialized`` (no response)

No third-party SDK is required (the host MCP SDK may be unavailable); the wire
format is implemented directly. The server is loopback-only by design -- it reads
JSON-RPC frames from stdin and writes responses to stdout; it never binds a
socket, and it never logs the control token.
"""
from __future__ import annotations

import json
import logging
import sys
from typing import Any, Dict, List, Optional

from .facade import McpFacade

logger = logging.getLogger("jcc.mcp_server")

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "jcc-mcp-facade"
SERVER_VERSION = "0.1.0"


class McpServer:
    def __init__(self, facade: McpFacade) -> None:
        self.facade = facade

    # --- JSON-RPC dispatch ------------------------------------------------ #
    def handle_message(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(message, dict):
            return self._error(None, -32600, "invalid request object")
        method = message.get("method")
        msg_id = message.get("id")
        is_notification = msg_id is None

        if method == "initialize":
            return self._response(msg_id, self._initialize())
        if method == "ping":
            return self._response(msg_id, {})
        if method == "notifications/initialized":
            return None  # notification: no response
        if method == "tools/list":
            return self._response(msg_id, {"tools": self.facade.list_mcp_tools()})
        if method == "tools/call":
            params = message.get("params") or {}
            name = params.get("name")
            args = params.get("arguments") or {}
            return self._response(msg_id, self._tool_call(name, args))

        # unknown method
        if is_notification:
            return None
        return self._error(msg_id, -32601, f"method not found: {method}")

    @staticmethod
    def _response(msg_id: Any, result: Any) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    @staticmethod
    def _error(msg_id: Any, code: int, message: str) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    # --- method handlers -------------------------------------------------- #
    def _initialize(self) -> Dict[str, Any]:
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }

    def _tool_call(self, name: Optional[str], args: Any) -> Dict[str, Any]:
        if not name:
            return {
                "content": [{"type": "text", "text": "missing tool name"}],
                "isError": True,
            }
        result = self.facade.call_tool(name, args if isinstance(args, dict) else {})
        content = [{"type": c.type, "text": c.text} for c in result.content]
        response: Dict[str, Any] = {"content": content, "isError": result.is_error}
        if result.structured:
            response["structuredContent"] = result.structured
        return response

    # --- stdio loop ------------------------------------------------------ #
    def run(self, stdin: Any = None, stdout: Any = None) -> None:
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                stdout.write(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": None,
                            "error": {"code": -32700, "message": f"parse error: {exc}"},
                        }
                    )
                    + "\n"
                )
                stdout.flush()
                continue
            try:
                response = self.handle_message(message)
            except Exception as exc:  # never crash the loop
                logger.exception("mcp dispatch error")
                response = self._error(message.get("id"), -32603, f"internal error: {exc}")
            if response is not None:
                stdout.write(json.dumps(response, default=str) + "\n")
                stdout.flush()
