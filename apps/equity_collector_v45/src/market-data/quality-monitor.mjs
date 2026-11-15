/**
 * P1 正式数据层 — Market Quality Monitor。
 *
 * 输入 snapshot / bar / orderbook 记录数组，输出 { verdict, ok, reasons[] }。
 * verdict 语义与既有 harness 一致："PASS" / "FAIL"（ok 为布尔镜像，兼容
 * market-guard.evaluateCycle 的 ok 语义）。本模块**不 import** market-guard.mjs
 * 的 evaluateCycle，保持 P1 独立可测；纯函数，无 IO、无网络、无时钟读取。
 *
 * 检查项：
 *   - 陈旧度 staleness（阈值可注入 stalenessThresholdMs）；
 *   - 序列缺口（orderbook lastUpdateId 连续 / bar openTime 递增无重复）；
 *   - 字段缺失（provenance 七项 + 各类记录核心字段）；
 *   - spread 异常（bid>=ask / spread<=0 / spreadBps 越界）；
 *   - isReal 与 dataMode 一致性：fixture 被标 real / LIVE 一律判 FAIL。
 *
 * 默认 fail-closed：任何异常 -> FAIL。
 */
export const QUALITY_CODES = Object.freeze({
  MISSING_PROVENANCE: "QUALITY_MISSING_PROVENANCE",
  MISSING_PROVENANCE_FIELD: "QUALITY_MISSING_PROVENANCE_FIELD",
  MISSING_FIELD: "QUALITY_MISSING_FIELD",
  STALE_DATA: "QUALITY_STALE_DATA",
  FUTURE_TIMESTAMP: "QUALITY_FUTURE_TIMESTAMP",
  SEQUENCE_GAP: "QUALITY_SEQUENCE_GAP",
  SEQUENCE_REGRESSION: "QUALITY_SEQUENCE_REGRESSION",
  SPREAD_ANOMALY: "QUALITY_SPREAD_ANOMALY",
  DATAMODE_UNKNOWN: "QUALITY_DATAMODE_UNKNOWN",
  ISREAL_DATAMODE_CONFLICT: "QUALITY_ISREAL_DATAMODE_CONFLICT",
  FIXTURE_MISLABELED_LIVE: "QUALITY_FIXTURE_MISLABELED_LIVE",
  HASH_SCOPE_FORBIDDEN: "QUALITY_HASH_SCOPE_FORBIDDEN",
  MARKET_CONTEXT_ID_MISSING: "QUALITY_MARKET_CONTEXT_ID_MISSING",
  STORE_BINDING_INVALID: "QUALITY_STORE_BINDING_INVALID",
  INTERNAL_ERROR: "QUALITY_INTERNAL_ERROR",
});

const PROV_FIELDS = ["venue", "symbol", "capturedAtMs", "source", "isReal", "dataMode", "captureMode"];
const SNAPSHOT_FIELDS = [];
const BAR_FIELDS = ["interval", "openTime", "closeTime", "open", "high", "low", "close", "volume"];
const ORDERBOOK_FIELDS = ["lastUpdateId", "bids", "asks", "bid", "ask", "spread", "spreadBps"];
const FIXTURE_MODES = ["fixture"];
const REAL_MODES = ["live", "real", "venue_verified"];

function push(reasons, code, message) {
  reasons.push({ code, message });
}

