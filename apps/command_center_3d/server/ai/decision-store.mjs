// JEV V1.6 Track B - persistence for AI decisions (ai_decisions) and the
// side-car attribution table (decision_order_links).
//
// The frozen paper engine is never modified. Attribution happens exclusively
// through decision_order_links, so paper_orders keeps its byte-for-byte schema
// and replay hash chain.
//
// All values crossing this boundary are already normalized decisions
// (decision-schema.mjs) or server verdicts (risk-gate.mjs). This module does
// not validate business rules; it only maps 1e8 fixed point and JSON columns.

const FP = 1e8;

function toFp(value) {
  if (value === null || value === undefined) return null;
  const n = Number(value);
  if (!Number.isFinite(n)) return null;
  return Math.round(n * FP);
}
function fromFp(value) {
  if (value === null || value === undefined) return null;
  const n = Number(value);
  return Number.isFinite(n) ? n / FP : null;
}
function nowIso(nowMs) { return new Date(nowMs == null ? Date.now() : nowMs).toISOString(); }
function parseJson(text, fallback) {
  try { const v = JSON.parse(text); return v == null ? fallback : v; } catch { return fallback; }
}

export function newDecisionId(seed) {
  const u = typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2);
  return "dec-" + (seed ? String(seed).slice(0, 24) + "-" : "") + u;
}

/** Map a normalized decision + provenance meta to an ai_decisions row. */
export function toDecisionRow(decision, meta) {
  const m = meta || {};
  return {
    decision_id: String(m.decisionId || newDecisionId()),
    strategy_id: decision.strategyId,
    provider_id: String(m.providerId || "unknown"),
    model_id: String(m.modelId || "unknown"),
    run_id: m.runId == null ? null : String(m.runId),
    symbol: decision.symbol,
    action: decision.action,
    side: decision.side,
    order_type: decision.orderType,
    quantity: toFp(decision.quantity),
    limit_price: toFp(decision.limitPrice),
    confidence: toFp(decision.confidence),
    reason: decision.reason,
    risk_flags_json: JSON.stringify(decision.riskFlags || []),
    market_context_id: decision.marketContextId,
    input_snapshot_sha256: String(m.inputSnapshotSha256 || ""),
    prompt_version: String(m.promptVersion || "jev.ai.prompt/v1"),
    request_payload_sha256: m.requestPayloadSha256 == null ? null : String(m.requestPayloadSha256),
    response_payload_sha256: m.responsePayloadSha256 == null ? null : String(m.responsePayloadSha256),
    price_ts: m.priceTs == null ? null : String(m.priceTs),
    data_mode: m.dataMode === "real" ? "real" : "mock",
    risk_check_result: "NOT_RUN",
    paper_order_id: null,
    paper_fill_ids_json: "[]",
    status: "PENDING",
    error_code: null,
    created_at: nowIso(m.nowMs),
  };
}

export function rowToDecisionView(row) {
  if (!row) return null;
  return {
    decisionId: row.decision_id,
    strategyId: row.strategy_id,
    providerId: row.provider_id,
    modelId: row.model_id,
    runId: row.run_id,
    symbol: row.symbol,
    action: row.action,
    side: row.side,
    orderType: row.order_type,
    quantity: fromFp(row.quantity),
    limitPrice: fromFp(row.limit_price),
    confidence: fromFp(row.confidence),
    reason: row.reason,
    riskFlags: parseJson(row.risk_flags_json, []),
    marketContextId: row.market_context_id,
    inputSnapshotSha256: row.input_snapshot_sha256,
    promptVersion: row.prompt_version,
    requestPayloadSha256: row.request_payload_sha256,
    responsePayloadSha256: row.response_payload_sha256,
    priceTs: row.price_ts,
    dataMode: row.data_mode,
    riskCheckResult: row.risk_check_result,
    paperOrderId: row.paper_order_id,
    paperFillIds: parseJson(row.paper_fill_ids_json, []),
    status: row.status,
    errorCode: row.error_code,
    createdAt: row.created_at,
  };
}

export async function getDecisionRow(db, decisionId) {
  return db.prepare("SELECT * FROM ai_decisions WHERE decision_id = ?").bind(decisionId).first();
}

export async function getDecision(db, decisionId) {
  return rowToDecisionView(await getDecisionRow(db, decisionId));
}

/** Insert once. A duplicate decision_id returns { inserted:false, row }. */
export async function insertDecision(db, row) {
  const existing = await getDecisionRow(db, row.decision_id);
  if (existing) return { inserted: false, duplicate: true, row: existing };
  try {
    await db.prepare(
      "INSERT INTO ai_decisions (decision_id, strategy_id, provider_id, model_id, run_id, symbol, action, side, order_type, quantity, limit_price, confidence, reason, risk_flags_json, market_context_id, input_snapshot_sha256, prompt_version, request_payload_sha256, response_payload_sha256, price_ts, data_mode, risk_check_result, paper_order_id, paper_fill_ids_json, status, error_code, created_at) " +
      "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
    ).bind(
      row.decision_id, row.strategy_id, row.provider_id, row.model_id, row.run_id, row.symbol,
      row.action, row.side, row.order_type, row.quantity, row.limit_price, row.confidence,
      row.reason, row.risk_flags_json, row.market_context_id, row.input_snapshot_sha256,
      row.prompt_version, row.request_payload_sha256, row.response_payload_sha256, row.price_ts,
      row.data_mode, row.risk_check_result, row.paper_order_id, row.paper_fill_ids_json,
      row.status, row.error_code, row.created_at
    ).run();
    return { inserted: true, duplicate: false, row };
  } catch (err) {
    const raced = await getDecisionRow(db, row.decision_id);
    if (raced) return { inserted: false, duplicate: true, row: raced };
    throw err;
  }
}

