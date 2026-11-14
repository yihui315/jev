// JEV Phase B — Aegis F-19 tamper/rollback + RealD1Ledger cross-instance audit.
//
// AUDIT SCOPE: real-backends/real-worm.mjs (F-19 WORM anchor) +
// real-backends/real-d1.mjs (RealD1Ledger cross-instance replay / revocation /
// anchor integrity).
//
// HARD CONSTRAINTS (Aegis / order §4):
//   * No production code modified; this file ONLY exercises the adapters.
//   * No network, no plaintext secrets / real credentials, no real trades.
//   * Every D1 / WORM client is an in-memory FAKE injected via the constructor
//     boundary (the module never opens network connections itself).
//   * F-19 must be FAIL-CLOSED: recomputed chain tip != external anchor => reject.
//
// FOUR CLASSES (order-mandated):
//   1. Replay — covered here for RealD1Ledger cross-instance (consumed on A => REPLAY on B).
//   2. Identity forgery — (covered in phase-b/tests/security.test.mjs).
//   3. Concurrency — (covered in phase-b/tests/security.test.mjs).
//   4. Tamper/rollback — F-19: verifyOnStartup mismatch => AUDIT_CHAIN_ANCHOR_MISMATCH,
//      including a mock client whose in-memory chain tip was MUTATED (simulated
//      external tamper / rollback). Plus RealD1Ledger append integrity.

import assert from "node:assert/strict";
import { test, describe } from "node:test";

import { RealWormAnchor, loadRealWormConfig } from "../real-worm.mjs";
import { RealD1Ledger, loadRealD1Config } from "../real-d1.mjs";
import { NotConfiguredError } from "../errors.mjs";

const CONFIGURED_ENV = {
  REAL_D1_ACCOUNT_ID: "acc",
  REAL_D1_DATABASE_ID: "db",
  REAL_D1_API_TOKEN: "tok",
  REAL_WORM_ENDPOINT: "https://worm.example.com",
  REAL_WORM_API_KEY: "key",
};

// ---- fake WORM client (mutable in-memory store for tamper simulation) -------
function makeFakeWorm(initial = null) {
  const store = initial ? [initial] : [];
  return {
    // expose the live array so the audit can MUTATE it (simulating external
    // tamper or rollback of the write-once store) — never possible on a real
    // object-lock / RFC3161 backend, which is exactly what we are proving here.
    _store: store,
    async append({ tipHash, anchoredAtMs }) {
      const entry = { seq: store.length + 1, tipHash, anchoredAtMs };
      store.push(entry);
      return entry;
    },
    async last() { return store.length ? store[store.length - 1] : null; },
    async count() { return store.length; },
  };
}

