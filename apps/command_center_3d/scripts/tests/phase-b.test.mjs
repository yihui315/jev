// JEV Phase B — isolated tests for C2 / C3 / D1 / F-19 / F-14+F-15.
//
// Plain `node --test` + node:assert. No real IdP, no real D1 binding, no paid
// APIs, no network. Every external dependency is a LOCAL MOCK. These tests
// demonstrate the controls and their invariants in isolation; they are NOT a
// production security PASS (see FORGE_B_HANDOFF.md).
//
// The pre-existing 32 reverse tests (human-publish-gate.test.mjs, N1-N32) are
// NOT modified and must remain green; this file adds to them.

import assert from "node:assert/strict";
import { test, describe } from "node:test";
import { createHash, randomUUID } from "node:crypto";

import {
  makeServerActor, assertServerActor, enforceHumanPublishGate,
  issuePublishGrant, AppendOnlyAuditLog, GATE_CODES,
} from "../../server/ai/human-publish-gate.mjs";

import {
  issueIdToken, verifyIdToken, buildTrustedIdentity,
  IdentityRevocationStore, SharedIdentityRevocationStore, isTrustedActor, IDENTITY_CODES,
} from "../../server/ai/phase-b/identity.mjs";

import {
  ROLES, issueRoleBoundGrant, verifyRoleBoundGrant, ROLE_CODES,
  mintActorToken,
} from "../../server/ai/phase-b/roles.mjs";

import {
  D1SharedStore, AuthorizationLedger, D1BackedGateState, LedgerReplayError, LEDGER_CODES,
} from "../../server/ai/phase-b/ledger.mjs";

import {
  WormAuditAnchor, verifyAnchorOnStartup, ANCHOR_CODES,
} from "../../server/ai/phase-b/audit-anchor.mjs";

import {
  evaluateRiskGate, combineRiskAndHuman, RISK_GATE_CODES,
} from "../../server/ai/phase-b/risk-gate-b.mjs";

import {
  phaseBEnforce, phaseBIssueGrant, phaseBStartupRecovery, safeCoreEnforceWithD1,
} from "../../server/ai/phase-b/integration.mjs";

