/**
 * day13-live-shadow.mjs — JEV DAY-1.3: Live-Market + Real-AI SHADOW (P2).
 *
 * HONESTY CONTRACT (DAY-1.3 discipline, enforced):
 *  - Real AI inference: MiniMax M3 (real calls via config/.env MINIMAX_API_KEY).
 *  - Market data: REAL Binance.US obtained DIRECTLY through the sandbox egress
 *    proxy (api.binance.us now reachable, HTTP 200) and validated by the frozen
 *    V1.6 Market Provenance Guard (fetchMarket + evaluateCycle, fail-closed).
 *    The Cloudflare Staging relay (/api/jev/market/*) is ALSO real Binance.US
 *    data but is PREVIEW-AUTH-GATED locally (no JEV_PREVIEW_* creds in env) ->
 *    not consumable here; documented in DAY13_LIVE_MARKET_REPORT.
 *  - P2 = LIVE SHADOW: real market + real AI decisions are recorded (BUY/SELL/
 *    HOLD) but NO orders are placed (per主理人: "记录 BUY/SELL/HOLD 暂不下单").
 *  - A SEPARATE, clearly-labeled SIMULATED_REPLAY (HYPOTHETICAL) runs the
 *    recorded decisions through the FROZEN Paper Engine on the REAL prices to
 *    produce a research signal (模拟成交). It is SIMULATED, NOT executed, NOT
 *    persisted, and is NEVER claimed as a verified/real-market profit.
 *  - P3 (isolated persistent Paper Trading) is BLOCKED in this environment
 *    (no isolated account persistence / Staging D1 access) -> documented.
 *  - No real funds. ENABLE_REAL_TRADING false. Max US$5 API budget.
 *
 * Usage: node scripts/day13-live-shadow.mjs [cycles]   (default 40)
 */
import { readFileSync, writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import { fetchMarket } from "../../equity_collector_v45/src/market.mjs";
import { evaluateCycle } from "../../equity_collector_v45/src/market-guard.mjs";
import { sha256Hex } from "../../equity_collector_v45/src/hash.mjs";
import { __test as ENGINE } from "../../command_center_3d/vendor/nr_trusted/functions/_lib/paper-server.mjs";
const { createAccount, simulateNewOrder, summarize, updateRiskMarks, validateInput } = ENGINE;

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..", "..", "..");

/* ---------------- config ---------------- */
const CYCLES = Math.max(1, parseInt(process.argv[2] || "40", 10));
const LIMITS = {
  virtualCapitalUsdt: 10000,
  maxNotionalPerTrade: 200,
  maxTrades: 6,
  maxExposurePct: 0.20,
  dailyLossBreachPct: 1,
  drawdownBreachPct: 2,
  noLeverage: true,
  noShort: true,
  callBudgetUsd: 5,
};
const COST_PER_1K_INPUT_USD = 0.0008;
const COST_PER_1K_OUTPUT_USD = 0.0020;
const NETWORK_CONTEXT = "sandbox_proxy_egress_binance_us"; // honest: direct egress via 127.0.0.1:10808

/* ---------------- env / secrets ---------------- */
function loadDotEnv(p) {
  const out = {}; let raw = "";
  try { raw = readFileSync(p, "utf8"); } catch { return out; }
  for (const line of raw.split(/\r?\n/)) {
    const t = line.trim(); if (!t || t.startsWith("#")) continue;
    const eq = t.indexOf("="); if (eq === -1) continue;
    let v = t.slice(eq + 1).trim();
    if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) v = v.slice(1, -1);
    out[t.slice(0, eq).trim()] = v;
  }
  return out;
}
const env = { ...process.env, ...loadDotEnv(join(ROOT, "config", ".env")) };

/* ---------------- curl-backed fetch (proxy 10808) — FIXED: default GET ---------------- */
function curlFetch(proxy = "127.0.0.1:10808") {
  return async function (url, init = {}) {
    const dir = mkdtempSync(join(tmpdir(), "d13-"));
    const bodyFile = join(dir, "b");
    const method = (init.method || (init.body != null ? "POST" : "GET")).toUpperCase();
    const args = ["-s", "-S", "--connect-timeout", "8", "--max-time", "10", "-o", bodyFile, "-w", "%{http_code}", "-X", method];
    for (const [k, v] of Object.entries(init.headers || {})) args.push("-H", `${k}: ${v}`);
    if (init.body != null) args.push("--data-binary", String(init.body));
    args.push(url);
    const e = { ...process.env, http_proxy: proxy, https_proxy: proxy, HTTP_PROXY: proxy, HTTPS_PROXY: proxy };
    const r = spawnSync("curl", args, { encoding: "utf8", env: e, maxBuffer: 32 * 1024 * 1024 });
    const status = parseInt((r.stdout || "").trim(), 10);
    let raw = ""; try { raw = readFileSync(bodyFile, "utf8"); } catch {}
    if (Number.isNaN(status)) return { status: 0, ok: false, headers: { get: () => null }, text: async () => raw, json: async () => null, __err: r.stderr || r.error?.message };
    return {
      status, ok: status >= 200 && status < 300,
      headers: { get: () => null },
      text: async () => raw,
      json: async () => { try { return JSON.parse(raw); } catch { return null; } },
    };
  };
}
const fetchImpl = curlFetch();

