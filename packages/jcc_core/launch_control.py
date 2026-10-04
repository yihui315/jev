"""Mission launch control plane (v1.3 Wave 9.2).

THE HOLE THIS CLOSES
--------------------
Wave 9.1 built the Host Launcher (``jcc_core.host_launcher``): it can verify a
signed launch directive, resolve an allowlisted command template and spawn a
real WorkBuddy workload. **Nothing called it.** There was no way for a human to
go from "I want this mission to run" to an actual process, so the product's
central promise -- *type a goal, pick an agent, press Start Mission, watch it
work* -- ended at a database row.

This module is the control plane that joins the two ends:

    operator POST (authenticated)
        -> Mission + Project resolved from the domain store
        -> LaunchIntent (bound to mission / project-root / adapter / template)
        -> signed directive (v2 form, short TTL, single use, nonce)
        -> nine checks            (HostLauncher.verify)
        -> spawn                  (HostLauncher.launch)
        -> child's OWN NDJSON     (HostLauncher.read_stream + classify_stream)
        -> AEP ingest, iff the child produced a real session id
        -> journal entry          (durable, append-only, no fabricated rows)

WHAT THIS IS NOT
----------------
* **Not an actuation gate.** ``AUTONOMOUS_ACTUATION`` stays False. No directive
  produced by policy can reach this module: the capability it honours,
  :data:`~jcc_core.host_launcher.CAPABILITY_USER_LAUNCH`, is a *different name*
  from anything the autonomous controller uses, and
  :meth:`LaunchController.request` is only reachable through an authenticated,
  human-initiated POST. ``user_approved`` is set by THIS code path as a
  statement of fact about how the request arrived -- it is never read from a
  request body, so a caller cannot approve itself.

* **Not a command builder.** The goal a user types is never joined into a
  string and never becomes argv[0]. It occupies exactly one argv slot that the
  template reserves for it; the head is resolved from the host environment by
  the template's own resolver. See ``host_launcher`` module docstring.

* **Not a claim.** ``capability()`` measures what this host can do and reports
  ``UNKNOWN`` when it has not measured -- an unmeasured capability is never
  rendered green.

IDENTITY RULE
-------------
A session id is only ever taken from the child's own output. When the child
exits without emitting one, there is no session and no AEP event: the refusal is
recorded, the mission is NOT walked to RUNNING, and an approval is raised so the
operator sees the exact reason in their inbox. Inventing an id (e.g. reusing the
mission id we happened to pass as ``--session-id``) would make the engine hold a
session that no process ever reported.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .host_launcher import (
    CAPABILITY_USER_LAUNCH,
    DEFAULT_COLLECT_TIMEOUT_S,
    DEFAULT_MODEL,
    LAUNCH_TEMPLATES,
    HostLauncher,
    LaunchIntent,
    LaunchTemplate,
    build_head,
    template_registry_digest,
)
from .schemas import Directive, DirectiveAction
from .signing import (
    REASON_OK,
    SIGNING_FORM_V2,
    DirectiveSigner,
    load_signing_key,
    sign_directive,
)

#: The canonical-payload policy-table token. One definition; the signer and the
#: verifier both read it off the directive rather than each supplying their own.
POLICY_TABLE_VERSION = "p3.0.0"

#: Policy version a launch directive is bound to. The engine reports the same
#: string on ``/api/health``; the launcher refuses a directive whose
#: ``policy_version`` differs from the one the intent was previewed under, so a
#: preview taken before a policy change cannot be committed after it.
DEFAULT_POLICY_VERSION = "p3.0.0"

#: Executability of a launch, MEASURED on this host. ``UNKNOWN`` is never green.
EXEC_LOCAL_SPAWN_READY = "LOCAL_SPAWN_READY"
EXEC_HOST_ACTIVATION_PENDING = "HOST_ACTIVATION_PENDING"
EXEC_UNKNOWN = "UNKNOWN"

#: The audit steps the launch order mandates, frozen so a reader can grep them
#: and a test can assert the ORDER rather than merely their presence.
STEP_REQUESTED = "LAUNCH_REQUESTED"
STEP_ACCEPTED = "LAUNCH_ACCEPTED"
STEP_PROCESS_STARTED = "PROCESS_STARTED"
STEP_NATIVE_SESSION = "NATIVE_SESSION_DISCOVERED"
STEP_AEP_FIRST = "AEP_FIRST_EVENT"
STEP_ORDER = (STEP_REQUESTED, STEP_ACCEPTED, STEP_PROCESS_STARTED,
              STEP_NATIVE_SESSION, STEP_AEP_FIRST)

#: Approval kind raised when a launch could not reach a real session. It is a
#: HUMAN task ("the host needs activating"), not an agent effect.
APPROVAL_HOST_ACTIVATION = "LAUNCH_HOST_ACTIVATION"

#: Adapter identity per agent. The adapter registry is keyed by agent name, so
#: the default is the agent itself; a mission may name its own connection.
ADAPTER_BY_AGENT = {"workbuddy": "workbuddy"}

#: The ONLY request-body value that reaches a command template is the model
#: name. It lands in the ``--model`` flag slot of an argv VECTOR (never a shell),
#: but a value is still a value: a name carrying whitespace, a path separator, a
#: quote or a leading dash could be read as another flag by the child's own
#: parser. So it is matched against a narrow allowlist alphabet and refused
#: otherwise -- an unvalidated string is not made safe by the absence of a shell.
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")


class LaunchControlError(RuntimeError):
    """A launch request could not even be BUILT. Carries a machine code."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__("%s%s" % (code, (": " + detail) if detail else ""))
        self.code = code
        self.detail = detail


