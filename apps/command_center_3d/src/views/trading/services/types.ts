/**
 * trading/services/types — TypeScript contract for the REAL Night Researcher
 * Paper API + the honest Binance public market-data source.
 *
 * Every shape here is derived from the actual server implementation
 * (`paper-server.mjs`, `paper-ledger.mjs`, `agent-orchestrator.mjs`), NOT from
 * the earlier fabricated `/api/v1/trading` client. See README.md for the audit.
 *
 * Hard rules:
 *  - Spot-only. There is NO leverage and NO short/long side on positions.
 *  - The server is the authority on balance / positions / fills. The browser is
 *    never the source of truth.
 *  - Market-data (ticker / candles / depth) comes from the same authoritative
 *    Binance public REST source the paper engine itself reads. Never faked.
 */

export type Symbol = "BTCUSDT" | "ETHUSDT" | "ORDIUSDT" | "1000SATSUSDT";
export const ALLOWED_SYMBOLS: readonly Symbol[] = [
  "BTCUSDT",
  "ETHUSDT",
  "ORDIUSDT",
  "1000SATSUSDT",
] as const;

export type Interval =
  | "1m" | "3m" | "5m" | "15m" | "30m"
  | "1h" | "2h" | "4h" | "6h" | "8h" | "12h"
  | "1d" | "3d" | "1w" | "1M";

export type Side = "BUY" | "SELL";
export type OrderType = "MARKET" | "LIMIT";

export type OrderStatus =
  | "NEW"
  | "OPEN"
  | "PARTIALLY_FILLED"
  | "FILLED"
  | "REJECTED"
  | "CANCELED";

// ---------------------------------------------------------------------------
// Market data (Binance public REST — honest, authoritative, labelled in UI)
// ---------------------------------------------------------------------------

export interface Ticker {
  symbol: Symbol;
  price: number;
}

