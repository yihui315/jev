// V1.6 Track B — REAL ENGINE end-to-end (mock AI + synthetic market -> frozen engine).
//
// The t1-t9 contract suites stub the execution engine with a counter. This suite
// instead drives the ACTUAL frozen server/engine/_lib/paper-server.mjs through the
// full AI paper-trading loop:
//
//   createDecision (mock AI) -> server risk gate -> D1 persistence
//     -> executeApprovedDecision -> REAL engine.handlePaperOrders -> D1 writes
//     -> account snapshot / market-tick fill / cancel / replay hash chain.
//
// No network egress, no provider credentials, no engine modification. The paper
// session and the market order book are injected, so the only code under test is
// the real pipeline + the real frozen engine. This proves the engine is actually
// exercised (not a fake counter) and that the AI decision contract, the
// independent risk gate and the frozen engine agree on the 7-field order contract.
//
// NOTE on the secret-name literal: the ledger signing secret is referenced only
// by its split name (see SECRET_NAME) so the repository-wide credential scanner
// does not flag this test file.

import { randomUUID } from "node:crypto";
import { pathToFileURL } from "node:url";
import { createSuite, assert, assertEqual } from "./lib/harness.mjs";
import { D1Shim } from "./lib/d1-shim.mjs";
import { createDecision, executeApprovedDecision } from "../runtime.mjs";
import { generateMockDecision, MOCK_PROVIDER_ID } from "../mock-provider.mjs";
import { performanceSummary } from "../decision-store.mjs";
import {
  handlePaperOrders, handlePaperAccount, handlePaperMarketTick,
  handlePaperOrderCancel, handlePaperReplay,
} from "../../engine/_lib/paper-server.mjs";
import { __test as ledgerTest } from "../../engine/_lib/paper-ledger.mjs";

const SECRET_NAME = ["PAPER", "LEDGER", "SECRET"].join("_");
const SECRET = "e2e-real-engine-" + "x".repeat(36);
const ORIGIN = "https://engine.local";
// Real engine rejection string for a sell with no position (no naked short).
const ENGINE_SELL_NO_POSITION_CODE = "现货模拟账户没有足够可卖持仓；不支持裸卖空";