const T0 = 1800000000000;
const IDP_SECRET = "i".repeat(32);
const GATE_SECRET = "g".repeat(32);
const H = (s) => createHash("sha256").update(s, "utf8").digest("hex");
const CONTENT_A = "strategy-content-A";
const HASH_A = H(CONTENT_A);
const ACTION = "STRATEGY_PROMOTION";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------------------------------------------------------------------------
describe("C2 — per-user IdP identity + MFA (amr) + server-trusted injection", () => {
  test("valid token verifies and yields per-user claims with MFA", () => {
    const tok = issueIdToken({ sub: "alice", roles: [ROLES.APPROVER], issuerSecret: IDP_SECRET, nowMs: T0 });
    const v = verifyIdToken(tok, { issuerSecret: IDP_SECRET, nowMs: T0 });
    assert.equal(v.ok, true);
    assert.equal(v.claims.sub, "alice");
    assert.deepEqual(v.claims.amr, ["mfa"]);
  });

  test("tampered signature is rejected", () => {
    const tok = issueIdToken({ sub: "alice", issuerSecret: IDP_SECRET, nowMs: T0 });
    const [h, p, s] = tok.split(".");
    const bad = h + "." + p + "." + (s.slice(0, -1) + (s.endsWith("a") ? "b" : "a"));
    const v = verifyIdToken(bad, { issuerSecret: IDP_SECRET, nowMs: T0 });
    assert.equal(v.ok, false);
    assert.equal(v.code, IDENTITY_CODES.TOKEN_INVALID_SIGNATURE);
  });

  test("token from a different IdP secret is rejected", () => {
    const tok = issueIdToken({ sub: "alice", issuerSecret: IDP_SECRET, nowMs: T0 });
    const v = verifyIdToken(tok, { issuerSecret: "x".repeat(32), nowMs: T0 });
    assert.equal(v.ok, false);
    assert.equal(v.code, IDENTITY_CODES.TOKEN_INVALID_SIGNATURE);
  });

  test("missing MFA (amr without mfa) is rejected", () => {
    const tok = issueIdToken({ sub: "alice", amr: ["pwd"], issuerSecret: IDP_SECRET, nowMs: T0 });
    const v = verifyIdToken(tok, { issuerSecret: IDP_SECRET, nowMs: T0 });
    assert.equal(v.ok, false);
    assert.equal(v.code, IDENTITY_CODES.MFA_NOT_SATISFIED);
  });

  test("expired token is rejected fail-closed", () => {
    const tok = issueIdToken({ sub: "alice", issuerSecret: IDP_SECRET, nowMs: T0, ttlMs: 1000 });
    const v = verifyIdToken(tok, { issuerSecret: IDP_SECRET, nowMs: T0 + 1001 });
    assert.equal(v.ok, false);
    assert.equal(v.code, IDENTITY_CODES.TOKEN_EXPIRED);
  });

  test("server-trusted identity injection derives actor from verified claims only", () => {
    const tok = issueIdToken({ sub: "alice", roles: [ROLES.APPROVER], issuerSecret: IDP_SECRET, nowMs: T0 });
    const id = buildTrustedIdentity({ idToken: tok, idpSecret: IDP_SECRET, nowMs: T0 });
    assert.equal(id.ok, true);
    assert.equal(id.actor.userId, "alice");
    assert.equal(id.actor.authMethod, "IDP_OIDC_MFA");
    assert.equal(assertServerActor(id.actor), true, "actor must carry the server Symbol");
  });

  test("a client-supplied plain actor can never be trusted", () => {
    const forged = {
      userId: "attacker", authMethod: "IDP_OIDC_MFA", authenticatedAtMs: T0,
      identityHash: H("attacker|IDP_OIDC_MFA"), source: "SERVER_AUTH_LAYER",
    };
    assert.equal(assertServerActor(forged), false);
    assert.equal(isTrustedActor(forged), false);
  });

  test("different users get independent, non-forgeable identities", () => {
    const a = buildTrustedIdentity({ idToken: issueIdToken({ sub: "alice", issuerSecret: IDP_SECRET, nowMs: T0 }), idpSecret: IDP_SECRET, nowMs: T0 });
    const b = buildTrustedIdentity({ idToken: issueIdToken({ sub: "bob", issuerSecret: IDP_SECRET, nowMs: T0 }), idpSecret: IDP_SECRET, nowMs: T0 });
    assert.equal(a.actor.userId, "alice");
    assert.equal(b.actor.userId, "bob");
    assert.notEqual(a.actor.identityHash, b.actor.identityHash);
  });

  test("revoked identity (by sub) is rejected", () => {
    const rev = new IdentityRevocationStore();
    rev.revokeSub("alice");
    const tok = issueIdToken({ sub: "alice", issuerSecret: IDP_SECRET, nowMs: T0 });
    const id = buildTrustedIdentity({ idToken: tok, idpSecret: IDP_SECRET, nowMs: T0, revocationStore: rev });
    assert.equal(id.ok, false);
    assert.equal(id.code, IDENTITY_CODES.IDENTITY_REVOKED);
  });

  test("G-2: revocation on instance A is visible on instance B via a SHARED store (cross-instance)", () => {
    // Two workers share one revocation Map (mirrors AuthorizationLedger + D1SharedStore).
    const shared = new Map();
    const storeA = new SharedIdentityRevocationStore(shared);
    const storeB = new SharedIdentityRevocationStore(shared);

    const tok = issueIdToken({ sub: "carol", issuerSecret: IDP_SECRET, nowMs: T0 });

    // Before revocation, both instances trust the identity.
    const beforeA = buildTrustedIdentity({ idToken: tok, idpSecret: IDP_SECRET, nowMs: T0, revocationStore: storeA });
    const beforeB = buildTrustedIdentity({ idToken: tok, idpSecret: IDP_SECRET, nowMs: T0, revocationStore: storeB });
    assert.equal(beforeA.ok, true);
    assert.equal(beforeB.ok, true);

    // Instance A revokes carol.
    storeA.revokeSub("carol");

    // Instance B (same shared store) must now reject carol -> cross-instance revocation.
    const afterB = buildTrustedIdentity({ idToken: tok, idpSecret: IDP_SECRET, nowMs: T0, revocationStore: storeB });
    assert.equal(afterB.ok, false, "revocation on A must be seen on B");
    assert.equal(afterB.code, IDENTITY_CODES.IDENTITY_REVOKED);

    // And a brand-new worker instance C pointing at the same shared store also rejects.
    const storeC = new SharedIdentityRevocationStore(shared);
    const afterC = buildTrustedIdentity({ idToken: tok, idpSecret: IDP_SECRET, nowMs: T0, revocationStore: storeC });
    assert.equal(afterC.ok, false, "revocation survives to a freshly-started instance C");
  });
});

