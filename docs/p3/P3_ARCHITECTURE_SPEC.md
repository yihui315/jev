# P3 Architecture Spec — Jev Shadow Supervision (Sentinel + Metric)

- **Companion ADR:** `docs/ADR/ADR-0008-p3-jev-shadow-supervision.md` (decisions **D-1…D-41**, **FROZEN**; Round-5/6 re-frozen — wording/precision and internal-consistency only)
- **Owner:** Atlas-2 (jcc-protocol-architect). Design only — **no product code is modified by this document.**
- **Baseline:** P2 frozen at `7f62f57`; ADR-0007 accepted.
- **Chain realized:** `Agent → Adapter → AEP → StateStore → Evidence → Jev assessment → deterministic arbitration → signed directive → (NO actuator)`

Notation: `module:function(signature) -> return`. All new code is under `F:\jev`.

---

## 0. Prerequisite (MUST land first) — ADR-0007 D-3

| Action | Path | Note |
|---|---|---|
| Create | `packages/jcc_core/redaction.py` | Move `scrub_str`, `scrub_value_secrets`, `redact`, `MAX_TEXT_LEN`, `MAX_SCRUB_DEPTH`, `DEPTH_LIMIT_MARK` here verbatim from `adapters/_common.py`. |
| Edit | `packages/jcc_core/adapters/_common.py` | Replace definitions with `from ..redaction import *` (re-export for back-compat). |
| Edit | `packages/jcc_core/core.py:25` | Change `from .adapters._common import scrub_value_secrets` → `from .redaction import scrub_value_secrets`. |
| Keep | `integrations/_common/jcc_emit.py:42` | Import from `jcc_core.adapters._common` still works via re-export (no forced churn). |

Owner: Forge. **P3 shadow MUST call the same kernel** for any JSON it persists (D-9). No new unscrubbed boundary.

---

## 1. New package: `packages/supervisor/` (+ neutral kernels in `jcc_core/`)

```
packages/supervisor/
  __init__.py
  ports.py            # Sentinel dispatch port: DispatchPort + ShadowActuationPort (D-41; the ONLY P4 attach point)
  arbitration.py      # THIN ADAPTER -> calls jcc_core.decision.arbitrate (D-32)
  budget.py           # BudgetGovernor; counters DERIVED from persisted state (D-34)
  shadow_store.py     # shadow_decisions + shadow_labels tables + hash chain
  sentinel.py         # Sentinel orchestrator (subscriber)
  evaluation.py       # Metric read/verify API
```
Neutral kernels (shared, imported DOWNWARD; no core→supervisor import — D-2/D-32/D-33):
```
packages/jcc_core/ports.py        # Protocols only
packages/jcc_core/decision.py     # THE arbitration authority: normalize() + ROWS + arbitrate() (D-32)
packages/jcc_core/signing.py      # sign_directive / verify_directive (HMAC-SHA256) (D-33)
packages/jcc_core/nonce_cache.py  # ReplayCache hoisted from integrations/ (D-33)
packages/jcc_core/redaction.py    # PREREQUISITE (ADR-0007 D-9)
```
`policy.decide` (frozen) becomes a thin wrapper over `jcc_core.decision` (D-32), so there is exactly ONE mapping authority.

---

## 2. Interfaces (exact signatures)

### 2.1 `jcc_core/ports.py` (NEW — neutral lower layer; Protocols only)

```python
class ActuationResult(TypedDict):
    acted: bool
    reason: str
    directive_id: str

class ActuationPort(Protocol):
    """The ENGINE seam only — invoked at CoreEngine.ingest step 6 (`core.py:143`).
    PERMANENTLY INERT: never an attach point, in P3 or later (D-41)."""
    def dispatch(self, directive: Directive) -> ActuationResult: ...

class AssessmentBudgetGuard(Protocol):
    def allow(self, session_id: str, units: int = 0) -> bool: ...
    def commit(self, session_id: str, units: int = 0) -> None: ...

class AllowAllGuard:            # default; preserves P0-P2 behaviour
    def allow(self, session_id: str, units: int = 0) -> bool: return True
    def commit(self, session_id: str, units: int = 0) -> None: return None
```
`jcc_core` imports **only** this module. `packages/supervisor` imports Protocols from here — never the reverse.

### 2.2 `supervisor/ports.py` — the Sentinel dispatch port (the ONLY P4 attach point; D-41)
```python
class DispatchPort(Protocol):
    """The SENTINEL seam — the sole P4 attach point. A future real actuator
    implements THIS and attaches at Sentinel._dispatch ONLY (D-41).
    DISTINCT port from jcc_core.ports.ActuationPort (which is permanently inert)."""
    def dispatch(self, directive: Directive) -> ActuationResult: ...

class ShadowActuationPort:
    """Implements DispatchPort. ALWAYS inert (D-1/D-3/D-41)."""
    def dispatch(self, directive: Directive) -> ActuationResult
        # -> {"acted": False, "reason": "shadow_no_actuation", "directive_id": directive.directive_id}
```
**Two distinct ports.** The engine binds `jcc_core.ports.ActuationPort` (permanently inert, `core.py:143`); the Sentinel binds `supervisor.ports.DispatchPort` (P3 = `ShadowActuationPort`; P4 = the real actuator). Only the **Sentinel** port is ever swapped.

