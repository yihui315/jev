/**
 * P4 收益/风险指标：真实 Sharpe + 双口径核算的对账纯函数。
 *
 * 字段名严格对齐 server/trading_api.mjs:318-319 与
 * functions/api/jev/[[path]].js:473 的 performance 口径：
 *   net_return / max_drawdown / win_rate / profit_factor / sharpe / total_trades
 * 绝不另起第二套字段名。
 *
 * 缺失一律 null + status:"NOT_MEASURED"，绝不返回 0。
 *
 * 真实 Sharpe = mean(returns) / 样本标准差(n-1)，不年化、不用固定 sqrt(24) 代理。
 * 代理算法（scripts/day1-shadow-minimax.mjs:233）为
 *   (mean / 总体标准差(n)) * Math.sqrt(24)，且 std=0 时返回 0。
 * 两者必须不同，以证明没有用代理冒充真实 Sharpe。
 */
export const METRIC_FIELDS = Object.freeze([
  "net_return", "max_drawdown", "win_rate", "profit_factor", "sharpe", "total_trades",
]);

function measured(value) { return { value, status: "MEASURED", reason: null }; }
function missing(reason) { return { value: null, status: "NOT_MEASURED", reason }; }

export function returnsFromEquity(equityCurve) {
  if (!Array.isArray(equityCurve) || equityCurve.length < 2) return [];
  const out = [];
  for (let i = 1; i < equityCurve.length; i++) {
    const prev = equityCurve[i - 1], cur = equityCurve[i];
    if (!Number.isFinite(prev) || !Number.isFinite(cur) || prev === 0) return [];
    out.push((cur - prev) / prev);
  }
  return out;
}

/** 样本标准差（n-1）；样本不足返回 null。 */
export function sampleStdDev(values) {
  if (!Array.isArray(values) || values.length < 2) return null;
  const mean = values.reduce((a, b) => a + b, 0) / values.length;
  const variance = values.reduce((a, v) => a + (v - mean) ** 2, 0) / (values.length - 1);
  return Math.sqrt(variance);
}

/** 总体标准差（n）；代理算法使用。 */
export function populationStdDev(values) {
  if (!Array.isArray(values) || values.length < 1) return null;
  const mean = values.reduce((a, b) => a + b, 0) / values.length;
  const variance = values.reduce((a, v) => a + (v - mean) ** 2, 0) / values.length;
  return Math.sqrt(variance);
}

/** 真实 Sharpe：收益率序列样本标准差计算，缺失返回 null（绝不返回 0）。 */
export function sharpeReal(returns) {
  if (!Array.isArray(returns) || returns.length < 2) return null;
  const mean = returns.reduce((a, b) => a + b, 0) / returns.length;
  const sd = sampleStdDev(returns);
  if (sd === null || sd === 0) return null;
  return mean / sd;
}

/** 代理 Sharpe（复刻 day1-shadow-minimax.mjs:233），仅用于对比，不作绩效结论。 */
export function sharpeProxy(returns) {
  if (!Array.isArray(returns) || returns.length < 1) return 0;
  const mean = returns.reduce((a, b) => a + b, 0) / returns.length;
  const sd = populationStdDev(returns);
  if (sd === null || sd === 0) return 0;
  return (mean / sd) * Math.sqrt(24);
}

export function netReturn(equityCurve) {
  if (!Array.isArray(equityCurve) || equityCurve.length < 2) return null;
  const first = equityCurve[0], last = equityCurve[equityCurve.length - 1];
  if (!Number.isFinite(first) || !Number.isFinite(last) || first === 0) return null;
  return (last - first) / first;
}

export function maxDrawdown(equityCurve) {
  if (!Array.isArray(equityCurve) || equityCurve.length < 2) return null;
  let peak = -Infinity, dd = 0;
  for (const v of equityCurve) {
    if (!Number.isFinite(v) || v <= 0) return null;
    if (v > peak) peak = v;
    const cur = (peak - v) / peak;
    if (cur > dd) dd = cur;
  }
  return dd; // 0 = 有效测量结果「无回撤」，不是「缺失」
}

/** trades: [{ pnl: number }]；无交易 / 无亏损 -> null（绝不返回 0）。 */
export function winRate(trades) {
  if (!Array.isArray(trades) || trades.length === 0) return null;
  const wins = trades.filter((t) => Number.isFinite(t.pnl) && t.pnl > 0).length;
  return wins / trades.length;
}

export function profitFactor(trades) {
  if (!Array.isArray(trades) || trades.length === 0) return null;
  let gp = 0, gl = 0;
  for (const t of trades) if (Number.isFinite(t.pnl)) {
    if (t.pnl > 0) gp += t.pnl; else if (t.pnl < 0) gl += -t.pnl;
  }
  if (gl === 0) return null; // 无亏损：不可表达为有限 profit factor，标 NOT_MEASURED
  return gp / gl;
}

/**
 * 对账纯函数（复用 day14-verify.mjs T5a/T8 语义，但【不 import】该文件——
 * 它间接 import 了被冻结的 day14-runner.mjs）。
 * MATCH 必须由 engine 与 harness 的 delta 计算得出，绝不写死。
 */
export function reconcileEquity(engineEquity, harnessEquity) {
  const delta = engineEquity - harnessEquity;
  return { delta, reconcile: delta === 0 ? "MATCH" : "MISMATCH" };
}

/**
 * 汇总指标。返回顶层字段名对齐 performance 口径，值为 number|null；
 * measurement 记录每个字段的 MEASURED/NOT_MEASURED 状态。
 */
export function computeMetrics(input = {}) {
  const { equityCurve = [], returns = null, trades = [], cost_model_id = null } = input;
  const rs = Array.isArray(returns) ? returns : returnsFromEquity(equityCurve);
  const raw = {
    net_return: netReturn(equityCurve),
    max_drawdown: maxDrawdown(equityCurve),
    win_rate: winRate(trades),
    profit_factor: profitFactor(trades),
    sharpe: sharpeReal(rs),
    total_trades: Array.isArray(trades) && trades.length > 0 ? trades.length : null,
  };
  const out = {
    data_mode: "FIXTURE",
    evidence_level: "SYNTHETIC_FIXTURE",
    isReal: false,
    venue: "fixture",
    cost_model_id, // 单一口径
    measurement: {},
    note: "SYNTHETIC_FIXTURE 指标，非真实绩效；缺失字段为 null + NOT_MEASURED，绝不返回 0",
  };
  for (const key of METRIC_FIELDS) {
    const value = raw[key];
    const m = value === null || value === undefined
      ? missing("NO_SAMPLES_OR_NOT_MEASURABLE")
      : measured(value);
    out[key] = m.value;
    out.measurement[key] = m.status;
  }
  return out;
}
