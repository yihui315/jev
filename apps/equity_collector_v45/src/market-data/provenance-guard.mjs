/**
 * P1 正式数据层 — Market Provenance Guard（强制门控，默认 fail-closed）。
 *
 * 对「任意数据记录」做门控。无放行捷径：任何可疑 / 矛盾 / 缺失一律
 * { approved:false, code, reasons }。通过仅返回 { approved:true, code:null, reasons:[] }。
 *
 * 门控项：
 *   - provenance 缺失 / 字段缺失或类型错；
 *   - venue 不在白名单；
 *   - symbol 非法；
 *   - isReal 与 dataMode 矛盾（含「fixture 被标 real / LIVE」——关键红线）；
 *   - 声明为 NOT_MEASURED 的实测值却填了数字（含 0 / 假值）；
 *   - 声明为 MEASURED 的实测值却是 null / 非有限数（反向矛盾）；
 *   - 哈希溯源信封：hash_scope 非 raw_upstream_bytes / 禁用别名 /
 *     market_context_id 缺失 / store_kind 或 db_persisted 伪造落库 /
 *     marketSnapshotHash 非 sha256 hex（team-lead 追加裁定）。
 *
 * 约定（缺失实测值）：写 null + 标记 "NOT_MEASURED"，严禁填 0 或数字。
 *   - provenance.notMeasuredFields?: string[]      字段名清单
 *   - record.measurementStatus?: { [field]: "MEASURED" | "NOT_MEASURED" }
 *   - 取值来源：record.measurements?.[field] ?? record[field]
 *
 * 仅 Node stdlib；纯函数；无 IO、无网络、无时钟读取。所有策略可注入覆盖。
 */
import { createHash } from "node:crypto";

export const GUARD_CODES = Object.freeze({
  PROVENANCE_MISSING: "PROVENANCE_MISSING",
  PROVENANCE_FIELD_MISSING: "PROVENANCE_FIELD_MISSING",
  PROVENANCE_FIELD_INVALID: "PROVENANCE_FIELD_INVALID",
  VENUE_NOT_ALLOWLISTED: "VENUE_NOT_ALLOWLISTED",
  SYMBOL_INVALID: "SYMBOL_INVALID",
  DATAMODE_UNKNOWN: "DATAMODE_UNKNOWN",
  ISREAL_DATAMODE_CONFLICT: "ISREAL_DATAMODE_CONFLICT",
  FIXTURE_MISLABELED_LIVE: "FIXTURE_MISLABELED_LIVE",
  MEASURED_VALUE_FABRICATED: "MEASURED_VALUE_FABRICATED",
  MEASURED_VALUE_MISSING: "MEASURED_VALUE_MISSING",
  // 哈希溯源信封门控
  HASH_SCOPE_MISSING: "HASH_SCOPE_MISSING",
  HASH_SCOPE_UNKNOWN: "HASH_SCOPE_UNKNOWN",
  HASH_SCOPE_FORBIDDEN: "HASH_SCOPE_FORBIDDEN",
  HASH_ALIAS_FORBIDDEN: "HASH_ALIAS_FORBIDDEN",
  HASH_ALGORITHM_INVALID: "HASH_ALGORITHM_INVALID",
  MARKET_CONTEXT_ID_MISSING: "MARKET_CONTEXT_ID_MISSING",
  STORE_BINDING_INVALID: "STORE_BINDING_INVALID",
  MARKET_SNAPSHOT_HASH_INVALID: "MARKET_SNAPSHOT_HASH_INVALID",
  INTERNAL_ERROR: "INTERNAL_ERROR",
});

export const DATA_MODES = Object.freeze({
  FIXTURE: "fixture",
  LIVE: "live",
  REAL: "real",
  VENUE_VERIFIED: "venue_verified",
});

/** fixture 口径：isReal 必须为 false。 */
export const FIXTURE_MODES = Object.freeze([DATA_MODES.FIXTURE]);
/** 真实口径：isReal 必须为 true（P1 不产出，但供 P5 一致性校验）。 */
export const REAL_MODES = Object.freeze([
  DATA_MODES.LIVE, DATA_MODES.REAL, DATA_MODES.VENUE_VERIFIED,
]);
/** 任何含 LIVE / real 字样的口径，禁止与 fixture 指示器共存。 */
const LIVE_LABELS = Object.freeze(["live", "real"]);

