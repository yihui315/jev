/**
 * T4 — Independent account isolation.
 * Two independent (session_id, generation) accounts: one LOSES, one PROFITS.
 * The profitable account is deliberately MUCH larger than the losing account's
 * peak, so any cross-account SUM / global-MAX bug would visibly inflate the
 * loser's peak and hide its drawdown. drawdown must stay independent.
 */
import {
  openDb, makeEnv, makeState, insertSession, insertAccount,
  controlledFetcher, REAL_BASE_PRICES, scalePrice, runCycle, rows,
  newRecorder,
} from "./lib/harness.mjs";
import { D, mul, add } from "../src/vendor/decimal.mjs";
import { replay } from "../src/replay.mjs";

const R = newRecorder("T4_account_isolation", {
  accounts: { s_loss: "BTCUSDT qty=1 (declining)", s_profit: "ETHUSDT qty=100 (rising, much larger equity)" },
});

const T = 1770000000000;
const INITIAL_CASH_FP = Number(D(100000));
const db = openDb();

const loss = await makeState({
  cash: 100000, initialCash: 100000,
  positions: { BTCUSDT: { qty: 1, avgPrice: 70000 } }, generation: 1,
});
await insertSession(db, "s_loss", { nowMs: T });
await insertAccount(db, "s_loss", loss.state_json, loss.state_hash, { version: 1, nowMs: T });

const profit = await makeState({
  cash: 100000, initialCash: 100000,
  positions: { ETHUSDT: { qty: 100, avgPrice: 2000 } }, generation: 1,
});
await insertSession(db, "s_profit", { nowMs: T });
await insertAccount(db, "s_profit", profit.state_json, profit.state_hash, { version: 1, nowMs: T });

const plan = [
  { key: "iso-c0", btc: 1.0, eth: 1.0 },
  { key: "iso-c1", btc: 0.8, eth: 1.2 },
  { key: "iso-c2", btc: 0.6, eth: 1.5 },
];
const cycles = [];
for (let i = 0; i < plan.length; i++) {
  const p = plan[i];
  const nowMs = T + i * 60000;
  const prices = {
    BTCUSDT: scalePrice(REAL_BASE_PRICES.BTCUSDT, p.btc),
    ETHUSDT: scalePrice(REAL_BASE_PRICES.ETHUSDT, p.eth),
  };
  const fetcher = controlledFetcher({ nowMs, serverTime: nowMs, prices });
  const env = makeEnv(db, fetcher, {});
  const summary = await runCycle(env, { db, fetcher, cycleKey: p.key, nowMs });
  cycles.push({ ...p, nowMs, prices, summary });
}

const lossSnaps = await rows(db,
  "SELECT * FROM paper_equity_snapshots WHERE session_id='s_loss' ORDER BY created_at ASC");
const profitSnaps = await rows(db,
  "SELECT * FROM paper_equity_snapshots WHERE session_id='s_profit' ORDER BY created_at ASC");
const allSnaps = await rows(db, "SELECT * FROM paper_equity_snapshots");

R.check("two_accounts_three_cycles_each",
  lossSnaps.length === 3 && profitSnaps.length === 3 && allSnaps.length === 6,
  { loss: lossSnaps.length, profit: profitSnaps.length, total: allSnaps.length });
R.check("all_verified", allSnaps.every((s) => s.mark_quality === "verified"), allSnaps.map((s) => s.mark_quality));

// Recompute each account's equity independently (1e8 fixed point).
const expLoss = cycles.map((c) => Number(add(D(100000), mul(D(1), D(c.prices.BTCUSDT)))));
const expProfit = cycles.map((c) => Number(add(D(100000), mul(D(100), D(c.prices.ETHUSDT)))));
R.check("loss_equity_recompute", lossSnaps.every((s, i) => s.equity === expLoss[i]), { expected: expLoss, actual: lossSnaps.map((s) => s.equity) });
R.check("profit_equity_recompute", profitSnaps.every((s, i) => s.equity === expProfit[i]), { expected: expProfit, actual: profitSnaps.map((s) => s.equity) });

R.check("loss_has_drawdown_that_grows",
  lossSnaps[0].drawdown === 0 && lossSnaps[1].drawdown > 0 && lossSnaps[2].drawdown > lossSnaps[1].drawdown,
  lossSnaps.map((s) => s.drawdown));
R.check("profit_has_zero_drawdown", profitSnaps.every((s) => s.drawdown === 0), profitSnaps.map((s) => s.drawdown));
R.check("profit_equity_strictly_rising", profitSnaps.every((s, i) => i === 0 || s.equity > profitSnaps[i - 1].equity), profitSnaps.map((s) => s.equity));

// THE isolation proof: the profit account is much larger than the loser's peak.
const lossPeak = lossSnaps[0].equity_peak;   // must stay == its own first equity
const maxProfitEquity = Math.max(...profitSnaps.map((s) => s.equity));
R.check("profit_account_is_larger_than_loss_peak", maxProfitEquity > lossPeak,
  { max_profit_equity: maxProfitEquity, loss_peak: lossPeak });
R.check("loss_peak_not_inflated_by_other_account",
  lossSnaps.every((s) => s.equity_peak === lossPeak) && !profitSnaps.some((s) => s.equity === lossPeak),
  { loss_peaks: lossSnaps.map((s) => s.equity_peak), loss_peak_expected: lossPeak, max_profit_equity: maxProfitEquity });
R.check("profit_peak_independent_and_rising",
  profitSnaps.every((s, i) => s.equity_peak === profitSnaps[i].equity),
  profitSnaps.map((s) => ({ eq: s.equity, peak: s.equity_peak })));

R.check("deterministic_ids_are_session_scoped",
  new Set(allSnaps.map((s) => s.snapshot_id)).size === 6 &&
  lossSnaps.every((s) => s.session_id === "s_loss") && profitSnaps.every((s) => s.session_id === "s_profit"),
  { unique_ids: new Set(allSnaps.map((s) => s.snapshot_id)).size });
R.check("one_snapshot_per_account_per_cycle",
  cycles.every((c) => c.summary.written === 2 && c.summary.sessions === 2),
  cycles.map((c) => ({ key: c.key, written: c.summary.written, sessions: c.summary.sessions })));

const rpLoss = replay(lossSnaps, { initialCashFp: INITIAL_CASH_FP });
const rpProfit = replay(profitSnaps, { initialCashFp: INITIAL_CASH_FP });
R.check("replay_loss_account_matches", rpLoss.allMatch && rpLoss.count === 3, rpLoss.rows);
R.check("replay_profit_account_matches", rpProfit.allMatch && rpProfit.count === 3, rpProfit.rows);

R.finish({
  cycles: cycles.map((c) => ({ key: c.key, btc: c.prices.BTCUSDT, eth: c.prices.ETHUSDT, written: c.summary.written })),
  loss: lossSnaps.map((s) => ({ equity: s.equity, peak: s.equity_peak, dd: s.drawdown })),
  profit: profitSnaps.map((s) => ({ equity: s.equity, peak: s.equity_peak, dd: s.drawdown })),
  isolation_proof: { loss_peak: lossPeak, max_profit_equity: maxProfitEquity, profit_larger: maxProfitEquity > lossPeak },
  replay: { loss: rpLoss, profit: rpProfit },
  verdict_labels: { account_isolation: "VERIFIED (controlled real-shaped prices)" },
});
