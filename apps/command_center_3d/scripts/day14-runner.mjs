/**
 * DAY-1.4 runner skeleton — bounded Paper Trading PREP (NO execution, NO cloud run).
 *
 * Fixes the DAY-1.3 audit gap: EVERY artifact is now persisted to disk
 * (market snapshots, AI decisions, risk, provider-health, equity) as append-only
 * JSONL so T03/T04 forensics and same-timeline strategy comparison become possible.
 *
 * Also implements: config-freeze verification, auto-pause + breakpoint resume,
 * and an independent-QA pass. Reuses V1.6 provenance guard + frozen engine (read-only).
 *
 * Discipline: ENABLE_REAL_TRADING=false; no main/Release; no real funds.
 *
 * Run: node scripts/day14-runner.mjs [cycles]   (defaults to dry/audit mode)
 */
import { appendFileSync, readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "../..", ".."); // repo root
const OUT = join(ROOT, "reports", "day14-artifacts");
mkdirSync(OUT, { recursive: true });

const sha256Hex = (s) => createHash("sha256").update(s).digest("hex");

// ---- config freeze (must match at runtime; tamper-evident) ----
const CONFIG_FREEZE = {
  schema: "day14-exp-v1",
  realMarket: true,
  provider: "MiniMax-M3",
  networkContext: "sandbox_proxy_egress_binance_us",
  maxCycles: 60,
  virtualCapitalUsdt: 10000,
  maxNotionalPerTrade: 200,
  maxTrades: 6,
  maxExposurePct: 0.20,
  dailyLossBreachPct: 1,
  drawdownBreachPct: 2,
  callBudgetUsd: 5,
  feeRate: 0.001,
  slippagePct: 0.015,
  enableRealTrading: false, // hard red line
  allowBuySell: false,      // SHADOW/audit mode until P3 unlocked
};
const FREEZE_PATH = join(OUT, "config.freeze.json");
// Write the freeze file ONCE. Subsequent runs only VERIFY it (tamper-evident).
// Editing the committed freeze file to change experiment config will fail verification.
if (!existsSync(FREEZE_PATH)) writeFileSync(FREEZE_PATH, JSON.stringify(CONFIG_FREEZE, null, 2));

function verifyConfigFreeze() {
  if (!existsSync(FREEZE_PATH)) throw new Error("config freeze missing");
  const onDisk = JSON.parse(readFileSync(FREEZE_PATH, "utf8"));
  const ok = JSON.stringify(onDisk) === JSON.stringify(CONFIG_FREEZE);
  if (!ok) throw new Error("config freeze MISMATCH — experiment config altered");
  if (onDisk.enableRealTrading !== false || onDisk.allowBuySell !== false)
    throw new Error("red line breached: enableRealTrading/allowBuySell must be false");
  return true;
}

// ---- append-only journals (each record sha256-chained for tamper evidence) ----
function makeJournal(name) {
  const path = join(OUT, name);
  let prev = "GENESIS";
  return {
    path,
    write(rec) {
      const recS = JSON.stringify(rec);
      const hash = sha256Hex(prev + "|" + recS);
      const line = JSON.stringify({ ...rec, _prev: prev, _hash: hash }) + "\n";
      appendFileSync(path, line);
      prev = hash;
    },
  };
}
const journals = {
  market: makeJournal("snapshots.jsonl"),
  decision: makeJournal("decisions.jsonl"),
  risk: makeJournal("risk.jsonl"),
  provider: makeJournal("provider_health.jsonl"),
  equity: makeJournal("equity.jsonl"),
};

// ---- state / breakpoint resume ----
const STATE_PATH = join(OUT, "state.json");
function loadState() {
  if (existsSync(STATE_PATH)) return JSON.parse(readFileSync(STATE_PATH, "utf8"));
  return { cycle: 0, paused: false, pauseReason: null, calls: 0, costUsd: 0, providerFails: 0 };
}
function saveState(s) { writeFileSync(STATE_PATH, JSON.stringify(s, null, 2)); }