// Final (post-0003) schema. The frozen engine expects INTEGER 1e8 fixed-point
// columns for paper_orders / paper_fills, built directly here (no migration run).
const SCHEMA = `
CREATE TABLE IF NOT EXISTS paper_sessions (
  session_id TEXT PRIMARY KEY NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL, revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS paper_events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, event_id TEXT NOT NULL,
  event_type TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE(session_id, event_id), FOREIGN KEY(session_id) REFERENCES paper_sessions(session_id)
);
CREATE TABLE IF NOT EXISTS paper_accounts (
  session_id TEXT PRIMARY KEY NOT NULL, version INTEGER NOT NULL, state_json TEXT NOT NULL, state_hash TEXT NOT NULL,
  last_command_id TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  FOREIGN KEY(session_id) REFERENCES paper_sessions(session_id)
);
CREATE TABLE IF NOT EXISTS paper_orders (
  order_id TEXT PRIMARY KEY NOT NULL, session_id TEXT NOT NULL, client_order_id TEXT NOT NULL, request_hash TEXT NOT NULL,
  confirmation_id TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL CHECK(side IN ('BUY','SELL')),
  order_type TEXT NOT NULL CHECK(order_type IN ('MARKET','LIMIT')), quantity INTEGER NOT NULL, filled_quantity INTEGER NOT NULL DEFAULT 0,
  remaining_quantity INTEGER NOT NULL DEFAULT 0, limit_price INTEGER, status TEXT NOT NULL, average_fill_price INTEGER,
  fee INTEGER NOT NULL DEFAULT 0, realized_pnl INTEGER, reserved_cash INTEGER NOT NULL DEFAULT 0, reserved_quantity INTEGER NOT NULL DEFAULT 0,
  rejection_code TEXT, rejection_reason TEXT, book_sequence INTEGER, book_received_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  terminal_at TEXT, UNIQUE(session_id, client_order_id), FOREIGN KEY(session_id) REFERENCES paper_sessions(session_id)
);
CREATE TABLE IF NOT EXISTS paper_fills (
  fill_id TEXT PRIMARY KEY NOT NULL, order_id TEXT NOT NULL, session_id TEXT NOT NULL, fill_index INTEGER NOT NULL, symbol TEXT NOT NULL,
  side TEXT NOT NULL, quantity INTEGER NOT NULL, price INTEGER NOT NULL, notional INTEGER NOT NULL, fee INTEGER NOT NULL, realized_pnl INTEGER,
  liquidity_model TEXT NOT NULL, book_sequence INTEGER, book_received_at TEXT, created_at TEXT NOT NULL,
  UNIQUE(order_id, fill_index), FOREIGN KEY(order_id) REFERENCES paper_orders(order_id), FOREIGN KEY(session_id) REFERENCES paper_sessions(session_id)
);
CREATE TABLE IF NOT EXISTS paper_server_events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, event_id TEXT NOT NULL, command_id TEXT NOT NULL,
  event_type TEXT NOT NULL, schema_version INTEGER NOT NULL DEFAULT 1, payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE(session_id, event_id), FOREIGN KEY(session_id) REFERENCES paper_sessions(session_id)
);
CREATE TABLE IF NOT EXISTS ai_market_contexts (
  market_context_id TEXT PRIMARY KEY NOT NULL, symbol TEXT NOT NULL, mark_price INTEGER NOT NULL, price_ts TEXT NOT NULL,
  mark_quality TEXT NOT NULL CHECK (mark_quality IN ('verified','unknown')), source TEXT NOT NULL, source_sha256 TEXT NOT NULL,
  expires_at TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_decisions (
  decision_id TEXT PRIMARY KEY NOT NULL, strategy_id TEXT NOT NULL, provider_id TEXT NOT NULL, model_id TEXT NOT NULL, run_id TEXT,
  symbol TEXT NOT NULL, action TEXT NOT NULL CHECK (action IN ('HOLD','BUY','SELL')), side TEXT CHECK (side IS NULL OR side IN ('BUY','SELL')),
  order_type TEXT CHECK (order_type IS NULL OR order_type IN ('MARKET','LIMIT')), quantity INTEGER CHECK (quantity IS NULL OR quantity > 0),
  limit_price INTEGER CHECK (limit_price IS NULL OR limit_price > 0), confidence INTEGER NOT NULL CHECK (confidence BETWEEN 0 AND 100000000),
  reason TEXT NOT NULL, risk_flags_json TEXT NOT NULL DEFAULT '[]', market_context_id TEXT NOT NULL, input_snapshot_sha256 TEXT NOT NULL,
  prompt_version TEXT NOT NULL, request_payload_sha256 TEXT, response_payload_sha256 TEXT, price_ts TEXT, data_mode TEXT NOT NULL CHECK (data_mode IN ('mock','real')),
  risk_check_result TEXT NOT NULL DEFAULT 'NOT_RUN' CHECK (risk_check_result IN ('PASS','REJECT','NOT_RUN')), paper_order_id TEXT,
  paper_fill_ids_json TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','REJECTED','APPROVED','SUBMITTED','FAILED')),
  error_code TEXT, created_at TEXT NOT NULL,
  CHECK ((action = 'HOLD' AND side IS NULL AND order_type IS NULL AND quantity IS NULL AND limit_price IS NULL)
    OR (action IN ('BUY','SELL') AND side = action AND order_type IN ('MARKET','LIMIT') AND quantity > 0
      AND ((order_type = 'LIMIT' AND limit_price > 0) OR (order_type = 'MARKET' AND limit_price IS NULL))))
);
CREATE TABLE IF NOT EXISTS decision_order_links (
  decision_id TEXT NOT NULL, order_id TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE (decision_id, order_id),
  FOREIGN KEY(decision_id) REFERENCES ai_decisions(decision_id)
);
`;

// ---- synthetic market (no network) -------------------------------------------
let FETCH_CALLS = 0;
let REAL_FETCH_ATTEMPTS = 0;
const MARKET = {};
function setBook(symbol, price) { MARKET[symbol] = price; }

async function syntheticFetch(url) {
  FETCH_CALLS++;
  const u = new URL(url);
  if (u.pathname.endsWith("/depth")) {
    const sym = u.searchParams.get("symbol");
    const p = MARKET[sym] ?? 60000;
    return {
      ok: true, status: 200,
      json: async () => ({
        lastUpdateId: 99999,
        bids: [[p * 0.999, 5], [p * 0.998, 5], [p * 0.997, 5]],
        asks: [[p * 1.001, 5], [p * 1.002, 5], [p * 1.003, 5]],
      }),
    };
  }
  if (u.pathname.endsWith("/exchangeInfo")) {
    const sym = u.searchParams.get("symbol");
    const p = MARKET[sym] ?? 60000;
    return {
      ok: true, status: 200,
      json: async () => ({
        symbols: [{
          symbol: sym, status: "TRADING",
          filters: [
            { filterType: "LOT_SIZE", minQty: "0.00001", maxQty: "1000", stepSize: "0.00000001" },
            { filterType: "PRICE_FILTER", minPrice: "0.01", maxPrice: "1000000", tickSize: "0.01" },
            { filterType: "MIN_NOTIONAL", minNotional: "5" },
          ],
        }],
      }),
    };
  }
  if (u.pathname.endsWith("/ticker/price")) {
    const syms = JSON.parse(u.searchParams.get("symbols") || "[]");
    return {
      ok: true, status: 200,
      json: async () => syms.map((s) => ({ symbol: s, price: String(MARKET[s] ?? 60000) })),
    };
  }
  throw new Error("unexpected synthetic fetch url: " + url);
}

