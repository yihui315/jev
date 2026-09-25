# P3 Architecture Gate — verdict

- **Owner:** atlas-2 (architecture / protocol). This is the **architecture leg only**.
- **Frozen SHA judged:** `49d91d45168cdcba61059c23d4701021bfee26a0` (`49d91d4`), tree clean (`dirty=False`) at review time.
- **Contract judged against:** `docs/ADR/ADR-0008-p3-jev-shadow-supervision.md` **D-1…D-41** (frozen), plus the normative `docs/p3/P3_ARCHITECTURE_SPEC.md`. **Not** judged against the implementation's own summary.
- **Verdict: `GO WITH CONDITIONS`** — every one of the five architecture questions resolves **PASS**; no **live** violation of a frozen decision was found. The conditions (see §6) are release-record and defence-in-depth items, not runtime defects.
- **This file is post-freeze and docs-only.** It adds no code and no decision; the range remains **D-1…D-41**.
- **Re-signed at `280bfa8`: `GO`.** This supersedes the `GO WITH CONDITIONS` recorded above, which **stands as the historical record for `49d91d4`**. The body below is that `49d91d4` verdict; it is unedited except where a closure is marked inline (C1 §4/§6, C3 §5/§6, C2 §6).

---

## 1. `D-32 / R6-1` — one mapping authority *by construction* → **PASS (closed)**

| Link | Evidence |
|---|---|
| Single decision kernel | `decision.decide_action` (`decision.py:712`) is the only function that maps an `Assessment` + `Thresholds` → `DirectiveAction`. |
| Engine resolves through *its own* injected surface, not the module default | `core.py:172` `action = policy.decide(assessment, sess.status, thresholds=self.thresholds)`; `policy.py:81` `cfg = _DEFAULT_THRESHOLDS if thresholds is None else thresholds`; `policy.py:85` `return decision.decide_action(assessment, cfg.for_agent(agent))`. |
| ONE surface object built once | `run.py:183` `threshold_cfg = ThresholdsConfig.from_config(thresholds)`; `run.py:203` `thresholds=threshold_cfg` (Sentinel); `run.py:212` `engine.thresholds = threshold_cfg` (engine). Same object, not merely equal values. |
| Shadow plane consumes the same object | `supervisor/sentinel.py:158` `thresholds = self.thresholds.for_agent(agent_name)` → `:220` passed into arbitration; `:307` records `policy_config_hash`. |
| No second resolver anywhere | Repo-wide search for threshold resolution finds only the sites above. `supervisor` never defines its own cut set; it consumes the core kernel (`test_supervisor.py:588` pins `arbitration.arbitrate(a).action is decision.decide_action(a)`). |
| Falsifier exists and is green | `test_R6_1_engine_and_shadow_resolve_the_SAME_threshold_surface` asserts object identity (`engine.thresholds is sentinel.thresholds`), non-vacuity of a real `workbuddy.steer_risk 0.4→0.6` override (default `STEER` vs override `CONTINUE` at risk 0.5), and the **persisted** equality `directives.action == shadow_decisions.action == "CONTINUE"` plus `policy_config_hash`. |

**Residual (LOW, does not reopen D-32):** `supervisor/sentinel.py:84` still has `self.thresholds = thresholds or ThresholdsConfig()` — a silent default if a caller omits the object. Unreachable in the production wiring (`run.py` always injects), but it is the same "silent default" shape as F-p3-1/F-p3-5. For the P0–P2 path, `engine.thresholds is None` is **intentional** backward compatibility and is unreachable while the shadow plane is disabled (`run.py:160-162` early return).

## 2. `D-2` — no `jcc_core` → `supervisor` import → **PASS (closed)**

