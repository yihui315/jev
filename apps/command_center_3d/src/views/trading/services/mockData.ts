/**
 * trading/services/mockData — typed fake data for the Paper Trading workbench.
 *
 * This is the ONLY data the UI draws on while `USE_MOCK` is true (the default
 * until the backend is merged). Every object satisfies the interfaces in
 * types.ts, so flipping `USE_MOCK` to false requires no change in the pages —
 * they keep receiving the same shapes from the real client.
 *
 * NO real exchange, NO real funds, NO fake "test passed". The mock is explicitly
 * a stand-in for a not-yet-deployed backend.
 */
import type {
  Candle,
  Interval,
  LedgerEvent,
  OrderBook,
  PaperAccount,
  PaperOrder,
  ResearchRun,
  RoleRun,
  Strategy,
  Symbol,
} from "./types";

const NOW = Date.now();

function buildCandles(base: number, n = 60, drift = 0): Candle[] {
  const out: Candle[] = [];
  let price = base * (1 - drift);
  const step = (base * drift) / n || base * 0.0008;
  for (let i = n; i > 0; i--) {
    const time = Math.floor(NOW / 1000) - i * 300;
    const open = price;
    const close = price + (Math.sin(i * 1.3) * base) / 400 + step;
    const high = Math.max(open, close) + (base / 800) * Math.abs(Math.cos(i));
    const low = Math.min(open, close) - (base / 800) * Math.abs(Math.sin(i * 0.7));
    const volume = Math.round(50 + Math.abs(Math.sin(i * 0.5)) * 950);
    out.push({
      time,
      open: round2(open),
      high: round2(high),
      low: round2(low),
      close: round2(close),
      volume,
    });
    price = close;
  }
  return out;
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

function lastOf(candles: Candle[]): number {
  return candles[candles.length - 1]?.close ?? 0;
}

function mkSnapshot(symbol: Symbol, interval: Interval, base: number) {
  const candles = buildCandles(base, 60, 0.02);
  const open = candles[0].open;
  const last = lastOf(candles);
  return {
    symbol,
    interval,
    last: round2(last),
    changePct: round2(((last - open) / open) * 100),
    open: round2(open),
    high: round2(Math.max(...candles.map((c) => c.high))),
    low: round2(Math.min(...candles.map((c) => c.low))),
    volume: candles.reduce((s, c) => s + c.volume, 0),
    ts: NOW,
    candles,
  };
}

export const MOCK_MARKET_SNAPSHOTS = {
  BTCUSDT: mkSnapshot("BTCUSDT", "5m", 67250),
  ETHUSDT: mkSnapshot("ETHUSDT", "5m", 3285),
  ORDIUSDT: mkSnapshot("ORDIUSDT", "5m", 38.4),
};

export const MOCK_QUOTES = Object.values(MOCK_MARKET_SNAPSHOTS);

export const MOCK_ORDERBOOKS: Record<Symbol, OrderBook> = {
  BTCUSDT: mkOrderBook("BTCUSDT", 67250),
  ETHUSDT: mkOrderBook("ETHUSDT", 3285),
  ORDIUSDT: mkOrderBook("ORDIUSDT", 38.4),
};

function mkOrderBook(symbol: Symbol, mid: number): OrderBook {
  const bids = [];
  const asks = [];
  let bidTotal = 0;
  let askTotal = 0;
  for (let i = 1; i <= 10; i++) {
    const size = round2(0.5 + Math.abs(Math.sin(i)) * 3.2);
    bidTotal += size;
    bids.push({ price: round2(mid - i * (mid * 0.0004)), size, total: round2(bidTotal) });
    const aSize = round2(0.5 + Math.abs(Math.cos(i)) * 3.0);
    askTotal += aSize;
    asks.push({ price: round2(mid + i * (mid * 0.0004)), size: aSize, total: round2(askTotal) });
  }
  return { venue: "PAPER-BOOK", symbol, bids, asks, ts: NOW };
}

export const MOCK_ACCOUNT: PaperAccount = {
  cash: 81250.44,
  equity: 100412.18,
  marginUsed: 19161.74,
  positions: [
    {
      symbol: "BTCUSDT",
      side: "long",
      quantity: 0.35,
      avgPrice: 64120.5,
      markPrice: 67250,
      unrealizedPnl: (67250 - 64120.5) * 0.35,
      leverage: 3,
    },
    {
      symbol: "ETHUSDT",
      side: "long",
      quantity: 4.2,
      avgPrice: 3010.25,
      markPrice: 3285,
      unrealizedPnl: (3285 - 3010.25) * 4.2,
      leverage: 2,
    },
    {
      symbol: "ORDIUSDT",
      side: "short",
      quantity: 120,
      avgPrice: 41.1,
      markPrice: 38.4,
      unrealizedPnl: (41.1 - 38.4) * 120,
      leverage: 1,
    },
  ],
  pnlDay: 1284.6,
  pnlTotal: 18412.18,
  maxDrawdown: 0.094,
  riskStatus: "warning",
  ts: NOW,
};

export const MOCK_STRATEGIES: Strategy[] = [
  {
    strategyId: "strat-night-mean-rev",
    name: "Night Mean Reversion",
    version: "v3.2.1",
    returns: 0.412,
    sharpe: 1.84,
    maxDrawdown: 0.072,
    winRate: 0.63,
    backtestPeriod: "2023-01..2024-06",
    oosPeriod: "2024-07..2025-09",
    status: "active",
  },
  {
    strategyId: "strat-breakout-tape",
    name: "Tape Breakout",
    version: "v1.9.0",
    returns: 0.287,
    sharpe: 1.21,
    maxDrawdown: 0.141,
    winRate: 0.51,
    backtestPeriod: "2023-01..2024-06",
    oosPeriod: "2024-07..2025-09",
    status: "active",
  },
  {
    strategyId: "strat-micro-structure",
    name: "Microstructure Edge",
    version: "v0.7.3",
    returns: 0.158,
    sharpe: 0.96,
    maxDrawdown: 0.058,
    winRate: 0.55,
    backtestPeriod: "2023-06..2024-06",
    oosPeriod: "2024-07..2025-09",
    status: "shadow",
  },
  {
    strategyId: "strat-legacy-mom",
    name: "Legacy Momentum",
    version: "v2.0.0",
    returns: 0.091,
    sharpe: 0.44,
    maxDrawdown: 0.233,
    winRate: 0.47,
    backtestPeriod: "2022-01..2023-12",
    oosPeriod: "2024-01..2024-12",
    status: "retired",
  },
];

/** One six-role run, returned by getResearchRun. */
export const MOCK_RESEARCH_RUN: ResearchRun = {
  runId: "run_night_8f3a21",
  providerId: "openai",
  symbols: ["BTCUSDT", "ETHUSDT"],
  snapshotId: "snap_2025-09-30T14:05:00Z",
  strategyVersion: "v3.2.1",
  modelOverride: "gpt-5-mini",
  createdAt: NOW - 1000 * 60 * 3,
  roles: buildSixRoles(),
};

function buildSixRoles(): RoleRun[] {
  const providers: Record<string, { model: string; provider: string }> = {
    SPOTTER: { model: "gpt-5-mini", provider: "openai" },
    PRIOR: { model: "claude-opus-4", provider: "anthropic" },
    EDGE: { model: "gemini-2.5-pro", provider: "google" },
    KELLY: { model: "gpt-5", provider: "openai" },
    TAKER: { model: "claude-opus-4", provider: "anthropic" },
    CLOSER: { model: "gpt-5-mini", provider: "openai" },
  };
  const decisions: Record<string, { decision: string; rejectionReason?: string }> = {
    SPOTTER: { decision: "MOMENTUM_BIAS_UP on BTCUSDT/ETHUSDT" },
    PRIOR: { decision: "PRIORITIZE BTCUSDT — liquidity best, edge stable" },
    EDGE: { decision: "EDGE_OK — expected value +1.8% per unit risk" },
    KELLY: { decision: "KELLY_FRACTION 0.18 of risk budget" },
    TAKER: {
      decision: "PROPOSAL_BLOCKED",
      rejectionReason: "Size exceeds intraday drawdown limit (KELLY 0.18 > cap 0.15)",
    },
    CLOSER: { decision: "NO_ACTION — awaiting human confirmation" },
  };
  const order: RoleRun["role"][] = ["SPOTTER", "PRIOR", "EDGE", "KELLY", "TAKER", "CLOSER"];
  return order.map((role, i) => ({
    role,
    decision: decisions[role].decision,
    rejectionReason: decisions[role].rejectionReason,
    model: providers[role].model,
    provider: providers[role].provider,
    tokensUsed: 1200 + i * 340,
    latencyMs: 180 + i * 47,
  }));
}

export const MOCK_LEDGER: LedgerEvent[] = [
  {
    eventId: "evt_a1",
    ts: NOW - 1000 * 60 * 12,
    type: "RESEARCH",
    note: "run_night_8f3a21 completed — TAKER blocked on size cap",
  },
  {
    eventId: "evt_a2",
    ts: NOW - 1000 * 60 * 9,
    type: "ORDER",
    symbol: "BTCUSDT",
    quantity: 0.1,
    price: 67120,
    amount: -6712,
    note: "LIMIT BUY 0.1 BTCUSDT @ 67120 (confirmed by operator)",
  },
  {
    eventId: "evt_a3",
    ts: NOW - 1000 * 60 * 8,
    type: "FILL",
    symbol: "BTCUSDT",
    quantity: 0.1,
    price: 67120,
    amount: -6712,
    note: "FILLED 0.1 BTCUSDT @ 67120",
  },
  {
    eventId: "evt_a4",
    ts: NOW - 1000 * 60 * 5,
    type: "RISK_BREACH",
    symbol: "ORDIUSDT",
    note: "Margin utilisation 82% — soft warning, no auto-liquidation",
  },
  {
    eventId: "evt_a5",
    ts: NOW - 1000 * 60 * 2,
    type: "CANCEL",
    symbol: "ETHUSDT",
    quantity: 0.5,
    note: "CANCELLED LIMIT BUY 0.5 ETHUSDT @ 3190 (operator)",
  },
];

/** A small set of pre-existing orders for the audit page. */
export const MOCK_ORDERS: PaperOrder[] = [
  {
    orderId: "ord_btc_1",
    symbol: "BTCUSDT",
    side: "BUY",
    type: "LIMIT",
    quantity: 0.1,
    limitPrice: 67120,
    status: "FILLED",
    filledQty: 0.1,
    avgFillPrice: 67120,
    confirmationId: "cfm_op_7c2",
    researchRunId: "run_night_8f3a21",
    createdAt: NOW - 1000 * 60 * 9,
  },
  {
    orderId: "ord_eth_1",
    symbol: "ETHUSDT",
    side: "BUY",
    type: "LIMIT",
    quantity: 0.5,
    limitPrice: 3190,
    status: "CANCELLED",
    filledQty: 0,
    confirmationId: "cfm_op_9d1",
    createdAt: NOW - 1000 * 60 * 2,
  },
  {
    orderId: "ord_ordi_1",
    symbol: "ORDIUSDT",
    side: "SELL",
    type: "MARKET",
    quantity: 120,
    status: "FILLED",
    filledQty: 120,
    avgFillPrice: 38.4,
    confirmationId: "cfm_op_4a8",
    researchRunId: "run_night_8f3a21",
    createdAt: NOW - 1000 * 60 * 30,
  },
];
