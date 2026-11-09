/**
 * V1.6 / M3 - FAIL-CLOSED market guard.
 *
 * Pure, unit-testable functions. No console.*, no credentials, no network,
 * no wall-clock reads. Error codes are CONTRACT-EXACT (see V16_ARCH_CONTRACT
 * section 3). On any failure the caller downgrades to an UNKNOWN provenance
 * row; it never throws past the cycle and never fabricates a price.
 */
import { SYMBOL_CONTRACTS, EXCHANGE_US } from "./market-contract.mjs";

export const ERROR_CODES = Object.freeze({
  MARKET_IDENTITY_INVALID: "MARKET_IDENTITY_INVALID",
  MARKET_MIXING_REJECTED: "MARKET_MIXING_REJECTED",
  PRICE_TS_SOURCE_UNVERIFIED: "PRICE_TS_SOURCE_UNVERIFIED",
  UNIT_CONTRACT_MISMATCH: "UNIT_CONTRACT_MISMATCH",
  MARKET_STALE_OR_PAYLOAD_UNVERIFIED: "MARKET_STALE_OR_PAYLOAD_UNVERIFIED",
  MARKET_SOURCE_NOT_VERIFIED: "MARKET_SOURCE_NOT_VERIFIED",
  HISTORY_PROVENANCE_UNPROVEN: "HISTORY_PROVENANCE_UNPROVEN",
  // QA2-D1 fix (additive rule 8): an unresolvable network context must NOT
  // produce a VERIFIED row. Without this rule the collector could write
  // data_quality='VERIFIED' with network_context='UNKNOWN' (fail-open).
  MARKET_CONTEXT_UNRESOLVED: "MARKET_CONTEXT_UNRESOLVED",
});

/** Only the US venue is allowlisted. Every other host is rejected. */
export const ALLOWED_HOSTS = new Set(["api.binance.us"]);
/** A market identity is only coherent when its host matches this map. */
export const EXCHANGE_HOST = Object.freeze({ "Binance.US": "api.binance.us" });

const VALID_EXCHANGES = ["Binance.US", "Binance Global spot", "UNKNOWN"];
const VALID_MARKETS = ["spot", "perp", "UNKNOWN"];
const VERIFIED_TS_SOURCES = ["exchange_serverTime", "trade_time", "http_date"];

export const NETWORK_CONTEXT = Object.freeze({
  TEST_SHIM: "local_test_shim",
  EDGE_PROD: "cloudflare_pages_edge_runtime_prod",
  UNKNOWN: "UNKNOWN",
});

/**
 * Deterministic network_context (contract 3.2). No Date.now(), no randomness,
 * no proxy inference: identical inputs MUST give identical output across runs.
 */
export function resolveNetworkContext({ env, fetcherInjected }) {
  const explicit = env && env.MARKET_NETWORK_CONTEXT;
  if (typeof explicit === "string" && explicit.length > 0) return explicit;
  if (fetcherInjected) return NETWORK_CONTEXT.TEST_SHIM;
  return NETWORK_CONTEXT.UNKNOWN;   // -> fail-closed
}

const PASS = () => ({ pass: true, code: null, failureClass: "NONE" });
const FAIL = (code, failureClass) => ({ pass: false, code, failureClass });

/** Rule 8 (QA2-D1) - network context must be resolved and non-UNKNOWN.
 * A snapshot cannot be VERIFIED when the outbound network context that
 * produced its marks is unknown: the provenance chain would be unlinkable. */
export function validateNetworkContext(networkContext) {
  if (typeof networkContext !== "string" || networkContext.length === 0 ||
      networkContext === NETWORK_CONTEXT.UNKNOWN) {
    return FAIL(ERROR_CODES.MARKET_CONTEXT_UNRESOLVED, "UNKNOWN");
  }
  return PASS();
}

/** Rule 1 - exchange/market identity + source_host<->exchange coherence. */
export function validateMarketIdentity({ exchange, market, sourceHost }) {
  if (!exchange || !market) return FAIL(ERROR_CODES.MARKET_IDENTITY_INVALID, "UNKNOWN");
  if (!VALID_EXCHANGES.includes(exchange) || !VALID_MARKETS.includes(market)) {
    return FAIL(ERROR_CODES.MARKET_IDENTITY_INVALID, "UNKNOWN");
  }
  if (exchange === "UNKNOWN" || market === "UNKNOWN") {
    return FAIL(ERROR_CODES.MARKET_IDENTITY_INVALID, "UNKNOWN");
  }
  const expected = EXCHANGE_HOST[exchange];
  if (!expected || sourceHost !== expected) {
    return FAIL(ERROR_CODES.MARKET_IDENTITY_INVALID, "UNKNOWN");
  }
  return PASS();
}