- `packages/jcc_core`'s **only** supervisor imports are **function-local**, inside the composition root: `run.py:165-167`, reached **after** the `enabled` early-return (`run.py:160-162`). The module-level import graph of `jcc_core` does not pull `supervisor`.
- The composition root is therefore the single meeting point, and the wiring is observe-only (`engine.subscribe`, `run.py:213`).
- Test-only imports of `supervisor` exist under `packages/jcc_core/tests/` (`test_p3_acceptance_gate.py:69-85`, `test_p3_core_integration.py:402`, `test_state_store.py:298/328`). They are **not** in the production import graph; noted for completeness only (LOW / informational — gate harness placed under the core package).

## 3. `D-23` / `D-24` / `D-26` — frozen tape, cut set, server boundary → **PASS (closed)**

- **D-23 (tape byte-frozen):** `packages/jcc_core/audit.py` has exactly **one** commit in its entire history — `a95eb70` (P1 control-plane skeleton). P3 never touched it, so the canonical payload and the `entry_hash` key set are unchanged. `acted`/`actuation_reason` are hash-covered on the **new** shadow tape instead, exactly as D-23 requires.
- **D-24 (Round-5/6 cut set):** `decision.py:191-194` defaults are exactly `escalate_risk 0.8 / pause_risk 0.6 / steer_risk 0.4 / finish_risk 0.1`, with the ordering invariants enforced in `__post_init__` (`:205-214`). `ThresholdsConfig` is `{default, per_agent}` (`:239-248`) — **no `enabled` toggle**. `verify_risk` and `enabled` are in `RETIRED_THRESHOLD_KEYS` (`:179`) and are **rejected loudly**, not silently ignored (`:224-229`, `:265`). This matches D-24's "`verify_risk` is DELETED, not aliased".
- **D-26 (server boundary):** `server.py:144-161` — an invalid envelope appends its audit row (`assessment="rejected:EXACT_BLOCKER"`, `directive="NONE:rejected"`, `agent` carried **verbatim** from the body) and *then* raises a structured `422`; `engine.ingest` is only reached at `:161`, so no event is persisted without a record. Mechanism unchanged at the frozen SHA.

## 4. `D-34` — one cost authority, fail-closed → **PASS (C1 closed at `280bfa8`)**

- **One table, not two (VERIFIED):** `policy.py:55` is the only definition of `COST_UNITS_PER_ACTION`; `supervisor/budget.py:53` is an **alias of that same object** (`COST_UNITS: Dict[DirectiveAction, float] = COST_UNITS_PER_ACTION`); `state_store.py:559` indexes it to derive the persisted `cost_units_used` from `directives.action`. A repo-wide search finds `COST_UNITS*` in exactly five files — one definition, one alias, one store consumer, two test/tool references. Direction is D-2-clean (supervisor → core).
- **Fail-closed on the durable path (VERIFIED):** an action string with no cost entry raises `ValueError` (`state_store.py:559-566`), which the governor turns into `CODE_CHECK_ERROR` (exhausted) via `budget.py:276-277`. Under-counting is impossible on that path.
- **C1 — CLOSED at `280bfa8` (this was the one open condition here).** At `49d91d4` this read `row["cost_units_used"] += COST_UNITS.get(action, 0.0)`: an unknown action was silently priced at `0.0` on the governor's in-memory ledger path — the fail-open mode D-34/D-5 forbid. `280bfa8` replaces it with a guarded direct index: `if action is not None: try: unit_cost = COST_UNITS[action] except (KeyError, ValueError) as exc: raise ValueError(...) from exc; row["cost_units_used"] += unit_cost`. The `action is None` path is **preserved** (a non-directive commit stays legal), and `CONTINUE` charging `0.0` is a **table hit**, not the fail-open. Reachability was and remains **nil** — the table covers **all seven** `DirectiveAction` members (`schemas.py:60-69` vs `policy.py:55-63`) — so this was **defence-in-depth, not a live defect**. **Contained, not a new crash path:** `Sentinel.on_event` (`sentinel.py:116-130`) catches `Exception`, counts `persistence_failures`, and never breaks ingest; the production site `sentinel.py:215` only ever passes an arbitrated `DirectiveAction`. **Ordering (architectural):** `commit` at `:215` precedes `_record` at `:218`, so an impossible-today raise drops the *record* rather than writing a bad cost — and it is **counted**, flipping `persistence_ok` (`sentinel.py:107-113`) False so D-40's non-vacuity check goes **RED** instead of vacuously green. **Pins:** `packages/supervisor/tests/test_budget_cost_failclosed.py` — alias **identity** (`is`, not `==`), table↔enum **totality**, raise-on-uncostable, and exact-value non-vacuity (`CONTINUE` leaves `13.0` unchanged, so "raises for everything" cannot pass).