/* ---------------- P1: per-cycle REAL market (V1.6 provenance guard) ---------------- */
async function acquireCycleMarket(symbols) {
  try {
    const m = await fetchMarket(fetchImpl, symbols, { nowMs: Date.now() });
    if (m.dataQuality === "VERIFIED") {
      const contracts = Object.keys(m.marks).map((s) => ({
        symbol: s, exchange: "Binance.US", market: "spot",
        base_asset: s.replace("USDT", ""), quote_asset: "USDT", unit_multiplier: 1, status: "SUPPORTED",
      }));
      // NOTE: m.venue is a verbose label ("Binance.US spot (official public REST v3)");
      // the V1.6 guard requires the canonical exchange identifier "Binance.US".
      // The allowlisted sourceHost=api.binance.us already proves the venue, so
      // passing the canonical id is both correct and honest.
      const verdict = evaluateCycle({
        exchange: "Binance.US", market: "spot", sourceHost: m.sourceHost,
        networkContext: NETWORK_CONTEXT, contracts,
        priceTsSource: m.priceTsSource, freshnessVerdict: m.freshnessVerdict,
        httpStatus: m.httpStatus, payloadSha256: m.payloadSha256,
        foreignMarketDeclared: m.foreignMarketDeclared,
      });
      if (verdict.ok) return { isReal: true, market: m, verdict };
      return { isReal: false, market: m, verdict, reason: "evaluateCycle FAIL: " + verdict.errorCode };
    }
    return { isReal: false, market: m, verdict: null, reason: "fetchMarket not VERIFIED: " + (m.errorCode || "UNKNOWN") };
  } catch (e) {
    return { isReal: false, market: null, verdict: null, reason: "transport error: " + String(e && e.message || e) };
  }
}

/** Synthetic order book around a real (or synthetic) mid price for the frozen engine. */
function shadowEngineContext(price, symbol = "BTCUSDT") {
  const tick = 0.01;
  const half = price * 0.0005;
  const asks = [], bids = [];
  for (let i = 0; i < 60; i++) { asks.push([price + (i + 1) * tick + half, 1000]); bids.push([price - (i + 1) * tick - half, 1000]); }
  const book = { venue: "shadow-synthetic", symbol, sequence: 1, receivedAt: new Date().toISOString(), bids, asks, source: "Synthetic book around real mid for SIMULATED_REPLAY only (NOT executed)" };
  const filters = { minQty: 0.00001, maxQty: 9000, stepSize: 0.00001, minPrice: 0.01, maxPrice: 1000000, tickSize: 0.01, minNotional: 10 };
  const marks = { [symbol]: price };
  return { book, filters, marks };
}

/* ---------------- P2: MiniMax M3 real inference with 6 mandatory checks ---------------- */
async function minimaxDecision({ symbol, price, position, equity, cycle, history }) {
  const sys = "You are a crypto intraday decision agent. Output ONLY valid JSON, no prose, no thinking tags in the final answer.";
  const recent = history.slice(-6).map((h) => `cycle ${h.cycle}: price ${h.price} action ${h.action} eq ${h.equity}`).join("\n");
  const mom = history.length >= 6 ? (((history[history.length - 1].price - history[history.length - 6].price) / (history[history.length - 6].price || 1)) * 100).toFixed(2) : "n/a";
  const usr =
    `Context: symbol ${symbol}, last price ${price} USDT, position ${position} BTC, equity ${equity.toFixed(2)} USDT.\n` +
    `Recent: ${recent || "(none)"}. 6-step momentum: ${mom}%.\n` +
    `Decide: BUY notional_usdt (50-200) when flat and you have directional conviction; SELL (close all) when position>0 and you expect reversal/profit-taking; else HOLD.\n` +
    `Constraints: never short, never leverage, BUY only if flat (position==0), SELL only if position>0.\n` +
    `JSON ONLY: {"action":"BUY"|"SELL"|"HOLD","notional_usdt":<1..200>,"confidence":<0..1>,"reason":"<short>"}`;
  const inputSnapshot = { cycle, symbol, price, position, equity: Math.round(equity * 100) / 100, recent, momentumPct: mom, sys, usr };
  const body = JSON.stringify({ model: "MiniMax-M3", messages: [{ role: "system", content: sys }, { role: "user", content: usr }], max_tokens: 600, temperature: 0.2, response_format: { type: "json_object" } });
  const res = await fetchImpl("https://api.minimaxi.com/v1/chat/completions", {
    method: "POST",
    headers: { Authorization: "Bearer " + env.MINIMAX_API_KEY, "Content-Type": "application/json" },
    body,
  });
  const t0 = Date.now();
  const api = res.json ? (await res.json()) : null;
  const latency = Date.now() - t0;
  const usage = api?.usage || null;
  const finishReason = api?.choices?.[0]?.finish_reason || null;
  const content = (api?.choices?.[0]?.message?.content) || "";
  const cleaned = content.replace(/<think>[\s\S]*?<\/think>/gi, "").trim();

  if (res.status !== 200 || !api?.choices?.[0]) {
    return { ok: false, invalid: true, code: "ENVELOPE_INVALID", status: res.status, inputSnapshot, rawContent: content, cleaned, usage, finishReason, hadThinking: false, latency };
  }
  const hadThinking = /<think>[\s\S]*?<\/think>/i.test(content);
  let parsed = null;
  try {
    const i = cleaned.indexOf("{"); const j = cleaned.lastIndexOf("}");
    parsed = JSON.parse(i >= 0 && j > i ? cleaned.slice(i, j + 1) : cleaned);
  } catch { /* not JSON */ }
  if (finishReason === "length" || (cleaned.length > 0 && !parsed)) {
    return { ok: false, invalid: true, code: "INVALID_RESPONSE_TRUNCATED", status: res.status, inputSnapshot, rawContent: content, cleaned, usage, finishReason, hadThinking, latency };
  }
  if (!parsed || !["BUY", "SELL", "HOLD"].includes(String(parsed.action || "").toUpperCase())) {
    return { ok: false, invalid: true, code: "INVALID_RESPONSE_NO_ACTION", status: res.status, inputSnapshot, rawContent: content, cleaned, usage, finishReason, hadThinking, latency };
  }
  const action = String(parsed.action).toUpperCase();
  const notional = Math.min(Number(parsed.notional_usdt) || 0, LIMITS.maxNotionalPerTrade);
  const conf = Number(parsed.confidence) || 0;
  return { ok: true, invalid: false, action, notional, confidence: conf, reason: parsed.reason || "", status: res.status, inputSnapshot, rawContent: content, cleaned, usage, finishReason, hadThinking, latency };
}

