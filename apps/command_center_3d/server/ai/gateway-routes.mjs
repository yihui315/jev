// JEV V1.6 Track B - HTTP mapping for /api/jev/ai/*.
//
// WHY A SEPARATE MODULE: the gateway functions/api/jev/[[path]].js documents the
// ENABLE_REAL_TRADING red line at a specific line number; keeping the AI routes
// out of that file (only a lazy import hook is added after the red lines) keeps
// the earlier bytes and line numbers untouched.
//
// Everything here runs AFTER the red-line, credential and Basic-auth checks in
// onRequest. No market egress host is added: the market context still comes
// from the gateway's existing fetchVerified() whitelist.

import { listProviders, testProvider, createDecision, executeApprovedDecision, readRun, readPerformance, readStrategies } from "./runtime.mjs";
import { riskCheckWithDb } from "./risk-gate.mjs";
import { getDecision } from "./decision-store.mjs";

function safeJson(text) {
  if (!text) return {};
  try { const v = JSON.parse(text); return v && typeof v === "object" ? v : null; } catch { return null; }
}

function viewToDecision(view) {
  return {
    schemaVersion: "jev.ai.decision/v1",
    strategyId: view.strategyId, symbol: view.symbol, action: view.action,
    side: view.side, orderType: view.orderType, quantity: view.quantity,
    limitPrice: view.limitPrice, confidence: view.confidence, reason: view.reason,
    riskFlags: view.riskFlags || [], marketContextId: view.marketContextId,
    validForSeconds: 60,
  };
}

async function aiMarketContext(ctx, symbol) {
  const sym = String(symbol || "").toUpperCase();
  if (!ctx.marketSupportedSymbols.includes(sym)) {
    return { error: "SYMBOL_NOT_SUPPORTED_BY_MARKET_SOURCE", symbol: sym, supported: ctx.marketSupportedSymbols.slice() };
  }
  const { payload, meta } = await ctx.fetchVerified(`${ctx.marketBase}/ticker/price?symbol=${encodeURIComponent(sym)}`);
  const price = Number(payload && payload.price);
  if (!Number.isFinite(price) || price <= 0) return { error: "MARKET_PRICE_INVALID", symbol: sym };
  const priceTs = meta.fetched_at_utc;
  return {
    marketContextId: "mctx-" + (await ctx.sha256Hex(`${sym}|${price}|${priceTs}`)).slice(0, 24),
    symbol: sym, markPriceUsd: price, priceTs,
    markQuality: "verified", source: meta.source, sourceSha256: meta.sha256,
    expiresAt: new Date(Date.parse(priceTs) + 120000).toISOString(),
    latencyMs: meta.latency_ms, httpStatus: meta.http_status, venue: ctx.marketVenue,
  };
}

/**
 * @returns {Promise<Response|null>} null when the path is not an AI route.
 */
