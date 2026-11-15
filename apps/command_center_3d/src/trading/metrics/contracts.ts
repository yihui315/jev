// Unified metrics layer for the JEV Trading Cockpit.
//
// HONESTY CONTRACT (non-negotiable):
//  * Never fabricate numbers. A value is emitted ONLY when it can be parsed
//    deterministically from a real field that actually exists inside CockpitRaw.
//  * Paper-derived values are ALWAYS domain:"PAPER". "REAL" is NEVER emitted:
//    the current system has no verified real-money fills. Missing data => N/A
//    with domain "UNAVAILABLE".
//  * Missing / null / NO_DATA / BLOCKED / verdict!=VERIFIED => value:null,
//    display:"N/A", naReason explaining why.
//  * API / inference success rate must NEVER be used as strategy win rate.
//  * The equity curve is only ever read from real snapshots; an empty array is
//    NO DATA, never a synthesized curve.
//  * All parsing is defensive: any `unknown` input degrades to honest N/A.

export type DataDomain = "PAPER" | "REAL" | "UNAVAILABLE";

export type MetricKey =
  | "grossPnl" | "netPnl" | "roi" | "winRate" | "orderAcceptanceRate"
  | "fillRate" | "profitFactor" | "maxDrawdown" | "tradingFees"
  | "slippage" | "aiInferenceCost";

export interface MetricCell {
  key: MetricKey;
  label: string;
  value: number | null;      // null => N/A
  display: string;           // value===null 时必须是 "N/A"
  unit: "USD" | "PCT" | "RATIO" | "COUNT";
  domain: DataDomain;
  source: string;            // 对应 /api/jev/* 路径，或 "NOT_AVAILABLE"
  naReason: string | null;
}

export interface CockpitSnapshot {
  metrics: Record<MetricKey, MetricCell>;
  equity: { value: number | null; display: string; domain: DataDomain };
  cash: { value: number | null; display: string; domain: DataDomain };
  sources: string[];
  warnings: string[];
  generatedAtUtc: string | null;
}

export interface ProviderRow {
  id: string;
  displayName: string;       // "MiniMax" | "M3" | "阿里百炼"
  verdict: "BLOCKED" | "NO_DATA" | "VERIFIED";
  decisionDirection: string | null;
  metrics: {
    inferenceSuccessRate: MetricCell | null;
    avgLatencyMs: MetricCell | null;
    tokenUsage: MetricCell | null;
    apiCostUsd: MetricCell | null;
    riskRejectRate: MetricCell | null;
    attributedNetPnl: MetricCell | null;
  };
  note: string;
}

export interface CockpitRaw {
  account?: unknown; orders?: unknown; events?: unknown; equity?: unknown;
  strategies?: unknown; providers?: unknown; system?: unknown;
}

export const COCKPIT_PALETTE: {
  bg: string; surface: string; surface2: string; border: string;
  text: string; muted: string; up: string; down: string; accent: string; amber: string;
} = {
  bg: "#070B12",
  surface: "#0D141F",
  surface2: "#111B29",
  border: "#1B2A3B",
  text: "#E4EDF7",
  muted: "#7C8DA3",
  up: "#22D3A6",
  down: "#FF5A6A",
  accent: "#3B82F6",
  amber: "#F5B544",
};

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------
export function displayNumber(v: number | null, unit: MetricCell["unit"]): string {
  if (v === null || !Number.isFinite(v)) return "N/A";
  switch (unit) {
    case "USD": {
      const sign = v < 0 ? "-" : "";
      return sign + "$" + Math.abs(v).toLocaleString("en-US", {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
      });
    }
    case "PCT":
      return v.toLocaleString("en-US", {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
      }) + "%";
    case "RATIO":
      return v.toLocaleString("en-US", {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
      });
    case "COUNT":
      return Math.round(v).toLocaleString("en-US");
    default:
      return String(v);
  }
}

