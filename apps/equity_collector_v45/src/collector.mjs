/**
 * V45 Equity Collector — INDEPENDENT Scheduled Worker (V1.6 hardened).
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
 * V1.6 (M3 FAIL-CLOSED): every cycle really calls market-guard.evaluateCycle.
 * On ANY guard failure we DOWNGRADE to an UNKNOWN row (marks={}, equity=0,
 * dataQuality=UNKNOWN, a specific errorCode + failureClass) but STILL write the
 * provenance audit row. We never throw past the cycle, never skip the audit,
 * never fabricate a price. History peak uses partition.selectHistoryPeak so a
 * different (exchange,market) can never pollute peak / drawdown.
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
import { EXCHANGE_US, MARKET_SPOT, buildMarkContracts } from "./market-contract.mjs";
import { resolveNetworkContext, evaluateCycle, classifyMarketFailure } from "./market-guard.mjs";
import { selectHistoryPeak } from "./partition.mjs";

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

/** VERIFIED (exchange,market) keys already present in this (session,generation) series. */
async function priorMarketKeys(db, sessionId, generation) {
  const r = await db.prepare(
    "SELECT DISTINCT exchange, market FROM v45_snapshot_provenance WHERE session_id = ? AND generation = ? AND data_quality = 'VERIFIED' AND exchange <> 'UNKNOWN' AND market <> 'UNKNOWN'"
  ).bind(String(sessionId), generation).all();
  return (r.results || []).map((row) => row.exchange + "|" + row.market);
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
  // Deterministic network_context (contract 3.2): injected fetcher => local
  // test shim; explicit env value wins; otherwise UNKNOWN -> fail-closed.
  const fetcherInjected = !!options.fetcher || (env && typeof env.__fetch === "function");
  const networkContext = resolveNetworkContext({ env, fetcherInjected });

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

      // (M2) one COMPLETE contract record per symbol.
      const contracts = buildMarkContracts({ market, marks: market.marks, networkContext });
      const markContractsJson = JSON.stringify(contracts);

      // (M3) REAL fail-closed guard call. priorMarketKeys let the guard reject a
      // cycle that would mix a second market into an existing VERIFIED series.
      const priorKeys = await priorMarketKeys(db, row.session_id, generation);
      const guard = evaluateCycle({
        exchange: EXCHANGE_US,
        market: MARKET_SPOT,
        sourceHost: market.sourceHost,
        priceTsSource: market.priceTsSource,
        networkContext,
        contracts,
        priorMarketKeys: priorKeys,
        freshnessVerdict: market.freshnessVerdict,
        httpStatus: market.httpStatus,
        payloadSha256: market.payloadSha256,
        foreignMarketDeclared: market.foreignMarketDeclared,
        fallbackUsed: false,
        usedLastGoodPrice: false,
        autoFallback: false,
        historyProvenance: [],
      });

      const wasVerified = market.dataQuality === "VERIFIED";
      let marksInfo, errorCode, failureClass, exchange, mkt, priceTsSource, effectiveDataQuality;

      if (guard.ok && wasVerified) {
        // Healthy VERIFIED path: keep ALL V1.5 behaviour / return fields.
        marksInfo = {
          marks: market.marks, marksJson: market.marksJson,
          markQuality: market.markQuality, source: market.source, missing: market.missing,
        };
        errorCode = market.errorCode;                 // null on the healthy path
        failureClass = "NONE";
        exchange = EXCHANGE_US; mkt = MARKET_SPOT;
        priceTsSource = market.priceTsSource;        // exchange_serverTime
        effectiveDataQuality = "VERIFIED";
      } else {
        // DOWNGRADE, never skip (contract 3.3 / H8): UNKNOWN row, marks={},
        // equity=0, still fully audited. Keep the honest market-layer errorCode
        // when the market layer already failed; otherwise use the guard code.
        marksInfo = {
          marks: {}, marksJson: "{}",
          markQuality: "unknown", source: market.source, missing: market.missing,
        };
        errorCode = wasVerified ? guard.errorCode : market.errorCode;
        failureClass = wasVerified ? guard.failureClass : classifyMarketFailure(market);
        exchange = "UNKNOWN"; mkt = "UNKNOWN";
        priceTsSource = "UNKNOWN";
        effectiveDataQuality = "UNKNOWN";
      }
      const partitionKey = exchange + "|" + mkt;

      // (M3.4) REPLACES the old "SELECT MAX(equity) ... WHERE mark_quality='verified'".
      // Peak is now restricted to the SAME (exchange,market) with proven data.
      const historyPeakFp = await selectHistoryPeak(db, {
        sessionId: row.session_id, generation, exchange, market: mkt,
      });

      const snapshot = await buildSnapshot(row, marksInfo, historyPeakFp, nowMs, {
        sessionId: row.session_id, generation, cycleKey,
        accountVersion: Number(row.version), stateHash: row.state_hash,
      });

      // Market-like object for buildProvenance: all V1.5 fields + overridden
      // errorCode / dataQuality so the audit row reflects the final verdict.
      const provMarket = {
        ...market,
        errorCode,
        dataQuality: effectiveDataQuality,
        markQuality: marksInfo.markQuality,
      };
      const v16 = {
        exchange,
        market: mkt,
        price_ts_source: priceTsSource,
        network_context: networkContext,
        venue: market.venue,
        failure_class: failureClass,
        mark_contracts_json: markContractsJson,
        market_partition_key: partitionKey,
      };
      const provenance = buildProvenance({
        snapshot, market: provMarket, cycleKey, engineSha256,
        initialCashFp: initialCashFp(row), nowMs, v16,
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
        dataQuality: effectiveDataQuality,
        freshnessVerdict: market.freshnessVerdict,
        freshnessMs: market.freshnessMs,
        clockSkewMs: market.clockSkewMs,
        errorCode,
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

  // TEST-ONLY controlled fault injection (cloud E2E negatives). This whole
  // block is UNREACHABLE in production: it sits behind the 403 gate above, and
  // scheduled() never reads `fault`. After acceptance V45_TEST_TRIGGER is set
  // back to false, so no `fault` parameter can ever be honoured on the edge.
  // With no `fault`, behaviour is byte-for-byte the V1.5 path (real globalThis
  // fetch, or the injected env.__fetch used by the local harness).
  const fault = u.searchParams.get("fault");
  let fetcher = env && typeof env.__fetch === "function" ? env.__fetch : globalThis.fetch;
  if (fault === "transport") {
    fetcher = async () => { throw new TypeError("fetch failed (simulated outage)"); };
  } else if (fault === "http503") {
    fetcher = async (url) => {
      const path = new URL(url).pathname;
      if (path === "/api/v3/ticker/price") {
        return new Response("simulated upstream 503", { status: 503 });
      }
      return new Response(JSON.stringify({ serverTime: Date.now() }), {
        status: 200, headers: { "content-type": "application/json" },
      });
    };
  } else if (fault === "global_host") {
    fetcher = async (url) => {
      const path = new URL(url).pathname;
      if (path === "/api/v3/ticker/price") {
        // Adversarial payload claiming a DIFFERENT market; the §3 guard (or the
        // market layer) MUST reject it as UNKNOWN. The label is an IDENTITY
        // tag, never a hostname (H4/T7 forbid a Global host literal in src).
        return new Response(JSON.stringify([
          { symbol: "BTCUSDT", price: "1.0", exchange: "Binance Global spot" },
        ]), { status: 200, headers: { "content-type": "application/json" } });
      }
      return new Response(JSON.stringify({ serverTime: Date.now() }), {
        status: 200, headers: { "content-type": "application/json" },
      });
    };
  }
  const summary = await runCycle(env, { cycleKey, nowMs, fetcher });
  return jsonResponse(summary, 200);
}

export default { scheduled, fetch };