export interface Candle {
  /** UNIX seconds (lightweight-charts UTCTimestamp). */
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface OrderBookLevel {
  price: number;
  qty: number;
  /** Client-computed cumulative quantity from the best price outward. */
  total: number;
}

export interface OrderBook {
  venue: "binance-spot";
  symbol: Symbol;
  lastUpdateId: number;
  bids: OrderBookLevel[];
  asks: OrderBookLevel[];
  source: string;
  receivedAt: string;
}

export interface ExchangeFilters {
  minQty: number;
  maxQty: number;
  stepSize: number;
  minPrice: number;
  maxPrice: number;
  tickSize: number;
  minNotional: number;
}

// ---------------------------------------------------------------------------
// Account (server-authoritative — `paper-server.mjs`)
// ---------------------------------------------------------------------------

export interface PositionSnapshot {
  symbol: Symbol;
  qty: number;
  avgPrice: number;
  mark: number;
  marketValue: number;
  unrealizedPnl: number;
  reservedQty: number;
}

export interface RawPosition {
  qty: number;
  avgPrice: number;
  reservedQty: number;
}

export interface PaperOrder {
  id: string;
  clientOrderId: string;
  confirmationId: string;
  symbol: Symbol;
  side: Side;
  type: OrderType;
  qty: number;
  limitPrice: number | null;
  status: OrderStatus;
  filledQty: number;
  remainingQty: number;
  avgFillPrice: number | null;
  fee: number;
  realizedPnl: number | null;
  reservedCash: number;
  reservedQty: number;
  rejectionCode: string | null;
  reason: string | null;
  timeInForce: "IOC" | "GTC";
  bookSequence: number | null;
  bookReceivedAt: string | null;
  createdAt: string;
  updatedAt: string;
  terminalAt: string | null;
  fillCount: number;
  cancelledQty?: number;
}

export interface Fill {
  id: string;
  fillIndex: number;
  orderId: string;
  timestamp: string;
  symbol: Symbol;
  side: Side;
  qty: number;
  price: number;
  notional: number;
  fee: number;
  realizedPnl: number | null;
  liquidityModel: string;
  bookSequence: number;
  bookReceivedAt: string;
}

export interface PaperAccount {
  version: number;
  generation: number;
  initialCash: number;
  cash: number;
  reservedCash: number;
  positions: Record<string, RawPosition>;
  orders: PaperOrder[];
  trades: Fill[];
  lastBookSequence: Record<string, number>;
  realizedPnl: number;
  equityPeak: number;
  dayKey: string;
  dayStartEquity: number;
  createdAt: string;
  updatedAt: string;
}

export interface AccountSummary {
  equity: number;
  cash: number;
  availableCash: number;
  reservedCash: number;
  marketValue: number;
  unrealizedPnl: number;
  realizedPnl: number;
  totalPnl: number;
  dailyPnl: number;
  drawdown: number;
  exposurePct: number;
  positions: PositionSnapshot[];
  dailyLossLocked: boolean;
  drawdownLocked: boolean;
}

export interface AccountResponse {
  ok: true;
  mode: "server-authoritative-paper";
  account: PaperAccount;
  marks: Record<string, number>;
  summary: AccountSummary;
  markQuality: "verified" | "unknown";
  version: number;
  stateHash: string;
}

// ---------------------------------------------------------------------------
// Order creation
// ---------------------------------------------------------------------------

export interface OrderProposal {
  symbol: Symbol;
  side: Side;
  type: OrderType;
  quantity: number;
  limitPrice?: number | null;
  /** Human-confirmation gate. Required server-side; TAKER cannot bypass it. */
  confirmationId: string;
  /** Idempotency key (8–100 chars: [A-Za-z0-9._:-]). */
  clientOrderId: string;
  researchRunId?: string;
}

export interface OrderRejection {
  code: string;
  reason: string;
}

export interface CreateOrderResponse extends AccountResponse {
  order: PaperOrder;
  fills: Fill[];
  duplicate: boolean;
  accepted: boolean;
  rejection: OrderRejection | null;
  /** Returned by the duplicate branch only (idempotency hit). */
  orderId?: string;
  orderStatus?: string;
}

export interface CancelOrderResponse extends AccountResponse {
  status: "CANCELED" | "NOT_OPEN" | "NOT_FOUND";
  order: PaperOrder | null;
  duplicate?: boolean;
}

// ---------------------------------------------------------------------------
// Research / AI roles (pages 04 & 06) — `agent-orchestrator.mjs`
// ---------------------------------------------------------------------------

export type AiRoleId =
  | "SPOTTER" | "PRIOR" | "EDGE" | "KELLY" | "TAKER" | "CLOSER";

export interface ResearchInput {
  /** Market snapshot object (serializable; explicitly untrusted by the server). */
  snapshot: unknown;
  /** Provider ID only. Credentials are resolved server-side. */
  provider: string;
  model?: string;
  runId?: string;
}

export interface ResearchRunResult {
  ok: boolean;
  runId: string;
  snapshotHash: string;
  promptVersion: string;
  provider: string;
  requestedModel: string;
  startedAt: string;
  completedAt: string;
  durationMs: number;
  runMode: "research-only";
  roles: AiRoleId[];
  outputs: Record<string, Record<string, unknown>>;
  usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
  safety: {
    autoExecution: false;
    liveTrading: false;
    paperOrderCreated: false;
    note: string;
  };
}

// ---------------------------------------------------------------------------
// Server events (audit / live) — `handlePaperServerEvents`
// ---------------------------------------------------------------------------

export type ServerEventType =
  | "paper.server.account.snapshot"
  | "paper.server.account.reset"
  | "paper.server.order.created"
  | "paper.server.order.filled"
  | "paper.server.order.rejected"
  | "paper.server.order.updated"
  | "paper.server.order.cancelled";

export interface ServerEvent {
  seq: number;
  eventId: string;
  commandId: string;
  eventType: ServerEventType | string;
  schemaVersion: number;
  payload: unknown;
  createdAt: string;
}

export interface ServerEventsResponse {
  ok: true;
  events: ServerEvent[];
  nextAfter: number;
}

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

export class PaperApiError extends Error {
  status: number;
  code: string | null;
  requestId: string | null;
  constructor(message: string, status = 0, code: string | null = null, requestId: string | null = null) {
    super(message);
    this.name = "PaperApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}