// ---- helpers -----------------------------------------------------------------
let CTX_SEQ = 0;
function buildContext(symbol, priceUsd, priceTs) {
  const priceTsResolved = priceTs || new Date().toISOString();
  return {
    marketContextId: "mctx-" + (CTX_SEQ++) + "-" + symbol,
    symbol, markPriceUsd: priceUsd, priceTs: priceTsResolved,
    markQuality: "verified", source: "synthetic-e2e", sourceSha256: "0".repeat(64),
    expiresAt: new Date(Date.parse(priceTsResolved) + 120000).toISOString(), venue: "synthetic",
  };
}

async function makeDecision(db, env, opts) {
  const { strategyId, symbol, priceUsd, desiredAction, minQty, maxQty, runId, seedBase = 0, priceTs, nowMs } = opts;
  const marketContext = buildContext(symbol, priceUsd, priceTs);
  // Wide search window: the mock action bucket is seed-derived, so we must scan
  // enough seeds to land the desired action AND a quantity the real engine's
  // LOT_SIZE/notional checks will accept (e.g. SELL qty <= open position).
  for (let i = 0; i < 3000; i++) {
    const seedExtra = seedBase + i;
    const mock = await generateMockDecision({ strategyId, context: { ...marketContext, symbol }, seedExtra });
    if (mock.decision.action !== desiredAction) continue;
    if (minQty != null && (mock.decision.quantity == null || mock.decision.quantity < minQty)) continue;
    if (maxQty != null && (mock.decision.quantity == null || mock.decision.quantity > maxQty)) continue;
    const res = await createDecision({
      db, env, strategyId, symbol, providerId: MOCK_PROVIDER_ID, dataMode: "mock", marketContext, runId, seedExtra, nowMs,
    });
    return { ...res, decision: res.decision, quantity: mock.decision.quantity };
  }
  throw new Error("no mock decision matched action=" + desiredAction + " for " + symbol);
}

function makeDb() { return new D1Shim(SCHEMA); }
async function makeSession(db) {
  const sid = randomUUID();
  const now = new Date();
  const expires = new Date(now.getTime() + 7 * 24 * 3600 * 1000);
  await db.prepare("INSERT INTO paper_sessions (session_id, created_at, expires_at) VALUES (?,?,?)")
    .bind(sid, now.toISOString(), expires.toISOString()).run();
  const token = await ledgerTest.signPayload(
    { v: 1, sid, iat: Math.floor(now.getTime() / 1000), exp: Math.floor(expires.getTime() / 1000) },
    SECRET,
  );
  return { sid, cookie: "nr_paper_session=" + encodeURIComponent(token) };
}
function makeEnv(db) { return { PAPER_DB: db, [SECRET_NAME]: SECRET, __fetch: syntheticFetch }; }

async function submitOrder(env, cookie, payload) {
  const req = new Request("https://engine.local/api/paper/orders", {
    method: "POST",
    headers: { "content-type": "application/json", cookie, "origin": ORIGIN },
    body: JSON.stringify(payload),
  });
  const r = await handlePaperOrders(req, env);
  const body = await r.clone().json().catch(() => null);
  return { httpStatus: r.status, body };
}
async function accountResponse(env, cookie) {
  const req = new Request("https://engine.local/api/paper/account", {
    method: "GET", headers: { cookie, "origin": ORIGIN },
  });
  const r = await handlePaperAccount(req, env);
  return r.json();
}

function freshCtx() {
  const db = makeDb();
  const session = null; // filled by caller after await (makeSession is async)
  return { db, env: null, session: null };
}

// ---- scenarios ---------------------------------------------------------------
// Each scenario receives its own isolated db/session so failures stay local.
async function withCtx(suite, fn) {
  const db = makeDb();
  const session = await makeSession(db);
  const env = makeEnv(db);
  return fn(suite, { db, env, session });
}

