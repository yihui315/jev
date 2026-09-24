# ADR-0008 — P3 Jev Shadow Supervision (Sentinel + Metric)

- **Status:** **FROZEN** — frozen in *Round 4* (lead rulings A–I, P4 attach-point ruling D-41, Metric's couplings), re-opened narrowly and re-frozen in *Round 5* (interpretation & precision), again in *Round 6* (lead-authorized **internal-consistency repair only**), and carried forward by a lead-authorized **precision addendum** (Add A / Add B / signal-count fix) and a lead-authorized **Errata** (E-1…E-4; reconciles frozen text with observed behaviour; resolves the `disabled`/`real` provider contract). Range of decisions: **D-1…D-41** — Rounds 5 & 6, the addendum, and the Errata add **no** new decision.
- **Date:** 2026-09-24 (frozen 2026-09-24; Round-5 re-freeze 2026-09-24; Round-6 re-freeze 2026-09-24; precision addendum 2026-09-24)
- **Owner:** Atlas-2 (jcc-protocol-architect)
- **Supersedes:** none
- **Superseded by:** none
- **Extends (does not re-open):** ADR-0002 (four planes), ADR-0003 (`session_id` identity), ADR-0004 (supervision & actuation — **P3 stays NON-ACTUATING**), ADR-0005 (security baseline), ADR-0007 (redaction boundary / shared kernel).
- **Baseline:** P2 frozen at product commit `7f62f57c72a6146e0d75b0138bc889af22beb886`; docs closeout `a4e7916`; ADR-0007 **ACCEPTED**.
- **Implementing spec:** `docs/p3/P3_ARCHITECTURE_SPEC.md`
- **Inputs folded:** `docs/p3/P3_METRIC_EVAL_DESIGN.md` (G-1…G-12), `docs/p3/P3_SENTINEL_DESIGN_INPUT.md` (Sentinel conflicts), lead rulings A–I + attach-point ruling.
- **Change control:** frozen — any alteration to D-1…D-41 requires a **superseding ADR** or an explicit re-open by the lead. **Rounds 5 and 6 were such authorized re-opens** (wording/precision; internal-consistency repair only), and the **precision addendum** (Add A / Add B / signal-count fix) was lead-authorized contract-recording only. See *Round 4 — Freeze*, *Round 5 — interpretation & precision fixes*, *Round 6 — internal-consistency repair*, the *Addenda — precision / contract-recording*, and the *Errata — reconciliation of frozen contradictions*.

## Context

P0–P2 built: the AEP contract, four agent adapters, the deterministic policy, the hash-chained audit tape, and a **disabled** actuator (all directives already resolve to `acted=False`, `actuator.py:51-58`). P3 introduces the **Jev shadow supervision plane** — two workstreams:

- **Sentinel** — the supervisor (ADR-0002 names `packages/supervisor` as the future Control-plane arbiter). It re-arbitrates Jev's scores into a directive and records a decision — **without actuating**.
- **Metric** — the evaluation consumer. It measures whether Jev's scoring is trustworthy (calibration / false-positive rate) by joining recorded decisions against observed outcomes.

The chain to realize (ADR-0002 `:13`): `Agent → Adapter → AEP → State Store → Evidence → Jev assessment → deterministic policy arbitration → signed directive → (NO actuator)`.

**Two hazards this ADR exists to prevent**, drawn from the P2 experience:

1. **Dependency inversion.** P2 introduced `core → adapters._common` (ADR-0007 D-3, `core.py:25`). P3 must not repeat this: the supervisor must never be imported by `jcc_core`. Interfaces (protocols) live in the neutral/lower layer; implementations live in the supervisor plane; wiring lives in the composition root.
2. **Unfalsifiable assurance.** A shadow that "looks correct" but cannot be recomputed or isn't signed is worthless to Metric. Every shadow decision must be independently recomputable and honestly marked.

## Decision

### D-1 — Shadow posture: observe, arbitrate, record; never actuate
P3 is **non-actuating**. The Sentinel performs `assess → arbitrate → record directive` and the recorded directive carries `acted=False` with `actuation_reason="shadow_no_actuation"`. No actuator side-effect path is reachable in P3. Reinforces ADR-0004 (P0–P3 shadow; actuation OFF) and core contract #4.

### D-2 — Plane ownership and dependency direction
- **Sentinel** is the **Control plane** (`packages/supervisor`). It consumes the **Telemetry-Evidence** plane **read-only** (`state_store`, `audit`).
- **Metric** is an offline evaluation consumer; it reads the store directly (see D-10).
- **`jcc_core` MUST NOT import `packages/supervisor`.** Interfaces the engine needs are declared as **Protocols in the lower layer** (`jcc_core/ports.py`); the supervisor *implements* them; `run.py` is the **composition root** that wires them. This is the direct application of the ADR-0007 "shared kernel depends downward" lesson.

### D-3 — The actuation seam (present, called, and deliberately inert)
- The **one canonical dispatch site** is `CoreEngine.ingest` step 6: `result = self.actuator.execute(directive)` (`core.py:143`).
- The **seam is the port** `jcc_core.ports.ActuationPort` with method `dispatch(directive) -> ActuationResult`.
- **P3 implementation:** `packages/supervisor/ports.ShadowActuationPort` returns, for every directive, `{"acted": False, "reason": "shadow_no_actuation", "directive_id": ...}`. The existing `jcc_core.actuator.Actuator` (disabled) remains the engine default.
- **Future (P4+):** a real actuator implements the **same** `ActuationPort` and is injected in `run.py`. Nothing else changes. The seam therefore exists, is invoked, and returns a no-op — a future actuator attaches *here* and nowhere else.

> **REVISED by D-41 (freeze):** the phrase "attaches *here* and nowhere else" no longer points at `core.py:143`. The lead ruled that the P4 attach point is **`Sentinel._dispatch` ONLY**, that the engine port and the Sentinel port are **distinct ports**, and that **`core.py:143` is permanently inert** (never an attach point, in P3 or later). This section survives only as the description of the engine's inert default; the authoritative seam text is **D-41**.

### D-4 — Deterministic arbitration (no model calls in the loop)
- Arbitration is a **pure function** `supervisor.arbitration.arbitrate(input) -> ArbitrationResult`.
- **Inputs:** Jev `Assessment` (probabilities + band + boolean flags), `SessionState.status`, evidence completeness flag, a `BudgetSnapshot`, and the frozen `Thresholds` snapshot.
- **Output:** a single directive action + a rule trace + `trace_hash`.
- **Prohibited inside arbitration:** provider/model calls, network, `time.*` reads, RNG, environment reads. Time is passed in; thresholds are passed in.
- **Determinism requirement:** identical inputs ⇒ identical `action` **and** identical `trace_hash`. This is the property Metric relies on and the property the acceptance harness must assert (recompute-from-record must reproduce).

### D-5 — Budget governance (fail-closed on exhaustion)
- **Metered:** assessments per session; directives per session; directive rate per rolling window (global); model units per window (real provider only; mock meters 0).
- **Default limits:** `max_assessments_per_session=200`, `max_directives_per_session=200`, `max_directives_per_minute=60`, `max_model_units_per_window=1000`, `window_seconds=60`.
- **Fail-closed behaviour on exhaustion:** the action is forced to the **safe default `VERIFY`** with `blocked=True`, `blocker_code=BUDGET_EXHAUSTED*`, session status → `AWAIT`, **no actuation**, and **no further model calls**. Exhaustion MUST NOT yield `CONTINUE` or `FINISH` (those imply "all clear").
- **Assessment-scope enforcement:** the engine may consult an injected `AssessmentBudgetGuard` **before** `provider.assess` (`core.py:125`); default is an allow-all guard so P0–P2 behaviour is unchanged. The directive-scope budget is fully enforced by the Sentinel.
- Budgets are **in-memory per process**, reset on restart; a restart MUST NOT silently reset an *escalation*, so the counter is also journalled into the shadow record for after-the-fact audit.

### D-6 — Directive signing
- **What "signed" means:** a detached **HMAC-SHA256** over the canonical JSON of the *signable* directive fields `{directive_id, session_id, timestamp, action, target_actuator, params, policy_version, evidence_hash}`, serialized exactly like `audit._canon` (sorted keys, `separators=(",",":")`). `signature = "hmac-sha256=<hexdigest>"`.
- **Key source:** environment variable named by `config.p3.shadow.signing.key_env` (default `JCC_DIRECTIVE_SIGNING_KEY`). **Never** stored in `config/jcc.yaml` — same rule as the control token (`config.py:4-5,82-83`). Loaded once at startup.
- **Verification point:** `jcc_core.signing.verify_directive(directive, signature)` — **D-33/ R6-4**: the signing module is the neutral `packages/jcc_core/signing.py`; D-6's original `supervisor/signing.py` is **superseded** (a `supervisor.signing` import would be a dependency inversion). A future real actuator MUST call it and reject forged/unsigned directives before any side-effect (ADR-0004 `:19`). In P3 the Sentinel self-verifies immediately after signing to prove the seam works.
- **Current `signed=False` semantics (P0–P2):** directives are UNSIGNED **by explicit design** because no actuator exists — `schemas.py:135` (`signed: bool = False`), `core.py:138-139` (`signed=False, signature=None`). This stays the engine default.
- **P3 semantics:** signing is **not actuation**, so P3 enables it. Key present → `signed=True`, `signature="hmac-sha256=<hex>"`, `signature_status="signed_verified"`. Key absent → `signed=False`, `signature=None`, `signature_status="unavailable:no_key"` — recorded **honestly**; a fake signature is never emitted. Fail-safe: an unsigned shadow directive is still recorded but is explicitly marked unauthentic so no downstream treats it as trustworthy.

### D-7 — Shadow decision record (the Metric evaluation hooks)
- New **hash-chained** table `shadow_decisions` (same tamper-evidence pattern as `audit_tape`: `prev_hash`/`entry_hash` over a canonical serialization). Additive; P0–P2 tables untouched.
- Each record MUST make the decision **independently recomputable**: it stores exactly the arbitration inputs (`assessment_probabilities`, `assessment_band`, `assessment_flags`, `session_status`, `evidence_complete`, `budget_snapshot`, `thresholds_snapshot`, `policy_version`) plus the outputs (`action`, `rationale`, `trace_hash`, `blocked`, `blocker_code`) and the directive identity/signing (`directive_id`, `signed`, `signature`, `signature_verified`, `acted=false`, `actuation_reason`).
- **Metric contract:** given a record, Metric can (a) re-run `arbitrate()` and confirm `action` + `trace_hash` match (falsification of the "deterministic" claim); (b) join `assessment_band`/`risk_score` against a later-filled `outcome_label` to measure calibration / false-positive rate. `outcome_observed`/`outcome_label` are nullable and filled out-of-band.
- **Counterfactual field:** `would_have_acted_in_p4` records the action a P4 actuator *would* consume — explicitly non-actuating, never wired to a side-effect.

### D-8 — Fail-closed rules and EXACT_BLOCKER
A closed blocker vocabulary: `NONE | EXACT_BLOCKER | BUDGET_EXHAUSTED | SIGNING_REQUIRED_UNAVAILABLE | AUDIT_CHAIN_BROKEN | CONTRADICTORY_EVIDENCE`.

- **`EXACT_BLOCKER`** = the event/decision cannot be attributed to **exactly one** identity/decision: the session is absent from the store at decision time, a cross-agent `session_isolation` conflict is observed, evidence is present but `schema_ok=False`, or the assessment lacks a required field. Handling: `action=VERIFY`, status → `AWAIT`, `blocked=True`, **no provider call, no actuation**, record emitted with `blocker_code=EXACT_BLOCKER`. Never `CONTINUE`/`FINISH`.
- **`AUDIT_CHAIN_BROKEN`** = `store.verify_audit()` returns `(False, seq)` (`audit.py:100-127`). The Sentinel MUST refuse to append and MUST raise a loud alarm rather than extend a broken chain.
- Every blocker path is fail-closed: default to `VERIFY`/`AWAIT`; never auto-mark safe/completed (ADR-0003).

### D-9 — Prerequisite: redaction-kernel hoist (ADR-0007 D-3)
The P3 shadow records persist assessment/evidence-derived JSON. Before P3 code lands, the redaction kernel MUST be hoisted from `packages/jcc_core/adapters/_common.py` to the neutral module `packages/jcc_core/redaction.py`, with `adapters/_common.py` re-exporting for back-compat (Forge is implementing this). The shadow record path MUST pass through the same kernel (no new unscrubbed persistence boundary) — the F-1/C1/FINDING-D classes must not reappear on a new table.

### D-10 — No new inbound surface (ADR-0005)
Metric reads `shadow_decisions` **directly from SQLite** via a read-only accessor. P3 adds **no** HTTP route and **no** new listener. This mirrors the already-accepted decision that `SecureWebhookReceiver` is not mounted and that P2 added no inbound surface.

### D-11 — Rollback
Shadow is additive and fully reversible: disable with `p3.shadow.enabled=false`; the `shadow_decisions` table can be dropped without touching any P0–P2 table; no destructive migration. Rollback evidence is required per ADR-0006.

### D-12 — Forward compatibility
All schema changes are **additive with defaults**: `Directive` gains optional `signing_alg: Optional[str] = None` and `signature_status: str = "unsigned"`; `aep/v0.1` is unchanged; existing rows and readers keep parsing. New models (`ShadowDecision`, `RuleTrace`, `ArbitrationResult`) are new names, not renames of frozen ones.

## Reconciliation with the Metric register (G-1…G-12)

`docs/p3/P3_METRIC_EVAL_DESIGN.md` §6 raises twelve schema/audit insufficiencies and hands each to this ADR. Each is resolved below (extend the **new** shadow substrate / keep frozen tables untouched / drop the metric). **No P0–P2 table is migrated in P3** — rollback safety (D-11, ADR-0006). The shadow record is the canonical Metric substrate.

- **D-13 (G-1, BLOCKER) — ground-truth store is a MULTI-SOURCE child table.** Labels are **not** columns on the immutable shadow row. They live in a separate, **mutable** table `shadow_labels`, keyed **`(decision_id, label_source)`**, so multiple sources (Tier-1 `oracle_next_event`, Tier-2 `human`, future adjudicators) can label the **same** directive and be compared — required to measure the oracle's own precision/recall (§1.2). Rationale for a child table over two slots: (i) unbounded sources without further schema churn; (ii) re-adjudication must **not** break the shadow tape's hash chain, so labels belong outside it; (iii) `label_window_events` is stored **per label row**, so a window `W` change is recomputable and never silently stale. Filled out-of-band via `evaluation.set_label(decision_id, source, value, window_events=...)`. No new inbound surface (D-10). Tier-1 is computed by Metric from the existing `events` table; Tier-2 is written by a reviewer.
- **D-14 (G-2, BLOCKER for calibration) — mock scores are not calibratable.** `DeterministicMockProvider` derives `risk_score` from a SHA-256 (`mock.py:27-31`) — uniform in [0,1), structurally uncalibratable. HARD RULE: any calibration/Brier/ECE claim computed under `provider="mock"` MUST be labelled **"mock-only, non-evidential"**; calibration claims are admissible only with the real scorer or a scored corpus. `provider`+`model` are recorded on every shadow row so this is machine-checkable.
- **D-15 (G-3, HIGH) — stall/progress feature.** The Sentinel records derived-at-decision `progress_seq` and `stall_events` (events since the last `tool_call`/`agent_message`/`evidence`), computed deterministically from the store. No wall-clock.
- **D-16 (G-4, HIGH) — off-track metric is DROPPED in P3.** There is no goal/task/plan field and `project` is metadata-only (`ADR-0007 D-6`). OFF-TRACK detection is out of scope until a future AEP `payload.goal_ref` exists (separate ADR). Metric MUST NOT report it. **Recorded consequence (accepted):** off-track was `STEER`'s only ground-truth label source (§2.6), so in P3 `STEER` directives are **recorded but unscored**. **Round 5 clarity (no intent change):** `STEER`'s **rule row survives** in the kernel `ROWS` (STEER still arbitrates normally); **DEFERRED applies to *metric scoring only*** (no label to score against), **not** to the decision path. **`work_off_track` stays on `Assessment`** (it remains a recorded signal); it is removed only as a **decision input** — the kernel does not read it. The P3 report MUST list **off-track AND steer as DEFERRED** with the reintroduction trigger `payload.goal_ref` — **not** as a passing `0/N-A`.
- **D-17 (G-5, HIGH) — latency timestamps; CORE injects the ingest bound.** `Assessment.raw` gains additive `timing={ingest_started_at, jev_started_at, jev_finished_at, jev_call_ms, timing_source}`. `provider.assess(event, sess)` (`base.py:21`) does **not** receive the ingest start, so **`CoreEngine.ingest` MUST inject `ingest_started_at` into `assessment.raw["timing"]` after the provider returns** (captured at the top of `ingest`, cf. `core.py:89`), **without clobbering** any `jev_*` keys the provider set. The provider sets `jev_started_at`/`jev_finished_at`/`jev_call_ms` around its own call; mock sets none and marks `timing_source="mock_no_call"`. Ownership is split by plane: ingest bound = core (telemetry), call bounds = provider (scoring); `ingest_started_at` MUST NOT be absent. Persisted via the existing `assessments.data` JSON — **no DDL change**.
- **D-18 (G-6, MEDIUM) — clock skew.** Evaluation windows are **event-count**, not wall-clock; the shadow row records the store's monotonic `audit_seq` (from `audit.py:19-20`) as `event_seq` for stable ordering.
- **D-19 (G-7, MEDIUM) — usage/cost.** `Assessment.raw.usage={prompt_tokens, completion_tokens, total_tokens, usage_source}`; pricing lives in a versioned `config.raw.p3.pricing` map (never in code). Mock sets `usage_source="mock"`, cost 0.
- **D-20 (G-8, MEDIUM) — confidence.** `Assessment` gains additive `confidence: Optional[float] = None` and `confidence_kind: str = "none"`. Persisted via `assessments.data` JSON.
- **D-21 (G-9, HIGH) — applied thresholds.** The shadow row stores `thresholds_snapshot` + `policy_version` + `policy_config_hash` (sha256 of the policy+thresholds block). Additionally, the live directive records `params["applied_thresholds"]` (the `directives.params` column already exists, `state_store.py:86`) — chain-safe: `directives.params` is **not** part of the audit payload (`audit.py:64-74`; the hashed `directive` field is the lossy `"ACTION:directive_id"` string, `core.py:173`).
- **D-22 (G-10, HIGH) — assessment↔event join.** Frozen `assessments` is **not** re-keyed (ADR-0006 rollback safety). The strict join is `shadow_decisions.event_id` (present by design); `assessments.data` additionally gains an `event_id` key so non-shadow consumers can join without DDL.
- **D-23 (G-11, MEDIUM) — safety flag in the hash chain.** Folding `acted`/`actuation_reason` into the **existing** `audit_tape` canonical payload would invalidate the P2 chain (changing `audit._canon` breaks `verify_chain` on stored rows — rejected). Instead the **shadow tape** hash-covers `acted=false` and `actuation_reason` on every row, making the safety flag tamper-evident there; the `directives` table remains the source of truth and `audit_tape` stays byte-frozen.
- **D-24 (G-12, BLOCKER) — inert thresholds become live; SINGLE source of truth for threshold application.** [**Round 5 — corrected; the old enabled/disabled branch is deleted**]. `jcc_core.decision.arbitrate` is **ALWAYS** the mapping authority and **ALWAYS** derives the action from the assessment signals using the applicable `Thresholds` — `p3.shadow.thresholds.per_agent[agent]`, falling back to `p3.shadow.thresholds.default` — applied to `assessment.risk_score`. **There is NO `thresholds.enabled` toggle and NO "defer to provider boolean flags" mode**: post-D-32 `mock` no longer emits those flags, so a deferral branch would be undefined and would yield EXACT_BLOCKER on every event. `policy.decide` is a **thin wrapper over the *same* kernel** ⇒ engine/shadow equivalence holds **by construction**, not by a test asserting two paths agree.
  - **Cut set [Round 6].** `Thresholds = {escalate_risk: 0.8, pause_risk: 0.6, steer_risk: 0.4, finish_risk: 0.1}`. **`verify_risk` is DELETED, not aliased** — VERIFY is evidence/uncertainty-driven (`policy.py:33` maps it to the `needs_verify` flag) and is **never** a risk band.
  - **Why the 4-key form is MANDATORY (not a nicety) [Round 6].** Because `policy.decide` becomes a thin wrapper over the *same* kernel, a `0.4 → VERIFY` reading (i.e. `verify_risk` as named) would change the **live engine's** mapping (STEER→VERIFY for `0.4 ≤ risk < 0.6`). That is a behaviour change to P1/P2-verified code smuggled in by a *shadow* phase. **P3 is shadow-only; it must not alter the live engine's decision mapping.** The 4-key form is precisely what makes "thin wrapper ⇒ equivalence by construction" and "nothing re-opens P0–P2" true **simultaneously**.
  - **Characterization test (replaces the old "equivalence test") — ASSERTED EQUALITY, not a recorded delta [Round 6].** Replay the **recorded P2 provider-flag fixtures** and assert the **engine-path output (`policy.decide`) == the P2 `policy.decide` output** on the same inputs. (A recorded-delta escape hatch would let exactly the live-mapping regression this round closes ship silently.)
  - **The mock caveat is handled by D-14** ("mock-only, non-evidential") — **not** by a toggle.
  - **`config.policy.thresholds` is RETIRED** (superseded by `p3.shadow.thresholds`): it was dead config (nothing read it). Retained only for config back-compat and MUST be marked inert in `jcc.yaml` so nobody edits it expecting effect. Removal is a P4 concern.
  - **Config shape (Round 5/6):** `p3.shadow.thresholds = {default: Thresholds, per_agent: {<agent>: Thresholds}}` with `Thresholds = {escalate_risk, pause_risk, steer_risk, finish_risk}` — **no `enabled` key, no `verify_risk`**.
  In all cases `thresholds_snapshot` + `policy_config_hash` are recorded (D-21) so the applied numbers are attributable.
- **D-25 (G-12, disclosure) — schema-identity break is DOCUMENTED, wire is UNTOUCHED.** P2's "schemas.py byte-identical to P1" claim was true **at P2** (`P2_ACCEPTANCE_REPORT.md:88`). P3 D-20/D-12 add fields, so `packages/jcc_core/schemas.py` is **no longer byte-identical** — this is **declared, expected drift**. Boundary: the **AEP WIRE envelope `AEPEnvelope` (`schemas.py:62-79`) is UNCHANGED** (aep/v0.1 wire stays fixed); the additive fields land on the **internal domain** model `Assessment` (`schemas.py:107-124`) and on new names, not the wire. `shadow_decisions` is a **new additive table**; **no frozen P0–P2 table receives DDL.** Stated so the P3 architecture gate does not flag this as undocumented drift.

## Round 3 — Consolidation with the Sentinel design input (D-26…D-40)

`docs/p3/P3_SENTINEL_DESIGN_INPUT.md` is a parallel design input to this ADR (author: Sentinel). It agrees with D-21/D-23 (audit tape frozen; keep the `entry_hash` key set fixed; `directives.params` is chain-safe) and raises real findings. Where it diverges from D-1…D-25, this section decides. **Revised earlier decisions:** D-4 (arbitration location) → **D-32**; D-5 (budget) → **D-34**; D-8 (EXACT_BLOCKER action) → **D-35**; D-6 (signing details) → **D-33**. All revisions keep P3 **non-actuating** and do **not** re-open the four-plane **architecture** (ADR-0002) — they make it true.

### D-26 (HIGH, finding F1) — provider-exception boundary closes an audit-tape hole
`CoreEngine.ingest` persists the event at `core.py:105` **before** calling the provider at `core.py:125`, and appends the audit row at `:166`. `provider.assess` is **uncaught**, so a provider exception leaves an event persisted with **no assessment/directive/audit row** — a hole in the authoritative tape. Decision: wrap `provider.assess` at the boundary; on exception synthesize an **EXACT_BLOCKER** assessment (`blocker_kind=EXACT`, `blocker_reasons=["provider_error"]`) → ESCALATE, and **still append the audit row**. The record is never dropped. (VERIFIED: `core.py:105,125,166`.)

**Extended (Round 5) — same hole class at the server boundary.** `AEPEnvelope(**body)` at `server.py:133-136` can raise an **unhandled `ValidationError`** → HTTP 500 with **no audit row** (found by Sentinel; lead verified the shape). Decision: **no unhandled 500 on the ingest boundary** — a malformed/unauthorised envelope MUST produce a **blocked/EXACT** record that **still writes its audit row**, never a silent 500. Same rule as the provider boundary: the record is never dropped. (VERIFIED: `server.py:133-136`.)

**Mechanism frozen — Add A (lead-authorized precision).** The two boundaries are **deliberately different** and MUST NOT be conflated:
- **Provider boundary** (body already accepted, session known): synthesise an **EXACT_BLOCKER assessment → ESCALATE** (absorbing, above), and still audit.
- **Server boundary** (body rejected before any session/decision exists): return a **structured `422` + write an audit row, and synthesise NO directive** — there is no session/decision to attribute the rejected body to.
- **Only the server boundary uses a `4xx` + audit; only the provider boundary uses `EXACT_BLOCKER → ESCALATE`.** `422` is the ratified shape (forge `e77037d`; zero prior hits in the ADR/spec). This removes the earlier ambiguous "(or a structured 4xx)".

### D-27 (HIGH, finding §4.2) — shadow by CONSTRUCTION, not by accident
Today `core.py:143` calls `actuator.execute` on **every** ingest; INV-SHADOW holds only because the actuator happens to be disabled. Decision: on the ingest path the engine MUST NOT call `Actuator.execute`. It dispatches through `jcc_core.ports.ActuationPort` whose P3 implementation is **verify-only** (`actuator.verify_envelope`, side-effect-free) and sets `acted=False`, `executable=False`, `actuation_reason="shadow_mode"`. This refines D-3: the single seam stays `ActuationPort`, but the P3 port verifies and records — it never executes. `Actuator.execute` is unreachable from ingest in P3.

### D-28 (MEDIUM, finding) — close the sole policy bypass
`server.py:139-160` `_manual()` builds a `Directive` and calls `engine.actuator.execute(d)` at `:149` — outside policy, and §6-3's `execute` path. ADR-0004 requires the supervisor to reject policy-bypassing directives. Decision: `/api/pause|steer|retry` MUST route through the arbitration + signing path with `requires_human=true`, `executable=false`, and MUST NOT call `Actuator.execute`. (VERIFIED: `server.py:139-149`.) Owner: implementer; the deviation is recorded, not silently kept.

### D-29 (MEDIUM, finding) — signatures must be readable from stored state
`state_store.get_directives` (`:325-338`) never selects `signature`, although `add_directive` writes it (`:299-313`); `_row_to_directive` (`:343-357`) omits it too. Decision: extend the read path + mapper to include `signature` and the D-33 columns — otherwise a persisted signature cannot be replayed/verified and signing is unverifiable. (VERIFIED.)

### D-30 (MEDIUM, finding F11) — config load fails closed
`load_config` (`config.py:86-91`) silently falls back to defaults when the config file is missing/unreadable, silently pinning `policy.version` and (post-P3) the whole budget/threshold surface. Decision: if `JCC_CONFIG` is **explicitly set but unreadable**, `load_config` raises; and `run.main` **refuses to start** if the resolved config path is missing. Note: the default-missing refusal lives in the **server entrypoint**, not in `load_config` itself, so unit tests that build defaults are unaffected. (VERIFIED: `config.py:86-91`.)

### D-31 (LOW, finding F2) — no magic-band control signal
`real.py:48` sets `band="unverified"`, a value **outside** the documented band enum (`schemas.py:117`), and policy currently infers "unverified" from it. Decision: add an explicit additive `Assessment.provider_verified: bool` (populated from `JevProvider.verified`) and stop using a band value as a control signal; keep the documented band enum authoritative. (VERIFIED: `real.py:48`.)

### D-32 (HIGH) — ONE arbitration authority, hoisted to a neutral kernel (REVISES D-4)
Headline finding: the **provider**, not policy, currently chooses the action — `DeterministicMockProvider.assess` hard-codes 0.8/0.6/0.4 into `escalate`/`pause`/`steer`/`finished` (`mock.py:62-69`), and `policy.decide` merely walks those flags (`policy.py:30-49`); meanwhile `config.policy.thresholds` is dead config. Decision:
- Introduce **`packages/jcc_core/decision.py`** — a pure, total, stdlib-only kernel: `normalize()` + an **ordered rule table** `ROWS` + `arbitrate(signals, thresholds) -> (DirectiveAction, reason_code, matched_rows)`. No I/O, no model, no clock/RNG; AST-enforced.
- **Single authority:** the rule table's action column MUST equal `POLICY_PRIORITY` (`schemas.py:157-165`). `policy.decide` becomes a **thin wrapper over the *same* kernel** ⇒ equivalence **by construction** (no toggle; D-24 Round-5), so there is literally one mapping authority — the engine and the shadow plane call the **same** function. This is the ADR-0007 "shared kernel depends downward" pattern applied again; placement in `jcc_core/` (not `supervisor/`) avoids the core→supervisor inversion.
- `supervisor/arbitration.py` (D-4) becomes a thin adapter: it builds `Signals` from the `Assessment` and calls `decision.arbitrate`. It is **not** a second authority.
- `providers/mock.py` **stops** emitting directive-shaped booleans and **stops** cutting bands; it emits the normalised signal fields (deterministic from the same SHA-256 seed so fixtures stay reproducible).
- Consequence: `tests/test_policy.py` is rewritten against the kernel (Sentinel §1.7 D1–D8); `test_acceptance_harness` keeps its intent (missing evidence ⇒ VERIFY).

### D-33 (HIGH) — signing contract (REVISES D-6)
Adopt the fuller Sentinel §3 contract; key env name is **`JCC_DIRECTIVE_SIGNING_KEY`** (this ADR is authoritative; Sentinel's `JCC_DIRECTIVE_KEY` is superseded).
- **Meaning:** "signed" = integrity + origin-as-key-holder, **NOT** authorisation (authorisation is a separate, later check). This distinction is normative.
- **Key:** symmetric HMAC-SHA256 from the env var; never in config/DB/log; read via a `Config` property like `JCC_CONTROL_TOKEN` (`config.py:81-83`).
- **Canonical (byte-stable):** `policy_table_version.policy_version.session_id.directive_id.int(issued_at).ttl_s.nonce.action.target_actuator.evidence_hash.` + `json.dumps(params, sort_keys=True, separators=(",",":"))`. **`ttl_s` and `nonce` are inside the signed payload** (else a valid signature replays with a fresh timestamp).
- **Module:** `packages/jcc_core/signing.py` (neutral; both the policy plane signs and the actuator plane verifies, so it is a shared kernel — superseding `supervisor/signing.py` in D-6).
- **Verification (ordered, first failure wins, no side effects):** 1 `signed`+`signature` present → 2 recompute HMAC (`hmac.compare_digest`) → 3 TTL/clock-skew → 4 nonce replay → 5 session match → 6 capability → 7 policy/table version → 8 budget re-check → 9 human approval. **Check 4 runs AFTER check 2** (unauthenticated traffic must not poison the replay cache — same rule already at `jcc_receiver.py:166`).
- **Schema (additive):** `Directive` gains `issued_at`, `ttl_s`, `nonce`, `policy_table_version`, `executable: bool = False`, `requires_human: bool = False`, `verify_reason`, `shadow: bool = True`, `human_approval`. Existing `signed`/`signature` semantics kept; columns already exist (`state_store.py:88-89`).
- **Replay cache:** hoist to neutral `packages/jcc_core/nonce_cache.py` in the same move as the ADR-0007 D-9 redaction kernel — **do NOT import `integrations/` upward** (that would be a second dependency inversion).
- **Honest limitation (record):** HMAC is symmetric — it does **not** provide non-repudiation against the control plane itself. If P4/P5 needs "the policy plane cannot forge a directive it did not issue", that requires asymmetric signing (Ed25519/ECDSA) ⇒ a new dependency. **Explicit P4 decision; not assumed.**

### D-34 (HIGH) — budget model (REVISES D-5)
Adopt the Sentinel §2 model, which is strictly more restart-safe than in-memory accounting:
- **Counters are DERIVED from persisted state** (`COUNT(*) FROM assessments|directives|events WHERE session_id=?`, `sessions.created_at`), so a restart cannot reset an escalation — direct ADR-0006 support. One new store surface: `StateStore.budget_snapshot(session_id)`.
- **Metered (defaults):** `assess_calls` 200, `scorer_tokens` 200_000, `events` 2000, `directives` 200, `retries` 5, `escalations` 3, `wall_clock_s` 28800, `cost_units` 500, `cost_units_per_hour` 2000, `directives_per_minute` 600.
- **Exhaustion (fail-closed, ordered after arbitration):** **replace, never suppress** — the action becomes **ESCALATE** with `params={budget_exhausted, budget_kind, budget_limit, budget_observed}`, `requires_human=true`, `executable=false`, `acted=false`; the event is **still persisted and audited**. Never `CONTINUE`. *Note: this revises D-5's "VERIFY on exhaustion" to ESCALATE.*
- **Subsystem error ⇒ exhausted** (a counter read that raises ⇒ ESCALATE with `budget_check_error`). **Monotonic.** **No `budget.enabled` off-switch** (a flippable off-switch is how a budget becomes decorative; an override must require an explicit `acknowledge_unbounded: true` and otherwise fail config load).
- Off-by-one note for implementers: the directive count is read **before** the current directive is inserted.

### D-35 (HIGH) — EXACT_BLOCKER semantics (REVISES D-8)
Adopt Sentinel §5.1.
- **Representation (additive):** `Assessment.blocker_kind ∈ {"NONE","EXACT"}` + `blocker_reasons: list[str]` **sorted + de-duplicated** (byte-stable, so the evidence fingerprint is deterministic); mirror onto `directive.params.blocker_kind/blocker_reasons`.
- **Rule (reading R1, adopted):** `EXACT` ⇒ **ESCALATE**, `requires_human=true`, `executable=false`; it **never** yields a resuming action (`CONTINUE`/`STEER`/`RETRY`/`FINISH`) and never `PAUSE` (a PAUSE would be a decision we do not have). *This revises D-8's "EXACT_BLOCKER → VERIFY".* Reading **R2** ("yield no directive at all") is **rejected as schema rework** — `Directive.action` is non-optional (`schemas.py:131`) and one-directive-per-event is the audit shape; R2 would create an audit gap.
- **Distinction preserved:** **missing/incomplete evidence ⇒ VERIFY** (P1-frozen, `mock.py:55-60`, `ADR-0003:21`); **EXACT_BLOCKER** is the narrower "signals cannot be trusted/attributed" case (unknown required signal, contradiction between same-tier signals, unverified provider, provider_error).
- **Absorbing state:** once a session's directive is ESCALATE-by-blocker, the session's arbitration floor stays ESCALATED until an explicit, recorded `human_clear` signal — **never a timeout**. **Round 5 clarity:** `human_clear` travels in **`payload`** — **do NOT add an `EventType` member** (the enum and the wire are frozen, D-25/D-38). (Session-state semantic change, bounded and fail-closed, accepted here.)

> **Scoped by Errata E-3 (lead-authorized).** The "unknown required signal" blocker is narrowed to a provider that **supplied evidence**: absent signals from a provider that **declares non-scoring abstention** (free-form `raw["provider_abstained"] is True`) and supplies **no numeric channels** are **incomplete evidence → row 5 → VERIFY**, not a blocker. **`provider_unverified` and any undeclared absence still ESCALATE.** This **restores** D-35's own stated intent ("incomplete evidence ⇒ VERIFY", `decision.py:464-466`) rather than amending it. See *Errata*.

### D-36 (decision) — audit tape: no string-format change
Sentinel §6 offers extending the `directive` string passed to the tape (`core.py:173`) as a chain-safe way to carry `signed/verify/budget`. Both of us agree `audit.py` and the `entry_hash` key set stay **frozen** (D-23). Decision: **keep the tape format as-is**; the new detail lives on the `directives` table (D-29, D-33) and on the shadow tape (D-23). Rationale: avoids churn in `core.py`/`test_audit.py` and keeps one tamper-evident carrier (shadow tape) rather than two.

### D-37 (decision) — versioning
Bump `policy.version` `p1.0.0 → p3.0.0` (`config.py:55`, `config/jcc.yaml:32`) and add `SCHEMA_INTERNAL_VERSION = "p3.0.0"`. `Directive.policy_version` default → `p3.0.0`. Additive; `AEPEnvelope`/`aep/v0.1` unchanged (D-25/D-38).

### D-38 (decision) — CONFIRMS Sentinel A2
**Confirmed:** the frozen wire contract is `AEPEnvelope` + `version="aep/v0.1"` (`schemas.py:62-79`). `Assessment`/`Directive` are **internal domain** models; extending them does **not** breach the P2 "wire contract frozen" condition. This resolves Sentinel's A2 open question and restates D-25.

### D-39 (decision) — A3 resolved as a P4 item
Sentinel A3 (HMAC vs asymmetric) is resolved: P3 uses symmetric HMAC (D-33); asymmetric non-repudiation is an **explicit P4 decision**, not assumed sufficient. Sentinel's §3.7 limitation is carried verbatim.

### D-40 (HIGH) — the shadow invariant must be FALSIFIABLE
Adopt Sentinel §4.3's five independent proofs (**wording corrected in Round 5**):
- **(a) poison-pill / tripwire on the LEGACY `Actuator.execute`** (`execute_calls == 0`). Note: the **engine port `dispatch` IS expected to be called once per ingest** and MUST return `acted=False` — the tripwire pins `execute`, **not** `dispatch` (reconciles D-27/D-41).
- **(b) static AST scan** of the shadow path: no `execute` / `os.system` / `subprocess` / `socket` / `urllib` / filesystem writes; allow-list of referenced side-effect-free symbols = **`{dispatch, verify_envelope, can_act}`** (the engine port method is **`dispatch`**; the helper is **`verify_envelope`**); no `FORBIDDEN_EFFECTS` token.
- **(c) end-to-end, the 14 fixtures driven THROUGH THE ADAPTERS in-process** (normalize → AEP → `ingest`). The fixtures are **raw native payloads**; `/api/assess` validates `AEPEnvelope` with **no adapter**, so posting them directly is rejected (`ValidationError` — same class as D-26 Round-5). Assert all recorded `acted=false` + `verify_audit()` intact.
- **(d) persisted-artifact assert** (`SELECT COUNT(*) FROM directives WHERE acted=1` == 0).
- **(e) negative control** proving (a)+(d) **CAN** fail.
No invariant is accepted without its falsifier.

## Round 4 — Lead rulings A–I, P4 attach-point ruling, and FREEZE (D-41)

### Lead rulings A–I (folded)
The lead adjudicated the Sentinel conflicts. Every ruling is realized by an existing decision; this table records the ruling → decision mapping so the freeze is auditable. **No ruling re-opens P0–P2, and none makes P3 actuating.**

| Rule | Lead ruling | Decision |
|---|---|---|
| **A** | `provider.assess` at `core.py:125` must be wrapped so an exception cannot leave an event persisted with **no audit row**; still audit | **D-26** |
| **B** | P3 must be non-actuating **by construction**, not because the actuator happens to be disabled | **D-27** |
| **C** | Budget counters MUST be **persisted/derived**, never in-memory, so a restart cannot silently reset an escalation | **D-34** |
| **D** | `ttl_s` **and** `nonce` MUST be **inside the signed payload**; verification order = signature (check 2) **before** replay (check 4) | **D-33** |
| **E** | Exactly **one** `EXACT_BLOCKER` definition; it **ESCALATEs** (absorbing) and **never** resumes | **D-35** |
| **F** | No P3 decision may depend on `work_off_track` (no goal field exists); **drop** it and record STEER/off-track as **DEFERRED** | **D-16** |
| **G** | `get_directives`/`_row_to_directive` MUST read `signature` (+ signing columns) so a persisted signature is verifiable | **D-29** |
| **H** | `server.py` `_manual` MUST NOT call `Actuator.execute`; route `/api/pause\|steer\|retry` through arbitration + signing | **D-28** |
| **I** | Config load MUST fail closed when `JCC_CONFIG` is explicitly set but unreadable; the entrypoint refuses a missing default | **D-30** |

### D-41 — P4 attach-point ruling (freeze; revises D-3 / D-27)
The lead ruled the future-actuator attach point. This supersedes the D-3/D-27 **conflation of one shared port**:

- **The ONLY P4 attach point is `Sentinel._dispatch`.** A future real actuator is injected as the Sentinel's dispatch port and attaches **there and nowhere else**.
- **Two DISTINCT ports, not one:**
  - **`jcc_core.ports.ActuationPort`** — the **engine** seam invoked at `CoreEngine.ingest` step 6 (`core.py:143`). Its implementation is **permanently inert**: verify-only, always `acted=False`, `executable=False`. **`core.py:143` is never an attach point — in P3 or in P4+.** It exists for observational completeness only.
  - **`supervisor/ports.py` dispatch port** — the **Sentinel** seam. In P3 it is `ShadowActuationPort` (inert); in P4 a real actuator replaces **only this port**.
- **Consequence for D-3:** the sentence "*the one canonical dispatch site is `core.py:143`*" is superseded. The engine's single dispatch site remains `core.py:143`, but it is a permanently inert no-op; the seam a future actuator attaches to is **`Sentinel._dispatch` only**.
- **Falsifier (D-40), corrected in Round 5:** `jcc_core.ports.ActuationPort.dispatch` has **exactly ONE** call site (`core.py:143`) and `supervisor.ports.DispatchPort.dispatch` has **exactly ONE** call site (`Sentinel._dispatch`) — two **distinct types**, one site each (**not** one type with two sites). The negative control proves a swapped **Sentinel** port *can* actuate while the **engine** site *cannot*.

### Metric's 2 couplings + refinements (confirmed)
- **Coupling 1 — threshold application ⇔ provider flags.** Because `mock.assess` sets directive-shaped booleans from 0.8/0.6/0.4 and `policy.decide` reads only those flags, threshold application MUST be a **single exclusive source of truth**. Pinned as **D-24** — *as corrected in Round 5*: `decision.arbitrate` **always** derives the action from `risk_score` via thresholds (per-agent → `default`); **there is no toggle and no provider-flag deferral**, because D-32 removes those flags. Ordering coupling with **D-32**: `mock` stops emitting directive-shaped booleans in the *same* change that makes `decision.arbitrate` the sole authority.
- **Coupling 2 — off-track drop ⇒ STEER loses its only P3 label source.** Pinned as **D-16**; the P3 report MUST carry **off-track AND STEER as DEFERRED** (reintroduction trigger `payload.goal_ref`), never as a passing `0/N-A`.
- **Refinements already pinned:** **D-13** (multi-source `shadow_labels` child table), **D-17** (`ingest_started_at` injected by core *after* `provider.assess` returns), **D-25** (schema-identity break disclosed; wire untouched).

### G→D resolution table (freeze)
| Gap (`docs/p3/P3_METRIC_EVAL_DESIGN.md` §6) | G-1 | G-2 | G-3 | G-4 | G-5 | G-6 | G-7 | G-8 | G-9 | G-10 | G-11 | G-12 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **Decision** | D-13 | D-14 | D-15 | **D-16 (dropped)** | D-17 | D-18 | D-19 | D-20 | D-21 | D-22 | D-23 | D-24 |

Dropped/refused by design (closed-negative, **not** deferred): **G-4** (no goal field — D-16) and **G-11** (would break the frozen P2 chain — D-23).

### Freeze confirmations
- **`aep/v0.1` is UNTOUCHED.** The wire envelope `AEPEnvelope` (`schemas.py:62-79`) is byte-frozen; P3 adds **no** wire field (D-25, D-38).
- **All new fields are optional with defaults.** `Directive`/`Assessment` additions are `Optional[...] = None` or defaulted (`signature_status="unsigned"`, `confidence_kind="none"`, `executable=False`, `requires_human=False`, `shadow=True`); existing rows and readers keep parsing (D-12).
- **No frozen P0–P2 table receives DDL.** The only new tables are `shadow_decisions` (+ the mutable `shadow_labels` child); the `audit_tape` canonical payload and its `entry_hash` key set stay **frozen** (D-23, D-36).
- **P3 is non-actuating** and every recorded directive is `acted=False` (D-1, D-27, D-41).
- **Prerequisite D-9** (`jcc_core/redaction.py` hoist, owner Forge) **blocks** shadow persistence.
- **Freeze:** this ADR is **FROZEN at D-1…D-41**. Any change requires a superseding ADR or an explicit re-open by the lead.

## Round 5 — interpretation & precision fixes (lead-authorized re-open; re-frozen)

This round was **authorized by the lead as a narrow re-open — wording / precision only**. No design intent changed, **no new decision added**, nothing re-opens P0–P2. The frozen range stays **D-1…D-41**.

| # | Decision | What changed | Why (defect fixed) |
|---|---|---|---|
| 1 | **D-24** (+ D-32 wording) | Delete the "thresholding disabled ⇒ defer to provider flags" branch. `decision.arbitrate` is **always** the mapping authority and **always** derives from `risk_score` via thresholds (per-agent → `default`). **No `thresholds.enabled` toggle.** `policy.decide` = thin wrapper ⇒ equivalence **by construction**. Add a **characterization test against recorded P2 provider-flag fixtures**. Config shape → `{default, per_agent}`. | **D-24 ↔ D-32 contradiction:** D-32 removes the provider booleans, so the deferral branch was undefined (→ EXACT_BLOCKER on every event). Mock caveat now carried by **D-14**, not a toggle. |
| 2 | **D-40(a)** | Name the tripwire object: it pins the **legacy `Actuator.execute`** (`execute_calls == 0`); the **engine port `dispatch` IS expected to be called once per ingest** and returns `acted=False`. | Reconciles the falsifier with D-27/D-41. |
| 3 | **D-41 falsifier** | "exactly two call sites" → **exactly one call site per distinct type** (`ActuationPort.dispatch` at `core.py:143`; `DispatchPort.dispatch` at `Sentinel._dispatch`). | The old wording described one type with two sites; the two ports are **distinct types**. |
| 4 | **D-40(b)** | AST allow-list → **`{dispatch, verify_envelope, can_act}`** (the engine port method is `dispatch`). | The old allow-list (`verify_envelope`/`can_act`) **rejected correct code**. |
| 5 | **D-40(c)** + **D-26 (extended)** | Fixtures run **through the adapters in-process** (normalize → AEP → `ingest`), not posted raw. **D-26 extended** to the server boundary: `AEPEnvelope(**body)` at `server.py:133-136` must not 500 with no audit row — write a blocked/EXACT record. | Raw native payloads are rejected by `/api/assess` (no adapter); the server boundary is the **same hole class** as the provider boundary. |
| 6 | **D-35** | `human_clear` travels in **`payload`** — **no** new `EventType` member. | Keeps the wire/enum frozen (D-25/D-38). |
| 7 | **D-16** | `STEER`'s **rule row survives**; DEFERRED applies to **metric scoring only**; `work_off_track` stays on `Assessment`, removed only as a **decision input**. | Prevents reading "off-track dropped" as removing the STEER decision path. |

**Round-5 confirmations (unchanged):** `aep/v0.1` untouched; all new fields optional/defaulted; no DDL on frozen P0–P2 tables; P3 non-actuating; **D-1…D-41 is FROZEN again** after this round.

## Round 6 — internal-consistency repair (lead-authorized re-open; re-frozen)

Lead-authorized **internal-consistency repair only**; **no new decision**, design intent unchanged, nothing re-opens P0–P2. Range stays **D-1…D-41**. Items raised by Metric and verified by the lead.

| # | Item | Decision | What changed | Why |
|---|---|---|---|---|
| R6-1 | engine ≡ shadow | D-24/D-32 — **a consequence, recorded in the SPEC, not the ADR** | The engine's `policy.decide` wraps the same kernel and resolves **per-agent → default** identically to the Sentinel; equivalence is **by construction**. Lives in `docs/p3/P3_ARCHITECTURE_SPEC.md` (§2.3/§3/§6.14). | Follows from D-32 ("same function") — a consequence, so it does not belong in the decision range. |
| R6-2 | `Thresholds` cut set | **D-24** | `Thresholds = {escalate_risk: 0.8, pause_risk: 0.6, steer_risk: 0.4, finish_risk: 0.1}`; **`verify_risk` DELETED** (VERIFY is evidence-driven, `policy.py:33`). Added the P0–P2 rationale (below). | The frozen 3-key set **misnamed** the 0.4 cut and could not reproduce P2's 4 risk cuts (`mock.py:62-69`). |
| R6-3 | rule-table integrity | D-16 / D-24 (`docs/p3/P3_SENTINEL_DESIGN_INPUT.md` §1.4) | PAUSE row drops `work_off_track`; VERIFY row reduced to evidence/uncertainty; the 0.4 band moves to a **STEER** row (`score >= steer_risk`); FINISH row bounded by `score < finish_risk`. Row order still == `POLICY_PRIORITY`. | R5 ruling #7 removed `work_off_track` as a decision input; the 0.4 band must map to STEER to reproduce P2. |
| R6-4 | signing module location | D-33 | Spec §4 import corrected to **`from jcc_core.signing import …`**. | §1/§2.5/D-33 place `signing.py` in the neutral core; §4 contradicted them. |
| R6-5 | config shape / D6 | D-24 | `p3.shadow.thresholds` has **no `enabled`**; Sentinel **D6** retargeted from the *retired* `config.policy.thresholds` to the *live* `p3.shadow.thresholds`. | The retired block is inert; the test must exercise the live surface. |
| R6-6 | characterization test | D-24 | Strengthened from "recorded delta" to **ASSERTED EQUALITY** (engine-path `policy.decide` == P2 `policy.decide` on the recorded P2 fixtures). | Makes R6-2 enforceable; a delta escape hatch would let a live-mapping regression ship silently. |

**R6-2 rationale (the load-bearing argument).** Because `policy.decide` becomes a thin wrapper over the kernel, a `0.4 → VERIFY` reading would change the **live engine's** mapping (STEER→VERIFY for `0.4 ≤ risk < 0.6`) — a behaviour change to P1/P2-verified code introduced by a *shadow* phase. **P3 is shadow-only; it must not alter the live engine's decision mapping.** The 4-key form makes "thin wrapper ⇒ equivalence by construction" and "nothing re-opens P0–P2" true **simultaneously**.

**Round-6 confirmations:** `aep/v0.1` untouched; all new fields optional/defaulted; no DDL on frozen P0–P2 tables; P3 non-actuating; **D-1…D-41 FROZEN again**. This round's decision text changes are confined to **D-24** (plus spec cross-references); no decision was added or removed.

## Addenda — precision / contract-recording (lead-authorized; range unchanged D-1…D-41)

Lead-authorized **precision / contract-recording only** (carried in the lead's post-Round-6 follow-ups). **No new decision**, design intent unchanged, nothing re-opens P0–P2. Range stays **D-1…D-41**.

| # | Item | Decision | What changed | Why |
|---|---|---|---|---|
| **Add A** | server-boundary **mechanism** frozen | **D-26** (text above) | Server boundary = **structured `422` + audit row, NO synthesised directive**; provider boundary keeps **EXACT_BLOCKER → ESCALATE**; only the server boundary uses `4xx`+audit. `422` is the ratified shape (forge `e77037d`). | `422` had **zero hits** across the ADR/spec; the mechanism was left as an ambiguous "blocked/EXACT record (or a structured 4xx)". |
| **Add B** | shadow-path **provider contract** (spec-only) | D-31 / D-26 (mirrored in spec) | A provider MUST emit **all six required boolean signals** (`REQUIRED_SIGNAL_FIELDS`) **plus `band` and `score`**, and set `provider_verified` from its own `verified` attribute. **`work_off_track` is recorded-only** (carried on the assessment, read by no row, never a blocker — D-16 / R5-7 / R6-3). Omission of any required signal, `band is None`, or `score is None` ⇒ a blocker ⇒ **row 1 ⇒ EXACT_BLOCKER → ESCALATE** — **designed fail-closed behaviour, not a defect**. Recorded in `docs/p3/P3_ARCHITECTURE_SPEC.md` §2.3 (subsection) + §5 matrix row. | The provider contract was implicit; a gate could misread fail-closed ESCALATE as a bug. Also, a *recorded-only* field must not escalate when absent. |
| **8→6** | signal-count correction (Sentinel doc) | D-31 / D-35 / D-16 (`docs/p3/P3_SENTINEL_DESIGN_INPUT.md`) | `docs/p3/P3_SENTINEL_DESIGN_INPUT.md:84/:152/:394/:401` "8 fields/signals" → **6 required** (+ `band`/`score`; `work_off_track` recorded-only) — matches `decision.py:61-73` (`REQUIRED_SIGNAL_FIELDS` = 6 booleans; `RECORDED_SIGNAL_FIELDS = ("work_off_track",)`). §1.2 gains an explicit "6+1 split" note. (`:393`'s "8 existing tests" is a different eight — untouched.) | Internal contradiction. **Lead corrected the count 7→6:** a *recorded-only* field (R5 #7 / R6-3) must not escalate when it is absent. |

**Addenda confirmations:** `aep/v0.1` untouched; all new fields optional/defaulted; no DDL on frozen P0–P2 tables; P3 non-actuating; **D-1…D-41 unchanged**.

## Errata — reconciliation of frozen contradictions (E-1…E-4; lead-authorized; range unchanged D-1…D-41)

Lead-authorized **errata only** (the *Round-6 pattern* applied to items raised after the Round-6 re-freeze). Each item **reconciles text frozen in D-1…D-41 with observed implementation behaviour** — **no new decision**, design intent unchanged, nothing re-opens P0–P2. Range stays **D-1…D-41**. E-3 resolves the last open CHANGE REQUEST (the `disabled`/`real` provider band+signals contract, task #53) by the lead's ruling **(c)** — declared abstention — which needs **no re-open and no schema change**.

| # | Item | Decision(s) reconciled | Record | Why |
|---|---|---|---|---|
| **E-1** | two distinct dispatch **ports** carry two distinct **reason strings** | D-3 / D-27 / D-41 | The **engine** `ActuationPort` records `reason="shadow_mode"` (`actuator.py:74`); the **Sentinel** `DispatchPort` records `reason="shadow_no_actuation"` (`supervisor/ports.py:24`). They are **deliberately distinct** and each is pinned independently (`test_p3_core_integration.py:127`, `:286`). | D-41 makes the two ports **distinct types**; recorded so a future gate does not "unify" the two reason strings. Declared divergence, not accidental drift. |
| **E-2** | *(reserved)* | — | **Reserved — folded into the Round-6 table.** Not reproduced from any artifact. (The lead's candidate — "audit-tape row count exceeding event count" — was **not** recovered; **not asserted**.) | E-1/E-2 were reserved for the R6-1 items; unrecovered content is marked reserved rather than invented. |
| **E-3** | D-35 abstention scoping | D-35 (restored) / D-31 (F2) / D-16 | See §E-3 below. | Vacuously, every in-enum band with `provider_verified=True` still hits `required_signal_unknown` first — **you cannot band your way to VERIFY**; scoping the *absence* while leaving `provider_unverified` intact restores D-35's stated intent. |
| **E-4** | missing-evidence + high risk absorbs before the evidence row | D-35 row ordering / D-24 | See §E-4 below. | Ratified fail-closed engine-mapping change (P2-provider short-circuit removed); recorded as a **named exception**, never toward a resuming action. |

### E-3 — D-35 abstention scoping (reconciles frozen F3 with D-35's blanket row-1 rule)

**Statement.** `required_signal_unknown` and `risk_level_unknown` are blockers **only when the provider supplied evidence** — a signal set present with a required member UNKNOWN (partial supply), or a provider claiming to be a scoring provider that supplied nothing. When a provider **declares non-scoring abstention** (explicit declaration in the **existing free-form `raw` mapping**, e.g. `raw["provider_abstained"] is True`) **and** supplies **no numeric channels** (empty `probabilities`, falsy `score`), the absence of the six required signals is **incomplete evidence → row 5 → VERIFY**. **All other blockers are unaffected, including `provider_unverified`** — unverified providers and any **undeclared** absence still fail closed to **ESCALATE**.

**Rationale.** `decision.py:464-466` states an exact blocker means the signals *"cannot be trusted or attributed"*, *"strictly worse than **incomplete evidence (that case is VERIFY, unchanged from P1)**"*. Absent signals are not untrustworthy signals, so the blanket rule over-reached its own stated intent — scoping **restores** D-35 rather than amending it. Frozen **F3** (`docs/p3/P3_SENTINEL_DESIGN_INPUT.md:340`) independently requires `DisabledProvider → VERIFY`.

**Consequences (to record).**
- `disabled` (non-scoring; declared abstaining; `verified=True`) → **VERIFY** (F3). **Load-bearing: this requires BOTH the declaration AND `provider_verified=True`** (D-31) — without the latter, `provider_unverified` fires first ⇒ `ESCALATE`. It must **NOT** emit the six booleans — they are unknowable to it, and choosing values to reach an action would re-introduce provider-side directive selection that D-32 removed. It MUST also supply a **present falsy** score (`risk_score=0.0`, **not `None`**): `score_unknown` is **not** scoped by E-3, so `score is None` still `ESCALATE`s (fail-closed).
- `real` (`verified=False`, **not** declared abstaining) → `provider_unverified` → **EXACT_BLOCKER → ESCALATE** (frozen **F2**, `:339`).
- **No schema change:** the declaration rides in the existing free-form `raw` mapping; **no re-open of D-31/D-35**. The scoping is Errata-grade (the Round-6 pattern), justified by D-35's **own** docstring.

### E-4 — missing-evidence + high risk absorbs before the evidence row (ratified engine-mapping change)

**Statement.** On the **missing-evidence** path with **risk ≥ 0.6**, the engine now returns **PAUSE/ESCALATE** where P2 returned **VERIFY**. P2's provider short-circuited to VERIFY **before reading risk** (old `mock.py:56-61` early-returned with `needs_verify=True` and set no risk flag), so a session at `risk=0.997` was labelled VERIFY. Rows 1–4 precede row 5 and `POLICY_PRIORITY` + `test_policy.py:62-64` mandate that order, so **this cannot be fixed by reordering**.

**Measured** (two independent probes agreeing): forge **777/2000 (38.9%)**, sentinel **1566/4000 (39.2%)**, all in `risk ∈ [0.6, 1.0)`, outcomes `{PAUSE, ESCALATE}`; remaining cells exact VERIFY; evidence-present corpus **0 divergent**.

**Ratification.** Fail-closed safety improvement (never toward a resuming action), recorded as a **named exception** — **§6.11 strict equality is scoped to the evidence-PRESENT corpus**. Pinning test: `test_engine_path_missing_evidence_corner_is_measured_not_hidden`. **Range stays D-1…D-41** — no decision added or removed.

**Errata confirmations:** `aep/v0.1` untouched; all new fields optional/defaulted; no DDL on frozen P0–P2 tables; P3 non-actuating; **D-1…D-41 unchanged**.

## Consequences

- **Positive:** the shadow chain is real and observable but cannot act; the future-actuator seam is a single named port; arbitration is provably deterministic; Metric gets a falsifiable, tamper-evident record; the P2 dependency-inversion mistake is structurally prevented (Protocols downward, implementation upward, wiring at the root); the Metric gap-register is resolved without migrating any frozen table.
- **Negative / cost:** a second plane to build and keep boundary-clean; signing introduces key management (env-only); budget accounting is per-process and must be journalled to survive restart-honesty.
- **Constraint preserved:** `session_id` primary; default-deny on ambiguity; Jev scorer-only, Policy decides; P3 non-actuating; loopback-only; UI not source of truth.

## Verification labels

- P0–P2 actuation is OFF and every directive is `acted=False`: **VERIFIED** (`actuator.py:51-58`, `core.py:143-145`).
- Engine dispatch site is `core.py:143` (permanently inert; **never** an attach point): **VERIFIED**; the sole P4 attach point is `Sentinel._dispatch` — **DESIGN (D-41)**.
- Directive `signed=False` semantics in P0–P2: **VERIFIED** (`schemas.py:135`, `core.py:138-139`).
- `state_store` / `audit` hash-chain primitives usable as-is: **VERIFIED** (`audit.py:37-97`, `state_store.py:292-357`).
- `packages/supervisor` exists: **VERIFIED ABSENT** (P3 creates it).
- `packages/jcc_core/redaction.py` exists: **VERIFIED ABSENT** (P3 prerequisite, D-9).
- Sentinel arbitration determinism, signing, budget, shadow tape, Metric recompute: **UNVERIFIED** — design only; no P3 code exists yet.
- ADR-0008 **FROZEN** at D-1…D-41 (lead rulings A–I + P4 attach-point ruling D-41): **FROZEN (design)**.
