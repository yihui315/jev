// T-C Metrics — token metering, cost recording, latency percentiles, failure-rate.
// Stdlib-only.
import assert from "node:assert/strict";
import { PROVIDER_MODELS } from "../catalog.mjs";
import { createGateway } from "../gateway.mjs";

const checks = [];
function check(name, fn) { checks.push([name, fn]); }

export async function run() {
  let PASS = 0, FAIL = 0;
  const jev = PROVIDER_MODELS.find((m) => m.providerId === "jev");
  const bailian = PROVIDER_MODELS.find((m) => m.providerId === "bailian");

  check("successful requests meter calls, tokens, cost and latency", async () => {
    const gw = createGateway({
      models: [jev],
      policyDefault: "explicit",
      behaviorByProvider: { jev: { baseLatencyMs: 10, latencyJitterMs: 0 } },
    });
    for (let i = 0; i < 3; i++) await gw.request({ prompt: "request number " + i + " with some length" });
    const snap = gw.metrics.snapshot();
    const m = snap.jev;
    assert.equal(m.calls, 3);
    assert.equal(m.ok, 3);
    assert.equal(m.failures, 0);
    assert.equal(m.failureRate, 0);
    assert.ok(m.tokens.total > 0, "tokens should accumulate");
    assert.ok(m.costUsd > 0, "mock cost should accumulate");
    assert.ok(m.latencyMs.avg >= 0 && m.latencyMs.max >= m.latencyMs.min);
    // latency percentiles present and ordered
    assert.ok(m.latencyMs.p50 >= m.latencyMs.min);
    assert.ok(m.latencyMs.max >= m.latencyMs.p50);
  });

  check("failures are recorded with their error class and raise failure-rate", async () => {
    const gw = createGateway({
      models: [jev],
      policyDefault: "explicit",
      maxRetries: 0,
      behaviorByProvider: { jev: { baseLatencyMs: 10, latencyJitterMs: 0, fault: { fatalRate: 1 } } },
    });
    await gw.request({ prompt: "boom" });
    const m = gw.metrics.snapshot().jev;
    assert.equal(m.calls, 1);
    assert.equal(m.ok, 0);
    assert.equal(m.failures, 1);
    assert.equal(m.failureRate, 1);
    assert.ok("PROVIDER_BAD_RESPONSE" in m.byClass, "byClass should record the error taxonomy code");
  });

  check("cost accrues across providers independently (no cross-model aggregation)", async () => {
    const gw = createGateway({
      models: [jev, bailian],
      policyDefault: "cost-optimized",
      behaviorByProvider: {
        jev: { baseLatencyMs: 6, latencyJitterMs: 0 },
        bailian: { baseLatencyMs: 6, latencyJitterMs: 0 },
      },
    });
    await gw.request({ prompt: "alpha" }); // cheapest -> jev
    await gw.request({ prompt: "beta", preferredModel: "bailian" }); // bailian
    const snap = gw.metrics.snapshot();
    assert.ok(snap.jev.costUsd > 0);
    assert.ok(snap.bailian.costUsd > 0);
    // the two providers must NOT share a single aggregated cost figure
    assert.ok(snap.jev.calls === 1 && snap.bailian.calls === 1, "each provider metered independently");
  });

  for (const [name, fn] of checks) {
    try { await fn(); PASS++; console.log("[PASS] " + name); }
    catch (e) { FAIL++; console.log("[FAIL] " + name + " :: " + (e && e.message ? e.message : String(e))); }
  }
  return { name: "metrics", pass: PASS, fail: FAIL };
}
