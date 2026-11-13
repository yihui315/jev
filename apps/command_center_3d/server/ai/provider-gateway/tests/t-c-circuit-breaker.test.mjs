// T-C Circuit Breaker — state machine + gateway integration with failover.
// Stdlib-only.
import assert from "node:assert/strict";
import { PROVIDER_MODELS } from "../catalog.mjs";
import { createCircuitBreaker, BREAKER_STATES } from "../circuit-breaker.mjs";
import { createGateway } from "../gateway.mjs";

const checks = [];
function check(name, fn) { checks.push([name, fn]); }

export async function run() {
  let PASS = 0, FAIL = 0;

  check("unit: CLOSED -> OPEN after failureThreshold failures", () => {
    const cb = createCircuitBreaker({ failureThreshold: 3, cooldownMs: 1000, clock: () => 0 });
    assert.equal(cb.state(), BREAKER_STATES.CLOSED);
    cb.recordFailure(); cb.recordFailure();
    assert.equal(cb.state(), BREAKER_STATES.CLOSED, "should stay closed under threshold");
    cb.recordFailure();
    assert.equal(cb.state(), BREAKER_STATES.OPEN);
  });

  check("unit: OPEN rejects calls; after cooldown transitions to HALF_OPEN", () => {
    let t = 0;
    const cb = createCircuitBreaker({ failureThreshold: 1, cooldownMs: 1000, clock: () => t });
    cb.recordFailure();
    assert.equal(cb.state(), BREAKER_STATES.OPEN);
    assert.equal(cb.allow(), false, "still within cooldown -> reject");
    t = 1001;
    assert.equal(cb.allow(), true, "cooldown elapsed -> half-open permitted");
    assert.equal(cb.state(), BREAKER_STATES.HALF_OPEN);
  });

  check("unit: HALF_OPEN success closes; failure re-opens", () => {
    let t = 0;
    const cb = createCircuitBreaker({ failureThreshold: 1, cooldownMs: 1000, clock: () => t });
    cb.recordFailure();
    t = 1001;
    cb.allow();
    assert.equal(cb.state(), BREAKER_STATES.HALF_OPEN);
    cb.recordSuccess();
    assert.equal(cb.state(), BREAKER_STATES.CLOSED, "half-open success closes breaker");
    // re-open then half-open then fail -> re-open
    cb.recordFailure();
    t = 2001;
    cb.allow();
    cb.recordFailure();
    assert.equal(cb.state(), BREAKER_STATES.OPEN, "half-open failure re-opens breaker");
  });

  check("gateway: retryable exhaustion on A falls back to healthy B and succeeds", async () => {
    const jev = PROVIDER_MODELS.find((m) => m.providerId === "jev");       // A: always timeout
    const bailian = PROVIDER_MODELS.find((m) => m.providerId === "bailian"); // B: healthy
    const gw = createGateway({
      models: [jev, bailian],
      policyDefault: "cost-optimized",
      maxRetries: 2,
      breakerConfig: { failureThreshold: 5 }, // keep A closed so failover path is exercised
      behaviorByProvider: { jev: { baseLatencyMs: 50, latencyJitterMs: 0, fault: { timeoutRate: 1 } } },
    });
    const res = await gw.request({ prompt: "fallback please", timeoutMs: 5 });
    assert.equal(res.ok, true, "should succeed via fallback to B");
    assert.equal(res.modelId, "bailian-qwen-plus", "fallback should land on B");
    const reasons = res.switchLog.map((s) => s.reason);
    assert.ok(reasons.includes("retry-exhausted-fallback"), "switch log should record the fallback");
  });

  check("gateway: breaker OPEN on A is skipped and request succeeds on B", async () => {
    const jev = PROVIDER_MODELS.find((m) => m.providerId === "jev");
    const bailian = PROVIDER_MODELS.find((m) => m.providerId === "bailian");
    const gw = createGateway({
      models: [jev, bailian],
      policyDefault: "cost-optimized",
      maxRetries: 2,
      breakerConfig: { failureThreshold: 2 }, // trips after 2 failures
      behaviorByProvider: { jev: { baseLatencyMs: 50, latencyJitterMs: 0, fault: { timeoutRate: 1 } } },
    });
    // first request: A times out (3 attempts, 3 failures) -> breaker trips to OPEN, fallback to B succeeds
    const r1 = await gw.request({ prompt: "first", timeoutMs: 5 });
    assert.equal(r1.ok, true);
    assert.equal(gw.breakers.jev.state(), BREAKER_STATES.OPEN, "A breaker should now be OPEN");
    // second request: A is OPEN -> skipped, B chosen directly
    const r2 = await gw.request({ prompt: "second" });
    assert.equal(r2.ok, true);
    const reasons = r2.switchLog.map((s) => s.reason);
    assert.ok(reasons.includes("circuit-open-skip:jev"), "switch log should record circuit-open skip of A");
    assert.equal(r2.modelId, "bailian-qwen-plus");
  });

  check("gateway: all-OPEN single provider returns PROVIDER_CIRCUIT_OPEN fast", async () => {
    const jev = PROVIDER_MODELS.find((m) => m.providerId === "jev");
    const gw = createGateway({
      models: [jev],
      policyDefault: "explicit",
      maxRetries: 1,
      breakerConfig: { failureThreshold: 1 }, // trips after the 1st failure
      behaviorByProvider: { jev: { baseLatencyMs: 10, latencyJitterMs: 0, fault: { fatalRate: 1 } } },
    });
    const r1 = await gw.request({ prompt: "fail" });
    assert.equal(r1.ok, false);
    assert.equal(r1.errorClass, "PROVIDER_BAD_RESPONSE");
    assert.equal(gw.breakers.jev.state(), BREAKER_STATES.OPEN);
    const r2 = await gw.request({ prompt: "fail-again" });
    assert.equal(r2.ok, false);
    assert.equal(r2.errorClass, "PROVIDER_CIRCUIT_OPEN", "no healthy candidate -> circuit open");
    assert.equal(r2.circuitState, null);
    const ev = gw.evidence.get(r2.evidenceId);
    assert.equal(ev.errorClass, "PROVIDER_CIRCUIT_OPEN");
  });

  for (const [name, fn] of checks) {
    try { await fn(); PASS++; console.log("[PASS] " + name); }
    catch (e) { FAIL++; console.log("[FAIL] " + name + " :: " + (e && e.message ? e.message : String(e))); }
  }
  return { name: "circuit-breaker", pass: PASS, fail: FAIL };
}