# --------------------------------------------------------------------------- #
# Building the bound intent and the signed directive
# --------------------------------------------------------------------------- #
def default_measure_path(state_store_db_path: str) -> Path:
    """Where the last MEASURED launch outcome is kept.

    Next to the state store rather than in the repo: it is runtime evidence, not
    a product artifact, and a measurement that lived in version control would be
    a claim about a host rather than a reading from one.
    """
    return Path(state_store_db_path).parent / "launch_measure.json"


def template_for_agent(agent: str,
                       templates: Optional[Dict[str, LaunchTemplate]] = None
                       ) -> Optional[LaunchTemplate]:
    """The FIRST allowlisted template for ``agent``, or None. Registry order wins.

    Deterministic on purpose: ``dict`` insertion order is stable, and a launcher
    that picked a template by, say, shortest id would silently change behaviour
    when a new template was added.
    """
    reg = LAUNCH_TEMPLATES if templates is None else templates
    for tid in reg:
        if reg[tid].agent == agent:
            return reg[tid]
    return None


def build_intent(mission: Any, project: Any, *, template: LaunchTemplate,
                 policy_version: str = DEFAULT_POLICY_VERSION,
                 capability_version: str = "",
                 model: str = DEFAULT_MODEL,
                 project_root: Optional[str] = None) -> LaunchIntent:
    """Bind a Mission to a launchable command. Raises if anything is missing.

    Every field the launcher checks is derived from the domain objects, not from
    caller input: ``project_root`` is the project's own root (or the mission's
    working directory as a documented fallback), and ``adapter_id`` is the
    adapter registered for the mission's agent. A caller may override the root
    only to *narrow* it to a subdirectory of the project -- a launch must not be
    pointable at an arbitrary directory.

    ``capability_version`` names the template REGISTRY that will execute, and the
    caller passes the launcher's own digest. It is deliberately NOT computed here
    from the single template: a digest of one template is not the registry the
    launch runs under, and binding a directive to the wrong anchor would make the
    anchor decorative.
    """
    if mission is None:
        raise LaunchControlError("MISSION_NOT_FOUND")
    if project is None:
        raise LaunchControlError("PROJECT_NOT_FOUND", mission.project_id)

    root = project_root or project.root_path or mission.working_directory or ""
    if not root:
        raise LaunchControlError(
            "PROJECT_ROOT_MISSING",
            "project %s has no root_path and mission %s has no working_directory"
            % (project.project_id, mission.mission_id),
        )

    adapter_id = ADAPTER_BY_AGENT.get(mission.agent, mission.agent)
    return LaunchIntent(
        mission_id=mission.mission_id,
        project_id=project.project_id,
        agent_id=mission.agent,
        adapter_id=adapter_id,
        project_root=str(root),
        template_id=template.template_id,
        goal=mission.goal or mission.title or "mission",
        nonce=uuid.uuid4().hex,
        user_approved=True,      # fact about THIS path: authenticated human POST
        policy_version=policy_version,
        capability_version=capability_version,
        model=model or DEFAULT_MODEL,
    )


