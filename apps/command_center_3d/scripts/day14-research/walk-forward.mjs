/**
 * P3 Walk-forward：锚定扩展窗口（anchored expanding window）的 SYNTHETIC 演练。
 *
 * REQUIRED_FOLDS = 3，沿用 apps/command_center_3d/server/engine/_lib/verification.mjs:19
 * 的既有常量语义（仅注明出处并按值定义；按主理人限制，本目录只可 import 冻结引擎
 * __test 与 decimal.mjs，故不 import verification.mjs）。
 *
 * 纪律：不打乱、不泄漏未来 —— 每折严格 trainEnd < testStart（bar 索引）。
 * 输出标签 SYNTHETIC_OOS_DRILL，oos_pass:0，verified:0，
 * counts_toward_real_oos_gate:false；本轮绝不提前宣称 OOS 通过。
 */
export const REQUIRED_FOLDS = 3;
export const FOLDS_SOURCE = "apps/command_center_3d/server/engine/_lib/verification.mjs:19 REQUIRED_FOLDS=3";

export function buildWalkForward(timeline, options = {}) {
  const folds = Number.isInteger(options.folds) ? options.folds : REQUIRED_FOLDS;
  if (folds !== REQUIRED_FOLDS) {
    throw new Error(`WALK_FORWARD_FOLDS_MISMATCH: 需要 ${REQUIRED_FOLDS} 折，收到 ${folds}`);
  }
  const n = timeline.bars;
  const minTrain = Math.max(1, Math.floor(n * 0.20));
  const testLen = Math.max(1, Math.floor(n * 0.10));
  // 容量校验：minTrain + folds*testLen <= n
  if (minTrain + folds * testLen > n) {
    throw new Error(`WALK_FORWARD_INSUFFICIENT_BARS: bars=${n} 不足以构造 ${folds} 折无泄漏窗口`);
  }

  const foldList = [];
  let trainEnd = minTrain - 1; // 最后一个训练 bar 的索引
  for (let k = 0; k < folds; k++) {
    const testStart = trainEnd + 1; // 严格 > trainEnd
    const testEnd = testStart + testLen - 1;
    foldList.push({
      fold: k + 1,
      trainStart: 0, // 锚定：起点恒为 0
      trainEnd,
      testStart,
      testEnd,
      train_bars: trainEnd - 0 + 1,
      test_bars: testLen,
      anchored: true,
      expanding_window: true,
      trainEnd_lt_testStart: trainEnd < testStart,
      data_mode: "FIXTURE",
      evidence_level: "SYNTHETIC_FIXTURE",
      isReal: false,
      venue: "fixture",
      no_synthetic_data: false,
      timeline_sha256: timeline.timeline_sha256,
    });
    trainEnd = testEnd; // 扩展窗口：下一折训练纳入本折之前的全部数据（皆为「过去」）
  }

  return {
    label: "SYNTHETIC_OOS_DRILL",
    oos_pass: 0,
    verified: 0,
    counts_toward_real_oos_gate: false,
    REQUIRED_FOLDS,
    fold_count: foldList.length,
    folds_source: FOLDS_SOURCE,
    sample_foundation_note: timeline.sample_foundation_note,
    data_mode: "FIXTURE",
    evidence_level: "SYNTHETIC_FIXTURE",
    isReal: false,
    venue: "fixture",
    no_synthetic_data: false,
    seed: timeline.seed,
    timeline_sha256: timeline.timeline_sha256,
    note: "本轮无真实历史行情样本，所有回测为 SYNTHETIC_FIXTURE；不计入真实 OOS 门",
    folds: foldList,
  };
}
