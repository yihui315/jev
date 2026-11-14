// JEV V2.0 Phase E/F Supervisor — orchestrator entry point.
//
// PURPOSE: tie the config loader, gate, launcher, health check and persistence
// together behind one convenient entry. In THIS session it is fail-closed:
// `runSupervisor()` will refuse to start under the default / empty config and
// will NEVER create a continuous loop, timer, or scheduler.
//
// No network, no paid APIs, no real trading, no real provider/model calls.

import { loadSupervisorConfig } from "./config.mjs";
import { evaluateStartGate } from "./gate.mjs";
import { startSupervisor, getActualStartCount } from "./launcher.mjs";
import { checkHealth } from "./health.mjs";
import {
  runSupervisorLoop,
  writeDailySummary,
  summarizeDay,
  stableHash,
} from "./loop.mjs";

/**
 * Load config, evaluate the start gate, and either refuse (this session) or
 * return a started handle (future, fully-authorized environment only).
 *
 * @param {Record<string,string|undefined>} [env] environment (default process.env)
 * @param {{phaseBGo?:boolean, phaseCGo?:boolean, phaseDGo?:boolean}} [flags]
 *        mock-layer GO booleans. Default: all false => refuse.
 * @param {{ledgerDir?:string}} [opts] optional ledger dir for health probe
 * @returns {object} { started:false, reasons, config, health } in this session;
 *          a { started:true, handle } shape only in a future authorized env.
 */
export function runSupervisor(
  env = process.env,
  flags = { phaseBGo: false, phaseCGo: false, phaseDGo: false },
  opts = {}
) {
  const config = loadSupervisorConfig(env);
  const health = checkHealth(config, opts.ledgerDir);

  const result = startSupervisor(config, flags, opts);
  if (!result.started) {
    return { started: false, reasons: result.reasons, config, health };
  }

  // Future, fully-authorized environment only. Not reached in this session.
  return result;
}

// Re-export everything a caller / test might need.
export {
  loadSupervisorConfig,
  evaluateStartGate,
  startSupervisor,
  getActualStartCount,
  checkHealth,
  runSupervisorLoop,
  writeDailySummary,
  summarizeDay,
  stableHash,
};

export { ALLOWED_MODES } from "./config.mjs";
export { REQUIRED_AUTH_FLAGS } from "./gate.mjs";
export {
  appendRecord,
  rotateDaily,
  readArchive,
  dayKey,
} from "./persistence.mjs";
