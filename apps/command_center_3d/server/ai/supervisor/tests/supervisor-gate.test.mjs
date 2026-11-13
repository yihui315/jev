// JEV V2.0 Phase E/F Supervisor — gate self-test (node:test).
//
// PROVES that the Supervisor scaffold is FAIL-CLOSED: with the default / empty
// config it refuses to start and creates NO timers / loops, and that even with
// phase GO flags forced true, external authorization and LIVE real-trading
// guards still block a start. NO network, NO trading, NO provider calls.
//
// Run: node apps/command_center_3d/server/ai/supervisor/tests/supervisor-gate.test.mjs
// Expect: all cases PASS, process exit code 0.

import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { loadSupervisorConfig } from "../config.mjs";
import { evaluateStartGate, REQUIRED_AUTH_FLAGS } from "../gate.mjs";
import { startSupervisor, getActualStartCount } from "../launcher.mjs";
import { runSupervisor } from "../index.mjs";

// --- tally + summary --------------------------------------------------------
const tally = { pass: 0, fail: 0 };
process.on("exit", () => {
  process.stdout.write(`\nRESULT: PASS=${tally.pass} FAIL=${tally.fail}\n`);
  if (tally.fail > 0) process.exitCode = 1;
});

function wrap(name, fn) {
  test(name, () => {
    try {
      fn();
      tally.pass += 1;
    } catch (err) {
      tally.fail += 1;
      throw err; // let node:test report the failure too
    }
  });
}

// (a) Default config refuses start: MODE_OFF + phase flags + missing auth.
wrap("(a) default config refuses: MODE_OFF + phase NOT_GO + MISSING_AUTH", () => {
  const config = loadSupervisorConfig({});
  const flags = { phaseBGo: false, phaseCGo: false, phaseDGo: false };
  const { canStart, reasons } = evaluateStartGate(config, flags);

  assert.equal(canStart, false, "default config must refuse to start");
  assert.ok(reasons.includes("MODE_OFF"), "reasons must include MODE_OFF");
  for (const p of ["PHASE_B_NOT_GO", "PHASE_C_NOT_GO", "PHASE_D_NOT_GO"]) {
    assert.ok(reasons.includes(p), `reasons must include ${p}`);
  }
  for (const f of REQUIRED_AUTH_FLAGS) {
    assert.ok(
      reasons.includes("MISSING_AUTH:" + f),
      `reasons must include MISSING_AUTH:${f}`
    );
  }
  // Nothing was authorized, so the count of blockers is exactly all of them.
  assert.equal(reasons.length, 1 + 3 + REQUIRED_AUTH_FLAGS.length);
});

// (b) Even with phase GO forced true, missing external auth still blocks.
wrap("(b) phase GO all true but auth false => canStart=false (auth blocks)", () => {
  // SHADOW mode so MODE_OFF is not the cause; isolate the auth blocker.
  const config = loadSupervisorConfig({ JEV_MODE: "SHADOW" });
  const flags = { phaseBGo: true, phaseCGo: true, phaseDGo: true };
  const { canStart, reasons } = evaluateStartGate(config, flags);

  assert.equal(canStart, false, "missing external auth must block start");
  assert.ok(!reasons.includes("MODE_OFF"), "MODE_OFF should not be present");
  for (const p of ["PHASE_B_NOT_GO", "PHASE_C_NOT_GO", "PHASE_D_NOT_GO"]) {
    assert.ok(!reasons.includes(p), `phase should be GO: no ${p}`);
  }
  for (const f of REQUIRED_AUTH_FLAGS) {
    assert.ok(
      reasons.includes("MISSING_AUTH:" + f),
      `reasons must include MISSING_AUTH:${f}`
    );
  }
});

// (c) LIVE mode with trading enabled but auth missing => canStart=false.
//     Also proves REAL_TRADING_DISABLED when trading is NOT enabled.
wrap("(c) LIVE trading enabled + auth false => canStart=false (missing auth)", () => {
  const config = loadSupervisorConfig({
    JEV_MODE: "LIVE",
    ENABLE_REAL_TRADING: "true",
    ALLOW_BUY_SELL: "true",
  });
  const flags = { phaseBGo: true, phaseCGo: true, phaseDGo: true };
  const { canStart, reasons } = evaluateStartGate(config, flags);

  assert.equal(canStart, false, "LIVE without auth must refuse");
  for (const f of REQUIRED_AUTH_FLAGS) {
    assert.ok(
      reasons.includes("MISSING_AUTH:" + f),
      `LIVE reasons must include MISSING_AUTH:${f}`
    );
  }
  // Trading flags are true here, so REAL_TRADING_DISABLED must NOT be present.
  assert.ok(
    !reasons.includes("REAL_TRADING_DISABLED"),
    "REAL_TRADING_DISABLED should be absent when trading enabled"
  );

  // Independent proof of the REAL_TRADING_DISABLED branch:
  const configNoTrade = loadSupervisorConfig({
    JEV_MODE: "LIVE",
    ENABLE_REAL_TRADING: "false",
    ALLOW_BUY_SELL: "false",
  });
  const r2 = evaluateStartGate(configNoTrade, flags);
  assert.equal(r2.canStart, false);
  assert.ok(
    r2.reasons.includes("REAL_TRADING_DISABLED"),
    "LIVE with trading disabled must yield REAL_TRADING_DISABLED"
  );
});

// (d) startSupervisor with default config refuses AND creates NO timers/loops.
wrap("(d) startSupervisor default config: no start, no timer/loop created", () => {
  const before = getActualStartCount();

  // Instrument the global timers so any creation during the call is detected.
  let timerCalls = 0;
  const origInterval = global.setInterval;
  const origTimeout = global.setTimeout;
  global.setInterval = (...a) => {
    timerCalls += 1;
    return origInterval(...a);
  };
  global.setTimeout = (...a) => {
    timerCalls += 1;
    return origTimeout(...a);
  };
  try {
    const res = startSupervisor(loadSupervisorConfig({}), {
      phaseBGo: false,
      phaseCGo: false,
      phaseDGo: false,
    });
    assert.equal(res.started, false, "must not start with default config");
    assert.equal(getActualStartCount(), before, "no actual start counted");
    assert.equal(timerCalls, 0, "no setInterval/setTimeout created");
  } finally {
    global.setInterval = origInterval;
    global.setTimeout = origTimeout;
  }
});

// (e) runSupervisor() with default config returns started:false.
wrap("(e) runSupervisor default config returns started:false (no real-trading path)", () => {
  const res = runSupervisor();
  assert.equal(res.started, false, "runSupervisor must refuse under default config");
  assert.ok(Array.isArray(res.reasons) && res.reasons.length > 0, "reasons provided");
  assert.ok(res.config && res.health, "config and health included");
  assert.equal(res.health.healthy, true, "default local health should be healthy (no LIVE)");
  assert.equal(getActualStartCount(), 0, "no actual start occurred");
});

// Sanity: a fully authorized (future) env description is reachable in principle
// but is intentionally NOT executed here — guard the counter stays 0 overall.
wrap("(f) guard: no actual start counter leaked across the suite", () => {
  assert.equal(getActualStartCount(), 0, "real start counter must remain 0");
});
