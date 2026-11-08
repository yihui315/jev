import { json, isSameOrigin, requireConfig, readSession } from "./paper-ledger.mjs";
import {
  D, U, add, sub, mul, div, cmp, gt, gte, lt, lte, isPos, isZero, neg, abs, max, toNumber
} from "./decimal.mjs";

const API_BASE = "https://data-api.binance.vision/api/v3";
const ALLOWED_SYMBOLS = new Set(["BTCUSDT", "ETHUSDT", "ORDIUSDT", "1000SATSUSDT"]);
const CFG = Object.freeze({
  initialCash: "10000",            // 定点字符串，D() 解析为 1e8 缩放
  feeRate: "0.001",
  minNotional: "5",
  maxOrderPct: 0.10,
  maxExposurePct: 0.40,
  dailyLossPct: 0.03,
  maxDrawdownPct: 0.10,
  maxBookAgeMs: 5000,
  maxBodyBytes: 16000
});
const OPEN_STATUSES = new Set(["OPEN", "PARTIALLY_FILLED"]);

function nowIso() { return new Date().toISOString(); }
function utcDay() { return new Date().toISOString().slice(0, 10); }
function finite(value) { return Number.isFinite(Number(value)); }

function createAccount() {
  const now = nowIso();
  const cash = D(CFG.initialCash);
  return {
    version: 1,
    generation: 1,
    initialCash: U(cash),
    cash: U(cash),
    reservedCash: U(0n),
    positions: {},
    orders: [],
    trades: [],
    lastBookSequence: {},
    realizedPnl: U(0n),
    equityPeak: U(cash),
    dayKey: utcDay(),
    dayStartEquity: U(cash),
    createdAt: now,
    updatedAt: now
  };
}

function stableStringify(value) {
  if (Array.isArray(value)) return "[" + value.map(stableStringify).join(",") + "]";
  if (value && typeof value === "object") {
    return "{" + Object.keys(value).sort().map(key => JSON.stringify(key) + ":" + stableStringify(value[key])).join(",") + "}";
  }
  return JSON.stringify(value);
}

async function sha256(value) {
  const bytes = new TextEncoder().encode(typeof value === "string" ? value : stableStringify(value));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
}

async function parseBody(request) {
  const raw = await request.text();
  if (new TextEncoder().encode(raw).byteLength > CFG.maxBodyBytes) return { error: json({ ok: false, code: "BODY_TOO_LARGE", error: "请求内容过大" }, 413) };
  try {
    const body = JSON.parse(raw || "{}");
    if (!body || typeof body !== "object" || Array.isArray(body)) throw new Error("invalid");
    return { body };
  } catch (_) {
    return { error: json({ ok: false, code: "INVALID_JSON", error: "JSON 请求体无效" }, 400) };
  }
}

function writeOriginOk(request) {
  return request.headers.get("Origin") === new URL(request.url).origin;
}

async function authorize(request, env, write = false) {
  const config = requireConfig(env);
  if (config.error) return { response: config.error };
  if (write ? !writeOriginOk(request) : !isSameOrigin(request)) {
    return { response: json({ ok: false, code: "CROSS_ORIGIN", error: "跨站请求已拒绝" }, 403) };
  }
  const auth = await readSession(request, env);
  if (auth.error) return { response: auth.error };
  return { sessionId: auth.session.sid };
}

function dbFetch(env) {
  return typeof env.__fetch === "function" ? env.__fetch : fetch;
}

async function readAccountRow(db, sessionId) {
  return db.prepare(
    "SELECT session_id, version, state_json, state_hash, last_command_id, created_at, updated_at FROM paper_accounts WHERE session_id = ?"
  ).bind(sessionId).first();
}

async function ensureAccount(db, sessionId) {
  let row = await readAccountRow(db, sessionId);
  if (row) return row;

  const account = createAccount();
  const stateJson = JSON.stringify(account);
  const stateHash = await sha256(account);
  const createdAt = account.createdAt;
  const payload = {
    schemaVersion: 1,
    version: account.version,
    previousStateHash: null,
    stateHash,
    account,
    reason: "initial-account"
  };
  await db.batch([
    db.prepare(
      "INSERT OR IGNORE INTO paper_accounts (session_id, version, state_json, state_hash, last_command_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
    ).bind(sessionId, account.version, stateJson, stateHash, "account-opened", createdAt, createdAt),
    db.prepare(
      "INSERT OR IGNORE INTO paper_server_events (session_id, event_id, command_id, event_type, schema_version, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
    ).bind(sessionId, "account-opened:paper.server.account.snapshot", "account-opened", "paper.server.account.snapshot", 1, JSON.stringify(payload), createdAt)
  ]);
  row = await readAccountRow(db, sessionId);
  if (!row) throw new Error("PAPER_ACCOUNT_BOOTSTRAP_FAILED");
  return row;
}

function accountFromRow(row) {
  let account;
  try { account = JSON.parse(row.state_json); } catch (_) { throw new Error("PAPER_ACCOUNT_STATE_CORRUPT"); }
  if (!account || account.version !== Number(row.version) || !account.positions || !Array.isArray(account.orders) || !Array.isArray(account.trades)) {
    throw new Error("PAPER_ACCOUNT_STATE_CORRUPT");
  }
  return { account, version: Number(row.version), stateHash: row.state_hash };
}

function findOrderByClientId(db, sessionId, clientOrderId) {
  return db.prepare(
    "SELECT order_id, client_order_id, request_hash, status, rejection_code, rejection_reason FROM paper_orders WHERE session_id = ? AND client_order_id = ?"
  ).bind(sessionId, clientOrderId).first();
}

// 金额字段（字符串->Number）对外暴露，保持与原客户端契约一致且精确（< 2^53 无损）
function num(b) { return toNumber(b); }

function accountEquity(account, marks) {
  let equity = D(account.cash || 0);
  for (const [symbol, position] of Object.entries(account.positions || {})) {
    const mark = D(marks[symbol]);
    if (!(mark > 0n)) throw new Error("缺少持仓标的的最新服务端标记价格：" + symbol);
    equity = add(equity, mul(D(position.qty || 0), mark));
  }
  return equity;
}

