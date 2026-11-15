/**
 * T3 — Outage / recovery / missing-symbol honesty.
 *  - fetch throws, HTTP 503, missing symbol, non-positive price => UNKNOWN,
 *    equity=0, marks_json={} (NO fabricated price, NO last-good-price backfill).
 *  - ORDIUSDT / 1000SATSUSDT (NOT listed on Binance.US) => UNKNOWN.
 *  - Next cycle recovers => VERIFIED, but the equity peak must use ONLY
 *    VERIFIED snapshots (the outage rows never raise or pollute the peak).
 */
import {
  openDb, makeEnv, makeState, insertSession, insertAccount,
  controlledFetcher, REAL_BASE_PRICES, runCycle, rows,
  newRecorder,
} from "./lib/harness.mjs";
import { div, sub } from "../src/vendor/decimal.mjs";
import { replay } from "../src/replay.mjs";
import { D } from "../src/vendor/decimal.mjs";

const R = newRecorder("T3_outage_recovery", {
  scenarios: ["ok", "throw", "http503", "missing", "nonpositive", "recover", "unlisted_symbols"],
});

const T = 1770000000000;
const INITIAL_CASH_FP = Number(D(100000));
const db = openDb();

const s1 = await makeState({
  cash: 100000, initialCash: 100000,
  positions: { BTCUSDT: { qty: 1, avgPrice: 70000 } }, generation: 1,
});
await insertSession(db, "s1", { nowMs: T });
await insertAccount(db, "s1", s1.state_json, s1.state_hash, { version: 1, nowMs: T });

// Account holding ONLY symbols NOT listed on Binance.US.
const sU = await makeState({
  cash: 100000, initialCash: 100000,
  positions: {
    ORDIUSDT: { qty: 100, avgPrice: 3 },
    "1000SATSUSDT": { qty: 1000000, avgPrice: 0.000008 },
  }, generation: 1,
});
await insertSession(db, "s_unlisted", { nowMs: T });
await insertAccount(db, "s_unlisted", sU.state_json, sU.state_hash, { version: 1, nowMs: T });

const plan = [
  { key: "c0_ok", mode: "ok", price: REAL_BASE_PRICES.BTCUSDT },
  { key: "c1_throw", mode: "throw" },
  { key: "c2_503", mode: "http503" },
  { key: "c3_missing", mode: "missing", price: REAL_BASE_PRICES.BTCUSDT },
  { key: "c4_nonpositive", mode: "nonpositive", price: REAL_BASE_PRICES.BTCUSDT },
  { key: "c5_recover", mode: "ok", price: "70000.00000000" },
];

const cycles = [];
for (let i = 0; i < plan.length; i++) {
  const p = plan[i];
  const nowMs = T + i * 60000;
  const fetcher = controlledFetcher({
    nowMs, serverTime: nowMs, mode: p.mode,
    prices: { BTCUSDT: p.price || REAL_BASE_PRICES.BTCUSDT },
  });
  const env = makeEnv(db, fetcher, {});
  const summary = await runCycle(env, { db, fetcher, cycleKey: p.key, nowMs });
  cycles.push({ ...p, nowMs, summary });
}

const s1Snaps = await rows(db,
  "SELECT s.*, p.cycle_key, p.data_quality, p.error_code, p.http_status, p.input_marks_json, p.freshness_verdict FROM paper_equity_snapshots s JOIN v45_snapshot_provenance p ON p.snapshot_id = s.snapshot_id WHERE s.session_id = 's1' ORDER BY s.created_at ASC");
const unlistedSnaps = await rows(db,
  "SELECT s.*, p.cycle_key, p.data_quality, p.error_code, p.http_status FROM paper_equity_snapshots s JOIN v45_snapshot_provenance p ON p.snapshot_id = s.snapshot_id WHERE s.session_id = 's_unlisted' ORDER BY s.created_at ASC");

const byKey = Object.fromEntries(s1Snaps.map((r) => [r.cycle_key, r]));

R.check("s1_has_6_snapshots", s1Snaps.length === 6, { count: s1Snaps.length });
R.check("ok_and_recover_are_verified",
  byKey.c0_ok.mark_quality === "verified" && byKey.c5_recover.mark_quality === "verified",
  { c0: byKey.c0_ok.mark_quality, c5: byKey.c5_recover.mark_quality });
R.check("four_outage_cycles_are_unknown",
  ["c1_throw", "c2_503", "c3_missing", "c4_nonpositive"].every((k) => byKey[k].mark_quality === "unknown"),
  ["c1_throw", "c2_503", "c3_missing", "c4_nonpositive"].map((k) => ({ k, q: byKey[k].mark_quality })));
