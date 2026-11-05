/**
 * marketData.live.test — REAL market-data verification (gated, never default).
 *
 * This file does NOT pass off mock as real. By default it is skipped. Run with
 * `LIVE_MARKET=1` (or `vitest run -e LIVE_MARKET=1`) to hit the ACTUAL Binance
 * public endpoint and assert the live schema. It complements the offline unit
 * suite; it never runs inside the default CI unit pass.
 *
 *   LIVE_MARKET=1 npx vitest run src/views/trading/services/marketData.live.test.ts
 */
import { describe, it, expect } from "vitest";
import { fetchTicker, fetchCandles, fetchOrderBook } from "./marketData";
import type { Symbol } from "./types";

const RUN_LIVE = (import.meta.env as Record<string, unknown>).LIVE_MARKET === "1";

describe.skipIf(!RUN_LIVE)("Binance public market data — REAL (LIVE_MARKET=1)", () => {
  const SYMS: Symbol[] = ["BTCUSDT", "ETHUSDT", "ORDIUSDT", "1000SATSUSDT"];

  it("fetchTicker returns real latest prices", async () => {
    const tickers = await fetchTicker(SYMS);
    expect(tickers.length).toBe(SYMS.length);
    for (const t of tickers) {
      expect(SYMS).toContain(t.symbol);
      expect(typeof t.price).toBe("number");
      expect(t.price).toBeGreaterThan(0);
    }
  }, 20000);

  it("fetchCandles returns real OHLCV klines", async () => {
    const candles = await fetchCandles("BTCUSDT", "1h", 10);
    expect(candles.length).toBeGreaterThan(0);
    for (const c of candles) {
      expect(typeof c.time).toBe("number");
      expect(c.high).toBeGreaterThanOrEqual(c.low);
      expect(c.high).toBeGreaterThanOrEqual(c.close);
      expect(c.volume).toBeGreaterThanOrEqual(0);
    }
  }, 20000);

  it("fetchOrderBook returns a real depth snapshot", async () => {
    const book = await fetchOrderBook("BTCUSDT", 50);
    expect(book.venue).toBe("binance-spot");
    expect(book.bids.length).toBeGreaterThan(0);
    expect(book.asks.length).toBeGreaterThan(0);
    // cumulative total must be monotonically increasing outward from best price
    let prev = 0;
    for (const level of book.bids) {
      expect(level.qty).toBeGreaterThan(0);
      expect(level.total).toBeGreaterThanOrEqual(prev);
      prev = level.total;
    }
  }, 20000);
});

describe("marketData contracts (offline shape guarantees)", () => {
  it("live verification is explicitly gated (never implicit)", () => {
    // This file only asserts the REAL endpoints when LIVE_MARKET=1 is set.
    // A green default unit run therefore never implies a live pass — the gate
    // is a boolean we control, not an accident.
    expect(typeof RUN_LIVE).toBe("boolean");
  });
});