/** 校验单条记录 provenance 的完整性 + isReal/dataMode 一致性。 */
function checkProvenance(rec, label, reasons) {
  const p = rec && rec.provenance;
  if (!p || typeof p !== "object" || Array.isArray(p)) {
    push(reasons, QUALITY_CODES.MISSING_PROVENANCE, `${label}: provenance missing`);
    return null;
  }
  let missing = false;
  for (const f of PROV_FIELDS) {
    if (!Object.prototype.hasOwnProperty.call(p, f)) {
      push(reasons, QUALITY_CODES.MISSING_PROVENANCE_FIELD, `${label}: provenance.${f} missing`);
      missing = true;
    }
  }
  if (missing) return p;

  const mode = String(p.dataMode).toLowerCase();
  const isFixtureMode = FIXTURE_MODES.includes(mode);
  const isRealMode = REAL_MODES.includes(mode);
  if (!isFixtureMode && !isRealMode) {
    push(reasons, QUALITY_CODES.DATAMODE_UNKNOWN, `${label}: dataMode '${p.dataMode}' unknown`);
    return p;
  }
  if (isFixtureMode && p.isReal !== false) {
    push(reasons, QUALITY_CODES.ISREAL_DATAMODE_CONFLICT, `${label}: fixture mode but isReal=${p.isReal} (synthetic data labelled real/LIVE)`);
  }
  if (isRealMode && p.isReal !== true) {
    push(reasons, QUALITY_CODES.ISREAL_DATAMODE_CONFLICT, `${label}: real mode but isReal=${p.isReal}`);
  }
  // 关键红线：fixture 指示器 + LIVE/real 标签
  const fixtureIndicator = isFixtureMode ||
    /fixture|local/i.test(String(p.captureMode || "")) ||
    /fixture|local/i.test(String(p.source || ""));
  const liveIndicator = p.isReal === true || /live|real/.test(mode) ||
    /live|real/i.test(String(p.source || ""));
  if (fixtureIndicator && liveIndicator) {
    push(reasons, QUALITY_CODES.FIXTURE_MISLABELED_LIVE, `${label}: fixture/local record carries a LIVE/real label`);
  }
  return p;
}

/** 校验单类记录的核心字段。 */
function checkFields(rec, label, fields, reasons) {
  for (const f of fields) {
    if (!Object.prototype.hasOwnProperty.call(rec, f))
      push(reasons, QUALITY_CODES.MISSING_FIELD, `${label}: field '${f}' missing`);
  }
}

/** 校验 orderbook 的 spread 异常。 */
function checkSpread(rec, label, maxSpreadBps, reasons) {
  if (typeof rec.bid === "number" && typeof rec.ask === "number" && !(rec.bid < rec.ask)) {
    push(reasons, QUALITY_CODES.SPREAD_ANOMALY, `${label}: bid ${rec.bid} >= ask ${rec.ask}`);
  }
  if (typeof rec.spread === "number" && !(rec.spread > 0)) {
    push(reasons, QUALITY_CODES.SPREAD_ANOMALY, `${label}: spread ${rec.spread} <= 0`);
  }
  if (typeof rec.spreadBps === "number" && (!Number.isFinite(rec.spreadBps) || rec.spreadBps < 0 || rec.spreadBps > maxSpreadBps)) {
    push(reasons, QUALITY_CODES.SPREAD_ANOMALY, `${label}: spreadBps ${rec.spreadBps} out of [0, ${maxSpreadBps}]`);
  }
}

/** 校验哈希溯源信封贯穿（snapshot/bar/orderbook 四类记录）。 */
function checkEnvelope(rec, label, reasons) {
  if (rec.hash_scope !== "raw_upstream_bytes")
    push(reasons, QUALITY_CODES.HASH_SCOPE_FORBIDDEN, `${label}: hash_scope '${rec.hash_scope}' not raw_upstream_bytes`);
  if (typeof rec.market_context_id !== "string" || rec.market_context_id.length === 0)
    push(reasons, QUALITY_CODES.MARKET_CONTEXT_ID_MISSING, `${label}: market_context_id missing`);
  if (rec.store_kind !== "file" || rec.db_persisted !== false)
    push(reasons, QUALITY_CODES.STORE_BINDING_INVALID, `${label}: store_kind/db_persisted invalid (must be file / false)`);
}

