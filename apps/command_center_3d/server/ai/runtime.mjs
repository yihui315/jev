// JEV V1.6 Track B - runtime wiring: provider registry, real connectivity test,
// decision creation (mock|real), and idempotent paper execution.
//
// This module is transport-agnostic. The Cloudflare gateway (functions/api/jev/
// [[path]].js) maps HTTP to these functions; tests call them directly with an
// in-memory D1 shim and an injected fetch / engine handler.
//
// FAIL-CLOSED RULES
//   * dataMode 'real' without a resolvable credential => PROVIDER_BLOCKED.
//     Nothing is written and nothing is claimed: no fabricated inference.
//   * A decision must pass decision-schema.mjs AND risk-gate.mjs before it can
//     be executed. Execution refuses anything whose stored risk_check_result is
//     not PASS, so a caller cannot bypass the gate by calling execute directly.
//   * Re-executing the same decision_id never calls the engine twice.

import {
  buildRequest, performRequest, classifyResponse, describeRequest, describeSecret,
  normalizeProvider, DEFAULT_BASE_URLS, ERROR_CODES,
} from "./provider-adapter.mjs";
import { validateDecision, canonicalize, DECISION_SCHEMA_VERSION } from "./decision-schema.mjs";
import { riskCheck, RISK_ALLOWED_SYMBOLS, loadMarketContext, RISK_CODES } from "./risk-gate.mjs";
import { toDecisionRow, insertDecision, recordRiskVerdict, recordSubmission, linkOrder, getDecision, listRunDecisions, performanceSummary } from "./decision-store.mjs";
import { generateMockDecision, mockChatCompletion, MOCK_MODEL_ID, MOCK_PROVIDER_ID } from "./mock-provider.mjs";

export const PROMPT_VERSION = "jev.ai.prompt/v1";
const MAX_DECISION_BODY_USD = 1000;

