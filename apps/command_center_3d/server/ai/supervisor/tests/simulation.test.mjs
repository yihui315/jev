// JEV V2.0 Phase E/F — SIMULATION harness tests (node:test, deterministic).
//
// These tests prove the Supervisor MECHANICS (daily archive rotation, health
// check, auto-pause decision, archive replay, gate-integrity) work correctly
// over a simulated calendar. They do NOT prove any real trading outcome.
//
// No network, no paid APIs, no real provider/model calls.

import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { simulateRun, formatReport } from "../simulation.mjs";
import { loadSupervisorConfig } from "../config.mjs";
import { evaluateStartGate } from "../gate.mjs";
import { checkHealth } from "../health.mjs";

function tmpDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "jev-sim-"));
}

test("(1) 7-day SHADOW simulation with no breach completes all days", () => {
  const dir = tmpDir();
  const r = simulateRun({ dir, mode: "SHADOW", days: 7, seed: 12345 });
  assert.equal(r.simulation, true);
  assert.equal(r.daysRequested, 7);
  assert.equal(r.daysRun, 7);
  assert.equal(r.daysSkipped, 0);
  assert.equal(r.paused, false);
  assert.equal(r.autoPauses, 0);
  // Daily rotation: 7 distinct day files (one per simulated UTC day).
  assert.equal(r.archiveDayFileCount, 7);
  // One day-summary record per day, no pause records.
  assert.equal(r.archiveRecordCount, 7);
  // Honesty labels present.
  assert.match(r.WARNING, /SYNTHETIC_DATA/);
  assert.equal(r.realGateRefusesUnderDefaultConfig, true);
});

test("(2) health check is healthy for a sane non-LIVE sim config", () => {
  const dir = tmpDir();
  const r = simulateRun({ dir, mode: "PAPER", days: 3, seed: 7 });
  // A sane, non-LIVE, fully-mock config must report healthy.
  // (health is exercised internally; re-check here via the same primitive path.)
  const h = checkHealth(
    {
      JEV_MODE: "PAPER",
      SHADOW_DAYS: 7,
      PAPER_DAYS: 30,
      ENABLE_REAL_TRADING: "false",
      ALLOW_BUY_SELL: "false",
      HAS_REAL_IDP: false,
      HAS_REAL_D1: false,
      HAS_EXTERNAL_WORM: false,
      HAS_PAID_API_BUDGET: false,
      HAS_REAL_CI: false,
    },
    dir,
  );
  assert.equal(h.healthy, true);
  assert.deepEqual(h.issues, []);
});

test("(3) auto-pause triggers on a simulated breach day and halts further runs", () => {
  const dir = tmpDir();
  const r = simulateRun({
    dir,
    mode: "SHADOW",
    days: 7,
    seed: 99,
    breachDay: 4,
    autoPauseDailyLossPct: 5,
  });
  assert.equal(r.paused, true);
  assert.equal(r.pausedDay, 4);
  assert.equal(r.autoPauses, 1);
  // Day 4 runs (summary) then pauses; days 1-4 summaries = 4 run days.
  assert.equal(r.daysRun, 4);
  // Days 5,6,7 are skipped.
  assert.equal(r.daysSkipped, 3);
  // Records: 4 summaries + 1 auto-pause + 3 skipped = 8.
  assert.equal(r.archiveRecordCount, 8);
  // All 7 simulated days still each have a file (rotation intact).
  assert.equal(r.archiveDayFileCount, 7);
});

test("(4) real-trading guard is pinned OFF throughout the simulation", () => {
  const dir = tmpDir();
  const r = simulateRun({ dir, days: 5, seed: 1 });
  assert.equal(r.realTradingGuard.ENABLE_REAL_TRADING, "false");
  assert.equal(r.realTradingGuard.ALLOW_BUY_SELL, "false");
  // The harness must never enable real trading — proven by the guard values.
  assert.notEqual(r.realTradingGuard.ENABLE_REAL_TRADING, "true");
});

test("(5) simulation is deterministic for a fixed seed", () => {
  const d1 = tmpDir();
  const d2 = tmpDir();
  const a = simulateRun({ dir: d1, days: 10, seed: 2026 });
  const b = simulateRun({ dir: d2, days: 10, seed: 2026 });
  assert.equal(a.endingCapital, b.endingCapital);
  assert.equal(a.cumulativePnl, b.cumulativePnl);
  assert.equal(a.daysRun, b.daysRun);
  assert.deepEqual(a.dailySummaries, b.dailySummaries);
});

test("(6) the real start gate STILL refuses under default config (no bypass)", () => {
  const defaultConfig = loadSupervisorConfig({});
  const g = evaluateStartGate(defaultConfig, {
    phaseBGo: true,
    phaseCGo: true,
    phaseDGo: true,
  });
  assert.equal(g.canStart, false);
  // Default config: MODE_OFF + 5 missing external-auth flags.
  assert.ok(g.reasons.includes("MODE_OFF"));
  assert.ok(g.reasons.some((x) => x.startsWith("MISSING_AUTH:")));
});

test("(7) formatReport produces a clearly-labeled synthetic report", () => {
  const dir = tmpDir();
  const r = simulateRun({ dir, days: 2, seed: 5 });
  const md = formatReport(r);
  assert.match(md, /SIMULATION REPORT/);
  assert.match(md, /SYNTHETIC/);
  assert.match(md, /NOT_REAL_TRADING/);
});
