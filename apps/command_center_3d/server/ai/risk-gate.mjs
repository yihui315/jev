// JEV V1.6 Track B - INDEPENDENT server-side risk gate.
//
// The AI opinion is untrusted input. This module is the only authority that can
// turn a decision into an executable intent, and it never reads
// `decision.confidence`: a model that says 0.99 is treated exactly like one
// that says 0.01. Rejections produced here cannot be overridden by any AI
// output, prompt, or retry.
//
// It also never trusts the decision's own view of the market: the price must
// come from a stored market context row whose mark_quality is 'verified'
// (i.e. produced by a real outbound market fetch, not by the model).

import { validateDecision } from "./decision-schema.mjs";

// Cloud-side tradable whitelist. Deliberately narrower than the frozen engine
// (which accepts ORDIUSDT/1000SATSUSDT): the reachable venue only lists these
// two, so an AI intent in anything else is rejected here, before the engine.
export const RISK_ALLOWED_SYMBOLS = Object.freeze(["BTCUSDT", "ETHUSDT"]);

export const DEFAULT_RISK_LIMITS = Object.freeze({
  minNotionalUsd: 5,
  maxNotionalUsd: 1000,
  maxQuantity: 10,
  maxContextAgeMs: 60000,
});

export const RISK_CODES = Object.freeze({
  APPROVED: "RISK_APPROVED",
  HOLD_NO_ORDER: "RISK_HOLD_NO_ORDER",
  INVALID_DECISION: "RISK_INVALID_DECISION",
  SYMBOL_NOT_ALLOWED: "RISK_SYMBOL_NOT_ALLOWED",
  SYMBOL_MISMATCH: "RISK_SYMBOL_MISMATCH",
  CONTEXT_MISSING: "RISK_CONTEXT_MISSING",
  CONTEXT_NOT_VERIFIED: "RISK_CONTEXT_NOT_VERIFIED",
  CONTEXT_INVALID_TS: "RISK_CONTEXT_INVALID_TS",
  CONTEXT_STALE: "RISK_CONTEXT_STALE",
  CONTEXT_EXPIRED: "RISK_CONTEXT_EXPIRED",
  NOTIONAL_TOO_SMALL: "RISK_NOTIONAL_TOO_SMALL",
  LIMIT_EXCEEDED: "RISK_LIMIT_EXCEEDED",
  PRICE_UNAVAILABLE: "RISK_PRICE_UNAVAILABLE",
});

const MAX_FUTURE_SKEW_MS = 5000;

function reject(code, reason, extra = {}) {
  return { approved: false, code, reason, ...extra };
}

/** Read a verified market context row by id (D1-shaped db). */
export async function loadMarketContext(db, marketContextId) {
  if (!db || typeof marketContextId !== "string" || !marketContextId) return null;
  const row = await db.prepare(
    "SELECT market_context_id, symbol, mark_price, price_ts, mark_quality, source, source_sha256, expires_at FROM ai_market_contexts WHERE market_context_id = ?"
  ).bind(marketContextId).first();
  return row || null;
}

export function marketContextPriceUsd(row) {
  if (!row) return null;
  const scaled = Number(row.mark_price);
  if (!Number.isFinite(scaled) || scaled <= 0) return null;
  return scaled / 1e8;
}

/**
 * Accept either the DB row shape (snake_case, 1e8 integer price) or the
 * in-process context shape (camelCase, USD float). This is the module boundary
 * between pure evaluation and D1, so both are normalized once, here.
 */
function normalizeContext(input) {
  if (!input || typeof input !== "object") return null;
  const dbPrice = input.mark_price == null ? null : Number(input.mark_price) / 1e8;
  const usdPrice = input.markPriceUsd == null ? null : Number(input.markPriceUsd);
  const price = Number.isFinite(dbPrice) && dbPrice > 0 ? dbPrice : (Number.isFinite(usdPrice) ? usdPrice : null);
  return {
    market_context_id: input.market_context_id || input.marketContextId || null,
    symbol: input.symbol == null ? null : String(input.symbol).toUpperCase(),
    price,
    price_ts: input.price_ts || input.priceTs || null,
    mark_quality: input.mark_quality || input.markQuality || null,
    expires_at: input.expires_at || input.expiresAt || null,
  };
}

/**
 * Pure decision + context evaluation.
 *   decision: raw or already-normalized decision object
 *   context:  ai_market_contexts row (or null)
 *   nowMs:    injectable clock (tests use it to prove staleness rejection)
 */
