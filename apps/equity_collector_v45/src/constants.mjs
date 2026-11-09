/**
 * V45 Equity Collector — static, auditable constants.
 * No credentials, no tokens, no secrets are ever read or stored here.
 */

export const COLLECTOR_VERSION = "v45-equity-collector/1.0.0";
export const FORMULA_VERSION = "v45-equity-fp/1";

// ---- market source (HARDCODED, fail-closed) --------------------------------
// Verified reachable from the Cloudflare Worker edge in V1.4 (HTTP 200).
// This is Binance.US SPOT — a DIFFERENT venue from the Binance.com global book.
// data-api.binance.vision is FORBIDDEN as a primary source: it was WAF-403 at
// the edge AND it lists ORDIUSDT/1000SATSUSDT which do NOT exist on Binance.US.
export const MARKET_HOST = "api.binance.us";
// ORIGIN ONLY. Full paths live in MARKET_ENDPOINT / TIME_ENDPOINT (both start
// with /api/v3) so `${MARKET_BASE}${MARKET_ENDPOINT}` is a valid REST URL.
export const MARKET_BASE = `https://${MARKET_HOST}`;
export const MARKET_VENUE = "Binance.US spot (official public REST v3)";
export const MARKET_ENDPOINT = "/api/v3/ticker/price";
export const TIME_ENDPOINT = "/api/v3/time";
export const SOURCE = "binance.us-public-ticker-v3";

// Symbols that actually exist on the reachable venue. Verified by a real
// /api/v3/ticker/price probe: ["BTCUSDT","ETHUSDT"] -> 200 with real prices.
// Anything outside this set is treated as "no quote on this venue" => UNKNOWN.
// NEVER fall back to another venue (e.g. data-api.binance.vision) for these.
export const VENUE_SUPPORTED_SYMBOLS = ["BTCUSDT", "ETHUSDT"];
// Documented as NOT listed on Binance.US (real probe returns
// {"code":-1121,"msg":"Invalid symbol."}). Must be honestly marked UNKNOWN.
export const VENUE_UNSUPPORTED_SYMBOLS = ["ORDIUSDT", "1000SATSUSDT"];

export const ALLOWED_ENDPOINTS = new Set([MARKET_ENDPOINT, TIME_ENDPOINT]);
export const FRESH_THRESHOLD_MS = 60_000;     // >60s => STALE
export const FETCH_TIMEOUT_MS = 9_000;
export const USER_AGENT = "JEV-V45-Equity-Collector/1.0 (public REST; read-only)";

// ENABLE_REAL_TRADING must be "false" semantics. The collector refuses to run
// otherwise; it never connects to real trading / real funds.
export const REAL_TRADING_FORCED_FALSE = true;
export const MIN_VERIFIED_SNAPSHOTS = 2;
