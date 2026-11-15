// JEV Phase C — Unified Provider Gateway orchestrator.
//
// MOCK-ONLY. This is the single entry point that ties together:
//   * Model Router (policy-based selection + healthy failover ordering)
//   * Timeout (per-call budget via Promise.race)
//   * Bounded Retry (retryable errors only, capped at maxRetries)
//   * Circuit Breaker (per provider; OPEN => reject fast, no egress)
//   * Token metering + cost recording (from MOCK pricing)
//   * Explicit model-switch logging
//   * Decision-evidence archive (input hash, output, latency, cost, timestamp)
//   * Normalized response that reuses the EXISTING provider-interface contract
//     (normalizeResult + assertResponseAttribution) so attribution is never
//     re-derived here.
//
// Real paid API calls stay OFF. Mock providers simulate each backend. No
// network egress, no credentials, no real spend.

import { PROVIDER_MODELS } from "./catalog.mjs";
import { createMockProvider } from "./mock-providers.mjs";
import { createCircuitBreaker, BREAKER_STATES } from "./circuit-breaker.mjs";
import { createMetrics } from "./metrics.mjs";
import { createEvidenceArchive } from "./evidence-archive.mjs";
import { createSwitchLogger } from "./logging.mjs";
import { selectModel } from "./router.mjs";
import { computeMockCost } from "./pricing.mjs";
import {
  hashInput,
  hashOutput,
  finalizeSuccess,
  finalizeError,
} from "./protocol.mjs";
import { normalizeResult, assertResponseAttribution, ERROR_TAXONOMY, RESULT_POLICY } from "../provider-interface.mjs";

// Gateway-layer error codes, layered on top of the existing taxonomy.
export const GATEWAY_ERROR_CODES = Object.freeze({
  ...ERROR_TAXONOMY,
  PROVIDER_TRANSIENT: "Transient backend failure; eligible for bounded retry.",
  PROVIDER_CIRCUIT_OPEN: "Circuit breaker OPEN for this provider; call rejected without egress.",
});

// Only these classes are eligible for bounded retry. Auth / attribution / bad
// response are NON-retryable (fail fast, do not burn budget).
const RETRYABLE = new Set(["PROVIDER_TIMEOUT", "PROVIDER_TRANSIENT"]);

export const DEFAULT_TIMEOUT_MS = RESULT_POLICY.defaultTimeoutMs; // 45000 (documented policy)

/**
 * @param {object} [opts]
 * @param {Array}    [opts.models]
 * @param {object}   [opts.behaviorByProvider]  { [providerId]: behavior } for mock providers
 * @param {object}   [opts.rngByProvider]       { [providerId]: rng fn }
 * @param {Function} [opts.clock]               injectable clock
 * @param {Function} [opts.sleep]               injectable sleep
 * @param {string}   [opts.policyDefault]
 * @param {number}   [opts.maxRetries]
 * @param {object}   [opts.breakerConfig]
 * @param {object}   [opts.metrics|evidence|switchLogger]  injectable (for tests)
 */