export function riskCheck({ decision, context, nowMs = Date.now(), limits = {} }) {
  const cfg = { ...DEFAULT_RISK_LIMITS, ...limits };

  const schema = validateDecision(decision);
  if (!schema.valid) {
    return reject(RISK_CODES.INVALID_DECISION, "decision failed schema validation", { errors: schema.errors });
  }
  const d = schema.decision;

  if (d.action === "HOLD") {
    return {
      approved: true, code: RISK_CODES.HOLD_NO_ORDER,
      reason: "HOLD carries no order; nothing to execute",
      confidence_ignored: d.confidence, checks: [{ name: "schema", pass: true }],
    };
  }

  if (!RISK_ALLOWED_SYMBOLS.includes(d.symbol)) {
    return reject(RISK_CODES.SYMBOL_NOT_ALLOWED, "symbol is not in the cloud tradable whitelist", { symbol: d.symbol, allowed: RISK_ALLOWED_SYMBOLS.slice(), confidence_ignored: d.confidence });
  }
  const ctx = normalizeContext(context);
  if (!ctx) {
    return reject(RISK_CODES.CONTEXT_MISSING, "market context not found", { marketContextId: d.marketContextId });
  }
  if (String(ctx.mark_quality || "") !== "verified") {
    return reject(RISK_CODES.CONTEXT_NOT_VERIFIED, "market context mark_quality is not verified", { mark_quality: ctx.mark_quality ?? null });
  }
  if (String(ctx.symbol || "").toUpperCase() !== d.symbol) {
    return reject(RISK_CODES.SYMBOL_MISMATCH, "market context symbol does not match the decision", { context_symbol: ctx.symbol, symbol: d.symbol });
  }

  const priceTsMs = Date.parse(String(ctx.price_ts || ""));
  if (!Number.isFinite(priceTsMs)) {
    return reject(RISK_CODES.CONTEXT_INVALID_TS, "market context price_ts is not a valid ISO timestamp", { price_ts: ctx.price_ts ?? null });
  }
  const ageMs = nowMs - priceTsMs;
  if (ageMs < -MAX_FUTURE_SKEW_MS) {
    return reject(RISK_CODES.CONTEXT_INVALID_TS, "market context price_ts is in the future beyond allowed skew", { skew_ms: -ageMs });
  }
  const allowedAgeMs = Math.min(cfg.maxContextAgeMs, d.validForSeconds * 1000);
  if (ageMs > allowedAgeMs) {
    return reject(RISK_CODES.CONTEXT_STALE, "market context is older than the decision validity window", { age_ms: ageMs, allowed_ms: allowedAgeMs });
  }
  if (ctx.expires_at) {
    const expiresMs = Date.parse(String(ctx.expires_at));
    if (Number.isFinite(expiresMs) && nowMs > expiresMs) {
      return reject(RISK_CODES.CONTEXT_EXPIRED, "market context has expired", { expires_at: ctx.expires_at });
    }
  }

  const markPrice = ctx.price;
  if (!markPrice) return reject(RISK_CODES.PRICE_UNAVAILABLE, "market context has no usable price");

  const referencePrice = d.orderType === "LIMIT" ? d.limitPrice : markPrice;
  const notionalUsd = d.quantity * referencePrice;
  if (!Number.isFinite(notionalUsd) || notionalUsd <= 0) {
    return reject(RISK_CODES.PRICE_UNAVAILABLE, "notional could not be computed");
  }
  if (d.quantity > cfg.maxQuantity) {
    return reject(RISK_CODES.LIMIT_EXCEEDED, "quantity exceeds the server limit", { quantity: d.quantity, max_quantity: cfg.maxQuantity, confidence_ignored: d.confidence });
  }
  if (notionalUsd < cfg.minNotionalUsd) {
    return reject(RISK_CODES.NOTIONAL_TOO_SMALL, "notional below the server minimum", { notional_usd: round2(notionalUsd), min_notional_usd: cfg.minNotionalUsd });
  }
  if (notionalUsd > cfg.maxNotionalUsd) {
    return reject(RISK_CODES.LIMIT_EXCEEDED, "notional exceeds the server limit", { notional_usd: round2(notionalUsd), max_notional_usd: cfg.maxNotionalUsd, confidence_ignored: d.confidence });
  }

  return {
    approved: true, code: RISK_CODES.APPROVED,
    reason: "server risk gate approved",
    confidence_ignored: d.confidence,
    evaluated: {
      symbol: d.symbol, action: d.action, order_type: d.orderType,
      quantity: d.quantity, reference_price_usd: referencePrice,
      notional_usd: round2(notionalUsd), mark_price_usd: markPrice,
      context_age_ms: ageMs, allowed_age_ms: allowedAgeMs,
      market_context_id: d.marketContextId,
    },
  };
}

function round2(n) { return Math.round(n * 100) / 100; }

/** Convenience: load the context then evaluate. */
export async function riskCheckWithDb({ db, decision, nowMs = Date.now(), limits = {} }) {
  const schema = validateDecision(decision);
  const marketContextId = schema.valid ? schema.decision.marketContextId : (decision && decision.marketContextId);
  const context = await loadMarketContext(db, marketContextId);
  return riskCheck({ decision, context, nowMs, limits });
}