export async function handleAiApi(request, env, url, ctx) {
  const pathname = url.pathname;
  // P0-A: HUMAN_PUBLISH_GATE routes. They sit behind the SAME lazy import
  // boundary as the rest of /api/jev/ai/*, i.e. after the red lines and Basic
  // auth in onRequest. A dynamic import keeps non-publish routes from loading
  // the gate module (Cloudflare Workers runtime constraint). The Paper
  // unattended path /api/jev/ai/paper/execute below is deliberately untouched.
  if (pathname.startsWith("/api/jev/ai/publish/")) {
    const { handlePublishApi } = await import("./publish-routes.mjs");
    return handlePublishApi(request, env, url, ctx);
  }
  const { envelope, buildEngineRequest, engine, engineEnv } = ctx;
  const engineRuntime = engineEnv || env;
  const db = env.PAPER_DB;
  let bodyText = "";
  if (request.method === "POST" || request.method === "PUT") bodyText = await request.text();

  if (pathname === "/api/jev/ai/providers" && request.method === "GET") {
    const data = listProviders(env);
    return envelope({
      data,
      meta: { label: data.verdict, channel: "AI_PROVIDER", source: "server-side credential references (names only, never values)" },
    });
  }

  if (pathname === "/api/jev/ai/providers/test" && request.method === "POST") {
    const body = safeJson(bodyText);
    if (!body || !body.providerId) return envelope({ ok: false, error: "providerId required", code: "BAD_REQUEST", status: 400 });
    const result = await testProvider({ env, providerId: String(body.providerId) });
    const status = result.status === "CONNECTED" ? 200 : result.status === "BLOCKED" ? 409 : 502;
    return envelope({
      ok: result.status === "CONNECTED",
      data: result,
      error: result.status === "CONNECTED" ? undefined : (result.evidence && result.evidence.reason) || result.code,
      code: result.code || undefined,
      meta: { label: result.status, channel: "AI_PROVIDER", real_request: result.status !== "BLOCKED", data_mode: "real" },
      status,
    });
  }

  if (pathname === "/api/jev/ai/strategies" && request.method === "GET") {
    const data = await readStrategies(db);
    return envelope({ data, meta: { label: data.status, channel: "STRATEGY_REGISTRY", source: "D1 strategy_versions (read-only)" } });
  }

  if (pathname === "/api/jev/ai/decisions" && request.method === "POST") {
    const body = safeJson(bodyText);
    if (!body) return envelope({ ok: false, error: "invalid JSON body", code: "BAD_REQUEST", status: 400 });
    if (body.dataMode !== "mock" && body.dataMode !== "real") {
      return envelope({ ok: false, error: "dataMode must be explicitly 'mock' or 'real'", code: "DATA_MODE_REQUIRED", status: 400 });
    }
    let market;
    try { market = await aiMarketContext(ctx, body.symbol); }
    catch (err) {
      return envelope({ ok: false, error: String(err.message || err), code: "MARKET_CONTEXT_UNAVAILABLE", meta: { label: "BLOCKED", data_mode: body.dataMode }, status: 502 });
    }
    if (!market || market.error) {
      return envelope({ ok: false, error: market ? market.error : "MARKET_CONTEXT_UNAVAILABLE", code: (market && market.error) || "MARKET_CONTEXT_UNAVAILABLE", data: market, status: 400 });
    }
    try {
      const result = await createDecision({
        db, env, strategyId: body.strategyId, symbol: body.symbol,
        providerId: body.providerId || null, dataMode: body.dataMode,
        marketContext: market, runId: body.runId || null,
      });
      return envelope({
        ok: true,
        data: { decision: result.decision, risk: result.risk, duplicate: result.duplicate, market_context: market, data_mode: result.dataMode },
        meta: {
          label: result.risk && result.risk.approved ? "APPROVED" : "REJECTED",
          channel: "AI_DECISION", data_mode: result.dataMode,
          note: "risk verdict is server-authoritative; confidence is advisory only",
        },
        status: 201,
      });
    } catch (err) {
      const code = err.code || "AI_DECISION_FAILED";
      const status = code === "PROVIDER_BLOCKED" ? 409
        : ["NOT_CONNECTED", "TIMEOUT", "RATE_LIMITED", "SERVER_ERROR", "INVALID_RESPONSE", "MODEL_UNAVAILABLE", "AUTH_FAILED"].includes(code) ? 502
          : 400;
      return envelope({
        ok: false, error: String(err.message || code), code,
        data: err.evidence ? { evidence: err.evidence } : undefined,
        meta: { label: code === "PROVIDER_BLOCKED" ? "BLOCKED" : "FAILED", data_mode: body.dataMode },
        status,
      });
    }
  }

  if (pathname === "/api/jev/ai/risk-check" && request.method === "POST") {
    const body = safeJson(bodyText);
    if (!body) return envelope({ ok: false, error: "invalid JSON body", code: "BAD_REQUEST", status: 400 });
    let decision = body.decision || null;
    if (!decision && body.decisionId) {
      const stored = await getDecision(db, String(body.decisionId));
      if (!stored) return envelope({ ok: false, error: "decision not found", code: "DECISION_NOT_FOUND", status: 404 });
      decision = viewToDecision(stored);
    }
    if (!decision) return envelope({ ok: false, error: "decision or decisionId required", code: "BAD_REQUEST", status: 400 });
    const verdict = await riskCheckWithDb({ db, decision });
    return envelope({ data: verdict, meta: { label: verdict.approved ? "APPROVED" : "REJECTED", channel: "RISK_GATE" } });
  }

  if (pathname === "/api/jev/ai/paper/execute" && request.method === "POST") {
    const body = safeJson(bodyText);
    if (!body || !body.decisionId) return envelope({ ok: false, error: "decisionId required", code: "BAD_REQUEST", status: 400 });
    const submitOrder = async (payload) => {
      const preq = buildEngineRequest(request, "/api/paper/orders", "", JSON.stringify(payload));
      const r = await engine.handlePaperOrders(preq, engineRuntime);
      const engineBody = await r.clone().json().catch(() => null);
      return { httpStatus: r.status, body: engineBody };
    };
    const result = await executeApprovedDecision({
      db, decisionId: String(body.decisionId), submitOrder,
      confirmationId: body.confirmationId, clientOrderId: body.clientOrderId,
    });
    const status = result.httpStatus || (result.ok ? 200 : 409);
    return envelope({
      ok: result.ok === true, data: result,
      error: result.ok ? undefined : (result.code || "EXECUTE_REJECTED"),
      code: result.ok ? undefined : result.code,
      meta: {
        label: result.ok ? (result.duplicate ? "DUPLICATE" : "SUBMITTED") : "REJECTED",
        channel: "SERVER_ENGINE", engine_modified: false,
        note: "engine is called with its own 7-field contract; no engine code was copied",
      },
      status,
    });
  }

  const runMatch = pathname.match(/^\/api\/jev\/ai\/runs\/([^/]+)$/);
  if (runMatch && request.method === "GET") {
    const data = await readRun(db, decodeURIComponent(runMatch[1]), url.searchParams.get("limit"));
    return envelope({ data, meta: { label: data.status, channel: "AI_DECISION", source: "D1 ai_decisions + decision_order_links" } });
  }

  if (pathname === "/api/jev/ai/performance" && request.method === "GET") {
    const data = await readPerformance(db, { strategyId: url.searchParams.get("strategyId") || null });
    return envelope({ data, meta: { label: data.status, channel: "AI_DECISION", source: "D1 ai_decisions + decision_order_links + paper_fills" } });
  }

  return null;
}
