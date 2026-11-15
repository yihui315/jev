// =============================================================================
// JEV PHASE D — Trading Closed-Loop Verification & Gap-Fill Tests
// =============================================================================
//
// Owner:   metric  (FORGE + METRIC for Phase D)
// Run:     node apps/command_center_3d/scripts/tests/phase-d/phase-d-verify.mjs
// Exit:    0 = all pass, 1 = any fail.
//
// DATA PROVENANCE (hard constraint from task):
//   * ALL market data below is MOCK / FIXTURE — synthetic numbers generated
//     IN-PROCESS. No outbound fetch, no paid market API, no real trading.
//   * ENABLE_REAL_TRADING is, by construction, never enabled here: we never call
//     fetch()/network and never place an order. Risk gating is exercised purely
//     on synthetic inputs.
//   * Where a module itself would call a live endpoint (fetchMarks/fetchMarket
//     Context) we DO NOT invoke it; we test the pure functions with injected
//     MOCK marks/contexts. This is verified — not impersonated as REAL.
//
// Components under test (read from the worktree, unmodified):
//   - apps/command_center_3d/server/engine/_lib/paper-server.mjs  (sim engine + risk caps)
//   - apps/command_center_3d/server/engine/_lib/equity-snapshot.mjs (equity drawdown, risk basis)
//   - apps/command_center_3d/server/ai/risk-gate.mjs            (decision risk gate)
//   - apps/command_center_3d/server/engine/_lib/paper-ledger.mjs (event ledger idempotency)
//   - apps/equity_collector_v45/src/market-data/provenance-guard.mjs (market provenance)
//   - apps/equity_collector_v45/src/replay.mjs                 (event replay recompute)
// =============================================================================

import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';
// Node 22 global crypto.subtle / randomUUID are used by the modules; ensure present.
if (!globalThis.crypto) globalThis.crypto = webcrypto;

import * as PS from '../../../server/engine/_lib/paper-server.mjs';   // __test exports
import * as ES from '../../../server/engine/_lib/equity-snapshot.mjs'; // __test exports
import * as RG from '../../../server/ai/risk-gate.mjs';
import * as PL from '../../../server/engine/_lib/paper-ledger.mjs';
import * as PG from '../../../../../apps/equity_collector_v45/src/market-data/provenance-guard.mjs';
import * as RP from '../../../../../apps/equity_collector_v45/src/replay.mjs';

const T = PS.__test; // CFG, createAccount, walkBook, eligibleLevels, summarize, simulateNewOrder, validateInput, updateRiskMarks, processLimitOrders

// ---------------------------------------------------------------- test harness
let PASS = 0, FAIL = 0;
const lines = [];
async function test(name, fn) {
  try { await fn(); PASS++; lines.push('PASS  ' + name); }
  catch (e) {
    FAIL++;
    lines.push('FAIL  ' + name + '\n        ' + (e && e.message ? e.message : String(e)));
  }
}
const approx = (a, b, eps = 1e-6) => Math.abs(a - b) <= eps;
const clone = (o) => structuredClone(o);

// ---------------------------------------------------------------- MOCK builders
// All MOCK: synthetic order book + marks, never from a live source.
function mockBook(symbol, asks, bids, receivedAtMs = Date.now()) {
  return {
    venue: 'binance-spot', symbol,
    sequence: 99001,
    receivedAt: new Date(receivedAtMs).toISOString(),
    bids, asks, source: 'MOCK_BOOK',
  };
}
function mockFilters(over = {}) {
  return {
    stepSize: 0.00001, minQty: 0.00001, maxQty: 1000,
    tickSize: 0.01, minPrice: 0.01, maxPrice: 1000000, minNotional: 5,
    ...over,
  };
}
// A fresh account with a crafted equityPeak / dayStartEquity for circuit-breaker tests.
function accountWith(over = {}) {
  const a = T.createAccount();
  return Object.assign(a, over);
}

// =============================================================================
// 1) REAL-TIME MARKET-DATA READ — PROVENANCE + TIMESTAMP/EXPIRY (reject stale/expired)
// =============================================================================
await test('1.1 provenance-guard: well-formed FIXTURE record is APPROVED', () => {
  const rec = PG.buildMarketEnvelope({
    provenance: PG.buildFixtureProvenance({ symbol: 'BTCUSDT', capturedAtMs: Date.now() }),
    symbol: 'BTCUSDT',
  });
  const r = PG.guardRecord(rec);
  assert.equal(r.approved, true, 'fixture record must pass: ' + JSON.stringify(r.reasons));
});

await test('1.2 provenance-guard: fixture data labelled REAL/LIVE is REJECTED (FIXTURE_MISLABELED_LIVE)', () => {
  // A record whose captureMode/source screams fixture but carries a live label.
  const rec = PG.buildMarketEnvelope({
    provenance: {
      venue: 'LOCAL_FIXTURE', symbol: 'BTCUSDT', capturedAtMs: Date.now(),
      source: 'jev-p1-local-fixture', isReal: true, dataMode: 'real',
      captureMode: 'local-fixture',
    },
    symbol: 'BTCUSDT',
  });
  const r = PG.guardRecord(rec);
  assert.equal(r.approved, false);
  assert.equal(r.code, PG.GUARD_CODES.FIXTURE_MISLABELED_LIVE, 'red line: synthetic must never wear a live label');
});

