"""The JCC Operator Skill (S-01).

Thirteen tools -- nine read-only reads, one inert ingest, three gateway requests.
The two properties the acceptance gate checks are structural, not documented:

**No direct execution authority.** No tool in this package can bring about an
actuation: there is no reference to ``jcc_core.actuator`` anywhere in the module
graph, no shell, no socket bind and no write to the store's tables. ``jcc_assess``
records an assessment (its directive is INERT); ``jcc_pause``/``jcc_steer``/
``jcc_retry`` are requests that are re-validated by ``gates.Gateway`` and then
handed to the real control-plane endpoint -- which is the only writer. A test
pins this by AST-scanning the package (``test_no_execution_authority.py``).

**Every invocation leaves a trace.** Successes, refusals and errors alike. The
record is redacted by construction (``trace.TraceWriter``), so the control token
and any signature never reach the tape.

Read endpoints of the core API are open BY DESIGN (declared in
``jcc_core.server.OPEN_GET_ENDPOINTS``); the skill does not treat that as
something to fix, it simply never uses the absence of a token to do anything
other than read. Every mutation-capable path refuses first when the control token
is missing, before any request is issued.
"""
from __future__ import annotations

import dataclasses
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from jcc_core.decision import ThresholdsConfig, arbitrate, normalize, trace_hash
from jcc_core.schemas import AEPEnvelope, Assessment, DirectiveAction

from . import manifest as manifest_mod
from . import refusals as R
from .approvals import ApprovalRegistry
from .backends import BackendResult, RecordingBackend
from .gates import Gateway, GatewayContext, Principal
from .trace import REDACTED, TraceWriteError, TraceWriter, signature_ref

DEFAULT_TOKEN_ENV = "JCC_CONTROL_TOKEN"

# Sentinel distinguishing "caller explicitly supplied a value (incl. None)" from
# "caller did not supply anything, so fall back to the environment". Without this
# an explicitly injected ``control_token=None`` (the test's "no token"
# intent) was silently overwritten by ``JCC_CONTROL_TOKEN`` from the environment,
# which let a missing token masquerade as a present one and be reported as
# ``GATE_UNAVAILABLE`` instead of ``MISSING_CONTROL_TOKEN``.
_UNSET = object()


# --------------------------------------------------------------------------- #
# result
# --------------------------------------------------------------------------- #
@dataclass
class ToolResult:
    """What a tool call returned. A refusal is a normal outcome, never an exception."""

    tool: str
    mode: str
    outcome: Dict[str, Any]
    data: Any = None
    refusal: Optional[R.Refusal] = None
    trace: Optional[Dict[str, Any]] = None

    @property
    def ok(self) -> bool:
        return self.outcome.get("status") == "ok"

    @property
    def status(self) -> str:
        return str(self.outcome.get("status"))

    def as_dict(self) -> Dict[str, Any]:
        return {
            "tool": self.tool,
            "mode": self.mode,
            "ok": self.ok,
            "status": self.status,
            "outcome": self.outcome,
            "refusal": self.refusal.as_dict() if self.refusal else None,
            "data": self.data,
            "trace": self.trace,
        }


