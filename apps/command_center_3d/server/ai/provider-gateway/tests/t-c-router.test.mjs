// T-C Router — policy-based model selection + healthy failover ordering.
// Stdlib-only; bare `node` runnable. Exports async run().
import assert from "node:assert/strict";
import { PROVIDER_MODELS } from "../catalog.mjs";
import { selectModel, ROUTING_POLICIES } from "../router.mjs";

const checks = [];
function check(name, fn) { checks.push([name, fn]); }

export async function run() {
  let PASS = 0, FAIL = 0;
  const [wb, jev, m3, bailian] = PROVIDER_MODELS; // workbuddy, jev, minimax-m3, bailian

  check("policies list is the five documented policies", () => {
    assert.deepEqual(
      [...ROUTING_POLICIES].sort(),
      ["cost-optimized", "explicit", "latency-optimized", "risk-capability", "round-robin"].sort()
    );
  });

  check("explicit policy honours preferredModel (modelId)", () => {
    const { candidates } = selectModel({ request: { preferredModel: m3.modelId }, models: PROVIDER_MODELS, health: {} });
    assert.equal(candidates[0].modelId, m3.modelId);
  });

  check("explicit policy honours preferredModel (providerId)", () => {
    const { candidates } = selectModel({ request: { preferredModel: "bailian" }, models: PROVIDER_MODELS, health: {} });
    assert.equal(candidates[0].providerId, "bailian");
  });

  check("cost-optimized sorts cheapest mock pricing first (jev < bailian < minimax-m3 < workbuddy)", () => {
    const { candidates } = selectModel({ request: {}, models: PROVIDER_MODELS, health: {}, policy: "cost-optimized" });
    assert.equal(candidates[0].providerId, "jev");
    const ids = candidates.map((c) => c.providerId).join(",");
    assert.ok(ids.indexOf("jev") < ids.indexOf("bailian"));
    assert.ok(ids.indexOf("bailian") < ids.indexOf("minimax-m3"));
    assert.ok(ids.indexOf("minimax-m3") < ids.indexOf("workbuddy"));
  });

  check("latency-optimized sorts lowest base latency first (jev < workbuddy < bailian < minimax-m3)", () => {
    const { candidates } = selectModel({ request: {}, models: PROVIDER_MODELS, health: {}, policy: "latency-optimized" });
    assert.equal(candidates[0].providerId, "jev");
    assert.equal(candidates[candidates.length - 1].providerId, "minimax-m3");
  });

  check("risk-capability sorts highest riskCapability first (minimax-m3 > jev > bailian > workbuddy)", () => {
    const { candidates } = selectModel({ request: {}, models: PROVIDER_MODELS, health: {}, policy: "risk-capability" });
    assert.equal(candidates[0].providerId, "minimax-m3");
    const ids = candidates.map((c) => c.providerId).join(",");
    assert.ok(ids.indexOf("minimax-m3") < ids.indexOf("jev"));
    assert.ok(ids.indexOf("jev") < ids.indexOf("bailian"));
    assert.ok(ids.indexOf("bailian") < ids.indexOf("workbuddy"));
  });

  check("round-robin rotates the candidate list via shared cursor", () => {
    const rr = { index: 0 };
    const r1 = selectModel({ request: {}, models: PROVIDER_MODELS, health: {}, policy: "round-robin", rrState: rr });
    const r2 = selectModel({ request: {}, models: PROVIDER_MODELS, health: {}, policy: "round-robin", rrState: rr });
    assert.equal(r1.candidates[0].providerId, "workbuddy");
    assert.equal(r2.candidates[0].providerId, "jev");
    assert.notEqual(r1.candidates[0].providerId, r2.candidates[0].providerId);
  });

  check("router preserves policy order (failover is handled by the gateway's per-candidate OPEN skip)", () => {
    // Even with jev + minimax-m3 OPEN, cost-optimized still lists jev first:
    // the gateway skips OPEN candidates in its loop and logs the switch, so the
    // router must NOT silently reorder (which would hide the skip).
    const health = { workbuddy: { state: "CLOSED" }, jev: { state: "OPEN" }, "minimax-m3": { state: "OPEN" }, bailian: { state: "CLOSED" } };
    const { candidates } = selectModel({ request: {}, models: PROVIDER_MODELS, health, policy: "cost-optimized" });
    assert.equal(candidates[0].providerId, "jev", "policy order must be preserved (gateway does the OPEN skip)");
    assert.equal(candidates[1].providerId, "bailian");
  });

  check("unknown policy falls back to cost-optimized deterministically", () => {
    const { policy, candidates } = selectModel({ request: {}, models: PROVIDER_MODELS, health: {}, policy: "nonsense" });
    assert.equal(policy, "cost-optimized");
    assert.equal(candidates[0].providerId, "jev");
  });

  for (const [name, fn] of checks) {
    try { await fn(); PASS++; console.log("[PASS] " + name); }
    catch (e) { FAIL++; console.log("[FAIL] " + name + " :: " + (e && e.message ? e.message : String(e))); }
  }
  return { name: "router", pass: PASS, fail: FAIL };
}