## 5. The two documented deviations — neither changes the verdict

- **F-p3-1 waiver** (`P3_ARCHITECTURE_SPEC.md:500`) — engine-side `AssessmentBudgetGuard` unwired, deferred to P4. D-5 is **permissive** ("may consult") and the directive-scope budget **is** enforced by the Sentinel with the store-backed `BudgetGovernor` (`run.py:187-197`, including the required `clock=time.time` so the wall-clock dimension actually fires). The shadow plane is therefore non-vacuous. Recorded as a waiver, explicitly "not an implementation". **No MUST in D-1…D-41 is violated. Verdict unchanged.**
- **Errata E-6** (`P3_ARCHITECTURE_SPEC.md:579`) — D-28 signing on the manual control-plane path deferred to P4. The **operative half has landed**: `server.py:165-183` builds the manual directive `executable=False, requires_human=True` and routes it through `engine.actuator.dispatch(d)` — **there is no `Actuator.execute` call on that path** (falsified by `4b0f127`, in-tree). The manual path is inert, so the residual is **provenance** (an unsigned control-plane directive), not actuation. Recorded, not silently kept. **Verdict unchanged.**

### Release-record findings surfaced while verifying §4/§5

- **R1 — a cited SHA is not in the tree.** `7aa56f6` ("fix(p3): derive cost_units_used … via one `COST_UNITS_PER_ACTION` authority (D-34) (#63)") is **not an ancestor of `49d91d4`** (`git merge-base --is-ancestor 7aa56f6 HEAD` → exit 1) and does not appear in `git log -80`. It is an orphan commit sharing the author-date (`06:42:03`) and stat (+42 lines, `packages/supervisor/tests/test_supervisor.py`) of `02bdb3f`.
- **R2 — commit message vs content mismatch.** `02bdb3f` *is* in the frozen tree and is labelled "pin supervisor COST_UNITS as an alias of the single core cost authority (D-34, #63)", but its diff adds an **unrelated** test — `test_production_write_path_reaches_append_shadow_outside_a_transaction` (a good E-5 precondition pin). Consequence: **no test anywhere pins the alias identity** (`budget.COST_UNITS is policy.COST_UNITS_PER_ACTION`); the gate pins the table's **values** (`test_p3_acceptance_gate.py:958`), not the identity. The alias itself is real and source-verified, so §4's "one table" holds — but the promised falsifier is absent, and the release record cites a SHA that is not in the tree. **Closed in substance at `280bfa8`:** the alias-identity pin now exists (`packages/supervisor/tests/test_budget_cost_failclosed.py`). What remains is the **record**: the `02bdb3f` message/content mislabel and the orphan `7aa56f6` are annotated by the lead in `P3_RELEASE_GATE.md` §4.1 (annotation, never a rebase).
- **R3 — stale line citations in the spec** (docs-only, caused by `#62` shifting `server.py`): the Errata **E-2** row cites manual control-plane rows as `server.py:174-183` (now **`:184-193`**), and **E-6** cites the `execute`→`dispatch` site as `server.py:173` (now **`:183`**). The cited *facts* are correct; only the line numbers are stale.

## 6. Conditions

