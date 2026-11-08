/**
 * trading/services/paperApi — the single Trading API Client.
 *
 * Binds to the REAL Night Researcher Paper API (`/api/paper/*`) and the six-role
 * research endpoint (`/api/agents/research`). Market data (ticker / candles /
 * depth) always comes from the authoritative Binance public REST source the
 * paper engine itself reads — never faked.
 *
 * `USE_MOCK` (default true) swaps the implementation for an offline, spot-only
 * mock with identical envelopes. Flipping it to false binds the real API with
 * NO page changes. See README.md.
 *
 * Hard rules honoured here:
 *  - Only the fields the server's `validateInput` allows are sent on order
 *    creation (`clientOrderId, confirmationId, symbol, side, type, quantity,
 *    limitPrice`). `researchRunId` is intentionally NOT forwarded to the server.
 *  - Auth for `/api/paper/*` is the `nr_paper_session` cookie (bootstrapped by
 *    `ensureSession`). In production these paths must be served same-origin.
 *  - `safety.autoExecution` is always false; TAKER proposals never bypass the
 *    human confirmation gate.
 */
import type {
  AccountResponse,
  CancelOrderResponse,
  CreateOrderResponse,
  Interval,
  OrderBook,
  OrderProposal,
  PaperAccount,
  PaperOrder,
  ResearchInput,
  ResearchRunResult,
  ServerEventsResponse,
  Symbol,
  Ticker,
  Candle,
  Fill,
} from "./types";
import { PaperApiError } from "./types";
import {
  fetchCandles as realFetchCandles,
  fetchOrderBook as realFetchOrderBook,
  fetchTicker as realFetchTicker,
  MARKET_DATA_SOURCE,
} from "./marketData";
import { validateOrderProposal } from "../lib/finance";
import {
  MOCK_SEED_ORDERS,
  MOCK_SERVER_EVENTS,
  MOCK_TICKERS,
  mockAccountResponse,
  mockCandles,
  mockOrderBook,
  mockResearchRun,
} from "./mockData";

export const USE_MOCK = true;
export { MARKET_DATA_SOURCE, MOCK_SEED_ORDERS };

const PAPER_BASE = "/api/paper";
const AGENTS_BASE = "/api/agents";

export interface PaperApiClient {
  ensureSession(): Promise<void>;
  getAccount(): Promise<AccountResponse>;
  createOrder(input: OrderProposal): Promise<CreateOrderResponse>;
  cancelOrder(orderId: string, clientActionId: string): Promise<CancelOrderResponse>;
  resetAccount(input: { clientActionId: string; confirmationId: string }): Promise<AccountResponse>;
  getTicker(symbols: Symbol[]): Promise<Ticker[]>;
  getCandles(symbol: Symbol, interval: Interval, limit?: number): Promise<Candle[]>;
  getOrderBook(symbol: Symbol, limit?: number): Promise<OrderBook>;
  createResearchRun(input: ResearchInput): Promise<ResearchRunResult>;
  getServerEvents(after?: number, limit?: number): Promise<ServerEventsResponse>;
}

// ---------------------------------------------------------------------------
// Real client — targets the real Night Researcher endpoints (same-origin).
// ---------------------------------------------------------------------------

class RealPaperApiClient implements PaperApiClient {
  async ensureSession(): Promise<void> {
    try {
      await this.raw(PAPER_BASE + "/session", { method: "POST", body: "{}" });
    } catch {
      /* session may already be active or backend unreachable in dev */
    }
  }

  private async raw(path: string, init: RequestInit = {}): Promise<Response> {
    const res = await fetch(path, {
      credentials: "same-origin",
      cache: "no-store",
      headers: { accept: "application/json", ...(init.body ? { "content-type": "application/json" } : {}) },
      ...init,
    });
    if (!res.ok) {
      let body: { error?: string; code?: string } | null = null;
      try {
        body = await res.json();
      } catch {
        /* ignore */
      }
      throw new PaperApiError(body?.error || `HTTP ${res.status} @ ${path}`, res.status, body?.code ?? null);
    }
    return res;
  }

  async getAccount(): Promise<AccountResponse> {
    const res = await this.raw(PAPER_BASE + "/account");
    return (await res.json()) as AccountResponse;
  }

  async createOrder(input: OrderProposal): Promise<CreateOrderResponse> {
    // Forward ONLY the fields the server's validateInput permits.
    const body: Record<string, unknown> = {
      clientOrderId: input.clientOrderId,
      confirmationId: input.confirmationId,
      symbol: input.symbol,
      side: input.side,
      type: input.type,
      quantity: input.quantity,
    };
    if (input.type === "LIMIT") body.limitPrice = input.limitPrice ?? null;
    const res = await this.raw(PAPER_BASE + "/orders", {
      method: "POST",
      body: JSON.stringify(body),
    });
    const status = res.status;
    const json = (await res.json()) as CreateOrderResponse;
    if (status === 409 && json.rejection) {
      throw new PaperApiError(json.rejection.reason, 409, json.rejection.code);
    }
    return json;
  }

  async cancelOrder(orderId: string, clientActionId: string): Promise<CancelOrderResponse> {
    const res = await this.raw(`${PAPER_BASE}/orders/${encodeURIComponent(orderId)}/cancel`, {
      method: "POST",
      body: JSON.stringify({ clientActionId }),
    });
    return (await res.json()) as CancelOrderResponse;
  }

