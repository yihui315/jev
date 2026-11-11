/**
 * DAY-1.4 runner — bounded Paper Trading PREP.
 *
 * P0 gap closure (A–E), branch feature/v16-p0-gap-closure.
 *
 * 硬红线（代码层强制，不依赖任何外部配置文件）：
 *   ENABLE_REAL_TRADING=false / allowBuySell=false / 不触网 / 不调付费模型 /
 *   不提交订单 / 不写生产 DB / 不改冻结引擎与正式风控源码 / 不改旧 freeze。
 *
 * A 真实接线：真实路径不再占位。链路真正调用
 *   fetchMarket(fetchImpl,...) -> buildMarkContracts -> evaluateCycle(...)
 *   -> riskCheck(formal gate) -> providerImpl -> engineImpl(冻结引擎 __test)。
 *   fetchImpl / providerImpl / engineImpl / riskCheckImpl / nowMs 全部依赖注入。
 *   默认 dry=true 且所有 impl 默认为本地 fixture（确定性假数据 + 可验证
 *   provenance），绝不发起真实网络请求、绝不调用付费模型、绝不提交订单。
 *   运行标签由实际 provenance 决定（fixture-verified），不因配置名贴 LIVE。
 *
 * B fail-closed：正式 risk-gate 导入失败直接 throw 终止；彻底删除
 *   fallbackRiskGate 的放行能力；非 HOLD 必须构造完整 context 交给 riskCheck
 *   全量校验；HOLD 通过契约(RISK_HOLD_NO_ORDER) != BUY/SELL 通过(RISK_APPROVED)，
 *   日志用 decisionClass / orderable / code 明确区分；limits 由本 runner 固定，
 *   AI/调用方不得覆盖。
 *
 * C 链续接 + 恢复：makeJournal 启动时全量校验既有链（逐条 _prev===上一条_hash，
 *   首条 GENESIS，且重算每条 _hash 防内容篡改），校验失败 throw；恢复末尾哈希；
 *   校验已完成周期与 state、检查未完成订单、核对累计预算/调用/失败/风险状态；
 *   state 原子更新并持久化 costUsd/providerFails/calls/riskBreach/
 *   reconcileMismatch/engineAccount/harness；预算判定用累计值。
 *
 * D 真实对账：用 engineImpl.createAccount / summarize（及账户对象）读取现金、
 *   持仓、费用、状态版本，独立 harness 账本核对权益；无成交如实标注 noFills；
 *   reconcile 给出比对依据（引擎值 vs harness 账本值 + 差额），不写死 MATCH。
 *
 * E 不可变 runId 配置：每次新实验生成新 runId，产物隔离到 runs/<runId>/；
 *   旧 config.freeze.json 只读校验、绝不修改；run.json 区分「配置意图」与
 *   「运行证据」，清除 realMarket:true 误导；双口径成本模型分列、不合并。
 *
 * Run: node scripts/day14-runner.mjs [cycles] [--new-run]
 */
import {
  appendFileSync, readFileSync, writeFileSync, mkdirSync, existsSync, renameSync,
} from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createHash, randomBytes } from "node:crypto";
import { spawnSync } from "node:child_process";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "../..", ".."); // repo root
const DEFAULT_OUT = join(ROOT, "reports", "day14-artifacts");
mkdirSync(DEFAULT_OUT, { recursive: true });

const sha256Hex = (s) => createHash("sha256").update(s).digest("hex");

// ---- 硬红线常量（硬编码，不从文件读，防止文件篡改绕过） ----
const RED_LINES = Object.freeze({ enableRealTrading: false, allowBuySell: false });

// ---- legacy config.freeze.json：只读校验，绝不修改（P0-E 向后兼容） ----
// 该历史文件含 realMarket:true，是误导标签；保留只读，新配置(run.json)已清除。
const LEGACY_FREEZE_EXPECTED = Object.freeze({
  schema: "day14-exp-v1",
  realMarket: true,
  provider: "MiniMax-M3",
  networkContext: "sandbox_proxy_egress_binance_us",
  maxCycles: 60,
  virtualCapitalUsdt: 10000,
  maxNotionalPerTrade: 200,
  maxTrades: 6,
  maxExposurePct: 0.2,
  dailyLossBreachPct: 1,
  drawdownBreachPct: 2,
  callBudgetUsd: 5,
  feeRate: 0.001,
  slippagePct: 0.015,
  enableRealTrading: false,
  allowBuySell: false,
  riskGateModule: "server/ai/risk-gate.mjs",
});

// 运行时只读配置（值继承 legacy，红线另由 RED_LINES 强制）
const CFG = Object.freeze({
  maxCycles: LEGACY_FREEZE_EXPECTED.maxCycles,
  virtualCapitalUsdt: LEGACY_FREEZE_EXPECTED.virtualCapitalUsdt,
  maxNotionalPerTrade: LEGACY_FREEZE_EXPECTED.maxNotionalPerTrade,
  callBudgetUsd: LEGACY_FREEZE_EXPECTED.callBudgetUsd,
  // engine 保守口径（COMMUNITY-VERIFIED，需实测）：fee 0.001 + slippage 0.015
  engineFeeRate: LEGACY_FREEZE_EXPECTED.feeRate,
  engineSlippagePct: LEGACY_FREEZE_EXPECTED.slippagePct,
});

