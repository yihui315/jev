/**
 * 四组对照 D：回放真实 AI 决策日志（decisions.jsonl，provenance isReal 为真）。
 *
 * 硬裁定：仓库当前没有任何 isReal 为真 的真实 AI 决策日志（现存 decisions.jsonl
 * 均为 kind:"synthetic" / aiKind:"mock" 的 fixture），因此默认直接返回
 * { status:"NO_DATA", reason:"NO_REAL_AI_DECISION_LOG" }。
 * 绝对禁止合成/编造 AI 决策来凑四组对照（synthetic_decisions_generated 恒为 0）。
 *
 * decision-schema.mjs 明确：data_mode 由 server 持久化时赋值，模型/本骨架
 * 不得自称 real；故这里只接受显式 isReal 为真 且 data_mode/provenance 为 real 的记录。
 */
import { armResult, makeAccount, equityRecord, buyAtMark, sellAtMark } from "./_kit.mjs";

export const ARM_ID = "D_AI_DECISIONS";

export function run(timeline, options = {}) {
  const costModelId = options.cost_model_id;
  const decisions = options.decisions || null;
  const real = Array.isArray(decisions)
    ? decisions.filter((d) => d && d.isReal === true &&
        (d.data_mode === "real" || d.provenance === "real" || d.evidence_level === "REAL_AI_DECISION"))
    : [];

  if (!Array.isArray(decisions) || real.length === 0) {
    return armResult(timeline, ARM_ID, costModelId, {
      status: "NO_DATA",
      reason: "NO_REAL_AI_DECISION_LOG",
      equity: [],
      fills: [],
      decisions_replayed: 0,
      synthetic_decisions_generated: 0, // 明确 0：绝不合成 AI 决策
      note: "仓库当前没有 provenance isReal 为真 的 decisions.jsonl；直接 NO_DATA，绝不合成 AI 决策凑四组对照",
    });
  }

  // 真实回放路径（本轮不会触发）：按 decision 到达顺序对齐 bar 索引，确定性撮合。
  // 真实接入时应按 decision 时间戳对齐 bar（见 HANDOFF 遗留项）。
  const account = makeAccount();
  const fills = [];
  const equity = [];
  const byBar = new Map();
  real.forEach((d, k) => {
    const idx = k % timeline.series.length;
    byBar.set(idx, (byBar.get(idx) || []).concat(d));
  });
  for (const bar of timeline.series) {
    for (const d of (byBar.get(bar.i) || [])) {
      if (d.action === "BUY" && Number(d.quantity) > 0) {
        fills.push(buyAtMark(account, timeline, costModelId, ARM_ID, bar, Number(d.quantity), bar.i));
      } else if (d.action === "SELL") {
        const f = sellAtMark(account, timeline, costModelId, ARM_ID, bar, bar.i);
        if (f) fills.push(f);
      }
    }
    equity.push(equityRecord(timeline, account, bar, costModelId));
  }
  return armResult(timeline, ARM_ID, costModelId, {
    status: "OK",
    equity,
    fills,
    decisions_replayed: real.length,
    synthetic_decisions_generated: 0,
    note: "回放真实 decisions.jsonl（isReal 为真）于 SYNTHETIC_FIXTURE 行情，非真实绩效",
  });
}
