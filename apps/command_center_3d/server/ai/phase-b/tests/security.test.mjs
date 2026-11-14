// JEV Phase B — Aegis independent security audit (node --test).
//
// AUDIT SCOPE: C2 identity (identity.mjs) + C3 role-bound grant (roles.mjs) +
// D1 authorization ledger (ledger.mjs).
//
// HARD CONSTRAINTS (Aegis / order §4):
//   * No production code is modified — this file ONLY exercises the modules.
//   * No network, no plaintext secrets / real credentials, no real trades.
//   * All ledgers are in-memory mocks; clocks are injected (nowMs).
//   * This is an ISOLATED mock audit. Results must NEVER be reported as
//     "production security PASS".
//
// Covers the four mandated classes:
//   1. Replay: nonce consumed once => second use (incl. via D1 ledger path) REPLAY.
//   2. Identity forgery: wrong-secret IdP token, non-APPROVER / no-MFA grant,
//      approver===executor => SEPARATION_VIOLATION, cross-identity replay.
//   3. Concurrency: Promise.all on one nonce => exactly ONE CONSUMED (atomic).
//   4. Tamper/rollback: (F-19 WORM lives in real-worm.test.mjs; here we assert
//      D1 ledger append integrity + replay fail-closed).

import assert from "node:assert/strict";
import { test, describe } from "node:test";
import { createHash } from "node:crypto";

import {
  issueIdToken, verifyIdToken, assertMfaSatisfied, IDENTITY_CODES,
} from "../identity.mjs";
import {
  issueRoleBoundGrant, verifyRoleBoundGrant, ROLES, ROLE_CODES,
} from "../roles.mjs";
import {
  D1SharedStore, AuthorizationLedger, LEDGER_CODES,
} from "../ledger.mjs";

// ---- injected secrets (mock-only, never real credentials) -------------------
const IDP_SECRET = "idp-audit-secret-min-16-bytes-long-0001";
const IDP_SECRET_OTHER = "idp-audit-secret-min-16-bytes-long-0002";
const ROLE_SECRET = "role-audit-secret-min-16-bytes-long-0001";
const ROLE_SECRET_OTHER = "role-audit-secret-min-16-bytes-long-0002";
const NOW = 1_700_000_000_000;
const TTL = 300_000;

function hex64(input) {
  return createHash("sha256").update(input).digest("hex"); // always [a-f0-9]{64}
}

/** Mint verified IdP claims for a subject with given roles + amr. */
function mintClaims(sub, roles, nowMs = NOW, amr = ["mfa"]) {
  const token = issueIdToken({ sub, roles, amr, issuerSecret: IDP_SECRET, nowMs });
  const v = verifyIdToken(token, { issuerSecret: IDP_SECRET, nowMs });
  assert.equal(v.ok, true, "mintClaims must produce a valid token");
  return v.claims;
}

function freshLedger() {
  return new AuthorizationLedger(new D1SharedStore());
}

function issueStandardGrant({ approverSub = "approver1", executorSub = "executor1", nowMs = NOW, secret = ROLE_SECRET } = {}) {
  const approver = mintClaims(approverSub, [ROLES.APPROVER], nowMs);
  const grant = issueRoleBoundGrant({
    approverClaims: approver,
    action: "EXECUTE_STRATEGY",
    strategyId: "S-42",
    strategyVersion: "v3",
    contentHash: hex64("payload-1"),
    executorId: executorSub,
    ttlMs: TTL,
    nowMs,
    secret,
    nonce: "nonce-fixed-" + approverSub + "-" + executorSub,
  });
  assert.equal(grant.ok, true, "grant issuance must succeed in the happy path");
  return { approver, grant: grant.grant };
}