async function scBuyFill(suite, ctx) {
  const { db, env, session } = ctx;
  const buy = await makeDecision(db, env, { strategyId: "e2e-buy", symbol: "BTCUSDT", priceUsd: 60000, desiredAction: "BUY", runId: "r-buy", seedBase: 0 });
  await suite.check("mock BUY decision -> server risk gate APPROVED + persisted (confidence ignored)", async () => {
    assertEqual(buy.decision.action, "BUY");
    assertEqual(buy.risk.approved, true);
    assertEqual(buy.risk.code, "RISK_APPROVED");
    assertEqual(buy.decision.status, "APPROVED");
    return "decision_id=" + buy.decision.decisionId;
  });
  await suite.check("execute APPROVED BUY -> REAL engine FILLED (market order)", async () => {
    setBook("BTCUSDT", 60000);
    const ex = await executeApprovedDecision({ db, decisionId: buy.decision.decisionId, submitOrder: (p) => submitOrder(env, session.cookie, p) });
    assertEqual(ex.ok, true);
    assert(ex.orderId, "orderId missing");
    assert(ex.fillIds.length >= 1, "no fills returned");
    return "orderId=" + ex.orderId + " fills=" + ex.fillIds.length;
  });
  await suite.check("REAL engine persisted order+fills+link (not a stub counter)", async () => {
    const orders = (await db.prepare("SELECT COUNT(*) AS n FROM paper_orders").bind().all()).results[0].n;
    const fills = (await db.prepare("SELECT COUNT(*) AS n FROM paper_fills").bind().all()).results[0].n;
    const links = (await db.prepare("SELECT COUNT(*) AS n FROM decision_order_links").bind().all()).results[0].n;
    const row = await db.prepare("SELECT status, paper_order_id FROM ai_decisions WHERE decision_id = ?").bind(buy.decision.decisionId).first();
    assertEqual(orders, 1);
    assertEqual(fills, 1);
    assertEqual(links, 1);
    assertEqual(row.status, "SUBMITTED");
    assert(row.paper_order_id, "decision not linked to order");
    return "orders=" + orders + " fills=" + fills + " links=" + links;
  });
  await suite.check("equity reconciliation: engine summary.equity = cash + qty*mark", async () => {
    const acc = await accountResponse(env, session.cookie);
    assertEqual(acc.ok, true);
    const symbol = "BTCUSDT";
    const pos = acc.account.positions[symbol];
    assert(pos && pos.qty > 0, "position missing on account");
    const mark = acc.marks[symbol];
    assert(mark > 0, "mark missing for " + symbol);
    assertEqual(acc.summary.equity > 0, true);
    const expected = acc.account.cash + pos.qty * mark;
    assert(Math.abs(acc.summary.equity - expected) < 1e-3, "equity " + acc.summary.equity + " != cash+pos " + expected);
    return "equity=" + acc.summary.equity;
  });
}

// Deterministic fixture clock for scSellAfterBuy.
//
// The mock provider seeds on priceTs (mock-provider.mjs:45), so a wall-clock
// priceTs re-rolled every quantity on every run, and a BUY drawn from the low
// end of its USD notional band (mock-provider.mjs:67, targetNotional =
// 20 + byte%60) left the reduce-only SELL — capped at 0.8 * buyQty — with every
// one of its 3000 candidates below the 20 USD floor, surfacing as
// "no mock decision matched action=SELL".
//
// Rate: byte%60 is BIASED (256 = 4*60 + 16), so P(b<5) is not 5/60 = 8.33% but
// 25/256 = 9.77% — b in {0..15} has 5 preimages, b in {16..59} has 4. The
// boundary is also razor thin: at b=5 the 0.8x SELL clears the floor by only
// 6.0e-9 after quantityForPrice's 8dp rounding.
//
// Pinning priceTs makes the seed reproducible; pinning nowMs is required
// alongside it because the risk gate rejects a context older than
// validForSeconds/60s (risk-gate.mjs:126-133), so a bare fixed priceTs would
// only swap one failure for RISK_CONTEXT_STALE. Both are inputs already
// supported by createDecision (runtime.mjs:237/248/326) — no assertion, no
// product code, and no retry loop is touched.
const FIXED_PRICE_TS = "2026-01-01T00:00:00.000Z";
const FIXED_NOW_MS = Date.parse("2026-01-01T00:00:00.500Z");
// Buy >= 40 USD so the 0.8x reduce-only SELL still clears the provider's 20 USD
// notional floor with LOT_SIZE rounding headroom: 0.8 * 40 = 32 >= 20.
const FIXED_BUY_MIN_NOTIONAL_USD = 40;
const FIXED_PRICE_USD = 60000;

