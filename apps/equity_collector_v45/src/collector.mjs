/**
 * V45 Equity Collector — INDEPENDENT Scheduled Worker.
 *
 * Ownership: READ-ONLY w.r.t. the frozen trading ledger.
 *   - READS:  paper_sessions, paper_accounts (state_json / state_hash / version)
 *   - WRITES: paper_equity_snapshots (frozen schema) + v45_snapshot_provenance
 *   - NEVER writes paper_accounts, paper_server_events, paper_orders, fills;
 *     never changes order status or the replay hash chain.
 *
 * ENABLE_REAL_TRADING must be false semantics. Real trading / real funds are
 * never connected. No credentials or tokens are read, printed or stored.
 *
 * Entrypoints:
 *   - scheduled(controller, env, ctx): one collection cycle per cron tick.
 *     cycle_key is derived from controller.scheduledTime => the same tick is
 *     idempotent even if Cloudflare replays it.
 *   - fetch(request, env, ctx): CONTROLLED local test trigger. DISABLED by
 *     default (V45_TEST_TRIGGER !== "true" -> 403) so it cannot be driven in
 *     production. Used only by the isolated local test harness.
 */
import { computeEngineSha256 } from "./hash.mjs";
import { COLLECTOR_VERSION } from "./constants.mjs";
import { fetchMarket } from "./market.mjs";
import { buildSnapshot, snapshotInsertStatement, initialCashFp } from "./compute.mjs";
import { buildProvenance, provenanceInsertStatement } from "./provenance.mjs";

function assertNoRealTrading(env) {
  const v = env && env.ENABLE_REAL_TRADING;
  const falseSemantics =
    v === "false" || v === false || v === 0 || v === "0" || v === undefined || v === null;
  if (!falseSemantics) throw new Error("ENABLE_REAL_TRADING_MUST_BE_FALSE");
}

async function countSnapshots(db) {
  const r = await db.prepare("SELECT COUNT(*) AS n FROM paper_equity_snapshots").first();
  return Number((r && r.n) || 0);
}

/**
 * One collection cycle. Deterministic + idempotent:
 *   snapshot_id = sha256(session_id, generation, cycle_key, account_version,
 *                        state_hash)  -- NO volatile inputs.
 *   INSERT OR IGNORE on BOTH tables => re-running the SAME cycle does not add
 *   rows and does not change any existing field.
 */
