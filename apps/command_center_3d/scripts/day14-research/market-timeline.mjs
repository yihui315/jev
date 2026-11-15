/**
 * P3 确定性 seeded OHLCV fixture 时间线。
 *
 * 全部数据为 SYNTHETIC_FIXTURE：isReal:false，venue:"fixture"，绝不冒充真实行情。
 * 同 seed 两次生成必须产出完全相同的 timeline_sha256（确定性，无时间/随机源）。
 * sample_foundation_note 依据 server/trading_api.mjs:320-325：
 *   "Real bar counts are sample foundation, NOT strategy performance.
 *    BACKFILL bars are excluded from live increments."
 * 即 bar 数量只是样本基础，不是策略绩效；BACKFILL bar 不计入 live 增量。
 */
import { createHash } from "node:crypto";

const BASE_TS = Date.UTC(2020, 0, 1, 0, 0, 0); // 固定起点，保证确定性（不是真实行情时间）
const BAR_MS = 60 * 1000;

export const SAMPLE_FOUNDATION_NOTE =
  "Real bar counts are sample foundation, NOT strategy performance. " +
  "BACKFILL bars are excluded from live increments. " +
  "(source: server/trading_api.mjs:320-325)";

export const TIMELINE_EVIDENCE = Object.freeze({
  data_mode: "FIXTURE",
  evidence_level: "SYNTHETIC_FIXTURE",
  isReal: false,
  venue: "fixture",
  no_synthetic_data: false, // false = 确实使用了合成数据（诚实标注）
});

function sha256Hex(text) {
  return createHash("sha256").update(text).digest("hex");
}

function canonical(value) {
  if (Array.isArray(value)) return "[" + value.map(canonical).join(",") + "]";
  if (value && typeof value === "object") {
    return "{" + Object.keys(value).sort()
      .map((k) => JSON.stringify(k) + ":" + canonical(value[k])).join(",") + "}";
  }
  return JSON.stringify(value);
}

// mulberry32：确定性 PRNG，无外部随机源
function mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * 生成确定性 OHLCV 时间线。
 * @param {{seed?:number, bars?:number, startPrice?:number}} options
 */
export function generateTimeline(options = {}) {
  const seed = Number.isInteger(options.seed) ? options.seed : 20261111;
  const bars = Number.isInteger(options.bars) && options.bars > 0 ? options.bars : 240;
  const startPrice = Number.isFinite(options.startPrice) && options.startPrice > 0 ? options.startPrice : 70000;
  const rng = mulberry32(seed);

  const core = [];
  let price = startPrice;
  for (let i = 0; i < bars; i++) {
    const open = price;
    // 确定性随机游走 + 轻微上行漂移；round 到 2 位小数避免浮点尾差
    const shock = (rng() - 0.5) * 0.012; // 约 ±0.6%
    const drift = 0.0002;                 // 固定漂移，无随机参数
    const close = Math.round(open * (1 + drift + shock) * 100) / 100;
    const high = Math.round(Math.max(open, close) * (1 + rng() * 0.004) * 100) / 100;
    const low = Math.round(Math.min(open, close) * (1 - rng() * 0.004) * 100) / 100;
    const volume = Math.round((100 + rng() * 900) * 100) / 100;
    core.push({
      i,
      ts: new Date(BASE_TS + i * BAR_MS).toISOString(),
      open, high, low, close, volume,
      ...TIMELINE_EVIDENCE,
      seed,
    });
    price = close;
  }

  // hash 只对业务 core 计算（不含 timeline_sha256 自身，避免循环依赖）
  const timeline_sha256 = sha256Hex(canonical({ seed, bars: core.length, series: core }));

  // 每条 bar 补齐派生证据字段（timeline_sha256 / sample_foundation_note）
  const series = core.map((b) => ({ ...b, timeline_sha256, sample_foundation_note: SAMPLE_FOUNDATION_NOTE }));

  return {
    ...TIMELINE_EVIDENCE,
    seed,
    bars: series.length,
    timeline_sha256,
    sample_foundation_note: SAMPLE_FOUNDATION_NOTE,
    sample_foundation: {
      bars_total: series.length,
      bars_realtime: 0,
      bars_backfill: 0,
      gaps: 0,
      note: SAMPLE_FOUNDATION_NOTE,
      basis: "SYNTHETIC_FIXTURE（bar 数量不构成任何真实样本基础）",
    },
    provenance_source: "server/trading_api.mjs:320-325",
    series,
  };
}