function summarize(account, marks) {
  const equity = accountEquity(account, marks);
  let marketValue = 0n;
  let unrealizedPnl = 0n;
  const positions = [];
  for (const [symbol, p] of Object.entries(account.positions || {})) {
    const mark = D(marks[symbol]);
    const value = mul(D(p.qty), mark);
    const pnl = sub(value, mul(D(p.qty), D(p.avgPrice)));
    marketValue = add(marketValue, value);
    unrealizedPnl = add(unrealizedPnl, pnl);
    positions.push({
      symbol, qty: num(p.qty), avgPrice: num(p.avgPrice), mark: num(mark),
      marketValue: num(value), unrealizedPnl: num(pnl), reservedQty: num(p.reservedQty || 0)
    });
  }
  const drawdown = equity > 0n ? (equity < D(account.equityPeak) ? div(sub(D(account.equityPeak), equity), D(account.equityPeak)) : 0n) : 0n;
  const dayStartEquity = D(account.dayStartEquity || account.initialCash);
  const dailyPnl = sub(equity, dayStartEquity);
  return {
    equity: num(equity),
    cash: num(account.cash),
    availableCash: num(sub(D(account.cash), D(account.reservedCash || 0))),
    reservedCash: num(account.reservedCash),
    marketValue: num(marketValue),
    unrealizedPnl: num(unrealizedPnl),
    realizedPnl: num(account.realizedPnl || 0),
    totalPnl: num(sub(equity, D(account.initialCash))),
    dailyPnl: num(dailyPnl),
    drawdown: num(drawdown),
    exposurePct: equity > 0n ? num(div(marketValue, equity)) : 0,
    positions,
    dailyLossLocked: lte(dailyPnl, neg(mul(dayStartEquity, D(CFG.dailyLossPct)))),
    drawdownLocked: gte(drawdown, D(CFG.maxDrawdownPct))
  };
}

function updateRiskMarks(account, marks) {
  const equity = accountEquity(account, marks);
  const day = utcDay();
  if (account.dayKey !== day) {
    account.dayKey = day;
    account.dayStartEquity = U(equity);
  }
  account.equityPeak = U(max(D(account.equityPeak || account.initialCash), equity));
  return summarize(account, marks);
}

async function fetchJson(fetcher, url) {
  // V4.2 PHASE 2 修复（真实 E2E run3 发现）：Cloudflare Workers / miniflare 的
  // RequestInit 不实现浏览器专属的 `cache` 字段，传入会直接抛
  // "The 'cache' field on 'RequestInitializerDict' is not implemented."，
  // 导致 fetchMarketContext 失败、订单被 REJECTED（409）。
  // 与 agent-orchestrator / ai-core / paper-ledger 保持一致：用标准
  // cache-control 请求头表达 no-store 意图；Workers fetch 本身不做隐式缓存。
  const response = await fetcher(url, {
    headers: { accept: "application/json", "cache-control": "no-store, max-age=0" }
  });
  if (!response || !response.ok) throw new Error("MARKET_HTTP_" + (response && response.status || "ERROR"));
  return response.json();
}

function normalizeLevels(raw, descending) {
  if (!Array.isArray(raw) || !raw.length) throw new Error("MARKET_BOOK_EMPTY");
  const levels = raw.slice(0, 1000).map(level => {
    const price = Number(level && level[0]);
    const qty = Number(level && level[1]);
    if (!(price > 0) || !(qty > 0)) throw new Error("MARKET_BOOK_INVALID_LEVEL");
    return [price, qty];
  });
  for (let i = 1; i < levels.length; i++) {
    if (descending ? levels[i][0] > levels[i - 1][0] : levels[i][0] < levels[i - 1][0]) throw new Error("MARKET_BOOK_UNSORTED");
  }
  return levels;
}

function parseFilters(info, symbol) {
  const row = Array.isArray(info && info.symbols) ? info.symbols.find(item => item.symbol === symbol) : null;
  if (!row || row.status !== "TRADING" || !Array.isArray(row.filters)) throw new Error("EXCHANGE_FILTERS_UNAVAILABLE");
  const lot = row.filters.find(item => item.filterType === "LOT_SIZE");
  const price = row.filters.find(item => item.filterType === "PRICE_FILTER");
  const notional = row.filters.find(item => item.filterType === "NOTIONAL" || item.filterType === "MIN_NOTIONAL");
  if (!lot || !price || !(D(lot.stepSize) > 0n) || !(D(price.tickSize) > 0n)) throw new Error("EXCHANGE_FILTERS_INCOMPLETE");
  return {
    minQty: Number(lot.minQty),
    maxQty: Number(lot.maxQty),
    stepSize: Number(lot.stepSize),
    minPrice: Number(price.minPrice),
    maxPrice: Number(price.maxPrice),
    tickSize: Number(price.tickSize),
    minNotional: Math.max(Number(CFG.minNotional), Number(notional && (notional.minNotional || notional.notional) || 0))
  };
}

async function fetchMarketContext(symbol, env, needFilters = true) {
  if (!ALLOWED_SYMBOLS.has(symbol)) throw new Error("SYMBOL_NOT_ALLOWED");
  const fetcher = dbFetch(env);
  const startedAt = Date.now();
  const depthUrl = API_BASE + "/depth?symbol=" + encodeURIComponent(symbol) + "&limit=1000";
  const tasks = [fetchJson(fetcher, depthUrl)];
  if (needFilters) {
    tasks.push(fetchJson(fetcher, API_BASE + "/exchangeInfo?symbol=" + encodeURIComponent(symbol)));
  } else {
    tasks.push(Promise.resolve(null));
  }
  const symbols = encodeURIComponent(JSON.stringify(Array.from(ALLOWED_SYMBOLS)));
  tasks.push(fetchJson(fetcher, API_BASE + "/ticker/price?symbols=" + symbols));
  const [depth, exchangeInfo, tickerData] = await Promise.all(tasks);
  const receivedAt = nowIso();
  if (Date.now() - startedAt > CFG.maxBookAgeMs) throw new Error("MARKET_BOOK_STALE");
  const sequence = Number(depth && depth.lastUpdateId);
  if (!Number.isSafeInteger(sequence) || sequence < 0) throw new Error("MARKET_BOOK_SEQUENCE_INVALID");
  const bids = normalizeLevels(depth.bids, true);
  const asks = normalizeLevels(depth.asks, false);
  if (!(bids[0][0] < asks[0][0])) throw new Error("MARKET_BOOK_CROSSED");
  const marks = {};
  const tickers = Array.isArray(tickerData) ? tickerData : [tickerData];
  for (const item of tickers) {
    if (item && ALLOWED_SYMBOLS.has(item.symbol) && Number(item.price) > 0) marks[item.symbol] = Number(item.price);
  }
  if (!marks[symbol]) marks[symbol] = (bids[0][0] + asks[0][0]) / 2;
  const filters = needFilters ? parseFilters(exchangeInfo, symbol) : null;
  return {
    book: { venue: "binance-spot", symbol, sequence, receivedAt, bids, asks, source: "Binance public REST depth snapshot" },
    filters,
    marks
  };
}

function stepValid(value, step) {
  const s = Number(step);
  if (!(s > 0)) return false;
  const r = Number(value);
  if (!(r >= 0)) return false;
  const ratio = r / s;
  return Math.abs(ratio - Math.round(ratio)) < 1e-9;
}

function walkBook(levels, quantity) {
  let remaining = Number(quantity);
  let filledQty = 0;
  let notional = 0n;
  const fills = [];
  for (const [price, available] of levels || []) {
    if (!(remaining > 0)) break;
    const take = remaining < available ? remaining : available;
    if (!(take > 0)) continue;
    const fillNotional = mul(take, price);
    fills.push({ qty: take, price, notional: fillNotional });
    filledQty = filledQty + take;
    notional = add(notional, fillNotional);
    remaining = remaining - take;
  }
  return {
    fills,
    filledQty,
    remainingQty: remaining < 0 ? 0 : remaining,
    notional,
    avgPrice: filledQty > 0 ? num(div(notional, D(filledQty))) : null
  };
}

