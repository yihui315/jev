/**
 * trading/services/mockData — OFFLINE, spot-only mock data.
 *
 * Shapes are identical to the real API envelopes (see README.md). The mock
 * client resolves these; flipping `USE_MOCK` to false binds the real Night
 * Researcher Paper API with NO page changes. No leverage, no short/long — spot.
 */
import type {
  AccountResponse,
  AccountSummary,
  Candle,
  Fill,
  Interval,
  OrderBook,
  OrderBookLevel,
  PaperAccount,
  PaperOrder,
  ResearchRunResult,
  ServerEvent,
  Symbol,
  Ticker,
} from "./types";

const SYMBOLS: Symbol[] = ["BTCUSDT", "ETHUSDT", "ORDIUSDT", "1000SATSUSDT"];

export const MOCK_TICKERS: Ticker[] = [
  { symbol: "BTCUSDT", price: 67000 },
  { symbol: "ETHUSDT", price: 3300 },
  { symbol: "ORDIUSDT", price: 0.85 },
  { symbol: "1000SATSUSDT", price: 0.00032 },
];

/** Shared market factor so the mock series show realistic cross-asset
 *  correlation (BTC/ETH/etc. move together), instead of four independent
 *  noises that would render every correlation edge invisible offline. This is
 *  still explicitly mock data — it only makes the correlation scene meaningful
 *  in demo mode; the real API path computes correlation from live Binance. */
let _marketWalk: number[] | null = null;
function marketWalk(n: number): number[] {
  if (_marketWalk && _marketWalk.length >= n) return _marketWalk.slice(0, n);
  const out: number[] = [];
  let v = 1;
  let s = 99173;
  for (let i = 0; i < n; i++) {
    s = (s * 1103515245 + 12345) & 0x7fffffff;
    const r = (s / 0x7fffffff - 0.5) * 2 * 0.008;
    v = Math.max(v * (1 + r), 0.5);
    out.push(v);
  }
  _marketWalk = out;
  return out;
}

/** Deterministic pseudo-random walk so offline renders and tests are stable. */
function walk(seed: number, base: number, n: number, vol: number): number[] {
  const out: number[] = [];
  let v = base;
  let s = seed;
  for (let i = 0; i < n; i++) {
    s = (s * 1103515245 + 12345) & 0x7fffffff;
    const r = (s / 0x7fffffff - 0.5) * 2 * vol;
    v = Math.max(v * (1 + r), base * 0.5);
    out.push(v);
  }
  return out;
}

export function mockCandles(symbol: Symbol, interval: Interval, limit = 120): Candle[] {
  const ticker = MOCK_TICKERS.find((t) => t.symbol === symbol);
  const base = ticker ? ticker.price : 1;
  const vol = symbol === "BTCUSDT" ? 0.01 : symbol === "ETHUSDT" ? 0.012 : 0.02;
  const mw = marketWalk(limit);
  const idio = walk(symbol.length * 7919 + limit, 1, limit, vol);
  // 70% shared market factor + 30% idiosyncratic → realistic positive corr.
  const closes = mw.map((m, i) => base * (0.7 * m + 0.3 * idio[i]));
  const start = Math.floor(Date.now() / 1000) - limit * 300;
  const ms = intervalMinutes(interval) * 60;
  return closes.map((c, i) => {
    const open = i === 0 ? c * 0.999 : closes[i - 1];
    const high = Math.max(open, c) * (1 + vol * 0.3);
    const low = Math.min(open, c) * (1 - vol * 0.3);
    const volume = 50 + (i % 7) * 13;
    return {
      time: start + i * ms,
      open: round(open),
      high: round(high),
      low: round(low),
      close: round(c),
      volume,
    };
  });
}

function intervalMinutes(interval: Interval): number {
  const map: Record<string, number> = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "6h": 360, "8h": 480, "12h": 720,
    "1d": 1440, "3d": 4320, "1w": 10080, "1M": 43200,
  };
  return map[interval] ?? 5;
}

function round(n: number): number {
  return Math.round(n * 1e8) / 1e8;
}

export function mockOrderBook(symbol: Symbol, depth = 12): OrderBook {
  const ticker = MOCK_TICKERS.find((t) => t.symbol === symbol);
  const mid = ticker ? ticker.price : 1;
  const tick = mid * 0.0002;
  let cumB = 0;
  let cumA = 0;
  const bids: OrderBookLevel[] = [];
  const asks: OrderBookLevel[] = [];
  for (let i = 0; i < depth; i++) {
    const bq = round(0.5 + (i % 3) * 0.4);
    const aq = round(0.5 + ((i + 1) % 3) * 0.4);
    cumB += bq;
    cumA += aq;
    bids.push({ price: round(mid - tick * (i + 1)), qty: bq, total: cumB });
    asks.push({ price: round(mid + tick * (i + 1)), qty: aq, total: cumA });
  }
  return {
    venue: "binance-spot",
    symbol,
    lastUpdateId: 1_000_000,
    bids,
    asks,
    source: "Binance public REST depth snapshot (mock)",
    receivedAt: new Date().toISOString(),
  };
}

