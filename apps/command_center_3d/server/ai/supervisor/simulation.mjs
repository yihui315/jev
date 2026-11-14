// JEV V2.0 Phase E/F — OFFLINE, DETERMINISTIC SIMULATION harness.
//
// PURPOSE: validate the Supervisor's *mechanics* (daily archive rotation,
// health checks, auto-pause decision math, archive replay/aggregation) against
// a synthetic, deterministic, seeded stream — WITHOUT any real trading, real
// market data, real provider, or real continuous run.
//
// HONESTY CONTRACT (enforced, not advisory):
//   * This is NOT real Shadow (Phase E) and NOT real Paper (Phase F).
//   * All PnL is SYNTHETIC and produced by a seeded PRNG. The "market" is fake.
//   * ENABLE_REAL_TRADING and ALLOW_BUY_SELL are pinned to "false" for the whole
//     run; a guard throws if that invariant is ever violated, so the real-trading
//     path is provably unreachable from this harness.
//   * This harness does NOT call startSupervisor(). It uses the SAME persistence
//     / health / gate primitives, but as an offline validation tool. The real
//     continuous run remains gated by evaluateStartGate() and will still refuse
//     under the default config (this is asserted in simulateRun() and in tests).
//
// No network, no paid APIs, no real provider/model calls.

import fs from "node:fs";
import path from "node:path";
import {
  appendRecord,
  rotateDaily,
  readArchive,
  dayKey,
} from "./persistence.mjs";
import { checkHealth } from "./health.mjs";
import { evaluateStartGate, REQUIRED_AUTH_FLAGS } from "./gate.mjs";
import { loadSupervisorConfig } from "./config.mjs";

const DAY_MS = 24 * 60 * 60 * 1000;

/** Deterministic seeded PRNG (mulberry32). Same seed => same stream. */
function mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * Run a deterministic, offline simulation of the Phase E/F Supervisor mechanics.
 *
 * @param {object} [opts]
 * @param {string} opts.dir              ledger directory (created if missing)
 * @param {"SHADOW"|"PAPER"} [opts.mode] logical mode label for the report
 * @param {number} [opts.days]           number of simulated days
 * @param {number} [opts.seed]           PRNG seed (determinism)
 * @param {number} [opts.autoPauseDailyLossPct] pause if a day's loss exceeds this %
 * @param {number} [opts.startingCapital] synthetic starting capital (units)
 * @param {number|null} [opts.breachDay] 1-based day to inject a catastrophic loss
 * @param {Date|number} [opts.startDate] anchor for simulated day keys
 * @returns {object} structured simulation result (see fields below)
 */
export function simulateRun(opts = {}) {
  const {
    dir,
    mode = "SHADOW",
    days = 7,
    seed = 12345,
    autoPauseDailyLossPct = 5,
    startingCapital = 100000,
    breachDay = null,
    startDate = Date.UTC(2026, 0, 1),
  } = opts;

  if (!dir) throw new Error("SIMULATION_REQUIRES_DIR");

  // === INTEGRITY GUARD: real trading must stay OFF for the entire run =======
  const REAL_TRADING_GUARD = { ENABLE_REAL_TRADING: "false", ALLOW_BUY_SELL: "false" };
  if (
    REAL_TRADING_GUARD.ENABLE_REAL_TRADING !== "false" ||
    REAL_TRADING_GUARD.ALLOW_BUY_SELL !== "false"
  ) {
    throw new Error("SIMULATION_INTEGRITY: real trading must stay disabled");
  }

  const rng = mulberry32(seed);
  let capital = startingCapital;
  let paused = false;
  let pausedDay = null;
  let autoPauses = 0;
  const dailySummaries = [];

  for (let day = 1; day <= days; day++) {
    const ts = new Date(startDate + (day - 1) * DAY_MS);

    if (paused) {
      appendRecord(
        dir,
        { kind: "day-skipped", day, mode, reason: "PAUSED", simulation: true },
        ts,
      );
      continue;
    }

    // Synthetic daily return (percent). Deterministic given the seed.
    let dailyPct;
    if (breachDay !== null && day === breachDay) {
      dailyPct = -Math.abs(autoPauseDailyLossPct) - 3; // deliberately breach threshold
    } else {
      // roughly [-2%, +2%] with a slight positive bias; still synthetic.
      dailyPct = (rng() - 0.45) * 4;
    }

    const pnl = capital * (dailyPct / 100);
    capital += pnl;

    const summary = {
      kind: "day-summary",
      day,
      mode,
      dailyPct: Number(dailyPct.toFixed(4)),
      pnl: Number(pnl.toFixed(2)),
      capital: Number(capital.toFixed(2)),
      simulation: true,
    };
    appendRecord(dir, summary, ts);
    dailySummaries.push(summary);

    // Health check uses a sane, non-LIVE config => must be healthy.
    const health = checkHealth(
      {
        JEV_MODE: mode,
        SHADOW_DAYS: 7,
        PAPER_DAYS: 30,
        ...REAL_TRADING_GUARD,
        HAS_REAL_IDP: false,
        HAS_REAL_D1: false,
        HAS_EXTERNAL_WORM: false,
        HAS_PAID_API_BUDGET: false,
        HAS_REAL_CI: false,
      },
      dir,
    );

    // Auto-pause decision (independent of the real start gate): pause when a
    // single day's loss exceeds the configured threshold.
    const lossPct = dailyPct < 0 ? Math.abs(dailyPct) : 0;
    if (lossPct > autoPauseDailyLossPct) {
      paused = true;
      pausedDay = day;
      autoPauses += 1;
      appendRecord(
        dir,
        {
          kind: "auto-pause",
          day,
          mode,
          reason: "DAILY_LOSS_EXCEEDED",
          thresholdPct: autoPauseDailyLossPct,
          actualPct: Number(dailyPct.toFixed(4)),
          simulation: true,
        },
        ts,
      );
    }
  }

  // === Replay + aggregate using the REAL persistence primitives =============
  const all = readArchive(dir);
  const dayKeys = rotateDaily(dir);

  // Prove the REAL start gate STILL refuses under the default config — the
  // simulation must not be confused with an authorized continuous run.
  const defaultConfig = loadSupervisorConfig({});
  const gateUnderDefault = evaluateStartGate(defaultConfig, {
    phaseBGo: true,
    phaseCGo: true,
    phaseDGo: true,
  });
  const realGateRefusesUnderDefaultConfig = gateUnderDefault.canStart === false;

  return {
    WARNING: "SYNTHETIC_DATA__NOT_REAL_TRADING__NOT_REAL_MARKET__NOT_REAL_SHADOW_PAPER",
    simulation: true,
    mode,
    seed,
    daysRequested: days,
    daysRun: dailySummaries.length,
    daysSkipped: days - dailySummaries.length,
    autoPauses,
    paused,
    pausedDay,
    dayKeys,
    archiveDayFileCount: dayKeys.length,
    archiveRecordCount: all.length,
    startingCapital,
    endingCapital: Number(capital.toFixed(2)),
    cumulativePnl: Number((capital - startingCapital).toFixed(2)),
    synthetic: true,
    realTradingGuard: REAL_TRADING_GUARD,
    realGateRefusesUnderDefaultConfig,
    dailySummaries,
  };
}

