# FORGE — Phase B (Server-Side Authz) G-1 / G-2 Hardening Handoff

**Worktree:** `D:/jev_worktrees/v2ops`
**Branch:** `feature/jev-v2-ops-integration`
**Node:** v22
**Date:** 2026-11-13
**Scope:** Implementation + verification of Phase B G-1 (cross-instance replay / no double-spend) and G-2 (shared identity revocation) hardening. Local mock only — no network, no real IdP, no real D1, no real WORM anchor.

---

## (a) Duplicate-export fix (`server/ai/phase-b/integration.mjs`)

**Root cause (per mission):** `runCoreGateWithD1State` (declared `export function` at line ~140) and `safeCoreEnforceWithD1` (declared `export function` at line ~155) were *also* listed in the trailing `export { ... }` block (line ~177). A binding may be exported only once, so the module failed to parse:

```
SyntaxError: Duplicate export of 'runCoreGateWithD1State'
```

**Before (defective trailing block):**
```js
export {
  buildTrustedIdentity, IdentityRevocationStore, SharedIdentityRevocationStore, IDENTITY_CODES,
  issueRoleBoundGrant, verifyRoleBoundGrant, ROLE_CODES, ROLES,
  AuthorizationLedger, D1BackedGateState, LedgerReplayError,
  WormAuditAnchor, verifyAnchorOnStartup, ANCHOR_CODES,
  evaluateRiskGate, combineRiskAndHuman,
  AppendOnlyAuditLog, safeCoreEnforceWithD1, runCoreGateWithD1State,   // <-- duplicate
};
```

**After (corrected trailing block):**
```js
export {
  buildTrustedIdentity, IdentityRevocationStore, SharedIdentityRevocationStore, IDENTITY_CODES,
  issueRoleBoundGrant, verifyRoleBoundGrant, ROLE_CODES, ROLES,
  AuthorizationLedger, D1BackedGateState, LedgerReplayError,
  WormAuditAnchor, verifyAnchorOnStartup, ANCHOR_CODES,
  evaluateRiskGate, combineRiskAndHuman,
  AppendOnlyAuditLog,
};
```

The two functions remain exported via their `export function` declarations; the only change is removing them from the re-export list. All other re-exports of imported bindings are preserved.

**`node --check` result:** `node --check apps/command_center_3d/server/ai/phase-b/integration.mjs` → **CHECK_OK** (exit 0, no SyntaxError).

---

## (a2) Additional fix required for the suite to pass — `safeCoreEnforceWithD1` denied-result masking

While finalizing G-1 it was found that the suite could not reach green purely via the export fix: `safeCoreEnforceWithD1` returned `{ ok: true, result }` **unconditionally**, even when the wrapped core gate *denied* the request. The core gate (`verifyPublishGrant`, `human-publish-gate.mjs:415`) has a same-instance pre-check `state.consumedNonces.has(nonce)` that returns a REPLAY *denial* (not a thrown `LedgerReplayError`) before the adapter's `add()` is reached. That denial was being reported to callers as a successful enforcement, defeating the fail-closed guarantee.

**Fix:** in `safeCoreEnforceWithD1`, after `await coreEnforce(args)`, if `result.ok === false`, return `{ ok: false, code: result.code, detail: result.detail, result }`. The thrown-`LedgerReplayError` path (cross-instance TOCTOU) is preserved; this adds the same-instance returned-denial path. This is the wrapper-behavior correction that makes the G-1 invariants provable and is consistent with both the main suite and the independent AEgis adversarial probe.

**Related test fix:** `phase-b-adversarial.test.mjs` called the (async) `safeCoreEnforceWithD1` without `await`, so `first.ok` was read off a Promise (`undefined`). Added `await` to those two call sites. This was a pre-existing test-authoring bug, not a regression from the wrapper fix.

---

## (b) Phase B test results

**Primary suite — `node apps/command_center_3d/scripts/tests/phase-b.test.mjs`** (exit 0):
```
# tests 39
# suites 6
# pass 39
# fail 0
# cancelled 0
# skipped 0
# todo 0
# duration_ms 42.6267
```
Note: the mission predicted `PASS=35 FAIL=0`; the actual committed suite contains **39** tests (the D1 ledger suite includes two extra G-1 concurrency/TOCTOU tests and the count reflects the richer suite). All 39 pass. Two of these — "G-1: safeCoreEnforceWithD1 converts the REPLAY throw into a clean REPLAY denial" and "G-1: concurrent core enforcements of the SAME nonce -> exactly one ALLOW, the other REPLAY (TOCTOU closed)" — were the G-1 tests that failed before the wrapper fix and now pass.