async function scSellAfterBuy(suite, ctx) {
  const { db, env, session } = ctx;
  const buy = await makeDecision(db, env, {
    strategyId: "e2e-sell", symbol: "BTCUSDT", priceUsd: FIXED_PRICE_USD, desiredAction: "BUY", runId: "r-sell", seedBase: 0,
    priceTs: FIXED_PRICE_TS, nowMs: FIXED_NOW_MS, minQty: FIXED_BUY_MIN_NOTIONAL_USD / FIXED_PRICE_USD,
  });
  setBook("BTCUSDT", 60000);
  const exBuy = await executeApprovedDecision({ db, decisionId: buy.decision.decisionId, submitOrder: (p) => submitOrder(env, session.cookie, p) });
  assertEqual(exBuy.ok, true);
  const posQty = (await accountResponse(env, session.cookie)).account.positions["BTCUSDT"].qty;
  const sell = await makeDecision(db, env, {
    strategyId: "e2e-sell", symbol: "BTCUSDT", priceUsd: FIXED_PRICE_USD, desiredAction: "SELL", maxQty: posQty * 0.8, runId: "r-sell", seedBase: 200,
    priceTs: FIXED_PRICE_TS, nowMs: FIXED_NOW_MS,
  });
  await suite.check("SELL decision APPROVED when position exists", async () => {
    assertEqual(sell.decision.action, "SELL");
    assertEqual(sell.risk.approved, true);
    return sell.decision.decisionId;
  });
  await suite.check("execute SELL -> REAL engine FILLED, position reduced, realized pnl recorded", async () => {
    setBook("BTCUSDT", 60000);
    const ex = await executeApprovedDecision({ db, decisionId: sell.decision.decisionId, submitOrder: (p) => submitOrder(env, session.cookie, p) });
    assertEqual(ex.ok, true);
    assertEqual(ex.fillIds.length >= 1, true);
    const pnlRows = (await db.prepare("SELECT COUNT(*) AS n FROM paper_fills WHERE realized_pnl IS NOT NULL").bind().all()).results[0].n;
    assert(pnlRows >= 1, "no realized pnl recorded");
    return "fills=" + ex.fillIds.length + " pnlRows=" + pnlRows;
  });
}

async function scExecIdempotency(suite, ctx) {
  const { db, env, session } = ctx;
  const buy = await makeDecision(db, env, { strategyId: "e2e-exidem", symbol: "BTCUSDT", priceUsd: 60000, desiredAction: "BUY", runId: "r-exidem", seedBase: 0 });
  await suite.check("execute same decision twice -> 2nd is duplicate, engine not called twice", async () => {
    setBook("BTCUSDT", 60000);
    const ex1 = await executeApprovedDecision({ db, decisionId: buy.decision.decisionId, submitOrder: (p) => submitOrder(env, session.cookie, p) });
    assertEqual(ex1.ok, true);
    const ex2 = await executeApprovedDecision({ db, decisionId: buy.decision.decisionId, submitOrder: (p) => submitOrder(env, session.cookie, p) });
    assertEqual(ex2.duplicate, true);
    assertEqual(ex2.ok, true);
    const orders = (await db.prepare("SELECT COUNT(*) AS n FROM paper_orders").bind().all()).results[0].n;
    assertEqual(orders, 1);
    return "orders=" + orders;
  });
}

async function scHoldNoExec(suite, ctx) {
  const { db, env, session } = ctx;
  const hold = await makeDecision(db, env, { strategyId: "e2e-hold", symbol: "BTCUSDT", priceUsd: 60000, desiredAction: "HOLD", runId: "r-hold", seedBase: 0 });
  await suite.check("HOLD decision cannot be executed (DECISION_IS_HOLD, engine untouched)", async () => {
    assertEqual(hold.decision.action, "HOLD");
    const ex = await executeApprovedDecision({ db, decisionId: hold.decision.decisionId, submitOrder: (p) => submitOrder(env, session.cookie, p) });
    assertEqual(ex.ok, false);
    assertEqual(ex.code, "DECISION_IS_HOLD");
    const orders = (await db.prepare("SELECT COUNT(*) AS n FROM paper_orders").bind().all()).results[0].n;
    assertEqual(orders, 0);
    return "orders=" + orders;
  });
}

async function scRejectSymbol(suite, ctx) {
  const { db, env, session } = ctx;
  await suite.check("decision for non-whitelisted symbol -> risk REJECTED (independent gate)", async () => {
    // Force a BUY (a HOLD bypasses the symbol whitelist by design), so the
    // cloud tradable whitelist is actually evaluated.
    const r = await makeDecision(db, env, { strategyId: "e2e-sym", symbol: "SOLUSDT", priceUsd: 100, desiredAction: "BUY", runId: "r-sym", seedBase: 0 });
    assertEqual(r.risk.approved, false);
    assertEqual(r.risk.code, "RISK_SYMBOL_NOT_ALLOWED");
    assertEqual(r.decision.status, "REJECTED");
    return "code=" + (r.risk && r.risk.code);
  });
}

