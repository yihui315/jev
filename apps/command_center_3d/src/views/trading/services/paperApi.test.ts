/**
 * paperApi.test.ts — the mock contract and the order-validation gate.
 *
 * These tests pin the behaviour the UI depends on: the mock returns correctly
 * shaped data, and `createPaperOrder` enforces the human-confirmation rule plus
 * the side/type/quantity rules. TAKER proposals MUST NOT bypass confirmation —
 * the client rejects any proposal lacking a confirmationId, exactly as the
 * server would.
 */
import { describe, it, expect } from "vitest";
import { getPaperApi, USE_MOCK } from "./paperApi";
import type { PaperApiError } from "./types";

const api = getPaperApi("");

describe("mock mode is the default", () => {
  it("reports USE_MOCK = true until the backend is merged", () => {
    expect(USE_MOCK).toBe(true);
  });
});

describe("market snapshot (mock shape)", () => {
  it("returns one snapshot per requested symbol with candles", async () => {
    const snaps = await api.getMarketSnapshot({ symbols: ["BTCUSDT", "ETHUSDT", "ORDIUSDT"], interval: "5m" });
    expect(snaps).toHaveLength(3);
    for (const s of snaps) {
      expect(s).toMatchObject({
        symbol: expect.any(String),
        interval: "5m",
        last: expect.any(Number),
        changePct: expect.any(Number),
        open: expect.any(Number),
        high: expect.any(Number),
        low: expect.any(Number),
        volume: expect.any(Number),
        ts: expect.any(Number),
      });
      expect(Array.isArray(s.candles)).toBe(true);
      expect(s.candles.length).toBeGreaterThan(0);
      for (const c of s.candles) {
        expect(c.high).toBeGreaterThanOrEqual(c.low);
        expect(c.high).toBeGreaterThanOrEqual(c.open);
        expect(c.high).toBeGreaterThanOrEqual(c.close);
      }
    }
  });
});

describe("order book (mock shape)", () => {
  it("returns bids and asks with cumulative totals", async () => {
    const book = await api.getOrderBook({ venue: "PAPER-BOOK", symbol: "BTCUSDT" });
    expect(book.symbol).toBe("BTCUSDT");
    expect(book.bids.length).toBeGreaterThan(0);
    expect(book.asks.length).toBeGreaterThan(0);
    // Asks strictly above bids (no crossed book in the mock).
    expect(book.asks[0].price).toBeGreaterThan(book.bids[0].price);
    // Cumulative totals are monotonic non-decreasing.
    for (let i = 1; i < book.bids.length; i++) {
      expect(book.bids[i].total).toBeGreaterThanOrEqual(book.bids[i - 1].total);
    }
  });
});

describe("paper account (server is the authority)", () => {
  it("returns equity, cash, positions and a risk status", async () => {
    const acc = await api.getPaperAccount();
    expect(acc.equity).toBeGreaterThan(0);
    expect(Array.isArray(acc.positions)).toBe(true);
    expect(["ok", "warning", "breach"]).toContain(acc.riskStatus);
    for (const p of acc.positions) {
      // Long PnL = (mark - avg) * qty; short PnL = (avg - mark) * qty.
      const expected = (p.side === "long" ? p.markPrice - p.avgPrice : p.avgPrice - p.markPrice) * p.quantity;
      expect(p.unrealizedPnl).toBeCloseTo(expected, 2);
    }
  });
});

describe("strategies (mock shape)", () => {
  it("returns strategy rows with returns/sharpe/drawdown", async () => {
    const s = await api.listStrategies();
    expect(s.length).toBeGreaterThan(0);
    for (const x of s) {
      expect(x).toMatchObject({
        strategyId: expect.any(String),
        name: expect.any(String),
        version: expect.any(String),
        returns: expect.any(Number),
        sharpe: expect.any(Number),
        maxDrawdown: expect.any(Number),
      });
    }
  });
});

