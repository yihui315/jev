/**
 * trading/services/paperApi — the TypeScript client for the Paper Trading API.
 *
 * Mirrors the backend contract at base `/api/v1/trading`:
 *   getMarketSnapshot, getOrderBook, createResearchRun, getResearchRun,
 *   listStrategies, getPaperAccount, createPaperOrder, cancelPaperOrder,
 *   subscribeEvents.
 *
 * While `USE_MOCK` is true (the default until the backend is deployed/merged)
 * every method resolves from typed fake data in mockData.ts. When it is false,
 * the real methods use the EXISTING transport (`authorizedFetch` / `openFetch`
 * from lib/authorizedFetch.ts) — this module does NOT rebuild API infra, and it
 * does NOT create a second provider/API-key system; it only talks to the Paper
 * API.
 *
 * CRITICAL: the browser is never the authority on balance/positions/risk. Those
 * values come from `getPaperAccount` (server) only. `createPaperOrder` enforces
 * the human-confirmation rule: a proposal without a valid confirmationId is
 * rejected — TAKER proposals MUST NOT bypass this.
 */
import { authorizedFetch, openFetch } from "../../../lib/authorizedFetch";
import {
  MOCK_ACCOUNT,
  MOCK_LEDGER,
  MOCK_MARKET_SNAPSHOTS,
  MOCK_ORDERBOOKS,
  MOCK_ORDERS,
  MOCK_RESEARCH_RUN,
  MOCK_STRATEGIES,
} from "./mockData";
import type {
  CreatePaperOrderInput,
  CreateResearchRunInput,
  GetMarketSnapshotInput,
  GetOrderBookInput,
  LedgerEvent,
  MarketSnapshot,
  OrderBook,
  PaperAccount,
  PaperApiError,
  PaperOrder,
  ResearchRun,
  Strategy,
  SubscribeEventsInput,
  Symbol,
} from "./types";
import { validateOrderProposal } from "../lib/finance";

/**
 * Flip to `false` once the backend at `/api/v1/trading` is merged and deployed.
 * Until then every call resolves from mockData so the pages render and the
 * tests pass offline.
 */
export const USE_MOCK = true;

const TRADING_BASE = "/api/v1/trading";

export interface PaperApiClient {
  getMarketSnapshot(input: GetMarketSnapshotInput): Promise<MarketSnapshot[]>;
  getOrderBook(input: GetOrderBookInput): Promise<OrderBook>;
  createResearchRun(input: CreateResearchRunInput, controlToken?: string): Promise<ResearchRun>;
  getResearchRun(runId: string, controlToken?: string): Promise<ResearchRun>;
  listStrategies(controlToken?: string): Promise<Strategy[]>;
  getPaperAccount(controlToken?: string): Promise<PaperAccount>;
  createPaperOrder(input: CreatePaperOrderInput, controlToken?: string): Promise<PaperOrder>;
  cancelPaperOrder(orderId: string, controlToken?: string): Promise<{ ok: boolean; orderId: string }>;
  subscribeEvents(input?: SubscribeEventsInput): Promise<LedgerEvent[]>;
}

/** Small delay so the mock feels like a network round-trip (and async stays real). */
function delay<T>(value: T, ms = 120): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}

function newId(prefix: string): string {
  return `${prefix}_${Math.random().toString(36).slice(2, 10)}`;
}

// ---------------------------------------------------------------------------
// MOCK IMPLEMENTATION
// ---------------------------------------------------------------------------

const mockClient: PaperApiClient = {
  async getMarketSnapshot(input: GetMarketSnapshotInput) {
    const snaps = input.symbols.map((s) => {
      const base = MOCK_MARKET_SNAPSHOTS[s];
      return { ...base, interval: input.interval };
    });
    return delay(snaps);
  },

  async getOrderBook(input: GetOrderBookInput) {
    const book = MOCK_ORDERBOOKS[input.symbol];
    if (!book) {
      const e: PaperApiError = Object.assign(
        new Error(`no order book for ${input.symbol}`),
        { name: "PaperApiError", code: "BAD_INPUT" as const },
      );
      throw e;
    }
    return delay({ ...book, venue: input.venue || book.venue });
  },

  async createResearchRun(input: CreateResearchRunInput) {
    // Mirror the mock run but stamp the caller's symbols/provider/version.
    const run: ResearchRun = {
      ...MOCK_RESEARCH_RUN,
      runId: newId("run"),
      providerId: input.providerId,
      symbols: input.symbols,
      snapshotId: input.snapshotId,
      strategyVersion: input.strategyVersion,
      modelOverride: input.modelOverride,
      createdAt: Date.now(),
    };
    return delay(run);
  },

  async getResearchRun(runId: string) {
    return delay({ ...MOCK_RESEARCH_RUN, runId });
  },

  async listStrategies() {
    return delay(MOCK_STRATEGIES);
  },

  async getPaperAccount() {
    return delay(MOCK_ACCOUNT);
  },

  async createPaperOrder(input: CreatePaperOrderInput) {
    // The server-side rule, enforced here so the UI never sends an unconfirmed
    // order. TAKER proposals in particular MUST carry a human confirmationId.
    const proposal = { ...input, confirmationId: input.confirmationId };
    const v = validateOrderProposal(proposal);
    if (!v.ok) {
      const code = v.error?.includes("confirmation") ? "MISSING_CONFIRMATION" : "BAD_INPUT";
      const e: PaperApiError = Object.assign(new Error(v.error ?? "invalid order"), {
        name: "PaperApiError",
        code: code as PaperApiError["code"],
      });
      throw e;
    }
    const order: PaperOrder = {
      orderId: newId("ord"),
      symbol: input.symbol,
      side: input.side,
      type: input.type,
      quantity: input.quantity,
      limitPrice: input.limitPrice,
      // A confirmed proposal is accepted; in paper mode it fills immediately.
      status: "FILLED",
      filledQty: input.quantity,
      avgFillPrice: input.limitPrice ?? MOCK_MARKET_SNAPSHOTS[input.symbol].last,
      confirmationId: input.confirmationId,
      researchRunId: input.researchRunId,
      createdAt: Date.now(),
    };
    return delay(order);
  },

  async cancelPaperOrder(orderId: string) {
    return delay({ ok: true, orderId });
  },

  async subscribeEvents(input?: SubscribeEventsInput) {
    const after = input?.after ?? 0;
    return delay(MOCK_LEDGER.filter((e) => e.ts >= after));
  },
};