function eligibleLevels(side, type, limitPrice, book) {
  const levels = side === "BUY" ? book.asks : book.bids;
  if (type !== "LIMIT") return levels;
  const lp = Number(limitPrice);
  return levels.filter(([price]) => side === "BUY" ? price <= lp : price >= lp);
}

function reservedBuyExposure(account, exceptOrderId) {
  return (account.orders || []).filter(order =>
    OPEN_STATUSES.has(order.status) && order.side === "BUY" && order.id !== exceptOrderId
  ).reduce((sum, order) => add(sum, mul(D(order.remainingQty || 0), D(order.limitPrice || 0))), 0n);
}

function releaseReservation(account, order, quantity) {
  const remainingBefore = D(order.remainingQty || 0);
  const releaseQty = remainingBefore < D(quantity) ? remainingBefore : D(quantity);
  if (order.side === "BUY" && D(order.reservedCash) > 0n && remainingBefore > 0n) {
    const release = mul(D(order.reservedCash), div(releaseQty, remainingBefore));
    account.reservedCash = U(sub(D(account.reservedCash), release));
    order.reservedCash = U(sub(D(order.reservedCash), release));
  }
  if (order.side === "SELL" && D(order.reservedQty) > 0n) {
    const release = releaseQty < D(order.reservedQty) ? releaseQty : D(order.reservedQty);
    const position = account.positions[order.symbol];
    if (position) position.reservedQty = U(sub(D(position.reservedQty || 0), release));
    order.reservedQty = U(sub(D(order.reservedQty), release));
  }
}

function releaseAllReservation(account, order) {
  releaseReservation(account, order, D(order.remainingQty || 0));
}

function applyFill(account, order, rawFill, fillIndex, book) {
  const qty = D(rawFill.qty);
  const price = D(rawFill.price);
  const notional = mul(qty, price);
  const fee = mul(notional, D(CFG.feeRate));
  let realizedPnl = null;

  if (order.side === "BUY") {
    const cost = add(notional, fee);
    const available = sub(D(account.cash), D(account.reservedCash || 0));
    if (gt(cost, available)) throw new Error("INSUFFICIENT_CASH");
    let position = account.positions[order.symbol];
    if (!position) position = account.positions[order.symbol] = { qty: "0", avgPrice: "0", reservedQty: "0" };
    position.avgPrice = U(div(add(mul(D(position.qty), D(position.avgPrice)), cost), add(D(position.qty), qty)));
    position.qty = U(add(D(position.qty), qty));
    account.cash = U(sub(D(account.cash), cost));
  } else {
    const position = account.positions[order.symbol];
    const available = sub(D(position ? position.qty : 0n), D(position ? position.reservedQty || 0 : 0));
    if (!position || gt(qty, available)) throw new Error("INSUFFICIENT_POSITION");
    realizedPnl = sub(sub(notional, fee), mul(D(position.avgPrice), qty));
    position.qty = U(sub(D(position.qty), qty));
    account.cash = U(add(D(account.cash), sub(notional, fee)));
    account.realizedPnl = U(add(D(account.realizedPnl || 0), realizedPnl));
    if (D(position.qty) <= 0n && D(position.reservedQty || 0) <= 0n) delete account.positions[order.symbol];
  }

  const priorFilled = D(order.filledQty || 0);
  order.filledQty = U(add(priorFilled, qty));
  order.remainingQty = U(sub(D(order.remainingQty), qty) < 0n ? 0n : sub(D(order.remainingQty), qty));
  order.fee = U(add(D(order.fee || 0), fee));
  order.avgFillPrice = D(order.filledQty) > 0n
    ? U(div(add(mul(D(order.avgFillPrice || 0), priorFilled), notional), D(order.filledQty)))
    : null;
  if (realizedPnl != null) order.realizedPnl = U(add(D(order.realizedPnl || 0), realizedPnl));

  order.fillCount = Number(order.fillCount || 0) + 1;
  const stableFillIndex = order.fillCount;
  const fill = {
    id: "F-" + order.id + "-" + String(stableFillIndex).padStart(3, "0"),
    fillIndex: stableFillIndex,
    orderId: order.id,
    timestamp: nowIso(),
    symbol: order.symbol,
    side: order.side,
    qty: U(qty),
    price: U(price),
    notional: U(notional),
    fee: U(fee),
    realizedPnl: realizedPnl == null ? null : U(realizedPnl),
    liquidityModel: "server-visible-orderbook-walk",
    bookSequence: book.sequence,
    bookReceivedAt: book.receivedAt
  };
  account.trades.unshift(fill);
  if (account.trades.length > 300) account.trades.length = 300;
  return fill;
}

function validateInput(body) {
  const allowed = new Set(["clientOrderId", "confirmationId", "symbol", "side", "type", "quantity", "limitPrice"]);
  if (Object.keys(body).some(key => !allowed.has(key))) throw new Error("订单请求包含不允许的字段；盘口、账户和成交数据必须由服务端读取");
  const clientOrderId = String(body.clientOrderId || "");
  const confirmationId = String(body.confirmationId || "");
  if (!/^[a-zA-Z0-9._:-]{8,100}$/.test(clientOrderId)) throw new Error("clientOrderId 无效或缺失");
  if (!/^[a-zA-Z0-9._:-]{8,100}$/.test(confirmationId)) throw new Error("confirmationId 无效或缺失");
  const symbol = String(body.symbol || "");
  const side = String(body.side || "").toUpperCase();
  const type = String(body.type || "MARKET").toUpperCase();
  const quantity = Number(body.quantity);
  const limitPrice = body.limitPrice == null ? null : Number(body.limitPrice);
  if (!ALLOWED_SYMBOLS.has(symbol)) throw new Error("交易对白名单不允许该标的");
  if (side !== "BUY" && side !== "SELL") throw new Error("订单方向无效");
  if (type !== "MARKET" && type !== "LIMIT") throw new Error("订单类型无效");
  if (!isPos(quantity)) throw new Error("订单数量必须为正数");
  if (type === "LIMIT" && !isPos(limitPrice)) throw new Error("限价单必须提供有效限价");
  if (type === "MARKET" && limitPrice != null) throw new Error("市价单不得提交限价字段");
  return { clientOrderId, confirmationId, symbol, side, type, quantity, limitPrice };
}

