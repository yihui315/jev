// JEV V1.6 / P2 — 多模型执行编排（注入式、纯内存、fail-closed）。
//
// 关键语义（MASTER PLAN V2.0 §4/P2）：
//   任一模型超时 / 抛错 / ok:false / 解析失败 → 整体 decisionClass="NO_ACTION"、
//   failClosedReason="PROVIDER_FAILED"、execution="NONE"、orderable=false，
//   绝不改写为模型主动 HOLD。成功模型的 evidence 仍须保留，不得因部分失败丢弃。
//
// 反面教材：agent-orchestrator.mjs:269/345-349 把 4 个 LLM 角色 usage 累加成
//   单个聚合对象。本模块禁止任何形式的跨模型 token / cost 累加：每个模型只产出
//   一条独立 evidence，返回结果不含任何聚合 usage / cost 字段。
//
// 本层是纯证据层，恒 isReal:false / dataMode:"fixture" / providerId:"mock"，
// 不下单、不接线 runner（接线推迟到 P3 解冻）。不 import 任何凭据模块、不发起网络请求。
// 决策期 modelId 权威来源是 runtime.mjs:providerRegistryFromEnv（仅在 index.mjs 声明，
// 本模块不 import、不复制其清单，不假装已接线）。

import {
  buildInferenceEvidence,
  FIXTURE_PROMPT_VERSION,
  FIXTURE_MARKET_SNAPSHOT_HASH,
  DEFAULT_RISK_VERDICT,
  MOCK_PROVIDER_ID,
} from "./inference-evidence.mjs";

export const MULTI_MODEL_RESULT_SCHEMA_VERSION = "jev.multi-model.run/v1";
export const DEFAULT_TIMEOUT_MS = 1000;
const HEX64_RE = /^[0-9a-fA-F]{64}$/;

function isPlainObject(v) {
  return !!v && typeof v === "object" && !Array.isArray(v);
}
function isNonEmptyString(v) {
  return typeof v === "string" && v.trim().length > 0;
}

function describeError(err) {
  const e = err && typeof err === "object" ? err : {};
  return {
    name: isNonEmptyString(e.name) ? String(e.name).slice(0, 64) : "Error",
    message: isNonEmptyString(e.message) ? String(e.message).slice(0, 200) : "provider call failed",
    code: isNonEmptyString(e.code) ? String(e.code).slice(0, 64) : "PROVIDER_ERROR",
    timedOut: e.code === "TIMEOUT",
  };
}

/** 带硬超时的 Promise 包装；同步抛错也会被转成 rejection。 */
function withTimeout(promise, ms, modelId) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => {
      const err = new Error("provider timeout for modelId=" + modelId);
      err.code = "TIMEOUT";
      err.modelId = modelId;
      reject(err);
    }, ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

/**
 * 默认纯内存 fixture provider。不触网、不读凭据、不伪造真实模型调用或账单。
 * token 用量与推理成本一律「未实测」（null + NOT_MEASURED / UNVERIFIED + 诚实空值 note）。
 */
export function defaultFixtureProvider(modelId, opts = {}) {
  const nowMs = Number.isFinite(opts.nowMs) ? opts.nowMs : 0;
  const raw = JSON.stringify({
    jevEvent: "FIXTURE_INFERENCE",
    note: "in-memory fixture; no LLM call, no network, no bill, no order",
    providerId: MOCK_PROVIDER_ID,
    modelId,
    nowMs,
    decision: { action: "HOLD", reason: "fixture deterministic hold" },
  });
  return {
    ok: true,
    parseFailed: false,
    rawResponseBytes: raw,
    parsedDecision: { action: "HOLD", reason: "fixture deterministic hold", confidence: null },
    tokenUsage: { prompt: null, completion: null, total: null, provenance: "NOT_MEASURED" },
    // 故意不提供 actualInferenceCost：由 buildInferenceEvidence 落为 UNVERIFIED。
  };
}

function resolveEntry(raw) {
  const entry = typeof raw === "string" ? { modelId: raw } : isPlainObject(raw) ? raw : {};
  const modelId = isNonEmptyString(entry.modelId) ? entry.modelId : null;
  const promptVersion = isNonEmptyString(entry.promptVersion) ? entry.promptVersion : FIXTURE_PROMPT_VERSION;
  const marketSnapshotHash = isNonEmptyString(entry.marketSnapshotHash)
    ? entry.marketSnapshotHash
    : FIXTURE_MARKET_SNAPSHOT_HASH;
  // marketContextId 成对 P1 锚点：仅注入消费，本层不自造第二套重放锚点；缺失写 null + note。
  const marketContextId = isNonEmptyString(entry.marketContextId) ? entry.marketContextId : null;
  // 未评估/未实测的 riskVerdict 写 null（诚实空值），不塞字符串占位。
  const riskVerdict = isNonEmptyString(entry.riskVerdict) ? entry.riskVerdict : DEFAULT_RISK_VERDICT;
  return { modelId, promptVersion, marketSnapshotHash, marketContextId, riskVerdict };
}

/**
 * 把 providerImpl 的返回值规整成「字段一定齐全」的形态，确保
 * buildInferenceEvidence 不会因编排层缺字段而抛错。失败时把错误事件本身作为
 * 原始响应字节留存（诚实、可核验），绝不伪造模型输出。
 */
