/**
 * 四组对照 A：HOLD / CASH，零成交。
 * 账户始终保持冻结引擎 createAccount() 的初始状态；权益每 bar 由
 * __test.summarize 计算得出（不是把净值写死成某个固定常量），fills 恒为空。
 */
import { armResult, makeAccount, equityRecord } from "./_kit.mjs";

export const ARM_ID = "A_HOLD_CASH";

export function run(timeline, options = {}) {
  const costModelId = options.cost_model_id;
  const account = makeAccount();
  const equity = timeline.series.map((bar) => equityRecord(timeline, account, bar, costModelId));
  return armResult(timeline, ARM_ID, costModelId, {
    status: "OK",
    equity,
    fills: [],
    note: "A：HOLD/CASH，零成交；权益由冻结引擎 summarize 计算（非硬编码），SYNTHETIC_FIXTURE 非绩效结论",
  });
}
