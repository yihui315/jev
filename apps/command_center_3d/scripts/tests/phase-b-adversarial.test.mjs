// JEV Phase B — Aegis adversarial probe battery.
//
// Re-runnable by Aegis to confirm the two CONDITIONAL-GO gaps (G-1 adapter
// REPLAY swallow, G-2 per-instance revocation) are closed and nothing regressed.
// Local mocks only; no production PASS claim. Additive: does not modify the core
// gate or the 32 reverse tests.
//
// Probes: 50- & 200-way nonce race, MFA bypass, risk-override, F-19 mismatch,
// cross-instance replay, cross-instance revocation, G-1 closed.

import assert from "node:assert/strict";
import { test, describe } from "node:test";
import { createHash } from "node:crypto";

import { makeServerActor, enforceHumanPublishGate, issuePublishGrant, AppendOnlyAuditLog, GATE_CODES, assertServerActor } from "../../server/ai/human-publish-gate.mjs";
import {
  issueIdToken, verifyIdToken, buildTrustedIdentity, SharedIdentityRevocationStore, IDENTITY_CODES,
} from "../../server/ai/phase-b/identity.mjs";
import {
  ROLES, issueRoleBoundGrant, verifyRoleBoundGrant, ROLE_CODES, mintActorToken,
} from "../../server/ai/phase-b/roles.mjs";
import {
  D1SharedStore, AuthorizationLedger, D1BackedGateState, LedgerReplayError, LEDGER_CODES,
} from "../../server/ai/phase-b/ledger.mjs";
import { WormAuditAnchor, verifyAnchorOnStartup, ANCHOR_CODES } from "../../server/ai/phase-b/audit-anchor.mjs";
import {
  evaluateRiskGate, combineRiskAndHuman, RISK_GATE_CODES,
} from "../../server/ai/phase-b/risk-gate-b.mjs";
import {
  phaseBEnforce, phaseBIssueGrant, safeCoreEnforceWithD1,
} from "../../server/ai/phase-b/integration.mjs";

const T0 = 1800000000000;
const IDP_SECRET = "i".repeat(32);
const GATE_SECRET = "g".repeat(32);
const H = (s) => createHash("sha256").update(s, "utf8").digest("hex");
const HASH_A = H("strategy-content-A");
const ACTION = "STRATEGY_PROMOTION";

// ---------------------------------------------------------------------------
describe("AEgis probe: nonce race (50- and 200-way, atomic, cross-instance)", () => {
  async function race(N) {
    const store = new D1SharedStore();
    const ledger = new AuthorizationLedger(store);
    const results = await Promise.all(
      Array.from({ length: N }, (_, i) =>
        ledger.consumeNonce({ nonce: "race-n", grantId: "g" + i, actorHash: "h", strategyId: "S", nowMs: T0 })),
    );
    return results;
  }

  test("50-way concurrent same-nonce consume -> exactly 1 CONSUMED", async () => {
    const r = await race(50);
    assert.equal(r.filter((x) => x.ok).length, 1, "exactly one winner");
    assert.equal(r.filter((x) => !x.ok && x.code === LEDGER_CODES.REPLAY).length, 49);
  });

  test("200-way concurrent same-nonce consume -> exactly 1 CONSUMED", async () => {
    const r = await race(200);
    assert.equal(r.filter((x) => x.ok).length, 1, "exactly one winner");
    assert.equal(r.filter((x) => !x.ok && x.code === LEDGER_CODES.REPLAY).length, 199);
  });
});

// ---------------------------------------------------------------------------
describe("AEgis probe: MFA bypass attempts all denied", () => {
  const validTok = issueIdToken({ sub: "alice", issuerSecret: IDP_SECRET, nowMs: T0 });

  test("token with no MFA in amr is rejected", () => {
    const tok = issueIdToken({ sub: "alice", amr: ["pwd"], issuerSecret: IDP_SECRET, nowMs: T0 });
    const v = verifyIdToken(tok, { issuerSecret: IDP_SECRET, nowMs: T0 });
    assert.equal(v.ok, false);
    assert.equal(v.code, IDENTITY_CODES.MFA_NOT_SATISFIED);
  });

  test("tampered signature is rejected", () => {
    const [h, p, s] = validTok.split(".");
    const bad = h + "." + p + "." + (s.endsWith("a") ? "b" : "a") + "x";
    const v = verifyIdToken(bad, { issuerSecret: IDP_SECRET, nowMs: T0 });
    assert.equal(v.ok, false);
    assert.equal(v.code, IDENTITY_CODES.TOKEN_INVALID_SIGNATURE);
  });

  test("expired token is rejected", () => {
    const tok = issueIdToken({ sub: "alice", issuerSecret: IDP_SECRET, nowMs: T0, ttlMs: 1000 });
    const v = verifyIdToken(tok, { issuerSecret: IDP_SECRET, nowMs: T0 + 1001 });
    assert.equal(v.ok, false);
    assert.equal(v.code, IDENTITY_CODES.TOKEN_EXPIRED);
  });

  test("a client-supplied plain actor can never be trusted", () => {
    const forged = { userId: "attacker", authMethod: "IDP_OIDC_MFA", authenticatedAtMs: T0, identityHash: H("attacker|IDP_OIDC_MFA"), source: "SERVER_AUTH_LAYER" };
    const id = buildTrustedIdentity({ idToken: validTok, idpSecret: IDP_SECRET, nowMs: T0 });
    assert.equal(id.ok, true);
    // The forged object lacks the module-private Symbol -> assertServerActor
    // rejects it; buildTrustedIdentity only ever uses server-verified claims.
    assert.equal(assertServerActor(forged), false);
  });
});