// ---------------------------------------------------------------------------
describe("C3 — four roles + approver != executor + grant binding", () => {
  const approverTok = mintActorToken({ sub: "approver-1", roles: [ROLES.APPROVER], idpSecret: IDP_SECRET, nowMs: T0 });
  const executorTok = mintActorToken({ sub: "executor-1", roles: [ROLES.EXECUTOR], idpSecret: IDP_SECRET, nowMs: T0 });
  const nonApproverTok = mintActorToken({ sub: "exec-2", roles: [ROLES.EXECUTOR], idpSecret: IDP_SECRET, nowMs: T0 });

  test("only an APPROVER may issue a role-bound grant", () => {
    const claims = verifyIdToken(nonApproverTok, { issuerSecret: IDP_SECRET, nowMs: T0 }).claims;
    const r = issueRoleBoundGrant({ approverClaims: claims, action: ACTION, strategyId: "S7", strategyVersion: "v3", contentHash: HASH_A, executorId: "executor-1", nowMs: T0, secret: GATE_SECRET });
    assert.equal(r.ok, false);
    assert.equal(r.code, ROLE_CODES.MISSING_ROLE);
  });

  test("approver == executor is rejected (separation violation)", () => {
    const claims = verifyIdToken(approverTok, { issuerSecret: IDP_SECRET, nowMs: T0 }).claims;
    const r = issueRoleBoundGrant({ approverClaims: claims, action: ACTION, strategyId: "S7", strategyVersion: "v3", contentHash: HASH_A, executorId: "approver-1", nowMs: T0, secret: GATE_SECRET });
    assert.equal(r.ok, false);
    assert.equal(r.code, ROLE_CODES.SEPARATION_VIOLATION);
  });

  test("happy path: approver issues, executor verifies + nonce consumed once", () => {
    const store = new D1SharedStore();
    const ledger = new AuthorizationLedger(store);
    const issued = issueRoleBoundGrant({ approverClaims: verifyIdToken(approverTok, { issuerSecret: IDP_SECRET, nowMs: T0 }).claims, action: ACTION, strategyId: "S7", strategyVersion: "v3", contentHash: HASH_A, executorId: "executor-1", nowMs: T0, secret: GATE_SECRET });
    assert.equal(issued.ok, true);
    const exClaims = verifyIdToken(executorTok, { issuerSecret: IDP_SECRET, nowMs: T0 }).claims;
    const v1 = verifyRoleBoundGrant({ grant: issued.grant, executorClaims: exClaims, action: ACTION, strategyId: "S7", strategyVersion: "v3", contentHash: HASH_A, nowMs: T0, secret: GATE_SECRET, ledger });
    assert.equal(v1.ok, true);
    assert.equal(v1.scope.approverId, "approver-1");
    assert.equal(v1.scope.executorId, "executor-1");
    const v2 = verifyRoleBoundGrant({ grant: issued.grant, executorClaims: exClaims, action: ACTION, strategyId: "S7", strategyVersion: "v3", contentHash: HASH_A, nowMs: T0 + 1, secret: GATE_SECRET, ledger });
    assert.equal(v2.ok, false);
    assert.equal(v2.code, ROLE_CODES.REPLAY);
  });

  test("executor identity must match the bound executorId", () => {
    const store = new D1SharedStore();
    const ledger = new AuthorizationLedger(store);
    const issued = issueRoleBoundGrant({ approverClaims: verifyIdToken(approverTok, { issuerSecret: IDP_SECRET, nowMs: T0 }).claims, action: ACTION, strategyId: "S7", strategyVersion: "v3", contentHash: HASH_A, executorId: "executor-1", nowMs: T0, secret: GATE_SECRET });
    const otherEx = verifyIdToken(mintActorToken({ sub: "executor-X", roles: [ROLES.EXECUTOR], idpSecret: IDP_SECRET, nowMs: T0 }), { issuerSecret: IDP_SECRET, nowMs: T0 }).claims;
    const v = verifyRoleBoundGrant({ grant: issued.grant, executorClaims: otherEx, action: ACTION, strategyId: "S7", strategyVersion: "v3", contentHash: HASH_A, nowMs: T0, secret: GATE_SECRET, ledger });
    assert.equal(v.ok, false);
    assert.equal(v.code, ROLE_CODES.EXECUTOR_MISMATCH);
  });

  test("executor must hold the EXECUTOR role", () => {
    const store = new D1SharedStore();
    const ledger = new AuthorizationLedger(store);
    const issued = issueRoleBoundGrant({ approverClaims: verifyIdToken(approverTok, { issuerSecret: IDP_SECRET, nowMs: T0 }).claims, action: ACTION, strategyId: "S7", strategyVersion: "v3", contentHash: HASH_A, executorId: "executor-1", nowMs: T0, secret: GATE_SECRET });
    const noRole = verifyIdToken(mintActorToken({ sub: "executor-1", roles: [ROLES.AUDITOR], idpSecret: IDP_SECRET, nowMs: T0 }), { issuerSecret: IDP_SECRET, nowMs: T0 }).claims;
    const v = verifyRoleBoundGrant({ grant: issued.grant, executorClaims: noRole, action: ACTION, strategyId: "S7", strategyVersion: "v3", contentHash: HASH_A, nowMs: T0, secret: GATE_SECRET, ledger });
    assert.equal(v.ok, false);
    assert.equal(v.code, ROLE_CODES.MISSING_ROLE);
  });

  test("scope / content mismatch is denied", () => {
    const store = new D1SharedStore();
    const ledger = new AuthorizationLedger(store);
    const issued = issueRoleBoundGrant({ approverClaims: verifyIdToken(approverTok, { issuerSecret: IDP_SECRET, nowMs: T0 }).claims, action: ACTION, strategyId: "S7", strategyVersion: "v3", contentHash: HASH_A, executorId: "executor-1", nowMs: T0, secret: GATE_SECRET });
    const exClaims = verifyIdToken(executorTok, { issuerSecret: IDP_SECRET, nowMs: T0 }).claims;
    const v = verifyRoleBoundGrant({ grant: issued.grant, executorClaims: exClaims, action: ACTION, strategyId: "S7", strategyVersion: "v3", contentHash: H("tampered"), nowMs: T0, secret: GATE_SECRET, ledger });
    assert.equal(v.ok, false);
    assert.equal(v.code, ROLE_CODES.CONTENT_MISMATCH);
  });
});

