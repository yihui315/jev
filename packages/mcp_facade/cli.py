"""CLI entry point for the JCC MCP facade stdio server.

The facade is THIN: it constructs an ``OperatorSkill`` (with a transport backend
this package provides) and serves it over MCP. Gateway wiring beyond the default
is a deployment concern and is intentionally not forced here -- gateway tools
refuse through the facade's own capability check and the Operator Skill's five
gates when the deployment has not authorised them.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

from ._compat import ensure_backends

ensure_backends()  # must run before importing operator_skill

from operator_skill.skill import OperatorSkill  # noqa: E402

from .facade import McpFacade  # noqa: E402
from .server import McpServer  # noqa: E402
from .transport import LoopbackHttpBackend, RecordingBackend  # noqa: E402


def build_skill(
    trace_path: str,
    backend=None,
    gateway=None,
    approvals=None,
    token_env: str = "JCC_CONTROL_TOKEN",
) -> OperatorSkill:
    backend = backend or LoopbackHttpBackend()
    return OperatorSkill(
        backend,
        trace_path=trace_path,
        gateway=gateway,
        approvals=approvals,
        control_token_env=token_env,
    )


def main(argv: Optional[list] = None) -> int:  # type: ignore[name-defined]
    p = argparse.ArgumentParser(prog="jcc-mcp-facade")
    p.add_argument("--trace-path", default="runtime/jcc/mcp-facade-trace.jsonl")
    p.add_argument("--base-url", default="http://127.0.0.1:4778")
    p.add_argument("--mode", choices=["http", "record"], default="http")
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    if args.mode == "record":
        backend = RecordingBackend()
    else:
        backend = LoopbackHttpBackend(base_url=args.base_url)
    skill = build_skill(args.trace_path, backend=backend)
    facade = McpFacade(skill)
    server = McpServer(facade)
    server.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
