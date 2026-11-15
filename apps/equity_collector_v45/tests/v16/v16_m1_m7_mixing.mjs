/**
 * QA-M1..M7 - partition key, peak restricted to (exchange,market), no
 * fallback / last-good-price, stable id, constant mark series, no cross-market
 * aggregation, and HISTORY_UNPROVEN / received_at / UNKNOWN context excluded
 * from peak. Uses REAL runCycle rows PLUS deliberately injected fixtures.
 */
import {
  openDb, makeEnv, makeState, insertSession, insertAccount,
  controlledFetcher, REAL_BASE_PRICES, scalePrice, runCycle, rows,
} from "../lib/harness.mjs";
import { selectHistoryPeak } from "../../src/partition.mjs";
import { validateSourceHost, validateHistoryProvenance, ERROR_CODES } from "../../src/market-guard.mjs";
import { v16Recorder, insertFixture } from "./lib.mjs";

const R = v16Recorder("v16_m1_m7_mixing", { coverage: "QA-M1..M7" });
const T = 1770000000000;
const db = openDb();
const st = await makeState({
  cash: 100000, initialCash: 100000,
  positions: { BTCUSDT: { qty: 1, avgPrice: 70000 } }, generation: 1,
});
await insertSession(db, "s1", { nowMs: T });
await insertAccount(db, "s1", st.state_json, st.state_hash, { version: 1, nowMs: T });

// Two REAL US VERIFIED cycles.
for (let i = 0; i < 2; i++) {
  const nowMs = T + i * 60000;
  const price = scalePrice(REAL_BASE_PRICES.BTCUSDT, [1.0, 1.1][i]);
  const f = controlledFetcher({ nowMs, serverTime: nowMs, prices: { BTCUSDT: price } });
  const env = makeEnv(db, f, {});
  await runCycle(env, { db, fetcher: f, cycleKey: "mx-c" + i, nowMs });
}
const usSnaps = await rows(db, "SELECT * FROM paper_equity_snapshots WHERE session_id='s1' ORDER BY created_at");
const usPeak = Math.max(...usSnaps.map((s) => s.equity));
const provs = await rows(db, "SELECT * FROM v45_snapshot_provenance");

// M1 - partition key self-consistency on every real row.
R.check("M1_partition_key_self_consistent",
  provs.every((p) => p.market_partition_key === p.exchange + "|" + p.market),
  provs.map((p) => p.market_partition_key));

// M2 / M6 - inject a DIFFERENT-market VERIFIED high-equity row into the SAME
// (session,generation); it must not pollute the US peak, but must remain
// visible in its OWN partition (proving real partitioning, not blanket drop).
// Sentinel MUST exceed the real US peak (~1.8e13 in 1e8 fixed point); otherwise
// a broken partition query would still return the larger US MAX and the test
// would pass "naturally" (contract forbids that). 9.9e13 > any real US equity.
const GLOBAL_EQ = 99000000000000;
insertFixture(db, {
  snapshotId: "fx_global", equity: GLOBAL_EQ,
  prov: {
    exchange: "Binance Global spot", market: "spot",
    market_partition_key: "Binance Global spot|spot",
    data_quality: "VERIFIED", failure_class: "NONE",
    price_ts_source: "exchange_serverTime", network_context: "local_test_shim",
  },
});
const peakUS = await selectHistoryPeak(db, { sessionId: "s1", generation: 1, exchange: "Binance.US", market: "spot" });
const peakGlobal = await selectHistoryPeak(db, { sessionId: "s1", generation: 1, exchange: "Binance Global spot", market: "spot" });
R.check("M2_global_sentinel_exceeds_us_peak_so_leak_is_visible",
  GLOBAL_EQ > usPeak, { usPeak, globalEq: GLOBAL_EQ });
R.check("M2_peak_restricted_to_same_partition",
  peakUS === usPeak && peakUS !== GLOBAL_EQ,
  { usPeak, peakUS, globalEq: GLOBAL_EQ });
R.check("M6_no_cross_market_aggregation_global_isolated",
  peakGlobal === GLOBAL_EQ, { peakGlobal, globalEq: GLOBAL_EQ });

