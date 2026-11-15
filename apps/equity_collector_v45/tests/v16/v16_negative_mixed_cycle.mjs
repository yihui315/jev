/**
 * MANDATORY NEGATIVE CASES (contract 4.2). These do NOT pass "naturally" from a
 * single source - a second market is deliberately INJECTED.
 *   1) One cycle injects BOTH Binance.US and Binance Global spot quotes ->
 *      MARKET_MIXING_REJECTED (still writes an UNKNOWN provenance row).
 *   2) A Global VERIFIED high-equity row is pre-seeded in the DB, then a US
 *      cycle runs -> the cycle is REJECTED and the US peak is NOT polluted.
 */
import {
  openDb, makeEnv, makeState, insertSession, insertAccount,
  controlledFetcher, REAL_BASE_PRICES, runCycle, rows,
} from "../lib/harness.mjs";
import { selectHistoryPeak } from "../../src/partition.mjs";
import { v16Recorder, insertFixture } from "./lib.mjs";

const R = v16Recorder("v16_negative_mixed_cycle", { coverage: "MANDATORY mixing + peak-pollution negatives" });
const T = 1770000000000;
const st = await makeState({
  cash: 100000, initialCash: 100000,
  positions: { BTCUSDT: { qty: 1, avgPrice: 70000 } }, generation: 1,
});

// ---------------------------------------------------------------------------
// CASE 1 - same cycle injects US (BTCUSDT) + Global (1000SATSUSDT) quotes.
// The ticker payload carries a Global-market identifier symbol; the guard sees
// two (exchange,market) keys and fail-closes to MARKET_MIXING_REJECTED.
// ---------------------------------------------------------------------------
const db1 = openDb();
await insertSession(db1, "s1", { nowMs: T });
await insertAccount(db1, "s1", st.state_json, st.state_hash, { version: 1, nowMs: T });

function mixedFetcher(nowMs) {
  return async (url) => {
    const u = new URL(url);
    if (u.pathname === "/api/v3/time") {
      return new Response(JSON.stringify({ serverTime: nowMs }), {
        status: 200, headers: { "content-type": "application/json" },
      });
    }
    if (u.pathname === "/api/v3/ticker/price") {
      const items = [
        { symbol: "BTCUSDT", price: REAL_BASE_PRICES.BTCUSDT },
        { symbol: "1000SATSUSDT", price: "0.00000800" },  // Global-market quote injected
      ];
      return new Response(JSON.stringify(items), {
        status: 200,
        headers: { "content-type": "application/json", date: new Date(nowMs).toUTCString() },
      });
    }
    return new Response("{}", { status: 404 });
  };
}
const mf = mixedFetcher(T);
const env1 = makeEnv(db1, mf, {});
const sum1 = await runCycle(env1, { db: db1, fetcher: mf, cycleKey: "mix-c0", nowMs: T });
const p1 = await rows(db1, "SELECT * FROM v45_snapshot_provenance");
const s1 = await rows(db1, "SELECT * FROM paper_equity_snapshots");

R.check("NEG1_us_plus_global_quotes_rejected",
  sum1.verified === 0 && sum1.unknown === 1 && p1.length === 1 &&
  p1[0].error_code === "MARKET_MIXING_REJECTED",
  { verified: sum1.verified, unknown: sum1.unknown, provRows: p1.length, errorCode: p1[0] && p1[0].error_code });
R.check("NEG1_failure_class_market_mixing",
  p1.length === 1 && p1[0].failure_class === "MARKET_MIXING",
  { failure_class: p1[0] && p1[0].failure_class });
R.check("NEG1_still_writes_unknown_provenance",
  p1.length === 1 && p1[0].data_quality === "UNKNOWN",
  { dq: p1[0] && p1[0].data_quality });
R.check("NEG1_no_fabricated_price",
  s1.length === 1 && s1[0].equity === 0 && s1[0].marks_json === "{}",
  { rows: s1.length, equity: s1[0] && s1[0].equity, marks: s1[0] && s1[0].marks_json });
R.check("NEG1_original_two_market_evidence_preserved",
  (() => {
    try {
      const o = JSON.parse(p1[0].mark_contracts_json);
      if (o === null || typeof o !== "object" || Array.isArray(o)) return false;
      const vals = Object.values(o);
      return Object.keys(o).length === 2 &&
        vals.some((c) => c.exchange === "Binance Global spot") &&
        vals.some((c) => c.exchange === "Binance.US");
    } catch { return false; }
  })(),
  { mark_contracts_json: p1[0] && p1[0].mark_contracts_json });

