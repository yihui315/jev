/**
 * P4 手续费：复刻冻结引擎 paper-server.mjs 的手续费语义。
 *
 * 冻结引擎没有 Decimal 类（decimal.mjs 是函数式定点 API），其 applyFill 的
 * 真实语义为（paper-server.mjs:353-354）：
 *     notional = mul(qty, price)
 *     fee      = mul(notional, D(CFG.feeRate))
 * 因此等价实现为 mul(D(notional), D(__test.CFG.feeRate)) 再以 U() 取 8 位定点，
 * 即主理人伪代码 Decimal(notional).mul(CFG.feeRate).toDecimalPlaces(8) 的落地。
 * feeRate 一律通过 __test.CFG.feeRate 读取，绝不硬编码 0.001。
 */
import assert from "node:assert/strict";
import { D, mul, U } from "../../vendor/nr_trusted/functions/_lib/decimal.mjs";
import { __test } from "../../vendor/nr_trusted/functions/_lib/paper-server.mjs";

export const ENGINE_FEE_RATE = __test.CFG.feeRate; // "0.001"，来自冻结引擎

/** 返回与冻结引擎 fills[].fee 同型的 8 位定点字符串。 */
export function computeFee(notional) {
  return U(mul(D(notional), D(__test.CFG.feeRate)));
}

export function computeFeeNumber(notional) {
  return Number(computeFee(notional));
}

/**
 * 契约断言（fail-closed）：构造最小合法 book，调用冻结引擎
 * __test.simulateNewOrder(...)，断言 fills[0].fee 与 computeFee(qty*price)
 * 相等（容差 1e-8）。若最小 book 无法构造（无 fill），绝不退化成
 * 200*0.001=0.2 之类的弱测试，而是抛 UNVERIFIED 如实上报卡点。
 *
 * 返回值语义（防止与数据 provenance 撞车）：
 *   - status 恒为 "FEE_CONTRACT_MATCHES_FROZEN_ENGINE"，仅表达「computeFee 与
 *     冻结引擎 fee 的算术一致性」，不代表任何数据真实性验证。
 *   - provenance 恒为 "SYNTHETIC_FIXTURE"（本断言用的是本地合成最小 book）。
 */
export function assertEngineFeeContract() {
  const account = __test.createAccount();
  const qty = 0.001;
  const price = 101;
  const input = {
    clientOrderId: "p3-feecontract-0001",
    confirmationId: "p3-feecontract-conf-0001",
    symbol: "BTCUSDT",
    side: "BUY",
    type: "MARKET",
    quantity: qty,
    limitPrice: null,
  };
  const book = {
    venue: "fixture",
    symbol: "BTCUSDT",
    sequence: 1,
    receivedAt: new Date().toISOString(), // 当前时间，避开 maxBookAgeMs=5000 的 stale 拒绝
    bids: [[100, 1]],
    asks: [[price, 1]],
  };
  const filters = {
    minQty: 0.00001, maxQty: 10, stepSize: 0.00001,
    minPrice: 0.01, maxPrice: 1000000, tickSize: 0.01,
    minNotional: 0.001,
  };
  const context = { book, filters, marks: {} };

  let fills;
  try {
    fills = __test.simulateNewOrder(account, input, context).fills;
  } catch (e) {
    throw new Error("UNVERIFIED: 最小合法 book 无法构造 simulateNewOrder 成交：" + e.message);
  }
  if (!Array.isArray(fills) || fills.length === 0) {
    throw new Error("UNVERIFIED: 最小合法 book 未产生任何 fill，无法完成手续费契约断言");
  }
  const fillFee = fills[0].fee;
  const notional = qty * price;
  const computedFee = computeFee(notional);
  const delta = Math.abs(Number(fillFee) - Number(computedFee));
  assert.ok(delta <= 1e-8,
    `冻结引擎 fills[0].fee 与 computeFee(qty*price) 不等：fill=${fillFee} computed=${computedFee} delta=${delta}`);
  assert.equal(fillFee, computedFee, "8 位定点字符串应完全相等");
  return {
    status: "FEE_CONTRACT_MATCHES_FROZEN_ENGINE",
    provenance: "SYNTHETIC_FIXTURE",
    feeRate: ENGINE_FEE_RATE,
    qty, price, notional,
    fillFee, computedFee, delta,
    fills: fills.length,
    note: "SYNTHETIC_FIXTURE：本断言用的是本地合成最小 book，不是真实成交/真实行情；status 表达的是与冻结引擎的算术一致性，不代表任何数据真实性验证。",
  };
}