// 双口径成本模型（分列，不合并）；写入 run.json，仅作标注，不参与真实成交。
const COST_MODELS = Object.freeze({
  engine_conservative: Object.freeze({
    // 来源：冻结引擎 CFG.feeRate=0.001（保守口径）+ 实验配置 slippagePct=0.015。
    // 状态：COMMUNITY-VERIFIED（社区/内部保守估计，非交易所实测），需真实环境实测校准。
    feeRate: 0.001,
    slippagePct: 0.015,
    provenance: "COMMUNITY-VERIFIED (conservative planning estimate; frozen paper-engine CFG.feeRate)",
    status: "需实测 / NOT venue-measured",
    note: "仅用于 harness 账本与保守估算，勿与 venue_verified 合并。",
  }),
  venue_verified: Object.freeze({
    // 真实交易所费率/滑点口径：本任务未实测，明确留空，禁止用保守口径冒充。
    feeRate: null,
    slippagePct: null,
    provenance: "UNVERIFIED",
    status: "NOT_MEASURED — 需要真实 venue 费率/滑点实测",
    note: "独立口径，未测得前不得合并或回填 engine_conservative 数值。",
  }),
  note: "两个口径分列，严禁合并；feeRate 0.001 保留为引擎保守口径。",
});

function verifyConfigFreeze() {
  const p = join(DEFAULT_OUT, "config.freeze.json");
  if (!existsSync(p)) throw new Error("legacy config.freeze.json missing (read-only, never rewritten)");
  const raw = readFileSync(p, "utf8");
  let onDisk;
  try { onDisk = JSON.parse(raw); }
  catch (e) { throw new Error("config.freeze.json not parseable: " + e.message); }
  // 逐字段校验（tamper-evident），不因序列化格式差异误判
  for (const [k, v] of Object.entries(LEGACY_FREEZE_EXPECTED)) {
    if (onDisk[k] !== v) throw new Error("config.freeze.json MISMATCH on field '" + k + "' — legacy freeze altered");
  }
  if (onDisk.enableRealTrading !== false || onDisk.allowBuySell !== false)
    throw new Error("red line breached: legacy enableRealTrading/allowBuySell must be false");
  if (RED_LINES.enableRealTrading !== false || RED_LINES.allowBuySell !== false)
    throw new Error("red line breached: hard-coded RED_LINES must remain false");
  return { freezeHash: sha256Hex(raw), legacyNote: "legacy file contains realMarket:true — misleading label, retained read-only" };
}

// ---- 顶层真实模块导入：失败即 throw 终止（P0-B fail-closed，不 warn 继续） ----
let fetchMarket, evaluateCycle, resolveNetworkContext, buildMarkContracts;
let EXCHANGE_US, MARKET_SPOT, frozenEngine, formalRiskGate, RISK_CODES, DEFAULT_RISK_LIMITS;
let DECISION_SCHEMA_VERSION;
try {
  ({ fetchMarket } = await import("../../equity_collector_v45/src/market.mjs"));
  ({ evaluateCycle, resolveNetworkContext } = await import("../../equity_collector_v45/src/market-guard.mjs"));
  ({ buildMarkContracts, EXCHANGE_US, MARKET_SPOT } = await import("../../equity_collector_v45/src/market-contract.mjs"));
  ({ __test: frozenEngine } = await import("../vendor/nr_trusted/functions/_lib/paper-server.mjs"));
  ({ DECISION_SCHEMA_VERSION } = await import("../server/ai/decision-schema.mjs"));
} catch (e) {
  throw new Error("[day14] FATAL: reuse import (provenance/engine/schema) failed — fail-closed: " + e.message);
}
try {
  ({ riskCheck: formalRiskGate, RISK_CODES, DEFAULT_RISK_LIMITS } = await import("../server/ai/risk-gate.mjs"));
} catch (e) {
  // 正式风控不可用 => 直接终止，绝无 fallback / warn-and-continue
  throw new Error("[day14] FATAL: formal risk-gate import failed — fail-closed termination: " + e.message);
}
if (typeof formalRiskGate !== "function") throw new Error("[day14] FATAL: riskCheck not exported as function");

// 风控 limits 由本 runner 固定；AI/provider/调用方不得覆盖（P0-B）。
const RISK_LIMITS = Object.freeze({ ...DEFAULT_RISK_LIMITS });
const RISK_APPROVED = RISK_CODES.APPROVED;      // "RISK_APPROVED" (BUY/SELL 通过)
const RISK_HOLD = RISK_CODES.HOLD_NO_ORDER;      // "RISK_HOLD_NO_ORDER" (HOLD 无订单)

// ---- append-only journals（启动全量校验链 + 恢复末尾哈希，P0-C） ----
function makeJournal(dir, name) {
  const path = join(dir, name);
  let prev = "GENESIS";
  let count = 0;
  if (existsSync(path)) {
    const lines = readFileSync(path, "utf8").split("\n").filter((l) => l.trim().length > 0);
    for (let i = 0; i < lines.length; i++) {
      let rec;
      try { rec = JSON.parse(lines[i]); }
      catch (e) { throw new Error("journal " + name + " line " + i + " not JSON: " + e.message); }
      const { _prev, _hash, ...body } = rec;
      if (typeof _prev !== "string" || typeof _hash !== "string")
        throw new Error("journal " + name + " line " + i + " missing _prev/_hash (tamper)");
      if (_prev !== prev)
        throw new Error("journal " + name + " CHAIN BROKEN at line " + i + ": _prev !== previous _hash");
      // 重算 _hash 防内容篡改（不仅是链接篡改）
      const recomputed = sha256Hex(_prev + "|" + JSON.stringify(body));
      if (recomputed !== _hash)
        throw new Error("journal " + name + " HASH TAMPER at line " + i + ": content hash mismatch");
      prev = _hash;
      count++;
    }
  }
  return {
    path,
    get prev() { return prev; },
    get count() { return count; },
    write(rec) {
      const recS = JSON.stringify(rec);
      const hash = sha256Hex(prev + "|" + recS);
      appendFileSync(path, JSON.stringify({ ...rec, _prev: prev, _hash: hash }) + "\n");
      prev = hash;
      count++;
      return hash;
    },
  };
}

