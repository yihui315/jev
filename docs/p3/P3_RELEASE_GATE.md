# P3 (Jev Shadow) — RELEASE GATE at frozen SHA `49d91d4`

**Status: P3 ACCEPTED — GATE = PASS.**
**Frozen SHA:** `49d91d45368…` (`49d91d4`) — tree **clean** (`git status --porcelain` empty at run time).
**Ruling authority:** lead (Orion). **Date:** 2026-09-25.

---

## 1. Frozen-SHA verdict (the only numbers that count)

Command (tracked in-repo; the runner was force-added precisely so this is reproducible):

```
F:\jev\.venv\Scripts\python.exe F:\jev\artifacts\acceptance\p3_gate_run.py --full
```

| Field | Value |
|---|---|
| HEAD | `49d91d4` |
| dirty | `False` |
| passed / failed / errors / skipped | **349 / 0 / 0 / 0** |
| runner exit code | **0** |
| `§5` A-matrix A1–A11 | **all PASSED** |
| D-40 falsifiers (a–e + non-vacuity) | **all PASSED** |
| absorbed failures | **none** (there is no known-pending exemption left in the runner) |
| vacuity guard | not triggered (`[]`) |
| parse reconciliation | **`349 == 349`, reconciled** |

Gate rule enforced: `passed > 0 AND failed == 0 AND errors == 0 AND skipped == 0`.
A skip is a FAILURE. Nothing is reclassified.

Machine artifact: `artifacts/acceptance/p3_gate_result.json` (carries `head`, `dirty`,
`counts`, `a_matrix`, `vacuity_guard`, `summary_reconciliation`).

---

## 2. The three gate legs

| Leg | Verdict | Basis |
|---|---|---|
| **Architecture** (ADR-0008 D-1…D-41 conformance) | **GO** | see §2.1 |
| **Security** | **GO** | `docs/SECURITY-P3-REVIEW.md` (independent reviewer), blockers = 0 |
| **SRE / Acceptance** | **GO** | §1 — frozen-SHA `--full` run, zero skips, nothing absorbed |

### 2.1 Architecture leg — the one blocker and its discharge

The architect's standing position was explicit: *"F-p3-4 / R6-1 remains open — I do not sign
the P3 architecture gate."* That blocker is discharged by commit `63d1e27`, verified by the lead
by reading the diff rather than the summary:

- `CoreEngine.__init__` gained `thresholds: Optional[ThresholdsConfig] = None`.
- `core.py` now calls `policy.decide(assessment, sess.status, thresholds=self.thresholds)` — the
  engine no longer silently falls back to the module-private `_DEFAULT_THRESHOLDS`.
- `run._wire_shadow` builds **one** `threshold_cfg = ThresholdsConfig.from_config(thresholds)` and
  injects the *same object* into both the Sentinel and the engine, with the early return preserved
  so `engine.thresholds is None` for the P0–P2 path (which is therefore unchanged).
- **D-2** holds: the composition root remains the only place `jcc_core` and `supervisor` meet.

D-32 requires one mapping authority **by construction**, not by coincidence; a non-empty
`per_agent` map previously made the engine and the shadow plane resolve *different* actions while
every label still described a policy that never ran. That is now structurally impossible.

> Formal architect re-signature at `49d91d4` was requested from the architecture owner; the leg is
> recorded as **lead-verified GO** on the evidence above, and is upgraded to the architect's own
> signature when the reply is recorded.

---

## 3. What the gate itself caught (why it is not a rubber stamp)

Every one of these was found *because* the gate was built to be able to fail:

| # | Defect | How it surfaced | Outcome |
|---|---|---|---|
| 1 | `cost_units_used` hard-coded `0.0` — the `cost_units` limit could never fire (fail-open, contrary to D-34"D-5") | budget exhaustion matrix red | FIXED (`#63`, `b5b418e`) — now derived from persisted `directives.action` via one `COST_UNITS_PER_ACTION` authority |
| 2 | **Two** threshold resolvers (engine vs Sentinel) | R6-1 differential row red | FIXED (`#61`, `63d1e27`) |
| 3 | `A10` audit↔event parity false-FAILed on any rejected ingest | found by the metric reviewer against the code | FIXED — exclusion + positive justification assertions |
| 4 | `R6-1` row was **unsatisfiable** (it called a bare module-level `policy.decide`, which cannot observe any engine-injected surface) | implementer proved no implementation could make it green | FIXED (`a6796de`) — now drives the real wiring and reads persisted state |
| 5 | Gate runner could print **`GATE = PASS` with zero tests executed** | independent security reviewer's adversarial probe | FIXED — six vacuity guards, incl. per-target contribution and A-matrix completeness |
| 6 | Gate runner's `_STATUS_RE` **silently dropped 10 tests** whose parametrized node ids contain spaces → reported 339 while pytest ran 349; a FAILED line for such a test would also have been dropped (fail-open) | found by the lead reconciling the runner's totals against a direct pytest run | FIXED (`49d91d4`) — anchor on the `[ NN%]` marker + **reconcile the parse against pytest's own tally, fatal on mismatch** |

