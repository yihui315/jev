"""OpenInference-style semantic conventions for the JCC supervision chain (S-03).

Why an "OpenInference-style" convention and not OpenInference verbatim
----------------------------------------------------------------------
OpenInference (Arize) defines ``openinference.span.kind``, ``input.value``,
``output.value``, ``llm.*``, ``tool.*``... this package adopts exactly that
namespace and key vocabulary so that a future ``openinference-instrumentation``
backend consumes these spans without translation. It does NOT depend on, import
or require the OpenInference packages: this module is pure data + small
helpers, and its only runtime dependency is this package.

The chain
---------
The hop names below are **not invented**: each one is pinned to a call site
that exists in the shipped source, and ``tests/test_chain_hop_provenance.py``
fails if a pinned token disappears from the file it is claimed to prove.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional, Tuple

# --------------------------------------------------------------------------- #
# Attribute keys (OpenInference namespace + the JCC-local ``jcc.*`` keys)
# --------------------------------------------------------------------------- #
ATTR_SPAN_KIND = "openinference.span.kind"
ATTR_INPUT_VALUE = "input.value"
ATTR_INPUT_MIME = "input.mime_type"
ATTR_OUTPUT_VALUE = "output.value"
ATTR_OUTPUT_MIME = "output.mime_type"
ATTR_LLM_MODEL = "llm.model_name"
ATTR_LLM_PROVIDER = "llm.provider"
ATTR_LLM_TOKENS_PROMPT = "llm.token_count.prompt"
ATTR_LLM_TOKENS_COMPLETION = "llm.token_count.completion"
ATTR_LLM_TOKENS_TOTAL = "llm.token_count.total"
ATTR_TOOL_NAME = "tool.name"
ATTR_TOOL_PARAMS = "tool.parameters"

#: JCC-specific keys. Every one of them is a non-secret scalar by construction;
#: anything else on a hop span goes through the redaction gate first.
ATTR_SESSION_ID = "jcc.session_id"
ATTR_EVENT_ID = "jcc.event_id"
ATTR_DELIVERY_ID = "jcc.delivery_id"
ATTR_AGENT = "jcc.agent"
ATTR_PROJECT = "jcc.project"
ATTR_ACTION = "jcc.action"
ATTR_DIRECTIVE_ID = "jcc.directive_id"
ATTR_POLICY_VERSION = "jcc.policy_version"
ATTR_POLICY_TABLE_VERSION = "jcc.policy_table_version"
ATTR_EVIDENCE_HASH = "jcc.evidence_hash"
ATTR_ACTED = "jcc.acted"
ATTR_EXECUTABLE = "jcc.executable"
ATTR_ACTUATION_REASON = "jcc.actuation_reason"
ATTR_ORIGIN = "jcc.origin"
ATTR_DEDUPE = "jcc.dedupe"
ATTR_PROVIDER = "jcc.provider"
ATTR_BAND = "jcc.band"
ATTR_RISK_SCORE = "jcc.risk_score"
ATTR_DURATION_MS = "jcc.duration_ms"
ATTR_STEP = "jcc.step"

#: Resource attributes (OpenInference ``resource.attributes``).
RESOURCE_SERVICE_NAME = "service.name"
RESOURCE_SERVICE_VERSION = "service.version"
RESOURCE_SCHEMA_URL = "schema.url"

#: ``schema.url`` for this convention, when a backend records one.
SCHEMA_URL = "https://github.com/Arize-ai/openinference-spec/spec/semantic_conventions.md"

#: OpenInference span kinds.
KIND_CHAIN = "CHAIN"
KIND_AGENT = "AGENT"
KIND_LLM = "LLM"
KIND_TOOL = "TOOL"
KIND_RETRIEVER = "RETRIEVER"
KIND_UNKNOWN = "UNKNOWN"


class ChainHop(str, Enum):
    """The seven supervision hops this layer instrumented.

    Values are stable wire names (they appear in exported spans and in the
    health object); the enum *members* are the code-facing handles.

    Provenance (file -> the call site that makes the hop real)::

        ingest      packages/jcc_core/core.py   -> CoreEngine.ingest
        aep         packages/jcc_core/core.py   -> StateStore.seen (AEP envelope identity + dedupe)
        state       packages/jcc_core/state_store.py -> StateStore.add_event / add_evidence / upsert_session
        assessment  packages/jcc_core/core.py   -> JevProvider.assess
        policy      packages/jcc_core/policy.py -> policy.decide -> decision.arbitrate
        directive   packages/jcc_core/core.py   -> ActuationPort.dispatch of the built Directive
        audit       packages/jcc_core/audit.py  -> StateStore.append_audit (hash-chained tape)
    """

    INGEST = "ingest"
    AEP = "aep"
    STATE = "state"
    ASSESSMENT = "assessment"
    POLICY = "policy"
    DIRECTIVE = "directive"
    AUDIT = "audit"

    @property
    def is_chain_hop(self) -> bool:
        return True


#: The order the hops occur in on ONE event. ``loop`` asserts it; a new hop in
#: the middle of the chain without an entry here is a visible gap.
CHAIN_ORDER: Tuple[ChainHop, ...] = (
    ChainHop.INGEST,
    ChainHop.AEP,
    ChainHop.STATE,
    ChainHop.ASSESSMENT,
    ChainHop.POLICY,
    ChainHop.DIRECTIVE,
    ChainHop.AUDIT,
)

#: hop -> (span name, OpenInference span kind).
#: Span names are namespaced (``jcc.``), the kind is the OpenInference one.
HOP_SPAN: Dict[ChainHop, Tuple[str, str]] = {
    ChainHop.INGEST: ("jcc.chain.ingest", KIND_CHAIN),
    ChainHop.AEP: ("jcc.aep.envelope", KIND_AGENT),
    ChainHop.STATE: ("jcc.state.write", KIND_CHAIN),
    ChainHop.ASSESSMENT: ("jcc.jev.assess", KIND_LLM),
    ChainHop.POLICY: ("jcc.policy.decide", KIND_CHAIN),
    ChainHop.DIRECTIVE: ("jcc.directive.dispatch", KIND_CHAIN),
    ChainHop.AUDIT: ("jcc.audit.append", KIND_RETRIEVER),
}


def span_name(hop: ChainHop) -> str:
    return HOP_SPAN[hop][0]


def span_kind(hop: ChainHop) -> str:
    return HOP_SPAN[hop][1]


def hop_of(name: str) -> Optional[ChainHop]:
    """Inverse of :func:`span_name`; ``None`` for anything else."""
    for hop, (span, _) in HOP_SPAN.items():
        if span == name:
            return hop
    return None


def as_kind(value: Optional[str]) -> str:
    """Coerce a free-form kind string to a known OpenInference kind."""
    known = {KIND_CHAIN, KIND_AGENT, KIND_LLM, KIND_TOOL, KIND_RETRIEVER}
    return value if value in known else KIND_UNKNOWN


def base_resource(service_name: str = "jcc-core", version: str = "p1.0.0") -> Dict[str, Any]:
    """The default OpenInference resource attributes for this platform."""
    return {
        RESOURCE_SERVICE_NAME: service_name,
        RESOURCE_SERVICE_VERSION: version,
        RESOURCE_SCHEMA_URL: SCHEMA_URL,
    }