function normalizeProviderResult(got, ctx) {
  const base = isPlainObject(got) ? got : {};
  const err = describeError(base.__providerError);
  const ok = base.ok === true;
  const parseFailed = base.parseFailed === true;
  const parsedDecision = isPlainObject(base.parsedDecision) ? base.parsedDecision : null;
  let raw = base.rawResponseBytes;
  let hasRaw = false;
  if (typeof raw === "string") hasRaw = raw.length > 0;
  else if (raw instanceof Uint8Array) hasRaw = raw.length > 0;
  else if (Array.isArray(raw)) hasRaw = raw.length > 0;
  if (!hasRaw) {
    raw = JSON.stringify({
      jevEvent: ok ? "RAW_RESPONSE_MISSING_OR_EMPTY" : "PROVIDER_FAILURE_NO_RAW_RESPONSE",
      modelId: ctx.modelId,
      ok,
      parseFailed,
      error: err,
      note: "此字节记录的是失败/缺失事件，不是模型输出；禁止据此产生交易意图",
    });
  }
  return {
    ok,
    parseFailed,
    rawResponseBytes: raw,
    parsedDecision,
    tokenUsage: base.tokenUsage,
    actualInferenceCost: base.actualInferenceCost,
    // 透传 hash_scope / marketContextId / 自称的 providerId / dataMode，供证据层
    // 校验（快照 scope 守卫）与「不采信真实标记」约束；绝不自造清单。
    hash_scope: base.marketSnapshotHashScope ?? base.hash_scope,
    marketContextId: base.marketContextId ?? ctx.marketContextId,
    providerId: base.providerId,
    dataMode: base.dataMode,
  };
}

/**
 * 多模型执行。返回整体判定 + 每个模型一条独立 evidence。
 *
 * @param {object} p
 * @param {Array<string|object>} p.models   modelId 列表，或
 *        {modelId, promptVersion?, marketSnapshotHash?, marketContextId?, riskVerdict?} 对象。
 * @param {Function} p.providerImpl  注入实现：(modelId, {timeoutMs, nowMs}) => providerResult
 * @param {number} p.timeoutMs
 * @param {number} p.nowMs
 */
export async function runMultiModel(p = {}) {
  const params = isPlainObject(p) ? p : {};
  if (!Array.isArray(params.models) || params.models.length === 0) {
    throw new TypeError("runMultiModel: models 必须是非空数组");
  }
  if (params.providerImpl !== undefined && typeof params.providerImpl !== "function") {
    throw new TypeError("runMultiModel: providerImpl 必须是函数");
  }
  // nowMs 由调用方注入以保证确定性；缺失时退化为 0（非真实时间戳，仅是兜底）。
  const capturedAtMs = Number.isFinite(params.nowMs) ? params.nowMs : 0;
  const timeoutMs = Number.isFinite(params.timeoutMs) && params.timeoutMs > 0
    ? params.timeoutMs
    : DEFAULT_TIMEOUT_MS;
  const impl = typeof params.providerImpl === "function" ? params.providerImpl : defaultFixtureProvider;

  const entries = params.models.map(resolveEntry);
  // 系统边界校验：modelId 与 marketSnapshotHash 必须合法，否则拒绝整个 run（fail-closed）。
  for (const e of entries) {
    if (!e.modelId) throw new TypeError("runMultiModel: 存在缺失或非法的 modelId");
    if (!HEX64_RE.test(e.marketSnapshotHash)) {
      throw new TypeError("runMultiModel: marketSnapshotHash 必须为 64 位 hex");
    }
  }

  const evidence = [];
  for (const ctx of entries) {
    let got;
    try {
      got = await withTimeout(
        Promise.resolve().then(() => impl(ctx.modelId, { timeoutMs, nowMs: capturedAtMs })),
        timeoutMs,
        ctx.modelId,
      );
    } catch (err) {
      got = { ok: false, parseFailed: true, rawResponseBytes: null, __providerError: err };
    }
    const providerResult = normalizeProviderResult(got, ctx);
    const record = buildInferenceEvidence({
      providerResult,
      modelId: ctx.modelId,
      promptVersion: ctx.promptVersion,
      marketSnapshotHash: ctx.marketSnapshotHash,
      marketContextId: ctx.marketContextId,
      riskVerdict: ctx.riskVerdict,
      nowMs: capturedAtMs,
    });
    evidence.push(record);
  }

  const providerFailureCount = evidence.filter(
    (e) => e.decisionClass === "NO_ACTION" && e.failClosedReason === "PROVIDER_FAILED",
  ).length;
  const anyProviderFailure = providerFailureCount > 0;
  const allApproved = evidence.length > 0 && evidence.every((e) => e.decisionClass === "APPROVED");

  return {
    schemaVersion: MULTI_MODEL_RESULT_SCHEMA_VERSION,
    capturedAtMs,
    modelCount: entries.length,
    providerFailureCount,
    decisionClass: anyProviderFailure ? "NO_ACTION" : (allApproved ? "APPROVED" : "NO_ACTION"),
    failClosedReason: anyProviderFailure ? "PROVIDER_FAILED" : null,
    execution: "NONE",
    orderable: false,
    evidence,
    isReal: false,
    dataMode: "fixture",
    providerId: MOCK_PROVIDER_ID,
  };
}