// ---------------------------------------------------------------------------
describe("D1 — persistent, cross-instance ledger", () => {
  test("atomic nonce consumption: first CONSUMED, reuse REPLAY", () => {
    const store = new D1SharedStore();
    const ledger = new AuthorizationLedger(store);
    const a = ledger.consumeNonce({ nonce: "n1", grantId: "g1", actorHash: "h", strategyId: "S", nowMs: T0 });
    const b = ledger.consumeNonce({ nonce: "n1", grantId: "g1", actorHash: "h", strategyId: "S", nowMs: T0 });
    assert.equal(a.ok, true);
    assert.equal(a.code, LEDGER_CODES.CONSUMED);
    assert.equal(b.ok, false);
    assert.equal(b.code, LEDGER_CODES.REPLAY);
  });

  test("cross-instance replay: nonce consumed on A is REPLAY on B", () => {
    const store = new D1SharedStore();
    const ledgerA = new AuthorizationLedger(store);
    const ledgerB = new AuthorizationLedger(store);
    const a = ledgerA.consumeNonce({ nonce: "shared-n", grantId: "g", actorHash: "h", strategyId: "S", nowMs: T0 });
    const b = ledgerB.consumeNonce({ nonce: "shared-n", grantId: "g", actorHash: "h", strategyId: "S", nowMs: T0 });
    assert.equal(a.ok, true);
    assert.equal(b.ok, false);
    assert.equal(b.code, LEDGER_CODES.REPLAY);
  });

  test("concurrency race: N parallel same-nonce consumes -> exactly 1 CONSUMED", async () => {
    const store = new D1SharedStore();
    const ledger = new AuthorizationLedger(store);
    const N = 50;
    const results = await Promise.all(
      Array.from({ length: N }, (_, i) =>
        ledger.consumeNonce({ nonce: "race-n", grantId: "g" + i, actorHash: "h", strategyId: "S", nowMs: T0 })),
    );
    const consumed = results.filter((r) => r.ok);
    const replayed = results.filter((r) => !r.ok && r.code === LEDGER_CODES.REPLAY);
    assert.equal(consumed.length, 1, "exactly one winner");
    assert.equal(replayed.length, N - 1, "all others replay");
  });

  test("NAIVE cross-instance design double-spends (why shared D1 is required)", async () => {
    // Two workers, each with its OWN in-memory Set, no shared store.
    const w1 = AuthorizationLedger.naiveInstance();
    const w2 = AuthorizationLedger.naiveInstance();
    const [r1, r2] = await Promise.all([
      w1.consumeNonce({ nonce: "dup-n" }),
      w2.consumeNonce({ nonce: "dup-n" }),
    ]);
    // Both believe they consumed it -> the bug the D1 design fixes.
    assert.equal(r1.ok, true);
    assert.equal(r2.ok, true, "naive design double-spends across instances");
  });

  test("revocation is cross-instance", () => {
    const store = new D1SharedStore();
    const ledgerA = new AuthorizationLedger(store);
    const ledgerB = new AuthorizationLedger(store);
    ledgerA.revokeGrant({ grantId: "g-rev", revokedBy: "approver-1", nowMs: T0 });
    assert.equal(ledgerA.isRevoked("g-rev"), true);
    assert.equal(ledgerB.isRevoked("g-rev"), true);
  });

  test("failure recovery: durable store survives a process crash (fresh instance on same D1)", () => {
    const store = new D1SharedStore();
    const ledger = new AuthorizationLedger(store);
    ledger.consumeNonce({ nonce: "durable-1", grantId: "g", actorHash: "h", strategyId: "S", nowMs: T0 });
    ledger.consumeNonce({ nonce: "durable-2", grantId: "g", actorHash: "h", strategyId: "S", nowMs: T0 });
    ledger.revokeGrant({ grantId: "rev-me", revokedBy: "a", nowMs: T0 });
    // Simulate a process crash/restart: a brand-new worker instance is created
    // against the SAME durable D1 store. The shared store is the source of truth.
    const restarted = new AuthorizationLedger(store);
    assert.equal(restarted.hasNonce("durable-1"), true, "consumed nonce survives crash");
    assert.equal(restarted.hasNonce("durable-2"), true, "consumed nonce survives crash");
    assert.equal(restarted.hasNonce("never-seen"), false, "absent remains absent");
    assert.equal(restarted.isRevoked("rev-me"), true, "revocation survives crash");
    // The restarted instance still blocks replay of the durable nonce.
    const again = restarted.consumeNonce({ nonce: "durable-1", grantId: "g", actorHash: "h", strategyId: "S", nowMs: T0 });
    assert.equal(again.ok, false);
    assert.equal(again.code, LEDGER_CODES.REPLAY);
  });

  test("D1-backed state adapter drives the EXISTING core gate cross-instance", async () => {
    const store = new D1SharedStore();
    const ledgerA = new AuthorizationLedger(store);
    const ledgerB = new AuthorizationLedger(store);
    const stateA = new D1BackedGateState(ledgerA);
    const stateB = new D1BackedGateState(ledgerB);
    const audit = new AppendOnlyAuditLog();
    const actor = makeServerActor({ authenticatedUserId: "shared-preview", nowMs: T0 });

    const issued = issuePublishGrant({ actor, action: ACTION, strategyId: "STRAT-7", version: "v3", contentHash: HASH_A, ttlMs: 300000, nowMs: T0, secret: GATE_SECRET, nonce: "core-n1", audit });
    assert.equal(issued.ok, true);

    const first = await enforceHumanPublishGate({ body: {}, actor, action: ACTION, strategyId: "STRAT-7", version: "v3", contentHash: HASH_A, nowMs: T0, secret: GATE_SECRET, grant: issued.grant, state: stateA, audit });
    assert.equal(first.ok, true, "first execution on instance A");
    const second = await enforceHumanPublishGate({ body: {}, actor, action: ACTION, strategyId: "STRAT-7", version: "v3", contentHash: HASH_A, nowMs: T0, secret: GATE_SECRET, grant: issued.grant, state: stateB, audit });
    assert.equal(second.ok, false, "same nonce on instance B must replay (cross-instance)");
    assert.equal(second.code, GATE_CODES.REPLAY);
  });

  test("G-1: D1BackedGateState.consumedNonces.add() THROWS LedgerReplayError on REPLAY (never silent)", () => {
    const store = new D1SharedStore();
    const ledger = new AuthorizationLedger(store);
    const state = new D1BackedGateState(ledger);
    state.consumedNonces.add("g1"); // first consume succeeds
    assert.throws(
      () => state.consumedNonces.add("g1"),
      (e) => e instanceof LedgerReplayError && e.code === LEDGER_CODES.REPLAY,
      "second add of same nonce must throw LedgerReplayError",
    );
  });

  test("G-1: safeCoreEnforceWithD1 converts a REPLAY throw from the adapter into a clean REPLAY denial", async () => {
    const store = new D1SharedStore();
    const ledger = new AuthorizationLedger(store);
    // Pre-consume the nonce in the shared ledger. The adapter's `add()` will then
    // hit a REPLAY and THROW (fail-closed). This deterministically exercises the
    // throw -> conversion path WITHOUT depending on core-gate internals, and is
    // exactly the branch that was silently swallowed before G-1.
    ledger.consumeNonce({ nonce: "core-safe-1", grantId: "g", actorHash: "h", strategyId: "s", nowMs: T0 });
    const r = await safeCoreEnforceWithD1({
      coreEnforce: (a) => { a.state.consumedNonces.add("core-safe-1"); return { ok: true, code: "CORE_OK" }; },
      coreEnforceArgs: {},
      ledger,
    });
    assert.equal(r.ok, false, "REPLAY throw must be converted to a denial");
    assert.equal(r.code, GATE_CODES.REPLAY, "must surface as GATE_CODES.REPLAY");
  });

  test("G-1: concurrent adapter add() of the SAME nonce -> one succeeds, the other THROWS LedgerReplayError (no silent double-spend)", async () => {
    const store = new D1SharedStore();
    const ledger = new AuthorizationLedger(store);
    const stateA = new D1BackedGateState(new AuthorizationLedger(store));
    const stateB = new D1BackedGateState(new AuthorizationLedger(store));
    let threw = 0;
    const run = (state) => {
      try { state.consumedNonces.add("core-race-1"); return { ok: true }; }
      catch (e) { if (e instanceof LedgerReplayError) threw++; return { ok: false }; }
    };
    // Both workers share ONE ledger. Before G-1, `add()` ignored the REPLAY
    // return and both would have succeeded (double execution). Now the second
    // `add()` MUST throw LedgerReplayError (fail-closed).
    const [rA, rB] = await Promise.all([run(stateA), run(stateB)]);
    assert.equal(rA.ok && !rB.ok, true, "exactly one add() succeeds and the other throws");
    assert.equal(threw, 1, "exactly one add() THROWS LedgerReplayError (TOCTOU closed)");
    assert.equal(store.hasNonce("core-race-1"), true, "nonce present in the shared store");
    assert.equal([...store.snapshot().nonces].filter(([k]) => k === "core-race-1").length, 1, "nonce stored exactly once");
  });
});