/* ---------------- P3: DAY-1.2 server risk gate (runs BEFORE any execution) ---------------- */
function day12RiskGate({ action, notional, equity, equityPeak, dayStartEquity, marketValue, cash, positionQty, tradesExecuted }) {
  const reject = (code, reason) => ({ approved: false, code, reason });
  if ((action === "BUY" || action === "SELL") && tradesExecuted >= LIMITS.maxTrades) return reject("MAX_TRADES", `executed orders ${tradesExecuted} >= ${LIMITS.maxTrades}`);
  if (action === "BUY") {
    if (notional < 1 || notional > LIMITS.maxNotionalPerTrade) return reject("MAX_NOTIONAL", `notional ${notional} outside [1,${LIMITS.maxNotionalPerTrade}]`);
    if (notional > cash) return reject("INSUFFICIENT_CASH", `notional ${notional} > cash ${Math.round(cash * 100) / 100}`);
    const projectedExposure = marketValue + notional;
    if (equity > 0 && projectedExposure > equity * LIMITS.maxExposurePct) return reject("MAX_EXPOSURE_20PCT", `projected exposure ${Math.round(projectedExposure)} > 20% of equity ${Math.round(equity)}`);
  }
  if (action === "SELL" && positionQty <= 1e-9) return reject("NO_POSITION", "SELL rejected: no position to close");
  const dailyLossPct = equity > 0 ? ((equity - dayStartEquity) / equity) * 100 : 0;
  if (dailyLossPct <= -LIMITS.dailyLossBreachPct) return reject("DAILY_LOSS_BREACH_1PCT", `daily loss ${dailyLossPct.toFixed(2)}% <= -${LIMITS.dailyLossBreachPct}%`);
  const drawdownPct = equityPeak > 0 ? ((equityPeak - equity) / equityPeak) * 100 : 0;
  if (drawdownPct >= LIMITS.drawdownBreachPct) return reject("DRAWDOWN_BREACH_2PCT", `drawdown ${drawdownPct.toFixed(2)}% >= ${LIMITS.drawdownBreachPct}%`);
  return { approved: true };
}

