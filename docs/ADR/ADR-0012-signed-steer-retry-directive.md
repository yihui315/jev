# ADR-0012 — Signed Steer / Retry Directive Contract (fail-closed, same gates as every directive)

- **Status:** PROPOSED — this is an **implementation spec**. No steer/retry gating code exists yet; the
  platform engineer implements against §Decision and the pinning gate pins it.
- **Date:** 2026-09-25
- **Owner:** atlas-4 (`docs/ADR` only — does **not** own `packages/`; per the standing rule, test nails
  live in `packages/` and have exactly one owner, see §5 and the handoff note).
- **Extends (does not re-open):** ADR-0004 (supervision & actuation; P0–P2 no auto-actuation),
  ADR-0008 (D-28, D-33, D-34, D-41), ADR-0003 (`session_id` primary identity), ADR-0005 (security
  baseline — signing key env-only).
- **Informs (the code this ADR prescribes, none of it written yet):** `packages/jcc_core/signing.py`
  (reason vocabulary + the fail-closed check-4 fix), `packages/jcc_core/server.py` (`/api/steer`,
  `/api/retry` become verifiers, not unsigned-directive minting sites), `packages/supervisor/budget.py`
  (`CODE_RETRIES` accounting for retry), `packages/jcc_core/schemas.py` (no new columns required —
  all fields already exist, see §1), and a **new** pinning gate file (owner: forge, see §5).
- **Closes (the steer/retry subset of):** `docs/ADR/KNOWN_DEVIATIONS.md` **KD-002** (manual directives
  carry no signature / no verified identity). KD-002 stays OPEN for the remaining unsigned manual paths
  (`/api/pause`, and any other unsigned control-plane directive); this ADR narrows KD-002's scope, it
  does not wholesale close it. **KD-001** (the second actuation-port call site in `server._manual`) is
  also addressed for steer/retry — see §2 check G1/G5 and §Relationship.

---

## Context

The user requirement (§7) is explicit: **steering an agent and retrying a failed action must pass
through the SAME gates as everything else** — signature, TTL, nonce, session, scope, policy, capability,
budget, and human approval where required. Today they do not.

**Current state (VERIFIED by reading `packages/jcc_core/server.py:206-262`).** `/api/steer` and
`/api/retry` both call `server._manual`, which:

- builds a `Directive` with `signed=False`, `signature=None`, `evidence_hash=None`,
  `requires_human=True`, `executable=False`, `directive_id=f"dir_manual_{ms}"`;
- dispatches it straight into `engine.actuator.dispatch(d)` — the **second** actuation-port call site
  (KD-001), outside policy arbitration and outside signing.

That is precisely KD-002: a steer/retry directive reaches the actuation seam **unsigned and
unverified**. ADR-0008 **D-28** already ruled that `/api/pause|steer|retry` "MUST route through the
arbitration + signing path with `requires_human=true`, `executable=false`, and MUST NOT call
`Actuator.execute`." The code never implemented D-28 for these paths (it is recorded as a deviation,
not silently kept). This ADR is the concrete contract that lets a platform engineer implement D-28 for
steer/retry: **the steer/retry directive is a policy-signed directive and enters the same 9-check
pipeline as every other directive, plus two retry-specific checks.**

**What already exists and is reused (VERIFIED from source, not invented here):**

- `jcc_core/signing.py` defines the 9-check pipeline and a **frozen reason vocabulary**: `REASON_UNSIGNED`,
  `REASON_NO_KEY`, `REASON_BAD_SIGNATURE`, `REASON_EXPIRED`, `REASON_NOT_YET_VALID`, `REASON_REPLAY`,
  `REASON_SESSION_MISMATCH`, `REASON_CAPABILITY_DENIED`, `REASON_POLICY_MISMATCH`, `REASON_BUDGET_EXHAUSTED`,
  `REASON_APPROVAL_MISSING`. Steer/retry reuse these verbatim.
- The canonical signed payload (D-33, VERIFIED in `signing.py:16-37`):
  `policy_table_version . policy_version . session_id . directive_id . int(issued_at) . ttl_s . nonce .
  action . target_actuator . evidence_hash .` + `json.dumps(params, sort_keys=True, separators=(",",":"))`.
  `ttl_s` and `nonce` are **inside** the signed bytes so neither can be swapped on replay.
- Key: symmetric **HMAC-SHA256** under `config.p3.shadow.signing.key_env` (default
  `JCC_DIRECTIVE_SIGNING_KEY`), never in config/DB/log (ADR-0005). Signature string is `"sha256=<hex>"`.
- `nonce_cache.ReplayCache` (`DEFAULT_REPLAY_TTL_S=600.0`, `DEFAULT_REPLAY_MAXSIZE=20000`) — VERIFIED.
- `signing.DEFAULT_CLOCK_SKEW_S=30.0`, applied **only** to "not yet valid"; expiry is **never** skewed
  (fail-closed) — VERIFIED.
- `supervisor/budget.py`: `CODE_RETRIES="retries"` (default limit `retries: int = 5`, VERIFIED
  `budget.py:111,145`), `CODE_DIRECTIVES`, `CODE_COST_UNITS`, `BudgetGovernor.check/commit` — VERIFIED.
- `policy.COST_UNITS_PER_ACTION`: `STEER=2.0`, `RETRY=3.0` (VERIFIED, also pinned by
  `test_budget_defaults_match_config_yaml`).