// ---------------------------------------------------------------------------
// REAL IMPLEMENTATION (used when USE_MOCK === false)
// ---------------------------------------------------------------------------

function makeRealClient(controlToken: string): PaperApiClient {
  return {
    async getMarketSnapshot(input) {
      const syms = input.symbols.join(",");
      const res = await openFetch<MarketSnapshot[]>(
        `${TRADING_BASE}/market-snapshot?symbols=${syms}&interval=${input.interval}`,
      );
      if (!res.ok || !res.data) {
        throw apiError("NETWORK", res.status, res.error ?? "market snapshot failed");
      }
      return res.data;
    },

    async getOrderBook(input) {
      const res = await openFetch<OrderBook>(
        `${TRADING_BASE}/order-book?venue=${encodeURIComponent(input.venue)}&symbol=${input.symbol}`,
      );
      if (!res.ok || !res.data) {
        throw apiError("NETWORK", res.status, res.error ?? "order book failed");
      }
      return res.data;
    },

    async createResearchRun(input, token) {
      const res = await authorizedFetch<ResearchRun>(
        token ?? controlToken,
        `${TRADING_BASE}/research-runs`,
        { method: "POST", body: input },
      );
      if (!res.ok || !res.data) {
        throw apiError(
          res.authFailed ? "AUTH" : "SERVER",
          res.status,
          res.error ?? "create research run failed",
        );
      }
      return res.data;
    },

    async getResearchRun(runId, token) {
      const res = await authorizedFetch<ResearchRun>(
        token ?? controlToken,
        `${TRADING_BASE}/research-runs/${runId}`,
        { method: "GET" },
      );
      if (!res.ok || !res.data) {
        throw apiError(
          res.authFailed ? "AUTH" : "SERVER",
          res.status,
          res.error ?? "get research run failed",
        );
      }
      return res.data;
    },

    async listStrategies(token) {
      const res = await authorizedFetch<Strategy[]>(
        token ?? controlToken,
        `${TRADING_BASE}/strategies`,
        { method: "GET" },
      );
      if (!res.ok || !res.data) {
        throw apiError(
          res.authFailed ? "AUTH" : "SERVER",
          res.status,
          res.error ?? "list strategies failed",
        );
      }
      return res.data;
    },

    async getPaperAccount(token) {
      const res = await authorizedFetch<PaperAccount>(
        token ?? controlToken,
        `${TRADING_BASE}/account`,
        { method: "GET" },
      );
      if (!res.ok || !res.data) {
        throw apiError(
          res.authFailed ? "AUTH" : "SERVER",
          res.status,
          res.error ?? "get paper account failed",
        );
      }
      // Server is the ONLY authority on balance/positions/risk.
      return res.data;
    },

    async createPaperOrder(input, token) {
      // Enforce the human-confirmation rule even on the real path.
      const v = validateOrderProposal({ ...input, confirmationId: input.confirmationId });
      if (!v.ok) {
        const code = v.error?.includes("confirmation") ? "MISSING_CONFIRMATION" : "BAD_INPUT";
        throw apiError(code, undefined, v.error ?? "invalid order");
      }
      const res = await authorizedFetch<PaperOrder>(
        token ?? controlToken,
        `${TRADING_BASE}/orders`,
        { method: "POST", body: input },
      );
      if (!res.ok || !res.data) {
        throw apiError(
          res.authFailed ? "AUTH" : "SERVER",
          res.status,
          res.error ?? "create paper order failed",
        );
      }
      return res.data;
    },

    async cancelPaperOrder(orderId, token) {
      const res = await authorizedFetch<{ ok: boolean; orderId: string }>(
        token ?? controlToken,
        `${TRADING_BASE}/orders/${orderId}`,
        { method: "DELETE" },
      );
      if (!res.ok || !res.data) {
        throw apiError(
          res.authFailed ? "AUTH" : "SERVER",
          res.status,
          res.error ?? "cancel paper order failed",
        );
      }
      return res.data;
    },

    async subscribeEvents(input) {
      const after = input?.after ?? 0;
      const res = await openFetch<LedgerEvent[]>(
        `${TRADING_BASE}/events?after=${after}`,
      );
      if (!res.ok || !res.data) {
        throw apiError("NETWORK", res.status, res.error ?? "subscribe events failed");
      }
      return res.data;
    },
  };
}

function apiError(
  code: PaperApiError["code"],
  status: number | undefined,
  message: string,
): PaperApiError {
  return Object.assign(new Error(message), { name: "PaperApiError", code, status });
}

/**
 * Get the client. In mock mode this ignores the token (no server yet). In real
 * mode it binds the control token so every write is authenticated through the
 * existing transport.
 */
export function getPaperApi(controlToken = ""): PaperApiClient {
  if (USE_MOCK) return mockClient;
  return makeRealClient(controlToken);
}

/** Re-export the static mock orders for the audit page's initial render. */
export const MOCK_SEED_ORDERS: PaperOrder[] = MOCK_ORDERS;
