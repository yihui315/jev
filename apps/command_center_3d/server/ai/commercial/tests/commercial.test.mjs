// commercial/tests/commercial.test.mjs
import test from "node:test";
import assert from "node:assert/strict";

import {
  NET_PROFIT_FORMULA,
  computeNetProfit,
  COMMERCIAL_GATES,
  evaluateCommercialVerdict,
  isGo,
} from "../index.mjs";

// ---------------------------------------------------------------------------
// net-profit arithmetic (only on REAL data)
// ---------------------------------------------------------------------------
test("computeNetProfit: correct arithmetic on REAL data", () => {
  const r = computeNetProfit({
    grossTradingPnl: 1200,
    fees: 80,
    slippage: 40,
    aiCost: 300,
    opsCost: 100,
    dataSource: "REAL",
  });
  assert.equal(r.net, 1200 - 80 - 40 - 300 - 100); // 680
  assert.equal(r.positive, true);
  assert.equal(r.formula, NET_PROFIT_FORMULA);
});

test("computeNetProfit: negative net when costs exceed gains", () => {
  const r = computeNetProfit({
    grossTradingPnl: 200,
    fees: 50,
    slippage: 30,
    aiCost: 150,
    opsCost: 80,
    dataSource: "REAL",
  });
  assert.equal(r.net, -110);
  assert.equal(r.positive, false);
});

test("computeNetProfit: defaults to zero when fields omitted (REAL)", () => {
  const r = computeNetProfit({ dataSource: "REAL" });
  assert.equal(r.net, 0);
});

test("computeNetProfit: rejects non-finite inputs", () => {
  assert.throws(
    () => computeNetProfit({ grossTradingPnl: "x", dataSource: "REAL" }),
    TypeError,
  );
  assert.throws(
    () => computeNetProfit({ aiCost: NaN, dataSource: "REAL" }),
    TypeError,
  );
});

test("computeNetProfit: REFUSES on MOCK/SYNTHETIC data (fail-closed)", () => {
  for (const ds of ["UNKNOWN", "MOCK", "SYNTHETIC", "FIXTURE", undefined]) {
    assert.throws(
      () =>
        computeNetProfit({
          grossTradingPnl: 500,
          aiCost: 100,
          dataSource: ds,
        }),
      /REFUSES to compute on non-real data/,
      `should refuse dataSource=${JSON.stringify(ds)}`,
    );
  }
});

// ---------------------------------------------------------------------------
// commercial verdict engine
// ---------------------------------------------------------------------------
test("verdict: NO-GO when data is not REAL (hard rule)", () => {
  const v = evaluateCommercialVerdict({
    dataSource: "SYNTHETIC",
    validSamples: 1000,
    outOfSampleSamples: 300,
    aiCostMeasured: true,
    riskControlAutomated: true,
    independentAuditSigned: true,
  });
  assert.equal(v.verdict, "NO-GO");
  assert.equal(v.computable, false);
  assert.ok(v.reasons.some((r) => r.startsWith("DATA_NOT_REAL")));
});

test("verdict: NO-GO when valid samples below 100", () => {
  const v = evaluateCommercialVerdict({
    dataSource: "REAL",
    validSamples: 99,
    outOfSampleSamples: 30,
    aiCostMeasured: true,
    riskControlAutomated: true,
    independentAuditSigned: true,
  });
  assert.equal(v.verdict, "NO-GO");
  assert.ok(v.reasons.some((r) => r.startsWith("INSUFFICIENT_VALID_SAMPLES")));
});

test("verdict: NO-GO when out-of-sample below 30", () => {
  const v = evaluateCommercialVerdict({
    dataSource: "REAL",
    validSamples: 100,
    outOfSampleSamples: 29,
    aiCostMeasured: true,
    riskControlAutomated: true,
    independentAuditSigned: true,
  });
  assert.equal(v.verdict, "NO-GO");
  assert.ok(v.reasons.some((r) => r.startsWith("INSUFFICIENT_OUT_OF_SAMPLE")));
});

test("verdict: NO-GO when AI cost not measured", () => {
  const v = evaluateCommercialVerdict({
    dataSource: "REAL",
    validSamples: 100,
    outOfSampleSamples: 30,
    aiCostMeasured: false,
    riskControlAutomated: true,
    independentAuditSigned: true,
  });
  assert.equal(v.verdict, "NO-GO");
  assert.ok(v.reasons.some((r) => r.startsWith("AI_COST_NOT_MEASURED")));
});

test("verdict: NO-GO when risk control not automated", () => {
  const v = evaluateCommercialVerdict({
    dataSource: "REAL",
    validSamples: 100,
    outOfSampleSamples: 30,
    aiCostMeasured: true,
    riskControlAutomated: false,
    independentAuditSigned: true,
  });
  assert.equal(v.verdict, "NO-GO");
  assert.ok(v.reasons.some((r) => r.startsWith("RISK_CONTROL_NOT_AUTOMATED")));
});

test("verdict: CONDITIONAL GO when all fundamentals met but audit pending", () => {
  const v = evaluateCommercialVerdict({
    dataSource: "REAL",
    validSamples: 100,
    outOfSampleSamples: 30,
    aiCostMeasured: true,
    riskControlAutomated: true,
    independentAuditSigned: false,
  });
  assert.equal(v.verdict, "CONDITIONAL GO");
  assert.equal(isGo(v.verdict), true);
  assert.ok(v.reasons.some((r) => r.startsWith("INDEPENDENT_AUDIT_PENDING")));
});

test("verdict: GO when every gate passes on REAL data", () => {
  const v = evaluateCommercialVerdict({
    dataSource: "REAL",
    validSamples: 250,
    outOfSampleSamples: 75,
    aiCostMeasured: true,
    riskControlAutomated: true,
    independentAuditSigned: true,
  });
  assert.equal(v.verdict, "GO");
  assert.equal(isGo(v.verdict), true);
  assert.equal(v.reasons.length, 0);
});

test("verdict: gate thresholds match documented constants", () => {
  assert.equal(COMMERCIAL_GATES.VALID_SAMPLES_MIN, 100);
  assert.equal(COMMERCIAL_GATES.OUT_OF_SAMPLE_MIN, 30);
});