function makeJournals(dir) {
  return {
    market: makeJournal(dir, "snapshots.jsonl"),
    decision: makeJournal(dir, "decisions.jsonl"),
    risk: makeJournal(dir, "risk.jsonl"),
    provider: makeJournal(dir, "provider_health.jsonl"),
    equity: makeJournal(dir, "equity.jsonl"),
  };
}

// ---- runId + 不可变配置（P0-E） ----
function genRunId(nowMs) {
  const ts = new Date(nowMs).toISOString().replace(/[:.]/g, "-");
  return "run-" + ts + "-" + randomBytes(4).toString("hex");
}

function makeRunConfig({ runId, nowMs, gitSha, freezeHash }) {
  const body = {
    schema: "day14-run-config/v2",
    runId,
    configVersion: "jev.day14.runconfig/v2",
    createdAt: new Date(nowMs).toISOString(),
    gitSha,
    // 「配置意图」与「运行证据」严格分离：intent 不是证据，不得据此贴 LIVE。
    intent: {
      marketIntent: "verified Binance.US spot marks, else fail-closed",
      providerIntent: "MiniMax-M3 (call gated by allowBuySell=false; NOT invoked by default dry fixture)",
      note: "intent describes desired config, NOT runtime evidence. Runtime label is decided by provenance.",
    },
    redLines: { enableRealTrading: false, allowBuySell: false, noEgress: true, noPaidModel: true, noOrderSubmit: true },
    costModels: COST_MODELS,
    legacyFreezeHash: freezeHash,
    legacyNote: "legacy config.freeze.json 含 realMarket:true，属误导标签；保留只读，不修改。运行证据由 provenance 决定。",
  };
  const configHash = sha256Hex(JSON.stringify(body));
  return { ...body, configHash };
}

function resolveRun({ outDir, newRun, nowMs, freezeHash }) {
  const runsDir = join(outDir, "runs");
  const ptrPath = join(outDir, "active-run.json");
  mkdirSync(runsDir, { recursive: true });
  let runId, isNew = false;
  if (!newRun && existsSync(ptrPath)) {
    const ptr = JSON.parse(readFileSync(ptrPath, "utf8"));
    if (!ptr.runId) throw new Error("active-run.json missing runId");
    runId = ptr.runId;
  } else {
    runId = genRunId(nowMs);
    isNew = true;
  }
  const dir = join(runsDir, runId);
  mkdirSync(dir, { recursive: true });
  const cfgPath = join(dir, "run.json");
  if (isNew) {
    const runCfg = makeRunConfig({ runId, nowMs, gitSha: gitSha(), freezeHash });
    writeFileSync(cfgPath, JSON.stringify(runCfg, null, 2));
    writeFileSync(ptrPath, JSON.stringify({ runId, updatedAt: new Date(nowMs).toISOString() }, null, 2));
  } else {
    if (!existsSync(cfgPath)) throw new Error("run.json missing for active run " + runId);
    const cfg = JSON.parse(readFileSync(cfgPath, "utf8"));
    const { configHash, ...body } = cfg;
    if (configHash !== sha256Hex(JSON.stringify(body)))
      throw new Error("immutable run config ALTERED: " + runId);
    if (cfg.runId !== runId) throw new Error("run.json runId mismatch");
    if (cfg.redLines.enableRealTrading !== false || cfg.redLines.allowBuySell !== false)
      throw new Error("run config red line breached");
  }
  return { runId, dir, isNew, runsDir };
}

// ---- state / 恢复（P0-C） ----
function blankState(runId) {
  return {
    runId, cycle: 0, paused: false, pauseReason: null,
    calls: 0, costUsd: 0, providerFails: 0, providerConsecutiveFails: 0,
    riskDenials: 0, riskBreach: false, reconcileMismatch: false, shadowGatedOrders: 0,
    engineAccount: null, harness: null,
    lastRecoveryAt: null, updatedAt: null,
  };
}
function loadState(dir, runId) {
  const p = join(dir, "state.json");
  if (existsSync(p)) {
    const s = JSON.parse(readFileSync(p, "utf8"));
    // 补齐旧 state 缺失字段（向后兼容）
    return { ...blankState(runId), ...s, runId };
  }
  return blankState(runId);
}
// 原子写：temp + rename，避免恢复状态写一半
function saveState(dir, s) {
  const p = join(dir, "state.json");
  const tmp = p + ".tmp";
  writeFileSync(tmp, JSON.stringify(s, null, 2));
  renameSync(tmp, p);
}

function evaluatePause(s, ctx) {
  if (s.costUsd > CFG.callBudgetUsd) return "BUDGET_EXCEEDED";       // 累计预算
  if (ctx.providerConsecutiveFails >= 3) return "PROVIDER_DOWN";
  if (s.reconcileMismatch) return "RECONCILE_MISMATCH";
  if (s.riskBreach) return "RISK_BREACH";
  if (s.cycle >= CFG.maxCycles) return "MAX_CYCLES";
  return null;
}

