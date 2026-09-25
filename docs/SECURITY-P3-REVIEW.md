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

---

## ADDENDUM §D — P4 BATCH DELTA (`42a63c4` / `4e96c1a7`), INCLUDING A RETRACTION

Triggered by atlas-2's report that **A8 is RED at HEAD** (`run.py` pin mismatch), plus my own standing obligation to re-derive rather than inherit the §C verdict. **This addendum supersedes §C8's implication that the pin is portable to the current head.**

**(D1) RETRACTION — my earlier "12/12 A8 match" is STALE and is withdrawn.** I previously reported 8/8 and 12/12 pin matches at `3936c96`. That was true *at that commit* and is **false at the current head**. Recomputed on a **pristine `git archive` tree of HEAD `4e96c1a7`** (not the worktree, so no dirty-tree ambiguity), EOL-normalised, 12 pins:

| pinned file | pin | actual @4e96c1a7 | |
|---|---|---|---|
| `packages/jcc_core/run.py` | `d820cd84…` | `0f9acd37…` | **DRIFT** (#77/#78 F-6) |
| `packages/jcc_core/server.py` | `92ac49cf…` | `80a8f685…` | **DRIFT** (#77 F-6) |
| `packages/supervisor/sentinel.py` | `65dbfea5…` | `79b2aaec…` | **DRIFT** (#73/#74/#75 F-3) |
| `packages/supervisor/budget.py` | `96a11b69…` | `4bf29744…` | **DRIFT** (#73 follow-through, scorer_tokens) |
| other 8 | — | — | OK |

**8 matched, 4 drifted.** This **independently confirms atlas-2's finding**, including the exact `run.py` digest, and confirms it is **committed product drift, not EOL** — the mismatch is present in the pristine committed tree, so no amount of EOL normalisation explains it. I therefore **agree with atlas-2's discipline and took no re-baselining action**: the pin values were left untouched, and I do not treat this as a flake. Consistent with ADR-0010, I did **not** perform a precautionary re-baseline of the 8 matching files either. **A re-baseline belongs in the same commit that freezes the SHA, under lead authority.**

**(D2) The working tree is NOT in a reviewable state right now (in-flight edit, not a committed defect).** While probing, the tree moved twice (`42a63c4` → `9b82393` `.gitattributes`/ADR-0010 → `4e96c1a` ADR-0009 EV-11/EV-12), and `packages/jcc_core/state_store.py` carried an **uncommitted** edit (+60/−49, P4 #82 "one row per economic event") that left the worktree file **unparseable** (`IndentationError` at `state_store.py:520`). The **committed** version at HEAD parses cleanly; `git diff HEAD` shows only that file. **No P0 is being asserted against HEAD** — but any gate run against the live worktree would fail at import, and my verdict is therefore pinned to the **committed tree**, verified via `git archive`, not to the working copy. Two further hygiene facts: an earlier `budget.py` "ghost dirty" proved to be a transient EOL write (worktree now byte-identical to HEAD, `4bf29744…`, 22875 B), and the only remaining tracked dirty file is docs-only (ADR-0009/ADR-0010).

**(D3) F-1 → CLOSED at `4e96c1a7`, re-verified by a NON-VACUOUS tripwire.** The `server.py` D-28 face moved (`92ac49cf`→`80a8f685` via #77), so I re-derived it rather than inheriting §C. AST scan of every non-test module under `packages/`: **0** production `Actuator.execute` callers. `server.py:206-237` `_manual` still dispatches with `executable=False, requires_human=True`. Executed probe (`aegis2_p4_recheck.py`, `_run_pristine.txt`, **13/13 pass**) against the real composition root (`run._wire_shadow` + shipped `jcc.yaml`): the manual routes are now **`/api/pause`, `/api/steer`, `/api/retry`** (`server.py:239-249`); my first attempt hit `/shadow/manual` and returned **404**, i.e. a *vacuous* tripwire, which I rejected and fixed — with the correct control token (`X-JCC-Token`, env name taken from `config.control_token_env`) all three return **200**, the handlers genuinely ran, and `Actuator.execute` was called **0 times**. Non-vacuous. **F-1 CLOSED.**

**(D4) F-3 → STILL OPEN as scoped; `571f2a6` did not close it (commit message vs code).** `571f2a6` is titled "wire the **engine-side** cost guard (D-5/F-3)". Its changed files are `packages/supervisor/budget.py` (+147), `packages/supervisor/sentinel.py` (+104) and two supervisor tests — **`packages/jcc_core/core.py` is not touched**. What was actually wired is a **post-arbitration PRICED gate inside the supervisor's `Sentinel._on_event_inner`** (`sentinel.py:249-256`, `budget.check(..., action=action)`), which is a real improvement to the shadow plane's cost ceiling and I credit it as such. But my F-3 as written was: *the P3 engine ingest path never consults a budget*. Re-verified by execution at `4e96c1a7`: `core.py` contains **no** `BudgetGovernor` / `budget.check` / `budget_snapshot` reference, and `run.py` still builds `CoreEngine(store, provider, actuator, config)` with no guard. **The engine surface is unchanged, so F-3 remains OPEN / waived** — I am flagging the wording, not re-opening the severity: the waiver (lead's call, recorded at `P3_ARCHITECTURE_SPEC.md:500`) stands, and it must keep reading as an **accepted risk + P4 precondition**, never as "fixed". `#82b` (hostile pins, 7 tests, non-vacuous by construction) meaningfully hardens the waiver's falsifiability — emptying `WAIVED_DIMENSIONS` turns all 7 RED — but hardens a *semantic* claim; it does not wire the engine.

**(D5) F-5 → CLOSED at `4e96c1a7` (`a59fb96`).** `audit.append_audit` now issues **`BEGIN IMMEDIATE` before the tail read** (asserted by source-order probe: `begin@847 < tail@1898`), claims the single-writer row, hashes, inserts and commits inside that transaction — closing the cross-process chain-fork window §C described. The `if not conn.in_transaction` guard means the ordering also holds inside a caller's already-open transaction (SQLite rejects a nested `BEGIN`), which is the case §C's in-process `threading.Lock` could not cover. **One observation, not a blocker:** `writer_owner()` has **no production consumer** — the only caller is `test_audit_chain_atomicity.py:302/306`. The marker is therefore *forensic, not enforced*; the actual serialisation comes from `BEGIN IMMEDIATE`. That is sound today, but it is a latent trap: if `BEGIN IMMEDIATE` is ever relaxed, the single-writer guard degrades to decoration **silently**. Recommend a test that asserts the write would be rejected/locked if the guard were removed — that converts an assumption into an enforced invariant.

**(D6) F-6 → CLOSED at `4e96c1a7` (`053936c`).** Note the direction of the claim: `server.py`'s docstring says the loopback refusal lives in `run.py`, so I verified it **at the implementation**, not at the comment. Executed: `run.check_bind_host` **raises `SystemExit` on `server.host: 0.0.0.0`** and passes `127.0.0.1`; `BIND_HOST == "127.0.0.1"` is applied unconditionally at `run.py:302`, so an omitted key cannot expose the listener. Fail-closed, not a preference. (`0.0.0.0` is refused rather than silently corrected — correct choice.) **`.gitattributes` (`9b82393`, `* text=auto eol=lf`) is neutral for A8**: fresh checkouts are already LF, and A8 normalises CRLF→LF anyway.

**(D7) New observation (LOW, not a blocker) — the priced gate can drop an event.** With the raise now reachable in `check()` as well as `commit()` (`_unit_cost`, `budget.py:121-143`), an uncostable action raises **before** `sentinel._record` runs; `on_event`'s blanket `except Exception` (`sentinel.py:116-130`) then increments `persistence_failures` and returns `None`, so the assessment is **logged but not recorded on the tape**. Fail-closed on actuation (nothing is issued silently) and **gate-visible** (the D-40 pin asserts `persistence_failures == 0`, so the gate would go RED rather than pass silently) — but it is a *record-fidelity* consequence, not an actuation risk. Worth a line in P4 if the pricing table ever gains a member.

**(D8) Verdict — SECURITY GATE = GO at `4e96c1a7` (committed tree).** Product security blockers = **0**, unchanged in substance across the P4 batch. Independently re-verified **CLOSED** this round: **F-1** (non-vacuous tripwire, 3/3 manual routes 200, 0 execute calls), **F-5** (BEGIN IMMEDIATE precedes tail read, incl. caller-open-txn case), **F-6** (fail-closed loopback refusal). **F-3 remains OPEN/waived** (engine surface untouched by `571f2a6` — the message says engine-side, the code is supervisor-side). Four A8 pins are **RED**, which I confirm as a **freeze-state fact, not a security blocker** — it does not weaken any security invariant, it only means the tree has moved past the frozen baseline and needs a lead-authorised re-baseline in the freeze commit. **Not a NO-GO.** This is a **security-only** signature; the acceptance/SRE gate is not mine to sign.

---

## ADDENDUM §E — "假成功" PROVEN, AND THE CONTRACT FOR SENTINEL (Aegis-2)

Triggered by the lead's escalation (§D7): a persistence failure that is absorbed by "return None so the caller treats it as nothing happened" is not a record-fidelity nit, it is **假成功** — the same class as the three already killed today. **Owner of the fix: Sentinel. Owner of the contract below: me.**

**(E1) The defect is PROVEN, and it is not where I first described it.** My §D7 said the swallow happened in `Sentinel.on_event`. Executing a poisoned store (`PoisonedStore(real, fail_from=2)`) shows the real chain, in execution order:

1. `CoreEngine.ingest` writes the ENGINE row at **`core.py:214` `self.store.add_directive(directive)`**. Poisoning call #1 makes `ingest` **raise outright** — so the engine's own persist path is already loud.
2. A shadow/charging row goes through **`sentinel.py:318 → budget.py:399 commit → :441 _persist_directive → store.add_directive`**, i.e. *inside* `on_event` (`sentinel.py:116-130`, blanket `except Exception`).
3. `on_event` logs `shadow record failed for event_id=…`, increments `persistence_failures`, returns `None`.
4. **`CoreEngine._broadcast` (`core.py:74-79`) swallows it: `except Exception: pass`.**
5. `ingest` then returns `out` (`:228-238`) **which is identical in shape to the healthy case**, and `:241 self._broadcast(out)` is the last thing that ran.

Measured (`aegis2_fake_success.py`, `_run_fakesuccess.txt`, **proven**):

```
control healthy keys: ['acted','action','actuation_reason','assessment','directive',
                       'event_id','evidence_hash','session_id','session_status']
FAILED  ingest  keys: ['acted','action','actuation_reason','assessment','directive',
                       'event_id','evidence_hash','session_id','session_status']   <- same
persistence_failures = 1  but  'persistence_failures' not in json.dumps(out)
no field among {shadow_evaluated, evaluated, recorded, unevaluated, shadow_error,
                persistence, …} present in `out`
```

So the caller cannot distinguish "the shadow arbitrated and substituted ESCALATE" from "the shadow never ran and nothing was recorded" — and the shadow's substitute is precisely the thing that gets lost. **UNVERIFIED: the HTTP surface (`/api/assess`).** My first HTTP probe aborted on the poison's call counter, so I am not asserting anything about what the API returns; it needs its own probe with a per-event poison rather than a call-count poison.

**(E2) Two vacuities I hit in my own probe and rejected.** (a) Both engines were built against the same `tmp/jcc.db`, so the second engine's `(session_id, event_id)` hit the dedup branch and returned the *dedup* shape — which would have "proved" the defect while actually measuring nothing; fixed with a distinct db per build. (b) My control assertion tested `out.get("deduped") is False`, but the success branch never carries a `deduped` key at all — my assertion was wrong, not the code. Both fixed before any conclusion was drawn.

**(E3) THE CONTRACT (Sentinel implements; this is my signature, not a suggestion).**

- **S-1 — `_broadcast` must stop being a black hole.** `CoreEngine._broadcast` returns `List[Tuple[callback, Optional[BaseException]]]`; a subscriber that raises is *attributed* to that subscriber, not dropped. `ingest` folds the Sentinel's outcome into `out`. The `except Exception: pass` at `core.py:79` may only be reached when a subscriber has already recorded the failure itself — never as the primary sink.
- **S-2 — the returned dict must carry the truth.** Add to `out` (`:228-238`):
  - `shadow_evaluated: bool` — True only if every subscriber completed **and** recorded;
  - `shadow_unevaluated: List[str]` — the directive/event ids the shadow could not evaluate;
  - `persistence_failures: int` — mirrored from the Sentinel, so the counter is reachable from the one surface the caller holds.
- **S-3 — `on_event` returns a decision, never a bare `None` for a failure.** Return the `ShadowDecision` with `evaluated=False`, `recorded=False`, `persistence_error=<str>`. `persistence_failures` stays as a counter but must no longer be the *only* evidence.
- **S-4 — "not evaluated" must be separable from "evaluated as CONTINUE".** This is the load-bearing rule. Today a silently-failed shadow is indistinguishable from a shadow that decided `CONTINUE`. After S-2, `out["action"]` must not be consumable as shadow-arbitrated unless `shadow_evaluated is True`.
- **Recommend fail-open on liveness, fail-visible on truth: do NOT raise from `ingest`.** P3 is observe-only and a shadow-plane I/O fault should not take the ingest path down. The guarantee is that the result can no longer be *read* as success. If the lead wants strictness, add an opt-in `_strict` flag for P5, not a default.

**(E4) The falsifier, ready to paste (non-vacuous by construction).**
```python
store = PoisonedStore(real, fail_from=2)      # engine row lands; shadow row dies
out   = engine.ingest(env)
assert out["shadow_evaluated"] is False
assert out["shadow_unevaluated"] == ["dir_<sid>_<event>"]
assert out["persistence_failures"] == 1
# non-vacuity: a healthy store must show the exact opposite, so a fix that
# hard-codes False cannot pass these asserts.
assert healthy_out["shadow_evaluated"] is True and healthy_out["shadow_unevaluated"] == []
```

**(E5) A8 re-baseline — I did nothing, per the lead's ownership.** The lead owns the re-baseline (discipline ㉗) and authorised it in the freeze commit only. I applied discipline ㉞ to my own work: the four digests quoted in §D are **valid only for the commits that produced them** and must be recomputed, not copied, when the freeze commit moves.

**(E6) F-3 — I accept the lead's ruling.** F-3 is **CLOSED**, wording corrected to *"priced at the arbitration point, on the supervisor plane"* — the commit message said engine-side and was simply wrong; the invariant that matters ("nothing is issued or charged unless it was priced") does hold. The residual survives as **F-3-residual (ACCEPTED RISK + P5 PRECONDITION)**: the engine ingest loop does not consult a budget, so any publishing path that bypasses `Sentinel.on_event` bypasses the ceiling. Today that is untested-but-true (`Actuator.execute` = 0 production callers), not guaranteed — at P5 actuation it must become an assertion.

---

## ADDENDUM #82 — P4 独立安全复核 (Aegis, 2026-09-25)

- **Reviewer:** Aegis（独立安全工程师）
- **Reviewed at:** commit `83d32a8`（HEAD, master）之前；复核对象为 #82 已落地的五笔改动
- **Date:** 2026-09-25
- **Scope:** `packages/jcc_core/state_store.py`、`packages/jcc_core/core.py`、`packages/supervisor/sentinel.py`、`packages/supervisor/budget.py`
- **Constraint honoured:** **只读**产品代码 + 写安全评审文档；唯一新增的可执行产物是测试**钉子** `packages/jcc_core/tests/test_aegis_p4_review_82.py`（位于 `packages/*/tests/` 下，符合约束）。未改动任何产品实现文件。
- **复核对象 commit：**
  - forge `b29d587` — F1/F2 origin 归属 + A2 按前缀分区
  - forge `959231e` — 预算计数器折叠两平面 R1-R4
  - forge `973217f` — M1 回填迁移
  - forge `d91a09f` — M2：老库可写 + 回填后 F2 仍活
  - sentinel `632f0dd` — S-1~S-4 持久化失败可见性契约 + supervisor 侧 `origin="supervisor"` 写入

**方法学：** 四点结论均不是"读代码觉得对"，而是实际构造攻击并**观测防御是否成立**（每点下方的"证伪"均运行了真实代码，证据见 `test_aegis_p4_review_82.py`，18 个 nails 全绿）。本复核与 ADDENDUM §E 的关系：§E 是 Aegis-2 在 S-1..S-4 **实现前**拟定的合同；本复核是**实现后**的实测，重点标记实现与 §E 合同的偏差。

---

### 1. 新 `origin` 列的三个攻击面 — **CLOSED**

**(1a) 调用方不传 `origin` 必须 `TypeError`，不能默认成 `engine`。**
- 构造：`StateStore.add_directive(_mint("dir_s-origin_e1"))`（完全省略）→ 期望 `TypeError`；`add_directive(_mint(...), "engine")`（位置传参）→ 期望 `TypeError`。
- 结果（实测）：两者都抛 `TypeError`，且 `get_directives("s-origin") == []`——拒绝发生在写入**之前**，没有任何 `origin='engine'` 默认行落地。
- 额外核对：`inspect.signature` 显示 `origin` 的 `default is empty` 且 `kind is KEYWORD_ONLY`。
- 结论：默认归因不可达。

**(1b) 伪造 origin 让一行被记成 engine 或 supervisor。**
- 构造（参数化 5 例）：`("dir_..","supervisor")`（engine 命名空间 + sentinel 名）、`("shadow_..","engine")` 反向、`("weird_..","engine"/"supervisor")`（无法识别形状）、`("dir_","engine")`（裸前缀无事件）。
- 结果：全部 `ValueError`，且 `get_directives` 为空——F1 `resolve_directive_origin` 的前缀归属拦住了。再用两个合法写入（`dir_`→engine、`shadow_`→supervisor）确认正常路径仍可写且归属正确。
- 结论：F1 前缀白名单 + 交叉拒绝成立，伪造 origin 无法落地。

**(1c) M1 回填能否被诱导"补"成某 origin 而非留 NULL 进 refuse 名单。**
- 读 `backfill_directive_origins`（`state_store.py:196-283`）：它只动 `origin IS NULL` 的行，取值为 `directive_plane_of(directive_id)`（与 F1 同一查找），`None` 则留 NULL、进 `refused`/`still_null`，**永不**写成某 origin。
- 构造：插入一行形状无可归属（`weird_s-x_e1`，`origin=NULL`），调用 `backfill_directive_origins`。
- 结果（实测）：`report["refused"]==["weird_s-x_e1"]`、`report["still_null"]==["weird_s-x_e1"]`、`admissible_as_release_evidence is False`；**回读该行 `origin` 仍为 `NULL`**（非 `engine`/非 `supervisor`）。可归属行则按 `directive_plane_of` 推导（engine/supervisor），`attributed==2`。
- 结论：M1 不会为无法归属的行伪造 provenance；§E 担心的"把 #82 缺陷洗白成某 origin"被 fail-closed 挡住。

---

### 2. "假成功"现在是否真可见 — **PARTIALLY CLOSED（边界仍是黑洞）**

手工构造 sentinel 持久化失败路径（覆盖 `transaction()` 使其抛 `RuntimeError`，即 §E 的"毒库"等价物），实测：

- `on_event` 返回 `None`；`persistence_failures == 1`；`persistence_durable_writes == 0`；`persistence_ok is False`（`test_p2_on_event_returns_none_and_counts_the_failure_once`）。**计数只一次**（不是 `on_event` 的 except 与 `_append_shadow` 双重计数）。
- 这证明 S-1..S-4（commit `632f0dd`）**确实**把"丢失的记录"变成了一个被计数、可观测的信号——相对 §E 之前"被 `except: pass` 吞掉、完全不可见"的状态，这是**真实改进**。

**但**本复核独立发现两件事，使得"真可见"只在 sentinel 进程内成立，在引擎边界不成立：

**(2i) 引擎调用方（engine.ingest 的返回）仍无法区分"shadow 仲裁并替换成 ESCALATE"与"shadow 根本没跑"。**
- 构造 A/B 两组**引擎侧逐字节相同**的 ingest（用确定性 `_FixedProvider` 固定 assessment，隔离 shadow 为唯一变量）：A 组 shadow 正常落库；B 组 shadow 的 `shadow_decisions` 追加失败。
- 结果（实测）：`out_a["action"] == out_b["action"]`；折叠后的预算计数器 `directives_used/escalations_used/cost_units_used/rate_used` 在 A/B 间**完全相同**；`out` 中**不含** `shadow`/`persistence`/`escalate` 任一键；唯一差异是 `sent_b.persistence_ok is False`（只存在于 sentinel 对象上）。
- 原因：`core.py:74-79` 的 `_broadcast` 用 `except Exception: pass` 吞掉订阅者异常，且它本身**丢弃** `on_event` 的返回值；`ingest` 末尾返回的 `out`（`core.py:229-245`）只装引擎自己的决策。**所以 S-1..S-4 把失败变可见了，但引擎边界把信号扔掉了。**

**(2ii) 独立修正题面前提：`is_event_evaluated` 在 shadow-only 失败时并不会变 `False`。**
- 题面声称"persistence_failures 只计一次、`is_event_evaluated` 因此为假"。实测**不成立**：S-2 按"经济事件 key"折叠，引擎的 `dir_` 行（或实测中 sentinel 自己的 `directives` 充电行）已满足该 key，所以事件仍判为"已评估"。`is_event_evaluated` 检测的是"**事件缺行**"，不是"shadow 单独失败"。真实信号在 `sentinel.persistence_ok`，既不在引擎侧、也不在 S-2。

**(2iii) 关键独立发现：`add_directive` 不走 `transaction()`。**
- `StateStore.add_directive`（`state_store.py:610`）用 `self._lock` + `self._conn()` **直接**写，**不调用** `self.transaction()`。而 `Sentinel._append_via_store_transaction`（`sentinel.py:557`）才走 `transaction()`。
- 后果：覆盖 `transaction()` 让 `shadow_decisions`（哈希链审计记录）追加失败，但 sentinel 的 `directives` **充电行**仍通过 `budget.commit → _persist_directive → add_directive` 落库（实测 `test_p2_directives_charge_survives_shadow_decision_failure`：即便 `persistence_ok is False`，`shadow_` 充电行已写入且 `origin=="supervisor"`）。
- 评价：对 #82 而言这是**好事**——归因行比审计哈希链更健壮，归因缺陷（#82 真正关心的事）不受该失败模式影响。但它意味着"§E 式 `on_event` 返回 None ⇒ 什么都没持久化"的解读是错的：`directives` 充电行可能已写。两个持久化是**相互独立**的。

**(2iv) 与 §E 合同的偏差。** §E 的 S-1/S-2/S-3 明确要求把 `shadow_evaluated`/`shadow_unevaluated`/`persistence_failures` **注入 `out`** 并让 `_broadcast` 归因失败而非丢弃。**实测 `632f0dd` 未实现这些边界改动**：`out` 结构未变，`_broadcast` 仍是 `except: pass`。所以 §E 的"假成功不再能被读成成功"在**进程内 sentinel 观测者**层面达成（persistence_ok），在**引擎外部调用方**层面**未达成**。

---

### 3. 归属反转的时序依赖 — **CURRENTLY HONORED，但只靠代码顺序，无钉子**

折叠规则是"保留最后写入者"（`_directive_events`，`state_store.py:330-333`："ORDER MATTERS … the surviving row is the LAST written"；`budget_snapshot` 注释 `state_store.py:860-866`："the surviving row is the LAST one written … the sentinel's charging record"）。

- 依赖写在哪里：`core.py` 的 `ingest` 在**步骤 8** 先写引擎 `dir_` 行（`core.py:217 self.store.add_directive(directive, origin="engine")`），**然后**才 `core.py:244 self._broadcast(out)` 触发 sentinel 写 `shadow_` 行。因此 shadow 行**总是**后写、总是存活。
- 若反过来（引擎后写）：实测（`test_p3_reversed_order_loses_the_escalation_no_nail_blocks_it`）——先写 shadow ESCALATE、再写 engine CONTINUE，**折叠保留 CONTINUE，ESCALATE 丢失**，`escalations_used==0`。
- **是否有钉子挡住：** **没有。** 现有 `test_the_fold_takes_the_substituted_action_not_the_engine_action`（`packages/supervisor/tests/test_p4_directive_origin_pins.py`）只是手工先插引擎行再跑 sentinel，证明"正确顺序有效"，但**不会**在顺序被反转时失败。当前仅由代码顺序保护。
- **需要新增的钉子（不实现产品代码）：** 一个集成回归测试，把"引擎 `add_directive` 必须在 `_broadcast` 之前"固化为不变量——更稳妥的做法是让折叠在事件两平面都出现时**优先保留 charging 记录的 action**（而非依赖 rowid 顺序）。在 P5 前至少应加一条"若 `ingest` 把 `_broadcast` 移到 `add_directive` 之前，escalation 必须仍被保留"的会失败型回归钉。

---

### 4. K-分叉方向性 — **CLOSED（保守方向，且不可由攻击触发）**

`directive_event_key`（`state_store.py:114-127`）对无法识别的形状返回包含 `unrecognised:` 的唯一 key，永不并入已识别事件。

- 构造：`directive_event_key("zog_s_1","s") != directive_event_key("dir_zog_s_1","s")`；相同形状 key 稳定；含 `"unrecognised"`。
- 实测（`test_p4_unrecognised_shapes_never_merge_and_only_over_count`）：插入一行无法识别形状，独立成 1 个 `directives_used`（**多算**方向），未消失进已识别事件。
- **"多算=保守"的判定：** 漏计费（少算）才是 #82 的病；多算只会让预算更早触发，不会让事件被当作"没发生"。
- **多算会不会把预算永久吃满、后续事件全拒？** 不可由攻击触发：`add_directive` 在写入时强制 F1 前缀归属，构造 `("zog_..","engine"/"supervisor")` 均被 `ValueError` 拒绝（`test_p4_attacker_cannot_inject_unrecognised_rows_through_the_write_path`），所以攻击者**无法**注入无法识别形状的行来膨胀预算。多算仅限 legacy/预-F1 残留，是有界的一次性量，正常事件仍每事件折成 1。
- **二级发现（保守、fail-closed，非 #82 新面）：** 一行无法识别形状若带**不可计价** `action`（如 `NOT_A_REAL_ACTION`），`budget_snapshot` 的 `cost_units_used` 推导会抛 `ValueError`（`test_p4_a_corrupted_legacy_row_with_bad_action_raises_not_silent_undercount`）→ 治理器转 `CODE_CHECK_ERROR`（耗尽）。这是 **fail-closed**（宁可阻断也不少算），仅对已损坏的 legacy 数据有可用性影响，非新攻击面，记为 residual。

---

### 结论（GO / NO-GO）

**GO** — #82 的根因（同一经济事件被两平面各写一行 `directives` 且归属缺失）已被 F1/F2/M1/M2 + S-3 的 `origin` 写入实质性修复，且本复核独立构造了四类攻击全部被拦下（点 1/3/4 闭合，点 3 仅缺钉子）。

| 点 | 结论 | 证据 |
|----|------|------|
| (1) origin 三攻击面 | **CLOSED** | `p1a`/`p1b`/`p1c` 三个 nail 实测全绿 |
| (2) 假成功可见性 | **PARTIAL** | S-1..S-4 在 sentinel 进程内可见（`persistence_ok`/None），但引擎边界仍是黑洞（见 2i/2iv，与 §E 合同偏差） |
| (3) 时序依赖 | **HONORED·无钉** | 当前顺序正确；反序实测丢失 ESCALATE；无回归钉 |
| (4) K-分叉方向 | **CLOSED** | 仅多算、不可由攻击触发；legacy 残留有界 |

**唯一 residual（不阻塞 P4 冻结，需跟进）：**
- **点 2 边界**：`core.py:74-79` `_broadcast` 仍丢弃 `on_event` 返回值并吞异常，`out` 未承载 §E 的 `shadow_evaluated`/`persistence_failures` 字段。需要谁修：**Sentinel / lead**（按 §E 合同收尾，建议 P5 完成边界注入；或至少加钉断言"引擎调用方应能从 `out` 区分 shadow 成败"）。本复核不擅自改产品代码。
- **点 3 缺钉**：建议新增"引擎 `add_directive` 必须先于 `_broadcast`"或"折叠优先保留 charging 记录 action"的回归钉（见点 3）。
- **点 4 二级**：legacy 坏 `action` 行会让 `budget_snapshot` 抛错→`CODE_CHECK_ERROR`；建议对 `backfill` 拒绝集外的行做 `action` 可计价预检（residual，保守方向）。

**可复现证据：** `packages/jcc_core/tests/test_aegis_p4_review_82.py`（18 passed，`CODEBUDDY_SAFE_DELETE_ENABLED=0` 下运行以绕过沙箱安全删除守卫对 pytest 临时目录的误触发）。

> 注：本复核未修改任何生产实现文件，仅新增上述测试钉子与安全文档，符合"只读 + 写评审文档"约束。
