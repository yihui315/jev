// JEV Phase C — Same-dataset blind comparison harness (MOCK-ONLY).
//
// Runs EVERY model across the SAME synthetic dataset and records, per model:
//   * accuracy             (matches a MOCK ground-truth reference label)
//   * decision consistency (intra-model determinism + inter-model agreement)
//   * response time        (avg / p50 / p95 / max)
//   * cost per call        (mock pricing)
//   * error rate
//   * risk-judgment ability (verdict distribution on risk_judgment tasks)
//
// CRITICAL: all inputs, outputs, latency, cost and "ground truth" are simulated
// via mock providers + mock pricing. This harness DEMONSTRATES the measurement
// capability only. It must NEVER be read as a real model benchmark.

import { PROVIDER_MODELS } from "./catalog.mjs";
import { createGateway } from "./gateway.mjs";

const RISK_LABELS = ["LOW_RISK", "MEDIUM_RISK", "HIGH_RISK"];

/**
 * Build a synthetic risk_judgment dataset with a MOCK ground-truth label.
 * @param {number} n
 * @param {Function} [rng] injectable in [0,1)
 */
export function buildSyntheticDataset(n = 12, rng) {
  const r = typeof rng === "function" ? rng : Math.random;
  const items = [];
  for (let i = 0; i < n; i++) {
    const label = RISK_LABELS[Math.floor(r() * RISK_LABELS.length) % RISK_LABELS.length];
    items.push({
      id: "case-" + i,
      prompt: "evaluate risk scenario " + i,
      task: "risk_judgment",
      riskContext: { scenario: i },
      referenceLabel: label,
    });
  }
  return items;
}

function safeVerdict(res) {
  if (!res || !res.ok) return null;
  try {
    return JSON.parse(res.output).verdict;
  } catch {
    return null;
  }
}

function summarizeLatency(arr) {
  const lat = [...arr].sort((a, b) => a - b);
  const avg = (xs) => (xs.length ? Math.round((xs.reduce((s, x) => s + x, 0) / xs.length) * 1000) / 1000 : 0);
  const pct = (p) => (lat.length ? lat[Math.min(lat.length - 1, Math.floor(p * lat.length))] : 0);
  return { avg: avg(lat), p50: pct(0.5), p95: pct(0.95), max: lat.length ? lat[lat.length - 1] : 0 };
}

/**
 * @param {object} p
 * @param {Array}  [p.dataset]
 * @param {Array}  [p.models]
 * @param {string} [p.policy]
 * @param {object} [p.gatewayOpts]  forwarded to createGateway (incl. mock behavior)
 */
export async function runComparison(p = {}) {
  const dataset = p.dataset || buildSyntheticDataset(12);
  const models = p.models || PROVIDER_MODELS;
  const policy = p.policy || "risk-capability";
  const gatewayOpts = p.gatewayOpts || {};

  // ---- per-model measurement across the SAME dataset ----
  const perModel = {};
  for (const m of models) {
    const gw = createGateway({ ...gatewayOpts, models: [m], policyDefault: policy });
    const rec = {
      calls: 0, ok: 0, errors: 0, latencies: [], costs: [], verdicts: Object.create(null), matches: 0,
    };
    for (const item of dataset) {
      const res = await gw.request({
        prompt: item.prompt,
        task: item.task,
        riskContext: item.riskContext,
        riskContextLabel: item.referenceLabel,
      });
      rec.calls += 1;
      if (res.ok) {
        rec.ok += 1;
        rec.latencies.push(res.latencyMs);
        rec.costs.push(res.cost ? res.cost.usd : 0);
        const v = safeVerdict(res);
        if (v) {
          rec.verdicts[v] = (rec.verdicts[v] || 0) + 1;
          if (v === item.referenceLabel) rec.matches += 1;
        }
      } else {
        rec.errors += 1;
      }
    }
    perModel[m.modelId] = {
      modelId: m.modelId,
      providerId: m.providerId,
      calls: rec.calls,
      ok: rec.ok,
      errors: rec.errors,
      errorRate: rec.calls ? Math.round((rec.errors / rec.calls) * 1e6) / 1e6 : 0,
      accuracy: rec.ok ? Math.round((rec.matches / rec.ok) * 1e6) / 1e6 : 0,
      responseTimeMs: summarizeLatency(rec.latencies),
      costPerCallUsd: rec.ok ? Math.round((rec.costs.reduce((s, x) => s + x, 0) / rec.ok) * 1e8) / 1e8 : 0,
      riskVerdictDistribution: { ...rec.verdicts },
    };
  }

  // ---- intra-model determinism (mock is deterministic given same input) ----
  const intraModelConsistency = {};
  for (const m of models) {
    const first = {};
    const gwA = createGateway({ ...gatewayOpts, models: [m], policyDefault: policy });
    for (const item of dataset) {
      const r = await gwA.request({ prompt: item.prompt, task: item.task, riskContext: item.riskContext });
      first[item.id] = safeVerdict(r);
    }
    const second = {};
    const gwB = createGateway({ ...gatewayOpts, models: [m], policyDefault: policy });
    for (const item of dataset) {
      const r = await gwB.request({ prompt: item.prompt, task: item.task, riskContext: item.riskContext });
      second[item.id] = safeVerdict(r);
    }
    let agree = 0;
    for (const item of dataset) {
      if (first[item.id] !== null && first[item.id] === second[item.id]) agree += 1;
    }
    intraModelConsistency[m.modelId] = {
      intraModelDeterminism: dataset.length ? Math.round((agree / dataset.length) * 1e6) / 1e6 : 0,
    };
  }

  // ---- inter-model agreement (pairwise) ----
  const perModelVerdicts = {};
  for (const m of models) {
    const gw = createGateway({ ...gatewayOpts, models: [m], policyDefault: policy });
    const map = {};
    for (const item of dataset) {
      const r = await gw.request({ prompt: item.prompt, task: item.task, riskContext: item.riskContext });
      map[item.id] = safeVerdict(r);
    }
    perModelVerdicts[m.modelId] = map;
  }
  const interModelAgreement = {};
  for (let i = 0; i < models.length; i++) {
    for (let j = i + 1; j < models.length; j++) {
      const a = models[i].modelId;
      const b = models[j].modelId;
      let agree = 0;
      let both = 0;
      for (const item of dataset) {
        if (perModelVerdicts[a][item.id] !== null && perModelVerdicts[b][item.id] !== null) {
          both += 1;
          if (perModelVerdicts[a][item.id] === perModelVerdicts[b][item.id]) agree += 1;
        }
      }
      interModelAgreement[a + "/" + b] = both ? Math.round((agree / both) * 1e6) / 1e6 : 0;
    }
  }

  return {
    disclaimer:
      "MOCK-ONLY: all latency/cost/accuracy/risk-judgment figures are simulated via mock " +
      "providers and mock pricing tables. No real model performance or real cost was measured.",
    datasetSize: dataset.length,
    models: perModel,
    intraModelConsistency,
    interModelAgreement,
  };
}