**Independent AEgis probe — `node apps/command_center_3d/scripts/tests/phase-b-adversarial.test.mjs`** (exit 0):
```
# tests 17
# suites 7
# pass 17
# fail 0
```

---

## (c) G-1 proof result (`evidence/forge_proof.mjs`, exit 0)

```
=== G-1 PROOF ===
PASS - G-1 cross-instance: 2nd add of same nonce throws LedgerReplayError
PASS - G-1 safeCoreEnforceWithD1: first consume ok
PASS - G-1 safeCoreEnforceWithD1: 2nd returns ok:false + code:GATE_CODES.REPLAY
PASS - G-1 200-way: all 200 consumed ok
PASS - G-1 200-way: 0 collisions
PASS - G-1 200-way: ledger count == 200
```

- Two `D1BackedGateState` instances share ONE `AuthorizationLedger`/`D1SharedStore`. Consuming the same nonce on the second instance throws `LedgerReplayError` (no silent double-spend).
- `safeCoreEnforceWithD1` converts that path into a clean `{ ok: false, code: GATE_CODES.REPLAY }`.
- **200-way parallel distinct-nonce consumption on one shared ledger:** all 200 succeed, **0 collisions**, and the durable ledger count is exactly **200** — proving atomic, collision-free consumption under concurrency.

---

## (d) G-2 proof result (`evidence/forge_proof.mjs`, exit 0)

```
=== G-2 PROOF ===
PASS - G-2 storeB sees u1 revoked (true)
PASS - G-2 storeB sees u2 not revoked (false)
```

Two `SharedIdentityRevocationStore` instances share ONE `Map`. `storeA.revokeSub("u1")` makes `storeB.isRevoked({ sub: "u1" }) === true` and `storeB.isRevoked({ sub: "u2" }) === false` — revocation is visible across instances via the shared backing store.

---

## (e) MOCK-ONLY — NOT a production security PASS

This verification is performed entirely against **local mocks**:

- The "D1" ledger is an in-memory `D1SharedStore` (a `Map`), NOT a real Cloudflare D1 binding.
- The IdP is a Mock HMAC IdP (`buildTrustedIdentity`), NOT a real identity provider.
- The WORM audit anchor (`WormAuditAnchor`) is in-process append-only state, NOT an external/durable WORM store.
- No network calls, no paid APIs, no real credentials were used.

**Therefore this is a local logic/invariant proof, NOT a production security acceptance.** The following external-authorization items remain unmet and MUST be satisfied before any production claim:

1. **Real IdP integration** — server-trusted per-user identity must be backed by a real identity provider with real MFA/AMR.
2. **Real Cloudflare D1 binding** — the shared, durable, UNIQUE-constrained store must be a real D1 instance (with isolated/locked-down access), not the in-memory simulation.
3. **External WORM anchor** — F-19's audit anchor must be a real external WORM store with independent verification; the startup-recovery check must run against that real anchor.
4. Independent third-party penetration testing and a real production threat model review are still required.

The Phase A closure artifacts (`gate-assertion-floor.mjs`, `P0_P2_ACCEPTANCE_REPORT.md`, `reports/JEV_REAL_TRADING_SECURITY.md`, `reports/evidence/v16/independent_verify.json`) were **not modified** and remain the authoritative production-gate references.

---

## Files changed (staged in this commit)

- `apps/command_center_3d/server/ai/phase-b/integration.mjs` — removed duplicate export identifiers; fixed `safeCoreEnforceWithD1` to surface core denials as `ok:false`.
- `apps/command_center_3d/scripts/tests/phase-b-adversarial.test.mjs` — added `await` to `safeCoreEnforceWithD1` call sites (pre-existing test bug).
- `apps/command_center_3d/server/ai/phase-b/evidence/FORGE_B_HANDOFF.md` — this document.
- `apps/command_center_3d/server/ai/phase-b/evidence/forge_proof.mjs` — reproducible G-1/G-2 proof (kept untracked / not part of the committed proof surface; re-runnable locally).

**Protected files left untouched:** `apps/command_center_3d/scripts/gate-assertion-floor.mjs`, `artifacts/release/p0_p2_acceptance/P0_P2_ACCEPTANCE_REPORT.md`, `reports/JEV_REAL_TRADING_SECURITY.md`, `reports/evidence/v16/independent_verify.json`.