### 2.3 `jcc_core/decision.py` — THE arbitration authority (D-4, D-24, D-32)
Moved to the neutral kernel so engine + shadow plane share ONE authority. `supervisor/arbitration.py` is a thin adapter that builds `Signals` and calls `decision.arbitrate`.
Ordered rule table `ROWS` (row number == priority; action column MUST equal `POLICY_PRIORITY`, `schemas.py:157-165`); `normalize()` is a pure total coercion table (never raises; UNKNOWN absorbed by row 1). Full table + coercion + reason codes: `P3_SENTINEL_DESIGN_INPUT.md` §1.2–§1.5 (normative).
```python
@dataclass(frozen=True)
class Thresholds:                           # D-24 (Round 6): 4 risk cuts; VERIFY is evidence-driven
    escalate_risk: float = 0.8
    pause_risk: float = 0.6
    steer_risk: float = 0.4
    finish_risk: float = 0.1

@dataclass(frozen=True)
class ThresholdsConfig:                     # D-24 (Round 5): the LIVE, per-agent threshold surface.
    default: Thresholds = Thresholds()      #   NO `enabled` toggle — arbitrate ALWAYS applies thresholds.
    per_agent: Mapping[str, Thresholds] = field(default_factory=dict)
    @classmethod
    def from_config(cls, th: Mapping) -> "ThresholdsConfig": ...
    def resolve(self, agent: str) -> Thresholds: ...    # per_agent[agent] -> default

@dataclass(frozen=True)
class RuleTrace:
    rule: str            # e.g. "escalate", "pause", "verify_default_deny", "budget_guard"
    value: object
    matched: bool

@dataclass(frozen=True)
class ArbitrationInput:
    assessment: Assessment            # probabilities + band + flags
    session_status: SessionStatus
    evidence_complete: bool
    budget: "BudgetSnapshot"
    thresholds: Thresholds
    policy_version: str

@dataclass(frozen=True)
class ArbitrationResult:
    action: DirectiveAction
    rationale: tuple[RuleTrace, ...]
    trace_hash: str                  # sha256 over canonical(action, rationale, policy_version)
    blocked: bool
    blocker_code: str | None         # see BlockerCode (2.7)
    needs_human: bool

def arbitrate(inp: ArbitrationInput) -> ArbitrationResult: ...
def trace_hash(action: DirectiveAction, rationale: tuple[RuleTrace, ...], policy_version: str) -> str: ...
```
Semantics: priority `ESCALATE > PAUSE > VERIFY > STEER > RETRY > FINISH > CONTINUE` (== `POLICY_PRIORITY`); row 1 absorbs all UNKNOWN (EXACT_BLOCKER → **ESCALATE**, D-35); missing evidence ⇒ VERIFY (D-35); budget substitution ⇒ ESCALATE (D-34). VERIFY is selected by evidence/uncertainty (`needs_verify`), **never** by a risk band (D-24 R6). Deterministic + total (never raises). Kernel is `jcc_core/decision.py` (D-32). **Engine ≡ shadow by construction (R6-1):** the engine's `policy.decide` wraps *this same* function and resolves **per-agent → `default`** identically to the Sentinel.