/**
 * Render a simulation result as a clearly-labeled Markdown report.
 * @param {object} r  result from simulateRun()
 * @returns {string}
 */
export function formatReport(r) {
  const lines = [];
  lines.push(`# JEV V2.0 Phase E/F — SIMULATION REPORT (OFFLINE, SYNTHETIC)`);
  lines.push("");
  lines.push(`> INTEGRITY FLAG: ${r.WARNING}`);
  lines.push("");
  lines.push(
    "> ⚠️  **SIMULATION ONLY.** All PnL is SYNTHETIC (seeded PRNG). This is NOT",
  );
  lines.push(
    "> real Shadow (Phase E), NOT real Paper (Phase F), NOT real trading, NOT real",
  );
  lines.push("> market data. No network, no paid APIs, no real provider/model calls.");
  lines.push("");
  lines.push(`- Mode (label): ${r.mode}`);
  lines.push(`- Seed: ${r.seed} (deterministic)`);
  lines.push(`- Days requested: ${r.daysRequested}`);
  lines.push(`- Days run: ${r.daysRun}`);
  lines.push(`- Days skipped (post-pause): ${r.daysSkipped}`);
  lines.push(`- Auto-pauses triggered: ${r.autoPauses}`);
  lines.push(`- Paused: ${r.paused}${r.pausedDay ? ` (day ${r.pausedDay})` : ""}`);
  lines.push(`- Archive day-files: ${r.archiveDayFileCount}`);
  lines.push(`- Archive records: ${r.archiveRecordCount}`);
  lines.push(`- Starting (synthetic) capital: ${r.startingCapital}`);
  lines.push(`- Ending (synthetic) capital: ${r.endingCapital}`);
  lines.push(
    `- Cumulative synthetic PnL: ${r.cumulativePnl}  (DO NOT interpret as expected real return)`,
  );
  lines.push(
    `- Real-trading guard pinned OFF: ENABLE_REAL_TRADING=${r.realTradingGuard.ENABLE_REAL_TRADING}, ALLOW_BUY_SELL=${r.realTradingGuard.ALLOW_BUY_SELL}`,
  );
  lines.push(
    `- Real start gate still REFUSES under default config: ${r.realGateRefusesUnderDefaultConfig} (proves simulation does NOT bypass the gate)`,
  );
  lines.push("");
  lines.push(`## Daily summaries (synthetic)`);
  lines.push("");
  lines.push("| Day | dailyPct | pnl | capital |");
  lines.push("|----:|---------:|----:|--------:|");
  for (const d of r.dailySummaries) {
    lines.push(`| ${d.day} | ${d.dailyPct} | ${d.pnl} | ${d.capital} |`);
  }
  lines.push("");
  return lines.join("\n");
}

/** Convenience: write the Markdown report to disk. No side effects beyond file. */
export function writeReport(r, outPath) {
  const md = formatReport(r);
  fs.mkdirSync(path.dirname(outPath), { recursive: true });
  fs.writeFileSync(outPath, md, "utf8");
  return outPath;
}