await test('1.3 provenance-guard: missing provenance / bad venue / bad symbol rejected', () => {
  assert.equal(PG.guardRecord({ symbol: 'BTCUSDT' }).approved, false);
  assert.equal(PG.guardRecord({ symbol: 'BTCUSDT' }).code, PG.GUARD_CODES.PROVENANCE_MISSING);
  // bad venue
  const r1 = PG.guardRecord(PG.buildMarketEnvelope({
    provenance: PG.buildFixtureProvenance({ venue: 'SomeScaryExchange', symbol: 'BTCUSDT', capturedAtMs: Date.now() }),
    symbol: 'BTCUSDT',
  }));
  assert.equal(r1.code, PG.GUARD_CODES.VENUE_NOT_ALLOWLISTED);
  // bad symbol
  const r2 = PG.guardRecord(PG.buildMarketEnvelope({
    provenance: PG.buildFixtureProvenance({ symbol: 'DOGEUSDT', capturedAtMs: Date.now() }),
    symbol: 'DOGEUSDT',
  }));
  assert.equal(r2.code, PG.GUARD_CODES.SYMBOL_INVALID);
});

await test('1.4 provenance-guard: NOT_MEASURED field filled with a number is REJECTED (fabrication)', () => {
  const rec = PG.buildMarketEnvelope({
    provenance: PG.buildFixtureProvenance({ symbol: 'BTCUSDT', capturedAtMs: Date.now(), notMeasuredFields: ['price'] }),
    symbol: 'BTCUSDT',
    price: 50000, // declared NOT_MEASURED yet a number is present
  });
  const r = PG.guardRecord(rec);
  assert.equal(r.approved, false);
  assert.equal(r.code, PG.GUARD_CODES.MEASURED_VALUE_FABRICATED);
});

await test('1.5 provenance-guard: MEASURED field missing value is REJECTED', () => {
  const rec = PG.buildMarketEnvelope({
    provenance: PG.buildFixtureProvenance({ symbol: 'BTCUSDT', capturedAtMs: Date.now() }),
    symbol: 'BTCUSDT',
    measurementStatus: { price: 'MEASURED' },
    price: null, // declared measured but null
  });
  const r = PG.guardRecord(rec);
  assert.equal(r.approved, false);
  assert.equal(r.code, PG.GUARD_CODES.MEASURED_VALUE_MISSING);
});

await test('1.6 envelope: wrong hash_scope / algorithm / missing context id / db_persisted REJECTED', () => {
  const base = () => PG.buildMarketEnvelope({
    provenance: PG.buildFixtureProvenance({ symbol: 'BTCUSDT', capturedAtMs: Date.now() }),
    symbol: 'BTCUSDT',
  });
  let r = PG.validateMarketEnvelope({ ...base(), hash_scope: 'canonical_row' });
  assert.equal(r.ok, false); assert.equal(r.code, PG.GUARD_CODES.HASH_SCOPE_FORBIDDEN);

  r = PG.validateMarketEnvelope({ ...base(), hash_algorithm: 'md5' });
  assert.equal(r.code, PG.GUARD_CODES.HASH_ALGORITHM_INVALID);

  r = PG.validateMarketEnvelope({ ...base(), market_context_id: '' });
  assert.equal(r.code, PG.GUARD_CODES.MARKET_CONTEXT_ID_MISSING);

  r = PG.validateMarketEnvelope({ ...base(), store_kind: 'd1', db_persisted: true });
  assert.equal(r.code, PG.GUARD_CODES.STORE_BINDING_INVALID);

  r = PG.validateMarketEnvelope({ ...base(), marketSnapshotHash: 'not-a-hash' });
  assert.equal(r.code, PG.GUARD_CODES.MARKET_SNAPSHOT_HASH_INVALID);
});

await test('1.7 risk-gate: STALE market context (price_ts too old) -> CONTEXT_STALE', () => {
  const decision = mkDecision({ action: 'BUY', symbol: 'BTCUSDT', quantity: 0.01, orderType: 'MARKET' });
  const ctx = mkContext({ symbol: 'BTCUSDT', price: 50000, priceTsMs: Date.now() - 120000, markQuality: 'verified' });
  const r = RG.riskCheck({ decision, context: ctx, nowMs: Date.now() });
  assert.equal(r.approved, false);
  assert.equal(r.code, RG.RISK_CODES.CONTEXT_STALE, 'stale context must be rejected');
});

await test('1.8 risk-gate: EXPIRED market context (expires_at in past) -> CONTEXT_EXPIRED', () => {
  const decision = mkDecision({ action: 'BUY', symbol: 'BTCUSDT', quantity: 0.01, orderType: 'MARKET' });
  const ctx = mkContext({ symbol: 'BTCUSDT', price: 50000, priceTsMs: Date.now(), markQuality: 'verified',
    expiresAt: new Date(Date.now() - 1000).toISOString() });
  const r = RG.riskCheck({ decision, context: ctx, nowMs: Date.now() });
  assert.equal(r.approved, false);
  assert.equal(r.code, RG.RISK_CODES.CONTEXT_EXPIRED);
});

await test('1.9 risk-gate: FUTURE price_ts beyond skew -> CONTEXT_INVALID_TS', () => {
  const decision = mkDecision({ action: 'BUY', symbol: 'BTCUSDT', quantity: 0.01, orderType: 'MARKET' });
  const ctx = mkContext({ symbol: 'BTCUSDT', price: 50000, priceTsMs: Date.now() + 60000, markQuality: 'verified' });
  const r = RG.riskCheck({ decision, context: ctx, nowMs: Date.now() });
  assert.equal(r.approved, false);
  assert.equal(r.code, RG.RISK_CODES.CONTEXT_INVALID_TS);
});

await test('1.10 risk-gate: unverified mark_quality -> CONTEXT_NOT_VERIFIED', () => {
  const decision = mkDecision({ action: 'BUY', symbol: 'BTCUSDT', quantity: 0.01, orderType: 'MARKET' });
  const ctx = mkContext({ symbol: 'BTCUSDT', price: 50000, priceTsMs: Date.now(), markQuality: 'unknown' });
  const r = RG.riskCheck({ decision, context: ctx, nowMs: Date.now() });
  assert.equal(r.approved, false);
  assert.equal(r.code, RG.RISK_CODES.CONTEXT_NOT_VERIFIED);
});