// ---------------------------------------------------------------------------
describe("AEgis probe: risk override — human allow cannot beat risk breach/unknown", () => {
  test("risk breach + human allow -> DENIED", () => {
    const breach = evaluateRiskGate({ riskState: { exposureUsd: 99999, drawdownPct: 1, openOrders: 0, intendedOrderUsd: 5 } });
    const human = { ok: true, code: GATE_CODES.OK };
    const c = combineRiskAndHuman({ risk: breach, human });
    assert.equal(c.approved, false);
    assert.equal(c.risk.code, RISK_GATE_CODES.BREACH);
  });

  test("risk unknown + human allow -> DENIED (fail closed)", () => {
    const unknown = evaluateRiskGate({ riskState: null });
    const c = combineRiskAndHuman({ risk: unknown, human: { ok: true, code: GATE_CODES.OK } });
    assert.equal(c.approved, false);
    assert.equal(c.risk.code, RISK_GATE_CODES.STATE_UNKNOWN);
  });

  test("within limits + valid grant -> APPROVED", () => {
    const ok = evaluateRiskGate({ riskState: { exposureUsd: 10, drawdownPct: 1, openOrders: 0, intendedOrderUsd: 5 } });
    assert.equal(ok.approved, true);
    const c = combineRiskAndHuman({ risk: ok, human: { ok: true, code: GATE_CODES.OK } });
    assert.equal(c.approved, true);
  });
});

// ---------------------------------------------------------------------------
describe("AEgis probe: F-19 audit anchor mismatch fails closed", () => {
  test("audit chain non-empty but anchor missing -> MISMATCH (rollback/loss)", () => {
    const worm = new WormAuditAnchor(); // no anchor
    const audit = new AppendOnlyAuditLog();
    audit.append({ atMs: T0, type: "X", code: "C" });
    const tip = audit.entries().at(-1).hash;
    const r = verifyAnchorOnStartup({ worm, recomputedTipHash: tip, auditStateKnown: true });
    assert.equal(r.ok, false);
    assert.equal(r.code, ANCHOR_CODES.MISMATCH);
  });

  test("rollback to older anchor mismatches recomputed tip", () => {
    const intactWorm = new WormAuditAnchor();
    intactWorm.anchor("tip1", T0); intactWorm.anchor("tip2", T0 + 1); intactWorm.anchor("tip3", T0 + 2);
    const rolledBack = new WormAuditAnchor();
    rolledBack.anchor("tip1", T0);
    const r = verifyAnchorOnStartup({ worm: rolledBack, recomputedTipHash: "tip3", auditStateKnown: true });
    assert.equal(r.ok, false);
    assert.equal(r.code, ANCHOR_CODES.MISMATCH);
  });

  test("WORM exposes no working mutation API", () => {
    const worm = new WormAuditAnchor();
    worm.anchor("tip1", T0);
    assert.equal(worm.update(), false);
    assert.equal(worm.delete(), false);
    assert.equal(worm.clear(), false);
  });
});

