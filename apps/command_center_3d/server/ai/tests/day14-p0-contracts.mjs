/**
 * P0-F Independent QA — CONTRACT tests (T2c / T3-ref / T6a / T7 / T8-engine / schema).
 *
 * These tests target STABLE / FROZEN modules that trading-engineer is NOT allowed
 * to modify (risk-gate.mjs, decision-schema.mjs, paper-server.mjs::__test). They
 * therefore run NOW and produce VERIFIED results independent of the runner fix.
 *
 * Run: node day14-p0-contracts.mjs
 *      (exit code 0 = all pass, 1 = any fail)
 *
 * Discipline: pure assertions, no existence checks, no qa.pass, no network.
 */
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";

import {
  harness, sha256Hex, chainLines, verifyChainFile, writeJsonl, logEvidence,
  REPO, AI_DIR, LIB_DIR,
} from "./day14-p0-lib.mjs";

// Import the real (frozen) modules under test — resolved from the repo, not a copy.
const { validateDecision, DECISION_SCHEMA_VERSION } = await import(
  pathToFileURL(join(AI_DIR, "decision-schema.mjs")).href
);
const { riskCheck, RISK_CODES, DEFAULT_RISK_LIMITS, RISK_ALLOWED_SYMBOLS } = await import(
  pathToFileURL(join(AI_DIR, "risk-gate.mjs")).href
);
const { __test: engine } = await import(
  pathToFileURL(join(LIB_DIR, "paper-server.mjs")).href
);

const NOW = 1_700_000_000_000; // fixed injectable clock (2023-11-14T22:13:20Z)
const iso = (ms) => new Date(ms).toISOString();

function mkBuy(over = {}) {
  return {
    schemaVersion: DECISION_SCHEMA_VERSION,
    strategyId: "qa-strategy",
    symbol: "BTCUSDT",
    action: "BUY",
    side: "BUY",
    orderType: "MARKET",
    quantity: 0.01,
    limitPrice: null,
    confidence: 0.5,
    reason: "qa-contract",
    riskFlags: [],
    marketContextId: "mc_qa_1",
    validForSeconds: 30,
    ...over,
  };
}
function mkHold(over = {}) {
  return {
    ...mkBuy({ action: "HOLD", side: null, orderType: null, quantity: null, limitPrice: null }),
    ...over,
  };
}
function mkCtx(over = {}) {
  return {
    marketContextId: "mc_qa_1",
    symbol: "BTCUSDT",
    markPriceUsd: 70000,
    priceTs: iso(NOW),
    markQuality: "verified",
    expiresAt: null,
    ...over,
  };
}

const H = harness("P0-F CONTRACTS (frozen modules, VERIFIED now)");

// ---------------------------------------------------------------------------
H.test("T0 schema contract: DECISION_SCHEMA_VERSION + strict validator", () => {
  assert.equal(DECISION_SCHEMA_VERSION, "jev.ai.decision/v1");
  const ok = validateDecision(mkBuy());
  assert.equal(ok.valid, true, "a well-formed BUY must validate");
  const bad = validateDecision({ ...mkBuy(), confidence: "0.9" });
  assert.equal(bad.valid, false, "type must NOT be coerced");
  const unknown = validateDecision({ ...mkBuy(), extraField: 1 });
  assert.equal(unknown.valid, false, "unknown field must be rejected");
  logEvidence("schemaVersion", DECISION_SCHEMA_VERSION);
  logEvidence("whitelist", RISK_ALLOWED_SYMBOLS);
  logEvidence("defaultLimits", DEFAULT_RISK_LIMITS);
});

