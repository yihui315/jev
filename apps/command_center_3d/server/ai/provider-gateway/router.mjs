// JEV Phase C — Model Router.
//
// MOCK-ONLY. Chooses an ORDERED candidate list of models for a request given a
// routing policy. Healthy providers (circuit not OPEN) are always ranked ahead
// of OPEN ones, so the gateway can fail over. Policies: explicit, cost-optimized,
// latency-optimized, risk-capability, round-robin.
//
// The router reasons over MOCK fixtures (pricing / latency / risk capability)
// from catalog.mjs. It does NOT perform any call or measurement.

import { PROVIDER_MODELS } from "./catalog.mjs";
import { resolvePreferred } from "./catalog.mjs";

export const ROUTING_POLICIES = Object.freeze([
  "explicit",
  "cost-optimized",
  "latency-optimized",
  "risk-capability",
  "round-robin",
]);

/**
 * @param {object} p
 * @param {object} p.request      normalized request (has .policy, .preferredModel)
 * @param {Array}  [p.models]     catalog descriptors
 * @param {object} [p.health]     { [providerId]: { state } } (informational; the
 *                                  gateway performs the real OPEN skip per-candidate)
 * @param {string} [p.policy]     override policy
 * @param {object} [p.rrState]    { index } mutable round-robin cursor (shared)
 * @returns {{policy:string, candidates:Array}}
 */
export function selectModel({ request, models = PROVIDER_MODELS, health = {}, policy, rrState } = {}) {
  void health; // informational only; the gateway performs the real OPEN skip per-candidate
  const requested = policy || (request && request.preferredModel ? "explicit" : (request && request.policy)) || "cost-optimized";
  // normalize unknown policies to cost-optimized (fail-safe default)
  const effective = ROUTING_POLICIES.includes(requested) ? requested : "cost-optimized";

  let ordered;
  switch (effective) {
    case "explicit": {
      const pref = resolvePreferred(request && request.preferredModel, models);
      ordered = pref
        ? [pref, ...models.filter((m) => m !== pref)]
        : [...models];
      break;
    }
    case "latency-optimized":
      ordered = [...models].sort((a, b) => a.baseLatencyMs - b.baseLatencyMs);
      break;
    case "risk-capability":
      ordered = [...models].sort((a, b) => b.riskCapability - a.riskCapability);
      break;
    case "round-robin": {
      const start = rrState && Number.isFinite(rrState.index) ? ((rrState.index % models.length) + models.length) % models.length : 0;
      ordered = models.slice(start).concat(models.slice(0, start));
      if (rrState) rrState.index = (start + 1) % models.length;
      break;
    }
    case "cost-optimized":
    default: {
      // estimated cost using a fixed mock token assumption (40 prompt / 60 completion)
      const est = (m) => (40 / 1000) * m.pricing.promptPer1k + (60 / 1000) * m.pricing.completionPer1k;
      ordered = [...models].sort((a, b) => est(a) - est(b));
      break;
    }
  }

  // NOTE: we deliberately do NOT reorder by health here. The gateway inspects
  // each candidate's circuit state in the loop and fast-skips OPEN providers
  // (logging a model-switch), which is what makes failover observable and the
  // explicit skip log reachable. Policy order is therefore preserved.
  return { policy: effective, candidates: ordered };
}
