"""The Sentinel: supervisor-plane orchestrator and sole actuation seam owner.

ADR-0008 D-2/D-3/D-7/D-34/D-35/D-41. It subscribes to the engine's observe-only
broadcast, re-arbitrates through the *same* kernel (D-32), signs, dispatches
through the single ``_dispatch`` seam, and appends a hash-chained shadow record.

Invariants enforced here, all falsifiable:
* **Never actuates.** The injected ``DispatchPort`` (P3: ``ShadowActuationPort``)
  always returns ``acted=False``; the directive is recorded with
  ``executable=False``.
* **Budget exhaustion replaces, never suppresses** (D-34): the action becomes
  ESCALATE with a budget blocker, and the record is still written.
* **EXACT_BLOCKER is absorbing** (D-35): once blocked, the action is ESCALATE and
  the directive is never resuming and never executable.
* **Signing is honest** (D-33): no key ⇒ ``signed=False``, never a fake
  signature; ``require_signature`` with no key ⇒ ESCALATE.
* **Store access is PUBLIC-only** (spec §2.6 items 1–2, Errata **E-5**): the
  shadow WRITE goes through the store's **lock-scoped** ``transaction()`` context
  manager — never the lock-free ``connection()`` (which cannot serialise the
  ``SELECT tail → INSERT`` pair, so two appends could fork the chain), and never
  a private ``_conn`` poke (``_conn`` is a *method*, not a connection: the #54
  defect that silently recorded ZERO rows).
* **The budget ceiling is wired, not decorative** (D-5 / F-3, task #73): the
  pre-arbitration gate cannot know the action, so a SECOND gate re-checks with
  the real, arbitrated action and prices it off the ONE cost table. A
  forced-ESCALATE directive is issued and recorded, so it is charged too
  (task #75a) — one charge per issued directive, never two.
* **No silent defaults** (task #74): thresholds come from an explicit source or
  the constructor raises. ``thresholds_source`` records which one supplied them.
* **A directive that was not persisted is NOT a completed directive** (P4 #82
  **S-1**): after the shadow record is appended, durability is checked against
  the very counter that append increments. A lost record returns ``None`` —
  never a ``ShadowDecision`` carrying a settled action, because "ESCALATE, but
  the plane recorded nothing" is the #54 defect wearing a decision.
* **Resume re-runs what was never folded** (P4 #82 **S-2**): "was this event
  evaluated?" is answered from the SAME economic-event folding the store uses,
  and a row the fold did not yield counts as **un-evaluated**.
* **Plane identity is stated, never inferred** (P4 #82 **S-3**): a ``directives``
  row names its plane (``origin``) and demonstrates it (``directive_id``
  prefix). The two must agree, and disagreement fails closed.
* **A lost record cannot be mistaken for a trace** (P4 #82 **S-4**): the return
  value is the failure signal, the counters carry it, and a caller that looks at
  ``persistence_ok`` sees ``False``. The engine's ``_broadcast`` swallows
  subscriber exceptions (``core.py:74-79``), so a failure that is only *logged*
  is a failure that did not happen — nothing here relies on log delivery.
"""
from __future__ import annotations

import logging
import secrets
import time
import uuid
from enum import Enum
from typing import Any, Callable, Dict, Optional

from jcc_core import signing as signing_kernel
from jcc_core import state_store
from jcc_core.decision import Thresholds, ThresholdsConfig
from jcc_core.schemas import Assessment, Directive, DirectiveAction, SessionStatus, ShadowDecision

from . import arbitration, shadow_store
from .ports import DispatchPort, ShadowActuationPort

log = logging.getLogger("jcc.supervisor.sentinel")

#: Default directive TTL when the caller does not supply one (design §3.6.1).
DEFAULT_TTL_S = 120.0

