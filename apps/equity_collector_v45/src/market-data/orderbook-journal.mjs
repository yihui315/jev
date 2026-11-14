/**
 * P1 正式数据层 — Order Book Journal（全新）。
 *
 * depth 快照含 lastUpdateId / bids / asks / bid / ask / spread / spreadBps + provenance。
 * append-only hash chain，按 runId 隔离目录。
 *
 * 域校验（fail-closed，序列断裂一律拒绝追加）：
 *   - lastUpdateId 严格递增且连续（新 === 既有最大 + 1）：
 *       回退/重复 => SEQUENCE_REGRESSION；跳号 => SEQUENCE_GAP。
 *   - bids / asks 深度非空；每档 [price, qty] 为有限数且 price > 0、qty >= 0；
 *   - bid = max(bids 价)、ask = min(asks 价) 必须与记录字段一致；
 *   - bid < ask（bid >= ask => 拒绝）；
 *   - spread = ask - bid > 0，spreadBps = spread / mid * 10000（mid=(bid+ask)/2）自洽。
 *
 * 依赖注入：readImpl / writeImpl / existsImpl / mkdirImpl / hashImpl / nowMs /
 * fetchImpl / guard。默认本地 fixture，绝不触网。每条记录强制 provenance 门控。
 */
import { join } from "node:path";
import { createAppendOnlyLog, GENESIS } from "./append-only-log.mjs";
import {
  guardRecord, buildFixtureProvenance, hasProvenanceShape, buildMarketEnvelope, GUARD_CODES,
} from "./provenance-guard.mjs";

export const ORDERBOOK_FILE = "orderbook.jsonl";

export const ORDERBOOK_CODES = Object.freeze({
  PROVENANCE_MISSING: GUARD_CODES.PROVENANCE_MISSING,
  FIELD_MISSING: "ORDERBOOK_FIELD_MISSING",
  FIELD_INVALID: "ORDERBOOK_FIELD_INVALID",
  DEPTH_EMPTY: "ORDERBOOK_DEPTH_EMPTY",
  DEPTH_LEVEL_INVALID: "ORDERBOOK_DEPTH_LEVEL_INVALID",
  BOOK_INCONSISTENT: "ORDERBOOK_BOOK_INCONSISTENT",
  BID_GE_ASK: "ORDERBOOK_BID_GE_ASK",
  SPREAD_INVALID: "ORDERBOOK_SPREAD_INVALID",
  SEQUENCE_REGRESSION: "ORDERBOOK_SEQUENCE_REGRESSION",
  SEQUENCE_GAP: "ORDERBOOK_SEQUENCE_GAP",
});

function reject(code, message) {
  const e = new Error(`[orderbook-journal][${code}] ${message}`);
  e.code = code;
  throw e;
}

const EPS = 1e-9;
const near = (a, b) => Math.abs(a - b) <= EPS;

/** 默认本地 fixture depth（确定性；lastUpdateId 随 seq +1 保证连续）。 */
function defaultOrderbookFetcher({ seq, symbol = "BTCUSDT" }) {
  const bid = 69990 + seq;
  const ask = 70010 + seq;
  const spread = ask - bid;
  const mid = (bid + ask) / 2;
  const spreadBps = (spread / mid) * 10000;
  return {
    type: "orderbook",
    seq,
    symbol,
    lastUpdateId: 1000 + seq,
    bids: [[bid, 10], [bid - 10, 20]],
    asks: [[ask, 10], [ask + 10, 20]],
    bid,
    ask,
    spread,
    spreadBps,
  };
}

function bestBid(bids) {
  let m = -Infinity;
  for (const lvl of bids) if (lvl[0] > m) m = lvl[0];
  return m;
}
function bestAsk(asks) {
  let m = Infinity;
  for (const lvl of asks) if (lvl[0] < m) m = lvl[0];
  return m;
}

function validateLevels(levels, side) {
  if (!Array.isArray(levels) || levels.length === 0)
    return { code: ORDERBOOK_CODES.DEPTH_EMPTY, message: `${side} depth empty` };
  for (const lvl of levels) {
    if (!Array.isArray(lvl) || lvl.length < 2 ||
        typeof lvl[0] !== "number" || !Number.isFinite(lvl[0]) || lvl[0] <= 0 ||
        typeof lvl[1] !== "number" || !Number.isFinite(lvl[1]) || lvl[1] < 0)
      return { code: ORDERBOOK_CODES.DEPTH_LEVEL_INVALID, message: `${side} level invalid: ${JSON.stringify(lvl)}` };
  }
  return null;
}

