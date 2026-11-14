// commercial/net-profit.mjs
//
// Phase G (commercial) — net-profit accounting primitive.
//
// HARD RULE (fail-closed): net profit is ONLY meaningful over REAL measured
// trading PnL AND REAL measured AI cost. This function REFUSES to compute on
// any non-"REAL" dataSource. Synthetic / mock / fixture figures must NEVER be
// netted into a commercial claim. Returning a number here is a promise that the
// inputs are real measurements — callers must not pass synthetic data and call
// it a result.
//
// Formula (single source of truth):
//   net = grossTradingPnl - fees - slippage - aiCost - opsCost

export const NET_PROFIT_FORMULA =
  "net = grossTradingPnl - fees - slippage - aiCost - opsCost";

/**
 * @typedef {Object} NetProfitInput
 * @property {number} [grossTradingPnl]  REAL gross trading PnL (after position close)
 * @property {number} [fees]             REAL exchange/commission fees paid
 * @property {number} [slippage]         REAL realized slippage cost
 * @property {number} [aiCost]           REAL measured AI inference cost
 * @property {number} [opsCost]          REAL measured operational cost
 * @property {string} [dataSource]       MUST be "REAL" or this throws
 */

/**
 * Compute commercial net profit.
 * @param {NetProfitInput} input
 * @returns {{grossTradingPnl:number,fees:number,slippage:number,aiCost:number,opsCost:number,net:number,positive:boolean,dataSource:string,formula:string}}
 */
export function computeNetProfit(input) {
  const {
    grossTradingPnl = 0,
    fees = 0,
    slippage = 0,
    aiCost = 0,
    opsCost = 0,
    dataSource = "UNKNOWN",
  } = input || {};

  for (const [k, v] of [
    ["grossTradingPnl", grossTradingPnl],
    ["fees", fees],
    ["slippage", slippage],
    ["aiCost", aiCost],
    ["opsCost", opsCost],
  ]) {
    if (typeof v !== "number" || !Number.isFinite(v)) {
      throw new TypeError(
        `commercial.computeNetProfit: '${k}' must be a finite number, got ${JSON.stringify(v)}`,
      );
    }
  }

  if (dataSource !== "REAL") {
    throw new Error(
      `commercial.computeNetProfit: REFUSES to compute on non-real data ` +
        `(dataSource=${JSON.stringify(dataSource)}). Net profit is only meaningful ` +
        `over REAL measured trading + REAL AI cost. Synthetic/mock figures must ` +
        `never be netted into a commercial claim.`,
    );
  }

  const net = grossTradingPnl - fees - slippage - aiCost - opsCost;
  return {
    grossTradingPnl,
    fees,
    slippage,
    aiCost,
    opsCost,
    net,
    positive: net > 0,
    dataSource,
    formula: NET_PROFIT_FORMULA,
  };
}
