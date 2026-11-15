/**
 * QA-T1-1 - every VERIFIED provenance row has all 8 new columns non-empty,
 * values in domain, a self-consistent market_partition_key, source_url host
 * coherent with exchange, and a COMPLETE per-symbol mark contract record.
 */
import {
  openDb, makeEnv, makeState, insertSession, insertAccount,
  controlledFetcher, REAL_BASE_PRICES, scalePrice, runCycle, rows,
} from "../lib/harness.mjs";
import { v16Recorder, NEW_COLS } from "./lib.mjs";

const R = v16Recorder("v16_t1_field_contract", { coverage: "QA-T1-1: 8 new columns on VERIFIED rows" });
const T = 1770000000000;
const db = openDb();
const st = await makeState({
  cash: 100000, initialCash: 100000,
  positions: { BTCUSDT: { qty: 1, avgPrice: 70000 } }, generation: 1,
});
await insertSession(db, "s1", { nowMs: T });
await insertAccount(db, "s1", st.state_json, st.state_hash, { version: 1, nowMs: T });

const factors = [1.0, 1.05, 0.9];
for (let i = 0; i < factors.length; i++) {
  const nowMs = T + i * 60000;
  const price = scalePrice(REAL_BASE_PRICES.BTCUSDT, factors[i]);
  const f = controlledFetcher({ nowMs, serverTime: nowMs, prices: { BTCUSDT: price } });
  const env = makeEnv(db, f, {});
  await runCycle(env, { db, fetcher: f, cycleKey: "t1-c" + i, nowMs });
}

const provs = await rows(db, "SELECT * FROM v45_snapshot_provenance WHERE data_quality='VERIFIED' ORDER BY created_at");
const EXCHANGES = ["Binance.US", "Binance Global spot", "UNKNOWN"];
const MARKETS = ["spot", "perp", "UNKNOWN"];
const TS_SOURCES = ["exchange_serverTime", "trade_time", "http_date", "received_at", "UNKNOWN"];
const FAILURE_CLASSES = ["NONE", "TRANSPORT", "HTTP_ERROR", "VENUE_BUSINESS", "STALE", "MARKET_MIXING", "HISTORY_UNPROVEN", "UNIT_MISMATCH", "PAYLOAD_UNVERIFIED", "UNKNOWN"];
const REQUIRED_CONTRACT_FIELDS = [
  "exchange", "market", "symbol", "base_asset", "quote_asset", "price_unit",
  "unit_multiplier", "price_ts", "price_ts_source", "source_host", "source_url",
  "network_context", "fetched_at", "freshness_ms", "freshness_verdict",
  "payload_sha256", "data_quality",
];

R.check("three_verified_rows", provs.length === 3, { count: provs.length });
R.check("all_8_new_columns_non_empty",
  provs.every((p) => NEW_COLS.every((c) => p[c] !== null && p[c] !== undefined && String(p[c]).length > 0)),
  provs.map((p) => NEW_COLS.filter((c) => p[c] === null || p[c] === undefined || String(p[c]).length === 0)));
R.check("exchange_in_domain_and_is_us",
  provs.every((p) => EXCHANGES.includes(p.exchange) && p.exchange === "Binance.US"),
  provs.map((p) => p.exchange));
R.check("market_in_domain_and_is_spot",
  provs.every((p) => MARKETS.includes(p.market) && p.market === "spot"),
  provs.map((p) => p.market));
R.check("price_ts_source_in_domain_and_verified",
  provs.every((p) => TS_SOURCES.includes(p.price_ts_source) && p.price_ts_source === "exchange_serverTime"),
  provs.map((p) => p.price_ts_source));
R.check("network_context_non_unknown_nonempty",
  provs.every((p) => p.network_context.length > 0 && p.network_context !== "UNKNOWN"),
  provs.map((p) => p.network_context));
R.check("failure_class_none_for_verified",
  provs.every((p) => FAILURE_CLASSES.includes(p.failure_class) && p.failure_class === "NONE"),
  provs.map((p) => p.failure_class));
R.check("venue_nonempty_carries_market_venue",
  provs.every((p) => p.venue.length > 0 && p.venue.includes("Binance.US")),
  provs.map((p) => p.venue));
R.check("market_partition_key_self_consistent",
  provs.every((p) => p.market_partition_key === p.exchange + "|" + p.market),
  provs.map((p) => p.market_partition_key));
R.check("source_url_host_matches_exchange",
  provs.every((p) => {
    try {
      const h = new URL(p.source_url).hostname;
      return p.exchange === "Binance.US" ? h === "api.binance.us" : h !== "api.binance.us";
    } catch { return false; }
  }),
  provs.map((p) => ({ url: p.source_url, exchange: p.exchange })));
R.check("mark_contracts_json_is_symbol_keyed_object_not_array",
  provs.every((p) => {
    let o;
    try { o = JSON.parse(p.mark_contracts_json); } catch { return false; }
    return o !== null && typeof o === "object" && !Array.isArray(o) && Object.keys(o).length > 0;
  }),
  provs.map((p) => p.mark_contracts_json));
R.check("mark_contracts_json_complete_per_symbol",
  provs.every((p) => {
    let o;
    try { o = JSON.parse(p.mark_contracts_json); } catch { return false; }
    if (o === null || typeof o !== "object" || Array.isArray(o)) return false;
    const entries = Object.entries(o);
    if (entries.length === 0) return false;
    // Keys MUST be symbols; each value carries the full contract field set.
    return entries.every(([symbol, c]) =>
      c && typeof c === "object" && c.symbol === symbol &&
      REQUIRED_CONTRACT_FIELDS.every((k) => Object.prototype.hasOwnProperty.call(c, k)));
  }),
  provs.map((p) => p.mark_contracts_json));

R.finish({ verified_rows: provs.length });
