/**
 * P1 正式数据层 — Market Snapshot Store。
 *
 * append-only JSONL 快照链：每条 _prev + _hash（sha256），构造时全量校验链
 * （首条 _prev="GENESIS"、逐条 _prev===上条 tip、重算 hash 防内容篡改），恢复 tip。
 * 按 runId 隔离目录：<baseDir>/market-data/<runId>/snapshots.jsonl。
 *
 * 依赖注入：readImpl / writeImpl / existsImpl / mkdirImpl / hashImpl / nowMs /
 * fetchImpl / guard。默认走 node:fs（本地）+ 确定性本地 fixture，绝不触网。
 * 每条记录强制 provenance（七项字段），并经 provenance-guard fail-closed 门控。
 */
import { join } from "node:path";
import { createAppendOnlyLog, GENESIS } from "./append-only-log.mjs";
import {
  guardRecord, buildFixtureProvenance, hasProvenanceShape, buildMarketEnvelope, GUARD_CODES,
} from "./provenance-guard.mjs";

export const SNAPSHOT_FILE = "snapshots.jsonl";

function reject(code, message) {
  const e = new Error(`[snapshot-store][${code}] ${message}`);
  e.code = code;
  throw e;
}

/** 默认本地 fixture：确定性假数据，不含 provenance（由 capture 附加）。 */
function defaultSnapshotFetcher({ nowMs, seq, symbol = "BTCUSDT" }) {
  return {
    type: "snapshot",
    seq,
    symbol,
    marksUsd: { [symbol]: 70000 + seq },
    priceTs: nowMs,
    priceTsSource: "fixture",
  };
}

export function createSnapshotStore(opts = {}) {
  const {
    baseDir,
    runId,
    readImpl,
    writeImpl,
    existsImpl,
    mkdirImpl,
    hashImpl,
    nowMs = () => Date.now(),
    fetchImpl = defaultSnapshotFetcher,
    guard = guardRecord,
    guardOptions = {},
  } = opts;
  if (typeof runId !== "string" || runId.length === 0)
    throw new Error("[snapshot-store] runId required (per-run directory isolation)");
  const readNow = typeof nowMs === "function" ? nowMs : () => nowMs;
  const dir = baseDir ? join(baseDir, "market-data", runId) : join("market-data", runId);
  const path = join(dir, SNAPSHOT_FILE);

  const log = createAppendOnlyLog({
    path, readImpl, writeImpl, existsImpl, mkdirImpl, hashImpl,
  });

  function append(record) {
    if (!record || typeof record !== "object" || Array.isArray(record))
      reject(GUARD_CODES.PROVENANCE_MISSING, "record must be an object");
    // Snapshot Store 生成哈希溯源信封：market_context_id 派生 + 本地文件记录 id，
    // 显式 store_kind:"file" / db_persisted:false。已存在字段保留（非法 hash_scope 透传被拒）。
    const enriched = buildMarketEnvelope(record, {
      isReal: record.provenance ? record.provenance.isReal === true : false,
      recordId: `snapshot:${log.count}`,
    });
    if (!hasProvenanceShape(enriched))
      reject(GUARD_CODES.PROVENANCE_MISSING, "record missing required provenance shape");
    const verdict = guard(enriched, guardOptions);
    if (!verdict.approved)
      reject(verdict.code, `provenance gate rejected: ${verdict.code} — ${verdict.reasons.join("; ")}`);
    return log.append(enriched);
  }

  /** 解析 market_context_id 指向的本地文件记录（重启后从链重建，始终不查 DB）。 */
  function resolveMarketContext(marketContextId) {
    return log.readAll().find((r) => r && r.market_context_id === marketContextId) || null;
  }

  /** 用 fetchImpl（默认本地 fixture）+ nowMs 构造带 provenance 的记录后追加。 */
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
    kind: "snapshot",
    get dir() { return dir; },
    get path() { return path; },
    get tip() { return log.tip; },
    get count() { return log.count; },
    get genesis() { return GENESIS; },
    readAll() { return log.readAll(); },
    verifyChain() { return log.verify(); },
    resolveMarketContext,
    append,
    capture,
  };
}
