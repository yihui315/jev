// T-C Blind Comparison Harness — same dataset, every model, records accuracy,
// decision consistency, response time, cost/call, error rate, risk-judgment.
// MOCK-ONLY: all figures are simulated. Stdlib-only.
import assert from "node:assert/strict";
import { buildSyntheticDataset, runComparison } from "../comparison.mjs";

const checks = [];
function check(name, fn) { checks.push([name, fn]); }

export async function run() {
  let PASS = 0, FAIL = 0;

  check("harness returns a MOCK-ONLY disclaimer and measures all four models", async () => {
    const r = await runComparison({ dataset: buildSyntheticDataset(10) });
    assert.ok(/MOCK-ONLY/.test(r.disclaimer), "must declare MOCK-ONLY");
    assert.equal(r.datasetSize, 10);
    const ids = Object.keys(r.models);
    assert.equal(ids.length, 4, "all four provider models compared");
    for (const m of ids) {
      const row = r.models[m];
      assert.ok("accuracy" in row && "errorRate" in row, m + " missing accuracy/errorRate");
      assert.ok("responseTimeMs" in row && "costPerCallUsd" in row, m + " missing timing/cost");
      assert.ok("riskVerdictDistribution" in row, m + " missing risk verdict distribution");
      assert.equal(typeof row.accuracy, "number");
      assert.equal(row.errorRate, 0, m + " should have zero errors in happy path");
      assert.ok(row.responseTimeMs.avg >= 0 && row.responseTimeMs.max >= 0);
    }
  });

  check("intra-model determinism is 1.0 (mock is deterministic per input)", async () => {
    const r = await runComparison({ dataset: buildSyntheticDataset(8) });
    const ids = Object.keys(r.intraModelConsistency);
    assert.equal(ids.length, 4);
    for (const m of ids) {
      assert.equal(r.intraModelConsistency[m].intraModelDeterminism, 1, m + " must be deterministic");
    }
  });

  check("inter-model agreement computed for all C(4,2)=6 pairs", async () => {
    const r = await runComparison({ dataset: buildSyntheticDataset(8) });
    assert.equal(Object.keys(r.interModelAgreement).length, 6, "six pairwise agreements");
    for (const k of Object.keys(r.interModelAgreement)) {
      assert.ok(/^.+\/.+$/.test(k), "pair key shape modelA/modelB");
      assert.equal(typeof r.interModelAgreement[k], "number");
    }
  });

  check("error injection raises measured error rate (still MOCK)", async () => {
    const r = await runComparison({
      dataset: buildSyntheticDataset(6),
      gatewayOpts: {
        policyDefault: "risk-capability",
        maxRetries: 0,
        behaviorByProvider: { "minimax-m3": { baseLatencyMs: 10, latencyJitterMs: 0, fault: { fatalRate: 1 } } },
      },
    });
    // minimax-m3 was forced to always fail -> its errorRate should be 1
    const m3 = Object.values(r.models).find((x) => x.providerId === "minimax-m3");
    assert.equal(m3.errorRate, 1, "forced-fault model must report errorRate=1 (mock)");
    const other = Object.values(r.models).find((x) => x.providerId !== "minimax-m3");
    assert.equal(other.errorRate, 0, "healthy models still error-free");
    assert.ok(/MOCK-ONLY/.test(r.disclaimer));
  });

  for (const [name, fn] of checks) {
    try { await fn(); PASS++; console.log("[PASS] " + name); }
    catch (e) { FAIL++; console.log("[FAIL] " + name + " :: " + (e && e.message ? e.message : String(e))); }
  }
  return { name: "comparison", pass: PASS, fail: FAIL };
}