export async function sha256Hex(text) {
  const bytes = new TextEncoder().encode(String(text));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function runtimeError(code, message, extra) {
  const err = new Error(message || code);
  err.code = code;
  if (extra) err.detail = extra;
  return err;
}

function envStr(env, key, fallback) {
  const v = env && env[key];
  return typeof v === "string" && v.length ? v : fallback;
}

/**
 * Static provider catalogue. Only NAMES of credentials appear here; the value
 * is resolved from env at call time. A provider is "configured" only if its
 * secretRef resolves to a non-empty value (ollama may legitimately have none).
 */
export function providerRegistryFromEnv(env) {
  return [
    {
      providerId: "openai", providerType: "openai",
      baseUrl: envStr(env, "JEV_AI_OPENAI_BASE_URL", DEFAULT_BASE_URLS.openai),
      modelId: envStr(env, "JEV_AI_OPENAI_MODEL", "gpt-4o-mini"),
      secretRef: "OPENAI_API_KEY", scope: "cloud",
    },
    {
      providerId: "minimax", providerType: "openai-compatible",
      baseUrl: envStr(env, "JEV_AI_MINIMAX_BASE_URL", "https://api.minimaxi.com/v1"),
      modelId: envStr(env, "JEV_AI_MINIMAX_MODEL", "MiniMax-M3"),
      secretRef: "MINIMAX_API_KEY", scope: "cloud",
    },
    {
      providerId: "qwen", providerType: "openai-compatible",
      baseUrl: envStr(env, "JEV_AI_QWEN_BASE_URL", ""),
      modelId: envStr(env, "JEV_AI_QWEN_MODEL", "qwen3-coder-next"),
      secretRef: "DASHSCOPE_API_KEY", scope: "cloud",
    },
    {
      providerId: "deepseek", providerType: "openai-compatible",
      baseUrl: envStr(env, "JEV_AI_DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
      modelId: envStr(env, "JEV_AI_DEEPSEEK_MODEL", "deepseek-chat"),
      secretRef: "DEEPSEEK_API_KEY", scope: "cloud",
    },
    {
      providerId: "openrouter", providerType: "openai-compatible",
      baseUrl: envStr(env, "JEV_AI_OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
      modelId: envStr(env, "JEV_AI_OPENROUTER_MODEL", "openai/gpt-4o-mini"),
      secretRef: "OPENROUTER_API_KEY", scope: "cloud",
    },
    {
      providerId: "ollama", providerType: "ollama",
      baseUrl: envStr(env, "JEV_AI_OLLAMA_BASE_URL", DEFAULT_BASE_URLS.ollama),
      modelId: envStr(env, "JEV_AI_OLLAMA_MODEL", "llama3.1"),
      secretRef: "OLLAMA_API_KEY", scope: "local", optionalSecret: true,
    },
  ];
}

export function resolveSecret(provider, env) {
  const ref = provider && provider.secretRef ? String(provider.secretRef) : "";
  const raw = ref && env ? env[ref] : undefined;
  const value = typeof raw === "string" ? raw : "";
  return { present: value.length > 0, value, ref };
}

/** Status projection. "configured" is a credential fact, never a connectivity claim. */
export function listProviders(env) {
  const providers = providerRegistryFromEnv(env).map((raw) => {
    const secret = resolveSecret(raw, env);
    const localOnly = !secret.present && raw.optionalSecret === true;
    const configured = secret.present || localOnly;
    let normalized = null;
    let invalid = null;
    try { normalized = normalizeProvider(raw); } catch (err) { invalid = err.code || "PROVIDER_INVALID"; }
    return {
      providerId: raw.providerId,
      providerType: raw.providerType,
      baseUrl: raw.baseUrl || null,
      modelId: raw.modelId || null,
      secretRef: raw.secretRef,
      scope: raw.scope,
      configured,
      credential_present: secret.present,
      last4: secret.present ? describeSecret(secret.value).last4 : null,
      status: invalid ? "NOT_CONFIGURED" : secret.present ? "CONFIGURED" : localOnly ? "CONFIGURED_LOCAL_ONLY" : "NOT_CONNECTED",
      reason: invalid || (configured ? null : "SECRET_REF_UNRESOLVED"),
      capabilities: normalized ? normalized.capabilities : { chat: false, models: false, requiresApiKey: true, jsonMode: false },
      connectivity: "UNVERIFIED",
      note: "CONFIGURED only means a credential reference resolved. It is NOT a connectivity claim; call POST /api/jev/ai/providers/test.",
    };
  });
  const credentials = providers.filter((p) => p.credential_present).length;
  const anyConfigured = providers.some((p) => p.configured);
  return {
    verdict: credentials > 0 ? "UNVERIFIED" : "BLOCKED",
    code: credentials > 0 ? "PROVIDER_CONFIGURED_UNVERIFIED" : "PROVIDER_NOT_CONFIGURED",
    credentials,
    providers_configured: anyConfigured ? providers.filter((p) => p.configured).length : 0,
    verified_connected: 0,
    lastTestedAt: null,
    providers,
  };
}

export function findProvider(env, providerId) {
  return providerRegistryFromEnv(env).find((p) => p.providerId === providerId) || null;
}

/**
 * Real minimal inference. BLOCKED (not CONNECTED, not FAILED) when no
 * credential is resolvable - a missing secret is a configuration state, not a
 * provider outage, and must never be dressed up as either.
 */
export async function testProvider({ env, providerId, fetchImpl, nowMs }) {
  const raw = findProvider(env, providerId);
  if (!raw) return { ok: false, status: "BLOCKED", code: "PROVIDER_UNKNOWN", providerId, evidence: null };
  const secret = resolveSecret(raw, env);
  if (!secret.present && raw.optionalSecret !== true) {
    return {
      ok: false, status: "BLOCKED", code: "SECRET_REF_UNRESOLVED", providerId,
      secretRef: raw.secretRef,
      evidence: { reason: "No credential is stored for this secretRef on this runtime. Nothing was sent.", http_status: null },
    };
  }
  let spec;
  try {
    spec = buildRequest(raw, [{ role: "user", content: 'Reply with the JSON object {"ok":true} and nothing else.' }], {
      credential: secret.value, maxTokens: 8, jsonMode: false, temperature: 0,
    });
  } catch (err) {
    return { ok: false, status: "FAILED", code: err.code || "PROVIDER_INVALID", providerId, evidence: { reason: String(err.message || err) } };
  }
  const started = nowMs == null ? Date.now() : nowMs;
  const res = await performRequest(spec, { fetchImpl });
  const verdict = classifyResponse(raw, res.httpStatus, res.body, res.elapsedMs, {
    networkError: res.networkError, timedOut: res.timedOut, rawChars: res.rawChars,
  });
  const evidence = {
    ...describeRequest(spec),
    ...verdict.evidence,
    started_at: new Date(started).toISOString(),
    response_sha256: await sha256Hex(JSON.stringify(res.body === null ? { raw: res.rawText } : { keys: Object.keys(res.body) })),
  };
  if (!verdict.ok) {
    return { ok: false, status: "FAILED", code: verdict.code, providerId, retryable: verdict.retryable, evidence };
  }
  return { ok: true, status: "CONNECTED", code: null, providerId, model: spec.model, evidence: { ...evidence, finish_reason: verdict.finish_reason, usage: verdict.usage } };
}

/** Persist the verified mark snapshot a decision refers to. */
export async function saveMarketContext(db, context, nowMs) {
  const at = new Date(nowMs == null ? Date.now() : nowMs).toISOString();
  const markPriceFp = Math.round(Number(context.markPriceUsd) * 1e8);
  await db.prepare(
    "INSERT OR IGNORE INTO ai_market_contexts (market_context_id, symbol, mark_price, price_ts, mark_quality, source, source_sha256, expires_at, created_at) VALUES (?,?,?,?,?,?,?,?,?)"
  ).bind(
    context.marketContextId, String(context.symbol).toUpperCase(), markPriceFp, String(context.priceTs),
    context.markQuality === "verified" ? "verified" : "unknown", String(context.source || "unknown"),
    String(context.sourceSha256 || ""), context.expiresAt || null, at
  ).run();
  return loadMarketContext(db, context.marketContextId);
}

export function buildDecisionPrompt({ strategyId, symbol, context }) {
  const system = [
    "You are the ruling agent of a PAPER-ONLY trading desk. Real money is never involved.",
    "Return EXACTLY one JSON object, no prose, no markdown fences, matching this contract:",
    '{"schemaVersion":"' + DECISION_SCHEMA_VERSION + '","strategyId":string,"symbol":string,"action":"HOLD|BUY|SELL",',
    '"side":"BUY|SELL|null","orderType":"MARKET|LIMIT|null","quantity":number|null,"limitPrice":number|null,',
    '"confidence":number,"reason":string,"riskFlags":string[],"marketContextId":string,"validForSeconds":integer}',
    "If action is HOLD then side, orderType, quantity and limitPrice MUST be null.",
    "Your confidence is advisory only; a deterministic server gate decides execution.",
  ].join("\n");
  const user = [
    "strategyId=" + strategyId,
    "symbol=" + symbol,
    "marketContextId=" + context.marketContextId,
    "markPriceUsd=" + Number(context.markPriceUsd),
    "priceTs=" + context.priceTs,
    "maxNotionalUsd=" + MAX_DECISION_BODY_USD,
  ].join("\n");
  return [{ role: "system", content: system }, { role: "user", content: user }];
}

function stripFences(text) {
  const t = String(text || "").trim();
  const m = /^```(?:json)?\s*([\s\S]*?)\s*```$/i.exec(t);
  return m ? m[1] : t;
}

function parseDecisionContent(content) {
  const raw = stripFences(content);
  try { return { parsed: JSON.parse(raw), error: null }; }
  catch (err) { return { parsed: null, error: String(err && err.message ? err.message : err) }; }
}

/**
 * Create, validate, risk-check and persist one decision.
 *
 * marketContext must come from a REAL market read (the gateway builds it from
 * fetchVerified); this function will not invent a price. `mock` mode uses the
 * deterministic mock provider but still goes through schema + risk gate + D1.
 */
export async function createDecision({ db, env, strategyId, symbol, providerId, dataMode, marketContext, fetchImpl, nowMs, runId, seedExtra }) {
  const mode = dataMode === "real" ? "real" : dataMode === "mock" ? "mock" : null;
  if (!mode) throw runtimeError("DATA_MODE_INVALID", "dataMode must be 'mock' or 'real'");
  const sym = String(symbol || "").toUpperCase();
  if (!/^[A-Z0-9]{5,20}$/.test(sym)) throw runtimeError("SYMBOL_INVALID", "symbol is invalid");
  if (!strategyId || !/^[A-Za-z0-9._:-]{3,100}$/.test(String(strategyId))) throw runtimeError("STRATEGY_INVALID", "strategyId is invalid");
  if (!marketContext || marketContext.symbol !== sym) throw runtimeError("MARKET_CONTEXT_INVALID", "market context does not match the symbol");

  const ctxRow = await saveMarketContext(db, marketContext, nowMs);
  if (!ctxRow) throw runtimeError("MARKET_CONTEXT_INVALID", "market context could not be stored");

  const clk = nowMs == null ? Date.now() : nowMs;
  let responseBody;
  let modelId;
  let resolvedProviderId;
  let requestHash = null;

  if (mode === "real") {
    const raw = findProvider(env, providerId);
    if (!raw) throw runtimeError("PROVIDER_UNKNOWN", "unknown providerId: " + providerId);
    const secret = resolveSecret(raw, env);
    if (!secret.present && raw.optionalSecret !== true) {
      throw runtimeError("PROVIDER_BLOCKED", "No credential for secretRef=" + raw.secretRef + ". A real decision cannot be produced.");
    }
    const spec = buildRequest(raw, buildDecisionPrompt({ strategyId, symbol: sym, context: marketContext }), {
      credential: secret.value, temperature: 0.2,
    });
    requestHash = await sha256Hex(canonicalize(describeRequest(spec)));
    const res = await performRequest(spec, { fetchImpl });
    const verdict = classifyResponse(raw, res.httpStatus, res.body, res.elapsedMs, {
      networkError: res.networkError, timedOut: res.timedOut, rawChars: res.rawChars,
    });
    if (!verdict.ok) {
      const e = runtimeError(verdict.code, "provider call failed: " + verdict.code);
      e.evidence = { ...verdict.evidence, request_sha256: requestHash };
      throw e;
    }
    responseBody = res.body;
    modelId = spec.model;
    resolvedProviderId = raw.providerId;
  } else {
    if (providerId && providerId !== MOCK_PROVIDER_ID) throw runtimeError("DATA_MODE_MISMATCH", "mock mode cannot use provider " + providerId);
    const mock = await generateMockDecision({ strategyId, context: { ...marketContext, symbol: sym }, seedExtra });
    responseBody = mockChatCompletion(mock.decision);
    modelId = MOCK_MODEL_ID;
    resolvedProviderId = MOCK_PROVIDER_ID;
    requestHash = await sha256Hex(canonicalize({ mode: "mock", strategyId, symbol: sym, context: marketContext.marketContextId }));
  }

  const content = responseBody && responseBody.choices && responseBody.choices[0] && responseBody.choices[0].message
    ? responseBody.choices[0].message.content : "";
  const parsed = parseDecisionContent(content);
  if (parsed.error || !parsed.parsed) {
    const e = runtimeError(ERROR_CODES.INVALID_RESPONSE, "model output is not valid JSON");
    e.evidence = { parse_error: parsed.error, content_chars: String(content).length };
    throw e;
  }
  const check = validateDecision(parsed.parsed);
  if (!check.valid) {
    const e = runtimeError("DECISION_INVALID", "model output failed the decision contract");
    e.evidence = { errors: check.errors };
    throw e;
  }

  const decisionId = "dec-" + (await sha256Hex([strategyId, sym, marketContext.marketContextId, runId || "", mode].join("|"))).slice(0, 32);
  const row = toDecisionRow(check.decision, {
    decisionId,
    providerId: resolvedProviderId,
    modelId,
    runId: runId || null,
    promptVersion: mode === "mock" ? "jev.ai.prompt.mock/v1" : PROMPT_VERSION,
    inputSnapshotSha256: await sha256Hex(canonicalize({ strategyId, symbol: sym, context: {
      marketContextId: marketContext.marketContextId, markPriceUsd: marketContext.markPriceUsd,
      priceTs: marketContext.priceTs, source: marketContext.source, sourceSha256: marketContext.sourceSha256,
    } })),
    requestPayloadSha256: requestHash,
    responsePayloadSha256: await sha256Hex(canonicalize({ content, model: modelId, mode })),
    priceTs: marketContext.priceTs,
    dataMode: mode,
    nowMs: clk,
  });

  const inserted = await insertDecision(db, row);
  if (!inserted.inserted) {
    const existing = await getDecision(db, decisionId);
    return { ok: true, duplicate: true, decision: existing, risk: { approved: existing.riskCheckResult === "PASS", code: existing.errorCode, reason: "duplicate decision_id - not re-evaluated" } };
  }

  const contextRow = await loadMarketContext(db, check.decision.marketContextId);
  const risk = riskCheck({ decision: check.decision, context: contextRow, nowMs: clk });
  const stored = await recordRiskVerdict(db, decisionId, risk);
  return { ok: true, duplicate: false, decision: stored, risk, dataMode: mode };
}

/**
 * Execute an APPROVED decision through the frozen paper engine.
 * `submitOrder(payload)` must return {httpStatus, body} from the engine handler.
 * Idempotent by decision_id: a decision that already has paper_order_id never
 * reaches the engine a second time.
 */
export async function executeApprovedDecision({ db, decisionId, submitOrder, confirmationId, clientOrderId, nowMs }) {
  if (!db || !decisionId) throw runtimeError("DECISION_INVALID", "decisionId is required");
  const decision = await getDecision(db, decisionId);
  if (!decision) return { ok: false, code: "DECISION_NOT_FOUND", httpStatus: 404 };
  if (decision.status === "SUBMITTED" || decision.paperOrderId) {
    return {
      ok: true, duplicate: true, decisionId,
      orderId: decision.paperOrderId,
      note: "decision_id already produced an order; the engine was not called again",
    };
  }
  if (decision.action === "HOLD") return { ok: false, code: "DECISION_IS_HOLD", httpStatus: 409 };
  if (decision.riskCheckResult !== "PASS" || decision.status !== "APPROVED") {
    return {
      ok: false, code: "EXECUTE_NOT_APPROVED", httpStatus: 409,
      riskCheckResult: decision.riskCheckResult, status: decision.status, errorCode: decision.errorCode,
      note: "risk gate did not approve this decision; no AI output can override that",
    };
  }

  const cid = String(clientOrderId || ("ai-" + decisionId));
  if (!/^[a-zA-Z0-9._:-]{8,100}$/.test(cid)) return { ok: false, code: "CLIENT_ORDER_ID_INVALID", httpStatus: 400 };
  const derivedConfirmation = confirmationId == null || confirmationId === "";
  const conf = derivedConfirmation ? "confirm-" + (await sha256Hex("confirmation|" + decisionId)).slice(0, 24) : String(confirmationId);
  if (!/^[a-zA-Z0-9._:-]{8,100}$/.test(conf)) return { ok: false, code: "CONFIRMATION_ID_INVALID", httpStatus: 400 };

  const payload = {
    clientOrderId: cid,
    confirmationId: conf,
    symbol: decision.symbol,
    side: decision.side,
    type: decision.orderType,
    quantity: decision.quantity,
    ...(decision.orderType === "LIMIT" ? { limitPrice: decision.limitPrice } : {}),
  };
  let result;
  try {
    result = await submitOrder(payload);
  } catch (err) {
    // Transport failure is NOT a business rejection: leave the decision APPROVED
    // so an operator can retry once the engine is reachable again.
    return { ok: false, code: "ENGINE_CALL_FAILED", httpStatus: 502, retryable: true, detail: String(err && err.message ? err.message : err) };
  }
  const body = (result && result.body) || {};
  const accepted = body.ok !== false && body.accepted !== false;
  const orderId = body.orderId || (body.order && body.order.id) || null;
  const fillIds = Array.isArray(body.fills) ? body.fills.map((f) => f.fillId || f.id).filter(Boolean) : [];
  if (!accepted || !orderId) {
    const engineStatus = (result && result.httpStatus) || 409;
    const code = (body.rejection && body.rejection.code) || body.code || "ENGINE_REJECTED";
    // 401/403 means the caller has no valid paper session: nothing was decided
    // about the order, so the decision stays APPROVED and remains retryable.
    if (engineStatus === 401 || engineStatus === 403) {
      return { ok: false, code: String(code), httpStatus: engineStatus, retryable: true, note: "paper session required; decision remains APPROVED" };
    }
    await recordSubmission(db, decisionId, { status: "FAILED", errorCode: String(code) });
    return { ok: false, code: "ENGINE_REJECTED", engine_code: String(code), httpStatus: engineStatus, engine: body };
  }
  await linkOrder(db, decisionId, orderId, nowMs);
  const stored = await recordSubmission(db, decisionId, { status: "SUBMITTED", orderId, fillIds });
  return {
    ok: true, duplicate: false, decisionId, orderId, fillIds,
    confirmationDerived: derivedConfirmation,
    status: stored.status, engine_http_status: result.httpStatus,
  };
}

export async function readRun(db, runId, limit) {
  const rows = await listRunDecisions(db, String(runId), limit);
  return {
    runId: String(runId),
    count: rows.length,
    status: rows.length === 0 ? "NO_DATA" : "RECORDED",
    decisions: rows,
  };
}

export async function readPerformance(db, options) {
  return performanceSummary(db, options);
}

export async function readStrategies(db) {
  try {
    const res = await db.prepare(
      "SELECT strategy_id, version, name, family, lifecycle, frozen, created_at FROM strategy_versions ORDER BY created_at DESC LIMIT 200"
    ).bind().all();
    const rows = res.results || [];
    return { status: rows.length ? "RECORDED" : "NO_DATA", count: rows.length, strategies: rows };
  } catch (err) {
    return { status: "NO_DATA", count: 0, strategies: [], error: "STRATEGY_TABLE_UNAVAILABLE", detail: String(err && err.message ? err.message : err) };
  }
}

export { RISK_ALLOWED_SYMBOLS, RISK_CODES, ERROR_CODES, DECISION_SCHEMA_VERSION };
