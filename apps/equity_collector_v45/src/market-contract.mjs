/**
 * V1.6 / M2 - Market & price-unit contracts.
 *
 * Every string here is an IDENTITY LABEL, never a hostname. The only host
 * permitted for a REAL quote is the allowlist in market-guard.mjs
 * (api.binance.us). A symbol tagged as Global is used for IDENTIFICATION only
 * and is NEVER used to price an account (it is not listed on the US venue).
 */
export const EXCHANGE_US = "Binance.US";
export const EXCHANGE_GLOBAL = "Binance Global spot";
export const MARKET_SPOT = "spot";
export const MARKET_PERP = "perp";
export const MARKET_UNKNOWN = "UNKNOWN";

export const STATUS_SUPPORTED = "SUPPORTED";
export const STATUS_BLOCKED = "BLOCKED";
export const REASON_NOT_LISTED_US = "NOT_LISTED_ON_BINANCE_US";
export const REASON_GLOBAL_ID_ONLY = "GLOBAL_ONLY_IDENTIFIER_NEVER_QUOTED";

const US_UNIT = "USDT per 1 base_asset unit";

/**
 * Per-symbol contract registry. 1000SATSUSDT carries unit_multiplier=1000 and
 * base_asset="1000SATS": one quoted unit is 1000 SATS, NOT 1 SATS. It belongs
 * to the Global spot book (identification only) and is BLOCKED for quoting.
 */
export const SYMBOL_CONTRACTS = Object.freeze({
  BTCUSDT: Object.freeze({
    exchange: EXCHANGE_US, market: MARKET_SPOT, symbol: "BTCUSDT",
    base_asset: "BTC", quote_asset: "USDT", price_unit: US_UNIT,
    unit_multiplier: 1, status: STATUS_SUPPORTED, reason: null,
  }),
  ETHUSDT: Object.freeze({
    exchange: EXCHANGE_US, market: MARKET_SPOT, symbol: "ETHUSDT",
    base_asset: "ETH", quote_asset: "USDT", price_unit: US_UNIT,
    unit_multiplier: 1, status: STATUS_SUPPORTED, reason: null,
  }),
  ORDIUSDT: Object.freeze({
    exchange: EXCHANGE_US, market: MARKET_SPOT, symbol: "ORDIUSDT",
    base_asset: "ORDI", quote_asset: "USDT", price_unit: US_UNIT,
    unit_multiplier: 1, status: STATUS_BLOCKED, reason: REASON_NOT_LISTED_US,
  }),
  "1000SATSUSDT": Object.freeze({
    exchange: EXCHANGE_GLOBAL, market: MARKET_SPOT, symbol: "1000SATSUSDT",
    base_asset: "1000SATS", quote_asset: "USDT",
    price_unit: "USDT per 1 (1000SATS) unit = 1000 SATS",
    unit_multiplier: 1000, status: STATUS_BLOCKED, reason: REASON_GLOBAL_ID_ONLY,
  }),
  SATSUSDT: Object.freeze({
    exchange: EXCHANGE_GLOBAL, market: MARKET_SPOT, symbol: "SATSUSDT",
    base_asset: "SATS", quote_asset: "USDT", price_unit: "USDT per 1 SATS unit",
    unit_multiplier: 1, status: STATUS_BLOCKED, reason: REASON_GLOBAL_ID_ONLY,
  }),
});

/** Contract for a symbol; unknown symbols get an explicit UNKNOWN/BLOCKED one. */
export function contractFor(symbol) {
  return SYMBOL_CONTRACTS[symbol] || Object.freeze({
    exchange: MARKET_UNKNOWN, market: MARKET_UNKNOWN, symbol: String(symbol),
    base_asset: MARKET_UNKNOWN, quote_asset: MARKET_UNKNOWN,
    price_unit: "UNKNOWN", unit_multiplier: 1, status: STATUS_BLOCKED,
    reason: "NO_SYMBOL_CONTRACT",
  });
}

/** Inverse unit transform: per-1-(1000SATS) quote -> per-1-SATS quote. */
export function toPerBaseUnit(price, unitMultiplier) {
  return Number(price) / Number(unitMultiplier);
}
/** Forward unit transform: per-1-SATS quote -> per-1-(1000SATS) quote. */
export function fromPerBaseUnit(perBase, unitMultiplier) {
  return Number(perBase) * Number(unitMultiplier);
}

/**
 * Build one COMPLETE contract record per symbol (V1.6 M2).
 * `market` is the fetchMarket provenance object; `marks` is the effective price
 * map; `networkContext` comes from market-guard.resolveNetworkContext.
 * Records contain NO BigInt price values, so JSON.stringify is deterministic
 * across identical cycles (V1.6 idempotency depends on this).
 *
 * CONTRACT §2: the result is a symbol-keyed OBJECT, NOT an array. An empty
 * marks map yields `{}` (never `[]`); a priced map yields
 * `{"BTCUSDT": {...}, ...}`. Each value carries the full per-symbol field set.
 * Object.create(null) avoids prototype pollution from an adversarial ticker
 * symbol such as "__proto__"; JSON.stringify still emits a plain JSON object.
 */
export function buildMarkContracts({ market, marks, networkContext }) {
  const m = market || {};
  const src = marks || {};
  const symbols = Object.keys(src).sort();
  const out = Object.create(null);
  for (const symbol of symbols) {
    const c = contractFor(symbol);
    out[symbol] = {
      exchange: c.exchange,
      market: c.market,
      symbol: c.symbol,
      base_asset: c.base_asset,
      quote_asset: c.quote_asset,
      price_unit: c.price_unit,
      unit_multiplier: c.unit_multiplier,
      status: c.status,
      reason: c.reason,
      price_ts: m.priceTs ?? null,
      price_ts_source: m.priceTsSource ?? "UNKNOWN",
      source_host: m.sourceHost ?? "UNKNOWN",
      source_url: m.sourceUrl ?? "UNKNOWN",
      network_context: networkContext ?? "UNKNOWN",
      fetched_at: m.fetchedAt ?? null,
      freshness_ms: m.freshnessMs ?? null,
      freshness_verdict: m.freshnessVerdict ?? "UNKNOWN",
      payload_sha256: m.payloadSha256 ?? null,
      data_quality: m.dataQuality ?? "UNKNOWN",
    };
  }
  return out;
}