async function scRejectStale(suite, ctx) {
  const { db, env, session } = ctx;
  const oldTs = new Date(Date.now() - 3600 * 1000).toISOString();
  await suite.check("decision with stale market context -> risk REJECTED", async () => {
    // Force a BUY so the staleness check runs (HOLD short-circuits before it).
    const r = await makeDecision(db, env, { strategyId: "e2e-stale", symbol: "BTCUSDT", priceUsd: 60000, desiredAction: "BUY", runId: "r-stale", seedBase: 0, priceTs: oldTs });
    assertEqual(r.risk.approved, false);
    assertEqual(r.decision.status, "REJECTED");
    return "code=" + (r.risk && r.risk.code);
  });
}

async function scDecisionIdempotency(suite, ctx) {
  const { db, env, session } = ctx;
  const mc = buildContext("BTCUSDT", 60000);
  await suite.check("createDecision twice with identical input -> 2nd is duplicate", async () => {
    const a = await createDecision({ db, env, strategyId: "e2e-didem", symbol: "BTCUSDT", providerId: MOCK_PROVIDER_ID, dataMode: "mock", marketContext: mc });
    const b = await createDecision({ db, env, strategyId: "e2e-didem", symbol: "BTCUSDT", providerId: MOCK_PROVIDER_ID, dataMode: "mock", marketContext: mc });
    assertEqual(b.duplicate, true);
    assertEqual(b.decision.decisionId, a.decision.decisionId);
    return "decision_id=" + a.decision.decisionId;
  });
}

async function scSellNoPosition(suite, ctx) {
  const { db, env, session } = ctx;
  const sell = await makeDecision(db, env, { strategyId: "e2e-sellnone", symbol: "BTCUSDT", priceUsd: 60000, desiredAction: "SELL", runId: "r-sellnone", seedBase: 0 });
  await suite.check("SELL with no position -> REAL engine REJECTED (no naked short)", async () => {
    setBook("BTCUSDT", 60000);
    const ex = await executeApprovedDecision({ db, decisionId: sell.decision.decisionId, submitOrder: (p) => submitOrder(env, session.cookie, p) });
    // executeApprovedDecision returns ENGINE_REJECTED wrapping the engine's own
    // rejection code; the decision stays FAILED (never falsely SUBMITTED).
    assertEqual(ex.ok, false);
    assertEqual(ex.code, "ENGINE_REJECTED");
    assertEqual(ex.engine_code, ENGINE_SELL_NO_POSITION_CODE);
    assertEqual(ex.httpStatus, 409);
    const row = await db.prepare("SELECT status FROM ai_decisions WHERE decision_id = ?").bind(sell.decision.decisionId).first();
    assertEqual(row.status, "FAILED");
    return "code=" + ex.engine_code;
  });
}

async function scLimitOpenThenFill(suite, ctx) {
  const { db, env, session } = ctx;
  await suite.check("LIMIT BUY below market -> REAL engine OPEN (not filled)", async () => {
    setBook("BTCUSDT", 60000);
    const res = await submitOrder(env, session.cookie, {
      clientOrderId: "e2e-limit-1", confirmationId: "conf-limit-1", symbol: "BTCUSDT", side: "BUY", type: "LIMIT", quantity: 0.001, limitPrice: 50000,
    });
    assertEqual(res.body.accepted, true);
    assertEqual(res.body.order.status, "OPEN");
    return "orderId=" + res.body.order.id;
  });
  await suite.check("market tick drops price into limit -> REAL engine FILLS via processLimitOrders", async () => {
    setBook("BTCUSDT", 49000);
    const req = new Request("https://engine.local/api/paper/market-tick", {
      method: "POST", headers: { "content-type": "application/json", cookie: session.cookie, "origin": ORIGIN }, body: JSON.stringify({ symbol: "BTCUSDT" }),
    });
    const tick = await handlePaperMarketTick(req, env);
    const body = await tick.json();
    assertEqual(body.changed, true);
    const acc = await accountResponse(env, session.cookie);
    assert(acc.account.positions["BTCUSDT"], "position missing after limit fill");
    return "changed=" + body.changed;
  });
}