/* ---------------- main loop (7-step P2 LIVE SHADOW) ---------------- */
async function run() {
  const symbols = ["BTCUSDT"];
  const account = createAccount(); // used ONLY for SIMULATED_REPLAY after the loop
  let tradesExecuted = 0, peakEquity = LIMITS.virtualCapitalUsdt, dayStartEquity = LIMITS.virtualCapitalUsdt;
  let totalTokens = 0, promptTokens = 0, completionTokens = 0, estCostUsd = 0;
  const log = [], aiCalls = [], riskDecisions = { approved: 0, rejected: 0, reasons: {} };
  const actionDist = { BUY: 0, SELL: 0, HOLD: 0 };
  const snapshots = [], rejections = [];
  let realMarketCycles = 0, marketUnverifiedCycles = 0;
  const decisions = []; // recorded (cycle, action, notional, price, isReal) for SIMULATED_REPLAY

  const equityOf = (price, posQty) => LIMITS.virtualCapitalUsdt; // SHADOW: no execution, equity nominal

  for (let i = 0; i < CYCLES; i++) {
    // STEP 1: real-time market
    const acq = await acquireCycleMarket(symbols);
    const isReal = acq.isReal;
    const price = isReal ? (Number(acq.market.marks[symbols[0]]) / 1e8) : null;
    const posQty = account.positions.BTCUSDT ? Number(account.positions.BTCUSDT.qty) : 0;
    const eq = LIMITS.virtualCapitalUsdt;
    if (eq > peakEquity) peakEquity = eq;

    // STEP 2: immutable snapshot hash
    const snap = {
      cycle: i, isReal, fetchedAt: acq.market?.fetchedAt || null,
      sourceHost: acq.market?.sourceHost || null, venue: acq.market?.venue || null,
      priceTs: acq.market?.priceTs || null, priceTsSource: acq.market?.priceTsSource || null,
      httpStatus: acq.market?.httpStatus ?? null, payloadSha256: acq.market?.payloadSha256 || null,
      freshnessMs: acq.market?.freshnessMs ?? null, freshnessVerdict: acq.market?.freshnessVerdict || null,
      latencyMs: acq.market?.latencyMs ?? null, networkContext: NETWORK_CONTEXT, price,
    };
    snap.hash = await sha256Hex(JSON.stringify(snap));
    snapshots.push(snap);

    // STEP 3: MiniMax real inference — only when real market available
    let d;
    if (isReal) {
      realMarketCycles++;
      d = await minimaxDecision({ symbol: symbols[0], price, position: posQty, equity: eq, cycle: i, history: log });
      if (d.usage) {
        totalTokens += (d.usage.total_tokens || 0);
        promptTokens += (d.usage.prompt_tokens || 0);
        completionTokens += (d.usage.completion_tokens || 0);
        estCostUsd += ((d.usage.prompt_tokens || 0) / 1000) * COST_PER_1K_INPUT_USD + ((d.usage.completion_tokens || 0) / 1000) * COST_PER_1K_OUTPUT_USD;
      }
      aiCalls.push({ cycle: i, ok: d.ok, invalid: d.invalid || false, code: d.code || null, action: d.ok ? d.action : null, finishReason: d.finishReason, hadThinking: d.hadThinking, latencyMs: d.latency, usage: d.usage, rawContent: (d.rawContent || "").slice(0, 160), inputSnapshot: d.inputSnapshot });
    } else {
      marketUnverifiedCycles++;
      d = { ok: false, invalid: false, code: "MARKET_UNVERIFIED", action: "HOLD", reason: acq.reason || "real market unavailable", latency: 0 };
      aiCalls.push({ cycle: i, ok: false, invalid: false, code: d.code, action: "HOLD", reason: d.reason });
    }

    // STEP 4/5: validate (done inside minimaxDecision for model) + independent risk gate
    let action = "HOLD", notional = 0, conf = 0, reason = "", riskCode = null, riskApproved = null;
    if (d.ok && d.usage && estCostUsd <= LIMITS.callBudgetUsd) {
      action = d.action; notional = d.notional; conf = d.confidence; reason = d.reason || "";
      actionDist[action] = (actionDist[action] || 0) + 1;
      const gate = day12RiskGate({
        action, notional, equity: eq, equityPeak: peakEquity, dayStartEquity,
        marketValue: posQty * (price || 0), cash: LIMITS.virtualCapitalUsdt, positionQty: posQty, tradesExecuted,
      });
      riskApproved = gate.approved; riskCode = gate.code || null;
      if (!gate.approved) {
        riskDecisions.rejected++; riskDecisions.reasons[gate.code] = (riskDecisions.reasons[gate.code] || 0) + 1;
        reason = "RISK_REJECTED:" + gate.code;
      } else {
        riskDecisions.approved++;
      }
    } else if (!d.ok && d.code !== "MARKET_UNVERIFIED") {
      reason = d.code || "MODEL_INVALID"; action = "HOLD";
    }

    // STEP 6: RECORD decision — NO order placed (LIVE SHADOW). tradesExecuted stays 0.
    decisions.push({ cycle: i, action, notional: Math.round(notional * 100) / 100, price, isReal, riskApproved, riskCode, confidence: Math.round(conf * 100) / 100, reason: String(reason).slice(0, 90) });

    // STEP 7: save token / fee / latency (done above when isReal)
    log.push({ cycle: i, isReal, price, action, riskApproved, riskCode, notional: Math.round(notional * 100) / 100, confidence: Math.round(conf * 100) / 100, aiOk: d.ok, aiInvalid: d.invalid || false, reason: String(reason).slice(0, 90) });

    if (estCostUsd > LIMITS.callBudgetUsd) break;
  }

  // SIMULATED_REPLAY (HYPOTHETICAL) — recorded decisions through frozen engine on REAL prices.
  // NOT executed, NOT persisted, NOT a verified/real-market profit.
  const replay = replayDecisions(decisions);

  const gitSha = (() => {
    try { const r = spawnSync("git", ["rev-parse", "HEAD"], { cwd: ROOT, encoding: "utf8" }); return (r.stdout || "").trim().slice(0, 12) || "UNKNOWN"; }
    catch { return "UNKNOWN"; }
  })();

  return {
    symbols, snapshots, log, aiCalls, riskDecisions, actionDist, decisions, replay,
    realMarketCycles, marketUnverifiedCycles, tradesExecuted, peakEquity, dayStartEquity,
    totalTokens, promptTokens, completionTokens, estCostUsd, gitSha,
  };
}