await test('1.11 paper-server: STALE order book (receivedAt > maxBookAgeMs) -> MARKET_BOOK_STALE', () => {
  const account = T.createAccount();
  const book = mockBook('BTCUSDT', [[50010, 10], [50020, 10]], [[50000, 10]], Date.now() - 6000); // 6s old > 5s
  const ctx = { book, filters: mockFilters(), marks: {} };
  const input = T.validateInput({ clientOrderId: 'c-STALE-0001', confirmationId: 'k-STALE-0001',
    symbol: 'BTCUSDT', side: 'BUY', type: 'MARKET', quantity: 0.01 });
  assert.throws(() => T.simulateNewOrder(account, input, ctx), /MARKET_BOOK_STALE/);
});

await test('1.12 equity-snapshot: STALE equity snapshot in risk basis -> not usable', async () => {
  const db = fakeDb(
    [{ session_id: 's1', state_json: JSON.stringify({ generation: 1, positions: {} }) }],
    { s1: [
      { equity: 1_000_000_000_000, equity_peak: 1_000_000_000_000, drawdown: 0,
        created_at: new Date(Date.now() - (ES.MAX_SNAPSHOT_AGE_MS + 2000)).toISOString() },
      { equity: 1_000_000_000_000, equity_peak: 1_000_000_000_000, drawdown: 0,
        created_at: new Date(Date.now() - (ES.MAX_SNAPSHOT_AGE_MS + 1000)).toISOString() },
    ] },
  );
  const basis = await ES.buildEquityRiskBasis(db, { nowMs: Date.now() });
  assert.equal(basis.usable, false);
  assert.ok(String(basis.reason).includes('STALE_EQUITY_SNAPSHOT'), 'stale snapshot must fail the risk basis: ' + basis.reason);
});

await test('1.13 equity-snapshot: too few verified snapshots -> not usable', async () => {
  const db = fakeDb(
    [{ session_id: 's1', state_json: JSON.stringify({ generation: 1, positions: {} }) }],
    { s1: [{ equity: 1_000_000_000_000, equity_peak: 1_000_000_000_000, drawdown: 0,
      created_at: new Date().toISOString() }] }, // only 1 (< MIN_VERIFIED_SNAPSHOTS=2)
  );
  const basis = await ES.buildEquityRiskBasis(db, { nowMs: Date.now() });
  assert.equal(basis.usable, false);
  assert.ok(String(basis.reason).includes('INSUFFICIENT_VERIFIED_SNAPSHOTS'));
});

// =============================================================================
// 2) SERVER-AUTHORITATIVE SIMULATION MATCHING (orders/fills, partial, concurrency)
// =============================================================================
await test('2.1 walkBook: full fill walks the book and matches price levels', () => {
  const asks = [[50010, 1], [50020, 5], [50030, 10]];
  const r = T.walkBook(asks, 0.5);
  assert.equal(r.filledQty, 0.5);
  assert.equal(r.remainingQty, 0);
  assert.equal(r.fills.length, 1);
  assert.equal(r.fills[0].price, 50010);
  assert.equal(r.fills[0].qty, 0.5);
  // notional = qty * price, 1e8 scaled fixed point
  assert.equal(r.notional, 2500500000000n);
});

await test('2.2 walkBook: PARTIAL fill when book depth is insufficient', () => {
  const asks = [[50010, 1], [50020, 1]]; // only 2 available
  const r = T.walkBook(asks, 5);
  assert.equal(r.filledQty, 2);
  assert.equal(r.remainingQty, 3);
  assert.equal(r.fills.length, 2);
});

await test('2.3 eligibleLevels: LIMIT buy only consumes asks at or below limit price', () => {
  const book = { asks: [[50010, 1], [50020, 1], [50030, 1]] };
  const elig = T.eligibleLevels('BUY', 'LIMIT', 50015, book);
  assert.deepEqual(elig, [[50010, 1]]); // 50020 and 50030 exceed the 50015 limit
  // MARKET ignores the limit
  assert.deepEqual(T.eligibleLevels('BUY', 'MARKET', 0, book), book.asks);
});

await test('2.4 simulateNewOrder MARKET BUY: full fill, fees charged at 0.1%, cash & position updated', () => {
  const account = T.createAccount();              // cash = 10000 (1e8 scaled)
  const book = mockBook('BTCUSDT', [[50010, 1], [50020, 5]], [[50000, 1]]);
  const ctx = { book, filters: mockFilters(), marks: {} };
  const input = T.validateInput({ clientOrderId: 'c-BUY-0001', confirmationId: 'k-BUY-0001',
    symbol: 'BTCUSDT', side: 'BUY', type: 'MARKET', quantity: 0.01 });
  const { order, fills } = T.simulateNewOrder(account, input, ctx);

  assert.equal(order.status, 'FILLED');
  assert.equal(Number(order.filledQty), 0.01);
  assert.equal(fills.length, 1);
  assert.equal(Number(fills[0].price), 50010);
  // fee = notional * feeRate(0.001)
  assert.ok(approx(Number(fills[0].fee), 0.01 * 50010 * 0.001, 1e-4), 'fee must be notional*0.001, got ' + fills[0].fee);
  assert.ok(approx(Number(fills[0].fee) / Number(fills[0].notional), 0.001, 1e-9), 'fee/notional ratio must be 0.001');
  // server-authoritative price from the book, not a mid quote
  assert.equal(fills[0].liquidityModel, 'server-visible-orderbook-walk');
  assert.equal(Number(fills[0].bookSequence), 99001);
  // position created, cash reduced by (notional + fee)
  assert.equal(Number(account.positions['BTCUSDT'].qty), 0.01);
  const expectedCash = 10000 - (0.01 * 50010) - Number(fills[0].fee);
  assert.ok(approx(Number(account.cash), expectedCash, 1e-3), 'cash should be ' + expectedCash + ' got ' + account.cash);
});

