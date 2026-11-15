/**
 * JEV V4.3 / §3 - mark-to-market equity snapshots.
 *
 * This module is READ-ONLY with respect to the trading account. It never
 * UPDATEs paper_accounts, never touches state_json / state_hash / version, and
 * never writes paper_server_events, so the existing state-hash replay chain is
 * byte-for-byte preserved.
 *
 * Real marks come from the public Binance ticker endpoint. If ANY held symbol
 * cannot be marked, the snapshot is stored with mark_quality='unknown' for
 * audit purposes but is EXCLUDED from risk gating. No stale price, no
 * synthetic price, no "use last good mark" fallback.
 *
 * Drawdown is computed per (session_id, generation) against that account's own
 * equity peak. Independent accounts are NEVER summed: the risk gate takes the
 * WORST account so one account's profit cannot mask another's loss.
 */
import { D, U, add, sub, mul, div, max, toNumber } from "./decimal.mjs";

export const MARKET_BASE = "https://data-api.binance.vision/api/v3";
export const SCALE = 100_000_000;
export const MIN_VERIFIED_SNAPSHOTS = 2;   // a single point is not an equity PATH
export const MAX_SNAPSHOT_AGE_MS = 60 * 60 * 1000;

/** BigInt -> D1-safe integer. Never silently lose precision. */
function fp(value) {
  const n = Number(D(value));
  if (!Number.isSafeInteger(n)) throw new Error("EQUITY_FIXED_POINT_OUT_OF_SAFE_RANGE");
  return n;
}

function d1Row(values) {
  return values.map((v) => (typeof v === "bigint" ? fp(v) : v));
}

async function fetchJson(fetcher, url) {
  // Workers/miniflare does not implement RequestInit.cache (browser-only).
  const response = await fetcher(url, {
    headers: { accept: "application/json", "cache-control": "no-cache" }
  });
  if (!response || !response.ok) throw new Error("MARKET_HTTP_" + (response && response.status || "ERROR"));
  return response.json();
}

/**
 * Fetch real mark prices. Returns quality='unknown' on any failure or missing
 * symbol. Callers MUST NOT promote an unknown snapshot into a risk decision.
 */
export async function fetchMarks(fetcher, symbols, nowMs = Date.now()) {
  const unique = [...new Set((symbols || []).map(String).filter(Boolean))].sort();
  const at = new Date(nowMs).toISOString();
  if (!unique.length) {
    return { marks: {}, marksJson: "{}", markQuality: "verified", source: "binance-public-ticker", fetchedAt: at, missing: [] };
  }
  try {
    const url = MARKET_BASE + "/ticker/price?symbols=" + encodeURIComponent(JSON.stringify(unique));
    const payload = await fetchJson(fetcher, url);
    const items = Array.isArray(payload) ? payload : [payload];
    const marks = {};
    for (const item of items) {
      if (!item || typeof item.symbol !== "string") continue;
      const price = D(item.price);
      if (price > 0n) marks[item.symbol] = price;
    }
    const missing = unique.filter((s) => !(marks[s] > 0n));
    return {
      marks, marksJson: JSON.stringify(Object.fromEntries(
        Object.entries(marks).map(([k, v]) => [k, U(v)])
      )),
      markQuality: missing.length ? "unknown" : "verified",
      source: "binance-public-ticker", fetchedAt: at, missing
    };
  } catch (_) {
    return { marks: {}, marksJson: "{}", markQuality: "unknown", source: "binance-public-ticker", fetchedAt: at, missing: unique };
  }
}

/**
 * Pure computation. `accountRow` is the paper_accounts row, `marksInfo` comes
 * from fetchMarks, `historyPeakFp` is the best verified equity of THIS
 * (session, generation) seen in earlier snapshots (a plain integer or null).
 */
