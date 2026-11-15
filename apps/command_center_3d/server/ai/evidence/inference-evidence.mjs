// JEV V1.6 / P2 — AI 多模型决策「原始证据留存与溯源层」。
//
// 定位：与冻结的 runner / decision-schema / risk-gate / paper engine 完全解耦的
// 独立证据层。本模块只依赖 Node 标准库，不发起任何网络请求、不读取任何凭据、
// 不 import ai-core.mjs / provider-adapter.mjs / key-store.mjs / runtime.mjs。
//
// 硬约束（MASTER PLAN V2.0 / R-2 / R-4 + 主理人增量裁定）：
//   * 纯增量：本文件为新建，不改动任何 tracked 源码；与 runner 的接线推迟到 P3 解冻。
//   * 所有外部输入（provider 结果、时钟、市场快照 hash、风控结论）一律依赖注入。
//   * 推理成本三口径分列：本层只保留「实测」口径 actualInferenceCost；未实测恒为
//     amount:null + provenance:"UNVERIFIED" + evidence_level:"UNVERIFIED"，
//     绝不用 estCostUsd / state.costUsd / 预算值回填，绝不跨模型累加。
//   * 诚实空值：复用 trading_api.mjs:299 既有契约 —— 未实测字段一律 null +
//     note:"UNKNOWN, never 0. No real inference has been executed."，禁止 0 / 空对象 /
//     预算值回填；覆盖 tokenUsage / actualInferenceCost / parsedDecision / riskVerdict /
//     marketContextId。
//   * 本层为离线证据层：所有记录恒为 isReal:false / dataMode:"fixture" /
//     providerId:"mock"（docs/JEV_AI_PROVIDER_API.md:116，data_mode 由服务器写），
//     严禁标 LIVE / REAL，严禁用真实 modelId 冒充 real 推理。
//   * hash_scope：marketSnapshotHash 只消费 P1 定义 raw_upstream_bytes（成对
//     marketContextId），rawResponseHash 独立指原始响应字节；绝不把 redacted_response /
//     canonical_input 等口径平移成快照哈希，不造第二套重放锚点。
//   * fail-closed：缺任一必填字段、原始响应为空、marketSnapshotHash 非 64 位 hex、
//     riskVerdict 完全缺失 / 空串 → 抛错，绝不产出「部分证据」。

import { createHash } from "node:crypto";
import { readFileSync, appendFileSync, existsSync } from "node:fs";

export const INFERENCE_EVIDENCE_SCHEMA_VERSION = "jev.inference.evidence/v1";
export const GENESIS = "GENESIS";

// 明确标记的 fixture 默认上下文。hash 是对固定字符串求得的确定性 64 位 hex，
// 可被任何人独立复算；它不是真实市场快照，配合 isReal:false / dataMode:"fixture" 使用。
export const FIXTURE_PROMPT_VERSION = "jev.p2.evidence.fixture/v1";
// 诚实空值：未评估/未实测的风控结论写 null，不塞字符串占位（trading_api.mjs:299 口径）。
export const DEFAULT_RISK_VERDICT = null;
export const FIXTURE_MARKET_SNAPSHOT_HASH = createHash("sha256")
  .update("JEV_P2_EVIDENCE_FIXTURE_MARKET_SNAPSHOT_V1", "utf8")
  .digest("hex");

export const DECISION_CLASSES = Object.freeze(["APPROVED", "NO_ACTION"]);
export const FAIL_CLOSED_REASONS = Object.freeze(["PROVIDER_FAILED", "RISK_NOT_APPROVED"]);
export const PROVENANCE_UNVERIFIED = "UNVERIFIED";
export const PROVENANCE_NOT_MEASURED = "NOT_MEASURED";