function validateAgainstMarket(account, input, context) {
  const { book, filters, marks } = context;
  if (Date.now() - Date.parse(book.receivedAt) > CFG.maxBookAgeMs) throw new Error("MARKET_BOOK_STALE");
  if (!stepValid(input.quantity, filters.stepSize) || lt(input.quantity, filters.minQty) || gt(input.quantity, filters.maxQty)) {
    throw new Error("订单数量不符合交易所 LOT_SIZE 精度或范围");
  }
  if (input.type === "LIMIT" && (!stepValid(input.limitPrice, filters.tickSize) || lt(input.limitPrice, filters.minPrice) || gt(input.limitPrice, filters.maxPrice))) {
    throw new Error("限价不符合交易所 PRICE_FILTER tickSize 或价格范围");
  }
  const best = input.side === "BUY" ? book.asks[0] : book.bids[0];
  const reference = input.type === "LIMIT" ? input.limitPrice : best[0];
  if (!isPos(reference)) throw new Error("服务端盘口没有有效报价");
  const notional = mul(input.quantity, reference);
  if (lt(notional, filters.minNotional)) throw new Error("订单金额低于交易所最低名义金额");
  const snapshot = updateRiskMarks(account, marks);
  if (!(snapshot.equity > 0)) throw new Error("账户净值无效");
  if (gt(notional, mul(snapshot.equity, D(CFG.maxOrderPct)))) throw new Error("超过单笔风险上限（账户净值的 10%）");

  if (input.side === "BUY") {
    if (snapshot.dailyLossLocked) throw new Error("触发单日亏损熔断，暂停新增买入");
    if (snapshot.drawdownLocked) throw new Error("触发账户回撤熔断，暂停新增买入");
    if (gt(add(snapshot.marketValue, reservedBuyExposure(account)), add(notional, mul(snapshot.equity, D(CFG.maxExposurePct))))) {
      throw new Error("超过总持仓敞口上限（账户净值的 40%）");
    }
    const reservePrice = input.type === "LIMIT" ? input.limitPrice : reference;
    if (gt(mul(mul(reservePrice, input.quantity), add(1n, D(CFG.feeRate))), snapshot.availableCash)) throw new Error("可用 USDT 不足");
  } else {
    const position = account.positions[input.symbol];
    const available = position ? sub(D(position.qty), D(position.reservedQty || 0)) : 0n;
    if (gt(input.quantity, available)) throw new Error("现货模拟账户没有足够可卖持仓；不支持裸卖空");
  }
  return { reference, snapshot };
}

function makeOrder(input, book) {
  const now = nowIso();
  return {
    id: "O-" + crypto.randomUUID(),
    clientOrderId: input.clientOrderId,
    confirmationId: input.confirmationId,
    symbol: input.symbol,
    side: input.side,
    type: input.type,
    qty: U(input.quantity),
    limitPrice: input.limitPrice == null ? null : U(input.limitPrice),
    status: "NEW",
    filledQty: "0",
    remainingQty: U(input.quantity),
    avgFillPrice: null,
    fee: "0",
    realizedPnl: null,
    reservedCash: "0",
    reservedQty: "0",
    rejectionCode: null,
    reason: null,
    timeInForce: input.type === "MARKET" ? "IOC" : "GTC",
    bookSequence: book ? book.sequence : null,
    bookReceivedAt: book ? book.receivedAt : null,
    createdAt: now,
    updatedAt: now,
    terminalAt: null,
    fillCount: 0
  };
}

function simulateNewOrder(account, input, context) {
  const { book } = context;
  const order = makeOrder(input, book);
  const fills = [];
  const { reference } = validateAgainstMarket(account, input, context);
  const eligible = eligibleLevels(input.side, input.type, input.limitPrice || 0, book);
  const walk = walkBook(eligible, input.quantity);

  if (input.type === "MARKET" && !isPos(walk.filledQty)) throw new Error("可见盘口没有可成交深度");
  if (input.type === "MARKET" && (!isPos(walk.filledQty) && isPos(walk.remainingQty))) throw new Error("盘口深度不足，未产生模拟成交");

  if (input.type === "MARKET") {
    const pre = summarize(account, context.marks);
    const preEquity = D(pre.equity);
    const preMarketValue = D(pre.marketValue);
    const preAvailable = D(pre.availableCash);
    if (input.side === "BUY" && (gt(walk.notional, mul(preEquity, D(CFG.maxOrderPct))) ||
      gt(add(preMarketValue, reservedBuyExposure(account)), add(walk.notional, mul(preEquity, D(CFG.maxExposurePct)))))) {
      throw new Error("盘口冲击使实际成交金额超过风险上限，未模拟成交");
    }
    if (input.side === "BUY" && gt(mul(add(walk.notional, mul(walk.notional, D(CFG.feeRate))), 1n), preAvailable)) {
      throw new Error("盘口冲击导致可用 USDT 不足，未模拟成交");
    }
    if (input.side === "SELL") {
      const position = account.positions[input.symbol];
      const available = position ? sub(D(position.qty), D(position.reservedQty || 0)) : 0n;
      if (gt(walk.filledQty, available)) throw new Error("可卖持仓不足，未模拟成交");
    }
  }

  for (const rawFill of walk.fills) fills.push(applyFill(account, order, rawFill, order.fillCount + 1, book));

  if (input.type === "MARKET") {
    if (isPos(walk.remainingQty)) {
      order.cancelledQty = U(walk.remainingQty);
      order.remainingQty = "0";
      order.status = "PARTIALLY_FILLED";
      order.terminalAt = nowIso();
    } else {
      order.status = "FILLED";
      order.terminalAt = nowIso();
    }
  } else {
    if (isPos(order.remainingQty)) {
      if (input.side === "BUY") {
        const reserve = mul(mul(D(order.remainingQty), D(input.limitPrice)), add(1n, D(CFG.feeRate)));
        const available = sub(D(account.cash), D(account.reservedCash || 0));
        if (gt(reserve, available)) throw new Error("可用 USDT 不足，无法冻结剩余限价委托金额");
        order.reservedCash = U(reserve);
        account.reservedCash = U(add(D(account.reservedCash || 0), reserve));
      } else {
        const position = account.positions[input.symbol];
        const available = position ? sub(D(position.qty), D(position.reservedQty || 0)) : 0n;
        if (!position || gt(D(order.remainingQty), available)) throw new Error("剩余限价卖单超过可卖持仓");
        order.reservedQty = U(order.remainingQty);
        position.reservedQty = U(add(D(position.reservedQty || 0), D(order.remainingQty)));
      }
      order.status = isPos(order.filledQty) ? "PARTIALLY_FILLED" : "OPEN";
    } else {
      order.status = "FILLED";
      order.terminalAt = nowIso();
    }
  }

  order.updatedAt = nowIso();
  account.orders.unshift(order);
  if (account.orders.length > 300) account.orders.length = 300;
  return { order, fills };
}

function rejectionOrder(input, error) {
  const order = makeOrder(input, null);
  order.status = "REJECTED";
  order.rejectionCode = String(error && error.message || "ORDER_REJECTED").slice(0, 80);
  order.reason = String(error && error.message || "订单被拒绝").slice(0, 300);
  order.remainingQty = "0";
  order.terminalAt = nowIso();
  order.updatedAt = nowIso();
  return order;
}

