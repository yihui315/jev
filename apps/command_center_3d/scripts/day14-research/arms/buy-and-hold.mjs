/**
 * 四组对照 B：首 bar 买入并持有到末。
 * 买入量按初始现金固定比例确定性计算（无随机）；手续费复刻冻结引擎
 * computeFee 语义；每 bar 权益由 __test.summarize 计算得出。
 */
import { armResult, makeAccount, equityRecord, buyAtMark } from "./_kit.mjs";

export const ARM_ID = "B_BUY_AND_HOLD";

export function run(timeline, options = {}) {
  const costModelId = options.cost_model_id;
  const account = makeAccount();
  const fills = [];
  const equity = [];
  if (timeline.series.length > 0) {
    const first = timeline.series[0];
    const initialCash = Number(account.cash); // 来自冻结引擎 createAccount，非硬编码
    const qty = Math.round((initialCash * 0.10 / first.close) * 1e6) / 1e6;
    if (qty > 0) {
      fills.push(buyAtMark(account, timeline, costModelId, ARM_ID, first, qty, first.i));
    }
  }
  for (const bar of timeline.series) {
    equity.push(equityRecord(timeline, account, bar, costModelId));
  }
  return armResult(timeline, ARM_ID, costModelId, {
    status: "OK",
    equity,
    fills,
    note: "B：首 bar 买入并持有到末；成交手续费复刻冻结引擎 computeFee，SYNTHETIC_FIXTURE",
  });
}