// --- Account (spot-only, no leverage) -------------------------------------

function summarize(account: PaperAccount, marks: Record<string, number>): AccountSummary {
  let marketValue = 0;
  let unrealizedPnl = 0;
  const positions = Object.entries(account.positions).map(([symbol, p]) => {
    const mark = marks[symbol] ?? p.avgPrice;
    const mv = p.qty * mark;
    const upnl = (mark - p.avgPrice) * p.qty;
    marketValue += mv;
    unrealizedPnl += upnl;
    return {
      symbol: symbol as Symbol,
      qty: p.qty,
      avgPrice: p.avgPrice,
      mark,
      marketValue: mv,
      unrealizedPnl: upnl,
      reservedQty: p.reservedQty,
    };
  });
  const equity = account.cash + marketValue;
  const drawdown = account.equityPeak > 0 && equity < account.equityPeak
    ? (account.equityPeak - equity) / account.equityPeak
    : 0;
  const dailyPnl = equity - account.dayStartEquity;
  return {
    equity,
    cash: account.cash,
    availableCash: account.cash - account.reservedCash,
    reservedCash: account.reservedCash,
    marketValue,
    unrealizedPnl,
    realizedPnl: account.realizedPnl,
    totalPnl: equity - account.initialCash,
    dailyPnl,
    drawdown,
    exposurePct: equity > 0 ? marketValue / equity : 0,
    positions,
    dailyLossLocked: dailyPnl <= -account.dayStartEquity * 0.03,
    drawdownLocked: drawdown >= 0.1,
  };
}

const MOCK_ACCOUNT: PaperAccount = {
  version: 1,
  generation: 1,
  initialCash: 10000,
  cash: 7300,
  reservedCash: 0,
  positions: {
    BTCUSDT: { qty: 0.03, avgPrice: 60000, reservedQty: 0 },
    ETHUSDT: { qty: 0.3, avgPrice: 3000, reservedQty: 0 },
  },
  orders: [],
  trades: [],
  lastBookSequence: {},
  realizedPnl: 0,
  equityPeak: 10300,
  dayKey: new Date().toISOString().slice(0, 10),
  dayStartEquity: 10000,
  createdAt: new Date(Date.now() - 86400000).toISOString(),
  updatedAt: new Date().toISOString(),
};

const MOCK_MARKS: Record<string, number> = {
  BTCUSDT: 67000,
  ETHUSDT: 3300,
  ORDIUSDT: 0.85,
  "1000SATSUSDT": 0.00032,
};

export function mockAccountResponse(): AccountResponse {
  const account: PaperAccount = JSON.parse(JSON.stringify(MOCK_ACCOUNT));
  // Seed the demo orders so the audit / risk pages have data offline. The
  // account remains the single source of truth (no localStorage authority).
  account.orders = JSON.parse(JSON.stringify(MOCK_SEED_ORDERS));
  return {
    ok: true,
    mode: "server-authoritative-paper",
    account,
    marks: { ...MOCK_MARKS },
    summary: summarize(account, MOCK_MARKS),
    markQuality: "verified",
    version: account.version,
    stateHash: "mock-state-hash",
  };
}

// --- Seed orders / trades (for the audit page) -----------------------------

function mkOrder(o: Partial<PaperOrder> & Pick<PaperOrder, "id" | "symbol" | "side" | "type" | "status">): PaperOrder {
  return {
    clientOrderId: "mock_" + o.id,
    confirmationId: "cfm_mock_" + o.id,
    qty: o.qty ?? 0.01,
    limitPrice: o.limitPrice ?? null,
    filledQty: o.filledQty ?? 0,
    remainingQty: o.remainingQty ?? 0,
    avgFillPrice: o.avgFillPrice ?? null,
    fee: o.fee ?? 0,
    realizedPnl: o.realizedPnl ?? null,
    reservedCash: o.reservedCash ?? 0,
    reservedQty: o.reservedQty ?? 0,
    rejectionCode: o.rejectionCode ?? null,
    reason: o.reason ?? null,
    timeInForce: o.type === "MARKET" ? "IOC" : "GTC",
    bookSequence: o.bookSequence ?? 1,
    bookReceivedAt: o.bookReceivedAt ?? new Date().toISOString(),
    createdAt: o.createdAt ?? new Date().toISOString(),
    updatedAt: o.updatedAt ?? new Date().toISOString(),
    terminalAt: o.terminalAt ?? null,
    fillCount: o.fillCount ?? 0,
    ...o,
  } as PaperOrder;
}