export async function runCycle(env, options = {}) {
  assertNoRealTrading(env);
  const db = options.db || (env && env.EQ_DB);
  if (!db) throw new Error("EQ_DB_MISSING");

  const nowMs = options.nowMs ?? Date.now();
  const nowIso = new Date(nowMs).toISOString();
  const cycleKey = options.cycleKey || ("adhoc:" + nowIso);
  const fetcher = options.fetcher || (env && typeof env.__fetch === "function" ? env.__fetch : globalThis.fetch);
  const engineSha256 = await computeEngineSha256();

  // FAIL-CLOSED: only VALID sessions (exists, not revoked, not expired).
  // No valid session => no account rows => ZERO snapshot writes.
  const accounts = await db.prepare(
    `SELECT a.session_id, a.version, a.state_json, a.state_hash
       FROM paper_accounts a
       JOIN paper_sessions s ON s.session_id = a.session_id
      WHERE s.revoked_at IS NULL AND s.expires_at > ?
      ORDER BY a.session_id`
  ).bind(nowIso).all();
  const rows = accounts.results || [];

  const beforeCount = await countSnapshots(db);
  const summary = {
    ok: true,
    collectorVersion: COLLECTOR_VERSION,
    cycleKey, nowIso, engineSha256,
    sessions: rows.length,
    written: 0, verified: 0, unknown: 0,
    failures: [],
    snapshots: [],
  };

  for (const row of rows) {
    try {
      const account = JSON.parse(row.state_json);
      const held = Object.keys(account.positions || {});
      const generation = Number(account.generation || 1);
      const market = await fetchMarket(fetcher, held, { nowMs });

      // Per-(session, generation) history peak using VERIFIED snapshots ONLY.
      const peakRow = await db.prepare(
        "SELECT MAX(equity) AS peak FROM paper_equity_snapshots WHERE session_id = ? AND generation = ? AND mark_quality = 'verified'"
      ).bind(String(row.session_id), generation).first();
      const historyPeakFp = peakRow ? peakRow.peak : null;

      const marksInfo = {
        marks: market.marks,
        marksJson: market.marksJson,
        markQuality: market.markQuality,
        source: market.source,
        missing: market.missing,
      };
      const snapshot = await buildSnapshot(row, marksInfo, historyPeakFp, nowMs, {
        sessionId: row.session_id, generation, cycleKey,
        accountVersion: Number(row.version), stateHash: row.state_hash,
      });
      const provenance = buildProvenance({
        snapshot, market, cycleKey, engineSha256,
        initialCashFp: initialCashFp(row), nowMs,
      });

      const s1 = snapshotInsertStatement(snapshot);
      const s2 = provenanceInsertStatement(provenance);
      await db.prepare(s1.sql).bind(...s1.values).run();
      await db.prepare(s2.sql).bind(...s2.values).run();

      if (snapshot.markQuality === "verified") summary.verified++;
      else summary.unknown++;
      summary.snapshots.push({
        snapshotId: snapshot.snapshotId,
        sessionId: snapshot.sessionId,
        generation: snapshot.generation,
        cycleKey,
        accountVersion: snapshot.accountVersion,
        stateHash: snapshot.stateHash,
        equity: snapshot.equity,
        cash: snapshot.cash,
        marketValue: snapshot.marketValue,
        unrealizedPnl: snapshot.unrealizedPnl,
        realizedPnl: snapshot.realizedPnl,
        equityPeak: snapshot.equityPeak,
        drawdown: snapshot.drawdown,
        markQuality: snapshot.markQuality,
        dataQuality: market.dataQuality,
        freshnessVerdict: market.freshnessVerdict,
        freshnessMs: market.freshnessMs,
        clockSkewMs: market.clockSkewMs,
        errorCode: market.errorCode,
        httpStatus: market.httpStatus,
        missingMarks: market.missing,
        createdAt: snapshot.createdAt,
      });
    } catch (error) {
      summary.failures.push({
        sessionId: String(row.session_id),
        error: String((error && error.message) || error).slice(0, 200),
      });
    }
  }

  const afterCount = await countSnapshots(db);
  summary.written = afterCount - beforeCount;   // 0 on idempotent re-run
  return summary;
}

async function scheduled(controller, env, ctx) {
  assertNoRealTrading(env);
  const scheduledTime = (controller && controller.scheduledTime) || Date.now();
  const cycleKey = "cron:" + new Date(scheduledTime).toISOString();
  return runCycle(env, { cycleKey, nowMs: scheduledTime });
}

function jsonResponse(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });
}

async function fetch(request, env, ctx) {
  // Controlled test entrypoint. Disabled by default in production.
  if (!env || env.V45_TEST_TRIGGER !== "true") {
    return jsonResponse({ ok: false, error: "TEST_TRIGGER_DISABLED" }, 403);
  }
  try { assertNoRealTrading(env); }
  catch (e) { return jsonResponse({ ok: false, error: String(e.message || e), code: "RED_LINE" }, 500); }

  const u = new URL(request.url);
  if (u.pathname !== "/__v45/collect") {
    return jsonResponse({ ok: false, error: "NOT_FOUND" }, 404);
  }
  const cycleKey = u.searchParams.get("cycle_key") || ("fetch:" + new Date().toISOString());
  const nowMs = u.searchParams.has("now_ms") ? Number(u.searchParams.get("now_ms")) : Date.now();
  const fetcher = env && typeof env.__fetch === "function" ? env.__fetch : globalThis.fetch;
  const summary = await runCycle(env, { cycleKey, nowMs, fetcher });
  return jsonResponse(summary, 200);
}

export default { scheduled, fetch };