await test('2.5 simulateNewOrder MARKET BUY with thin book: PARTIALLY_FILLED + cancelled remainder', () => {
  const account = T.createAccount();
  // Low-priced synthetic book so notional (qty 1 * ~100) stays within the 10% cap (1000 USD).
  const book = mockBook('BTCUSDT', [[100, 0.2], [101, 0.2]], [[99, 1]]); // only 0.4 avail
  const ctx = { book, filters: mockFilters(), marks: {} };
  const input = T.validateInput({ clientOrderId: 'c-PART-0001', confirmationId: 'k-PART-0001',
    symbol: 'BTCUSDT', side: 'BUY', type: 'MARKET', quantity: 1 });
  const { order } = T.simulateNewOrder(account, input, ctx);
  assert.equal(order.status, 'PARTIALLY_FILLED');
  assert.equal(Number(order.filledQty), 0.4);
  assert.equal(Number(order.cancelledQty), 0.6);
  assert.equal(order.remainingQty, '0');
});

await test('2.6 simulateNewOrder LIMIT BUY partial: reserves cash for remaining qty', () => {
  const account = T.createAccount();
  // Low-priced book; limit 100.5 only admits the 0.2 @100 level -> 0.2 filled, 0.3 reserved.
  const book = mockBook('BTCUSDT', [[100, 0.2], [101, 5]], [[99, 1]]);
  const ctx = { book, filters: mockFilters(), marks: {} };
  const input = T.validateInput({ clientOrderId: 'c-LIM-0001', confirmationId: 'k-LIM-0001',
    symbol: 'BTCUSDT', side: 'BUY', type: 'LIMIT', quantity: 0.5, limitPrice: 100.5 });
  const { order } = T.simulateNewOrder(account, input, ctx);
  assert.equal(order.status, 'PARTIALLY_FILLED');
  assert.equal(Number(order.filledQty), 0.2);
  assert.ok(Number(order.reservedCash) > 0, 'remaining limit buy must reserve cash');
  assert.ok(Number(account.reservedCash) > 0);
});

await test('2.7 simulateNewOrder SELL with insufficient position -> rejected (no naked short)', () => {
  const account = T.createAccount(); // no positions
  const book = mockBook('BTCUSDT', [[50010, 5]], [[50000, 5]]);
  const ctx = { book, filters: mockFilters(), marks: { BTCUSDT: 50000 } };
  const input = T.validateInput({ clientOrderId: 'c-SELL-0001', confirmationId: 'k-SELL-0001',
    symbol: 'BTCUSDT', side: 'SELL', type: 'MARKET', quantity: 0.01 });
  assert.throws(() => T.simulateNewOrder(account, input, ctx), /没有足够可卖持仓|不支持裸卖空/);
});

await test('2.8 slippage: larger market order walks deeper and fills at a HIGHER average price', () => {
  const asks = [[50010, 1], [51000, 1], [52000, 10]];
  const small = T.walkBook(asks, 0.5);  // 0.5 @ 50010
  const big = T.walkBook(asks, 1.5);    // 1 @ 50010 + 0.5 @ 51000
  assert.ok(big.avgPrice > small.avgPrice, 'larger qty must slip to higher avg price');
  assert.equal(small.fills[0].price, 50010);
  assert.equal(big.fills[1].price, 51000);
});

await test('2.9 determinism / concurrency-safety: identical inputs yield identical fills', () => {
  const run = () => {
    const account = T.createAccount();
    const book = mockBook('BTCUSDT', [[50010, 1], [50020, 5]], [[50000, 1]]);
    const ctx = { book: clone(book), filters: mockFilters(), marks: {} };
    const input = T.validateInput({ clientOrderId: 'c-DET-0001', confirmationId: 'k-DET-0001',
      symbol: 'BTCUSDT', side: 'BUY', type: 'MARKET', quantity: 0.01 });
    const { order, fills } = T.simulateNewOrder(account, input, ctx);
    return { status: order.status, filledQty: order.filledQty, fillSig: fills.map((f) => [f.price, f.qty, f.fee]) };
  };
  assert.deepEqual(run(), run(), 'simulation must be deterministic across runs (no hidden time/random in matching)');
});

// =============================================================================
// 3) ACCOUNT / POSITION / EQUITY SNAPSHOTS / FEES / SLIPPAGE / NET P&L
// =============================================================================
await test('3.1 summarize: equity = cash + marketValue; totalPnl = equity - initialCash', () => {
  const account = T.createAccount();
  account.positions = { BTCUSDT: { qty: '1', avgPrice: '5000.00000000', reservedQty: '0' } }; // qty1 @ 5000
  const summary = T.summarize(account, { BTCUSDT: 6000 }); // mark 6000 -> MV 6000
  assert.ok(approx(summary.equity, 10000 + 6000, 1e-3));
  assert.ok(approx(summary.marketValue, 6000, 1e-3));
  assert.ok(approx(summary.totalPnl, 6000, 1e-3), 'total P&L should be +6000');
  assert.ok(approx(summary.unrealizedPnl, 1000, 1e-3), 'unrealized P&L should be (6000-5000)=1000');
  assert.ok(approx(summary.exposurePct, 6000 / 16000, 1e-6));
});

