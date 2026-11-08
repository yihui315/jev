/**
 * trading/services/marketData — honest market-data access.
 *
 * Night Researcher does NOT expose a candles/orderbook proxy. Its paper engine
 * itself reads from the public Binance data API (`data-api.binance.vision`).
 * We use that SAME authoritative source directly and label it in the UI — no
 * fabricated feeds, no fake fund flows.
 *
 * The base URL is configurable via `VITE_MARKET_BASE` (defaults to the public
 * endpoint the engine uses). It is a public, CORS-enabled, no-auth API.
 */
import type { Candle, Interval, OrderBook, OrderBookLevel, Symbol, Ticker } from "./types";

const MARKET_BASE =
  (import.meta.env.VITE_MARKET_BASE as string | undefined)?.replace(/\/+$/, "") ||
  "https://data-api.binance.vision/api/v3";

function asSymbol(value: string): Symbol {
  const v = value as Symbol;
  if (v === "BTCUSDT" || v === "ETHUSDT" || v === "ORDIUSDT" || v === "1000SATSUSDT") return v;
  // Best-effort coercion for other upstream symbols; UI restricts to the allow-list.
  return v as Symbol;
}

async function getJson(url: string): Promise<unknown> {
  const res = await fetch(url, { cache: "no-store", headers: { accept: "application/json" } });
  if (!res.ok) throw new Error(`market-data ${res.status} @ ${url}`);
  return res.json();
}

/** Latest prices for the given symbols. */
export async function fetchTicker(symbols: Symbol[]): Promise<Ticker[]> {
  const url =
    `${MARKET_BASE}/ticker/price?symbols=` +
    encodeURIComponent(JSON.stringify(symbols));
  const data = (await getJson(url)) as Array<{ symbol: string; price: string }>;
  return data.map((d) => ({ symbol: asSymbol(d.symbol), price: Number(d.price) }));
}

/** K-line candles (OHLCV). `time` is converted to UNIX seconds for charting. */
export async function fetchCandles(
  symbol: Symbol,
  interval: Interval,
  limit = 500,
): Promise<Candle[]> {
  const url =
    `${MARKET_BASE}/klines?symbol=${symbol}&interval=${interval}&limit=${String(limit)}`;
  const rows = (await getJson(url)) as unknown[][];
  return rows.map((r) => ({
    time: Math.floor(Number(r[0]) / 1000),
    open: Number(r[1]),
    high: Number(r[2]),
    low: Number(r[3]),
    close: Number(r[4]),
    volume: Number(r[5]),
  }));
}

function buildLevels(raw: [string, string][], descending: boolean): OrderBookLevel[] {
  const parsed = raw
    .map(([p, q]) => ({ price: Number(p), qty: Number(q) }))
    .filter((l) => l.price > 0 && l.qty > 0)
    .sort((a, b) => (descending ? b.price - a.price : a.price - b.price));
  let cumulative = 0;
  return parsed.map((l) => {
    cumulative += l.qty;
    return { price: l.price, qty: l.qty, total: cumulative };
  });
}

/** Order-book depth. Cumulative `total` is computed client-side for the depth UI. */
export async function fetchOrderBook(symbol: Symbol, limit = 200): Promise<OrderBook> {
  const url = `${MARKET_BASE}/depth?symbol=${symbol}&limit=${String(limit)}`;
  const data = (await getJson(url)) as {
    lastUpdateId: number;
    bids: [string, string][];
    asks: [string, string][];
  };
  return {
    venue: "binance-spot",
    symbol,
    lastUpdateId: data.lastUpdateId,
    bids: buildLevels(data.bids, true),
    asks: buildLevels(data.asks, false),
    source: "Binance public REST depth snapshot",
    receivedAt: new Date().toISOString(),
  };
}

export const MARKET_DATA_SOURCE = MARKET_BASE;