// ---------------------------------------------------------------------------
// T7 — risk counterexamples: each must be approved:false with a RISK_CODES code.
H.test("T7 risk counterexamples: 8 rejects (symbol/quality/stale/expired/future/notional/quantity/parse) + valid BUY + HOLD", () => {
  const rejects = [
    { id: "non-whitelist symbol ORDIUSDT", decision: mkBuy({ symbol: "ORDIUSDT" }), context: mkCtx(), nowMs: NOW, limits: {}, expect: RISK_CODES.SYMBOL_NOT_ALLOWED },
    { id: "mark_quality != verified", decision: mkBuy(), context: mkCtx({ markQuality: "unverified" }), nowMs: NOW, limits: {}, expect: RISK_CODES.CONTEXT_NOT_VERIFIED },
    { id: "missing context (unverified market)", decision: mkBuy(), context: null, nowMs: NOW, limits: {}, expect: RISK_CODES.CONTEXT_MISSING },
    { id: "stale context (age 120s > 30s)", decision: mkBuy(), context: mkCtx({ priceTs: iso(NOW - 120000) }), nowMs: NOW, limits: {}, expect: RISK_CODES.CONTEXT_STALE },
    { id: "expired_at in the past", decision: mkBuy(), context: mkCtx({ expiresAt: iso(NOW - 1000) }), nowMs: NOW, limits: {}, expect: RISK_CODES.CONTEXT_EXPIRED },
    { id: "future price_ts beyond skew", decision: mkBuy(), context: mkCtx({ priceTs: iso(NOW + 10000) }), nowMs: NOW, limits: {}, expect: RISK_CODES.CONTEXT_INVALID_TS },
    { id: "quantity > maxQuantity", decision: mkBuy({ quantity: 2 }), context: mkCtx({ markPriceUsd: 100 }), nowMs: NOW, limits: { maxQuantity: 1 }, expect: RISK_CODES.LIMIT_EXCEEDED },
    { id: "notional > maxNotionalUsd", decision: mkBuy({ quantity: 0.02 }), context: mkCtx(), nowMs: NOW, limits: { maxNotionalUsd: 1000 }, expect: RISK_CODES.LIMIT_EXCEEDED },
  ];
  const allRejectCodes = Object.values(RISK_CODES);
  for (const c of rejects) {
    const r = riskCheck({ decision: c.decision, context: c.context, nowMs: c.nowMs, limits: c.limits });
    assert.equal(r.approved, false, `REJECT expected for: ${c.id} (got approved=true, code=${r.code})`);
    assert.equal(r.code, c.expect, `wrong code for: ${c.id}`);
    assert.ok(allRejectCodes.includes(r.code), `code ${r.code} must be a declared RISK_CODES value`);
    logEvidence("reject " + c.id, { approved: r.approved, code: r.code });
  }

  // valid BUY -> approved
  const good = riskCheck({ decision: mkBuy({ quantity: 0.01 }), context: mkCtx(), nowMs: NOW });
  assert.equal(good.approved, true, "a fully valid BUY must be approved");
  assert.equal(good.code, RISK_CODES.APPROVED);
  logEvidence("valid BUY", { approved: good.approved, code: good.code, notional_usd: good.evaluated?.notional_usd });

  // HOLD -> approved with NO context requirement
  const hold = riskCheck({ decision: mkHold(), context: null, nowMs: NOW });
  assert.equal(hold.approved, true, "HOLD must be approved without context");
  assert.equal(hold.code, RISK_CODES.HOLD_NO_ORDER);
  logEvidence("HOLD no-context", { approved: hold.approved, code: hold.code });
});

// ---------------------------------------------------------------------------
// T7b — confidence is NEVER used to flip a decision.
H.test("T7b risk gate never reads confidence (0.99 cannot buy approval)", () => {
  const low = riskCheck({ decision: mkBuy({ confidence: 0.01 }), context: mkCtx(), nowMs: NOW });
  const high = riskCheck({ decision: mkBuy({ confidence: 0.99 }), context: mkCtx(), nowMs: NOW });
  assert.equal(low.approved, true);
  assert.equal(high.approved, true);
  assert.equal(low.code, high.code, "confidence must not change the verdict");
  // A rejected case stays rejected even with 0.99 confidence.
  const rejected = riskCheck({ decision: mkBuy({ symbol: "ORDIUSDT", confidence: 0.99 }), context: mkCtx(), nowMs: NOW });
  assert.equal(rejected.approved, false, "0.99 confidence must NOT override a symbol rejection");
  assert.equal(rejected.code, RISK_CODES.SYMBOL_NOT_ALLOWED);
  logEvidence("confidence ignored", { low: low.code, high: high.code, rejectedWith099: rejected.code });
});