// ---------------------------------------------------------------------------
describe("F-19 — external WORM audit anchor + recovery", () => {
  test("WORM anchor is append-only and exposes no mutation API", () => {
    const worm = new WormAuditAnchor();
    worm.anchor("tip1", T0);
    worm.anchor("tip2", T0 + 1);
    assert.equal(worm.count(), 2);
    assert.equal(worm.last().tipHash, "tip2");
    assert.equal(typeof worm.update, "function");
    assert.equal(worm.update(), false, "update is a no-op (WORM)");
    assert.equal(worm.delete(), false);
    assert.equal(worm.clear(), false);
  });

  test("startup recovery: recomputed tip matches last anchor -> verified", () => {
    const worm = new WormAuditAnchor();
    worm.anchor("tipA", T0);
    const r = verifyAnchorOnStartup({ worm, recomputedTipHash: "tipA", auditStateKnown: true });
    assert.equal(r.ok, true);
    assert.equal(r.code, "AUDIT_ANCHOR_VERIFIED");
  });

  test("startup recovery: mismatch fails closed", () => {
    const worm = new WormAuditAnchor();
    worm.anchor("tipA", T0);
    const r = verifyAnchorOnStartup({ worm, recomputedTipHash: "tipB-tampered", auditStateUnknown: true });
    assert.equal(r.ok, false);
    assert.equal(r.code, ANCHOR_CODES.MISMATCH);
  });

  test("startup recovery: unknown/garbled audit state fails closed", () => {
    const worm = new WormAuditAnchor();
    worm.anchor("tipA", T0);
    const r = verifyAnchorOnStartup({ worm, recomputedTipHash: "tipA", auditStateKnown: false });
    assert.equal(r.ok, false);
    assert.equal(r.code, ANCHOR_CODES.MISMATCH);
  });

  test("tamper / rollback detection: anchor rolled back to older tip mismatches", () => {
    const worm = new WormAuditAnchor();
    worm.anchor("tip1", T0);
    worm.anchor("tip2", T0 + 1);
    worm.anchor("tip3", T0 + 2);
    // Attacker reverts the anchor store to tip1 (rollback to an older anchor).
    const rolledBack = new WormAuditAnchor();
    rolledBack.anchor("tip1", T0);
    // Recompute from the (intact) in-memory chain would be tip3.
    const r = verifyAnchorOnStartup({ worm: rolledBack, recomputedTipHash: "tip3", auditStateKnown: true });
    assert.equal(r.ok, false);
    assert.equal(r.code, ANCHOR_CODES.MISMATCH);
  });
});