// ---- fixtures（确定性假数据 + 可验证 provenance，绝不触网/调模型，P0-A） ----
// fetchImpl：注入给真正 fetchMarket() 的 fetcher。返回 Binance.US 形态的响应。
function makeFixtureFetcher(nowMs, opts = {}) {
  const price = opts.price ?? 70000;
  return async (url) => {
    const u = new URL(url);
    const headers = { get: () => null };
    if (u.pathname === "/api/v3/time") {
      const serverTime = nowMs - (opts.serverTimeSkewMs || 0);
      return { ok: true, status: 200, headers, text: async () => JSON.stringify({ serverTime }) };
    }
    if (u.pathname === "/api/v3/ticker/price") {
      let syms = [];
      try { syms = JSON.parse(u.searchParams.get("symbols") || "[]"); } catch { /* empty */ }
      const items = [];
      for (const s of syms) {
        if (opts.missingSymbol === s) continue;           // 制造 missing/unlisted
        const it = { symbol: s, price: String(price) };
        if (opts.foreignMarket) it.exchange = "Binance Global spot"; // 制造 mixing/source-not-verified
        items.push(it);
      }
      return { ok: true, status: 200, headers, text: async () => JSON.stringify(items) };
    }
    throw new Error("FIXTURE_UNEXPECTED_URL:" + url);
  };
}

// providerImpl：默认 fixture，恒 HOLD/no-signal，绝不调用付费模型。
function makeFixtureProvider() {
  return async () => ({
    ok: true,
    kind: "fixture",
    model: "fixture-no-llm",
    status: "FIXTURE_NO_LLM_CALL",
    latencyMs: 0,
    costUsd: 0,
    intent: { action: "HOLD", symbol: "BTCUSDT", quantity: null, confidence: 0, reason: "no-signal (fixture)" },
  });
}

// ---- decision contract（严格匹配 decision-schema.mjs） ----
function buildDecision(cycle, intent, marketContextId) {
  const action = intent.action;
  return {
    schemaVersion: DECISION_SCHEMA_VERSION,
    strategyId: "day14-prep",
    symbol: intent.symbol || "BTCUSDT",
    action,
    side: action === "HOLD" ? null : action,
    orderType: action === "HOLD" ? null : "MARKET",
    quantity: action === "HOLD" ? null : intent.quantity,
    limitPrice: null,
    confidence: intent.confidence ?? 0,
    reason: intent.reason || "no-signal",
    riskFlags: [],
    marketContextId,
    validForSeconds: 30,
  };
}

// 从 VERIFIED provenance 构造完整 context（P0-B）；不读 provider 输出里的任何风控字段。
function buildRiskContext(acq, decision) {
  if (!acq.verified) return null; // fail-closed：无 verified provenance 不构造可交易 context
  return {
    marketContextId: acq.marketContextId,
    symbol: decision.symbol,
    markPriceUsd: acq.marksUsd[decision.symbol] ?? null,
    priceTs: acq.market.priceTs,
    markQuality: "verified",
  };
}

// ---- 真实行情门控链路：fetchMarket + evaluateCycle（P0-A，不再是占位） ----
async function acquireCycleMarket({ fetchImpl, nowMs, heldSymbols, networkContext }) {
  const market = await fetchMarket(fetchImpl, heldSymbols, { nowMs });
  const contracts = buildMarkContracts({ market, marks: market.marks, networkContext });
  const guard = evaluateCycle({
    exchange: EXCHANGE_US,
    market: MARKET_SPOT,
    sourceHost: market.sourceHost,
    priceTsSource: market.priceTsSource,
    networkContext,
    contracts,
    priorMarketKeys: [],
    freshnessVerdict: market.freshnessVerdict,
    httpStatus: market.httpStatus,
    payloadSha256: market.payloadSha256,
    foreignMarketDeclared: market.foreignMarketDeclared,
    fallbackUsed: false,
    usedLastGoodPrice: false,
    autoFallback: false,
    historyProvenance: [],
  });
  const wasVerified = market.dataQuality === "VERIFIED";
  const verified = guard.ok && wasVerified;
  const marksUsd = Object.fromEntries(
    Object.entries(market.marks).map(([k, v]) => [k, Number(v) / 1e8])
  );
  const marketContextId = "mc_fx_" + sha256Hex(market.payloadSha256 || market.priceTs || "none").slice(0, 12);
  return {
    verified,
    kind: verified ? "fixture-verified" : "unverified",
    marketKind: verified ? "fixture-verified" : "unverified",
    isReal: false, // 由 provenance 决定：fixture fetcher 永不产生真实 egress
    dataMode: "fixture",
    market,
    guard,
    marksUsd,
    marketContextId,
    snapHash: market.payloadSha256 || sha256Hex(market.priceTs || "none"),
    price: marksUsd["BTCUSDT"] ?? null,
    reason: verified ? null : (market.errorCode || guard.errorCode || "MARKET_UNVERIFIED"),
  };
}

// ---- harness 独立账本（P0-D：独立于 engine 的权益核对） ----
function blankHarness() {
  return {
    initialCash: Number(CFG.virtualCapitalUsdt),
    cash: Number(CFG.virtualCapitalUsdt),
    positions: {},
    feesPaid: 0,
    fills: 0,
    realizedPnl: 0,
  };
}
function harnessEquity(h, marksUsd) {
  let eq = h.cash;
  for (const [sym, qty] of Object.entries(h.positions)) {
    const mark = marksUsd[sym];
    // 无 mark 时不臆造价格：未验证标的按 0 计入并标注（fail-closed 精神）
    eq += qty * (Number.isFinite(mark) && mark > 0 ? mark : 0);
  }
  return eq;
}