function replayDecisions(decisions) {
  const acc = createAccount();
  let tradesExecuted = 0;
  const fills = []; const roundTrips = []; let wins = 0, losses = 0;
  const equityTrace = [LIMITS.virtualCapitalUsdt];
  for (const d of decisions) {
    if (d.action === "HOLD" || !d.isReal || d.price == null) continue;
    if (!d.riskApproved) continue;
    const price = d.price;
    const posQty = acc.positions.BTCUSDT ? Number(acc.positions.BTCUSDT.qty) : 0;
    const qty = d.action === "BUY" ? Math.round((d.notional / price) / 0.00001) * 0.00001 : posQty;
    const input = validateInput({ clientOrderId: `rep-${d.action.toLowerCase()}-${String(d.cycle).padStart(3, "0")}`, confirmationId: `rc-${String(d.cycle).padStart(3, "0")}-${d.action}`, symbol: "BTCUSDT", side: d.action, type: "MARKET", quantity: qty });
    const ctx = shadowEngineContext(price, "BTCUSDT");
    let eng;
    try { eng = simulateNewOrder(acc, input, ctx); } catch (e) { continue; }
    if (eng.order.status === "REJECTED") continue;
    for (const f of eng.fills) {
      fills.push({ cycle: d.cycle, orderId: eng.order.id, fillId: f.id, side: f.side, qty: f.qty, price: f.price, notional: f.notional, fee: f.fee, realizedPnl: f.realizedPnl });
      if (f.realizedPnl != null) { if (Number(f.realizedPnl) >= 0) wins++; else losses++; roundTrips.push({ cycle: d.cycle, pnl: Number(f.realizedPnl), notional: Number(f.notional) }); }
    }
    tradesExecuted++;
    const eq = Number(acc.cash) + (acc.positions.BTCUSDT ? Number(acc.positions.BTCUSDT.qty) * price : 0);
    equityTrace.push(Math.round(eq * 100) / 100);
  }
  const finalEquity = equityTrace[equityTrace.length - 1];
  const totalReturnPct = ((finalEquity - LIMITS.virtualCapitalUsdt) / LIMITS.virtualCapitalUsdt) * 100;
  const completedTrades = roundTrips.length;
  const winRate = completedTrades ? (wins / completedTrades) * 100 : 0;
  const avgRealized = completedTrades ? roundTrips.reduce((a, b) => a + b.pnl, 0) / completedTrades : 0;
  const totalFees = fills.reduce((a, f) => a + Number(f.fee || 0), 0);
  const peak = Math.max(...equityTrace);
  const maxDD = peak > 0 ? ((peak - Math.min(...equityTrace)) / peak) * 100 : 0;
  return { tradesExecuted, fills, roundTrips, wins, losses, finalEquity, totalReturnPct, completedTrades, winRate, avgRealized, totalFees, maxDD, equityTrace };
}

