# P0–P2 ACCEPTANCE REPORT — CI Gate Hardening + Independent Verification

> NOTE ON THIS PATH. This file previously held the **control-plane repair**
> acceptance (deliverables B–J, dated 2026-09-26). Its content is **NOT
> destroyed**: it is preserved byte-identically at
> `artifacts/acceptance/P0_P2_ACCEPTANCE_REPORT.pre-ci-gate-backup-20261111_2240.md`
> (15342 bytes, `cp -p`). That report in turn preserved the phase-1
> host-security report at `...phase1-backup-20260926_2235.md`. Three layers of
> evidence, none overwritten in place.

- Host: Windows, Git Bash (POSIX sh), Node **v22.22.2**
- Worktree under test: `D:\jev_worktrees\v2ops`, branch `feature/jev-v2-ops-integration`
- HEAD at verification start: **`8da3947`** (earlier anchors `be80d3c` → `6a12192` → `2b50ac6` → `d3b4ee5`)
- Authoritative environment: **clean `git clone` of `8da3947`** at
  `C:\Users\Administrator\AppData\Local\Temp\cleanci`, `git status --short` = **0 lines**, untracked = **0**
- CI platform for every job: `runs-on: ubuntu-latest` (verified: `ci.yml:24`, `ci.yml:159`, `v16-market-provenance.yml:30`)
- Author: Harbor (Test / SRE / release gate). I did **not** write product code, and I did
  **not** run `git add/commit/push/checkout/reset/restore/stash` at any point.

**Every command below was executed; the exit code and count are the real observed
values.** Nothing here is inferred from a diff.

---

## 0. Scope and hard constraints honoured

Owned and touched: `.github/workflows/*`, `apps/command_center_3d/scripts/*`.
Never touched: anything under `reports/` (Aegis's `reports/JEV_REAL_TRADING_SECURITY.md`,
the supplements), any product source, any secret.

- No new dependency. The two scripts are `node:` stdlib only (`node:fs`, `node:path`,
  `node:url`, `node:assert/strict`). No `package.json`, no lockfile, no install.
- The seven line-start `node apps/...` invocations in `ci.yml` are intact; the floor script is
  referenced through `$GATE_FLOOR_SCRIPT` precisely so it cannot be mistaken for an eighth suite.
- No assertion deleted or relaxed. A1–A8, Y1, Y2 all still fail when violated (falsified one by
  one, §4).
- `apps/command_center_3d/node_modules` is **ABSENT** on this host and all seven gate suites still
  exit 0 — the empirical justification for running the gate before `npm ci`.

---

## 1. Commands, exit codes, counts

Every step body is extracted **verbatim from the YAML** (block-scalar extraction by indent) and run
under `bash -eo pipefail` — the same semantics GitHub uses for `shell: bash`. Nothing retyped by hand.

| # | Command (verbatim step body) | Exit | Real output |
|---|---|---|---|
| 1 | `node apps/command_center_3d/scripts/check-no-fake-live.mjs` | **0** | `CORE_CASES=7 EXTRA_CASES=6 TOTAL_CASES=13` |
| 2 | ci.yml step *P0/P1/P2/P3P4 gap-closure gate* | **0** | 7/7 suites; 7 log files written |
| 3 | ci.yml step *Gate case-count floor (C12 / F-22-G)* | **0** | `GATE_FLOOR_OK suites=7 total_cases=97` |
| 4 | v16 step *V1.5 baseline suite* | **0** | `V1.5_BASELINE_OK groups=8 assertions=95` |
| 5 | v16 step *V1.6 new suites* | **0** | `OVERALL=PASS {"total":8,"pass":8,"fail":0}` · `assertions={"total":104,"passed":104,"failed":0}` |
| 6 | v16 step *Track B — AI Mock Contract Tests* | **0** | `TOTAL checks=120 passed=120 failed=0 => PASS` · `V16_AI_CONTRACT_OK ... network_calls=0` |
| 7 | mutation matrix, 11 cases (§4.1) | A=**0**, B1–B10=**1** | baseline green, every mutation red |
| 8 | deletion matrix, Y3/Y4/Y5 (§4.3) | suite=**0**, floor=**1** | `GATE_FLOOR_VIOLATION 7-no-fake-live expected=13 actual=12` |
| 9 | `bash -n` on 5 extracted step bodies | **0** | gate / floor / V1.5 / V1.6 / Track B all parse |
| 10 | secrets scan of both workflows | — | `pull_request_target`, `secrets.`, `ghp_`, `github_pat_`: **0 hits** |

Per-suite case counts inside the gate (from step 3, the declaring table):

```
1-p0-contracts        expected=7   actual=7
2-p0-runner           expected=24  actual=24
3-market-data         expected=10  actual=10
4-inference-evidence  expected=17  actual=17
5-p3-contracts        expected=12  actual=12
6-supervisor          expected=14  actual=14
7-no-fake-live        expected=13  actual=13     <-- 10 -> 12 -> 13 in this round
                                        total_cases=97

# arithmetic check: 7+24+10+17+12+14+13 = 97
```

---

## 2. What changed, and the defect each change closes

| ID | Defect (proven, not assumed) | Change | Falsifier |
|---|---|---|---|
| **F-21** | The P0–P2 gate was step #10. On Run `42968141252` a red unit test made GitHub report it **`skipped`** — the gate vanished exactly when needed. | Gate is now the **first `run` step** of `build-and-test`, before `npm ci`, teeing to `${{ runner.temp }}/jcc-gate`. Rationale in YAML comments. | Y2 asserts `gate index < Install index`; injecting an `Install` step before the gate turns it red. Confirmed at step level on a real runner, not only by the YAML order: Run `43045467136` shows gate=step#4, floor=step#5, Install=step#6; on `be80d3c` the same gate was step#10, after Install/Typecheck/Build/Unit. |
| **F-22-C** | `ci.yml` `pull_request.branches` could not match `feature/**`. Note the filter is applied to the **base** ref, not the head, so the accurate statement is: a PR whose **base** was not one of the listed branches created no job at all. An earlier revision of this line said "PRs **from** the integration branch ran zero checks", which is the head/base confusion this row is itself about. | `+ "feature/**"` (additive only). | Y2 branch guard. |
| **C14** | `v16-market-provenance.yml`: same hole on `push` (`feature/jev-**`) and on PR base (`feature/v16-**`). | Both added; `release/jcc-v1.3-final` **retained** and now asserted *inside* `pull_request.branches`. | Y1, four separate asserts. |
| **F-23** | Y2 said "5 gate scripts" when there are 7, and could be fooled by commenting out `node ...` lines. | Comment lines stripped → anchor `^node\s+(\S+\.mjs)` → compare **set equality** against `GATE_SUITES`. | Commenting out one suite: `missing(1)` + exit 1; restore → green. |
| **C12 / F-22-G** | An exit code cannot tell "96 cases passed" from "3 passed and the rest were deleted". | `set -euo pipefail` + `node <suite> \| tee .gate/<slug>.log` per suite, then `gate-assertion-floor.mjs` with **exact** per-suite floors. | `6-supervisor expected=14 actual=13` → exit 1; deleting the summary signature → `reason=summary-signature-not-found` → exit 1. |
| **R15 / F-25** | Track B had **no** assertion floor: a suite that silently lost checks still passed. Demonstrated by the `scSellAfterBuy` flake (`checks=35` instead of 37). | Frozen `suites===10 && checks===120`, `console.error` + `process.exit(1)`. | §4.1 B1/B2/B6. |
| **F-26** | Track B validated a **git-tracked** JSON, so a stale copy could certify a run that never happened. | `rm -f ../../reports/evidence/v16_ai/v16_ai_contract_tests.json` **before** the runner; the verifier's `require()` then fails closed. | §3 — reproduced end to end from the YAML itself. |
| **R22** | The V1.6 step had the **identical** fail-open (`reports/evidence/v16/summary.json` is tracked too). | Same `rm -f` before `node tests/v16/run-all.mjs`. | §3 — I verified R22's claim independently instead of trusting the commit message. |
| **R19** | "94 assertions" was a **mislabelled** number: it is 94 *named cases*. 751 `assert.*` statements sit behind them. | Emitted fields renamed `CORE/EXTRA_ASSERTIONS` → `*_CASES`; floor regex and `total_assertions` → `total_cases`, so the wrong sentence stops being writable. The exact `assert.*` total is deliberately **not** quoted — a stale hard number is worse than none. | header of `gate-assertion-floor.mjs`. |
| **R23** | The comment-bypass guard was **assumed**, never demonstrated. | It is now demonstrated by mutation (§4.1 B1). | §4.1 |

