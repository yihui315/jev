/**
 * P4 双口径核算 - 权威数据契约（DATA CONTRACT RECORD）。
 *
 * 本文件是「记录」，不是 day14-runner.mjs 代码的复制；它只声明两套成本口径的
 * 权威定义与 fail-closed 校验规则，供回测骨架引用。
 *
 * 口径一 ENGINE_CONSERVATIVE：
 *   - provenance: COMMUNITY-VERIFIED，但 status 明确为「需实测 / NOT venue-measured」。
 *   - feeRate 0.001 对应冻结引擎 CFG.feeRate（paper-server.mjs:10）。
 *   - slippagePct 0.015 是【规划估算】，绝不能当成冻结引擎参数：
 *       冻结引擎没有任何显式滑点参数；滑点隐含在 walkBook() 逐档撮合
 *       （paper-server.mjs:293-315，按盘口 asks/bids 逐档吃单）之中。
 *       把 0.015 说成「引擎实测滑点」即为伪造。
 *
 * 口径二 VENUE_VERIFIED：
 *   - provenance: UNVERIFIED，status: NOT_MEASURED，费率/滑点均为 null。
 *   - 只有拿到交易所实测证据后才能填数；本轮没有任何真实成交样本，保持 null。
 *
 * 纪律：两套口径绝不合并。任何一条结果记录只能带一个 cost_model_id。
 */
export const ENGINE_CONSERVATIVE = Object.freeze({
  cost_model_id: "engine_conservative",
  feeRate: 0.001,
  slippagePct: 0.015,
  provenance: "COMMUNITY-VERIFIED",
  status: "需实测 / NOT venue-measured",
  slippagePct_is_planning_estimate: true,
  slippagePct_note: "0.015 是规划估算，不是冻结引擎参数；引擎无显式滑点参数，滑点隐含在 walkBook 逐档撮合中",
  feeRate_source: "paper-server.mjs __test.CFG.feeRate = \"0.001\"",
});

export const VENUE_VERIFIED = Object.freeze({
  cost_model_id: "venue_verified",
  feeRate: null,
  slippagePct: null,
  provenance: "UNVERIFIED",
  status: "NOT_MEASURED",
});

export const COST_MODELS = Object.freeze({
  engine_conservative: ENGINE_CONSERVATIVE,
  venue_verified: VENUE_VERIFIED,
});

export const DEFAULT_COST_MODEL_ID = ENGINE_CONSERVATIVE.cost_model_id;

// ---- fail-closed 校验 ----

function collectResultRecords(report) {
  if (Array.isArray(report)) return report;
  if (report && Array.isArray(report.records)) return report.records;
  return [report];
}

function declaredIds(rec) {
  const ids = new Set();
  if (typeof rec.cost_model_id === "string") ids.add(rec.cost_model_id);
  if (Array.isArray(rec.cost_model_id)) {
    for (const x of rec.cost_model_id) if (typeof x === "string") ids.add(x);
  }
  if (Array.isArray(rec.cost_model_ids)) {
    for (const x of rec.cost_model_ids) if (typeof x === "string") ids.add(x);
  }
  // 一条结果记录里同时拼接两个口径的数值块 = 合并
  for (const key of ["engine_conservative", "venue_verified"]) {
    const block = rec[key];
    if (block && typeof block === "object" &&
        (Object.prototype.hasOwnProperty.call(block, "feeRate") ||
         Object.prototype.hasOwnProperty.call(block, "slippagePct"))) {
      ids.add(key);
    }
  }
  return [...ids];
}

function hasVerifiedProof(rec) {
  if (rec.venue_measured === true) return true;
  if (typeof rec.provenance === "string" && rec.provenance.toUpperCase() === "VERIFIED") return true;
  const e = rec.evidence || rec.provenance_evidence;
  if (e && typeof e === "object" &&
      (e.level === "VERIFIED" || e.verified === true || e.venue_measured === true)) return true;
  return false;
}

function checkVenueFabrication(rec, tag) {
  const fee = rec.feeRate === undefined ? null : rec.feeRate;
  const slip = rec.slippagePct === undefined ? null : rec.slippagePct;
  // (3) 把 engine_conservative 的数值回填进 venue_verified —— 无论是否声称 VERIFIED 都拒绝
  if (fee === ENGINE_CONSERVATIVE.feeRate || slip === ENGINE_CONSERVATIVE.slippagePct) {
    throw new Error(`COST_MODEL_MERGE_REJECTED:${tag}:ENGINE_VALUES_BACKFILLED_INTO_VENUE_VERIFIED`);
  }
  // (2) venue_verified 出现非 null 数值，但没有 VERIFIED 实测证据
  const nonNull = fee !== null || slip !== null;
  if (nonNull && !hasVerifiedProof(rec)) {
    throw new Error(`COST_MODEL_MERGE_REJECTED:${tag}:VENUE_VERIFIED_NON_NULL_WITHOUT_VERIFIED_EVIDENCE`);
  }
}

/**
 * 检查一份回测结果报告是否违反了「双口径绝不合并」：
 *   (1) 任一结果记录含多个 cost_model_id / 同时拼接两个口径数值块 -> 抛错
 *   (2) venue_verified 出现非 null 费率/滑点但无 VERIFIED 证据 -> 抛错
 *   (3) 把 engine_conservative 的数值回填进 venue_verified -> 抛错
 * 合法输入返回 true；任何违规一律 throw（fail-closed），绝不静默放行。
 */
export function assertNoMerge(report) {
  if (report === null || typeof report !== "object") {
    throw new Error("COST_MODEL_MERGE_REJECTED:REPORT_NOT_OBJECT");
  }
  const records = collectResultRecords(report);
  for (let i = 0; i < records.length; i++) {
    const rec = records[i];
    const tag = `record[${i}]`;
    if (!rec || typeof rec !== "object") continue;
    const ids = declaredIds(rec);
    // (1) 单一口径纪律
    if (ids.length > 1) {
      throw new Error(`COST_MODEL_MERGE_REJECTED:${tag}:MULTIPLE_COST_MODELS(${ids.join("|")})`);
    }
    const id = ids[0] || null;
    if (id === "venue_verified") checkVenueFabrication(rec, tag);
    // 记录自带的 venue_verified 嵌套块（即使没有单条 id）也校验
    const nested = rec.venue_verified;
    if (nested && typeof nested === "object" && id !== "venue_verified") {
      checkVenueFabrication({ ...nested, provenance: nested.provenance ?? rec.provenance }, `${tag}.venue_verified`);
    }
  }
  // 配置容器形态：costModels.venue_verified 也不得被回填 / 伪造数值
  const container = report.costModels || report.models || null;
  if (container && typeof container === "object") {
    const vv = container.venue_verified;
    if (vv && typeof vv === "object") {
      checkVenueFabrication({ ...vv, provenance: vv.provenance }, "costModels.venue_verified");
    }
  }
  return true;
}