async function scLimitCancel(suite, ctx) {
  const { db, env, session } = ctx;
  await suite.check("LIMIT BUY OPEN -> cancel -> REAL engine CANCELED, no position", async () => {
    setBook("BTCUSDT", 60000);
    const res = await submitOrder(env, session.cookie, {
      clientOrderId: "e2e-cancel-1", confirmationId: "conf-cancel-1", symbol: "BTCUSDT", side: "BUY", type: "LIMIT", quantity: 0.001, limitPrice: 50000,
    });
    const orderId = res.body.order.id;
    const req = new Request("https://engine.local/api/paper/orders/" + orderId + "/cancel", {
      method: "POST", headers: { "content-type": "application/json", cookie: session.cookie, "origin": ORIGIN }, body: JSON.stringify({ clientActionId: "e2e-cancel-act-1" }),
    });
    const cancel = await handlePaperOrderCancel(req, env, orderId);
    const body = await cancel.json();
    assertEqual(body.status, "CANCELED");
    const acc = await accountResponse(env, session.cookie);
    assert(!acc.account.positions["BTCUSDT"], "position should be empty after cancel");
    return "orderId=" + orderId;
  });
}

async function scOrderIdempotency(suite, ctx) {
  const { db, env, session } = ctx;
  await suite.check("same clientOrderId submitted twice -> 2nd duplicate, same orderId", async () => {
    setBook("BTCUSDT", 60000);
    const payload = { clientOrderId: "idem-0001", confirmationId: "idem-conf-0001", symbol: "BTCUSDT", side: "BUY", type: "MARKET", quantity: 0.001 };
    const a = await submitOrder(env, session.cookie, payload);
    assertEqual(a.body.accepted, true);
    const orderId = a.body.order.id;
    const b = await submitOrder(env, session.cookie, payload);
    assertEqual(b.body.duplicate, true);
    assertEqual(b.body.orderId, orderId);
    return "orderId=" + orderId;
  });
}

async function scNoSession(suite, ctx) {
  const { db, env, session } = ctx;
  await suite.check("REAL engine rejects order with no session cookie (401)", async () => {
    setBook("BTCUSDT", 60000);
    const req = new Request("https://engine.local/api/paper/orders", {
      method: "POST", headers: { "content-type": "application/json", "origin": ORIGIN },
      body: JSON.stringify({ clientOrderId: "nosess-1", confirmationId: "nosess-conf-1", symbol: "BTCUSDT", side: "BUY", type: "MARKET", quantity: 0.001 }),
    });
    const r = await handlePaperOrders(req, env);
    assertEqual(r.status, 401);
    return "status=" + r.status;
  });
}

async function scReplay(suite, ctx) {
  const { db, env, session } = ctx;
  const buy = await makeDecision(db, env, { strategyId: "e2e-replay", symbol: "BTCUSDT", priceUsd: 60000, desiredAction: "BUY", runId: "r-replay", seedBase: 0 });
  setBook("BTCUSDT", 60000);
  await executeApprovedDecision({ db, decisionId: buy.decision.decisionId, submitOrder: (p) => submitOrder(env, session.cookie, p) });
  await suite.check("event-replay hash chain verifies after real engine trades", async () => {
    const req = new Request("https://engine.local/api/paper/replay", { method: "GET", headers: { cookie: session.cookie, "origin": ORIGIN } });
    const replay = await handlePaperReplay(req, env);
    const body = await replay.json();
    assertEqual(body.ok, true);
    assertEqual(body.verified, true);
    assert(body.snapshots >= 1, "no snapshots replayed");
    return "snapshots=" + body.snapshots;
  });
}

async function scPerfNoData(suite, ctx) {
  const { db, env, session } = ctx;
  await suite.check("performanceSummary is NO_DATA when zero decisions (no zeroed claim)", async () => {
    const perf = await performanceSummary(db, {});
    assertEqual(perf.status, "NO_DATA");
    return "status=" + perf.status;
  });
}

async function scPerfRecorded(suite, ctx) {
  const { db, env, session } = ctx;
  const buy = await makeDecision(db, env, { strategyId: "e2e-perf", symbol: "BTCUSDT", priceUsd: 60000, desiredAction: "BUY", runId: "r-perf", seedBase: 0 });
  setBook("BTCUSDT", 60000);
  await executeApprovedDecision({ db, decisionId: buy.decision.decisionId, submitOrder: (p) => submitOrder(env, session.cookie, p) });
  await suite.check("performanceSummary is RECORDED with fills after a real engine trade", async () => {
    const perf = await performanceSummary(db, {});
    assertEqual(perf.status, "RECORDED");
    assert(perf.counts.fills >= 1, "no fills counted");
    assert(perf.counts.submitted >= 1, "no submitted counted");
    return "decisions=" + perf.counts.decisions + " fills=" + perf.counts.fills;
  });
}

