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
import crypto from "node:crypto";

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

// ---- offline RSA signing harness (no network, no real IdP) ------------------
// Generates a real RSA key pair, exposes a JWKS doc + a sign() that produces a
// genuinely RS256-signed JWT, and a fake fetch that returns the JWKS. This lets
// verifyToken be exercised end-to-end offline without any credentials.
function makeRsaSigner() {
  const { publicKey, privateKey } = crypto.generateKeyPairSync("rsa", { modulusLength: 2048 });
  const jwk = publicKey.export({ format: "jwk" });
  const kid = "k1";
  const jwksDoc = { keys: [{ ...jwk, kid, use: "sig", alg: "RS256" }] };
  const b64u = (obj) => Buffer.from(typeof obj === "string" ? obj : JSON.stringify(obj)).toString("base64url");
  const sign = (payload) => {
    const header = { alg: "RS256", kid, typ: "JWT" };
    const h = b64u(header);
    const p = b64u(payload);
    const sig = crypto.sign("sha256", Buffer.from(`${h}.${p}`), privateKey);
    return `${h}.${p}.${sig.toString("base64url")}`;
  };
  const fakeFetch = async () => ({ ok: true, json: async () => jwksDoc });
  return { jwksDoc, fakeFetch, sign };
}

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
  test("RealIdP constructs with configured env", async () => {
    const idp = new RealIdP(loadRealIdPConfig(CONFIGURED_ENV));
    assert.equal(idp.config.configured, true);
  });

  describe("RealIdP.verifyToken (implemented, offline via injected fetch)", () => {
    const H = makeRsaSigner();
    const baseClaims = (over = {}) => ({
      iss: CONFIGURED_ENV.REAL_IDP_ISSUER_URL,
      aud: CONFIGURED_ENV.REAL_IDP_AUDIENCE,
      exp: Math.floor((Date.now() + 3600_000) / 1000),
      sub: "user-1",
      amr: ["mfa"],
      ...over,
    });
    const mkIdP = () =>
      new RealIdP({ ...loadRealIdPConfig(CONFIGURED_ENV), fetchImpl: H.fakeFetch });

    test("malformed token => MALFORMED_TOKEN", async () => {
      const r = await mkIdP().verifyToken("not-a-jwt");
      assert.equal(r.ok, false);
      assert.equal(r.code, "MALFORMED_TOKEN");
    });

    test("valid signed token => IDP_VERIFIED with claims", async () => {
      const r = await mkIdP().verifyToken(H.sign(baseClaims()));
      assert.equal(r.ok, true);
      assert.equal(r.code, "IDP_VERIFIED");
      assert.equal(r.claims.sub, "user-1");
    });

    test("tampered signature => SIGNATURE_INVALID", async () => {
      const tok = H.sign(baseClaims());
      const [h, p, s] = tok.split(".");
      const bad = `${h}.${p}.${s.slice(0, -1)}A`;
      const r = await mkIdP().verifyToken(bad);
      assert.equal(r.ok, false);
      assert.equal(r.code, "SIGNATURE_INVALID");
    });

    test("expired token => EXP_EXPIRED", async () => {
      const r = await mkIdP().verifyToken(
        H.sign(baseClaims({ exp: Math.floor((Date.now() - 1000) / 1000) })),
      );
      assert.equal(r.ok, false);
      assert.equal(r.code, "EXP_EXPIRED");
    });

    test("missing MFA amr => AMR_MISSING", async () => {
      const r = await mkIdP().verifyToken(H.sign(baseClaims({ amr: ["pwd"] })));
      assert.equal(r.ok, false);
      assert.equal(r.code, "AMR_MISSING");
      assert.deepEqual(r.detail.missing, ["mfa"]);
    });

    test("wrong audience => AUD_MISMATCH", async () => {
      const r = await mkIdP().verifyToken(H.sign(baseClaims({ aud: "other-aud" })));
      assert.equal(r.ok, false);
      assert.equal(r.code, "AUD_MISMATCH");
    });

    test("wrong issuer => ISS_MISMATCH", async () => {
      const r = await mkIdP().verifyToken(H.sign(baseClaims({ iss: "https://evil.example.com" })));
      assert.equal(r.ok, false);
      assert.equal(r.code, "ISS_MISMATCH");
    });

    test("unsupported alg (HS256) => UNSUPPORTED_ALG (checked before crypto.verify)", async () => {
      const header = { alg: "HS256", kid: "k1" };
      const h = Buffer.from(JSON.stringify(header)).toString("base64url");
      const p = Buffer.from(JSON.stringify(baseClaims())).toString("base64url");
      const r = await mkIdP().verifyToken(`${h}.${p}.AAAA`);
      assert.equal(r.ok, false);
      assert.equal(r.code, "UNSUPPORTED_ALG");
    });
  });

  describe("RealIdP.verifyToken fail-closed network path (no injected fetch)", () => {
    test("attempts the real IdP JWKS endpoint and fails closed (JWKS_UNAVAILABLE)", async () => {
      // Uses the default global fetch against the configured (unreachable) URL.
      const idp = new RealIdP(loadRealIdPConfig(CONFIGURED_ENV));
      const header = { alg: "RS256", kid: "k1" };
      const h = Buffer.from(JSON.stringify(header)).toString("base64url");
      const p = Buffer.from(JSON.stringify({ iss: "x" })).toString("base64url");
      const r = await idp.verifyToken(`${h}.${p}.AAAA`);
      assert.equal(r.ok, false);
      assert.equal(r.code, "JWKS_UNAVAILABLE");
    });
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