export function computeEquitySnapshot(accountRow, marksInfo, historyPeakFp = null, nowMs = Date.now()) {
  if (!accountRow || typeof accountRow.state_json !== "string") {
    throw new Error("EQUITY_ACCOUNT_ROW_INVALID");
  }
  let account;
  try { account = JSON.parse(accountRow.state_json); }
  catch (_) { throw new Error("EQUITY_ACCOUNT_STATE_UNREADABLE"); }
  if (!account || typeof account.positions !== "object" || account.positions === null) {
    throw new Error("EQUITY_ACCOUNT_POSITIONS_INVALID");
  }

  const generation = Number(account.generation || 1);
  if (!Number.isSafeInteger(generation) || generation < 1) throw new Error("EQUITY_GENERATION_INVALID");
  const cash = D(account.cash || 0);
  const initialCash = D(account.initialCash || 0);
  if (initialCash <= 0n) throw new Error("EQUITY_INITIAL_CASH_INVALID");

  const marks = marksInfo.marks || {};
  let marketValue = 0n;
  let unrealizedPnl = 0n;
  const held = Object.keys(account.positions);
  for (const symbol of held) {
    const p = account.positions[symbol];
    const qty = D(p.qty || 0);
    if (qty <= 0n) continue;
    const mark = marks[symbol];
    if (!(mark > 0n)) {
      // No real mark: still record the account, but never claim a verified
      // mark-to-market equity. The gate will fail closed on this row.
      return {
        snapshotId: crypto.randomUUID(),
        sessionId: String(accountRow.session_id),
        generation,
        accountVersion: Number(accountRow.version),
        stateHash: String(accountRow.state_hash),
        equity: 0, cash: fp(cash), marketValue: 0, unrealizedPnl: 0,
        realizedPnl: fp(D(account.realizedPnl || 0)),
        equityPeak: 0, drawdown: 0,
        markQuality: "unknown", marksJson: marksInfo.marksJson || "{}",
        source: marksInfo.source || "binance-public-ticker",
        missingMarks: [symbol], createdAt: new Date(nowMs).toISOString(),
        usable: false, reason: "MISSING_REAL_MARK:" + symbol
      };
    }
    const value = mul(qty, mark);
    marketValue = add(marketValue, value);
    unrealizedPnl = add(unrealizedPnl, sub(value, mul(qty, D(p.avgPrice || 0))));
  }

  const equity = add(cash, marketValue);
  const realizedPnl = D(account.realizedPnl || 0);
  const statePeak = D(account.equityPeak || account.initialCash);
  // historyPeakFp is ALREADY a 1e8 scaled integer from D1. D(number) would
  // interpret it as a real-world amount and scale it a second time.
  const historyPeak = historyPeakFp == null ? 0n : BigInt(historyPeakFp);
  const peak = max(max(max(statePeak, historyPeak), equity), initialCash);
  const drawdown = peak > 0n && equity < peak ? div(sub(peak, equity), peak) : 0n;

  return {
    snapshotId: crypto.randomUUID(),
    sessionId: String(accountRow.session_id),
    generation,
    accountVersion: Number(accountRow.version),
    stateHash: String(accountRow.state_hash),
    equity: fp(equity),
    cash: fp(cash),
    marketValue: fp(marketValue),
    unrealizedPnl: fp(unrealizedPnl),
    realizedPnl: fp(realizedPnl),
    equityPeak: fp(peak),
    drawdown: fp(drawdown),
    markQuality: marksInfo.markQuality === "verified" ? "verified" : "unknown",
    marksJson: marksInfo.marksJson || "{}",
    source: marksInfo.source || "binance-public-ticker",
    missingMarks: marksInfo.missing || [],
    createdAt: new Date(nowMs).toISOString(),
    usable: marksInfo.markQuality === "verified",
    reason: marksInfo.markQuality === "verified" ? null : "MARK_QUALITY_UNKNOWN"
  };
}

export function equityInsertStatement(snapshot) {
  return {
    sql: "INSERT INTO paper_equity_snapshots (snapshot_id, session_id, generation, account_version, state_hash, equity, cash, market_value, unrealized_pnl, realized_pnl, equity_peak, drawdown, mark_quality, marks_json, source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    values: d1Row([
      snapshot.snapshotId, snapshot.sessionId, snapshot.generation, snapshot.accountVersion,
      snapshot.stateHash, snapshot.equity, snapshot.cash, snapshot.marketValue,
      snapshot.unrealizedPnl, snapshot.realizedPnl, snapshot.equityPeak, snapshot.drawdown,
      snapshot.markQuality, snapshot.marksJson, snapshot.source, snapshot.createdAt
    ])
  };
}

/**
 * Collect one snapshot per existing paper account using REAL market marks.
 * Returns a summary; never throws on a single bad account (it records the
 * failure and continues), because a scheduled worker must not lose the other
 * accounts' evidence.
 */
export async function collectEquitySnapshots(db, env, options = {}) {
  const fetcher = options.fetcher || (typeof env.__fetch === "function" ? env.__fetch : fetch);
  const nowMs = options.nowMs ?? Date.now();
  const accounts = await db.prepare(
    "SELECT session_id, version, state_json, state_hash, created_at FROM paper_accounts ORDER BY session_id"
  ).all();
  const rows = accounts.results || [];
  const snapshots = [];
  const failures = [];
  for (const row of rows) {
    try {
      let account;
      try { account = JSON.parse(row.state_json); }
      catch (_) { throw new Error("EQUITY_ACCOUNT_STATE_UNREADABLE"); }
      const generation = Number(account.generation || 1);
      const held = Object.keys(account.positions || {});
      const marksInfo = await fetchMarks(fetcher, held, nowMs);
      const peakRow = await db.prepare(
        "SELECT MAX(equity) AS peak FROM paper_equity_snapshots WHERE session_id = ? AND generation = ? AND mark_quality = 'verified'"
      ).bind(String(row.session_id), generation).first();
      const snapshot = computeEquitySnapshot(row, marksInfo, peakRow ? peakRow.peak : null, nowMs);
      snapshots.push(snapshot);
    } catch (error) {
      failures.push({ sessionId: String(row.session_id), error: String(error && error.message || error).slice(0, 200) });
    }
  }
  if (snapshots.length) {
    await db.batch(snapshots.map((s) => {
      const stmt = equityInsertStatement(s);
      return db.prepare(stmt.sql).bind(...stmt.values);
    }));
  }
  return {
    collectedAt: new Date(nowMs).toISOString(),
    accounts: rows.length,
    written: snapshots.length,
    verified: snapshots.filter((s) => s.markQuality === "verified").length,
    unknown: snapshots.filter((s) => s.markQuality === "unknown").length,
    failures,
    snapshots: snapshots.map((s) => ({
      snapshotId: s.snapshotId, sessionId: s.sessionId, generation: s.generation,
      equity: s.equity, drawdown: s.drawdown, markQuality: s.markQuality,
      accountVersion: s.accountVersion, stateHash: s.stateHash, missingMarks: s.missingMarks
    }))
  };
}

