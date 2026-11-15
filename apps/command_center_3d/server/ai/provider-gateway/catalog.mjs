// JEV Phase C — Unified AI Multi-Model Gateway: provider model catalog.
//
// MOCK-ONLY. Every entry here is a SIMULATED backend descriptor used by the
// offline gateway + mock providers. None of these values is a real endpoint, a
// real key, a real latency measurement, or a real price quote. They are
// deliberately-shaped fixtures so the router / breaker / metrics / comparison
// harness have something deterministic to reason about WITHOUT egress.
//
// HONESTY RULES (same discipline as server/ai/provider-interface.mjs):
//   * riskCapability / baseLatencyMs / pricing are MOCK fixtures, not measured.
//   * They exist so the harness can DEMONSTRATE selection + metering logic.
//   * No real model performance or real cost is ever claimed from these numbers.

export const MOCK_DISCLAIMER =
  "MOCK-ONLY: catalog values are simulated fixtures (latency/cost/risk capability). " +
  "No real API call, real latency, real price, or real model performance is represented.";

/**
 * Four logical provider families requested for Phase C:
 *   - workbuddy  : WorkBuddy gateway
 *   - jev        : JEV internal scheduler / model
 *   - minimax-m3 : MiniMax M3
 *   - bailian    : 阿里百炼 (Alibaba Bailian / DashScope Qwen)
 *
 * Each descriptor drives the router, the mock provider simulation, and pricing.
 */
export const PROVIDER_MODELS = Object.freeze([
  Object.freeze({
    providerId: "workbuddy",
    modelId: "workbuddy-gateway-v1",
    displayName: "WorkBuddy Gateway",
    backend: "workbuddy",
    // mock fixture: relative risk-judgment capability used by risk-capability policy
    riskCapability: 0.6,
    // mock fixture: simulated baseline round-trip latency in ms
    baseLatencyMs: 120,
    // mock fixture: simulated latency jitter in ms
    latencyJitterMs: 40,
    // mock pricing fixture (USD per 1K tokens) — NOT a real quote
    pricing: Object.freeze({ promptPer1k: 0.001, completionPer1k: 0.002 }),
  }),
  Object.freeze({
    providerId: "jev",
    modelId: "jev-internal-v1",
    displayName: "JEV Internal Scheduler",
    backend: "jev",
    riskCapability: 0.85,
    baseLatencyMs: 60,
    latencyJitterMs: 20,
    pricing: Object.freeze({ promptPer1k: 0.0005, completionPer1k: 0.0008 }),
  }),
  Object.freeze({
    providerId: "minimax-m3",
    modelId: "MiniMax-M3",
    displayName: "MiniMax M3",
    backend: "minimax_m3",
    riskCapability: 0.9,
    baseLatencyMs: 200,
    latencyJitterMs: 80,
    pricing: Object.freeze({ promptPer1k: 0.0012, completionPer1k: 0.0018 }),
  }),
  Object.freeze({
    providerId: "bailian",
    modelId: "bailian-qwen-plus",
    displayName: "阿里百炼 Qwen-Plus (DashScope)",
    backend: "bailian",
    riskCapability: 0.75,
    baseLatencyMs: 160,
    latencyJitterMs: 60,
    pricing: Object.freeze({ promptPer1k: 0.0009, completionPer1k: 0.0015 }),
  }),
]);

/** Build a fast lookup by providerId / modelId. */
export function indexModels(models = PROVIDER_MODELS) {
  const byProvider = new Map();
  const byModel = new Map();
  for (const m of models) {
    byProvider.set(m.providerId, m);
    byModel.set(m.modelId, m);
  }
  return { byProvider, byModel };
}

/** Resolve a request's preferredModel (modelId OR providerId) to a descriptor. */
export function resolvePreferred(preferred, models = PROVIDER_MODELS) {
  if (!preferred) return null;
  const { byProvider, byModel } = indexModels(models);
  return byModel.get(preferred) || byProvider.get(preferred) || null;
}
