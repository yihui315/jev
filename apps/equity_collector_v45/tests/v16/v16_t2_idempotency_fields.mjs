/**
 * QA-T2-1 - same cycle run 3 times: full-row hash (INCLUDING the 8 new columns)
 * must be byte-identical; a 4th run with different prices under the SAME
 * cycle_key must NOT rewrite the stored row (no price backfill).
 */
import {
  openDb, makeEnv, makeState, insertSession, insertAccount,
  controlledFetcher, REAL_BASE_PRICES, scalePrice, runCycle, rows, hashRow,
} from "../lib/harness.mjs";
import { v16Recorder, NEW_COLS } from "./lib.mjs";

const R = v16Recorder("v16_t2_idempotency_fields", { coverage: "QA-T2-1: idempotency with new columns" });
const T = 1770000000000;
const CYCLE_KEY = "same-cycle-v16";
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
    snapRow: s[0] || null, provRow: p[0] || null,
  };
}

const runs = [];
for (let i = 0; i < 3; i++) {
  const f = controlledFetcher({ nowMs: T, serverTime: T, prices: { BTCUSDT: REAL_BASE_PRICES.BTCUSDT } });
  const env = makeEnv(db, f, {});
  const summary = await runCycle(env, { db, fetcher: f, cycleKey: CYCLE_KEY, nowMs: T });
  runs.push({ i, summary, state: await snapshotState() });
}

R.check("first_run_writes_one", runs[0].summary.written === 1, { written: runs[0].summary.written });
R.check("repeats_write_zero", runs[1].summary.written === 0 && runs[2].summary.written === 0,
  runs.map((r) => r.summary.written));
R.check("provenance_row_count_stays_one", runs.every((r) => r.state.provCount === 1),
  runs.map((r) => r.state.provCount));
R.check("full_provenance_row_hash_identical_across_3_runs",
  runs[0].state.provHash === runs[1].state.provHash && runs[1].state.provHash === runs[2].state.provHash,
  runs.map((r) => r.state.provHash));
R.check("full_snapshot_row_hash_identical_across_3_runs",
  runs[0].state.snapHash === runs[1].state.snapHash && runs[1].state.snapHash === runs[2].state.snapHash,
  runs.map((r) => r.state.snapHash));

// Prove the hash REALLY covers the 8 new columns (strip them -> different hash).
const full = runs[0].state.provRow;
const stripped = { ...full };
for (const c of NEW_COLS) delete stripped[c];
const strippedHash = await hashRow([stripped]);
R.check("new_columns_contribute_to_row_hash",
  strippedHash !== runs[0].state.provHash && NEW_COLS.every((c) => Object.prototype.hasOwnProperty.call(full, c)),
  { fullHash: runs[0].state.provHash, strippedHash });
R.check("network_context_constant_across_3_runs",
  runs.every((r) => r.state.provRow.network_context === runs[0].state.provRow.network_context),
  runs.map((r) => r.state.provRow.network_context));
R.check("mark_contracts_json_constant_across_3_runs",
  runs[0].state.provRow.mark_contracts_json === runs[1].state.provRow.mark_contracts_json &&
  runs[1].state.provRow.mark_contracts_json === runs[2].state.provRow.mark_contracts_json,
  runs.map((r) => r.state.provRow.mark_contracts_json));

// STRENGTHENED: different prices under the SAME cycle_key must NOT overwrite.
const changedPrice = scalePrice(REAL_BASE_PRICES.BTCUSDT, 0.5);
const f4 = controlledFetcher({ nowMs: T, serverTime: T, prices: { BTCUSDT: changedPrice } });
const env4 = makeEnv(db, f4, {});
const s4 = await runCycle(env4, { db, fetcher: f4, cycleKey: CYCLE_KEY, nowMs: T });
const state4 = await snapshotState();
R.check("different_price_same_cycle_does_not_rewrite",
  s4.written === 0 && state4.provHash === runs[0].state.provHash,
  { written: s4.written, changedPrice, hashUnchanged: state4.provHash === runs[0].state.provHash });
R.check("no_price_backfill_into_stored_row",
  state4.snapRow.equity === runs[0].state.snapRow.equity &&
  state4.snapRow.marks_json === runs[0].state.snapRow.marks_json &&
  state4.provRow.mark_contracts_json === runs[0].state.provRow.mark_contracts_json,
  { storedEquity: state4.snapRow.equity, storedMarks: state4.snapRow.marks_json });

R.finish({ cycle_key: CYCLE_KEY });