// ===========================================================================
// C2 — IDENTITY FORGERY
// ===========================================================================
describe("C2 identity forgery (identity.mjs)", () => {
  test("token signed with WRONG secret is rejected (TOKEN_INVALID_SIGNATURE)", () => {
    const tok = issueIdToken({ sub: "u1", roles: [], amr: ["mfa"], issuerSecret: IDP_SECRET, nowMs: NOW });
    const v = verifyIdToken(tok, { issuerSecret: IDP_SECRET_OTHER, nowMs: NOW });
    assert.equal(v.ok, false);
    assert.equal(v.code, IDENTITY_CODES.TOKEN_INVALID_SIGNATURE);
  });

  test("tampered payload after signing fails signature verification", () => {
    const tok = issueIdToken({ sub: "u1", roles: [], amr: ["mfa"], issuerSecret: IDP_SECRET, nowMs: NOW });
    const [h, p, s] = tok.split(".");
    const claims = JSON.parse(Buffer.from(p, "base64url").toString("utf8"));
    claims.sub = "attacker";
    const p2 = Buffer.from(JSON.stringify(claims)).toString("base64url");
    const forged = h + "." + p2 + "." + s; // signature no longer matches
    const v = verifyIdToken(forged, { issuerSecret: IDP_SECRET, nowMs: NOW });
    assert.equal(v.ok, false);
    assert.equal(v.code, IDENTITY_CODES.TOKEN_INVALID_SIGNATURE);
  });

  test("token without MFA (amr missing mfa) is rejected (MFA_NOT_SATISFIED)", () => {
    const tok = issueIdToken({ sub: "u2", roles: [], amr: ["pwd"], issuerSecret: IDP_SECRET, nowMs: NOW });
    const v = verifyIdToken(tok, { issuerSecret: IDP_SECRET, nowMs: NOW });
    assert.equal(v.ok, false);
    assert.equal(v.code, IDENTITY_CODES.MFA_NOT_SATISFIED);
    assert.equal(assertMfaSatisfied(v.claims || { amr: ["pwd"] }), false);
  });

  test("assertMfaSatisfied is false for claims lacking mfa", () => {
    assert.equal(assertMfaSatisfied({ amr: [] }), false);
    assert.equal(assertMfaSatisfied(null), false);
    assert.equal(assertMfaSatisfied({ amr: ["mfa"] }), true);
  });

  test("expired token is rejected (TOKEN_EXPIRED)", () => {
    const tok = issueIdToken({ sub: "u3", roles: [], amr: ["mfa"], issuerSecret: IDP_SECRET, nowMs: NOW, ttlMs: 1000 });
    const v = verifyIdToken(tok, { issuerSecret: IDP_SECRET, nowMs: NOW + 5000 });
    assert.equal(v.ok, false);
    assert.equal(v.code, IDENTITY_CODES.TOKEN_EXPIRED);
  });

  test("malformed / empty token is rejected fail-closed", () => {
    assert.equal(verifyIdToken("", { issuerSecret: IDP_SECRET, nowMs: NOW }).ok, false);
    assert.equal(verifyIdToken("a.b", { issuerSecret: IDP_SECRET, nowMs: NOW }).code, IDENTITY_CODES.TOKEN_MALFORMED);
  });
});

// ===========================================================================
// C3 — ROLE-BOUND GRANT: replay / forgery / separation / cross-identity
// ===========================================================================
describe("C3 role-bound grant replay (roles.mjs)", () => {
  test("happy path: APPROVER issues, EXECUTOR verifies => ALLOWED", () => {
    const { grant } = issueStandardGrant();
    const executor = mintClaims("executor1", [ROLES.EXECUTOR]);
    const r = verifyRoleBoundGrant({
      grant, executorClaims: executor,
      action: "EXECUTE_STRATEGY", strategyId: "S-42", strategyVersion: "v3",
      contentHash: hex64("payload-1"),
      nowMs: NOW + 1000, secret: ROLE_SECRET, ledger: freshLedger(),
    });
    assert.equal(r.ok, true);
    assert.equal(r.code, "ROLEBOUND_ALLOWED");
  });

  test("REPLAY: same grant verified twice is rejected on second use (REPLAY)", () => {
    const { grant } = issueStandardGrant();
    const executor = mintClaims("executor1", [ROLES.EXECUTOR]);
    const ledger = freshLedger();
    const first = verifyRoleBoundGrant({
      grant, executorClaims: executor,
      action: "EXECUTE_STRATEGY", strategyId: "S-42", strategyVersion: "v3",
      contentHash: hex64("payload-1"), nowMs: NOW + 1000, secret: ROLE_SECRET, ledger,
    });
    assert.equal(first.ok, true);
    const second = verifyRoleBoundGrant({
      grant, executorClaims: executor,
      action: "EXECUTE_STRATEGY", strategyId: "S-42", strategyVersion: "v3",
      contentHash: hex64("payload-1"), nowMs: NOW + 1000, secret: ROLE_SECRET, ledger,
    });
    assert.equal(second.ok, false);
    assert.equal(second.code, ROLE_CODES.REPLAY); // ROLEBOUND_REPLAYED
  });

  test("REPLAY across instances via shared D1 ledger path (cross-instance)", () => {
    const { grant } = issueStandardGrant();
    const executor = mintClaims("executor1", [ROLES.EXECUTOR]);
    const shared = new D1SharedStore();
    const ledgerA = new AuthorizationLedger(shared);
    const ledgerB = new AuthorizationLedger(shared); // different worker, same store
    const onA = verifyRoleBoundGrant({
      grant, executorClaims: executor,
      action: "EXECUTE_STRATEGY", strategyId: "S-42", strategyVersion: "v3",
      contentHash: hex64("payload-1"), nowMs: NOW + 1000, secret: ROLE_SECRET, ledger: ledgerA,
    });
    assert.equal(onA.ok, true);
    const onB = verifyRoleBoundGrant({
      grant, executorClaims: executor,
      action: "EXECUTE_STRATEGY", strategyId: "S-42", strategyVersion: "v3",
      contentHash: hex64("payload-1"), nowMs: NOW + 1000, secret: ROLE_SECRET, ledger: ledgerB,
    });
    assert.equal(onB.ok, false);
    assert.equal(onB.code, ROLE_CODES.REPLAY);
  });
});

