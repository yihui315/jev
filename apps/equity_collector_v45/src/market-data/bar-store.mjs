/**
 * P1 正式数据层 — Historical Bar Store（全新）。
 *
 * OHLCV bar：含 interval / openTime / closeTime + provenance。
 * append-only hash chain（复用 append-only-log.mjs），按 runId 隔离目录。
 *
 * 域校验：
 *   - 时间单调递增（新 openTime > 既有最大 openTime）；
 *   - 无重复 openTime（与全部既有 openTime 比对）；
 *   - openTime < closeTime，且均为有限数；
 *   - OHLC 关系合法：high >= max(open,close,low)、low <= min(open,close,high)、high >= low；
 *   - open/high/low/close/volume 均为有限数且 >= 0。
 *
 * 依赖注入：readImpl / writeImpl / existsImpl / mkdirImpl / hashImpl / nowMs /
 * fetchImpl / guard。默认本地 fixture，绝不触网。每条记录强制 provenance 门控。
 */
import { join } from "node:path";
import { createAppendOnlyLog, GENESIS } from "./append-only-log.mjs";
import {
  guardRecord, buildFixtureProvenance, hasProvenanceShape, buildMarketEnvelope, GUARD_CODES,
} from "./provenance-guard.mjs";

export const BAR_FILE = "bars.jsonl";

export const BAR_CODES = Object.freeze({
  PROVENANCE_MISSING: GUARD_CODES.PROVENANCE_MISSING,
  BAR_FIELD_MISSING: "BAR_FIELD_MISSING",
  BAR_FIELD_INVALID: "BAR_FIELD_INVALID",
  BAR_TIME_WINDOW_INVALID: "BAR_TIME_WINDOW_INVALID",
  BAR_TIME_REGRESSION: "BAR_TIME_REGRESSION",
  BAR_DUPLICATE_OPENTIME: "BAR_DUPLICATE_OPENTIME",
  BAR_OHLC_INVALID: "BAR_OHLC_INVALID",
});

function reject(code, message) {
  const e = new Error(`[bar-store][${code}] ${message}`);
  e.code = code;
  throw e;
}

const NUMERIC_FIELDS = ["open", "high", "low", "close", "volume"];

/** 默认本地 fixture bar（确定性，不含 provenance）。 */
function defaultBarFetcher({ seq, symbol = "BTCUSDT", interval = "1m", stepMs = 60000 }) {
  const openTime = 1700000000000 + seq * stepMs;
  const base = 70000 + seq;
  return {
    type: "bar",
    seq,
    interval,
    symbol,
    openTime,
    closeTime: openTime + stepMs,
    open: base,
    high: base + 10,
    low: base - 10,
    close: base + 2,
    volume: 100 + seq,
  };
}