/** 七项必填 provenance 字段。 */
export const PROVENANCE_FIELDS = Object.freeze([
  "venue", "symbol", "capturedAtMs", "source", "isReal", "dataMode", "captureMode",
]);

/** 默认白名单：与 collector 的 VENUE_SUPPORTED_SYMBOLS 一致，但本模块自持常量保持独立。 */
export const DEFAULT_VENUE_ALLOWLIST = Object.freeze([
  "Binance.US spot (official public REST v3)",
  "LOCAL_FIXTURE",
]);
export const DEFAULT_SYMBOL_ALLOWLIST = Object.freeze(["BTCUSDT", "ETHUSDT"]);

// ---- 哈希溯源字段权威契约（team-lead 追加裁定）--------------------------------
/** hash_scope 枚举（5 值登记）；本轮默认且仅允许 raw_upstream_bytes。 */
export const HASH_SCOPES = Object.freeze({
  RAW_UPSTREAM_BYTES: "raw_upstream_bytes",
  CANONICAL_ROW: "canonical_row",
  CANONICAL_INPUT: "canonical_input",
  REDACTED_RESPONSE: "redacted_response",
  SOURCE_FILE: "source_file",
});
export const ALLOWED_HASH_SCOPE = HASH_SCOPES.RAW_UPSTREAM_BYTES;
export const HASH_ALGORITHM = "sha256";
/** 严禁当作「快照原始字节哈希」的别名（脱敏响应 / canonicalize 决策输入）。 */
export const FORBIDDEN_HASH_ALIASES = Object.freeze([
  "response_payload_sha256", "input_snapshot_sha256",
]);
/** 权威命名契约：运行时字段 marketSnapshotHash；market_snapshot_sha256 仅保留为未来落库列名。 */
export const MARKET_SNAPSHOT_HASH_CONTRACT = Object.freeze({
  runtimeField: "marketSnapshotHash",
  reservedDbColumn: "market_snapshot_sha256",
  reservedDbColumnStatus: "DECLARED_NOT_WIRED",
  algorithm: HASH_ALGORITHM,
  allowedScopeThisRound: HASH_SCOPES.RAW_UPSTREAM_BYTES,
  note: "market_snapshot_sha256 is ONLY a future DB-column naming contract; not wired, no DB writes this round.",
});
/** 未来接线目标：本轮不写 ai_market_contexts D1 表、不建 0007 迁移、不碰数据库。 */
export const AI_MARKET_CONTEXTS_BINDING = "DECLARED_NOT_WIRED";

/** 默认 sha256（信封哈希专用；与链 hashImpl 解耦，可注入覆盖）。 */
function envelopeHashImpl(str) {
  return createHash("sha256").update(String(str)).digest("hex");
}

