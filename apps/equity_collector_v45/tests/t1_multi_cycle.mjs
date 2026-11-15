/**
 * T1 — Multi-cycle collection (3+ cycles). Equity / peak / drawdown must vary
 * with REAL-SHAPED market prices and be fully recomputable.
 *
 * Honesty note: base price 74951.77 is a REAL api.binance.us quote probed in
 * this session (T0 / WebFetch). Cycle-to-cycle price changes are CONTROLLED
 * test inputs (explicitly labelled) so the arithmetic is deterministic and
 * recomputable; they are NOT a claim of a live feed, because the local proxy
 * cannot reach api.binance.us. The collector itself never fabricates prices.
 */
import {
  openDb, makeEnv, makeState, insertSession, insertAccount,
  controlledFetcher, scalePrice, REAL_BASE_PRICES, runCycle, rows,
  newRecorder, hashRow,
} from "./lib/harness.mjs";
import { D, mul, add, div, sub } from "../src/vendor/decimal.mjs";
import { replay } from "../src/replay.mjs";

const R = newRecorder("T1_multi_cycle", {
  price_source: "REAL api.binance.us base quote (74951.77) + CONTROLLED deterministic cycle variation",
  note: "controlled variation is a test fixture, not a live-feed claim",
});

const T = 1770000000000;
const INITIAL_CASH_FP = Number(D(100000));
const QTY = 2;
const AVG = 70000;
const factors = [1.0, 1.05, 0.90, 0.80];

const db = openDb();
const st = await makeState({
  cash: 100000, initialCash: 100000,
  positions: { BTCUSDT: { qty: QTY, avgPrice: AVG } },
  generation: 1,
});
await insertSession(db, "s1", { nowMs: T });
await insertAccount(db, "s1", st.state_json, st.state_hash, { version: 1, nowMs: T });

const cycles = [];
for (let i = 0; i < factors.length; i++) {
  const nowMs = T + i * 60000;
  const price = scalePrice(REAL_BASE_PRICES.BTCUSDT, factors[i]);
  const fetcher = controlledFetcher({ nowMs, serverTime: nowMs, prices: { BTCUSDT: price } });
  const env = makeEnv(db, fetcher, {});
  const summary = await runCycle(env, { db, fetcher, cycleKey: `t1-c${i}`, nowMs });
  cycles.push({ i, nowMs, price, summary });
}

const snaps = await rows(db, "SELECT * FROM paper_equity_snapshots ORDER BY created_at ASC, snapshot_id ASC");
const provs = await rows(db, "SELECT * FROM v45_snapshot_provenance ORDER BY created_at ASC, snapshot_id ASC");

R.check("four_snapshots_written", snaps.length === 4, { count: snaps.length });
R.check("all_cycles_verified",
  cycles.every((c) => c.summary.verified === 1 && c.summary.unknown === 0 && c.summary.written === 1),
  cycles.map((c) => ({ cycle: c.i, written: c.summary.written, verified: c.summary.verified })));
R.check("no_failures", cycles.every((c) => c.summary.failures.length === 0), cycles.map((c) => c.summary.failures));

// Recompute expected equity / peak / drawdown independently with the SAME
// 1e8 fixed-point primitives the engine uses.
const expected = [];
let runningPeak = BigInt(INITIAL_CASH_FP);
for (const c of cycles) {
  const eq = add(D(100000), mul(D(QTY), D(c.price)));   // bigint, 1e8 fp
  const eqN = Number(eq);
  if (eq > runningPeak) runningPeak = eq;
  const dd = runningPeak > 0n && eq < runningPeak ? Number(div(sub(runningPeak, eq), runningPeak)) : 0;
  expected.push({ price: c.price, equity: eqN, peak: Number(runningPeak), drawdown: dd });
}
const actual = snaps.map((s) => ({ equity: s.equity, peak: s.equity_peak, drawdown: s.drawdown }));

R.check("equity_matches_recompute",
  actual.every((a, i) => a.equity === expected[i].equity),
  { expected: expected.map((e) => e.equity), actual: actual.map((a) => a.equity) });
R.check("equity_varies_with_market",
  new Set(actual.map((a) => a.equity)).size === factors.length,
  actual.map((a) => a.equity));
R.check("equity_peak_matches_recompute",
  actual.every((a, i) => a.peak === expected[i].peak),
  { expected: expected.map((e) => e.peak), actual: actual.map((a) => a.peak) });
R.check("drawdown_matches_recompute",
  actual.every((a, i) => a.drawdown === expected[i].drawdown),
  { expected: expected.map((e) => e.drawdown), actual: actual.map((a) => a.drawdown) });
R.check("drawdown_appears_and_grows_on_decline",
  actual[0].drawdown === 0 && actual[1].drawdown === 0 &&
  actual[2].drawdown > 0 && actual[3].drawdown > actual[2].drawdown,
  actual.map((a) => a.drawdown));

// Historical replay (pure) must reproduce every stored peak / drawdown.
const rp = replay(snaps, { initialCashFp: INITIAL_CASH_FP });
R.check("replay_all_rows_match", rp.allMatch && rp.count === 4, rp);
R.check("replay_peak_nonzero_for_verified", rp.rows.every((r) => r.recomputed_peak > 0), rp.rows.map((r) => r.recomputed_peak));

// Provenance completeness on EVERY row.
R.check("provenance_row_count", provs.length === 4, { count: provs.length });
R.check("provenance_complete_fields",
  provs.every((p) => p.engine_sha256 && p.collector_version && p.cycle_key &&
    p.data_quality === "VERIFIED" && p.freshness_verdict === "FRESH" &&
    p.http_status === 200 && p.payload_sha256 && p.source_host === "api.binance.us" &&
    p.endpoint === "/api/v3/ticker/price" && p.price_ts && p.fetched_at &&
    p.clock_skew_ms === 0 && p.error_code === null),
  provs.map((p) => ({ cycle: p.cycle_key, dq: p.data_quality, fresh: p.freshness_verdict, http: p.http_status, sha: !!p.payload_sha256 })));
R.check("state_hash_anchored", snaps.every((s) => s.state_hash === st.state_hash), { expectedHash: st.state_hash });
R.check("marks_json_records_real_price", snaps.every((s) => s.marks_json.includes("BTCUSDT") && s.marks_json !== "{}"), snaps.map((s) => s.marks_json));

R.finish({
  initial_cash_fp: INITIAL_CASH_FP,
  qty: QTY, avg_price: AVG,
  cycles: cycles.map((c) => ({ cycle_key: `t1-c${c.i}`, now_iso: new Date(c.nowMs).toISOString(), controlled_price: c.price, factor: factors[c.i] })),
  expected,
  actual,
  replay: rp,
  snapshot_hash: await hashRow(snaps.map((s) => ({ snapshot_id: s.snapshot_id, equity: s.equity, peak: s.equity_peak, dd: s.drawdown }))),
  verdict_labels: {
    multi_cycle_arithmetic: "VERIFIED (controlled real-shaped prices)",
    live_market_fetch_from_this_process: "UNVERIFIED / BLOCKED (local proxy cannot reach api.binance.us)",
  },
});
