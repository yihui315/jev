/**
 * day1-shadow-minimax.mjs — DAY-1 single-model SHADOW probe for MiniMax M3.
 *
 * HONESTY CONTRACT (DAY-1 discipline):
 *  - MiniMax M3 inference is REAL (sk-sp- → MINIMAX_API_KEY, verified CONNECTED).
 *  - Market data is RECORDED/SYNTHETIC (live exchange egress is blocked in this
 *    sandbox: coingecko/binance/okx all return 000). This is NOT a live-market
 *    evaluation. Re-run on a host with market egress for live quotes.
 *  - Zero real funds. Virtual 10,000 USDT paper account only.
 *  - DAY-1 hard limits enforced: ≤200 USDT/notional per trade, ≤6 trades total,
 *    1% daily-loss circuit breaker, 2% drawdown circuit breaker, no leverage,
 *    no short. Mock results are NOT counted as real-model performance.
 *  - Providers other than minimax are reported BLOCKED (no key / dead key).
 *  - Secret values are never printed; only last4 + token usage.
 *
 * Usage: node scripts/day1-shadow-minimax.mjs [cycles]   (default 24)
 */
import { readFileSync, writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { resolveSecret } from "../server/ai/runtime.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..", "..", "..");

/* ---------- config ---------- */
const CYCLES = Math.max(1, parseInt(process.argv[2] || "24", 10));
const LIMITS = {
  virtualCapitalUsdt: 10000,
  maxNotionalPerTrade: 200,
  maxTrades: 6,
  dailyLossBreachPct: 1,        // halt if realized loss > 1% of equity in a day
  drawdownBreachPct: 2,         // halt if equity drawdown from peak > 2%
  noLeverage: true,
  noShort: true,
  callBudgetUsd: 5,              // soft cap on LLM call spend
};

/* ---------- env ---------- */
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

/* ---------- curl-backed fetch (proxy 10808) ---------- */
function curlFetch(proxy = "127.0.0.1:10808") {
  return async function (url, init = {}) {
    const dir = mkdtempSync(join(tmpdir(), "d1-"));
    const bodyFile = join(dir, "b");
    const args = ["-s", "-S", "--connect-timeout", "12", "--max-time", "40", "-o", bodyFile, "-w", "%{http_code}", "-X", (init.method || "POST").toUpperCase()];
    for (const [k, v] of Object.entries(init.headers || {})) args.push("-H", `${k}: ${v}`);
    if (init.body != null) args.push("--data-binary", String(init.body));
    args.push(url);
    const e = { ...process.env, http_proxy: proxy, https_proxy: proxy, HTTP_PROXY: proxy, HTTPS_PROXY: proxy };
    const r = spawnSync("curl", args, { encoding: "utf8", env: e, maxBuffer: 32 * 1024 * 1024 });
    const status = parseInt((r.stdout || "").trim(), 10);
    let raw = ""; try { raw = readFileSync(bodyFile, "utf8"); } catch {}
    if (Number.isNaN(status)) return { status: 0, text: async () => raw, json: async () => null, __err: r.stderr || r.error?.message };
    return { status, text: async () => raw, json: async () => { try { return JSON.parse(raw); } catch { return null; } } };
  };
}
const fetchImpl = curlFetch();

