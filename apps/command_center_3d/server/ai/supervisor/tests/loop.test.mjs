// JEV V2.0 Phase E/F Supervisor — unattended loop self-test (node:test).
//
// PROVES:
//   (a) startSupervisor() with the default config still returns started:false
//       and __actualStartCount stays 0 (no gate bypass; no timer/loop created).
//   (a2) runSupervisorLoop() with the REAL gate + default config also refuses
//        and creates NO timer.
//   (b) With an injected gate that forces canStart, runSupervisorLoop() builds a
//       real loop and:
//         - calls the health check once per tick (periodic),
//         - writes a `type:"pause"` event when health drops below threshold,
//         - generates a `type:"daily-summary"` on UTC day rollover,
//         - deduplicates the same task within a day,
//         - bounded-retries (recovers) on a transient failure without pausing.
//   (c) stop() correctly terminates the loop (driven via injected sleep/clock,
//       no long real timers).
//
// All tests use LOCAL tmp dirs, mock clocks/sleep/health/gate. No network, no
// paid APIs, no real trading, no setInterval/setTimeout long waits.

import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { evaluateStartGate } from "../gate.mjs";
import { loadSupervisorConfig } from "../config.mjs";
import { startSupervisor, getActualStartCount } from "../launcher.mjs";
import {
  runSupervisorLoop,
  writeDailySummary,
  summarizeDay,
  stableHash,
} from "../loop.mjs";
import { readArchive } from "../persistence.mjs";

// --- helpers ----------------------------------------------------------------
function tmpDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "jev-loop-"));
}

// A gate that always passes (used to exercise the loop body directly).
const passGate = () => ({ canStart: true, reasons: [] });
const fixedClock = (day = 1) => () => new Date(Date.UTC(2026, 0, day, 0, 0, 0));
const noopSleep = async () => {};

// --- (a) startSupervisor FAIL-CLOSED ---------------------------------------
test("(a) startSupervisor 默认配置返回 started:false 且 __actualStartCount===0", () => {
  const before = getActualStartCount();
  const res = startSupervisor(loadSupervisorConfig({}), {
    phaseBGo: false,
    phaseCGo: false,
    phaseDGo: false,
  });
  assert.equal(res.started, false, "must not start with default config");
  assert.ok(Array.isArray(res.reasons) && res.reasons.length > 0, "reasons provided");
  assert.equal(getActualStartCount(), before, "no actual start counted");
  assert.equal(getActualStartCount(), 0, "real start counter must remain 0");
});

// --- (a2) runSupervisorLoop refuses with the REAL gate ---------------------
test("(a2) runSupervisorLoop 真实 gate + 默认配置 拒绝且不建循环", () => {
  let timers = 0;
  const oi = global.setInterval;
  const ot = global.setTimeout;
  global.setInterval = (...a) => {
    timers += 1;
    return oi(...a);
  };
  global.setTimeout = (...a) => {
    timers += 1;
    return ot(...a);
  };
  try {
    const res = runSupervisorLoop({
      config: loadSupervisorConfig({}),
      flags: { phaseBGo: false, phaseCGo: false, phaseDGo: false },
    });
    assert.equal(res.started, false, "loop must refuse under default config");
    assert.ok(res.reasons && res.reasons.length > 0, "reasons provided");
    assert.equal(timers, 0, "no timer/loop created when gate refuses");
  } finally {
    global.setInterval = oi;
    global.setTimeout = ot;
  }
});

// --- (b1) periodic health check --------------------------------------------
test("(b1) 健康检查被周期性调用且受 maxTicks 约束终止", async () => {
  const dir = tmpDir();
  let healthCalls = 0;
  const deps = {
    gate: passGate,
    clock: fixedClock(1),
    sleep: noopSleep,
    healthCheck: () => {
      healthCalls += 1;
      return { healthy: true, issues: [] };
    },
    ledgerDir: dir,
    maxTicks: 4,
  };
  const res = runSupervisorLoop({ config: {}, flags: {}, deps });
  assert.equal(res.started, true, "loop should start with pass gate");
  const final = await res.handle.done;
  assert.equal(final.tickCount, 4, "ran exactly maxTicks iterations");
  assert.equal(healthCalls, 4, "health check called once per tick");
});

// --- (b2) pause on health below threshold ----------------------------------
test("(b2) 健康低于阈值时写 pause 事件并停止循环", async () => {
  const dir = tmpDir();
  const deps = {
    gate: passGate,
    clock: fixedClock(1),
    sleep: noopSleep,
    healthCheck: () => ({ healthy: false, issues: ["BOOM"], transient: false }),
    ledgerDir: dir,
  };
  const res = runSupervisorLoop({ config: {}, flags: {}, deps });
  const final = await res.handle.done;
  assert.equal(final.tickCount, 1, "paused after first unhealthy tick");
  const recs = readArchive(dir);
  const pause = recs.find((r) => r.type === "pause");
  assert.ok(pause, "a pause event was written");
  assert.equal(pause.reason, "HEALTH_BELOW_THRESHOLD");
  assert.deepEqual(pause.issues, ["BOOM"]);
});