describe("research run (six roles)", () => {
  it("returns a run with exactly the six roles in order", async () => {
    const run = await api.getResearchRun("run_night_8f3a21");
    expect(run.roles).toHaveLength(6);
    expect(run.roles.map((r) => r.role)).toEqual([
      "SPOTTER",
      "PRIOR",
      "EDGE",
      "KELLY",
      "TAKER",
      "CLOSER",
    ]);
    const taker = run.roles.find((r) => r.role === "TAKER");
    expect(taker?.rejectionReason).toBeTruthy();
  });

  it("createResearchRun stamps the caller's symbols/provider/version", async () => {
    const run = await api.createResearchRun({
      providerId: "anthropic",
      symbols: ["ORDIUSDT"],
      snapshotId: "snap_x",
      strategyVersion: "v9.9.9",
    });
    expect(run.providerId).toBe("anthropic");
    expect(run.symbols).toEqual(["ORDIUSDT"]);
    expect(run.strategyVersion).toBe("v9.9.9");
  });
});

describe("events (ledger consistency)", () => {
  it("returns ledger events; after= filters by timestamp", async () => {
    const all = await api.subscribeEvents();
    expect(all.length).toBeGreaterThan(0);
    const cutoff = all[Math.floor(all.length / 2)].ts;
    const later = await api.subscribeEvents({ after: cutoff });
    expect(later.every((e) => e.ts >= cutoff)).toBe(true);
  });
});

describe("createPaperOrder — the human-confirmation gate", () => {
  it("rejects a proposal with no confirmationId (TAKER cannot bypass)", async () => {
    await expect(
      api.createPaperOrder({
        symbol: "BTCUSDT",
        side: "BUY",
        type: "LIMIT",
        quantity: 0.1,
        limitPrice: 60000,
        confirmationId: "",
      }),
    ).rejects.toMatchObject({ code: "MISSING_CONFIRMATION" });
  });

  it("rejects an invalid side", async () => {
    await expect(
      api.createPaperOrder({
        symbol: "BTCUSDT",
        // @ts-expect-error — exercising the runtime guard with a bad value
        side: "HODL",
        type: "LIMIT",
        quantity: 0.1,
        limitPrice: 60000,
        confirmationId: "cfm_x",
      }),
    ).rejects.toMatchObject({ code: "BAD_INPUT" });
  });

  it("rejects a non-positive quantity", async () => {
    await expect(
      api.createPaperOrder({
        symbol: "BTCUSDT",
        side: "BUY",
        type: "LIMIT",
        quantity: 0,
        limitPrice: 60000,
        confirmationId: "cfm_x",
      }),
    ).rejects.toMatchObject({ code: "BAD_INPUT" });
  });

  it("rejects a LIMIT order with no limit price", async () => {
    await expect(
      api.createPaperOrder({
        symbol: "BTCUSDT",
        side: "BUY",
        type: "LIMIT",
        quantity: 0.1,
        confirmationId: "cfm_x",
      }),
    ).rejects.toMatchObject({ code: "BAD_INPUT" });
  });

  it("accepts a valid, confirmed order and returns a filled PaperOrder", async () => {
    const order = await api.createPaperOrder({
      symbol: "ETHUSDT",
      side: "SELL",
      type: "MARKET",
      quantity: 1.5,
      confirmationId: "cfm_op_123",
      researchRunId: "run_night_8f3a21",
    });
    expect(order.orderId).toBeTruthy();
    expect(order.symbol).toBe("ETHUSDT");
    expect(order.side).toBe("SELL");
    expect(order.status).toBe("FILLED");
    expect(order.filledQty).toBe(1.5);
    expect(order.confirmationId).toBe("cfm_op_123");
    expect(order.researchRunId).toBe("run_night_8f3a21");
  });
});

describe("cancelPaperOrder", () => {
  it("acknowledges cancellation in the mock", async () => {
    const res = await api.cancelPaperOrder("ord_eth_1");
    expect(res.ok).toBe(true);
    expect(res.orderId).toBe("ord_eth_1");
  });
});

// Ensure the typed error is the one we expect so future refactors keep the code.
it("throws PaperApiError with a code on bad input", async () => {
  try {
    await api.createPaperOrder({
      symbol: "BTCUSDT",
      side: "BUY",
      type: "LIMIT",
      quantity: -1,
      limitPrice: 1,
      confirmationId: "",
    });
    throw new Error("should have thrown");
  } catch (e) {
    const err = e as PaperApiError;
    expect(err.name).toBe("PaperApiError");
    expect(typeof err.code).toBe("string");
  }
});