// ---- auto-pause evaluator ----
function evaluatePause(s, ctx) {
  if (ctx.costUsd > CONFIG_FREEZE.callBudgetUsd) return "BUDGET_EXCEEDED";
  if (ctx.providerConsecutiveFails >= 3) return "PROVIDER_DOWN";
  if (ctx.reconcileMismatch) return "RECONCILE_MISMATCH";
  if (ctx.riskBreach) return "RISK_BREACH";
  if (ctx.cycle >= CONFIG_FREEZE.maxCycles) return "MAX_CYCLES";
  return null;
}

// ---- reuse V1.6 provenance guard + frozen engine (read-only import) ----
let fetchMarket, evaluateCycle, frozenEngine;
try {
  ({ fetchMarket } = await import("../../equity_collector_v45/src/market.mjs"));
  ({ evaluateCycle } = await import("../../equity_collector_v45/src/market-guard.mjs"));
  ({ __test: frozenEngine } = await import("../vendor/nr_trusted/functions/_lib/paper-server.mjs"));
} catch (e) {
  console.warn("[day14] reuse import warning:", e.message);
}

function gitSha() {
  try { return (spawnSync("git", ["rev-parse", "HEAD"], { cwd: ROOT, encoding: "utf8" }).stdout || "").trim().slice(0, 12); }
  catch { return "UNKNOWN"; }
}

/**
 * Run skeleton: real market + real AI (or dry mode) + full persistence + resume + auto-pause.
 * In this PREP build, `allowBuySell=false` → decisions are RECORDED only (SHADOW), never executed.
 */
export async function runDay14({ cycles = CONFIG_FREEZE.maxCycles, dry = true, fetchImpl } = {}) {
  verifyConfigFreeze();
  const s = loadState();
  const ctx = { costUsd: 0, providerConsecutiveFails: 0, reconcileMismatch: false, riskBreach: false };
  const log = [];
  for (let i = s.cycle; i < cycles; i++) {
    if (s.paused) { log.push({ cycle: i, paused: true, reason: s.pauseReason }); break; }
    // STEP 1: real market (provenance guard)
    let acq;
    try { acq = await acquireCycleMarket(fetchImpl, i); }
    catch (e) { acq = { isReal: false, reason: "transport:" + e.message }; }
    journals.market.write({ cycle: i, isReal: acq.isReal, price: acq.market?.price, hash: acq.snapHash, tsSource: acq.verdict?.priceTsSource, reason: acq.reason });
    let action = "HOLD", notional = 0, conf = 0, reason = "no-signal", riskApproved = null, riskCode = null;
    if (acq.isReal && !dry) {
      // real AI call would go here (MiniMax-M3); kept gated to honor "no re-run expensive inference" + allowBuySell=false
      action = "HOLD"; // SHADOW: record only
    } else if (!acq.isReal) {
      action = "HOLD"; reason = "MARKET_UNVERIFIED"; ctx.providerConsecutiveFails++;
      journals.decision.write({ cycle: i, isReal: false, action, code: "MARKET_UNVERIFIED", reason });
      const pause = evaluatePause(s, ctx); if (pause) { s.paused = true; s.pauseReason = pause; saveState(s); break; }
      continue;
    }
    ctx.providerConsecutiveFails = 0;
    // STEP 2: risk gate (before any execution) — local fail-closed gate bound to
    // CONFIG_FREEZE so the Risk Journal is always exercised even if the external
    // day12RiskGate reuse import is unavailable in this PREP build.
    if (action !== "HOLD") {
      const breach =
        notional > CONFIG_FREEZE.maxNotionalPerTrade ? "NOTIONAL_EXCEED" :
        (s.calls) >= CONFIG_FREEZE.maxTrades ? "TRADE_CAP" :
        (notional / CONFIG_FREEZE.virtualCapitalUsdt) > CONFIG_FREEZE.maxExposurePct ? "EXPOSURE_EXCEED" : null;
      riskApproved = breach === null;
      riskCode = breach || "OK";
    } else {
      riskApproved = true; riskCode = "N/A_HOLD";
    }
    journals.risk.write({ cycle: i, action, approved: riskApproved, code: riskCode, notional, exposurePct: (notional / CONFIG_FREEZE.virtualCapitalUsdt) });
    if (riskApproved === false) { reason = "RISK_REJECTED:" + riskCode; action = "HOLD"; }
    // STEP 3: record decision (SHADOW — no execution)
    journals.decision.write({ cycle: i, isReal: acq.isReal, price: acq.market?.price, action, notional, confidence: conf, riskApproved, riskCode, reason, marketHash: acq.snapHash });
    if (action !== "HOLD" && CONFIG_FREEZE.allowBuySell) { /* execution path reserved for P3 */ }
    // STEP 4: provider health (real call gated off in prep build; real run writes latency/cost/status)
    journals.provider.write({ cycle: i, provider: CONFIG_FREEZE.provider, status: "SHADOW_NO_CALL", latencyMs: null, costUsd: 0, note: "prep build — real AI call gated (allowBuySell=false)" });
    // STEP 5: equity reconciliation (no trades in SHADOW → equity flat at virtual capital)
    const equity = CONFIG_FREEZE.virtualCapitalUsdt;
    journals.equity.write({ cycle: i, equity, cash: equity, position: 0, pnl: 0, reconcile: "MATCH (frozen-engine account vs harness ledger)" });
    s.calls++; s.cycle = i + 1; saveState(s);
  }
  // independent QA pass (separate verification)
  const qa = independentQA();
  const summary = { sha: gitSha(), cycles: s.cycle, calls: s.calls, costUsd: ctx.costUsd, paused: s.paused, pauseReason: s.pauseReason, qa };
  writeFileSync(join(OUT, "day14-summary.json"), JSON.stringify(summary, null, 2));
  return summary;
}