export const MOCK_SEED_ORDERS: PaperOrder[] = [
  mkOrder({
    id: "O-mock-1", symbol: "BTCUSDT", side: "BUY", type: "MARKET", status: "FILLED",
    qty: 0.03, filledQty: 0.03, remainingQty: 0, avgFillPrice: 60000, fee: 0.18, fillCount: 1,
  }),
  mkOrder({
    id: "O-mock-2", symbol: "ETHUSDT", side: "BUY", type: "LIMIT", status: "PARTIALLY_FILLED",
    qty: 0.5, limitPrice: 3000, filledQty: 0.3, remainingQty: 0.2, avgFillPrice: 3000, fee: 0.09, fillCount: 1,
    reservedCash: 600,
  }),
  mkOrder({
    id: "O-mock-3", symbol: "ORDIUSDT", side: "BUY", type: "MARKET", status: "REJECTED",
    qty: 100, filledQty: 0, remainingQty: 0, rejectionCode: "MAX_EXPOSURE", reason: "超过总持仓敞口上限（账户净值的 40%）",
  }),
];

export const MOCK_SERVER_EVENTS: ServerEvent[] = [
  {
    seq: 1, eventId: "e1", commandId: "account-opened", eventType: "paper.server.account.snapshot",
    schemaVersion: 1, payload: { account: MOCK_ACCOUNT }, createdAt: new Date(Date.now() - 86400000).toISOString(),
  },
  {
    seq: 2, eventId: "e2", commandId: "order:mock-1", eventType: "paper.server.order.created",
    schemaVersion: 1, payload: { order: MOCK_SEED_ORDERS[0] }, createdAt: new Date(Date.now() - 3600000).toISOString(),
  },
  {
    seq: 3, eventId: "e3", commandId: "order:mock-1", eventType: "paper.server.order.filled",
    schemaVersion: 1, payload: { orderId: "O-mock-1" }, createdAt: new Date(Date.now() - 3600000).toISOString(),
  },
  {
    seq: 4, eventId: "e4", commandId: "order:mock-3", eventType: "paper.server.order.rejected",
    schemaVersion: 1, payload: { order: MOCK_SEED_ORDERS[2], reason: "MAX_EXPOSURE" }, createdAt: new Date(Date.now() - 1800000).toISOString(),
  },
];

// --- Research run (mirrors real server: KELLY/TAKER BLOCKED with null metrics)
// ---------------------------------------------------------------------------

export function mockResearchRun(symbols: Symbol[], provider: string, model?: string): ResearchRunResult {
  const runId = "run_mock_" + Math.random().toString(36).slice(2, 10);
  const startedAt = new Date().toISOString();
  return {
    ok: true,
    runId,
    snapshotHash: "mock-snapshot-hash",
    promptVersion: "nr-agent-research-v1",
    provider,
    requestedModel: model ?? "default",
    startedAt,
    completedAt: startedAt,
    durationMs: 0,
    runMode: "research-only",
    roles: ["SPOTTER", "PRIOR", "EDGE", "KELLY", "TAKER", "CLOSER"],
    outputs: {
      SPOTTER: {
        title: "机会发现",
        opportunities: symbols.map((s) => ({
          symbol: s, setup: "区间震荡中的突破观察", evidence: ["价格位于近期区间上沿"], missing_data: ["成交量确认"], watch_only: true,
        })),
        model: model ?? "default",
      },
      PRIOR: {
        title: "市场先验", regime: "range", prior_confidence: 0.5, evidence: ["盘整"], counter_evidence: ["突发新闻"], unknowns: ["资金流向"],
        model: model ?? "default",
      },
      EDGE: {
        title: "优势验证", verdict: "WATCH", symbol: "", side: "WATCH", hypothesis: "需更多样本验证", edge_source: "结构突破", entry_trigger: "放量突破", invalidation: "跌破区间",
        costs_to_test: ["手续费", "滑点"], required_evidence: ["OOS 样本"], model: model ?? "default",
      },
      KELLY: {
        status: "BLOCKED", allocationPct: 0, fullKellyPct: 0, conservativeWinRate: null, sampleCount: 0, oosTrades: 0,
        reasons: ["策略未同时通过历史与 OOS 放行门禁", "全样本有效交易数不足 100", "OOS 有效交易数不足 30"],
      },
      TAKER: {
        status: "BLOCKED", symbol: "", side: "WATCH", autoSubmit: false, requiresManualConfirmation: true,
        reason: "EDGE 未给出 RESEARCH 结论，TAKER 不得创建提案（研究运行无服务端校验的回测/OOS 指标）",
      },
      CLOSER: {
        title: "退出与复盘", decision: "MONITOR", exit_conditions: ["触及失效条件"], failure_modes: ["流动性骤降"], next_validation: ["等待 OOS 样本"], final_risk_note: "仅研究结论，不构成订单授权",
        model: model ?? "default",
      },
    },
    usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
    safety: {
      autoExecution: false,
      liveTrading: false,
      paperOrderCreated: false,
      note: "AI 只产生研究结论；KELLY 与 TAKER 为确定性程序；任何模拟订单都必须由用户点击并重新通过 paper-engine 风控",
    },
  };
}

export { SYMBOLS };