// ---- 成交走 engineImpl（冻结引擎 __test），仅内存模拟，P3 解锁前被红线 gate ----
// simulateFills 仅用于本地验证接线：不触网、不提交订单、不写 DB，只改内存账户对象。
function executeIntent({ engineImpl, account, harness, decision, acq, cycle }) {
  const symbol = decision.symbol;
  const priceUsd = acq.marksUsd[symbol];
  const context = {
    book: {
      venue: "fixture-binance-us", symbol, sequence: 1,
      // receivedAt 用真实时钟：引擎 validateAgainstMarket 用 Date.now() 校验盘口新鲜度
      receivedAt: new Date().toISOString(),
      bids: [[priceUsd * 0.999, 10], [priceUsd * 0.998, 10]],
      asks: [[priceUsd * 1.001, 10], [priceUsd * 1.002, 10]],
      source: "fixture local in-memory book (no egress)",
    },
    filters: { minQty: 0.00001, maxQty: 100, stepSize: 0.00001, minPrice: 0.01, maxPrice: 1e9, tickSize: 0.01, minNotional: 5 },
    marks: { [symbol]: priceUsd },
  };
  const input = {
    clientOrderId: "day14-" + cycle + "-" + decision.action.toLowerCase(),
    confirmationId: "day14-conf-" + cycle,
    symbol, side: decision.action, type: "MARKET",
    quantity: decision.quantity, limitPrice: null,
  };
  const { order, fills } = engineImpl.simulateNewOrder(account, input, context);
  // 独立 harness 账本同步（不复用引擎 summarize）
  for (const f of fills) {
    const qty = Number(f.qty), notional = Number(f.notional), fee = Number(f.fee);
    harness.cash -= (notional + fee);
    harness.feesPaid += fee;
    harness.positions[symbol] = (harness.positions[symbol] || 0) + qty;
    harness.fills++;
  }
  return { orderStatus: order.status, fillCount: fills.length, fills: fills.length };
}

// ---- 真实对账（P0-D） ----
function reconcile({ engineImpl, account, harness, acq }) {
  const marksUsd = acq.verified ? acq.marksUsd : {};
  const sum = engineImpl.summarize(account, marksUsd);
  const hEq = harnessEquity(harness, marksUsd);
  const delta = Math.round((sum.equity - hEq) * 1e8) / 1e8;
  const tolerant = Math.abs(delta) <= 1e-6;
  const feesPaid = (account.trades || []).reduce((a, t) => a + Number(t.fee || 0), 0);
  const noFills = (account.trades || []).length === 0;
  return {
    equity: sum.equity, cash: sum.cash, position: sum.marketValue,
    realizedPnl: sum.realizedPnl, unrealizedPnl: sum.unrealizedPnl,
    apiFeeUsd: feesPaid,
    reconcile: tolerant ? "MATCH" : "MISMATCH",
    noFills,
    basis: {
      engine: {
        equity: sum.equity, cash: sum.cash, marketValue: sum.marketValue,
        realizedPnl: sum.realizedPnl, unrealizedPnl: sum.unrealizedPnl,
        feesFromTrades: feesPaid, trades: (account.trades || []).length,
        version: account.version, generation: account.generation,
        equityPeak: account.equityPeak,
      },
      harness: {
        equity: hEq, cash: harness.cash, positions: harness.positions,
        feesPaid: harness.feesPaid, fills: harness.fills,
      },
      delta,
      tolerance: 1e-6,
      note: noFills ? "no fills — engine 账户对象读取实际值（无成交）" : "fills present — 引擎值 vs harness 账本值比对",
    },
  };
}

// ---- recovery report（P0-C：核对累计/未完成订单/链末尾哈希） ----
function writeRecovery({ dir, s, journals, chainTips, openOrders, nowMs }) {
  const journalLengths = {};
  for (const [name, j] of Object.entries(journals)) journalLengths[name] = j.count;
  const report = {
    runId: s.runId,
    at: new Date(nowMs).toISOString(),
    resumed: s.cycle > 0,
    completedCycles: s.cycle,
    journalLengths,
    chainValid: true,
    recoveredTipHashes: chainTips,
    cumulative: {
      calls: s.calls, costUsd: s.costUsd, providerFails: s.providerFails,
      riskDenials: s.riskDenials, riskBreach: s.riskBreach,
      reconcileMismatch: s.reconcileMismatch, paused: s.paused, pauseReason: s.pauseReason,
    },
    budgetUsd: CFG.callBudgetUsd,
    budgetRemainingUsd: CFG.callBudgetUsd - s.costUsd,
    openOrders,
    pendingOrdersNeedReview: openOrders.length > 0,
    engineAccountVersion: s.engineAccount ? s.engineAccount.version : null,
    engineAccountGeneration: s.engineAccount ? s.engineAccount.generation : null,
    note: "恢复流程：全量校验链 -> 恢复末尾哈希 -> 校验已完成周期与 state -> 检查未完成订单 -> 核对累计预算/调用/失败/风险状态 -> 原子更新 state",
  };
  writeFileSync(join(dir, "recovery-report.json"), JSON.stringify(report, null, 2));
  return report;
}

function gitSha() {
  try { return (spawnSync("git", ["rev-parse", "HEAD"], { cwd: ROOT, encoding: "utf8" }).stdout || "").trim().slice(0, 12); }
  catch { return "UNKNOWN"; }
}

/**
 * 主运行体。cycles = 本次推进的周期数（增量，断点续传友好）。
 * dry=true（默认）走本地 fixture；所有 impl 均可注入用于验证；绝不触网/调模型/下单。
 */
