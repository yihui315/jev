// JEV V2.0 Phase E/F Supervisor — unattended continuous run loop body.
//
// PURPOSE: a REAL loop that is built ONLY when the start gate (evaluateStartGate)
// passes. When built it performs, against LOCAL state only:
//
//   1. Periodic local health checks (injectable interval / clock / health fn).
//   2. Bounded auto-recovery on TRANSIENT health failure, rebuilding loop state
//      from the local JSONL ledger (never an infinite retry).
//   3. Auto-pause when health stays below threshold -> writes a `type:"pause"`
//      event via persistence.mjs and stops the loop.
//   4. Daily run summary (writeDailySummary) generated on UTC day rollover,
//      aggregated from the same-day archive via readArchive.
//   5. Task deduplication keyed by a per-day task hash (in-memory Set + persisted
//      via appendRecord, warmed up at startup from readArchive).
//   6. A stop() handle to terminate the loop (test "stop").
//
// This module is SAFE BY CONSTRUCTION: it never reads ENABLE_REAL_TRADING /
// ALLOW_BUY_SELL, never places/cancels orders, never connects to a network,
// never calls a paid API, and never writes to a production DB. Every durable
// mutation lands in LOCAL JSONL via persistence.mjs.
//
// In the current (fail-closed) environment the gate always refuses, so a real
// loop is never built here. The logic below is complete and unit-tested through
// injected deps (mock clock / sleep / health / gate) — no real timers, no real
// network.

import os from "node:os";
import path from "node:path";

import { evaluateStartGate } from "./gate.mjs";
import { checkHealth } from "./health.mjs";
import { appendRecord, readArchive, dayKey } from "./persistence.mjs";

const DEFAULT_INTERVAL_MS = 60_000;
const DEFAULT_MAX_RECOVERY = 3;

// --- injectable real-behavior defaults ------------------------------------
const defaultClock = () => new Date();
const defaultSleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Deterministic, dependency-free string hash (FNV-1a, 32-bit hex).
 * Used to dedupe tasks without external state.
 * @param {string|object} input
 * @returns {string} 8-char hex hash
 */
export function stableHash(input) {
  const s = typeof input === "string" ? input : JSON.stringify(input);
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return ("0000000" + (h >>> 0).toString(16)).slice(-8);
}

/**
 * Aggregate one UTC day's ledger records into a small summary object.
 *
 * @param {string} dir  ledger directory
 * @param {string} day  UTC day key, e.g. "2026-11-13"
 * @param {object} [persistence]  injected persistence api (default real module)
 * @returns {{day:string, tickCount:number, taskCount:number, pauseCount:number, recoverCount:number, lastHealth:object|null}}
 */
export function summarizeDay(dir, day, persistence = { readArchive }) {
  const recs = readArchive(dir).filter((r) => r._day === day);
  const health = recs.filter((r) => r.type === "health");
  return {
    day,
    tickCount: health.length,
    taskCount: recs.filter((r) => r.type === "task").length,
    pauseCount: recs.filter((r) => r.type === "pause").length,
    recoverCount: recs.filter((r) => r.type === "recover").length,
    lastHealth: health.length ? health[health.length - 1] : null,
  };
}

/**
 * Persist a daily summary as a `type:"daily-summary"` record into the archive.
 * The stamped `_day` is forced to `day` so yesterday's summary lands in the
 * correct file even though it is written during today's first tick.
 *
 * @param {string} dir
 * @param {string} day  UTC day key the summary describes
 * @param {object} summary  payload (e.g. from summarizeDay)
 * @param {object} [persistence]  injected persistence api (default real module)
 * @returns {string} the written JSON line
 */
export function writeDailySummary(dir, day, summary, persistence = { appendRecord }) {
  const [y, m, d] = day.split("-").map(Number);
  // Noon UTC of that day — keeps the record inside the right day file.
  const ts = new Date(Date.UTC(y, m - 1, d, 12, 0, 0));
  return appendRecord(dir, { type: "daily-summary", day, summary }, ts);
}

/**
 * Build and run the unattended Supervisor loop.
 *
 * The function is FAIL-CLOSED: if `deps.gate(config, flags).canStart` is false
 * it returns `{ started:false, reasons }` immediately and builds NO loop, NO
 * timer, NO scheduler. A real loop is only built when the gate passes (a future,
 * fully-authorized environment). All deps are injectable so the loop can be
 * exercised deterministically in unit tests without real timers or network.
 *
 * @param {{config:object, flags?:object, deps?:object}} args
 * @param {object} args.config  resolved config (see config.mjs)
 * @param {{phaseBGo?:boolean, phaseCGo?:boolean, phaseDGo?:boolean}} [args.flags]
 * @param {object} [args.deps]
 * @param {Function} [args.deps.gate]            (config, flags) => {canStart, reasons}
 * @param {Function} [args.deps.clock]           () => Date
 * @param {Function} [args.deps.sleep]           (ms) => Promise<void>
 * @param {Function} [args.deps.healthCheck]     (config, dir) => {healthy, issues, transient?}
 * @param {object}   [args.deps.persistence]     { appendRecord, readArchive, dayKey }
 * @param {string}   [args.deps.ledgerDir]       base dir for JSONL (default tmp)
 * @param {number}   [args.deps.healthCheckIntervalMs] default 60_000
 * @param {number}   [args.deps.maxRecoveryAttempts]   default 3
 * @param {Function} [args.deps.getPendingTasks] () => Array<{id:string, hash?:string}>
 * @param {Function} [args.deps.hashTask]        (task) => string
 * @param {number}   [args.deps.maxTicks]        bounded run length (default Infinity)
 * @returns {{started:false, reasons:string[]} | {started:true, handle:object}}
 *          handle = { stopped, stop(), done:Promise<object> }
 */
