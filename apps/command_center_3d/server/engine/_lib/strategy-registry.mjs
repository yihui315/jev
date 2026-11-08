/**
 * JEV V4.2 PHASE 4 — strategy domain, server-verified and FAIL CLOSED.
 *
 * Hard rules:
 *  - No client-supplied performance / OOS metrics are ever persisted.
 *    The HTTP API only READS these tables. `verified = 1` can only be set by a
 *    future server-side recomputation from raw trades; until then every gate
 *    evaluation returns NOT_ELIGIBLE / SUSPEND.
 *  - Performance is computed ONLY from the real `paper_fills` rows in D1.
 *  - Evaluation never places an order, never promotes a strategy automatically,
 *    and never changes `safety.autoExecution`. TAKER remains PROPOSAL_ONLY.
 *  - Rejections are append-only and mirrored as trusted `strategy.gate.*`
 *    events (already whitelisted in paper-ledger.mjs).
 */
import {
  json, isSameOrigin, requireConfig, readSession, appendTrustedEvent
} from "./paper-ledger.mjs";
import { computeFractionalKelly } from "./agent-orchestrator.mjs";
import { buildEquityRiskBasis } from "./equity-snapshot.mjs";
import { appendStrategyEvent } from "./strategy-events.mjs";

export const SCALE = 100_000_000; // 1e8 fixed point, matches 0003_fixed_point.sql
const MAX_BODY_BYTES = 32_000;
const ACTIVE_LIFECYCLES = new Set(["backtest", "oos", "paper"]);
const MIN_SAMPLE_COUNT = 100;
const MIN_OOS_TRADES = 30;
const MAX_DRAWDOWN = 0.20; // 20% equity drawdown -> deterministic auto-pause

