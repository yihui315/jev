"""Mission runtime — the bridge that makes a Mission actually RUN (v1.3 Wave 3).

THE HOLE THIS CLOSES
--------------------
Wave 1 gave the control plane a Mission domain: a mission can be created,
transitioned through a 10-state machine, and given an ``AgentRun``. But nothing
in the domain connected a Mission to the AEP telemetry pipeline. The domain could
say ``RUNNING`` while the engine's session store, assessment, policy, directive
and audit tape all remained empty -- a mission that claims to run with no
evidence is exactly the class of lie this project exists to prevent.

This module is the ONE place that joins the two halves:

    Mission (domain)  --launch-->  AgentRun + Session (AEP)  -->  engine.ingest
                                      |
                                      +--> assessment -> policy -> directive
                                           -> audit -> SSE broadcast
                                      |
                                      +--> back-reference: mission.session_id
                                           run.session_id, run.status

Design rules (all enforced here, none by convention):

1. **The runtime never fabricates an AEP envelope.** It builds the envelope from
   a *real adapter* (``jcc_core.adapters``) or from a real launch driver's
   output. A synthetic "hello I am running" heartbeat is a hard failure of the
   gate that drives this, not something the runtime will paper over.

2. **The runtime never actuates.** It has no reference to an actuator. It cannot
   dispatch, sign, or execute. ``actuation`` stays False by construction because
   the seam is simply not reachable from this module.

3. **Fail-closed, and the failure is recorded.** If the adapter cannot normalise
   a launch event, the run is marked FAILED and the mission goes to FAILED (or
   NEEDS_ATTENTION when a human is required). It does not silently stay RUNNING.

4. **A refused transition is a refusal.** ``launch()`` walks the state machine
   step by step and stops at the first refusal; it never skips a state to "make
   it work".

5. **Identity is the adapter's.** The session id in the AEP envelope is the one
   the adapter derived from the real agent event. The runtime never invents a
   session id and then claims telemetry arrived under it.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .adapters import adapter_for
from .adapters._common import UnidentifiableEvent
from .domain import (
    AgentRun,
    AttentionKind,
    DomainStore,
    Mission,
    MissionStatus,
)
from .redaction import redact

#: The states a launch walks, in order. Mirrors the mission state machine:
#: DRAFT -> READY -> STARTING -> RUNNING. Kept as a literal list (not derived
#: from MISSION_TRANSITIONS) so that a change to the machine that would let a
#: launch skip a state is a visible diff in THIS file too, not a silent
#: loosening that only the transition table knows about.
LAUNCH_SEQUENCE: List[MissionStatus] = [
    MissionStatus.READY,
    MissionStatus.STARTING,
    MissionStatus.RUNNING,
]

#: Which agents the product can actually launch today. The mission studio reads
#: the same fact from the frontend; this constant is the server-side truth.
LAUNCHABLE_AGENTS = ("workbuddy",)


class MissionRuntimeError(RuntimeError):
    """A launch could not complete. Always carries the step it died on."""

    def __init__(self, step: str, detail: str) -> None:
        super().__init__("%s: %s" % (step, detail))
        self.step = step
        self.detail = detail


class MissionRuntime:
    """Drive a Mission from DRAFT to RUNNING against the real AEP pipeline.

    Constructed with the domain store and the core engine. It is deliberately
    NOT constructed with an actuator: there is no actuation seam in this class.
    """

    def __init__(self, domain: DomainStore, engine: Any, *, adapter_lookup: Any = None) -> None:
        self.domain = domain
        self.engine = engine
        # The adapter lookup is the ONLY source of normalisation. A caller may
        # inject one (tests use the real ``adapter_for``; a future Wave may pass
        # a resolver with extra adapters), but there is no code path here that
        # builds an envelope without one.
        self._adapter_lookup = adapter_lookup or adapter_for

    # -- launch ------------------------------------------------------------- #

    def launch(self, mission_id: str, *, event: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Create a real AgentRun + Session, walk the state machine, ingest AEP.

        ``event`` is the RAW agent event a launch driver captured (e.g. the
        WorkBuddy hook JSON, or the launch driver's own session record). It is
        handed to the adapter, which normalises it into an AEP envelope. When
        ``event`` is None the runtime will not invent one -- it raises, because a
        launch with no telemetry is not a launch this product is allowed to
        report as successful.

        Returns a structured result; never raises for a *refused transition*
        (that is a legitimate negative outcome), only for a *programming* error
        (unknown mission, unusable event).
        """
        mission = self.domain.get_mission(mission_id)
        if mission is None:
            raise MissionRuntimeError("resolve", "unknown mission %r" % mission_id)

        if mission.agent not in LAUNCHABLE_AGENTS:
            raise MissionRuntimeError(
                "preflight",
                "agent %r is not launchable by this runtime (launchable: %s)"
                % (mission.agent, ", ".join(LAUNCHABLE_AGENTS)),
            )

        result: Dict[str, Any] = {
            "mission_id": mission_id,
            "agent": mission.agent,
            "steps": [],
            "run_id": None,
            "session_id": None,
            "ingested": False,
            "ok": False,
        }

        # -- step 0: resolve the real event BEFORE creating any run ---------- #
        # A launch with no usable event must leave NO orphaned AgentRun behind.
        # An earlier version created the run first and then bailed, which left a
        # dead run attached to a DRAFT mission (a Wave 3 gate finding: the runs
        # endpoint returned 2 rows for one launch). Resolve the event first, and
        # only open a run once there is something real to run on.
        if event is None:
            detail = ("launch requires a real agent event; refusing to fabricate "
                      "an AEP envelope (a launch with no telemetry is not a launch)")
            result["steps"].append({"step": "normalize", "ok": False, "detail": detail})
            result["error"] = detail
            self._mark_needs_attention(mission_id, detail)
            return result

        try:
            adapter = self._adapter_lookup(mission.agent)
        except Exception as exc:  # noqa: BLE001
            detail = "adapter lookup for agent %r raised: %s" % (mission.agent, exc)
            result["steps"].append({"step": "normalize", "ok": False, "detail": detail})
            result["error"] = detail
            self._mark_needs_attention(mission_id, detail)
            return result
        if adapter is None:
            detail = "no adapter registered for agent %r" % mission.agent
            result["steps"].append({"step": "normalize", "ok": False, "detail": detail})
            result["error"] = detail
            self._mark_needs_attention(mission_id, detail)
            return result

        import asyncio

        try:
            envelope = asyncio.run(adapter.normalize(redact_event(event)))
        except UnidentifiableEvent as exc:
            # The adapter is allowed to refuse an event it cannot identify. That
            # refusal is a fact about the event, and it must surface -- not be
            # replaced by a fabricated session id.
            detail = "adapter refused the event: %s" % exc
            result["steps"].append({"step": "normalize", "ok": False, "detail": detail})
            result["error"] = detail
            self._mark_needs_attention(mission_id, detail)
            return result
        except Exception as exc:  # noqa: BLE001
            detail = "adapter raised: %s" % exc
            result["steps"].append({"step": "normalize", "ok": False, "detail": detail})
            result["error"] = detail
            self._mark_needs_attention(mission_id, detail)
            return result

        session_id = str(envelope.session_id)
        result["session_id"] = session_id
        result["steps"].append(
            {"step": "normalize", "ok": True,
             "detail": "session_id=%s event_id=%s" % (session_id, envelope.event_id)})

        # -- step 1: NOW open the AgentRun (the unit that owns a Session) ---- #
        try:
            run = self.domain.create_run(
                mission_id, mission.agent, connection_ref=mission.connection_ref)
        except ValueError as exc:
            result["steps"].append({"step": "create_run", "ok": False, "detail": str(exc)})
            result["error"] = str(exc)
            return result
        result["run_id"] = run.run_id
        result["steps"].append({"step": "create_run", "ok": True, "detail": run.run_id})

        # -- step 2: bind the Session to the mission + run BEFORE the walk --- #
        # The binding happens first so that a later refusal leaves a mission that
        # names the run it tried, rather than an anonymous failure.
        self.domain.attach_session(mission_id, session_id)
        self.domain.attach_run_session(run.run_id, session_id)


        # -- step 3: walk the state machine, one legal step at a time -------- #
        for target in LAUNCH_SEQUENCE:
            current = self.domain.get_mission(mission_id)
            if current is None:  # pragma: no cover -- deleted mid-launch
                raise MissionRuntimeError("transition", "mission vanished mid-launch")
            if current.status == target:
                result["steps"].append(
                    {"step": "transition:%s" % target.value, "ok": True,
                     "detail": "already at %s" % target.value})
                continue
            try:
                self.domain.transition_mission(
                    mission_id, target, reason="mission runtime launch")
            except ValueError as exc:
                detail = str(exc)
                result["steps"].append(
                    {"step": "transition:%s" % target.value, "ok": False,
                     "detail": detail})
                self._fail_run(mission_id, run.run_id, "transition_refused", detail)
                result["error"] = detail
                return result
            result["steps"].append(
                {"step": "transition:%s" % target.value, "ok": True, "detail": ""})

        # -- step 4: ingest the REAL envelope through the engine ------------- #
        try:
            ingest_out = self.engine.ingest(envelope)
        except Exception as exc:  # noqa: BLE001
            detail = "engine.ingest raised: %s" % exc
            result["steps"].append({"step": "ingest", "ok": False, "detail": detail})
            self._fail_run(mission_id, run.run_id, "ingest_error", detail)
            result["error"] = detail
            return result

        result["ingested"] = True
        result["ingest"] = ingest_out
        result["steps"].append(
            {"step": "ingest", "ok": True,
             "detail": "action=%s directive=%s"
                       % (ingest_out.get("action"),
                          (ingest_out.get("directive") or {}).get("directive_id"))})

        # -- step 5: record the run as LAUNCHED ------------------------------ #
        self.domain.mark_run_launched(run.run_id)
        result["ok"] = True
        return result

    # -- helpers ------------------------------------------------------------ #

    def _mark_needs_attention(self, mission_id: str, detail: str) -> None:
        """Flag a mission whose launch could not start, without inventing a run.

        Used when the failure happens BEFORE any AgentRun is opened (no event,
        no adapter, an unidentifiable event). The mission must not look RUNNING
        and must not carry a dead run; it goes to NEEDS_ATTENTION so a human sees
        the exact reason. A DRAFT mission has no legal path to NEEDS_ATTENTION, so
        it simply stays DRAFT -- which is the honest state for work that never
        started.
        """
        current = self.domain.get_mission(mission_id)
        if current is None:
            return
        if current.status in (MissionStatus.RUNNING, MissionStatus.PAUSED,
                              MissionStatus.VERIFYING,
                              MissionStatus.NEEDS_ATTENTION):
            try:
                self.domain.transition_mission(
                    mission_id, MissionStatus.NEEDS_ATTENTION,
                    reason="launch failed: %s" % detail,
                    attention_kind=AttentionKind.AGENT_STUCK,
                    attention_reason=detail,
                )
            except Exception:  # noqa: BLE001 -- never mask the original failure
                pass

    def _fail_run(self, mission_id: str, run_id: str, reason: str, detail: str) -> None:
        """Mark the run failed AND decide the mission's next honest state.

        A launch failure is not automatically terminal: when the caller can still
        fix something (agent not launchable, no telemetry) the mission goes to
        NEEDS_ATTENTION so a human sees it; a hard error goes to FAILED. Both are
        recorded -- the one thing that must never happen is a mission sitting in
        RUNNING with a dead run attached.

        BUT the mission state is only changed when the failure is the mission's
        problem. If the mission already has ANOTHER live run -- e.g. a re-launch
        refused by the state machine while the first run is still healthy -- then
        downgrading the whole mission to NEEDS_ATTENTION would be wrong: the work
        is fine, only the second attempt failed. In that case only THIS run is
        closed and the mission keeps its state.
        """
        try:
            self.domain.end_run(run_id, reason=reason)
        except Exception:  # noqa: BLE001 -- never mask the original failure
            pass
        current = self.domain.get_mission(mission_id)
        if current is None:
            return
        if self._has_other_live_run(mission_id, excluding=run_id):
            # The mission is still backed by a healthy run; do not touch it.
            return
        # Mission states that can reach NEEDS_ATTENTION directly.
        direct_attention = current.status in (
            MissionStatus.RUNNING, MissionStatus.NEEDS_ATTENTION,
            MissionStatus.PAUSED, MissionStatus.VERIFYING,
        )
        try:
            if direct_attention:
                self.domain.transition_mission(
                    mission_id, MissionStatus.NEEDS_ATTENTION,
                    reason="launch failed: %s" % detail,
                    attention_kind=AttentionKind.AGENT_STUCK,
                    attention_reason=detail,
                )
            elif current.status == MissionStatus.STARTING:
                self.domain.transition_mission(
                    mission_id, MissionStatus.FAILED,
                    reason="launch failed: %s" % detail)
            elif current.status == MissionStatus.READY:
                # READY cannot go straight to FAILED; return it to DRAFT so it is
                # visibly un-launched rather than stranded.
                self.domain.transition_mission(
                    mission_id, MissionStatus.DRAFT,
                    reason="launch failed: %s" % detail)
        except Exception:  # noqa: BLE001 -- a refused cleanup must not mask the cause
            pass

    def _has_other_live_run(self, mission_id: str, *, excluding: str) -> bool:
        """True when the mission has a RUNNING run other than ``excluding``."""
        try:
            runs = self.domain.list_runs(mission_id)
        except Exception:  # noqa: BLE001
            return False
        for r in runs:
            if r.get("run_id") == excluding:
                continue
            if r.get("status") == MissionStatus.RUNNING.value and r.get("session_id"):
                return True
        return False

    # -- observation -------------------------------------------------------- #

    def workload(self, mission_id: str) -> Dict[str, Any]:
        """Live workload summary for a mission: the AEP telemetry it produced.

        This is what the World/Home surfaces read to show a mission as *actually*
        working. It is derived from the engine's session store, never from the
        mission row -- a mission that claims RUNNING with no session telemetry
        reports zero live events, which is the honest answer.
        """
        mission = self.domain.get_mission(mission_id)
        if mission is None:
            raise MissionRuntimeError("workload", "unknown mission %r" % mission_id)

        runs = self.domain.list_runs(mission_id)
        session_ids = [r["session_id"] for r in runs if r.get("session_id")]
        if mission.session_id and mission.session_id not in session_ids:
            session_ids.append(mission.session_id)

        sessions = []
        for sid in session_ids:
            sess = self.engine.store.get_session(sid)
            if sess is None:
                sessions.append({"session_id": sid, "known": False})
                continue
            d = sess.model_dump(mode="json") if hasattr(sess, "model_dump") else dict(sess)
            d["known"] = True
            sessions.append(d)

        return {
            "mission_id": mission_id,
            "mission_status": mission.status.value,
            "agent": mission.agent,
            "run_count": len(runs),
            "runs": runs,
            "session_ids": session_ids,
            "sessions": sessions,
            "live_session_count": sum(1 for s in sessions if s.get("known")),
            # Explicit: this runtime cannot actuate. Read by the UI as proof.
            "actuation": False,
            "observed_at": time.time(),
        }


def redact_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """Key-name redact a raw launch event before it reaches the adapter.

    The engine redacts again at the ingest chokepoint (defense in depth), but a
    raw hook event can carry a token under an unexpected key. Redacting here
    means the adapter -- and anything that logs its input -- never holds a
    secret, even transiently.
    """
    out = redact(event)
    return out if isinstance(out, dict) else {"_redacted": out}