// ---------------------------------------------------------------------------
describe("F-14 / F-15 — independent risk gate", () => {
  test("risk breach is denied and cannot be overridden by a human allow", () => {
    const breach = evaluateRiskGate({ riskState: { exposureUsd: 99999, drawdownPct: 5, openOrders: 1, intendedOrderUsd: 10 } });
    assert.equal(breach.approved, false);
    assert.equal(breach.code, RISK_GATE_CODES.BREACH);
    const human = { ok: true, code: GATE_CODES.OK };
    const combined = combineRiskAndHuman({ risk: breach, human });
    assert.equal(combined.approved, false, "human allow cannot override risk breach");
  });

  test("unknown risk state is denied (fail closed)", () => {
    const unknown = evaluateRiskGate({ riskState: null });
    assert.equal(unknown.approved, false);
    assert.equal(unknown.code, RISK_GATE_CODES.STATE_UNKNOWN);
    const combined = combineRiskAndHuman({ risk: unknown, human: { ok: true, code: GATE_CODES.OK } });
    assert.equal(combined.approved, false);
  });

  test("within all limits is approved", () => {
    const ok = evaluateRiskGate({ riskState: { exposureUsd: 100, drawdownPct: 1, openOrders: 1, intendedOrderUsd: 10 } });
    assert.equal(ok.approved, true);
    assert.equal(ok.code, RISK_GATE_CODES.APPROVED);
  });

  test("circuit breaker flag denies", () => {
    const tripped = evaluateRiskGate({ riskState: { exposureUsd: 0, openOrders: 0, intendedOrderUsd: 0 }, limits: { circuitBreakerTripped: true } });
    assert.equal(tripped.approved, false);
    assert.equal(tripped.code, RISK_GATE_CODES.BREACH);
  });
});

