// JEV Phase C — Unified Provider Gateway: request/response protocol + hashing.
//
// MOCK-ONLY. This module defines the UNIFIED request protocol and the UNIFIED
// (normalized) response protocol shared by every backend behind the gateway.
// Attribution is NEVER re-derived here: the canonical envelope / attribution
// guard is delegated to the EXISTING contract in ../provider-interface.mjs so
// Phase C extends, and does not fork, that contract.

import crypto from "node:crypto";
import { normalizeResult, assertResponseAttribution } from "../provider-interface.mjs";

export const GATEWAY_RESULT_SCHEMA = "jev/provider-gateway/result/1";
export const DATA_MODE = "mock";
export const IS_REAL = false;

export const MOCK_DISCLAIMER =
  "MOCK-ONLY: no real API calls, no real cost, no real model performance was measured. " +
  "All latency/cost/accuracy figures are simulated via mock providers + mock pricing tables.";

// ---------------------------------------------------------------------------
// Hashing (attribution + evidence integrity)
// ---------------------------------------------------------------------------

/** sha256 hex of a string or Buffer. */
export function sha256Hex(input) {
  const buf = Buffer.isBuffer(input) ? input : Buffer.from(String(input), "utf8");
  return crypto.createHash("sha256").update(buf).digest("hex");
}

/**
 * Deterministic hash of the meaningful inputs to a request. Used as the
 * inputHash stored in the decision-evidence archive. Two requests with the
 * same prompt/task/riskContext/policy hash identically.
 */
export function hashInput(request) {
  const key = JSON.stringify({
    prompt: request.prompt ?? "",
    task: request.task ?? "",
    riskContext: request.riskContext ?? null,
    policy: request.policy ?? null,
    preferredModel: request.preferredModel ?? null,
  });
  return sha256Hex("jev/gw/input:" + key);
}

/** Hash of a normalized output (for outputRef-style integrity in the archive). */
export function hashOutput(output) {
  return sha256Hex("jev/gw/output:" + (typeof output === "string" ? output : JSON.stringify(output)));
}

// ---------------------------------------------------------------------------
// Normalized (unified) response finalizers
// ---------------------------------------------------------------------------

/**
 * Finalize a SUCCESS response: reuse the existing attribution guard +
 * canonical envelope, then attach gateway-layer fields (switch log, circuit
 * state, evidence id, input hash, MOCK disclaimer). Attribution is copied
 * verbatim from the provider; the gateway never re-derives it.
 */
export function finalizeSuccess({ request, model, raw, cost, output, latencyMs, switchLog, circuitState, evidenceId, inputHash }) {
  const attribution = assertResponseAttribution(
    { providerId: model.providerId, model: model.modelId },
    {
      providerId: model.providerId,
      model: model.modelId,
      latencyMs,
      usage: raw.usage,
      cost,
      finishReason: raw.finishReason,
      rawResponseRef: raw.rawResponseRef,
    }
  );
  const env = normalizeResult({
    providerId: model.providerId,
    model: model.modelId,
    ok: true,
    latencyMs,
    usage: raw.usage,
    cost,
    rawResponseRef: raw.rawResponseRef,
    finishReason: raw.finishReason,
    errorClass: null,
  });
  return Object.freeze({
    ...env,
    schema: GATEWAY_RESULT_SCHEMA, // override env.schema with the gateway schema
    modelId: model.modelId, // explicit alias alongside env.model
    output,
    inputHash,
    switchLog: Object.freeze(switchLog),
    circuitState,
    evidenceId,
    dataMode: DATA_MODE,
    isReal: IS_REAL,
    mockDisclaimer: MOCK_DISCLAIMER,
    receivedAtMs: request.receivedAtMs ?? null,
  });
}

/**
 * Finalize an ERROR response. errorClass must be a member of the gateway error
 * taxonomy (reuses ../provider-interface.mjs ERROR_TAXONOMY plus the gateway
 * local codes supplied by the caller). `retry` is optional
 * ({ attempted, max, exhausted }) used for PROVIDER_RETRY_EXHAUSTED.
 */
export function finalizeError({ request, model, providerId, errorClass, switchLog, circuitState, evidenceId, inputHash, retry }) {
  const env = normalizeResult({
    providerId: providerId || (model && model.providerId) || request.providerId || null,
    model: model ? model.modelId : null,
    ok: false,
    errorClass,
    retryAttempted: retry ? retry.attempted : 0,
    retryExhausted: !!(retry && retry.exhausted),
  });
  return Object.freeze({
    ...env,
    schema: GATEWAY_RESULT_SCHEMA, // override env.schema with the gateway schema
    modelId: model ? model.modelId : null, // explicit alias alongside env.model
    output: null,
    inputHash,
    switchLog: Object.freeze(switchLog),
    circuitState,
    evidenceId,
    dataMode: DATA_MODE,
    isReal: IS_REAL,
    mockDisclaimer: MOCK_DISCLAIMER,
    receivedAtMs: request.receivedAtMs ?? null,
  });
}