  async resetAccount(input: { clientActionId: string; confirmationId: string }): Promise<AccountResponse> {
    const res = await this.raw(PAPER_BASE + "/account/reset", {
      method: "POST",
      body: JSON.stringify(input),
    });
    return (await res.json()) as AccountResponse;
  }

  async getTicker(symbols: Symbol[]): Promise<Ticker[]> {
    return realFetchTicker(symbols);
  }

  async getCandles(symbol: Symbol, interval: Interval, limit?: number): Promise<Candle[]> {
    return realFetchCandles(symbol, interval, limit);
  }

  async getOrderBook(symbol: Symbol, limit?: number): Promise<OrderBook> {
    return realFetchOrderBook(symbol, limit);
  }

  async createResearchRun(input: ResearchInput): Promise<ResearchRunResult> {
    const res = await this.raw(AGENTS_BASE + "/research", {
      method: "POST",
      body: JSON.stringify({
        snapshot: input.snapshot,
        provider: input.provider,
        model: input.model,
        runId: input.runId,
      }),
    });
    return (await res.json()) as ResearchRunResult;
  }

  async getServerEvents(after = 0, limit = 100): Promise<ServerEventsResponse> {
    const qs = `?after=${String(after)}&limit=${String(limit)}`;
    const res = await this.raw(PAPER_BASE + "/events" + qs);
    return (await res.json()) as ServerEventsResponse;
  }
}

// ---------------------------------------------------------------------------
// Mock client — offline, spot-only, identical envelopes.
// ---------------------------------------------------------------------------

class MockPaperApiClient implements PaperApiClient {
  async ensureSession(): Promise<void> {
    /* no-op offline */
  }

  async getAccount(): Promise<AccountResponse> {
    return mockAccountResponse();
  }

  async createOrder(input: OrderProposal): Promise<CreateOrderResponse> {
    const v = validateOrderProposal(input);
    if (!v.ok) {
      throw new PaperApiError(v.error ?? "invalid order", 400, "BAD_INPUT");
    }
    const base = mockAccountResponse();
    const now = new Date().toISOString();
    const order: PaperOrder = {
      id: "O-mock-" + Math.random().toString(36).slice(2, 10),
      clientOrderId: input.clientOrderId,
      confirmationId: input.confirmationId,
      symbol: input.symbol,
      side: input.side,
      type: input.type,
      qty: input.quantity,
      limitPrice: input.type === "LIMIT" ? input.limitPrice ?? null : null,
      status: "FILLED",
      filledQty: input.quantity,
      remainingQty: 0,
      avgFillPrice: base.marks[input.symbol] ?? null,
      fee: input.quantity * (base.marks[input.symbol] ?? 0) * 0.001,
      realizedPnl: null,
      reservedCash: 0,
      reservedQty: 0,
      rejectionCode: null,
      reason: null,
      timeInForce: input.type === "MARKET" ? "IOC" : "GTC",
      bookSequence: 1,
      bookReceivedAt: now,
      createdAt: now,
      updatedAt: now,
      terminalAt: now,
      fillCount: 1,
    };
    const fill: Fill = {
      id: "F-" + order.id + "-001",
      fillIndex: 1,
      orderId: order.id,
      timestamp: now,
      symbol: order.symbol,
      side: order.side,
      qty: order.filledQty,
      price: order.avgFillPrice ?? 0,
      notional: order.filledQty * (order.avgFillPrice ?? 0),
      fee: order.fee,
      realizedPnl: null,
      liquidityModel: "server-visible-orderbook-walk",
      bookSequence: 1,
      bookReceivedAt: now,
    };
    const account: PaperAccount = JSON.parse(JSON.stringify(base.account));
    account.orders = [order, ...account.orders].slice(0, 50);
    account.trades = [fill, ...account.trades].slice(0, 50);
    return {
      ...base,
      account,
      order,
      fills: [fill],
      duplicate: false,
      accepted: true,
      rejection: null,
    };
  }

  async cancelOrder(orderId: string): Promise<CancelOrderResponse> {
    const base = mockAccountResponse();
    const order = base.account.orders.find((o) => o.id === orderId) ?? null;
    return { ...base, status: order ? "NOT_OPEN" : "NOT_FOUND", order };
  }

  async resetAccount(): Promise<AccountResponse> {
    return mockAccountResponse();
  }

  async getTicker(): Promise<Ticker[]> {
    return MOCK_TICKERS;
  }

  async getCandles(symbol: Symbol, interval: Interval, limit?: number): Promise<Candle[]> {
    return mockCandles(symbol, interval, limit);
  }

  async getOrderBook(symbol: Symbol): Promise<OrderBook> {
    return mockOrderBook(symbol);
  }

  async createResearchRun(input: ResearchInput): Promise<ResearchRunResult> {
    const snap = (input.snapshot ?? null) as { symbols?: Symbol[] } | null;
    const symbols = Array.isArray(snap?.symbols) ? snap!.symbols! : [];
    return mockResearchRun(symbols, input.provider, input.model);
  }

  async getServerEvents(after = 0): Promise<ServerEventsResponse> {
    const events = MOCK_SERVER_EVENTS.filter((e) => e.seq > after);
    return { ok: true, events, nextAfter: events.length ? events[events.length - 1].seq : after };
  }
}

// ---------------------------------------------------------------------------
// Factory
// ---------------------------------------------------------------------------

export function getPaperApi(_controlToken = ""): PaperApiClient {
  return USE_MOCK ? new MockPaperApiClient() : new RealPaperApiClient();
}