await test('3.2 net P&L on SELL: realized P&L = (proceeds - fee) - costBasis', () => {
  const account = T.createAccount();
  // seed a long position: qty 0.2 @ avg 5000 (=5000.00000000). notional 1000 stays within 10% of equity (11000).
  account.positions = { BTCUSDT: { qty: '0.2', avgPrice: '5000.00000000', reservedQty: '0' } };
  const book = mockBook('BTCUSDT', [[5100, 5]], [[5000, 5]]); // sell into bid 5000
  const ctx = { book, filters: mockFilters(), marks: { BTCUSDT: 5000 } };
  const input = T.validateInput({ clientOrderId: 'c-SELLPL-0001', confirmationId: 'k-SELLPL-0001',
    symbol: 'BTCUSDT', side: 'SELL', type: 'MARKET', quantity: 0.2 });
  const { order, fills } = T.simulateNewOrder(account, input, ctx);
  assert.equal(order.status, 'FILLED');
  // notional = 0.2*5000 = 1000; fee = 1; proceeds = 999; costBasis = 1000 -> realized = -1
  assert.ok(approx(Number(account.realizedPnl), -1, 1e-3), 'realized P&L should be -1, got ' + account.realizedPnl);
  assert.ok(approx(fills[0].realizedPnl, -1, 1e-3));
  assert.equal(Object.keys(account.positions).length, 0, 'position fully closed -> removed');
});

await test('3.3 equity-snapshot computeEquitySnapshot: drawdown = (peak-equity)/peak (1e8 scaled)', () => {
  const accountRow = {
    session_id: 's1', version: 1,
    state_json: JSON.stringify({
      generation: 1, cash: '9000.00000000', initialCash: '10000.00000000',
      positions: { BTCUSDT: { qty: '1', avgPrice: '2000.00000000', reservedQty: '0' } },
      realizedPnl: '0.00000000', equityPeak: '10000.00000000',
    }),
    state_hash: 'h',
  };
  const marksInfo = { marks: { BTCUSDT: 1000 }, marksJson: '{}', markQuality: 'verified', source: 'MOCK', missing: [] };
  const snap = ES.computeEquitySnapshot(accountRow, marksInfo, 1200000000000); // historyPeak 12000
  // equity = 9000 cash + 1*1000 mark = 10000 USD -> stored as 1e8 scaled integer 1000000000000
  assert.equal(snap.equity, 1000000000000);
  // drawdown = (12000-10000)/12000 = 0.1666.. -> 1e8 scaled = 16666667
  assert.equal(snap.drawdown, 16666667, 'drawdown must be (peak-equity)/peak in 1e8 fixed point');
  assert.equal(snap.markQuality, 'verified');
  assert.equal(snap.usable, true);
});

await test('3.4 equity-snapshot: MISSING real mark -> usable:false (fail-closed, never synthetic mark)', () => {
  const accountRow = {
    session_id: 's1', version: 1,
    state_json: JSON.stringify({
      generation: 1, cash: '10000.00000000', initialCash: '10000.00000000',
      positions: { BTCUSDT: { qty: '1', avgPrice: '5000.00000000', reservedQty: '0' } },
      realizedPnl: '0.00000000', equityPeak: '10000.00000000',
    }),
    state_hash: 'h',
  };
  // mark missing for BTCUSDT
  const marksInfo = { marks: {}, marksJson: '{}', markQuality: 'verified', source: 'MOCK', missing: ['BTCUSDT'] };
  const snap = ES.computeEquitySnapshot(accountRow, marksInfo);
  assert.equal(snap.usable, false);
  assert.ok(String(snap.reason).includes('MISSING_REAL_MARK'), 'missing real mark must fail closed: ' + snap.reason);
  assert.equal(snap.markQuality, 'unknown');
});

await test('3.5 equity-snapshot: fresh risk basis over 2 verified snapshots is usable with worst-account drawdown', async () => {
  const db = fakeDb(
    [{ session_id: 's1', state_json: JSON.stringify({ generation: 1, positions: {} }) }],
    { s1: [
      { equity: 1_000_000_000_000, equity_peak: 1_000_000_000_000, drawdown: 0, created_at: new Date(Date.now() - 2000).toISOString() },
      { equity: 900_000_000_000, equity_peak: 1_000_000_000_000, drawdown: 10000000, created_at: new Date().toISOString() },
    ] },
  );
  const basis = await ES.buildEquityRiskBasis(db, { nowMs: Date.now() });
  assert.equal(basis.usable, true);
  assert.equal(basis.riskBasis, 'mark-to-market-equity-snapshots');
  assert.ok(basis.max_drawdown_pct >= 10000000, 'worst-account drawdown must surface: ' + basis.max_drawdown_pct);
});

// =============================================================================
// 4) RISK ENGINE — INDEPENDENT tests (drawdown cap, exposure cap, circuit breaker) — fail-closed
// =============================================================================
await test('4.1 circuit breaker: drawdown cap breached -> new BUY rejected (drawdownLocked)', () => {
  const account = accountWith({ equityPeak: '12000.00000000' }); // peak 12000 > equity 10000 -> dd ~16.7% > 10% cap
  const book = mockBook('BTCUSDT', [[50010, 1], [50020, 5]], [[50000, 1]]);
  const ctx = { book, filters: mockFilters(), marks: {} };
  const input = T.validateInput({ clientOrderId: 'c-DD-0001', confirmationId: 'k-DD-0001',
    symbol: 'BTCUSDT', side: 'BUY', type: 'MARKET', quantity: 0.01 });
  assert.throws(() => T.simulateNewOrder(account, input, ctx), /回撤|熔断|DRAWDOWN/i, 'buy must be halted when drawdown cap breached');
  // and the summary flag is set
  const s = T.updateRiskMarks(account, {});
  assert.equal(s.drawdownLocked, true);
  assert.ok(s.drawdown >= 0.10, 'drawdown must exceed the 10% cap (fraction), got ' + s.drawdown);
});