/** 稳定序列化：对象键排序、数组保序，用于确定性信封哈希（私有，不与回放模块冲突）。 */
function stableStringify(value) {
  if (value === null || value === undefined) return "null";
  if (typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return "[" + value.map(stableStringify).join(",") + "]";
  const keys = Object.keys(value).sort();
  return "{" + keys.map((k) => JSON.stringify(k) + ":" + stableStringify(value[k])).join(",") + "}";
}

const DENY = (code, reasons) => ({ approved: false, code, reasons });
const APPROVE = () => ({ approved: true, code: null, reasons: [] });

/**
 * 构造 P1 本地 fixture 的 provenance：恒 isReal:false / dataMode:"fixture"。
 * 缺失实测字段用 notMeasuredFields 声明（调用方须把对应值置 null）。
 */
export function buildFixtureProvenance({
  venue = "LOCAL_FIXTURE",
  symbol,
  capturedAtMs,
  source = "jev-p1-local-fixture",
  captureMode = "local-fixture",
  notMeasuredFields = [],
} = {}) {
  return {
    venue,
    symbol,
    capturedAtMs,
    source,
    isReal: false,
    dataMode: DATA_MODES.FIXTURE,
    captureMode,
    notMeasuredFields: [...notMeasuredFields],
  };
}

/** 浅层形状检查：provenance 存在且七项字段齐备、基础类型正确。 */
export function hasProvenanceShape(record) {
  if (!record || typeof record !== "object" || Array.isArray(record)) return false;
  const p = record.provenance;
  if (!p || typeof p !== "object" || Array.isArray(p)) return false;
  for (const f of PROVENANCE_FIELDS) {
    if (!Object.prototype.hasOwnProperty.call(p, f)) return false;
  }
  if (typeof p.venue !== "string" || p.venue.length === 0) return false;
  if (typeof p.symbol !== "string" || p.symbol.length === 0) return false;
  if (typeof p.capturedAtMs !== "number" || !Number.isFinite(p.capturedAtMs)) return false;
  if (typeof p.source !== "string" || p.source.length === 0) return false;
  if (typeof p.isReal !== "boolean") return false;
  if (typeof p.dataMode !== "string" || p.dataMode.length === 0) return false;
  if (typeof p.captureMode !== "string" || p.captureMode.length === 0) return false;
  return true;
}

function readMeasuredValue(record, field) {
  if (record.measurements && typeof record.measurements === "object" &&
      Object.prototype.hasOwnProperty.call(record.measurements, field)) {
    return record.measurements[field];
  }
  return record[field];
}

function isNumericFill(v) {
  return typeof v === "number"; // 含 0 / NaN —— 声明 NOT_MEASURED 却出现数字即视为伪造
}

function collectNotMeasured(record) {
  const out = new Set();
  const p = record.provenance || {};
  if (Array.isArray(p.notMeasuredFields)) {
    for (const f of p.notMeasuredFields) if (typeof f === "string") out.add(f);
  }
  const status = record.measurementStatus;
  if (status && typeof status === "object" && !Array.isArray(status)) {
    for (const [f, s] of Object.entries(status)) {
      if (s === "NOT_MEASURED") out.add(f);
    }
  }
  return out;
}

function collectMeasured(record) {
  const out = new Set();
  const status = record.measurementStatus;
  if (status && typeof status === "object" && !Array.isArray(status)) {
    for (const [f, s] of Object.entries(status)) {
      if (s === "MEASURED") out.add(f);
    }
  }
  return out;
}

// ---- 哈希溯源信封：派生 / 构造 / 校验 --------------------------------------

/** market_context_id 派生：fixture 前缀 mc_fx_，真实前缀 mctx-。 */
export function deriveMarketContextId({ isReal = false, recordId, hashImpl = envelopeHashImpl } = {}) {
  const prefix = isReal ? "mctx-" : "mc_fx_";
  return prefix + hashImpl("market-context|" + String(recordId)).slice(0, 16);
}

/** payload_byte_range：真实上游字节范围未测，用 null + NOT_MEASURED（禁止填 0/假值）。 */
function defaultPayloadByteRange() {
  return {
    hash_scope: HASH_SCOPES.RAW_UPSTREAM_BYTES,
    unit: "byte",
    local_stand_in: "canonical_local_record",
    real_upstream_start_byte: null,
    real_upstream_end_byte: null,
    real_upstream_status: "NOT_MEASURED",
    source_kind: "fixture",
    note: "P1 fixture: no real upstream payload; real upstream byte range is NOT_MEASURED (null).",
  };
}

/**
 * 构造 / 补全哈希溯源信封字段，贯穿 snapshot / bar / orderbook / replay 四类记录。
 * 已存在的字段一律保留（尤其让非法 hash_scope 透传到门控被拒，而非被默认值覆盖）。
 * ctx: { hashImpl, isReal, recordId, marketContextId }。
 */
export function buildMarketEnvelope(record, ctx = {}) {
  const {
    hashImpl = envelopeHashImpl,
    isReal = false,
    recordId = "unknown",
    marketContextId = null,
  } = ctx;
  const base = { ...record };
  if (!Object.prototype.hasOwnProperty.call(base, "record_id"))
    base.record_id = String(recordId);
  if (base.hash_scope === undefined) base.hash_scope = ALLOWED_HASH_SCOPE;
  if (base.hash_algorithm === undefined) base.hash_algorithm = HASH_ALGORITHM;
  if (base.store_kind === undefined) base.store_kind = "file";
  if (base.db_persisted === undefined) base.db_persisted = false;
  if (base.market_context_id === undefined)
    base.market_context_id = marketContextId ||
      deriveMarketContextId({ isReal, recordId: base.record_id, hashImpl });
  if (base.payload_byte_range === undefined) base.payload_byte_range = defaultPayloadByteRange();
  // marketSnapshotHash = sha256(规范化记录，排除自身)，确定性、无时钟/随机。
  const { marketSnapshotHash: _omitted, ...hashless } = base;
  if (base.marketSnapshotHash === undefined)
    base.marketSnapshotHash = hashImpl(stableStringify(hashless));
  return base;
}

/** 校验信封：默认 fail-closed，返回 { ok, code, reasons }。 */
export function validateMarketEnvelope(record) {
  const deny = (code, msg) => ({ ok: false, code, reasons: [msg] });
  if (!record || typeof record !== "object" || Array.isArray(record))
    return deny(GUARD_CODES.PROVENANCE_MISSING, "record is not an object");
  // 禁止把脱敏响应 / canonicalize 决策输入当作快照原始字节哈希
  for (const a of FORBIDDEN_HASH_ALIASES) {
    if (Object.prototype.hasOwnProperty.call(record, a))
      return deny(GUARD_CODES.HASH_ALIAS_FORBIDDEN, `forbidden hash alias '${a}' must not stand in for raw-bytes snapshot hash`);
  }
  const scope = record.hash_scope;
  if (scope === undefined || scope === null)
    return deny(GUARD_CODES.HASH_SCOPE_MISSING, "hash_scope missing (must be raw_upstream_bytes this round)");
  if (!Object.values(HASH_SCOPES).includes(scope))
    return deny(GUARD_CODES.HASH_SCOPE_UNKNOWN, `hash_scope '${scope}' unknown`);
  if (scope !== ALLOWED_HASH_SCOPE)
    return deny(GUARD_CODES.HASH_SCOPE_FORBIDDEN, `hash_scope '${scope}' forbidden this round (only ${ALLOWED_HASH_SCOPE})`);
  if (record.hash_algorithm !== HASH_ALGORITHM)
    return deny(GUARD_CODES.HASH_ALGORITHM_INVALID, "hash_algorithm must be sha256");
  if (typeof record.market_context_id !== "string" || record.market_context_id.length === 0)
    return deny(GUARD_CODES.MARKET_CONTEXT_ID_MISSING, "market_context_id missing");
  if (record.store_kind !== "file" || record.db_persisted !== false)
    return deny(GUARD_CODES.STORE_BINDING_INVALID, "store_kind must be 'file' and db_persisted false (no DB this round)");
  if (typeof record.marketSnapshotHash !== "string" ||
      !/^[0-9a-f]{64}$/.test(record.marketSnapshotHash))
    return deny(GUARD_CODES.MARKET_SNAPSHOT_HASH_INVALID, "marketSnapshotHash must be 64-char lowercase sha256 hex");
  return { ok: true, code: null, reasons: [] };
}

/**
 * 强制门控。options 可注入：venueAllowlist / symbolAllowlist（默认见上）。
 * 默认 fail-closed：任何异常都返回 approved:false。
 */
export function guardRecord(record, options = {}) {
  try {
    const venueAllowlist = options.venueAllowlist || DEFAULT_VENUE_ALLOWLIST;
    const symbolAllowlist = options.symbolAllowlist || DEFAULT_SYMBOL_ALLOWLIST;
    const reasons = [];

    if (!record || typeof record !== "object" || Array.isArray(record))
      return DENY(GUARD_CODES.PROVENANCE_MISSING, ["record is not an object"]);
    const p = record.provenance;
    if (!p || typeof p !== "object" || Array.isArray(p))
      return DENY(GUARD_CODES.PROVENANCE_MISSING, ["record.provenance missing"]);

    // 1) 七项必填字段
    for (const f of PROVENANCE_FIELDS) {
      if (!Object.prototype.hasOwnProperty.call(p, f)) {
        reasons.push(`provenance.${f} missing`);
        return DENY(GUARD_CODES.PROVENANCE_FIELD_MISSING, reasons);
      }
    }
    // 2) 字段类型
    const invalid = [];
    if (typeof p.venue !== "string" || !p.venue) invalid.push("venue");
    if (typeof p.symbol !== "string" || !p.symbol) invalid.push("symbol");
    if (typeof p.capturedAtMs !== "number" || !Number.isFinite(p.capturedAtMs)) invalid.push("capturedAtMs");
    if (typeof p.source !== "string" || !p.source) invalid.push("source");
    if (typeof p.isReal !== "boolean") invalid.push("isReal");
    if (typeof p.dataMode !== "string" || !p.dataMode) invalid.push("dataMode");
    if (typeof p.captureMode !== "string" || !p.captureMode) invalid.push("captureMode");
    if (invalid.length) {
      reasons.push(`invalid provenance fields: ${invalid.join(",")}`);
      return DENY(GUARD_CODES.PROVENANCE_FIELD_INVALID, reasons);
    }

    // 3) venue 白名单
    if (!venueAllowlist.includes(p.venue)) {
      reasons.push(`venue '${p.venue}' not in allowlist`);
      return DENY(GUARD_CODES.VENUE_NOT_ALLOWLISTED, reasons);
    }
    // 4) symbol 合法
    if (!symbolAllowlist.includes(p.symbol)) {
      reasons.push(`symbol '${p.symbol}' invalid`);
      return DENY(GUARD_CODES.SYMBOL_INVALID, reasons);
    }

    // 5) dataMode 必须属于已知口径
    const mode = p.dataMode;
    const isFixtureMode = FIXTURE_MODES.includes(mode);
    const isRealMode = REAL_MODES.includes(mode);
    if (!isFixtureMode && !isRealMode) {
      reasons.push(`dataMode '${mode}' unknown`);
      return DENY(GUARD_CODES.DATAMODE_UNKNOWN, reasons);
    }

    // 6) isReal 与 dataMode 一致性矩阵
    if (isFixtureMode && p.isReal !== false) {
      reasons.push(`dataMode='${mode}' requires isReal:false, got ${p.isReal}`);
      return DENY(GUARD_CODES.ISREAL_DATAMODE_CONFLICT, reasons);
    }
    if (isRealMode && p.isReal !== true) {
      reasons.push(`dataMode='${mode}' requires isReal:true, got ${p.isReal}`);
      return DENY(GUARD_CODES.ISREAL_DATAMODE_CONFLICT, reasons);
    }

    // 7) 关键红线：fixture 指示器 + 任何 LIVE/real 标签 => 一律拒绝
    //    （禁止把合成数据贴 LIVE 标签；含 isReal:true / dataMode 含 live|real 字样 / source 含 live 字样）
    const fixtureIndicator =
      isFixtureMode ||
      /fixture|local/i.test(p.captureMode || "") ||
      /fixture|local/i.test(p.source || "");
    const liveIndicator =
      p.isReal === true ||
      LIVE_LABELS.some((l) => String(mode).toLowerCase().includes(l)) ||
      /live|real/i.test(p.source || "");
    if (fixtureIndicator && liveIndicator) {
      reasons.push("fixture/local record carries a LIVE/real label — forbidden");
      return DENY(GUARD_CODES.FIXTURE_MISLABELED_LIVE, reasons);
    }

    // 8) 实测值缺失却填数字 / MEASURED 却为空 —— 双向矛盾
    for (const f of collectNotMeasured(record)) {
      const v = readMeasuredValue(record, f);
      if (isNumericFill(v)) {
        reasons.push(`field '${f}' declared NOT_MEASURED but filled with number ${v}`);
        return DENY(GUARD_CODES.MEASURED_VALUE_FABRICATED, reasons);
      }
    }
    for (const f of collectMeasured(record)) {
      const v = readMeasuredValue(record, f);
      if (v === null || v === undefined || typeof v !== "number" || !Number.isFinite(v)) {
        reasons.push(`field '${f}' declared MEASURED but value is missing/non-finite`);
        return DENY(GUARD_CODES.MEASURED_VALUE_MISSING, reasons);
      }
    }

    // 9) 哈希溯源信封（放在最后，保持前述错误码优先级；非法 hash_scope 在此被拒）
    const env = validateMarketEnvelope(record);
    if (!env.ok) return DENY(env.code, env.reasons);

    return APPROVE();
  } catch (e) {
    return DENY(GUARD_CODES.INTERNAL_ERROR, [`internal guard error: ${e && e.message}`]);
  }
}