/**
 * V1.6: mark contracts are stored as a symbol-keyed OBJECT (contract 2), but
 * the guard also accepts arrays (unit tests / other callers). Normalize once.
 */
function asContractList(contracts) {
  if (Array.isArray(contracts)) return contracts;
  if (contracts && typeof contracts === "object") return Object.values(contracts);
  return [];
}

/** Distinct (exchange,market) keys present in a set of contract records. */
export function distinctMarketKeys(contracts) {
  const keys = new Set();
  for (const c of asContractList(contracts)) keys.add(String(c.exchange) + "|" + String(c.market));
  return [...keys];
}

/**
 * Rule 2 - two (exchange,market) in one cycle, OR a different market already
 * VERIFIED in the same (session,generation) series -> MARKET_MIXING_REJECTED.
 */
export function validateMarketMixing({ contracts, priorMarketKeys }) {
  const keys = new Set(distinctMarketKeys(contracts));
  for (const k of priorMarketKeys || []) keys.add(k);
  if (keys.size > 1) return FAIL(ERROR_CODES.MARKET_MIXING_REJECTED, "MARKET_MIXING");
  return PASS();
}

/** Rule 3 - received_at / UNKNOWN / missing timestamp source is NOT verified. */
export function validatePriceTsSource(priceTsSource) {
  if (!VERIFIED_TS_SOURCES.includes(priceTsSource)) {
    return FAIL(ERROR_CODES.PRICE_TS_SOURCE_UNVERIFIED, "UNKNOWN");
  }
  return PASS();
}

/** 1000SATS and SATSUSDT must never share one series. */
export function detectSatsSeriesConflict(contracts) {
  const list = asContractList(contracts);
  const has1000 = list.some((c) => c.symbol === "1000SATSUSDT");
  const hasSats = list.some((c) => c.symbol === "SATSUSDT" || c.base_asset === "SATS");
  if (!has1000 || !hasSats) return { conflict: false, identicalMultiplier: false };
  const m1000 = (list.find((c) => c.symbol === "1000SATSUSDT") || {}).unit_multiplier;
  const mSats = (list.find((c) => c.symbol === "SATSUSDT") || {}).unit_multiplier;
  return { conflict: true, identicalMultiplier: m1000 === mSats };
}

/** Rule 4 - unit_multiplier / base_asset / quote_asset vs the symbol contract. */
export function validateUnitContracts(contracts) {
  for (const c of asContractList(contracts)) {
    const ref = SYMBOL_CONTRACTS[c.symbol];
    if (!ref) return FAIL(ERROR_CODES.UNIT_CONTRACT_MISMATCH, "UNIT_MISMATCH");
    if (c.base_asset !== ref.base_asset || c.quote_asset !== ref.quote_asset ||
        c.unit_multiplier !== ref.unit_multiplier) {
      return FAIL(ERROR_CODES.UNIT_CONTRACT_MISMATCH, "UNIT_MISMATCH");
    }
    if (ref.status !== "SUPPORTED") return FAIL(ERROR_CODES.UNIT_CONTRACT_MISMATCH, "UNIT_MISMATCH");
  }
  if (detectSatsSeriesConflict(contracts).conflict) {
    return FAIL(ERROR_CODES.UNIT_CONTRACT_MISMATCH, "UNIT_MISMATCH");
  }
  return PASS();
}

/** Rule 5 - STALE / non-2xx / missing or un-recomputable payload. */
export function validateFreshnessPayload({ freshnessVerdict, httpStatus, payloadSha256, payloadRecomputed }) {
  if (freshnessVerdict === "STALE") {
    return FAIL(ERROR_CODES.MARKET_STALE_OR_PAYLOAD_UNVERIFIED, "STALE");
  }
  const status = Number(httpStatus);
  if (!(status >= 200 && status < 300)) {
    return FAIL(ERROR_CODES.MARKET_STALE_OR_PAYLOAD_UNVERIFIED, "PAYLOAD_UNVERIFIED");
  }
  if (!payloadSha256) {
    return FAIL(ERROR_CODES.MARKET_STALE_OR_PAYLOAD_UNVERIFIED, "PAYLOAD_UNVERIFIED");
  }
  if (payloadRecomputed != null && payloadRecomputed !== payloadSha256) {
    return FAIL(ERROR_CODES.MARKET_STALE_OR_PAYLOAD_UNVERIFIED, "PAYLOAD_UNVERIFIED");
  }
  return PASS();
}

