/**
 * 四臂共享的内部工具（纯新建，位于 arms/ 子目录）。
 * 只读复用冻结引擎 __test（createAccount/summarize/CFG）与 decimal.mjs，
 * 手续费走 fee-wrap.computeFee（复刻引擎语义），对账走 metrics.reconcileEquity
 * （T5a/T8 语义，MATCH 由 delta 计算得出，非写死）。
 */
import { __test } from "../../../vendor/nr_trusted/functions/_lib/paper-server.mjs";
import { D, U, add, sub, mul, div, toNumber } from "../../../vendor/nr_trusted/functions/_lib/decimal.mjs";
import { computeFee } from "../fee-wrap.mjs";
import { reconcileEquity } from "../metrics.mjs";

export const SYMBOL = "BTCUSDT";

export function evidenceFields(timeline, costModelId) {
  return {
    data_mode: "FIXTURE",
    evidence_level: "SYNTHETIC_FIXTURE",
    isReal: false,
    venue: "fixture",
    seed: timeline.seed,
    timeline_sha256: timeline.timeline_sha256,
    no_synthetic_data: false,
    cost_model_id: costModelId,
  };
}

export function armResult(timeline, armId, costModelId, extra = {}) {
  const {
    status = "OK", reason = null, equity = [], fills = [], note = "",
    decisions_replayed = 0, synthetic_decisions_generated = 0,
  } = extra;
  return {
    arm_id: armId,
    status,
    reason,
    ...evidenceFields(timeline, costModelId),
    sample_foundation_note: timeline.sample_foundation_note,
    bar_count: timeline.bars,
    equity,
    fills,
    decisions_replayed,
    synthetic_decisions_generated,
    note,
  };
}

export function makeAccount() {
  return __test.createAccount();
}

/**
 * 以 bar.close 为成交价确定性买入 qty（MARKET 语义）。
 * 手续费 = computeFee(notional)（冻结引擎语义）；账户以定点字符串更新，
 * 以便 __test.summarize 能算出 engine equity（非硬编码）。
 */
export function buyAtMark(account, timeline, costModelId, armId, bar, qty, barIndex) {
  const price = bar.close;
  const notional = mul(D(qty), D(price));
  const fee = D(computeFee(toNumber(notional)));
  const cost = add(notional, fee);
  let pos = account.positions[SYMBOL] || { qty: "0", avgPrice: "0", reservedQty: "0" };
  const newQty = add(D(pos.qty), D(qty));
  const newAvg = div(add(mul(D(pos.qty), D(pos.avgPrice)), cost), newQty);
  pos.qty = U(newQty);
  pos.avgPrice = U(newAvg);
  account.positions[SYMBOL] = pos;
  account.cash = U(sub(D(account.cash), cost));
  account.trades.unshift({ id: `SYN-${armId}-${barIndex}`, fee: U(fee) });
  return {
    ...evidenceFields(timeline, costModelId),
    bar: barIndex,
    side: "BUY",
    qty,
    price,
    notional: toNumber(notional),
    fee: toNumber(fee),
  };
}

/** 按 bar.close 确定性平掉全部持仓（SELL 语义），手续费同样复刻引擎。 */
export function sellAtMark(account, timeline, costModelId, armId, bar, barIndex) {
  const price = bar.close;
  const pos = account.positions[SYMBOL];
  const q = pos ? D(pos.qty) : D(0);
  if (!(q > 0n)) return null;
  const notional = mul(q, D(price));
  const fee = D(computeFee(toNumber(notional)));
  const proceeds = sub(notional, fee);
  const realized = sub(proceeds, mul(D(pos.avgPrice), q));
  account.cash = U(add(D(account.cash), proceeds));
  account.realizedPnl = U(add(D(account.realizedPnl), realized));
  delete account.positions[SYMBOL];
  account.trades.unshift({ id: `SYN-${armId}-${barIndex}`, fee: U(fee) });
  return {
    ...evidenceFields(timeline, costModelId),
    bar: barIndex,
    side: "SELL",
    qty: toNumber(q),
    price,
    notional: toNumber(notional),
    fee: toNumber(fee),
    realized_pnl: toNumber(realized),
  };
}

/**
 * 权益快照：engine equity 由冻结引擎 summarize 计算，harness equity 由独立
 * 定点算式计算；reconcile 由 reconcileEquity 的 delta 得出（计算得出，非写死）。
 * 字段名对齐 tests/v16/lib.mjs SNAP_COLS 口径（equity/cash/market_value/...）。
 */
export function equityRecord(timeline, account, bar, costModelId, opts = {}) {
  const { version = 1 } = opts;
  const noFills = account.trades.length === 0;
  const marks = { [SYMBOL]: U(D(bar.close)) };
  const engine = __test.summarize(account, marks);
  const pos = account.positions[SYMBOL];
  const qty = pos ? D(pos.qty) : D(0);
  const harnessEquity = toNumber(add(D(account.cash), mul(qty, D(bar.close))));
  const { delta, reconcile } = reconcileEquity(engine.equity, harnessEquity);
  return {
    ...evidenceFields(timeline, costModelId),
    bar: bar.i,
    mark_price: bar.close,
    equity: engine.equity,
    cash: engine.cash,
    market_value: engine.marketValue,
    unrealized_pnl: engine.unrealizedPnl,
    realized_pnl: engine.realizedPnl,
    drawdown: engine.drawdown,
    reconcile,
    basis: {
      engine: { equity: engine.equity, version, trades: account.trades.length },
      harness: { equity: harnessEquity },
      delta,
    },
    noFills,
  };
}
