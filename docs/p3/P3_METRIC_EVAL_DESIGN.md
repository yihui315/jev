# P3 — METRIC EVALUATION DESIGN (Jev Shadow)

- **Status:** DESIGN INPUT — feeds ADR-0008 (owner: Atlas-2) + the P3 evaluation harness. **Reconciled to ADR-0008 FROZEN (D-1…D-41, 2026-09-24)** — §5 amended for D-27/D-32/D-40/D-41; **Round 5 @ `d9979e2` folded: D-24 corrected (no `thresholds.enabled` toggle; kernel always applies thresholds; `policy.decide` = thin wrapper) → §3.1 rewritten; Q1 RESOLVED = UNIFIED (engine≡shadow by construction) ⇒ C1 is policy-vs-GOLD (§1.1)**; §6 G→D map resolved (see ADR-0008 G→D table). **Open: Round-6 threshold-key precision (3-key `Thresholds` cannot reproduce P2's 4 risk cuts).**
- **Author:** Metric (jcc-evaluation-scientist, P3+ only).
- **Date:** 2026-09-24
- **Scope:** READ-ONLY. This document modifies **no** product file, test, or config. It produces threshold *recommendations*, confidence intervals, data-quality judgments, and experiment conclusions only. No production policy is edited; no actuation is enabled.
- **Baselines read (citations are `file:line` against frozen P2 `7f62f57` / docs `a4e7916`):**
  `packages/jcc_core/schemas.py`, `core.py`, `policy.py`, `actuator.py`, `state_store.py`, `audit.py`, `config.py`, `providers/{base,mock,real,disabled}.py`, `providers/__init__.py`, `server.py`, `config/jcc.yaml`, `docs/ADR/ADR-0002/0003/0004/0007`, `artifacts/acceptance/P2_ACCEPTANCE_REPORT.md`.

**Verification labels:** every claim below is tagged **VERIFIED** (read in frozen source), **DERIVED** (computed from VERIFIED structure), or **UNVERIFIED** (assumption / needs a P3 experiment).

---

## 0. Executive summary (one paragraph)

The frozen data plane is **sufficient to *record* shadow decisions but *insufficient to *score* them.** Every directive already carries `acted`, `actuation_reason`, `policy_version`, `target_actuator`, and an `evidence_hash` (`schemas.py:127-139`), and each ingest appends exactly one hash-chained audit row (`core.py:166-175`, `audit.py:45-97`) — so "shadow ≠ actuation" is **already mechanically provable today** (§5). But there is **no ground-truth outcome field, no per-call latency, no cost/usage, no confidence, and no applied-threshold record**, and `config.policy.thresholds` (`config.py:61-67`) is **dead config: consumed by nothing** (`policy.py:40-49` reads only boolean flags). Therefore P3's evaluation harness cannot be built on the existing tables alone; §2 and §6 enumerate the exact schema/audit extensions Atlas must resolve before any quality metric can be computed. **No actuation may ever be enabled while these gaps remain (§4, fail-closed).**

---

## 1. Falsifiable definition of "shadow supervision improves decisions"

### 1.1 Claim under test

> **C1:** For each agent `a ∈ {workbuddy, claude, codex, hermes}`, on a **held-out labeled replay corpus** of real sessions, the deterministic policy's directive sequence (computed shadow-only, `acted=false`) predicts the **ground-truth per-directive action** strictly better than a **null baseline policy**, by a **pre-registered margin**, with **upper 95% CI of the improvement > 0** and minimum sample size met.

The system under test is the *pipeline* `Assessment → policy.decide(assessment, status)` (`core.py:125-140`, `policy.py:40-49`), not Jev alone. The metric is **directive-level agreement with ground truth**, not raw score correlation.

> **C1 is policy-vs-GOLD, never policy-vs-engine.** Per the frozen architecture (atlas-2 authoritative reading, Round 5), the engine and the Sentinel call the **same** kernel with the **same** per-agent→default resolver ⇒ **engine ≡ shadow by construction**, so `shadow.action == engine.action` is **trivially 1.0** and is **not** a metric (at most a same-kernel regression guard). The experiment therefore compares **recorded decisions/outcomes against the oracle/human labels** (§1.2). The shadow's marginal contribution is **observability / tamper-evidence / gradability**, not a second policy — so a NULL here means *the policy (kernel+thresholds) does not beat the do-nothing baseline on gold*, which is exactly the claim under test.

### 1.2 Ground truth obtainable WITHOUT actuation (shadow-only)

Two tiers, so we can run at scale and still anchor validity:

- **Tier-1 — the "next-event oracle" (fully passive, automatic).** From the already-persisted `events` table (`state_store.py:49-60`) we read the *observable future* after each directive and derive a label without any side-effect:
  - `FINISH` directive but ≥1 substantive `event_type ∈ {tool_call, agent_message, evidence}` occurs within the next **W** events → **false_finish**.
  - `CONTINUE`/`STEER` directive but an `event_type=error` (`schemas.py:36`) or `SessionStatus.QUARANTINE` follows within **W** events → **missed_intervention**.
  - `PAUSE`/`ESCALATE` directive but no adverse event and no stall within **W** events → **false_pause** / **false_escalate**.
  - `VERIFY` directive where `evidence.complete=0` at decision time (`state_store.py:69,262`) → **verify_warranted**.
  - Window **W** must be in **event-count**, not wall-clock, because `events.timestamp` is `event.timestamp or 0.0` (`state_store.py:160`) and is caller-supplied → **not monotonic across agents** (CONSTRAINT, §6/G-6).
- **Tier-2 — human-adjudicated gold labels on a stratified sample.** Reviewers assign the *desired* action per directive on a random stratified subset (strata = agent × band). Used to (a) measure the oracle's own precision/recall and (b) debias Tier-1.

**Ground truth is shadow-only by construction**: labels are read from persisted telemetry or supplied by a reviewer. Jev never acts; no label depends on an actuation having happened.

### 1.3 The NULL hypothesis (what "does NOT help" looks like)

Define `Δ = AUROC(shadow policy) − AUROC(null baseline)` on the held-out test split, where the **null baseline** is the *no-supervision* policy:

- **Baseline-B0 (do-nothing):** always `CONTINUE`.
- **Baseline-B1 (contract minimum only):** `VERIFY` iff evidence missing/incomplete, else `CONTINUE` — i.e. exactly what `DeterministicMockProvider.assess` already does via `needs_verify` (`mock.py:55-60`).

**H0 (shadow supervision does NOT improve decisions) is "not rejected" iff, on the ≥N_min corpus:**
1. `Δ ≤ 0`, **or** the 95% bootstrap CI of `Δ` **includes 0**; **or**
2. improvement exists but is **not agent-wide** (present for ≥3 agents but absent for ≥1, i.e. concentrated/leaky); **or**
3. improvement is **inside the cost noise band** — i.e. `Δlatency_p95` and `Δcost/session` exceed the pre-registered budget with no decision-quality gain (cost buys nothing); **or**
4. the result is **not reproducible** across two disjoint resamples (split-half sign flips).

A NULL outcome is a **valid, reportable result.** It is a **hard stop for P4**: no actuation is enabled on a NULL (§4).

### 1.3a CORPUS STATUS = NOT OBTAINABLE WITHIN P3 (lead ruling, 2026-09-24)

**The quantitative verdict C1 is DEFERRED — not faked.** Rationale (lead): we currently have **zero real sessions** — no live telemetry (all four agent connectors are `AUTH_REQUIRED`) and no recorded real sessions to replay. Therefore neither *live collection* nor *replay* can reach `N_min ≈ 200 labeled directives/agent` inside P3.

- **What P3 DOES deliver:** the **mechanism** + a fixture-validated harness + everything provable without a corpus (§5 A1–A11 probes, arbitration determinism, chain tamper-evidence, G-2 mock-non-evidential marking).
- **Every corpus-dependent metric** (§2.1–2.7, 2.10–2.13 and T12–T16) MUST be reported as **"NO CORPUS — non-evidential"**. **No mock-derived number is a verdict.**
- **Acquisition trigger (record explicitly):** the verdict becomes obtainable once **(a)** the four agents are actually in use (connectors authorized) **AND (b)** ≥ `N_min` labeled directives/agent accrue over **≥ 2 disjoint windows**. Until then the P3 eval leg reports **DEFERRED + reason**, **never** a pass.

**Pre-registered margins (proposed, to be frozen before data collection — unchanged by the corpus deferral):** `δ_min = +0.10 AUROC` over B0 and `+0.05` over B1; action-rate budget `≤ 25%` non-CONTINUE directives/session; latency budget `p95 ≤ 1500 ms/call`; cost budget to be set once a real provider exists (§2.9).

---

## 2. Metric definitions + data sources

Legend: **src** = table.column (with `file:line`); **join** = key used; **gap** = what is missing.

| # | Metric | Definition / formula | Source fields | Join | Gap / constraint |
|---|---|---|---|---|---|
| 2.1 | **shadow decision agreement** | exact-match rate `= Σ[directive.action == gold_action] / Σ directives`, reported with **Cohen's κ** (chance-corrected) per agent | `directives.action` (`state_store.py:80-93`, written `:305`); audit `directive="ACTION:directive_id"` (`core.py:173`) | `directive_id = f"dir_{session_id}_{event_id}"` (`core.py:131`) → recover `event_id` | **G-1: no `labels`/gold store exists** → needs a sidecar corpus or a `labels` table (§6). |
| 2.2 | **false pause rate** | `#(action=PAUSE ∧ oracle≠pause) / #(action=PAUSE)` | `directives.action`; lookahead over `events.event_type`,`events.payload` (`state_store.py:49-60`) | session_id + event-count window | **G-6:** wall-clock window unreliable (per-agent clock skew). Use event-count W. |
| 2.3 | **false finish rate** | `#(action=FINISH ∧ substantive event follows) / #(action=FINISH)` | as 2.2 | as 2.2 | **G-6.** This is the **highest-cost** FP for safety (premature completion). |
| 2.4 | **false escalate rate** | `#(action=ESCALATE ∧ no adverse event) / #(action=ESCALATE)` | as 2.2 | as 2.2 | — |
| 2.5 | **stuck detection P/R** | "stuck" = no `tool_call`/`agent_message` progress within W **and** repeated `payload` fingerprint. `P=TP/(TP+FP)`, `R=TP/(TP+FN)` where detection = policy `PAUSE`; gold = Tier-1/2 label | `events.event_type`, `events.payload`; `evidence.fingerprint` (`state_store.py:62-70`, `:262`) | session_id | **G-3: no derived `seconds_since_progress`/stall feature exists** → harness must compute (§6). |
| 2.6 | **off-track detection P/R** | detection = `STEER`/`ESCALATE`; gold = "session diverged from declared goal" | — | — | **G-4: OFF-TRACK IS CURRENTLY UNDEFINABLE.** No goal/task/plan field exists; `project` is metadata-only, explicitly barred from identity (`ADR-0007 D-6`, `schemas.py:70,85`). Needs a goal reference in AEP `payload` or a human label. **Atlas must resolve or drop this metric.** |
| 2.7 | **verification usefulness** | precision proxy `= P(evidence.complete=0 | action=VERIFY)`; recall proxy `= P(missing evidence supplied within K events)` | `evidence.complete` (`state_store.py:262`), `events` ordering by `seq` | session_id + seq ordering | Needs seq-ordered join (audit `seq` is monotonic, `audit.py:19-20`). |
| 2.8 | **latency p50/p95** | per-call `jev_call_ms = jev_finished − jev_started`; ingest `= directive.timestamp − ingest_started` | `directives.timestamp` (`core.py:133`); `assessments.timestamp` (`mock.py:47`) | — | **G-5: NO call/ingest timestamps.** `assessment.timestamp` = `event.timestamp or time.time()` (`mock.py:47`), `directive.timestamp = now` (`core.py:132`) → both are ingest-time, differ only by execution. **Today only throughput is derivable**, not per-call latency. Needs `ingest_started_at` + `jev_started_at`/`jev_finished_at`. |
| 2.9 | **Jev calls/session** | `= COUNT(assessments) per session_id` | `assessments.id` (`state_store.py:71-79`, `:270-289`) | session_id | — (computable today). |
| 2.10 | **cost/session** | `= Σ usage_cost per session` | `assessments.data` JSON — `provider`,`model` present (`state_store.py:276-284`); free-form carrier `raw` (`schemas.py:124`). | session_id | **G-7: no usage/tokens/cost field.** Mock is free; `RealProvider` makes no call (`real.py:36-51`). Needs `raw.usage={prompt_tokens,completion_tokens,...}` + a versioned price map. |
| 2.11 | **agent-specific calibration** | reliability curve + **Brier score** + **ECE** of `probability.risk` vs adverse-outcome label, **per agent** | `assessments.agent`, `assessments.data.probabilities.risk`, `...risk_score` (`schemas.py:115-117`) | session_id + timestamp | **G-2 (blocking):** mock `risk_score` is a SHA-256 hash, uniform in [0,1) (`mock.py:27-31`) → **structurally uncalibratable.** Calibration claims require the real scorer or a scored corpus; mock-only results MUST be labeled "not a calibration claim." |
| 2.12 | **confidence calibration** | as 2.11 but over the provider's stated confidence for `needs_verify`/`escalate` | — | — | **G-8: `Assessment` has NO `confidence` field** (`schemas.py:107-124`). Add one + persist. |
| 2.13 | **threshold drift** | PSI / KS between a **versioned reference window** and the current window of `risk_score` per agent; also drift of the *realized action rate* | `assessments.data.risk_score`, `.agent`, `.timestamp` | agent + window | **G-9: the numeric thresholds actually applied are NOT persisted** — only `directives.policy_version` string (`schemas.py:134`, `:309`). Cannot attribute a shift to threshold vs. data. Persist applied thresholds in `directive.params` (currently `{}`, `schemas.py:133`) or store a policy-config hash. |

**Data-quality note (VERIFIED):** the `assessments` table **has no `event_id`** (`state_store.py:71-79`) — an assessment couples to its directive only via `(session_id, timestamp)`. Because `directive_id` *does* embed `event_id` (`core.py:131`) and each ingest writes exactly one audit row (`core.py:166-175`), the reliable join path today is **audit `seq` → directive_id**, not assessment↔event. This makes 2.11–2.13 fragile: they need a stricter key (§6/G-10).

**Metric-validity note (Round 5, UNIFIED):** for §2.1, the **only** admissible pairing is **`directive.action` vs GOLD `gold_action`** (Tier-1/Tier-2, §1.2). **Do NOT** compute "agreement" as `directives.action` vs `shadow_decisions.action` — under engine≡shadow-by-construction that is **trivially 1.0** and would falsely report perfect agreement. Same rule for §2.2–2.4: denominators are gold-labelled, not engine-labelled.

---

## 3. Per-agent threshold calibration

**Mandate (core contract):** one unified stuck/finish threshold MUST NOT cover all four agents. WorkBuddy / Claude / Codex / Hermes differ in workload, hook kind, and status (`config/jcc.yaml:44-76`: codex `DEGRADED`, hermes `signed-outbound-hook`, etc.), so their risk distributions differ.

### 3.1 Where thresholds live (frozen ADR-0008, Round 5 @ `d9979e2`)

- **Pre-P3 (superseded):** `config.policy.thresholds = {escalate_risk:0.8, pause_risk:0.6, verify_risk:0.4}` (`config.py:61-67`, `jcc.yaml:34-37`) was **consumed by NOTHING** (grep-verified); `policy.decide` mapped **boolean flags only** (`policy.py:30-49`) and the mock re-hardcoded 0.8/0.6/0.4 into those flags (`mock.py:62-70`).
- **Frozen P3 (Round 5, commit `d9979e2`):** `decision.arbitrate` **ALWAYS** applies thresholds to `risk_score` (per-agent `per_agent[agent]` → `default`); there is **NO `thresholds.enabled` toggle** — the "disabled ⇒ defer to provider flags" branch is **deleted**. `policy.decide` becomes a **thin wrapper over the same kernel**, so equivalence is **by construction**. Config surface = `p3.shadow.thresholds: {default, per_agent}`; `config.policy.thresholds` is **RETIRED** (ADR-0008 D-24).
- ⇒ the threshold surface is now **live, per-agent, and single-sourced** (kernel `jcc_core/decision.py`). **There is no two-path equivalence test** — it is replaced by a **characterization test against recorded P2 provider-flag fixtures** (the mapping delta is explicit and reviewed, not asserted-equal). My harness must **not** assert `arbitrate == policy.decide` over a two-config toggle; where it needs determinism, it re-runs the kernel with the *same* recorded thresholds (see §5 A5-note / T12).
- **Calibration-surface (Round-6 item; atlas-2 escalation):** P2's risk→action cuts are **0.8 ESCALATE, 0.6 PAUSE, 0.4 STEER, 0.1 FINISH**; `[0.1, 0.4)` → CONTINUE. **VERIFY is evidence-driven** (`needs_verify`, `band=="unknown"`), **NOT a risk cut** (`mock.py:62-69`, `policy.py:30-49`). The frozen `Thresholds{escalate_risk, pause_risk, verify_risk}` has **3 keys and misnames the 0.4 cut as `verify_risk`** → it **cannot** reproduce P2's 4 risk cuts, so the Round-5 characterization test would show a **spurious delta**. Correct surface = `{escalate_risk:0.8, pause_risk:0.6, steer_risk:0.4, finish_risk:0.1}`, with VERIFY left evidence-driven. **HOLD the characterization-fixture + parity-probe wording until Round 6 lands**; safety probes (A1–A11, D-40) are unaffected.
- **RESOLVED (atlas-2, authoritative reading of the frozen text): Option 2 — UNIFIED.** D-32 (`policy.decide` is a thin wrapper over the **same** kernel; "the engine and the shadow plane call the **same** function") + D-24-R5 (arbitrate **ALWAYS** derives the action, per-agent → `default`) ⇒ the engine's `policy.decide` resolves **exactly like** the Sentinel. **Engine ≡ shadow by construction.**
  - ⇒ **`shadow.action == engine.action` is trivially 1.0 — it is NOT a metric.** Keep it, at most, as a same-kernel *regression guard*, never as "agreement".
  - ⇒ **C1 is policy-vs-GOLD** (recorded decisions/outcomes vs oracle/human labels), **never** policy-vs-engine.
  - ⇒ the shadow's marginal value = **observability + signing + budget + the P4 attach seam + independent recomputability** — it makes the decision tamper-evident and gradable; it does **not** add a second policy. Consistent with ADR-0008's "observe, arbitrate, record."
  - **Implementation flag (atlas-2):** `policy.decide(assessment, status)` (`policy.py:40`) takes **no** threshold config → the wrapper needs the single threshold resolver injected by the composition root; the engine and shadow must share **one** resolver, else "by construction" fails silently.

### 3.2 Method (per agent `a`)

1. **Corpus.** Freeze a replay corpus per agent with Tier-1 labels on all sessions + Tier-2 gold on a stratified sample (§1.2).
2. **Objective.** Cost-sensitive, not raw accuracy. Set `cost(false_finish) ≫ cost(false_pause) > cost(false_verify)` (safety-first). Select `τ_a = (escalate, pause, verify, steer)` minimizing expected cost on the **validation** split.
3. **Sweep.** 1-D / coordinate sweep per threshold (monotone monotone in risk) — grid of ~50 points; no gradient methods on tiny data.
4. **Split by session, never by event.** Events within a session are correlated; splitting by event leaks. Use 60/20/20 **session-level** train/val/test.
5. **Report.** Point estimate + **1000× session-level bootstrap** 95% CI for every metric in §2.
6. **Refresh policy.** Thresholds are a **frozen, versioned artifact**. Refresh ONLY on (a) a scheduled cadence (e.g. monthly) or (b) a drift alarm (`PSI > 0.25`). **No continuous auto-tuning** — auto-tuned shared state is itself an automation hazard.
7. **Record.** Each refresh bumps `policy_version` and stores the config hash + applied thresholds per directive (§6/G-9).

### 3.3 Minimum sample-size & confidence rules (guard against overfitting)

- **Proportion estimates:** Wilson 95% half-width `≈0.15` needs `n ≈ 30`; `≈0.10` needs `n ≈ 100`. **Per agent per action-of-interest** (PAUSE/FINISH/VERIFY counts, not total events).
- **Threshold selection stability:** ≥ **200 labeled directives per agent** (≈10–20 events per estimated parameter; ≤4 thresholds → ≥80 floor, target 200).
- **Do NOT publish a threshold** whose metric CI spans the decision boundary → mark agent **UNCALIBRATED** and **fall back to fail-closed `VERIFY`** (`policy.py` already defaults to VERIFY on ambiguity per `ADR-0003`).
- **Overfitting guard:** split-half stability — if the selected τ moves by more than the pre-registered tolerance between two independent resamples, treat as **unstable → revert to default, do not ship**.
- **No calibration claim on mock data.** Per G-2, mock scores are a hash; calibrating a threshold on them is overfitting to a hash. P3 threshold results under `provider=mock` MUST be labeled **"mock-only, non-evidential."**

---

## 4. Unsafe-automation guardrails (gates a future P4)

**Fail-closed default: `actuation.enabled=false`, `allowed_actions=[]` (`config.py:48-50`, `jcc.yaml:27-29`).** Actuation could be considered **only if ALL of the following hold**, per agent, on held-out data:

| # | Condition | Threshold (proposed) | Evidence required |
|---|---|---|---|
| G1 | **Decision quality** | `false_finish ≤ 0.01` **and** `false_escalate ≤ 0.02`, **upper 95% CI < the cap** (not point estimate) | §2.3/2.4 on test split |
| G2 | **Sample sufficiency** | ≥ **500** labeled directives **and** ≥ **100** distinct real sessions **per agent**, over ≥ **2** disjoint time windows | §3.3 |
| G3 | **Calibration** | Brier `≤ 0.15` and `ECE ≤ 0.05`; reliability slope ≈ 1 | §2.11 (requires real scorer) |
| G4 | **Stability** | metrics stable across 2 disjoint resamples; `PSI < 0.25` | §2.13 |
| G5 | **Reversibility** | one-command kill-switch + rollback **tested** (ADR-0006); only **reversible/idempotent** actions allowed; `FORBIDDEN_EFFECTS` (shell_exec, browser_open, file_delete, external_send, git_push, prod_db_write, network_bind — `actuator.py:21-29`) remain permanently excluded | rollback drill log |
| G6 | **Shadow↔armed parity** | replay of the exact directives that *would* actuate reproduces the shadow record bit-for-bit | dry-run diff = ∅ |
| G7 | **Human-in-the-loop** | high-risk bands still require human approval (`ADR-0004:20`) | policy design |
| G8 | **Budget** | `calls/session`, `cost/session`, `p95 latency` within ceilings; hard rate limiter present | §2.8–2.10 |

**Fail-closed triggers — actuation stays OFF if ANY is true:** metric UNKNOWN/UNVERIFIED · sample < N_min · provider is `mock` or the `real` stub (`real.py:10`) · drift alarm firing · audit chain not intact (`state_store.py:397-403`) · session isolation unproven (`ADR-0003`).

**Any "enable actuation" recommendation from this role MUST state all four (per core contract):**

> **(i) false-positive risk** (per-agent FP point estimate + upper CI), **(ii) sample size** (n directives, n sessions, n windows), **(iii) confidence** (95% CI on each gating metric), **(iv) rollback signal** (defined *before* enabling).

**Rollback signal (must be armed pre-enable):** *"if realized `false_finish` over any rolling 50 actuations exceeds `0.01`, auto-disable actuation and revert to the prior `policy_version` within one ingest cycle."* Monitors: realized FP rate, audit-chain intactness, latency/cost budget.

---

## 5. Verification that shadow ≠ actuation

Concrete assertions/probes for the P3 harness (run against the frozen P3 DB; a P3 GO requires **all** PASS):

> **Frozen-ADR alignment (2026-09-24, ADR-0008 FROZEN at D-1…D-41).** The probes below are reconciled with the frozen decisions. Where the freeze strengthened a probe, the ADR form wins:
> - **D-40(a–e)** supersedes my A1/A4/A5/A7/A8/A9 intent with five stronger falsifiers: (a) poison-pill tripwire (`execute_calls == 0`), (b) static AST scan of the shadow path (no `execute`/`os.system`/`subprocess`/`socket`/`urllib`/writes; only `verify_envelope`/`can_act`), (c) end-to-end over the real server (all `acted=false`, chain intact), (d) persisted assert `SELECT COUNT(*) FROM directives WHERE acted=1 == 0`, (e) a negative control proving (a)+(d) *can* fail. My A1–A11 remain valid but MUST be read as *additional* to D-40, not instead of it.
> - **D-41** resolves my earlier ADR↔spec contradiction: the **only** P4 attach point is `Sentinel._dispatch`; `core.py:143` is **permanently inert** (never an attach point). There are **two distinct ports** (`jcc_core.ports.ActuationPort` at the engine; `supervisor/ports` dispatch at the Sentinel). The harness must probe **both** inert.
> - **D-27** makes the engine's ingest path verify-only (`actuator.execute` unreachable from ingest; `actuation_reason="shadow_mode"`), and **D-1** gives the Sentinel path `actuation_reason="shadow_no_actuation"`. These are NEW reason values — A2/A9 below are updated accordingly (the P2 value `actuation_disabled` is no longer produced on the ingest path).

| ID | Assertion / probe | Backing evidence |
|---|---|---|
| A1 | `SELECT COUNT(*) FROM directives WHERE acted=1` **= 0** | `directives.acted` (`state_store.py:80-93,305`); served `/api/decisions` (`server.py:121-123`) |
| A2 | DB reasons are **exactly the two port values**: `directives.actuation_reason == 'shadow_mode'` (engine port, `core.py:146`) and `shadow_decisions.actuation_reason == 'shadow_no_actuation'` (Sentinel port, D-1). **Neither may be `actuation_disabled`.** Scope strictly to the two *persisted* surfaces — the legacy string `actuation_disabled` still exists but is reachable **only** by a direct `Actuator.execute()` unit call (forge-confirmed, 2026-09-24), which never persists. | `core.py:144-146`; `actuator.py:51-58`; ADR-0008 D-1/D-27/D-41 |
| A3 | loaded config `actuation.enabled == False` **and** `allowed_actions == []`; `/api/health.actuation_enabled == False` | `config.py:48-50`; `server.py:100` |
| A4 | `Actuator.can_act(d)` is `False` for **every** directive in the tape (property test) | `actuator.py:43-49` |
| A5 | every dispatch on **both** ports returns `acted=False`/`executable=False`: engine `jcc_core.ports.ActuationPort` (verify-only, D-27) **and** `Sentinel._dispatch` (D-41). Spy must be scoped to the **port surfaces only** — a *direct* `Actuator.execute()` class call legitimately still returns `actuation_disabled`; it is NOT a port surface and must not be counted (forge-confirmed; `test_actuator.py:14` stays valid, the `Actuator` class survives unchanged per D-3). | ADR-0008 D-3/D-27/D-40(b)/D-41 |
| A6 | every directive `target_actuator == 'noop'` — no real actuator name ever used | `core.py:135`; `server.py:146` |
| A7 | `verify_audit() == (True, N)` at end of P3 **and** after restart/replay | `state_store.py:397-403`; `audit.py:100-127` |
| A8 | **negative control (throwaway sandbox):** forcing `enabled=True` with empty `allowed_actions` still yields `action_not_allowed`; production artifact hashes (jcc.yaml + core files) unchanged | `actuator.py:54-55` |
| A9 | log scan on the **port surface**: only inert-outcome lines (no side-effect line). Non-vacuity is guaranteed by **D-40(a)**: the engine `dispatch` **IS** called once per ingest (`acted=False`), so the probe always exercises a real port call; the legacy `Actuator.execute` string path is pinned separately by the D-40(a) tripwire. | `actuator.py:60-65`; ADR-0008 D-27/D-40(a) |
| A10 | audit↔event is 1:1 **for ingested agent events only**: `COUNT(audit_tape WHERE agent IN {four agents}) == COUNT(accepted events)`; each such audit row's `ACTION:directive_id` matches a `directives` row. **CORRECTION:** the naive `COUNT(audit_tape) == COUNT(events)` is **wrong** — the manual control endpoints append audit rows with `agent="control-plane"` that are not tied to any ingested event (`server.py:139-160`, still append after D-28). Exclude `agent="control-plane"` (and any non-agent emitter) from the equality. | `core.py:166-175`; `server.py:150-159`; `audit.py:64-97` |
| A11 | **no new inbound surface** (P2 guardrail preserved): `/api/hooks` still 404; mutating endpoints remain exactly `/api/assess,pause,steer,retry` and all resolve to DENIED | `P2_ACCEPTANCE_REPORT.md:82-88`; `server.py:133-172` |

**Important (DERIVED, CONFIRMED by the freeze):** the **audit tape alone cannot prove actuation was off** — its payload is `{event_id,timestamp,session_id,agent,project,assessment,directive,evidence_hash}` (`audit.py:64-74`) and the `directive` field is the lossy string `ACTION:directive_id` (`core.py:173`). The `acted`/`actuation_reason` truth lives in the **`directives` table** (`state_store.py:80-93`). A1/A2/A4 therefore read the DB, not the tape. My recommendation to fold `acted` into the hash-chained row was resolved as **D-23**: the frozen `audit_tape` stays byte-frozen, and the **new `shadow_decisions` tape** hash-covers `acted=false` + `actuation_reason` instead. ⇒ the harness must read **three** surfaces: frozen `directives` (A1/A2), new `shadow_decisions` (D-40d + spec test #2), and the *content-free* `audit_tape` (A7/A10 integrity only).

---

## 6. SCHEMA / AUDIT INSUFFICIENCY REGISTER (design constraints for Atlas)

Everything the current record **cannot** supply. Each needs an ADR-0008 decision (extend schema vs. sidecar corpus vs. drop the metric).

> **RESOLVED (ADR-0008 FROZEN):** every row below now maps to a decision — G-1→D-13, G-2→D-14, G-3→D-15, G-4→**D-16 (dropped)**, G-5→D-17, G-6→D-18, G-7→D-19, G-8→D-20, G-9→D-21, G-10→D-22, G-11→**D-23 (refused; shadow tape covers it)**, G-12→D-24. The table is retained as the *provenance* of the constraints, not as open work.

| ID | Missing | Blocks | Severity |
|---|---|---|---|
| G-1 | no **ground-truth / label** store | §2.1–2.6, §1 | **BLOCKER** |
| G-2 | mock `risk_score` is a hash, not a probability (`mock.py:27-31`) | §2.11/2.12 calibration | **BLOCKER for calibration claims** |
| G-3 | no derived **stall / seconds-since-progress** feature | §2.5 | HIGH |
| G-4 | no **goal/task/plan** field → off-track **undefined**; `project` is metadata-only (`ADR-0007 D-6`) | §2.6 | HIGH |
| G-5 | no **ingest/call timestamps** → latency not derivable | §2.8 | HIGH |
| G-6 | `events.timestamp` caller-supplied, non-monotonic; windows must be event-count | §2.2–2.5 | MEDIUM |
| G-7 | no **usage/tokens/cost** field; `raw` is the only carrier | §2.10 | MEDIUM |
| G-8 | no **`confidence`** field on `Assessment` | §2.12 | MEDIUM |
| G-9 | **applied numeric thresholds not persisted** (only `policy_version` string) | §2.13, §3 refresh | HIGH |
| G-10 | `assessments` has **no `event_id`** — assessment↔event join is `(session_id,timestamp)`-fragile | §2.11–2.13 | HIGH |
| G-11 | **`acted`/`actuation_reason` not in the hash chain** (audit payload `audit.py:64-74`) | §5 tamper-evidence of the safety flag | MEDIUM |
| G-12 | `config.policy.thresholds` is **inert** (`policy.py` never reads it; mock re-hardcodes 0.8/0.6/0.4) | §3 entirely | **BLOCKER** |

**Minimum P3 schema delta Atlas must decide:** (a) make `thresholds` actually feed `policy.decide`, keyed by agent; (b) persist applied thresholds + `policy_version` hash per directive; (c) add `event_id` to `assessments`; (d) add `confidence` + `raw.usage` to `Assessment`; (e) add ingest/jev-call timestamps; (f) a label store or documented sidecar corpus; (g) fold `acted` into the audit hash chain.

---

## TESTS (what the P3 harness must run)

| ID | Test | Expected | Notes |
|---|---|---|---|
| T1–T11 | §5 A1–A11 probes | all PASS | automation-required; fail-closed on any FAIL |
| T12 | replay determinism: same corpus fixture → identical directive sequence | PASS | mock is deterministic by construction (`mock.py:1-9`) |
| T13 | metric recomputation reproducibility (2× runs, same DB) | identical to tolerance 0 | catches join nondeterminism |
| T14 | split-half threshold stability | within tolerance | §3.3 |
| T15 | audit chain intact pre/post shadow run | `(True,N)` | §5/A7 |
| T16 | **NOT YET EXECUTABLE**: calibration (T14-adjacent) and cost — **blocked** on G-2/G-7 | SKIP with reason | must be recorded as skipped, not passed |

**Not executed at design time:** all of T1–T16 (this document is design-only; no product code changed). Reason: READ-ONLY mandate.

---

## RISKS

| Sev | Risk | Owner | Next step |
|---|---|---|---|
| **BLOCKER** | No ground truth (G-1) / inert thresholds (G-12) → eval harness not buildable as-is | Atlas (ADR-0008) | decide label store + threshold wiring |
| **BLOCKER (calibration)** | Mock scores uncalibratable (G-2) → any calibration claim is invalid | Atlas / provider owner | require real scorer or scored corpus; label mock results non-evidential |
| HIGH | Latency/cost unmeasurable (G-5/G-7) → cannot price the shadow benefit | Atlas + Forge | add timestamp + usage fields |
| HIGH | Off-track undefined (G-4) | Atlas | add goal ref or drop metric |
| MEDIUM | Audit tape omits the safety flag (G-11) | Atlas | extend audit payload |
| MEDIUM | `assessments` join fragility (G-10) | Atlas/Forge | add `event_id` |
| LOW | Caller-supplied `event.timestamp` skew (G-6) | Metric | event-count windows (§2.2) |
| **RESOLVED (lead ruling)** | Labeled corpus at `N_min≈200`/agent is **NOT obtainable within P3** (zero real sessions; all four connectors `AUTH_REQUIRED`) → **C1 DEFERRED, not faked**; every corpus-dependent metric = **"NO CORPUS — non-evidential"** (§1.3a). | team-lead (ratified 2026-09-24) | re-open when (a) agents actually in use **and** (b) `N_min` labeled directives/agent accrue over ≥ 2 disjoint windows |

---

## DECISION

**GO WITH CONDITIONS — as a P3 evaluation *design input* only.**

- This document is **INFORMATION ONLY** w.r.t. any other gate; I sign **no** architecture, security, or SRE gate. I do not authorize any actuation.
- **§6 blockers RESOLVED IN DESIGN** by the frozen ADR-0008 (D-1…D-41): G-1→**D-13**, G-2→**D-14**, G-12→**D-24**; G-4→**D-16 (dropped/closed-negative)**, G-11→**D-23 (refused; shadow tape covers the flag)**; HIGH items G-5→D-17, G-9→D-21, G-10→D-22. Full G→D map in ADR-0008 §"G→D resolution table". ⇒ the harness is now **specifiable**; it still cannot *run* until the P3 code (satisfying D-9 prerequisite, D-27/D-32, D-13/D-17) lands and passes the independent gates.
- **No P4 actuation** is permitted on a NULL result (§1.3) or while any §4 fail-closed trigger holds; the frozen D-41 designates `Sentinel._dispatch` as the **only** P4 attach point, and P3 stays non-actuating.

---

## HANDOFF

- **Next owner:** **Forge** — build the P3 harness to the frozen spec (`P3_ARCHITECTURE_SPEC.md` §6/§9) using §5 A1–A11 **as amended** by the frozen-ADR alignment note above + D-40(a–e); **Harbor/Aegis** for the §4/§5 gate leg at P3 close. Atlas-2 owns ADR-0008 (now **FROZEN**).
- **Artifacts to pass:** this file (`docs/p3/P3_METRIC_EVAL_DESIGN.md`); ADR-0008 (`docs/ADR/ADR-0008-p3-jev-shadow-supervision.md`), esp. the G→D table + D-40/D-41; `P3_ARCHITECTURE_SPEC.md` §6.
- **§5 amendments for the harness builder (must not be missed):** (a) `actuation_reason` values are now `shadow_mode` (engine, D-27) / `shadow_no_actuation` (Sentinel, D-1) — **not** `actuation_disabled`; (b) probe **two distinct ports** (engine `core.py:143` permanently inert; `Sentinel._dispatch` = sole P4 attach point, D-41); (c) A10's naive `COUNT(audit_tape)==COUNT(events)` is **wrong** — exclude `agent="control-plane"` manual rows; (d) D-40(a–e) is the authoritative falsifier set and supersedes my A1/A4/A5/A7/A8/A9 *intent*.
- **Facts the lead must forward:** (1) `config.policy.thresholds` is **dead config** (ADR-0008 D-24 retires it; `p3.shadow.thresholds` is live, per-agent); (2) mock scores **cannot** support calibration — calibration requires the real scorer (D-14); (3) the audit tape **does not** contain `acted` — safety proof reads `directives` + `shadow_decisions` (D-23); (4) **off-track is dropped** and **STEER is unscored** in P3 (D-16) — report as DEFERRED, never `0/N-A`.
- **Do not let the next phase misunderstand:** "shadow improves decisions" is a **falsifiable AUROC-delta claim vs. a do-nothing baseline** (§1), **not** a restatement of "the pipeline runs." A NULL result is a legitimate, reportable outcome and a hard **NO-GO** for actuation.