/**
 * Build the risk basis consumed by evaluateStrategyGate().
 *
 * STRICT: every current account must have at least MIN_VERIFIED_SNAPSHOTS
 * verified snapshots and a fresh one; otherwise riskBasis stays
 * 'realized-pnl-only' and the gate MUST fail closed. The worst account's
 * drawdown wins - never an aggregate that could hide a loss.
 */
export async function buildEquityRiskBasis(db, options = {}) {
  const nowMs = options.nowMs ?? Date.now();
  const accounts = await db.prepare("SELECT session_id, state_json FROM paper_accounts").all();
  const rows = accounts.results || [];
  if (!rows.length) {
    return { usable: false, riskBasis: "realized-pnl-only", reason: "NO_PAPER_ACCOUNTS", sessions: 0, accounts: [] };
  }
  const details = [];
  let worstDrawdown = 0;
  for (const row of rows) {
    let generation = 1;
    try { generation = Number(JSON.parse(row.state_json).generation || 1); }
    catch (_) { /* treated as generation 1 below */ }
    const snaps = await db.prepare(
      "SELECT equity, equity_peak, drawdown, created_at FROM paper_equity_snapshots WHERE session_id = ? AND generation = ? AND mark_quality = 'verified' ORDER BY created_at ASC, snapshot_id ASC"
    ).bind(String(row.session_id), generation).all();
    const list = snaps.results || [];
    if (list.length < MIN_VERIFIED_SNAPSHOTS) {
      return {
        usable: false, riskBasis: "realized-pnl-only",
        reason: `INSUFFICIENT_VERIFIED_SNAPSHOTS:${row.session_id}:${list.length}`,
        sessions: rows.length, accounts: details
      };
    }
    const newest = Date.parse(list[list.length - 1].created_at || "");
    if (!Number.isFinite(newest) || nowMs - newest > MAX_SNAPSHOT_AGE_MS) {
      return {
        usable: false, riskBasis: "realized-pnl-only",
        reason: `STALE_EQUITY_SNAPSHOT:${row.session_id}`, sessions: rows.length, accounts: details
      };
    }
    let peak = 0n;
    let accountWorst = 0;
    for (const s of list) {
      // V4.3 E2E run1 regression: computeEquitySnapshot() ALREADY seeds
      // equity_peak with the pre-trade baseline (initialCash) and the running
      // high-water mark. Starting the peak from 0 here would treat the FIRST
      // losing snapshot as the peak and report drawdown = 0, silently
      // disabling the 20% auto-pause gate exactly when it is needed.
      const storedPeak = BigInt(s.equity_peak || 0);
      if (storedPeak > peak) peak = storedPeak;
      const eq = BigInt(s.equity);
      if (eq > peak) peak = eq;
      const dd = peak > 0n && eq < peak ? Number(div(sub(peak, eq), peak)) : 0;
      if (dd > accountWorst) accountWorst = dd;
    }
    if (!Number.isSafeInteger(accountWorst)) {
      return { usable: false, riskBasis: "realized-pnl-only", reason: "DRAWDOWN_OUT_OF_RANGE", sessions: rows.length, accounts: details };
    }
    details.push({ sessionId: String(row.session_id), generation, snapshots: list.length,
                   maxDrawdownPct: accountWorst, newestAt: list[list.length - 1].created_at });
    worstDrawdown = Math.max(worstDrawdown, accountWorst);
  }
  return {
    usable: true,
    riskBasis: "mark-to-market-equity-snapshots",
    reason: null,
    worstAccountWins: true,
    sessions: rows.length,
    snapshotCount: details.reduce((n, d) => n + d.snapshots, 0),
    max_drawdown_pct: worstDrawdown,   // 1e8 fixed point, WORST account
    accounts: details
  };
}

export const __test = {
  computeEquitySnapshot, fetchMarks, buildEquityRiskBasis, collectEquitySnapshots,
  equityInsertStatement, fp, MARKET_BASE, MIN_VERIFIED_SNAPSHOTS, MAX_SNAPSHOT_AGE_MS
};
