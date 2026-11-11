/**
 * 四组对照 C：确定性趋势策略（固定 SMA 参数，无随机、无参数优化）。
 * 金叉（fast 上穿 slow）且空仓 -> 买入；死叉且持仓 -> 平仓。
 * 手续费/权益计算复用 _kit（冻结引擎语义），信号完全由 timeline 决定。
 */
import { armResult, makeAccount, equityRecord, buyAtMark, sellAtMark } from "./_kit.mjs";

export const ARM_ID = "C_TREND_SMA";
export const SMA_FAST = 5;
export const SMA_SLOW = 20;

function sma(values, end, len) {
  if (end < 0 || end + 1 < len) return null;
  let s = 0;
  for (let i = end - len + 1; i <= end; i++) s += values[i];
  return s / len;
}

export function run(timeline, options = {}) {
  const costModelId = options.cost_model_id;
  const account = makeAccount();
  const fills = [];
  const equity = [];
  const closes = timeline.series.map((b) => b.close);
  const initialCash = Number(account.cash);
  const tradeQty = (c) => Math.round((initialCash * 0.05 / c) * 1e6) / 1e6;
  let held = 0;
  for (let i = 0; i < timeline.series.length; i++) {
    const bar = timeline.series[i];
    const fast = sma(closes, i, SMA_FAST);
    const slow = sma(closes, i, SMA_SLOW);
    const prevFast = sma(closes, i - 1, SMA_FAST);
    const prevSlow = sma(closes, i - 1, SMA_SLOW);
    if (fast !== null && slow !== null && prevFast !== null && prevSlow !== null) {
      const goldenCross = prevFast <= prevSlow && fast > slow;
      const deathCross = prevFast >= prevSlow && fast < slow;
      if (goldenCross && held === 0) {
        const q = tradeQty(bar.close);
        if (q > 0) {
          fills.push(buyAtMark(account, timeline, costModelId, ARM_ID, bar, q, bar.i));
          held = 1;
        }
      } else if (deathCross && held === 1) {
        const f = sellAtMark(account, timeline, costModelId, ARM_ID, bar, bar.i);
        if (f) fills.push(f);
        held = 0;
      }
    }
    equity.push(equityRecord(timeline, account, bar, costModelId));
  }
  return armResult(timeline, ARM_ID, costModelId, {
    status: "OK",
    equity,
    fills,
    note: "C：固定 SMA(5,20) 趋势策略，确定性无随机；SYNTHETIC_FIXTURE 非绩效结论",
  });
}
