"""JEV COMMAND CENTER -- MCP facade (S-02).

A THIN MCP transport surface over the Operator Skill. See :mod:`mcp_facade.facade`
for the authority model. The package ensures the Operator Skill's transport
module is importable (it is broken on this tree) before importing it.
"""
from __future__ import annotations

from ._compat import ensure_backends

ensure_backends()  # MUST run before any `import operator_skill` in this process

from .capability import (  # noqa: E402
    AdapterCapabilityProbe,
    CapabilityProbe,
    StubProbe,
)
from .facade import McpFacade, McpContent, McpToolResult  # noqa: E402
from .manifest import (  # noqa: E402
    build_capabilities_manifest,
    write_capabilities_manifest,
)
from .server import McpServer  # noqa: E402
from .transport import (  # noqa: E402
    ASGIBackend,
    Backend,
    BackendResult,
    LoopbackHttpBackend,
    RecordingBackend,
)

__all__ = [
    "AdapterCapabilityProbe",
    "ASGIBackend",
    "Backend",
    "BackendResult",
    "CapabilityProbe",
    "LoopbackHttpBackend",
    "McpContent",
    "McpFacade",
    "McpServer",
    "McpToolResult",
    "RecordingBackend",
    "StubProbe",
    "build_capabilities_manifest",
    "write_capabilities_manifest",
]