export function createGateway(opts = {}) {
  const clock = typeof opts.clock === "function" ? opts.clock : () => Date.now();
  const sleep = typeof opts.sleep === "function" ? opts.sleep : (ms) => new Promise((res) => setTimeout(res, ms));
  const policyDefault = opts.policyDefault || "cost-optimized";
  const maxRetries = Number.isFinite(opts.maxRetries) ? opts.maxRetries : RESULT_POLICY.maxRetries; // 2
  const models = opts.models || PROVIDER_MODELS;
  const behaviorByProvider = opts.behaviorByProvider || {};
  const rngByProvider = opts.rngByProvider || {};
  const breakerCfg = opts.breakerConfig || {};

  const providers = {};
  for (const m of models) {
    providers[m.providerId] = createMockProvider({
      model: m,
      behavior: behaviorByProvider[m.providerId],
      rng: rngByProvider[m.providerId],
      sleep,
      clock,
    });
  }
  const breakers = {};
  for (const m of models) breakers[m.providerId] = createCircuitBreaker({ clock, ...breakerCfg });

  const metrics = opts.metrics || createMetrics();
  const evidence = opts.evidence || createEvidenceArchive();
  const switchLogger = opts.switchLogger || createSwitchLogger();
  const rr = { index: 0 };

  function timeoutWrap(promise, ms, providerId) {
    let t;
    const to = new Promise((_, rej) => {
      t = setTimeout(() => {
        const e = new Error("gateway timeout provider=" + providerId);
        e.code = "PROVIDER_TIMEOUT";
        rej(e);
      }, ms);
    });
    return Promise.race([promise, to]).finally(() => clearTimeout(t));
  }

  async function attemptOnce(provider, model, request, timeoutMs) {
    metrics.recordAttempt(provider.providerId);
    const started = clock();
    try {
      const raw = await timeoutWrap(provider.complete(request, { timeoutMs, clock }), timeoutMs, provider.providerId);
      const usage = raw.usage;
      const cost = computeMockCost(model.pricing, usage);
      const latencyMs = clock() - started;
      metrics.recordSuccess(provider.providerId, { latencyMs, usage, cost });
      breakers[provider.providerId].recordSuccess();
      return { ok: true, raw, cost, latencyMs };
    } catch (err) {
      const code = err && err.code ? err.code : "PROVIDER_TRANSIENT";
      metrics.recordFailure(provider.providerId, code);
      breakers[provider.providerId].recordFailure();
      return { ok: false, errorClass: code };
    }
  }

  function normalizeRequest(r) {
    const now = clock();
    return {
      requestId: r.requestId || "req-" + now + "-" + Math.random().toString(36).slice(2, 8),
      prompt: r.prompt ?? "",
      task: r.task ?? "chat",
      riskContext: r.riskContext ?? null,
      policy: r.policy ?? null,
      preferredModel: r.preferredModel ?? null,
      maxCostUsd: r.maxCostUsd ?? null,
      timeoutMs: Number.isFinite(r.timeoutMs) ? r.timeoutMs : DEFAULT_TIMEOUT_MS,
      receivedAtMs: r.receivedAtMs ?? now,
    };
  }

  function healthView() {
    const h = {};
    for (const m of models) h[m.providerId] = { state: breakers[m.providerId].state() };
    return h;
  }

  /**
   * Submit a unified request. Returns a normalized (frozen) response.
   * @param {object} userRequest
   */
  async function request(userRequest) {
    const request = normalizeRequest(userRequest);
    const inputHash = hashInput(request);
    // a requested model implies explicit routing; otherwise fall back to default
    const policy = request.preferredModel ? "explicit" : (request.policy || policyDefault);
    const { candidates } = selectModel({ request, models, health: healthView(), policy, rrState: rr });
    const switchLog = [];
    let lastError = null;

    for (let i = 0; i < candidates.length; i++) {
      const model = candidates[i];
      const breaker = breakers[model.providerId];
      const state = breaker.state();
      const hasNext = i < candidates.length - 1;

      // OPEN breaker: skip, try next candidate (failover)
      if (state === BREAKER_STATES.OPEN) {
        switchLog.push({
          atMs: clock(),
          from: i > 0 ? candidates[i - 1].modelId : (request.preferredModel || null),
          to: null,
          reason: "circuit-open-skip:" + model.providerId,
          policy,
        });
        continue;
      }

      // explicit model-switch logging
      if (i === 0) {
        switchLog.push({
          atMs: clock(),
          from: request.preferredModel || null,
          to: model.modelId,
          reason: request.preferredModel ? "explicit" : "policy-select:" + policy,
          policy,
        });
      } else {
        switchLog.push({
          atMs: clock(),
          from: candidates[i - 1].modelId,
          to: model.modelId,
          reason: state === BREAKER_STATES.OPEN ? "circuit-open-fallback" : "retry-exhausted-fallback",
          policy,
        });
      }

      // bounded retry loop
      let res = null;
      let attempt = 0;
      while (true) {
        res = await attemptOnce(providers[model.providerId], model, request, request.timeoutMs);
        if (res.ok) break;
        const canRetry = RETRYABLE.has(res.errorClass) && attempt < maxRetries;
        if (!canRetry) break;
        attempt += 1;
      }

      if (res.ok) {
        const output = res.raw.output;
        const evidenceId = evidence.add({
          requestId: request.requestId,
          modelId: model.modelId,
          providerId: model.providerId,
          inputHash,
          outputRef: hashOutput(output),
          output,
          latencyMs: res.latencyMs,
          costUsd: res.cost.usd,
          timestampMs: clock(),
          policy,
          switchReason: switchLog.length ? switchLog[switchLog.length - 1].reason : null,
          circuitState: state,
          errorClass: null,
          ok: true,
        });
        return finalizeSuccess({
          request,
          model,
          raw: res.raw,
          cost: res.cost,
          output,
          latencyMs: res.latencyMs,
          switchLog,
          circuitState: state,
          evidenceId,
          inputHash,
        });
      }

      lastError = res.errorClass;

      // Retryable exhausted: fail over to next candidate if available.
      if (RETRYABLE.has(res.errorClass) && attempt >= maxRetries) {
        if (hasNext) {
          switchLog.push({
            atMs: clock(),
            from: model.modelId,
            to: null,
            reason: "retry-exhausted-fallback",
            policy,
          });
          continue;
        }
        const evidenceId = evidence.add({
          requestId: request.requestId,
          modelId: model.modelId,
          providerId: model.providerId,
          inputHash,
          outputRef: null,
          output: null,
          latencyMs: 0,
          costUsd: 0,
          timestampMs: clock(),
          policy,
          switchReason: "retry-exhausted",
          circuitState: state,
          errorClass: "PROVIDER_RETRY_EXHAUSTED",
          ok: false,
        });
        return finalizeError({
          request,
          model,
          providerId: model.providerId,
          errorClass: "PROVIDER_RETRY_EXHAUSTED",
          switchLog,
          circuitState: state,
          evidenceId,
          inputHash,
          retry: { attempted: attempt, max: maxRetries, exhausted: true },
        });
      }

      // Non-retryable failure: stop, do not fail over.
      const evidenceId = evidence.add({
        requestId: request.requestId,
        modelId: model.modelId,
        providerId: model.providerId,
        inputHash,
        outputRef: null,
        output: null,
        latencyMs: 0,
        costUsd: 0,
        timestampMs: clock(),
        policy,
        switchReason: switchLog.length ? switchLog[switchLog.length - 1].reason : null,
        circuitState: state,
        errorClass: res.errorClass,
        ok: false,
      });
      return finalizeError({
        request,
        model,
        providerId: model.providerId,
        errorClass: res.errorClass,
        switchLog,
        circuitState: state,
        evidenceId,
        inputHash,
      });
    }

    // Every candidate was skipped (OPEN) or failed over with no remaining target.
    const code = lastError || "PROVIDER_CIRCUIT_OPEN";
    const evidenceId = evidence.add({
      requestId: request.requestId,
      modelId: null,
      providerId: null,
      inputHash,
      outputRef: null,
      output: null,
      latencyMs: 0,
      costUsd: 0,
      timestampMs: clock(),
      policy,
      switchReason: "all-candidates-failed",
      circuitState: null,
      errorClass: code,
      ok: false,
    });
    return finalizeError({
      request,
      model: null,
      providerId: null,
      errorClass: code,
      switchLog,
      circuitState: null,
      evidenceId,
      inputHash,
    });
  }

  return {
    request,
    metrics,
    evidence,
    switchLogger,
    breakers,
    providers,
    models,
    healthView,
  };
}
