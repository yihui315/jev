// T3 - server risk gate. Rejection must be independent of any AI self-report.

import { riskCheck, riskCheckWithDb, RISK_CODES } from "../risk-gate.mjs";
import { saveMarketContext } from "../runtime.mjs";
import { validBuy, validHold, makeContext, makeDb } from "./lib/fixtures.mjs";
import { assert, assertEqual } from "./lib/harness.mjs";

export const id = "t3_risk_gate";
export const title = "Risk gate: whitelist, verified context, freshness, notional limits, confidence immunity";

const NOW = Date.parse("2026-11-09T12:00:00.000Z");

export async function run(suite) {
  await suite.check("fresh verified context + small notional is approved", () => {
    const r = riskCheck({ decision: validBuy({ confidence: 0.2 }), context: makeContext(NOW), nowMs: NOW });
    assertEqual(r.approved, true);
    assertEqual(r.code, RISK_CODES.APPROVED);
    assert(r.evaluated.notional_usd > 0, "notional must be computed");
    return "notional_usd=" + r.evaluated.notional_usd;
  });

  await suite.check("symbol outside the cloud whitelist is rejected even with max confidence", () => {
    const r = riskCheck({ decision: validBuy({ symbol: "ORDIUSDT", confidence: 1 }), context: makeContext(NOW, { symbol: "ORDIUSDT" }), nowMs: NOW });
    assertEqual(r.approved, false);
    assertEqual(r.code, RISK_CODES.SYMBOL_NOT_ALLOWED);
    return r.code;
  });

  await suite.check("missing market context is rejected", () => {
    const r = riskCheck({ decision: validBuy(), context: null, nowMs: NOW });
    assertEqual(r.approved, false);
    assertEqual(r.code, RISK_CODES.CONTEXT_MISSING);
    return r.code;
  });

  await suite.check("unknown mark_quality is rejected (verified only)", () => {
    const r = riskCheck({ decision: validBuy(), context: makeContext(NOW, { markQuality: "unknown" }), nowMs: NOW });
    assertEqual(r.code, RISK_CODES.CONTEXT_NOT_VERIFIED);
    return r.code;
  });

  await suite.check("stale snapshot is rejected", () => {
    const stale = makeContext(NOW - 10 * 60 * 1000);
    const r = riskCheck({ decision: validBuy(), context: stale, nowMs: NOW });
    assertEqual(r.code, RISK_CODES.CONTEXT_STALE);
    assert(typeof r.reason === "string" && r.reason.length > 0, "rejection must carry a reason");
    assert(r.evaluated === undefined, "stale must not carry approval metrics");
    return r.code + " age=600000ms";
  });

  await suite.check("context from a different symbol is rejected", () => {
    const r = riskCheck({ decision: validBuy(), context: makeContext(NOW, { symbol: "ETHUSDT" }), nowMs: NOW });
    assertEqual(r.code, RISK_CODES.SYMBOL_MISMATCH);
    return r.code;
  });

  await suite.check("expired context (expires_at) is rejected", () => {
    const ctx = makeContext(NOW, { expiresAt: new Date(NOW - 1000).toISOString() });
    const r = riskCheck({ decision: validBuy(), context: ctx, nowMs: NOW });
    assertEqual(r.code, RISK_CODES.CONTEXT_EXPIRED);
    return r.code;
  });

  await suite.check("notional above the server limit is rejected", () => {
    const r = riskCheck({ decision: validBuy({ quantity: 0.1 }), context: makeContext(NOW), nowMs: NOW });
    assertEqual(r.code, RISK_CODES.LIMIT_EXCEEDED);
    assertEqual(r.approved, false);
    assertEqual(r.notional_usd, 6000);
    return r.code + " notional_usd=6000";
  });

  await suite.check("quantity above the server limit is rejected", () => {
    const r = riskCheck({ decision: validBuy({ quantity: 20, limitPrice: 1, orderType: "LIMIT" }), context: makeContext(NOW), nowMs: NOW });
    assertEqual(r.code, RISK_CODES.LIMIT_EXCEEDED);
    return r.code;
  });

  await suite.check("confidence cannot flip a rejection (0.01 vs 1.0 identical verdict)", () => {
    const stale = makeContext(NOW - 10 * 60 * 1000);
    const low = riskCheck({ decision: validBuy({ confidence: 0.01 }), context: stale, nowMs: NOW });
    const high = riskCheck({ decision: validBuy({ confidence: 1 }), context: stale, nowMs: NOW });
    assertEqual(low.approved, false);
    assertEqual(high.approved, false);
    assertEqual(low.code, high.code);
    const approvedLow = riskCheck({ decision: validBuy({ confidence: 0.01 }), context: makeContext(NOW), nowMs: NOW });
    const highButOversized = riskCheck({ decision: validBuy({ confidence: 1, quantity: 0.1 }), context: makeContext(NOW), nowMs: NOW });
    assertEqual(approvedLow.approved, true);
    assertEqual(highButOversized.approved, false);
    return "verdict depends on context/limits only";
  });

  await suite.check("HOLD is approved as a no-order decision", () => {
    const r = riskCheck({ decision: validHold(), context: null, nowMs: NOW });
    assertEqual(r.approved, true);
    assertEqual(r.code, RISK_CODES.HOLD_NO_ORDER);
    return r.code;
  });

  await suite.check("schema-invalid decision is rejected before any risk maths", () => {
    const r = riskCheck({ decision: { ...validBuy(), sneaky: true }, context: makeContext(NOW), nowMs: NOW });
    assertEqual(r.code, RISK_CODES.INVALID_DECISION);
    assert(Array.isArray(r.errors) && r.errors.length > 0, "schema errors must be surfaced");
    return r.code;
  });

  await suite.check("db-backed context: stored 1e8 mark price is loaded and used", async () => {
    const db = makeDb();
    const ctx = makeContext(NOW);
    await saveMarketContext(db, ctx, NOW);
    const r = await riskCheckWithDb({ db, decision: validBuy(), nowMs: NOW });
    assertEqual(r.approved, true);
    assertEqual(r.evaluated.reference_price_usd, 60000);
    const missing = await riskCheckWithDb({ db, decision: validBuy({ marketContextId: "mctx-does-not-exist" }), nowMs: NOW });
    assertEqual(missing.code, RISK_CODES.CONTEXT_MISSING);
    return "reference_price_usd=60000";
  });
}
