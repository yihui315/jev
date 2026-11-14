/**
 * P3/P4 可验证引擎骨架 - 聚合导出（纯 ESM）。
 *
 * 全部为内存计算，不写任何持久化报告；本轮无真实历史行情样本，所有回测
 * 均为 SYNTHETIC_FIXTURE，绝不输出盈利排行榜 / alpha 结论 / OOS 通过。
 */
import { ENGINE_CONSERVATIVE, VENUE_VERIFIED, COST_MODELS, DEFAULT_COST_MODEL_ID, assertNoMerge } from "./cost-models.mjs";
import { computeFee, computeFeeNumber, ENGINE_FEE_RATE, assertEngineFeeContract } from "./fee-wrap.mjs";
import { generateTimeline, TIMELINE_EVIDENCE, SAMPLE_FOUNDATION_NOTE } from "./market-timeline.mjs";
import { buildWalkForward, REQUIRED_FOLDS, FOLDS_SOURCE } from "./walk-forward.mjs";
import {
  computeMetrics, reconcileEquity, sharpeReal, sharpeProxy, METRIC_FIELDS,
  returnsFromEquity, sampleStdDev, populationStdDev,
  netReturn, maxDrawdown, winRate, profitFactor,
} from "./metrics.mjs";
import { run as runHoldCash, ARM_ID as A_ID } from "./arms/hold-cash.mjs";
import { run as runBuyAndHold, ARM_ID as B_ID } from "./arms/buy-and-hold.mjs";
import { run as runTrend, ARM_ID as C_ID, SMA_FAST, SMA_SLOW } from "./arms/trend.mjs";
import { run as runAiDecisions, ARM_ID as D_ID } from "./arms/ai-decisions.mjs";

export const SYNTHETIC_DISCLAIMER = "本轮无真实历史行情样本，所有回测为 SYNTHETIC_FIXTURE";

export {
  ENGINE_CONSERVATIVE, VENUE_VERIFIED, COST_MODELS, DEFAULT_COST_MODEL_ID, assertNoMerge,
  computeFee, computeFeeNumber, ENGINE_FEE_RATE, assertEngineFeeContract,
  generateTimeline, TIMELINE_EVIDENCE, SAMPLE_FOUNDATION_NOTE,
  buildWalkForward, REQUIRED_FOLDS, FOLDS_SOURCE,
  computeMetrics, reconcileEquity, sharpeReal, sharpeProxy, METRIC_FIELDS,
  returnsFromEquity, sampleStdDev, populationStdDev,
  netReturn, maxDrawdown, winRate, profitFactor,
  runHoldCash, runBuyAndHold, runTrend, runAiDecisions,
  A_ID, B_ID, C_ID, D_ID, SMA_FAST, SMA_SLOW,
};

export const ARMS = Object.freeze([
  { arm_id: A_ID, run: runHoldCash },
  { arm_id: B_ID, run: runBuyAndHold },
  { arm_id: C_ID, run: runTrend },
  { arm_id: D_ID, run: runAiDecisions },
]);

/**
 * 四组对照聚合：共享同一 timeline_sha256 与同一个 cost_model_id。
 * 任一臂 NO_DATA -> 整体 INSUFFICIENT_DATA，且【不输出】排名/胜出策略/alpha 结论。
 * 即便四臂全部 OK，本骨架也不计算排名或 alpha（需真实 OOS 样本，见 HANDOFF）。
 */
export function runFourArms(timeline, options = {}) {
  const costModelId = options.cost_model_id || DEFAULT_COST_MODEL_ID;
  const arms = ARMS.map(({ run }) => run(timeline, { ...options, cost_model_id: costModelId }));
  const anyNoData = arms.some((a) => a.status !== "OK");
  const base = {
    status: anyNoData ? "INSUFFICIENT_DATA" : "OK",
    data_mode: "FIXTURE",
    evidence_level: "SYNTHETIC_FIXTURE",
    isReal: false,
    venue: "fixture",
    seed: timeline.seed,
    timeline_sha256: timeline.timeline_sha256,
    no_synthetic_data: false,
    cost_model_id: costModelId,
    sample_foundation_note: timeline.sample_foundation_note,
    bar_count: timeline.bars,
    arms,
    note: SYNTHETIC_DISCLAIMER,
  };
  if (anyNoData) {
    // 不输出 ranking / winner / alpha（连键都不出现）
    base.no_ranking = true;
    base.insufficient_reason = arms.filter((a) => a.status !== "OK")
      .map((a) => `${a.arm_id}:${a.reason}`).join(",");
  }
  // 排名/胜出/alpha 结论在本骨架中一律不生成（两分支均不含这些键）
  return base;
}