// ---- fake D1 client with SHARED maps (cross-instance single source of truth)-
function makeSharedFakeD1() {
  const nonces = new Map();
  const revocations = new Map();
  const anchors = [];
  return {
    _nonces: nonces, _revocations: revocations, _anchors: anchors,
    prepare(sql) {
      return {
        bind(...args) {
          return {
            async run() {
              if (sql.startsWith("INSERT OR IGNORE INTO auth_nonces")) {
                const [nonce] = args;
                if (nonces.has(nonce)) return { changes: 0 };
                nonces.set(nonce, { args });
                return { changes: 1 };
              }
              if (sql.startsWith("INSERT OR IGNORE INTO auth_revocations")) {
                const [grantId] = args;
                if (revocations.has(grantId)) return { changes: 0 };
                revocations.set(grantId, { args });
                return { changes: 1 };
              }
              if (sql.startsWith("INSERT INTO audit_anchors")) {
                const [tipHash, anchoredAtMs] = args;
                const seq = anchors.length + 1;
                anchors.push({ seq, tipHash, anchoredAtMs });
                return { changes: 1, lastRowId: seq };
              }
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

// ===========================================================================
// F-19 — WORM tamper / rollback (fail-closed)
// ===========================================================================
describe("F-19 WORM tamper/rollback (real-worm.mjs)", () => {
  test("fail-closed when unconfigured (NotConfiguredError, never a mock ok)", () => {
    assert.throws(() => new RealWormAnchor(loadRealWormConfig({}), makeFakeWorm()), NotConfiguredError);
  });

  test("verifyOnStartup: consistent recomputed tip => AUDIT_ANCHOR_VERIFIED", async () => {
    const worm = new RealWormAnchor(loadRealWormConfig(CONFIGURED_ENV), makeFakeWorm({ seq: 1, tipHash: "tip-A", anchoredAtMs: 10 }));
    const r = await worm.verifyOnStartup({ recomputedTipHash: "tip-A", auditStateKnown: true });
    assert.equal(r.ok, true);
    assert.equal(r.code, "AUDIT_ANCHOR_VERIFIED");
  });

  test("F-19 fail-closed: recomputed chain tip != external anchor => AUDIT_CHAIN_ANCHOR_MISMATCH", async () => {
    const worm = new RealWormAnchor(loadRealWormConfig(CONFIGURED_ENV), makeFakeWorm({ seq: 1, tipHash: "tip-A", anchoredAtMs: 10 }));
    const r = await worm.verifyOnStartup({ recomputedTipHash: "tip-B", auditStateKnown: true });
    assert.equal(r.ok, false);
    assert.equal(r.code, "AUDIT_CHAIN_ANCHOR_MISMATCH");
    assert.ok(r.detail && r.detail.anchored === "tip-A" && r.detail.recomputed === "tip-B");
  });

  test("F-19 fail-closed: MUTATED in-memory chain tip (simulated external tamper) => mismatch", async () => {
    const client = makeFakeWorm();
    await client.append({ tipHash: "good-tip", anchoredAtMs: 10 });
    // attacker rewrites the external write-once store's chain tip in place
    client._store[client._store.length - 1].tipHash = "tampered-tip";
    const worm = new RealWormAnchor(loadRealWormConfig(CONFIGURED_ENV), client);
    // the local ledger recomputes the honest tip; it must NOT match the tampered one
    const r = await worm.verifyOnStartup({ recomputedTipHash: "good-tip", auditStateKnown: true });
    assert.equal(r.ok, false);
    assert.equal(r.code, "AUDIT_CHAIN_ANCHOR_MISMATCH");
  });

  test("F-19 fail-closed: unreadable audit chain => AUDIT_CHAIN_ANCHOR_MISMATCH", async () => {
    const worm = new RealWormAnchor(loadRealWormConfig(CONFIGURED_ENV), makeFakeWorm());
    const r = await worm.verifyOnStartup({ recomputedTipHash: "x", auditStateKnown: false });
    assert.equal(r.ok, false);
    assert.equal(r.code, "AUDIT_CHAIN_ANCHOR_MISMATCH");
  });

  test("F-19 empty chain (no anchor, empty recompute) => AUDIT_ANCHOR_NONE (clean)", async () => {
    const worm = new RealWormAnchor(loadRealWormConfig(CONFIGURED_ENV), makeFakeWorm());
    const r = await worm.verifyOnStartup({ recomputedTipHash: "", auditStateKnown: true });
    assert.equal(r.ok, true);
    assert.equal(r.code, "AUDIT_ANCHOR_NONE");
  });
});

// ===========================================================================
// RealD1Ledger — cross-instance replay / revocation / anchor integrity
// ===========================================================================
describe("RealD1Ledger cross-instance (real-d1.mjs)", () => {
  test("fail-closed when unconfigured (NotConfiguredError)", () => {
    assert.throws(() => new RealD1Ledger(loadRealD1Config({}), makeSharedFakeD1()), NotConfiguredError);
  });

  test("cross-instance REPLAY: nonce consumed on instance A is REPLAY on instance B", async () => {
    const client = makeSharedFakeD1(); // single shared store
    const a = new RealD1Ledger(loadRealD1Config(CONFIGURED_ENV), client);
    const b = new RealD1Ledger(loadRealD1Config(CONFIGURED_ENV), client);
    const first = await a.consumeNonce({ nonce: "n-shared", grantId: "g", actorHash: "h", strategyId: "s", nowMs: 1000 });
    assert.equal(first.ok, true);
    assert.equal(first.code, "NONCE_CONSUMED");
    const replay = await b.consumeNonce({ nonce: "n-shared", grantId: "g", actorHash: "h", strategyId: "s", nowMs: 1001 });
    assert.equal(replay.ok, false);
    assert.equal(replay.code, "REPLAY");
    assert.equal(await b.hasNonce("n-shared"), true);
  });

  test("revocation durable + visible across instances (isRevoked)", async () => {
    const client = makeSharedFakeD1();
    const a = new RealD1Ledger(loadRealD1Config(CONFIGURED_ENV), client);
    const b = new RealD1Ledger(loadRealD1Config(CONFIGURED_ENV), client);
    const r1 = await a.revokeGrant({ grantId: "gX", revokedBy: "admin", nowMs: 1 });
    assert.equal(r1.ok, true);
    const r2 = await a.revokeGrant({ grantId: "gX", revokedBy: "admin", nowMs: 2 });
    assert.equal(r2.ok, false); // idempotent
    assert.equal(r2.code, "ALREADY_REVOKED");
    assert.equal(await a.isRevoked("gX"), true);
    assert.equal(await b.isRevoked("gX"), true); // visible on the other instance
  });

  test("concurrency: Promise.all of consumeNonce on one nonce (shared client) => exactly ONE CONSUMED", async () => {
    const client = makeSharedFakeD1();
    const instances = Array.from({ length: 12 }, () => new RealD1Ledger(loadRealD1Config(CONFIGURED_ENV), client));
    const results = await Promise.all(
      instances.map((d1) => d1.consumeNonce({ nonce: "race-d1", grantId: "g", actorHash: "h", strategyId: "s", nowMs: 1 })),
    );
    const consumed = results.filter((r) => r.ok && r.code === "NONCE_CONSUMED");
    const replayed = results.filter((r) => !r.ok && r.code === "REPLAY");
    assert.equal(consumed.length, 1, "exactly one successful consumption");
    assert.equal(replayed.length, results.length - 1, "all others replayed");
  });

  test("append integrity: anchors are ordered + anchorCount consistent + lastAnchor matches", async () => {
    const d1 = new RealD1Ledger(loadRealD1Config(CONFIGURED_ENV), makeSharedFakeD1());
    const tips = ["tip-0", "tip-1", "tip-2", "tip-3"];
    for (const t of tips) await d1.appendAnchor({ tipHash: t, nowMs: 5 });
    assert.equal(await d1.anchorCount(), tips.length);
    const last = await d1.lastAnchor();
    assert.equal(last.tipHash, tips[tips.length - 1]);
    assert.equal(last.seq, tips.length); // 1-based AUTOINCREMENT
  });
});
