# P3 SENTINEL DESIGN INPUT — Policy / Supervision / Actuation Contract

- **Deliverable type:** DESIGN INPUT to **ADR-0008** (owner: Atlas). Not a gate. Not a product change.
- **Author / owner:** Sentinel (supervision, policy, actuation contract)
- **Date:** 2026-09-24
- **Reviewed state:** `git rev-parse HEAD` = `a4e7916d56ad056c20af981abfa485a76984910d`; `git status --porcelain` = `?? _atlas_ls.txt` (not authored by me). No product file, test, config or source module was modified, created or deleted by this task.
- **Inputs read (all at HEAD):** `packages/jcc_core/{core,policy,actuator,schemas,state_store,audit,config,server,run}.py`, `packages/jcc_core/providers/*`, `packages/jcc_core/adapters/{_common,hermes}.py`, `packages/jcc_core/tests/{test_policy,test_actuator,test_audit,test_acceptance_harness}.py`, `packages/conftest.py`, `config/jcc.yaml`, `integrations/_common/jcc_receiver.py`, `docs/ADR/ADR-{0002,0003,0004,0005,0006,0007}*.md`, `artifacts/acceptance/P2_ACCEPTANCE_REPORT.md`.
- **Doc location note:** `artifacts/` is gitignored by ADR-0006 §2 (`docs/ADR/ADR-0006-version-control-and-rollback.md:26`), so this file is an untracked acceptance artifact like every other `artifacts/acceptance/P*.md`.

---

## 0. Assumptions (state them; challenge any of them)

| ID | Assumption | Status |
|---|---|---|
| A1 | P3 keeps `actuation.enabled: false` and adds **no** code path that can set it true. `config/jcc.yaml:27-29`, `actuator.py:51-58`. | **VERIFIED** (state today) |
| A2 | `Assessment` / `Directive` are **internal** models, not the AEP wire contract. The frozen wire contract is `AEPEnvelope` + `version="aep/v0.1"` (`schemas.py:62-79`). Extending the internal models therefore does **not** breach the P2 condition "AEP wire contract unchanged" (`P2_ACCEPTANCE_REPORT.md:88`). | **UNVERIFIED** — needs Atlas's explicit confirmation in ADR-0008 |
| A3 | "Signed directive" in P3 means **symmetric HMAC-SHA256** with a key from the environment, unless ADR-0008 rules otherwise. Asymmetric non-repudiation is out of scope (see §3.7). | **UNVERIFIED** (design choice) |
| A4 | Budget counters and the signing key come from **env / persisted state**, never from config files; **no new third-party dependency**. | **UNVERIFIED** (design choice) |
| A5 | The audit tape's `entry_hash` **input key set is frozen forever** (P0–P2 rows must keep verifying). This is the single hardest constraint in §6. | **VERIFIED** as a requirement, derived from `audit.py:100-127` |
| A6 | Sentinel's authority here is the supervision/policy/actuation contract only. The real Jev scorer's internals (typesafe endpoint, token accounting) are **out of scope**; I only define the *interface* Jev must satisfy. | scope statement |
| A7 | P3 is **shadow**: no actuator is invoked. This document does not authorise any P3 actuation. | **VERIFIED** (`P2_ACCEPTANCE_REPORT.md:94`) |

**Headline finding before anything else.** Today the **provider already decides the directive**. `DeterministicMockProvider.assess` sets `escalate=`/`pause=`/`steer=`/`finished=` (`providers/mock.py:62-69`) by hard-coding the thresholds 0.8/0.6/0.4; `policy.decide` then merely walks those flags (`policy.py:30-49`). Meanwhile `PolicyConfig.thresholds` (`config.py:61-67`, `config/jcc.yaml:34-37`) is **dead config** — the name appears nowhere else in `packages/` or `config/`. So there are three sources for "risk → action": the provider's flag block, the provider's band cuts (`mock.py:37-42`), and an unused config block. And `config.py:65` names a threshold `verify_risk: 0.4` while `mock.py:66-67` maps `risk >= 0.4` to **STEER**, not VERIFY. P3 must collapse this to one source (§1.6). This is the highest-value thing in this document: as long as the scorer picks the action, "deterministic policy arbitration" is a fiction and ADR-0002's "Jev outputs probabilities ONLY; Policy produces the directive" is not actually met.

---

## 1. Deterministic policy arbitration

### 1.1 Contract shape

```
arbitrate : (Signals, Thresholds, BudgetState) -> Decision
```

- `Signals` = pure, total normalisation of the Jev assessment (§1.2).
- `Thresholds` = **`p3.shadow.thresholds`** (`{default, per_agent}`, no `enabled`) — the *only* consumer, §1.6. (**Round 6:** `config.policy.thresholds` is RETIRED/inert per ADR-0008 D-24.)
- `BudgetState` = counter snapshot (§2).
- `Decision` = exactly one `DirectiveAction` + a machine-readable `reason_code` + the ordered list of matched rows.

**Hard rules for the arbitration loop:**
1. **No model calls, ever.** `policy` must not import `providers`, `adapters`, `socket`, `requests`, `urllib`, `http`, `subprocess`, `time`, or `random`. Enforced by an AST test (§1.7 D4).
2. **Pure + total.** No I/O, no clock, no RNG, no exception escape. Every input — including `None`, `NaN`, wrong types, missing keys, extra keys — must resolve to one action.
3. **First-match-wins over a fixed row order.** The row order *is* the tie-break. There is no second tie-break mechanism, and none is needed (§1.4).
4. **`decide()` must remain callable** as a thin wrapper so `policy.priority_index` and the module surface used by `tests/test_policy.py:43-45` survive. See §6-1 for the required test rewrite.

### 1.2 Stage 1 — Normalisation (pure, total, no exceptions)

Input field set (fixed; this is the Jev assessment contract for P3):

| Signal | Source key | Type | Normalised to |
|---|---|---|---|
| `meaningful_progress` | `meaningful_progress` | bool \| float \| str | `True \| False \| UNKNOWN` |
| `worker_stuck` | `worker_stuck` | bool \| float \| str | same |
| `work_off_track` | `work_off_track` | bool \| float \| str | same |
| `requirements_satisfied` | `requirements_satisfied` | bool \| float \| str | same |
| `needs_verification` | `needs_verification` | bool \| float \| str | same |
| `ready_to_finish` | `ready_to_finish` | bool \| float \| str | same |
| `needs_human` | `needs_human` | bool \| float \| str | same |
| `risk_level` | `risk_level` | str | `0..3` ordinal, or `UNKNOWN` |
| `band` | `band` | str | `0..3` ordinal, or `UNKNOWN` |
| `score` | `risk_score` (or `probabilities["risk"]`) | float | `[0,1]`, or `UNKNOWN` |

Coercion is a **fixed table**, not Python truthiness:

```
bool  -> itself
float -> UNKNOWN if NaN or not in [0,1]; else True if p >= FIELD_DECISION_THRESHOLD else False
int   -> 0 -> False, 1 -> True, anything else -> UNKNOWN
str   -> casefold+strip in {"true","yes","1"} -> True
                          {"false","no","0"}  -> False
                          else                -> UNKNOWN
None / missing key / any other type -> UNKNOWN
```