function safeNum(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

export function fpToRatio(value) {
  return safeNum(value) / SCALE;
}

export function ratioToFp(value) {
  return Number.isFinite(value) ? Math.round(value * SCALE) : 0;
}

/** Server-derived metrics for the deterministic KELLY gate. */
export function buildKellyMetrics(backtest, oos) {
  let metrics = {};
  try {
    metrics = JSON.parse((backtest && backtest.metrics_json) || "{}") || {};
  } catch (_) {
    metrics = {};
  }
  return {
    strategyPass: Number(backtest && backtest.verified) === 1 &&
      Number(backtest && backtest.strategy_pass) === 1,
    oosPass: Number(oos && oos.verified) === 1 &&
      Number(oos && oos.oos_pass) === 1,
    sampleCount: safeNum(backtest && backtest.sample_count),
    oosTrades: safeNum(oos && oos.oos_trades),
    winRate: Number(metrics.winRate ?? metrics.win_rate ?? 0),
    avgWinPct: Number(metrics.avgWinPct ?? 0),
    avgLossPct: Number(metrics.avgLossPct ?? 0)
  };
}

/**
 * Pure, deterministic release/auto-pause gate. No network, no DB, no LLM.
 * Returns ELIGIBLE only when every piece of evidence is server-verified.
 */
export function evaluateStrategyGate(input) {
  const version = input && input.version || null;
  const backtest = input && input.backtest || null;
  const oos = input && input.oos || null;
  const performance = input && input.performance || null;
  const reasons = [];

  if (!version) {
    reasons.push("策略版本不存在");
  } else {
    if (Number(version.frozen) !== 1) reasons.push("策略版本未冻结");
    if (version.lifecycle === "retired") reasons.push("策略版本已退役");
    if (version.lifecycle === "suspended") reasons.push("策略版本已被自动暂停");
  }

  if (!backtest || Number(backtest.verified) !== 1) {
    reasons.push("缺少服务端复核通过的回测结果（verified=1）；静态 JSON 快照不作为放行依据");
  } else {
    if (Number(backtest.strategy_pass) !== 1) reasons.push("历史回测放行门禁未通过");
    if (safeNum(backtest.sample_count) < MIN_SAMPLE_COUNT) {
      reasons.push(`全样本有效交易数不足 ${MIN_SAMPLE_COUNT}`);
    }
  }

  if (!oos || Number(oos.verified) !== 1) {
    reasons.push("缺少服务端复核通过的样本外结果（verified=1）；不得伪造 OOS 通过");
  } else {
    if (Number(oos.oos_pass) !== 1) reasons.push("样本外 OOS 门禁未通过");
    if (safeNum(oos.oos_trades) < MIN_OOS_TRADES) {
      reasons.push(`OOS 有效交易数不足 ${MIN_OOS_TRADES}`);
    }
  }

  const performanceUsable = !!(
    performance &&
    performance.source === "real-d1-paper-fills" &&
    performance.riskBasis === "mark-to-market-equity-snapshots" &&
    Number.isSafeInteger(Number(performance.sessions)) &&
    Number(performance.sessions) > 0 &&
    Number.isSafeInteger(Number(performance.max_drawdown_pct)) &&
    Number(performance.max_drawdown_pct) >= 0 &&
    performance.equityEvidence && performance.equityEvidence.usable === true &&
    performance.equityEvidence.worstAccountWins !== false
  );
  if (!performanceUsable) {
    reasons.push("缺少可用的服务端真实 Paper 权益快照；仅有已实现盈亏不足以验证最大回撤，必须 fail-closed");
  } else if (fpToRatio(performance.max_drawdown_pct) > MAX_DRAWDOWN) {
    reasons.push(`真实账户最大回撤超过 ${MAX_DRAWDOWN * 100}%`);
  }

  // V4.3: verified=1 backtest/OOS rows are NOT enough. A real trust anchor
  // (GitHub Actions OIDC or an approved HMAC service run) must have produced
  // them. A local E2E run can write verified=1 to test the pipeline, but with
  // trusted=0 it can NEVER open the release gate.
  const receipt = input && input.receipt || null;
  const receiptTrusted = !!(
    receipt &&
    Number(receipt.trusted) === 1 &&
    (receipt.auth_method === "github-actions-oidc" || receipt.auth_method === "hmac-service") &&
    String(receipt.run_id || "") === String((backtest && backtest.run_id) || "__no_backtest__") &&
    typeof receipt.bundle_sha256 === "string" && /^[0-9a-f]{64}$/.test(receipt.bundle_sha256)
  );
  if (!receiptTrusted) {
    reasons.push("研究证据缺少可信 Runner 来源回执；本地/浏览器提交的结果不得作为放行依据");
  }

  const eligible = reasons.length === 0;
  const active = !!(version && ACTIVE_LIFECYCLES.has(version.lifecycle));
  const metrics = buildKellyMetrics(backtest, oos);
  return {
    status: eligible ? "ELIGIBLE" : (active ? "SUSPEND" : "NOT_ELIGIBLE"),
    eligible,
    autoExecution: false,
    reasons,
    thresholds: { minSampleCount: MIN_SAMPLE_COUNT, minOosTrades: MIN_OOS_TRADES, maxDrawdown: MAX_DRAWDOWN },
    kelly: computeFractionalKelly(eligible ? metrics : null),
    evidence: {
      hasVersion: !!version,
      backtestVerified: Number(backtest && backtest.verified) === 1,
      oosVerified: Number(oos && oos.verified) === 1,
      performanceAvailable: performanceUsable,
      performanceSource: performance ? performance.source : null,
      performanceRiskBasis: performance ? performance.riskBasis : null,
      receiptTrusted,
      receiptAuthMethod: receipt ? receipt.auth_method : null
    }
  };
}

/**
 * Compute REAL daily performance from immutable paper_fills rows.
 * All monetary inputs are 1e8 fixed-point integers; baselineCash is a plain
 * number read from paper_accounts.state_json.initialCash.
 */
export function computeDailyPerformance(fillRows, baselineBySession = {}) {
  const rows = Array.isArray(fillRows) ? fillRows.slice() : [];
  rows.sort((a, b) => {
    const ta = String(a.created_at || ""), tb = String(b.created_at || "");
    if (ta !== tb) return ta < tb ? -1 : 1;
    return String(a.fill_id || "").localeCompare(String(b.fill_id || ""));
  });

  const sessions = Object.create(null);
  // V4.2 PHASE 4 fix (real E2E run1 evidence): the equity peak MUST be seeded
  // from the pre-trade baseline. Otherwise a losing first fill becomes the
  // peak and reports max_drawdown_pct = 0, disabling the auto-pause gate.
  for (const [sid, baseline] of Object.entries(baselineBySession)) {
    const baselineFp = baseline && typeof baseline === "object" && Number.isSafeInteger(Number(baseline.fixedPoint))
      ? Number(baseline.fixedPoint)
      : Math.round(safeNum(baseline) * SCALE);
    if (!Number.isSafeInteger(baselineFp) || baselineFp <= 0) {
      throw new Error("INVALID_PERFORMANCE_BASELINE:" + sid);
    }
    sessions[sid] = { baselineFp, cumulativeFp: 0, equityFp: baselineFp };
  }
  const days = new Map();
  let globalPeakFp = 0;
  for (const key of Object.keys(sessions)) globalPeakFp += sessions[key].equityFp;
  let globalMaxDrawdown = 0;

  for (const row of rows) {
    const sid = String(row.session_id || "unknown");
    if (!sessions[sid]) {
      throw new Error("MISSING_SESSION_PERFORMANCE_BASELINE:" + sid);
    }
    const s = sessions[sid];
    const grossFp = safeNum(row.realized_pnl);
    const feeFp = safeNum(row.fee);
    const netFp = grossFp - feeFp;
    s.cumulativeFp += netFp;
    s.equityFp = s.baselineFp + s.cumulativeFp;

    let totalFp = 0;
    for (const key of Object.keys(sessions)) totalFp += sessions[key].equityFp;
    if (totalFp > globalPeakFp) globalPeakFp = totalFp;
    const drawdown = globalPeakFp > 0 ? (globalPeakFp - totalFp) / globalPeakFp : 0;
    globalMaxDrawdown = Math.max(globalMaxDrawdown, drawdown);

    const day = String(row.created_at || "").slice(0, 10);
    if (!day) continue;
    if (!days.has(day)) {
      days.set(day, { day, trades: 0, wins: 0, gross_pnl: 0, fees: 0, net_pnl: 0, win_rate: 0, max_drawdown_pct: 0 });
    }
    const d = days.get(day);
    d.trades += 1;
    if (grossFp > 0) d.wins += 1;
    d.gross_pnl += grossFp;
    d.fees += feeFp;
    d.net_pnl += netFp;
    d.win_rate = d.trades > 0 ? ratioToFp(d.wins / d.trades) : 0;
    d.max_drawdown_pct = Math.max(d.max_drawdown_pct, ratioToFp(drawdown));
  }

  const dayList = [...days.values()];
  const totals = dayList.reduce((acc, d) => {
    acc.trades += d.trades; acc.wins += d.wins;
    acc.gross_pnl += d.gross_pnl; acc.fees += d.fees; acc.net_pnl += d.net_pnl;
    return acc;
  }, { trades: 0, wins: 0, gross_pnl: 0, fees: 0, net_pnl: 0 });
  return {
    source: "real-d1-paper-fills",
    baselineSource: "paper_accounts.state_json.initialCash",
    sampleFills: rows.length,
    sessions: Object.keys(sessions).length,
    days: dayList,
    totals,
    max_drawdown_pct: ratioToFp(globalMaxDrawdown),
    win_rate: totals.trades > 0 ? ratioToFp(totals.wins / totals.trades) : 0
  };
}

async function authorize(request, env, write = false) {
  const config = requireConfig(env);
  if (config.error) return { response: config.error };
  const sameOrigin = write
    ? request.headers.get("Origin") === new URL(request.url).origin
    : isSameOrigin(request);
  if (!sameOrigin) {
    return { response: json({ ok: false, code: "CROSS_ORIGIN", error: "跨站请求已拒绝" }, 403) };
  }
  const auth = await readSession(request, env);
  if (auth.error) return { response: auth.error };
  return { sessionId: auth.session.sid };
}

async function readSmallBody(request) {
  const raw = await request.text();
  if (new TextEncoder().encode(raw).byteLength > MAX_BODY_BYTES) {
    return { error: json({ ok: false, code: "BODY_TOO_LARGE", error: "请求内容过大" }, 413) };
  }
  try {
    const body = JSON.parse(raw || "{}");
    if (!body || typeof body !== "object" || Array.isArray(body)) throw new Error("invalid");
    return { body };
  } catch (_) {
    return { error: json({ ok: false, code: "INVALID_JSON", error: "JSON 请求体无效" }, 400) };
  }
}

function guardTables(error, label) {
  // Missing 0004 migration must be an explicit failure, never an empty PASS.
  return json({
    ok: false, code: "STRATEGY_TABLES_MISSING",
    error: "策略域数据表不存在，请先应用 migrations/0004_strategy_registry.sql",
    operation: label, detail: String(error && error.message || error).slice(0, 200)
  }, 503);
}

export function buildPerformanceWindow(accountRows, resetRows, allFillRows, sinceIso) {
  const sinceMs = Date.parse(sinceIso);
  if (!Number.isFinite(sinceMs)) throw new Error("INVALID_PERFORMANCE_WINDOW");
  const resetBySession = new Map(
    (resetRows || []).map(row => [String(row.session_id), String(row.reset_at || "")])
  );
  const fillsBySession = new Map();
  for (const row of allFillRows || []) {
    const sid = String(row.session_id || "");
    if (!sid) throw new Error("FILL_SESSION_MISSING");
    if (!fillsBySession.has(sid)) fillsBySession.set(sid, []);
    fillsBySession.get(sid).push(row);
  }

  const baselineBySession = {};
  const windowRows = [];
  const sessionWindowStarts = {};
  for (const account of accountRows || []) {
    const sid = String(account.session_id || "");
    if (!sid) throw new Error("ACCOUNT_SESSION_MISSING");
    let state;
    try { state = JSON.parse(account.state_json || "{}"); }
    catch (_) { throw new Error("ACCOUNT_STATE_UNREADABLE:" + sid); }
    const initialCash = Number(state.initialCash);
    if (!Number.isFinite(initialCash) || initialCash <= 0) {
      throw new Error("ACCOUNT_INITIAL_CASH_INVALID:" + sid);
    }

    const resetAt = resetBySession.get(sid) || String(account.created_at || "");
    const resetMs = Date.parse(resetAt);
    if (!Number.isFinite(resetMs)) throw new Error("ACCOUNT_BASELINE_TIME_INVALID:" + sid);
    const windowStartMs = Math.max(sinceMs, resetMs);
    const windowStart = new Date(windowStartMs).toISOString();
    let baselineFp = Math.round(initialCash * SCALE);
    if (!Number.isSafeInteger(baselineFp) || baselineFp <= 0) {
      throw new Error("ACCOUNT_BASELINE_OUT_OF_RANGE:" + sid);
    }

    for (const fill of fillsBySession.get(sid) || []) {
      const fillMs = Date.parse(fill.created_at || "");
      if (!Number.isFinite(fillMs)) throw new Error("FILL_TIMESTAMP_INVALID:" + sid);
      if (fillMs < resetMs) continue; // fills from an earlier account generation are excluded
      const realized = Number(fill.realized_pnl || 0);
      const fee = Number(fill.fee || 0);
      if (!Number.isSafeInteger(realized) || !Number.isSafeInteger(fee)) {
        throw new Error("FILL_FIXED_POINT_INVALID:" + sid);
      }
      if (fillMs < windowStartMs) {
        baselineFp += realized - fee; // carry pre-window realized PnL into the window-start equity
      } else {
        windowRows.push(fill);
      }
    }
    if (!Number.isSafeInteger(baselineFp) || baselineFp <= 0) {
      throw new Error("PERFORMANCE_BASELINE_DEPLETED:" + sid);
    }
    baselineBySession[sid] = { fixedPoint: baselineFp };
    sessionWindowStarts[sid] = windowStart;
  }

  windowRows.sort((a, b) => {
    const ta = String(a.created_at || ""), tb = String(b.created_at || "");
    if (ta !== tb) return ta < tb ? -1 : 1;
    return String(a.fill_id || "").localeCompare(String(b.fill_id || ""));
  });
  return { fillRows: windowRows, baselineBySession, sessionWindowStarts };
}

async function loadRealPerformance(env, days) {
  const windowStart = new Date(Date.now() - Math.max(1, Math.min(days, 365)) * 86400000).toISOString();
  const [accounts, resets] = await Promise.all([
    env.PAPER_DB.prepare("SELECT session_id, state_json, created_at FROM paper_accounts").all(),
    env.PAPER_DB.prepare(
      "SELECT session_id, MAX(created_at) AS reset_at FROM paper_server_events WHERE event_type = 'paper.server.account.reset' GROUP BY session_id"
    ).all()
  ]);
  const accountRows = accounts.results || [];
  const resetRows = resets.results || [];
  const resetBySession = new Map(resetRows.map(row => [String(row.session_id), String(row.reset_at || "")]));
  const fillSets = await Promise.all(accountRows.map(account => {
    const sid = String(account.session_id || "");
    const startAt = resetBySession.get(sid) || String(account.created_at || "");
    return env.PAPER_DB.prepare(
      "SELECT fill_id, session_id, created_at, realized_pnl, fee FROM paper_fills WHERE session_id = ? AND created_at >= ? ORDER BY created_at, fill_id"
    ).bind(sid, startAt).all();
  }));
  const allFillRows = fillSets.flatMap(result => result.results || []);
  const window = buildPerformanceWindow(accountRows, resetRows, allFillRows, windowStart);
  const performance = computeDailyPerformance(window.fillRows, window.baselineBySession);

  // V4.3 / PHASE 4 Step 2: the final release gate must use REAL
  // mark-to-market equity snapshots, not realized PnL. Until snapshots exist
  // (or if migration 0005 has not been applied) riskBasis stays
  // 'realized-pnl-only' and evaluateStrategyGate() fails closed.
  let riskBasis = "realized-pnl-only";
  let equityEvidence = null;
  try {
    const risk = await buildEquityRiskBasis(env.PAPER_DB);
    equityEvidence = risk;
    if (risk.usable === true) {
      riskBasis = "mark-to-market-equity-snapshots";
      // Worst-account drawdown replaces the realized-PnL approximation.
      performance.max_drawdown_pct = risk.max_drawdown_pct;
    }
  } catch (error) {
    equityEvidence = {
      usable: false, riskBasis: "realized-pnl-only",
      reason: "EQUITY_SNAPSHOT_QUERY_FAILED",
      detail: String(error && error.message || error).slice(0, 200)
    };
  }
  return {
    ...performance,
    riskBasis, equityEvidence,
    windowDays: Math.max(1, Math.min(days, 365)),
    requestedWindowStart: windowStart,
    sessionWindowStarts: window.sessionWindowStarts
  };
}

export async function handleStrategyVersions(request, env) {
  if (request.method !== "GET") return json({ ok: false, error: "仅支持 GET" }, 405, { allow: "GET" });
  const auth = await authorize(request, env, false);
  if (auth.response) return auth.response;
  try {
    const result = await env.PAPER_DB.prepare(
      "SELECT strategy_id, version, name, family, source, source_sha256, lifecycle, frozen, created_at, updated_at FROM strategy_versions ORDER BY strategy_id"
    ).all();
    return json({ ok: true, source: "real-d1", count: (result.results || []).length, strategies: result.results || [] });
  } catch (error) {
    return guardTables(error, "strategy.versions");
  }
}

export async function handleResearchRuns(request, env) {
  if (request.method !== "GET") return json({ ok: false, error: "仅支持 GET" }, 405, { allow: "GET" });
  const auth = await authorize(request, env, false);
  if (auth.response) return auth.response;
  const url = new URL(request.url);
  const strategyId = String(url.searchParams.get("strategyId") || "");
  const limit = Math.max(1, Math.min(Number(url.searchParams.get("limit")) || 50, 200));
  try {
    const runs = strategyId
      ? await env.PAPER_DB.prepare(
          "SELECT * FROM research_runs WHERE strategy_id = ? ORDER BY created_at DESC LIMIT ?"
        ).bind(strategyId, limit).all()
      : await env.PAPER_DB.prepare(
          "SELECT * FROM research_runs ORDER BY created_at DESC LIMIT ?"
        ).bind(limit).all();
    const backtests = await env.PAPER_DB.prepare("SELECT * FROM backtest_results").all();
    const oos = await env.PAPER_DB.prepare("SELECT * FROM oos_results").all();
    const btByRun = new Map((backtests.results || []).map(r => [String(r.run_id), r]));
    const oosByRun = new Map((oos.results || []).map(r => [String(r.run_id), r]));
    const items = (runs.results || []).map(run => {
      const bt = btByRun.get(String(run.run_id)) || null;
      const o = oosByRun.get(String(run.run_id)) || null;
      return {
        ...run,
        backtest: bt && { verified: bt.verified, sample_count: bt.sample_count, strategy_pass: bt.strategy_pass },
        oos: o && { verified: o.verified, oos_pass: o.oos_pass, oos_trades: o.oos_trades, folds: o.folds },
        evidenceThresholdsMet: !!(
          run.status === "verified" &&
          bt && Number(bt.verified) === 1 && Number(bt.strategy_pass) === 1 &&
          safeNum(bt.sample_count) >= MIN_SAMPLE_COUNT &&
          o && Number(o.verified) === 1 && Number(o.oos_pass) === 1 &&
          safeNum(o.oos_trades) >= MIN_OOS_TRADES
        ),
        finalRiskGateRequired: true
      };
    });
    return json({ ok: true, source: "real-d1", count: items.length, runs: items,
      note: "evidenceThresholdsMet 仅表示回测/OOS 证据阈值满足；最终放行仍必须调用 POST /api/strategy/evaluate 并通过真实账户绩效风控。" });
  } catch (error) {
    return guardTables(error, "research.runs");
  }
}

export async function handleStrategyPerformance(request, env) {
  if (request.method !== "GET") return json({ ok: false, error: "仅支持 GET" }, 405, { allow: "GET" });
  const auth = await authorize(request, env, false);
  if (auth.response) return auth.response;
  const url = new URL(request.url);
  const days = Math.max(1, Math.min(Number(url.searchParams.get("days")) || 30, 365));
  try {
    const perf = await loadRealPerformance(env, days);
    return json({ ok: true, windowDays: days, ...perf });
  } catch (error) {
    return guardTables(error, "strategy.performance");
  }
}

export async function handleRiskRejections(request, env) {
  if (request.method !== "GET") return json({ ok: false, error: "仅支持 GET" }, 405, { allow: "GET" });
  const auth = await authorize(request, env, false);
  if (auth.response) return auth.response;
  const url = new URL(request.url);
  const strategyId = String(url.searchParams.get("strategyId") || "");
  const limit = Math.max(1, Math.min(Number(url.searchParams.get("limit")) || 50, 200));
  try {
    const result = strategyId
      ? await env.PAPER_DB.prepare(
          "SELECT * FROM risk_rejections WHERE strategy_id = ? ORDER BY created_at DESC LIMIT ?"
        ).bind(strategyId, limit).all()
      : await env.PAPER_DB.prepare(
          "SELECT * FROM risk_rejections ORDER BY created_at DESC LIMIT ?"
        ).bind(limit).all();
    return json({ ok: true, source: "real-d1", count: (result.results || []).length, rejections: result.results || [] });
  } catch (error) {
    return guardTables(error, "strategy.rejections");
  }
}

/**
 * Deterministic, server-computed release / auto-pause evaluation.
 * Writes an append-only rejection and a trusted gate event on every failure.
 * NEVER places an order, NEVER promotes a strategy, NEVER enables autoExecution.
 */
export async function handleStrategyEvaluate(request, env) {
  if (request.method !== "POST") return json({ ok: false, error: "仅支持 POST" }, 405, { allow: "POST" });
  const auth = await authorize(request, env, true);
  if (auth.response) return auth.response;
  const parsed = await readSmallBody(request);
  if (parsed.error) return parsed.error;
  const strategyId = String(parsed.body.strategyId || "").trim();
  const clientActionId = String(parsed.body.clientActionId || "").trim();
  if (!/^[a-zA-Z0-9_.-]{1,80}$/.test(strategyId) || !/^[a-zA-Z0-9_.:-]{8,120}$/.test(clientActionId)) {
    return json({ ok: false, code: "BAD_INPUT", error: "strategyId / clientActionId 不合法" }, 400);
  }
  const at = new Date().toISOString();
  try {
    const db = env.PAPER_DB;
    const version = await db.prepare("SELECT * FROM strategy_versions WHERE strategy_id = ?")
      .bind(strategyId).first();
    let backtest = null;
    let oos = null;
    if (version) {
      // Backtest and OOS evidence must belong to the SAME verified research run
      // and the exact registered strategy version; never combine stale rows.
      backtest = await db.prepare(
        "SELECT bt.* FROM backtest_results bt JOIN research_runs rr ON rr.run_id = bt.run_id WHERE bt.strategy_id = ? AND rr.strategy_version = ? AND rr.status = 'verified' AND bt.verified = 1 ORDER BY bt.computed_at DESC LIMIT 1"
      ).bind(strategyId, version.version).first();
      if (backtest) {
        oos = await db.prepare(
          "SELECT o.* FROM oos_results o JOIN research_runs rr ON rr.run_id = o.run_id WHERE o.strategy_id = ? AND o.run_id = ? AND rr.strategy_version = ? AND rr.status = 'verified' AND o.verified = 1 ORDER BY o.computed_at DESC LIMIT 1"
        ).bind(strategyId, backtest.run_id, version.version).first();
      }
    }
    let performance = null;
    let performanceError = null;
    try {
      performance = await loadRealPerformance(env, 30);
    } catch (error) {
      performanceError = String(error && error.message || error).slice(0, 200);
    }
    // V4.3: a verified backtest row alone is not a trust anchor. The receipt
    // must come from the SAME run and be backed by a real Runner identity.
    let receipt = null;
    let receiptError = null;
    if (backtest) {
      try {
        receipt = await db.prepare("SELECT * FROM verification_receipts WHERE run_id = ?")
          .bind(backtest.run_id).first();
      } catch (error) {
        receiptError = String(error && error.message || error).slice(0, 200);
      }
    }
    const decision = evaluateStrategyGate({ version, backtest, oos, performance, receipt });
    if (!performance && performanceError) decision.performanceError = performanceError;
    if (receiptError) decision.receiptError = receiptError;

    const rejectionId = clientActionId + ":strategy-gate";
    const gate = decision.status === "SUSPEND" ? "auto.pause" : "strategy.release";
    let rejectionWritten = false;
    let lifecycleAfter = version ? version.lifecycle : null;
    if (!decision.eligible) {
      await db.prepare(
        "INSERT OR IGNORE INTO risk_rejections (rejection_id, strategy_id, run_id, gate, reason, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
      ).bind(rejectionId, strategyId, (backtest && backtest.run_id) || null, gate,
             decision.reasons.join("；").slice(0, 900),
             JSON.stringify({ decision, kelly: decision.kelly, performance: performance && {
               source: performance.source, sampleFills: performance.sampleFills,
               max_drawdown_pct: performance.max_drawdown_pct
             } }), at).run();
      rejectionWritten = true;
      if (decision.status === "SUSPEND" && version) {
        await db.prepare(
          "UPDATE strategy_versions SET lifecycle = 'suspended', updated_at = ? WHERE strategy_id = ? AND lifecycle IN ('backtest','oos','paper')"
        ).bind(at, strategyId).run();
        lifecycleAfter = "suspended";
      }
    }
    // V4.3: every evaluation (and especially every auto-pause) gets an
    // independently replayable, hash-linked D1 event in strategy_events.
    // This chain is separate from paper_server_events, so trading replay stays
    // byte-for-byte untouched.
    let strategyEvent = null;
    let strategyEventError = null;
    try {
      strategyEvent = await appendStrategyEvent(db, strategyId,
        decision.status === "SUSPEND" ? "strategy.auto.paused" : "strategy.gate.evaluated", {
          clientActionId, at, status: decision.status, reasons: decision.reasons,
          riskBasis: performance ? performance.riskBasis : null,
          maxDrawdownPct: performance ? performance.max_drawdown_pct : null,
          equitySnapshotCount: performance && performance.equityEvidence
            ? (performance.equityEvidence.snapshotCount || 0) : 0,
          receiptRunId: receipt ? receipt.run_id : null,
          receiptAuthMethod: receipt ? receipt.auth_method : null,
          receiptTrusted: decision.evidence.receiptTrusted,
          lifecycleAfter, autoExecution: false
        });
    } catch (error) {
      strategyEventError = String(error && error.message || error).slice(0, 200);
    }

    const eventType = decision.eligible ? "strategy.gate.passed" : "strategy.gate.rejected";
    const eventEmitted = await appendTrustedEvent(request, env, {
      eventId: clientActionId + ":" + eventType,
      eventType,
      payload: {
        strategyId, gate, status: decision.status, reasons: decision.reasons,
        autoExecution: false, kellyStatus: decision.kelly.status,
        backtestVerified: decision.evidence.backtestVerified,
        oosVerified: decision.evidence.oosVerified,
        performanceSource: decision.evidence.performanceSource,
        performanceRiskBasis: decision.evidence.performanceRiskBasis,
        lifecycleAfter, at
      }
    });
    return json({
      ok: true, strategyId, status: decision.status, eligible: decision.eligible,
      reasons: decision.reasons, thresholds: decision.thresholds,
      evidence: decision.evidence, kelly: decision.kelly,
      rejectionWritten, eventType, eventEmitted, lifecycleAfter,
      receipt: receipt ? {
        runId: receipt.run_id, authMethod: receipt.auth_method,
        trusted: Number(receipt.trusted) === 1,
        bundleSha256: receipt.bundle_sha256,
        commitSha: receipt.commit_sha, workflowRunId: receipt.workflow_run_id
      } : null,
      strategyEventHash: strategyEvent ? strategyEvent.hash : null,
      strategyEventError,
      riskBasis: performance ? performance.riskBasis : null,
      equityEvidence: performance ? performance.equityEvidence : null,
      promotionApplied: false, autoExecution: false,
      note: "本端点仅做确定性判定与拒绝留痕；即使 ELIGIBLE 也不自动晋级、不下单。"
    });
  } catch (error) {
    return guardTables(error, "strategy.evaluate");
  }
}

export const __test = {
  evaluateStrategyGate, buildKellyMetrics, computeDailyPerformance,
  buildPerformanceWindow, fpToRatio, ratioToFp, SCALE
};
