# AEGIS — Phase B Independent Re-Audit (B-AUDIT)

**Auditor:** aegis (independent; did NOT trust implementer self-report)
**Worktree:** `D:/jev_worktrees/v2ops`  **Branch:** `feature/jev-v2-ops-integration`
**HEAD base:** `e1131a2208bcf772e189317858769e8dc7e0c71d` (e1131a2)
**Local forge commit:** `f4ef082` (NOT pushed, protected files untouched)
**Node:** v22  **Date:** 2026-11-13
**Method:** fresh probes written by aegis against the REAL modules (not `forge_proof.mjs`).

---

## 1. Regression suites — independently re-run

| Suite | Expected | Result |
|---|---|---|
| `human-publish-gate.test.mjs` | `# pass 32 # fail 0` | **RESULT PASS=32 FAIL=0** |
| `phase-b.test.mjs` | `pass 39 fail 0` | **pass 39 fail 0** |
| `phase-b-adversarial.test.mjs` | `pass 17 fail 0` | **pass 17 fail 0** |

All three green. (The 39/17 suites were part of the forge commit; re-run here independently and confirmed green.)

---

## 2. Fresh adversarial probe — `aegis_reprobe.mjs`

Wrote and ran an independent probe (`apps/command_center_3d/server/ai/phase-b/evidence/aegis_reprobe.mjs`). Result: **9 pass / 0 fail**.

### G-1: D1 cross-instance nonce anti-replay (was "silently swallowed REPLAY")

- **G-1a** — Two `D1BackedGateState` sharing ONE `AuthorizationLedger` (shared store); consume the SAME nonce twice.
  `PASS` — second `consumedNonces.add()` **throws** `LedgerReplayError` (not silent).
  *Confirmed in `ledger.mjs:199-204`: `add()` throws when `consumeNonce` returns `code === LEDGER_CODES.REPLAY`.*

- **G-1b** — `safeCoreEnforceWithD1` wrapping a core enforce:
  - core **RETURNS** a REPLAY denial → `PASS` wrapper returns `ok:false, code=GRANT_REPLAYED` (NOT masked as `ok:true`).
  - core **THROWS** `LedgerReplayError` (cross-instance TOCTOU) → `PASS` wrapper returns `ok:false, code=GRANT_REPLAYED`.
  *Confirmed in `integration.mjs:169-178`: returned `ok:false` is surfaced; caught `LedgerReplayError` → `REPLAY`. No denial is re-mapped to success.*

- **G-1c** — 200 parallel DISTINCT-nonce consumes on one shared ledger.
  `PASS` — all 200 `ok`, 0 replays/collisions, ledger durable size == 200.

**G-1 status: FIXED** (independently confirmed).

### G-2: cross-instance identity revocation (was per-instance in-memory)

- **G-2** — Two `SharedIdentityRevocationStore` sharing ONE `Map`; `storeA.revokeSub("u1")`.
  `PASS` — `storeB.isRevoked({sub:"u1"}) === true` and `storeB.isRevoked({sub:"u2"}) === false`.
  *Confirmed in `identity.mjs:164-185`: store is a shared `Map`, revocation visible across instances.*

**G-2 status: FIXED** (independently confirmed).

---

## 3. Negative controls — fail-closed integrity of production `phaseBEnforce`

All executed against the native `phaseBEnforce` path (NOT the demo adapter):

- **MFA bypass** — executor token WITHOUT `amr` containing MFA → `PASS`, `ok:false`, `IDENTITY_MFA_NOT_SATISFIED`.
- **Role self-approval** — grant with `approverId === executorId` (validly signed) → `PASS`, `ok:false`, `ACTOR_SEPARATION_VIOLATION`.
- **Risk override** — `riskState` breached (drawdownPct 99 > 25) with otherwise-valid grant → `PASS`, `ok:false`, `PHASEB_RISK_DENIED`. Human grant did NOT override risk.
- **F-19 ordering** — on an ALLOWED flow, the WORM anchor tip equals the LAST audit entry hash, which is the just-appended `PHASEB_ALLOWED` record; anchor is computed AFTER the append. `PASS` (confirmed `integration.mjs:102-113` and `audit-anchor.mjs` fail-closed on mismatch/unreadable/missing/rollback).

All negative controls PASS.

---

## 4. Source review — wrapper did NOT weaken fail-closed guarantees

- `safeCoreEnforceWithD1` (`integration.mjs:155-179`): converts thrown `LedgerReplayError` → `{ok:false,REPLAY}`; converts any returned `ok:false` → `{ok:false}` (no masking); returns `ok:true` ONLY when the core genuinely returned `ok:true`. It is DEMONSTRATION-ONLY; the production path is `phaseBEnforce`.
- `phaseBEnforce` (`integration.mjs:67-116`): order is identity(MFA) → independent risk gate → role-bound grant verify + D1 atomic nonce consume → F-19 anchor. A risk breach denies regardless of the human grant (F-14/F-15). No denial path returns `ok:true`.
- `D1BackedGateState.add()` throws on REPLAY — fail-closed cross-instance.
- `AuthorizationLedger.consumeNonce` is a single atomic insert with UNIQUE(nonce) — no TOCTOU.
- `SharedIdentityRevocationStore` is shared-Map backed — cross-instance visible.
- `verifyAnchorOnStartup` / `WormAuditAnchor` are fail-closed and append-only.

No evidence the forge change weakened any fail-closed property.

---

## 5. Protected-file integrity

`git show --stat f4ef082` touched only 3 files: `phase-b-adversarial.test.mjs` (new), `evidence/FORGE_B_HANDOFF.md` (new), `integration.mjs` (new). The 4 protected files remain pre-existing ` M ` (Phase A closure) and were NOT modified by me or by the forge commit:

- `apps/command_center_3d/scripts/gate-assertion-floor.mjs`
- `artifacts/release/p0_p2_acceptance/P0_P2_ACCEPTANCE_REPORT.md`
- `reports/JEV_REAL_TRADING_SECURITY.md`
- `reports/evidence/v16/independent_verify.json`

---

## 6. Final verdict

**GO (mock-layer)** — *MOCK-ONLY.*

G-1 and G-2 are independently confirmed fixed, all three regression suites are green, and every negative control passes. The Phase B server-side authz logic is correct and fail-closed **under the local mock harness**.

### Standing caveats — remaining external-authorization gaps that BLOCK any production PASS

1. **Real IdP with real MFA/AMR** — current `identity.mjs` is a mock HMAC IdP; production requires a real OIDC/SAML provider + MFA enforcement.
2. **Real Cloudflare D1 binding with UNIQUE constraint** — `D1SharedStore` is an in-memory Map; production needs a real D1 instance with `UNIQUE(nonce)` enforced at the DB layer (not just in JS).
3. **External durable WORM anchor for F-19** — `WormAuditAnchor` is a local mock; production needs a real external RFC3161/append-only anchor with cold read + recovery drill.
4. **Third-party pentest** — independent adversarial validation by an external pentest team is still required before production sign-off.

> No production security claim is made. This GO is scoped strictly to the mock-layer correctness and fail-closed behavior verified above.

---

## Artifacts produced by aegis (untracked — per mission, not pushed)
- `apps/command_center_3d/server/ai/phase-b/evidence/aegis_reprobe.mjs` (independent probe)
- `apps/command_center_3d/server/ai/phase-b/evidence/AEGIS_B_AUDIT.md` (this report)

Neither was committed; both left untracked on the feature branch.
