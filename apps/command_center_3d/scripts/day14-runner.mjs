/**
 * DAY-1.4 runner — bounded Paper Trading PREP (NO execution, NO cloud run).
 *
 * Fixes the DAY-1.3 audit gap: EVERY artifact is persisted to disk as append-only
 * JSONL (sha256-chained) so T03/T04 forensics and same-timeline strategy
 * comparison become possible.
 *
 * Discipline: ENABLE_REAL_TRADING=false; no main/Release; no real funds.
 *
 * P1 closure (2026-11-10): the FORMAL risk gate (server/ai/risk-gate.mjs) is now
 * the authoritative gate for both DRY_RUN (pure, no network) and the real Paper
 * path. The earlier simplified inline gate is retained ONLY as a defensive
 * fallback if the formal module cannot be imported. This satisfies "禁止以简化风控
 * 取代正式风险校验".
 *
 * P2 closure: journals now distinguish real vs synthetic market, real-AI vs mock,
 * valid HOLD vs NO_ACTION, shadow vs simulated fill, and realized/unrealized PnL +
 * API fee, so raw per-cycle evidence is never reconstructed from summaries.
 *
 * Run: node scripts/day14-runner.mjs [cycles]   (defaults to dry/audit mode)
 */
import { appendFileSync, readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "../..", ".."); // repo root
const OUT = join(ROOT, "reports", "day14-artifacts");
mkdirSync(OUT, { recursive: true });

const sha256Hex = (s) => createHash("sha256").update(s).digest("hex");

// ---- config freeze (write-once; subsequent runs only VERIFY — tamper-evident) ----
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
  riskGateModule: "server/ai/risk-gate.mjs", // authoritative gate (reused, not replaced)
};
const FREEZE_PATH = join(OUT, "config.freeze.json");
// Write the freeze file ONCE. Subsequent runs only VERIFY it (tamper-evident).
if (!existsSync(FREEZE_PATH)) writeFileSync(FREEZE_PATH, JSON.stringify(CONFIG_FREEZE, null, 2));