// —— 复用仓库既有口径，只声明、不新造 ——
// trading_api.mjs:299 agentsStatus() 的诚实空值 note 字面。
export const HONEST_EMPTY_NOTE = "UNKNOWN, never 0. No real inference has been executed.";
// docs/JEV_AI_PROVIDER_API.md:116 mock 模式 provider_id 强制字面量。
export const MOCK_PROVIDER_ID = "mock";
// 消费 P1 定义：快照哈希口径 raw_upstream_bytes，成对 marketContextId。
export const HASH_SCOPE_MARKET_SNAPSHOT = "raw_upstream_bytes";
// 原始响应字节口径，独立于快照哈希。
export const HASH_SCOPE_RAW_RESPONSE = "raw_response_bytes";
// 绝不允许平移成快照哈希的其它口径（含脱敏响应 / 规范化输入 / 响应字节）。
export const FORBIDDEN_SNAPSHOT_SCOPES = Object.freeze([
  "redacted_response", "canonical_input", "canonical_structured",
  "canonical_whole_row", "whole_snapshot_json", HASH_SCOPE_RAW_RESPONSE,
]);
// docs/JEV_AI_PROVIDER_API.md:70 列表态 verdict 允许值。
export const LIST_VERDICT_STATES = Object.freeze(["BLOCKED", "UNVERIFIED"]);
// docs/JEV_AI_PROVIDER_API.md:72/77 0 样本绩效口径。
export const NO_DATA = "NO_DATA";

const HEX64_RE = /^[0-9a-fA-F]{64}$/;

// 未实测推理成本的固定形态。冻结，避免任何调用方就地改写。
export const UNVERIFIED_ACTUAL_COST = Object.freeze({
  amount: null,
  currency: null,
  provenance: PROVENANCE_UNVERIFIED,
  evidence_level: PROVENANCE_UNVERIFIED,
  note: HONEST_EMPTY_NOTE,
});

function isPlainObject(v) {
  return !!v && typeof v === "object" && !Array.isArray(v);
}

function isNonEmptyString(v) {
  return typeof v === "string" && v.trim().length > 0;
}

/** sha256(bytes|string) → 64 位小写 hex。 */
export function sha256Hex(input) {
  const bytes = toBytes(input);
  return createHash("sha256").update(bytes).digest("hex");
}

/** 把任意输入归一化为字节；无法归一或为空时返回 null。 */
function toBytes(raw) {
  if (raw == null) return null;
  if (typeof raw === "string") {
    const b = new TextEncoder().encode(raw);
    return b.length ? b : null;
  }
  if (raw instanceof Uint8Array) return raw.length ? raw : null;
  if (Array.isArray(raw)) {
    const b = Uint8Array.from(raw);
    return b.length ? b : null;
  }
  return null;
}

/** 稳定 JSON（键排序、递归），用于哈希与链记录；与任何第三方库无关。 */
export function canonicalize(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return "[" + value.map(canonicalize).join(",") + "]";
  const keys = Object.keys(value).sort();
  return "{" + keys.map((k) => JSON.stringify(k) + ":" + canonicalize(value[k])).join(",") + "}";
}

// ---- Provider 状态口径（只声明、不接线、不触网、不读凭据）------------------
// 复用 docs/JEV_AI_PROVIDER_API.md:70-72/119 与 trading_api.mjs agentsStatus 语义。
// 这些是「口径声明 + 可验证纯函数」，不是第五套 Provider 清单（清单权威见 index.mjs）。

/** 列表态 GET /providers：verdict 只可能是 BLOCKED / UNVERIFIED，绝不返回 CONNECTED。 */
export function providerListVerdict(opts = {}) {
  const { credentialPresent = false } = opts;
  return credentialPresent ? "UNVERIFIED" : "BLOCKED";
}

/** 单次 POST /providers/test：仅真实 200 + 有内容时 CONNECTED；无凭据 BLOCKED；其余 UNVERIFIED。 */
export function providerTestStatus(opts = {}) {
  const { credentialPresent = false, httpStatus = null, hasContent = false } = opts;
  if (!credentialPresent) return "BLOCKED";
  if (httpStatus === 200 && hasContent === true) return "CONNECTED";
  return "UNVERIFIED";
}

/** 0 样本绩效 → NO_DATA，绝不返回 0；有样本也不在本层自定绩效结论。 */
export function performanceVerdict(opts = {}) {
  const { sampleCount = 0 } = opts;
  return sampleCount > 0 ? "UNVERIFIED" : "NO_DATA";
}

/**
 * 成本口径守卫：拒绝任何「合并 / 多口径 / 跨模型累加」对象。
 * 命中即抛错（fail-closed），绝不把预算 / 估算 / 实测混进一个金额。
 */