export async function recordRiskVerdict(db, decisionId, verdict) {
  const approved = verdict && verdict.approved === true;
  const status = approved ? "APPROVED" : "REJECTED";
  await db.prepare(
    "UPDATE ai_decisions SET risk_check_result = ?, status = ?, error_code = ? WHERE decision_id = ? AND status = 'PENDING'"
  ).bind(approved ? "PASS" : "REJECT", status, approved ? null : String((verdict && verdict.code) || "RISK_REJECTED"), decisionId).run();
  return getDecision(db, decisionId);
}

export async function recordSubmission(db, decisionId, submission) {
  const s = submission || {};
  await db.prepare(
    "UPDATE ai_decisions SET paper_order_id = ?, paper_fill_ids_json = ?, status = ?, error_code = ? WHERE decision_id = ?"
  ).bind(
    s.orderId == null ? null : String(s.orderId),
    JSON.stringify(Array.isArray(s.fillIds) ? s.fillIds : []),
    s.status || "SUBMITTED",
    s.errorCode == null ? null : String(s.errorCode),
    decisionId
  ).run();
  return getDecision(db, decisionId);
}

export async function linkOrder(db, decisionId, orderId, nowMs) {
  const res = await db.prepare(
    "INSERT OR IGNORE INTO decision_order_links (decision_id, order_id, created_at) VALUES (?,?,?)"
  ).bind(decisionId, orderId, nowIso(nowMs)).run();
  return { linked: Number(res && res.meta && res.meta.changes) > 0 };
}

export async function listLinks(db, decisionId) {
  const res = await db.prepare(
    "SELECT decision_id, order_id, created_at FROM decision_order_links WHERE decision_id = ? ORDER BY created_at ASC"
  ).bind(decisionId).all();
  return res.results || [];
}

export async function listRunDecisions(db, runId, limit) {
  const n = Math.min(200, Math.max(1, Number(limit) || 50));
  const res = await db.prepare(
    "SELECT * FROM ai_decisions WHERE run_id = ? ORDER BY created_at ASC LIMIT ?"
  ).bind(runId, n).all();
  const rows = res.results || [];
  const out = [];
  for (const row of rows) out.push({ ...rowToDecisionView(row), links: await listLinks(db, row.decision_id) });
  return out;
}

/**
 * Decision-level attribution and performance. Real numbers only: every metric
 * derives from stored ai_decisions / decision_order_links / paper_fills rows.
 * Zero samples returns status NO_DATA rather than a zeroed performance claim.
 */
export async function performanceSummary(db, options = {}) {
  const strategyId = options.strategyId ? String(options.strategyId) : null;
  const where = strategyId ? " WHERE strategy_id = ?" : "";
  const args = strategyId ? [strategyId] : [];
  const countRow = async (sql, ...bindArgs) => {
    const r = await db.prepare(sql).bind(...bindArgs).first();
    return Number((r && r.n) || 0) || 0;
  };
  const decisions = await countRow("SELECT COUNT(*) AS n FROM ai_decisions" + where, ...args);
  const approved = await countRow("SELECT COUNT(*) AS n FROM ai_decisions" + (strategyId ? " WHERE strategy_id = ? AND" : " WHERE") + " risk_check_result = 'PASS'", ...args);
  const rejected = await countRow("SELECT COUNT(*) AS n FROM ai_decisions" + (strategyId ? " WHERE strategy_id = ? AND" : " WHERE") + " risk_check_result = 'REJECT'", ...args);
  const submitted = await countRow("SELECT COUNT(*) AS n FROM ai_decisions" + (strategyId ? " WHERE strategy_id = ? AND" : " WHERE") + " status = 'SUBMITTED'", ...args);
  const linked = await countRow(
    "SELECT COUNT(*) AS n FROM decision_order_links l JOIN ai_decisions d ON d.decision_id = l.decision_id" + (strategyId ? " WHERE d.strategy_id = ?" : ""),
    ...args
  );

  let realizedPnlFp = 0;
  let fills = 0;
  if (linked > 0) {
    const sql =
      "SELECT COUNT(*) AS n, COALESCE(SUM(f.realized_pnl), 0) AS pnl FROM decision_order_links l " +
      "JOIN ai_decisions d ON d.decision_id = l.decision_id " +
      "JOIN paper_fills f ON f.order_id = l.order_id" +
      (strategyId ? " WHERE d.strategy_id = ?" : "");
    const r = await db.prepare(sql).bind(...args).first();
    fills = Number((r && r.n) || 0) || 0;
    realizedPnlFp = Number((r && r.pnl) || 0) || 0;
  }

  const byStrategyRows = await db.prepare(
    "SELECT strategy_id, data_mode, COUNT(*) AS n FROM ai_decisions GROUP BY strategy_id, data_mode ORDER BY strategy_id"
  ).bind().all();
  const byStrategy = (byStrategyRows.results || []).map((r) => ({
    strategyId: r.strategy_id, dataMode: r.data_mode, decisions: Number(r.n) || 0,
  }));

  return {
    status: decisions === 0 ? "NO_DATA" : "RECORDED",
    strategyId,
    counts: { decisions, approved, rejected, submitted, linkedOrders: linked, fills },
    realizedPnl: fromFp(realizedPnlFp),
    byStrategy,
    note: decisions === 0
      ? "No AI decisions recorded. Performance is NOT zero - it is UNKNOWN."
      : "Derived from stored rows only. No synthesized return, win rate or drawdown.",
  };
}
