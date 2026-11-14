// JEV Phase C — Mock provider backends (simulated, offline, no egress).
//
// MOCK-ONLY. Each createMockProvider() returns an object with a `complete()`
// method that SIMULATES one backend's behaviour:
//   * simulated latency = base + jitter*rng()
//   * optional fault injection (timeout / transient / fatal) driven by an
//     injectable rng so tests are deterministic
//   * deterministic token estimate derived from a hash of the prompt (NOT a real
//     token count)
//   * a deterministic mock output (structured decision / answer)
//
// No network, no credentials, no real inference. The simulated latency/cost are
// fixtures; timeouts are real timers but only to exercise the gateway's timeout
// path — nothing is ever sent anywhere.

import { sha256Hex } from "./protocol.mjs";

function seedFrom(str) {
  return [...sha256Hex(str).match(/.{2}/g)].map((h) => parseInt(h, 16));
}

/**
 * @param {object} opts
 * @param {object} opts.model          descriptor from catalog.mjs
 * @param {object} [opts.behavior]     { baseLatencyMs, latencyJitterMs, fault }
 * @param {object} [opts.fault]        { timeoutRate, retryableRate, fatalRate } in [0,1]
 * @param {Function} [opts.rng]        injectable RNG (default Math.random)
 * @param {Function} [opts.sleep]      injectable sleep (default real setTimeout)
 */
export function createMockProvider({ model, behavior = {}, rng, sleep } = {}) {
  if (!model || !model.providerId) throw new TypeError("createMockProvider: model descriptor required");
  const r = typeof rng === "function" ? rng : Math.random;
  const doSleep = typeof sleep === "function" ? sleep : (ms) => new Promise((res) => setTimeout(res, ms));

  const baseLatency = Number.isFinite(behavior.baseLatencyMs) ? behavior.baseLatencyMs : model.baseLatencyMs;
  const jitter = Number.isFinite(behavior.latencyJitterMs) ? behavior.latencyJitterMs : model.latencyJitterMs;
  const fault = Object.assign(
    { timeoutRate: 0, retryableRate: 0, fatalRate: 0 },
    behavior.fault || {}
  );

  function mockOutput(request, seed) {
    const task = request.task || "chat";
    if (task === "risk_judgment") {
      // deterministic risk verdict, biased by the model's MOCK riskCapability so
      // different models produce different (still simulated) risk judgments.
      const capability = Number(model.riskCapability) || 0.5;
      const riskScore = Math.min(1, (seed[2] / 255) * 0.7 + capability * 0.3);
      const verdict = riskScore > 0.6 ? "HIGH_RISK" : riskScore > 0.3 ? "MEDIUM_RISK" : "LOW_RISK";
      return JSON.stringify({
        providerId: model.providerId,
        model: model.modelId,
        task,
        verdict,
        riskScore: Math.round(riskScore * 1000) / 1000,
        confidence: Math.round((seed[3] / 255) * 1000) / 1000,
        note: "MOCK decision; not a real model output",
      });
    }
    return JSON.stringify({
      providerId: model.providerId,
      model: model.modelId,
      task,
      answer: "mock-response-" + (seed[0] % 1000),
      note: "MOCK response; no real inference performed",
    });
  }

  async function complete(request, ctx = {}) {
    const timeoutMs = Number(ctx.timeoutMs) || 0;
    const lat = Math.max(0, Math.round(baseLatency + r() * jitter));

    // --- fault injection (deterministic via injected rng) ---
    const roll = r();
    let forceCode = null;
    if (roll < fault.timeoutRate) forceCode = "PROVIDER_TIMEOUT";
    else if (roll < fault.timeoutRate + fault.retryableRate) forceCode = "PROVIDER_TRANSIENT";
    else if (roll < fault.timeoutRate + fault.retryableRate + fault.fatalRate) forceCode = "PROVIDER_BAD_RESPONSE";

    if (forceCode === "PROVIDER_TIMEOUT") {
      // simulate a slow backend that exceeds the timeout budget
      await doSleep(Math.min(lat || 1, Math.max(timeoutMs, 1)));
      const e = new Error("simulated backend timeout for " + model.providerId);
      e.code = "PROVIDER_TIMEOUT";
      throw e;
    }
    if (forceCode === "PROVIDER_TRANSIENT") {
      const e = new Error("simulated transient failure for " + model.providerId);
      e.code = "PROVIDER_TRANSIENT";
      throw e; // retryable
    }
    if (forceCode === "PROVIDER_BAD_RESPONSE") {
      const e = new Error("simulated malformed response for " + model.providerId);
      e.code = "PROVIDER_BAD_RESPONSE";
      throw e; // non-retryable
    }

    // --- success path ---
    await doSleep(Math.min(lat, timeoutMs || lat));
    const seed = seedFrom(String(request.prompt || "") + "|" + model.modelId);
    const promptLen = request.prompt ? String(request.prompt).length : 0;
    const promptTokens = Math.max(1, Math.round(promptLen / 4 + (seed[0] / 255) * 30));
    const completionTokens = Math.max(1, Math.round(24 + (seed[1] / 255) * 96));
    const usage = {
      promptTokens,
      completionTokens,
      totalTokens: promptTokens + completionTokens,
      provenance: "MOCK_ESTIMATE",
    };
    const output = mockOutput(request, seed);
    const rawResponseRef = "ref-" + sha256Hex(model.modelId + ":" + String(request.prompt || "")).slice(0, 16);
    return {
      providerId: model.providerId,
      model: model.modelId,
      ok: true,
      output,
      usage,
      finishReason: "stop",
      rawResponseRef,
      latencyMs: lat,
    };
  }

  return Object.freeze({ providerId: model.providerId, modelId: model.modelId, complete });
}