/** 纯校验：返回 { code, message } 或 null。prior 为已落盘 bar 记录体数组。 */
export function validateBar(record, prior = []) {
  for (const f of ["interval", "openTime", "closeTime", ...NUMERIC_FIELDS]) {
    if (!Object.prototype.hasOwnProperty.call(record, f))
      return { code: BAR_CODES.BAR_FIELD_MISSING, message: `missing field '${f}'` };
  }
  if (typeof record.interval !== "string" || record.interval.length === 0)
    return { code: BAR_CODES.BAR_FIELD_INVALID, message: "interval must be non-empty string" };
  if (typeof record.openTime !== "number" || !Number.isFinite(record.openTime) ||
      typeof record.closeTime !== "number" || !Number.isFinite(record.closeTime))
    return { code: BAR_CODES.BAR_FIELD_INVALID, message: "openTime/closeTime must be finite numbers" };
  for (const f of NUMERIC_FIELDS) {
    if (typeof record[f] !== "number" || !Number.isFinite(record[f]) || record[f] < 0)
      return { code: BAR_CODES.BAR_FIELD_INVALID, message: `field '${f}' must be a finite number >= 0` };
  }
  if (!(record.closeTime > record.openTime))
    return { code: BAR_CODES.BAR_TIME_WINDOW_INVALID, message: "closeTime must be > openTime" };

  const { open, high, low, close } = record;
  if (!(high >= low) || !(high >= Math.max(open, close, low)) || !(low <= Math.min(open, close, high)))
    return { code: BAR_CODES.BAR_OHLC_INVALID, message: "OHLC relation invalid (high>=max, low<=min)" };

  const seen = new Set();
  let maxOpenTime = -Infinity;
  for (const b of prior) {
    if (b && typeof b.openTime === "number") {
      seen.add(b.openTime);
      if (b.openTime > maxOpenTime) maxOpenTime = b.openTime;
    }
  }
  if (seen.has(record.openTime))
    return { code: BAR_CODES.BAR_DUPLICATE_OPENTIME, message: `duplicate openTime ${record.openTime}` };
  if (Number.isFinite(maxOpenTime) && !(record.openTime > maxOpenTime))
    return { code: BAR_CODES.BAR_TIME_REGRESSION, message: `openTime ${record.openTime} not > prior max ${maxOpenTime} (regression/equal)` };

  return null;
}

export function createBarStore(opts = {}) {
  const {
    baseDir,
    runId,
    readImpl,
    writeImpl,
    existsImpl,
    mkdirImpl,
    hashImpl,
    nowMs = () => Date.now(),
    fetchImpl = defaultBarFetcher,
    guard = guardRecord,
    guardOptions = {},
  } = opts;
  if (typeof runId !== "string" || runId.length === 0)
    throw new Error("[bar-store] runId required (per-run directory isolation)");
  const readNow = typeof nowMs === "function" ? nowMs : () => nowMs;
  const dir = baseDir ? join(baseDir, "market-data", runId) : join("market-data", runId);
  const path = join(dir, BAR_FILE);

  const log = createAppendOnlyLog({ path, readImpl, writeImpl, existsImpl, mkdirImpl, hashImpl });

  function append(record) {
    if (!record || typeof record !== "object" || Array.isArray(record))
      reject(GUARD_CODES.PROVENANCE_MISSING, "record must be an object");
    // 哈希溯源信封贯穿 bar 记录（已存在字段保留，非法 hash_scope 透传被拒）。
    const enriched = buildMarketEnvelope(record, {
      isReal: record.provenance ? record.provenance.isReal === true : false,
      recordId: `bar:${log.count}`,
    });
    if (!hasProvenanceShape(enriched))
      reject(GUARD_CODES.PROVENANCE_MISSING, "record missing required provenance shape");
    const verdict = guard(enriched, guardOptions);
    if (!verdict.approved)
      reject(verdict.code, `provenance gate rejected: ${verdict.code} — ${verdict.reasons.join("; ")}`);
    const bad = validateBar(enriched, log.readAll());
    if (bad) reject(bad.code, bad.message);
    return log.append(enriched);
  }

  async function capture(spec = {}) {
    const ts = readNow();
    const raw = await fetchImpl({ ...spec, nowMs: ts, seq: log.count });
    if (!raw || typeof raw !== "object" || Array.isArray(raw))
      reject(GUARD_CODES.PROVENANCE_MISSING, "fetchImpl returned an invalid record body");
    const record = {
      ...raw,
      provenance: raw.provenance || buildFixtureProvenance({
        symbol: raw.symbol || spec.symbol || "BTCUSDT",
        capturedAtMs: ts,
      }),
    };
    return append(record);
  }

  return {
    kind: "bar",
    get dir() { return dir; },
    get path() { return path; },
    get tip() { return log.tip; },
    get count() { return log.count; },
    get genesis() { return GENESIS; },
    readAll() { return log.readAll(); },
    verifyChain() { return log.verify(); },
    append,
    capture,
  };
}