export async function runDay14(opts = {}) {
  const {
    cycles = CFG.maxCycles,
    dry = true,
    outDir = DEFAULT_OUT,
    newRun = false,
    nowMs = Date.now(),
    fetchImpl = makeFixtureFetcher(nowMs),
    providerImpl = makeFixtureProvider(),
    engineImpl = frozenEngine,
    riskCheckImpl = formalRiskGate,
    simulateFills = false,
    initialAccount = null,
  } = opts;

  // (0) fail-closed 前置检查：正式风控不可用 => 终止，且绝不产生任何 approved 记录
  if (typeof riskCheckImpl !== "function")
    throw new Error("[day14] FATAL fail-closed: formal riskCheck unavailable — termination (no records, no approvals)");
  if (typeof fetchMarket !== "function" || typeof evaluateCycle !== "function" || !engineImpl)
    throw new Error("[day14] FATAL fail-closed: real path modules unavailable");
  const { freezeHash } = verifyConfigFreeze();

  // (1) runId + 不可变配置
  const run = resolveRun({ outDir, newRun, nowMs, freezeHash });
  const { dir, runId } = run;

  // (2) journals：启动全量校验链 + 恢复末尾哈希（模拟重启同样生效）
  const journals = makeJournals(dir);

  // (3) state 恢复
  let s = loadState(dir, runId);
  // 注入账户覆盖（验证用）：仅在未持久化账户时生效
  if (initialAccount && !s.engineAccount) s.engineAccount = initialAccount;
  if (!s.engineAccount) s.engineAccount = engineImpl.createAccount();
  if (!s.harness) s.harness = blankHarness();
  const account = s.engineAccount;

  // (4) 恢复流程：校验已完成周期与 state
  for (const [name, j] of Object.entries(journals)) {
    if (j.count !== s.cycle)
      throw new Error("RECOVERY_CYCLE_MISMATCH: " + name + " has " + j.count + " records but state.cycle=" + s.cycle);
  }
  const chainTips = {};
  for (const [name, j] of Object.entries(journals)) chainTips[name] = j.prev;
  const openOrders = (account.orders || []).filter((o) => o.status === "OPEN" || o.status === "PARTIALLY_FILLED");
  const recovery = writeRecovery({ dir, s, journals, chainTips, openOrders, nowMs });

  const ctx = {
    costUsd: s.costUsd,
    providerConsecutiveFails: s.providerConsecutiveFails,
    reconcileMismatch: s.reconcileMismatch,
    riskBreach: s.riskBreach,
  };
  const networkContext = resolveNetworkContext({ fetcherInjected: true }); // "local_test_shim"：诚实标注非真实 egress
  const log = [];

  for (let k = 0; k < cycles; k++) {
    const i = s.cycle; // 绝对周期号
    if (s.paused) { log.push({ cycle: i, paused: true, reason: s.pauseReason }); break; }

    // STEP 1: 真实行情门控链路（fetchMarket + evaluateCycle 真正调用）
    const heldSymbols = Object.keys(account.positions || {});
    if (heldSymbols.length === 0) heldSymbols.push("BTCUSDT"); // 保证 ticker 请求 -> payloadSha256 存在
    let acq;
    try { acq = await acquireCycleMarket({ fetchImpl, nowMs, heldSymbols, networkContext }); }
    catch (e) { acq = { verified: false, kind: "unverified", marketKind: "unverified", isReal: false, dataMode: "fixture", market: {}, guard: { ok: false, errorCode: "TRANSPORT" }, marksUsd: {}, marketContextId: "mc_fx_err_" + i, snapHash: "ERR", price: null, reason: "transport:" + e.message }; }
    journals.market.write({
      cycle: i, kind: acq.kind, marketKind: acq.marketKind, isReal: !!acq.isReal,
      dataMode: acq.dataMode, networkContext,
      price: acq.price, hash: acq.snapHash,
      tsSource: acq.market.priceTsSource, sourceHost: acq.market.sourceHost,
      fetchOk: acq.market.tickerOk, freshnessVerdict: acq.market.freshnessVerdict,
      markQuality: acq.market.markQuality, guardOk: acq.guard.ok,
      guardErrorCode: acq.guard.errorCode, marketContextId: acq.marketContextId,
      reason: acq.reason,
    });

    let decision, providerResult, decisionClass, aiKind, execution = "NONE", orderable = false, gate, gateName;

    if (!acq.verified) {
      // fail-closed：无 verified provenance -> NO_ACTION（不是模型 HOLD），绝不构造可交易意图
      decisionClass = "NO_ACTION";
      aiKind = "none";
      providerResult = { ok: false, kind: "no-call", model: null, status: "NO_CALL_UNVERIFIED_MARKET", latencyMs: null, costUsd: 0, note: "fail-closed: market unverified, provider not invoked" };
      decision = buildDecision(i, { action: "HOLD", symbol: "BTCUSDT", reason: "MARKET_UNVERIFIED_FAILCLOSED" }, acq.marketContextId);
      gate = riskCheckImpl({ decision, context: null, nowMs, limits: RISK_LIMITS });
      gateName = "formal-risk-gate(fail-closed)";
      ctx.providerConsecutiveFails++;
    } else {
      // STEP 2: Provider 调用走 providerImpl（默认 fixture，绝不调付费模型）
      try {
        providerResult = await providerImpl({ cycle: i, market: acq, nowMs, dry });
      } catch (e) {
        providerResult = { ok: false, kind: "error", model: null, status: "PROVIDER_ERROR", latencyMs: null, costUsd: 0, note: String(e.message) };
      }
      aiKind = (providerResult && providerResult.kind) || "unknown";
      const intent = providerResult && providerResult.intent;
      const intentValid = !!intent && typeof intent === "object" &&
        ["HOLD", "BUY", "SELL"].includes(intent.action) &&
        (intent.action === "HOLD" || (typeof intent.quantity === "number" && intent.quantity > 0));
      const providerFailed = !providerResult || providerResult.ok === false || !intentValid;

      if (providerFailed) {
        // fail-closed（任务书严格口径）：provider 超时/抛错/解析失败/无有效意图，
        // 绝不改写成模型 HOLD —— 记 NO_ACTION，与未验证行情口径一致，杜绝审计证据污染。
        if (providerResult && providerResult.status !== "PROVIDER_ERROR") {
          providerResult = { ...providerResult, ok: false, status: "PARSE_FAILED", note: "provider returned no valid intent (parse failure) — fail-closed NO_ACTION" };
        }
        decisionClass = "NO_ACTION";
        decision = buildDecision(i, { action: "HOLD", symbol: "BTCUSDT", reason: "PROVIDER_FAILED_FAILCLOSED" }, acq.marketContextId);
        const context = buildRiskContext(acq, decision);
        gate = riskCheckImpl({ decision, context, nowMs, limits: RISK_LIMITS });
        gateName = "formal-risk-gate(fail-closed-provider)";
        orderable = false;
        execution = "NONE";
      } else {
        decision = buildDecision(i, intent, acq.marketContextId);
        decisionClass = intent.action === "HOLD" ? "HOLD" : "ORDER_INTENT";

        // STEP 3: 正式风控全量校验；context 由 provenance 构造，limits 固定，不接受 provider 覆盖
        const context = buildRiskContext(acq, decision);
        gate = riskCheckImpl({ decision, context, nowMs, limits: RISK_LIMITS });
        gateName = "formal-risk-gate";
        orderable = gate.approved === true && gate.code === RISK_APPROVED && decision.action !== "HOLD";

        if (orderable && RED_LINES.allowBuySell) {
          // 真实成交路径（P3 解锁后）；本任务 allowBuySell=false 恒不进入。
          execution = "ENGINE_EXEC";
        } else if (orderable && simulateFills) {
          // 仅本地内存模拟，验证「成交走 engineImpl」接线；不触网/不下单/不写 DB。
          const r = executeIntent({ engineImpl, account, harness: s.harness, decision, acq, cycle: i });
          execution = "SIMULATED_FILL_LOCAL:" + r.fillCount;
          s.shadowGatedOrders++;
        } else if (orderable) {
          execution = "SHADOW_GATED"; // 可下单意图被红线拦截
          s.shadowGatedOrders++;
        } else if (decision.action !== "HOLD" && gate.approved === false) {
          execution = "RISK_REJECTED";
          s.riskDenials++;
        }
      }
    }

    const riskApproved = gate.approved;
    const riskCode = gate.code;
    // HOLD 通过契约(approved=true, RISK_HOLD_NO_ORDER) != BUY/SELL 通过(RISK_APPROVED)：
    // 用 orderable / decisionClass / code 明确区分，避免「approved:true == 可交易」误导。
    journals.risk.write({
      cycle: i, gate: gateName, action: decision.action,
      approved: riskApproved, code: riskCode, orderable,
      decisionClass, notional: decision.action === "HOLD" ? 0 : decision.quantity,
      failClosedReason: decisionClass === "NO_ACTION"
        ? (acq.verified ? "PROVIDER_FAILED" : "MARKET_UNVERIFIED") : null,
      riskLimits: RISK_LIMITS,
      context: acq.verified ? { symbol: decision.symbol, markQuality: "verified", priceTs: acq.market.priceTs, markPriceUsd: acq.marksUsd[decision.symbol] ?? null } : null,
      checks: gate.checks || null, evaluated: gate.evaluated || null,
      confidenceIgnored: gate.confidence_ignored ?? null,
    });
    journals.decision.write({
      cycle: i, kind: acq.kind, marketKind: acq.marketKind, aiKind, decisionClass,
      action: decision.action, notional: decision.action === "HOLD" ? 0 : decision.quantity,
      confidence: decision.confidence, riskApproved, riskCode, orderable,
      reason: decision.reason, marketContextId: decision.marketContextId,
      marketHash: acq.snapHash, mode: dry ? "DRY_FIXTURE" : "INJECTED", execution,
    });
    // STEP 4: provider 状态来自 providerImpl 实际返回，绝不写死 CALL_OK
    journals.provider.write({
      cycle: i, provider: LEGACY_FREEZE_EXPECTED.provider,
      status: providerResult.status, model: providerResult.model, kind: providerResult.kind,
      latencyMs: providerResult.latencyMs ?? null, costUsd: providerResult.costUsd || 0,
      note: providerResult.note || ("real AI call gated (allowBuySell=false); " + (dry ? "dry fixture" : "injected impl")),
    });

    // STEP 5: 真实对账（engineImpl.summarize + 独立 harness）
    const rec = reconcile({ engineImpl, account, harness: s.harness, acq });
    journals.equity.write({
      cycle: i, mode: dry ? "simulated" : "injected",
      equity: rec.equity, cash: rec.cash, position: rec.position,
      realizedPnl: rec.realizedPnl, unrealizedPnl: rec.unrealizedPnl, apiFeeUsd: rec.apiFeeUsd,
      reconcile: rec.reconcile, noFills: rec.noFills, basis: rec.basis,
    });
    if (rec.reconcile === "MISMATCH") { s.reconcileMismatch = true; ctx.reconcileMismatch = true; }

    // STEP 6: 累计状态原子持久化（costUsd / providerFails / calls / riskBreach / reconcileMismatch / 账户 / harness）
    s.costUsd = ctx.costUsd + (providerResult.costUsd || 0);
    ctx.costUsd = s.costUsd;
    if (providerResult.ok === false && providerResult.kind !== "no-call") {
      s.providerFails++;
      ctx.providerConsecutiveFails++;
    } else if (providerResult.kind !== "no-call") {
      ctx.providerConsecutiveFails = 0;
    }
    s.providerConsecutiveFails = ctx.providerConsecutiveFails;
    s.calls++;
    s.cycle = i + 1;
    s.lastRecoveryAt = recovery.at;
    s.updatedAt = new Date(nowMs).toISOString();
    saveState(dir, s); // 原子写

    const pause = evaluatePause(s, ctx);
    if (pause) { s.paused = true; s.pauseReason = pause; saveState(dir, s); break; }
  }

  const anchor = writeAnchor({ dir, journals, runId, freezeHash, recovery, s });
  const sanity = internalSanity({ dir, journals, s, freezeHash, runId });
  const summary = {
    sha: gitSha(), runId, runDir: dir,
    cyclesThisRun: s.cycle - recovery.completedCycles,
    cycles: s.cycle, calls: s.calls, costUsd: s.costUsd,
    paused: s.paused, pauseReason: s.pauseReason,
    reconcileMismatch: s.reconcileMismatch, riskBreach: s.riskBreach,
    costModels: COST_MODELS,
    internalSanity: sanity, anchor, recovery,
  };
  writeFileSync(join(dir, "day14-summary.json"), JSON.stringify(summary, null, 2));
  return summary;
}

