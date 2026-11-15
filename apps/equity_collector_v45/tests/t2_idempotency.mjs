/**
 * T2 — Idempotency of a SINGLE collection cycle.
 * The same cycle_key is run 3 times; snapshot rows must not increase and ALL
 * fields must be byte-identical (hash comparison). A 4th run with DIFFERENT
 * prices under the SAME cycle_key must ALSO leave the stored row unchanged,
 * proving INSERT OR IGNORE really prevents overwrites / last-good-price
 * backfill even when fresh market data differs.
 */
import {
  openDb, makeEnv, makeState, insertSession, insertAccount,
  controlledFetcher, REAL_BASE_PRICES, scalePrice, runCycle, rows,
  newRecorder, hashRow,
} from "./lib/harness.mjs";

const R = newRecorder("T2_idempotency", { cycle_key: "same-cycle", repeats: 3 });

const T = 1770000000000;
const CYCLE_KEY = "same-cycle";
const db = openDb();
const st = await makeState({
  cash: 100000, initialCash: 100000,
  positions: { BTCUSDT: { qty: 1, avgPrice: 70000 } }, generation: 1,
});
await insertSession(db, "s1", { nowMs: T });
await insertAccount(db, "s1", st.state_json, st.state_hash, { version: 1, nowMs: T });

async function snapshotState() {
  const s = await rows(db, "SELECT * FROM paper_equity_snapshots");
  const p = await rows(db, "SELECT * FROM v45_snapshot_provenance");
  return {
    snapCount: s.length, provCount: p.length,
    snapHash: await hashRow(s), provHash: await hashRow(p),
    snapshotId: s.length ? s[0].snapshot_id : null,
    snapRow: s[0] || null, provRow: p[0] || null,
  };
}

const runs = [];
for (let i = 0; i < 3; i++) {
  const fetcher = controlledFetcher({ nowMs: T, serverTime: T, prices: { BTCUSDT: REAL_BASE_PRICES.BTCUSDT } });
  const env = makeEnv(db, fetcher, {});
  const summary = await runCycle(env, { db, fetcher, cycleKey: CYCLE_KEY, nowMs: T });
  runs.push({ i, summary, state: await snapshotState() });
}

R.check("first_run_writes_one", runs[0].summary.written === 1, { written: runs[0].summary.written });
R.check("repeats_write_zero", runs[1].summary.written === 0 && runs[2].summary.written === 0,
  runs.map((r) => r.summary.written));
R.check("snapshot_row_count_stays_one", runs.every((r) => r.state.snapCount === 1),
  runs.map((r) => r.state.snapCount));
R.check("provenance_row_count_stays_one", runs.every((r) => r.state.provCount === 1),
  runs.map((r) => r.state.provCount));
R.check("all_fields_identical_across_3_runs",
  runs[0].state.snapHash === runs[1].state.snapHash && runs[1].state.snapHash === runs[2].state.snapHash &&
  runs[0].state.provHash === runs[1].state.provHash && runs[1].state.provHash === runs[2].state.provHash,
  { snapHashes: runs.map((r) => r.state.snapHash), provHashes: runs.map((r) => r.state.provHash) });
R.check("deterministic_snapshot_id_stable",
  new Set(runs.map((r) => r.state.snapshotId)).size === 1 && runs[0].state.snapshotId === runs[2].state.snapshotId,
  runs.map((r) => r.state.snapshotId));

// STRENGTHENED: different prices under the SAME cycle_key must NOT overwrite.
const changedPrice = scalePrice(REAL_BASE_PRICES.BTCUSDT, 0.5);
const fetcher4 = controlledFetcher({ nowMs: T, serverTime: T, prices: { BTCUSDT: changedPrice } });
const env4 = makeEnv(db, fetcher4, {});
const s4 = await runCycle(env4, { db, fetcher: fetcher4, cycleKey: CYCLE_KEY, nowMs: T });
const state4 = await snapshotState();
R.check("different_price_same_cycle_does_not_rewrite",
  s4.written === 0 && state4.snapCount === 1 &&
  state4.snapHash === runs[0].state.snapHash && state4.provHash === runs[0].state.provHash,
  { written: s4.written, changed_price: changedPrice, row_count: state4.snapCount, hash_unchanged: state4.snapHash === runs[0].state.snapHash });
R.check("no_price_backfill_into_stored_row",
  state4.snapRow.equity === runs[0].state.snapRow.equity &&
  state4.snapRow.marks_json === runs[0].state.snapRow.marks_json,
  { stored_equity: state4.snapRow.equity, stored_marks: state4.snapRow.marks_json });

R.finish({
  cycle_key: CYCLE_KEY,
  runs: runs.map((r) => ({ i: r.i, written: r.summary.written, verified: r.summary.verified,
    snapshot_id: r.state.snapshotId, snap_hash: r.state.snapHash, prov_hash: r.state.provHash })),
  hardened_run: { changed_price: changedPrice, written: s4.written, snap_hash: state4.snapHash, prov_hash: state4.provHash },
  final_snapshot: state4.snapRow,
  final_provenance: state4.provRow,
  verdict_labels: { idempotency: "VERIFIED" },
});