/** 纯校验：返回 { code, message } 或 null。prior 为已落盘 orderbook 记录体数组。 */
export function validateOrderbook(record, prior = []) {
  for (const f of ["lastUpdateId", "bids", "asks", "bid", "ask", "spread", "spreadBps"]) {
    if (!Object.prototype.hasOwnProperty.call(record, f))
      return { code: ORDERBOOK_CODES.FIELD_MISSING, message: `missing field '${f}'` };
  }
  if (typeof record.lastUpdateId !== "number" || !Number.isFinite(record.lastUpdateId) ||
      !Number.isInteger(record.lastUpdateId) || record.lastUpdateId < 0)
    return { code: ORDERBOOK_CODES.FIELD_INVALID, message: "lastUpdateId must be a finite integer >= 0" };
  for (const f of ["bid", "ask", "spread", "spreadBps"]) {
    if (typeof record[f] !== "number" || !Number.isFinite(record[f]))
      return { code: ORDERBOOK_CODES.FIELD_INVALID, message: `field '${f}' must be a finite number` };
  }

  const badBids = validateLevels(record.bids, "bids");
  if (badBids) return badBids;
  const badAsks = validateLevels(record.asks, "asks");
  if (badAsks) return badAsks;

  const bb = bestBid(record.bids);
  const ba = bestAsk(record.asks);
  if (!near(record.bid, bb) || !near(record.ask, ba))
    return { code: ORDERBOOK_CODES.BOOK_INCONSISTENT, message: `bid/ask not best levels (bestBid=${bb}, bestAsk=${ba})` };

  if (!(record.bid < record.ask))
    return { code: ORDERBOOK_CODES.BID_GE_ASK, message: `bid ${record.bid} >= ask ${record.ask}` };

  const spread = record.ask - record.bid;
  const mid = (record.bid + record.ask) / 2;
  const spreadBps = (spread / mid) * 10000;
  if (!(spread > 0) || !near(record.spread, spread) || !near(record.spreadBps, spreadBps))
    return { code: ORDERBOOK_CODES.SPREAD_INVALID, message: `spread/spreadBps inconsistent (spread=${spread}, bps=${spreadBps})` };

  // 序列连续性：新 lastUpdateId 必须 === 既有最大 + 1
  let prev = null;
  for (const r of prior) {
    if (r && typeof r.lastUpdateId === "number" && Number.isFinite(r.lastUpdateId)) {
      if (prev === null || r.lastUpdateId > prev) prev = r.lastUpdateId;
    }
  }
  if (prev !== null) {
    if (record.lastUpdateId <= prev)
      return { code: ORDERBOOK_CODES.SEQUENCE_REGRESSION, message: `lastUpdateId ${record.lastUpdateId} <= prior max ${prev} (regression/duplicate)` };
    if (record.lastUpdateId !== prev + 1)
      return { code: ORDERBOOK_CODES.SEQUENCE_GAP, message: `lastUpdateId ${record.lastUpdateId} skips from prior max ${prev} (expected ${prev + 1})` };
  }

  return null;
}

export function createOrderbookJournal(opts = {}) {
  const {
    baseDir,
    runId,
    readImpl,
    writeImpl,
    existsImpl,
    mkdirImpl,
    hashImpl,
    nowMs = () => Date.now(),
    fetchImpl = defaultOrderbookFetcher,
    guard = guardRecord,
    guardOptions = {},
  } = opts;
  if (typeof runId !== "string" || runId.length === 0)
    throw new Error("[orderbook-journal] runId required (per-run directory isolation)");
  const readNow = typeof nowMs === "function" ? nowMs : () => nowMs;
  const dir = baseDir ? join(baseDir, "market-data", runId) : join("market-data", runId);
  const path = join(dir, ORDERBOOK_FILE);

  const log = createAppendOnlyLog({ path, readImpl, writeImpl, existsImpl, mkdirImpl, hashImpl });

  function append(record) {
    if (!record || typeof record !== "object" || Array.isArray(record))
      reject(GUARD_CODES.PROVENANCE_MISSING, "record must be an object");
    // 哈希溯源信封贯穿 orderbook 记录（已存在字段保留，非法 hash_scope 透传被拒）。
    const enriched = buildMarketEnvelope(record, {
      isReal: record.provenance ? record.provenance.isReal === true : false,
      recordId: `orderbook:${log.count}`,
    });
    if (!hasProvenanceShape(enriched))
      reject(GUARD_CODES.PROVENANCE_MISSING, "record missing required provenance shape");
    const verdict = guard(enriched, guardOptions);
    if (!verdict.approved)
      reject(verdict.code, `provenance gate rejected: ${verdict.code} — ${verdict.reasons.join("; ")}`);
    const bad = validateOrderbook(enriched, log.readAll());
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
    kind: "orderbook",
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