# -- Plane identity (P4 #82 / S-3) ----------------------------------------- #
#
# Two planes write a row to the engine's ``directives`` table for the SAME
# economic event: the engine (``core.py``, ``dir_<session>_<event>``) and this
# plane's charging copy (``BudgetGovernor._persist_directive``,
# ``shadow_<session>_<event>``). They are not two directives — they are the
# engine's decision and this plane's view of it — so they must be counted ONCE,
# and the count must be attributed by ``origin`` rather than inferred.
#
# ``origin`` and the ``directive_id`` prefix are the same fact written twice.
# The pairing is CLOSED and PINNED here: a row that says "engine" while its id
# says ``shadow_`` is a fail-closed bug, because the only alternative is to be
# unable to tell which plane a budget charge belongs to.
SUPERVISOR_ORIGIN = "supervisor"
ENGINE_ORIGIN = "engine"
SHADOW_DIRECTIVE_PREFIX = "shadow_"
ENGINE_DIRECTIVE_PREFIX = "dir_"

#: The complete set of legal (origin -> id prefix) pairings. Adding a plane
#: means editing this dict AND the store's ``_DIRECTIVE_EVENT_PREFIXES``, so the
#: two planes cannot drift apart on the identity they claim.
DIRECTIVE_ORIGIN_PREFIXES = {
    ENGINE_ORIGIN: ENGINE_DIRECTIVE_PREFIX,
    SUPERVISOR_ORIGIN: SHADOW_DIRECTIVE_PREFIX,
}


class BlockerCode(str, Enum):
    NONE = "NONE"
    EXACT_BLOCKER = "EXACT_BLOCKER"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    SIGNING_REQUIRED_UNAVAILABLE = "SIGNING_REQUIRED_UNAVAILABLE"
    AUDIT_CHAIN_BROKEN = "AUDIT_CHAIN_BROKEN"
    CONTRADICTORY_EVIDENCE = "CONTRADICTORY_EVIDENCE"


#: Actions that would "resume" work — never permitted while blocked (D-35).
_RESUMING = frozenset({
    DirectiveAction.CONTINUE, DirectiveAction.STEER,
    DirectiveAction.RETRY, DirectiveAction.FINISH, DirectiveAction.PAUSE,
})