def sign_launch_directive(intent: LaunchIntent, *, signer: DirectiveSigner,
                          policy_table_version: str = POLICY_TABLE_VERSION,
                          human_approval: str = "operator:control-token",
                          ttl_s: Optional[float] = None) -> Directive:
    """Mint the single-use, mission-scoped directive a launch executes against.

    The directive is bound four ways, and every binding is INSIDE the signed
    bytes: the mission (``session_id``), the authority
    (``params['capability']``), the policy era (``policy_version``) and the
    registry (``evidence_hash`` covers ``capability_version``). It is short-lived
    and carries a nonce, so it is neither replayable nor usable after a policy or
    registry change.
    """
    if not signer.available:
        raise LaunchControlError(
            "NO_SIGNING_KEY",
            "signing key absent; a launch directive cannot be minted (fail-closed)",
        )
    issued = float(intent.issued_at)
    ttl = float(ttl_s) if ttl_s is not None else max(1.0,
                                                    float(intent.expires_at) - issued)
    d = Directive(
        directive_id="dir-launch-%s" % uuid.uuid4().hex[:16],
        session_id=intent.mission_id,
        timestamp=issued,
        action=DirectiveAction.CONTINUE,
        target_actuator="host_launcher:%s" % intent.template_id,
        params={
            "capability": CAPABILITY_USER_LAUNCH,
            "budget_exhausted": False,
            "template_id": intent.template_id,
            "agent_id": intent.agent_id,
            "adapter_id": intent.adapter_id,
            "project_id": intent.project_id,
            "capability_version": intent.capability_version,
        },
        policy_version=intent.policy_version,
        policy_table_version=policy_table_version,
        signed=True,
        evidence_hash=intent.evidence_hash(),
        issued_at=issued,
        ttl_s=ttl,
        nonce=intent.nonce,
        human_approval=human_approval,
        scope="mission:%s|project_root:%s|adapter:%s" % (
            intent.mission_id, intent.project_root, intent.adapter_id),
        shadow=True,
    )
    d.signature = sign_directive(d, signer.key, form=SIGNING_FORM_V2)
    d.signing_alg = "hmac-sha256"
    d.signature_status = "signed_verified"
    d.signature_verified = bool(signer.verify(d))
    return d


def session_id_from_stream(events: List[Dict[str, Any]]) -> Optional[str]:
    """The child's OWN session id, or None. Never inferred, never defaulted.

    The headless CLI emits a ``system`` init record carrying the session id it
    actually ran under. If that record is absent (or the run died before it was
    written) then no session exists and this function says so.
    """
    for e in events:
        if str(e.get("type") or "") != "system":
            continue
        sid = e.get("session_id") or e.get("sessionId")
        if isinstance(sid, str) and sid.strip():
            return sid.strip()
    return None


def receipt_from_stream(intent: LaunchIntent, *, events: List[Dict[str, Any]],
                        pid: Optional[int], result: Dict[str, Any],
                        started_at: float, ended_at: float) -> Optional[Dict[str, Any]]:
    """A REAL session record derived from the child's own output, or None.

    Returns None unless the child reported a session id of its own. That is the
    whole point: a launch that produced no identifiable session has no AEP event
    to ingest, and manufacturing one from the id we passed on the command line
    would put a phantom session in the engine's store.
    """
    sid = session_id_from_stream(events)
    if not sid:
        return None
    init = next((e for e in events if str(e.get("type") or "") == "system"), {})
    return {
        "_source": "session_file",
        "sessionId": sid,
        "pid": pid,
        "kind": "jcc-launcher",
        "mode": "headless",
        "cwd": init.get("cwd") or intent.project_root,
        "version": init.get("version") or init.get("cli_version"),
        "os": init.get("os"),
        "arch": init.get("arch"),
        "startedAt": started_at,
        "lastHeartbeat": ended_at,
        # Provenance of the DISCOVERY, so nothing downstream has to guess whether
        # this session predated the launch.
        "_jcc_launch": {
            "mission_id": intent.mission_id,
            "template_id": intent.template_id,
            "args_hash": result.get("args_hash"),
            "pid": pid,
            "discovered_by": "host_launcher_stream",
        },
    }