// --- (b3) daily summary on UTC day rollover --------------------------------
test("(b3) UTC 日切换时生成昨日 daily-summary", async () => {
  const dir = tmpDir();
  let day = 1;
  const deps = {
    gate: passGate,
    clock: () => new Date(Date.UTC(2026, 0, day, 0, 0, 0)),
    // advance the calendar day between ticks, before the next tick runs.
    sleep: async () => {
      day += 1;
    },
    healthCheck: () => ({ healthy: true, issues: [] }),
    ledgerDir: dir,
    maxTicks: 3,
    getPendingTasks: () => [{ id: "t1", hash: "h1" }],
  };
  const res = runSupervisorLoop({ config: {}, flags: {}, deps });
  await res.handle.done;

  const recs = readArchive(dir);
  const summaries = recs.filter((r) => r.type === "daily-summary");
  assert.ok(summaries.length >= 2, "at least two day summaries generated on rollover");
  const days = summaries.map((s) => s.day);
  assert.equal(new Set(days).size, days.length, "summaries cover distinct days");
  for (const s of summaries) {
    assert.ok(s.summary && typeof s.summary.tickCount === "number", "summary aggregates the day");
  }
});

// --- (b4) task deduplication within a day ----------------------------------
test("(b4) 同一任务当日去重，不重复排程", async () => {
  const dir = tmpDir();
  const deps = {
    gate: passGate,
    clock: fixedClock(1), // fixed day => all ticks on the same UTC day
    sleep: noopSleep,
    healthCheck: () => ({ healthy: true, issues: [] }),
    ledgerDir: dir,
    maxTicks: 3,
    getPendingTasks: () => [{ id: "task-A", hash: "abc" }],
  };
  const res = runSupervisorLoop({ config: {}, flags: {}, deps });
  await res.handle.done;

  const recs = readArchive(dir);
  const tasks = recs.filter((r) => r.type === "task" && r.taskHash === "abc");
  assert.equal(tasks.length, 1, "same task scheduled only once within the day");
});

// --- (b5) bounded recovery on transient failure ----------------------------
test("(b5) 瞬时失败 bounded 重试恢复且不写 pause", async () => {
  const dir = tmpDir();
  let n = 0;
  const deps = {
    gate: passGate,
    clock: fixedClock(1),
    sleep: noopSleep,
    healthCheck: () => {
      n += 1;
      if (n === 1) return { healthy: false, issues: ["TRANSIENT_X"], transient: true };
      return { healthy: true, issues: [] };
    },
    ledgerDir: dir,
    maxRecoveryAttempts: 3,
    maxTicks: 4,
  };
  const res = runSupervisorLoop({ config: {}, flags: {}, deps });
  const final = await res.handle.done;

  const recs = readArchive(dir);
  const recover = recs.filter((r) => r.type === "recover");
  const pause = recs.filter((r) => r.type === "pause");
  assert.equal(pause.length, 0, "no pause on a transient failure that recovers");
  assert.ok(recover.length >= 1, "recovery was attempted on transient failure");
  assert.ok(final.tickCount >= 3, "loop continued past the transient failure");
});

// --- (c) stop() terminates the loop ----------------------------------------
test("(c) stop() 能正确终止循环（可控 sleep/clock，无长等待）", async () => {
  const dir = tmpDir();
  const resolvers = [];
  let healthCalls = 0;
  const deps = {
    gate: passGate,
    clock: fixedClock(1),
    // sleep parks until the test resolves it manually (no real timer).
    sleep: () => new Promise((resolve) => resolvers.push(resolve)),
    healthCheck: () => {
      healthCalls += 1;
      return { healthy: true, issues: [] };
    },
    ledgerDir: dir,
  };
  const res = runSupervisorLoop({ config: {}, flags: {}, deps });

  // Wait until the loop has run ONE tick and parked inside sleep.
  let spins = 0;
  while (resolvers.length === 0) {
    if (++spins > 10000) throw new Error("loop never parked in sleep");
    await new Promise((r) => setImmediate(r));
  }
  assert.equal(healthCalls, 1, "exactly one tick ran before stop()");

  // Stop and resume the parked sleep so the loop can observe the stop flag.
  res.handle.stop();
  const resolve = resolvers.shift();
  resolve();

  const final = await res.handle.done;
  assert.equal(final.stopped, true, "loop reports stopped");
  assert.equal(healthCalls, 1, "no extra runaway ticks after stop()");
});

// --- sanity: writeDailySummary / summarizeDay primitives -------------------
test("(d) writeDailySummary + summarizeDay 落盘并可回放", () => {
  const dir = tmpDir();
  const day = "2026-01-15";
  const summary = summarizeDay(dir, day);
  assert.equal(summary.day, day);
  assert.equal(summary.tickCount, 0);
  writeDailySummary(dir, day, summary);
  const recs = readArchive(dir);
  const s = recs.find((r) => r.type === "daily-summary");
  assert.ok(s, "daily-summary record persisted");
  assert.equal(s.day, day);
  assert.equal(s._day, day, "stamped into the correct day file");
  // stableHash is deterministic.
  assert.equal(stableHash("x"), stableHash("x"));
  assert.notEqual(stableHash("x"), stableHash("y"));
});
