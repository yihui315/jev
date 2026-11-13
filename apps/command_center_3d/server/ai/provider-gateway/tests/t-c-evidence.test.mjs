// T-C Decision-Evidence Archive — every decision archived with input hash,
// output, latency, cost, timestamp, routing context. Stdlib-only.
import assert from "node:assert/strict";
import { PROVIDER_MODELS } from "../catalog.mjs";
import { createGateway } from "../gateway.mjs";
import { sha256Hex } from "../protocol.mjs";

const checks = [];
function check(name, fn) { checks.push([name, fn]); }

export async function run() {
  let PASS = 0, FAIL = 0;
  const jev = PROVIDER_MODELS.find((m) => m.providerId === "jev");

  check("each successful request produces one archive entry with required fields", async () => {
    const gw = createGateway({
      models: [jev],
      policyDefault: "explicit",
      behaviorByProvider: { jev: { baseLatencyMs: 8, latencyJitterMs: 0 } },
    });
    const req = { prompt: "decide something meaningful", policy: "latency-optimized" };
    const res = await gw.request(req);
    assert.equal(res.ok, true);
    const all = gw.evidence.all();
    assert.ok(all.length >= 1);
    const rec = gw.evidence.get(res.evidenceId);
    assert.ok(rec, "evidence id from response must resolve");
    assert.equal(rec.modelId, jev.modelId);
    assert.equal(rec.providerId, jev.providerId);
    assert.ok(/^[0-9a-f]{64}$/.test(rec.inputHash), "inputHash must be sha256 hex");
    assert.ok(/^[0-9a-f]{64}$/.test(rec.outputRef), "outputRef must be sha256 hex");
    assert.equal(rec.output, res.output, "archived output matches response output");
    assert.equal(typeof rec.latencyMs, "number");
    assert.equal(typeof rec.costUsd, "number");
    assert.equal(typeof rec.timestampMs, "number");
    assert.equal(rec.policy, "latency-optimized");
    assert.equal(rec.ok, true);
    // input hash is deterministic for the same request
    const again = await gw.request(req);
    const rec2 = gw.evidence.get(again.evidenceId);
    assert.equal(rec2.inputHash, rec.inputHash, "same request -> same inputHash");
  });

  check("failed requests are also archived (fail-closed, never silent)", async () => {
    const gw = createGateway({
      models: [jev],
      policyDefault: "explicit",
      maxRetries: 0,
      behaviorByProvider: { jev: { baseLatencyMs: 10, latencyJitterMs: 0, fault: { fatalRate: 1 } } },
    });
    const res = await gw.request({ prompt: "will fail" });
    assert.equal(res.ok, false);
    const rec = gw.evidence.get(res.evidenceId);
    assert.ok(rec, "failure must be archived");
    assert.equal(rec.ok, false);
    assert.equal(rec.errorClass, "PROVIDER_BAD_RESPONSE");
    assert.equal(rec.output, null, "no fabricated output on failure");
  });

  check("archive supports byRequest / byModel / byProvider / serialize", async () => {
    const gw = createGateway({
      models: [jev],
      policyDefault: "explicit",
      behaviorByProvider: { jev: { baseLatencyMs: 8, latencyJitterMs: 0 } },
    });
    const res = await gw.request({ prompt: "query", requestId: "REQ-42" });
    assert.equal(gw.evidence.byRequest("REQ-42").length, 1);
    assert.equal(gw.evidence.byModel(jev.modelId).length, 1);
    assert.equal(gw.evidence.byProvider(jev.providerId).length, 1);
    const json = gw.evidence.serialize();
    assert.ok(json.includes('"schema": "jev/provider-gateway/evidence-archive/v1"'), "serialize carries archive schema");
    // independently verify hash helper exists and is stable
    assert.equal(sha256Hex("abc"), sha256Hex("abc"));
  });

  for (const [name, fn] of checks) {
    try { await fn(); PASS++; console.log("[PASS] " + name); }
    catch (e) { FAIL++; console.log("[FAIL] " + name + " :: " + (e && e.message ? e.message : String(e))); }
  }
  return { name: "evidence", pass: PASS, fail: FAIL };
}