// 锚点：覆盖恢复后的链 tip + runId + freezeHash + recovery（P0-E 扩展）
function writeAnchor({ dir, journals, runId, freezeHash, recovery, s }) {
  const tips = {};
  for (const [name, j] of Object.entries(journals)) tips[name] = j.prev;
  const root = sha256Hex(JSON.stringify({ freezeHash, runId, tips, cycle: s.cycle, ts: recovery.at }));
  const anchor = {
    root, freezeHash, runId, journalTips: tips, cycle: s.cycle,
    chainResumedFrom: recovery.recoveredTipHashes,
    generatedAt: recovery.at,
    note: "local anchor over recovered chain + runId + legacy freeze hash; external anchor = commit SHA (not pushed in this task)",
  };
  writeFileSync(join(dir, "day14-anchor.json"), JSON.stringify(anchor, null, 2));
  return anchor;
}

// 内部自检（NOT independent QA；独立验收见 task #2 independent-qa）。不自证整体绿。
function internalSanity({ dir, journals, s, freezeHash, runId }) {
  const checks = [
    { item: "5 journals present & non-empty after run", ok: Object.values(journals).every((j) => j.count > 0) },
    { item: "state.json persisted with cumulative fields", ok: existsSync(join(dir, "state.json")) && typeof s.costUsd === "number" && typeof s.calls === "number" },
    { item: "day14-anchor.json written (recovered chain + runId)", ok: existsSync(join(dir, "day14-anchor.json")) },
    { item: "run.json immutable config present", ok: existsSync(join(dir, "run.json")) },
    { item: "legacy config.freeze.json hash bound to anchor", ok: typeof freezeHash === "string" && freezeHash.length === 64 },
    { item: "no real trading (RED_LINES false)", ok: RED_LINES.enableRealTrading === false && RED_LINES.allowBuySell === false },
    { item: "formal risk gate wired (no fallback)", ok: typeof formalRiskGate === "function" },
  ];
  return {
    scope: "INTERNAL_SANITY_ONLY — 非独立 QA，不得据此宣称整体验收通过（independent-qa = task #2）",
    pass: checks.every((c) => c.ok),
    checks,
  };
}

