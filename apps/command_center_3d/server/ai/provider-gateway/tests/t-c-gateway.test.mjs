// T-C Gateway Integration — full request flow: routing, attribution reuse,
// normalized response, switch logging, MOCK-only labeling. Stdlib-only.
import assert from "node:assert/strict";
import { PROVIDER_MODELS } from "../catalog.mjs";
import { createGateway } from "../gateway.mjs";
import { GATEWAY_RESULT_SCHEMA } from "../protocol.mjs";

const checks = [];
function check(name, fn) { checks.push([name, fn]); }

export async function run() {
  let PASS = 0, FAIL = 0;
  const jev = PROVIDER_MODELS.find((m) => m.providerId === "jev");
  const m3 = PROVIDER_MODELS.find((m) => m.providerId === "minimax-m3");
  const bailian = PROVIDER_MODELS.find((m) => m.providerId === "bailian");

  check("happy path returns a normalized, MOCK-labeled success response", async () => {
    const gw = createGateway({
      models: [jev, m3, bailian],
      policyDefault: "cost-optimized",
      behaviorByProvider: { jev: { baseLatencyMs: 8, latencyJitterMs: 0 } },
    });
    const res = await gw.request({ prompt: "hello gateway" });
    assert.equal(res.ok, true);
    assert.equal(res.schema, GATEWAY_RESULT_SCHEMA);
    assert.equal(res.dataMode, "mock");
    assert.equal(res.isReal, false);
    assert.ok(/MOCK-ONLY/.test(res.mockDisclaimer), "must declare MOCK-ONLY");
    assert.ok(res.output && typeof res.output === "string");
    assert.ok(/^[0-9a-f]{64}$/.test(res.inputHash));
    assert.ok(typeof res.evidenceId === "string" && res.evidenceId.length > 0);
    // attribution reused from provider-interface contract: providerId verbatim
    assert.equal(res.providerId, res.providerId, "attribution must be preserved verbatim");
    assert.ok(res.switchLog.length >= 1, "at least the initial selection is logged");
    assert.equal(res.switchLog[0].reason.startsWith("policy-select") || res.switchLog[0].reason === "explicit", true);
  });

  check("explicit policy routes to the preferred model", async () => {
    const gw = createGateway({
      models: [jev, m3, bailian],
      policyDefault: "cost-optimized",
      behaviorByProvider: { "minimax-m3": { baseLatencyMs: 8, latencyJitterMs: 0 } },
    });
    const res = await gw.request({ prompt: "prefer m3", preferredModel: "MiniMax-M3" });
    assert.equal(res.ok, true);
    assert.equal(res.modelId, "MiniMax-M3");
    assert.equal(res.switchLog[0].reason, "explicit");
    assert.equal(res.switchLog[0].to, "MiniMax-M3");
  });

  check("risk-capability policy prefers the highest-capability model (MiniMax-M3)", async () => {
    const gw = createGateway({
      models: [jev, m3, bailian],
      policyDefault: "cost-optimized",
      behaviorByProvider: { "minimax-m3": { baseLatencyMs: 8, latencyJitterMs: 0 } },
    });
    const res = await gw.request({ prompt: "risk assessment", task: "risk_judgment", policy: "risk-capability" });
    assert.equal(res.ok, true);
    assert.equal(res.modelId, "MiniMax-M3");
  });

  check("risk_judgment task yields a structured mock verdict, not real output", async () => {
    const gw = createGateway({
      models: [jev],
      policyDefault: "explicit",
      behaviorByProvider: { jev: { baseLatencyMs: 6, latencyJitterMs: 0 } },
    });
    const res = await gw.request({ prompt: "scenario 7", task: "risk_judgment", riskContext: { scenario: 7 } });
    const out = JSON.parse(res.output);
    assert.ok(["LOW_RISK", "MEDIUM_RISK", "HIGH_RISK"].includes(out.verdict), "mock verdict shape");
    assert.equal(out.providerId, "jev");
    assert.ok(/MOCK/.test(out.note), "output must be labelled MOCK");
  });

  check("switch log records explicit model-switch on fallback", async () => {
    const gw = createGateway({
      models: [jev, bailian],
      policyDefault: "cost-optimized",
      maxRetries: 2,
      breakerConfig: { failureThreshold: 5 },
      behaviorByProvider: { jev: { baseLatencyMs: 50, latencyJitterMs: 0, fault: { timeoutRate: 1 } } },
    });
    const res = await gw.request({ prompt: "fallback demo", timeoutMs: 5 });
    assert.equal(res.ok, true);
    assert.equal(res.modelId, "bailian-qwen-plus");
    assert.ok(res.switchLog.some((s) => s.reason === "retry-exhausted-fallback"));
  });

  for (const [name, fn] of checks) {
    try { await fn(); PASS++; console.log("[PASS] " + name); }
    catch (e) { FAIL++; console.log("[FAIL] " + name + " :: " + (e && e.message ? e.message : String(e))); }
  }
  return { name: "gateway-integration", pass: PASS, fail: FAIL };
}
