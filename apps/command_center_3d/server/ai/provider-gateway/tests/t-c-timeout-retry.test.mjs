// T-C Timeout + Bounded Retry — gateway honours per-call timeout budget and
// retries only retryable failures, up to a hard cap. Stdlib-only.
import assert from "node:assert/strict";
import { PROVIDER_MODELS } from "../catalog.mjs";
import { createGateway, GATEWAY_ERROR_CODES } from "../gateway.mjs";

const checks = [];
function check(name, fn) { checks.push([name, fn]); }

export async function run() {
  let PASS = 0, FAIL = 0;
  const single = [PROVIDER_MODELS.find((m) => m.providerId === "jev")];

  check("gateway-layer error taxonomy extends the existing six codes", () => {
    assert.ok("PROVIDER_CIRCUIT_OPEN" in GATEWAY_ERROR_CODES, "missing PROVIDER_CIRCUIT_OPEN");
    assert.ok("PROVIDER_TRANSIENT" in GATEWAY_ERROR_CODES, "missing PROVIDER_TRANSIENT");
    assert.ok("PROVIDER_TIMEOUT" in GATEWAY_ERROR_CODES);
  });

  check("retryable timeout is bounded: 3 attempts then PROVIDER_RETRY_EXHAUSTED (maxRetries=2)", async () => {
    const gw = createGateway({
      models: single,
      policyDefault: "explicit",
      maxRetries: 2,
      // force timeout every time; provider latency (60ms) far exceeds 5ms budget
      behaviorByProvider: { jev: { baseLatencyMs: 60, latencyJitterMs: 0, fault: { timeoutRate: 1 } } },
    });
    const res = await gw.request({ prompt: "x", timeoutMs: 5 });
    assert.equal(res.ok, false);
    assert.equal(res.errorClass, "PROVIDER_RETRY_EXHAUSTED");
    assert.equal(res.retry.attempted, 2, "should have attempted exactly maxRetries=2 retries");
    assert.equal(res.retry.max, 2);
    assert.equal(res.retry.exhausted, true);
    const snap = gw.metrics.snapshot();
    assert.equal(snap.jev.calls, 3, "metrics should record 3 attempts");
    assert.equal(snap.jev.failures, 3);
    assert.equal(snap.jev.failureRate, 1);
  });

  check("non-retryable (PROVIDER_BAD_RESPONSE) is NOT retried", async () => {
    const gw = createGateway({
      models: single,
      policyDefault: "explicit",
      maxRetries: 2,
      behaviorByProvider: { jev: { baseLatencyMs: 10, latencyJitterMs: 0, fault: { fatalRate: 1 } } },
    });
    const res = await gw.request({ prompt: "y" });
    assert.equal(res.ok, false);
    assert.equal(res.errorClass, "PROVIDER_BAD_RESPONSE");
    assert.equal(res.retry.attempted, 0, "fatal error must not be retried");
    const snap = gw.metrics.snapshot();
    assert.equal(snap.jev.calls, 1, "only one attempt for a fatal error");
  });

  check("success path records latency, tokens and (mock) cost; failureRate=0", async () => {
    const gw = createGateway({
      models: single,
      policyDefault: "explicit",
      behaviorByProvider: { jev: { baseLatencyMs: 8, latencyJitterMs: 0 } },
    });
    const res = await gw.request({ prompt: "hello world this is a prompt" });
    assert.equal(res.ok, true);
    assert.equal(typeof res.latencyMs, "number");
    assert.ok(res.usage && res.usage.totalTokens > 0, "tokens should be metered");
    assert.ok(res.cost && res.cost.usd > 0, "mock cost should be recorded");
    assert.equal(res.cost.provenance, "MOCK_PRICING");
    const snap = gw.metrics.snapshot();
    assert.equal(snap.jev.ok, 1);
    assert.equal(snap.jev.failureRate, 0);
  });

  for (const [name, fn] of checks) {
    try { await fn(); PASS++; console.log("[PASS] " + name); }
    catch (e) { FAIL++; console.log("[FAIL] " + name + " :: " + (e && e.message ? e.message : String(e))); }
  }
  return { name: "timeout-retry", pass: PASS, fail: FAIL };
}