export function assertNoCostMerge(raw) {
  if (raw == null) return;
  if (!isPlainObject(raw)) {
    throw new TypeError("actualInferenceCost 必须是对象或 null");
  }
  if (raw.merged === true || raw.aggregated === true || raw.isAggregate === true) {
    throw new Error("COST_MERGE_REJECTED: 多口径/跨模型合并的成本对象不被接受");
  }
  if (raw.scope === "MULTI_MODEL" || raw.scope === "MERGED") {
    throw new Error("COST_MERGE_REJECTED: 成本作用域不可为 MULTI_MODEL/MERGED");
  }
  if (Array.isArray(raw.sources) && raw.sources.length > 1) {
    throw new Error("COST_MERGE_REJECTED: 成本来源数组不得含多个来源");
  }
  if (Array.isArray(raw.models) && raw.models.length > 1) {
    throw new Error("COST_MERGE_REJECTED: 成本不得跨模型累加");
  }
  const MIX_KEYS = [
    "budgetUsd", "estimatedUsd", "measuredUsd", "budget", "estimated", "measured",
    "estCostUsd", "stateCostUsd", "actualCostUsd",
  ];
  const present = MIX_KEYS.filter((k) => k in raw);
  if (present.length > 1) {
    throw new Error("COST_MERGE_REJECTED: 单个成本对象内不得同时出现多个口径字段 " + present.join(","));
  }
}

/**
 * 归一化「实测推理成本」。P2 阶段无真实账单来源，故除命中严格 MEASURED 条件外，
 * 一律返回 UNVERIFIED 常量——尤其绝不回填 raw.amount（可能来自预算/估算/0）。
 */
export function normalizeActualInferenceCost(raw) {
  assertNoCostMerge(raw);
  if (
    isPlainObject(raw) &&
    raw.evidence_level === "MEASURED" &&
    isNonEmptyString(raw.provenance) &&
    typeof raw.amount === "number" &&
    Number.isFinite(raw.amount) &&
    raw.amount >= 0
  ) {
    // 预留给 P3 解冻后经可信账单注入的实测值；P2 的纯内存 fixture 不会命中此分支。
    return {
      amount: raw.amount,
      currency: isNonEmptyString(raw.currency) ? raw.currency : null,
      provenance: raw.provenance,
      evidence_level: "MEASURED",
      note: null,
    };
  }
  return { ...UNVERIFIED_ACTUAL_COST };
}

function unknownTokenUsage() {
  return {
    prompt: null,
    completion: null,
    total: null,
    provenance: PROVENANCE_NOT_MEASURED,
    note: HONEST_EMPTY_NOTE,
  };
}

function normalizeTokenUsage(raw) {
  // 缺失 / 空对象 → 诚实空值，绝不产生空对象或 0。
  if (!isPlainObject(raw) || Object.keys(raw).length === 0) return unknownTokenUsage();
  const pick = (a, b) => {
    const v = a !== undefined ? a : b;
    return typeof v === "number" && Number.isFinite(v) ? v : null;
  };
  const prompt = pick(raw.prompt, raw.prompt_tokens);
  const completion = pick(raw.completion, raw.completion_tokens);
  const total = pick(raw.total, raw.total_tokens);
  // 任一分量为 0 / null → 全部按「未实测」处理，绝不填 0、绝不用 total 反推。
  if (prompt === 0 || completion === 0 || total === 0) return unknownTokenUsage();
  if (prompt === null || completion === null || total === null) return unknownTokenUsage();
  const provenance = isNonEmptyString(raw.provenance) ? raw.provenance : PROVENANCE_NOT_MEASURED;
  return { prompt, completion, total, provenance, note: null };
}

function classify(providerResult, riskVerdict) {
  const providerOk =
    providerResult.ok === true &&
    providerResult.parseFailed !== true &&
    isPlainObject(providerResult.parsedDecision);
  if (!providerOk) {
    return { decisionClass: "NO_ACTION", failClosedReason: "PROVIDER_FAILED" };
  }
  if (riskVerdict === "APPROVED") {
    return { decisionClass: "APPROVED", failClosedReason: null };
  }
  return { decisionClass: "NO_ACTION", failClosedReason: "RISK_NOT_APPROVED" };
}

/**
 * 构造一条推理证据。任一必填项不满足即抛错（fail-closed），绝不产出部分证据。
 *
 * @param {object} p
 * @param {object} p.providerResult  注入的 provider 原始结果，需含 rawResponseBytes、
 *                                   ok、parsedDecision、tokenUsage（可选）等。
 * @param {string} p.modelId
 * @param {string} p.promptVersion
 * @param {string} p.marketSnapshotHash  64 位 hex，口径 raw_upstream_bytes（消费 P1）
 * @param {string|null} p.riskVerdict  非空字符串或显式 null（诚实空值）；undefined/空串抛错
 * @param {string} [p.marketContextId]  成对 P1 锚点（注入，缺失写 null + note）
 * @param {number} p.nowMs            注入时钟（确定性）
 */