/**
 * V4.2 PHASE 2 修复（真实 E2E 发现，证据 phase3_e2e_run2_FAILED_evidence.json）：
 * D1 的 `.bind()` 不接受 BigInt，而 0003_fixed_point.sql 将金额列定义为
 * INTEGER（值 = 实际值 * 1e8）。orderRow()/fillRow() 直接把 D() 的 BigInt
 * 定点数交给 bind，导致所有下单/撤单/重置写入返回
 * `D1_TYPE_ERROR: Type 'bigint' not supported ...` (503)。
 *
 * 这里只做类型桥接，不改变数值语义：BigInt 缩放整数 -> Number 缩放整数。
 * 账户规模下该值远小于 2^53；若超出安全范围则显式抛错，绝不静默丢精度。
 */
function d1BindValues(values) {
  return values.map((v) => {
    if (typeof v !== "bigint") return v;
    const n = Number(v);
    if (!Number.isSafeInteger(n)) throw new Error("D1_FIXED_POINT_OUT_OF_SAFE_RANGE");
    return n;
  });
}

function orderRow(order, sessionId, requestHash) {
  return d1BindValues([
    order.id, sessionId, order.clientOrderId, order.requestHash || requestHash, order.confirmationId,
    order.symbol, order.side, order.type, D(order.qty), D(order.filledQty), D(order.remainingQty),
    order.limitPrice == null ? null : D(order.limitPrice), order.status, order.avgFillPrice == null ? null : D(order.avgFillPrice),
    D(order.fee), order.realizedPnl == null ? null : D(order.realizedPnl),
    D(order.reservedCash), D(order.reservedQty), order.rejectionCode, order.reason,
    order.bookSequence, order.bookReceivedAt, order.createdAt, order.updatedAt, order.terminalAt
  ]);
}

function fillRow(fill, order, sessionId, index) {
  return d1BindValues([
    fill.id, order.id, sessionId, index, fill.symbol, fill.side, D(fill.qty), D(fill.price),
    D(fill.notional), D(fill.fee), fill.realizedPnl == null ? null : D(fill.realizedPnl), fill.liquidityModel,
    fill.bookSequence, fill.bookReceivedAt, fill.timestamp
  ]);
}

async function commitState(db, sessionId, current, account, commandId, options = {}) {
  account.version = current.version + 1;
  account.updatedAt = nowIso();
  const stateJson = JSON.stringify(account);
  const stateHash = await sha256(account);
  const previousStateHash = current.stateHash;
  const eventSpecs = (options.events || []).slice();
  eventSpecs.push({
    eventType: "paper.server.account.snapshot",
    payload: {
      schemaVersion: 1,
      version: account.version,
      previousStateHash,
      stateHash,
      account,
      commandId
    }
  });
  const guard = [sessionId, account.version, commandId];
  const statements = [
    db.prepare(
      "UPDATE paper_accounts SET state_json = ?, version = ?, state_hash = ?, last_command_id = ?, updated_at = ? WHERE session_id = ? AND version = ?"
    ).bind(stateJson, account.version, stateHash, commandId, account.updatedAt, sessionId, current.version)
  ];

  for (const order of options.orders || []) {
    const values = orderRow(order, sessionId, options.requestHash || "");
    const placeholders = values.map(() => "?").join(", ");
    statements.push(db.prepare(
      "INSERT INTO paper_orders (order_id, session_id, client_order_id, request_hash, confirmation_id, symbol, side, order_type, quantity, filled_quantity, remaining_quantity, limit_price, status, average_fill_price, fee, realized_pnl, reserved_cash, reserved_quantity, rejection_code, rejection_reason, book_sequence, book_received_at, created_at, updated_at, terminal_at) SELECT " +
      placeholders + " WHERE EXISTS (SELECT 1 FROM paper_accounts WHERE session_id = ? AND version = ? AND last_command_id = ?) ON CONFLICT(order_id) DO UPDATE SET filled_quantity=excluded.filled_quantity, remaining_quantity=excluded.remaining_quantity, status=excluded.status, average_fill_price=excluded.average_fill_price, fee=excluded.fee, realized_pnl=excluded.realized_pnl, reserved_cash=excluded.reserved_cash, reserved_quantity=excluded.reserved_quantity, rejection_code=excluded.rejection_code, rejection_reason=excluded.rejection_reason, book_sequence=excluded.book_sequence, book_received_at=excluded.book_received_at, updated_at=excluded.updated_at, terminal_at=excluded.terminal_at"
    ).bind(...values, ...guard));
  }

  for (const item of options.fills || []) {
    const values = fillRow(item.fill, item.order, sessionId, item.fill.fillIndex || item.index);
    const placeholders = values.map(() => "?").join(", ");
    statements.push(db.prepare(
      "INSERT OR IGNORE INTO paper_fills (fill_id, order_id, session_id, fill_index, symbol, side, quantity, price, notional, fee, realized_pnl, liquidity_model, book_sequence, book_received_at, created_at) SELECT " +
      placeholders + " WHERE EXISTS (SELECT 1 FROM paper_accounts WHERE session_id = ? AND version = ? AND last_command_id = ?)"
    ).bind(...values, ...guard));
  }

  eventSpecs.forEach((spec, index) => {
    const eventId = commandId + ":" + spec.eventType + ":" + String(index);
    const payload = JSON.stringify(spec.payload || {});
    statements.push(db.prepare(
      "INSERT OR IGNORE INTO paper_server_events (session_id, event_id, command_id, event_type, schema_version, payload_json, created_at) SELECT ?, ?, ?, ?, 1, ?, ? WHERE EXISTS (SELECT 1 FROM paper_accounts WHERE session_id = ? AND version = ? AND last_command_id = ?)"
    ).bind(sessionId, eventId, commandId, spec.eventType, payload, account.updatedAt, ...guard));
  });

  const results = await db.batch(statements);
  const changes = Number(results && results[0] && results[0].meta && results[0].meta.changes || 0);
  if (changes !== 1) return { committed: false, stateHash };
  return { committed: true, stateHash, account };
}

function bookEvent(book) {
  if (!book) return null;
  return {
    venue: book.venue,
    symbol: book.symbol,
    sequence: book.sequence,
    receivedAt: book.receivedAt,
    source: book.source,
    bids: book.bids,
    asks: book.asks
  };
}

