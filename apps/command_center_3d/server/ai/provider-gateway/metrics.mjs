// JEV Phase C — Per-provider metrics: token metering, cost, latency, failure-rate.
//
// MOCK-ONLY. Token counts + costs come from the mock providers' estimates and the
// mock pricing table; they are NOT real measurements. The snapshot() view exposes
// latency percentiles (p50/p95/max), running token totals, accumulated (mock)
// cost, and a failure-rate. All rates are rounded but not faked as zero.

function blank() {
  return {
    providerId: null,
    calls: 0,
    ok: 0,
    failures: 0,
    byClass: Object.create(null),
    totalLatencyMs: 0,
    latencySamples: [],
    promptTokens: 0,
    completionTokens: 0,
    totalTokens: 0,
    costUsd: 0,
  };
}

function percentile(sorted, p) {
  if (sorted.length === 0) return 0;
  const idx = Math.min(sorted.length - 1, Math.floor(p * sorted.length));
  return sorted[idx];
}

export function createMetrics() {
  const perProvider = new Map();

  function get(id) {
    let m = perProvider.get(id);
    if (!m) {
      m = blank();
      m.providerId = id;
      perProvider.set(id, m);
    }
    return m;
  }

  return {
    /** Count an attempt (before success/failure is known). */
    recordAttempt(providerId) {
      get(providerId).calls += 1;
    },

    /** Record a successful call's metering. */
    recordSuccess(providerId, { latencyMs, usage, cost }) {
      const m = get(providerId);
      m.ok += 1;
      m.totalLatencyMs += Number(latencyMs) || 0;
      m.latencySamples.push(Number(latencyMs) || 0);
      m.promptTokens += (usage && usage.promptTokens) || 0;
      m.completionTokens += (usage && usage.completionTokens) || 0;
      m.totalTokens += (usage && usage.totalTokens) || 0;
      m.costUsd = Math.round((m.costUsd + (cost && cost.usd ? cost.usd : 0)) * 1e8) / 1e8;
    },

    /** Record a failed call with its error class. */
    recordFailure(providerId, errorClass) {
      const m = get(providerId);
      m.failures += 1;
      const key = String(errorClass);
      m.byClass[key] = (m.byClass[key] || 0) + 1;
    },

    /** Snapshot of every provider's metrics. */
    snapshot() {
      const out = {};
      for (const [id, m] of perProvider) {
        const samples = [...m.latencySamples].sort((a, b) => a - b);
        const n = samples.length;
        const avg = n ? m.totalLatencyMs / n : 0;
        out[id] = {
          providerId: id,
          calls: m.calls,
          ok: m.ok,
          failures: m.failures,
          failureRate: m.calls ? Math.round((m.failures / m.calls) * 1e6) / 1e6 : 0,
          latencyMs: {
            avg: Math.round(avg * 1000) / 1000,
            min: n ? samples[0] : 0,
            p50: percentile(samples, 0.5),
            p95: percentile(samples, 0.95),
            max: n ? samples[n - 1] : 0,
          },
          tokens: { prompt: m.promptTokens, completion: m.completionTokens, total: m.totalTokens },
          costUsd: m.costUsd,
          byClass: { ...m.byClass },
        };
      }
      return out;
    },

    perProvider,
  };
}