- `decision.POLICY_TABLE_VERSION="p3.0.0"` (VERIFIED, `decision.py:44`).
- D-34 (VERIFIED in ADR-0008): exhaustion ⇒ **replace with ESCALATE** (never suppress, never CONTINUE),
  record still written and audited; subsystem error ⇒ exhausted; **no `budget.enabled` off-switch**.
- D-41 (VERIFIED in ADR-0008): P3 is non-actuating (`executable=False`, `acted=False`); the P4 real
  actuator attaches at `Sentinel._dispatch` only. This ADR gates **authorisation integrity**, not
  actuation — even a fully gate-passing steer/retry directive stays inert in P0–P3.

**One fail-open defect this ADR must correct (VERIFIED in `signing.py:216-218`):** check 4 (replay) is
skipped when `replay is None` ("if replay is not None"). That is a fail-OPEN: a missing nonce store
silently disables replay protection. §2 G3 makes a missing nonce store a **refusal**.

---

## Decision

### 1 — Directive shapes for `steer` and `retry`

No new columns are required. Every field below already exists on `Directive`
(`schemas.py:143-169`). "Required" means the gate **refuses** (named reason in §2) if it is absent or
wrong-typed. "Optional" means the field may be defaulted by the signer and the gate does not refuse on
its absence.

#### 1.1 Common signed envelope (every directive, reused verbatim from D-33)

| field | required? | rule |
|---|---|---|
| `directive_id` | required | globally unique; namespaced `steer_<session>_<event>` / `retry_<session>_<event>` |
| `session_id` | required | the **only** session this directive may touch (ADR-0003) |
| `timestamp` | required | issuance wall-clock |
| `action` | required | `DirectiveAction.STEER` or `DirectiveAction.RETRY` |
| `target_actuator` | required | must resolve to the session's own agent actuator (G7) |
| `policy_version` | required | must equal `config.policy.version` (G6) |
| `policy_table_version` | required | must equal `decision.POLICY_TABLE_VERSION` (`"p3.0.0"`) (G6) |
| `signed` | required `True` | an unsigned directive is refused at G1 |
| `signature` | required | `"sha256=<hex>"` over the canonical payload (G1) |
| `signing_alg` | required | `"hmac-sha256"` |
| `issued_at` | required | inside the signed payload; `int(issued_at)` is what is signed |
| `ttl_s` | required | inside the signed payload; must be a finite float > 0 and ≤ `STEER_RETRY_MAX_TTL_S` (see §3.4) |
| `nonce` | required | inside the signed payload; globally unique per key; replay-refused at G3 |
| `evidence_hash` | required-if-evidence | `None` is allowed **only** when the directive carries no evidence claim and the reason is recorded in `verify_reason`; any evidence-backed steer/retry MUST carry a non-null `evidence_hash` |
| `params` | required | must be a JSON object; sub-fields below are validated per action (G5/G10/G11) |
| `executable` | required `False` | P0–P3: never executable (D-1/D-27/D-41) |
| `requires_human` | required | `True` for any operator/manual steer/retry (D-28); the gate clears it only after a valid approval (G9) |
| `human_approval` | optional | filled with the approving token/id once G9 is satisfied |
| `shadow` | required `True` | |

#### 1.2 STEER `params` (how the bound is expressed)

```json
{
  "scope": "session",            // REQUIRED, literal; any other value => REASON_SCOPE_VIOLATION (G5)
  "guidance": "<steer instruction>",  // human- or policy-authored text; opaque to the gate
  "applied_thresholds": { ... }  // optional, per D-21
}
```

- The **bound** of a steer directive is its **scope**: it reaches **only** the session named in
  `session_id`, and only that session's own agent actuator. It MUST NOT contain any cross-session
  reference, a second `session_id`, a second agent name, a global-config key, or another directive id.
- `target_actuator` MUST resolve to the session's agent actuator (capability check, G7). A steer that
  names a different actuator is a scope violation.

#### 1.3 RETRY `params` (how the bound is expressed)

```json
{
  "scope": "same_action",            // REQUIRED, literal; widening => REASON_SCOPE_VIOLATION (G5)
  "previous_directive_id": "<id>",   // REQUIRED — the FAILED directive this retry re-attempts (G10)
  "attempt": 1,                      // REQUIRED — 1-based attempt number for this chain
  "max_attempts": 3,                 // REQUIRED — chain-local ceiling (the primary bound, G11)
  "backoff_s": 5.0                   // REQUIRED — minimum delay before acting (>= 0, G11)
}
```

- The **bound** of a retry is expressed by **three** independent limits that all must hold:
  1. `max_attempts` — the chain-local ceiling (G11).
  2. `backoff_s` — the minimum temporal spacing between attempts (G11).
  3. `CODE_RETRIES` — the global per-session budget dimension (default 5, D-34) charged on every
     retry directive (G8).
- A retry MUST NOT escalate scope: its `session_id` and `target_actuator` MUST equal those of
  `previous_directive_id`, and `scope` MUST be `"same_action"`. Narrowing is allowed; widening is a
  refusal (G5). See §3.

### 2 — Verification: one NAMED rejection reason per check

Order is **normative and first-failure-wins** (D-33). Checks G1–G9 are the "same gates as everything
else"; G10/G11 are the retry-specific extensions. **Every reason is a frozen string** so the gate and
the signer cannot drift. Reused reasons are already defined in `signing.py`; **new** reasons (★) are
defined here and MUST be added to the `signing.py` vocabulary when the gate is implemented.