export function buildInferenceEvidence(p) {
  const params = isPlainObject(p) ? p : {};
  const providerResult = isPlainObject(params.providerResult) ? params.providerResult : null;
  const { modelId, promptVersion, marketSnapshotHash, riskVerdict, nowMs, marketContextId } = params;

  if (!providerResult) throw new TypeError("EVIDENCE_FAILCLOSED: providerResult 缺失");
  if (!isNonEmptyString(modelId)) throw new TypeError("EVIDENCE_FAILCLOSED: modelId 缺失");
  if (!isNonEmptyString(promptVersion)) throw new TypeError("EVIDENCE_FAILCLOSED: promptVersion 缺失");
  if (!isNonEmptyString(marketSnapshotHash) || !HEX64_RE.test(marketSnapshotHash)) {
    throw new TypeError("EVIDENCE_FAILCLOSED: marketSnapshotHash 必须为 64 位 hex");
  }
  // riskVerdict 完全未传 / 空串 → fail-closed 抛错；显式 null → 诚实空值（允许）。
  if (riskVerdict === undefined || riskVerdict === "") {
    throw new TypeError("EVIDENCE_FAILCLOSED: riskVerdict 缺失");
  }
  if (riskVerdict !== null && !isNonEmptyString(riskVerdict)) {
    throw new TypeError("EVIDENCE_FAILCLOSED: riskVerdict 必须为非空字符串或 null");
  }
  if (typeof nowMs !== "number" || !Number.isFinite(nowMs)) {
    throw new TypeError("EVIDENCE_FAILCLOSED: nowMs 必须为有限数字");
  }

  // hash_scope 守卫：快照哈希只允许 P1 的 raw_upstream_bytes，
  // 绝不把脱敏响应 / 规范化输入 / 原始响应字节等口径平移成快照哈希。
  const declaredScope = providerResult.marketSnapshotHashScope
    ?? providerResult.hash_scope
    ?? params.marketSnapshotHashScope
    ?? HASH_SCOPE_MARKET_SNAPSHOT;
  if (declaredScope !== HASH_SCOPE_MARKET_SNAPSHOT) {
    throw new Error(
      "SNAPSHOT_SCOPE_FORBIDDEN: marketSnapshotHash 仅允许 raw_upstream_bytes，拒绝 " + String(declaredScope),
    );
  }

  const bytes = toBytes(providerResult.rawResponseBytes);
  if (!bytes) throw new TypeError("EVIDENCE_FAILCLOSED: 原始响应为空，拒绝产出部分证据");

  const rawResponseHash = createHash("sha256").update(bytes).digest("hex");
  // rawResponseBase64 留存的是原始响应字节的 base64 完整编码，不是字节数、不是长度、不是解析后对象；
  // 真实字节数由 Buffer.from(x,"base64").length 得出。第三方可用 verifyRawResponseHash() /
  // decodeRawResponse() 独立重算核验。
  const rawResponseBase64 = Buffer.from(bytes).toString("base64");
  const parsedDecision = isPlainObject(providerResult.parsedDecision)
    ? JSON.parse(canonicalize(providerResult.parsedDecision))
    : null;
  const { decisionClass, failClosedReason } = classify(providerResult, riskVerdict);

  const tokenUsage = normalizeTokenUsage(providerResult.tokenUsage);
  const actualInferenceCost = normalizeActualInferenceCost(providerResult.actualInferenceCost);
  const resolvedMarketContextId = isNonEmptyString(marketContextId)
    ? marketContextId
    : (isNonEmptyString(providerResult.marketContextId) ? providerResult.marketContextId : null);

  const hasHonestEmpty =
    parsedDecision === null ||
    riskVerdict === null ||
    resolvedMarketContextId === null ||
    tokenUsage.note !== null ||
    actualInferenceCost.note !== null;
  const note = hasHonestEmpty ? HONEST_EMPTY_NOTE : null;

  return {
    schemaVersion: INFERENCE_EVIDENCE_SCHEMA_VERSION,
    modelId,
    promptVersion,
    marketSnapshotHash,
    marketContextId: resolvedMarketContextId,
    hashScopes: {
      marketSnapshotHash: HASH_SCOPE_MARKET_SNAPSHOT,
      rawResponseHash: HASH_SCOPE_RAW_RESPONSE,
    },
    rawResponseHash,
    rawResponseBase64,
    parsedDecision,
    tokenUsage,
    actualInferenceCost,
    riskVerdict,
    decisionClass,
    failClosedReason,
    capturedAtMs: nowMs,
    isReal: false,
    dataMode: "fixture",
    providerId: MOCK_PROVIDER_ID,
    note,
  };
}