/** 主入口。 */
export function evaluateQuality(opts = {}) {
  const {
    snapshots = [],
    bars = [],
    orderbooks = [],
    nowMs = () => Date.now(),
    stalenessThresholdMs = 60_000,
    maxSpreadBps = 2000,
  } = opts;
  const reasons = [];
  try {
    const now = typeof nowMs === "function" ? nowMs() : nowMs;
    const streams = [
      { name: "snapshot", list: snapshots, fields: SNAPSHOT_FIELDS },
      { name: "bar", list: bars, fields: BAR_FIELDS },
      { name: "orderbook", list: orderbooks, fields: ORDERBOOK_FIELDS },
    ];

    for (const { name, list, fields } of streams) {
      if (!Array.isArray(list)) {
        push(reasons, QUALITY_CODES.INTERNAL_ERROR, `${name}: not an array`);
        continue;
      }
      list.forEach((rec, i) => {
        const label = `${name}[${i}]`;
        if (!rec || typeof rec !== "object" || Array.isArray(rec)) {
          push(reasons, QUALITY_CODES.MISSING_PROVENANCE, `${label}: not an object`);
          return;
        }
        const p = checkProvenance(rec, label, reasons);
        checkFields(rec, label, fields, reasons);
        checkEnvelope(rec, label, reasons);
        if (name === "orderbook") checkSpread(rec, label, maxSpreadBps, reasons);
        // 陈旧度 / 未来时间戳
        if (p && typeof p.capturedAtMs === "number" && Number.isFinite(p.capturedAtMs)) {
          const age = now - p.capturedAtMs;
          if (age > stalenessThresholdMs)
            push(reasons, QUALITY_CODES.STALE_DATA, `${label}: stale by ${age}ms > ${stalenessThresholdMs}ms`);
          if (age < -stalenessThresholdMs)
            push(reasons, QUALITY_CODES.FUTURE_TIMESTAMP, `${label}: capturedAtMs is ${-age}ms in the future`);
        }
      });
    }

    // 序列缺口：bar openTime 严格递增且无重复
    const seenOpen = new Set();
    let prevOpen = null;
    bars.forEach((b, i) => {
      if (!b || typeof b.openTime !== "number" || !Number.isFinite(b.openTime)) return;
      if (seenOpen.has(b.openTime))
        push(reasons, QUALITY_CODES.SEQUENCE_REGRESSION, `bar[${i}]: duplicate openTime ${b.openTime}`);
      seenOpen.add(b.openTime);
      if (prevOpen !== null && !(b.openTime > prevOpen))
        push(reasons, QUALITY_CODES.SEQUENCE_GAP, `bar[${i}]: openTime ${b.openTime} not increasing from ${prevOpen}`);
      prevOpen = b.openTime;
    });

    // 序列缺口：orderbook lastUpdateId 必须 === prev + 1
    let prevId = null;
    orderbooks.forEach((o, i) => {
      if (!o || typeof o.lastUpdateId !== "number" || !Number.isFinite(o.lastUpdateId)) return;
      if (prevId !== null) {
        if (o.lastUpdateId <= prevId)
          push(reasons, QUALITY_CODES.SEQUENCE_REGRESSION, `orderbook[${i}]: lastUpdateId ${o.lastUpdateId} <= prev ${prevId}`);
        else if (o.lastUpdateId !== prevId + 1)
          push(reasons, QUALITY_CODES.SEQUENCE_GAP, `orderbook[${i}]: lastUpdateId ${o.lastUpdateId} skips from prev ${prevId}`);
      }
      prevId = o.lastUpdateId;
    });
  } catch (e) {
    push(reasons, QUALITY_CODES.INTERNAL_ERROR, `internal quality error: ${e && e.message}`);
  }

  const verdict = reasons.length === 0 ? "PASS" : "FAIL";
  return {
    verdict,
    ok: verdict === "PASS",
    reasons,
    thresholds: { stalenessThresholdMs, maxSpreadBps },
    recordCount: snapshots.length + bars.length + orderbooks.length,
  };
}
