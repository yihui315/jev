// JEV real-backends — contract test.
//
// Verifies, WITHOUT any network / real credentials:
//   1. Every adapter is FAIL-CLOSED: unconfigured => throws NotConfiguredError
//      (never a mock result).
//   2. The unblock path is wired: with env present, adapters construct and
//      realBackendsAvailable() flips to true.
//   3. The D1 adapter's atomic-consume logic is correct against a SQL-semantics
//      fake client (first insert CONSUMED, replay => REPLAY), proving the
//      UNIQUE(nonce) + INSERT OR IGNORE + changes===0 design.
//   4. The WORM adapter detects a recomputed-tip mismatch fail-closed.
//   5. The IdP adapter never silently returns ok — verifyToken refuses to run
//      without a live provider.
//
// NOTE: this test does NOT exercise real network calls. The fake D1/WORM clients
// simulate SQL/append semantics locally to validate adapter logic only.

import assert from "node:assert/strict";
import { test, describe } from "node:test";

import {
  realBackendsAvailable,
  RealIdP, RealD1Ledger, RealWormAnchor, NotConfiguredError,
  loadRealIdPConfig, loadRealD1Config, loadRealWormConfig,
} from "../index.mjs";

// ---- fake D1 client: minimal SQL-semantics interpreter ---------------------
function makeFakeD1() {
  const nonces = new Map();
  const revocations = new Map();
  const anchors = [];
  const runInsertNonce = (a) => {
    const [nonce, grantId, actorHash, strategyId, consumedAtMs] = a;
    if (nonces.has(nonce)) return { changes: 0 };
    nonces.set(nonce, { grantId, actorHash, strategyId, consumedAtMs });
    return { changes: 1 };
  };
  const runInsertRev = (a) => {
    const [grantId, revokedBy, revokedAtMs] = a;
    if (revocations.has(grantId)) return { changes: 0 };
    revocations.set(grantId, { revokedBy, revokedAtMs });
    return { changes: 1 };
  };
  const runInsertAnchor = (a) => {
    const [tipHash, anchoredAtMs] = a;
    const seq = anchors.length + 1;
    anchors.push({ seq, tipHash, anchoredAtMs });
    return { changes: 1, lastRowId: seq };
  };
  return {
    prepare(sql) {
      return {
        bind(...args) {
          return {
            async run() {
              if (sql.startsWith("INSERT OR IGNORE INTO auth_nonces")) return runInsertNonce(args);
              if (sql.startsWith("INSERT OR IGNORE INTO auth_revocations")) return runInsertRev(args);
              if (sql.startsWith("INSERT INTO audit_anchors")) return runInsertAnchor(args);
              return { changes: 0 };
            },
            async first() {
              if (sql.startsWith("SELECT 1 FROM auth_nonces")) return nonces.has(args[0]) ? { "1": 1 } : null;
              if (sql.startsWith("SELECT 1 FROM auth_revocations")) return revocations.has(args[0]) ? { "1": 1 } : null;
              if (sql.startsWith("SELECT seq,tip_hash,anchored_at_ms FROM audit_anchors")) {
                return anchors.length ? anchors[anchors.length - 1] : null;
              }
              if (sql.startsWith("SELECT COUNT(*) AS c FROM audit_anchors")) return { c: anchors.length };
              return null;
            },
          };
        },
      };
    },
  };
}

// ---- fake WORM client ------------------------------------------------------
function makeFakeWorm(initial) {
  let store = initial ? [initial] : [];
  return {
    async append({ tipHash, anchoredAtMs }) {
      const entry = { seq: store.length + 1, tipHash, anchoredAtMs };
      store.push(entry);
      return entry;
    },
    async last() { return store.length ? store[store.length - 1] : null; },
    async count() { return store.length; },
  };
}

const CONFIGURED_ENV = {
  REAL_IDP_ISSUER_URL: "https://idp.example.com",
  REAL_IDP_JWKS_URL: "https://idp.example.com/jwks",
  REAL_IDP_AUDIENCE: "jev",
  REAL_D1_ACCOUNT_ID: "acc",
  REAL_D1_DATABASE_ID: "db",
  REAL_D1_API_TOKEN: "tok",
  REAL_WORM_ENDPOINT: "https://worm.example.com",
  REAL_WORM_API_KEY: "key",
};