// 对外账户对象：金额字段由字符串转换为精确 Number（< 2^53 无损），保持客户端契约
function toPublicAccount(account) {
  const clone = JSON.parse(JSON.stringify(account));
  clone.cash = num(clone.cash);
  clone.reservedCash = num(clone.reservedCash);
  clone.realizedPnl = num(clone.realizedPnl);
  clone.initialCash = num(clone.initialCash);
  clone.dayStartEquity = num(clone.dayStartEquity);
  clone.equityPeak = num(clone.equityPeak);
  for (const [symbol, p] of Object.entries(clone.positions || {})) {
    p.qty = num(p.qty); p.avgPrice = num(p.avgPrice); p.reservedQty = num(p.reservedQty || 0);
  }
  for (const order of (clone.orders || [])) {
    order.qty = num(order.qty); order.limitPrice = order.limitPrice == null ? null : num(order.limitPrice);
    order.filledQty = num(order.filledQty); order.remainingQty = num(order.remainingQty);
    order.avgFillPrice = order.avgFillPrice == null ? null : num(order.avgFillPrice);
    order.fee = num(order.fee); order.realizedPnl = order.realizedPnl == null ? null : num(order.realizedPnl);
    order.reservedCash = num(order.reservedCash); order.reservedQty = num(order.reservedQty);
  }
  for (const t of (clone.trades || [])) {
    t.qty = num(t.qty); t.price = num(t.price); t.notional = num(t.notional); t.fee = num(t.fee);
    t.realizedPnl = t.realizedPnl == null ? null : num(t.realizedPnl);
  }
  return clone;
}

async function accountResponse(db, sessionId, env) {
  const row = await ensureAccount(db, sessionId);
  const { account, version, stateHash } = accountFromRow(row);
  let marks = {};
  let markQuality = "unknown";
  const symbols = Object.keys(account.positions || {});
  if (symbols.length) {
    try {
      const symbolsQuery = encodeURIComponent(JSON.stringify(symbols));
      const tickers = await fetchJson(dbFetch(env), API_BASE + "/ticker/price?symbols=" + symbolsQuery);
      for (const item of (Array.isArray(tickers) ? tickers : [tickers])) {
        if (item && Number(item.price) > 0) marks[item.symbol] = Number(item.price);
      }
      markQuality = symbols.every(symbol => marks[symbol] > 0) ? "verified" : "unknown";
    } catch (_) { markQuality = "unknown"; }
  } else {
    markQuality = "verified";
  }
  let summary = null;
  try { summary = summarize(account, marks); } catch (_) { /* account remains readable when market marks are unavailable */ }
  return { ok: true, mode: "server-authoritative-paper", account: toPublicAccount(account), marks, summary, markQuality, version, stateHash };
}

export async function handlePaperAccount(request, env) {
  if (request.method !== "GET") return json({ ok: false, error: "仅支持 GET" }, 405, { allow: "GET" });
  const auth = await authorize(request, env, false);
  if (auth.response) return auth.response;
  try {
    return json(await accountResponse(env.PAPER_DB, auth.sessionId, env));
  } catch (error) {
    return json({ ok: false, code: "PAPER_ACCOUNT_READ_FAILED", error: String(error.message || error) }, 503);
  }
}

export async function handlePaperOrders(request, env) {
  if (request.method !== "POST") return json({ ok: false, error: "仅支持 POST" }, 405, { allow: "POST" });
  const auth = await authorize(request, env, true);
  if (auth.response) return auth.response;
  const parsed = await parseBody(request);
  if (parsed.error) return parsed.error;

  let input;
  try { input = validateInput(parsed.body); }
  catch (error) { return json({ ok: false, code: "INVALID_ORDER", error: error.message }, 400); }

  const db = env.PAPER_DB;
  const requestHash = await sha256({
    symbol: input.symbol, side: input.side, type: input.type,
    quantity: U(input.quantity), limitPrice: input.limitPrice == null ? null : U(input.limitPrice)
  });
  try {
    const duplicate = await findOrderByClientId(db, auth.sessionId, input.clientOrderId);
    if (duplicate) {
      if (duplicate.request_hash !== requestHash) return json({ ok: false, code: "IDEMPOTENCY_CONFLICT", error: "同一 clientOrderId 不得用于不同订单参数" }, 409);
      const restored = await accountResponse(db, auth.sessionId, env);
      const existingOrder = restored.account.orders.find(order => order.clientOrderId === input.clientOrderId) || null;
      return json({
        ...restored, duplicate: true, order: existingOrder,
        orderId: duplicate.order_id, orderStatus: duplicate.status,
        accepted: duplicate.status !== "REJECTED",
        rejection: duplicate.status === "REJECTED" ? { code: duplicate.rejection_code, reason: duplicate.rejection_reason } : null
      });
    }

    const row = await ensureAccount(db, auth.sessionId);
    const current = accountFromRow(row);
    const account = JSON.parse(JSON.stringify(current.account));
    let context = null;
    let order;
    let fills = [];
    let events = [];

    try {
      context = await fetchMarketContext(input.symbol, env, true);
      const result = simulateNewOrder(account, input, context);
      order = result.order;
      order.requestHash = requestHash;
      fills = result.fills;
      events.push({
        eventType: "paper.server.order.created",
        payload: { order, request: input, book: bookEvent(context.book), filters: context.filters }
      });
      if (fills.length) {
        fills.forEach((fill, index) => events.push({
          eventType: "paper.server.order.filled",
          payload: { orderId: order.id, fill, fillIndex: index + 1, bookSequence: context.book.sequence }
        }));
      }
    } catch (error) {
      order = rejectionOrder(input, error);
      order.requestHash = requestHash;
      account.orders.unshift(order);
      if (account.orders.length > 300) account.orders.length = 300;
      events.push({
        eventType: "paper.server.order.rejected",
        payload: { order, request: input, code: order.rejectionCode, reason: order.reason, book: context ? bookEvent(context.book) : null }
      });
    }

    const commandId = "order:" + input.clientOrderId;
    const committed = await commitState(db, auth.sessionId, current, account, commandId, {
      orders: [order],
      fills: fills.map((fill, index) => ({ fill, order, index: index + 1 })),
      events,
      requestHash
    });
    if (!committed.committed) {
      const raced = await findOrderByClientId(db, auth.sessionId, input.clientOrderId);
      if (raced && raced.request_hash === requestHash) {
        const restored = await accountResponse(db, auth.sessionId, env);
        const existingOrder = restored.account.orders.find(order => order.clientOrderId === input.clientOrderId) || null;
        return json({
          ...restored, duplicate: true, order: existingOrder,
          orderId: raced.order_id, orderStatus: raced.status,
          accepted: raced.status !== "REJECTED",
          rejection: raced.status === "REJECTED" ? { code: raced.rejection_code, reason: raced.rejection_reason } : null
        });
      }
      return json({ ok: false, code: "ACCOUNT_VERSION_CONFLICT", error: "账户在订单处理期间已变化，请刷新账户后重试" }, 409);
    }

    const response = await accountResponse(db, auth.sessionId, env);
    const savedOrder = order;
    return json({
      ...response,
      order: savedOrder,
      fills,
      duplicate: false,
      accepted: order.status !== "REJECTED",
      rejection: order.status === "REJECTED" ? { code: order.rejectionCode, reason: order.reason } : null
    }, order.status === "REJECTED" ? 409 : 201);
  } catch (error) {
    return json({ ok: false, code: "PAPER_ORDER_FAILED", error: String(error.message || error) }, 503);
  }
}