### 2.1 My own false claim, corrected

The first version of the F-25 guard used `l.join("\n")` and therefore stayed **GREEN** when the
floor was commented out: the string it searched for was still present, just dead. My code comment
asserted "verified empirically — commenting the floor out turns this red". **That claim was false in
my own harness.** Root cause: `yamlLines()` does not strip comments. Fixed with a shared
`stripLineComments()` helper (`^\s*(//|#)`) applied to every structural guard.

Second self-caught error: I first appended the new guards **inside** Y1. That kept `TOTAL_CASES=10`
(convenient) but made the new guards *unguarded* — delete them and Y1 still passes. That is the
exact defect class being fixed one level up. They are now separate checks Y3/Y4/Y5, counted by the
C12 floor. §4.3 proves the recursion is closed.

---

## 3. The residual fail-open (F-26 / R22), reproduced from the YAML

Extracted the step body verbatim, replaced only the runner invocation with `: # no-op` (a runner
that exits 0 and writes nothing), ran under `bash -eo pipefail`.

**Track B** — `reports/evidence/v16_ai/v16_ai_contract_tests.json`, confirmed tracked by `git ls-files`:

| Variant | Exit | Output |
|---|---|---|
| no `rm -f`, no-op runner | **0** | `V16_AI_CONTRACT_OK {"suites":10,"checks":120,...}` ← **fail-open** |
| `rm -f`, no-op runner | **1** | `Cannot find module .../v16_ai_contract_tests.json` / `MODULE_NOT_FOUND` |
| `rm -f`, real runner | **0** | `TOTAL checks=120 passed=120 failed=0 => PASS` |