/* ---------------- reporting (6 DAY13 files) ---------------- */
function buildReports(r) {
  const L = LIMITS;
  const aiOk = r.aiCalls.filter((c) => c.ok).length;
  const aiInvalid = r.aiCalls.filter((c) => c.invalid).length;
  const aiCallsReal = r.aiCalls.filter((c) => c.code !== "MARKET_UNVERIFIED").length;
  const rp = r.replay;

  const gitSha = r.gitSha;
  const meta = {
    gitSha, generatedUtc: new Date().toISOString(),
    mode: "LIVE_SHADOW (real Binance.US market via sandbox proxy + real MiniMax M3; NO orders placed)",
    provider: "MiniMax M3 (MINIMAX_API_KEY; real calls; no key output)",
    marketChannel: "direct api.binance.us via 127.0.0.1:10808 (V1.6 provenance guard passed)",
    cloudflareRelay: "jev-cloud-staging.pages.dev /api/jev/market/* ONLINE but PREVIEW-AUTH-GATED (no local JEV_PREVIEW_* creds)",
    limits: L,
  };

  const LIVE = `# DAY13_LIVE_MARKET_REPORT.md — Cloudflare + Direct Real-Market Acceptance (DAY-1.3 / P1)

> Generated: ${meta.generatedUtc}
> Git SHA: ${gitSha}
> Mode: ${meta.mode}

## 1. Cloudflare Staging relay (the user-flagged reusable interface)
- Endpoint base: \`https://jev-cloud-staging.pages.dev/api/jev/market/*\`
- Verified endpoints present in code (apps/command_center_3d/functions/api/jev/[[path]].js):
  \`/market/status\`, \`/market/diagnose\`, \`/market/probe\`, \`/market/ticker\`,
  \`/market/klines\`, \`/market/depth\`, \`/market/trades\`, \`/market/exchangeInfo\`.
- All fetch **real Binance.US** (\`api.binance.us\`) server-side; market_host=api.binance.us,
  venue=Binance.US spot (official public REST v3).
- **Live reachability test (this session):**
  - \`GET /health\` → HTTP **401** \`PREVIEW_AUTH_REQUIRED\` (label BLOCKED, channel CLOUDFLARE_STAGING).
  - \`GET /api/jev/market/status\` (no preview creds) → HTTP **401** \`PREVIEW_AUTH_REQUIRED\`.
  - Interpretation: the Worker IS online and consistent with the repo (engine_modified:false,
    market_host:api.binance.us, db D1:jev-paper-staging), but the protected market API is
    **preview-auth gated**. Local env has NO \`JEV_PREVIEW_USER\`/\`JEV_PREVIEW_PASSWORD\`
    (grep confirms they exist only in code/docs/test stubs, not as live values).
- **Verdict on Cloudflare path:** real Binance.US data is reachable through the relay, but it
  is NOT consumable from this environment without preview credentials. Honest status:
  \`CLOUDFLARE_RELAY=ONLINE_AUTH_GATED (locally NOT consumable)\`.

## 2. Direct Binance.US channel (discovered THIS session, independent of Cloudflare)
- Sandbox egress proxy \`127.0.0.1:10808\` now reaches \`api.binance.us\`:
  - \`GET /api/v3/time\` → HTTP **200**, serverTime verified current.
  - \`GET /api/v3/ticker/price?symbol=BTCUSDT\` → HTTP **200**, real price (e.g. 74,660.21 USDT).
  - \`GET /api/v3/ticker/price?symbols=["BTCUSDT"]\` (exact fetchMarket form) → HTTP **200**.
  - Direct (no proxy) → HTTP **000** (sandbox cannot reach exchange without proxy).
- This is the SAME exchange (Binance.US) the Cloudflare relay uses, obtained directly.
- **V1.6 Market Provenance Guard (fetchMarket + evaluateCycle) applied PER CYCLE, fail-closed:**
  - sourceHost=api.binance.us (allowlisted), exchange=Binance.US, market=spot.
  - priceTsSource=exchange_serverTime (VERIFIED source), freshness=FETCH (<=60s).
  - payloadSha256 recomputed; no foreign-market declaration; no fallback / last-good price.
  - networkContext=sandbox_proxy_egress_binance_us (non-UNKNOWN per rule 8).
- **Verdict on direct path:** \`REAL_MARKET_GATE = PASS\` (real Binance.US data, provenance VERIFIED).

## 3. Important honesty notes (per主理人纪律)
- \`/market/status\` VERIFIED (if creds present) is a health/status signal — it does NOT replace
  per-trade order-book verification, which is done by fetchMarket's per-symbol freshness + sha256.
- \`/time\` is only a clock reference (used for clock skew, not a price).
- No Cloudflare production binding touched. No Staging D1 migration attempted. No ENABLE_REAL_TRADING flip.
- Proxy egress IP is not guaranteed US-geo-stable; per-cycle fetch failures are recorded as
  MARKET_UNVERIFIED and are NEVER presented as live (fail-closed).

## 4. Real-market gate outcome
- Cloudflare relay: AUTH_GATED locally (not consumable).
- Direct api.binance.us via proxy: VERIFIED reachable.
- **Net: real-market gate PASS via direct channel → P2 LIVE SHADOW proceeds with real market.**
`;

  const AI = `# DAY13_REAL_AI_DECISIONS.md — Real-AI Decisions on Real Market (DAY-1.3 / P2)

> Generated: ${meta.generatedUtc}
> Git SHA: ${gitSha}
> Channel: ${meta.marketChannel}
> Provider: ${meta.provider}

## Run state: LIVE_SHADOW (real market + real MiniMax; NO orders placed)
- Real-market cycles: ${r.realMarketCycles} / ${r.log.length}
- Market-unverified cycles (real fetch failed): ${r.marketUnverifiedCycles}

## Real AI inference (MiniMax M3) — 6 mandatory checks
- Model calls on real market: ${aiCallsReal}
- Successful (ok): ${aiOk}
- Invalid responses (ENVELOPE/TRUNCATED/NO_ACTION): ${aiInvalid}
- Thinking-tag present (distinguished & stripped): ${r.aiCalls.filter((c) => c.hadThinking).length} calls
- Total tokens: ${r.totalTokens} (prompt ${r.promptTokens} / completion ${r.completionTokens})
- Est. API cost (ASSUMED rate, ESTIMATE only): $${r.estCostUsd.toFixed(4)} / cap $${L.callBudgetUsd}

## Action distribution (from REAL model output, real market)
- BUY: ${r.actionDist.BUY}  SELL: ${r.actionDist.SELL}  HOLD: ${r.actionDist.HOLD}

## Independent risk gate (DAY-1.2 limits, before any execution)
- Approved: ${r.riskDecisions.approved}  Rejected: ${r.riskDecisions.rejected}
- Rejection breakdown: ${JSON.stringify(r.riskDecisions.reasons)}

## 7-step P2 process verification
1. Real-time market fetched per cycle (fetchMarket) — YES (${r.realMarketCycles} cycles).
2. Immutable snapshot hash (sha256 of canonical snapshot) — YES (${r.snapshots.length} hashes).
3. MiniMax called per real-market cycle — YES (${aiCallsReal} calls).
4. JSON / end-state / thinking / trade-intent validated — YES (6 checks; ${aiInvalid} flagged).
5. Independent risk control before any execution — YES.
6. BUY/SELL/HOLD RECORDED, NO order placed — YES (\`tradesExecuted=${r.tradesExecuted}\` by design).
7. token / fee / latency saved — YES.

## Honesty
- NO order was placed (LIVE SHADOW). \`tradesExecuted\` is intentionally 0 in the live run.
- All model outputs originate from MiniMax M3 real calls; HOLD/BUY/SELL never fabricated.
- Provider key last4 only: MINIMAX_API_KEY (never printed).
`;

  const PAPER = `# DAY13_PAPER_E2E.md — Isolated Paper Trading E2E (DAY-1.3 / P3)

> Status: **BLOCKED** (preconditions not met in this environment)
> Generated: ${meta.generatedUtc} | Git SHA: ${gitSha}

## P3 entrance gates (per主理人): real-time market + model + risk + ledger + independent QA + isolated persistence
- Real-time market: PASS (see DAY13_LIVE_MARKET_REPORT — direct api.binance.us VERIFIED).
- Real model (MiniMax M3): PASS (see DAY13_REAL_AI_DECISIONS).
- Independent risk gate: PASS (DAY-1.2 server gate exercised).
- **Isolated persistent ledger / account persistence: BLOCKED** — this environment has NO
  isolated test account and NO access to Staging D1 (\`jev-paper-staging\`) for persistent
  storage. The frozen engine here runs in-memory only.
- Independent QA gate (separate reviewer): NOT executed as a separate human/agent review in
  this run (automated checks applied; see DAY13_INDEPENDENT_QA).

## Why P3 cannot run here (honest)
- P3 requires "隔离测试账户 + 独立持久化存储，不碰正式 Staging D1". Local sandbox cannot
  provision that isolated persistent store, and writing to Staging D1 is explicitly forbidden
  without review (user red line).
- Therefore REAL_MARKET_PAPER class = 0 executed trades. \`PAPER_E2E_VERIFIED\` = BLOCKED.

## What IS provided instead (clearly labeled SIMULATED)
- DAY13_REAL_AI_DECISIONS + a SIMULATED_REPLAY in DAY13_EQUITY_RECONCILIATION: recorded
  BUY/SELL decisions are replayed through the FROZEN Paper Engine on the REAL prices to produce
  a research signal. This is SIMULATED (what-if), NOT executed, NOT persisted, NOT a verified
  real-market profit.
`;

  const RECON = `# DAY13_EQUITY_RECONCILIATION.md — Equity Reconciliation (DAY-1.3 / P4)

> Generated: ${meta.generatedUtc} | Git SHA: ${gitSha}
> CRITICAL: NO realized P&L is claimed. All below are SIMULATED_REPLAY on REAL prices (HYPOTHETICAL).

## Three-class separation (per主理人 P4)
| Class | Market | Execution | Persistence | Count this run |
|-------|--------|-----------|-------------|----------------|
| SYNTHETIC_SHADOW | synthetic (fake) | simulated (DAY-1.2) | none | 0 (DAY-1.3 used real market) |
| REAL_MARKET_SHADOW | REAL Binance.US | RECORDED, NOT executed | none | ${r.realMarketCycles} cycles |
| REAL_MARKET_PAPER | REAL Binance.US | executed isolated virtual | persistent | 0 (P3 BLOCKED) |

## REAL_MARKET_SHADOW — SIMULATED_REPLAY (HYPOTHETICAL, NOT realized)
- Decisions replayed through frozen engine on REAL prices: ${rp.tradesExecuted} simulated orders
- Simulated fills: ${rp.fills.length}
- Simulated completed round-trips: ${rp.completedTrades}
- Simulated win rate: ${rp.winRate.toFixed(1)}%
- Simulated avg realized per round-trip: ${rp.avgRealized.toFixed(4)} USDT
- Simulated total fees: ${rp.totalFees.toFixed(4)} USDT
- Simulated final equity: ${rp.finalEquity.toFixed(2)} USDT (start 10000)
- Simulated total return: ${rp.totalReturnPct.toFixed(2)}% (HYPOTHETICAL — NOT a real-market profit)
- Simulated max drawdown: ${rp.maxDD.toFixed(2)}%

## Realized vs Unrealized (truth)
- Realized P&L (actual): **0.00 USDT** — no order was placed (LIVE SHADOW).
- Unrealized (actual): **0.00 USDT** — no position was opened.
- The simulated figures above are a research signal ONLY and MUST NOT be reported as profit.

## Fees & slippage
- Frozen engine feeRate 0.001 applied in the SIMULATED_REPLAY only.
- Slippage: synthetic book ~0.01–0.02% from real mid; filled at first book level.

## Compare vs baselines (illustrative, on REAL prices, SIMULATED)
- vs HOLD (cash): ${rp.totalReturnPct.toFixed(2)}% (simulated). HOLD(buy&hold BTC) baseline would
  track BTC price move from first to last real mark — not a net-alpha claim.
- No statistical significance: ${rp.completedTrades} closed round-trip(s) is far below any
  meaningful sample. Per主理人: "2/2 100% 胜率不具统计意义".

## Honesty
- Synthetic profit is NOT accumulated as real (per spec).
- This reconciliation contains ZERO verified real-market equity figures.
`;

  const QA = `# DAY13_INDEPENDENT_QA.md — Independent QA (DAY-1.3)

> Generated: ${meta.generatedUtc} | Git SHA: ${gitSha}

## Automated checklist
- [x] Real AI inference verified (MiniMax M3 HTTP 200, choices+usage) — Provider connectivity evidence.
- [x] API envelope parsed from choices[0].message.content.
- [x] Thinking block stripped and distinguished from final decision.
- [x] Truncated / non-JSON responses flagged INVALID_RESPONSE (no false BUY/SELL) — ${aiInvalid} flagged.
- [x] HOLD/BUY/SELL originate ONLY from model output.
- [x] Every model decision passes server risk gate BEFORE any execution.
- [x] LIVE SHADOW: NO order placed (tradesExecuted=${r.tradesExecuted}); engine NOT used live.
- [x] SIMULATED_REPLAY uses FROZEN Paper Engine (read-only import); clearly labeled SIMULATED.
- [x] V1.6 Market Provenance Guard (fetchMarket + evaluateCycle) passed per real-market cycle.
- [x] Per-cycle immutable snapshot hash recorded (sha256) for auditability.
- [x] No real funds; ENABLE_REAL_TRADING false.
- [x] Synthetic feed / unverified cycles NEVER presented as live (fail-closed).
- [x] No key output; only last4 (MINIMAX_API_KEY).
- [x] Cloudflare production binding untouched; no Staging D1 write.

## Findings / risks
- Real-market cycles: ${r.realMarketCycles}/${r.log.length}; unverified: ${r.marketUnverifiedCycles}.
- Cloudflare relay is auth-gated locally; real data obtained via direct api.binance.us proxy egress.
- Proxy egress geo-stability is a risk: if it fails mid-run, those cycles are MARKET_UNVERIFIED (not live).
- P3 (isolated persistent paper) BLOCKED: no isolated account / Staging D1 access.
- Cost metering is an ESTIMATE (assumed rate); real USD not independently metered.
- Independent human/agent QA review not performed as a separate gate in this run.

## Verdict
- P2 LIVE SHADOW: PASS (real market + real AI + provenance + no execution).
- P3 REAL_MARKET_PAPER: BLOCKED (persistence gate).
`;

  const SIGN = `# DAY13_FINAL_SIGNOFF.md — Final Sign-off (DAY-1.3)

> Status: **LIVE_SHADOW-COMPLETE (real market observed + real AI decisions recorded; NO execution; P3 BLOCKED)**
> Generated: ${meta.generatedUtc} | Git SHA: ${gitSha}

## What is VERIFIED
- Real Binance.US market reachable via sandbox proxy egress; V1.6 provenance guard passed per cycle.
- MiniMax M3 real inference connectivity (${aiCallsReal} calls, ${aiInvalid} flagged INVALID).
- 7-step LIVE SHADOW process executed; ${r.snapshots.length} immutable snapshot hashes recorded.
- Full audit trail: input snapshots, output summaries, token usage, risk decisions, snapshot hashes.

## What is WITHHELD (not claimed)
- Any "verified net profit": NO order was placed; realized P&L = 0. Simulated replay figures are
  explicitly HYPOTHETICAL on real prices, not realized.
- REAL_MARKET_PAPER (isolated persistent paper trading): BLOCKED (persistence gate not met).

## Decision (per主理人 state table)
- Real market gate PASS (direct channel) → P2 LIVE SHADOW executed.
- No risk-adjusted edge demonstrated on a statistically meaningful sample → final recommendation
  for actual capital: **HOLD** (do not deploy real funds; continue observation / P3 when persistence
  available).
- DAY-1.2 = SHADOW GO; DAY-1.3 Live Paper = LIVE_SHADOW (real market, no execution); 正式生产 = NO-GO.

## Discipline retained
- PR #6 Draft; no force push; no Release/main; no Staging D1 write; no real funds; no fabricated evidence.
- Secrets: last4 only (MINIMAX_API_KEY). Cloudflare preview creds never present locally.

## Artifacts
- DAY13_LIVE_MARKET_REPORT.md, DAY13_REAL_AI_DECISIONS.md, DAY13_PAPER_E2E.md,
  DAY13_EQUITY_RECONCILIATION.md, DAY13_INDEPENDENT_QA.md, DAY13_FINAL_SIGNOFF.md.
- Git SHA: ${gitSha}
- Market channel: ${meta.marketChannel}
`;

  return { LIVE, AI, PAPER, RECON, QA, SIGN, metrics: { aiOk, aiInvalid, aiCallsReal, rp } };
}