# --------------------------------------------------------------------------- #
# The controller
# --------------------------------------------------------------------------- #
class LaunchController:
    """Turn an authenticated human request into a run, and record what happened.

    Constructed with the domain store, the mission runtime and (optionally) a
    signer, a launcher and an audit sink. It holds no actuator and can reach no
    actuator: the only effect it has on the world is ``HostLauncher.launch``.
    """

    def __init__(self, domain: Any, runtime: Any, *,
                 signer: Optional[DirectiveSigner] = None,
                 launcher: Optional[HostLauncher] = None,
                 policy_version: str = DEFAULT_POLICY_VERSION,
                 measure_path: Optional[Path] = None,
                 journal_path: Optional[Path] = None,
                 audit: Optional[Callable[[Dict[str, Any]], None]] = None,
                 collect_timeout_s: float = DEFAULT_COLLECT_TIMEOUT_S,
                 scope_base: Optional[str] = None) -> None:
        self.domain = domain
        self.runtime = runtime
        self.policy_version = policy_version
        self.signer = signer or DirectiveSigner(load_signing_key(), form=SIGNING_FORM_V2)
        # CR-003: the scope base reaches the launcher this way, so the configured
        # boundary is the one enforced. Constructed WITHOUT it here would leave
        # `launcher.project_scope_base` in the config file as a decorative key --
        # readable, plausible, and inert -- which is the failure mode this project
        # treats as worse than an absent control.
        self.launcher = launcher or HostLauncher(signer=self.signer,
                                                 scope_base=scope_base)
        self.measure_path = Path(measure_path) if measure_path else None
        self.journal_path = Path(journal_path) if journal_path else None
        self.audit = audit
        self.collect_timeout_s = float(collect_timeout_s)

    # -- capability ---------------------------------------------------------- #

    def capability(self, agent: str = "workbuddy") -> Dict[str, Any]:
        """What this host can ACTUALLY do with a launch. Measured, not asserted.

        Three states, and the third is never green:

        * ``LOCAL_SPAWN_READY`` -- the template's head resolved here, so an argv
          can be built and a process started.
        * ``HOST_ACTIVATION_PENDING`` -- a launch was attempted and the CLI
          refused for lack of credentials. Measured on this host.
        * ``UNKNOWN`` -- not measured, or no template can be resolved at all.

        ``end_to_end_verified`` is True only when a real run produced a real
        session id. Anything less stays False, including the case where the host
        is perfectly able to spawn but has never completed a turn.
        """
        tpl = template_for_agent(agent, self.launcher.templates)
        head: Optional[List[str]] = None
        problem: Optional[str] = None
        if tpl is None:
            problem = "NO_TEMPLATE_FOR_AGENT"
        else:
            head, problem = build_head(tpl)

        spawn_ready = bool(head) and not problem
        last = self._read_measurement()
        if not spawn_ready:
            executability = EXEC_UNKNOWN
        elif last and last.get("classification") == "AUTH_REQUIRED":
            executability = EXEC_HOST_ACTIVATION_PENDING
        elif last is None:
            executability = EXEC_LOCAL_SPAWN_READY
        else:
            executability = EXEC_LOCAL_SPAWN_READY

        return {
            "schema": "jcc/v1.3-launch-capability/1",
            "capability": CAPABILITY_USER_LAUNCH,
            "agent": agent,
            "template_id": (tpl.template_id if tpl else None),
            "capability_version": self.launcher.registry_digest,
            "spawn_ready": spawn_ready,
            "spawn_problem": problem,
            "resolved_head_len": len(head) if head else 0,
            "signing_available": bool(self.signer.available),
            "executability": executability,
            "end_to_end_verified": bool(
                last and last.get("classification") == "LAUNCHED"
                and last.get("session_discovered")),
            "last_measured": last,
            "autonomous_actuation": False,
            "note": ("executability is a MEASUREMENT on this host; UNKNOWN is not "
                     "a failure and not a success"),
        }

    # -- request (preview-first) --------------------------------------------- #

    def request(self, mission_id: str, *, commit: bool = False,
                model: str = DEFAULT_MODEL,
                project_root: Optional[str] = None,
                human_approval: str = "operator:control-token") -> Dict[str, Any]:
        """Preview a launch, and -- only when ``commit`` is true -- perform it.

        ``commit=False`` (the default, and what the UI calls first) builds the
        intent, mints the directive, runs the preview and asks ``dry_verify``
        whether the directive WOULD be accepted. Nothing is spawned and the
        nonce is NOT spent, so the same directive can immediately be committed.

        ``commit=True`` re-runs the identical nine checks (this time spending the
        nonce), spawns, waits up to ``collect_timeout_s``, reads the child's own
        stream and classifies it. The classification -- not the mere existence of
        a process -- is what decides whether the mission is walked to RUNNING.
        """
        t0 = time.time()
        model = str(model or DEFAULT_MODEL)
        if not _MODEL_RE.match(model):
            raise LaunchControlError(
                "INVALID_MODEL",
                "model %r is not an accepted model token (allowed: %s)"
                % (model, _MODEL_RE.pattern))
        mission = self.domain.get_mission(mission_id)
        if mission is None:
            raise LaunchControlError("MISSION_NOT_FOUND", mission_id)
        project = self.domain.get_project(mission.project_id)
        if project is None:
            raise LaunchControlError("PROJECT_NOT_FOUND", mission.project_id)

        tpl = template_for_agent(mission.agent, self.launcher.templates)
        if tpl is None:
            raise LaunchControlError(
                "NO_TEMPLATE_FOR_AGENT",
                "no allowlisted launch template for agent %r" % mission.agent)

        intent = build_intent(mission, project, template=tpl,
                              policy_version=self.policy_version,
                              capability_version=self.launcher.registry_digest,
                              model=model, project_root=project_root)
        directive = sign_launch_directive(intent, signer=self.signer,
                                          human_approval=human_approval)
        preview = self.launcher.preview(intent)
        # Two things must BOTH hold before a launch can proceed: the nine checks
        # must pass, AND the command must be constructible on this host (the
        # template head must resolve and the project root must exist). They are
        # computed by the layer that owns each question and then COMPOSED here
        # into one answer -- because a preview that reported checks-ok while the
        # argv could not be built would be exactly the two-answers-to-one-question
        # defect this project bans. When only the command is the blocker, the
        # reason says so rather than reporting a check failure that did not happen.
        would_ok, would_reason = self.launcher.dry_verify(intent, directive)
        preview_problems = [p.get("code") for p in (preview.get("problems") or [])]
        command_ok = bool(preview.get("launchable"))
        would_accept = bool(would_ok and command_ok)
        if not command_ok:
            would_reason = "COMMAND_NOT_BUILDABLE:" + ",".join(str(c) for c in preview_problems)

        out: Dict[str, Any] = {
            "schema": "jcc/v1.3-launch-request/1",
            "mission_id": mission_id,
            "project_id": project.project_id,
            "committed": bool(commit),
            "capability": CAPABILITY_USER_LAUNCH,
            "capability_version": self.launcher.registry_digest,
            "policy_version": self.policy_version,
            "directive": directive.model_dump(mode="json"),
            "preview": preview,
            "checks_ok": bool(would_ok),
            "command_buildable": command_ok,
            "preview_problems": preview_problems,
            "would_accept": would_accept,
            "would_reason": would_reason,
            "steps": [{"step": STEP_REQUESTED, "ok": True,
                       "detail": "template=%s" % tpl.template_id}],
            "actuation": False,
        }
        self._journal({"event": STEP_REQUESTED, "mission_id": mission_id,
                       "template_id": tpl.template_id, "args_hash": preview.get("args_hash"),
                       "would_accept": bool(would_ok), "would_reason": would_reason,
                       "committed": bool(commit), "at": t0})

        if not commit:
            out["elapsed_s"] = round(time.time() - t0, 3)
            out["note"] = ("preview only: nothing spawned, no nonce consumed")
            return out

        # ---- commit -------------------------------------------------------- #
        result = self.launcher.launch(intent, directive)
        out["result"] = result
        out["spawned"] = bool(result.get("spawned"))
        out["pid"] = result.get("pid")
        # The launcher also opens with LAUNCH_REQUESTED -- it is recording that
        # IT was handed a request, which is a different fact from the operator
        # having asked. Both are true, but the ordered step list is a single
        # narrative and the mandated order names LAUNCH_REQUESTED exactly once
        # (it is the first element). So the controller's record stands and the
        # launcher's duplicate is dropped -- not renamed, dropped, because a
        # second name for one step is how a reader loses the order.
        _skip_dup_requested = True
        for s in result.get("steps", []):
            if s.get("step") == STEP_REQUESTED and _skip_dup_requested:
                _skip_dup_requested = False
                continue
            out["steps"].append(s)
        self._journal({"event": STEP_ACCEPTED if result.get("spawned") else "LAUNCH_REFUSED",
                       "mission_id": mission_id, "reason": result.get("reason"),
                       "pid": result.get("pid"), "at": time.time()})

        if not result.get("spawned"):
            out["classification"] = "REFUSED"
            out["reason"] = result.get("reason")
            out["elapsed_s"] = round(time.time() - t0, 3)
            return out

        # Wait, bounded. A child still running at the deadline is reported as
        # still running -- never as finished.
        exited, exit_code = self.launcher.wait(
            int(result["pid"]), timeout_s=self.collect_timeout_s)
        ended_at = time.time()
        events, bad = self.launcher.read_stream(str(result.get("stdout_file") or ""))
        cls = self.launcher.classify_stream(events, exit_code=exit_code)
        cls["exited"] = bool(exited)
        cls["unparseable_lines"] = len(bad)
        out["classification"] = cls["classification"]
        out["stream"] = {
            "event_count": cls["event_count"],
            "event_types": cls["event_types"],
            "unparseable_lines": len(bad),
            "auth_error": cls.get("auth_error"),
            "exited": bool(exited),
            "exit_code": exit_code,
            "stdout_file": result.get("stdout_file"),
            "job_object": result.get("job_object"),
        }
        self._journal({"event": "STREAM_CLASSIFIED", "mission_id": mission_id,
                       "classification": cls["classification"], "pid": result.get("pid"),
                       "at": ended_at})

        # ---- AEP handoff (only with a real identity) ----------------------- #
        # TWO DIFFERENT FACTS, deliberately not collapsed:
        #   * the child reported a session id   -> the session EXISTED
        #   * the child produced a model turn   -> the mission RAN
        # `runtime.launch` walks the mission DRAFT->READY->STARTING->RUNNING, so
        # handing it a stream that only proves "a process started and was refused
        # at authentication" would put a mission in RUNNING having done no work.
        # That is the precise lie this project exists to prevent, and it is why
        # the handoff below is gated on the CLASSIFICATION, not on the session.
        receipt = receipt_from_stream(intent, events=events, pid=result.get("pid"),
                                      result=result, started_at=t0, ended_at=ended_at)
        out["aep"] = {"session_discovered": bool(receipt), "session_id": None,
                      "ingested": False, "classification": None}
        if receipt:
            out["steps"].append({"step": STEP_NATIVE_SESSION, "ok": True,
                                 "detail": "session_id=%s (from the child's own stream)"
                                           % receipt["sessionId"]})
            out["aep"]["session_id"] = receipt["sessionId"]
            self._journal({"event": STEP_NATIVE_SESSION, "mission_id": mission_id,
                           "session_id": receipt["sessionId"], "at": time.time()})
            if cls["classification"] != "LAUNCHED":
                detail = ("not ingested: classification=%s; an unproductive run must "
                          "not walk the mission to RUNNING" % cls["classification"])
                out["steps"].append({"step": STEP_AEP_FIRST, "ok": False, "detail": detail})
                self._journal({"event": STEP_AEP_FIRST, "mission_id": mission_id,
                               "session_id": receipt["sessionId"], "ok": False,
                               "detail": detail, "at": time.time()})
            else:
                try:
                    ingest = self.runtime.launch(mission_id, event=receipt)
                    out["aep"]["ingested"] = bool(ingest.get("ok"))
                    out["aep"]["classification"] = "AEP_INGEST"
                    out["aep"]["runtime"] = ingest
                    if ingest.get("ok"):
                        out["steps"].append({"step": STEP_AEP_FIRST, "ok": True,
                                             "detail": "session_id=%s"
                                                       % receipt["sessionId"]})
                        self._journal({"event": STEP_AEP_FIRST,
                                       "mission_id": mission_id,
                                       "session_id": receipt["sessionId"],
                                       "at": time.time()})
                    else:
                        out["steps"].append({
                            "step": STEP_AEP_FIRST, "ok": False,
                            "detail": str(ingest.get("error") or "runtime refused")})
                except Exception as exc:  # noqa: BLE001 -- surface, never mask
                    out["aep"]["error"] = "%s: %s" % (type(exc).__name__, exc)
                    out["steps"].append({"step": STEP_AEP_FIRST, "ok": False,
                                         "detail": out["aep"]["error"]})
        else:
            out["steps"].append({
                "step": STEP_NATIVE_SESSION, "ok": False,
                "detail": "child reported no session id; no AEP event is fabricated"})
            self._journal({"event": STEP_NATIVE_SESSION, "mission_id": mission_id,
                           "session_id": None, "at": time.time()})

        # A launch that spawned but did not become a running mission is a HUMAN
        # task, not a silent failure. Raise an approval so the blocker lands on
        # the mission. Both halves are covered: a run that never authenticated
        # AND a run that produced output under no identifiable session.
        # The mission's own STATUS is left alone on purpose: it never legally
        # reached RUNNING, and NEEDS_ATTENTION is only reachable from RUNNING, so
        # forcing it would be the same lie by a different route.
        if receipt is None or cls["classification"] != "LAUNCHED":
            self._raise_host_attention(mission_id, cls)

        self._write_measurement({
            "mission_id": mission_id,
            "classification": cls["classification"],
            "session_discovered": bool(receipt),
            "pid": result.get("pid"),
            "auth_error": cls.get("auth_error"),
            "event_types": cls["event_types"],
            "measured_at": ended_at,
            "template_id": tpl.template_id,
            "args_hash": result.get("args_hash"),
        })
        out["elapsed_s"] = round(time.time() - t0, 3)
        if self.audit is not None:
            try:
                self.audit(out)
            except Exception:  # noqa: BLE001 -- an audit sink must not fail a launch
                pass
        return out

    # -- internals ----------------------------------------------------------- #

    def _raise_host_attention(self, mission_id: str, cls: Dict[str, Any]) -> None:
        """Record the exact reason a spawned workload did not become a mission."""
        reason = cls.get("auth_error") or (
            "the launch process produced no model turn (%s)"
            % cls.get("classification"))
        try:
            self.domain.create_approval(
                mission_id, APPROVAL_HOST_ACTIVATION,
                reason=str(reason)[:500],
                requested_by="jcc-launch-control",
                payload={"classification": cls.get("classification"),
                         "exit_code": cls.get("exit_code"),
                         "event_types": cls.get("event_types")},
            )
        except Exception:  # noqa: BLE001 -- never mask the launch result
            pass

    def _read_measurement(self) -> Optional[Dict[str, Any]]:
        if not self.measure_path or not self.measure_path.is_file():
            return None
        try:
            d = json.loads(self.measure_path.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else None
        except Exception:  # noqa: BLE001 -- an unreadable measurement is "not measured"
            return None

    def _write_measurement(self, payload: Dict[str, Any]) -> None:
        if not self.measure_path:
            return
        try:
            self.measure_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.measure_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8", newline="\n")
            os.replace(str(tmp), str(self.measure_path))
        except Exception:  # noqa: BLE001
            pass

    def _journal(self, entry: Dict[str, Any]) -> None:
        """Append-only NDJSON journal of every launch attempt.

        Durable and greppable. It deliberately carries no secret and no goal
        text: the goal is the user's content, and the audit trail only needs to
        name the mission and the binding (``args_hash``).
        """
        if not self.journal_path:
            return
        try:
            self.journal_path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(entry, sort_keys=True, ensure_ascii=False,
                              default=str)
            with open(self.journal_path, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(line + "\n")
        except Exception:  # noqa: BLE001
            pass