R.check("outage_rows_have_no_fabricated_price",
  ["c1_throw", "c2_503", "c3_missing", "c4_nonpositive"].every((k) =>
    byKey[k].equity === 0 && byKey[k].market_value === 0 && byKey[k].marks_json === "{}" &&
    byKey[k].input_marks_json === "{}" && byKey[k].data_quality === "UNKNOWN"),
  ["c1_throw", "c2_503", "c3_missing", "c4_nonpositive"].map((k) => ({ k, eq: byKey[k].equity, marks: byKey[k].marks_json, dq: byKey[k].data_quality })));
R.check("outage_rows_are_audit_only_peak_zero",
  ["c1_throw", "c2_503", "c3_missing", "c4_nonpositive"].every((k) => byKey[k].equity_peak === 0 && byKey[k].drawdown === 0),
  ["c1_throw", "c2_503", "c3_missing", "c4_nonpositive"].map((k) => ({ k, peak: byKey[k].equity_peak, dd: byKey[k].drawdown })));
R.check("honest_error_codes",
  byKey.c1_throw.error_code && byKey.c2_503.error_code.includes("503") &&
  byKey.c3_missing.error_code && byKey.c4_nonpositive.error_code &&
  byKey.c2_503.http_status === 503 && byKey.c1_throw.http_status === null,
  { throw: byKey.c1_throw.error_code, http503: byKey.c2_503.error_code, http_status_503: byKey.c2_503.http_status,
    missing: byKey.c3_missing.error_code, nonpositive: byKey.c4_nonpositive.error_code });
R.check("recover_is_fresh_verified",
  byKey.c5_recover.data_quality === "VERIFIED" && byKey.c5_recover.freshness_verdict === "FRESH",
  { dq: byKey.c5_recover.data_quality, fresh: byKey.c5_recover.freshness_verdict });

// Peak uses VERIFIED only: c5 peak must equal c0 equity (the best VERIFIED),
// NOT anything from the outage rows.
const expPeak = byKey.c0_ok.equity;
const expDd = Number(div(sub(BigInt(expPeak), BigInt(byKey.c5_recover.equity)), BigInt(expPeak)));
R.check("recover_peak_uses_verified_only",
  byKey.c5_recover.equity_peak === expPeak && byKey.c5_recover.drawdown === expDd,
  { c0_equity: expPeak, recover_equity: byKey.c5_recover.equity, recover_peak: byKey.c5_recover.equity_peak,
    recover_dd: byKey.c5_recover.drawdown, expected_dd: expDd });

// ORDI / 1000SATS are honestly UNKNOWN on every cycle, with no prices stored.
R.check("unlisted_symbols_always_unknown",
  unlistedSnaps.length === 6 && unlistedSnaps.every((r) =>
    r.mark_quality === "unknown" && r.equity === 0 && r.marks_json === "{}"),
  { count: unlistedSnaps.length, sample: unlistedSnaps[0] && { q: unlistedSnaps[0].mark_quality, eq: unlistedSnaps[0].equity, err: unlistedSnaps[0].error_code } });
R.check("unlisted_symbols_no_venue_quotable",
  unlistedSnaps.every((r) => r.error_code === "NO_VENUE_QUOTABLE_SYMBOLS"),
  [...new Set(unlistedSnaps.map((r) => r.error_code))]);

// Replay (pure) reproduces all rows, including the UNKNOWN ones.
const rp = replay(s1Snaps, { initialCashFp: INITIAL_CASH_FP });
R.check("replay_all_rows_match_with_unknown", rp.allMatch && rp.count === 6, rp.rows.map((r) => ({ c: r.cycle_key, q: r.mark_quality, match: r.match })));

R.finish({
  cycles: cycles.map((c) => ({ key: c.key, mode: c.mode, written: c.summary.written, verified: c.summary.verified, unknown: c.summary.unknown })),
  s1_rows: s1Snaps.map((r) => ({ cycle: r.cycle_key, equity: r.equity, peak: r.equity_peak, dd: r.drawdown,
    q: r.mark_quality, dq: r.data_quality, err: r.error_code, http: r.http_status, marks: r.marks_json })),
  unlisted_rows: unlistedSnaps.map((r) => ({ cycle: r.cycle_key, q: r.mark_quality, eq: r.equity, err: r.error_code })),
  replay: rp,
  verdict_labels: {
    outage_fail_closed_and_no_backfill: "VERIFIED",
    ordi_sats_unknown: "VERIFIED (real api.binance.us Invalid-symbol probe in T0)",
    live_market_fetch_from_this_process: "UNVERIFIED / BLOCKED (local proxy cannot reach api.binance.us)",
  },
});