class Sentinel:
    """Observe-only shadow orchestrator; owns ``_dispatch`` (the P4 seam)."""

    def __init__(
        self,
        store: Any,
        *,
        signer: Any,
        budget: Any,
        dispatch_port: Optional[DispatchPort] = None,
        thresholds: Optional[ThresholdsConfig] = None,
        thresholds_resolver: Optional[Callable[[], Any]] = None,
        policy_version: str = "p3.0.0",
        policy_table_version: Optional[str] = None,
        require_signature: bool = False,
        ttl_s: float = DEFAULT_TTL_S,
        clock: Callable[[], float] = time.time,
        nonce_factory: Callable[[], str] = lambda: secrets.token_hex(16),
    ) -> None:
        self.store = store
        self.signer = signer
        self.budget = budget
        self.dispatch_port: DispatchPort = dispatch_port or ShadowActuationPort()
        self.thresholds, self.thresholds_source = self._resolve_thresholds(
            thresholds, thresholds_resolver
        )
        self.policy_version = policy_version
        self.policy_table_version = policy_table_version or arbitration.decision.POLICY_TABLE_VERSION
        self.require_signature = bool(require_signature)
        self.ttl_s = float(ttl_s)
        self._clock = clock
        self._nonce_factory = nonce_factory
        self._memory: list = []
        self._schema_ready = False

        # -- persistence telemetry (task #54 F4) -----------------------------
        # "The shadow plane recorded nothing" must be ASSERTABLE, not inferred
        # from a log line. ``persistence_attempts`` counts records we tried to
        # store; ``persistence_failures`` counts records that are NOT durably
        # stored (a store error, or the in-memory degraded fallback);
        # ``persistence_durable_writes`` counts confirmed writes. A healthy run
        # has ``failures == 0`` AND ``durable_writes == attempts`` AND
        # ``attempts > 0`` — the last clause is what stops the D-40 "every
        # recorded row is inert" check from passing over an EMPTY set.
        self.persistence_attempts = 0
        self.persistence_failures = 0
        self.persistence_durable_writes = 0

    @staticmethod
    def _resolve_thresholds(thresholds, thresholds_resolver):
        """Resolve the threshold surface EXPLICITLY, and FAIL CLOSED if it cannot.

        ``thresholds or ThresholdsConfig()`` (task #74) made a wiring mistake
        — an omitted argument — run the whole plane on kernel-default
        thresholds. That is worse than a crash: the recorded
        ``policy_config_hash`` (D-21) would describe a policy nobody ever chose,
        and every label would be attributed to a threshold set that never ran, so
        the shadow tape would look authoritative while describing a different
        policy. A default is a *choice*; inventing one here is not.

        One of the two sources MUST exist:
        ``"injected"`` — the caller handed a ``ThresholdsConfig``;
        ``"resolver"``  — a resolver produced the surface (parse errors propagate);
        otherwise RAISE at construction, before any directive exists.
        """
        if thresholds is not None:
            if not isinstance(thresholds, ThresholdsConfig):
                # Reject a duck-typed stand-in outright: ``for_agent`` /
                # ``config_hash`` are load-bearing on the record, and a partial
                # object would fail later and far away from this line.
                raise TypeError(
                    "thresholds must be a ThresholdsConfig, got "
                    f"{type(thresholds).__name__}. Pass thresholds=<ThresholdsConfig> "
                    "or thresholds_resolver=<callable>."
                )
            return thresholds, "injected"
        if thresholds_resolver is not None:
            if not callable(thresholds_resolver):
                raise TypeError(
                    f"thresholds_resolver must be callable, got "
                    f"{type(thresholds_resolver).__name__}"
                )
            resolved = thresholds_resolver()
            if isinstance(resolved, ThresholdsConfig):
                return resolved, "resolver"
            # A resolver returning raw config goes through the canonical parser, so
            # a malformed surface fails here rather than silently at first use.
            return ThresholdsConfig.from_config(resolved), "resolver"
        raise ValueError(
            "no thresholds source: pass thresholds=<ThresholdsConfig> or "
            "thresholds_resolver=<callable>. Falling back to the kernel's "
            "module-private defaults is the #74 defect (fail closed, not silent)."
        )

    @property
    def persistence_ok(self) -> bool:
        """True iff every attempted shadow record was durably stored."""
        return (
            self.persistence_failures == 0
            and self.persistence_durable_writes == self.persistence_attempts
        )

    # -- S-3: plane identity is STATED, not inferred ----------------------- #
    @staticmethod
    def directive_plane(directive_id: str, origin: str) -> str:
        """Verify that a ``directives`` row names the plane its id proves (S-3).

        Raises unless ``origin`` is a known plane AND ``directive_id`` carries
        that plane's pinned prefix. The point is narrower than it looks: the
        failure mode being closed is *inferring* the plane from whichever field
        happens to be populated. A ``shadow_`` row written with
        ``origin="engine"`` is not a third plane — it is a charge that will be
        counted against the wrong budget, so it fails closed here rather than
        being reconciled later by somebody reading the table.
        """
        expected = DIRECTIVE_ORIGIN_PREFIXES.get(origin)
        if expected is None:
            raise ValueError(
                f"unknown directive origin {origin!r}; the closed set is "
                f"{sorted(DIRECTIVE_ORIGIN_PREFIXES)}"
            )
        if not str(directive_id).startswith(expected):
            raise RuntimeError(
                f"directive {directive_id!r} claims origin {origin!r} but does not "
                f"carry the pinned prefix {expected!r}. The plane would have to be "
                "INFERRED from the row's shape, which is exactly what S-3 bans; "
                "refusing rather than guessing which budget to charge"
            )
        return origin

    # -- S-2: "was this economic event evaluated?" ------------------------- #
    def evaluated_directive_events(self, session_id: str) -> Dict[str, Any]:
        """Economic events present in the engine's ``directives`` table.

        Folding is NOT re-implemented here. ``state_store._directive_events``
        owns "which row survives for one event" (last written wins, and the
        surviving row is the sentinel's charging record); copying that rule into
        the supervisor plane would give the two planes a second, driftable
        definition of "one event". This folds the store's OWN public reader
        through the store's OWN public ``directive_event_key``, so the key set
        is identical by construction.

        Only EXISTENCE is derived here. Which action a surviving row carries is
        a budget question and is answered by ``StateStore.budget_snapshot``.
        """
        key_fn = getattr(state_store, "directive_event_key", None)
        if not callable(key_fn):
            raise RuntimeError(
                "jcc_core.state_store no longer exposes directive_event_key(); the "
                "event identity the engine's fold uses is gone, so an un-evaluated "
                "event cannot be told apart from an evaluated one (S-2)"
            )
        reader = getattr(self.store, "get_directives", None)
        if not callable(reader):
            raise RuntimeError(
                f"store {type(self.store).__name__} exposes no get_directives(); "
                "directive rows cannot be read back, so nothing can be judged "
                "evaluated (S-2)"
            )
        keys = set()
        for row in reader(session_id=session_id) or []:
            keys.add(key_fn(row["directive_id"], session_id))
        return keys

    def is_event_evaluated(self, session_id: str, directive_id: str) -> bool:
        """True iff the economic event behind ``directive_id`` was evaluated.

        P4 #82 **S-2**. The question is deliberately "is there a row for this
        event's key", not "is there a row for this id": the engine and this
        plane each write their own row for the same event, so answering from the
        id would report a shadow row as evidence the ENGINE evaluated something.

        A row the fold did not yield is UN-EVALUATED — the safe direction. A
        missing event must cause a re-run, never be read as a completed
        evaluation.
        """
        key_fn = getattr(state_store, "directive_event_key", None)
        if not callable(key_fn):
            raise RuntimeError(
                "jcc_core.state_store no longer exposes directive_event_key(); an "
                "un-evaluated event cannot be distinguished from an evaluated one"
            )
        return key_fn(directive_id, session_id) in self.evaluated_directive_events(session_id)

    # -- engine subscriber (D-2) --------------------------------------------
    def on_event(self, out: Dict[str, Any]) -> Optional[ShadowDecision]:
        """Broadcast handler. Must never raise into the engine's loop.

        P4 #82 **S-4**. The engine's ``_broadcast`` ends in a bare
        ``except Exception: pass`` (``core.py:74-79``), so ANY failure raised
        out of here is swallowed with no signal to the ingest caller — an
        exception alone is NOT a visible failure on this path. What makes the
        failure visible is the RETURN VALUE plus the counters: a lost record
        returns ``None`` (never a ``ShadowDecision``), ``persistence_failures``
        is incremented exactly once, and ``persistence_ok`` goes ``False``. A
        caller that treats a non-``None`` return as "the shadow left a trace"
        will therefore be wrong in the only direction that matters — it cannot
        be wrong into a false success.
        """
        try:
            return self._on_event_inner(out)
        except Exception:  # noqa: BLE001 — observe-only: never break ingest
            # Never break ingest (the engine's broadcast also swallows), but do
            # NOT fail silently: a shadow plane that records nothing must be
            # visible — and COUNTED — or the D-40 "acted == false" falsifiers
            # pass VACUOUSLY (zero rows trivially satisfy "every recorded row is
            # inert"). This is the ONE place a lost record is counted on the
            # broadcast path; ``_append_shadow`` deliberately does NOT also count
            # the raise path, so a failure is never double-counted.
            self.persistence_failures += 1
            log.exception("shadow record failed for event_id=%s", out.get("event_id"))
            return None

    def _on_event_inner(self, out: Dict[str, Any]) -> ShadowDecision:
        raw_assessment = out.get("assessment") or {}
        assessment = raw_assessment if isinstance(raw_assessment, Assessment) else Assessment(**raw_assessment)
        agent = getattr(assessment.agent, "value", assessment.agent)
        return self.arbitrate_and_record(
            assessment=assessment,
            event_id=out.get("event_id", ""),
            agent=agent,
            project=out.get("project", ""),
            evidence_hash=out.get("evidence_hash"),
            session_status=out.get("session_status"),
        )

    # -- core orchestration --------------------------------------------------
    def arbitrate_and_record(
        self,
        *,
        assessment: Assessment,
        event_id: str = "",
        agent: Optional[str] = None,
        project: str = "",
        evidence_hash: Optional[str] = None,
        session_status: Optional[str] = None,
    ) -> Optional[ShadowDecision]:
        """Arbitrate one event and record it.

        Returns ``None`` when the record did not become DURABLE (P4 #82 S-1) —
        a lost record is the failure signal, not an absent-but-successful event.
        """
        session_id = assessment.session_id
        agent_name = agent or getattr(assessment.agent, "value", assessment.agent)
        thresholds: Thresholds = self.thresholds.for_agent(agent_name)

        # 1) Fail-closed gates that REPLACE the action with ESCALATE.
        blocker_code = BlockerCode.NONE
        forced_escalate = False
        budget_code: Optional[str] = None

        if self.require_signature and not getattr(self.signer, "available", False):
            forced_escalate = True
            blocker_code = BlockerCode.SIGNING_REQUIRED_UNAVAILABLE

        budget_decision = self.budget.check(session_id, kind="directive", units=0)
        if not budget_decision.allowed:
            forced_escalate = True
            budget_code = budget_decision.code
            if blocker_code is BlockerCode.NONE:
                blocker_code = BlockerCode.BUDGET_EXHAUSTED

        # 2) Arbitration through the ONE kernel.
        result = arbitration.arbitrate(assessment, thresholds)
        action = DirectiveAction.ESCALATE if forced_escalate else result.action

        # 2b) POST-arbitration, PRICED gate — D-5 / F-3 (task #73).
        #
        # The gate above runs BEFORE the kernel decides, so it cannot know which
        # action is about to be issued and was priced with ``units=0``: the
        # ``cost_units`` ceiling therefore only ever saw the *already committed*
        # total and was DECORATIVE. Re-check now, with the real action, and let
        # the governor price it off the ONE cost table.
        #
        # This deliberately does NOT reverse the arbitration order: the kernel
        # still decides first, and ``forced_escalate`` semantics are unchanged —
        # refusal still means "replace with ESCALATE and record", never suppress.
        # The only thing added is that the ceiling is now evaluated against the
        # action that is actually about to be signed, dispatched and recorded.
        if not forced_escalate:
            priced = self.budget.check(session_id, kind="directive", units=0, action=action)
            if not priced.allowed:
                forced_escalate = True
                budget_code = priced.code
                if blocker_code is BlockerCode.NONE:
                    blocker_code = BlockerCode.BUDGET_EXHAUSTED
                action = DirectiveAction.ESCALATE

        if result.blocked and blocker_code is BlockerCode.NONE:
            blocker_code = BlockerCode.EXACT_BLOCKER
        blocked = bool(result.blocked or forced_escalate)

        # 3) Build + sign the directive (never executable in P3).
        now = self._clock()
        # The id shape is NOT cosmetic: it is half of the (origin, prefix)
        # pairing S-3 pins, and the store derives the economic-event key by
        # stripping the prefix, so ``shadow_<session>_<event>`` must carry EXACTLY
        # ONE underscore after the prefix (the engine writes ``dir_<session>_``
        # the same way). A second one turns the row into its own event and the
        # fold then counts this directive twice (R1/R2) -- identical in a log,
        # invisible until the cost ceiling fires at half the real rate.
        # Pinned by ``test_the_two_planes_agree_on_the_economic_event_key``.
        directive = Directive(
            directive_id=(f"{SHADOW_DIRECTIVE_PREFIX}{session_id}_"
                          f"{event_id or uuid.uuid4().hex[:12]}"),
            session_id=session_id,
            timestamp=now,
            action=action,
            target_actuator="noop",
            params={
                "reason_code": result.reason_code,
                "blocker_code": blocker_code.value,
                "arbitration_inputs_hash": result.trace_hash,
            },
            policy_version=self.policy_version,
            policy_table_version=self.policy_table_version,
            evidence_hash=evidence_hash,
            issued_at=now,
            ttl_s=self.ttl_s,
            nonce=self._nonce_factory(),
            executable=False,
            requires_human=bool(blocked or action is DirectiveAction.ESCALATE),
            shadow=True,
        )
        # S-3: this plane's row STATES its origin and its id proves it. Asserted
        # at the write site, before any charge depends on the attribution.
        self.directive_plane(directive.directive_id, SUPERVISOR_ORIGIN)
        self._apply_signature(directive)

        # 4) The ONE seam: dispatch through the injected port.
        dispatch_result = self._dispatch(directive)
        directive.acted = bool(dispatch_result.get("acted", False))
        directive.actuation_reason = dispatch_result.get("reason")
        directive.executable = bool(dispatch_result.get("executable", False))

        # 4b) Charge EVERY directive that is issued AND recorded (task #75a).
        #
        # Adjudication: the previous ``if not forced_escalate:`` was a DEFECT, and
        # it was the same defect twice over.
        #   * SIGNING_REQUIRED_UNAVAILABLE — the signer being unavailable granted
        #     that session an UNMETERED directive budget: every event after it
        #     still ran arbitration, signing, dispatch and a hash-chained append,
        #     and none of it was charged. A failure must never be cheaper than
        #     success.
        #   * BUDGET_EXHAUSTED — the replacement ESCALATE is still issued, signed,
        #     dispatched and recorded, so it is still work the plane performed.
        #     Skipping its charge does not "save" the session anything (it is
        #     already over budget); it just stops the counter describing reality.
        #     Charging it once is uniform with the signer case, and the two paths
        #     cannot drift apart again.
        #
        # DEFINITIVELY NOT double-billing #73's priced pre-check: ``check`` is a
        # pure read (it reserves nothing), and ``commit`` below is the single
        # writer of ``cost_units_used``. One issued directive => exactly one
        # charge here, whatever path produced the ESCALATE.
        #
        # ``directive=`` is what makes the charge DURABLE against a store-backed
        # governor: the store derives the counters from its own ``directives``
        # rows, so charging without persisting the directive would be charging
        # against a counter we do not control (see ``_persist_directive``).
        self.budget.commit(session_id, kind="directive", units=0, action=action,
                           directive=directive)

        # 5) Record (hash-chained) — and HOLD THE LINE on durability (S-1).
        #
        # ``_append_shadow`` is the only thing that increments
        # ``persistence_durable_writes``, so comparing it across the append is a
        # direct read of "did THIS record land" rather than a re-derivation of
        # durability that could disagree with the counter.
        #
        # S-1: an action that left no durable shadow trail is NOT a completed
        # directive. Returning the ``ShadowDecision`` here is the specific trap
        # being closed — the caller reads ``decision.action`` ("ESCALATE") and
        # reasonably concludes the shadow plane evaluated and escalated, while
        # the plane recorded nothing at all. That is #54 with a decision bolted
        # on, and it is worse than #54 because the audit answer now looks
        # authoritative. So a non-durable record yields NO decision.
        durable_before = self.persistence_durable_writes
        decision = self._record(
            assessment=assessment, directive=directive, result=result,
            thresholds=thresholds, session_id=session_id, event_id=event_id,
            agent=agent_name, project=project, evidence_hash=evidence_hash,
            session_status=session_status, blocked=blocked,
            blocker_code=blocker_code, budget_code=budget_code,
        )
        if self.persistence_durable_writes == durable_before:
            # Already counted once, by whichever branch lost the record.
            log.error(
                "shadow record for event_id=%s (session=%s) was NOT durable; "
                "returning no decision rather than one with no trail behind it "
                "(S-1)", event_id, session_id,
            )
            return None
        return decision

    # -- THE seam (D-3/D-41) -------------------------------------------------
    def _dispatch(self, directive: Directive):
        """The only actuation call site in the supervisor plane.

        A P4 actuator attaches by swapping the injected ``DispatchPort``; this
        method itself never changes. In P3 the port returns ``acted=False``.
        """
        return self.dispatch_port.dispatch(directive)

    # -- signing -------------------------------------------------------------
    def _apply_signature(self, directive: Directive) -> None:
        if not getattr(self.signer, "available", False):
            directive.signed = False
            directive.signature = None
            directive.signing_alg = None
            directive.signature_verified = False
            directive.signature_status = (
                "required_missing" if self.require_signature else "unavailable:no_key"
            )
            return
        signature = self.signer.sign(directive)
        directive.signed = signature is not None
        directive.signature = signature
        directive.signing_alg = getattr(self.signer, "SIGN_ALG", signing_kernel.SIGN_ALG)
        verified = bool(signature is not None and self.signer.verify(directive, signature))
        directive.signature_verified = verified
        directive.signature_status = "signed_verified" if verified else "unsigned"

    # -- persistence ---------------------------------------------------------
    def _append_via_store_transaction(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Append through the store's **lock-scoped** ``transaction()`` (§2.6 item 1).

        This is the PRIMARY write path, not a fallback (Errata E-5). The lock is
        load-bearing: ``shadow_store.append_shadow`` does a non-atomic
        ``SELECT … ORDER BY seq DESC LIMIT 1`` then ``INSERT``, so a lock-free
        accessor lets two appends read the same ``prev_hash`` and **fork the hash
        chain**. ``store.connection()`` is deliberately lock-free and covers
        READS only; writing through it was the open hazard CR #56 raised.

        The Sentinel MUST NOT reach for a RAW connection either: neither
        ``store._conn`` nor the ``getattr(store, "_conn", None)`` idiom. That
        idiom yields a truthy *bound method* (``_conn`` is a **method** returning
        a fresh connection, ``state_store.py:110``), whose later use raised
        ``AttributeError`` — swallowed by ``on_event`` — so the shadow plane
        silently recorded **ZERO** rows against the real store (the #54 defect).

        Must not call back into ``StateStore`` inside the ``with``: ``_lock`` is
        a non-reentrant ``threading.Lock`` and a nested store call would deadlock.
        ``shadow_store.*`` touches only ``conn``, so this body is safe.
        """
        with self.store.transaction() as conn:
            if not self._schema_ready:
                shadow_store.ensure_schema(conn)
                self._schema_ready = True
            return shadow_store.append_shadow(conn, record=record)

    def _record(self, *, assessment, directive, result, thresholds, session_id, event_id,
                agent, project, evidence_hash, session_status, blocked, blocker_code,
                budget_code) -> ShadowDecision:
        signals = arbitration.build_signals(assessment)
        record = {
            "decision_id": f"sd_{directive.directive_id}",
            "session_id": session_id,
            "event_id": event_id,
            "agent": agent,
            "project": project,
            "observed_at": directive.timestamp,
            "model": getattr(assessment, "model", None),
            "provider": getattr(assessment, "provider", None),
            "assessment_probabilities": getattr(assessment, "probabilities", None),
            "assessment_band": getattr(assessment, "band", None),
            "assessment_flags": signals.as_dict(),
            "session_status": session_status,
            "evidence_complete": signals.evidence_complete,
            "event_seq": 0,
            "progress_seq": 0,
            "stall_events": 0,
            "budget_snapshot": None,
            "thresholds_snapshot": thresholds.as_dict(),
            "policy_version": self.policy_version,
            "policy_config_hash": self.thresholds.config_hash(),
            "action": directive.action.value,
            "rationale": [{"rule": t.rule, "value": t.value, "matched": t.matched} for t in result.rationale],
            "trace_hash": result.trace_hash,
            "arbitration_inputs_hash": result.trace_hash,
            "blocked": blocked,
            "blocker_code": blocker_code.value if blocked else None,
            "needs_human": directive.requires_human,
            "directive_id": directive.directive_id,
            "signed": directive.signed,
            "signature": directive.signature,
            "signing_alg": directive.signing_alg,
            "signature_verified": directive.signature_verified,
            "acted": directive.acted,
            "actuation_reason": directive.actuation_reason,
            "would_have_acted_in_p4": None if blocked else directive.action.value,
        }
        stored = self._append_shadow(record)
        return ShadowDecision(
            decision_id=record["decision_id"],
            session_id=session_id,
            event_id=event_id,
            observed_at=directive.timestamp,
            action=directive.action,
            trace_hash=result.trace_hash,
            blocked=blocked,
            blocker_code=(record["blocker_code"] or None),
            needs_human=directive.requires_human,
            directive_id=directive.directive_id,
            signed=directive.signed,
            signature=directive.signature,
            signing_alg=directive.signing_alg,
            signature_verified=directive.signature_verified,
            signature_status=directive.signature_status,
            acted=directive.acted,
            actuation_reason=directive.actuation_reason,
            would_have_acted_in_p4=record["would_have_acted_in_p4"],
            policy_version=self.policy_version,
            policy_table_version=self.policy_table_version,
            policy_config_hash=record["policy_config_hash"],
            prev_hash=stored.get("prev_hash"),
            entry_hash=stored.get("entry_hash"),
        )

    def _append_shadow(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Persist one shadow record via the store's lock-scoped ``transaction()``.

        Contract (ADR-0008 spec §2.6 item 1, Errata **E-5** — this REPLACES the
        earlier, erroneous "delegate to ``shadow_store``" draft):

        * ``store.transaction()`` — the store's **lock-scoped** context manager.
          The lock must span the ``SELECT tail → INSERT`` pair, so it is the write
          path and nothing else is tried first.
        * With no ``transaction()`` the record is kept in memory and logged
          **LOUDLY**: "not durable" must never be mistaken for success. There is
          deliberately **no** ``append_shadow``-delegate branch (``StateStore``
          can never offer one — that would be a ``jcc_core``→``supervisor``
          import, D-2) and **no** ``connection()`` write fallback (lock-free ⇒
          chain-fork hazard).

        Never a private ``_conn`` poke (see ``_append_via_store_transaction``).
        Every outcome is counted (``persistence_attempts`` /
        ``persistence_failures`` / ``persistence_durable_writes``) so a silent
        no-op is `assert`-able.
        """
        self.persistence_attempts += 1
        transaction = getattr(self.store, "transaction", None)
        if callable(transaction):
            stored = self._append_via_store_transaction(record)  # raise -> on_event
            self.persistence_durable_writes += 1
            return stored
        # Degraded: the store exposes no lock-scoped write accessor. This does NOT
        # raise, so it counts its own failure here (on_event never sees it).
        self.persistence_failures += 1
        log.error(
            "store %r exposes no transaction(); shadow record kept IN MEMORY ONLY "
            "(session_id=%s) — NOT durable",
            type(self.store).__name__, record.get("session_id"),
        )
        self._memory.append(record)
        return record