describe("C3 role-bound grant forgery (roles.mjs)", () => {
  test("WRONG role secret => BAD_SIGNATURE", () => {
    const { grant } = issueStandardGrant({ secret: ROLE_SECRET });
    const executor = mintClaims("executor1", [ROLES.EXECUTOR]);
    const r = verifyRoleBoundGrant({
      grant, executorClaims: executor,
      action: "EXECUTE_STRATEGY", strategyId: "S-42", strategyVersion: "v3",
      contentHash: hex64("payload-1"), nowMs: NOW + 1000, secret: ROLE_SECRET_OTHER, ledger: freshLedger(),
    });
    assert.equal(r.ok, false);
    assert.equal(r.code, ROLE_CODES.BAD_SIGNATURE); // ROLEBOUND_SIGNATURE_INVALID
  });

  test("tampered executorId after signing => BAD_SIGNATURE", () => {
    const { grant } = issueStandardGrant();
    const tampered = { ...grant, executorId: "executor9" }; // not re-signed
    const executor = mintClaims("executor9", [ROLES.EXECUTOR]);
    const r = verifyRoleBoundGrant({
      grant: tampered, executorClaims: executor,
      action: "EXECUTE_STRATEGY", strategyId: "S-42", strategyVersion: "v3",
      contentHash: hex64("payload-1"), nowMs: NOW + 1000, secret: ROLE_SECRET, ledger: freshLedger(),
    });
    assert.equal(r.ok, false);
    assert.equal(r.code, ROLE_CODES.BAD_SIGNATURE);
  });

  test("tampered contentHash after signing => BAD_SIGNATURE", () => {
    const { grant } = issueStandardGrant();
    const tampered = { ...grant, contentHash: hex64("payload-other") };
    const executor = mintClaims("executor1", [ROLES.EXECUTOR]);
    const r = verifyRoleBoundGrant({
      grant: tampered, executorClaims: executor,
      action: "EXECUTE_STRATEGY", strategyId: "S-42", strategyVersion: "v3",
      contentHash: hex64("payload-other"), nowMs: NOW + 1000, secret: ROLE_SECRET, ledger: freshLedger(),
    });
    assert.equal(r.ok, false);
    assert.equal(r.code, ROLE_CODES.BAD_SIGNATURE);
  });

  test("issuance by a NON-APPROVER is rejected (MISSING_ROLE)", () => {
    const notApprover = mintClaims("nobody", [ROLES.EXECUTOR]);
    const r = issueRoleBoundGrant({
      approverClaims: notApprover,
      action: "EXECUTE_STRATEGY", strategyId: "S-42", strategyVersion: "v3",
      contentHash: hex64("payload-1"), executorId: "executor1", ttlMs: TTL,
      nowMs: NOW, secret: ROLE_SECRET, nonce: "n-missing-role",
    });
    assert.equal(r.ok, false);
    assert.equal(r.code, ROLE_CODES.MISSING_ROLE); // ROLE_REQUIRED
  });

  test("issuance without MFA on approver is rejected (MFA_NOT_SATISFIED)", () => {
    const token = issueIdToken({ sub: "approverNoMfa", roles: [ROLES.APPROVER], amr: ["pwd"], issuerSecret: IDP_SECRET, nowMs: NOW });
    const claims = verifyIdToken(token, { issuerSecret: IDP_SECRET, nowMs: NOW }).claims;
    const r = issueRoleBoundGrant({
      approverClaims: claims,
      action: "EXECUTE_STRATEGY", strategyId: "S-42", strategyVersion: "v3",
      contentHash: hex64("payload-1"), executorId: "executor1", ttlMs: TTL,
      nowMs: NOW, secret: ROLE_SECRET, nonce: "n-no-mfa",
    });
    assert.equal(r.ok, false);
    assert.equal(r.code, ROLE_CODES.MFA_NOT_SATISFIED);
  });

  test("verify by a NON-EXECUTOR (no EXECUTOR role) is rejected (MISSING_ROLE)", () => {
    const { grant } = issueStandardGrant();
    const notExecutor = mintClaims("executor1", [ROLES.APPROVER]); // wrong role for execution
    const r = verifyRoleBoundGrant({
      grant, executorClaims: notExecutor,
      action: "EXECUTE_STRATEGY", strategyId: "S-42", strategyVersion: "v3",
      contentHash: hex64("payload-1"), nowMs: NOW + 1000, secret: ROLE_SECRET, ledger: freshLedger(),
    });
    assert.equal(r.ok, false);
    assert.equal(r.code, ROLE_CODES.MISSING_ROLE);
  });

  test("verify with MFA-less executor is rejected (MFA_NOT_SATISFIED)", () => {
    const { grant } = issueStandardGrant();
    const token = issueIdToken({ sub: "executor1", roles: [ROLES.EXECUTOR], amr: ["pwd"], issuerSecret: IDP_SECRET, nowMs: NOW });
    const claims = verifyIdToken(token, { issuerSecret: IDP_SECRET, nowMs: NOW }).claims;
    const r = verifyRoleBoundGrant({
      grant, executorClaims: claims,
      action: "EXECUTE_STRATEGY", strategyId: "S-42", strategyVersion: "v3",
      contentHash: hex64("payload-1"), nowMs: NOW + 1000, secret: ROLE_SECRET, ledger: freshLedger(),
    });
    assert.equal(r.ok, false);
    assert.equal(r.code, ROLE_CODES.MFA_NOT_SATISFIED);
  });
});