describe("realBackendsAvailable", () => {
  test("false across the board with empty env", () => {
    const r = realBackendsAvailable({});
    assert.equal(r.all, false);
    assert.equal(r.idp, false);
    assert.equal(r.d1, false);
    assert.equal(r.worm, false);
  });
  test("all true with every required env present", () => {
    const r = realBackendsAvailable(CONFIGURED_ENV);
    assert.equal(r.all, true);
    assert.equal(r.idp, true);
    assert.equal(r.d1, true);
    assert.equal(r.worm, true);
  });
});

describe("fail-closed (unconfigured => NotConfiguredError)", () => {
  test("RealIdP throws when unconfigured", () => {
    assert.throws(() => new RealIdP(loadRealIdPConfig({})), NotConfiguredError);
  });
  test("RealD1Ledger throws when unconfigured", () => {
    assert.throws(() => new RealD1Ledger(loadRealD1Config({}), makeFakeD1()), NotConfiguredError);
  });
  test("RealWormAnchor throws when unconfigured", () => {
    assert.throws(() => new RealWormAnchor(loadRealWormConfig({}), makeFakeWorm()), NotConfiguredError);
  });
  test("RealD1Ledger throws TypeError (not NotConfigured) without a client even if configured", () => {
    assert.throws(() => new RealD1Ledger(loadRealD1Config(CONFIGURED_ENV)), TypeError);
  });
});

describe("unblock path wired (configured => constructs + behaves)", () => {
  test("RealIdP constructs and verifyToken refuses to run without live provider", async () => {
    const idp = new RealIdP(loadRealIdPConfig(CONFIGURED_ENV));
    assert.equal(idp.config.configured, true);
    await assert.rejects(() => idp.verifyToken("any.token.here"), /live provider integration/);
  });

  test("RealD1Ledger: first consume CONSUMED, replay => REPLAY (UNIQUE + INSERT OR IGNORE)", async () => {
    const d1 = new RealD1Ledger(loadRealD1Config(CONFIGURED_ENV), makeFakeD1());
    const first = await d1.consumeNonce({
      nonce: "n1", grantId: "g1", actorHash: "a1", strategyId: "s1", nowMs: 1000,
    });
    assert.equal(first.ok, true);
    assert.equal(first.code, "NONCE_CONSUMED");
    const replay = await d1.consumeNonce({
      nonce: "n1", grantId: "g1", actorHash: "a1", strategyId: "s1", nowMs: 1001,
    });
    assert.equal(replay.ok, false);
    assert.equal(replay.code, "REPLAY");
    assert.equal(await d1.hasNonce("n1"), true);
  });

  test("RealD1Ledger: revokeGrant idempotent + anchors", async () => {
    const d1 = new RealD1Ledger(loadRealD1Config(CONFIGURED_ENV), makeFakeD1());
    const r1 = await d1.revokeGrant({ grantId: "gX", revokedBy: "admin", nowMs: 1 });
    assert.equal(r1.ok, true);
    const r2 = await d1.revokeGrant({ grantId: "gX", revokedBy: "admin", nowMs: 2 });
    assert.equal(r2.ok, false);
    assert.equal(r2.code, "ALREADY_REVOKED");
    assert.equal(await d1.isRevoked("gX"), true);
    await d1.appendAnchor({ tipHash: "h1", nowMs: 5 });
    const last = await d1.lastAnchor();
    assert.equal(last.tipHash, "h1");
    assert.equal(await d1.anchorCount(), 1);
  });

  test("RealWormAnchor: append + mismatch detected fail-closed", async () => {
    const worm = new RealWormAnchor(loadRealWormConfig(CONFIGURED_ENV), makeFakeWorm());
    await worm.anchor("tip-A", 10);
    const okVerify = await worm.verifyOnStartup({ recomputedTipHash: "tip-A", auditStateKnown: true });
    assert.equal(okVerify.ok, true);
    const badVerify = await worm.verifyOnStartup({ recomputedTipHash: "tip-B", auditStateKnown: true });
    assert.equal(badVerify.ok, false);
    assert.equal(badVerify.code, "AUDIT_CHAIN_ANCHOR_MISMATCH");
    const unreadable = await worm.verifyOnStartup({ recomputedTipHash: "x", auditStateKnown: false });
    assert.equal(unreadable.ok, false);
    assert.equal(unreadable.code, "AUDIT_CHAIN_ANCHOR_MISMATCH");
  });
});