# --------------------------------------------------------------------------- #
# the skill
# --------------------------------------------------------------------------- #
class OperatorSkill:
    """Read-only-by-default tool surface over the REAL control plane."""

    def __init__(
        self,
        backend,
        *,
        trace_path: str,
        gateway: Optional[Gateway] = None,
        approvals: Optional[ApprovalRegistry] = None,
        control_token_env: str = DEFAULT_TOKEN_ENV,
        control_token: Optional[str] = _UNSET,  # type: ignore[assignment]
        clock: Callable[[], float] = time.time,
        trace_writer: Optional[TraceWriter] = None,
    ) -> None:
        self.backend = backend
        self.clock = clock
        self.token_env = control_token_env
        self._control_token = control_token
        self.gateway = gateway
        self.gateway_context: Optional[GatewayContext] = (
            gateway.context if gateway is not None else None
        )
        self.approvals = approvals
        self.budget = (
            self.gateway_context.budget if self.gateway_context is not None else None
        )
        if trace_writer is not None:
            self.tracer = trace_writer
        else:
            secrets = (self.control_token,)
            self.tracer = TraceWriter(trace_path, secrets=secrets)
        self.trace_path = self.tracer.path

    # -- control token ------------------------------------------------------ #
    @property
    def control_token(self) -> Optional[str]:
        """The token, read from the environment unless it was injected.

        Never logged, never placed on a trace record, never returned to a caller:
        the value is only ever handed to the backend as a header.

        If the caller injected an explicit value -- including ``None`` -- that
        value is authoritative and the environment is NOT consulted. Only when
        the caller supplied nothing (``_UNSET``) do we fall back to the
        environment variable. This is what keeps "caller did not provide a
        token" (``MISSING_CONTROL_TOKEN``) distinct from "the control gate
        service is unavailable" (``GATE_UNAVAILABLE``).
        """
        if self._control_token is _UNSET:
            self._control_token = os.environ.get(self.token_env) or None
        return self._control_token

    @property
    def control_token_configured(self) -> bool:
        return bool(self.control_token)

    def _require_token(self) -> Optional[R.Refusal]:
        if self.control_token:
            return None
        return R.refusal(
            R.CODE_MISSING_CONTROL_TOKEN,
            f"the control token ({self.token_env}) is not configured; every "
            "mutation-capable tool refuses before it issues a request",
            tool="",
        )

    # -- dispatch ----------------------------------------------------------- #
    def call(
        self,
        tool: str,
        arguments: Optional[Dict[str, Any]] = None,
        *,
        principal: Optional[Principal] = None,
    ) -> ToolResult:
        """Run one tool. Never raises: every failure becomes a refusal."""
        principal = principal or Principal()
        try:
            spec = manifest_mod.spec(tool)
        except KeyError as exc:
            refusal = R.refusal(
                R.CODE_UNKNOWN_TOOL, str(exc).replace("'", ""), tool=tool
            )
            return self._finish(None, arguments, principal, refusal, data=None,
                                endpoint=None, http_status=None, directive=None)

        try:
            args = self._bind(spec, arguments)
        except R.Refusal as exc:
            return self._finish(spec, arguments, principal, exc, data=None,
                                endpoint=None, http_status=None, directive=None)
        except Exception as exc:  # noqa: BLE001 - never crash the caller
            refusal = R.refusal(
                R.CODE_MALFORMED_ARGUMENT,
                f"arguments could not be read: {type(exc).__name__}",
                tool=spec.name,
            )
            return self._finish(spec, arguments, principal, refusal, data=None,
                                endpoint=None, http_status=None, directive=None)

        handler = getattr(self, f"_tool_{spec.name}")
        try:
            outcome, data, directive = handler(spec, args, principal)
        except R.Refusal as exc:
            return self._finish(spec, arguments, principal, exc, data=None,
                                endpoint=spec.endpoint, http_status=None, directive=None)
        except Exception as exc:  # noqa: BLE001 - a crash is a refusal, not a pass
            refusal = R.refusal(
                R.CODE_INTERNAL_ERROR,
                f"tool {spec.name} failed: {type(exc).__name__}: {exc}",
                tool=spec.name,
                session_id=args.get("session_id"),
            )
            return self._finish(spec, arguments, principal, refusal, data=None,
                                endpoint=spec.endpoint, http_status=None, directive=None)

        return self._finish(spec, arguments, principal, None, data=data,
                            endpoint=spec.endpoint, http_status=outcome.get("http_status"),
                            directive=directive, outcome=outcome)

    # -- argument binding (strict) ------------------------------------------ #
    def _bind(self, spec, arguments: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Bind arguments to the DECLARED key set. Anything else is refused.

        This is the first line of the privilege-escalation defence: a read-only
        tool refuses an argument it does not declare rather than forwarding or
        ignoring it, so a caller cannot smuggle ``action``, ``approver``,
        ``approve`` or ``tool`` into a read.
        """
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise R.refusal(
                R.CODE_MALFORMED_ARGUMENT,
                f"arguments must be a mapping, got {type(arguments).__name__}",
                tool=spec.name,
            )
        unknown = sorted(str(k) for k in arguments if str(k) not in spec.arguments)
        if unknown:
            allowed = ", ".join(spec.arguments) or "(none)"
            raise R.refusal(
                R.CODE_MALFORMED_ARGUMENT,
                f"tool {spec.name!r} does not accept argument(s) {unknown}; allowed: {allowed}",
                tool=spec.name,
            )
        bound = dict(arguments)
        if "session_id" in spec.arguments and not bound.get("session_id"):
            raise R.refusal(
                R.CODE_MALFORMED_ARGUMENT,
                f"tool {spec.name!r} requires a non-empty 'session_id'",
                tool=spec.name,
            )
        if "session_id" in bound and not manifest_mod.is_valid_session_id(bound["session_id"]):
            raise R.refusal(
                R.CODE_MALFORMED_SESSION_ID,
                "session_id must match the identity shape "
                f"{manifest_mod.SESSION_ID_RE.pattern}; refusing to use it as an "
                "identity or to interpolate it into a request path",
                tool=spec.name,
                session_id=str(bound["session_id"])[:64],
            )
        return bound

    # -- transport helper --------------------------------------------------- #
    def _request(
        self, method: str, path: str, spec, token: bool, json_body=None
    ) -> BackendResult:
        return self.backend.request(
            method, path, json_body=json_body, token=self.control_token if token else None
        )

    @staticmethod
    def _map_status(result: BackendResult, spec, tool: str) -> Optional[R.Refusal]:
        """Turn an API status into a structured refusal."""
        if result.status == 403:
            return R.refusal(
                R.CODE_INVALID_CONTROL_TOKEN,
                "the control plane refused the request: invalid or missing control "
                "token (HTTP 403)",
                tool=tool,
            )
        if result.status == 401:
            return R.refusal(
                R.CODE_INVALID_CONTROL_TOKEN,
                "the control plane is locked: no control token is configured "
                "(HTTP 401)",
                tool=tool,
            )
        if result.status == 404:
            return R.refusal(
                R.CODE_UNKNOWN_SESSION, "the control plane does not know that session (HTTP 404)",
                tool=tool,
            )
        if result.status is not None and result.status >= 400:
            return R.refusal(
                R.CODE_UPSTREAM_ERROR,
                f"the control plane returned HTTP {result.status}",
                tool=tool,
            )
        if not result.ok:
            return R.refusal(
                R.CODE_UPSTREAM_ERROR, f"transport failed: {result.error}", tool=tool
            )
        return None

    def _finish(
        self,
        spec,
        arguments: Optional[Dict[str, Any]],
        principal: Principal,
        refusal: Optional[R.Refusal],
        *,
        data: Any,
        endpoint: Optional[str],
        http_status: Optional[int],
        directive: Optional[Any] = None,
        outcome: Optional[Dict[str, Any]] = None,
    ) -> ToolResult:
        """Assemble the outcome and ALWAYS write the trace record."""
        spec = spec or None
        session_id = None
        if arguments and isinstance(arguments, dict):
            session_id = arguments.get("session_id")
        if refusal is not None:
            status, code, summary = "refused", refusal.code, refusal.message
            ref = None
        else:
            status = "ok"
            code = None
            summary = str(outcome.get("summary", "")) if outcome else ""
            ref = outcome.get("referenced") if outcome else None
        if outcome is not None and status == "refused":
            outcome = {**outcome, "status": "refused", "code": code, "summary": summary}

        record = {
            "tool": spec.name if spec else "",
            "tool_mode": spec.mode if spec else "",
            "arguments": dict(arguments or {}),
            "principal": {"name": principal.name,
                          "capabilities": sorted(principal.capabilities)},
            "session_id": session_id,
            "outcome": outcome
            or {
                "status": status,
                "code": code,
                "summary": summary,
                # The skill never actuates. Pinned False on every path, including
                # the successful ones -- see test_no_execution_authority.py.
                "executed": False,
                "referenced": ref,
            },
            "directive_ref": self._directive_ref(directive),
            "endpoint": endpoint,
            "http_status": http_status,
            "evidence_path": str(self.trace_path),
        }
        written: Dict[str, Any] = {}
        try:
            written = self.tracer.append(record)
        except TraceWriteError as exc:
            # The tape failing is itself a refusal; the failure is reported rather
            # than swallowed, and the in-memory record explains why.
            refusal = R.refusal(
                R.CODE_TRACE_UNAVAILABLE,
                f"the trace tape could not be written: {exc}",
                tool=spec.name if spec else "",
                session_id=session_id,
            )
            record["outcome"] = {
                "status": "error",
                "code": R.CODE_TRACE_UNAVAILABLE,
                "summary": refusal.message,
                "executed": False,
            }
            return ToolResult(
                tool=spec.name if spec else "",
                mode=spec.mode if spec else "",
                outcome=record["outcome"],
                data=None,
                refusal=refusal,
                trace=record,
            )

        result = ToolResult(
            tool=spec.name if spec else "",
            mode=spec.mode if spec else "",
            outcome=record["outcome"],
            data=data,
            refusal=refusal,
            trace=written,
        )
        return result

    @staticmethod
    def _directive_ref(directive: Optional[Any]) -> Optional[Dict[str, Any]]:
        if directive is None:
            return None
        return {
            "directive_id": getattr(directive, "directive_id", None),
            "action": getattr(directive, "action", None),
            "signature_ref": signature_ref(getattr(directive, "signature", None)),
            "signed": bool(getattr(directive, "signed", False)),
            "requires_human": bool(getattr(directive, "requires_human", False)),
        }

    # ------------------------------------------------------------------ #
    # read-only tools
    # ------------------------------------------------------------------ #
    def _tool_jcc_status(self, spec, args, principal) -> Tuple[Dict[str, Any], Any, None]:
        result = self._request("GET", "/api/health", spec, token=False)
        refusal = self._map_status(result, spec, spec.name)
        if refusal:
            raise refusal
        health = result.payload or {}
        summary = (
            f"control plane ok (provider={health.get('jev_provider')}, "
            f"actuation_enabled={health.get('actuation_enabled')})"
        )
        outcome = {
            "status": "ok",
            "summary": summary,
            "executed": False,
            "http_status": result.status,
            "referenced": None,
        }
        data = {
            "health": health,
            # Configuration STATE only. The token VALUE is never read back here,
            # and the trace redactor masks it even if it somehow reached a field.
            "control_token_configured": self.control_token_configured,
            "tools_offered": manifest_mod.tool_names(),
            "read_only_tools": [n for n in manifest_mod.tool_names()
                                if not manifest_mod.spec(n).can_actuate],
        }
        return outcome, data, None

    def _tool_jcc_health(self, spec, args, principal) -> Tuple[Dict[str, Any], Any, None]:
        result = self._request("GET", "/api/health", spec, token=False)
        refusal = self._map_status(result, spec, spec.name)
        if refusal:
            raise refusal
        return (
            {
                "status": "ok",
                "summary": "health payload returned",
                "executed": False,
                "http_status": result.status,
                "referenced": None,
            },
            result.payload,
            None,
        )

    def _tool_jcc_sessions(self, spec, args, principal) -> Tuple[Dict[str, Any], Any, None]:
        result = self._request("GET", "/api/sessions", spec, token=False)
        refusal = self._map_status(result, spec, spec.name)
        if refusal:
            raise refusal
        rows = result.payload or []
        return (
            {
                "status": "ok",
                "summary": f"{len(rows)} session(s)",
                "executed": False,
                "http_status": result.status,
                "referenced": None,
            },
            rows,
            None,
        )

    def _tool_jcc_session(self, spec, args, principal) -> Tuple[Dict[str, Any], Any, None]:
        session_id = args["session_id"]
        result = self._request(
            "GET", f"/api/sessions/{session_id}", spec, token=False
        )
        refusal = self._map_status(result, spec, spec.name)
        if refusal:
            raise refusal
        return (
            {
                "status": "ok",
                "summary": f"session {session_id}: "
                           f"{str((result.payload or {}).get('status'))}",
                "executed": False,
                "http_status": result.status,
                "referenced": None,
            },
            result.payload,
            None,
        )

    def _tool_jcc_assess(self, spec, args, principal) -> Tuple[Dict[str, Any], Any, None]:
        # The envelope is built and validated through the REAL wire schema, so a
        # malformed ingest is refused here (and the server still writes its
        # rejection audit row) instead of reaching the engine as a half-event.
        refusal = self._require_token()
        if refusal:
            raise refusal
        envelope = self._build_envelope(args)
        result = self._request("POST", "/api/assess", spec, token=True,
                               json_body=envelope)
        refusal = self._map_status(result, spec, spec.name)
        if refusal:
            raise refusal
        payload = result.payload or {}
        directive = payload.get("directive") or {}
        referenced = {
            "directive_id": directive.get("directive_id"),
            "action": directive.get("action"),
            "signature_ref": signature_ref(directive.get("signature")),
            "acted": payload.get("acted"),
            "actuation_reason": payload.get("actuation_reason"),
        }
        outcome = {
            "status": "ok",
            "summary": (
                f"assessed event {envelope['event_id']} -> "
                f"{directive.get('action')} (inert: acted="
                f"{payload.get('acted')}, reason={payload.get('actuation_reason')})"
            ),
            "executed": False,
            "http_status": result.status,
            "referenced": referenced,
        }
        return outcome, payload, None

    @staticmethod
    def _build_envelope(args: Dict[str, Any]) -> Dict[str, Any]:
        agent = args.get("agent") or "generic"
        body = {
            "version": "aep/v0.1",
            "event_id": args.get("event_id") or f"evt_{uuid.uuid4().hex[:12]}",
            "delivery_id": args.get("delivery_id") or f"del_{uuid.uuid4().hex[:12]}",
            "session_id": args["session_id"],
            "agent": agent,
            "project": args.get("project") or "unknown",
            "timestamp": time.time(),
            "event_type": args.get("event_type") or "agent_message",
            "payload": args.get("payload") if isinstance(args.get("payload"), dict) else {},
            "evidence": args.get("evidence"),
            "schema_ok": True,
        }
        envelope = AEPEnvelope(**body)  # raises ValidationError -> refused upstream
        # The scrub is the core ingest path's own (``core.CoreEngine.ingest``
        # step 0). Doing it here too is defence in depth, not duplication: this
        # is the only place a *client-side* envelope is assembled.
        from jcc_core.redaction import scrub_value_secrets

        envelope.payload = scrub_value_secrets(envelope.payload)
        if envelope.evidence is not None:
            envelope.evidence = scrub_value_secrets(envelope.evidence)
        return envelope.model_dump_api()

    def _tool_jcc_explain(self, spec, args, principal) -> Tuple[Dict[str, Any], Any, None]:
        session_id = args["session_id"]
        result = self._request(
            "GET", f"/api/sessions/{session_id}", spec, token=False
        )
        refusal = self._map_status(result, spec, spec.name)
        if refusal:
            raise refusal
        snapshot = result.payload or {}
        raw = snapshot.get("current_assessment")
        if not raw:
            raise R.refusal(
                R.CODE_NOTHING_TO_EXPLAIN,
                f"session {session_id!r} has no assessment to explain",
                tool=spec.name,
                session_id=session_id,
            )
        assessment = raw if isinstance(raw, Assessment) else Assessment(**raw)
        thresholds = self._thresholds()
        # ``trace_hash`` and the policy predicates expect the INNER ``Thresholds``
        # object, not the ``ThresholdsConfig`` wrapper ``_thresholds`` may return
        # (ThresholdsConfig has no ``as_dict``); otherwise jcc_explain crashes on
        # every real call.
        inner_thresholds = (
            thresholds.default if isinstance(thresholds, ThresholdsConfig) else thresholds
        )
        signals = normalize(assessment)
        verdict = arbitrate(signals, inner_thresholds, assessment=assessment)
        current = snapshot.get("current_directive") or {}
        explained = verdict.as_dict()
        outcome = {
            "status": "ok",
            "summary": (
                f"{session_id}: row {verdict.reason_code!r} -> "
                f"{verdict.action.value} (blocked={verdict.blocked})"
            ),
            "executed": False,
            "http_status": result.status,
            "referenced": {
                "directive_id": current.get("directive_id"),
                "signature_ref": signature_ref(current.get("signature")),
                "trace_hash": trace_hash(signals, inner_thresholds, verdict)[:16],
            },
        }
        data = {
            "session_id": session_id,
            "action": verdict.action.value,
            "reason_code": verdict.reason_code,
            "matched_rows": list(verdict.matched_rows),
            "blocked": verdict.blocked,
            "blocker_reasons": list(verdict.blocker_reasons),
            "table_version": verdict.table_version,
            "policy_config_hash": thresholds.config_hash(),
            "signals": signals.as_dict(),
            "current_directive_action": current.get("action"),
            "explanation": explained,
        }
        return outcome, data, None

    def _thresholds(self) -> ThresholdsConfig:
        policy = getattr(self.gateway_context, "policy", None)
        if policy is not None and getattr(policy, "thresholds", None) is not None:
            return policy.thresholds
        return ThresholdsConfig()

    def _tool_jcc_verify(self, spec, args, principal) -> Tuple[Dict[str, Any], Any, None]:
        result = self._request("GET", "/api/audit/verify", spec, token=False)
        refusal = self._map_status(result, spec, spec.name)
        if refusal:
            raise refusal
        payload = result.payload or {}
        return (
            {
                "status": "ok",
                "summary": f"audit tape {'intact' if payload.get('intact') else 'BROKEN'} "
                           f"({payload.get('entries')} entries)",
                "executed": False,
                "http_status": result.status,
                "referenced": None,
            },
            payload,
            None,
        )

    def _tool_jcc_recent_decisions(
        self, spec, args, principal
    ) -> Tuple[Dict[str, Any], Any, None]:
        session_id = args.get("session_id")
        path = "/api/decisions" + (f"?sid={session_id}" if session_id else "")
        result = self._request("GET", path, spec, token=False)
        refusal = self._map_status(result, spec, spec.name)
        if refusal:
            raise refusal
        rows = result.payload or []
        return (
            {
                "status": "ok",
                "summary": f"{len(rows)} directive(s)"
                           + (f" for {session_id}" if session_id else ""),
                "executed": False,
                "http_status": result.status,
                "referenced": None,
            },
            rows,
            None,
        )

    def _tool_jcc_budget(self, spec, args, principal) -> Tuple[Dict[str, Any], Any, None]:
        # The budget is a REAL read of the DERIVED counters (``BudgetGovernor``
        # over the real store), not a private copy of the numbers.
        if self.budget is None:
            raise R.refusal(
                R.CODE_BUDGET_UNAVAILABLE,
                "no budget governor is wired for this skill; the counters it would "
                "report are not available, so nothing is invented in their place",
                tool=spec.name,
            )
        session_id = args.get("session_id")
        if not session_id:
            rows = self._tool_jcc_sessions(spec, {}, principal)[1] or []
            session_ids = [str(r.get("session_id")) for r in rows if r.get("session_id")]
        else:
            session_ids = [session_id]
        snapshots = []
        for sid in session_ids:
            snap = self.budget.snapshot(sid)
            snapshots.append(
                {
                    "session_id": sid,
                    "exhausted_code": snap.exhausted_code,
                    "healthy": snap.healthy,
                    "assessments_used": snap.assessments_used,
                    "directives_used": snap.directives_used,
                    "rate_used": snap.rate_used,
                    "model_units_used": snap.model_units_used,
                    "cost_units_used": snap.cost_units_used,
                    "waived_dimensions": list(snap.waived_dimensions),
                    "limits": dataclasses.asdict(snap.limits) if dataclasses.is_dataclass(snap.limits) else snap.limits,
                }
            )
        outcome = {
            "status": "ok",
            "summary": f"{len(snapshots)} budget snapshot(s); "
                       f"exhausted="
                       f"{[s['exhausted_code'] for s in snapshots if not s['healthy']]}",
            "executed": False,
            "http_status": 200,
            "referenced": {"source": "supervisor.budget.BudgetGovernor.snapshot"},
        }
        return outcome, snapshots, None

    # ------------------------------------------------------------------ #
    # gateway tools -- requests, never commands
    # ------------------------------------------------------------------ #
    def _tool_jcc_pause(self, spec, args, principal) -> Tuple[Dict[str, Any], Any, Any]:
        return self._gateway(spec, args, principal, DirectiveAction.PAUSE)

    def _tool_jcc_steer(self, spec, args, principal) -> Tuple[Dict[str, Any], Any, Any]:
        return self._gateway(spec, args, principal, DirectiveAction.STEER)

    def _tool_jcc_retry(self, spec, args, principal) -> Tuple[Dict[str, Any], Any, Any]:
        return self._gateway(spec, args, principal, DirectiveAction.RETRY)

    def _gateway(
        self, spec, args, principal, action: DirectiveAction
    ) -> Tuple[Dict[str, Any], Any, Any]:
        session_id = args["session_id"]
        from .gates import GatewayRequest

        # 0) The control token gates the real endpoint. Refuse BEFORE issuing
        #    any request, so a missing token cannot reach the API at all.
        refusal = self._require_token()
        if refusal:
            raise refusal

        # 1) Read the session (a READ) so the policy gate can arbitrate against
        #    the assessment the plane already recorded. Read-only I/O only.
        snapshot_result = self._request(
            "GET", f"/api/sessions/{session_id}", spec, token=False
        )
        snap_refusal = self._map_status(snapshot_result, spec, spec.name)
        if snap_refusal:
            raise snap_refusal

        request = GatewayRequest(
            tool=spec.name,
            session_id=session_id,
            requested_action=action,
            principal=principal,
            session_snapshot=snapshot_result.payload or {},
            endpoint=spec.endpoint,
            arguments=dict(args),
        )

        if self.gateway is None:
            raise R.refusal(
                R.CODE_GATE_UNAVAILABLE,
                "no gateway is wired: the request was not evaluated, so it is not "
                "sent to the control plane",
                tool=spec.name,
                gate=None,
                session_id=session_id,
            )
        verdict = self.gateway.evaluate(request)
        if not verdict.allowed:
            raise verdict.refusal()

        # 2) All five gates passed. Forward the REQUEST to the real endpoint,
        #    which is the only writer (and which still applies its own
        #    ``require_control_token``). Nothing is executed here.
        result = self._request(
            "POST", spec.endpoint, spec, token=True, json_body={"session_id": session_id}
        )
        refusal = self._map_status(result, spec, spec.name)
        if refusal:
            raise refusal
        payload = result.payload or {}
        directive = verdict.directive
        referenced = {
            "directive_id": getattr(directive, "directive_id", None),
            "action": getattr(directive, "action", None),
            "signature_ref": signature_ref(getattr(directive, "signature", None)),
            "signed": bool(getattr(directive, "signed", False)),
            "requires_human": bool(getattr(directive, "requires_human", False)),
        }
        outcome = {
            "status": "ok",
            "summary": (
                f"{spec.name} forwarded to {spec.endpoint} as a REQUEST for "
                f"{action.value}; the control plane recorded directive "
                f"{payload.get('directive_id')} (acted={payload.get('acted')}, "
                f"reason={payload.get('actuation_reason')})"
            ),
            "executed": False,
            "http_status": result.status,
            "referenced": referenced,
            "gates_passed": list(verdict.details.keys()),
        }
        return outcome, payload, directive