| # | check | NAMED reason (refusal) | definition |
|---|---|---|---|
| **G1** | signature **presence** | `REASON_UNSIGNED` | `signed is not True`, or `signature` missing/empty, or any of `issued_at`/`ttl_s`/`nonce` is `None` (they are inside the payload and cannot be verified absent) |
| **G1** | signature **validity** | `REASON_NO_KEY` | signing key (`JCC_DIRECTIVE_SIGNING_KEY`) unset/blank ⇒ cannot verify ⇒ refuse (never invent a signature) |
| **G1** | signature **validity** | `REASON_BAD_SIGNATURE` | recomputed HMAC (over the canonical payload, `hmac.compare_digest`) ≠ `signature` |
| **G2** | TTL / clock | `REASON_EXPIRED` | `now - issued_at > ttl_s` (expiry is **never** skewed) |
| **G2** | TTL / clock | `REASON_NOT_YET_VALID` | `issued_at - now > DEFAULT_CLOCK_SKEW_S` (30.0 s tolerance, future-dated only) |
| **G2** | TTL / clock | `REASON_TTL_UNPARSEABLE` ★ | `ttl_s` present but not a finite float ⇒ refuse (cannot bound validity) |
| **G2** | TTL / clock | `REASON_CLOCK_UNTRUSTED` ★ | the caller-supplied `now` is `None`/NaN/out-of-operating-range ⇒ the gate cannot establish validity or freshness ⇒ refuse (fail-closed on an untrustworthy clock) |
| **G3** | nonce | `REASON_REPLAY` | `nonce` already seen in the replay cache within its TTL ⇒ refuse; the directive is **not** charged to budget and **not** side-effected |
| **G3** | nonce store | `REASON_NO_NONCE_STORE` ★ | the replay cache is unavailable/`None` ⇒ refuse (replaces the current `if replay is not None` skip — fixes the fail-open at `signing.py:216`) |
| **G4** | session | `REASON_SESSION_MISMATCH` | `session_id` is absent, not present in the store, or not the session the directive is being applied to ⇒ refuse |
| **G5** | scope | `REASON_SCOPE_VIOLATION` ★ | steer/retry reaches beyond its bounded scope (wrong `scope` literal, cross-session/agent/config reference, or a retry that widens the referenced failure's session/actuator) ⇒ refuse |
| **G6** | policy | `REASON_POLICY_MISMATCH` | `policy_table_version != decision.POLICY_TABLE_VERSION` **or** `policy_version != config.policy.version` ⇒ refuse (no version negotiation — fail-closed) |
| **G7** | capability | `REASON_CAPABILITY_DENIED` | `target_actuator` not in `cfg.actuation.allowed_actions`, or `action` (STEER/RETRY) not in `allowed_actions`, or `can_act(d)` is `False`, or a non-null `evidence_hash` does not correspond to provider-verified evidence ⇒ refuse |
| **G8** | budget | `REASON_BUDGET_EXHAUSTED` | `BudgetGovernor.check` returns `allowed=False` on any dimension — STEER charges `CODE_DIRECTIVES` + `CODE_COST_UNITS(2.0)`; RETRY charges `CODE_RETRIES` + `CODE_DIRECTIVES` + `CODE_COST_UNITS(3.0)`. A counter read that raises ⇒ treated as exhausted (D-34) |
| **G9** | human approval | `REASON_APPROVAL_MISSING` | `requires_human is True` and no valid `human_approval` record references this `directive_id` ⇒ refuse |
| **G10** | retry chain | `REASON_RETRY_CHAIN_BROKEN` ★ | `previous_directive_id` absent, not found in the store, or its referenced directive is **not** in a failed state (not `acted=False`/error/refused) ⇒ refuse |
| **G11** | retry bound | `REASON_RETRY_EXHAUSTED` ★ | `attempt >= max_attempts` ⇒ refuse (chain-local ceiling; distinct from `REASON_BUDGET_EXHAUSTED` so the operator can tell a chain limit from the global budget) |
| **G11** | retry bound | `REASON_BACKOFF_NOT_ELAPSED` ★ | `now < issued_at + backoff_s` ⇒ refuse-to-act (recorded, not side-effected; in P3 this is a record, not an actuation) |

**Positive path.** When all checks pass: the directive is recorded (`directives` row, `shadow` tape),
`signature_status="signed_verified"`, `acted=False`, `actuation_reason` reflects shadow inertness. The
12-check pipeline runs the **same** for a steer/retry as for an ESCALATE/CONTINUE; only `params` and
`action` differ.

**Check-4 ordering preserved (D-33).** G3 replay runs **after** G1 validity, so unauthenticated traffic
cannot poison the replay cache. The `REASON_NO_NONCE_STORE` refusal is evaluated at G3 entry (before any
replay lookup), so an unavailable store is a hard refusal, not a skipped check.

### 3 — Bounded retry semantics

**3.1 What counts as a retry.** A directive with `action == RETRY` whose `params.previous_directive_id`
references an existing directive that is in a **failed** state (not yet acted / error / refused). A
retry against a succeeded or absent directive is `REASON_RETRY_CHAIN_BROKEN`.

**3.2 Max bound.** `params.max_attempts` (int ≥ 1) is the chain-local ceiling. Additionally the global
`CODE_RETRIES` budget (default 5, D-34) caps retries per session. **Both must hold** — `max_attempts` is
a per-chain contract; `CODE_RETRIES` is a per-session safety net. Neither is optional.

**3.3 Backoff.** `params.backoff_s` (float ≥ 0) is the minimum delay before the retry may act. The gate
refuses-to-act with `REASON_BACKOFF_NOT_ELAPSED` while `now < issued_at + backoff_s`. It is recorded,
never side-effected, in P3.

**3.4 TTL ceiling.** To keep replay windows sane, `ttl_s` on a steer/retry directive MUST be
`0 < ttl_s <= STEER_RETRY_MAX_TTL_S` (recommended `300.0` s, i.e. half the `DEFAULT_REPLAY_TTL_S`). A
`ttl_s` above the ceiling is `REASON_TTL_UNPARSEABLE`-class refusal (treated as unverifiable bound).
This is a contract constant, not a config override.

**3.5 Behaviour at the bound.**
- `attempt >= max_attempts` ⇒ `REASON_RETRY_EXHAUSTED` (refuse, record, **no scope escalation**).
- `CODE_RETRIES` exhausted ⇒ `REASON_BUDGET_EXHAUSTED` (D-34: replace-with-ESCALATE, record still
  written and audited).
- `now < issued_at + backoff_s` ⇒ `REASON_BACKOFF_NOT_ELAPSED` (recorded).
In every at-the-bound case the failure is **recorded and audited**, never silently dropped, and never
auto-escalated into a broader action.

**3.6 May a retry escalate scope? — NEVER.** A retry's scope is pinned to the referenced failed
directive's session and actuator. The retry MAY only re-attempt the **same** action. Any widening
(different `session_id`, different `target_actuator`, `scope != "same_action"`, or a higher-precedence
action than the failure) is `REASON_SCOPE_VIOLATION`. If an operator genuinely needs to change scope,
they issue a **new** directive through the same 12-check gate — not a retry that escalates. This is the
load-bearing fail-closed property: a retry cannot become a privilege expansion.

### 4 — Fail-closed defaults table (absent input ⇒ refusal, never a pass)

The invariant: **absent required input is a refusal, never a pass.** Table maps each check to its
absent-input behaviour and the named reason.

| check | absent input | behaviour | reason |
|---|---|---|---|
| G1 signature | `signed=False` / `signature` missing | refuse | `REASON_UNSIGNED` |
| G1 key | `JCC_DIRECTIVE_SIGNING_KEY` unset/blank | refuse | `REASON_NO_KEY` |
| G1 validity | HMAC mismatch | refuse | `REASON_BAD_SIGNATURE` |
| G2 TTL | `ttl_s` missing | refuse (payload unverifiable) | `REASON_UNSIGNED` |
| G2 TTL | `ttl_s` non-numeric | refuse | `REASON_TTL_UNPARSEABLE` |
| G2 clock | `now` None/NaN/out-of-range | refuse | `REASON_CLOCK_UNTRUSTED` |
| G3 nonce | `nonce` missing | refuse (payload unverifiable) | `REASON_UNSIGNED` |
| G3 nonce store | replay cache unavailable/`None` | refuse | `REASON_NO_NONCE_STORE` |
| G4 session | `session_id` absent / not in store / not active | refuse | `REASON_SESSION_MISMATCH` |
| G5 scope | `scope` missing or wrong literal / cross-ref | refuse | `REASON_SCOPE_VIOLATION` |
| G6 policy | either version missing or mismatched | refuse | `REASON_POLICY_MISMATCH` |
| G7 capability | actuator not allowed / unverified evidence | refuse | `REASON_CAPABILITY_DENIED` |
| G8 budget | counter read raises / over limit | refuse (error ⇒ exhausted) | `REASON_BUDGET_EXHAUSTED` |
| G9 approval | `requires_human=True`, no approval record | refuse | `REASON_APPROVAL_MISSING` |
| G10 retry chain | `previous_directive_id` missing/not-failed | refuse | `REASON_RETRY_CHAIN_BROKEN` |
| G11 retry bound | `max_attempts` missing / `attempt>=max` / backoff not elapsed | refuse | `REASON_RETRY_EXHAUSTED` / `REASON_BACKOFF_NOT_ELAPSED` |

### 5 — Test matrix the gate must require

One row per check above, each asserting the **named rejection reason** it produces. The matrix is the
implementation contract; a platform engineer implements against these ids and the **final gate pins
them**. Per the standing "one owner per nail" rule (ADR-0011 §*Who writes what*), these tests live in
**one new file, owned by forge**:

> `packages/jcc_core/tests/test_p4_steer_retry_directive_gate.py`

atlas-4 authors only this ADR (`docs/`). The ids below are the proposed pin set; the implementer may
split a row into finer tests but MUST cover every reason named in §2/§4.

| # | gate under test | reason asserted | proposed test id |
|---|---|---|---|
| 1 | G1 unsigned present | `REASON_UNSIGNED` | `test_sr_sig_unsigned_refused` |
| 2 | G1 key unavailable | `REASON_NO_KEY` | `test_sr_sig_no_key_refused` |
| 3 | G1 bad HMAC | `REASON_BAD_SIGNATURE` | `test_sr_sig_bad_signature_refused` |
| 4 | G2 expired | `REASON_EXPIRED` | `test_sr_ttl_expired_refused` |
| 5 | G2 future > skew | `REASON_NOT_YET_VALID` | `test_sr_ttl_not_yet_valid_refused` |
| 6 | G2 ttl non-numeric | `REASON_TTL_UNPARSEABLE` | `test_sr_ttl_unparseable_refused` |
| 7 | G2 untrustworthy clock | `REASON_CLOCK_UNTRUSTED` | `test_sr_clock_untrusted_refused` |
| 8 | G3 replayed nonce | `REASON_REPLAY` | `test_sr_nonce_replay_refused` |
| 9 | G3 store missing (fail-closed) | `REASON_NO_NONCE_STORE` | `test_sr_nonce_store_missing_refused` |
| 10 | G4 wrong/absent session | `REASON_SESSION_MISMATCH` | `test_sr_session_mismatch_refused` |
| 11 | G5 steer scope not "session" | `REASON_SCOPE_VIOLATION` | `test_sr_steer_scope_violation_refused` |
| 12 | G5 steer cross-agent actuator | `REASON_SCOPE_VIOLATION` | `test_sr_steer_wrong_actuator_refused` |
| 13 | G6 version mismatch | `REASON_POLICY_MISMATCH` | `test_sr_policy_mismatch_refused` |
| 14 | G7 actuator not allowed | `REASON_CAPABILITY_DENIED` | `test_sr_capability_denied_refused` |
| 15 | G7 unverified evidence hash | `REASON_CAPABILITY_DENIED` | `test_sr_capability_unverified_evidence_refused` |
| 16 | G8 budget exhausted (STEER) | `REASON_BUDGET_EXHAUSTED` | `test_sr_steer_budget_exhausted_refused` |
| 17 | G8 retries budget exhausted (RETRY) | `REASON_BUDGET_EXHAUSTED` | `test_sr_retry_code_retries_exhausted_refused` |
| 18 | G9 approval missing | `REASON_APPROVAL_MISSING` | `test_sr_approval_missing_refused` |
| 19 | G10 previous directive missing/failed | `REASON_RETRY_CHAIN_BROKEN` | `test_sr_retry_chain_broken_refused` |
| 20 | G11 attempt ≥ max_attempts | `REASON_RETRY_EXHAUSTED` | `test_sr_retry_max_attempts_exhausted_refused` |
| 21 | G11 backoff not elapsed | `REASON_BACKOFF_NOT_ELAPSED` | `test_sr_retry_backoff_not_elapsed_refused` |
| 22 | G5 retry widens scope | `REASON_SCOPE_VIOLATION` | `test_sr_retry_scope_escalation_refused` |
| 23 | fail-closed default: unsigned | `REASON_UNSIGNED` | `test_sr_default_unsigned_refused` |
| 24 | fail-closed default: missing ttl | `REASON_UNSIGNED` | `test_sr_default_missing_ttl_refused` |
| 25 | fail-closed default: no nonce store | `REASON_NO_NONCE_STORE` | `test_sr_default_no_nonce_store_refused` |
| 26 | fail-closed default: unverified capability | `REASON_CAPABILITY_DENIED` | `test_sr_default_unverified_capability_refused` |
| 27 | **positive** steer passes all gates | `acted=False, signature_status="signed_verified"` | `test_sr_steer_passes_same_gates` |
| 28 | **positive** retry passes all gates | `acted=False, signature_status="signed_verified"` | `test_sr_retry_passes_same_gates` |
| 29 | **non-vacuity falsifier** | a properly-signed steer/retry is ADMITTED and a malformed one is REFUSED (proves the gate can both pass and fail) | `test_sr_falsifier_gate_can_admit_and_refuse` |

**Non-vacuity rule (the KD-004 / P3-gate bar).** Rows 27–29 are mandatory: a gate that only ever
refuses is decorative (the #54 failure mode). Row 29 is the negative control — it MUST be able to admit a
correctly-signed directive and refuse a malformed one, or the suite is vacuous and the ADR's contract
is unmet.

**A8 / freeze note.** The steer/retry gate is a NEW `packages/` file. If it is added inside the P4
freeze window it becomes an A8 pin candidate; that decision is the freeze owner's, not atlas-4's. This
ADR only prescribes behaviour and test ids.

---

## Consequences

- **Positive:** steer/retry now satisfy user requirement §7 — identical gates to every other directive.
  KD-002's steer/retry subset closes; the second call-site bypass (KD-001) is removed for these paths
  because the endpoint becomes a **verifier**, not an unsigned-directive minter. The fail-open replay
  skip (`signing.py:216`) is corrected to fail-closed. Retry scope-escalation is structurally
  impossible.
- **Negative / cost:** a steer/retry directive now requires a signing authority (policy plane) to have
  produced the signature; the control-plane endpoints can no longer self-mint. Operators must route
  through the signing path (D-28). Seven new named reasons must be added to `signing.py`. A retry needs
  `max_attempts`/`backoff_s`/`previous_directive_id` plumbing that does not exist today.
- **Constraint preserved:** `session_id` primary (ADR-0003); P0–P3 non-actuating (`executable=False`,
  `acted=False`, D-1/D-27/D-41); Jev scorer-only, Policy decides (ADR-0004); loopback-only,
  env-only signing key (ADR-0005); UI not source of truth (core contract #9). A gate-passing steer/retry
  is still a **record**, never a side effect, until the P4 real actuator attaches at `Sentinel._dispatch`.

## Verification labels

- `server._manual` mints unsigned steer/retry directives (KD-002): **VERIFIED** (`server.py:206-262`).
- `signing.py` 9-check pipeline + the 11 reason constants reused here: **VERIFIED** (source read).
- Canonical signed payload (D-33) / HMAC-SHA256 / `JCC_DIRECTIVE_SIGNING_KEY` env-only: **VERIFIED**.
- `ReplayCache` params (`600.0`/`20000`); `DEFAULT_CLOCK_SKEW_S=30.0`, expiry unskewed: **VERIFIED**.
- `CODE_RETRIES="retries"`, default limit `5`; `CODE_DIRECTIVES`/`CODE_COST_UNITS`; `BudgetGovernor.check/commit`: **VERIFIED** (`budget.py`, `test_p3_acceptance_gate.py` import).
- `COST_UNITS` STEER=2.0 / RETRY=3.0; `decision.POLICY_TABLE_VERSION="p3.0.0"`: **VERIFIED**.
- D-28 (`/api/steer|retry` must route through arbitration+signing): **VERIFIED as a ruling**; **UNVERIFIED as implemented** (the code still deviates — KD-001/KD-002).
- **The entire steer/retry gating logic, the 7 new named reasons (★), the `REASON_NO_NONCE_STORE`
  fix, and the test matrix (§5): UNVERIFIED — design only; no code exists. This ADR is the spec a
  platform engineer implements and the gate pins.**

## Relationship to KD-001 / KD-002

- **KD-002** ("manual directives carry no signature") — this ADR **closes the steer/retry subset**:
  those two endpoints MUST NOT mint unsigned directives. KD-002 remains OPEN for `/api/pause` and any
  other unsigned control-plane directive; closing the rest is a separate ruling, not asserted here.
- **KD-001** (second actuation-port call site in `server._manual`) — for steer/retry the endpoint
  becomes a verifier of a policy-signed directive, so it no longer calls `engine.actuator.dispatch`
  with an unsigned object. KD-001's *general* "two call sites" concern is reduced for these paths but
  the deviation entry stays open until `/api/pause` and the manual path are likewise ruled.

## Go / No-Go (scope of THIS ADR)

This ADR is a **specifications document**; it does not implement or enable actuation. Within atlas-4's
authority (architecture & protocol for `docs/`):

- **GO** — adopt this contract as the implementation spec for signed steer/retry directives and the
  pinning gate in §5.
- The platform engineer's subsequent implementation is a **separate gate**; atlas-4 does not sign off on
  the code being correct, only that this contract is the bar to meet. KD-002/KD-001 closure is
  conditional on that implementation landing and the §5 nails going green.

---
---

# Errata E-12-1 — the scope of ADR-0008 D-41: machine directives vs operator-intent records

- **Status:** **RULING, not yet ratified** — see *Ratification condition* below. Does not take effect
  until the lead ratifies it or a superseding ADR is issued. Before ratification this section is a
  **recommended reading of frozen D-1…D-41**, not an amendment of it.
- **Date:** 2026-09-25
- **Owner:** protocol-architect (`docs/ADR` only; `packages/` is not modified by this errata).
- **Triggered by:** the E-05 architecture leg's NO-GO, which held that
  `packages/jcc_core/server.py:206-239` breaks the invariant that *the policy kernel is the sole
  producer of directives*.
- **Extends (does not re-open):** ADR-0008 (D-1…D-41, **frozen**), ADR-0012 as above, ADR-0004.
- **Changes:** `docs/ADR/KNOWN_DEVIATIONS.md` **KD-001** and **KD-002** (both are **re-characterised,
  neither is closed** — see §5).

## E-12-1.1 — Question

Does ADR-0008 **D-41** govern a control-plane **manual** directive minted outside the policy kernel, or
only **machine** directives?

## E-12-1.2 — Findings, each measured rather than read

| # | Finding | Label |
|---|---|---|
| F1 | `server._manual` (`server.py:206-239`) mints a manual `Directive` and calls `engine.actuator.dispatch(d)` at `server.py:239`. The engine port is therefore called from **two** sites: `core.py:311` and `server.py:239`. | **VERIFIED** (source read) |
| F2 | The minted directive carries `signed=False`, `signature=None`, `evidence_hash=None`, `requires_human=True`, `executable=False`. | **VERIFIED** (probe 1) |
| F3 | The real signing kernel refuses it: `verify_directive(d, None) -> (False, "UNSIGNED")` **and** `verify_directive(d, KEY) -> (False, "UNSIGNED")` — i.e. supplying a key does not rescue it, so it can never become a trusted directive. | **VERIFIED** (probe 2) |
| F4 | `ActuationPort.dispatch` is permanently inert: returns `{'acted': False, 'executable': False, 'reason': 'shadow_mode', ...}` **even with `enabled=True` and the action allow-listed**. | **VERIFIED** (probe 3) |
| F5 | `requires_human` is **checked, not self-declared**. A correctly HMAC-signed directive that self-reports `requires_human=True` is refused at the human-approval gate (`APPROVAL_MISSING`) unless an approval exists **for that exact `directive_id`**; an approval recorded for a different id does not carry over. Only an approval naming that id yields `(True, 'approved by a human for this directive id')`. | **VERIFIED** (probes 4–6) |
| F6 | D-41's frozen falsifier text reads: *"`jcc_core.ports.ActuationPort.dispatch` has **exactly ONE** call site (`core.py:143`)"* (`ADR-0008:228`). The Round-5 precision fix #3 reframed *"exactly two call sites"* → *"exactly one call site **per distinct type**"*, reconciling the engine site with `Sentinel._dispatch`; it did **not** exempt any call site. | **VERIFIED** (`ADR-0008:228,258`) |
| F6b | **The `core.py:143` reference inside that falsifier is a stale line number.** `core.py:143` today is a broadcast-failure comment; the engine dispatch site is `core.py:311`. A gate or reader grepping for `core.py:143` finds nothing and may conclude the falsifier has no referent. | **VERIFIED** (`core.py:138-148`, `:311`) |
| F7 | **CORRECTED 2026-09-25 (team-lead review; the earlier wording of this row was false).** The phrase **"the policy kernel is the sole producer of directives"** is **not a normative statement in any ADR** — it appears in `docs/` only inside the **deviation register that cites it** and inside this erratum, never as a ruling a platform engineer must meet. Concretely, `git grep -in "sole producer" -- docs/` returns 4 hits: three in this erratum (this row, the INV-41 header, and §E-12-1.7) and one at `KNOWN_DEVIATIONS.md:26` — the sentence in the KD-001 ruling where the invariant is named as the criterion the deviation breaks. **The defensible claim is therefore: the invariant has no normative force; it exists only as the paraphrase that the register (and the E-05 leg) attached to KD-001.** An earlier draft of this row asserted "does not occur anywhere in `docs/`"; that was measured **before** this erratum was written, and the erratum itself then introduced the phrase, which made the row false at the moment of writing. Recorded here rather than quietly amended, because the same staleness can recur. | **VERIFIED, and self-corrected** (`git grep -in`, run 2026-09-25) |
| F8 | `packages/` holds **four** production `Directive(...)` mint sites, not two: `core.py:294` (policy kernel), `server.py:208` (control plane), `gates.py:173` (`operator-skill`, `params={"source": "operator-skill", "requires_human": True}`, `executable=False`, `shadow=True`), `sentinel.py:414` (supervisor, `shadow=True`). `apps/` holds none. | **VERIFIED** (grep) |
| F9 | Only `gates.py` and `server.py` set `params["source"]`. `core.py:294-304` sets no `params` at all and `sentinel.py:421-425` sets `params` **without** a `source` key. A classifier built on `params["source"]` therefore mis-classifies two of the four sites. | **VERIFIED** (source read) |
| F10 | `sentinel.py:507-514` annotates its own seam *"The only actuation call site in the supervisor plane"* and cites D-41 — i.e. D-41 is already applied **per plane** in the code. | **VERIFIED** (source read) |
| F11 | The behaviour is pinned green today: `test_server_surface.py::test_engine_port_dispatch_call_sites_are_pinned`, `::test_the_manual_path_produces_no_execution_result[/api/pause|steer|retry]`, `::test_the_manual_path_and_the_real_port_refuse_differently_but_equally` — **5 passed, exit 0** (`2026-09-25 18:1x`). The third asserts `actuation.reason == "shadow_mode"`, i.e. the manual path **does** reach the engine port. | **VERIFIED** (pytest run) |

## E-12-1.3 — Ruling

**D-41 does not distinguish machine directives from operator-intent records, and no exemption for
operator intent can be read into it.** Its subject is the **P4 attach point**, not directive provenance
(D-41's title; *"The ONLY P4 attach point is `Sentinel._dispatch`"*). Its frozen falsifier counts call
sites of a port **type**, with no carve-out for what the directive is. Attempting to read a
machine/manual exemption into the *falsifier* fails on the text.

**But that does not make `server.py:239` a competing attach point**, and this is the load-bearing part:

> A future actuator attaches to `supervisor.ports.DispatchPort` and is invoked **only** from
> `Sentinel._dispatch`. The engine port's implementation is, by D-41 itself, *"permanently inert … never
> an attach point — in P3 or in P4+"* (`ADR-0008:225`). A real actuator can therefore **never** be
> injected at `engine.actuator`, however many places call it. The call-site *count* is a proxy the frozen
> falsifier uses for attach-point uniqueness; it is not itself the property that matters. `server.py:239`
> fails the attach-point predicate, so it is not governed by D-41's attach-point clause.

So the deviation is real but misdescribed in the register, and the invariant the leg NO-GO'd over
(F7/F8) was never true.

### The boundary — stated so it can be applied

A mint-and-dispatch site is an **operator-intent record**, not a machine directive, iff **all three**
hold. All three are mechanically checkable.

- **B1 Attachability** — the port is the P4 attach port (`DispatchPort`) **and** the caller is
  `Sentinel._dispatch` (D-41's single attach point). Otherwise the site has **0** attach points.
- **B2 Executability** — the constructed directive carries `executable=False` (schema default is `False`;
  `schemas.py:165`). No construction site may set it true.
- **B3 Human standing** — the site records `requires_human=True`, and the human-approval gate is
  evaluated as a **check** (F5), not taken from the directive's own self-report.

`server.py:208-239` passes B1 (engine port, not the attach port), B2 (`executable=False`) and B3
(`requires_human=True`, and F5 proves the gate is checked rather than self-declared). It is therefore an
operator-intent record. **`server.py` is the fourth such site, not the second** (F8): `gates.py:173`
already established the pattern and is not registered as a deviation at all.

> **Trap, named so it is not walked into:** do **not** build the classifier on
> `params["source"]`. Two of the four sites (F9) do not set it, so that test would mis-classify the
> engine and supervisor planes. The classification is by the **plane that owns the mint site**, not by
> any field.

### Falsifier for this errata

This reading is void if any of the following becomes true:

> (i) any `ActuationPort.dispatch` call site returns a `reason` other than `"shadow_mode"`; (ii) any
> `Directive(...)` construction site in `packages/` sets `executable=True`; (iii) a `Directive(`
> construction site omits `executable=False` **and** its directive reaches a port; (iv) a real
> actuation implementation is reachable from anywhere other than `Sentinel._dispatch`.

None of (i)–(iv) holds today (F4, F11).

## E-12-1.4 — Consequences

- **The invariant is restated, not defended.** See §E-12-1.5 for the verbatim replacement. The old
  statement is **withdrawn as false** — it was already false before `server.py:239` existed, because
  `gates.py:173` and `sentinel.py:414` mint outside the kernel (F8).
- **`/api/pause|steer|retry` keep their behaviour and their Directive-shaped record.** Removing the site
  would break the D-28 manual-plane contract, terminate the `dir_manual_*` audit record, and force edits
  to three green pins (F11) — including one that asserts the manual path *does* reach the engine port.
  There is also **no verifier path to reroute to**: ADR-0012 §1's policy-signed envelope has never been
  implemented, so "reroute through the Sentinel" would change who signs what with nothing on the other
  side of the seam. Both remain open protocol decisions (the position `server.py:222-238` already takes).
- **KD-001 / KD-002 are re-characterised and stay OPEN** (§5). The "as designed" downgrade is
  **rejected** — see §E-12-1.6.
- **Nothing here exempts `server.py:239` from the ADR-0012 remediation.** It is the site ADR-0012
  §Relationship was written to fix.

## E-12-1.5 — Replacement invariant (verbatim — the E-05 leg re-signs against this, not the old claim)

> **INV-41 — directives and actuation in `packages/` (replaces the "policy kernel is the sole producer of
> directives" invariant; ADR-0008 D-41, as read by erratum E-12-1).**
>
> 1. **Every `Directive` constructed anywhere in `packages/` carries `executable=False`.** The schema
>    default is `False` (`schemas.py:165`) and no construction site overrides it.
> 2. **The engine actuation port (`ActuationPort.dispatch`) is permanently inert and is never an attach
>    point**, in P3 or later. The number of sites that call it is not a measure of attach-point
>    uniqueness.
> 3. **The only attach point for a future real actuator is `Sentinel._dispatch`**, which invokes an
>    injected `DispatchPort` and nothing else.
> 4. **A mint site is classified by the plane that owns it, not by `params["source"]`.** The `engine`
>    ingest path (`core.py:294`) produces a **machine directive**. The control-plane endpoint
>    (`server._manual`, `server.py:208`), the operator-skill gateway
>    (`gates.SignedDirectiveGate.issue`, `gates.py:173`) and the supervisor plane
>    (`sentinel.py:414`) produce **operator-intent / shadow-plane records**.
> 5. **No mint site may produce `executable=True`.** An operator-intent record carries
>    `requires_human=True` and does not reach any actuation implementation.

**Answer to the question asked of this ruling:** after this errata, *"the policy kernel is the sole
producer of machine directives"* is **false** about `packages/`, and was false before the disputed site
existed. Re-signing against it would be re-signing against a claim with no source in `docs/` (F7) and
three counter-examples in `packages/` (F8).

## E-12-1.6 — What is explicitly NOT allowed to be inferred from this errata

1. **Not** that `server.py:239` is correct, approved, or safe to keep as an actuation path. It remains a
   real deviation from D-41's frozen falsifier wording (F1, F6) and remains open as KD-001.
2. **Not** that ADR-0008 D-1…D-41 is amended. The range is frozen; this errata is a **recommended
   reading** whose binding force requires the ratification condition below. Nothing here re-opens it.
3. **Not** that KD-001 or KD-002 is closed, downgraded to "as designed", or transferred to another owner.
4. **Not** that the signature deficiency (KD-002) is resolved. F3 shows the manual directive is
   *permanently* unsigned; the deviation is structural, not incidental.
5. **Not** permission to delete `server.py:239` or to reroute it. Both are protocol decisions, and the
   second has no working alternative today (§E-12-1.4).
6. **Not** that `params["source"]` is authoritative for classification (F9).
7. **Not** that the platform is thereby released: KD-003, KD-004 and the unlanded ADR-0012 gate are
   unaffected by this errata.

### Ratification condition

This errata binds only when the lead ratifies §E-12-1.5 (INV-41) or a superseding ADR adopts it. **If it
is not ratified, D-41's frozen falsifier stands as written, `server.py:239` is a live deviation against
it, and the E-05 leg's finding on that point survives** — the answer to the leg then reverts to
**NO-GO on KD-001** rather than to GO.

## E-12-1.7 — Go / No-Go (scope of THIS errata)

Within protocol-architect's authority (architecture & protocol, `docs/` only):

- **GO WITH CONDITIONS** — adopt INV-41 (§E-12-1.5) as the wording the architecture leg re-signs
  against, **conditional on** the leg (a) withdrawing the "sole producer of directives" claim in writing,
  (b) recording that KD-001 and KD-002 stay open, and (c) recording that `gates.py:173` and
  `sentinel.py:414` are mint sites the leg did not previously count.
- **NO-GO (unconditional, on the ratification condition above)** — if the lead does not ratify,
  D-41's frozen text governs and the deviation stands.
- This errata does **not** sign off on any `packages/` code, and does not close KD-001/KD-002.

> **Path taken — 2026-09-25, team-lead ruling (this closes the open choice left at §E-12-1.4 and
> §E-12-1.6 item 5). 路径 A, the attach-point reading of D-41 as stated in INV-41 (§E-12-1.5):
> `server.py:239` is an operator-intent record, retained as built and neither deleted nor rerouted
> through the Sentinel, because the E-05 leg attests the architecture as built and KD-001 / KD-002
> therefore stay OPEN and are not downgraded to "as designed".**
