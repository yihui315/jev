/**
 * P1 正式数据层 — Replay Dataset。
 *
 * 从三个 store 的记录生成确定性回放数据集：
 *   - datasetHash：对规范化内容（canonicalize：对象键排序、数组保序）做 sha256；
 *     不掺入 nowMs / 随机值，故同一输入两次生成 hash 必一致。
 *   - recordCount / perStream；
 *   - provenance 汇总（venues / symbols / sources / dataModes / captureModes / isReal 取值等）；
 *   - isReal 汇总：只要任一来源 isReal:false，整集必须 containsFixture:true、
 *     绝不出现 LIVE 标签（label="FIXTURE"、isLive:false、tradeable:false）。
 *
 * 仅 Node stdlib（node:crypto）。纯函数；hashImpl 可注入。
 */
import { createHash } from "node:crypto";

export const REPLAY_SCHEMA = "jev.market-data.replay-dataset/v1";

export function defaultHashImpl(str) {
  return createHash("sha256").update(String(str)).digest("hex");
}

/** 规范化：对象键排序、数组保序；用于确定性哈希。 */
export function canonicalize(value) {
  if (value === null || value === undefined) return "null";
  if (typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return "[" + value.map(canonicalize).join(",") + "]";
  const keys = Object.keys(value).sort();
  return "{" + keys.map((k) => JSON.stringify(k) + ":" + canonicalize(value[k])).join(",") + "}";
}

function uniqueSorted(arr) {
  return [...new Set(arr)].sort();
}

function buildProvenanceSummary(provs) {
  const captured = provs
    .map((p) => p.capturedAtMs)
    .filter((n) => typeof n === "number" && Number.isFinite(n));
  return {
    venues: uniqueSorted(provs.map((p) => p.venue).filter((v) => v != null)),
    symbols: uniqueSorted(provs.map((p) => p.symbol).filter((v) => v != null)),
    sources: uniqueSorted(provs.map((p) => p.source).filter((v) => v != null)),
    dataModes: uniqueSorted(provs.map((p) => p.dataMode).filter((v) => v != null)),
    captureModes: uniqueSorted(provs.map((p) => p.captureMode).filter((v) => v != null)),
    isRealValues: uniqueSorted(provs.map((p) => p.isReal).filter((v) => v != null)),
    capturedAtRangeMs: captured.length
      ? { min: Math.min(...captured), max: Math.max(...captured) }
      : { min: null, max: null },
  };
}

/**
 * 生成确定性回放数据集。opts: { snapshots, bars, orderbooks, hashImpl }。
 */
export function buildReplayDataset(opts = {}) {
  const {
    snapshots = [],
    bars = [],
    orderbooks = [],
    hashImpl = defaultHashImpl,
    schema = REPLAY_SCHEMA,
  } = opts;
  if (!Array.isArray(snapshots) || !Array.isArray(bars) || !Array.isArray(orderbooks))
    throw new Error("[replay-dataset] snapshots/bars/orderbooks must be arrays");
  if (typeof hashImpl !== "function")
    throw new Error("[replay-dataset] hashImpl must be a function");

  const streams = { snapshots, bars, orderbooks };
  // datasetHash 覆盖规范化内容，含每条记录的 hash_scope / market_context_id /
  // marketSnapshotHash / store_kind / db_persisted 等信封字段；不掺 nowMs，故同输入两次必一致。
  const datasetHash = hashImpl(canonicalize(streams));

  const all = [...snapshots, ...bars, ...orderbooks];
  const provs = all.map((r) => (r && r.provenance) || null).filter(Boolean);

  const realCount = provs.filter((p) => p.isReal === true).length;
  const fixtureCount = provs.filter((p) => p.isReal === false).length;
  const containsFixture =
    fixtureCount > 0 ||
    provs.some((p) => String(p.dataMode).toLowerCase().includes("fixture"));
  const allReal = provs.length > 0 && provs.every((p) => p.isReal === true);
  const allFixture = provs.length > 0 && provs.every((p) => p.isReal === false);

  // P1 红线：含 fixture 绝不标 LIVE；标签仅 FIXTURE / EMPTY / NON_FIXTURE_UNVERIFIED。
  const label = containsFixture ? "FIXTURE" : (provs.length === 0 ? "EMPTY" : "NON_FIXTURE_UNVERIFIED");

  return {
    schema,
    datasetHash,
    recordCount: all.length,
    perStream: {
      snapshots: snapshots.length,
      bars: bars.length,
      orderbooks: orderbooks.length,
    },
    streams,
    provenanceSummary: buildProvenanceSummary(provs),
    // 哈希溯源信封汇总（字段本身已被 datasetHash 的 canonicalize(streams) 覆盖）
    envelopeSummary: {
      hashScopes: uniqueSorted(all.map((r) => r && r.hash_scope).filter((v) => v != null)),
      storeKinds: uniqueSorted(all.map((r) => r && r.store_kind).filter((v) => v != null)),
      dbPersistedValues: uniqueSorted(all.map((r) => r && r.db_persisted).filter((v) => v != null)),
      marketContextIds: uniqueSorted(all.map((r) => r && r.market_context_id).filter((v) => v != null)),
      marketSnapshotHashesPresent: all.filter((r) => r && typeof r.marketSnapshotHash === "string").length,
    },
    isRealSummary: {
      containsFixture,
      allReal,
      allFixture,
      realCount,
      fixtureCount,
    },
    label,
    isLive: false,          // P1 永不标 LIVE
    tradeable: false,
    note: containsFixture
      ? "Dataset contains fixture (isReal:false) sources — labelled FIXTURE, never LIVE, not tradeable."
      : "P1 does not certify realness — label stays non-LIVE; NOT tradeable.",
  };
}

/**
 * 校验数据集：重算 hash 一致性 + LIVE 不变量。
 * 返回 { hashMatch, liveInvariantOk, ok, recomputed, expected }。
 */
export function verifyReplayDataset(dataset, opts = {}) {
  const hashImpl = opts.hashImpl || defaultHashImpl;
  const recomputed = hashImpl(canonicalize(dataset.streams));
  const hashMatch = recomputed === dataset.datasetHash;
  const containsFixture = !!(dataset.isRealSummary && dataset.isRealSummary.containsFixture);
  const liveInvariantOk = !(containsFixture && (dataset.label === "LIVE" || dataset.isLive === true));
  return {
    hashMatch,
    liveInvariantOk,
    ok: hashMatch && liveInvariantOk,
    recomputed,
    expected: dataset.datasetHash,
  };
}