function processLimitOrders(account, symbol, book, marks) {
  const updatedOrders = [];
  const fills = [];
  const events = [];
  const orders = account.orders.filter(order =>
    order.symbol === symbol && OPEN_STATUSES.has(order.status) && D(order.remainingQty) > 0n
  ).sort((a, b) => String(a.createdAt).localeCompare(String(b.createdAt)));

  for (const order of orders) {
    const snapshot = updateRiskMarks(account, marks);
    if (order.side === "BUY" && (snapshot.dailyLossLocked || snapshot.drawdownLocked)) {
      releaseAllReservation(account, order);
      order.cancelledQty = U(D(order.remainingQty));
      order.remainingQty = "0";
      order.status = "REJECTED";
      order.rejectionCode = snapshot.dailyLossLocked ? "DAILY_LOSS_LIMIT" : "MAX_DRAWDOWN";
      order.reason = snapshot.dailyLossLocked ? "触发单日亏损熔断，限价买单拒绝成交" : "触发账户回撤熔断，限价买单拒绝成交";
      order.terminalAt = nowIso();
      order.updatedAt = order.terminalAt;
      updatedOrders.push(order);
      events.push({ eventType: "paper.server.order.rejected", payload: { order, reason: order.reason, book: bookEvent(book) } });
      continue;
    }

    const eligible = eligibleLevels(order.side, "LIMIT", Number(order.limitPrice), book);
    const walk = walkBook(eligible, Number(order.remainingQty));
    if (!isPos(walk.filledQty)) continue;

    const preExposure = add(snapshot.marketValue, reservedBuyExposure(account, order.id));
    if (order.side === "BUY" && gt(add(preExposure, walk.notional), mul(snapshot.equity, D(CFG.maxExposurePct)))) {
      releaseAllReservation(account, order);
      order.cancelledQty = U(D(order.remainingQty));
      order.remainingQty = "0";
      order.status = "REJECTED";
      order.rejectionCode = "MAX_EXPOSURE";
      order.reason = "账户敞口已变化，限价单拒绝成交";
      order.terminalAt = nowIso();
      order.updatedAt = order.terminalAt;
      updatedOrders.push(order);
      events.push({ eventType: "paper.server.order.rejected", payload: { order, reason: order.reason, book: bookEvent(book) } });
      continue;
    }

    const beforeRemaining = D(order.remainingQty);
    releaseReservation(account, order, walk.filledQty);
    const orderFills = [];
    for (const rawFill of walk.fills) {
      const fill = applyFill(account, order, rawFill, order.fillCount + 1, book);
      fills.push({ fill, order, index: fill.fillIndex });
      orderFills.push(fill);
    }
    if (D(order.remainingQty) <= 0n) {
      order.remainingQty = "0";
      order.status = "FILLED";
      order.terminalAt = nowIso();
      releaseAllReservation(account, order);
    } else {
      order.status = "PARTIALLY_FILLED";
    }
    order.updatedAt = nowIso();
    updatedOrders.push(order);
    events.push({ eventType: "paper.server.order.updated", payload: { order, fills: orderFills, previousRemainingQty: U(beforeRemaining), book: bookEvent(book) } });
    orderFills.forEach(fill => events.push({ eventType: "paper.server.order.filled", payload: { orderId: order.id, fill, bookSequence: book.sequence } }));
  }
  return { updatedOrders, fills, events };
}

export async function handlePaperMarketTick(request, env) {
  if (request.method !== "POST") return json({ ok: false, error: "仅支持 POST" }, 405, { allow: "POST" });
  const auth = await authorize(request, env, true);
  if (auth.response) return auth.response;
  const parsed = await parseBody(request);
  if (parsed.error) return parsed.error;
  const symbol = String(parsed.body.symbol || "");
  if (Object.keys(parsed.body).some(key => key !== "symbol") || !ALLOWED_SYMBOLS.has(symbol)) {
    return json({ ok: false, code: "INVALID_MARKET_TICK", error: "仅接受白名单交易对；盘口必须由服务端重新读取" }, 400);
  }

  const db = env.PAPER_DB;
  try {
    const row = await ensureAccount(db, auth.sessionId);
    const current = accountFromRow(row);
    const account = JSON.parse(JSON.stringify(current.account));
    if (!account.orders.some(order => order.symbol === symbol && OPEN_STATUSES.has(order.status) && D(order.remainingQty) > 0n)) {
      return json({ ok: true, changed: false, reason: "NO_OPEN_ORDERS", account: toPublicAccount(account) });
    }
    const context = await fetchMarketContext(symbol, env, false);
    const lastSeq = Number(account.lastBookSequence && account.lastBookSequence[symbol] || 0);
    if (lastSeq >= context.book.sequence) {
      return json({ ok: true, changed: false, reason: "BOOK_SEQUENCE_ALREADY_PROCESSED" });
    }
    const result = processLimitOrders(account, symbol, context.book, context.marks);
    if (!result.updatedOrders.length) return json({ ok: true, changed: false, reason: "NO_MARKETABLE_ORDERS" });
    account.lastBookSequence = account.lastBookSequence || {};
    account.lastBookSequence[symbol] = context.book.sequence;

    const commandId = "tick:" + symbol + ":" + context.book.sequence;
    const committed = await commitState(db, auth.sessionId, current, account, commandId, {
      orders: result.updatedOrders,
      fills: result.fills,
      events: result.events
    });
    if (!committed.committed) {
      return json({ ok: false, code: "ACCOUNT_VERSION_CONFLICT", error: "账户状态已变化，市场更新未提交；下次行情轮询会重试" }, 409);
    }
    return json({ ...(await accountResponse(db, auth.sessionId, env)), changed: true, updatedOrders: result.updatedOrders });
  } catch (error) {
    return json({ ok: false, code: "PAPER_MARKET_TICK_FAILED", error: String(error.message || error) }, 503);
  }
}

export async function handlePaperOrderCancel(request, env, orderId) {
  if (request.method !== "POST") return json({ ok: false, error: "仅支持 POST" }, 405, { allow: "POST" });
  const auth = await authorize(request, env, true);
  if (auth.response) return auth.response;
  const parsed = await parseBody(request);
  if (parsed.error) return parsed.error;
  const body = parsed.body;
  if (Object.keys(body).some(key => key !== "clientActionId") || !/^[a-zA-Z0-9._:-]{8,100}$/.test(String(body.clientActionId || ""))) {
    return json({ ok: false, code: "INVALID_CANCEL", error: "clientActionId 无效或缺失" }, 400);
  }
  const db = env.PAPER_DB;
  try {
    const row = await ensureAccount(db, auth.sessionId);
    const current = accountFromRow(row);
    const account = JSON.parse(JSON.stringify(current.account));
    const order = account.orders.find(item => item.id === orderId);
    if (!order) return json({ ok: false, code: "ORDER_NOT_FOUND", status: "NOT_FOUND" }, 404);
    if (!OPEN_STATUSES.has(order.status) || !(D(order.remainingQty) > 0n)) {
      return json({ ...(await accountResponse(db, auth.sessionId, env)), status: "NOT_OPEN", order });
    }
    const before = { ...order };
    releaseAllReservation(account, order);
    order.cancelledQty = U(D(order.remainingQty));
    order.remainingQty = "0";
    order.status = "CANCELED";
    order.terminalAt = nowIso();
    order.updatedAt = order.terminalAt;
    const commandId = "cancel:" + body.clientActionId;
    const committed = await commitState(db, auth.sessionId, current, account, commandId, {
      orders: [order],
      events: [{ eventType: "paper.server.order.cancelled", payload: { order, previous: before } }]
    });
    if (!committed.committed) return json({ ok: false, code: "ACCOUNT_VERSION_CONFLICT", error: "账户状态已变化，请刷新后重试" }, 409);
    return json({ ...(await accountResponse(db, auth.sessionId, env)), status: "CANCELED", order });
  } catch (error) {
    return json({ ok: false, code: "PAPER_CANCEL_FAILED", error: String(error.message || error) }, 503);
  }
}