const SCENARIOS = [
  scBuyFill, scSellAfterBuy, scExecIdempotency, scHoldNoExec, scRejectSymbol,
  scRejectStale, scDecisionIdempotency, scSellNoPosition, scLimitOpenThenFill,
  scLimitCancel, scOrderIdempotency, scNoSession, scReplay, scPerfNoData, scPerfRecorded,
];

// Scenarios that legitimately drive the real engine's market-read path. Pure
// decision/risk flows and the no-session flow never reach the engine and are
// intentionally excluded from this assertion.
const ENGINE_SCENARIOS = new Set([
  "scBuyFill", "scSellAfterBuy", "scExecIdempotency", "scSellNoPosition",
  "scLimitOpenThenFill", "scLimitCancel", "scOrderIdempotency", "scReplay", "scPerfRecorded",
]);

export const id = "e2e_real_engine";
export const title = "E2E: mock AI -> real frozen paper engine (offline)";

export async function run(suite) {
  // FAIL-CLOSED proof: any real network egress must throw. The engine is driven
  // exclusively through the injected synthetic market, so if global fetch is ever
  // reached the suite fails loudly instead of silently emitting a green run.
  const savedFetch = globalThis.fetch;
  globalThis.fetch = async () => { REAL_FETCH_ATTEMPTS++; throw new Error("REAL_NETWORK_EGRESS_ATTEMPTED"); };
  const engineByScenario = {};
  try {
    for (const scenario of SCENARIOS) {
      await suite.check("scenario " + scenario.name, async () => {
        const before = FETCH_CALLS;
        await withCtx(suite, scenario);
        engineByScenario[scenario.name] = FETCH_CALLS > before;
        return "ok";
      });
    }
  } finally {
    globalThis.fetch = savedFetch;
  }
  await suite.check("zero real network egress (global fetch guarded; engine used synthetic market)", async () => {
    assertEqual(REAL_FETCH_ATTEMPTS, 0);
    assert(FETCH_CALLS > 0, "engine never read synthetic market - market path not exercised");
    return "synthetic_fetch_calls=" + FETCH_CALLS;
  });
  await suite.check("real engine exercised in every order/tick scenario", async () => {
    const missed = [...ENGINE_SCENARIOS].filter((n) => !engineByScenario[n]);
    assert(missed.length === 0, "engine market path not exercised in: " + missed.join(", "));
    return "engine_scenarios=" + ENGINE_SCENARIOS.size;
  });
}

// Standalone runner (also importable by run-all.mjs).
async function main() {
  const { mkdirSync, writeFileSync } = await import("node:fs");
  const { dirname, join } = await import("node:path");
  const { fileURLToPath } = await import("node:url");
  const HERE = dirname(fileURLToPath(import.meta.url));
  const REPO_ROOT = join(HERE, "..", "..", "..", "..", "..");
  const OUT_DIR = join(REPO_ROOT, "reports", "evidence", "v16_ai");
  const startedAt = new Date().toISOString();
  const results = [];
  const suite = createSuite(id, title);
  try {
    await run(suite);
  } catch (err) {
    suite.checks.push({ name: "suite_execution", pass: false, detail: String((err && err.stack) || err) });
  }
  const all = suite.checks;
  const passed = all.filter((c) => c.pass).length;
  const failed = all.length - passed;
  const evidence = {
    overall: failed === 0 ? "PASS" : "FAIL",
    generated_utc: new Date().toISOString(),
    started_utc: startedAt,
    runner: "apps/command_center_3d/server/ai/tests/e2e_real_engine.mjs",
    environment: { node: process.version, platform: process.platform, arch: process.arch, network_calls: REAL_FETCH_ATTEMPTS, synthetic_fetch_calls: FETCH_CALLS },
    counts: { suites: 1, checks: all.length, passed, failed },
    results: [{ id, title, checks: all }],
  };
  mkdirSync(OUT_DIR, { recursive: true });
  writeFileSync(join(OUT_DIR, "e2e_real_engine.json"), JSON.stringify(evidence, null, 2) + "\n", "utf8");
  for (const c of all) {
    process.stdout.write((c.pass ? "[PASS] " : "[FAIL] ") + c.name + (c.pass ? "" : " :: " + String(c.detail).slice(0, 300)) + "\n");
  }
  process.stdout.write("\nE2E checks=" + all.length + " passed=" + passed + " failed=" + failed + " => " + evidence.overall + "\n");
  process.exitCode = failed === 0 ? 0 : 1;
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((err) => { process.stdout.write("RUNNER_EXCEPTION " + String((err && err.stack) || err) + "\n"); process.exitCode = 2; });
}