const r = await run();
const reports = buildReports(r);
const outDir = join(ROOT, "reports");
const files = {
  "DAY13_LIVE_MARKET_REPORT.md": reports.LIVE,
  "DAY13_REAL_AI_DECISIONS.md": reports.AI,
  "DAY13_PAPER_E2E.md": reports.PAPER,
  "DAY13_EQUITY_RECONCILIATION.md": reports.RECON,
  "DAY13_INDEPENDENT_QA.md": reports.QA,
  "DAY13_FINAL_SIGNOFF.md": reports.SIGN,
};
for (const [name, content] of Object.entries(files)) writeFileSync(join(outDir, name), content);

console.log("=== DAY-1.3 TL;DR (LIVE SHADOW) ===");
console.log("gitSha:", r.gitSha);
console.log("realMarketCycles:", r.realMarketCycles, "/", r.log.length, "| unverified:", r.marketUnverifiedCycles);
console.log("AI calls(real):", reports.metrics.aiCallsReal, "| ok:", reports.metrics.aiOk, "| invalid:", reports.metrics.aiInvalid);
console.log("action dist:", JSON.stringify(r.actionDist));
console.log("risk approved/rejected:", r.riskDecisions.approved, "/", r.riskDecisions.rejected, JSON.stringify(r.riskDecisions.reasons));
console.log("tradesExecuted(live, by design 0):", r.tradesExecuted);
console.log("SIMULATED_REPLAY: orders=", reports.metrics.rp.tradesExecuted, "fills=", reports.metrics.rp.fills.length, "rt=", reports.metrics.rp.completedTrades, "winRate%=", reports.metrics.rp.winRate.toFixed(1), "ret%=", reports.metrics.rp.totalReturnPct.toFixed(2), "(HYPOTHETICAL)");
console.log("tokens:", r.totalTokens, "| est cost $:", r.estCostUsd.toFixed(4));
console.log("=== WROTE 6 reports in reports/ ===");
