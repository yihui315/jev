# P3 (Jev Shadow) — INDEPENDENT SECURITY GATE REVIEW (Aegis-2)

- **Reviewer:** Aegis-2 (independent security gate)
- **Reviewed:** commit `b329498` (HEAD, master) — tree dirty with an untracked gate harness; forge landing D-28/D-30/D-37 concurrently
- **Date:** 2026-09-25
- **Scope:** `packages/jcc_core/*`, `packages/supervisor/*`, `config/jcc.yaml`, `artifacts/acceptance/p3_gate_run.py`, `packages/jcc_core/tests/test_p3_acceptance_gate.py`
- **Constraint honoured:** read-only w.r.t. production source. No production file was modified.

---

## INPUT

Team-lead dispatch (task #46): independent SECURITY GATE review of P3, a Phase-F blocking gate, over 9 angles: (1) non-actuation invariant D-27/D-40/D-41 incl. the current `server.py:173` `execute` call and the D-28 fix shape; (2) control-plane auth; (3) secrets hygiene for `JCC_DIRECTIVE_SIGNING_KEY` / `JCC_CONTROL_TOKEN` (D-6); (4) localhost binding; (5) replay/TTL cache (D-33 order); (6) SQL parameter binding; (7) filesystem; (8) DoS / unwired engine budget; (9) gate honesty of harbor's harness and runner.
Deliverable: this file, findings table with `file:line` evidence, a GO/NO-GO line, and the flip list. Every claim carries evidence; anything not proven is marked UNVERIFIED.

## EVIDENCE

All evidence files under `artifacts/acceptance/_aegis2_scratch/` (gitignored).

**(1) Non-actuation (D-27/D-40/D-41).**
- Ingest is inert by construction: `core.py:178` `result = self.actuator.dispatch(directive)`; `core.py:179-181` sets `acted/actuation_reason/executable` from the port. `grep "self.actuator.execute("` in `core.py` → **no hit**; `grep "self.actuator.dispatch("` → present.
- `Actuator.dispatch` (`actuator.py:61-76`) is unconditionally inert: returns `acted=False, executable=False, reason="shadow_mode"` **regardless of `enabled`** — this is what makes the D-28 target seam permanently inert rather than config-dependent.
- My runtime tripwire: `Actuator.execute` wrapped with a counter; 6 HTTP ingests (`/api/assess`) + 3 direct `engine.ingest()` → `execute_calls=0` (`aegis2_p3_sec_out.txt`).
- **CURRENT residual — `server.py:173`**: `res = engine.actuator.execute(d)` inside `_manual()`, reached by the token-gated `POST /api/pause` `/api/steer` `/api/retry`. Measured: execute **is** called (delta=3) but every result is `acted=false, reason=actuation_disabled` (`server.py` probe lines 36-38 of `aegis2_p3_sec_stdout.txt`). With `actuation.enabled=true` **and** the action allowlisted it still returns `real_actuation_not_implemented` (`actuator.py:58`) — i.e. no side effect is reachable under any config. `Directive.executable` defaults `False` (`schemas.py:155`), `requires_human` defaults `False` (`schemas.py:156`).
- **D-28 fix shape assessment: SOUND.** Replacing `execute` with `dispatch` makes the manual route's inertness invariant under `enabled`/`allowed_actions` instead of config-dependent, and yields the declared `shadow_mode` reason rather than the legacy `actuation_disabled` (which A2/D-40 assert must appear on **neither** persisted surface — `test_p3_acceptance_gate.py:206-208`). Adding `executable=False, requires_human=True` closes the semantic gap that `requires_human` currently defaults to `False`.

**(2) Control-plane auth.**
- `require_control_token` (`server.py:72-91`): `hmac.compare_digest(presented, expected)`; unset token → **401** (`:78-82`); missing/wrong → **403** (`:86-90`); accepts `X-JCC-Token` or `Authorization: Bearer` (`:83-85`).
- Measured (`aegis2_p3_sec_out.txt`): no token → 403; wrong token → 403; bearer → 200; token unset → **401 fail-closed**, and `/api/pause` → 401. Mutating routes are exactly `[/api/assess, /api/pause, /api/retry, /api/steer]`; **no** anonymous caller reaches any of them (`no_anon_mutating_route :: []`).
- WS gate (`server.py:201-215`): token taken from `subprotocols[0]`, `compare_digest`, close **4401** on failure. Measured: with token → snapshot delivered; without → `WebSocketDisconnect code=4401`.
- Read-only GETs are **open by design** (`server.py:96-129`): `/api/health`, `/api/sessions`, `/api/sessions/{sid}`, `/api/audit`, `/api/decisions`, `/api/audit/verify` all returned 200 unauthenticated.

**(3) Secrets hygiene (D-6).**
- Key source: `signing.py:89-102 load_signing_key` reads the env var only; blank → `None` (honest "no key"). Wired at the composition root `run.py:171-179`. `config/jcc.yaml:71-73` stores only `key_env: JCC_DIRECTIVE_SIGNING_KEY` — **the key is not in config (D-6 satisfied)**.
- Sinks grepped: `JCC_DIRECTIVE_SIGNING_KEY` / `JCC_CONTROL_TOKEN` appear only in env reads (`config.py:73,83,116`, `server.py:77,203`, `run.py:178`, `signing.py:56`) and tests. **No log, no DB column, no serialization of either secret.**
- Measured end-to-end with a planted key/token: after 10 ingests + 3 manual routes, `KEY` and `TOKEN` are **absent** from the raw `jcc.db` + `-wal` + `-shm` bytes, from the audit tape, from `directives` (incl. `params`), and from `shadow_decisions` (8/8 checks PASS). Non-vacuous: `signed_rows=11`, so signing was live while the leak scan ran. A planted value-embedded secret (`sk-aegis2P3-0001deadbeefcafe`) was scrubbed at rest.

**(4) Binding.** `run.py:43 BIND_HOST = "127.0.0.1"`, `run.py:237 uvicorn.run(app, host=BIND_HOST, ...)`; `run.py:206-207` warns and overrides any config host. Grep over `packages/**.py` for `0.0.0.0` → only comments and a test helper that deliberately binds it as a probe (`tests/test_acceptance_harness.py:429`). CORS is an explicit allowlist (`server.py:61-67`, `jcc.yaml:7-11`); no wildcard.

**(5) Replay/TTL (D-33).** `nonce_cache.py`: `DEFAULT_REPLAY_TTL_S = 600.0` (`:29`), `DEFAULT_REPLAY_MAXSIZE = 20000` (`:32`), `_prune` drops expired from the front and enforces an exact size cap (`:58-77`), `check_and_add` under a `threading.Lock` (`:80-88`). **Bounded — no unbounded-growth memory DoS.** Verification order is correct: `signing.py:202-205` check 2 (signature) → `:207-213` check 3 (TTL/skew) → `:215-218` check 4 (replay, "AFTER signature"), matching `INTEGRITY_CHECK_ORDER` (`:78-83`).

**(6) SQL.** Every `conn.execute` in `state_store.py` and `supervisor/shadow_store.py` uses `?` placeholders. Grep for `execute(f"..."|.format(|%s` → **no matches**. The two f-string SQL sites interpolate only module constants: `shadow_store.py:131` (`_INSERT_COLUMNS`) and `:144` (`_HASH_COLUMNS`); values are bound (`shadow_store.py:128-132`). The `BEGIN IMMEDIATE` write path (`shadow_store.py:118-120`) is guarded by `conn.in_transaction`. `audit.py:76-93` is parameterized.

**(7) Filesystem.** DB path `jcc.yaml:17 F:/jev/runtime/jcc/jcc.db`, overridable by `JCC_DB_PATH` (`config.py:100-104`); `StateStore.__init__` does `os.makedirs(parent, exist_ok=True)` (`state_store.py:103-105`). Marker path is `dirname(db)/JCC-marker` with a literal name (`run.py:45-49`) → no traversal. No world-writable target observed; spool dir is isolated by `conftest.py:65`. No temp-file/symlink hazard identified in the P3 surface.

**(8) DoS.** See F-3/F-4 below. The replay cache is bounded (angle 5); the growth risk is the append-only tables.

**(9) Gate honesty.**
- **The harness CAN fail.** Real negative controls exist: `test_A8_negative_control_enabled_without_allowlist` (forces `enabled=True`; asserts refusal + production artifacts byte-unchanged, `:297-309`), `test_D40e_negative_control_swapped_sentinel_port_can_act_but_engine_cannot` (swaps in an actuating port and asserts the tape then shows `acted==1`, proving the invariant is not vacuous, `:492-525`), `test_A10` (deliberately creates a control-plane audit row so a naive count fails, `:349-360`), and `test_D40_shadow_non_actuation_is_non_vacuous` (`persistence_attempts > 0` + `durable_writes == attempts` + row count, `:476-489`). Skips are treated as failures by the runner (`p3_gate_run.py:145` requires `skipped == 0`), and `KNOWN_PENDING` (`:67-74`) does **not** whitewash — those tests still count as failures.
- **BUT the runner passes vacuously.** See F-2.
- **UNVERIFIED — I could not execute the suite.** Repeated attempts aborted because the host sandbox's bulk-delete guard (`F:\WorkBuddy\...\sitecustomize.py:826/851`, `_check_bulk_delete_guard`) raises `SystemExit(1)` when pytest cleans an accumulated basetemp: `[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED] {"count":172,"threshold":50,"scope":"turn",...}` → `ERROR at setup` for all 29 tests (`_aegis2_p3gate_pytest3.txt:30-44`). One representative test with a **fresh** basetemp did run clean: `test_D40e_...` → **1 passed** (`_aegis2_p3gate_pytest5.txt:8`). I therefore mark the full-suite outcome **UNVERIFIED** rather than asserting PASS or FAIL.

## CHANGES

- **0 production files modified** (docs-only mandate honoured).
- Added (evidence, gitignored `artifacts/`): `_aegis2_scratch/aegis2_p3_sec.py`, `aegis2_p3_sec_out.txt`, `aegis2_p3_sec_stdout.txt`, `aegis2_gate_vacuity.py`, `_aegis2_gate_vacuity_out.txt`, `_aegis2_scratch/p3_gate_result.json`, `aegis2_gate_real.py`, `aegis2_p3_gate_driver.py`, `_aegis2_p3gate_pytest*.txt`, `_aegis2_p3gate_batches*`.
- Added (deliverable): `docs/SECURITY-P3-REVIEW.md`.

## TESTS

| Probe | Result |
|---|---|
| `aegis2_p3_sec.py` (own: non-actuation, auth, WS, secret sinks, budget) | **PASS=30 FAIL=1 TOTAL=31** (exit 0); the single FAIL is the expected `cost_units_used=0.0` derivation gap |
| `Actuator.execute` tripwire over ingest (HTTP + in-process) | PASS — `execute_calls=0` |
| Manual-route `execute` reachability + inertness | PASS — called 3×, all `acted=false / actuation_disabled` |
| Auth matrix (no token / wrong / bearer / unset) | PASS — 403 / 403 / 200 / 401 |
| WS with and without token | PASS — snapshot / 4401 |
| KEY+TOKEN absent from db bytes, audit, directives, shadow | PASS — 8/8 |
| Vacuity: runner vs a target that collects 0 tests | **FAIL** — `GATE = PASS`, exit 0 |
| `test_p3_acceptance_gate.py::test_D40e_...` (fresh basetemp) | PASS — `1 passed` |
| Full P3 suite / full gate module | **NOT EXECUTED** — sandbox bulk-delete guard (see EVIDENCE 9). Marked UNVERIFIED |

## RISKS

| ID | Severity | Evidence (`file:line`) | Recommendation | Owner |
|---|---|---|---|---|
| **F-1** | ~~LOW (residual)~~ **CLOSED** (@`92e069f`) | was `server.py:173` `_manual` → `engine.actuator.execute(d)`. **Now** `server.py:183` `engine.actuator.dispatch(d)` + `executable=False, requires_human=True` (`:179-180`); zero production `Actuator.execute(` calls remain. Delta re-verified: tripwire `delta=0` on all manual routes, all return `{acted:false, executable:false, reason:"shadow_mode"}` (see ADDENDUM §B1) | — (done; pinned by `4b0f127`) | forge |
| **F-2** | ~~HIGH (gate integrity)~~ **CLOSED** (independently verified, §A/§B3/§C4) | was `p3_gate_run.py:145` `ok = (failed == 0 and skipped == 0 and not a_bad and not d_bad)` — **no guard that any test ran**; reproduced `GATE = PASS` at `{'PASSED':0,…}`. **Now** vacuity guard (`:195-238`, `KNOWN_PENDING` empty `:83`); real runs at `cfbba8e`/`49d91d4` = 183/0/0/0 and `--full` 349/0/0/0, GATE=PASS | — (done) | harbor |
| **F-2a** | **HIGH (gate integrity — 2nd hole, found by lead)** | `p3_gate_run.py` old `_STATUS_RE = \S+::\S+` silently **dropped 10 space-containing node ids** (under-count; a space-id `FAILED` would also be dropped → `failed==0` could hold while a test failed). **FIXED** at `49d91d4`: regex anchors the `[ NN%]` marker (`:90-93`) **and** the parse is reconciled against pytest's tally, mismatch fatal (`:224-249`, `:282`). Adversarially verified: `aegis2_f2b_parse.py` **11/11** (old regex misses space-id `FAILED`; new captures it; reconciliation fatal on under-count/hidden-FAILED/missing-tally); `--full` reconciled 349==349 | — (done) | lead / harbor |
| **F-3** | MEDIUM (residual P3 DoS) | `audit_tape`/`shadow_decisions`/`events`/`assessments` grow unbounded; the engine-side budget is **not wired** — `ports.py:45-65` defines `AssessmentBudgetGuard`/`AllowAllGuard` but `core.py:71-229` never calls a guard; `jcc.yaml:43` `p3.shadow.enabled: false` so the Sentinel budget is also off by default | Wire the guard into `CoreEngine.ingest` (or add table retention/caps). A token holder can otherwise grow the DB without bound. Localhost + token-gated bounds the blast radius | team-lead → forge |
| **F-4** | ~~LOW (known)~~ **CLOSED** (@`02bdb3f`) | was `state_store.py:545-546` `model_units_used: 0.0, cost_units_used: 0.0`. **Now** `cost_units_used` is DERIVED from persisted `directives.action` via `COST_UNITS_PER_ACTION` (`state_store.py:552-566`), fail-closed on uncostable action; `model_units_used` intentionally `0.0` (inert-by-design, waived, documented `:515-518`). Delta re-verified: `cost_units_used=40.0` (see ADDENDUM §B2) | — (done) | forge |
| **F-5** | LOW | `audit.py:58-93` appends `audit_tape` as a non-atomic SELECT-then-INSERT under only the in-process `threading.Lock` (`state_store.py:436-442`), while `shadow_store.py:118-120` correctly adds `BEGIN IMMEDIATE`. `run.py:221` warns on a second live instance but **proceeds** | Add `BEGIN IMMEDIATE` to `append_audit`, or make second-instance detection fail-closed | team-lead |
| **F-6** | LOW (by design) | `server.py:96-129` read-only GETs need no token; measured all 200 unauthenticated, incl. `/api/audit` and `/api/decisions` | Acceptable on a loopback-only bind; document the decision or token-gate the audit tape before any non-loopback deployment | team-lead |
| **F-7** | INFO | `server.py:208-212` accepts the socket before closing 4401 on auth failure | Prefer rejecting before `accept()`; cosmetic | forge |

**UNVERIFIED (do not promote to VERIFIED):** *none remaining on the P3 gate surface.* The `--full` `jcc_core/tests`+`supervisor/tests` tree is now **executed green in my environment** (349/0/0/0, ADDENDUM §C4) — closing the earlier host-sandbox UNVERIFIED. ~~full P3 suite outcome; `test_budget_*`; post-D-28 `server.py:173`; `--full` tree~~ → all **resolved** (§B, §C).

## DECISION

**SECURITY GATE = GO**  *(re-affirmed at frozen SHA `49d91d4` — see ADDENDUM §C; full P3 tree independently green 349/349)*

Rationale: **product security blockers = 0.** The non-actuation invariant holds (execute unreachable from every ingest path; the one reachable `execute` seam is inert under every configuration), auth is fail-closed with constant-time comparison, neither the signing key nor the control token reaches any persisted or logged sink, the bind is localhost-only, the replay cache is bounded and correctly ordered, and all SQL is parameterized. The residual items are all LOW/MEDIUM and either known-waived or local-only; **F-1 must still land (D-28) before closeout.**

This signature covers the **security** question only. I do **not** sign the acceptance/SRE gate, and I explicitly flag that **F-2 makes harbor's gate runner unable to distinguish "0 tests ran" from "all tests passed"** — so no P3 acceptance decision should rest on that runner's `GATE = PASS` until F-2 is closed.

**Items that would flip this verdict to NO-GO:**
1. ~~**F-2 unfixed** *and* the P3 closeout relies on `p3_gate_run.py` output as its acceptance evidence~~ → **SATISFIED: F-2 closed and independently verified** (ADDENDUM §A/§B3); the runner is no longer vacuous.
2. Any evidence that `Actuator.execute` becomes reachable **from the ingest path** (currently `execute_calls=0`), or that `Actuator.dispatch` can return `acted=True`.
3. Any sink where `JCC_DIRECTIVE_SIGNING_KEY` or `JCC_CONTROL_TOKEN` is logged, persisted, or serialized into `params`/`raw`/`shadow_decisions` (currently clean, 8/8 — re-confirmed at `cfbba8e`).
4. Any `0.0.0.0` bind or wildcard CORS in product code (currently none).
5. Any attacker-influenced data interpolated into SQL (currently none).
6. ~~F-1 (D-28) landing in a way that makes `executable`/`reason` config-dependent again~~ → **SATISFIED: F-1 landed constant-inert** (`reason="shadow_mode"`, `executable=false` under every config; ADDENDUM §B1).

## HANDOFF

- **Next owners (updated at `cfbba8e`):** ~~harbor (F-2)~~ **CLOSED**; ~~forge (F-1/D-28, F-4)~~ **CLOSED** (ADDENDUM §B); **forge/team-lead — F-3** (wire `AssessmentBudgetGuard` into `CoreEngine.ingest`, or add table retention); **team-lead — F-5** (add `BEGIN IMMEDIATE` to `append_audit`) and **F-6** policy (read-only GET exposure). F-7 cosmetic.
- **Artifacts to forward:** `docs/SECURITY-P3-REVIEW.md`; `_aegis2_scratch/aegis2_p3_sec.py` + `aegis2_p3_sec_out.txt` (30/31); `_aegis2_gate_vacuity.py` + `_aegis2_gate_vacuity_out.txt` + `p3_gate_result.json` (F-2 reproduction); `_aegis2_p3gate_pytest3.txt` (sandbox guard causing UNVERIFIED); `_aegis2_p3gate_pytest5.txt` (D-40e passed).
- **Facts to forward verbatim:** (1) `Actuator.execute` is unreachable from ingest (measured 0 calls) but **is** called by `_manual` and is inert under every configuration; (2) the P3 gate runner printed `GATE = PASS` with exit 0 while executing **zero** tests; (3) neither secret appears in DB bytes, audit, directives, or shadow rows, with signing proven live (11 signed rows); (4) the engine-side budget is not wired, so tape growth is unbounded for a token holder.
- **Do NOT misunderstand:** the GO is a **product-security** verdict — it is not a signature for the acceptance/SRE gate, and it does not certify that the P3 suite passed (that is UNVERIFIED here because the host sandbox aborted every full-module run). Also: the presence of `KNOWN_PENDING` in the runner is **not** whitewashing — those tests still count as failures; the defect is the missing zero-tests guard, which is a different failure mode.

---

## ADDENDUM §A — F-2 CLOSURE: INDEPENDENTLY VERIFIED (Aegis-2), 2026-09-25

Harbor reported F-2 fixed. Per gate discipline I re-verified it myself rather than accepting the report. Copies of the **current** runner were patched only in `REPO`, `ARTIFACTS` and the target list; output written to scratch so harbor's artifact was not clobbered.

**(A1) The fix, read from the current source.** `p3_gate_run.py:195-238` adds a `vacuity` list that is fatal: empty parse set (`:197-199`), **every declared target must contribute ≥1 row** (`:204-207`), the A1–A11 matrix must be *complete* (`:211-215`), the load-bearing D-40 falsifier must be present (`:218-220`), pytest rc ∉ {0,1} (`:227-229`), and rc≠0 with no parsed failure (`:230-232`). `ok = (... and counts["PASSED"] > 0 and not vacuity)` (`:237-238`). `KNOWN_PENDING` is now **empty** (`:83`) — nothing is absorbed.

**(A2) Zero-rows case → FAIL (was PASS).** `_aegis2_f2_caseA.txt`: bogus nodeid → `totals {PASSED:0,…}` → six `VACUITY GUARD` reasons → **`GATE = FAIL`, `EXIT_A=1`**. The exact input that printed PASS/exit 0 before now fails closed.

**(A3) Partial-execution case → FAIL (the load-bearing one).** `_aegis2_f2_caseB.txt`: good gate module **+ a real-but-testless path** (`jcc_core/providers`) → `totals {'PASSED': 30, 'FAILED': 0, 'ERROR': 0, 'SKIPPED': 0}` — **no failures and PASSED>0, which the pre-fix runner printed as PASS/exit 0** — but the per-target guard fired (`declared target contributed NO results: jcc_core/providers`) → **`GATE = FAIL`, `EXIT_B=1`**. This is precisely my reported scenario; the `PASSED > 0` clause alone cannot see it, which is why the per-target guard is the substantive fix.

**(A4) Real 6-target gate → PASS (green, non-vacuous).** Running the unmodified `P3_SUITES` (`_aegis2_f2_realfull.txt`, HEAD `16a3e8f`): `totals {'PASSED': 183, 'FAILED': 0, 'ERROR': 0, 'SKIPPED': 0}`, `A-rows not PASSED: none`, `D-40 rows not PASSED: none`, `failing tests: none`, **no `VACUITY GUARD` lines at all**, **`GATE = PASS`, exit 0**. So the anti-vacuity guard is silent on a genuine full run and the PASS is earned.

**(A5) Consequence — evidence upgrade.** The P3 acceptance-gate module is now **independently executed green** in my environment (30/30; `p3_gate_result.json` from case B lists every test, incl. A1–A11, D-40 a–e + the non-vacuity falsifier, D-26, spec §2.6, and all four budget tests). This **supersedes** the earlier "full-suite UNVERIFIED" for the declared P3 gate surface only — the broader `jcc_core/tests` tree (`--full`) was still not run and stays UNVERIFIED. The new basetemp strategy (`p3_gate_run.py:111-138`, fresh unique child under `tempfile.gettempdir()` + TEMP/TMP pinned to the base) is what unblocked the host sandbox bulk-delete guard.

**(A6) Likely F-4 resolution.** `test_budget_store_derived_dimensions_are_live` and `test_budget_exhaustion_matrix` now **PASS** in the gate module (`p3_gate_result.json`), i.e. `cost_units_used` appears to be derived rather than inert — consistent with forge's in-flight `config.py` work. **Not yet re-confirmed by me directly** (no re-run of my `aegis2_p3_sec.py` against the new SHA); to be nailed down in the delta pass.

**(A7) Provenance correction (for the record, not a dispute).** Harbor noted `counts["PASSED"] > 0` was already in `ok` before my CR. My archived copy of the revision I *executed* shows otherwise: `aegis2_gate_vacuity.py:138` is `ok = (failed == 0 and skipped == 0 and not a_bad and not d_bad)` — **no `PASSED > 0` clause**, and `KNOWN_PENDING` was non-empty there (`:60`). My `GATE = PASS` at `PASSED:0` is therefore a faithful reproduction at that revision. This does not diminish the fix (which adds more than was asked) and needs no action; recorded only so the chain is not rewritten.

**Revised F-2 status: CLOSED (independently verified, `a6796de`/`16a3e8f`).** My product-security verdict is unchanged: **SECURITY GATE = GO**; flip item (1) in the DECISION section is now satisfied. Still open from this review: F-1 already-closed-per-forge (to re-confirm), F-3, F-5, F-6, and the `--full` tree (UNVERIFIED).

---

## ADDENDUM §B — DELTA RE-CHECK AT `cfbba8e` (Aegis-2), 2026-09-25

The review body above was written at `b329498`. Forge has since landed D-28/D-30/D-37 and the #61–#67 items, moving HEAD to `cfbba8e`. Because the findings were kept **structured per-file**, the delta pass is mechanical: re-read the cited lines, re-run both probes. Result: **F-1 and F-4 are CLOSED; F-3 and F-5 remain OPEN as originally rated; no new defect.**

**(B1) F-1 → CLOSED (was LOW-residual).** `server.py:163-194` `_manual` now calls **`engine.actuator.dispatch(d)`** at `:183` and constructs the directive with `executable=False, requires_human=True` (`:179-180`); `core.py:191` (ingest step 6) uses `dispatch`. A whole-repo search for a production `Actuator.execute(...)` call now returns **none** (only sqlite `conn.execute(...)` remain). Landed at `92e069f`, pinned by `4b0f127` (`test_...` asserts `"self.actuator.execute(" not in core_src`). **Direct re-run of my own probe, re-baselined for the new tree** (`aegis2_p3_sec_delta.py`, `_a2_delta_console.txt`): poison-pill tripwire on `Actuator.execute` stays at **`delta=0`** across all ingest *and* the three manual routes; `/api/{pause,steer,retry}` all return `{acted:false, executable:false, reason:"shadow_mode"}`. The old `actuation_disabled` string no longer appears on any manual surface — exactly the re-verification F-1's recommendation demanded.

**(B2) F-4 → CLOSED (was LOW-known).** `state_store.budget_snapshot` (`:487-579`) now **derives** `cost_units_used` from the persisted `directives.action` column via `jcc_core.policy.COST_UNITS_PER_ACTION` (`:552-566`), and **raises** on an uncostable action string (fail-closed, mirroring D-34's `budget_check_error`) rather than silently under-counting. `model_units_used` stays `0.0` and is explicitly documented as **not-derivable / inert-by-design (waived)** (`:515-518`), i.e. honestly reported, not faked. Delta probe: `DELTA_budget_cost_units_derived :: cost_units_used=40.0` (was `0.0`). Pinned by `02bdb3f`.

**(B3) F-2 → CLOSED (re-confirmed at the new SHA).** Re-running the unmodified gate runner at `cfbba8e` (`_a2_p3gate_cfbba8e.txt`): `totals {'PASSED': 183, 'FAILED': 0, 'ERROR': 0, 'SKIPPED': 0, 'XFAIL': 0, 'XPASS': 0}`, **A1–A11 all PASSED**, **D-40a–e all PASSED** (incl. the swapped-port negative control `test_D40e`), `A-rows not PASSED: none`, `D-40 rows not PASSED: none`, **no `VACUITY GUARD` lines**, **`GATE = PASS`, exit 0**. Note: this run (the real, unmodified runner) regenerated `artifacts/acceptance/p3_gate_result.json`; that is the gate's own output file, and the scratch delta copies remain the provenance for the F-2 negative cases.

**(B4) Whole-probe delta → 32/32, exit 0.** `aegis2_p3_sec_delta.py` (`_a2_delta_console.txt`, `PASS=32 FAIL=0 TOTAL=32`): execute unreachable from ingest (0 calls) and from manual routes (0); auth fail-closed (`no-token 403`, `wrong 403`, `bearer 200`, `env-unset 401`, WS `4401`); exactly four mutating routes, none anonymous-reachable; **secrets absent from all four sinks 8/8** (db bytes / audit tape / directives / shadow_decisions), planted value-secret still scrubbed at rest, signing proven live (11 signed rows); 127.0.0.1-only bind; all read-only GETs open by design.

**(B5) F-3 → STILL OPEN (MEDIUM, unchanged).** `CoreEngine.ingest` **still never consults** `AssessmentBudgetGuard`: a whole-file search finds only the word "ActuationPort" in a comment (`core.py:187`) and no guard call; `run.py:237-242` builds the engine as `CoreEngine(store, provider, actuator, config)` with **no guard argument**, and `_wire_shadow` wires `BudgetGovernor` (`run.py:187-197`) **only into the Sentinel** — the engine path is untouched. With `jcc.yaml:43` `p3.shadow.enabled: false` by default, neither plane bounds the token-holder's ability to grow `events/assessments/directives/audit_tape`. Non-blocking (localhost + token-gated), but the residual P3 DoS stands exactly as rated. Owner team-lead → forge.

**(B6) F-5 → STILL OPEN (LOW, unchanged).** `audit.py:58-94` is still a SELECT-then-INSERT followed by `conn.commit()`, with **no `BEGIN IMMEDIATE`**; `state_store.append_audit` (`:437-443`) still wraps it in only the in-process `threading.Lock` + a dedicated connection. The cross-process chain-fork race the fix recommended is unaddressed (contrast `shadow_store.py:118-120`, which does hold `BEGIN IMMEDIATE`). Owner team-lead.

**(B7) F-6 / F-7 unchanged.** Read-only GETs (incl. `/api/audit`, `/api/decisions`) remain open by design (B4 measured all 200 unauthenticated); WS still `accept()`s before closing `4401` (`server.py:218-222`). Both were rated LOW/INFO and neither flips the gate.

**(B8) New items strengthened (no security defect introduced).** D-30 config fail-closed now lives in the entrypoint (`run.py:219-230`, `9542933`); D-37 policy version bumped (`9ee4507`); the R6-1 threshold resolver injects the *same* `ThresholdsConfig` object into both planes (`run.py:183`, `core.py:172`), closing the "engine ran a different policy than the shadow recorded" labelling gap I noted in EVIDENCE.

**Delta verdict: SECURITY GATE = GO (unchanged).** Product security blockers remain **0**; the F-1 and F-4 residuals I raised are now **independently verified CLOSED** at `cfbba8e`. Remaining open items are the same residuals as the original review — **F-3 (MEDIUM, DoS/engine budget unwired)**, **F-5 (LOW, audit no BEGIN IMMEDIATE)** — plus policy items F-6/F-7 and the still-UNVERIFIED `--full` tree. None is a product-security blocker at this scope (localhost-only, token-gated, observe-only). This remains a **security-only** signature; the acceptance/SRE gate is not mine to sign.

---

## ADDENDUM §C — DELTA RE-CHECK AT FROZEN SHA `49d91d4` (Aegis-2), 2026-09-25

Lead pinned the frozen SHA. Re-stated my findings against it. **Note on head drift (not a code drift):** the tree read `49d91d4` when I started; a docs-only commit `1082fff` (`git diff --name-only 49d91d4 1082fff` = `docs/p3/P3_RELEASE_GATE.md` only, +147 lines) landed mid-pass, so some gate runs printed `HEAD=1082fff`. **The executing code is identical to the frozen SHA** — every result below is valid for `49d91d4`.

**(C1) F-1 → CLOSED (re-confirmed at `49d91d4`).** Production `Actuator.execute` callers in `packages/` = **0**. The only textual hits are two *comments* (`server.py:175`, `core.py:189`) plus test spies (`test_manual_actuation.py`, `test_p3_acceptance_gate.py`, `test_p3_core_integration.py`) — no production call site. `server.py:183` is `engine.actuator.dispatch(d)` with `executable=False, requires_human=True, params={"source":"control-plane","requires_human":True}`. Probe (`_a2_delta_49d91d4.txt`): `DELTA_manual_routes_execute_NOT_called :: delta=0`; all three manual routes `{acted:false, executable:false, reason:"shadow_mode"}`.

**(C2) F-4 → CLOSED (re-confirmed at `49d91d4`).** Same probe: `DELTA_budget_cost_units_derived :: cost_units_used=40.0` (was `0.0`); `model_units_used` stays `0.0`, documented inert-by-design. Whole delta probe **32/32, exit 0**.

**(C3) F-2a — SECOND runner hole (parse drop), independently adjudicated at `49d91d4`.** Lead reported and fixed a distinct hole in the same runner (`49d91d4`): the old per-test regex `\S+::\S+` silently **dropped node ids containing spaces** (10 of them, e.g. `test_secret_values_are_masked[curl -u alice:s3cr3tpw …]`), under-counting **and** able to hide a `FAILED` whose id had a space (a failing line would not have matched either). The fix (`p3_gate_run.py:90-93`) anchors the line **end** on pytest's `[ NN%]` progress marker — `^(?P<node>\S.*?)\s+(?P<status>…)\s+\[\s*\d+%\]\s*$` — **and** reconciles the per-test parse against pytest's own tally (`_pytest_summary_counts` `:110-136`; mismatch appended to `vacuity` `:224-249`+`:282` ⇒ fatal).
  - **Adversarial test** (`aegis2_f2b_parse.py`, `_a2_f2b_console.txt`, **11/11, exit 0**): proves the old `\S+::\S+` **misses** a space-containing `PASSED` *and* a space-containing **`FAILED`** (the hole was real), that the new regex **captures the space-containing `FAILED` as `FAILED`**, that the runner's own `[gate] … PASSED` prose lines do **not** match (no self-pollution), that a node id embedding `[ 100%]` still binds the status to the **final** token, and that the reconciliation is **fatal** on an under-count, on a hidden `FAILED`, and on a missing tally line.
  - **End-to-end, no false positive:** the `--full` run (C4) reconciled parse `349` vs pytest tally `349` with **no** `PARSE INCOMPLETE`/`VACUITY GUARD` line.
  - **F-2a verdict: CLOSED (independently verified).** This is a genuine fail-open-parse class fix, materially stronger than the F-2 vacuity fix.

**(C4) Evidence upgrade — the last UNVERIFIED is now RESOLVED.** At frozen-SHA code: default P3 surface (`_a2_p3gate_default_49d91d4.txt`) = `{PASSED:183, FAILED:0, ERROR:0, SKIPPED:0}`, A1–A11 + D-40a–e all PASSED, `GATE = PASS`, exit 0. **`--full`** (`_a2_p3gate_full_49d91d4.txt`) = `{PASSED:349, FAILED:0, ERROR:0, SKIPPED:0}`, A1–A11 + D-40a–e all PASSED, **no vacuity/parse warnings**, `GATE = PASS`, exit 0. This **supersedes** the earlier "`--full` UNVERIFIED" — the whole `jcc_core/tests` + `supervisor/tests` tree is now independently green in my environment (the host sandbox bulk-delete abort is gone via #60's basetemp derivation). My `349` matches lead's `349` independently.

**(C5) F-3 → waiver assessment (I concur, with a condition).** The lead has ruled F-3 a **P3 waiver, tracked to P4**, recorded at `P3_ARCHITECTURE_SPEC.md:500` (D-5 is permissive — *"may consult"*; the Sentinel-side budget **is** wired, so shadow-plane non-vacuity holds; the `AssessmentBudgetGuard` Protocol at `ports.py:46-55` is declared but **not exercised** in P3 — explicitly *"a waiver, not an implementation"*). **My DoS severity assessment does not change: it remains MEDIUM, non-blocking** (localhost-only bind + token-gated mutators bound the blast radius; observe-only plane). I do **not** object to the waiver — it is the lead's call and it is honestly recorded. **Condition:** it must stay a **visible accepted-risk line** in the release gate and as a **P4 precondition**, not be read as "fixed". I would not treat the engine-budget surface as verified.

**(C6) F-5 / F-6 → I concur with the dispositions.** F-5 (audit `append` lacks `BEGIN IMMEDIATE`; `audit.py:58-94`, `state_store.py:437-443`) deferred to P4 to avoid churning the frozen audit path — **agree, provided it stays recorded open (LOW)**; it is a cross-process chain-fork race, not reachable single-instance. F-6 (open read-only GETs incl. `/api/audit`,`/api/decisions`) accepted for P3 because the bind is `127.0.0.1` only — **agree**, and I concur it is a **P4 precondition** for any non-loopback deployment. **I do not disagree with either.**

**VERDICT — SECURITY GATE = GO at `49d91d4` (unchanged).** Product security blockers = **0**. Independently verified closed: **F-1** (no production `Actuator.execute` caller; manual path inert), **F-2** (vacuity guard), **F-2a** (parse-drop + reconciliation), **F-4** (cost derived). **Accepted-risk / deferred (open, non-blocking): F-3 (MEDIUM→P4 waiver), F-5 (LOW→P4), F-6 (LOW, loopback-only →P4 precondition).** Full P3 tree independently green (349/349). This remains a **security-only** signature; the acceptance/SRE gate is not mine to sign.

**(C7) Clean-tree frozen run — verified; head-drift 49d91d4→4cc657f is docs+test only.** Harbor produced a genuine **clean** frozen run in a pristine `git worktree` and swapped the artifact: `artifacts/acceptance/p3_gate_result.json` now records `head=4cc657f…, dirty=false, counts{PASSED:349,FAILED:0,ERROR:0,SKIPPED:0}, gate="PASS", pytest_returncode=0, vacuity_guard=[], summary_reconciliation{parsed_total:349, pytest_tally{PASSED:349}, mismatches:[], reconciled:true}`; the prior dirty run is preserved as `p3_gate_result.superseded_dirty_1082fff.json`. I read the artifact directly (not the report). `git diff --name-only 49d91d4 4cc657f` = `docs/p3/P3_ARCH_GO.md`, `docs/p3/P3_RELEASE_GATE.md`, `packages/jcc_core/tests/test_p3_acceptance_gate.py` — **no production code changed**, so every security result in §C holds at `4cc657f`. This also confirms the §C head-drift note: no earlier "`49d91d4`, `dirty=False`" run ever existed (the docs-only `1082fff` landed mid-pass); the clean artifact is the honest record.

**(C8) A8 pin portability fix (`4cc657f`) — test-only, no security impact.** Reviewed directly: `_sha` now hashes **EOL-normalised** bytes (`read_bytes().replace(b"\r\n", b"\n")`), keys in `_BASELINE_HASHES` are **repo-relative**, and a **symmetric `extra` guard** was added (baseline entry with no scope = dead weight). Net effect **strengthens** the pin; the 12 frozen digests are unchanged. EOL-only edits cannot alter Python semantics, so it cannot hide a behaviour change. `server.py` (carrying the D-28 fix) **is** inside the pin (`92ac49cf…`). Test-only; my verdict is unaffected.