// ---------------------------------------------------------------------------
// Defensive type guards / field pickers (internal, never exported)
// ---------------------------------------------------------------------------
function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function finite(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

// Accept either the bare data payload or a JevResponse-style envelope.
function unwrap(v: unknown): unknown {
  if (isRecord(v) && ("schema" in v || "meta" in v) && isRecord(v.data)) return v.data;
  return v;
}

function pickNum(root: unknown, paths: string[][]): number | null {
  if (!isRecord(root)) return null;
  for (const path of paths) {
    let cur: unknown = root;
    for (const key of path) {
      if (isRecord(cur) && key in cur) cur = cur[key];
      else { cur = undefined; break; }
    }
    const n = finite(cur);
    if (n !== null) return n;
  }
  return null;
}

function pickString(root: unknown, paths: string[][]): string | null {
  if (!isRecord(root)) return null;
  for (const path of paths) {
    let cur: unknown = root;
    for (const key of path) {
      if (isRecord(cur) && key in cur) cur = cur[key];
      else { cur = undefined; break; }
    }
    if (typeof cur === "string" && cur.length > 0) return cur;
  }
  return null;
}

// ---------------------------------------------------------------------------
// Cell construction — null ALWAYS collapses to an honest UNAVAILABLE N/A cell.
// ---------------------------------------------------------------------------
function makeCell(
  key: MetricKey,
  label: string,
  unit: MetricCell["unit"],
  value: number | null,
  domain: DataDomain,
  source: string,
  naReason: string | null,
): MetricCell {
  return {
    key,
    label,
    value,
    display: value === null ? "N/A" : displayNumber(value, unit),
    unit,
    domain: value === null ? "UNAVAILABLE" : domain,
    source: value === null ? "NOT_AVAILABLE" : source,
    naReason: value === null ? (naReason ?? "无真实数据") : null,
  };
}

function setMetric(
  metrics: Record<MetricKey, MetricCell>,
  key: MetricKey,
  value: number | null,
  domain: DataDomain,
  source: string,
  naReason: string,
): void {
  metrics[key] = makeCell(key, LABEL_MAP[key], UNIT_MAP[key], value, domain, source, naReason);
}

function emptyMetrics(): Record<MetricKey, MetricCell> {
  const out = {} as Record<MetricKey, MetricCell>;
  for (const k of ALL_KEYS) {
    out[k] = makeCell(k, LABEL_MAP[k], UNIT_MAP[k], null, "UNAVAILABLE",
      "NOT_AVAILABLE", EMPTY_REASON);
  }
  return out;
}

// ---------------------------------------------------------------------------
// Deterministic derivations from REAL paper fields only.
// Each returns null (=> N/A) whenever any required field is absent/invalid.
// ---------------------------------------------------------------------------
function extractOrderList(ordersRaw: unknown): unknown[] | null {
  if (Array.isArray(ordersRaw)) return ordersRaw;
  if (isRecord(ordersRaw) && Array.isArray(ordersRaw.orders)) return ordersRaw.orders;
  return null;
}

function extractTradeList(...sources: unknown[]): unknown[] | null {
  for (const s of sources) {
    if (isRecord(s) && Array.isArray(s.trades)) return s.trades;
    if (isRecord(s) && Array.isArray(s.fills)) return s.fills;
  }
  return null;
}

// Both rates require every order to carry a known, explicit status string.
// One bad/missing field => the whole derivation is N/A (never partial).
function orderRates(ordersRaw: unknown): { acceptance: number | null; fill: number | null } {
  const list = extractOrderList(ordersRaw);
  if (!list || list.length === 0) return { acceptance: null, fill: null };
  const KNOWN = ["NEW", "OPEN", "PARTIALLY_FILLED", "FILLED", "REJECTED", "CANCELED"];
  let accepted = 0;
  let filled = 0;
  for (const o of list) {
    if (!isRecord(o)) return { acceptance: null, fill: null };
    const status = o.status;
    if (typeof status !== "string") return { acceptance: null, fill: null };
    const st = status.toUpperCase();
    if (!KNOWN.includes(st)) return { acceptance: null, fill: null };
    if (st !== "REJECTED") accepted++;
    const fq = finite(o.filledQty);
    if (st === "FILLED" || st === "PARTIALLY_FILLED" || (fq !== null && fq > 0)) filled++;
  }
  const n = list.length;
  return { acceptance: (accepted / n) * 100, fill: (filled / n) * 100 };
}

// Fees: prefer an explicit account summary total; otherwise sum real per-fill
// `fee` fields. A single invalid fee invalidates the whole sum (no guessing).
function tradingFees(account: unknown, ordersRaw: unknown): number | null {
  const summaryFee = pickNum(account, [
    ["summary", "totalFees"], ["summary", "tradingFees"], ["summary", "feesPaid"], ["totalFees"],
  ]);
  if (summaryFee !== null) return summaryFee;
  const trades = extractTradeList(ordersRaw, account);
  if (!trades || trades.length === 0) return null;
  let sum = 0;
  for (const t of trades) {
    if (!isRecord(t)) return null;
    const fee = finite(t.fee);
    if (fee === null) return null;
    sum += fee;
  }
  return sum;
}

// Max drawdown is computed ONLY from a real, non-empty equity snapshot array.
// Empty / missing / any unparsable point => N/A. The curve is NEVER synthesized.
function maxDrawdown(equityRaw: unknown): number | null {
  let snaps: unknown[] | null = null;
  if (Array.isArray(equityRaw)) snaps = equityRaw;
  else if (isRecord(equityRaw) && Array.isArray(equityRaw.snapshots)) snaps = equityRaw.snapshots;
  if (!snaps || snaps.length === 0) return null;
  const values: number[] = [];
  for (const s of snaps) {
    const n = pickNum(s, [["equity"], ["equityUsd"], ["totalEquity"], ["value"]]);
    if (n === null) return null;
    values.push(n);
  }
  let peak = values[0];
  let maxDD = 0;
  for (const v of values) {
    if (v > peak) peak = v;
    if (peak > 0) {
      const dd = ((peak - v) / peak) * 100;
      if (dd > maxDD) maxDD = dd;
    }
  }
  return maxDD;
}

// ---------------------------------------------------------------------------
// Static maps / constants (must be initialized before EMPTY_* below)
// ---------------------------------------------------------------------------
const ALL_KEYS: MetricKey[] = [
  "grossPnl", "netPnl", "roi", "winRate", "orderAcceptanceRate",
  "fillRate", "profitFactor", "maxDrawdown", "tradingFees",
  "slippage", "aiInferenceCost",
];

const LABEL_MAP: Record<MetricKey, string> = {
  grossPnl: "毛收益 Gross P&L",
  netPnl: "净收益 Net P&L",
  roi: "投资回报率 ROI",
  winRate: "策略胜率 Win Rate",
  orderAcceptanceRate: "订单接受率",
  fillRate: "订单成交率 Fill Rate",
  profitFactor: "盈亏因子 Profit Factor",
  maxDrawdown: "最大回撤 Max Drawdown",
  tradingFees: "交易手续费",
  slippage: "滑点成本",
  aiInferenceCost: "AI 推理成本",
};

const UNIT_MAP: Record<MetricKey, MetricCell["unit"]> = {
  grossPnl: "USD", netPnl: "USD", roi: "PCT", winRate: "PCT",
  orderAcceptanceRate: "PCT", fillRate: "PCT", profitFactor: "RATIO",
  maxDrawdown: "PCT", tradingFees: "USD", slippage: "USD", aiInferenceCost: "USD",
};

const EMPTY_REASON = "统一指标层：未接收到可验证的真实数据";

const PROVIDER_DEFS: { id: string; displayName: string }[] = [
  { id: "MiniMax", displayName: "MiniMax" },
  { id: "M3", displayName: "M3" },
  { id: "ali-bailian", displayName: "阿里百炼" },
];

const BLOCKED_NOTE = "无真实推理记录，禁止以 API 成功率冒充策略胜率";

const NULL_METRICS = (): ProviderRow["metrics"] => ({
  inferenceSuccessRate: null,
  avgLatencyMs: null,
  tokenUsage: null,
  apiCostUsd: null,
  riskRejectRate: null,
  attributedNetPnl: null,
});

export const EMPTY_SNAPSHOT: CockpitSnapshot = {
  metrics: emptyMetrics(),
  equity: { value: null, display: "N/A", domain: "UNAVAILABLE" },
  cash: { value: null, display: "N/A", domain: "UNAVAILABLE" },
  sources: [],
  warnings: [],
  generatedAtUtc: null,
};

export const EMPTY_PROVIDER_ROWS: ProviderRow[] = PROVIDER_DEFS.map((d) => ({
  id: d.id,
  displayName: d.displayName,
  verdict: "BLOCKED",
  decisionDirection: null,
  metrics: NULL_METRICS(),
  note: BLOCKED_NOTE,
}));

// ---------------------------------------------------------------------------
// Public derivations
// ---------------------------------------------------------------------------
export function deriveCockpitSnapshot(raw: CockpitRaw): CockpitSnapshot {
  try {
    const safe: CockpitRaw = isRecord(raw) ? raw : {};
    const account = unwrap(safe.account);
    const ordersRaw = unwrap(safe.orders);
    const equityRaw = unwrap(safe.equity);
    const strategies = unwrap(safe.strategies);
    const providers = unwrap(safe.providers);
    const system = unwrap(safe.system);

    const sources: string[] = [];
    if (safe.account !== undefined && safe.account !== null) sources.push("/api/jev/paper/account");
    if (safe.orders !== undefined && safe.orders !== null) sources.push("/api/jev/paper/orders");
    if (safe.events !== undefined && safe.events !== null) sources.push("/api/jev/paper/events");
    if (safe.equity !== undefined && safe.equity !== null) sources.push("/api/jev/paper/equity");
    if (safe.strategies !== undefined && safe.strategies !== null) sources.push("/api/jev/strategies/status");
    if (safe.providers !== undefined && safe.providers !== null) sources.push("/api/jev/providers/status");
    if (safe.system !== undefined && safe.system !== null) sources.push("/api/jev/system/status");

    const warnings: string[] = [];
    const stratVerdict = pickString(strategies, [["verdict"]]);
    if (stratVerdict === "NO_DATA" || stratVerdict === null) {
      warnings.push("服务端 strategies/status 返回 NO_DATA：策略绩效类指标（胜率/盈亏因子等）一律 N/A");
    }
    const provVerdict = pickString(providers, [["verdict"]]);
    const inferRecs = pickNum(providers, [["real_inference_records"]]);
    if (provVerdict === "BLOCKED" || inferRecs === null || inferRecs <= 0) {
      warnings.push("providers/status 为 BLOCKED 或无真实推理记录：AI 推理成本、归因净收益一律 N/A");
    }
    warnings.push("本系统当前无经核验的真实成交(REAL)数据；含数值的指标均为 PAPER 模拟盘，REAL 域为空");

    // ---- Account snapshot (real paper fields only) --------------------------
    const equity = pickNum(account, [
      ["summary", "equity"], ["account", "summary", "equity"], ["data", "summary", "equity"], ["equity"],
    ]);
    const cash = pickNum(account, [
      ["summary", "cash"], ["account", "summary", "cash"], ["data", "summary", "cash"], ["cash"],
    ]);
    const equityCell: CockpitSnapshot["equity"] = {
      value: equity,
      display: equity === null ? "N/A" : displayNumber(equity, "USD"),
      domain: equity === null ? "UNAVAILABLE" : "PAPER",
    };
    const cashCell: CockpitSnapshot["cash"] = {
      value: cash,
      display: cash === null ? "N/A" : displayNumber(cash, "USD"),
      domain: cash === null ? "UNAVAILABLE" : "PAPER",
    };

    // ---- Deterministic derivations ------------------------------------------
    const netPnl = pickNum(account, [
      ["summary", "totalPnl"], ["account", "summary", "totalPnl"], ["data", "summary", "totalPnl"],
      ["summary", "netPnl"], ["totalPnl"], ["summary", "realizedPnl"], ["account", "summary", "realizedPnl"],
    ]);
    const grossPnl = pickNum(account, [
      ["summary", "grossPnl"], ["summary", "gross_pnl"], ["grossPnl"],
    ]);
    // ROI baseline: the frozen engine self-reports its initial funding as
    // account.initialCash (a real server field, not a client assumption).
    const initialEquity = pickNum(account, [
      ["account", "initialCash"], ["initialCash"], ["data", "account", "initialCash"],
      ["initialEquity"], ["summary", "initialEquity"], ["startingEquity"], ["baselineEquity"],
    ]);
    let roi: number | null = null;
    if (equity !== null && initialEquity !== null && initialEquity !== 0) {
      roi = ((equity - initialEquity) / Math.abs(initialEquity)) * 100;
    }
    const rates = orderRates(ordersRaw);
    const fees = tradingFees(account, ordersRaw);
    const dd = maxDrawdown(equityRaw);
    if (dd === null) {
      warnings.push("paper/equity 无真实权益快照（空数组或缺失）：最大回撤 N/A，未合成曲线");
    }

    const metrics = emptyMetrics();
    const SRC_ACC = "/api/jev/paper/account";
    const SRC_ORD = "/api/jev/paper/orders";
    const SRC_EQ = "/api/jev/paper/equity";
    const SRC_STRAT = "/api/jev/strategies/status";

    setMetric(metrics, "grossPnl", grossPnl, "PAPER", SRC_ACC,
      "服务端 paper/account 未提供可确定性推导的毛收益字段（totalPnl 为净额，缺独立 gross 字段）");
    setMetric(metrics, "netPnl", netPnl, "PAPER", SRC_ACC,
      "paper/account 未提供 summary.totalPnl / realizedPnl 等真实净收益字段");
    setMetric(metrics, "roi", roi, "PAPER", SRC_ACC,
      "缺少 paper/account 的真实初始资金字段（account.initialCash）或当前权益（summary.equity），无法确定性计算 ROI");
    // winRate MUST stay N/A: it needs real closed-trade P&L, never API success.
    setMetric(metrics, "winRate", null, "UNAVAILABLE", SRC_STRAT,
      "需要真实已平仓成交序列；strategies/status 返回 NO_DATA，禁止以 API/推理成功率冒充策略胜率");
    setMetric(metrics, "orderAcceptanceRate", rates.acceptance, "PAPER", SRC_ORD,
      "paper/orders 未提供每个订单都带明确 status 的可核验序列，无法确定性计算接受率");
    setMetric(metrics, "fillRate", rates.fill, "PAPER", SRC_ORD,
      "paper/orders 未提供每个订单都带明确成交状态的可核验序列，无法确定性计算成交率");
    // profitFactor MUST stay N/A: no real profit/loss trade sequence exists.
    setMetric(metrics, "profitFactor", null, "UNAVAILABLE", SRC_STRAT,
      "需要真实盈亏成交序列；strategies/status 返回 NO_DATA，当前服务端无数据");
    setMetric(metrics, "maxDrawdown", dd, "PAPER", SRC_EQ,
      "paper/equity 无真实权益快照（空数组或缺失），严禁合成曲线");
    setMetric(metrics, "tradingFees", fees, "PAPER", SRC_ORD,
      "paper/orders / paper/account 未提供可确定性汇总的手续费字段");
    // slippage & aiInferenceCost have NO backing data in the current server.
    setMetric(metrics, "slippage", null, "UNAVAILABLE", "NOT_AVAILABLE",
      "当前服务端无滑点数据（无可核验的成交价/参考价序列）");
    setMetric(metrics, "aiInferenceCost", null, "UNAVAILABLE", "NOT_AVAILABLE",
      "当前服务端无 AI 推理成本数据（providers/status 为 BLOCKED，无真实推理记录）");

    const generatedAtUtc =
      pickString(system, [["generated_utc"], ["fetched_at_utc"]]) ??
      pickString(account, [["updatedAt"], ["account", "updatedAt"],
        ["meta", "generated_utc"], ["meta", "fetched_at_utc"]]) ??
      pickString(equityRaw, [["generated_utc"]]) ??
      null;

    return { metrics, equity: equityCell, cash: cashCell, sources, warnings, generatedAtUtc };
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    return {
      ...EMPTY_SNAPSHOT,
      warnings: ["统一指标层解析异常，已降级为全 N/A 空快照：" + msg],
    };
  }
}

export function deriveProviderRows(raw: CockpitRaw): ProviderRow[] {
  try {
    const safe: CockpitRaw = isRecord(raw) ? raw : {};
    const providers = unwrap(safe.providers);
    const verdict = pickString(providers, [["verdict"]]);
    const recs = pickNum(providers, [["real_inference_records"]]);
    // BLOCKED / no records / missing data => 3 honest BLOCKED rows, all N/A.
    const blocked = !isRecord(providers) || verdict === "BLOCKED" || verdict === null
      || recs === null || recs <= 0;
    if (blocked) {
      return PROVIDER_DEFS.map((d) => ({
        id: d.id,
        displayName: d.displayName,
        verdict: "BLOCKED" as const,
        decisionDirection: null,
        metrics: NULL_METRICS(),
        note: BLOCKED_NOTE,
      }));
    }
    // Verified-with-records would still need per-model real metric fields.
    // None exist in the current server, so keep every cell null (NO_DATA)
    // rather than inventing any number.
    return PROVIDER_DEFS.map((d) => ({
      id: d.id,
      displayName: d.displayName,
      verdict: "NO_DATA" as const,
      decisionDirection: null,
      metrics: NULL_METRICS(),
      note: "未找到逐模型可核验的真实推理指标字段，保持 N/A；禁止填入任何估计值",
    }));
  } catch {
    return EMPTY_PROVIDER_ROWS.map((r) => ({ ...r, metrics: NULL_METRICS() }));
  }
}
