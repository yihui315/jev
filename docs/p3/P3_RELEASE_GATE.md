# P3 (Jev Shadow) — RELEASE GATE at frozen SHA `49d91d4`

**Status: P3 ACCEPTED — GATE = PASS.**
**Product freeze:** the 12 pinned production artifacts at `49d91d4` (byte-identical through `4cc657f`; proof in §1.3).
**Canonical verdict artifact:** clean detached-worktree run at `4cc657f`.
**Ruling authority:** lead (Orion). **Date:** 2026-09-25.

---

## 1. Frozen-SHA verdict

### 1.1 Canonical run (the numbers that count)

Produced in a **pristine detached worktree** of the frozen code, so `git status --porcelain` was
genuinely empty rather than empty-by-circumstance:

```
F:\jev\.venv\Scripts\python.exe <worktree>\artifacts\acceptance\p3_gate_run.py --full
```

| Field | Value |
|---|---|
| HEAD | `4cc657f` |
| dirty | **`False`** |
| passed / failed / errors / skipped | **349 / 0 / 0 / 0** |
| runner exit code | **0** |
| `§5` A-matrix A1–A11 | **all PASSED** |
| D-40 falsifiers (a–e + non-vacuity) | **all PASSED** |
| absorbed failures | **none** (the runner has no known-pending exemption left) |
| vacuity guard | not triggered (`[]`) |
| parse reconciliation | **`349 == 349`, reconciled** |

Gate rule enforced: `passed > 0 AND failed == 0 AND errors == 0 AND skipped == 0`. A skip is a
FAILURE. Nothing is reclassified.

Artifacts: `artifacts/acceptance/p3_gate_result.json` (machine), `artifacts/acceptance/P3_SRE_GATE_HARBOR.md`
(SRE leg report, independent), `.workbuddy/harbor_clean_full.txt` (raw output).

### 1.2 Provenance incident (recorded, not papered over)