await test('4.2 circuit breaker: daily-loss cap breached -> new BUY rejected (dailyLossLocked)', () => {
  const account = accountWith({ dayKey: new Date().toISOString().slice(0, 10), dayStartEquity: '12000.00000000' });
  // equity (cash only) = 10000; dailyPnl = -2000 < -3%*12000 = -360 -> locked
  const book = mockBook('BTCUSDT', [[50010, 1]], [[50000, 1]]);
  const ctx = { book, filters: mockFilters(), marks: {} };
  const input = T.validateInput({ clientOrderId: 'c-DL-0001', confirmationId: 'k-DL-0001',
    symbol: 'BTCUSDT', side: 'BUY', type: 'MARKET', quantity: 0.01 });
  assert.throws(() => T.simulateNewOrder(account, input, ctx), /亏损|熔断|DAILY/i, 'buy must be halted on daily-loss circuit breaker');
  const s = T.updateRiskMarks(account, {});
  assert.equal(s.dailyLossLocked, true);
});

await test('4.3 exposure cap: existing position already exceeds 40% exposure -> new BUY rejected', () => {
  const account = accountWith({ positions: { BTCUSDT: { qty: '1', avgPrice: '700000000000', reservedQty: '0' } } });
  const book = mockBook('BTCUSDT', [[7000, 5]], [[6900, 5]]);
  const ctx = { book, filters: mockFilters(), marks: { BTCUSDT: 7000 } };
  const input = T.validateInput({ clientOrderId: 'c-EXP-0001', confirmationId: 'k-EXP-0001',
    symbol: 'BTCUSDT', side: 'BUY', type: 'MARKET', quantity: 0.01 });
  assert.throws(() => T.simulateNewOrder(account, input, ctx), /敞口|EXPOSURE/i, 'buy must be rejected when exposure cap would be exceeded');
});

await test('4.4 exposure cap is BUY-only: SELL of an existing position still allowed when exposure high', () => {
  const account = accountWith({ positions: { BTCUSDT: { qty: '1', avgPrice: '700000000000', reservedQty: '0' } } });
  const book = mockBook('BTCUSDT', [[7100, 5]], [[7000, 5]]);
  const ctx = { book, filters: mockFilters(), marks: { BTCUSDT: 7000 } };
  const input = T.validateInput({ clientOrderId: 'c-EXPS-0001', confirmationId: 'k-EXPS-0001',
    symbol: 'BTCUSDT', side: 'SELL', type: 'MARKET', quantity: 0.01 });
  const { order } = T.simulateNewOrder(account, input, ctx); // must NOT throw
  assert.equal(order.status, 'FILLED');
});

await test('4.5 risk-gate: notional cap (max 1000 USD) and min notional enforced', () => {
  // over the cap
  const big = RG.riskCheck({ decision: mkDecision({ action: 'BUY', symbol: 'BTCUSDT', quantity: 1, orderType: 'MARKET' }),
    context: mkContext({ symbol: 'BTCUSDT', price: 50000, priceTsMs: Date.now(), markQuality: 'verified' }), nowMs: Date.now() });
  assert.equal(big.approved, false);
  assert.equal(big.code, RG.RISK_CODES.LIMIT_EXCEEDED);
  // under the min notional
  const tiny = RG.riskCheck({ decision: mkDecision({ action: 'BUY', symbol: 'BTCUSDT', quantity: 0.00001, orderType: 'MARKET' }),
    context: mkContext({ symbol: 'BTCUSDT', price: 50000, priceTsMs: Date.now(), markQuality: 'verified' }), nowMs: Date.now() });
  assert.equal(tiny.code, RG.RISK_CODES.NOTIONAL_TOO_SMALL);
});

await test('4.6 risk-gate: symbol not on cloud whitelist rejected; confidence IGNORED (0.99 == 0.01)', () => {
  const offSym = RG.riskCheck({ decision: mkDecision({ action: 'BUY', symbol: 'SOLUSDT', quantity: 0.01, orderType: 'MARKET' }),
    context: mkContext({ symbol: 'SOLUSDT', price: 100, priceTsMs: Date.now(), markQuality: 'verified' }), nowMs: Date.now() });
  assert.equal(offSym.code, RG.RISK_CODES.SYMBOL_NOT_ALLOWED);

  const mk = (conf) => RG.riskCheck({ decision: mkDecision({ action: 'BUY', symbol: 'BTCUSDT', quantity: 0.01, orderType: 'MARKET', confidence: conf }),
    context: mkContext({ symbol: 'BTCUSDT', price: 50000, priceTsMs: Date.now(), markQuality: 'verified' }), nowMs: Date.now() });
  const hi = mk(0.99), lo = mk(0.01);
  assert.equal(hi.approved, true); assert.equal(lo.approved, true);
  assert.equal(hi.approved, lo.approved);
  assert.equal(hi.evaluated.notional_usd, lo.evaluated.notional_usd);
});

await test('4.7 risk-gate: HOLD carries no order and is approved without a market context', () => {
  const r = RG.riskCheck({ decision: mkDecision({ action: 'HOLD', side: null, orderType: null, quantity: null, limitPrice: null }), context: null, nowMs: Date.now() });
  assert.equal(r.approved, true);
  assert.equal(r.code, RG.RISK_CODES.HOLD_NO_ORDER);
});