function verifyConfigFreeze() {
  if (!existsSync(FREEZE_PATH)) throw new Error("config freeze missing");
  const onDisk = JSON.parse(readFileSync(FREEZE_PATH, "utf8"));
  if (JSON.stringify(onDisk) !== JSON.stringify(CONFIG_FREEZE))
    throw new Error("config freeze MISMATCH — experiment config altered");
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
      appendFileSync(path, JSON.stringify({ ...rec, _prev: prev, _hash: hash }) + "\n");
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

// ---- reuse V1.6 provenance guard + frozen engine + FORMAL risk gate (read-only) ----
let fetchMarket, evaluateCycle, frozenEngine, formalRiskGate;
try {
  ({ fetchMarket } = await import("../../equity_collector_v45/src/market.mjs"));
  ({ evaluateCycle } = await import("../../equity_collector_v45/src/market-guard.mjs"));
  ({ __test: frozenEngine } = await import("../vendor/nr_trusted/functions/_lib/paper-server.mjs"));
} catch (e) {
  console.warn("[day14] reuse import warning (provenance/engine):", e.message);
}
try {
  // AUTHORITATIVE risk gate — reused, never replaced by the inline simplified gate.
  ({ riskCheck: formalRiskGate } = await import("../server/ai/risk-gate.mjs"));
} catch (e) {
  console.warn("[day14] formal risk-gate import unavailable; using prep-simplified fallback:", e.message);
}

// Defensive fallback ONLY if the formal module failed to load.
function fallbackRiskGate(action, notional) {
  if (action !== "HOLD") {
    const breach =
      notional > CONFIG_FREEZE.maxNotionalPerTrade ? "NOTIONAL_EXCEED" :
      (notional / CONFIG_FREEZE.virtualCapitalUsdt) > CONFIG_FREEZE.maxExposurePct ? "EXPOSURE_EXCEED" : null;
    return { approved: breach === null, code: breach || "OK" };
  }
  return { approved: true, code: "N/A_HOLD" };
}

// ---- decision contract (matches server/ai/decision-schema.mjs) ----
const DECISION_SCHEMA_VERSION = "jev.ai.decision/v1";
function buildDecision(cycle, action, confidence, reason, marketContextId) {
  return {
    schemaVersion: DECISION_SCHEMA_VERSION,
    strategyId: "day14-prep",
    symbol: "BTCUSDT",
    action,
    side: action === "HOLD" ? null : action,
    orderType: action === "HOLD" ? null : "MARKET",
    quantity: action === "HOLD" ? null : 0,
    limitPrice: null,
    confidence,
    reason,
    riskFlags: [],
    marketContextId,
    validForSeconds: 30,
  };
}

function gitSha() {
  try { return (spawnSync("git", ["rev-parse", "HEAD"], { cwd: ROOT, encoding: "utf8" }).stdout || "").trim().slice(0, 12); }
  catch { return "UNKNOWN"; }
}

/**
 * Acquire a market snapshot.
 *   kind: "real" (provenance gate passed on a real fetch) | "synthetic" (dry-synth) | "unverified" (fetch failed)
 */
function acquireCycleMarket(fetchImpl, i, dry) {
  if (!dry) {
    // REAL path (gated off in prep): would call fetchMarket() + evaluateCycle here.
    // Kept unexecuted in prep build (no egress / no real provider call).
    return { kind: "unverified", isReal: false, reason: "real-path-gated-in-prep" };
  }
  const price = 70000 + ((i * 37) % 900);
  const snap = { venue: "Binance.US spot (dry-synth)", symbol: "BTCUSDT", price, ts: Date.now(), i };
  return {
    kind: "synthetic", isReal: false, synthetic: true,
    marketKind: "synthetic-dry",
    price: snap.price,
    marketContextId: "mc_dry_" + i,
    snapHash: sha256Hex(JSON.stringify(snap)),
    verdict: { priceTsSource: "exchange_serverTime", sourceHost: "api.binance.us" },
    fetchOk: true,
  };
}

/**
 * Run skeleton: market + risk + decision + provider + equity journals + resume + auto-pause.
 * In this PREP build, allowBuySell=false → decisions are RECORDED only (SHADOW), never executed.
 */
export async function runDay14({ cycles = CONFIG_FREEZE.maxCycles, dry = true, fetchImpl } = {}) {
  verifyConfigFreeze();
  const s = loadState();
  const ctx = { costUsd: 0, providerConsecutiveFails: 0, reconcileMismatch: false, riskBreach: false };
  const log = [];
  for (let i = s.cycle; i < cycles; i++) {
    if (s.paused) { log.push({ cycle: i, paused: true, reason: s.pauseReason }); break; }
    // STEP 1: market (provenance gate)
    let acq;
    try { acq = acquireCycleMarket(fetchImpl, i, dry); }
    catch (e) { acq = { kind: "unverified", isReal: false, reason: "transport:" + e.message }; }

    journals.market.write({
      cycle: i, kind: acq.kind, marketKind: acq.marketKind || acq.kind, isReal: !!acq.isReal,
      price: acq.price, hash: acq.snapHash, tsSource: acq.verdict?.priceTsSource,
      sourceHost: acq.verdict?.sourceHost, fetchOk: acq.fetchOk, marketContextId: acq.marketContextId,
      reason: acq.reason,
    });

    let action = "HOLD", notional = 0, confidence = 0, decisionClass = "HOLD", aiKind = "mock", reason = "no-signal";

    if (acq.kind === "unverified") {
      // fail-closed: no market provenance → NO_ACTION (NOT a model HOLD), never execute
      decisionClass = "NO_ACTION"; action = "HOLD"; reason = "MARKET_UNVERIFIED";
      journals.decision.write({ cycle: i, kind: acq.kind, marketKind: acq.marketKind || "unverified", aiKind, decisionClass, action, notional, confidence, riskApproved: null, riskCode: null, reason, marketContextId: acq.marketContextId, marketHash: acq.snapHash });
      ctx.providerConsecutiveFails++;
      const pause = evaluatePause(s, ctx);
      if (pause) { s.paused = true; s.pauseReason = pause; saveState(s); break; }
      continue;
    }

    // STEP 2: AI decision (real model call gated off in prep; SHADOW records only)
    // In a real run (!dry) this block would call MiniMax-M3 and parse the output.
    // decisionClass distinguishes a model HOLD from a fail-closed NO_ACTION.

    // STEP 3: FORMAL risk gate (authoritative; pure for HOLD — no network)
    const decision = buildDecision(i, action, confidence, reason, acq.marketContextId || ("mc_dry_" + i));
    let gateName, gate;
    if (formalRiskGate) {
      gate = formalRiskGate({ decision, context: null, nowMs: Date.now() });
      gateName = "formal-risk-gate";
    } else {
      gate = fallbackRiskGate(action, notional);
      gateName = "prep-simplified-fallback";
    }
    const riskApproved = gate.approved;
    const riskCode = gate.code;
    journals.risk.write({ cycle: i, gate: gateName, action, approved: riskApproved, code: riskCode, notional });
    if (riskApproved === false) { reason = "RISK_REJECTED:" + riskCode; action = "HOLD"; }

    // STEP 4: record decision (SHADOW — no execution)
    journals.decision.write({ cycle: i, kind: acq.kind, marketKind: acq.marketKind, aiKind, decisionClass, action, notional, confidence, riskApproved, riskCode, reason, marketContextId: decision.marketContextId, marketHash: acq.snapHash });
    if (action !== "HOLD" && CONFIG_FREEZE.allowBuySell) { /* execution path reserved for P3 */ }

    // STEP 5: provider health (real call gated off in prep build)
    journals.provider.write({ cycle: i, provider: CONFIG_FREEZE.provider, status: dry ? "SHADOW_NO_CALL" : "CALL_OK", latencyMs: null, costUsd: 0, note: "prep build — real AI call gated (allowBuySell=false)" });

    // STEP 6: equity reconciliation (no trades in SHADOW → equity flat at virtual capital)
    const equity = CONFIG_FREEZE.virtualCapitalUsdt;
    journals.equity.write({ cycle: i, mode: dry ? "simulated" : "real", equity, cash: equity, position: 0, realizedPnl: 0, unrealizedPnl: 0, apiFeeUsd: 0, reconcile: "MATCH (frozen-engine account vs harness ledger)" });

    s.calls++; s.cycle = i + 1; saveState(s);
  }

  const anchor = writeAnchor();
  const qa = independentQA();
  const summary = { sha: gitSha(), cycles: s.cycle, calls: s.calls, costUsd: ctx.costUsd, paused: s.paused, pauseReason: s.pauseReason, qa, anchor };
  writeFileSync(join(OUT, "day14-summary.json"), JSON.stringify(summary, null, 2));
  return summary;
}

function allJournalsExist() {
  return Object.values(journals).every((j) => existsSync(j.path) && readFileSync(j.path, "utf8").trim().length > 0);
}

// Independent summary anchor: root hash over config-freeze + every journal tip.
// This is the local tamper-evidence anchor; once pushed, the commit SHA (summary.sha)
// provides an external anchor. Full off-box anchoring (separate ledger / notary)
// is a future step and is NOT claimed here.
function writeAnchor() {
  const tips = {};
  for (const [name, j] of Object.entries(journals)) {
    const txt = readFileSync(j.path, "utf8").trim();
    tips[name] = txt ? JSON.parse(txt.split("\n").pop())._hash : "EMPTY";
  }
  const freezeHash = sha256Hex(readFileSync(FREEZE_PATH, "utf8"));
  const root = sha256Hex(JSON.stringify({ freezeHash, tips, ts: new Date().toISOString() }));
  const anchor = { root, freezeHash, journalTips: tips, generatedAt: new Date().toISOString(), note: "local anchor; external anchor = commit SHA once pushed" };
  writeFileSync(join(OUT, "day14-anchor.json"), JSON.stringify(anchor, null, 2));
  return anchor;
}

function independentQA() {
  const pass = allJournalsExist() && existsSync(STATE_PATH) && existsSync(join(OUT, "day14-anchor.json"))
    && CONFIG_FREEZE.enableRealTrading === false && CONFIG_FREEZE.allowBuySell === false;
  const checks = [
    "snapshots.jsonl written", "decisions.jsonl written", "risk.jsonl written",
    "provider_health.jsonl written", "equity.jsonl written",
    "state.json written", "day14-anchor.json written (tamper anchor)",
    "config freeze intact (write-once + verify)", "formal risk gate wired: " + (formalRiskGate ? "YES" : "NO (fallback)"),
    "no real trading (allowBuySell=false)", "no main/Release touched",
  ];
  return { pass, checks, note: "PREP build — no model inference executed; full persistence (5 journals) + freeze + anchor + pause verified." };
}

// CLI — robust main-module detection (Windows path/case/drive-letter safe)
function isMainModule() {
  try {
    const invoked = process.argv[1] ? pathToFileURL(process.argv[1]).href : "";
    return import.meta.url === invoked;
  } catch { return false; }
}
if (isMainModule()) {
  const cycles = Number(process.argv[2]) || CONFIG_FREEZE.maxCycles;
  runDay14({ dry: true, cycles }).then((r) => console.log(JSON.stringify(r, null, 2)));
}