Defects 5 and 6 are the same *class*: a verifier whose arithmetic cannot be checked. The frozen
runner now fails closed on both, and the reconciliation guard makes the parse gap of defect 6 a
fatal, self-detecting condition rather than a silent under-count.

---

## 4. MUST-FIX batch landed before the freeze

| Item | Commit |
|---|---|
| `#60` pytest basetemp → OS temp (safe-delete exempt zone) | `9c0820d` |
| `#63` `cost_units_used` derived from persisted `directives.action` | `b5b418e` |
| `#66` budget `first_seen` wall-clock vs governor monotonic clock pinned | `1746fde` |
| `#68` `StateStore.transaction()` rollback + lock release on exception pinned | `0e08677` |
| `#67` `BEGIN IMMEDIATE` precedes the tail read (+ DEFERRED falsifier) | `7aa56f6`, `cfbba8e` |
| `#61` ONE threshold resolver injected into engine **and** Sentinel | `63d1e27` |
| `#62` manual endpoints dispatch (inert) instead of `Actuator.execute` (D-28/D-41) | `92e069f`, `4b0f127` |
| `#64` config load fails closed (D-30) | `9542933` |
| `#65` `policy.version → p3.0.0` + `SCHEMA_INTERNAL_VERSION` (D-37) | `9ee4507` |
| `#69` cost lookup fail-closed (no silent `0.0` for an unknown action) | (landed in the `#63` rework) |
| `#46` gate harness tracked + R6-1 repair + A8 re-baseline | `2175361`, `a6796de` |
| Gate runner parse fix + reconciliation | `49d91d4` |

Documentation frozen with the code: `P3_ARCHITECTURE_SPEC.md` (errata E-1…E-6, incl. the F-p3-1
waiver row), `P3_METRIC_EVAL_DESIGN.md`, `SECURITY-P3-REVIEW.md`.
ADR-0008 itself is **unchanged** — its errata register stays E-1…E-4, and the decision range
remains **D-1…D-41**.

---

## 5. Waivers, deferrals and residuals (recorded, not hidden)

| Item | Disposition |
|---|---|
| Engine-side `AssessmentBudgetGuard` not wired (F-p3-1 / F-3) | **WAIVED for P3.** ADR D-5 uses *"may consult"* and states the directive-scope budget is *"fully enforced by the Sentinel"* — which **is** wired, so shadow-plane non-vacuity holds. Recorded as a waiver (not an implementation) at `P3_ARCHITECTURE_SPEC.md:500`; tracked as **P4**. Residual accepted: a token holder could grow the tape unbounded; blast radius is bounded by `127.0.0.1` + token gate. |
| `scorer_tokens` / `model_units_used` inert | **WAIVED — inert by design.** No persisted column exists. Pinned by a positive test that FAILS if a column is added without wiring the governor. |
| D-28 signing half on the manual control-plane path | **Deferred to P4** (Errata E-6). The operative prohibition — `_manual` must not call `Actuator.execute` — **is** enforced; a manual directive has no `Assessment` to arbitrate and the signing key lives in the composition root. |
| `audit.py` append lacks `BEGIN IMMEDIATE` (F-5) | **Deferred to P4.** Deliberately not churning the tamper-evidence path at freeze. |
| Read-only GETs open (`/api/audit`, `/api/decisions`) (F-6) | **Accepted for P3** — loopback-only bind. **Precondition for any non-loopback deployment.** |
| `7aa56f6` carries a copy-pasted commit message (content correct: the `#67` test) | Not rewritten — the lead does not rewrite another worker's commit. Noted for the record. |

---

## 6. P4 backlog (carried forward)

1. Wire the engine-side budget guard (D-5) — closes the F-3 DoS residual.
2. D-28 signing on the manual control-plane path (E-6).
3. `audit.py` `BEGIN IMMEDIATE` + single-instance enforcement (F-5).
4. Token-gate or document the read-only GET surface before any non-loopback bind (F-6).
5. Asymmetric non-repudiation for directives (ADR D-39 — explicitly a P4 decision).
6. `Actuator.execute` remains permanently unreachable from any port (D-41 must not regress).
7. Metric evaluation activates only with real shadow sessions (corpus-gated; nothing evaluated at P3).

---

## 7. Signature

**P3 RELEASE GATE = PASS at `49d91d4`.**

- Architecture: **GO** (§2.1)
- Security: **GO** (`docs/SECURITY-P3-REVIEW.md`, blockers = 0)
- SRE / Acceptance: **GO** (§1 — 349/0/0/0, zero skips, nothing absorbed)

No P3 acceptance decision rests on a runner whose arithmetic cannot fail: the runner now refuses
to pass on zero tests, on partial execution, on a shrunken A-matrix, and on any disagreement
between its own parse and pytest's tally.