// ---- 可测纯函数 / 只读常量聚合导出（供 independent-qa 直接单测；纯增量，不改变运行行为） ----
//   makeJournal(dir,name)        构造时即全量校验链 + 恢复末尾哈希，断链/篡改即 throw
//   verifyConfigFreeze()         只读校验 legacy freeze 逐字段 + 红线，返回 {freezeHash,legacyNote}
//   reconcile({engineImpl,account,harness,acq})  独立账户对账，返回 engine vs harness + delta
//   makeFixtureFetcher(nowMs,opts) / makeFixtureProvider()  确定性本地 fixture（含故障注入选项）
//   sha256Hex(s)                 链哈希数学
//   RED_LINES / CFG / COST_MODELS / LEGACY_FREEZE_EXPECTED / RISK_LIMITS  只读常量
export {
  sha256Hex,
  verifyConfigFreeze,
  makeJournal,
  reconcile,
  makeFixtureFetcher,
  makeFixtureProvider,
  RED_LINES, CFG, COST_MODELS, LEGACY_FREEZE_EXPECTED, RISK_LIMITS,
};

// CLI — robust main-module detection (Windows path/case/drive-letter safe)
function isMainModule() {
  try {
    const invoked = process.argv[1] ? pathToFileURL(process.argv[1]).href : "";
    return import.meta.url === invoked;
  } catch { return false; }
}
if (isMainModule()) {
  const args = process.argv.slice(2);
  const cycles = Number(args.find((a) => /^\d+$/.test(a))) || CFG.maxCycles;
  const newRun = args.includes("--new-run");
  const outIdx = args.indexOf("--out");
  const outDir = outIdx >= 0 && args[outIdx + 1] ? args[outIdx + 1] : undefined;
  runDay14({ dry: true, cycles, newRun, ...(outDir ? { outDir } : {}) })
    .then((r) => console.log(JSON.stringify(r, null, 2)))
    .catch((e) => { console.error("[day14] RUN FAILED:", e.message); process.exitCode = 1; });
}