// =============================================================================
// 5) IDEMPOTENCY + CONCURRENCY SAFETY; DISCONNECT RECOVERY; EVENT REPLAY
//    (HTTP/closed-loop layer, exercised against a minimal in-memory D1 shim)
// =============================================================================
await test('5.1 event ledger: idempotent append (INSERT OR IGNORE by event_id) — re-send yields NO duplicate', async () => {
  const { env, token } = await openSession();
  const send = async (events) => (await PL.handlePaperLedger(mkRequest('POST', '/api/paper/ledger', token, { events }), env)).json();

  const ev = (id) => ({ eventId: id, eventType: 'paper.account.snapshot', payload: { v: 1 } });
  const r1 = await send([ev('evt-AAAAAAAA'), ev('evt-BBBBBBBB')]);
  assert.equal(r1.ok, true);
  assert.equal(r1.accepted.length, 2);

  // disconnect + re-send the SAME events (client retries after crash)
  const r2 = await send([ev('evt-AAAAAAAA'), ev('evt-BBBBBBBB')]);
  assert.equal(r2.ok, true);

  // the ledger must still hold exactly 2 events
  const count = await eventCount(env, token);
  assert.equal(count, 2, 'idempotent append must not duplicate events');
});

await test('5.2 event ledger: GET cursor replays events in seq order (disconnect recovery)', async () => {
  const { env, token } = await openSession();
  const send = async (events) => (await PL.handlePaperLedger(mkRequest('POST', '/api/paper/ledger', token, { events }), env)).json();
  const ev = (id) => ({ eventId: id, eventType: 'paper.account.snapshot', payload: {} });
  await send([ev('evt-11111111'), ev('evt-22222222'), ev('evt-33333333')]);
  const get = await (await PL.handlePaperLedger(mkRequest('GET', '/api/paper/ledger?after=0&limit=50', token), env)).json();
  assert.equal(get.ok, true);
  assert.equal(get.events.length, 3);
  // strictly increasing seq => replay order preserved
  const seqs = get.events.map((e) => e.seq);
  assert.deepEqual(seqs, [...seqs].sort((a, b) => a - b));
});

await test('5.3 event ledger: forbidden event type and bad eventId rejected (400)', async () => {
  const { env, token } = await openSession();
  const badType = await PL.handlePaperLedger(
    mkRequest('POST', '/api/paper/ledger', token, { events: [{ eventId: 'evt-44444444', eventType: 'paper.hack', payload: {} }] }), env);
  assert.equal(badType.status, 400);
  const badId = await PL.handlePaperLedger(
    mkRequest('POST', '/api/paper/ledger', token, { events: [{ eventId: '!!', eventType: 'paper.account.snapshot', payload: {} }] }), env);
  assert.equal(badId.status, 400);
});

await test('5.4 event replay (replay.mjs): recomputes equity_peak/drawdown and flags tampered stored values', () => {
  // one account, two verified snapshots; peak should be max(equity)
  const rows = [
    { snapshot_id: 's1', created_at: '2026-01-01T00:00:00Z', mark_quality: 'verified', equity: 1_000_000_000_000, equity_peak: 1_000_000_000_000, drawdown: 0 },
    { snapshot_id: 's2', created_at: '2026-01-01T00:01:00Z', mark_quality: 'verified', equity: 900_000_000_000, equity_peak: 1_000_000_000_000, drawdown: 10000000 },
  ];
  const out = RP.replay(rows, { initialCashFp: 1_000_000_000_000 });
  assert.equal(out.allMatch, true, 'recomputed peak/dd must match stored for honest rows');
  assert.equal(out.rows[1].recomputed_peak, 1_000_000_000_000);
  assert.equal(out.rows[1].recomputed_dd, 10000000);

  // tamper: stored peak wrong -> allMatch false (replay catches corruption)
  const tampered = clone(rows);
  tampered[1].equity_peak = 999_000_000_000;
  const out2 = RP.replay(tampered, { initialCashFp: 1_000_000_000_000 });
  assert.equal(out2.allMatch, false, 'tampered stored peak must be detected');

  // UNKNOWN snapshot must NOT update runningPeak
  const withUnknown = [
    { snapshot_id: 's1', created_at: '2026-01-01T00:00:00Z', mark_quality: 'verified', equity: 1_000_000_000_000, equity_peak: 1_000_000_000_000, drawdown: 0 },
    { snapshot_id: 's2', created_at: '2026-01-01T00:01:00Z', mark_quality: 'unknown', equity: 1_500_000_000_000, equity_peak: 0, drawdown: 0 },
  ];
  const out3 = RP.replay(withUnknown, { initialCashFp: 1_000_000_000_000 });
  assert.equal(out3.rows[1].recomputed_peak, 0, 'unknown snapshot must not advance running peak');
  assert.equal(out3.rows[1].match, true, 'unknown row is consistent with recomputed 0/0');
});

// =============================================================================
// helpers (decision/context builders, fake DB, in-memory D1 shim)
// =============================================================================
function mkDecision(over = {}) {
  return {
    schemaVersion: 'jev.ai.decision/v1',
    strategyId: 'strat-1',
    symbol: 'BTCUSDT',
    action: 'BUY', side: 'BUY', orderType: 'MARKET',
    quantity: 0.01, limitPrice: null,
    confidence: 0.5, reason: 'phase-d-test', riskFlags: [],
    marketContextId: 'mctx-abc12345', validForSeconds: 60,
    ...over,
  };
}
function mkContext({ symbol = 'BTCUSDT', price = 50000, priceTsMs = Date.now(), markQuality = 'verified', expiresAt = null } = {}) {
  return {
    market_context_id: 'mctx-abc12345', symbol,
    mark_price: Math.round(price * 1e8), price_ts: new Date(priceTsMs).toISOString(),
    mark_quality: markQuality, expires_at: expiresAt,
  };
}