/** 第三方独立核验：对留存的原始字节重算 sha256，比对 rawResponseHash。 */
export function verifyRawResponseHash(record) {
  if (!isPlainObject(record) || !isNonEmptyString(record.rawResponseBase64) || !isNonEmptyString(record.rawResponseHash)) {
    return false;
  }
  let bytes;
  try {
    bytes = Buffer.from(record.rawResponseBase64, "base64");
  } catch {
    return false;
  }
  return createHash("sha256").update(bytes).digest("hex") === record.rawResponseHash;
}

/** 解码留存的原始响应字节，供独立复核。 */
export function decodeRawResponse(record) {
  if (!isPlainObject(record) || !isNonEmptyString(record.rawResponseBase64)) return null;
  return Uint8Array.from(Buffer.from(record.rawResponseBase64, "base64"));
}

// ---- append-only JSONL 证据链 ----------------------------------------------

/** 链哈希：对「含 _prev、不含 _hash」的记录做稳定序列化后 sha256。 */
export function computeChainHash(recordWithPrev) {
  const copy = { ...recordWithPrev };
  delete copy._hash;
  return createHash("sha256").update(canonicalize(copy), "utf8").digest("hex");
}

/** 纯内存链校验。返回 {valid, errors, tip, count}。 */
export function verifyChain(records) {
  const errors = [];
  if (!Array.isArray(records)) {
    return { valid: false, errors: [{ code: "NOT_ARRAY" }], tip: GENESIS, count: 0 };
  }
  let prev = GENESIS;
  let tip = GENESIS;
  for (let i = 0; i < records.length; i += 1) {
    const line = records[i];
    if (!isPlainObject(line)) {
      errors.push({ index: i, code: "LINE_NOT_OBJECT" });
      continue;
    }
    if (i === 0) {
      if (line._prev !== GENESIS) errors.push({ index: i, code: "GENESIS_MISMATCH" });
    } else if (line._prev !== prev) {
      errors.push({ index: i, code: "PREV_MISMATCH" });
    }
    if (!isNonEmptyString(line._hash) || !HEX64_RE.test(line._hash)) {
      errors.push({ index: i, code: "INVALID_HASH" });
    } else if (computeChainHash(line) !== line._hash) {
      errors.push({ index: i, code: "HASH_MISMATCH" });
    }
    if (isNonEmptyString(line._hash)) {
      tip = line._hash;
      prev = line._hash;
    }
  }
  return { valid: errors.length === 0, errors, tip, count: records.length };
}

/** 读文件并全量校验链；空文件/不存在视为合法空链（tip=GENESIS）。 */
export function loadAndVerifyChain(filePath) {
  let text = "";
  if (existsSync(filePath)) text = readFileSync(filePath, "utf8");
  const lines = text.split("\n").filter((l) => l.trim().length > 0);
  const records = [];
  for (let i = 0; i < lines.length; i += 1) {
    let parsed;
    try {
      parsed = JSON.parse(lines[i]);
    } catch {
      return { valid: false, errors: [{ index: i, code: "LINE_PARSE_ERROR" }], tip: GENESIS, records: [] };
    }
    records.push(parsed);
  }
  const result = verifyChain(records);
  return { ...result, records };
}

/** 追加一条证据到 JSONL。写前全量校验链，链已损坏则抛错（fail-closed）。 */
export function appendEvidence(filePath, record) {
  if (!isPlainObject(record)) throw new TypeError("appendEvidence: record 必须是对象");
  const chain = loadAndVerifyChain(filePath);
  if (!chain.valid) throw new Error("EVIDENCE_CHAIN_INVALID: 证据链校验失败，拒绝追加");
  const line = { ...record, _prev: chain.tip };
  line._hash = computeChainHash(line);
  appendFileSync(filePath, canonicalize(line) + "\n", "utf8");
  return line;
}
