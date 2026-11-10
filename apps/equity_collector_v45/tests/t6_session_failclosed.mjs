/**
 * T6 — Expired / forged / missing Session => FAIL-CLOSED, ZERO snapshot writes.
 * Also: ENABLE_REAL_TRADING="true" red line aborts the cycle with zero writes.
 * A valid session is the control (must still write exactly one snapshot).
 */
import {
  openDb, makeEnv, makeState, insertSession, insertAccount,
  controlledFetcher, REAL_BASE_PRICES, runCycle, rows, newRecorder,
} from "./lib/harness.mjs";

const R = newRecorder("T6_session_failclosed", {
  cases: ["empty_db", "session_only_no_account", "expired_session", "revoked_session",
    "forged_session_id(ghost_account)", "real_trading_true_red_line", "valid_control"],
});
const T = 1770000000000;

async function scenario(name, setup, envOpts = {}) {
  const db = openDb();
  await setup(db);
  const base = controlledFetcher({ nowMs: T, serverTime: T, prices: { BTCUSDT: REAL_BASE_PRICES.BTCUSDT } });
  let calls = 0;
  const countingFetcher = async (u, i) => { calls++; return base(u, i); };
  const env = makeEnv(db, countingFetcher, envOpts);
  let summary = null, threw = null;
  try {
    summary = await runCycle(env, { db, fetcher: countingFetcher, cycleKey: "t6-" + name, nowMs: T });
  } catch (e) { threw = String((e && e.message) || e); }
  const snapCount = (await rows(db, "SELECT * FROM paper_equity_snapshots")).length;
  const provCount = (await rows(db, "SELECT * FROM v45_snapshot_provenance")).length;
  return { name, summary, threw, snapCount, provCount, fetcherCalls: calls };
}

const mkState = () => makeState({
  cash: 100000, initialCash: 100000,
  positions: { BTCUSDT: { qty: 1, avgPrice: 70000 } }, generation: 1,
});

const A = await scenario("empty_db", async () => {});
const B = await scenario("session_only_no_account", async (db) => {
  await insertSession(db, "s_valid", { nowMs: T });
});
const C = await scenario("expired_session", async (db) => {
  const st = await mkState();
  await insertSession(db, "s_exp", { nowMs: T, expiresInMs: -1000 });   // already expired
  await insertAccount(db, "s_exp", st.state_json, st.state_hash, { nowMs: T });
});
const D = await scenario("revoked_session", async (db) => {
  const st = await mkState();
  await insertSession(db, "s_rev", { nowMs: T, revokedAt: new Date(T - 1000).toISOString() });
  await insertAccount(db, "s_rev", st.state_json, st.state_hash, { nowMs: T });
});
const E = await scenario("forged_session_id", async (db) => {
  const st = await mkState();
  // Insert an account whose session_id has NO paper_sessions row (FK off).
  db.db.exec("PRAGMA foreign_keys = OFF;");
  await insertAccount(db, "ghost", st.state_json, st.state_hash, { nowMs: T });
  db.db.exec("PRAGMA foreign_keys = ON;");
});
const F = await scenario("real_trading_true", async (db) => {
  const st = await mkState();
  await insertSession(db, "s_ok", { nowMs: T });
  await insertAccount(db, "s_ok", st.state_json, st.state_hash, { nowMs: T });
}, { enableRealTrading: "true" });
const G = await scenario("valid_control", async (db) => {
  const st = await mkState();
  await insertSession(db, "s_ok", { nowMs: T });
  await insertAccount(db, "s_ok", st.state_json, st.state_hash, { nowMs: T });
});

const failClosed = [A, B, C, D, E];
R.check("all_fail_closed_cases_zero_snapshot_writes",
  failClosed.every((s) => s.snapCount === 0 && s.provCount === 0),
  failClosed.map((s) => ({ name: s.name, snap: s.snapCount, prov: s.provCount })));
R.check("fail_closed_cases_report_zero_sessions",
  failClosed.every((s) => s.summary && s.summary.sessions === 0 && s.summary.written === 0),
  failClosed.map((s) => ({ name: s.name, sessions: s.summary && s.summary.sessions, written: s.summary && s.summary.written })));
R.check("fail_closed_cases_never_call_market",
  failClosed.every((s) => s.fetcherCalls === 0),
  failClosed.map((s) => ({ name: s.name, calls: s.fetcherCalls })));
R.check("fail_closed_cases_do_not_throw", failClosed.every((s) => s.threw === null),
  failClosed.map((s) => ({ name: s.name, threw: s.threw })));

R.check("real_trading_true_red_line_aborts",
  F.threw && F.threw.includes("ENABLE_REAL_TRADING_MUST_BE_FALSE") && F.snapCount === 0 && F.provCount === 0,
  { threw: F.threw, snap: F.snapCount, prov: F.provCount });
R.check("real_trading_true_does_not_call_market", F.fetcherCalls === 0, { calls: F.fetcherCalls });

R.check("valid_control_writes_one_verified",
  G.threw === null && G.snapCount === 1 && G.provCount === 1 &&
  G.summary.written === 1 && G.summary.verified === 1 && G.summary.sessions === 1,
  { snap: G.snapCount, prov: G.provCount, written: G.summary && G.summary.written, verified: G.summary && G.summary.verified });

const all = [A, B, C, D, E, F, G];
R.check("no_fabricated_price_in_any_fail_closed_case",
  failClosed.concat([F]).every((s) => s.snapCount === 0),
  failClosed.map((s) => ({ name: s.name, snap: s.snapCount })));

R.finish({
  cases: all.map((s) => ({ name: s.name, threw: s.threw, snap: s.snapCount, prov: s.provCount,
    fetcherCalls: s.fetcherCalls, sessions: s.summary && s.summary.sessions, written: s.summary && s.summary.written })),
  verdict_labels: { session_fail_closed: "VERIFIED", real_trading_red_line: "VERIFIED" },
});