/** Rule 6 - non-allowlist host (incl. Global injection), auto-fallback, last
 *  good price, or a payload that declared a foreign market (test-only fault
 *  injection / cloud E2E negative) -> MARKET_SOURCE_NOT_VERIFIED. */
export function validateSourceHost({ sourceHost, exchange, fallbackUsed, usedLastGoodPrice, autoFallback, foreignMarketDeclared }) {
  if (!ALLOWED_HOSTS.has(sourceHost) || foreignMarketDeclared) {
    return FAIL(ERROR_CODES.MARKET_SOURCE_NOT_VERIFIED, "UNKNOWN");
  }
  if (exchange !== EXCHANGE_US) {
    return FAIL(ERROR_CODES.MARKET_SOURCE_NOT_VERIFIED, "UNKNOWN");
  }
  if (fallbackUsed || usedLastGoodPrice || autoFallback) {
    return FAIL(ERROR_CODES.MARKET_SOURCE_NOT_VERIFIED, "UNKNOWN");
  }
  return PASS();
}

/**
 * Rule 7 - a history row that CLAIMS VERIFIED but whose provenance is
 * unprovable must never participate as VERIFIED (peak exclusion is also
 * enforced structurally by partition.selectHistoryPeak SQL).
 */
export function validateHistoryProvenance(rows) {
  for (const r of rows || []) {
    if (r.data_quality !== "VERIFIED") continue;
    const unproven = r.failure_class === "HISTORY_UNPROVEN" ||
      r.exchange === "UNKNOWN" ||
      r.market === "UNKNOWN" ||
      r.price_ts_source === "received_at" ||
      r.price_ts_source === "UNKNOWN" ||
      r.network_context === "UNKNOWN";
    if (unproven) return FAIL(ERROR_CODES.HISTORY_PROVENANCE_UNPROVEN, "HISTORY_UNPROVEN");
  }
  return PASS();
}

/** Map a market-layer errorCode to a structured failure_class (contract 1.2). */
export function classifyMarketFailure(market) {
  const c = String((market && market.errorCode) || "");
  if (/TypeError|AbortError|TimeoutError|fetch failed|TIME_/.test(c)) return "TRANSPORT";
  if (/HTTP_\d/.test(c)) return "HTTP_ERROR";
  if (/NO_VENUE_QUOTABLE_SYMBOLS|MISSING_OR_UNLISTED_SYMBOL|Invalid symbol/.test(c)) return "VENUE_BUSINESS";
  if (/STALE/.test(c)) return "STALE";
  if (/PAYLOAD|NOTJSON|SHA/i.test(c)) return "PAYLOAD_UNVERIFIED";
  return "UNKNOWN";
}

/**
 * Run all checks in CONTRACT ORDER (1..7, then rule 8 = QA2-D1 fix); the first
 * failing rule decides the errorCode / failureClass. Pure: priorMarketKeys and
 * historyProvenance are supplied by the caller so no DB access happens inside
 * the guard.
 */
export function evaluateCycle(input) {
  const checks = [];
  const run = (name, fn) => checks.push({ name, ...fn() });
  run("market_identity", () => validateMarketIdentity({
    exchange: input.exchange, market: input.market, sourceHost: input.sourceHost,
  }));
  run("network_context", () => validateNetworkContext(input.networkContext));
  run("market_mixing", () => validateMarketMixing({
    contracts: input.contracts, priorMarketKeys: input.priorMarketKeys,
  }));
  run("price_ts_source", () => validatePriceTsSource(input.priceTsSource));
  run("unit_contract", () => validateUnitContracts(input.contracts));
  run("freshness_payload", () => validateFreshnessPayload({
    freshnessVerdict: input.freshnessVerdict, httpStatus: input.httpStatus,
    payloadSha256: input.payloadSha256, payloadRecomputed: input.payloadRecomputed,
  }));
  run("source_host", () => validateSourceHost({
    sourceHost: input.sourceHost, exchange: input.exchange,
    fallbackUsed: input.fallbackUsed, usedLastGoodPrice: input.usedLastGoodPrice,
    autoFallback: input.autoFallback, foreignMarketDeclared: input.foreignMarketDeclared,
  }));
  run("history_provenance", () => validateHistoryProvenance(input.historyProvenance));
  const firstFail = checks.find((c) => !c.pass);
  return {
    ok: !firstFail,
    errorCode: firstFail ? firstFail.code : null,
    failureClass: firstFail ? firstFail.failureClass : "NONE",
    checks,
  };
}
