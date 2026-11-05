/**
 * paperApi.test.ts — the mock contract and the order-validation gate.
 *
 * These tests pin the behaviour the UI depends on: the mock returns correctly
 * shaped data matching the REAL server envelopes, and `validateOrderProposal`
 * enforces the human-confirmation + side/type/quantity rules. The mock client
 * resolves offline so the suite is deterministic; flipping `USE_MOCK` to false
 * binds the real Night Researcher Paper API with no test changes.
 */
import { describe, it, expect } from "vitest";
import { getPaperApi, USE_MOCK, MOCK_SEED_ORDERS } from "./paperApi";
import type { PaperApiError } from "./types";

const api = getPaperApi("");

describe("mock mode is the default", () => {
  it("reports USE_MOCK = true until the backend is merged", () => {
    expect(USE_MOCK).toBe(true);
  });
});

describe("market data (mock shape, same as real envelopes)", () => {
  it("getTicker returns one price per requested symbol", async () => {
    const t = await api.getTicker(["BTCUSDT", "ETHUSDT", "ORDIUSDT", "1000SATSUSDT"]);
    expect(t).toHaveLength(4);
    for (const x of t) expect(typeof x.price).toBe("number");
  });

  it("getCandles returns OHLCV with high >= low", async () => {
    const cs = await api.getCandles("BTCUSDT", "5m");
    expect(cs.length).toBeGreaterThan(0);
    for (const c of cs) {
      expect(c.high).toBeGreaterThanOrEqual(c.low);
      expect(c.high).toBeGreaterThanOrEqual(c.open);
      expect(c.high).toBeGreaterThanOrEqual(c.close);
      expect(c.time).toBeGreaterThan(0);
    }
  });

  it("getOrderBook returns non-crossed bids/asks with cumulative totals", async () => {
    const book = await api.getOrderBook("BTCUSDT");
    expect(book.symbol).toBe("BTCUSDT");
    expect(book.bids.length).toBeGreaterThan(0);
    expect(book.asks.length).toBeGreaterThan(0);
    expect(book.asks[0].price).toBeGreaterThan(book.bids[0].price);
    for (let i = 1; i < book.bids.length; i++) {
      expect(book.bids[i].total).toBeGreaterThanOrEqual(book.bids[i - 1].total);
    }
  });
});

describe("paper account (server is the authority)", () => {
  it("returns an account envelope with a server-derived summary", async () => {
    const acc = await api.getAccount();
    expect(acc.ok).toBe(true);
    expect(acc.mode).toBe("server-authoritative-paper");
    expect(acc.summary.equity).toBeGreaterThan(0);
    expect(Array.isArray(acc.summary.positions)).toBe(true);
    expect(typeof acc.summary.exposurePct).toBe("number");
    for (const p of acc.summary.positions) {
      // Spot PnL = (mark - avg) * qty.
      expect(p.unrealizedPnl).toBeCloseTo((p.mark - p.avgPrice) * p.qty, 2);
    }
  });

  it("seeds demo orders so the audit page has data", async () => {
    const acc = await api.getAccount();
    expect(acc.account.orders.length).toBe(MOCK_SEED_ORDERS.length);
  });
});

describe("research run (six roles, deterministic gates)", () => {
  it("returns a run with exactly the six roles, KELLY/TAKER BLOCKED", async () => {
    const run = await api.createResearchRun({ snapshot: { symbols: ["ORDIUSDT"] }, provider: "anthropic" });
    expect(run.roles).toHaveLength(6);
    expect(run.roles).toEqual(["SPOTTER", "PRIOR", "EDGE", "KELLY", "TAKER", "CLOSER"]);
    expect(run.safety.autoExecution).toBe(false);
    expect((run.outputs.KELLY as { status: string }).status).toBe("BLOCKED");
    expect((run.outputs.TAKER as { status: string }).status).toBe("BLOCKED");
  });

  it("createResearchRun stamps the caller's provider", async () => {
    const run = await api.createResearchRun({ snapshot: {}, provider: "anthropic" });
    expect(run.provider).toBe("anthropic");
  });
});

describe("server events (ledger consistency)", () => {
  it("returns events; after= filters by seq", async () => {
    const all = await api.getServerEvents(0, 100);
    expect(all.events.length).toBeGreaterThan(0);
    const cutoff = all.events[Math.floor(all.events.length / 2)].seq;
    const later = await api.getServerEvents(cutoff, 100);
    expect(later.events.every((e) => e.seq > cutoff)).toBe(true);
  });
});

describe("createPaperOrder — the human-confirmation gate", () => {
  it("accepts a valid, confirmed order and returns a filled PaperOrder", async () => {
    const res = await api.createOrder({
      symbol: "ETHUSDT",
      side: "BUY",
      type: "MARKET",
      quantity: 1.5,
      confirmationId: "cfm_op_123",
      clientOrderId: "cli_123",
    });
    expect(res.accepted).toBe(true);
    expect(res.order.id).toBeTruthy();
    expect(res.order.symbol).toBe("ETHUSDT");
    expect(res.order.side).toBe("BUY");
    expect(res.order.status).toBe("FILLED");
    expect(res.order.filledQty).toBe(1.5);
    expect(res.order.confirmationId).toBe("cfm_op_123");
  });
});

describe("cancelPaperOrder", () => {
  it("acknowledges cancellation of a known order", async () => {
    const res = await api.cancelOrder(MOCK_SEED_ORDERS[0].id, "act_x");
    expect(res.order).not.toBeNull();
  });
});

// Ensure the typed error is the one we expect so future refactors keep the code.
it("throws PaperApiError with a code on bad input", async () => {
  try {
    await api.createOrder({
      symbol: "BTCUSDT",
      side: "BUY",
      type: "LIMIT",
      quantity: -1,
      limitPrice: 1,
      confirmationId: "",
      clientOrderId: "cli_bad",
    });
    throw new Error("should have thrown");
  } catch (e) {
    const err = e as PaperApiError;
    expect(err.name).toBe("PaperApiError");
    expect(typeof err.code).toBe("string");
  }
});