// M3 - no fallback / no last-good-price.
const fb = validateSourceHost({ sourceHost: "api.binance.us", exchange: "Binance.US", fallbackUsed: true, usedLastGoodPrice: false, autoFallback: false });
R.check("M3_auto_fallback_fails_closed",
  fb.pass === false && fb.code === ERROR_CODES.MARKET_SOURCE_NOT_VERIFIED, { code: fb.code });
const lgp = validateSourceHost({ sourceHost: "api.binance.us", exchange: "Binance.US", fallbackUsed: false, usedLastGoodPrice: true, autoFallback: false });
R.check("M3_last_good_price_fails_closed",
  lgp.pass === false && lgp.code === ERROR_CODES.MARKET_SOURCE_NOT_VERIFIED, { code: lgp.code });

// M5 - same cycle re-run: mark series is constant (INSERT OR IGNORE, no rewrite).
const idBefore = (await rows(db, "SELECT snapshot_id FROM v45_snapshot_provenance WHERE cycle_key='mx-c0'")).map((r) => r.snapshot_id);
const seriesBefore = (await rows(db, "SELECT mark_contracts_json FROM v45_snapshot_provenance WHERE cycle_key='mx-c0'"))[0].mark_contracts_json;
const f0 = controlledFetcher({ nowMs: T, serverTime: T, prices: { BTCUSDT: REAL_BASE_PRICES.BTCUSDT } });
const env0 = makeEnv(db, f0, {});
const rerun = await runCycle(env0, { db, fetcher: f0, cycleKey: "mx-c0", nowMs: T });
const seriesAfter = (await rows(db, "SELECT mark_contracts_json FROM v45_snapshot_provenance WHERE cycle_key='mx-c0'"))[0].mark_contracts_json;
const idAfter = (await rows(db, "SELECT snapshot_id FROM v45_snapshot_provenance WHERE cycle_key='mx-c0'")).map((r) => r.snapshot_id);
R.check("M5_mark_series_constant_across_rerun",
  rerun.written === 0 && seriesBefore === seriesAfter,
  { written: rerun.written, sameSeries: seriesBefore === seriesAfter });
// M4 - deterministic id stable across the re-run.
R.check("M4_snapshot_id_stable_across_rerun",
  idBefore.length === 1 && idBefore[0] === idAfter[0],
  { before: idBefore, after: idAfter });

// M7 - unproven rows must never enter the US peak.
// These sentinels also exceed the real US peak, so any leak into peak would
// raise MAX above usPeak and the assertion would FAIL (real, not natural pass).
insertFixture(db, { snapshotId: "fx_hist", equity: 80000000000000, prov: { failure_class: "HISTORY_UNPROVEN" } });
insertFixture(db, { snapshotId: "fx_ts", equity: 80000000000001, prov: { price_ts_source: "received_at" } });
insertFixture(db, { snapshotId: "fx_ctx", equity: 80000000000002, prov: { network_context: "UNKNOWN" } });
const peakUS2 = await selectHistoryPeak(db, { sessionId: "s1", generation: 1, exchange: "Binance.US", market: "spot" });
R.check("M7_unproven_rows_excluded_from_peak",
  peakUS2 === usPeak, { peakUS2, usPeak, sentinelEquities: [80000000000000, 80000000000001, 80000000000002] });
R.check("M7_sentinels_exceed_us_peak_so_leak_is_visible",
  [80000000000000, 80000000000001, 80000000000002].every((e) => e > usPeak),
  { usPeak, note: "each sentinel > usPeak; if exclusion failed, MAX would exceed usPeak" });
const hc = validateHistoryProvenance([{
  data_quality: "VERIFIED", failure_class: "HISTORY_UNPROVEN",
  exchange: "Binance.US", market: "spot",
  price_ts_source: "exchange_serverTime", network_context: "local_test_shim",
}]);
R.check("M7_history_provenance_guard_code",
  hc.pass === false && hc.code === ERROR_CODES.HISTORY_PROVENANCE_UNPROVEN, { code: hc.code });

R.finish({ usPeak, globalEq: GLOBAL_EQ, peakUS, peakGlobal, peakUS2 });