// ---------------------------------------------------------------------------
// CASE 2 - pre-seed a Global VERIFIED high-equity row, then run a US cycle.
// ---------------------------------------------------------------------------
const db2 = openDb();
await insertSession(db2, "s1", { nowMs: T });
await insertAccount(db2, "s1", st.state_json, st.state_hash, { version: 1, nowMs: T });

// High-equity sentinel: MUST exceed a normal US equity (~1.8e13 in 1e8 fixed
// point). If the Global row ever leaked into the US peak, MAX would jump to
// this value and the assertions below would FAIL (no "natural pass").
const GLOBAL_EQ = 99000000000000;
insertFixture(db2, {
  snapshotId: "fx_global", equity: GLOBAL_EQ,
  prov: {
    exchange: "Binance Global spot", market: "spot",
    market_partition_key: "Binance Global spot|spot",
    data_quality: "VERIFIED", failure_class: "NONE",
    price_ts_source: "exchange_serverTime", network_context: "local_test_shim",
  },
});
const f2 = controlledFetcher({ nowMs: T, serverTime: T, prices: { BTCUSDT: REAL_BASE_PRICES.BTCUSDT } });
const env2 = makeEnv(db2, f2, {});
const sum2 = await runCycle(env2, { db: db2, fetcher: f2, cycleKey: "us-c0", nowMs: T });
const usProv = await rows(db2, "SELECT * FROM v45_snapshot_provenance WHERE cycle_key='us-c0'");
const usSnap = await rows(db2,
  "SELECT s.* FROM paper_equity_snapshots s JOIN v45_snapshot_provenance p ON p.snapshot_id = s.snapshot_id WHERE p.cycle_key = 'us-c0'");

R.check("NEG2_us_cycle_rejected_by_prior_global_market",
  sum2.verified === 0 && sum2.unknown === 1 && usProv.length === 1 &&
  usProv[0].error_code === "MARKET_MIXING_REJECTED",
  { verified: sum2.verified, unknown: sum2.unknown, errorCode: usProv[0] && usProv[0].error_code });
R.check("NEG2_rejected_cycle_writes_unknown_provenance",
  usProv.length === 1 && usProv[0].data_quality === "UNKNOWN" && usProv[0].failure_class === "MARKET_MIXING",
  { dq: usProv[0] && usProv[0].data_quality, fc: usProv[0] && usProv[0].failure_class });
R.check("NEG2_us_snapshot_peak_not_polluted_by_global",
  usSnap.length === 1 && usSnap[0].equity_peak === 0 && usSnap[0].equity_peak !== GLOBAL_EQ,
  { rows: usSnap.length, peak: usSnap[0] && usSnap[0].equity_peak, globalEq: GLOBAL_EQ });

const peakUS = await selectHistoryPeak(db2, { sessionId: "s1", generation: 1, exchange: "Binance.US", market: "spot" });
const peakGlobal = await selectHistoryPeak(db2, { sessionId: "s1", generation: 1, exchange: "Binance Global spot", market: "spot" });
// Threshold is ~3x a normal 1e8-fp US equity (~1.8e13); GLOBAL_EQ=9.9e13
// clears it, so a leak of this row into the US peak would be unmistakable.
R.check("NEG2_global_sentinel_exceeds_normal_us_equity_scale",
  GLOBAL_EQ > 50000000000000,
  { globalEq: GLOBAL_EQ, threshold: 50000000000000, note: "normal US equity ~1.8e13; sentinel 9.9e13 > 3x that" });
R.check("NEG2_selectHistoryPeak_US_excludes_global_row",
  peakUS !== GLOBAL_EQ, { peakUS, globalEq: GLOBAL_EQ });
R.check("NEG2_global_row_isolated_in_its_own_partition",
  peakGlobal === GLOBAL_EQ, { peakGlobal, globalEq: GLOBAL_EQ });

R.finish({ case1_errorCode: p1[0] && p1[0].error_code, case2_errorCode: usProv[0] && usProv[0].error_code, peakUS, peakGlobal, globalEq: GLOBAL_EQ });