// ---------------------------------------------------------------------------
// T6a — parse failure / invalid decision must NOT be rewritten as HOLD.
H.test("T6a parse failure (invalid decision) => RISK_INVALID_DECISION, never HOLD/approved", () => {
  for (const garbage of [{}, { action: "BUY" }, "not-an-object", null, { ...mkBuy(), quantity: -1 }]) {
    const r = riskCheck({ decision: garbage, context: mkCtx(), nowMs: NOW });
    assert.equal(r.approved, false, "invalid decision must not be approved");
    assert.equal(r.code, RISK_CODES.INVALID_DECISION, "invalid decision must be INVALID_DECISION, not HOLD_NO_ORDER");
  }
  const v = validateDecision({ action: "HOLD" });
  assert.equal(v.valid, false, "a truncated parse result must fail schema validation");
  logEvidence("parse-failure =>", { code: RISK_CODES.INVALID_DECISION, isHold: false });
});

// ---------------------------------------------------------------------------
// T2c — the simplified 2-check gate is NOT a valid substitute (counterexample).
H.test("T2c simplified 2-check gate APPROVES what the formal gate REJECTS (falsifies substitution)", () => {
  // Exact logic from ebc3fcc day14-runner.mjs fallbackRiskGate (L125-133).
  const cfg = { maxNotionalPerTrade: 200, virtualCapitalUsdt: 10000, maxExposurePct: 0.20 };
  const simplifiedGate = (action, notional) => {
    if (action !== "HOLD") {
      const breach = notional > cfg.maxNotionalPerTrade ? "NOTIONAL_EXCEED" :
        (notional / cfg.virtualCapitalUsdt) > cfg.maxExposurePct ? "EXPOSURE_EXCEED" : null;
      return { approved: breach === null, code: breach || "OK" };
    }
    return { approved: true, code: "N/A_HOLD" };
  };

  // Counterexample 1: non-whitelisted symbol, tiny notional.
  const s1 = simplifiedGate("BUY", 100);
  const f1 = riskCheck({ decision: mkBuy({ symbol: "ORDIUSDT" }), context: mkCtx(), nowMs: NOW });
  assert.equal(s1.approved, true, "simplified gate SHOULD (wrongly) approve ORDIUSDT/100");
  assert.equal(f1.approved, false, "formal gate MUST reject ORDIUSDT");
  assert.equal(f1.code, RISK_CODES.SYMBOL_NOT_ALLOWED);

  // Counterexample 2: unverified / missing market context, tiny notional.
  const s2 = simplifiedGate("BUY", 100);
  const f2 = riskCheck({ decision: mkBuy(), context: null, nowMs: NOW });
  assert.equal(s2.approved, true, "simplified gate SHOULD (wrongly) approve with no provenance");
  assert.equal(f2.approved, false, "formal gate MUST reject missing context");
  assert.equal(f2.code, RISK_CODES.CONTEXT_MISSING);

  // Simplified HOLD is also an unconditional approve (N/A_HOLD) — no provenance.
  assert.equal(simplifiedGate("HOLD", 0).code, "N/A_HOLD");
  logEvidence("simplified-approves / formal-rejects", {
    case1: { simplified: s1.code, formal: f1.code },
    case2: { simplified: s2.code, formal: f2.code },
  });
});