**Shadow-path provider contract (Add B; lead-authorized).** In the P3 shadow path a provider **MUST** emit **all six required boolean signals** — `REQUIRED_SIGNAL_FIELDS` = `meaningful_progress, worker_stuck, requirements_satisfied, needs_verification, ready_to_finish, needs_human` (`decision.py:61-68`) — **plus `band` and `score`**, and **MUST** set `Assessment.provider_verified` from its own `verified` attribute (D-31). **`work_off_track` is recorded-only** (`RECORDED_SIGNAL_FIELDS`, `decision.py:73`): carried on the assessment for the record, read by **no** row, and it **never** blocks when absent (D-16 / R5 #7 / R6-3).

**Operative fail-closed rule (actionable, not merely declarative).** A missing required signal is fatal *because each `None` required signal, `band is None`, and `score is None` all append a blocker that row 1 absorbs*: `required_signal_unknown` (each of the 6), `risk_level_unknown` (`band is None`), `score_unknown` (`score is None`) — `decision.py:503-510`. Each ⇒ **row 1 ⇒ `EXACT_BLOCKER` → `ESCALATE`**. **This fail-closed `ESCALATE` is DESIGNED behaviour, not a defect** — an incomplete/unverified provider is an untrusted provider; it is never `CONTINUE`. (The deterministic mock emits its signal fields from the SHA-256 seed; `real.py` populates `provider_verified` from `JevProvider.verified`.)

### 2.4 `packages/supervisor/budget.py` (D-5 REVISED by D-34)
Counters are **DERIVED from persisted state** (restart-safe), not in-memory. New store surface: `StateStore.budget_snapshot(session_id) -> dict`.
```python
@dataclass(frozen=True)
class BudgetLimits:                # D-34 defaults
    assess_calls: int = 200
    scorer_tokens: int = 200_000
    events: int = 2000
    directives: int = 200
    retries: int = 5
    escalations: int = 3
    wall_clock_s: float = 28800.0
    cost_units: float = 500.0
    cost_units_per_hour: float = 2000.0
    directives_per_minute: int = 600
    # cost table (per action): CONTINUE=0 VERIFY=1 FINISH=1 STEER=2 RETRY=3 PAUSE=5 ESCALATE=10

@dataclass(frozen=True)
class BudgetSnapshot:
    assessments_used: int; directives_used: int
    rate_used: int; model_units_used: int
    limits: BudgetLimits; exhausted_code: str | None   # None == healthy

@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool; code: str | None; snapshot: BudgetSnapshot

class BudgetGovernor:
    def __init__(self, limits: BudgetLimits, *, clock: Callable[[], float] = time.monotonic) -> None: ...
    def check(self, session_id: str, *, kind: str, units: int = 0) -> BudgetDecision: ...
        #  kind in {"assessment","directive"}  -> returns allowed/False + code
    def commit(self, session_id: str, *, kind: str, units: int = 0) -> BudgetSnapshot: ...
    def snapshot(self, session_id: str) -> BudgetSnapshot: ...

class BudgetExhausted(Exception): ...
```
`BudgetGovernor` also implements `jcc_core.ports.AssessmentBudgetGuard` (`allow`/`commit` delegate to `check`/`commit`), so it can be injected into the engine (D-5). **Exhaustion ⇒ replace with ESCALATE** (never suppress, never CONTINUE; D-34). No `budget.enabled` off-switch.

### 2.5 `jcc_core/signing.py` (D-6 REVISED by D-33)
Neutral kernel (both the policy plane signs and the actuator plane verifies).
```python
def load_signing_key(env_name: str = "JCC_DIRECTIVE_SIGNING_KEY") -> bytes | None: ...

def sign_directive(directive: Directive, key: bytes) -> str: ...          # "sha256=<hex>"
def verify_directive(directive: Directive, key: bytes | None, *, now: float,
                     replay: "nonce_cache.ReplayCache") -> tuple[bool, str]: ...  # checks 1-4 (kernel); 5-9 at boundary

class DirectiveSigner:
    SIGN_ALG = "hmac-sha256"
    def __init__(self, key: bytes | None) -> None: ...
    @property
    def available(self) -> bool: ...                      # key is not None
    @staticmethod
    def canonical(directive: Directive) -> bytes: ...     # incl. ttl_s + nonce + policy_table_version
    def sign(self, directive: Directive) -> str | None:   # "sha256=<hex>" | None
    def verify(self, directive: Directive, signature: str | None) -> bool: ...  # hmac.compare_digest
```
Signable fields (D-33): `policy_table_version, policy_version, session_id, directive_id, int(issued_at), ttl_s, nonce, action, target_actuator, evidence_hash, params`. Verification = the 9 ordered checks in D-33 — **checks 1–4 run in this pure kernel** (presence, HMAC, TTL/skew, replay-after-signature); **checks 5–9** (session match, capability, policy/table version, budget re-check, human approval) **need context a pure kernel cannot hold and belong to the effect boundary** (D-33 `:151`: "authorisation is a separate, later check"). The kernel defines the reason constants for checks 5–9 so the two layers cannot drift, and **never returns a 5–9 reason** (Divergence 3). Check 4 runs AFTER check 2. Replay cache: `jcc_core/nonce_cache.py` (hoisted; no upward import).

### 2.6 `supervisor/shadow_store.py` (D-7) — additive table + hash chain
```python
SCHEMA = """
CREATE TABLE IF NOT EXISTS shadow_decisions (
  seq              INTEGER PRIMARY KEY AUTOINCREMENT,
  decision_id      TEXT UNIQUE,
  session_id       TEXT, event_id TEXT, agent TEXT, project TEXT,
  observed_at      REAL,
  model            TEXT, provider TEXT,
  assessment_probabilities TEXT, assessment_band TEXT, assessment_flags TEXT,
  session_status   TEXT, evidence_complete INTEGER,
  event_seq        INTEGER, progress_seq INTEGER, stall_events INTEGER,
  budget_snapshot  TEXT, thresholds_snapshot TEXT,
  policy_version   TEXT, policy_config_hash TEXT,
  action           TEXT, rationale TEXT, trace_hash TEXT,
  arbitration_inputs_hash TEXT,
  blocked          INTEGER, blocker_code TEXT, needs_human INTEGER,
  directive_id     TEXT, signed INTEGER, signature TEXT,
  signing_alg      TEXT, signature_verified INTEGER,
  acted            INTEGER, actuation_reason TEXT,
  would_have_acted_in_p4 TEXT,
  prev_hash        TEXT, entry_hash TEXT
);
-- D-13: labels are a SEPARATE, MUTABLE child table (NOT on the immutable tape),
-- keyed (decision_id, label_source) so oracle vs human can be compared and
-- re-adjudication never breaks the shadow hash chain.
CREATE TABLE IF NOT EXISTS shadow_labels (
  decision_id        TEXT,
  label_source       TEXT,       -- "oracle_next_event" | "human" | future
  label_value        TEXT,       -- e.g. "warranted_pause" | "false_finish" | action name
  label_numeric      REAL,
  label_window_events INTEGER,   -- the W actually used (recomputable, never silently stale)
  labeler            TEXT,
  labeled_at         REAL,
  PRIMARY KEY (decision_id, label_source)
);
"""

def append_shadow(conn, *, record: dict) -> dict: ...      # computes prev_hash/entry_hash, INSERT
def verify_shadow_chain(conn) -> tuple[bool, int]: ...      # (intact, first_bad_seq); mirrors audit.py:100-127
def read_decisions(conn, *, session_id: str | None = None, limit: int = 500,
                   with_labels: bool = True) -> list[dict]: ...
def set_label(conn, decision_id: str, *, source: str, value: str,
              numeric: float | None = None, window_events: int | None = None,
              labeler: str | None = None) -> None: ...        # UPSERT (decision_id, source)
def get_labels(conn, decision_id: str) -> dict[str, dict]: ...  # source -> label row
```
Labels are **mutable** (re-adjudication allowed) and therefore live **outside** the hash-chained tape; `shadow_decisions` stays append-only.
```
`StateStore` gains thin delegates (`append_shadow(**record)`, `verify_shadow()`, `get_shadow(...)`) that reuse its connection/lock — same pattern as its `append_audit`/`verify_audit` (`state_store.py:360-403`).

**Contract — the interface `StateStore` MUST provide for the shadow plane (the gap that let the #54 "records nothing" defect ship).**

1. **Shadow-persistence delegates.** `store.append_shadow(**record) -> dict`, `store.verify_shadow() -> (bool, int)`, `store.get_shadow(...)` MUST each follow the existing `append_audit`/`verify_audit` pattern (`state_store.py:362-406`): take the store lock, obtain a connection, delegate to `shadow_store.<fn>(conn, …)`, close in `finally`. **Precedent:** `StateStore.append_audit` (`state_store.py:363`) is exactly this store-delegates-to-a-sibling-module shape — copy it.
2. **Connection access is the store's business — the Sentinel MUST NOT reach for a raw connection.** It MUST NOT use `store._conn` **or** the `getattr(store, "_conn", None)` idiom. `_conn` is a **method** returning a *fresh* connection (`state_store.py:107`), so `getattr(store, "_conn", None)` yields a truthy **bound method** — not a connection. Passing it to `shadow_store.append_shadow(conn, …)` raises `AttributeError`, which the Sentinel's `except Exception: return None` swallows → **zero rows persisted, silently**. Any sibling module that genuinely needs a connection MUST receive it through the store's **public** accessor (a context manager), never by attribute-poking. (The store's `_conn`/`_lock` are private implementation; the public surface is the delegates.)
3. **`budget_snapshot(session_id) -> dict`** derived from the existing P1/P2 tables (D-34 — restart-safe counters, **not** an in-memory ledger). `run.py` MUST pass `store` into `BudgetGovernor`; otherwise `budget.py` silently falls back to its in-memory ledger and **D-34 is inert**.

### 2.7 `supervisor/sentinel.py` — the seam-owning orchestrator
```python
class BlockerCode(str, Enum):
    NONE = "NONE"
    EXACT_BLOCKER = "EXACT_BLOCKER"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    SIGNING_REQUIRED_UNAVAILABLE = "SIGNING_REQUIRED_UNAVAILABLE"
    AUDIT_CHAIN_BROKEN = "AUDIT_CHAIN_BROKEN"
    CONTRADICTORY_EVIDENCE = "CONTRADICTORY_EVIDENCE"

class Sentinel:
    def __init__(self, store: StateStore, *, signer: DirectiveSigner, budget: BudgetGovernor,
                 dispatch_port: DispatchPort, thresholds: ThresholdsConfig,
                 policy_version: str, require_signature: bool = False) -> None: ...

    def on_event(self, out: dict) -> ShadowDecision:
        """Engine broadcast subscriber (D-2). `out` == core.py:177-186 payload (+ session_status)."""

    def arbitrate_and_record(self, *, assessment: Assessment, session: SessionState,
                             event_id: str, agent: str, project: str) -> ShadowDecision:
        """Pure-ish orchestration: arbitrate -> sign -> dispatch(ShadowActuationPort) -> append."""

    def _dispatch(self, directive: Directive) -> ActuationResult:
        """THE SEAM (D-3, D-41). Calls self.dispatch_port.dispatch(directive).
        P3 port returns acted=False. A P4 actuator attaches by swapping THIS port only."""
```

**Exact seam (D-3, REVISED by D-41), stated once:** the future actuator attaches at **`Sentinel._dispatch` ONLY** → the supervisor dispatch port (`supervisor/ports.py.DispatchPort`). This is a **distinct port** from the engine's `jcc_core.ports.ActuationPort` at `CoreEngine.ingest` step 6 (`core.py:143`): the engine site is **permanently inert** (never an attach point, in P3 or later) and exists for observational completeness only. In P3 **both** ports are inert — the Sentinel's injected port is `ShadowActuationPort`, which returns `{"acted": False, "reason": "shadow_no_actuation"}` for every directive. Each `dispatch` has exactly **one** call site: `ActuationPort.dispatch` only at `core.py:143`; `DispatchPort.dispatch` only at `Sentinel._dispatch` (two **distinct types**, one site each). There is **no** other actuation call site.

### 2.8 `supervisor/evaluation.py` — Metric-facing API (D-7/D-10/D-13)
```python
def recompute_decision(record: dict) -> tuple[DirectiveAction, str]:
    """Re-run arbitrate() from the stored inputs; return (action, trace_hash)."""

def verify_decision(record: dict) -> bool:
    """True iff recomputed (action, trace_hash) == stored. Falsifies 'deterministic'."""

def oracle_precision_recall(conn, *, window_events: int) -> dict:
    """Compare label_source='oracle_next_event' vs 'human' on the SAME decision_id
    (D-13 multi-source child table). Window is recomputable from label_window_events."""

def calibration_report(conn, *, window: int = 10000) -> dict:
    """Counts by (assessment_band x label_value); false-positive rate; coverage.
    Rows under provider='mock' carry the 'mock-only, non-evidential' flag (D-14)."""

def integrity_report(conn) -> dict:
    """{'audit_ok':bool,'shadow_ok':bool,'broken_at':int|None}."""

def iter_decisions(conn, *, session_id=None, limit=500, with_labels=True) -> Iterator[dict]: ...
```
Metric is a **local reader** (CLI/offline); it opens the DB read-only. **No HTTP route** is added (D-10).
**STEER is unscored in P3:** off-track (its only label source) is dropped (D-16), so `calibration_report` MUST report STEER as **DEFERRED** with trigger `payload.goal_ref`, never as `0/N-A`.

---

## 3. Schemas — additive only (D-12, reconciled with the Metric register D-13…D-24)

`packages/jcc_core/schemas.py` (additive, defaults preserve parsing):
```python
class Directive(BaseModel):
    ...
    signing_alg: Optional[str] = None            # NEW  e.g. "hmac-sha256"
    signature_status: str = "unsigned"           # NEW  unsigned|signed_verified|unavailable:no_key|required_missing
    # params["applied_thresholds"] set live (D-21) — params column already exists

class Assessment(BaseModel):                     # ADDITIVE (Metric G-8/G-5/G-7 — D-17/D-19/D-20)
    ...
    confidence: Optional[float] = None           # NEW
    confidence_kind: str = "none"                # NEW  none|heuristic|model
    # raw["timing"] = {ingest_started_at, jev_started_at, jev_finished_at, jev_call_ms, timing_source}
    #   ingest_started_at  -> ALWAYS injected by CoreEngine.ingest after provider.assess returns (D-17)
    #   jev_started/finished/call_ms -> set by the provider around its own call (mock: none)
    # raw["usage"]  = {prompt_tokens, completion_tokens, total_tokens, usage_source}
    # raw["event_id"] = <str>                    # D-22 strict join without DDL

class RuleTrace(BaseModel):                      # NEW
    rule: str; value: Any = None; matched: bool = False

class ShadowDecision(BaseModel):                 # NEW  (mirrors shadow_decisions columns; D-15/D-18)
    decision_id: str; session_id: str; event_id: str
    observed_at: float
    event_seq: int; progress_seq: int = 0; stall_events: int = 0
    action: DirectiveAction
    rationale: List[RuleTrace] = []
    trace_hash: str
    blocked: bool = False
    blocker_code: Optional[str] = None
    directive_id: Optional[str] = None
    signed: bool = False
    signature_status: str = "unsigned"
    acted: bool = False
    would_have_acted_in_p4: Optional[str] = None
    # NOTE: labels are NOT here — see ShadowLabel (child table, D-13)

class ShadowLabel(BaseModel):                    # NEW  (mutable child table row; D-13)
    decision_id: str
    label_source: str                            # oracle_next_event|human|...
    label_value: str
    label_numeric: Optional[float] = None
    label_window_events: Optional[int] = None
    labeler: Optional[str] = None
    labeled_at: float
```
`aep/v0.1` **wire envelope `AEPEnvelope` (`schemas.py:62-79`) is UNCHANGED**; additive fields land on the **internal domain** model `Assessment` (`schemas.py:107-124`) and on new names. Therefore `schemas.py` is **no longer byte-identical** to P1 — declared, expected drift (ADR-0008 D-25), not the wire. No frozen field is renamed or removed; **no DDL on any frozen P0–P2 table** (D-22, rollback safety).

**Threshold application (D-24, Round-5/6 corrected):** `decision.arbitrate` **ALWAYS** derives the action from `assessment.risk_score` using thresholds (`per_agent[agent]` → `default`), applied exactly once, with cut set **`{escalate_risk: 0.8, pause_risk: 0.6, steer_risk: 0.4, finish_risk: 0.1}`** (**`verify_risk` is deleted** — VERIFY is evidence-driven, `policy.py:33`). There is **no toggle** and **no provider-flag deferral**. **Engine ≡ shadow by construction (R6-1):** `policy.decide` (engine) wraps *this same* kernel and resolves per-agent → default identically, so the live engine's mapping is unchanged vs P2 — asserted by §6.11 (R6-6).

**Metric-register resolution map (§6 of `P3_METRIC_EVAL_DESIGN.md`):** G-1→D-13, G-2→D-14, G-3→D-15, G-4→D-16 (dropped), G-5→D-17, G-6→D-18, G-7→D-19, G-8→D-20, G-9→D-21, G-10→D-22, G-11→D-23, G-12→D-24.

---

## 4. Composition root + config (wiring, no inversion)

`packages/jcc_core/run.py` (edit — `main()` after `engine` is built):
```python
from supervisor.sentinel import Sentinel, BlockerCode
from jcc_core.signing import DirectiveSigner, load_signing_key     # D-33 / R6-4: signing lives in the NEUTRAL core
from supervisor.budget import BudgetGovernor, BudgetLimits
from supervisor.ports import ShadowActuationPort

sh = config.raw.get("p3", {}).get("shadow", {})
if sh.get("enabled"):
    th = sh.get("thresholds", {})
    signer  = DirectiveSigner(load_signing_key(sh.get("signing", {}).get("key_env", "JCC_DIRECTIVE_SIGNING_KEY")))
    budget  = BudgetGovernor(BudgetLimits(**sh.get("budget", {})), store=store)   # D-34: MUST pass the store, else the ledger is in-memory/inert
    sentinel = Sentinel(store, signer=signer, budget=budget,
                        dispatch_port=ShadowActuationPort(),           # D-41: the Sentinel seam (distinct port)
                        thresholds=ThresholdsConfig.from_config(th),   # D-24 (R5): per-agent thresholds; no toggle
                        policy_version=config.policy.version,
                        require_signature=bool(sh.get("signing", {}).get("required", False)))
    engine.subscribe(sentinel.on_event)         # observe-only (D-2); core never imports supervisor
# The engine keeps its OWN inert port (jcc_core.ports.ActuationPort): the disabled
# `jcc_core.actuator.Actuator` default. `core.py:143` is never an attach point (D-41).
```
`config/jcc.yaml` (additive block, no secrets — env names only):
```yaml
policy:
  thresholds:                       # D-24: RETIRED / INERT — superseded by p3.shadow.thresholds.
    escalate_risk: 0.8              #   Keep for config back-compat ONLY. Editing it has NO effect.
    pause_risk: 0.6
    verify_risk: 0.4
p3:
  shadow:
    enabled: true
    budget:
      max_assessments_per_session: 200
      max_directives_per_session: 200
      max_directives_per_minute: 60
      max_model_units_per_window: 1000
      window_seconds: 60
    thresholds:                     # D-24 (Round 5): the LIVE threshold surface. NO `enabled` toggle.
      default: {escalate_risk: 0.8, pause_risk: 0.6, steer_risk: 0.4, finish_risk: 0.1}
      per_agent:                    # per-agent overrides (absent => default)
        workbuddy: {}
        claude: {}
        codex: {}
        hermes: {}
    signing:
      key_env: JCC_DIRECTIVE_SIGNING_KEY
      required: false            # true => refuse to record an unsigned shadow directive
```
Engine broadcast payload (additive, `core.py:177-186`): add `"session_status": sess.status.value` so the Sentinel can arbitrate without re-reading (keeps the subscriber pure). If omitted, the Sentinel falls back to `store.get_session(session_id)`.

**Timing injection (D-17):** `CoreEngine.ingest` captures the ingest start, and **after** `provider.assess(...)` returns sets `assessment.raw.setdefault("timing", {})["ingest_started_at"] = <t>` — **without overwriting** any `jev_*` keys the provider wrote. The provider owns `jev_started_at`/`jev_finished_at`/`jev_call_ms`. Rationale: `assess()` (`base.py:21`) never receives the ingest bound, so without core injection the field is silently absent and latency p50/p95 is uncomputable.

---

## 5. Failure / fail-closed matrix (D-26, D-34, D-35)

| Condition | Detection | Action | blocker/reason | Actuates? |
|---|---|---|---|---|
| Missing/incomplete evidence (`schema_ok=False`, evidence absent) | arbitration input | `VERIFY`, `AWAIT` | `NONE` (await) — P1-frozen | no |
| **EXACT_BLOCKER**: unknown required signal, same-tier contradiction, session absent, isolation conflict, provider unverified | `normalize()` / store read | **`ESCALATE`**, `requires_human=true`, absorbing (D-35) | `blocker_kind=EXACT` | no |
| `provider.assess` raised (F1) | boundary try/except | `ESCALATE` + **still append audit** | `blocker_reasons=["provider_error"]` | no |
| Incomplete provider signal set — a required signal absent, `band`/`score` `None`, or `provider_verified` unset/false (**Add B** provider contract; **6 required** booleans + `band`/`score`; `work_off_track` recorded-only) | provider contract / `find_blockers()` (`decision.py:503-510`) | **`ESCALATE`**, fail-closed, **designed** (not a defect) + **still append audit** | `required_signal_unknown` / `risk_level_unknown` / `score_unknown` / `provider_unverified` | no |
| Malformed/unauthorised AEP body — `AEPEnvelope(**body)` raises `ValidationError` at `server.py:133-136` (F1-class, **Round 5**; mechanism frozen **Add A**) | server-boundary try/except | **no unhandled 500**; **structured `422`** + **still append audit**; **NO synthesised directive** | HTTP **`422`** (`validation_error`) | no |
| Any budget exhausted | `budget_snapshot()` | **`ESCALATE`**, replace (never suppress), stop model calls | `budget_exhausted`/`budget_kind` | no |
| Budget read raises | exception at counter read | `ESCALATE` (fail-closed) | `budget_check_error` | no |
| `required=true` and no key | `DirectiveSigner.available is False` | `ESCALATE`, record unsigned honestly | `signing_required_unavailable` | no |
| Failed dry-run verification (any of 9 checks) | `verify_directive` | deny → `verify_reason`; **no downgrade** | `UNSIGNED`/`BAD_SIGNATURE`/`EXPIRED`/`REPLAY`/… | no |
| `verify_audit()`/`verify_shadow()` not intact | integrity check | **refuse append**, alarm | `audit_chain_broken` | no |
| Normal healthy decision | arbitration | assessed action | `NONE` | no (`shadow_mode`) |

Invariant (falsifiable form, D-40): **no input ever produces a directive with `executable == true` or a resuming action (`CONTINUE`/`STEER`/`RETRY`/`FINISH`) while `blocked`.**

---

## 6. Test plan (acceptance harness additions; P3 gate will execute — not run by this doc)

**Normative test rule (from the #54 "records nothing" defect): a test double MAY NOT define an interface the real collaborator lacks.** The supervisor / shadow tests MUST exercise the **real `StateStore`** on a temporary DB — **no `_FakeStore`** that supplies an attribute (e.g. `self._conn = conn`) the production `StateStore` does not have — and MUST assert that a row **actually lands** in `shadow_decisions` with a linked hash chain (`prev_hash` == the prior row's `entry_hash`; `verify_shadow_chain` intact). Rationale: 264 green tests missed #54 because `test_supervisor.py:30-34 _FakeStore` set `self._conn = conn` as a *real connection attribute*, i.e. it defined an interface the production store lacked. **A green suite over a lying fake is not evidence** — the same failure mode would otherwise recur in P4.

New: `packages/jcc_core/tests/test_p3_shadow.py`
1. `test_arbitration_is_deterministic` — same `ArbitrationInput` ×1000 ⇒ identical `action` + `trace_hash`.
2. `test_shadow_never_actuates` — every recorded `acted is False`, `actuation_reason == "shadow_no_actuation"`; monkeypatch a side-effecting port and assert it is **never** called by product code.
3. `test_seam_is_single_site` — static grep: engine `ActuationPort.dispatch` appears **only** at `core.py:143`; Sentinel `DispatchPort.dispatch` appears **only** in `Sentinel._dispatch`; no other caller. Includes the D-40 negative control (a swapped **Sentinel** port CAN actuate; the **engine** site CANNOT).
4. `test_signing_roundtrip` — sign→verify True; tamper any signable field → False; no key ⇒ `signed False`, `signature_status=="unavailable:no_key"`, never a fake signature.
5. `test_budget_fail_closed` — exhaust each limit ⇒ **`ESCALATE`** (replace, never suppress) with `blocked`, `requires_human=true`, `executable=false`, and **no** `CONTINUE`/`FINISH` (D-34).
6. `test_exact_blocker` — missing `session_id`/unknown evidence ⇒ `blocked=True`, `blocker_code=="EXACT_BLOCKER"`, no provider call.
7. `test_metric_recompute_matches` — `verify_decision(record)` True for a corpus; mutate stored `action` ⇒ False.
8. `test_shadow_chain_tamper_evident` — mutate a historical row ⇒ `verify_shadow_chain` False at that seq.
9. `test_no_new_inbound_surface` — server route table unchanged (assert `/api/*` set equals P2's).
10. `test_shadow_path_scrubbed` — plant `Bearer <SECRET>` in evidence ⇒ absent from `shadow_decisions` bytes (D-9).
11. `test_arbitration_characterization_vs_p2` — **ASSERTED EQUALITY (R6-6):** replay the **recorded P2 provider-flag fixtures** and assert the **engine-path output (`policy.decide`) == the P2 `policy.decide` output** on the same inputs (no delta escape hatch — D-24 Round 6). This is what makes the thin-wrapper change to the live engine enforceable.
12. `test_multi_source_labels` — `set_label(d, source="human", ...)` AND `set_label(d, source="oracle_next_event", ...)` both persist on the same `decision_id`; `oracle_precision_recall` compares them; `verify_decision` unaffected; re-labelling a source UPSERTs without touching the shadow chain (D-13).
13. `test_mock_calibration_marked_non_evidential` — a calibration report over `provider="mock"` rows carries the non-evidential flag (D-14).
14. `test_thresholds_always_applied` — `decision.arbitrate` derives the action from `risk_score` via `per_agent[agent]` → `default` with cut set **`{escalate_risk, pause_risk, steer_risk, finish_risk}`** (no `verify_risk`), applied exactly **once**; there is **no `enabled` toggle** and **no provider-flag deferral** (assert the removed toggle is absent / has no effect). VERIFY remains reachable only via evidence/uncertainty. (D-24 Round-6.)
15. `test_ingest_started_at_injected` — after ingest, `assessment.raw["timing"]["ingest_started_at"]` is present and provider `jev_*` keys are **not** clobbered (D-17).

**Metric probes adopted verbatim:** §5 A1–A11 of `P3_METRIC_EVAL_DESIGN.md` (shadow≠actuation) become part of this harness — they read the `directives`/`shadow_decisions` DB, NOT the audit tape (the tape omits `acted`, per that doc's DERIVED note). T12–T16 of the Metric doc are its own.

---

## 7. Open items handed back

- **Reconciled with `P3_METRIC_EVAL_DESIGN.md`:** the §6 register G-1…G-12 is now resolved by ADR-0008 D-13…D-24 (see §3 map above). Two of its items are **dropped/refused by design**, not deferred: **G-4** off-track (no goal field — D-16) and **G-11** altering the frozen `audit_tape` payload (would break the P2 chain — D-23). Metric should treat these as closed-negative, not pending.
- **Metric review round 2 pinned (D-13 child table, D-17 core-injected `ingest_started_at`, D-24 always-applied thresholds (no toggle; Round 5) + `policy.thresholds` retired, D-25 schema-identity disclosure).** Two consequences to record in the P3 report: **STEER is unscored** (off-track was its label source) → report as DEFERRED with trigger `payload.goal_ref`; and **`schemas.py` is no longer byte-identical to P1** → declared drift on the internal `Assessment` domain, **wire `AEPEnvelope` untouched**.
- **Prerequisite D-9** (`redaction.py`) is owned by Forge and **blocks** items 10 and any shadow persistence.
- **`require_signature` default** is `false` in P3 (shadow may run unsigned); flipping it to `true` is a P4+ decision — not taken here.
- **Budget is DERIVED from persisted state (D-34), so it survives restart** — this supersedes the earlier "in-memory" note. No `budget.enabled` off-switch.
- **No new inbound surface**: if a future Phase wants an HTTP view of shadow decisions, it must be justified against ADR-0005 and go through the security gate — P3 does not.
- **Versioning (D-37):** `policy.version` `p1.0.0 → p3.0.0`; new `SCHEMA_INTERNAL_VERSION="p3.0.0"`; `Directive.policy_version` default → `p3.0.0`.

---

## 8. Consolidation with the Sentinel design input (D-26…D-40)

`P3_SENTINEL_DESIGN_INPUT.md` is the second parallel design input to ADR-0008. Its normative detail (rule table §1.4, coercion §1.2, blocker §5.1, budget §2, signing §3, shadow proofs §4.3) is **adopted** and mirrored in ADR-0008 D-26…D-40. Findings → obligations:

| Sentinel finding | Sev | ADR | Obligation |
|---|---|---|---|
| Provider picks the action; `policy.thresholds` dead | HIGH | D-32 | `jcc_core/decision.py` = sole authority; `policy.decide` wrapper; `mock` emits signals |
| F1 provider exception ⇒ tape hole (`core.py:105` before `:125`, audit at `:166`) | HIGH | D-26 | boundary catch → EXACT_BLOCKER → **still audit** |
| INV-SHADOW holds by accident (`core.py:143` calls `execute`) | HIGH | D-27 | verify-only port; `execute` unreachable from ingest |
| Policy bypass `server.py:149` (`_manual` → `execute`) | MED | D-28 | route `/api/pause\|steer\|retry` through arbitration+signing |
| `get_directives` never reads `signature` | MED | D-29 | extend read path + `_row_to_directive` |
| F11 `load_config` silent default | MED | D-30 | fail closed on explicit-but-unreadable; entrypoint refuses missing default |
| F2 `band="unverified"` outside enum (`real.py:48`) | LOW | D-31 | explicit `provider_verified`; stop magic band |
| `ReplayCache` in `integrations/` would invert upward | MED | D-33 | hoist to `jcc_core/nonce_cache.py` (with D-9) |
| A2 internal-vs-wire model scope | — | D-38 | CONFIRMED: `AEPEnvelope`/`aep/v0.1` frozen; `Assessment`/`Directive` internal |
| A3 HMAC vs asymmetric | UNRESOLVED | D-39 | symmetric in P3; asymmetric = explicit P4 decision |

**Adopted design inputs are citable**: `P3_SENTINEL_DESIGN_INPUT.md` (rule table/budget/signing/shadow-proofs), `P3_METRIC_EVAL_DESIGN.md` (evaluation), `P3_ARCHITECTURE_SPEC.md` (this file), `docs/ADR/ADR-0008-…md` (decisions).

---

## 9. Freeze alignment (lead rulings A–I + D-41)

ADR-0008 is **FROZEN at D-1…D-41**. This spec is the frozen companion. The lead rulings A–I and the P4 attach-point ruling are realized as follows:

| Lead ruling | ADR decision | Where in this spec |
|---|---|---|
| A — provider-exception boundary (`core.py:125`) still audits | D-26 | §5 row "`provider.assess` raised (F1)" |
| B — non-actuating by construction | D-27 | §1, §2.1/§2.2, §5 invariant |
| C — persisted/derived budget counters | D-34 | §2.4 (`budget_snapshot`) |
| D — `ttl_s`+`nonce` signed; signature before replay | D-33 | §2.5 (canonical + 9 checks) |
| E — one `EXACT_BLOCKER`, ESCALATE (absorbing) | D-35 | §2.3 semantics, §5 row |
| F — no `work_off_track` dependency; STEER DEFERRED | D-16 | §2.8 note, §7 |
| G — `get_directives`/`_row_to_directive` read `signature` | D-29 | §2.6 store delegates |
| H — `_manual` must not call `Actuator.execute` | D-28 | §5 (no act path), §6.9 |
| I — config load fails closed | D-30 | §4 (entrypoint refuses missing default) |
| **P4 attach-point** — `Sentinel._dispatch` ONLY; distinct ports; `core.py:143` permanently inert | **D-41** | **§2.1 / §2.2 / §2.7 / §4 / §6.3** |

**Freeze confirmations carried by this spec:** `aep/v0.1` wire untouched (§3); all new fields optional/defaulted (§3); no DDL on frozen P0–P2 tables (§2.6, §3); P3 non-actuating (§5); prerequisite D-9 blocks shadow persistence (§0).

### Round 5 — interpretation & precision fixes (lead-authorized re-open; re-frozen)

Wording/precision only; no design intent changed; **no new decision**. Mirrors the *Round 5* table in ADR-0008:

| Ruling | Decision | Where in this spec |
|---|---|---|
| Delete the "defer to provider flags" branch; `arbitrate` **always** applies thresholds; equivalence by construction; config `{default, per_agent}` (no `enabled`) | D-24 (+D-32) | §2.3 (`ThresholdsConfig`), §3, §4, §6.11/§6.14 |
| Tripwire pins the legacy `Actuator.execute`; the engine `dispatch` is called **once per ingest** and returns `acted=False` | D-40(a) | §2.2, §6.3 |
| **One call site per distinct port type** (not two sites of one type) | D-41 | §2.7, §6.3 |
| AST allow-list = `{dispatch, verify_envelope, can_act}` | D-40(b) | §5, §6.3 |
| Fixtures through the adapters in-process; **server boundary** must not 500 without an audit row | D-40(c) + D-26 | §5 row (F1-class), §6 |
| `human_clear` travels in `payload`; **no** new `EventType` member | D-35 | §2.3 semantics |
| `STEER` rule row survives; DEFERRED = **metric scoring only**; `work_off_track` drops only as a **decision input** | D-16 | §2.8 note, §7 |

**Additional consistency fix (flagged to lead):** §6.5 aligned to the frozen D-34 — budget exhaustion ⇒ **`ESCALATE`** (was stale `VERIFY`).

### Round 6 — internal-consistency repair (lead-authorized re-open; re-frozen)

Wording/schema precision + internal-consistency only; **no new decision**. Mirrors the *Round 6* table in ADR-0008:

| Ruling | Decision | Where in this spec |
|---|---|---|
| R6-1 engine ≡ shadow by construction (per-agent → default; thin wrapper) | D-24/D-32 (consequence) | §2.3 semantics, §3, §6.11 |
| R6-2 `Thresholds` = `{escalate_risk, pause_risk, steer_risk, finish_risk}`; **`verify_risk` deleted** | D-24 | §2.3 (`Thresholds`), §3, §4, §6.14 |
| R6-3 rule-table integrity (drop `work_off_track`; VERIFY evidence-only; STEER band 0.4; FINISH band 0.1) | D-16 / D-24 | `P3_SENTINEL_DESIGN_INPUT.md` §1.4 |
| R6-4 `signing` import must be `jcc_core.signing` | D-33 | §4 imports |
| R6-5 no `thresholds.enabled`; Sentinel D6 points at the live surface | D-24 | §4 config |
| R6-6 characterization test = **ASSERTED EQUALITY** | D-24 | §6.11 |

**Reference correction:** the composition root is `packages/jcc_core/run.py` (228 lines). Forge's current wiring binds `jcc_core.decision` (import @ `run.py:170`), a `supervisor.signing` import (@ `run.py:168` — **to be changed to `jcc_core.signing`** per R6-4), and `ThresholdsConfig.from_config(...)` (@ `run.py:183`). (An earlier `run.py:320` citation was wrong.)

### Addenda — precision / contract-recording (lead-authorized; range unchanged D-1…D-41)

Lead-authorized **contract-recording only**; **no new decision**. Mirrors the *Addenda* table in ADR-0008:

| # | Item | Decision | Where in this spec |
|---|---|---|---|
| **Add A** server-boundary mechanism frozen = **structured `422` + audit row, NO synthesised directive** (provider boundary keeps `EXACT_BLOCKER → ESCALATE`); only the server boundary uses `4xx`+audit | D-26 | §5 row (F1-class, "mechanism frozen Add A") |
| **Add B** shadow-path provider contract — provider MUST emit **all six required** boolean signals **+ `band`/`score`** and set `provider_verified` (`work_off_track` **recorded-only**); omission ⇒ `EXACT_BLOCKER → ESCALATE` (**designed fail-closed, not a defect**) | D-31 / D-26 | §2.3 (provider-contract subsection + operative rule), §5 row |
| **8→6** signal-count correction in `P3_SENTINEL_DESIGN_INPUT.md` (`:84/:152/:394/:401`) — **6 required** (+ `band`/`score`; `work_off_track` recorded-only) | D-31 / D-35 / D-16 | sentinel doc only |