// Minimal in-memory D1-shaped fake for equity-snapshot.buildEquityRiskBasis.
// It only needs SELECT ... FROM paper_accounts / paper_equity_snapshots.
// Both the bound (.bind().all()) and unbound (.all()) paths return the same rows.
function fakeDb(accountRows, snapshotsBySession) {
  const query = (sql, params = []) => {
    const s = String(sql);
    if (s.includes('paper_accounts')) return { results: accountRows };
    if (s.includes('paper_equity_snapshots'))
      return { results: (snapshotsBySession[params[0]] || []).map((r) => ({ ...r })) };
    return { results: [] };
  };
  return {
    prepare(sql) {
      const s = String(sql);
      return {
        bind(...params) {
          return {
            all: async () => query(s, params),
            first: async () => (query(s, params).results[0] || null),
            run: async () => ({ meta: { changes: 0 } }),
          };
        },
        all: async () => query(s),
        first: async () => (query(s).results[0] || null),
        run: async () => ({ meta: { changes: 0 } }),
      };
    },
    batch() { return Promise.resolve([]); },
  };
}

// ---- in-memory D1 shim for paper-ledger.mjs (session + event ledger) ---------
function makeMemLedgerDb() {
  const sessions = new Map();   // session_id -> {expires_at, revoked_at}
  const events = [];            // {seq, session_id, event_id, event_type, payload_json, created_at}
  let seq = 0;
  const strip = (e) => ({ seq: e.seq, event_id: e.event_id, event_type: e.event_type, payload_json: e.payload_json, created_at: e.created_at });

  function query(sql, params) {
    const s = String(sql);
    if (s.startsWith('INSERT INTO paper_sessions')) {
      const [sid, created, expires] = params;
      if (!sessions.has(sid)) sessions.set(sid, { created_at: created, expires_at: expires, revoked_at: null });
      return { changes: 1 };
    }
    if (s.startsWith('SELECT session_id, expires_at, revoked_at FROM paper_sessions')) {
      const row = sessions.get(params[0]);
      return row ? [{ session_id: params[0], ...row }] : [];
    }
    if (s.startsWith('SELECT COUNT(*) AS count FROM paper_events')) {
      return [{ count: events.filter((e) => e.session_id === params[0]).length }];
    }
    if (s.startsWith('INSERT OR IGNORE INTO paper_events')) {
      const [sid, eid, etype, pjson, cat] = params;
      const exists = events.some((e) => e.session_id === sid && e.event_id === eid);
      if (!exists) { seq += 1; events.push({ seq, session_id: sid, event_id: eid, event_type: etype, payload_json: pjson, created_at: cat }); return { changes: 1 }; }
      return { changes: 0 };
    }
    if (s.startsWith('SELECT seq, event_id, event_type, created_at FROM paper_events WHERE session_id = ? AND event_id = ?')) {
      return events.filter((e) => e.session_id === params[0] && e.event_id === params[1]).map(strip);
    }
    if (s.includes('paper_events') && s.includes('seq > ?')) {
      // GET cursor replay: params = [session_id, after, limit]
      const sid = params[0], after = params[1], limit = params[2];
      return events.filter((e) => e.session_id === sid && e.seq > after)
        .slice().sort((a, b) => a.seq - b.seq)
        .slice(0, limit == null ? 50 : limit).map(strip);
    }
    if (s.includes('paper_events')) {
      // any other paper_events select (e.g. latest DESC)
      const sid = params[0];
      const limit = (typeof params[params.length - 1] === 'number') ? params[params.length - 1] : 50;
      return events.filter((e) => e.session_id === sid)
        .slice().sort((a, b) => b.seq - a.seq).slice(0, limit).map(strip);
    }
    return [];
  }

  return {
    prepare(sql) {
      const s = String(sql);
      const run = (params) => query(s, params);
      return {
        bind(...params) {
          return {
            all: async () => ({ results: run(params) }),
            first: async () => { const r = run(params); return Array.isArray(r) ? (r[0] || null) : r; },
            run: async () => { const r = run(params); return Array.isArray(r) ? { meta: { changes: r.length ? 1 : 0 } } : { meta: { changes: r.changes || 0 } }; },
          };
        },
        all: async () => ({ results: run([]) }),
        first: async () => { const r = run([]); return Array.isArray(r) ? (r[0] || null) : r; },
        run: async () => { const r = run([]); return Array.isArray(r) ? { meta: { changes: r.length ? 1 : 0 } } : { meta: { changes: r.changes || 0 } }; },
      };
    },
    batch(stmts) { return Promise.resolve(stmts.map(() => ({ meta: { changes: 1 } }))); },
    _events: events,
  };
}

function mkRequest(method, url, token, body) {
  const headers = { Origin: 'http://localhost' };
  if (token) headers['Cookie'] = 'nr_paper_session=' + encodeURIComponent(token);
  const init = { method, headers };
  if (body) init.body = JSON.stringify(body);
  return new Request('http://localhost' + url, init);
}

async function openSession() {
  const env = { PAPER_DB: makeMemLedgerDb(), PAPER_LEDGER_SECRET: 'x'.repeat(40) };
  const res = await PL.handlePaperSession(mkRequest('POST', '/api/paper/session'), env);
  const cookie = res.headers.get('set-cookie') || '';
  const m = cookie.match(/nr_paper_session=([^;]+)/);
  const token = m ? decodeURIComponent(m[1]) : null;
  assert.ok(token, 'session token must be issued');
  return { env, token };
}
async function eventCount(env, token) {
  const j = await (await PL.handlePaperLedger(mkRequest('GET', '/api/paper/ledger?after=0&limit=50', token), env)).json();
  return j.events.length;
}

// =============================================================================
console.log('=== JEV PHASE D — Trading Closed-Loop Verification (ALL MOCK/FIXTURE DATA) ===');
for (const l of lines) console.log(l);
console.log('RESULT PASS=' + PASS + ' FAIL=' + FAIL);
if (FAIL > 0) process.exitCode = 1;