describe("C3 separation + cross-identity replay (roles.mjs)", () => {
  test("approverId === executorId is rejected at issuance (SEPARATION_VIOLATION)", () => {
    const same = "actor-same";
    const approver = mintClaims(same, [ROLES.APPROVER]);
    const r = issueRoleBoundGrant({
      approverClaims: approver,
      action: "EXECUTE_STRATEGY", strategyId: "S-42", strategyVersion: "v3",
      contentHash: hex64("payload-1"), executorId: same, ttlMs: TTL,
      nowMs: NOW, secret: ROLE_SECRET, nonce: "n-sep",
    });
    assert.equal(r.ok, false);
    assert.equal(r.code, ROLE_CODES.SEPARATION_VIOLATION); // ACTOR_SEPARATION_VIOLATION
  });

  test("cross-identity replay: grant bound to executor1 verified by executor2 => EXECUTOR_MISMATCH", () => {
    const { grant } = issueStandardGrant({ executorSub: "executor1" });
    const other = mintClaims("executor2", [ROLES.EXECUTOR]); // valid EXECUTOR, wrong identity
    const r = verifyRoleBoundGrant({
      grant, executorClaims: other,
      action: "EXECUTE_STRATEGY", strategyId: "S-42", strategyVersion: "v3",
      contentHash: hex64("payload-1"), nowMs: NOW + 1000, secret: ROLE_SECRET, ledger: freshLedger(),
    });
    assert.equal(r.ok, false);
    assert.equal(r.code, ROLE_CODES.EXECUTOR_MISMATCH); // EXECUTOR_IDENTITY_MISMATCH
  });

  test("scope mismatch (wrong strategyId) is rejected (SCOPE_MISMATCH)", () => {
    const { grant } = issueStandardGrant();
    const executor = mintClaims("executor1", [ROLES.EXECUTOR]);
    const r = verifyRoleBoundGrant({
      grant, executorClaims: executor,
      action: "EXECUTE_STRATEGY", strategyId: "S-999", strategyVersion: "v3",
      contentHash: hex64("payload-1"), nowMs: NOW + 1000, secret: ROLE_SECRET, ledger: freshLedger(),
    });
    assert.equal(r.ok, false);
    assert.equal(r.code, ROLE_CODES.SCOPE_MISMATCH);
  });

  test("expired grant is rejected (EXPIRED)", () => {
    const { grant } = issueStandardGrant();
    const executor = mintClaims("executor1", [ROLES.EXECUTOR]);
    const r = verifyRoleBoundGrant({
      grant, executorClaims: executor,
      action: "EXECUTE_STRATEGY", strategyId: "S-42", strategyVersion: "v3",
      contentHash: hex64("payload-1"), nowMs: NOW + TTL + 1, secret: ROLE_SECRET, ledger: freshLedger(),
    });
    assert.equal(r.ok, false);
    assert.equal(r.code, ROLE_CODES.EXPIRED);
  });
});