// ---------------------------------------------------------------------------
describe("AEgis probe: cross-instance replay + G-1 closed", () => {
  const actor = makeServerActor({ authenticatedUserId: "shared-preview", nowMs: T0 });

  test("core gate via D1 adapter: same nonce on a different instance REPLAYs", async () => {
    const store = new D1SharedStore();
    const ledger = new AuthorizationLedger(store);
    const stateA = new D1BackedGateState(new AuthorizationLedger(store));
    const stateB = new D1BackedGateState(new AuthorizationLedger(store));
    const audit = new AppendOnlyAuditLog();
    const issued = issuePublishGrant({ actor, action: ACTION, strategyId: "STRAT-7", version: "v3", contentHash: HASH_A, ttlMs: 300000, nowMs: T0, secret: GATE_SECRET, nonce: "xinst-1", audit });
    const first = await safeCoreEnforceWithD1({ coreEnforce: (a) => enforceHumanPublishGate(a), coreEnforceArgs: { body: {}, actor, action: ACTION, strategyId: "STRAT-7", version: "v3", contentHash: HASH_A, nowMs: T0, secret: GATE_SECRET, grant: issued.grant, state: stateA, audit }, ledger, audit });
    const second = await safeCoreEnforceWithD1({ coreEnforce: (a) => enforceHumanPublishGate(a), coreEnforceArgs: { body: {}, actor, action: ACTION, strategyId: "STRAT-7", version: "v3", contentHash: HASH_A, nowMs: T0, secret: GATE_SECRET, grant: issued.grant, state: stateB, audit }, ledger, audit });
    assert.equal(first.ok && first.result.ok, true);
    assert.equal(second.ok, false);
    assert.equal(second.code, GATE_CODES.REPLAY);
  });

  test("G-1: concurrent core enforcements of ONE nonce -> exactly one ALLOW, one REPLAY (TOCTOU closed)", async () => {
    const store = new D1SharedStore();
    const ledger = new AuthorizationLedger(store);
    const stateA = new D1BackedGateState(new AuthorizationLedger(store));
    const stateB = new D1BackedGateState(new AuthorizationLedger(store));
    const audit = new AppendOnlyAuditLog();
    const issued = issuePublishGrant({ actor, action: ACTION, strategyId: "STRAT-7", version: "v3", contentHash: HASH_A, ttlMs: 300000, nowMs: T0, secret: GATE_SECRET, nonce: "g1-race", audit });
    const run = (state) => safeCoreEnforceWithD1({ coreEnforce: (a) => enforceHumanPublishGate(a), coreEnforceArgs: { body: {}, actor, action: ACTION, strategyId: "STRAT-7", version: "v3", contentHash: HASH_A, nowMs: T0, secret: GATE_SECRET, grant: issued.grant, state, audit }, ledger, audit });
    const [rA, rB] = await Promise.all([run(stateA), run(stateB)]);
    const allowed = [rA, rB].filter((r) => r.ok && r.result && r.result.ok).length;
    const replayed = [rA, rB].filter((r) => !r.ok && r.code === GATE_CODES.REPLAY).length;
    assert.equal(allowed, 1);
    assert.equal(replayed, 1);
  });
});

// ---------------------------------------------------------------------------
describe("AEgis probe: cross-instance identity revocation (G-2 closed)", () => {
  test("revoke on instance A rejected on instance B via shared store", () => {
    const shared = new Map();
    const storeA = new SharedIdentityRevocationStore(shared);
    const storeB = new SharedIdentityRevocationStore(shared);
    const tok = issueIdToken({ sub: "dave", issuerSecret: IDP_SECRET, nowMs: T0 });
    assert.equal(buildTrustedIdentity({ idToken: tok, idpSecret: IDP_SECRET, nowMs: T0, revocationStore: storeB }).ok, true);
    storeA.revokeSub("dave");
    const afterB = buildTrustedIdentity({ idToken: tok, idpSecret: IDP_SECRET, nowMs: T0, revocationStore: storeB });
    assert.equal(afterB.ok, false);
    assert.equal(afterB.code, IDENTITY_CODES.IDENTITY_REVOKED);
  });
});

// ---------------------------------------------------------------------------
describe("AEgis probe: C3 separation + role-bound single-use still hold", () => {
  const approverTok = mintActorToken({ sub: "approver-1", roles: [ROLES.APPROVER], idpSecret: IDP_SECRET, nowMs: T0 });
  const executorTok = mintActorToken({ sub: "executor-1", roles: [ROLES.EXECUTOR], idpSecret: IDP_SECRET, nowMs: T0 });

  test("approver == executor rejected", () => {
    const claims = verifyIdToken(approverTok, { issuerSecret: IDP_SECRET, nowMs: T0 }).claims;
    const r = issueRoleBoundGrant({ approverClaims: claims, action: ACTION, strategyId: "S7", strategyVersion: "v3", contentHash: HASH_A, executorId: "approver-1", nowMs: T0, secret: GATE_SECRET });
    assert.equal(r.ok, false);
    assert.equal(r.code, ROLE_CODES.SEPARATION_VIOLATION);
  });

  test("role-bound grant single-use across instances (D1)", () => {
    const store = new D1SharedStore();
    const ledger = new AuthorizationLedger(store);
    const issued = issueRoleBoundGrant({ approverClaims: verifyIdToken(approverTok, { issuerSecret: IDP_SECRET, nowMs: T0 }).claims, action: ACTION, strategyId: "S7", strategyVersion: "v3", contentHash: HASH_A, executorId: "executor-1", nowMs: T0, secret: GATE_SECRET });
    const exClaims = verifyIdToken(executorTok, { issuerSecret: IDP_SECRET, nowMs: T0 }).claims;
    const v1 = verifyRoleBoundGrant({ grant: issued.grant, executorClaims: exClaims, action: ACTION, strategyId: "S7", strategyVersion: "v3", contentHash: HASH_A, nowMs: T0, secret: GATE_SECRET, ledger });
    const v2 = verifyRoleBoundGrant({ grant: issued.grant, executorClaims: exClaims, action: ACTION, strategyId: "S7", strategyVersion: "v3", contentHash: HASH_A, nowMs: T0 + 1, secret: GATE_SECRET, ledger });
    assert.equal(v1.ok, true);
    assert.equal(v2.ok, false);
    assert.equal(v2.code, ROLE_CODES.REPLAY);
  });
});
