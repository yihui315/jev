/**
 * trading/services/types — TypeScript mirrors of the Paper Trading × Night
 * Researcher API contract (backend base `/api/v1/trading`).
 *
 * These shapes are the SINGLE source of truth for what the UI expects. The mock
 * module (mockData.ts) returns objects that satisfy these interfaces, and the
 * real client (paperApi.ts) will map backend JSON onto them. The UI only ever
 * sees these types — it never trusts the browser to be the authority on
 * balance, position or risk; all of that comes from server responses.
 */

/** A tradable symbol handled by the paper engine. */
export type Symbol = "BTCUSDT" | "ETHUSDT" | "ORDIUSDT";

/** Candle interval for the market snapshot / K-line. */
export type Interval = "1m" | "5m" | "15m" | "1h" | "4h" | "1d";

export interface Candle {
  /** Unix epoch seconds (lightweight-charts convention). */
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface MarketSnapshot {
  symbol: Symbol;
  interval: Interval;
  last: number;
  /** Percentage change vs the session open (e.g. -1.23 means -1.23%). */
  changePct: number;
  open: number;
  high: number;
  low: number;
  volume: number;
  /** Server timestamp of the snapshot, unix epoch milliseconds. */
  ts: number;
  /** Recent candles for the K-line (most recent last). */
  candles: Candle[];
}

export interface OrderBookLevel {
  price: number;
  /** Size at this price level. */
  size: number;
  /** Cumulative size from the top of book to this level. */
  total: number;
}

export interface OrderBook {
  venue: string;
  symbol: Symbol;
  bids: OrderBookLevel[];
  asks: OrderBookLevel[];
  ts: number;
}

export type PositionSide = "long" | "short";

export interface Position {
  symbol: Symbol;
  side: PositionSide;
  quantity: number;
  /** Average entry price (server-computed). */
  avgPrice: number;
  /** Mark price used for unrealized PnL (server-computed). */
  markPrice: number;
  /** Unrealized PnL (server-computed, never browser-derived). */
  unrealizedPnl: number;
  leverage: number;
}

export type AccountRiskStatus = "ok" | "warning" | "breach";

export interface PaperAccount {
  /** Free cash in the paper account, quote currency. */
  cash: number;
  /** Total equity = cash + sum(position notional + unrealized pnl). */
  equity: number;
  /** Margin currently locked by open positions. */
  marginUsed: number;
  positions: Position[];
  /** Realized + unrealized PnL for the day, quote currency. */
  pnlDay: number;
  /** All-time PnL, quote currency. */
  pnlTotal: number;
  /** Worst peak-to-trough drawdown observed, fraction (0.12 = 12%). */
  maxDrawdown: number;
  /** Server risk verdict for the account as a whole. */
  riskStatus: AccountRiskStatus;
  ts: number;
}

export type OrderSide = "BUY" | "SELL";
export type OrderType = "MARKET" | "LIMIT";

export type OrderStatus =
  | "PENDING_CONFIRMATION"
  | "SUBMITTED"
  | "PARTIAL"
  | "FILLED"
  | "CANCELLED"
  | "REJECTED";

export interface PaperOrder {
  orderId: string;
  symbol: Symbol;
  side: OrderSide;
  type: OrderType;
  quantity: number;
  limitPrice?: number;
  status: OrderStatus;
  filledQty: number;
  avgFillPrice?: number;
  /** The human confirmation id that authorised this order. */
  confirmationId: string;
  /** The research run this order was derived from, if any. */
  researchRunId?: string;
  createdAt: number;
}

/**
 * The object the trading desk builds BEFORE a human confirms it.
 *
 * It carries no server identity yet. `confirmationId` is filled in only after
 * the operator explicitly acknowledges the proposal — TAKER proposals MUST NOT
 * bypass this step, so `createPaperOrder` rejects any proposal lacking a valid
 * confirmation id.
 */
export interface PaperOrderProposal {
  symbol: Symbol;
  side: OrderSide;
  type: OrderType;
  quantity: number;
  limitPrice?: number;
  researchRunId?: string;
  confirmationId?: string;
}

export interface Strategy {
  strategyId: string;
  name: string;
  version: string;
  /** Cumulative return over the backtest window, fraction. */
  returns: number;
  /** Sharpe ratio. */
  sharpe: number;
  /** Max drawdown over the backtest window, fraction. */
  maxDrawdown: number;
  /** Win rate, fraction. */
  winRate: number;
  backtestPeriod: string;
  oosPeriod: string;
  status: "active" | "shadow" | "retired";
}

/** The six research roles of the Night Researcher pipeline. */
export type ResearchRole = "SPOTTER" | "PRIOR" | "EDGE" | "KELLY" | "TAKER" | "CLOSER";

export interface RoleRun {
  role: ResearchRole;
  /** Per-role model decision. */
  decision: string;
  /** Populated when the role rejected the proposal. */
  rejectionReason?: string;
  model: string;
  provider: string;
  /** Tokens consumed by this role. */
  tokensUsed: number;
  /** End-to-end latency for this role, ms. */
  latencyMs: number;
}

export interface ResearchRun {
  runId: string;
  providerId: string;
  symbols: Symbol[];
  snapshotId: string;
  strategyVersion: string;
  /** Model override applied to the run, if any. */
  modelOverride?: string;
  createdAt: number;
  /** One RoleRun per role (SPOTTER … CLOSER). */
  roles: RoleRun[];
}

export type LedgerEventType =
  | "ORDER"
  | "FILL"
  | "CANCEL"
  | "REJECT"
  | "RESEARCH"
  | "RISK_BREACH";

export interface LedgerEvent {
  eventId: string;
  ts: number;
  type: LedgerEventType;
  symbol?: Symbol;
  quantity?: number;
  price?: number;
  /** Signed cash impact (quote currency); positive = inflow. */
  amount?: number;
  note: string;
}

/** Input to the request helpers. */
export interface GetMarketSnapshotInput {
  symbols: Symbol[];
  interval: Interval;
}
export interface GetOrderBookInput {
  venue: string;
  symbol: Symbol;
}
export interface CreateResearchRunInput {
  providerId: string;
  symbols: Symbol[];
  snapshotId: string;
  strategyVersion: string;
  modelOverride?: string;
}
export interface CreatePaperOrderInput {
  symbol: Symbol;
  side: OrderSide;
  type: OrderType;
  quantity: number;
  limitPrice?: number;
  /** Human confirmation — REQUIRED. */
  confirmationId: string;
  researchRunId?: string;
}
export interface SubscribeEventsInput {
  after?: number;
}

/** Why a request failed. Surfaced verbatim to the operator, never swallowed. */
export class PaperApiError extends Error {
  constructor(
    message: string,
    public readonly code:
      | "BAD_INPUT"
      | "MISSING_CONFIRMATION"
      | "NETWORK"
      | "AUTH"
      | "SERVER",
    public readonly status?: number,
  ) {
    super(message);
    this.name = "PaperApiError";
  }
}