export function runSupervisorLoop({ config, flags = {}, deps = {} }) {
  const {
    gate = evaluateStartGate,
    clock = defaultClock,
    sleep = defaultSleep,
    healthCheck = checkHealth,
    persistence = { appendRecord, readArchive, dayKey },
    ledgerDir = path.join(os.tmpdir(), "jev-supervisor-archive"),
    healthCheckIntervalMs = DEFAULT_INTERVAL_MS,
    maxRecoveryAttempts = DEFAULT_MAX_RECOVERY,
    getPendingTasks = () => [],
    hashTask = (t) => t && t.hash ? t.hash : stableHash(t),
    maxTicks = Infinity,
  } = deps;

  const gateResult = gate(config, flags);
  if (!gateResult.canStart) {
    // FAIL-CLOSED: no loop, no timer, no scheduler.
    return { started: false, reasons: gateResult.reasons };
  }

  const { appendRecord: append, readArchive: read, dayKey: dk } = persistence;

  // --- warm-up dedupe set from the persisted ledger for today --------------
  const today = dk(clock());
  const seenToday = new Set();
  for (const rec of read(ledgerDir)) {
    if (rec.type === "task" && rec.day === today && rec.taskHash) {
      seenToday.add(rec.taskHash);
    }
  }

  let lastDay = today;
  let recoveryAttempts = 0;
  let tickCount = 0;

  const handle = {
    stopped: false,
    stop() {
      this.stopped = true;
    },
    done: null,
  };

  /**
   * One loop iteration: day-rollover summary, health check + recovery/pause,
   * and deduplicated task scheduling.
   */
  const tick = async () => {
    if (handle.stopped) return;
    tickCount += 1;

    const now = clock();
    const curDay = dk(now);

    // (4) UTC day rollover -> write yesterday's summary first.
    if (curDay !== lastDay) {
      const summary = summarizeDay(ledgerDir, lastDay, persistence);
      writeDailySummary(ledgerDir, lastDay, summary, persistence);
      lastDay = curDay;
      // Reset per-day dedupe for the new day.
      seenToday.clear();
    }

    // (1) periodic local health check.
    const health = healthCheck(config, ledgerDir);
    append(ledgerDir, {
      type: "health",
      healthy: health.healthy,
      issues: health.issues || [],
    }, now);

    if (!health.healthy) {
      // (2) bounded recovery on TRANSIENT failure; else (3) pause.
      const canRecover =
        health.transient === true && recoveryAttempts < maxRecoveryAttempts;
      if (canRecover) {
        recoveryAttempts += 1;
        // Rebuild loop state from the LOCAL JSONL ledger (no network).
        const archive = read(ledgerDir);
        seenToday.clear();
        for (const rec of archive) {
          if (rec.type === "task" && rec.day === curDay && rec.taskHash) {
            seenToday.add(rec.taskHash);
          }
        }
        // Re-sync the day cursor from the latest persisted record.
        const last = archive[archive.length - 1];
        if (last && last._day) lastDay = last._day;
        append(ledgerDir, { type: "recover", attempt: recoveryAttempts }, now);
        return; // loop continues on next tick
      }
      // Unrecoverable -> pause and stop.
      append(ledgerDir, {
        type: "pause",
        reason: "HEALTH_BELOW_THRESHOLD",
        issues: health.issues || [],
        recoveryAttempts,
      }, now);
      handle.stop();
      return;
    }

    // Healthy: reset the recovery counter.
    recoveryAttempts = 0;

    // (5) deduplicated task scheduling for the current day.
    const pending = getPendingTasks();
    for (const task of pending) {
      const h = hashTask(task);
      if (seenToday.has(h)) continue; // same task already scheduled today
      seenToday.add(h);
      append(ledgerDir, {
        type: "task",
        taskHash: h,
        taskId: task && task.id,
        day: curDay,
      }, now);
    }
  };

  // (6) the loop itself, awaited via handle.done.
  handle.done = (async () => {
    while (!handle.stopped) {
      await tick();
      if (handle.stopped) break;
      if (maxTicks !== Infinity && tickCount >= maxTicks) {
        handle.stop();
        break;
      }
      await sleep(healthCheckIntervalMs);
    }
    return {
      stopped: true,
      tickCount,
      seenCount: seenToday.size,
      lastDay,
    };
  })();

  return { started: true, handle };
}