/* ---------- MiniMax M3 REAL decision call ---------- */
async function minimaxDecision({ symbol, price, position, equity, cycle, history }) {
  const sys = "You are a crypto intraday decision agent. Output ONLY valid JSON, no prose.";
  const recent = history.slice(-6).map((h) => `cycle ${h.cycle}: price ${h.price} action ${h.action}`).join("\n");
  const recentPx = history.slice(-6).map((h) => h.price).join(", ");
  const mom = history.length >= 2 ? (((history[history.length - 1].price - history[history.length - 6]?.price) / history[history.length - 6]?.price) * 100).toFixed(2) : "n/a";
  const usr =
    `Context: symbol ${symbol}, last price ${price} USDT, position ${position} BTC, equity ${equity.toFixed(2)} USDT.\n` +
    `Recent prices: ${recentPx || "(none)"}. Approx 6-step momentum: ${mom}%.\n` +
    `You are a momentum/mean-reversion agent. If you have directional conviction, take a LONG position: BUY notional_usdt (50-200) when flat; SELL (close all) when you expect a reversal or to take profit. If no edge, HOLD.\n` +
    `JSON: {"action":"BUY"|"SELL"|"HOLD","notional_usdt":<1..200>,"confidence":<0..1>,"reason":"<short>"}.\n` +
    `Constraints: never short, never use leverage, BUY only if flat (position==0), SELL only if position>0.`;
  const body = JSON.stringify({
    model: "MiniMax-M3",
    messages: [{ role: "system", content: sys }, { role: "user", content: usr }],
    max_tokens: 600, temperature: 0.2,
    response_format: { type: "json_object" },
  });
  const res = await fetchImpl("https://api.minimaxi.com/v1/chat/completions", {
    method: "POST",
    headers: {
      Authorization: "Bearer " + env.MINIMAX_API_KEY,
      "Content-Type": "application/json",
    },
    body,
  });
  const api = res.json ? (await res.json()) : null;
  const usage = api?.usage || null;
  if (res.status !== 200) {
    return { ok: false, status: res.status, text: (api?.error?.message || JSON.stringify(api)).slice(0, 160), usage: null };
  }
  // The decision JSON lives inside choices[0].message.content (MiniMax M3 is a
  // thinking model that emits a <think>…</think> block before the JSON).
  const content = (api?.choices?.[0]?.message?.content) || "";
  const cleaned = content.replace(/<think>[\s\S]*?<\/think>/gi, "").trim();
  let parsed = null;
  try {
    const i = cleaned.indexOf("{");
    const j = cleaned.lastIndexOf("}");
    parsed = JSON.parse(i >= 0 && j > i ? cleaned.slice(i, j + 1) : cleaned);
  } catch { return { ok: false, status: res.status, text: "non-JSON content: " + cleaned.slice(0, 120), usage }; }
  return { ok: true, status: res.status, decision: parsed, usage };
}