**V1.6** — `reports/evidence/v16/summary.json`, also tracked (R22's claim, verified by me):

| Variant | Exit | Output |
|---|---|---|
| no `rm -f`, no-op runner | **0** | `V16_SUITE_OK {"total":8,...} assertions={"total":104,"passed":104,"failed":0}` ← **fail-open**, and it also satisfied the *newly frozen* 104, because the stale file carries 104 |
| `rm -f`, no-op runner | **1** | `MODULE_NOT_FOUND` |
| `rm -f`, real runner | **0** | 8/8 groups, 104/104 assertions |

The lesson worth keeping: **freezing a number does not make it fresh.** The 104 was frozen by the
same commit that closed the freshness hole; on its own it would have certified a stale copy.

---

## 4. Negative testing (this is what decides PASS/FAIL)

### 4.1 Mutation matrix — every guard must turn red

`A` is the untouched baseline, so a matrix that only ever prints 1 proves nothing.

```
A  baseline (untouched)                              exit 0
B1 floor commented out (// on both lines)            exit 1  R15/F-25: ... floor is GONE
B2 floor numbers drifted checks!==120 -> 100         exit 1  ... numbers drifted from suites=10 / checks=120
B3a soften NOT_GREEN exit(1)->exit(0)                exit 1  ... no longer fatal on its own line
B3b soften NETWORK_CALLS exit(1)->exit(0)            exit 1  ... no longer fatal on its own line
B3c soften COUNT_CHANGED exit(1)->exit(0)            exit 1  ... no longer fatal on its own line
B4  Track B gains continue-on-error: true            exit 1  ... must NOT have continue-on-error
B5  rm -f freshness precondition deleted             exit 1  F-26: ... stale git-tracked JSON can certify a run that never happened
B6  whole V16_AI_CHECK_COUNT_CHANGED block deleted   exit 1  ... floor is GONE
B7  rm -f moved AFTER the runner                     exit 1  F-26: ...
B8  soften V16 104-assertion floor exit(1)->exit(0)  exit 1  ... no longer fatal on its own line
B9  soften V1.5 95-assertion floor exit(1)->exit(0)  exit 1  ... no longer fatal on its own line
B10 V1.6 rm -f freshness precondition deleted        exit 1  R22: ...
-------------------------------------------------------------
restored baseline                                    exit 0
```

B8/B9/B10 exist because of my own mistake: a mis-targeted mutation softened
`V16_AI_CONTRACT_NOT_GREEN` while my guard inspected only the floor line, and the guard stayed
green. The facet is now an invariant over **all eight** `console.error('V1.5_*|V16_*` sites.

Two `N/A — MUTATION DID NOT APPLY` results appeared during development. Both were **harness** bugs
(`String.replace` hitting the wrong occurrence; a shell-mangled `\s`), not guard bugs; both were
re-run after fixing.

### 4.2 Y1 branch coverage

`push`: `feature/v16-market-provenance-hardening`, `feature/v16-p0-gap-closure`, `feature/v16-**`,
`feature/jev-**` — all present. `pull_request`: `release/jcc-v1.3-final` (retained) and
`feature/v16-**`. Removing the release rule turns Y1 red.

### 4.3 Guards may not be unguarded — deletion matrix

Delete a check outright and the suite still passes; the C12 floor must catch it:

```
deleted Y3   suite alone -> CORE_CASES=7 EXTRA_CASES=5 TOTAL_CASES=12 exit=0
             floor       -> GATE_FLOOR_VIOLATION 7-no-fake-live expected=13 actual=12   FLOOR_EXIT=1
deleted Y4   (identical)   expected=13 actual=12   FLOOR_EXIT=1
deleted Y5   (identical)   expected=13 actual=12   FLOOR_EXIT=1
restored     -> CORE_CASES=7 EXTRA_CASES=6 TOTAL_CASES=13 exit=0
```

---

## 5. Acceptance checklist from the brief — honest verdicts

I will not mark these green on someone else's say-so. Where no automated assertion exists, the
verdict says so.

| Requirement | Evidence I actually ran | Verdict |
|---|---|---|
| healthz | `apps/command_center_3d/cloud/preview-gateway.mjs:71` implements `/healthz`; probed only by `.github/workflows/cloud-staging-runtime.yml`, which needs a staging host | **PARTIAL** — implemented, no assertion in any gate |
| readyz | whole-repo grep for `readyz`: **0 hits** | **FAIL** — not implemented |
| auth / HMAC (`service-auth.mjs`) | **not "untested" — unreachable.** `grep` for each export across all `.mjs/.py/.ts`: `verifyServiceSignature` 2 hits, `consumeNonce` 3, `verifyGithubOidc` 2, `signServiceBody` 2 — **every hit is inside `service-auth.mjs` itself** (declaration + the module's own export list; `:149` calls `consumeNonce` internally). The two importers take **only `sha256Hex`**: `verification.mjs:14` and `strategy-events.mjs:9`. So 238 lines of HMAC-SHA256 + 300s skew + D1 replay cache + RS256/JWKS sit behind **zero callers**. | **FAIL (unverified) — but NOT a security blocker**: unreachable ⇒ no inbound attack surface. The failure is in the *claim*, not the control. |
| Hermes receiver (bad signature / replay / stale / oversize / session isolation) | **PASS — and it is tested.** `grep -cE "^def test_receiver" packages/jcc_core/tests/test_p2_integration_smoke.py` → **8** (1 positive control `:255 test_receiver_accepts_valid_and_ingests` + **7** rejection tests `:267,276,284,295,303,310,323`). I did **not** trust the test file: I drove `SecureWebhookReceiver.verify()` myself with a 10-scenario harness (`python`, no pytest, no venv) → **`SCENARIOS=10 FAILS=0`, exit 0**: valid; replay of the same signed delivery; `bad_signature`; body swapped after signing; cross-agent spoof (hermes signature, `X-JCC-Agent: codex`); stale `+1000s`; future `−1000s`; `unsupported_agent`; `payload_too_large`; empty secret ⇒ `ValueError` (fail-closed at construction). Second layer: `test_p3_acceptance_gate.py:814/815` asserts `GET`/`POST /api/hooks == 404` — the receiver is **not mounted** (ADR-0008). | **PASS** — library level + no inbound route = two independent fail-closed layers |
| dedupe / idempotency | `t2_idempotency` (V1.5) PASS, `v16_t2_idempotency_fields` PASS, `t4_idempotency` (Track B) PASS | **PASS** |
| session isolation | `t4_account_isolation` PASS, `t6_session_failclosed` PASS; plus the receiver's `session_isolation` rejection driven by me | **PASS (library)** |
| Windows paths | **9/9 jobs are `ubuntu-latest`, 0 Windows** (`ci.yml:24,159`, `cloud-staging-runtime.yml:11`, `jev-cloudflare-staging.yml:18`, `v14-acceptance.yml:21,40,88,143`, `v16-market-provenance.yml:30`). Existing evidence cannot rescue it: `artifacts/acceptance/REBOOT_RECOVERY.json` carries `release_sha = cefed66…` (the **2026-09-26** phase-1 SHA) and `checked_at=2026-09-26T22:18:43+08:00`; `restart_recovery.json` (`schema=jcc-e2e-evidence/2`) carries `provenance.generator_sha256` + `module_sha256` but **no commit/SHA and no platform field**. Neither names Windows; neither covers `8da3947`. | **FAIL — B2 gate blocker** (see §6 R-1b) |
| malformed / oversized payload | **must be split by surface.** `integrations/_common/jcc_receiver.py:36,148` → `payload_too_large`, asserted at `test_p2_integration_smoke.py:295` and reproduced by my harness → **PASS**. `apps/command_center_3d/server/engine/_lib/paper-server.mjs:63` (`BODY_TOO_LARGE`/413) and `strategy-registry.mjs:255` → **0 assertions**, and `paper-server` **is reachable** (`server/trading_api.mjs` adapter, `127.0.0.1:4791`, behind an authenticated gateway) → **FAIL (unverified)** on the engine surface. `service-auth.mjs:119` → unreachable (§ above). | **PASS** on the Hermes receiver / **FAIL (unverified)** on the engine HTTP surface — **partly superseded by §10.1 and §10.2** |
| state recovery | `t3_outage_recovery` PASS, `t5_replay_readonly` PASS, `t6_session_failclosed` PASS | **PASS** (process level) / **UNVERIFIED** for Windows reboot (§6 R-1b) |
| secret redaction | `t7_redaction` (5 checks) inside the 120/120 Track B run; redaction commit `c4db677`; V1.6 H7 pattern | **PASS** |
| no secrets in workflows | `pull_request_target` / `secrets.` / `ghp_` / `github_pat_`: 0 hits | **PASS** |
| four-agent identity distinguishable | `SUPPORTED_AGENTS` = 4 names and session→agent binding is enforced (my harness: codex reusing a hermes-bound session ⇒ `session_isolation`; cross-agent spoof ⇒ `bad_signature`). **But all four share one HMAC secret**, so identity is *self-declared*, not cryptographically separated. | **COMMUNITY-VERIFIED + caveat**, **not PASS** (see §5.2) |
| JCC binds localhost only | `cloud-entry.mjs:19-20` dispatches the real adapter as `HOST:127.0.0.1 PORT:4791`; the `0.0.0.0` at `cloud-entry.mjs:25` is the **staging preview gateway**, which has its own basic auth (≥20-char password) and throws unless `ENABLE_REAL_TRADING` is explicitly set | **PASS for JCC Core** — the `0.0.0.0` is **not** a violation (§5.2) |
| WorkBuddy native permission flow intact | outside this phase's diff | **NOT ASSESSED HERE** |
| no automatic side-effect actuator | outside this phase's diff | **NOT ASSESSED HERE** |
| clean start/stop/status/doctor, backup/uninstall/rollback | outside this phase's diff; nothing added or removed this round | **NOT ASSESSED HERE** |

### 5.1 Claims I had to correct in my own report

**(a) `service-auth.mjs` was described as "consumed, untested".** It is **unreachable**. My first
version said it was "consumed by `verification.mjs` and `strategy-events.mjs`", which implied the
HMAC surface was *wired but untested*. Those two modules import **only `sha256Hex`**
(`verification.mjs:14`, `strategy-events.mjs:9`), and the four auth entry points have **zero
callers anywhere in the repo**. The distinction matters in both directions — unreachable code is
not an exploitable surface (so not a blocker), but any sentence containing "service-to-service HMAC
authentication is implemented / trusted-runner authentication is in effect" is **false** today.

**(b) The Hermes test count.** I wrote "7 receiver tests". The correct number is **8**
(`grep -cE "^def test_receiver" packages/jcc_core/tests/test_p2_integration_smoke.py` → `8`):
one positive control (`:255 test_receiver_accepts_valid_and_ingests`) plus **7** rejection tests.
"7 rejection tests" was right; "7 receiver tests" was not. Counting is only truthful with the
command attached, so the command is attached.

**(c) `GATE_FLOOR ... total_cases=96`.** Stale after Y5 landed; re-measured **97** (§7.2).

### 5.1.1 What my Hermes harness does **not** cover

I drove `SecureWebhookReceiver.verify()` — the rejection pipeline — not `handle()`. `handle()`
pulls `jcc_emit → jcc_core.adapters → pydantic`, which is absent on this host (no venv), so the
ingest side effect cannot be exercised here. Consequence, stated plainly:

- **VERIFIED (me, 10 scenarios, exit 0):** signature, body-binding, cross-agent spoof, stale,
  future, unsupported agent, oversize, empty-secret construction, replay, happy-path `verify()`.
- **NOT VERIFIED locally:** the *ingest* half of `test_receiver_accepts_valid_and_ingests` — i.e.
  that an accepted envelope actually reaches the engine and the session is stored. That half rests
  on the pytest test, which cannot run on this host. It is not evidence I produced, and it is not
  counted as such.

### 5.2 Phrases this report forbids (they would each be a lie, not a simplification)

| Do **not** write | Why | Write instead |
|---|---|---|
| "Windows 已验证 / Windows restart recovery verified" | 9/9 CI jobs are `ubuntu-latest`; the only two reboot/recovery artifacts bind to `cefed66` (2026-09-26) and to test-module hashes, with no platform field | "Windows restart recovery **UNVERIFIED for `8da3947`**; B2 gate blocker" |
| "四个 agent 身份可区分 = 通过" | identity is *self-declared*: four names, **one shared HMAC secret** | "session→agent binding enforced (driven); cryptographic separation **NOT** present — any key-holder can impersonate any agent if the receiver is ever mounted" |
| "服务间 HMAC 认证已实现" | `service-auth.mjs` has zero callers | "HMAC/OIDC code exists but is **unreachable**; no inbound surface, and no claim of enforcement" |
| "Hermes 门禁无测试" | `test_p2_integration_smoke.py` has **8** `def test_receiver*` (1 positive + 7 rejection); I re-drove 10 scenarios independently, all correct | "Hermes receiver **PASS** (library-level rejection + `/api/hooks` 404)" |
| "`/api/hooks` 缺失 = 缺陷" | not mounted **by design** (ADR-0008); it is the second fail-closed layer | "/api/hooks is 404 **by design**; library correctness ≠ route will call it — if mounted later, first verify the route invokes `verify()`" |
| "`cloud-entry.mjs` 的 `0.0.0.0` = JCC 监听公网" | it is the staging **preview gateway**, which proxies the real adapter on `127.0.0.1:4791`, has its own basic auth, and throws unless `ENABLE_REAL_TRADING` is set | "a separate staging gateway, not JCC Core" — a false blocker here is worse than an omission |
| "断言总数守恒（TOTAL=13 未减少）" | the floor counts **cases**, not assertions (751 `assert.*` behind 96 cases) | "the **case** count is frozen and exact; assertion statements inside a case are not counted" |

---

## 6. Risks (ranked, with why each is not already closed)

- **R-1 (blocking) — no real GitHub Actions run on the final SHA.** Every green above is a *local
  faithful emulation*: verbatim step bodies, `bash -eo pipefail`, clean clone, 0 untracked. It is
  not a runner. I have no push ability (`gh` absent, no token), so "CI green on the accepted SHA"
  is **UNMET**. That alone prevents a full GO.
- **R-2 — the floor counts CASES, not assertions.** 751 `assert.*` statements behind 96 cases.
  Gutting a case while keeping its `suite.check(...)` wrapper is **not** caught (R19, stated in the
  floor header instead of being implied away). Closing it needs a per-case assertion floor.
- **R-1b (blocking) — B2: Windows restart recovery is UNVERIFIED for the accepted SHA.** I first
  listed this fourth. That ranking was wrong: `jcc-release-gate` makes "Windows restart recovery
  verified" an explicit GO condition, so it is the **second** blocker, not a footnote. All 9 jobs
  across 5 workflows are `ubuntu-latest`; `REBOOT_RECOVERY.json` binds `release_sha=cefed66…`
  (2026-09-26) and `restart_recovery.json` binds module hashes only with no platform marker.
  Owner: whoever owns the Windows side; next step is a `runs-on: windows-latest` job that kills and
  restarts the JCC services and binds the result to a commit SHA **and** a platform field.
- **R-3 — attribution is impossible.** Four agents share one git identity
  (`git-common-dir = D:/jev/.git`; every commit is `Harbor Falsifier`). Three commits landed **while
  I was verifying** (`2b50ac6 → d3b4ee5 → 8da3947`). Any "verified at SHA X" decays; mine is
  anchored at `8da3947` plus the two uncommitted files in §7.
- **R-4 — evidence churn.** Running any suite rewrites tracked `reports/evidence/**/*.json`
  (`generatedAt` / `startedAt`), so a CI job leaves the checkout dirty — the F-18 class on a second
  surface. Not fixed here.
- **R-5 — the V1.6 suite cannot run without `.git` metadata.** `0001_is_verbatim_baseline_blob`
  returns `gitExit:128` in a source-archive copy; the *Reproduce the V1.5 local test environment*
  step calls `sudo`, absent on this host (`exit 127`). Those two suites are therefore
  Linux-runner-only, cannot be reproduced on Windows, and cannot be verified from a tarball at all.
- **R-6 — `X || true` on a runner line silently deletes the fail-closed property.** Y4/Y5 assert it
  today for the two runners I touch; a third runner added later gets no protection unless the
  invariant is generalised.
- **R-7 — `/readyz` absent and `healthz` unasserted** mean readiness/liveness cannot be gated at
  all; any crash/restart-recovery claim resting on them has no automated basis.
- **R-8 — harness traps I hit and corrected** (recorded so nobody trusts a red result): an
  incomplete copy makes `t8_role_mapping` fail (`docs/` missing); a `.git`-less copy makes V1.6 `T7`
  fail; running a repo-root step from a subdirectory makes the V1.5 anchor rebuild fail. **Three
  false FAILs, all mine, all diagnosed before reporting.**
- **R-9 — four agents, one HMAC secret.** Session→agent binding is enforced and I drove it, but a
  key-holder can sign as any of the four names. Not exploitable today only because the receiver is
  unmounted. **If `/api/hooks` is ever mounted, this becomes a live impersonation path** — per-agent
  secrets (or a key-id header) must land in the same change.
- **R-10 — `/api/hooks` 404 is a *design* fact, not a guarantee about the future.** Library-level
  rejection being correct says nothing about whether a future route calls `verify()`. Mounting
  without wiring `verify()` would produce a silently unauthenticated endpoint.

---

## 7. Provenance of this report and of the numbers in it

### 7.1 The two files are now **committed**

```
feature/jev-v2-ops-integration
9e883f3  fix(gate): close the same fail-open in V1.6 (Y5), widen Y3 to all 8 fatal guards, retract the 751
8da3947  fix(gate): floor expects 12 cases, not 10 — my own stale-artifact mistake
```

`git status --short` in `D:/jev_worktrees/v2ops` → only ` M reports/JEV_V2_FINAL_ACCEPTANCE_SUPPLEMENT.md`
(not mine). Adds **Y5** (R22 freshness guard) and widens the fatality invariant from Track B's three
lines to all eight `V1.5_*|V16_*` sites. `TOTAL_CASES` 12 → **13**, so `total_cases` 96 → **97**.
They had to land in one commit: the floor bump alone reds the gate (`expected=13 actual=12`), and the
script alone reds it too. That was intentional.

### 7.2 A stale number in an earlier revision of this report, corrected

Earlier revisions of this file said `GATE_FLOOR_OK ... total_cases=96`. That was true **before** Y5
(7+24+10+17+12+14+**12** = 96) and false after it. Re-measured on `9e883f3`:

```
$ node "$GATE_FLOOR_SCRIPT" "$GATE_LOG_DIR"
GATE_FLOOR ok 7-no-fake-live expected=13 actual=13
GATE_FLOOR_OK suites=7 total_cases=97 exit=0
```

7+24+10+17+12+14+13 = **97**. The error is the same class this whole round was about: a count that
was right when written and silently wrong afterwards. Recorded rather than quietly edited.

### 7.3 Where this report lives — and why the first answer was wrong

Two worktrees of the same repo exist, on **different branches**:

```
D:/jev                  e5e34ff  [feat/jev-trading-workbench]
D:/jev_worktrees/v2ops  9e883f3  [feature/jev-v2-ops-integration]   <-- the reviewed candidate
```

The brief named the deliverable path `D:\jev\artifacts\acceptance\P0_P2_ACCEPTANCE_REPORT.md`, which
is on the **non-candidate** branch. I first tried to fix that by copying the file into the v2ops
tree at the same relative path — **that does not work**, and I proposed two options that are both
already forbidden:

```
$ git check-ignore -v artifacts/acceptance/P0_P2_ACCEPTANCE_REPORT.md
.gitignore:63:artifacts/acceptance/**     ← ignored as a whole
$ git ls-files artifacts | wc -l
192                                        ← tracked only because they were force-added historically
```

**ADR-0009 EV-12 already adjudicated this** (`docs/ADR/ADR-0009-release-evidence-provenance.md`,
"Evidence is promoted into the release directory; `artifacts/acceptance/` stays ignored"):

- `artifacts/acceptance/` stays ignored **permanently**, as a whole;
- a per-file allow-list inside it is the one shape that is **no longer permitted** (EV-12 lists four
  reasons, one being "it dirties the working tree on every run");
- remediation is **promotion, not allowance** — retained evidence is promoted into `artifacts/release/`;
- `artifacts/acceptance/FINAL_ACCEPTANCE_REPORT.md` being tracked is recorded by the ADR as a
  **legacy force-add**, not as a pattern to repeat.

So my earlier "`git add -f` or move to `docs/`" was wrong on both branches of the fork: the first
repeats the exact shape EV-12 kills, the second invents a fourth evidence location. Correct action,
now taken:

```
artifacts/release/p0_p2_acceptance/P0_P2_ACCEPTANCE_REPORT.md
$ git status --short artifacts/release/p0_p2_acceptance/
?? artifacts/release/p0_p2_acceptance/      ← visible to git ⇒ committable, no -f needed
$ md5sum artifacts/release/p0_p2_acceptance/P0_P2_ACCEPTANCE_REPORT.md \
         artifacts/acceptance/P0_P2_ACCEPTANCE_REPORT.md \
         /d/jev/artifacts/acceptance/P0_P2_ACCEPTANCE_REPORT.md \
  | awk '{print $1}' | sort -u | wc -l
1                                  ← all three copies byte-identical to EACH OTHER
```

> **Why there is no literal hash on the line above.** An earlier revision of this section
> printed `55367ff7…` here, and the immediate "fix" printed `3d5afce1…` — both are now wrong,
> which is the whole point. A document cannot quote its own digest: editing the sentence that
> states the digest changes the digest. The stale value then sits in release evidence looking
> authoritative, which is the same defect class as the `751` retraction in §7.2.
> The bindings that survive editing are instead:
>
> 1. the three copies are byte-identical **to each other** — machine-checkable at any time, by
>    anyone, with the command above;
> 2. content hashes of the three files the numbers in this report depend on. **Measured at
>    `34325e7`** (an immutable SHA — deliberately *not* "HEAD", which moved five times while this
>    report was being written and is therefore not an anchor for anything):
>
> | file | sha256 measured at `34325e7` |
> |---|---|
> | `apps/command_center_3d/scripts/check-no-fake-live.mjs` | `066da5aedaca4c09d39690bb502a60fa9bd92481acd813d25f54554b44e8c9ba` |
> | `apps/command_center_3d/scripts/gate-assertion-floor.mjs` | `5e06954909acfaa9b5ff014ded93717601a6fef533ca2e0750022efb6fede254` |
> | `apps/command_center_3d/server/ai/tests/e2e_real_engine.mjs` | `442bf00c154d7eb9a027395a92a10f0549671a40feaaeb6b9ad1a906b52fcf1e` |
>
> **These hashes are a falsification tool, not a permanence claim.** Re-run
> `sha256sum <path>`: if it matches, the code that produced `total_cases=97` is the code you are
> looking at. If it does **not** match, some `.mjs` changed after this report — which is the signal
> to re-run the floor and §1, not to doubt the arithmetic. A hash anchored to "HEAD" would have
> been unfalsifiable in exactly the same way as the self-digest above: the moment anyone touches
> the gate scripts it silently becomes a claim about a tree that no longer exists.
>
> **This warning fired on itself within the same session.** An earlier revision of this table was
> measured at `e12955c` and listed `check-no-fake-live.mjs` as `4223ef3d…`. `34325e7` then added a
> real guard to that file (Y1 now asserts the `feature/jev-**` PR-base entry that had been added
> unasserted — a fresh instance of F-23, "a guard nobody counts"). The hash changed to `066da5ae…`,
> so the table above was re-measured. Case count did **not** change (`TOTAL_CASES=13`, floor
> `total_cases=97`), which is why the numbers in §1 survived; a hash table that had been declared
> "permanent" would instead have quietly become a false statement about the current tree.

Three copies exist, one of them authoritative:

| Path | Trackable | Role |
|---|---|---|
| `artifacts/release/p0_p2_acceptance/P0_P2_ACCEPTANCE_REPORT.md` | **yes** | **canonical — the one to commit** |
| `D:/jev_worktrees/v2ops/artifacts/acceptance/P0_P2_ACCEPTANCE_REPORT.md` | no (ignored) | convenience copy next to the other acceptance files |
| `D:/jev/artifacts/acceptance/P0_P2_ACCEPTANCE_REPORT.md` | no (ignored) | the path named in the brief; non-candidate branch |

**Ordering matters for P-01.** `artifacts/acceptance/final_gate_run.py` P-01 asserts
`git status --porcelain == ""` ("working tree clean") and is **not wired into CI** —
`grep -rn final_gate_run .github/workflows/` → no hits. The tree is currently dirty
(` M reports/JEV_V2_FINAL_ACCEPTANCE_SUPPLEMENT.md`, another member's in-flight edit, plus this
promotion). Therefore: **commit first, run P-01 second.** Running P-01 on a dirty tree is a
deterministic self-inflicted FAIL.

---

## 8. Rollback

1. **Normal rollback of this round** (before commit):
   `git checkout -- apps/command_center_3d/scripts/check-no-fake-live.mjs apps/command_center_3d/scripts/gate-assertion-floor.mjs`
   → returns to the `8da3947` state, which is self-consistent and verified green above.
2. **Rollback of an already-committed round**: `git revert <sha>` of that single commit. Do **not**
   hand-edit either the emitted counts or the floor table alone.
3. **Emergency "disable the gate" is not a rollback.** Setting `continue-on-error` or deleting the
   gate step re-opens F-21 — and Y2/Y4/Y5 go red on it by design. The supported way to stop the
   gate is to revert the step, which is visible in review.
4. **This report**: restore with
   `cp -p artifacts/acceptance/P0_P2_ACCEPTANCE_REPORT.pre-ci-gate-backup-20261111_2240.md artifacts/acceptance/P0_P2_ACCEPTANCE_REPORT.md`.
   The `...phase1-backup-20260926_2235.md` layer is untouched.
5. No service start/stop/uninstall is implied by any change here: the diff is two `.mjs` files under
   `apps/command_center_3d/scripts/`. No `node_modules`, no daemon, no port.

---

## 9. Decision

- **CI gate hardening (F-21, F-22-C/C14, F-23, C12/F-22-G, F-25, F-26, R15, R19, R20, R22, R23):**
  **GO** — at `8da3947` plus the two files in §7. Seven gate suites green, floor green at 96 cases,
  V1.5 8/8·95, V1.6 8/8·104, Track B 10 suites/120 checks/120 passed, and **11/11 mutations plus
  3/3 deletions turn red with a green baseline beside them**.
- **Full P0–P2 product acceptance:** **NO-GO** — four independent reasons:
  1. `/readyz` does not exist anywhere in the repo.
  2. **B2** Windows restart recovery is UNVERIFIED for `8da3947` (9/9 jobs `ubuntu-latest`; the two
     existing artifacts bind to `cefed66` and to module hashes, with no platform field).
  3. The engine HTTP surface (`paper-server.mjs`, `strategy-registry.mjs`) returns `BODY_TOO_LARGE`
     / 413 with **zero assertions**, and it is reachable behind auth. (The Hermes receiver's
     oversize path **is** covered — I drove it — so this is one surface, not two.)
  4. No real GitHub Actions run exists on the accepted SHA (R-1).
  Plus `service-auth.mjs` is unreachable, so no claim of service-to-service HMAC enforcement can be
  made, and four-agent identity is self-declared (R-9).

**Scope discipline.** My NO-GO is mine alone and is **not** averaged with Aegis's. He holds two
gate blockers (B1 provenance, B2 Windows) and **security blockers = 0**; I hold the four reasons
above and the same B2. Where we overlapped I re-derived his claims from source and report the two
places I had to correct: `REBOOT_RECOVERY.json` *is* commit-bound (to `cefed66`, not unbound), and
`service-auth.mjs` is *unreachable*, which is a weaker security finding than "unverified" but a
stronger documentation finding. I do **not** sign B1 (provenance) — that is Aegis's gate.

---

## 10. 更正块（append-only，2026-11-12，harbor2）

> 规则：本节**只增不改**。上文原句全部保留；被推翻的表述就地加 `superseded` 标记，正文原样留着，供后来者对照。
> **依据形态声明**：本节的依据是**提交 `00e08e3`** 上的源码 + 本机实测命令输出，**不是 CI run id** —— 因为截至本节撰写时尚无任何一次 GitHub Actions 运行落在候选 SHA 上（这正是上文 NO-GO 理由 4，R-1）。凡标 `LOCAL` 的行，**不得**被读作"CI 已确认"。

### 10.1 更正：「malformed / oversized payload」一行把两个 facade 混成一个表面

**原文（保留）**：`paper-server` **is reachable**（`server/trading_api.mjs` adapter，`127.0.0.1:4791`，**behind an authenticated gateway**）→ FAIL (unverified)。

**更正后**：这句话对其中**一个** facade 成立，对**另一个**不成立，必须拆开写。

| facade | 读体发生在鉴权之前还是之后 | 可达性 | 结论 |
|---|---|---|---|
| `server/trading_api.mjs` | **之前**。`handle()` 在 `try {}` 与 `ledger.readSession()` 之前就 `await readNodeBodyBytes(req)`（旧版为 `readBody`） | 绑 `127.0.0.1:4791`。**任何网页**都能用 `text/plain` 简单请求（免 CORS 预检）打这个环回端口 ⇒ 无需公网 | **pre-auth 无界读体**，攻击者只需让受害者访问一个页面 |
| `functions/api/jev/[[path]].js` | **之后**。`onRequest` 里先过三条红线段与 Basic auth，之后才进 `handleApi()` 读体 | 公网（Pages Function） | **已认证后**的无界读体；原文的 "behind an authenticated gateway" 只对这一条成立 |

⇒ 该行的结论由「一个表面 FAIL」更正为「**两个表面、两种性质**」；「pre-auth」一词只属于 `trading_api.mjs`，不得套用到公网侧。

依据：`LOCAL` @ `00e08e3`，`trading_api.mjs` 读体位置 vs `[[path]].js` 的 `onRequest` Basic auth 段。

### 10.2 更正：M1 的主缺陷是**正确性**，DoS 是附带；"永挂"要降一档

**原文（保留，散见 M1 上报条目）**：定性为「读路径无界 / pre-auth 无限缓冲」，并称客户端的 `await` **永不 settle、永挂**。

**更正后**：

1. **主缺陷是逐 chunk 解码，不是缓冲。** 旧实现是
   `let s = ""; req.on("data", (c) => { s += c; });`，而 `c` 是 **Buffer**，`+=` 对**每个分片各自** `toString()`。多字节字符跨分片即被改写。实测（`LOCAL`）：`你` 的 3 字节切成 2+1 → `len_chars=2`、回编码 **6 字节**、`U+FFFD,U+FFFD`、`lossless=false`；按同一机制整包实测 **`origBytes=40 → recvBytes=46`、`same=false`**。
   后果：① 任何含非 ASCII 的请求体（中文备注、emoji）在跨 TCP 分段时被**静默改写**，单测全绿、生产偶发 400；② 任何对 body 做 HMAC/签名的校验经此 facade **必错** —— 冻结引擎的 `service-auth.mjs` `signServiceBody` 正是对 rawBody 求 sha256，一旦接线就是 100% 失败，指纹为「偶发、不可复现、日志无线索」。故此项为 **P1 正确性缺陷**。
2. **DoS 描述降一档。** `LOCAL` 实测本机 Node **v22.22.2** 的 `http.createServer()` 默认值：`requestTimeout=300000`、`headersTimeout=60000`、`timeout=0`、`keepAliveTimeout=5000`，而 facade 的 `createServer` **未覆盖任何一项**。所以准确表述是「**上限 5 分钟的挂起 + 窗口内无界累积**」，**不是"永挂"**；socket 销毁后悬挂 promise 可回收。严重度不下调，因为可达性是「任何网页免预检打环回端口」。
3. **修复必须三项一次做完**：持续解码（累积 Buffer，末尾 `toString("utf8")` 一次）＋ 按**字节**（非字符）判上限 ＋ `error`/`close`/`aborted` 有出口。**并且** 413 必须**先有界排空再应答**：先 `res.end` 后关socket 会被 Node 自身的 `destroySoon` 在"请求体还有未读字节"时关掉，内核发 RST、客户端丢掉 413（`LOCAL` 实测：该顺序下 10 次里 5 次 `ECONNRESET`）。

依据：`LOCAL` @ `00e08e3` + 新增套件 `8-api-body-read`（见 §10.6）。

### 10.3 新增读数约定：本仓对**同一批引擎字节**用了三种哈希约定，报告必须标算法

`LOCAL` 实例：`paper-server.mjs` 同时是 `9ed13096c8…` 与 `f9ac6e1424…` —— **不是两份分叉的引擎，是同一个 52458 字节文件的两种摘要**：

| 出处 | 算法 | 该文件的值 |
|---|---|---|
| `cloud/vendor-manifest.json` 的 `git_blob_sha` | git blob sha1 | `9ed13096c801c6d5842a5ef5bc48247afc298bf7` |
| `apps/equity_collector_v45/src/engine-anchor.mjs` 的 `ENGINE_ANCHORS` | 逐文件 sha256 | `f9ac6e142446ef2aa8deb928f2b129eb2d42f4227d1bc22469191087312e4d74` |
| `scripts/v16_independent_verify.py` 的 `ENGINE_TREE_SHA256` | `git ls-tree -r HEAD` 原始行 `"\n".join`（无尾换行）后取 sha256 | 全树 `aec5d1b7cc26db50736d355e7ef14e066fae3f2c2a376d94637c9a977008a9ad`（15 文件） |

⇒ 本报告此后**每个 hash 位旁必须并写算法与所绑 SHA**。撰写本节时我本人差一次命令就把上表前两行当成"两份分叉的冻结引擎"报成 P1；`diff` 0 行、两处均 1106 行 / 52458 字节才拦住。**不标算法 = 给下一个审计者预埋一个假 P1。**

**但三行不是三条守护** —— 必须同读：只有第三行（`ENGINE_TREE_SHA256`）是 **CI 强制**的（`check3` 在 `scripts/v16_independent_verify.py`，被 workflow 调用）；前两行分别只在 `cloud-entry.mjs` 启动路径与 collector 本地测试里用到，**在 `.github/workflows/` 命中 0**（详见 §10.4）。"三个摘要都算对了"绝不等于"三条路都有守护"。

### 10.4 新增：`vendor` 身份校验在 CI 中**从不执行**，且只覆盖 6/15

- `verify-engine-vendor.mjs` 全仓唯一引用是 `apps/command_center_3d/cloud/cloud-entry.mjs:1`；而 `cloud-entry` 在 5 个 workflow 里 **0 命中** ⇒ 不是"fail-late"，是**永不触发**。
- `cloud/vendor-manifest.json` 只有 **6** 条 `git_blob_sha`（`paper-server` / `paper-ledger` / migrations `0001-0003` / `decimal`），而 `check3` 覆盖 **15** ⇒ **9 个文件没有任何 vendor 身份覆盖**：`ai-core`、`agent-orchestrator`、`service-auth`、`strategy-events`、`strategy-registry`、`verification`、`equity-snapshot`、migrations `0004`、`0005`。
- 该检查的两端（`vendor/` 与 `server/engine/`）**同在本仓、且字节相同**（`diff` 0 行）⇒ 它只能抓"单边误改"，**不能证明**与上游 `origin_commit 34b2e54c@yihui315/night-researcher-dash` 一致（该对象本仓不存在）。

⇒ 不可写作"vendor 校验 fail-late 但存在"；应写作「**在 CI 中从不执行，覆盖 6/15，且两端同源，故不能证明与上游一致**」。

### 10.5 新增：`v12_isolation_engine_test.mjs` 不得计入"隔离已测"

`:69-70` 的夹具注释原文：`// Same-origin as the constructed URL (http://localhost...) so both read (isSameOrigin) and write (writeOriginOk) authorization checks pass.` 即**门被当成前置条件**；全文零 `CROSS_ORIGIN` / 403 负例；且该文件名**全仓 0 引用**、不在任何 workflow —— 不是"没被 CI 跑"，是**没有登记**。

⇒ 一个通过了的测试，若它 (a) 不在任何 workflow、(b) 全仓零引用、(c) 夹具以注释声明"我让门通过了"，那它不是"反例缺失"，而是**缺陷未被测试的记录**。把它计入"隔离已测"是三重计数最隐蔽的形式。

### 10.6 本次落地（**工作树未提交**，故本节不含 commit SHA）

> `git status --porcelain` 在撰写时显示下列改动均为未提交状态。**在获得 commit SHA 之前，任何读者都不得把本节读作"已入库"。** 提交后应在本节追加一行 `commit=<SHA>`。
>
> **`commit=4859e5c`** —— 已由主理人作为单一提交人提交到 `feature/jev-v2-ops-integration`，父提交 `00e08e3`，7 文件 / 830 insertions / 24 deletions。本节以下内容自此为**已入库事实**，不再是工作树状态。两个报告文件（本文件与 `reports/JEV_REAL_TRADING_SECURITY.md`）**不在该提交内**，仍为工作树改动。

| 文件 | 改动 |
|---|---|
| `apps/command_center_3d/server/lib/http-body.mjs` | **新增**。字节安全读取器：`readNodeBodyBytes`（累积 Buffer、末尾单次解码、字节上限、`error`/`close`/`aborted` 出口）＋ `readFetchBody`（声明长度预检 + 流式字节上限两段）＋ `drainThenRespond`（先排空再应答）＋ 两个错误类型 |
| `apps/command_center_3d/server/trading_api.mjs` | 删掉 `readBody`；改用 `readNodeBodyBytes`；413 走 `sendBodyError`（**先排空再应答**）；原始字节透传给 `buildEngineRequest`；启动横幅打印实际上限 |
| `apps/command_center_3d/functions/api/jev/[[path]].js` | `request.text()` → `readFetchBody`；5 处 `buildEngineRequest` 透传原始字节 |
| `apps/command_center_3d/server/tests/http-body-read.mjs` | **新增**。17 个用例，含**永久负控**（用例 4 跑修复前实现并断言它损坏 body，保证断言不会失去牙齿） |
| `.github/workflows/ci.yml` | 新增第 8 个 gate 套件（`test -f` 硬失败 + `node … \| tee`） |
| `apps/command_center_3d/scripts/check-no-fake-live.mjs` | `GATE_SUITES` 7 → 8 |
| `apps/command_center_3d/scripts/gate-assertion-floor.mjs` | 新增 `8-api-body-read expect=17` |

**floor old → new**：`GATE_FLOOR_OK suites=7 total_cases=97 exit=0` → **`GATE_FLOOR_OK suites=8 total_cases=114 exit=0`**（`114 = 97 + 17`，可加性校验成立）。§1 的 `7+24+10+17+12+14+13 = 97` 依旧为真，但只属于**7 套件时代**，不得与本行的 114 混读。

> ⚠ **`114` 是本次实测值，不是常量。** 本报告全文不陈述"当前"套件数/用例数；任何引用都必须落在某个具体捕获块或其 SHA 上。见 §10.7。

**证伪（删掉修复 ⇒ 变红）** —— 两次都是"只回退一个函数、其余从修复版 re-export"，因此红点是可归因的（`LOCAL`，均为 17 用例套件）：

```
# 证伪一：只回退 readNodeBodyBytes（逐 chunk 解码、无上限、无 error/close 出口）
FAIL  2 node: a multi-byte character split across two chunks survives (the original bug): bytes differ after a mid-character split
FAIL  3 node: one byte per chunk is still byte-identical: 1-byte chunks corrupted the body (65 != 33)
FAIL  4 node: NEGATIVE CONTROL … : fixed reader must be lossless on the input the legacy reader corrupts
FAIL  5 node: a body over the cap is rejected with BODY_TOO_LARGE/413: expected BodyTooLargeError, got null
FAIL  6 node: the cap counts BYTES, not characters … : a cap measured in characters would let a 60-byte / 20-character body through
FAIL  7 node: a client that goes away mid-body settles instead of hanging: reader never settled after the client aborted (the original code hung here)
FAIL 16 socket: a multi-byte body survives real TCP, end to end: body differed over a real socket (39 != 33)
FAIL 17 socket: an oversized body gets a real 413 with a parseable code: expected a 413 status line, got: HTTP/1.1 200 OK
RESULT PASS=9 FAIL=8   → exit 1

# 证伪二：只回退 readFetchBody 的两处字节上限（流式读取与单次解码保持不变）
FAIL 10 fetch: a chunked body over the cap is rejected while streaming (no content-length declared): expected BodyTooLargeError, got null
FAIL 11 fetch: a declared content-length over the cap is rejected BEFORE any read: expected BodyTooLargeError, got null
FAIL 12 fetch: the cap counts BYTES, not characters … : a cap measured in characters would let a 60-byte / 20-character body through
RESULT PASS=14 FAIL=3   → exit 1
```

证伪二只红 3 条、且**恰好是上限相关的那 3 条**，这是有意的设计：Workers 侧的缺陷**只是缺上限**，解码本来就是一次性的（`request.text()` 不信 chunk），所以"回退"后保真用例应当继续绿。若它把保真用例也弄红，就说明证伪模块实现错了、证明不了任何东西。

还原后用 sha256 校验字节回归（`f859bcc8b890d7ac17efd1dc17d05c9c063e73d94d9775932f304b22849ac187`），并连跑 12 次全绿（首次实现存在"先应答后排空"的竞态，10 次里 5 次 `ECONNRESET`；改为"先排空再应答"后 12/12 绿，该竞态已在上文 §10.2 第 3 点固化为要求）。

**冻结树未被触碰**：`git ls-tree -r HEAD -- apps/command_center_3d/server/engine/` 复算仍为 `aec5d1b7…`、`n=15`，与 `check3` 锚点逐字符一致；`git status --porcelain` 中 `server/engine/` 命中数为 **0**。新模块落在 `server/lib/`（非冻结），不触 `check3`、不逼人重钉 15 文件锚。

### 10.7 读数约定：本报告**不陈述"当前"的 `suites` / `total_cases`

本报告正文多处出现 `97`、`721/729`、`754/746` 一类聚合值与套件数（`:51 :70 :72 :225 :312 :324 :327 :396` 等）。**这些数字在它们被写下的那一刻是真的，写完之后每一次新增守卫都会让它们过期。** 逐处加 `superseded` 标记只会制造"有的标了、有的没标"的分裂状态，比统一不标更难读。故统一改为：

> **本报告全文不陈述"当前"的 `suites` / `total_cases` 值。** 仅两处有效：
> 1. **历史锚**：`13882c4` = 7 套件 / 97 块；
> 2. **当前值**：以 CI 运行时**自己打印**的那一行为准 —— `GATE_FLOOR_OK suites=N total_cases=M exit=0`（`gate-assertion-floor.mjs` 现算，非硬编码）。
>
> 任何形如"现在是 114 / 是 137 / 是 138"的转述**都不是**本报告的声明，属引用者自担。提交信息与对话里同样不应把该值当常量引用。

理由（与本报告 §7.3 同一条）：**复制聚合值必然过期**。本轮先是 97 → 114（新增 `8-api-body-read`），接着会 114 → 137（`6-supervisor` 14→37 的账本用例），再接着 `7-no-fake-live` 13→14（Z6 落地）。若每步都把新数写进文档，下一次更正只会再制造一个"文档改掉自己的锚"。

### 10.8 守卫升级：floor 表与 `GATE_SUITES` 的同步，从「数量」改为「集合」

**缺陷（由 harbor 与 aegis 独立指出，落在我这次提交的射程内）**：`check-no-fake-live.mjs` 的 Y2 里，`GATE_SUITES`（ci.yml 侧）已是 comment-proof 的**集合**比较，而 30 行之后对 floor 表的校验**只比 `slug:` 条数**：

```js
const floorSlugs = (floorSrc.match(/^\s{4}slug:/s*"[^"]+"/gm) || []).length;
assert.equal(floorSlugs, GATE_SUITES.length, ...);
```

于是「ci.yml + `GATE_SUITES` 加 `9-foo`、floor 表删 `1-p0-contracts`」⇒ 条数仍相等 ⇒ **绿**，而 `1-p0-contracts` 从此不再被核用例数。这正是该块注释声称要防的"加而不登记"，上一层形态。

**为什么它不红比红更糟**：本轮两处刚好都被我同时加过（`8-api-body-read`），`8 == 8` 是**巧合为绿**，不是判据为绿。红会被看见，巧合不会。

**修法**：floor 的 `label:` 里取出 `.mjs` 文件名，与 `GATE_SUITES` 的 basename 做 **`Set` 相等**（与 Y2 的 ci.yml 集合比较同一套写法），并先断言 basename 唯一——否则两个套件映射到一条 floor 条目，映射就不再是单射。

**变异证据（`LOCAL`，diagnostic 非发布证据）**：把 floor 表里 `1-p0-contracts` 的 label 换成不存在的文件（**条数不变，仍 8**）：

```
$ grep -c "slug:" apps/command_center_3d/scripts/gate-assertion-floor.mjs
8                                   ← 旧判据只看这个，仍然通过

$ node apps/command_center_3d/scripts/check-no-fake-live.mjs
AssertionError [ERR_ASSERTION]: gate-assertion-floor.mjs drifted from ci.yml/GATE_SUITES:
  missing from floor(1): ["day14-p0-contracts.mjs"]
  unexpected in floor(1): ["decoy-not-a-suite.mjs"]
exit 1
```

还原后 sha256 逐步核对为字节同一（`2cce1864824f2cd07c8249b2e5746877089a7503e92e14e4cc3d48cbc73f8c83`）。这组对照是本节的关键：**同一个变异，旧判据绿、新判据红。**

### 10.9 已知分叉：两份验收报告不同源，按任务书路径取到的是未更正版

```
febc575d…  D:/jev/artifacts/acceptance/P0_P2_ACCEPTANCE_REPORT.md          ← 仍是旧版，无 §10
<本文件>   artifacts/release/p0_p2_acceptance/P0_P2_ACCEPTANCE_REPORT.md    ← 已含 §10
```

`D:/jev/...` 是**任务书点名的路径**（本报告 `:416` 也这么记着），但它现在落后一整个 §10 更正块，而 §10.2 更正的恰恰是"永挂"这种最容易被下一份文档引用的定性。**谁按任务书路径去取，拿到的是未更正版。**

处置**不在本报告权限内**，需由主理人裁决二选一：① 把 `D:/jev` 那份同步为候选版；② 在 `D:/jev` 那份顶部加一行"本副本已过期，以候选路径为准"。我**没有**跨分支去改它——两份是不同分支的产物，我这边单方面同步等于替别人做了一次跨分支复制，且下次任何一方再改又会分叉。此条在裁决前对读者是**活跃风险**，不是已解决问题。

**已裁决（主理人）：选 ②，且要求「机器可检测」，不得只写一段叙述。** 理由（比我的更硬）：①（同步）会把一份**历史证据副本**变成必须永远对齐的活体，而它的价值恰恰在于"当时发布出去的就是这份字节"。

已执行：在 `D:/jev/artifacts/acceptance/P0_P2_ACCEPTANCE_REPORT.md` 顶部插入标记块，含两个机器可解析键——`superseded_by_path:` 与 `superseded_by_sha256:`（权威副本字节的 sha256）——**不含任何会滚动的聚合值**（不写套件数、不写 `total_cases`，与 §10.7 同纪律）。

该标记的**能力边界**必须写明，否则下一轮会被当成"已绑定"：

> 标记绑定的是**某一时刻权威副本的字节**，不是"权威副本"这个概念。权威副本此后**任何一次编辑都会让该 sha256 失真**——这与"文档不能引用自身摘要"同源。故：**权威副本每改一次，必须重算并重写该标记**；校验者若发现不匹配，先判断"权威副本前进了"还是"过期副本被篡改"，不得直接断言后者。

### 10.10 事故留痕：变异体曾被写到被测路径上（已恢复，未进提交）

**事实**（由主理人发现并修复，我认领根因）：我做证伪时把变异模块**直接覆盖到被测路径** `apps/command_center_3d/server/lib/http-body.mjs` 上运行，随后还原。中间有一个瞬间（`00:03:50`）该路径上是 F2 变异体：

```js
export { readNodeBodyBytes, … } from "file:///C:/Users/Administrator/AppData/Local/Temp/jcc-falsify/http-body.FIXED.20261112-000240.mjs";
export async function readFetchBody(request) { /* 无上限版本 */ }
```

该文件当时**未跟踪**，`git checkout` 救不回来。若会话在此中断，仓库将永久留下一个 re-export 到 `%TEMP%` 的假模块：任何机器上跑测试都会红，而报错指向 `AppData\Local\Temp`，排查成本极高。

**未进提交，已独立核验**（`LOCAL` @ `4859e5c`）：

```
$ git show HEAD:apps/command_center_3d/server/lib/http-body.mjs | sha256sum
f859bcc8b890d7ac17efd1dc17d05c9c063e73d94d9775932f304b22849ac187
$ git show HEAD:apps/command_center_3d/server/lib/http-body.mjs | grep -c "file:///C:/Users\|FALSIFICATION"
0
```

**根因**：我把"变异"和"被测对象"放在同一个文件路径上，只靠"跑完再 `cp` 回来"这个**人为步骤**还原。人为步骤不是机制——它在我自己盯着的时候有效，在中断/并发/第二个写者时无效。

**规矩（硬执行）**：变异体**不得写到被测路径**。变异写在 temp 或 `__mutants__/`，通过独立入口 `import` 注入；确实必须替换原文件时，`try/finally` + `trap` 双保险还原，并在报告里附**还原后 sha256 回显**。本节引用的 `f859bcc8…` 就是那条回显的产物——但它证明的是"最终状态对"，**不能**证明"过程中没出错"，两者不可互相替代。