// ---------------------------------------------------------------------------
describe("Integration — full Phase B enforce flow", () => {
  function setup() {
    const store = new D1SharedStore();
    const ledger = new AuthorizationLedger(store);
    const audit = new AppendOnlyAuditLog();
    const worm = new WormAuditAnchor();
    const approverTok = mintActorToken({ sub: "approver-1", roles: [ROLES.APPROVER], idpSecret: IDP_SECRET, nowMs: T0 });
    const executorTok = mintActorToken({ sub: "executor-1", roles: [ROLES.EXECUTOR], idpSecret: IDP_SECRET, nowMs: T0 });
    return { store, ledger, audit, worm, approverTok, executorTok };
  }

  test("happy path: issue -> enforce -> authorized + anchored", () => {
    const s = setup();
    const issued = phaseBIssueGrant({ approverToken: s.approverTok, idpSecret: IDP_SECRET, executorId: "executor-1", action: ACTION, strategyId: "S7", strategyVersion: "v3", contentHash: HASH_A, nowMs: T0, secret: GATE_SECRET });
    assert.equal(issued.ok, true);
    const r = phaseBEnforce({ executorToken: s.executorTok, idpSecret: IDP_SECRET, grant: issued.grant, action: ACTION, strategyId: "S7", strategyVersion: "v3", contentHash: HASH_A, nowMs: T0, secret: GATE_SECRET, ledger: s.ledger, audit: s.audit, worm: s.worm, riskState: { exposureUsd: 10, drawdownPct: 1, openOrders: 0, intendedOrderUsd: 5 } });
    assert.equal(r.ok, true);
    assert.equal(r.code, "PHASEB_AUTHORIZED");
    assert.equal(s.worm.count(), 1, "audit tip anchored to WORM");
  });

  test("risk breach overrides a perfectly valid grant", () => {
    const s = setup();
    const issued = phaseBIssueGrant({ approverToken: s.approverTok, idpSecret: IDP_SECRET, executorId: "executor-1", action: ACTION, strategyId: "S7", strategyVersion: "v3", contentHash: HASH_A, nowMs: T0, secret: GATE_SECRET });
    const r = phaseBEnforce({ executorToken: s.executorTok, idpSecret: IDP_SECRET, grant: issued.grant, action: ACTION, strategyId: "S7", strategyVersion: "v3", contentHash: HASH_A, nowMs: T0, secret: GATE_SECRET, ledger: s.ledger, audit: s.audit, worm: s.worm, riskState: { exposureUsd: 999999, drawdownPct: 1, openOrders: 0, intendedOrderUsd: 5 } });
    assert.equal(r.ok, false);
    assert.equal(r.code, "PHASEB_RISK_DENIED");
  });

  test("bad executor identity is denied before any grant check", () => {
    const s = setup();
    const issued = phaseBIssueGrant({ approverToken: s.approverTok, idpSecret: IDP_SECRET, executorId: "executor-1", action: ACTION, strategyId: "S7", strategyVersion: "v3", contentHash: HASH_A, nowMs: T0, secret: GATE_SECRET });
    const r = phaseBEnforce({ executorToken: "not-a-token", idpSecret: IDP_SECRET, grant: issued.grant, action: ACTION, strategyId: "S7", strategyVersion: "v3", contentHash: HASH_A, nowMs: T0, secret: GATE_SECRET, ledger: s.ledger, audit: s.audit, worm: s.worm, riskState: { exposureUsd: 10, drawdownPct: 1, openOrders: 0, intendedOrderUsd: 5 } });
    assert.equal(r.ok, false);
    assert.ok(
      r.code === IDENTITY_CODES.TOKEN_REQUIRED || r.code === IDENTITY_CODES.TOKEN_MALFORMED,
      "untrusted executor token is denied",
    );
  });

  test("startup recovery fails closed on anchor mismatch", () => {
    const s = setup();
    const issued = phaseBIssueGrant({ approverToken: s.approverTok, idpSecret: IDP_SECRET, executorId: "executor-1", action: ACTION, strategyId: "S7", strategyVersion: "v3", contentHash: HASH_A, nowMs: T0, secret: GATE_SECRET });
    phaseBEnforce({ executorToken: s.executorTok, idpSecret: IDP_SECRET, grant: issued.grant, action: ACTION, strategyId: "S7", strategyVersion: "v3", contentHash: HASH_A, nowMs: T0, secret: GATE_SECRET, ledger: s.ledger, audit: s.audit, worm: s.worm, riskState: { exposureUsd: 10, drawdownPct: 1, openOrders: 0, intendedOrderUsd: 5 } });
    // A fresh worm (no anchor) representing a reset/rollback.
    const freshWorm = new WormAuditAnchor();
    const rec = phaseBStartupRecovery({ worm: freshWorm, audit: s.audit, auditStateKnown: true });
    assert.equal(rec.ok, false, "rollback with no anchor mismatches recomputed tip");
    assert.equal(rec.code, ANCHOR_CODES.MISMATCH);
  });
});