An earlier in-place run at `49d91d4` also reported `HEAD=49d91d4 dirty=False`, `349/0/0/0`,
`GATE = PASS` (console log: `.workbuddy/p3_verdict_final.txt`). It was truthful — but it was
backed only by a gitignored log, and its machine JSON was **subsequently overwritten** by a
concurrent run at `1082fff` (`dirty=True`, caused by a co-worker's uncommitted doc edit), because
`p3_gate_result.json` is a single mutable path. The overwritten file is preserved as
`artifacts/acceptance/p3_gate_result.superseded_dirty_1082fff.json`.

Root cause and fix:
- **Cause:** a shared, mutable verdict artifact + a second run against a dirty tree.
- **Fix 1 (process):** the canonical verdict is now taken in a **detached worktree** outside the
  working tree; and any run taken on a dirty tree must be labelled as such and never overwrite the
  canonical artifact.
- **Fix 2 (durability):** verdict artifacts must be immutable per run (see P4 backlog §6).
- **Honesty note:** the dirty set at `1082fff` was provably non-production (a doc diff plus the
  co-worker's scratch files) and A8 still passed — so the substantive verdict never depended on it.

### 1.3 Product identity across the freeze window (why the freeze is not weakened)

`git diff 49d91d4 4cc657f -- <the 12 pinned artifacts>` is **empty**. The 12 files are byte-identical;
only docs and the test instrument moved between those commits. Verified in the main tree (raw bytes
12/12 == baseline) and in a fresh worktree (12/12 after CRLF→LF normalisation).

---

## 2. The three gate legs

| Leg | Verdict | Basis |
|---|---|---|
| **Architecture** (ADR-0008 D-1…D-41 conformance) | **GO WITH CONDITIONS** | `docs/p3/P3_ARCH_GO.md` (architect's own signature, commit `d002260`) |
| **Security** | **GO** | `docs/SECURITY-P3-REVIEW.md` (independent reviewer), product blockers = 0 |
| **SRE / Acceptance** | **GO WITH CONDITIONS** | §1 above + `artifacts/acceptance/P3_SRE_GATE_HARBOR.md` |

### 2.1 Architecture leg — the blocker, its discharge, and the architect's own verdict

The architect's standing blocker was: *"F-p3-4 / R6-1 remains open — I do not sign the P3
architecture gate."* It is discharged by commit `63d1e27`:
`CoreEngine` gained an injected `thresholds`; `core.py` passes `thresholds=self.thresholds`;
`run._wire_shadow` builds **one** `threshold_cfg` and gives the *same object* to both the Sentinel
and the engine, with the early return preserved so the P0–P2 path is unchanged.

The architect then re-reviewed the frozen tree and returned **GO WITH CONDITIONS** (all five
architecture questions PASS; `#69` absent; no live violation of a frozen decision). Its conditions,
carried forward verbatim in substance:

| # | Condition | Owner | Blocks the leg? |
|---|---|---|---|
| **C1** | `budget.py:256` `COST_UNITS.get(action, 0.0)` is fail-open — land `#69`, **or** record it as an accepted-LOW latent risk | forge / lead | No (latent only: the table covers all seven `DirectiveAction` members today) |
| **C2** | Refresh two stale `server.py` line citations in the spec (E-2 `:174-183`→`:184-193`; E-6 `:173`→`:183`) | atlas-2 | No (doc precision) |
| **C3** | Release-record integrity: re-attribute `02bdb3f`/`7aa56f6`; optionally add the one-line alias-identity pin | lead / forge | No (record only) |

It also recorded a LOW residual: `supervisor/sentinel.py:84` keeps `self.thresholds = thresholds or
ThresholdsConfig()` — a silent default if a caller omits the object. Unreachable in production
wiring, but the same "silent default" shape as F-p3-1/F-p3-5.

---

## 3. What the gate itself caught (why it is not a rubber stamp)

Every one of these was found *because* the gate was built to be able to fail:

| # | Defect | How it surfaced | Outcome |
|---|---|---|---|
| 1 | `cost_units_used` hard-coded `0.0` — the `cost_units` limit could never fire (fail-open, contrary to D-34/D-5) | budget exhaustion matrix red | FIXED (`#63`, `b5b418e`) — now derived from persisted `directives.action` via one `COST_UNITS_PER_ACTION` authority |
| 2 | **Two** threshold resolvers (engine vs Sentinel) | R6-1 differential row red | FIXED (`#61`, `63d1e27`) |
| 3 | `A10` audit↔event parity false-FAILed on any rejected ingest | found by the Metric reviewer against the code | FIXED — exclusion **plus** positive justification assertions |
| 4 | `R6-1` row was **unsatisfiable** (it called a bare module-level `policy.decide`, which cannot observe any engine-injected surface) | the implementer proved no implementation could make it green | FIXED (`a6796de`) — now drives the real wiring and reads persisted state |
| 5 | Gate runner could print **`GATE = PASS` with zero tests executed** | independent security reviewer's adversarial probe | FIXED — six vacuity guards (per-target contribution, A-matrix completeness, rc semantics, …) |
| 6 | Runner's `_STATUS_RE` **silently dropped 10 tests** whose parametrized node ids contain spaces → reported 339 while pytest ran 349; a FAILED line for such a test would also have been dropped (fail-open) | found by the lead reconciling the runner's totals against a direct pytest run | FIXED (`49d91d4`) — anchor on the `[ NN%]` marker **and reconcile the parse against pytest's own tally, fatal on mismatch** |
| 7 | The verdict artifact itself was **clobberable** by a concurrent dirty run (§1.2) | independent SRE leg re-derivation | RECORDED + procedure fixed; durability fix in §6 |
| 8 | A8 pin was **not portable** (absolute keys + un-normalised EOL vs `core.autocrlf=true`) | independent SRE leg's fresh-worktree check | FIXED (`4cc657f`) — EOL-normalised hashes, repo-relative keys, symmetric guard |

Defects 5 and 6 are the same *class*: a verifier whose arithmetic cannot be checked. Both are now
fatal-by-construction rather than silent.

---

## 4. MUST-FIX batch landed before the freeze

| Item | Commit(s) |
|---|---|
| `#60` pytest basetemp → OS temp (safe-delete exempt zone) | `9c0820d` |
| `#63` `cost_units_used` derived from persisted `directives.action`, one cost authority | `b5b418e` |
| `#66` budget `first_seen` wall-clock vs governor monotonic clock pinned | `1746fde` |
| `#68` `StateStore.transaction()` rollback + lock release on exception pinned | `0e08677` |
| `#67` `BEGIN IMMEDIATE` precedes the tail read (+ DEFERRED falsifier) | `cfbba8e` |
| `#61` ONE threshold resolver injected into engine **and** Sentinel | `63d1e27` |
| `#62` manual endpoints dispatch (inert) instead of `Actuator.execute` (D-28/D-41) | `92e069f`, `4b0f127` |
| `#64` config load fails closed (D-30) | `9542933` |
| `#65` `policy.version → p3.0.0` + `SCHEMA_INTERNAL_VERSION` (D-37) | `9ee4507` |
| `#46` gate harness tracked + R6-1 repair + A8 re-baseline | `2175361`, `a6796de` |
| A8 pin portability (EOL-normalised, repo-relative) | `4cc657f` |
| Gate runner parse fix + pytest-tally reconciliation | `49d91d4` |
| `#69` | **NOT LANDED** — see §2.1 C1 |

Documentation frozen with the code: `P3_ARCHITECTURE_SPEC.md` (errata E-1…E-6 incl. the F-p3-1
waiver row), `P3_METRIC_EVAL_DESIGN.md`, `SECURITY-P3-REVIEW.md`, `P3_ARCH_GO.md`.
ADR-0008 itself is **unchanged**; its errata register stays E-1…E-4 and the decision range remains
**D-1…D-41**.

### 4.1 Commit-record corrections (from §2.1 C3)

- **`7aa56f6` is an ORPHAN** — `git merge-base --is-ancestor 7aa56f6 HEAD` exits 1; it is not in the
  frozen tree and must not be cited as part of it. (It shares author-date and stat with `02bdb3f`.)
  It appears in no earlier draft of this record as a source of frozen content — this line exists to
  prevent the mistake being repeated.
- **`02bdb3f` does not do what its message says.** Its message claims it *"pin[s] supervisor
  COST_UNITS as an alias of the single core cost authority"*, but its diff adds an **unrelated**
  test (`test_production_write_path_reaches_append_shadow_outside_a_transaction` — a valid E-5
  precondition pin). Consequence: **no test pins the alias identity** today; the gate pins the
  table's *values*, not the object identity. The alias itself is real and source-verified
  (`budget.py:53`), so "one table" holds — but the promised falsifier is absent.
- Root cause of both: concurrent writers on one shared branch. Mitigation: path-scoped commits only;
  serialise commits at freeze windows.

---

## 5. Waivers, deferrals and residuals (recorded, not hidden)

| Item | Disposition |
|---|---|
| Engine-side `AssessmentBudgetGuard` not wired (F-p3-1 / F-3) | **WAIVED for P3.** ADR D-5 says the engine *"may consult"* and that the directive-scope budget is *"fully enforced by the Sentinel"* — which **is** wired, so the shadow plane is non-vacuous. Recorded as a waiver (not an implementation) at `P3_ARCHITECTURE_SPEC.md:500`; tracked as **P4**. Residual accepted: a token holder could grow the tape unbounded; blast radius bounded by loopback bind + token gate. |
| `scorer_tokens` / `model_units_used` inert | **WAIVED — inert by design.** No persisted column exists. Pinned by a positive test that FAILS if a column is added without wiring the governor. |
| `#69` `budget.py:256` `.get(action, 0.0)` fail-open | **ACCEPTED AS LATENT (LOW)**, not a live defect: the table covers all seven `DirectiveAction` members. Closing it by test pin (table↔enum totality + alias identity) rather than by product churn at freeze; product-side `COST_UNITS[action]` recorded as P4. |
| D-28 signing half on the manual control-plane path | **Deferred to P4** (Errata E-6). The operative prohibition — `_manual` must not call `Actuator.execute` — **is** enforced (`server.py:183` `dispatch`); a manual directive has no `Assessment` to arbitrate and the signing key lives in the composition root. |
| `audit.py` append lacks `BEGIN IMMEDIATE` (F-5) | **Deferred to P4.** Deliberately not churning the tamper-evidence path at freeze. |
| Read-only GETs open (`/api/audit`, `/api/decisions`) (F-6) | **Accepted for P3** — loopback-only bind. **Precondition for any non-loopback deployment.** |
| `sentinel.py:84` silent `thresholds or ThresholdsConfig()` default | **Recorded LOW** (architect). Unreachable in production wiring; P4 tidy-up. |
| `7aa56f6` orphan commit; `02bdb3f` message/content mismatch | **Recorded** (§4.1). Not rewritten — the lead does not rewrite another worker's commit. |

---

## 6. P4 backlog (carried forward)

1. Wire the engine-side budget guard (D-5) — closes the F-3 DoS residual.
2. `budget.py:256` → fail-closed indexing (`COST_UNITS[action]`), plus the two test pins from §5.
3. D-28 signing on the manual control-plane path (E-6).
4. `audit.py` `BEGIN IMMEDIATE` + single-instance enforcement (F-5).
5. Token-gate or document the read-only GET surface before any non-loopback bind (F-6).
6. Remove `sentinel.py:84`'s silent default (fail closed if no surface is injected).
7. Asymmetric non-repudiation for directives (ADR D-39 — explicitly a P4 decision).
8. **Repo/hygiene:** add `.gitattributes` (`* text=auto eol=lf`) — `core.autocrlf=true` with no
   `.gitattributes` is what made raw A8 hashes non-portable (F-H2). Deferred so it does not
   mass-mark files modified during closeout.
9. **Verdict-artifact immutability:** `p3_gate_result.json` is a single mutable path; a second run
   can clobber the canonical record (§1.2). Make per-run artifacts immutable/append-only.
10. Refresh stale line citations in the spec (C2) and re-attribute `02bdb3f`/`7aa56f6` (C3).
11. Metric evaluation activates only with real shadow sessions (corpus-gated; nothing evaluated at P3).
12. `Actuator.execute` must remain unreachable from any port (D-41 must not regress).

---

## 7. Signature

**P3 RELEASE GATE = PASS at product freeze `49d91d4` (canonical artifact `4cc657f`).**

- Architecture: **GO WITH CONDITIONS** (`docs/p3/P3_ARCH_GO.md`, architect's own verdict; conditions
  C1–C3 are record/latent items and do not block the leg)
- Security: **GO** (`docs/SECURITY-P3-REVIEW.md`, product blockers = 0)
- SRE / Acceptance: **GO WITH CONDITIONS** (`P3_SRE_GATE_HARBOR.md`; C1 = reconcile this record with
  its evidence — done in §1.2/§1.3; C2 = accept `4cc657f` — accepted)

No P3 acceptance decision rests on a runner whose arithmetic cannot fail: the runner refuses to pass
on zero tests, on partial execution, on a shrunken A-matrix, on contradictory return codes, and on
any disagreement between its own parse and pytest's tally.