// ===========================================================================
// D1 — AUTHORIZATION LEDGER: replay + cross-instance + concurrency atomicity
// ===========================================================================
describe("D1 ledger nonce consumption (ledger.mjs)", () => {
  test("same nonce consumed twice => first CONSUMED, second REPLAY", () => {
    const ledger = freshLedger();
    const a = ledger.consumeNonce({ nonce: "nx", grantId: "g", actorHash: "h", strategyId: "s", nowMs: NOW });
    assert.equal(a.ok, true);
    assert.equal(a.code, LEDGER_CODES.CONSUMED);
    const b = ledger.consumeNonce({ nonce: "nx", grantId: "g", actorHash: "h", strategyId: "s", nowMs: NOW });
    assert.equal(b.ok, false);
    assert.equal(b.code, LEDGER_CODES.REPLAY); // GRANT_REPLAYED
  });

  test("cross-instance REPLAY: shared store, instance A consumes, B replayed", () => {
    const shared = new D1SharedStore();
    const a = new AuthorizationLedger(shared);
    const b = new AuthorizationLedger(shared);
    const first = a.consumeNonce({ nonce: "ny", grantId: "g", actorHash: "h", strategyId: "s", nowMs: NOW });
    assert.equal(first.ok, true);
    const second = b.consumeNonce({ nonce: "ny", grantId: "g", actorHash: "h", strategyId: "s", nowMs: NOW });
    assert.equal(second.ok, false);
    assert.equal(second.code, LEDGER_CODES.REPLAY);
  });

  test("CONCURRENCY: Promise.all on one nonce => exactly ONE CONSUMED", async () => {
    const shared = new D1SharedStore();
    const instances = Array.from({ length: 12 }, () => new AuthorizationLedger(shared));
    const results = await Promise.all(
      instances.map((led) => Promise.resolve(led.consumeNonce({
        nonce: "race-nonce", grantId: "g", actorHash: "h", strategyId: "s", nowMs: NOW,
      }))),
    );
    const consumed = results.filter((r) => r.ok && r.code === LEDGER_CODES.CONSUMED);
    const replayed = results.filter((r) => !r.ok && r.code === LEDGER_CODES.REPLAY);
    assert.equal(consumed.length, 1, "exactly one successful consumption");
    assert.equal(replayed.length, results.length - 1, "all others replayed");
    assert.equal(shared.hasNonce("race-nonce"), true);
  });

  test("revocation is durable and visible cross-instance", () => {
    const shared = new D1SharedStore();
    const a = new AuthorizationLedger(shared);
    const b = new AuthorizationLedger(shared);
    a.revokeGrant({ grantId: "g-rev", revokedBy: "admin", nowMs: NOW });
    assert.equal(a.isRevoked("g-rev"), true);
    assert.equal(b.isRevoked("g-rev"), true); // visible on the other instance
  });

  test("append integrity: anchors are append-only, ordered, and lastAnchor consistent", () => {
    const shared = new D1SharedStore();
    const tips = ["t0", "t1", "t2", "t3"];
    for (const tip of tips) shared.appendAnchor({ tipHash: tip, anchoredAtMs: NOW });
    assert.equal(shared.anchorCount(), tips.length);
    // recomputed tip = last appended, integrity check
    const last = shared.lastAnchor();
    assert.equal(last.tipHash, tips[tips.length - 1]);
    assert.equal(last.seq, tips.length - 1);
    const snap = shared.snapshot();
    assert.deepEqual(snap.anchors.map((a) => a.tipHash), tips);
  });
});
