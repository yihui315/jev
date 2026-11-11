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
| **F-21** | The P0–P2 gate was step #10. On Run `42968141252` a red unit test made GitHub report it **`skipped`** — the gate vanished exactly when needed. | Gate is now the **first `run` step** of `build-and-test`, before `npm ci`, teeing to `${{ runner.temp }}/jcc-gate`. Rationale in YAML comments. | Y2 asserts `gate index < Install index`; injecting an `Install` step before the gate turns it red. |
| **F-22-C** | `ci.yml` `pull_request.branches` could not match `feature/**`, so PRs from the integration branch ran zero checks. | `+ "feature/**"` (additive only). | Y2 branch guard. |
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
| malformed / oversized payload | **must be split by surface.** `integrations/_common/jcc_receiver.py:36,148` → `payload_too_large`, asserted at `test_p2_integration_smoke.py:295` and reproduced by my harness → **PASS**. `apps/command_center_3d/server/engine/_lib/paper-server.mjs:63` (`BODY_TOO_LARGE`/413) and `strategy-registry.mjs:255` → **0 assertions**, and `paper-server` **is reachable** (`server/trading_api.mjs` adapter, `127.0.0.1:4791`, behind an authenticated gateway) → **FAIL (unverified)** on the engine surface. `service-auth.mjs:119` → unreachable (§ above). | **PASS** on the Hermes receiver / **FAIL (unverified)** on the engine HTTP surface |
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
> 2. content hashes of the files this report *describes* (the report does not modify them, so
>    these are stable), identical at `9e883f3` and at the HEAD this report was finalised on:
>
> | file | sha256 |
> |---|---|
> | `apps/command_center_3d/scripts/check-no-fake-live.mjs` | `4223ef3d3c111c57e5efe1e693303c5a9f33dba25e14232790541ac08f813da9` |
> | `apps/command_center_3d/scripts/gate-assertion-floor.mjs` | `5e06954909acfaa9b5ff014ded93717601a6fef533ca2e0750022efb6fede254` |
> | `apps/command_center_3d/server/ai/tests/e2e_real_engine.mjs` | `442bf00c154d7eb9a027395a92a10f0549671a40feaaeb6b9ad1a906b52fcf1e` |
>
> Point 2 is the load-bearing one for this report: it proves the `total_cases=97` capture was
> produced by the same code at both SHAs, so no re-run is needed to trust it across doc-only
> commits. Verify with
> `git show 9e883f3:<path> | sha256sum` and `sha256sum <path>`.

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