export async function handlePaperAccountReset(request, env) {
  if (request.method !== "POST") return json({ ok: false, error: "仅支持 POST" }, 405, { allow: "POST" });
  const auth = await authorize(request, env, true);
  if (auth.response) return auth.response;
  const parsed = await parseBody(request);
  if (parsed.error) return parsed.error;
  const body = parsed.body;
  if (Object.keys(body).some(key => key !== "clientActionId" && key !== "confirmationId") ||
      !/^[a-zA-Z0-9._:-]{8,100}$/.test(String(body.clientActionId || "")) ||
      !/^[a-zA-Z0-9._:-]{8,100}$/.test(String(body.confirmationId || ""))) {
    return json({ ok: false, code: "INVALID_RESET", error: "账户重置必须提供 clientActionId 和 confirmationId" }, 400);
  }
  const db = env.PAPER_DB;
  try {
    const commandId = "reset:" + body.clientActionId;
    const prior = await db.prepare(
      "SELECT event_id FROM paper_server_events WHERE session_id = ? AND event_id = ?"
    ).bind(auth.sessionId, commandId + ":paper.server.account.reset:0").first();
    if (prior) return json({ ...(await accountResponse(db, auth.sessionId, env)), duplicate: true });
    const row = await ensureAccount(db, auth.sessionId);
    const current = accountFromRow(row);
    const account = createAccount();
    account.version = current.version + 1;
    account.generation = Number(current.account.generation || 1) + 1;
    account.createdAt = current.account.createdAt;
    const committed = await commitState(db, auth.sessionId, current, account, commandId, {
      events: [{ eventType: "paper.server.account.reset", payload: { generation: account.generation, confirmationId: body.confirmationId, reason: "user-confirmed-reset" } }]
    });
    if (!committed.committed) return json({ ok: false, code: "ACCOUNT_VERSION_CONFLICT", error: "账户状态已变化，请刷新后重试" }, 409);
    return json(await accountResponse(db, auth.sessionId, env));
  } catch (error) {
    return json({ ok: false, code: "PAPER_RESET_FAILED", error: String(error.message || error) }, 503);
  }
}

export async function handlePaperServerEvents(request, env) {
  if (request.method !== "GET") return json({ ok: false, error: "仅支持 GET" }, 405, { allow: "GET" });
  const auth = await authorize(request, env, false);
  if (auth.response) return auth.response;
  try {
    const url = new URL(request.url);
    const after = Number(url.searchParams.get("after") || 0);
    const limit = Math.min(500, Math.max(1, Math.floor(Number(url.searchParams.get("limit") || 100))));
    if (!Number.isSafeInteger(after) || after < 0) return json({ ok: false, code: "INVALID_CURSOR", error: "after 游标无效" }, 400);
    const result = await env.PAPER_DB.prepare(
      "SELECT seq, event_id, command_id, event_type, schema_version, payload_json, created_at FROM paper_server_events WHERE session_id = ? AND seq > ? ORDER BY seq ASC LIMIT ?"
    ).bind(auth.sessionId, after, limit).all();
    const events = (result.results || []).map(row => {
      let payload = null;
      try { payload = JSON.parse(row.payload_json); } catch (_) {}
      return { seq: row.seq, eventId: row.event_id, commandId: row.command_id, eventType: row.event_type, schemaVersion: row.schema_version, payload, createdAt: row.created_at };
    });
    return json({ ok: true, events, nextAfter: events.length ? events[events.length - 1].seq : after });
  } catch (error) {
    return json({ ok: false, code: "PAPER_EVENTS_READ_FAILED", error: String(error.message || error) }, 503);
  }
}

export async function handlePaperReplay(request, env) {
  if (request.method !== "GET") return json({ ok: false, error: "仅支持 GET" }, 405, { allow: "GET" });
  const auth = await authorize(request, env, false);
  if (auth.response) return auth.response;
  try {
    const row = await ensureAccount(env.PAPER_DB, auth.sessionId);
    const materialized = accountFromRow(row);
    const result = await env.PAPER_DB.prepare(
      "SELECT seq, event_type, payload_json FROM paper_server_events WHERE session_id = ? ORDER BY seq ASC"
    ).bind(auth.sessionId).all();
    let replayed = null;
    let previousHash = null;
    let previousVersion = 0;
    let verified = true;
    let snapshots = 0;
    for (const event of result.results || []) {
      if (event.event_type !== "paper.server.account.snapshot") continue;
      let payload;
      try { payload = JSON.parse(event.payload_json); } catch (_) { verified = false; break; }
      if (!payload || !payload.account || payload.previousStateHash !== previousHash ||
          Number(payload.version) !== previousVersion + 1 ||
          await sha256(payload.account) !== payload.stateHash) {
        verified = false;
        break;
      }
      replayed = payload.account;
      previousHash = payload.stateHash;
      previousVersion = Number(payload.version);
      snapshots++;
    }
    const currentMatches = !!replayed && previousHash === materialized.stateHash &&
      stableStringify(replayed) === stableStringify(materialized.account);
    return json({
      ok: true,
      verified: verified && currentMatches,
      snapshots,
      lastSequence: (result.results || []).length ? result.results[result.results.length - 1].seq : 0,
      account: replayed ? toPublicAccount(replayed) : null,
      materializedVersion: materialized.version,
      replayedVersion: previousVersion,
      stateHash: previousHash,
      error: verified && currentMatches ? null : "EVENT_REPLAY_MISMATCH"
    });
  } catch (error) {
    return json({ ok: false, code: "PAPER_REPLAY_FAILED", error: String(error.message || error) }, 503);
  }
}

export const __test = {
  CFG, ALLOWED_SYMBOLS, createAccount, stableStringify, sha256, normalizeLevels,
  parseFilters, stepValid, walkBook, eligibleLevels, summarize, simulateNewOrder,
  processLimitOrders, validateInput, updateRiskMarks
};