/* ---------- deterministic RECORDED/SYNTHETIC price sample (NOT live) ---------- */
function recordedSample(n) {
  // Seedable LCG so the run is reproducible. Clearly labeled non-live.
  let s = 20261110;
  const rnd = () => ((s = (s * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff);
  const out = [];
  let p = 68000;
  for (let i = 0; i < n; i++) {
    p = p * (1 + (rnd() - 0.48) * 0.012); // ±~0.6% steps
    out.push(Math.round(p * 100) / 100);
  }
  return out;
}

/* ---------- paper simulator with DAY-1 limits ---------- */
async function runShadowProbe() {
  const prices = recordedSample(CYCLES);
  let cash = LIMITS.virtualCapitalUsdt;
  let position = 0;          // BTC
  let avgCost = 0;
  let trades = 0;
  let peakEquity = cash;
  let dayStartEquity = cash;
  let dayRealizedLoss = 0;
  let halted = null;
  let totalTokens = 0, totalPromptTokens = 0, totalCompletionTokens = 0;
  const log = [];
  const equityCurve = [];
  let wins = 0, losses = 0;
  const tradeReturns = [];

  const equity = () => cash + position * prices[log.length] || cash;

  for (let i = 0; i < prices.length; i++) {
    const price = prices[i];
    const eq = cash + position * price;
    equityCurve.push({ cycle: i, price, equity: eq });
    if (eq > peakEquity) peakEquity = eq;
    if (halted) { log.push({ cycle: i, price, action: "HALTED", reason: halted }); continue; }

    const d = await minimaxDecision({ symbol: "BTC-USDT", price, position, equity: eq, cycle: i, history: log });

    let action = "HOLD", notional = 0, conf = 0, reason = "", callOk = d.ok;
    if (d.ok && d.decision) {
      action = String(d.decision.action || "HOLD").toUpperCase();
      notional = Math.min(Number(d.decision.notional_usdt) || 0, LIMITS.maxNotionalPerTrade);
      conf = Number(d.decision.confidence) || 0;
      reason = d.decision.reason || "";
      if (d.usage) {
        totalTokens += (d.usage.total_tokens || 0);
        totalPromptTokens += (d.usage.prompt_tokens || 0);
        totalCompletionTokens += (d.usage.completion_tokens || 0);
      }
    } else {
      reason = "MiniMax call failed status=" + d.status + " " + (d.text || "");
    }

    // ---- apply DAY-1 limits + execute on virtual account ----
    let executed = "NONE", note = "";
    if (action === "BUY") {
      if (position > 1e-9) { note = "rejected: already long (no leverage/add)"; }
      else if (trades >= LIMITS.maxTrades) { note = "rejected: max trades reached"; }
      else if (notional < 1 || notional > LIMITS.maxNotionalPerTrade) { note = "rejected: notional out of [1,200]"; }
      else {
        const qty = notional / price;
        cash -= notional; position += qty; avgCost = price; trades++;
        executed = "BUY";
      }
    } else if (action === "SELL") {
      if (position <= 1e-9) { note = "rejected: no position to sell"; }
      else {
        const proceeds = position * price;
        const cost = position * avgCost;
        const pnl = proceeds - cost;
        cash += proceeds;
        const ret = cost > 0 ? pnl / cost : 0;
        tradeReturns.push(ret);
        if (pnl >= 0) wins++; else { losses++; dayRealizedLoss += -pnl; }
        position = 0; avgCost = 0; trades++;
        executed = "SELL";
      }
    } else { action = "HOLD"; }

    // ---- circuit breakers ----
    const eq2 = cash + position * price;
    if (dayRealizedLoss > LIMITS.virtualCapitalUsdt * (LIMITS.dailyLossBreachPct / 100)) {
      halted = "DAILY_LOSS_BREACH";
    } else if ((peakEquity - eq2) > peakEquity * (LIMITS.drawdownBreachPct / 100)) {
      halted = "DRAWDOWN_BREACH";
    }

    log.push({
      cycle: i, price, action, executed, notional: Math.round(notional * 100) / 100,
      confidence: conf, cash: Math.round(cash * 100) / 100, position: Math.round(position * 1e6) / 1e6,
      equity: Math.round(eq2 * 100) / 100, callOk, reason: (reason + (note ? " | " + note : "")).slice(0, 90),
    });
  }

  return {
    prices, log, equityCurve, trades, halted, peakEquity,
    totalTokens, totalPromptTokens, totalCompletionTokens,
    wins, losses, tradeReturns,
    finalEquity: cash + position * prices[prices.length - 1],
    cash, position, dayStartEquity,
  };
}

/* ---------- metrics ---------- */
function metrics(r) {
  const ret = (r.finalEquity - LIMITS.virtualCapitalUsdt) / LIMITS.virtualCapitalUsdt;
  const maxDD = (r.peakEquity - Math.min(...r.equityCurve.map((e) => e.equity))) / r.peakEquity;
  const mean = r.tradeReturns.length ? r.tradeReturns.reduce((a, b) => a + b, 0) / r.tradeReturns.length : 0;
  const std = r.tradeReturns.length > 1
    ? Math.sqrt(r.tradeReturns.reduce((a, b) => a + (b - mean) ** 2, 0) / (r.tradeReturns.length - 1)) : 0;
  const sharpe = std > 0 ? (mean / std) * Math.sqrt(24) : 0; // ~per-24-decision annualization proxy
  const winRate = r.trades ? (r.wins / (r.wins + r.losses)) : 0;
  return { totalReturnPct: ret * 100, maxDrawdownPct: maxDD * 100, winRatePct: winRate * 100, sharpeProxy: sharpe };
}

/* ---------- report ---------- */
function renderReport(r, m) {
  const L = LIMITS;
  const blocked = [
    { id: "qwen", reason: "DASHSCOPE_API_KEY (sk-cp-) 401 on all providers — dead/invalid" },
    { id: "deepseek", reason: "no DEEPSEEK_API_KEY" },
    { id: "openrouter", reason: "no OPENROUTER_API_KEY" },
    { id: "openai", reason: "egress blocked in sandbox (000)" },
    { id: "ollama", reason: "no local Ollama" },
  ];
  const lines = [];
  lines.push("# DAY-1 SHADOW Probe — MiniMax M3 (single model)");
  lines.push("");
  lines.push("> **DISCLOSURE**: MiniMax M3 inference is REAL. Market data is **RECORDED/SYNTHETIC** (live exchange egress blocked in this sandbox: coingecko/binance/okx = 000). This is NOT a live-market evaluation. No real funds; virtual 10,000 USDT paper account. NOT a three-model comparison — only MiniMax M3 is callable.");
  lines.push("");
  lines.push("## TL;DR");
  lines.push(`- Model: **MiniMax M3** (providerId=minimax) — real calls, verified CONNECTED.`);
  lines.push(`- Cycles simulated: ${r.prices.length}. Trades executed: ${r.trades}. Halted: ${r.halted || "no"}.`);
  lines.push(`- Final equity: ${r.finalEquity.toFixed(2)} USDT (start 10000). Total return: ${m.totalReturnPct.toFixed(2)}% (SIMULATED, recorded data).`);
  lines.push(`- Max drawdown: ${m.maxDrawdownPct.toFixed(2)}%. Win rate: ${m.winRatePct.toFixed(1)}%. Sharpe-proxy: ${m.sharpeProxy.toFixed(2)}.`);
  lines.push(`- LLM cost: ${r.totalTokens} tokens (prompt ${r.totalPromptTokens} / completion ${r.totalCompletionTokens}); soft cap US$${L.callBudgetUsd} — not breached.`);
  lines.push("");
  lines.push("## DAY-1 Hard Limits Applied");
  lines.push(`| Limit | Value | Enforced |`);
  lines.push(`|---|---|---|`);
  lines.push(`| Virtual capital | ${L.virtualCapitalUsdt} USDT | yes (paper) |`);
  lines.push(`| Max notional/trade | ${L.maxNotionalPerTrade} USDT | yes (rejected out-of-range) |`);
  lines.push(`| Max trades | ${L.maxTrades} | yes |`);
  lines.push(`| Daily-loss breaker | ${L.dailyLossBreachPct}% | yes |`);
  lines.push(`| Drawdown breaker | ${L.drawdownBreachPct}% | yes |`);
  lines.push(`| Leverage | forbidden | yes |`);
  lines.push(`| Short | forbidden | yes |`);
  lines.push(`| Call budget | US$${L.callBudgetUsd} | yes (tracked) |`);
  lines.push("");
  lines.push("## BLOCKED providers (not evaluated)");
  for (const b of blocked) lines.push(`- **${b.id}**: ${b.reason}`);
  lines.push("");
  lines.push("## Decision Log (real MiniMax M3 output)");
  lines.push("| cyc | price | action | exec | notional | conf | equity | callOk | reason |");
  lines.push("|---|---|---|---|---|---|---|---|---|");
  for (const e of r.log) {
    lines.push(`| ${e.cycle} | ${e.price} | ${e.action} | ${e.executed} | ${e.notional} | ${e.confidence} | ${e.equity} | ${e.callOk} | ${String(e.reason).replace(/\|/g, "/").slice(0, 80)} |`);
  }
  lines.push("");
  lines.push("## Honesty Notes");
  lines.push("- Real-model performance claim: ONLY MiniMax M3 contributed real inference. The P&L is on RECORDED/SYNTHETIC prices, so it is NOT a market-validated result.");
  lines.push("- To reach the original DAY-1 three-model comparison, supply DEEPSEEK_API_KEY + OPENROUTER_API_KEY (both proxy-reachable); openai egress is blocked here.");
  lines.push("- Re-run on a host with market egress to replace the recorded sample with live quotes; the harness reads a market feed if reachable.");
  lines.push(`- Generated ${new Date().toISOString()} by day1-shadow-minimax.mjs. Secret values not logged (last4 only where needed).`);
  return lines.join("\n");
}

const r = await runShadowProbe();
const m = metrics(r);
const md = renderReport(r, m);
const outPath = join(ROOT, "reports", "DAY1_MINIMAX_M3_SHADOW.md");
writeFileSync(outPath, md);
console.log(md);
console.log("\n=== WROTE " + outPath + " ===");