- `FIELD_DECISION_THRESHOLD = 0.5`, config-overridable. **The boundary is `>=`**, i.e. `p == 0.5 → True`. Documented explicitly because a boundary is a tie.
- `risk_level` / `band` map `low=0, medium=1, high=2, critical=3`, everything else → `UNKNOWN`.
- Nothing in normalisation raises. Anything unexpected yields `UNKNOWN`, which is *absorbed* by the blocker rule (§1.3). This is what makes the function total.
- **Required vs recorded — the 6+1 split (Add-B correction; matches `decision.py`).** The *required* signal set is the **6 booleans** `meaningful_progress, worker_stuck, requirements_satisfied, needs_verification, ready_to_finish, needs_human` (`REQUIRED_SIGNAL_FIELDS`, `decision.py:61-68`) **plus `band` and `score`**. **`work_off_track` is recorded-only** (`RECORDED_SIGNAL_FIELDS`, `decision.py:73`): carried on the assessment for the record, read by **no** row, and it **never** blocks when absent (D-16 / R5 #7 / R6-3).

### 1.3 Stage 2 — EXACT_BLOCKER detection (precedes the rule table)

```
EXACT  if  any of:
  a) any of the 6 required signals is UNKNOWN
  b) risk_level is UNKNOWN or band is UNKNOWN
  c) score is UNKNOWN (NaN / out of range / absent)
  d) assessment is None, not an Assessment, or the provider raised
  e) provider_verified is False
  f) CONTRADICTION between two same-tier authoritative signals (threshold-INDEPENDENT):
       - risk_score and probabilities["risk"] disagree — the provider's own two numeric channels
         (NOT number-vs-policy-derived-band: per-agent thresholds make that unsatisfiable — Divergence 2)
       - probabilities contains a value outside [0,1] or NaN
       - probabilities is an empty dict while risk_score is present (or vice versa)
```

- **Ties are NOT contradictions.** Two rows matching at different tiers is resolved by row order (§1.4). Contradiction (f) means two signals *claim the same authority* and cannot be ordered by the priority table at all.
- `blocker_reasons` is a **sorted, de-duplicated** list of the fixed reason codes `a..f`. Sorted+deduped so the directive params and the resulting `evidence_hash` are byte-stable for a given input set — determinism requires it.
- `provider_verified` comes from `JevProvider.verified` (`providers/base.py:18`; `real.py:24` is `False`, `mock.py:22` is `True`). Today policy infers this from `band == "unverified"` (`real.py:50`) — a band value that is **not** in the documented band set (`schemas.py:117` lists `low|medium|high|critical|unknown`). P3 must carry an explicit boolean instead of a magic string.

### 1.4 Stage 3 — The rule table (first match wins; row order == tie-break)

Rows are numbered; **the number is the priority**. The directive column, read top to bottom, MUST equal `POLICY_PRIORITY` (`schemas.py:157-165`) exactly — that equality is itself a test (§1.7 D5), so the table and the mandated order cannot drift.

| # | Predicate (pure function of normalised signals) | Directive | reason_code |
|---|---|---|---|
| 1 | `blocker_kind == EXACT` | `ESCALATE` | `exact_blocker` |
| 2 | `needs_human is True` | `ESCALATE` | `needs_human` |
| 3 | `risk_level == critical` **or** `score >= escalate_risk` | `ESCALATE` | `risk_critical` |
| 4 | `worker_stuck is True` **or** `risk_level == high` **or** `score >= pause_risk` | `PAUSE` | `stuck_or_high_risk` |
| 5 | `needs_verification is True` **or** `risk_level == unknown` **or** `evidence_complete is False` | `VERIFY` | `needs_verification` |
| 6 | `meaningful_progress is False` **or** `score >= steer_risk` | `STEER` | `no_progress_or_steer_band` |
| 7 | `requirements_satisfied is False` **and** `ready_to_finish is False` | `RETRY` | `requirements_open` |
| 8 | `ready_to_finish is True` **and** `requirements_satisfied is True` **and** `needs_verification is False` **and** `risk_level == low` **and** `score < finish_risk` | `FINISH` | `ready_to_finish` |
| 9 | (unconditional floor) | `CONTINUE` | `floor` |

Properties of this table that must be asserted, not assumed:
- **Reachability:** every one of the 7 actions has ≥1 input that selects it.
- **Coherence:** `[row.action for row in ROWS] == POLICY_PRIORITY`.
- **Priority correctness:** for any input, `decide(x) == min(ROWS matching x, key=priority_index)`. This is a property test, not an example test (D3).
- **No `UNKNOWN` reaches rows 2–9** (rows 1 absorbs them all), so rows 2–9 need no `UNKNOWN` branch — which is what keeps the table readable and the semantics closed.
- `score` may be `UNKNOWN` only when the blocker already fired (1.3c). Rows 3–5 therefore never see `UNKNOWN`.

> **Round-6 corrections (lead-authorized; ADR-0008 D-16/D-24).** (i) `work_off_track` is **not a decision input** — R5 ruling #7 removed it; the PAUSE row no longer matches it (it stays on `Assessment` as a *recorded signal* only). (ii) The **0.4 band maps to STEER** (`score >= steer_risk`), and the VERIFY row is **evidence/uncertainty only** — `verify_risk` is **deleted** (VERIFY is never a risk band). (iii) FINISH is bounded by `score < finish_risk` (0.1) to reproduce P2. (iv) The cut set is `{escalate_risk: 0.8, pause_risk: 0.6, steer_risk: 0.4, finish_risk: 0.1}`, sourced from `p3.shadow.thresholds` — **no `enabled`, no `verify_risk`**.

### 1.5 Stage 4 — Determinism sources pinned

Enumerate every source of non-determinism and state its disposition:

| Source | Disposition |
|---|---|
| dict iteration order | **eliminated** — only named key lookups; `blocker_reasons` sorted |
| float comparison boundary | **pinned** — `>=`, and floats quantised to 6 dp before comparison so 0.7999999 and 0.8000001 differ by a documented epsilon rather than by float noise |
| provider `band` produced with different cuts than policy | **eliminated** — contradiction is a blocker (1.3f); in P3 the provider is also changed to stop cutting (§6-9) |
| clock / RNG | **absent** from `policy` (AST test D4) |
| config reload mid-run | `Thresholds` snapshot is taken once per `CoreEngine` construction and its hash is recorded on the directive (§3.2 `policy_table_version`) |
| model/provider calls | **absent** (ADR-0002:41; contract rule 1) |

### 1.6 One source of truth for thresholds

Required change: `policy` reads **`p3.shadow.thresholds`** (`{default, per_agent}`, no `enabled`; **Round 6** — the old `config.policy.thresholds` is RETIRED/inert per ADR-0008 D-24); `providers/mock.py` stops reading 0.8/0.6/0.4 at `mock.py:62-69` and stops cutting bands at `mock.py:37-42` (or, minimally, the band function is moved into policy and the provider returns only `probabilities`). Cut set **`{escalate_risk: 0.8, pause_risk: 0.6, steer_risk: 0.4, finish_risk: 0.1}`** — **`verify_risk` is deleted**: the 0.4 cut maps to **STEER** (resolving `config.py:65` vs `mock.py:66-67`), and VERIFY is evidence-driven. Asserted by `test_thresholds_single_source` — mutate a threshold in config and observe the directive change.

### 1.7 Required tests (all falsifiable, all named)

| ID | Test name | Assertion |
|---|---|---|
| D1 | `test_decide_is_pure_repeatable` | `decide(x)` twice on the same frozen input → identical action **and** identical `reason_code`/params |
| D2 | `test_decide_is_total_fuzz` | 2^11 adversarial input corpus (seed pinned) incl. `None`/`NaN`/`"yes"`/`2`/missing keys/extra keys/nested junk → never raises, always returns exactly one `DirectiveAction` |
| D3 | `test_decide_equals_lowest_priority_index_match` | property: returned action == argmin priority over all matching rows |
| D4 | `test_policy_module_has_no_io_or_model_imports` | AST scan of `policy.py`: no import/attribute of `providers`, `adapters`, `socket`, `subprocess`, `urllib`, `requests`, `time`, `random`; no `Call` to `.assess(` |
| D5 | `test_rule_table_matches_mandated_priority` | `[r.action for r in ROWS] == POLICY_PRIORITY == schemas.POLICY_PRIORITY` |
| D6 | `test_thresholds_single_source` | changing **`p3.shadow.thresholds.default.pause_risk`** (the LIVE surface; `config.policy.thresholds` is RETIRED/inert — D-24 R5/R6) changes the mapping; `providers/mock.py` contains no literal `0.8`/`0.6`/`0.4` threshold (grep-level assert) |
| D7 | `test_boundary_is_inclusive` | `score == escalate_risk` → ESCALATE; `p == 0.5` on a boolean field → `True` |
| D8 | `test_unknown_required_field_is_exact_blocker` | each of the 6 required fields set to `None` in turn → `blocker_kind == EXACT`, row 1 fires |

---

## 2. Budget model

### 2.1 What is metered

| Dimension | Scope | Unit | Default | Rationale |
|---|---|---|---|---|
| `assess_calls` | session | count | 200 | bounds scorer spend even while the P3 provider is free; the counter is the interface a real scorer plugs into |
| `scorer_tokens` | session | tokens | 200_000 | **the** real cost meter once `RealProvider` lands; 0 in P3 (mock) but the counter must exist now or the real provider ships unmetered |
| `events` | session | count | 2000 | bounds ingest/DB growth |
| `directives` | session | count | 200 | bounds directive churn |
| `retries` | session | count | 5 | `RETRY` is the only directive that can loop a worker; a loop is the most realistic automated harm in P3/P4 |
| `escalations` | session | count | 3 | bounds human-notification storms |
| `wall_clock_s` | session | seconds | 28800 (8 h) | bounds an abandoned session |
| `cost_units` | session | weighted | 500 | static cost table per action: `CONTINUE=0, VERIFY=1, FINISH=1, STEER=2, RETRY=3, PAUSE=5, ESCALATE=10` |
| `cost_units_per_hour` | agent | weighted | 2000 | aggregate bound across all of one agent's sessions |
| `directives_per_minute` | global | count | 600 | process-wide runaway brake |

### 2.2 Where the counts live (this is the load-bearing design choice)

Counters are **derived from persisted state**, not from in-memory dicts:

- `assess_calls` = `SELECT COUNT(*) FROM assessments WHERE session_id=?` (`state_store.py:71-79`)
- `directives` / `cost_units` / `retries` / `escalations` = aggregate over `SELECT action FROM directives WHERE session_id=?` (`state_store.py:80-93`)
- `events` = `COUNT(*) FROM events WHERE session_id=?` (`state_store.py:49-60`)
- `wall_clock_s` = `now - sessions.created_at` (`state_store.py:35-48`)

**Consequence: an in-memory counter cannot be reset by a restart, because there is no in-memory counter.** That directly satisfies the ADR-0006 stop/restart/rollback story (`run.py:114-148`) and removes a whole class of fail-open. A new `StateStore.budget_snapshot(session_id) -> dict` is the only new store surface. A dedicated `budget_ledger` table is *optional* and not required for P3.

### 2.3 Exact fail-closed behaviour on exhaustion

Ordering: budget is evaluated **after** arbitration and **before** the directive is recorded/returned (`core.py` between the current steps 5 and 6), so the exhaustion is itself recorded and auditable.

1. **Replacement, never suppression.** The arbitrated action is replaced by `ESCALATE`, with `params = {budget_exhausted: true, budget_kind: "<dimension>", budget_limit: <float>, budget_observed: <float>}`, `requires_human = true`, `executable = false`, `acted = false`. The originating **event is still persisted and still audited** (`core.py:105`, `:166-175` unchanged) — we cap the *directive*, we never drop the *record*.
2. **Never fail-open, never `CONTINUE`.** Exhaustion must not degrade to the floor. Silently continuing is the one outcome that keeps spending money and keeps a runaway worker running.
3. **Saturating, deterministic suppression.** Once `escalations` exceeds its cap, further exhausted events still *count* but set `params.suppressed = true` instead of emitting a new distinct escalation. The suppression predicate is a pure function of the integer counter — no time window, no jitter, no RNG, so it is replayable.
4. **Budget subsystem failure ⇒ exhausted.** If reading counters raises (DB locked, corrupt, schema error), treat as **exhausted**: `ESCALATE` with `budget_check_error = "<exception class>"`. A test must monkeypatch the counter read to raise and assert this, because fail-open here is easy to write by accident via a bare `except: pass`.
5. **Monotonic.** Counters never decrease within a session; assert by reading `budget_snapshot` twice.
6. **No off switch.** Do **not** add `budget.enabled`. A `false` default that someone flips is exactly how a budget control becomes decorative. If ADR-0008 wants an override it must require an explicit `acknowledge_unbounded: true` and must fail config load otherwise.

### 2.4 Config surface

New `budget:` block in `config/jcc.yaml` + `BudgetConfig` dataclass mirroring `PolicyConfig` (`config.py:53-67`). Bump `policy.version` `p1.0.0 → p3.0.0` (`config.py:55`, `config/jcc.yaml:32`). The budget snapshot is written onto every directive (`params.budget_kind/limit/observed`) so a directive can be re-adjudicated from the tape alone.

### 2.5 Required tests

`test_budget_exhaustion_matrix` (each dimension individually → ESCALATE + correct `budget_kind`), `test_budget_survives_restart` (exhaust, restart the real server via the existing harness pattern, assert still exhausted), `test_budget_check_error_fails_closed` (fault injection), `test_budget_never_returns_continue`, `test_budget_suppression_is_deterministic`, `test_budget_defaults_match_config_yaml`.

---

## 3. Signed directive

### 3.1 What "signed" must mean, precisely

A signed directive is a directive whose `(session_id, directive_id, issued_at, ttl_s, nonce, action, target_actuator, params, evidence_hash, policy_version, policy_table_version)` tuple has been **authenticated and integrity-protected** by a key the policy plane holds and the actuator plane independently holds, such that the actuator can decide to act **without trusting the transport or the caller**.

"Signed" does **not** mean: the directive is authorised. Authorisation is a separate, later check (§3.4 steps 6–9). Integrity ≠ permission. This distinction must appear in ADR-0008 in those words, because collapsing them is how "signed" becomes a rubber stamp.

### 3.2 Key source, algorithm, canonicalisation

- **Key:** symmetric HMAC key from env var `JCC_DIRECTIVE_KEY`. **Never** in `config/jcc.yaml` (which stores env-var *names* only — `config/jcc.yaml:1`: "Secrets are NEVER stored here"), never in SQLite, never in the log. Handled exactly like `JCC_CONTROL_TOKEN` today: read at runtime via a `Config` property (`config.py:81-83`) and compared with `hmac.compare_digest` (`server.py:85`).
- **Algorithm:** HMAC-SHA256, hex, prefixed `sha256=`. **Zero new dependencies** — `hmac`, `hashlib`, `secrets`, `json`, `time` are all stdlib and already in use (`core.py:18-20`, `server.py:17-21`).
- **This pattern already exists and is proven in-repo**: `integrations/_common/jcc_receiver.py:120-122` (`hmac.new(secret, canonical, hashlib.sha256)` → `"sha256=" + hexdigest`) with canonical = `f"{timestamp}.{delivery_id}.{agent}." + raw_body` (`:117-118`). P3 should copy the *shape*, not the module (§3.6).
- **Canonicalisation** (byte-stable, mirrors the existing canonical-json convention at `core.py:39-41` and `audit.py:37-38`):

```
canonical = (
  f"{policy_table_version}.{policy_version}.{session_id}.{directive_id}."
  f"{int(issued_at)}.{ttl_s}.{nonce}.{action}.{target_actuator}."
  f"{evidence_hash or ''}." 
  + json.dumps(params, sort_keys=True, separators=(",", ":"))
).encode("utf-8")
```

`nonce` and `ttl_s` are **inside** the signed payload — otherwise an attacker replays a valid signature with a fresh timestamp. `int(issued_at)` (not the float) avoids platform float-repr drift.

### 3.3 Verification point

Verification belongs to the **actuator / supervisor**, never to policy. Policy signs; the consumer verifies. In P3 there is no consumer, so P3 introduces a **dry-run** verification (`Actuator.verifyEnvelope(directive) -> {"verified": bool, "reason": str}`) that is guaranteed side-effect-free, and the engine calls *that* instead of `execute`. This is what makes P3 a genuine dry run rather than a "we promise we won't".

### 3.4 Verification checks — order is normative, first failure wins, no side effects

| # | Check | Failure reason |
|---|---|---|
| 1 | `signed is True` and `signature` present | `UNSIGNED` |
| 2 | recompute HMAC over §3.2 canonical; `hmac.compare_digest` | `BAD_SIGNATURE` |
| 3 | `now - issued_at <= ttl_s` and `issued_at - now <= clock_skew_s` | `EXPIRED` / `NOT_YET_VALID` |
| 4 | `nonce` unseen in the replay cache | `REPLAY` |
| 5 | `directive.session_id` == the session being acted on | `SESSION_MISMATCH` |
| 6 | `(actuator, agent, action)` in declared capabilities | `CAPABILITY_DENIED` |
| 7 | `policy_version == config.policy.version` and `policy_table_version == current` | `POLICY_MISMATCH` |
| 8 | budget re-checked at the effect boundary | `BUDGET_EXHAUSTED` |
| 9 | `human_approval` reference present + valid for any `requires_human` action | `APPROVAL_MISSING` |

**Check 4 must run after check 2.** Unauthenticated/forged directives must not be able to poison the replay cache — the same reasoning is already recorded at `jcc_receiver.py:166` ("replay check AFTER signature (unauthenticated traffic can't poison cache)"). Reuse that principle verbatim.

Checks 5–9 are the "all side-effects must verify signature/TTL/nonce/session/capability/policy/budget/human approval" list from the charter; 1–4 are "signature/TTL/nonce". All nine are one function so no caller can implement a subset.

### 3.5 Why the current schema carries `signed=False`

Not an oversight — four independent, currently-verified reasons:

1. **Explicit phasing.** `schemas.py:135-136` carry the comment `# P4/P5 signing; UNSIGNED in P1`, and `core.py:138-140` hard-codes `signed=False, signature=None`. ADR-0004 §Decision assigns signing to the Policy engine but ADR-0004 §Consequences places it in the upgrade path; P1/P2 deliberately shipped unsigned (`ADR-0004:17-20`, `ADR-0004:26`).
2. **No verifier exists.** With `actuation.enabled: false`, `Actuator.execute` always returns `_deny(...)` (`actuator.py:51-58`). A signature with no consumer is theatre: it adds a key-management surface with zero enforcement value.
3. **No key management was in scope.** `config.py` has **no** signing-key field, and the security baseline (`ADR-0005`) never established a key-distribution story. Introducing a key without that story would be worse than not signing.
4. **The storage columns are already there.** `directives.signed` and `directives.signature` exist (`state_store.py:88-89`) and `add_directive` writes them (`state_store.py:299-313`). So **making signing real requires no schema migration to store it** — only to produce it. That is the cheapest part of P3.

### 3.6 Minimal change to make signing real, no new dependencies

1. `config.py`: add `SigningConfig(key_env="JCC_DIRECTIVE_KEY", ttl_s=120.0, clock_skew_s=30.0, required=False)` + a `signing_key` property returning `os.environ.get(...)`. If the key is absent: directives stay `signed=False`, and `signing.required=True` makes `run.py` **refuse to start** (mirroring the fail-closed stance of `server.py:76-81`). Default `required=False` for P3 so P3 is not blocked on key provisioning; the acceptance harness sets the env var and thereby *exercises* the real path.
2. New `packages/jcc_core/signing.py` (stdlib only): `sign_directive(directive, key) -> str` and `verify_directive(directive, key, *, now, replay_cache) -> (bool, reason)` implementing §3.4 checks 1–4. Checks 5–9 stay in `actuator.verifyEnvelope` because they need actuator/capability/budget/approval context.
3. **Replay cache — do not import it upward.** `ReplayCache` lives at `integrations/_common/jcc_receiver.py:53-78`; `packages/` importing from `integrations/` would be a *new* dependency inversion of exactly the kind ADR-0007 D-3 already flags (`docs/ADR/ADR-0007-...md:34-38`, backlog D-1 at `P2_ACCEPTANCE_REPORT.md:73`). **Recommendation: perform the ADR-0007 D-3 hoist once** — move the redaction kernel *and* a shared `nonce_cache.py` into neutral `packages/jcc_core/`, and let both `adapters/*` and `integrations/*` import downward. One mechanical move, two consumers, and it closes a known MEDIUM.
4. `schemas.py` `Directive`: add `issued_at: Optional[float]`, `ttl_s: Optional[float]`, `nonce: Optional[str]`, `policy_table_version: Optional[str]`, `executable: bool = False`, `requires_human: bool = False`, `verify_reason: Optional[str]`, `shadow: bool = True`, `human_approval: Optional[str]`. Keep `signed`/`signature` semantics; `signed=True` now finally means "HMAC present and verified at issuance".
5. `actuator.py`: add `verifyEnvelope()` (§3.4); `execute()` calls it first and **still denies** in P3. `can_act` semantics unchanged. `FORBIDDEN_EFFECTS` (`actuator.py:21-29`) stays as the single source for the static scan test (§4.2b).
6. `state_store.py`: `get_directives` currently **does not select `signature`** (`state_store.py:325-338`) even though `add_directive` writes it (`:299-313`). So today a persisted signature is unreadable via the API. P3 must add `signature` (and the new columns) to the read path and to `_row_to_directive` (`:343-357`) — otherwise the dry-run verification cannot be replayed from stored state, which defeats the purpose.

### 3.7 Honest limitation to record in ADR-0008

HMAC is **symmetric**: it proves integrity and origin-as-holder-of-the-key, but the control plane itself holds the key, so it is **not** public non-repudiation against the control plane. If P4/P5 needs "the policy plane cannot forge a directive it did not issue", that requires asymmetric signing (Ed25519 / ECDSA) and therefore **a new dependency** (`cryptography`) or a hand-rolled implementation (which I do not recommend). This document intentionally does **not** resolve that; it flags it as a P4 decision. Label: **UNVERIFIED** (asymmetric option).

### 3.8 Required tests

`test_sign_verify_roundtrip`, `test_signature_rejects_tampered_params`, `test_signature_rejects_tampered_action`, `test_expired_ttl_rejected`, `test_not_yet_valid_rejected`, `test_nonce_replay_rejected`, `test_replay_check_is_after_signature` (forge with a fresh nonce but bad signature → `BAD_SIGNATURE`, and the replay cache must be *empty* afterwards), `test_session_mismatch_rejected`, `test_capability_denied`, `test_policy_version_downgrade_rejected`, `test_missing_approval_rejected`, `test_no_signing_key_keeps_signed_false`, `test_signing_required_without_key_refuses_start`, `test_signature_is_readable_from_store` (the §3.6-6 gap), `test_verify_envelope_has_no_side_effects`.

---

## 4. Shadow semantics

### 4.1 Definition

```
shadow(event) = assess(event) → arbitrate(assessment) → sign → verify(dry-run) → record(acted=false)
```

| Stage | Module | Note |
|---|---|---|
| assess | `provider.assess(event, session)` (`providers/base.py:20-22`) | unchanged; Jev stays scorer-only |
| arbitrate | `policy.arbitrate(...)` (§1) | exactly one directive, deterministic |
| sign | `signing.sign_directive(...)` (§3) | `signed` true iff a key is configured |
| verify (dry-run) | `actuator.verifyEnvelope(...)` (§3.3) | **no side effects**, result stored in `verify_reason` |
| record | `store.add_assessment` / `add_directive` / `append_audit` (`core.py:164-175`) | unchanged shape |
| result | `directive.acted = False`, `actuation_reason = "shadow_mode"`, `executable = False`, `target_actuator = "noop"` | `target_actuator` already `"noop"` at `core.py:135` |

**The one code change that matters:** `core.py:143` (`result = self.actuator.execute(directive)`) must not be reached on the shadow path. In shadow mode the engine calls `verifyEnvelope` and sets `acted=False` directly. Today `execute` is called on **every** ingest even though it always denies (`core.py:143-145`) — correct by accident, not by design.

### 4.2 The invariant

> **INV-SHADOW (P3).** For every ingested event, in every phase, `Actuator.execute` is never invoked; no side-effecting syscall is made by the JCC process as a consequence of a directive; every recorded directive has `acted == False`, `executable == False`, `target_actuator == "noop"`, and a `verify_reason`.

Also state the *static* half: P3 adds **no** runtime path that can set `actuation.enabled = true`, and `Actuator.execute` keeps `return self._deny(directive, "real_actuation_not_implemented")` (`actuator.py:58`). The invariant is therefore a property of the code, not of a config flag.

### 4.3 How the invariant is proven — five independent strategies, one of which is a negative control

**(a) Poison-pill / tripwire actuator (dynamic, strongest).**
Inject an `Actuator` subclass whose `execute()` increments `execute_calls` and then `raise AssertionError`. Run the whole pipeline (unit + real-HTTP harness). Assert `execute_calls == 0` **and** the tripwire's sentinel file does not exist **and** the pipeline still produced N directives. A false PASS is structurally impossible: any invocation both counts and raises.
Test: `test_shadow_never_invokes_actuator_tripwire`.

**(b) Static AST scan (structural, independent of runtime).**
AST-walk every module reachable from the shadow entrypoint; assert no `Call` to `actuator.execute`/`.execute(`, `os.system`, `subprocess.*`, `socket.*`, `urllib*`, `requests.*`, `shutil.*`, or an `open(..., "w")` on the shadow path; the only permitted actuator calls are `.verifyEnvelope(` and `.can_act(`. The scan must also assert that **none of `actuator.FORBIDDEN_EFFECTS` (`actuator.py:21-29`) appears in the shadow path** — reusing the existing constant so the test and the actuator cannot drift apart.
Test: `test_shadow_path_has_no_sideeffect_calls`.

**(c) End-to-end over the real server (integration).**
Extend `test_acceptance_harness.py`: for each fixture in `packages/jcc_core/tests/fixtures/*.json` (14 fixtures exist; they cover all four agents), POST to `/api/assess`, then assert via `/api/decisions` that every directive has `acted == false`, `executable == false`, a non-empty `verify_reason`, and `signed` consistent with whether the harness set `JCC_DIRECTIVE_KEY`; then assert `/api/audit/verify` == `{"intact": true}` with exactly one new tape entry per event (`server.py:117-128`).
Tests: `test_shadow_directives_all_acted_false`, `test_shadow_audit_chain_intact`.

**(d) Persisted-artifact assert (hardest to fake).**
`acted` is persisted (`state_store.py:80-93`). Assert `SELECT COUNT(*) FROM directives WHERE acted = 1` == 0 over the whole DB after the run. This reads the artifact on disk, not the in-memory object, so an object that lies cannot pass.
Test: `test_shadow_db_has_zero_acted_directives`.

**(e) Negative control — prove the test can fail.**
Meta-test: instantiate a stub actuator that really returns `{"acted": True, "reason": "test_only"}` behind an explicit test-only flag, run the shadow pipeline, and assert that (a) and (d) **DO** fail. Without this, (a) is a toothless assertion. This mirrors the existing, already-accepted pattern at `test_acceptance_harness.py:412-458` (`test_bind_check_detects_real_0_0_0_0_bind`), which exists precisely because a bind check that cannot fail is worthless.
Test: `test_shadow_invariant_is_falsifiable`.

---

## 5. Fail-closed rules

Every row: trigger → observable → outcome. All outcomes are non-actuating and **recorded**; no silent path anywhere.

| ID | Trigger | Outcome | Evidence / citation |
|---|---|---|---|
| F1 | `provider.assess` **raises**. Today `core.py:125` is uncaught → HTTP 500 from `/api/assess` (`server.py:133-137`), the event **is** already persisted (`core.py:105`) but never gets an assessment, directive or audit entry → the tape has a **hole** relative to `events`. | Catch at the boundary; synthesise an assessment with `blocker_kind=EXACT`, `blocker_reasons=["provider_error"]` → **ESCALATE**, and **still append the audit entry**. | **Finding — current behaviour is fail-open on the audit tape.** `core.py:124-125`, `audit.py:100-127` |
| F2 | Unverified provider (`real.py:24` `verified=False`). Today policy infers this from `band == "unverified"` (`real.py:50`) — a value outside the documented band set (`schemas.py:117`). | Carry an explicit `provider_verified` flag; `False` → **EXACT_BLOCKER** → **ESCALATE**. | **Finding — undeclared band value used as a control signal.** |
| F3 | `jev.provider=real` requested but flag/key absent. | `build_provider` already fails closed to `DisabledProvider` (`providers/__init__.py:38-43`) → `needs_verify=True` (`disabled.py:30`) → **VERIFY**. Keep as-is; add a regression test. | **VERIFIED (already fail-closed)** |
| F4 | Missing evidence, or `schema_ok=False`. | **VERIFY** / await. Never auto-safe, never FINISH. `mock.py:55-60`, `schemas.py:8-9`, `ADR-0003:21`. | **VERIFIED (already fail-closed)** |
| F5 | **EXACT_BLOCKER** — see §5.1. | **ESCALATE**, `requires_human=true`, `executable=false`. | this doc §1.3, §5.1 |
| F6 | Contradictory same-tier signals (band vs derived band; out-of-range probability; `risk_level` not in enum). | **EXACT_BLOCKER** → **ESCALATE**. Ties between *different* tiers are **not** contradictions — row order resolves them (§1.4). | this doc §1.3f |
| F7 | Budget exhausted, or the budget read raises. | **ESCALATE** + `budget_exhausted` / `budget_check_error`. Never fail-open, never `CONTINUE`. | §2.3 |
| F8 | Signature invalid / expired / replayed / session mismatch / capability denied / policy downgrade / approval missing. | dry-run verification denies → `verify_reason` set, `acted=false`. **No downgrade path**: a failed verification must never fall back to an unsigned or partially verified directive. | §3.4 |
| F9 | Audit append fails. | The directive is **not** considered recorded; hard fail-closed (`blocker_kind=EXACT`). Chain integrity is structurally preserved because `append_audit` reads `prev_hash` and inserts within one connection/commit (`audit.py:58-94`), so a crash between the two leaves no hole. | `audit.py:58-94`, `state_store.py:360-366` |
| F10 | Redaction kernel error / depth > cap. | Collapsing marker `[DEPTH_LIMIT]`, never raw passthrough. | `adapters/_common.py:46-47`, ADR-0007 D-4 (`docs/ADR/ADR-0007-...md:40-41`) |
| F11 | `JCC_CONFIG` unreadable/misspelled. `load_config` **silently** falls back to defaults (`config.py:88-91`: `if path and os.path.exists(path)` … else `data = {}`). | **Finding — fail-open config.** P3 should mirror the `control_token` stance (`server.py:76-81`): if an explicit `JCC_CONFIG` is set but unreadable, **refuse to start**; if using the default path and it is missing, refuse too. A silent default silently sets `policy.version` and (after P3) the whole budget/threshold surface. | `config.py:86-91` |
| F12 | Second instance / orphan marker. | Already handled by `reconcile_marker` (`run.py:114-137`). P3 must not regress it; budget counters are per-DB and therefore per-instance-safe. | **VERIFIED (already fail-closed)** |

### 5.1 EXACT_BLOCKER — representation and rule

**Representation.**
- `Assessment.blocker_kind: Literal["NONE", "EXACT"] = "NONE"`
- `Assessment.blocker_reasons: list[str] = []` — **sorted + de-duplicated** fixed codes (`a..f` from §1.3), so the directive params and the evidence fingerprint are byte-stable.
- Mirror onto the directive: `params.blocker_kind`, `params.blocker_reasons`, `requires_human = true`, `executable = false`.
- Reuse `risk_level == "unknown"` and `SessionStatus.ESCALATED` (`schemas.py:45`) rather than inventing a fourth state vocabulary.

**Rule.**
`blocker_kind == EXACT` ⇒ row 1 ⇒ `ESCALATE`, and it **never** yields a directive that resumes or changes worker behaviour — not `CONTINUE`, `STEER`, `RETRY`, `FINISH`, and not `PAUSE` (a PAUSE would be a *decision*, which is exactly what we do not have). It is also never `executable` in any phase.

**Two readings of "never yields an action" — ADR-0008 must pick one:**

| | Reading | Consequence |
|---|---|---|
| **R1 (recommended)** | "never yields an **executable** or **resuming** directive" → `ESCALATE`, `executable=false`, `requires_human=true` | Fits the existing schema: `Directive.action: DirectiveAction` is non-optional (`schemas.py:131`) and the pipeline records exactly one directive per event (`core.py:130-175`). Fail-closed and auditable. |
| **R2 (stronger)** | "yields **no** directive at all" (action must be nullable) | Requires `Directive.action` to become optional, breaks the one-directive-per-event audit shape, and introduces a state where a session has an *assessment with no decision* — an audit gap. **I recommend against R2**; if ADR-0008 wants it, it is a schema rework and I would record it as `NEEDS REWORK`, not a thin change. |

Under **either** reading, in P3 the invariant is trivially satisfied because nothing is executable — so the *falsifiable* form of the requirement is: **no input, ever, produces a directive with `executable == true` or a resuming action.** That is what the tests in §5.2 assert.

**Absorbing state (design decision for ADR-0008).** Once a session's directive is ESCALATE-by-blocker, the arbitration floor for that session should stay at ESCALATE until a human clears it (`session.status == ESCALATED`), otherwise the next ordinary event silently drops the session back to `CONTINUE` and the blocker evaporates. Clearing must be an explicit, recorded `human_clear` event — never a timeout. I flag this because it changes session-state semantics beyond P2 and is therefore ADR-0008's call, not mine to assume.

### 5.2 Required tests

`test_provider_exception_yields_exact_blocker_and_still_audits` (F1 — must fail on today's code), `test_provider_unverified_is_exact_blocker` (F2), `test_disabled_provider_never_continues` (F3), `test_missing_evidence_never_finishes` (F4), `test_exact_blocker_never_yields_resuming_action` (F5 — property test over the whole fuzz corpus), `test_contradiction_is_exact_blocker` (F6), `test_budget_error_fails_closed` (F7), `test_failed_verification_never_downgrades` (F8), `test_audit_append_failure_is_fatal` (F9), `test_missing_config_refuses_start` (F11), `test_marker_reconcile_regression` (F12).

---

## 6. Integration seams — which modules I would touch, and how

### Hard constraint first (it governs everything below)

> **`audit.py` and the `entry_hash` input key set are FROZEN.**
> `verify_chain` rebuilds each entry from an explicit column list (`audit.py:110-120`) and recomputes `_hash(_canon(entry))` (`audit.py:121`, `_canon` at `:37-38`). Therefore **adding a key to that dict retroactively breaks the hash of every historical P0–P2 row and `verify_audit()` fails on real data** (`state_store.py:397-403`, `server.py:125-128`). The `audit_tape` table shape (`audit.py:18-32`) and `_canon` must not change.

Consequence — the two safe options, with a recommendation:

- **(a) Recommended, zero-change:** leave `audit.py` **entirely** untouched; carry the new detail on the `directives` row and inside the existing opaque `directive` *string* already passed to the tape (`core.py:172-173`). Extend that string to e.g. `ACTION:directive_id:signed=<0|1>:verify=<reason>:budget=<kind>`. This is chain-safe **by construction**: past rows keep their stored `entry_hash`, and future rows simply hash a different string — and `verify_chain` recomputes each row from that row's own stored values, so nothing breaks. Assert with `test_audit_tape_shape_frozen` (byte-compare `audit.SCHEMA` + a golden hash of the canonical payload key order) and `test_verify_chain_over_mixed_format_rows`.
- **(b) Only if new tape *columns* are genuinely required:** add nullable columns **plus** a `schema_version` column, and make `_canon` select its key set *by row version* so historical rows recompute identically. This is strictly more work and more risk. Note that simply `ALTER TABLE ... ADD COLUMN` **and** adding the new keys to the `verify_chain` dict is the trap — it silently invalidates the entire P0–P2 tape.

### Module-by-module

| # | Module | Change | Risk |
|---|---|---|---|
| 1 | `packages/jcc_core/policy.py` | Replace `_TRIGGERS` (`:30-37`) with the ordered rule table (§1.4); add `normalize()`, `arbitrate()`; keep `PRIORITY`/`priority_index` (`:19-27`, `:52-54`); add `POLICY_TABLE_VERSION` and hash it into the signature payload. Keep `decide()` as a thin wrapper. | Medium — the 8 existing tests in `tests/test_policy.py` are written against the *flag* model; they must be rewritten (§1). `test_policy.py:43-45` still applies. |
| 2 | `packages/jcc_core/schemas.py` | Extend `Assessment` (`:107-124`) with the fixed 6 required fields (+ recorded-only `work_off_track`) + `blocker_kind`/`blocker_reasons`/`provider_verified`/`evidence_complete`; extend `Directive` (`:127-139`) per §3.6-4. **`AEPEnvelope` and `version="aep/v0.1"` (`:62-79`, `:65`) stay byte-identical** (P2 condition, `P2_ACCEPTANCE_REPORT.md:88`). Add `SCHEMA_INTERNAL_VERSION = "p3.0.0"`. | Low — new fields are optional; all persistence is via `model_dump(mode="json")` (`core.py:159-160,181-182`; `state_store.py:284`) or explicit columns. |
| 3 | `packages/jcc_core/state_store.py` | **Add** columns to `directives` (§3.6-4) + `signature` to the read path (`:325-338`) and `_row_to_directive` (`:343-357`). Add `budget_snapshot()`. **`_init_db` uses `CREATE TABLE IF NOT EXISTS` (`:114-119`), so existing DBs will NOT gain the new columns** — P3 needs an explicit idempotent mini-migration (`PRAGMA table_info(directives)` → `ALTER TABLE ... ADD COLUMN` for the missing ones). Record this as a schema/version change. Do **not** touch `SCHEMA`'s audit portion (`:94` delegates to `audit_mod.SCHEMA`). | Medium — the migration is the non-obvious part; it is also the rollback-critical part (see below). |
| 4 | `packages/jcc_core/audit.py` | **No functional change.** Optionally a read-only helper, but `append_audit` already reads `prev_hash` itself (`:58-62`), so even that is unnecessary. | None (frozen) |
| 5 | `packages/jcc_core/actuator.py` | Add `verifyEnvelope()` (§3.4, side-effect-free). `execute()` calls it first and **still denies** (`:51-58` unchanged in outcome). Keep `FORBIDDEN_EFFECTS` (`:21-29`) as the static-scan source. | Low |
| 6 | `packages/jcc_core/core.py` | Add `shadow: bool = True`. In shadow: do **not** reach `:143`; call `verifyEnvelope`; set `acted=False`, `actuation_reason="shadow_mode"`. Wrap `provider.assess` (`:125`) → EXACT_BLOCKER (F1). Insert budget check and signing between steps 5 and 6. **Leave `:105`, `:164-175` (audit append) and `:189` (broadcast) semantically unchanged** so the P1 harness checks (f)/(h) keep passing (`test_acceptance_harness.py:273-285`, `:371-385`). | Medium |
| 7 | `packages/jcc_core/config.py` + `config/jcc.yaml` | Add `BudgetConfig` + `SigningConfig` (mirroring `PolicyConfig`, `config.py:53-67`); consume `policy.thresholds` (§1.6); bump `policy.version → p3.0.0` (`config.py:55`, `config/jcc.yaml:32`); make config load fail closed (F11). | Low |
| 8 | `packages/jcc_core/server.py` | Surface the shadow record **read-only**: extend `/api/decisions` (`:121-123`) and add a read-only `GET /api/directives/{id}/verify` that re-runs `verifyEnvelope`. **Add no mutating endpoint that can execute.** Note `_manual()` (`:139-160`) is the only place that builds a directive outside the policy path and calls `engine.actuator.execute` (`:149`) — a **policy bypass**, which ADR-0004 explicitly requires the supervisor to reject (`ADR-0004:21`). P3 should route `/api/pause|steer|retry` through arbitration+signing, or remove them, and record the deviation with an owner. | Medium — this is the one genuine policy-bypass seam in the codebase; flag it to Atlas and Aegis. |
| 9 | `packages/jcc_core/providers/mock.py` | Stop emitting directive-shaped booleans (`:62-69`) and stop cutting bands (`:37-42`); emit only the fixed signal fields (**6 required** + recorded-only `work_off_track`) + `probabilities` + `band`. Preserve determinism (`:27-31`) by deriving the new fields from the same SHA-256 seed so fixtures stay reproducible. | Low |
| 10 | `packages/jcc_core/tests/*` | New: `test_policy_table.py` (§1.7), `test_budget.py` (§2.5), `test_signing.py` (§3.8), `test_shadow_invariants.py` (§4.3), `test_config_failclosed.py` (F11), `test_audit_tape_frozen.py` (§6). Extend `test_acceptance_harness.py` with the shadow block (§4.3c). Keep `test_audit.py` untouched. | Low |
| 11 | `integrations/_common/jcc_receiver.py` | Only if the ADR-0007 D-3 hoist is executed: `ReplayCache` moves to a neutral module; behaviour and constants unchanged. **Do not** create a `packages/ → integrations/` import instead (§3.6-3). | Low |

### Rollback property (ADR-0006 §1 artifact-snapshot + marker)

The `directives` migration is **additive and nullable only**. P2's `get_directives` selects an explicit 11-column list (`state_store.py:325-338`) and maps it positionally (`:343-357`), so extra columns are ignored by the old binary — i.e. **rolling back to the P2 build against a P3-migrated DB is safe**. This is a verifiable property, not a hope: assert it with `test_p2_read_path_survives_p3_migration` (run P2's exact SELECT string against a migrated DB). Rollback evidence per ADR-0006 §1 = restore the prior `artifacts/` snapshot + the `JCC-marker`-guarded restart (`run.py:114-148`). Note the one genuine rollback wrinkle: directives signed with a P3 key will not verify under a P2 binary — expected, and harmless, because P2 never verifies anything.

### Schema / version changes (explicit register)

| Item | From → To |
|---|---|
| `Directive.policy_version` default | `p1.0.0` → `p3.0.0` (`schemas.py:134`, `config.py:55`, `config/jcc.yaml:32`) |
| `directives` table | + 11 nullable columns (§3.6-4, §2.4) via explicit idempotent migration |
| internal model version | new constant `SCHEMA_INTERNAL_VERSION = "p3.0.0"` |
| `AEPEnvelope` / `aep/v0.1` | **unchanged** (frozen) |
| `audit_tape` schema + `entry_hash` inputs | **unchanged** (frozen) |
| new env vars | `JCC_DIRECTIVE_KEY` (secret, never in config) |
| new config blocks | `budget:`, `signing:` |

---

## 7. Risk register (owner + next step for each)

| Sev | Item | Owner | Next step |
|---|---|---|---|
| **HIGH** | Provider chooses the directive via hard-coded thresholds (`mock.py:62-69`) while `policy.thresholds` is dead config — ADR-0002's "Policy produces the directive" is not actually met. | Sentinel (contract) → implementer | ADR-0008 must record the §1.4 table as the sole mapping authority and the §1.6 collapse to one threshold source. |
| **HIGH** | F1: an uncaught provider exception (`core.py:125`) leaves an event persisted with **no** assessment/directive/audit row → a hole in the authoritative tape (ADR-0002:25). | Sentinel → implementer | Add the boundary catch + EXACT_BLOCKER + still-audit; test must fail on today's code. |
| **HIGH** | §4.2 INV-SHADOW is currently satisfied only because the actuator happens to be disabled, while `core.py:143` still **calls** `execute` on every ingest. | Sentinel → implementer | Switch the shadow path to `verifyEnvelope`; prove with the tripwire (a) + DB assert (d) + negative control (e). |
| MEDIUM | Audit-tape freeze hazard: adding keys to `verify_chain`'s payload dict silently invalidates the whole P0–P2 tape. | Sentinel → implementer | Freeze `audit.py`; use §6 option (a); add `test_audit_tape_frozen` + `test_verify_chain_over_mixed_format_rows`. |
| MEDIUM | Policy bypass at `server.py:149` (`_manual` builds a directive and calls `execute` outside policy). ADR-0004:21 requires the supervisor to reject policy-bypassing directives. | Sentinel + Atlas | Decide in ADR-0008: route through arbitration+signing, or remove `/api/pause|steer|retry`. |
| MEDIUM | `state_store.get_directives` never reads `signature` (`:325-338` vs write at `:299-313`) → a real signature would be unverifiable from stored state. | Sentinel → implementer | Add `signature` + new columns to the read path and `_row_to_directive`. |
| MEDIUM | F11: `load_config` silently defaults when the config file is missing (`config.py:88-91`). | Sentinel → implementer | Fail closed on explicit-but-unreadable `JCC_CONFIG`. |
| MEDIUM | ADR-0007 D-3 dependency inversion (redaction kernel under `adapters/`) is still open and P3 would add a *second* one if `ReplayCache` is imported upward. | Forge / Atlas | Execute the D-3 hoist once (kernel + `nonce_cache.py`) before P3 signs anything. |
| LOW | F2: `band="unverified"` (`real.py:50`) is outside the documented band set (`schemas.py:117`) and is used as a control signal. | Sentinel → implementer | Explicit `provider_verified` boolean; extend the documented band enum. |
| LOW | `config.py:65` `verify_risk: 0.4` vs `mock.py:66-67` mapping 0.4 → STEER. | Sentinel | **Resolved (R6):** 0.4 is the **STEER** cut → renamed `steer_risk`; `verify_risk` **deleted** (VERIFY is evidence-driven). Assert in D6. |
| LOW | Budget/alarm suppression determinism: suppression windows implemented with time/jitter would break replayability. | Sentinel | Pure integer predicate (§2.3-3); assert with `test_budget_suppression_is_deterministic`. |
| **UNRESOLVED UNKNOWN** | A3/A7: whether P3 needs asymmetric non-repudiation (Ed25519 ⇒ new dependency). HMAC does **not** protect against the control plane itself. | Atlas (ADR-0008) | Explicit P4 decision; do not silently assume HMAC is sufficient. |
| **UNRESOLVED UNKNOWN** | A2: whether extending `Assessment`/`Directive` is acceptable under the P2 "wire contract frozen" condition. | Atlas | Confirm in ADR-0008 that `AEPEnvelope` is the frozen contract and the internal models are not. |
| **UNRESOLVED UNKNOWN** | §5.1: R1 vs R2 for EXACT_BLOCKER, and whether ESCALATE-by-blocker is an absorbing session state. | Atlas | ADR-0008 decision; R2 is a schema rework (`NEEDS REWORK`), R1 is a thin change. |

---

## 8. DECISION

**INFORMATION ONLY.**

This is a design input to ADR-0008, not a gate, and I hold no acceptance authority over P3. The role-scoped portion I *do* own — the supervision / policy / actuation contract — is **not yet conformant** at `a4e7916`:

- The deterministic-policy requirement is **not met**: the provider, not policy, selects the action (§0, §1.6). → **NEEDS REWORK** of `policy.py` + `providers/mock.py`, as specified in §1.
- The shadow / no-actuation requirement is met **by accident** (the actuator happens to be disabled) rather than by construction; `core.py:143` still invokes `execute` on every ingest. → **NEEDS REWORK** of the shadow path, proven by §4.3.
- The signed-directive requirement is **unimplemented by design** (`schemas.py:135-136`, `core.py:138-140`) and there is no verifier. → **GO WITH CONDITIONS**: signing may land in P3 **only together with** `verifyEnvelope`, a key source, TTL + nonce, and a replay check ordered after signature verification (§3).

I explicitly do **not** sign off on: architecture conformance (Atlas), security (Aegis), SRE/acceptance (Harbor), or the evaluation leg (Metric). Those gates remain independent and I am not averaging them into my conclusion.

---

## 9. HANDOFF

- **Next owner:** **Atlas** (ADR-0008 author) — consolidate this with Metric's parallel design input and the evaluation harness design.
- **Artifacts to pass:** this file (`artifacts/acceptance/P3_SENTINEL_DESIGN_INPUT.md`); the frozen P2 baseline (`P2_ACCEPTANCE_REPORT.md`, product commit `7f62f57`, docs closeout `a4e7916`); the open ADR-0007 D-3 hoist (a P3 precondition for §3.6-3).
- **Facts the lead must relay:**
  1. `policy.thresholds` is **dead config** and the provider picks the action — the headline finding.
  2. `audit.py` and the `entry_hash` input key set must be treated as **frozen forever**; the trap is `ALTER TABLE` + adding keys to `verify_chain`'s dict (§6).
  3. `state_store.get_directives` never reads `signature` — a gap that must be closed or signing is unverifiable from stored state.
  4. `core.py:125` has no exception boundary → an uncaught provider error leaves a hole in the authoritative tape.
  5. `server.py:149` is the sole remaining **policy bypass**; ADR-0004:21 forbids it.
- **Points the next phase must NOT mis-read:**
  - Do **not** read §3 as "P3 will actuate". P3 is shadow; `executable=false` everywhere; no `actuation.enabled=true` path is authored here (A1, ADR-0004:20).
  - Do **not** read "signed" as "authorised" (§3.1), and do **not** read HMAC as non-repudiation against the control plane (§3.7).
  - Do **not** treat "EXACT_BLOCKER never yields an action" as already satisfied — §5.1 defines the two readings and only the `executable=false` / non-resuming form is currently meaningful.
  - Do **not** add budget/threshold overrides that can be silently flipped (`enabled: false`) — §2.3-6.
  - Do **not** change `AEPEnvelope` or `version="aep/v0.1"` (`schemas.py:65`) to make any of this easier; the wire contract is frozen (`P2_ACCEPTANCE_REPORT.md:88`).
- **Explicitly out of scope for me (do not attribute to Sentinel):** Jev scorer internals and the real `typesafe` contract; UI/visual plane; installer behaviour; the evaluation metric definition.