| # | Condition | Owner | Blocks architecture leg? |
|---|---|---|---|
| **C1** | **CLOSED at `280bfa8`** — `commit()` indexes `COST_UNITS[action]` and raises on an uncostable action instead of charging 0.0; `action is None` path preserved; contained by `Sentinel.on_event`, production site passes a real `DirectiveAction`; pinned by `tests/test_budget_cost_failclosed.py` | forge (done) | Discharged |
| **C2** | Refresh the two stale `server.py` line citations (E-2 `:174-183`→`:184-193`, E-6 `:173`→`:183`) — **deferred to P4** (task #72) on the lead's ruling: doc-only, changes no verdict, and every post-verdict commit drifts the frozen SHA | atlas-2 | No (doc precision) |
| **C3** | Release-record integrity: re-attribute `02bdb3f`/`7aa56f6` (R1/R2) — **closed in substance at `280bfa8`** (the alias-identity pin now exists); annotation of the mislabel/orphan commit itself is the lead's, recorded in `P3_RELEASE_GATE.md` §4.1 | lead / forge | No (record only) |

## 7. Evidence (commands actually run at `49d91d4`, then re-run at `280bfa8`)

```
git rev-parse HEAD                      -> 49d91d45168cdcba61059c23d4701021bfee26a0
git status --porcelain                  -> clean apart from that turn's scratch files
git log --oneline -60                   -> #60/#61/#62/#63/#64/#65/#66/#67/#68 present; #69 ABSENT
git log --oneline -- packages/jcc_core/audit.py
                                        -> a95eb70 only (P1) => D-23 tape untouched by P3
git merge-base --is-ancestor 7aa56f6 HEAD -> exit 1 (NOT in tree)
python -m pytest packages/jcc_core/tests/test_policy.py
                 packages/jcc_core/tests/test_decision.py
                 packages/jcc_core/tests/test_state_store.py
                 packages/jcc_core/tests/test_p3_core_integration.py
                 packages/supervisor/tests/test_supervisor.py -q
                                        -> 126 passed, EXIT=0
python -m pytest packages/jcc_core/tests/test_p3_acceptance_gate.py -q
                 -k "R6_1 or cost or threshold or resolver or D_2 or alias"
                                        -> 2 passed, 28 deselected, EXIT=0
```

Re-run at the post-C1 SHA:

```
git rev-parse HEAD                       -> 280bfa8e1d15c5b357cc966ad312ad1f1b664f2c (280bfa8)
git show --stat 280bfa8                  -> packages/supervisor/budget.py
                                            packages/supervisor/tests/test_budget_cost_failclosed.py
                                            (those two files only)
python -m pytest packages/supervisor/tests/test_budget_cost_failclosed.py
                 packages/supervisor/tests/test_supervisor.py
                 packages/jcc_core/tests/test_policy.py
                 packages/jcc_core/tests/test_decision.py
                 packages/jcc_core/tests/test_state_store.py
                 packages/jcc_core/tests/test_p3_core_integration.py -q
                                        -> 131 passed, EXIT=0
                                           (= the 126 above + the 5 new C1/C3 tests; arithmetic cross-check
                                              that nothing else shifted and no test was silently dropped)
```

**Harness caveat (environment, not code):** a first attempt at the same subset returned `ERROR at setup of <every test>` with `SystemExit: 1` — the WorkBuddy safe-delete guard (`SAFE_DELETE_BULK_CONFIRM_REQUIRED`, count 208 > 50) firing while the session-scoped basetemp (`AppData\Local\Temp\jev-pytest`) was reaped. Re-running with a **fresh `--basetemp`** gave the clean `126 passed`. The errors were an artefact of a pre-used basetemp, not of the frozen code.

## 8. Scope limits

This signature covers **architecture conformance to D-1…D-41 only**. It is **not** the security gate (aegis-2), **not** the acceptance-gate run (harbor), and it does not assert the metric/evaluation design. No condition above was a live violation of a frozen decision. With **C1 and C3 closed at `280bfa8`** and **C2 deferred to P4 as a doc-only task (#72)**, this leg is an unconditional **`GO`** at `280bfa8`.