// ---------------------------------------------------------------------------
// T8-engine — the frozen engine's equity is DERIVED, not a hardcoded 10000.
H.test("T8-engine frozen engine summarize() derives equity from account state (10000 is NOT universal)", () => {
  assert.ok(engine && typeof engine.createAccount === "function" && typeof engine.summarize === "function",
    "__test must expose createAccount/summarize");
  assert.equal(typeof engine.simulateNewOrder, "function", "__test must expose simulateNewOrder");

  const account = engine.createAccount();
  const baseline = engine.summarize(account, {});
  assert.equal(baseline.equity, 10000, "fresh account baseline is 10000 by coincidence");
  logEvidence("engine fresh equity", baseline.equity);

  // Representative post-fill account state: cash reduced, a BTC position with a
  // mark above avg price. summarize() must compute equity from cash + position*mark.
  account.cash = "9500.00000000";
  account.positions = { BTCUSDT: { qty: "0.10000000", avgPrice: "70000.00000000", reservedQty: "0" } };
  const derived = engine.summarize(account, { BTCUSDT: "71000.00000000" });
  assert.notEqual(derived.equity, 10000, "equity must NOT be a hardcoded 10000");
  assert.equal(derived.equity, 16600, "equity = 9500 + 0.1*71000 = 16600");
  assert.equal(derived.marketValue, 7100);
  assert.equal(derived.unrealizedPnl, 100);
  assert.equal(derived.positions.length, 1);
  logEvidence("engine derived equity (non-default account)", {
    equity: derived.equity, cash: derived.cash, marketValue: derived.marketValue,
    unrealizedPnl: derived.unrealizedPnl, note: "10000/MATCH is not a universal engine conclusion",
  });

  // no-fills baseline: fresh account has zero trades/fills -> any honest
  // reconciliation must be able to say "no fills" rather than assert MATCH.
  assert.equal((account.trades || []).length >= 0, true);
  const fresh = engine.createAccount();
  assert.equal(fresh.trades.length, 0, "fresh engine account has no fills");
  assert.equal(fresh.orders.length, 0, "fresh engine account has no orders");
  logEvidence("no-fills baseline", { trades: fresh.trades.length, orders: fresh.orders.length });
});

// ---------------------------------------------------------------------------
// T3-ref — reference sha256 chain validator: valid passes, tamper/断链 detected.
H.test("T3-ref hash chain: valid chain verifies; content-tamper and _prev=GENESIS break are both detected", () => {
  const dir = mkdtempSync(join(tmpdir(), "jev-p0-chain-"));
  const p = join(dir, "chain.jsonl");
  const recs = [
    { cycle: 0, action: "HOLD", reason: "r0" },
    { cycle: 1, action: "HOLD", reason: "r1" },
    { cycle: 2, action: "HOLD", reason: "r2" },
    { cycle: 3, action: "HOLD", reason: "r3" },
  ];
  const lines = chainLines(recs);
  writeJsonl(p, lines);
  const ok = verifyChainFile(p);
  assert.equal(ok.ok, true, "a correctly chained journal must verify");
  assert.equal(ok.count, 4);
  logEvidence("valid chain", { count: ok.count, tipHash: lines[3]._hash.slice(0, 16), prev3: lines[3]._prev.slice(0, 16) });

  // Variant A: tamper a record's CONTENT without recomputing _hash.
  const a = lines.map((o) => ({ ...o }));
  a[2].reason = "TAMPERED";
  writeJsonl(p, a);
  const vA = verifyChainFile(p);
  assert.equal(vA.ok, false, "content tamper MUST be detected");
  assert.ok(/tampered content|_hash mismatch/.test(vA.error), "must report a hash mismatch: " + vA.error);
  logEvidence("tamper detected", { ok: vA.ok, error: vA.error, at: vA.at });

  // Variant B: break the chain by resetting a middle _prev to GENESIS.
  const b = lines.map((o) => ({ ...o }));
  b[2]._prev = "GENESIS";
  writeJsonl(p, b);
  const vB = verifyChainFile(p);
  assert.equal(vB.ok, false, "_prev=GENESIS break MUST be detected");
  assert.ok(/chain break/.test(vB.error), "must report a chain break: " + vB.error);
  logEvidence("chain-break detected", { ok: vB.ok, error: vB.error, at: vB.at });

  rmSync(dir, { recursive: true, force: true });
});

await H.run();