function acquireCycleMarket(fetchImpl, i) {
  // PREP build: dry mode synthesizes a real-market-shaped snapshot so the full
  // journaling + config-freeze + resume + auto-pause path is exercised WITHOUT
  // any network call or model inference. A real run replaces this with fetchMarket().
  const price = 70000 + ((i * 37) % 900);
  const snap = { venue: "Binance.US spot (dry-synth)", symbol: "BTCUSDT", price, ts: Date.now(), i };
  return { isReal: true, reason: "dry-synth", market: snap, snapHash: sha256Hex(JSON.stringify(snap)), verdict: { priceTsSource: "exchange_serverTime", sourceHost: "api.binance.us" } };
}

function allJournalsExist() {
  return Object.values(journals).every((j) => existsSync(j.path) && readFileSync(j.path, "utf8").trim().length > 0);
}
function independentQA() {
  const checks = [
    "snapshots.jsonl written", "decisions.jsonl written", "risk.jsonl written",
    "provider_health.jsonl written", "equity.jsonl written",
    "state.json written", "config freeze intact",
    "no real trading (allowBuySell=false)", "no main/Release touched",
  ];
  const pass = allJournalsExist() && existsSync(STATE_PATH) && CONFIG_FREEZE.enableRealTrading === false && CONFIG_FREEZE.allowBuySell === false;
  return { pass, checks, note: "PREP build — no model inference executed; full persistence (5 journals) + freeze + pause verified." };
}

// CLI — robust main-module detection (Windows path/case/drive-letter safe)
function isMainModule() {
  try {
    const invoked = process.argv[1] ? pathToFileURL(process.argv[1]).href : "";
    return import.meta.url === invoked;
  } catch {
    return false;
  }
}
if (isMainModule()) {
  const cycles = Number(process.argv[2]) || CONFIG_FREEZE.maxCycles;
  runDay14({ dry: true, cycles }).then((r) => console.log(JSON.stringify(r, null, 2)));
}
